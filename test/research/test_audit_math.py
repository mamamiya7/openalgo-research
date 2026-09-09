"""Durable independent regressions for audit A2/A3/A4/A8 (2026-09-06)."""

import copy
from datetime import date, timedelta
from itertools import permutations

import pytest

from research.data import cutoff_snapshot
from research.engine import POLICY_VERSION, TRIGGER_WARMUP_SESSIONS, evaluate, validate_config
from research.experiments import (
    EXPERIMENT_VERSION,
    STRATEGIES,
    grid_config,
    run_research,
    run_search,
    search_preflight,
    validate_research,
    validate_search,
)


def scenario(counts, high=102.5):
    sessions = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(counts) + 2)]
    signals = [
        {"symbol": f"S{n:02}", "date": sessions[i], "row": i * 100 + n + 2}
        for i, count in enumerate(counts)
        for n in range(count)
    ]
    bars = {
        s["symbol"]: {
            day: {"open": 100, "high": high, "low": 99.9, "close": 100} for day in sessions
        }
        for s in signals
    }
    return signals, {
        "sessions": sessions,
        "bars": bars,
        "provenance": {
            "provider": "independent-audit-fixture",
            "exchange": "NSE",
            "interval": "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "explicit-test-sessions",
            "synthetic": True,
        },
    }


@pytest.mark.parametrize("modes", STRATEGIES)
@pytest.mark.parametrize("prior", [0, 2])
@pytest.mark.parametrize("gap,folds", [(0, 1), (1, 2)])
def test_a2_window_eligibility_matches_full_causal_history(modes, prior, gap, folds):
    signals, snapshot = scenario(([6] * 7 + [prior, 3, 3, 0, 3]) * 3)
    config = validate_config({"modes": modes, "hold_sessions": 1, "cost_bps": 0})
    days = snapshot["sessions"]
    spec = validate_research(
        {
            "train_end": days[7],
            "test_end": days[34],
            "gap_sessions": gap,
            "scheme": "holdout" if folds == 1 else "walk_forward",
            "folds": folds,
            "variants": [{"name": "Same fees", "changes": {"cost_bps": 0}}],
        },
        config,
        {"signals": signals, "snapshot": snapshot},
    )
    original = copy.deepcopy(snapshot)
    actual = run_research(signals, snapshot, config, spec)
    for fold in actual["experiment"]["folds"]:
        window = fold["window"]
        candidates = [s for s in signals if window["test_from"] <= s["date"] <= window["test_end"]]
        full = cutoff_snapshot(snapshot, window["test_end"])
        expected = evaluate(candidates, full, config, signal_history=signals)
        assert fold["test_report"]["ledger"] == expected["ledger"]
        assert fold["test_report"]["summary"] == expected["summary"]
    assert snapshot == original
    assert actual["experiment"]["trigger_warmup_sessions"] == TRIGGER_WARMUP_SESSIONS == 8
    if modes == ["Uptick"] and prior == 2 and gap == 0:
        first = actual["experiment"]["folds"][0]["test_report"]["ledger"]
        assert (
            sum(bool(row["trigger_modes"]) for row in first if row["signal_date"] == days[8]) == 3
        )


def test_a2_count_warmup_requires_no_warmup_stock_candles():
    signals, snapshot = scenario([6] * 7 + [2, 3, 3, 3])
    days = snapshot["sessions"]
    for symbol in snapshot["bars"]:
        snapshot["bars"][symbol] = {
            day: bar for day, bar in snapshot["bars"][symbol].items() if day >= days[9]
        }
    cfg = validate_config({"modes": ["Uptick"], "hold_sessions": 1, "cost_bps": 0})
    spec = validate_research(
        {"train_end": days[7], "test_end": days[10], "gap_sessions": 0},
        cfg,
        {"signals": signals, "snapshot": snapshot},
    )
    report = run_research(signals, snapshot, cfg, spec)["experiment"]["folds"][0]["test_report"]
    assert report["summary"]["accepted_trades"] == 3


def first_search(high=102.5, target_low=1, target_high=3, budget=1):
    signals, snapshot = scenario([1] * 40, high)
    cfg = validate_config({"hold_sessions": 1, "order_size_pct": 5})
    spec = validate_search(
        {
            "mode": "quick",
            "budget": budget,
            "axes": {"target_pct": {"min": target_low, "max": target_high, "step": 1}},
        },
        cfg,
    )
    return signals, snapshot, cfg, run_search(signals, snapshot, cfg, spec)


def follow(signals, snapshot, cfg, parent, **kwargs):
    return run_search(
        signals,
        snapshot,
        cfg,
        parent["experiment"]["follow_up"],
        parent_rows=parent["experiment"]["rows"],
        parent_reports=parent["experiment"]["selected_reports"],
        **kwargs,
    )


@pytest.mark.parametrize("high,low,upper", [(102.5, 1, 3), (104, 1, 3), (101, 5, 7)])
def test_a3_better_worse_tied_followups_keep_cumulative_incumbent_and_all_rows(high, low, upper):
    signals, snapshot, cfg, first = first_search(high, low, upper)
    original = copy.deepcopy(first)
    second = follow(signals, snapshot, cfg, first)
    third = follow(signals, snapshot, cfg, second)
    for pass_number, report in enumerate((first, second, third), 1):
        experiment = report["experiment"]
        rows = experiment["rows"]
        assert len(rows) == len({r["config_id"] for r in rows}) == pass_number
        assert experiment["counts"]["evaluated_this_pass"] == 1
        assert experiment["counts"]["evaluated_all_passes"] == pass_number
        assert experiment["counts"]["stored_rows"] == pass_number
        assert experiment["counts"]["stored_pass_rows"] == 1
        assert (
            experiment["recommendation_id"]
            == min(rows, key=lambda row: (-row["score"], row["config_id"]))["config_id"]
        )
        assert (
            experiment["follow_up"] is not None
            if pass_number < 3
            else experiment["follow_up"] is None
        )
    assert first == original
    if high == 102.5:
        assert second["experiment"]["recommendation_id"] == first["experiment"]["recommendation_id"]
        assert second["summary"]["net_return_pct"] == pytest.approx(3.621172)
        assert second["experiment"]["pass_rows"][0]["summary"]["net_return_pct"] == pytest.approx(
            -0.392
        )
        assert second["experiment"]["state"] == "final_useful_result"
        for size, report in enumerate((first, second, third), 1):
            winner = report["experiment"]["recommendation_id"]
            neighbors = next(
                row for row in report["experiment"]["neighborhoods"] if row["config_id"] == winner
            )
            assert neighbors["tested_count"] == size
    if high == 104:
        assert second["summary"]["net_return_pct"] > first["summary"]["net_return_pct"]


def test_a3_never_recalculates_parent_metrics_or_selected_reports(monkeypatch):
    import research.experiments as experiments

    signals, snapshot, cfg, parent = first_search()
    previous = {row["config_id"] for row in parent["experiment"]["rows"]}
    original = experiments.evaluate

    def measured(*args, **kwargs):
        assert experiments.identity(args[2]) not in previous
        return original(*args, **kwargs)

    monkeypatch.setattr(experiments, "evaluate", measured)
    result = follow(signals, snapshot, cfg, parent)
    selected = parent["experiment"]["recommendation_id"]
    assert (
        result["experiment"]["selected_reports"][selected]
        is parent["experiment"]["selected_reports"][selected]
    )
    assert (
        next(row for row in result["experiment"]["rows"] if row["config_id"] == selected)
        == parent["experiment"]["rows"][0]
    )


def test_a3_followup_resume_preserves_union_and_parent_fingerprint():
    signals, snapshot, cfg, parent = first_search(target_high=12, budget=6)
    checkpoints = []

    def checkpoint(state, counts):
        checkpoints.append(copy.deepcopy(state))
        if counts["completed"] == 5:
            raise InterruptedError("checkpoint")

    with pytest.raises(InterruptedError):
        follow(signals, snapshot, cfg, parent, checkpoint=checkpoint)
    state = checkpoints[-1]
    assert state["experiment_version"] == EXPERIMENT_VERSION
    resumed = follow(signals, snapshot, cfg, parent, saved=state)
    clean = follow(signals, snapshot, cfg, parent)
    assert resumed == clean
    assert clean["experiment"]["counts"]["evaluated_all_passes"] == 12
    bad = {**state, "parent_evidence_id": "changed"}
    with pytest.raises(ValueError, match="parent evidence"):
        follow(signals, snapshot, cfg, parent, saved=bad)


def test_a3_followup_requires_complete_consistent_rows_and_saved_incumbent_report():
    signals, snapshot, cfg, parent = first_search()
    spec = parent["experiment"]["follow_up"]
    with pytest.raises(ValueError, match="complete verified parent"):
        run_search(signals, snapshot, cfg, spec)
    with pytest.raises(ValueError, match="exact verified parent report"):
        run_search(signals, snapshot, cfg, spec, parent_rows=parent["experiment"]["rows"])
    result = run_search(
        signals,
        snapshot,
        cfg,
        spec,
        parent_rows=parent["experiment"]["rows"] * 2,
        parent_reports=parent["experiment"]["selected_reports"],
    )
    assert result["experiment"]["counts"]["evaluated_all_passes"] == 2
    altered = copy.deepcopy(parent["experiment"]["rows"][0])
    altered["stage"] = "changed evidence"
    with pytest.raises(ValueError, match="Conflicting parent"):
        run_search(
            signals, snapshot, cfg, spec, parent_rows=[*parent["experiment"]["rows"], altered]
        )


def test_a4_mode_only_grid_never_claims_numeric_neighbors_and_is_permutation_invariant():
    signals, snapshot = scenario(([6] * 7 + [2, 3, 0, 3]) * 5)
    cfg = validate_config({"hold_sessions": 1, "order_size_pct": 1, "target_pct": 2})
    baseline = None
    for strategies in permutations([["Bypass"], ["Bottom Fishing"], ["Uptick"]]):
        spec = validate_search({"mode": "exhaustive", "mode_strategies": list(strategies)}, cfg)
        result = run_search(signals, snapshot, cfg, spec)
        if baseline is None:
            baseline = result
        assert result == baseline
        assert all(
            n["tested_count"] == 1 and n["finding"] == "Needs more neighboring samples"
            for n in result["experiment"]["neighborhoods"]
        )
        assert all(
            len(c["compared_config_ids"]) == 2
            for c in result["experiment"]["categorical_comparisons"]
        )


def test_a4_focusing_and_trailing_categories_are_order_independent():
    signals, snapshot = scenario(([6] * 7 + [2, 3, 0, 3]) * 3)
    cfg = validate_config({"hold_sessions": 1, "cost_bps": 0})
    choices = [
        {"enabled": False, "pct": 0},
        {"enabled": True, "pct": 0},
        {"enabled": True, "pct": 1},
    ]
    baseline = None
    for i, strategies in enumerate(permutations([["Bypass"], ["Bottom Fishing"], ["Uptick"]])):
        spec = validate_search(
            {
                "mode": "auto",
                "budget": 12,
                "mode_strategies": list(strategies),
                "axes": {"target_pct": {"min": 1, "max": 6, "step": 1}},
                "trailing_choices": choices if i % 2 else choices[::-1],
            },
            cfg,
        )
        report = run_search(signals, snapshot, cfg, spec)
        if baseline is None:
            baseline = report
        assert report == baseline
        rows = {r["config_id"]: r for r in report["experiment"]["rows"]}
        for neighborhood in report["experiment"]["neighborhoods"]:
            anchor = rows[neighborhood["config_id"]]["config"]
            for identifier in neighborhood["neighbor_config_ids"]:
                neighbor = rows[identifier]["config"]
                assert anchor["modes"] == neighbor["modes"]
                assert anchor["trailing_enabled"] == neighbor["trailing_enabled"]


@pytest.mark.parametrize("enabled,pct", [(False, 0), (False, 5), (True, 0), (True, 5)])
@pytest.mark.parametrize("include_off", [False, True])
@pytest.mark.parametrize("explicit_axis", [False, True])
def test_a8_baseline_trailing_state_values_and_axes_are_explicit(
    enabled, pct, include_off, explicit_axis
):
    cfg = validate_config({"trailing_enabled": enabled, "trailing_pct": pct})
    raw = {"mode": "exhaustive", "include_trailing_off": include_off}
    if explicit_axis:
        raw["axes"] = {"trailing_pct": {"min": pct, "max": pct, "step": 1}}
    expected_enabled = (pct != 0 or enabled) if explicit_axis else enabled
    if not include_off and not expected_enabled:
        with pytest.raises(ValueError, match="contradicts"):
            validate_search(raw, cfg)
        return
    spec = validate_search(raw, cfg)
    assert validate_search(spec, cfg) == spec
    preflight = search_preflight(spec, cfg)
    configs = [grid_config(i, spec, cfg) for i in range(preflight["grid_count"])]
    actual = {(row["trailing_enabled"], row["trailing_pct"]) for row in configs}
    expected = {(expected_enabled, pct)} | ({(False, 0)} if include_off else set())
    assert actual == expected
    assert preflight["experiment_version"] == EXPERIMENT_VERSION
    assert preflight["policy_version"] == POLICY_VERSION


def test_a8_disabled_positive_identity_does_not_inflate_numeric_evidence():
    signals, snapshot = scenario([1] * 40)
    cfg = validate_config({"trailing_enabled": False, "trailing_pct": 5})
    spec = validate_search({"mode": "exhaustive"}, cfg)
    report = run_search(signals, snapshot, cfg, spec)
    assert report["experiment"]["counts"]["stored_rows"] == 2
    assert all(n["tested_count"] == 1 for n in report["experiment"]["neighborhoods"])


def test_a8_enabled_zero_without_off_replays_exact_zero_stop_behavior():
    signals, snapshot = scenario([1] * 3)
    cfg = validate_config({"trailing_enabled": True, "trailing_pct": 0})
    spec = validate_search({"mode": "exhaustive", "include_trailing_off": False}, cfg)
    report = run_search(signals, snapshot, cfg, spec)
    assert report["config"] == cfg
    assert all(
        row["outcome"] == "trailing_stop" and row["exit_timing"] == "open"
        for row in report["ledger"]
    )


def test_old_search_version_and_old_research_checkpoint_cannot_resume_new_semantics():
    signals, snapshot = scenario([1] * 40)
    cfg = validate_config({})
    with pytest.raises(ValueError, match="version changed"):
        validate_search({"experiment_version": "scanner-experiments-v1"}, cfg)
    spec = validate_research(
        {"train_end": snapshot["sessions"][10], "test_end": snapshot["sessions"][30]},
        cfg,
        {"signals": signals, "snapshot": snapshot},
    )
    with pytest.raises(ValueError, match="version changed"):
        run_research(signals, snapshot, cfg, spec, saved={"records": {}, "folds": []})


def test_earlier_training_does_not_import_full_sample_search_exclusions():
    signals, snapshot = scenario([1] * 40)
    cfg = validate_config({})
    with pytest.raises(ValueError, match="fresh training search"):
        validate_research(
            {
                "intent": "select_earlier",
                "train_end": snapshot["sessions"][10],
                "test_end": snapshot["sessions"][30],
                "search": {
                    "mode": "quick",
                    "budget": 1,
                    "exclude_indices": [0],
                    "axes": {"target_pct": {"min": 1, "max": 3, "step": 1}},
                },
            },
            cfg,
            {"signals": signals, "snapshot": snapshot},
        )
