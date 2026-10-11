"""Registered conjunctions, causal membership and real bounded portfolio search."""

from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from test_automatic_hypotheses import Engine, Stopped, execute
from test_automatic_protocol import evidence as execution
from test_stock_market_context import bundle, refreeze, resign

from research import joint_stock_market as joint
from research.automatic_hypotheses import JOINT_VERSION, checkpoint_rows, policy_for_version
from research.automatic_protocol import automatic_portfolio, compile_recipe
from research.entry_conditions import apply as fixed_apply
from research.entry_conditions import enrich, normalize
from research.market_series import freeze_series
from research.stock_market_context import freeze_bundle, market_view, stock_view
from research.stock_relative_strength import BENCHMARK_DESCRIPTOR


def hypothesis(identifier):
    return next(row for row in joint.catalog() if row["id"] == identifier)


def test_live_history_stays_bounded_with_actual_first_and_latest_scores():
    from research.automatic_hypotheses import _display_history
    from services.research_activity import merge_activity

    trials = [{"number": i, "value": i / 10} for i in range(128)]
    original = deepcopy(trials)
    for end in range(129):
        points = _display_history(trials[:end])
        merge_activity({}, {"trials": {"history": points}}, 1)
        assert len(points) <= 100
        assert all(point["score"] == trials[point["trial"] - 1]["value"] for point in points)
        if end:
            assert points[0]["trial"] == 1 and points[-1]["trial"] == end
    assert trials == original


def prepared(sessions=160):
    value = execution(
        signal_sessions=sessions, config={"hold_sessions": 1, "target_pct": 4, "stop_pct": 2}
    )
    value["portfolio"]["automatic_research"] = {"version": JOINT_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["strategies"][0].update(deepcopy(value["portfolio"]["strategies"][0]))
    return freeze_context(value)


def freeze_context(value):
    value = deepcopy(value)
    earlier, cursor = [], date.fromisoformat(value["snapshot"]["sessions"][0]) - timedelta(days=1)
    while len(earlier) < 160:
        if cursor.weekday() < 5:
            earlier.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days = sorted(earlier) + value["snapshot"]["sessions"]

    def record(descriptor, required, warmup):
        calendar = {
            "sessions": required,
            "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in required},
            "provenance": {
                "exchange": "NSE",
                "calendar_basis": "openalgo-market-calendar-v1",
                "warmup_sessions": warmup,
            },
        }
        bars = {
            day: {
                "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
                "open": 100 + i / 10,
                "high": 102 + i / 10,
                "low": 99 + i / 10,
                "close": 101 + i / 10,
            }
            for i, day in enumerate(required)
        }
        return {
            "calendar": calendar,
            "evidence": freeze_series(descriptor, required, calendar, bars),
            "acquisition_receipts": [],
        }

    stocks = {
        "AAA": record(
            {"symbol": "AAA", "exchange": "NSE", "interval": "D", "role": "feature_warmup"},
            days[60:],
            100,
        )
    }
    market = record(BENCHMARK_DESCRIPTOR, days, 160)
    value["automatic_stock_market_context"] = freeze_bundle(
        stocks, market, value["snapshot"]["sessions"]
    )
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_registry_is_finite_portable_and_rejects_unregistered_combinations():
    rows = joint.catalog()
    assert len(rows) == 92 and len({row["id"] for row in rows}) == 92
    assert len({row["family"] for row in rows}) == 32
    assert joint.normalize_hypothesis(hypothesis("none"))["market"] is None
    changed = deepcopy(rows[1])
    changed["market"] = {"dimension": "trend", "regime": "crisis"}
    with pytest.raises(ValueError, match="supported joint"):
        joint.normalize_hypothesis(changed)
    source = [{"id": "scanner", "config": {}, "entry_condition": {"old": True}}]
    assert "entry_condition" not in joint.markers(source, hypothesis("none"))[0]
    assert joint.markers(source, rows[1], "first") == joint.markers(source, rows[1], "second")


@pytest.mark.parametrize("selected", joint.catalog(), ids=lambda row: row["id"])
def test_conjunction_matches_independent_native_gates(selected):
    from research.condition_replay import apply as market_gate
    from research.stock_relative_strength import apply as stock_gate

    context, value = bundle()
    before = deepcopy(value)
    _, left = stock_gate(value, stock_view(context), selected["stock_hypothesis"])
    right = None
    if selected["market"]:
        _, right = market_gate(
            value, market_view(context), {"strategy_id": "scanner", **selected["market"]}
        )
    rows, receipt = joint.apply(value, context, selected)
    states = [left["membership"][0]["state"]] + ([right["membership"][0]["state"]] if right else [])
    expected = next(
        (
            state
            for state in ("original_excluded", "pending", "unknown", "filtered")
            if state in states
        ),
        "allowed",
    )
    assert receipt["membership"][0]["state"] == expected
    assert sum(receipt["counts"].values()) == 1
    assert bool(rows[0]["signals"][0].get("research_exclusion")) == (
        expected in ("unknown", "filtered", "original_excluded")
    )
    assert value == before


def test_unknown_market_dominates_a_false_stock_condition():
    context, value = bundle(missing=(100,))
    selected = hypothesis("relative_20_gt0__trend_up")
    _, receipt = joint.apply(value, context, selected)
    row = receipt["membership"][0]
    assert row["stock_state"] == "filtered" and row["market_state"] == "unknown"
    assert row["state"] == "unknown" and receipt["counts"]["unknown"] == 1


def test_future_observations_cannot_change_prior_joint_membership():
    context, value = bundle()
    chosen = hypothesis("relative_20_gt0__trend_up")
    before = joint.apply(value, context, chosen)[1]["membership"]
    changed = deepcopy(context)
    cutoff = before[0]["decision_at"][:10]
    for record in [changed["market"], *changed["series"].values()]:
        for day, bar in record["evidence"]["bars"].items():
            if day >= cutoff:
                bar.update(open=900, high=902, low=899, close=901)
        refreeze(record)
    resign(changed)
    assert joint.apply(value, changed, chosen)[1]["membership"] == before


def test_fixed_rule_round_trip_uses_new_context_and_preserves_original_evidence():
    context, value = bundle()
    descriptor = {"version": joint.ENTRY_VERSION, "hypothesis": hypothesis("none__trend_up")}
    value["portfolio"]["entry_conditions"] = normalize(descriptor, value["strategies"])
    original = deepcopy(value)
    result = fixed_apply(value, context)
    assert result["automatic_stock_market_context"] == context
    assert "automatic_stock_features" not in result
    report = {"strategies": result["strategies"]}
    enrich(report, result)
    assert report["entry_conditions"] == descriptor
    assert report["automatic_stock_condition"]["bundle_id"] == context["id"]
    assert value == original


def test_v5_recipe_admits_joint_scope_without_changing_legacy_budget():
    from test_automatic_catalog import request

    from services.research_automatic_catalog import build_plan

    plan = build_plan(request(version=JOINT_VERSION), interval="D")
    assert plan["summary"]["condition_presets"] == 92
    assert plan["summary"]["max_simulations"] == 320
    value = prepared()
    recipe = value["automatic_recipe"]
    assert recipe["budget"]["proposals"] == 128
    assert recipe["budget"]["max_simulations"] == 320
    assert recipe["research_plan"]["data"]["stock_warmup_sessions"] == 100
    assert recipe["research_plan"]["data"]["benchmark_warmup_sessions"] == 160
    admitted = {
        row["id"] for row in recipe["research_plan"]["methods"] if row["status"] == "admitted"
    }
    assert {"stock_rsi", "bollinger", "atr", "relative_strength", "market_conditions"} <= admitted
    assert "automatic_stock_features" not in value
    assert policy_for_version("automatic-hypotheses-v4")["max_simulations"] == 240


def test_v5_pause_resume_keeps_committed_work_and_retained_rule_identity(monkeypatch):
    monkeypatch.setattr(
        "research.study_analysis.build_study_analysis", lambda *a, **k: {"charts": []}
    )
    value = prepared()
    engine = Engine(value)
    saved = {}

    def pause(state, _progress):
        saved.clear()
        saved.update(deepcopy(state))
        if len(state["trials"]) == 41:
            raise Stopped

    from research.automatic_hypotheses import run

    with pytest.raises(Stopped):
        run(
            value,
            evaluate=engine,
            checkpoint=pause,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    committed = len(engine.calls)
    assert checkpoint_rows(saved)
    result, final = execute(value, engine, saved=saved)
    assert len(final["trials"]) == 128
    assert len(engine.calls) == final["simulations"] <= 320
    assert committed < len(engine.calls)
    assert result["automatic_research"]["version"] == JOINT_VERSION
    assert all(
        row["strategies"][0].get("entry_condition", {}).get("version", joint.ENTRY_VERSION)
        == joint.ENTRY_VERSION
        for row in checkpoint_rows(final)
    )


# This runs the complete bounded native search; imported helpers do not inherit
# their module's 300-second limit, and hosted Windows can exceed the default 60s.
@pytest.mark.timeout(300)
def test_real_vectorbt_recalculates_every_joint_candidate(monkeypatch):
    from research.connectors.vectorbt_portfolio import evaluate

    monkeypatch.setattr(
        "research.study_analysis.build_study_analysis", lambda *a, **k: {"charts": []}
    )
    value = prepared(sessions=60)
    calls = []

    def native(rows, snapshot, capital, progress=None):
        calls.append(deepcopy(rows))
        return evaluate(rows, snapshot, capital, progress=progress)

    result, state = execute(value, native)
    assert len(calls) == state["simulations"] <= 320
    assert any(
        rows[0].get("entry_condition", {}).get("hypothesis", {}).get("market") for rows in calls
    )
    assert any(signal.get("research_exclusion") for rows in calls for signal in rows[0]["signals"])
    assert result["automatic_research"]["selected_is_baseline"]
    assert result["automatic_research"]["status"] != "supported"
