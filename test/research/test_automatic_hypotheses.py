"""Real serial Optuna, joint condition/exit interactions and durable native work."""

from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from test_automatic_protocol import evidence as original

from research.automatic_hypotheses import (
    BENCHMARK_VERSION,
    EXPANDED_VERSION,
    MAX_SIMULATIONS,
    VERSION,
    _gate,
    checkpoint_rows,
    policy_for_version,
    run,
)
from research.automatic_protocol import automatic_portfolio, compile_recipe
from research.connectors.optuna_portfolio import _fingerprint, _settings
from research.market_series import freeze_series
from research.portfolio import fingerprint
from research.report_contract import settings_identity
from research.stock_features import VERSION as FEATURE_VERSION
from research.stock_features import catalog

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(autouse=True)
def isolate_plot_rendering(monkeypatch):
    # Exercise real Optuna proposal/replay here; figure rendering has its own
    # contract tests and must not dominate interruption-path coverage.
    monkeypatch.setattr(
        "research.study_analysis.build_study_analysis", lambda *a, **k: {"charts": []}
    )


def prepared(*, hold=1, sessions=160, version=VERSION):
    value = original(
        signal_sessions=sessions, config={"hold_sessions": hold, "target_pct": 4, "stop_pct": 2}
    )
    value["portfolio"]["automatic_research"] = {"version": version}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["strategies"][0].update(deepcopy(value["portfolio"]["strategies"][0]))
    cursor = date.fromisoformat(value["snapshot"]["sessions"][0]) - timedelta(days=1)
    earlier = []
    while len(earlier) < 120:
        if cursor.weekday() < 5:
            earlier.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days = sorted(earlier) + value["snapshot"]["sessions"]
    calendar = {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }
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
    descriptor = {"symbol": "AAA", "exchange": "NSE", "interval": "D", "role": "feature_warmup"}
    bundle = {
        "version": FEATURE_VERSION,
        "series": {
            "AAA": {
                "evidence": freeze_series(descriptor, days, calendar, bars),
                "calendar": calendar,
                "acquisition_receipts": [],
            }
        },
    }
    bundle["id"] = fingerprint(bundle)
    if version == BENCHMARK_VERSION:
        from research.stock_relative_strength import BENCHMARK_DESCRIPTOR
        from research.stock_relative_strength import VERSION as CONTEXT_VERSION

        benchmark_bars = {
            day: {**bar, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
            for day, bar in bars.items()
        }
        bundle = {
            "version": CONTEXT_VERSION,
            "series": bundle["series"],
            "benchmark": {
                "evidence": freeze_series(BENCHMARK_DESCRIPTOR, days, calendar, benchmark_bars),
                "calendar": calendar,
                "acquisition_receipts": [],
            },
        }
        bundle["id"] = fingerprint(bundle)
    value["automatic_stock_features"] = bundle
    value["automatic_recipe"] = compile_recipe(value)
    return value


class Stopped(Exception):
    pass


class Engine:
    def __init__(self, value, *, final_loss=False, no_edge=False, fragile=False):
        self.recipe = value["automatic_recipe"]
        self.final_loss, self.no_edge, self.fragile = final_loss, no_edge, fragile
        self.calls = []

    def __call__(self, rows, snapshot, capital, progress=None):
        settings = _settings(rows)
        period = next(
            name
            for name, bounds in self.recipe["periods"].items()
            if bounds["from"] == snapshot["sessions"][0]
        )
        self.calls.append((period, settings_identity(settings)))
        if progress:
            progress(1, 1)
        hypothesis = rows[0].get("entry_condition", {}).get("hypothesis", {}).get("id", "none")
        cfg = rows[0]["config"]
        target = cfg["target_pct"]
        # Exit-only research prefers target 2. A stock hypothesis needs target 6:
        # none of the best exit-only candidates expose this smooth interaction.
        profit = 10 - 2 * abs(target - 2)
        if hypothesis == "ema_above_10":
            profit = 28 - 5 * abs(target - 6)
        elif hypothesis != "none":
            profit = 1
        if self.fragile and hypothesis == "ema_above_10":
            profit = 30 if target == 6 else -30
        if self.final_loss and period == "final" and hypothesis != "none":
            profit = -50
        if self.no_edge:
            profit = 0
        profit -= cfg["cost_bps"] / 100
        allowed = [signal for signal in rows[0]["signals"] if not signal.get("research_exclusion")]
        closed = min(10, len(allowed))
        return {
            "strategies": settings,
            "summary": {"net_return_pct": profit, "max_drawdown_pct": 2, "closed_trades": closed},
            "ledger": [
                {"status": "closed", "entry_date": signal["date"]} for signal in allowed[:closed]
            ],
        }


def execute(value, engine, *, saved=None, stop=None, admit_final=None, observe=None):
    checkpoints = []

    def checkpoint(state, counts):
        checkpoints.append(deepcopy(state))
        if stop and stop(state):
            raise Stopped()

    try:
        result = run(
            value,
            evaluate=engine,
            saved=saved,
            checkpoint=checkpoint,
            progress=lambda *a: None,
            cancelled=lambda: None,
            admit_final=admit_final,
            observe=observe,
        )
    except Stopped:
        return None, checkpoints[-1]
    return result, checkpoints[-1] if checkpoints else saved


def test_joint_search_gives_every_family_exits_before_refinement_and_finds_interaction():
    value = prepared()
    original_value = deepcopy(value)
    engine = Engine(value)
    result, state = execute(value, engine)
    finding = result["automatic_research"]
    assert finding["status"] == "supported"
    assert result["automatic_stock_condition"]["hypothesis"]["id"] == "ema_above_10"
    assert result["strategies"][0]["config"]["target_pct"] >= 5
    by_id = {row["id"]: row for row in catalog()}
    trials = result["experiment"]["trials"]
    assert len(trials) == 60
    # Captured before v3 existed: adding families must not change v2 proposals,
    # sampler policy or settings identity, including legacy checkpoint replay.
    assert fingerprint(trials) == "5b7733dcc73029fafaafdc678ee08e8ad02dc97f7d4b4af530f96a3f6dd6b552"
    assert (
        fingerprint(result["experiment"]["optimizer"])
        == "565e4ebf82e254637d799680751294aae629cf452051484bd8748b0974e8a70a"
    )
    assert (
        finding["selected_config_id"]
        == "69b325ab3f9244db8d0334d1cf871aed9211e0e7f46064391e248bc3327c5308"
    )
    assert state["simulations"] == 85
    assert all(t["phase"] == "family_screening" for t in trials[:30])
    assert all(t["phase"] == "joint_refinement" for t in trials[30:])
    for family in ("none", "ema_trend", "momentum"):
        screened = [t for t in trials[:30] if by_id[t["params"]["hypothesis"]]["family"] == family]
        assert len(screened) == 10
        assert len({t["params"]["scanner.target_pct"] for t in screened}) >= 3
    assert state["simulations"] == len(engine.calls) <= MAX_SIMULATIONS
    assert state["attempts"] == state["simulations"]
    assert value == original_value
    for checked in finding["checks"]:
        if checked["eligible"]:
            assert checked["stress"]
            assert 1 <= checked["stability"]["tested"] <= 4
            assert checked["stability"]["pass_fraction"] >= 0.75
            assert all(
                {w["period"] for w in n["windows"]} == {"check1", "check2"}
                for n in checked["stability"]["neighbors"]
            )


def test_no_edge_preserves_original_and_evaluates_final_baseline_once():
    value = prepared()
    engine = Engine(value, no_edge=True)
    result, state = execute(value, engine)
    assert result["automatic_research"]["selected_is_baseline"]
    assert result["automatic_research"]["status"] in ("not_supported", "inconclusive")
    assert result["strategies"] == _settings(value["strategies"])
    assert not result.get("entry_conditions")
    assert len([row for row in engine.calls if row[0] == "final"]) == 1
    assert state["attempts"] <= MAX_SIMULATIONS


def test_final_outcome_cannot_rerank_and_selection_is_saved_before_final_admission():
    value = prepared()
    good, _ = execute(value, Engine(value))
    observed = []

    def admission(recipe):
        observed.append(recipe["id"])

    bad, state = execute(value, Engine(value, final_loss=True), admit_final=admission)
    assert good["strategies"] == bad["strategies"]
    assert good["experiment"]["trials"] == bad["experiment"]["trials"]
    assert bad["automatic_research"]["status"] == "not_supported"
    assert bad["automatic_research"]["final"]["summary"]["net_return_pct"] < 0
    assert observed == [value["automatic_recipe"]["id"]]
    assert state["selection"]["config_id"] == settings_identity(bad["strategies"])
    selected = bad["experiment"]["selected_reports"][state["selection"]["config_id"]]
    assert selected["automatic_stock_condition"]["gate_id"]
    assert (
        selected["automatic_stock_condition"]["bundle_id"]
        == value["automatic_stock_features"]["id"]
    )
    assert selected["entry_conditions"] == selected["strategies"][0]["entry_condition"]
    assert selected["automatic_recipe"] == value["automatic_recipe"]
    assert bad["validation"]["result"]["entry_conditions"] == selected["entry_conditions"]


@pytest.mark.parametrize(
    "stage",
    ["baseline", "proposal_summary", "screening", "refinement", "checks", "selection", "final"],
)
def test_resume_replays_real_optuna_without_repeating_committed_native_calls(stage):
    value = prepared()
    engine = Engine(value)
    stops = {
        "baseline": lambda s: s["simulations"] == 1,
        "proposal_summary": lambda s: s["simulations"] == 8 and len(s["trials"]) == 6,
        "screening": lambda s: len(s["trials"]) == 12,
        "refinement": lambda s: len(s["trials"]) == 41,
        "checks": lambda s: (
            len(s["trials"]) == 60 and s["selection"] is None and s["simulations"] > 64
        ),
        "selection": lambda s: s["selection"] is not None,
        "final": lambda s: s["final_report"] is not None,
    }
    partial, saved = execute(value, engine, stop=stops[stage])
    assert partial is None
    result, finished = execute(value, engine, saved=saved)
    reference_engine = Engine(value)
    reference, _ = execute(value, reference_engine)
    assert engine.calls == reference_engine.calls
    assert result["experiment"]["trials"] == reference["experiment"]["trials"]
    assert result["automatic_research"] == reference["automatic_research"]
    assert finished["simulations"] == len(engine.calls)
    execute(value, engine, saved=finished)
    assert len(engine.calls) == finished["simulations"]
    assert checkpoint_rows(finished) == result["experiment"]["rows"]


def test_checkpoint_binds_recipe_features_and_completed_evidence():
    value = prepared()
    _, state = execute(value, Engine(value), stop=lambda s: len(s["trials"]) == 2)
    changed = deepcopy(state)
    changed["simulations"] += 1
    with pytest.raises(ValueError, match="checkpoint"):
        execute(value, Engine(value), saved=changed)
    altered = deepcopy(value)
    record = altered["automatic_stock_features"]["series"]["AAA"]
    first = record["calendar"]["sessions"][0]
    record["evidence"]["bars"][first]["close"] += 0.1
    with pytest.raises(ValueError, match="checkpoint|recipe|evidence changed"):
        execute(altered, Engine(altered), saved=state)


def test_retry_reservation_cannot_exceed_hard_native_call_cap():
    value = prepared()
    engine = Engine(value)
    _, state = execute(value, engine, stop=lambda s: s["simulations"] == 1)
    # Simulate prior interrupted attempts already charged by durable reservations.
    state["attempts"] = MAX_SIMULATIONS
    state["checkpoint_id"] = _fingerprint({k: v for k, v in state.items() if k != "checkpoint_id"})
    before = len(engine.calls)
    with pytest.raises(ValueError, match="calculation limit"):
        execute(value, engine, saved=state)
    assert len(engine.calls) == before


def test_long_horizon_exploratory_search_does_not_claim_validation_or_admit_final():
    value = prepared(hold=45)
    assert value["automatic_recipe"]["validation_mode"] == "exploratory_only"
    engine = Engine(value)

    def forbidden(recipe):
        raise AssertionError("Exploratory search has no held-out final period to admit")

    result, state = execute(value, engine, admit_final=forbidden)
    assert result["automatic_research"]["status"] == "inconclusive"
    assert result["automatic_research"]["final"] is None
    assert not result["automatic_research"]["checks"]
    assert "validation" not in result
    assert state["selection"] and state["final_report"] is None
    assert {row[0] for row in engine.calls} == {"search"}
    assert "no untouched later check" in result["automatic_research"]["evidence_scope"]


def test_engine_cannot_return_a_different_explicit_condition_identity():
    value = prepared()
    engine = Engine(value)

    def changes_marker(*args, **kwargs):
        result = engine(*args, **kwargs)
        for row in result["strategies"]:
            if row.get("entry_condition"):
                row["entry_condition"]["hypothesis"] = catalog()[-1]
        return result

    with pytest.raises(ValueError, match="different hypothesis or exit settings"):
        execute(value, changes_marker)


def test_recorded_joint_parameters_reconstruct_real_optuna_with_categorical_hypotheses():
    from research.study_analysis import reconstruct_study

    value = prepared()
    result, _ = execute(value, Engine(value))
    study, axes = reconstruct_study(result["experiment"])
    assert len(study.trials) == 60
    assert axes["hypothesis"]["type"] == "categorical"
    assert study.trials[0].params == result["experiment"]["trials"][0]["params"]
    assert study.best_value == result["experiment"]["rows"][0]["score"]


def test_real_future_execution_and_feature_bars_do_not_change_selection():
    value = prepared()
    good, _ = execute(value, Engine(value))
    changed = deepcopy(value)
    final_from = value["automatic_recipe"]["periods"]["final"]["from"]
    for day, bar in changed["snapshot"]["bars"]["AAA"].items():
        if day >= final_from:
            bar.update(open=1, high=2, low=0.5, close=1)
    bundle = changed["automatic_stock_features"]
    record = bundle["series"]["AAA"]
    series = record["evidence"]
    for day, bar in series["bars"].items():
        if day >= final_from:
            bar.update(open=1, high=2, low=0.5, close=1)
    record["evidence"] = freeze_series(
        series["descriptor"], series["required_dates"], record["calendar"], series["bars"]
    )
    bundle["id"] = fingerprint({k: v for k, v in bundle.items() if k != "id"})
    changed["automatic_recipe"] = compile_recipe(changed)
    bad, _ = execute(changed, Engine(changed, final_loss=True))
    assert good["automatic_recipe"]["id"] != bad["automatic_recipe"]["id"]
    assert (
        good["automatic_stock_condition"]["bundle_id"]
        != bad["automatic_stock_condition"]["bundle_id"]
    )
    assert good["strategies"] == bad["strategies"]
    assert good["experiment"]["trials"] == bad["experiment"]["trials"]
    assert (
        good["automatic_research"]["selected_config_id"]
        == bad["automatic_research"]["selected_config_id"]
    )


def test_activity_opens_and_finishes_only_new_proposals_on_resume():
    value = prepared()
    engine, events = Engine(value), []
    _, saved = execute(value, engine, observe=events.append, stop=lambda s: len(s["trials"]) == 41)
    assert events[0]["kind"] == "search_started" and events[0]["replayed"] == 0
    assert [row["number"] for row in events if row["kind"] == "proposal_started"] == list(range(41))
    assert [row["number"] for row in events if row["kind"] == "proposal_finished"] == list(
        range(41)
    )
    events.clear()
    execute(value, engine, saved=saved, observe=events.append)
    assert events[0]["kind"] == "search_started" and events[0]["replayed"] == 41
    assert [row["number"] for row in events if row["kind"] == "proposal_started"] == list(
        range(41, 60)
    )
    assert [row["number"] for row in events if row["kind"] == "proposal_finished"] == list(
        range(41, 60)
    )


@pytest.mark.parametrize("version", [VERSION, EXPANDED_VERSION, BENCHMARK_VERSION])
def test_real_vectorbt_receives_causal_gates_and_retains_report_settings(version):
    from research.connectors.vectorbt_portfolio import evaluate

    value = prepared(sessions=60, version=version)
    calls = []

    def native(rows, snapshot, capital, progress=None):
        calls.append((deepcopy(rows), len(snapshot["sessions"])))
        return evaluate(rows, snapshot, capital, progress=progress)

    result, state = execute(value, native)
    assert result["automatic_research"]["selected_is_baseline"]
    assert result["automatic_research"]["status"] != "supported"
    assert result["strategies"] == _settings(value["strategies"])
    assert len(calls) == state["simulations"] <= policy_for_version(version)["max_simulations"]
    conditioned = [rows for rows, _ in calls if rows[0].get("entry_condition")]
    assert conditioned
    assert any(
        signal.get("research_exclusion") for rows in conditioned for signal in rows[0]["signals"]
    )


def test_cached_membership_keeps_exact_candidate_receipt_and_never_reuses_entry_timing(monkeypatch):
    from research.automatic_protocol import slice_period
    from research.stock_features import apply

    value = prepared()
    current = slice_period(value, "search")
    hypothesis, cache = catalog()[1], OrderedDict()
    calls = []

    def counted(*args):
        calls.append(1)
        return apply(*args)

    monkeypatch.setattr("research.automatic_hypotheses.apply", counted)
    first_rows, first_receipt = _gate(
        current, value["automatic_stock_features"], hypothesis, cache, "search"
    )
    changed = deepcopy(current)
    changed["strategies"][0]["config"].update(target_pct=6, hold_sessions=2, cost_bps=15)
    cached_rows, cached_receipt = _gate(
        changed, value["automatic_stock_features"], hypothesis, cache, "search"
    )
    direct_rows, direct_receipt = apply(changed, value["automatic_stock_features"], hypothesis)
    assert len(calls) == 1
    assert (cached_rows, cached_receipt) == (direct_rows, direct_receipt)
    assert cached_receipt["membership"] == first_receipt["membership"]
    assert cached_receipt["id"] != first_receipt["id"]
    assert first_rows[0]["config"] != cached_rows[0]["config"]
    changed["strategies"][0]["config"]["entry_time"] = "10:00"
    _gate(changed, value["automatic_stock_features"], hypothesis, cache, "search")
    assert len(calls) == 2


def test_candidate_basis_binds_its_own_condition_and_cohort_across_checkpoint_resume():
    value = prepared()
    engine = Engine(value)
    _, partial = execute(value, engine, stop=lambda state: len(state["trials"]) == 41)
    retained = {
        row["config_id"]: deepcopy(row["evaluation_basis"]) for row in checkpoint_rows(partial)
    }
    result, finished = execute(value, engine, saved=partial)
    rows = result["experiment"]["rows"]
    assert checkpoint_rows(finished) == rows
    baseline_cohort = next(
        row["evaluation_basis"]["cohort_id"] for row in rows if row["hypothesis"]["id"] == "none"
    )
    filtered = []
    for row in rows:
        basis, hypothesis, counts = row["evaluation_basis"], row["hypothesis"], row["counts"]
        assert basis["status"] == "verified" and basis["period"]["kind"] == "selection"
        if row["config_id"] in retained:
            assert basis == retained[row["config_id"]]
        descriptor = basis["comparison"]["execution"].get("entry_conditions")
        if hypothesis["id"] == "none":
            assert descriptor is None
        else:
            assert descriptor == row["strategies"][0]["entry_condition"]
            assert descriptor["hypothesis"] == hypothesis
        assert basis["admission"] == {
            "eligible": counts["allowed"],
            "excluded": counts["filtered"] + counts["unknown"] + counts["original_excluded"],
            "pending": counts["pending"],
        }
        if counts["filtered"]:
            filtered.append(row)
            assert basis["cohort_id"] != baseline_cohort
    assert filtered


def test_v3_screens_all_seven_families_and_all_eighteen_hypotheses_before_refinement():
    from research.stock_indicators import ENTRY_VERSION
    from research.stock_indicators import catalog as indicator_catalog

    value = prepared(version=EXPANDED_VERSION)
    engine = Engine(value)
    result, state = execute(value, engine)
    experiment, finding = result["experiment"], result["automatic_research"]
    by_id = {row["id"]: row for row in indicator_catalog()}
    trials = experiment["trials"]
    assert len(trials) == 84 and len(by_id) == 18
    assert experiment["optimizer"]["startup_trials"] == 56
    assert experiment["optimizer"]["family_screening_trials"] == 8
    assert experiment["optimizer"]["adapter_version"] == EXPANDED_VERSION
    assert all(row["phase"] == "family_screening" for row in trials[:56])
    assert all(row["phase"] == "joint_refinement" for row in trials[56:])
    assert {row["params"]["hypothesis"] for row in trials[:56]} == set(by_id)
    families = {row["family"] for row in by_id.values()}
    assert len(families) == 7
    for family in families:
        screened = [
            row for row in trials[:56] if by_id[row["params"]["hypothesis"]]["family"] == family
        ]
        assert len(screened) == 8
        assert len({row["params"]["scanner.target_pct"] for row in screened}) >= 3
    assert finding["version"] == EXPANDED_VERSION
    assert finding["status"] == "supported"
    assert result["entry_conditions"]["version"] == ENTRY_VERSION
    assert finding["coverage"]["family_screening_proposals"] == 56
    assert state["simulations"] <= state["attempts"] <= 220
    assert experiment["counts"]["reused_trials"] == sum(row["reused"] for row in trials)
    assert len(experiment["rows"]) == len({row["config_id"] for row in trials})
    search_calls = [row for row in engine.calls if row[0] == "search"]
    # Only the selected full-report reconstruction may repeat a native search
    # calculation. Repeated Optuna proposals reuse their completed summaries.
    assert len(search_calls) == len(set(search_calls)) + 1
    for row in experiment["rows"]:
        descriptor = row["evaluation_basis"]["comparison"]["execution"].get("entry_conditions")
        assert (
            descriptor is None
            if row["hypothesis"]["id"] == "none"
            else descriptor["version"] == ENTRY_VERSION
        )


@pytest.mark.parametrize("pause_at", [19, 63])
def test_v3_resume_replays_84_proposals_without_repeating_completed_calculations(pause_at):
    value = prepared(version=EXPANDED_VERSION)
    engine = Engine(value)
    _, saved = execute(value, engine, stop=lambda state: len(state["trials"]) == pause_at)
    result, finished = execute(value, engine, saved=saved)
    reference_engine = Engine(value)
    reference, _ = execute(value, reference_engine)
    assert engine.calls == reference_engine.calls
    assert result["experiment"]["trials"] == reference["experiment"]["trials"]
    assert result["automatic_research"] == reference["automatic_research"]
    assert checkpoint_rows(finished) == result["experiment"]["rows"]
    execute(value, engine, saved=finished)
    assert finished["simulations"] == len(engine.calls)


def test_version_policies_are_isolated_and_v3_retry_reservations_stop_at_220():
    old, expanded = policy_for_version(VERSION), policy_for_version(EXPANDED_VERSION)
    assert (old["proposals"], old["max_simulations"], old["family_screen"]) == (60, 180, 10)
    assert (expanded["proposals"], expanded["max_simulations"], expanded["family_screen"]) == (
        84,
        220,
        8,
    )
    expanded["max_simulations"] = 999
    assert policy_for_version(EXPANDED_VERSION)["max_simulations"] == 220
    assert policy_for_version(VERSION) == old
    with pytest.raises(ValueError, match="supported automatic hypothesis"):
        policy_for_version("automatic-hypotheses-v5")
    value = prepared(version=EXPANDED_VERSION)
    engine = Engine(value)
    _, saved = execute(value, engine, stop=lambda state: state["simulations"] == 1)
    saved["attempts"] = 220
    saved["checkpoint_id"] = _fingerprint({k: v for k, v in saved.items() if k != "checkpoint_id"})
    assert checkpoint_rows(saved) == []
    before = list(engine.calls)
    with pytest.raises(ValueError, match="calculation limit"):
        execute(value, engine, saved=saved)
    assert engine.calls == before
    for change in ({"attempts": 221}, {"version": VERSION}, {"trials": [{}] * 85}):
        altered = {**saved, **change}
        altered["checkpoint_id"] = _fingerprint(
            {k: v for k, v in altered.items() if k != "checkpoint_id"}
        )
        with pytest.raises(ValueError, match="checkpoint"):
            checkpoint_rows(altered)


def test_v3_cached_gate_keeps_native_recipe_and_cannot_collide_with_v2():
    from research.automatic_protocol import slice_period
    from research.stock_features import apply as legacy_apply
    from research.stock_indicators import FEATURE_VERSION as INDICATOR_VERSION
    from research.stock_indicators import apply as indicator_apply
    from research.stock_indicators import markers as indicator_markers

    value = prepared(version=EXPANDED_VERSION)
    current = slice_period(value, "search")
    hypothesis, cache = catalog()[1], OrderedDict()
    old_rows, old_receipt = _gate(
        current, value["automatic_stock_features"], hypothesis, cache, "search"
    )
    new_rows, new_receipt = _gate(
        current,
        value["automatic_stock_features"],
        hypothesis,
        cache,
        "search",
        apply_fn=indicator_apply,
        markers_fn=indicator_markers,
        calculation_version=INDICATOR_VERSION,
    )
    assert len(cache) == 2
    assert old_rows[0]["entry_condition"]["version"] != new_rows[0]["entry_condition"]["version"]
    assert new_receipt["feature_recipe"]["version"] == INDICATOR_VERSION
    assert new_receipt["feature_recipe"]["dependency"]["version"] == "2.0.5"
    assert old_receipt["id"] != new_receipt["id"]
    changed = deepcopy(current)
    changed["strategies"][0]["config"].update(target_pct=6, cost_bps=15)
    cached = _gate(
        changed,
        value["automatic_stock_features"],
        hypothesis,
        cache,
        "search",
        apply_fn=indicator_apply,
        markers_fn=indicator_markers,
        calculation_version=INDICATOR_VERSION,
    )
    assert cached == indicator_apply(changed, value["automatic_stock_features"], hypothesis)
    with pytest.raises(ValueError, match="different calculation version"):
        _gate(
            current,
            value["automatic_stock_features"],
            hypothesis,
            OrderedDict(),
            "search",
            apply_fn=legacy_apply,
            markers_fn=indicator_markers,
            calculation_version=INDICATOR_VERSION,
        )


def test_v4_screening_admits_all_relative_hypotheses_and_resume_reuses_completed_work():
    from research.stock_relative_strength import catalog as relative_catalog

    value = prepared(version=BENCHMARK_VERSION)
    engine = Engine(value)
    _, saved = execute(value, engine, stop=lambda state: len(state["trials"]) == 67)
    result, finished = execute(value, engine, saved=saved)
    trials = result["experiment"]["trials"]
    registered = {row["id"]: row for row in relative_catalog()}
    assert len(trials) == 96 and len(registered) == 23
    assert {row["params"]["hypothesis"] for row in trials[:64]} == set(registered)
    assert all(row["phase"] == "family_screening" for row in trials[:64])
    assert all(row["phase"] == "joint_refinement" for row in trials[64:])
    families = {row["family"] for row in registered.values()}
    assert len(families) == 8
    assert all(
        sum(registered[t["params"]["hypothesis"]]["family"] == family for t in trials[:64]) == 8
        for family in families
    )
    assert result["automatic_research"]["version"] == BENCHMARK_VERSION
    assert result["entry_conditions"]["version"] == "research-stock-entry-conditions-v3"
    assert finished["simulations"] == len(engine.calls) <= finished["attempts"] <= 240
    reference_engine = Engine(value)
    reference, _ = execute(value, reference_engine)
    assert reference["experiment"]["trials"] == trials
    assert engine.calls == reference_engine.calls
    assert checkpoint_rows(finished) == result["experiment"]["rows"]
    execute(value, engine, saved=finished)
    assert len(engine.calls) == finished["simulations"]
    for row in result["experiment"]["rows"]:
        descriptor = row["evaluation_basis"]["comparison"]["execution"].get("entry_conditions")
        if row["hypothesis"]["id"] != "none":
            assert descriptor == row["strategies"][0]["entry_condition"]
    assert policy_for_version(EXPANDED_VERSION)["proposals"] == 84
    assert policy_for_version(VERSION)["proposals"] == 60


def test_v4_cached_membership_matches_direct_ratio_receipt_and_checks_calculation_version():
    from research import stock_relative_strength as relative
    from research.automatic_protocol import slice_period

    value = prepared(version=BENCHMARK_VERSION)
    current = slice_period(value, "search")
    selected = next(row for row in relative.catalog() if row["id"] == "relative_20_gt2")
    cache = OrderedDict()
    _, first = _gate(
        current,
        value["automatic_stock_features"],
        selected,
        cache,
        "search",
        apply_fn=relative.apply,
        markers_fn=relative.markers,
        calculation_version=relative.FEATURE_VERSION,
    )
    changed = deepcopy(current)
    changed["strategies"][0]["config"].update(target_pct=6, cost_bps=20)
    cached = _gate(
        changed,
        value["automatic_stock_features"],
        selected,
        cache,
        "search",
        apply_fn=relative.apply,
        markers_fn=relative.markers,
        calculation_version=relative.FEATURE_VERSION,
    )
    assert cached == relative.apply(changed, value["automatic_stock_features"], selected)
    assert cached[1]["id"] != first["id"]
    assert cached[1]["feature_recipe"]["benchmark"] == relative.BENCHMARK_DESCRIPTOR

    def wrong(*args):
        rows, receipt = relative.apply(*args)
        receipt["calculation_version"] = "unqualified-calculation"
        return rows, receipt

    with pytest.raises(ValueError, match="different calculation version"):
        _gate(
            current,
            value["automatic_stock_features"],
            selected,
            OrderedDict(),
            "search",
            apply_fn=wrong,
            markers_fn=relative.markers,
            calculation_version=relative.FEATURE_VERSION,
        )
