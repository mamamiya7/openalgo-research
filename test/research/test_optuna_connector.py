"""Real Optuna contract tests with deterministic, hand-checkable engine results."""

import copy
import json
import subprocess
import sys

import pytest

pytest.importorskip("optuna")

from research.connectors.optuna_adapter import run_search, validate_specification
from research.engine import config_defaults
from research.experiments import grid_config, identity


@pytest.fixture
def search_request():
    return {
        "signals": [{"date": "2026-01-05", "symbol": "TEST", "row": 2}],
        "snapshot": {"id": "frozen-test", "bars": {"TEST": {"2026-01-06": {"close": 100}}}},
        "config": config_defaults(),
        "spec": {
            "mode": "exhaustive",
            "axes": {
                "target_pct": {"min": 2, "max": 4, "step": 1},
                "stop_pct": {"min": 2, "max": 3, "step": 1},
            },
        },
        "execution": {
            "engine": "test-engine",
            "engine_version": "1.0",
            "adapter_version": "test-v1",
            "optimizer": "optuna",
        },
    }


def engine(signals, snapshot, config, progress=None):
    if progress:
        progress(1, 2)
    return {
        "config": config,
        "policy_version": "test-engine-v1",
        "metric_basis": "test-marked-equity",
        "summary": {
            "net_return_pct": config["target_pct"] * 3 - config["stop_pct"],
            "max_drawdown_pct": config["stop_pct"],
            "closed_trades": len(signals),
        },
        "equity_curve": [{"date": "2026-01-06", "equity": 100000}],
        "ledger": [{"symbol": "TEST", "status": "closed"}],
        "coverage": {"status": "ready"},
        "limits": [],
    }


def test_real_grid_sampler_covers_union_and_selects_hand_checked_winner(
    search_request, monkeypatch
):
    import optuna

    created = []
    create = optuna.create_study

    def record_study(**kwargs):
        study = create(**kwargs)
        created.append(study)
        return study

    monkeypatch.setattr(optuna, "create_study", record_study)
    report = run_search(**search_request, evaluate=engine)
    experiment = report["experiment"]
    spec = validate_specification(search_request["spec"], search_request["config"])
    expected = {identity(grid_config(i, spec, search_request["config"])) for i in range(6)}
    assert {row["config_id"] for row in experiment["rows"]} == expected
    assert report["config"]["target_pct"] == 4
    assert report["config"]["stop_pct"] == 2
    assert len(created[0].trials) == 6
    assert isinstance(created[0].sampler, optuna.samplers.GridSampler)
    assert experiment["optimizer"]["sampler"] == "GridSampler"
    assert experiment["counts"]["remaining"] == 0
    assert len(experiment["selected_reports"]) == 1
    assert all(trial.state == optuna.trial.TrialState.COMPLETE for trial in created[0].trials)
    assert set(created[0].trials[0].params) == {
        "target_pct",
        "stop_pct",
        "hold_sessions",
        "trailing",
        "modes",
    }


@pytest.mark.parametrize("mode,budget", [("quick", 2), ("full", 4), ("auto", 5)])
def test_partial_search_is_deterministic_bounded_distinct_grid(search_request, mode, budget):
    search_request["spec"].update(mode=mode, budget=budget)
    first = run_search(**search_request, evaluate=engine)
    second = run_search(**search_request, evaluate=engine)
    assert first == second
    experiment = first["experiment"]
    assert len({row["grid_index"] for row in experiment["rows"]}) == budget
    assert experiment["counts"]["remaining"] == 6 - budget
    assert experiment["optimizer"]["requested_mode"] == mode
    assert experiment["follow_up"] is None


def test_composite_trailing_and_trigger_choices_preserve_exact_grid(search_request):
    search_request["spec"] = {
        "mode": "exhaustive",
        "trailing_choices": [
            {"enabled": False, "pct": 0},
            {"enabled": True, "pct": 0},
            {"enabled": True, "pct": 2},
        ],
        "mode_strategies": [["Bypass"], ["Uptick"], ["Zero Only", "Uptick"]],
    }
    spec = validate_specification(search_request["spec"], search_request["config"])
    report = run_search(**search_request, evaluate=engine)
    expected = {identity(grid_config(i, spec, search_request["config"])) for i in range(9)}
    assert {row["config_id"] for row in report["experiment"]["rows"]} == expected


def test_minute_holding_grid_maps_engine_parameters(search_request):
    search_request["config"].update(trade_horizon="intraday", hold_minutes=10)
    search_request["spec"] = {
        "mode": "exhaustive",
        "axes": {"hold_minutes": {"min": 10, "max": 20, "step": 5}},
    }
    report = run_search(**search_request, evaluate=engine)
    assert {row["config"]["hold_minutes"] for row in report["experiment"]["rows"]} == {10, 15, 20}


def test_real_scanner_engine_grid_parity_retains_exact_winning_report(search_request):
    from research.data import fixture_snapshot
    from research.engine import evaluate

    search_request["snapshot"] = fixture_snapshot(search_request["signals"])
    spec = validate_specification(search_request["spec"], search_request["config"])
    expected = [
        evaluate(
            search_request["signals"],
            search_request["snapshot"],
            grid_config(index, spec, search_request["config"]),
        )
        for index in range(6)
    ]
    report = run_search(**search_request, evaluate=evaluate)
    winner = min(
        expected,
        key=lambda item: (
            -(item["summary"]["net_return_pct"] - item["summary"]["max_drawdown_pct"]),
            identity(item["config"]),
        ),
    )
    assert {key: value for key, value in report.items() if key != "experiment"} == winner
    expected_summaries = {identity(item["config"]): item["summary"] for item in expected}
    assert {
        row["config_id"]: row["summary"] for row in report["experiment"]["rows"]
    } == expected_summaries


def test_resume_uses_completed_optuna_trials_and_no_engine_repeats(search_request, monkeypatch):
    import optuna

    saved = []
    calls = []
    restored = []
    add_trial = optuna.study.Study.add_trial

    def observed_restore(study, trial):
        restored.append(trial)
        return add_trial(study, trial)

    monkeypatch.setattr(optuna.study.Study, "add_trial", observed_restore)

    def measured(signals, snapshot, config, **kwargs):
        assert signals is search_request["signals"]
        assert snapshot is search_request["snapshot"]
        calls.append(identity(config))
        return engine(signals, snapshot, config, **kwargs)

    def interrupt(state, counts):
        saved.append(json.loads(json.dumps(state, allow_nan=False)))
        if counts["completed"] == 3:
            raise InterruptedError("worker stopped")

    with pytest.raises(InterruptedError, match="worker stopped"):
        run_search(**search_request, evaluate=measured, checkpoint=interrupt)
    report = run_search(**search_request, evaluate=measured, saved=saved[-1])
    clean = run_search(**search_request, evaluate=engine)
    assert report == clean
    assert len(calls) == len(set(calls)) == 6
    assert len(restored) == 3
    assert all("snapshot" not in state for state in saved)


def test_finished_checkpoint_needs_no_recalculation(search_request):
    saved = []
    first = run_search(
        **search_request,
        evaluate=engine,
        checkpoint=lambda state, _: saved.append(copy.deepcopy(state)),
    )

    def unexpected(*args, **kwargs):
        pytest.fail("A completed trial must not be re-evaluated")

    assert run_search(**search_request, evaluate=unexpected, saved=saved[-1]) == first


@pytest.mark.parametrize("change", ["snapshot", "signals", "config", "spec", "execution"])
def test_resume_rejects_changed_input_or_versions_before_engine(search_request, change):
    saved = []
    run_search(
        **search_request,
        evaluate=engine,
        checkpoint=lambda state, _: saved.append(copy.deepcopy(state)),
    )
    if change == "snapshot":
        search_request[change]["bars"]["TEST"]["2026-01-06"]["close"] = 101
    elif change == "signals":
        search_request[change][0]["row"] = 3
    elif change == "config":
        search_request[change]["cost_bps"] = 50
    elif change == "spec":
        search_request[change]["rank_by"] = "return"
    else:
        search_request[change]["engine_version"] = "2.0"
    with pytest.raises(ValueError, match="inputs, settings or engine versions changed"):
        run_search(
            **search_request,
            evaluate=lambda *args, **kwargs: pytest.fail("must not evaluate"),
            saved=saved[-1],
        )


@pytest.mark.parametrize("damage", ["duplicate", "params", "summary", "winning_ledger"])
def test_resume_rejects_invalid_completed_trial_evidence(search_request, damage):
    saved = []
    run_search(
        **search_request,
        evaluate=engine,
        checkpoint=lambda state, _: saved.append(copy.deepcopy(state)),
    )
    state = saved[-1]
    if damage == "duplicate":
        state["rows"][1] = state["rows"][0]
        state["trials"][1] = state["trials"][0]
    elif damage == "params":
        state["trials"][0]["params"]["target_pct"] = 999
    elif damage == "summary":
        state["rows"][0]["summary"]["net_return_pct"] += 10
    else:
        state["winner_report"]["ledger"][0]["status"] = "changed"
    with pytest.raises(ValueError):
        run_search(
            **search_request,
            evaluate=lambda *args, **kwargs: pytest.fail("must not evaluate"),
            saved=state,
        )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), None, True])
def test_non_finite_or_missing_metrics_fail_and_never_checkpoint(search_request, invalid):
    checkpoints = []

    def invalid_engine(*args, **kwargs):
        report = engine(*args, **kwargs)
        report["summary"]["net_return_pct"] = invalid
        return report

    with pytest.raises(ValueError, match="non-finite"):
        run_search(
            **search_request,
            evaluate=invalid_engine,
            checkpoint=lambda *args: checkpoints.append(args),
        )
    assert checkpoints == []


def test_evaluator_failure_and_progress_cancellation_propagate(search_request):
    def failure(*args, **kwargs):
        raise RuntimeError("engine failure")

    with pytest.raises(RuntimeError, match="engine failure"):
        run_search(**search_request, evaluate=failure)

    def cancel(done, total):
        raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError, match="cancelled"):
        run_search(**search_request, evaluate=engine, progress=cancel)


@pytest.mark.parametrize(
    "update",
    [{"budget": 1}, {"budget": 5001}, {"exclude_indices": [0]}, {"parent_job_id": "a" * 32}],
)
def test_invalid_budget_and_followup_rejected_before_engine(search_request, update):
    search_request["spec"].update(update)
    with pytest.raises(ValueError):
        run_search(
            **search_request, evaluate=lambda *args, **kwargs: pytest.fail("must not evaluate")
        )


def test_optional_package_is_not_imported_at_module_import():
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import research.connectors.optuna_adapter; assert 'optuna' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr
