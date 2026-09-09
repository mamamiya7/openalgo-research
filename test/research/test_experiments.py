import copy
import json

import pytest

from research.data import fixture_snapshot
from research.engine import config_defaults
from research.experiments import (
    STRATEGIES,
    axis_values,
    grid_config,
    run_research,
    run_search,
    run_sensitivity,
    search_preflight,
    validate_research,
    validate_search,
    validate_variants,
)


@pytest.fixture
def evidence():
    from datetime import date, timedelta

    days = []
    day = date(2026, 1, 5)
    while len(days) < 70:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    signals = [{"date": day, "symbol": "TEST", "row": i + 2} for i, day in enumerate(days)]
    return {"signals": signals, "snapshot": fixture_snapshot(signals)}


def search(config, **changes):
    return validate_search(
        {
            "mode": "exhaustive",
            "axes": {
                "target_pct": {"min": 2, "max": 4, "step": 1},
                "stop_pct": {"min": 2, "max": 3, "step": 1},
            },
            **changes,
        },
        config,
    )


def test_grid_exact_eight_modes_and_off_baseline():
    cfg = config_defaults()
    spec = search(
        cfg, axes={"trailing_pct": {"min": 1, "max": 2, "step": 1}}, mode_strategies=STRATEGIES
    )
    preview = search_preflight(spec, cfg)
    assert preview["grid_count"] == 24
    configs = [grid_config(i, spec, cfg) for i in range(24)]
    assert len({json.dumps(c, sort_keys=True) for c in configs}) == 24
    assert sum(not c["trailing_enabled"] for c in configs) == 8


@pytest.mark.parametrize(
    "axis",
    [
        {"min": 1, "max": 2, "step": 0},
        {"min": 2, "max": 1, "step": 1},
        {"min": 0, "max": 10000000, "step": 0.001},
        {"min": "NaN", "max": 1, "step": 1},
    ],
)
def test_bad_axes_rejected_before_materialization(axis):
    with pytest.raises(ValueError):
        axis_values(axis)


def test_exhaustive_cannot_be_partial_or_duplicate_bypass():
    with pytest.raises(ValueError, match="Exhaustive"):
        search(config_defaults(), budget=1)
    with pytest.raises(ValueError, match="Duplicate"):
        search(config_defaults(), mode_strategies=[["Bypass"], ["Bypass", "Uptick"]])


def test_search_checkpoint_resumes_exact_next_unrecorded_work(evidence):
    cfg = config_defaults()
    spec = search(cfg)
    saved = []

    def checkpoint(state, counts):
        saved.append(copy.deepcopy(state))
        if counts["completed"] == 5:
            raise InterruptedError("power loss")

    with pytest.raises(InterruptedError):
        run_search(evidence["signals"], evidence["snapshot"], cfg, spec, checkpoint=checkpoint)
    resumed = run_search(evidence["signals"], evidence["snapshot"], cfg, spec, saved=saved[-1])
    clean = run_search(evidence["signals"], evidence["snapshot"], cfg, spec)
    assert resumed == clean
    rows = resumed["experiment"]["rows"]
    assert len(rows) == len({row["config_id"] for row in rows}) == 6
    recommendation = resumed["experiment"]["recommendation_id"]
    assert resumed["config"] == next(
        row["config"] for row in rows if row["config_id"] == recommendation
    )


def test_auto_search_finishes_distinct_focus_and_full_denominators(evidence):
    cfg = config_defaults()
    spec = search(cfg, mode="auto", budget=5)
    report = run_search(evidence["signals"], evidence["snapshot"], cfg, spec)
    investigation = report["experiment"]
    assert investigation["counts"]["evaluated_this_pass"] == 5
    assert investigation["counts"]["broad"] == 2
    assert len({r["grid_index"] for r in investigation["rows"]}) == 5
    assert investigation["state"].startswith("final_")
    assert all(n["tested_count"] <= 5 for n in investigation["neighborhoods"])


def research_spec(evidence, **changes):
    sessions = evidence["snapshot"]["sessions"]
    return validate_research(
        {
            "train_end": sessions[30],
            "test_end": sessions[65],
            "gap_sessions": 2,
            "variants": [{"name": "More fees", "changes": {"cost_bps": 50}}],
            **changes,
        },
        config_defaults(),
        evidence,
    )


def test_future_mutation_cannot_change_earlier_rank_or_choice(evidence):
    cfg = config_defaults()
    spec = research_spec(evidence, intent="select_earlier", min_train_closed=1, search=search(cfg))
    first = run_research(evidence["signals"], evidence["snapshot"], cfg, spec)
    mutated = copy.deepcopy(evidence)
    for series in mutated["snapshot"]["bars"].values():
        for day, bar in series.items():
            if day > spec["train_end"]:
                for key in ("open", "high", "low", "close"):
                    bar[key] *= 7
    mutated["signals"].append({"date": spec["test_end"], "symbol": "FUTURE", "row": 999})
    second = run_research(mutated["signals"], mutated["snapshot"], cfg, spec)
    assert (
        first["experiment"]["folds"][0]["training_ranks"]
        == second["experiment"]["folds"][0]["training_ranks"]
    )
    assert (
        first["experiment"]["folds"][0]["selected_config"]
        == second["experiment"]["folds"][0]["selected_config"]
    )


def test_fixed_setup_ignores_training_eligibility_and_folds_are_disjoint(evidence):
    cfg = config_defaults()
    spec = research_spec(evidence, min_train_closed=25000, scheme="walk_forward", folds=3)
    report = run_research(evidence["signals"], evidence["snapshot"], cfg, spec)
    folds = report["experiment"]["folds"]
    assert all(fold["selected_config"] == cfg and fold["test_report"] for fold in folds)
    for first, second in zip(folds, folds[1:], strict=False):
        assert first["window"]["test_end"] < second["window"]["test_from"]
    assert all(
        f["test_report"]["summary"]["initial_capital"] == cfg["initial_capital"] for f in folds
    )
    assert report["equity_curve"] == []


def test_no_eligible_training_never_invents_winner(evidence):
    cfg = config_defaults()
    spec = research_spec(
        evidence, intent="select_earlier", min_train_closed=25000, search=search(cfg)
    )
    report = run_research(evidence["signals"], evidence["snapshot"], cfg, spec)
    assert report["experiment"]["folds"][0]["selected_config"] is None
    assert report["experiment"]["folds"][0]["test_report"] is None


def test_sensitivity_preserves_supplied_baseline(evidence):
    cfg = config_defaults()
    spec = {"variants": validate_variants([], cfg)}
    report = run_sensitivity(evidence["signals"], evidence["snapshot"], cfg, spec)
    assert report["config"] == cfg
    assert len(report["experiment"]["variants"]) == 3
    assert "recommendation_id" not in report["experiment"]


def test_research_checkpoint_resume_matches_clean(evidence):
    spec = research_spec(evidence, scheme="walk_forward", folds=2)
    checkpoints = []

    def checkpoint(state, counts):
        checkpoints.append(copy.deepcopy(state))
        if state["folds"]:
            raise InterruptedError

    with pytest.raises(InterruptedError):
        run_research(
            evidence["signals"],
            evidence["snapshot"],
            config_defaults(),
            spec,
            checkpoint=checkpoint,
        )
    resumed = run_research(
        evidence["signals"], evidence["snapshot"], config_defaults(), spec, saved=checkpoints[-1]
    )
    assert resumed == run_research(
        evidence["signals"], evidence["snapshot"], config_defaults(), spec
    )


@pytest.mark.parametrize("parent", [None, True, 12, "", "not-a-job", "a" * 64])
def test_search_rejects_malformed_parent_identity(parent):
    with pytest.raises(ValueError, match="parent_job_id"):
        search(config_defaults(), parent_job_id=parent)


def test_search_parent_identity_canonical_and_stable():
    cfg = config_defaults()
    spec = search(cfg, parent_job_id="01234567-89AB-CDEF-0123-456789ABCDEF")
    assert spec["parent_job_id"] == "0123456789abcdef0123456789abcdef"
    assert validate_search(spec, cfg) == spec


def test_sensitivity_baseline_progress_allows_immediate_cancellation(evidence):
    calls = []

    def cancel(done, total):
        calls.append((done, total))
        raise InterruptedError("cancel during baseline")

    with pytest.raises(InterruptedError, match="baseline"):
        run_sensitivity(
            evidence["signals"],
            evidence["snapshot"],
            config_defaults(),
            {"variants": validate_variants([], config_defaults())},
            progress=cancel,
        )
    assert calls == [(0.0, 4)]


def test_sensitivity_progress_counts_baseline_and_variants(evidence):
    calls = []
    run_sensitivity(
        evidence["signals"],
        evidence["snapshot"],
        config_defaults(),
        {"variants": validate_variants([], config_defaults())},
        progress=lambda done, total: calls.append((done, total)),
    )
    assert calls[-1] == (4, 4)
    assert all(total == 4 for _, total in calls)
    assert [done for done, _ in calls] == sorted(done for done, _ in calls)


def test_earlier_auto_selection_uses_same_adaptive_planner_and_resumes(evidence):
    from research.data import cutoff_snapshot

    cfg = config_defaults()
    search_spec = search(
        cfg, mode="auto", budget=12, axes={"target_pct": {"min": 1, "max": 30, "step": 1}}
    )
    spec = research_spec(evidence, intent="select_earlier", min_train_closed=1, search=search_spec)
    earlier = [signal for signal in evidence["signals"] if signal["date"] <= spec["train_end"]]
    prices = cutoff_snapshot(evidence["snapshot"], spec["train_end"])
    standalone = run_search(earlier, prices, cfg, search_spec)
    clean = run_research(evidence["signals"], evidence["snapshot"], cfg, spec)
    ranks = clean["experiment"]["folds"][0]["training_ranks"]
    assert ranks == standalone["experiment"]["rows"]
    assert sum(row["stage"] == "broad" for row in ranks) == 6
    assert sum(row["stage"] == "focused" for row in ranks) == 6
    from research.experiments import evenly

    assert sorted(row["grid_index"] for row in ranks) != evenly(list(range(30)), 12)
    saved = []

    def interrupt(state, counts):
        saved.append(copy.deepcopy(state))
        if counts["completed"] == 10:
            raise InterruptedError("during focused training")

    with pytest.raises(InterruptedError):
        run_research(evidence["signals"], evidence["snapshot"], cfg, spec, checkpoint=interrupt)
    resumed = run_research(evidence["signals"], evidence["snapshot"], cfg, spec, saved=saved[-1])
    assert resumed == clean
    # Later prices and future-only signals cannot choose the focused training region.
    changed = copy.deepcopy(evidence)
    for series in changed["snapshot"]["bars"].values():
        for day, bar in series.items():
            if day > spec["train_end"]:
                for key in bar:
                    bar[key] *= 3
    changed["signals"].append({"symbol": "FUTURE", "date": spec["test_end"], "row": 1000})
    replay = run_research(changed["signals"], changed["snapshot"], cfg, spec)
    assert replay["experiment"]["folds"][0]["training_ranks"] == ranks


def test_non_session_signals_do_not_create_possible_entries(evidence):
    invalid = copy.deepcopy(evidence)
    invalid["signals"] = [
        evidence["signals"][0],
        {"date": "2026-02-14", "symbol": "TEST", "row": 3},
    ]
    with pytest.raises(ValueError, match="no possible next-session entries"):
        research_spec(invalid)
