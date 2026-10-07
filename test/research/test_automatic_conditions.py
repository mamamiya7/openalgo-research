"""Fixed causal condition catalog, frozen final choice and durable recovery."""

from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from test_automatic_protocol import evidence as original
from test_automatic_research import Engine, Stopped

from research.automatic_conditions import catalog, run
from research.automatic_protocol import CONDITION_VERSION, automatic_portfolio, compile_recipe
from research.entry_conditions import apply, markers, normalize
from research.market_series import freeze_series
from research.portfolio import fingerprint
from services.research_regimes import request_context

pytestmark = pytest.mark.timeout(300)


def prepared():
    value = original(config={"hold_sessions": 1, "target_pct": 4, "stop_pct": 2})
    value["portfolio"]["automatic_research"] = {"version": CONDITION_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    start = date.fromisoformat(value["snapshot"]["sessions"][0])
    warmup, cursor = [], start - timedelta(days=1)
    while len(warmup) < 160:
        if cursor.weekday() < 5:
            warmup.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days = sorted(warmup) + value["snapshot"]["sessions"]
    calendar = {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }
    descriptor = {"symbol": "NIFTY", "exchange": "NSE_INDEX", "interval": "D", "role": "benchmark"}
    bars = {
        day: {
            "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
            "open": 100 + i,
            "high": 102 + i,
            "low": 99 + i,
            "close": 101 + i,
        }
        for i, day in enumerate(days)
    }
    market = {
        "evidence": freeze_series(descriptor, days, calendar, bars),
        "calendar": calendar,
        "context": request_context(),
        "acquisition_receipts": [],
    }
    value["entry_condition_market"] = market
    value["automatic_recipe"] = compile_recipe(value)
    return value


def execute(value, engine, *, saved=None, stop=None):
    checkpoints = []

    def checkpoint(state, counts):
        checkpoints.append(deepcopy(state))
        if stop and stop(state):
            raise Stopped()

    try:
        report = run(
            value,
            evaluate=engine,
            saved=saved,
            checkpoint=checkpoint,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    except Stopped:
        return None, checkpoints[-1]
    return report, checkpoints[-1] if checkpoints else saved


def test_fixed_catalog_is_bounded_and_no_filter_is_a_real_candidate():
    value = prepared()
    entries = catalog(value["strategies"])
    assert len(entries) == 4 and entries[0]["entry_conditions"] is None
    for entry in entries[1:]:
        assert (
            normalize(entry["entry_conditions"], value["strategies"]) == entry["entry_conditions"]
        )
    assert value["automatic_recipe"]["budget"]["max_simulations"] == 100


def test_conditions_preserve_unchanged_engine_baseline_and_final_never_reranks():
    value = prepared()
    good, state = execute(value, Engine(value))
    bad, _ = execute(value, Engine(value, final_loss=True))
    assert (
        good["automatic_research"]["selected_config_id"]
        == bad["automatic_research"]["selected_config_id"]
    )
    assert good["strategies"] == bad["strategies"]
    assert good["automatic_research"]["counts"]["proposals"] == 50
    assert good["automatic_research"]["counts"]["simulations"] <= 100
    assert len(good["automatic_research"]["condition_candidates"]) <= 16
    assert state["selection"] is not None


def test_filtered_winner_retains_rule_identity_even_when_final_check_fails():
    class FilterEngine(Engine):
        def __call__(self, rows, snapshot, capital, progress=None):
            report = super().__call__(rows, snapshot, capital, progress)
            for source, target in zip(rows, report["strategies"], strict=True):
                if source.get("entry_condition"):
                    target["entry_condition"] = deepcopy(source["entry_condition"])
            rule = rows[0].get("entry_condition", {})
            if rule.get("regime") == "up":
                report["summary"]["net_return_pct"] += 20
                if (
                    self.final_loss
                    and snapshot["sessions"][0] == self.recipe["periods"]["final"]["from"]
                ):
                    report["summary"]["net_return_pct"] = -50
            return report

    value = prepared()
    good, _ = execute(value, FilterEngine(value))
    bad, _ = execute(value, FilterEngine(value, final_loss=True))
    assert good["entry_conditions"] == bad["entry_conditions"]
    assert good["entry_conditions"]["rules"][0]["regime"] == "up"
    assert good["strategies"] == bad["strategies"]
    assert (
        good["automatic_research"]["selected_config_id"]
        == bad["automatic_research"]["selected_config_id"]
    )
    assert good["automatic_research"]["status"] == "supported"
    assert bad["automatic_research"]["status"] == "not_supported"


@pytest.mark.parametrize("stage", ["search", "candidates", "selection", "final"])
def test_pause_recovery_preserves_completed_calculations(stage):
    value = prepared()
    engine = Engine(value)
    stops = {
        "search": lambda s: s["search_checkpoint"] and len(s["search_checkpoint"]["trials"]) == 7,
        "candidates": lambda s: s["candidates"] is not None,
        "selection": lambda s: s["selection"] is not None,
        "final": lambda s: s["final_report"] is not None,
    }
    _, state = execute(value, engine, stop=stops[stage])
    result, resumed = execute(value, engine, saved=state)
    expected_engine = Engine(value)
    expected, _ = execute(value, expected_engine)
    assert engine.calls == expected_engine.calls
    assert result["automatic_research"] == expected["automatic_research"]
    assert resumed["simulations"] == len(engine.calls)


def test_market_gate_uses_only_completed_prior_closes_and_preserves_original_rows():
    value = prepared()
    descriptor = catalog(value["strategies"])[1]["entry_conditions"]
    fixed = deepcopy(value)
    fixed["portfolio"]["entry_conditions"] = descriptor
    original_rows = deepcopy(fixed["strategies"])
    gated = apply(fixed, fixed["entry_condition_market"])
    assert len(gated["strategies"][0]["signals"]) == len(original_rows[0]["signals"])
    assert fixed["strategies"] == original_rows
    assert gated["entry_condition_gates"][0]["counts"]["allowed"] > 0
    marker = markers(original_rows, descriptor)[0]["entry_condition"]
    assert marker["recipe_id"] == descriptor["recipe"]["id"]
    altered = deepcopy(fixed["entry_condition_market"])
    final_start = fixed["automatic_recipe"]["periods"]["final"]["from"]
    series = altered["evidence"]
    for day, bar in series["bars"].items():
        if day >= final_start:
            bar.update(open=1, high=2, low=0.5, close=1)
    series["id"] = fingerprint({k: v for k, v in series.items() if k != "id"})
    changed = apply(fixed, altered)
    before = [
        r for r in gated["entry_condition_gates"][0]["membership"] if r["signal_date"] < final_start
    ]
    after = [
        r
        for r in changed["entry_condition_gates"][0]["membership"]
        if r["signal_date"] < final_start
    ]
    assert before == after


def test_catalog_and_checkpoint_tampering_are_rejected():
    value = prepared()
    _, state = execute(value, Engine(value), stop=lambda s: s["simulations"] == 1)
    state["simulations"] += 1
    with pytest.raises(ValueError, match="checkpoint"):
        execute(value, Engine(value), saved=state)
    unsupported = deepcopy(value["portfolio"])
    unsupported["engine"] = "nautilus"
    with pytest.raises(ValueError, match="VectorBT"):
        automatic_portfolio(unsupported)
