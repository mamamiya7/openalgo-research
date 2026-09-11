"""Real Optuna joint proposals, hand-checked objectives and JSON replay."""

import copy
import json
import subprocess
import sys

import pytest

optuna = pytest.importorskip("optuna")

from research.connectors.optuna_portfolio import (
    MAX_CHECKPOINT_BYTES,
    describe_search,
    requirement_strategies,
    run_search,
    validate_specification,
)
from research.engine import config_defaults


@pytest.fixture
def search_request():
    return {
        "strategies": [
            {
                "id": "breakout",
                "name": "Breakout",
                "allocation_pct": 50,
                "config": config_defaults(),
                "signals": [{"symbol": "FIRST", "date": "2026-01-05", "row": 2}],
                "search": {"target_pct": {"min": 2, "max": 6, "step": 2}},
            },
            {
                "id": "momentum",
                "name": "Momentum",
                "allocation_pct": 50,
                "config": {**config_defaults(), "cost_bps": 25, "stop_pct": 2},
                "signals": [{"symbol": "SECOND", "date": "2026-01-05", "row": 2}],
                "search": {"allocation_pct": {"min": 25, "max": 50, "step": 25}},
            },
        ],
        "snapshot": {
            "id": "one-frozen-native-snapshot",
            "bars": {"FIRST": {"2026-01-06": {"close": 100}}},
        },
        "capital": 100000,
        "specification": {"sampler": "grid", "trials": 50, "objective": "balanced", "seed": 0},
        "execution": {
            "engine": "test-shared-cash",
            "engine_version": "1.0",
            "adapter_version": "test-v1",
            "optimizer": "optuna",
        },
    }


def engine(strategies, snapshot, capital, progress=None):
    if progress:
        progress(1, 2)
    settings = [
        {key: strategy[key] for key in ("id", "name", "allocation_pct", "config")}
        for strategy in strategies
    ]
    result = sum(
        strategy["allocation_pct"] * strategy["config"]["target_pct"] / 100
        for strategy in strategies
    )
    drawdown = sum(
        strategy["allocation_pct"] * strategy["config"]["stop_pct"] / 100 for strategy in strategies
    )
    return {
        "strategies": settings,
        "summary": {
            "net_return_pct": result,
            "max_drawdown_pct": drawdown,
            "closed_trades": len(strategies),
        },
        "ledger": [{"strategy_id": strategy["id"], "status": "closed"} for strategy in strategies],
        "equity_curve": [{"date": "2026-01-06", "equity": capital * (1 + result / 100)}],
        "snapshot_id": snapshot["id"],
    }


def test_real_grid_joint_parameters_fixed_fields_and_exact_winner(search_request, monkeypatch):
    created = []
    original = optuna.create_study

    def capture(**kwargs):
        study = original(**kwargs)
        created.append(study)
        return study

    monkeypatch.setattr(optuna, "create_study", capture)
    before = copy.deepcopy(search_request)
    result = run_search(**search_request, evaluate=engine)
    assert search_request == before
    experiment = result["experiment"]
    assert len(experiment["rows"]) == 6
    assert isinstance(created[0].sampler, optuna.samplers.GridSampler)
    assert all(trial.state == optuna.trial.TrialState.COMPLETE for trial in created[0].trials)
    assert set(created[0].trials[0].params) == {"breakout.target_pct", "momentum.allocation_pct"}
    assert experiment["counts"] == {
        "grid": 6,
        "proposed": 6,
        "evaluated_this_pass": 6,
        "evaluated_all_passes": 6,
        "rejected_allocations": 0,
        "reused_trials": 0,
        "remaining": 0,
    }
    assert result["summary"]["net_return_pct"] == 8
    assert result["summary"]["max_drawdown_pct"] == 3.5
    assert experiment["rows"][0]["score"] == 4.5
    assert result["strategies"][0]["config"]["target_pct"] == 6
    assert result["strategies"][1]["allocation_pct"] == 50
    assert result["strategies"][1]["config"]["cost_bps"] == 25
    assert experiment["selected_strategies"] == result["strategies"]
    assert len(experiment["selected_reports"]) == 1
    assert all(
        "signals" not in strategy for row in experiment["rows"] for strategy in row["strategies"]
    )


def test_actual_tpe_adaptive_trials_reproducible_and_repeats_reuse_results(
    search_request, monkeypatch
):
    search_request["specification"].update(sampler="tpe", trials=18)
    created, evaluated = [], []
    original = optuna.create_study

    def capture(**kwargs):
        study = original(**kwargs)
        created.append(study)
        return study

    def record(*args, **kwargs):
        evaluated.append(args[0])
        return engine(*args, **kwargs)

    monkeypatch.setattr(optuna, "create_study", capture)
    first = run_search(**search_request, evaluate=record)
    second = run_search(**search_request, evaluate=engine)
    assert first == second
    assert isinstance(created[0].sampler, optuna.samplers.TPESampler)
    assert len(created[0].trials) == 18
    counts = first["experiment"]["counts"]
    assert len(evaluated) == counts["evaluated_this_pass"] <= 6
    assert counts["reused_trials"] == 18 - len(evaluated)
    assert first["experiment"]["optimizer"]["startup_trials"] == 10
    assert first["experiment"]["trials"][0]["params"] == {
        "breakout.target_pct": 2,
        "momentum.allocation_pct": 25,
    }


@pytest.mark.parametrize("sampler", ["grid", "tpe"])
def test_json_resume_matches_uninterrupted_without_recomputing_completed_trials(
    search_request, sampler
):
    search_request["specification"].update(sampler=sampler, trials=19)
    search_request["strategies"][0]["search"]["allocation_pct"] = {"min": 0, "max": 100, "step": 25}
    checkpoints = []
    complete = run_search(
        **search_request, evaluate=engine, checkpoint=lambda state, _: checkpoints.append(state)
    )
    # Replay beyond startup TPE as well as pruned allocation proposals.
    for index in (0, min(11, len(checkpoints) - 1), len(checkpoints) - 1):
        saved = json.loads(json.dumps(checkpoints[index], allow_nan=False))
        completed_ids = {row["config_id"] for row in saved["rows"]}
        evaluations = []

        def record(*args, evaluations=evaluations, **kwargs):
            evaluations.append(args[0])
            return engine(*args, **kwargs)

        resumed = run_search(**search_request, evaluate=record, saved=saved)
        assert resumed == complete
        assert len(evaluations) == len(complete["experiment"]["rows"]) - len(completed_ids)
    assert all("snapshot" not in state and "signals" not in state for state in checkpoints)
    assert len(checkpoints[0]["trials"]) == 1  # Callback snapshots do not mutate later.


def test_interruption_then_resume_preserves_exact_tpe_sequence(search_request):
    search_request["specification"].update(sampler="tpe", trials=17)
    search_request["strategies"][0]["search"]["target_pct"] = {"min": 1, "max": 50, "step": 1}
    expected = run_search(**search_request, evaluate=engine)
    checkpoints = []

    def cancel(done, total):
        if done >= 12:
            raise InterruptedError("stop requested")

    with pytest.raises(InterruptedError, match="stop requested"):
        run_search(
            **search_request,
            evaluate=engine,
            progress=cancel,
            checkpoint=lambda state, _: checkpoints.append(state),
        )
    assert len(checkpoints[-1]["trials"]) == 12
    actual = run_search(**search_request, evaluate=engine, saved=checkpoints[-1])
    assert actual == expected


def test_invalid_allocations_pruned_without_engine_or_normalization(search_request):
    search_request["strategies"][0]["search"] = {
        "allocation_pct": {"min": 25, "max": 100, "step": 25}
    }
    search_request["strategies"][1]["search"] = {
        "allocation_pct": {"min": 25, "max": 100, "step": 25}
    }
    allocations = []

    def record(strategies, *args, **kwargs):
        values = [strategy["allocation_pct"] for strategy in strategies]
        assert sum(values) <= 100
        allocations.append(values)
        return engine(strategies, *args, **kwargs)

    report = run_search(**search_request, evaluate=record)
    assert report["experiment"]["counts"]["proposed"] == 16
    assert report["experiment"]["counts"]["rejected_allocations"] == 10
    assert len(allocations) == 6
    assert all(all(value in (25, 50, 75, 100) for value in pair) for pair in allocations)


def test_tpe_feasible_first_trial_includes_a_positive_allocation(search_request):
    search_request["specification"].update(sampler="tpe", trials=1)
    for strategy in search_request["strategies"]:
        strategy["allocation_pct"] = 0
        strategy["search"] = {"allocation_pct": {"min": 0, "max": 100, "step": 25}}
    report = run_search(**search_request, evaluate=engine)
    assert [strategy["allocation_pct"] for strategy in report["strategies"]] == [25, 0]
    assert report["experiment"]["counts"]["rejected_allocations"] == 0


def test_no_feasible_sample_has_clear_failure_and_can_checkpoint_pruned_trials(search_request):
    search_request["specification"]["trials"] = 1
    search_request["strategies"][0]["search"] = {
        "allocation_pct": {"min": 25, "max": 100, "step": 25}
    }
    search_request["strategies"][1]["search"] = {
        "allocation_pct": {"min": 50, "max": 100, "step": 25}
    }
    # Find a seed whose one proposal is infeasible, using the real finite grid.
    saved = []
    failed = False
    for seed in range(10):
        search_request["specification"]["seed"] = seed
        try:
            run_search(
                **search_request, evaluate=engine, checkpoint=lambda state, _: saved.append(state)
            )
        except ValueError as error:
            assert "No feasible portfolio was evaluated" in str(error)
            failed = True
            break
    assert failed
    assert saved[-1]["winner_report"] is None
    assert saved[-1]["rows"] == []
    with pytest.raises(ValueError, match="No feasible portfolio was evaluated"):
        run_search(**search_request, evaluate=engine, saved=saved[-1])


def test_requirement_union_covers_maximum_hold_without_sampling_or_input_mutation(search_request):
    first, second = search_request["strategies"]
    first["search"]["hold_sessions"] = {"min": 2, "max": 12, "step": 2}
    second["config"].update(trade_horizon="intraday", hold_minutes=10)
    second["search"]["hold_minutes"] = {"min": 10, "max": 50, "step": 10}
    before = copy.deepcopy(search_request)
    required = requirement_strategies(search_request["strategies"], search_request["specification"])
    assert required[0]["config"]["hold_sessions"] == 12
    assert required[1]["config"]["hold_minutes"] == 50
    assert required[1]["config"]["trade_horizon"] == "intraday"
    assert search_request == before
    description = describe_search(search_request["strategies"], search_request["specification"])
    assert description["axes"]["momentum.hold_minutes"] == {"min": 10, "max": 50, "step": 10}
    assert description["grid_size"] == 3 * 6 * 2 * 5


@pytest.mark.parametrize("objective,score", [("balanced", 4.5), ("return", 8), ("drawdown", -3)])
def test_objective_definitions_are_exact_combined_portfolio_values(
    search_request, objective, score
):
    search_request["specification"]["objective"] = objective
    report = run_search(**search_request, evaluate=engine)
    assert report["experiment"]["rows"][0]["score"] == score
    assert report["experiment"]["optimizer"]["objective_definition"]


@pytest.mark.parametrize("sampler", ["grid", "tpe"])
def test_all_fixed_settings_evaluate_once(search_request, sampler):
    search_request["specification"].update(sampler=sampler, trials=50)
    for strategy in search_request["strategies"]:
        strategy["search"] = {}
    evaluated = []

    def record(*args, **kwargs):
        evaluated.append(args)
        return engine(*args, **kwargs)

    report = run_search(**search_request, evaluate=record)
    assert len(evaluated) == 1
    assert report["experiment"]["counts"]["proposed"] == 1


@pytest.mark.parametrize(
    "change", ["snapshot", "signals", "config", "capital", "specification", "execution"]
)
def test_resume_rejects_changed_inputs_before_engine(search_request, change):
    checkpoints = []
    run_search(
        **search_request, evaluate=engine, checkpoint=lambda state, _: checkpoints.append(state)
    )
    if change == "snapshot":
        search_request["snapshot"]["bars"]["FIRST"]["2026-01-06"]["close"] = 101
    elif change == "signals":
        search_request["strategies"][0]["signals"][0]["row"] = 3
    elif change == "config":
        search_request["strategies"][0]["config"]["cost_bps"] = 12
    elif change == "capital":
        search_request["capital"] = 200000
    elif change == "specification":
        search_request["specification"]["seed"] = 1
    else:
        search_request["execution"]["engine_version"] = "2.0"
    with pytest.raises(ValueError, match="inputs, settings or engine versions changed"):
        run_search(
            **search_request,
            evaluate=lambda *args, **kwargs: pytest.fail("must not evaluate"),
            saved=checkpoints[-1],
        )


@pytest.mark.parametrize("damage", ["params", "summary", "ledger", "extra_row", "trial_count"])
def test_resume_rejects_corrupted_evidence(search_request, damage):
    checkpoints = []
    run_search(
        **search_request, evaluate=engine, checkpoint=lambda state, _: checkpoints.append(state)
    )
    saved = checkpoints[-1]
    if damage == "params":
        saved["trials"][0]["params"]["breakout.target_pct"] = 999
    elif damage == "summary":
        saved["rows"][0]["summary"]["net_return_pct"] += 1
    elif damage == "ledger":
        saved["winner_report"]["ledger"][0]["status"] = "changed"
    elif damage == "extra_row":
        saved["rows"].append(saved["rows"][0])
    else:
        saved["trials"] *= 1001
    with pytest.raises(ValueError):
        run_search(
            **search_request,
            evaluate=lambda *args, **kwargs: pytest.fail("must not evaluate"),
            saved=saved,
        )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), None, True])
def test_invalid_engine_metrics_fail_before_checkpoint(search_request, invalid):
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
    assert not checkpoints


def test_engine_mismatched_settings_and_failure_propagate(search_request):
    def mismatched(*args, **kwargs):
        report = engine(*args, **kwargs)
        report["strategies"] = []
        return report

    with pytest.raises(ValueError, match="requested strategy settings"):
        run_search(**search_request, evaluate=mismatched)

    def failed(*args, **kwargs):
        raise RuntimeError("engine failed")

    with pytest.raises(RuntimeError, match="engine failed"):
        run_search(**search_request, evaluate=failed)


def test_cancellation_prevents_engine_and_storage_failure_propagates(search_request):
    def cancelled(*args):
        raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError, match="cancelled"):
        run_search(
            **search_request,
            evaluate=lambda *args, **kwargs: pytest.fail("must not evaluate"),
            progress=cancelled,
        )

    def no_storage(*args):
        raise OSError("storage full")

    with pytest.raises(OSError, match="storage full"):
        run_search(**search_request, evaluate=engine, checkpoint=no_storage)


@pytest.mark.parametrize(
    "invalid",
    [
        {"sampler": "native"},
        {"objective": "sharpe"},
        {"trials": 0},
        {"trials": 1001},
        {"trials": True},
        {"seed": -1},
        {"unknown": 1},
    ],
)
def test_invalid_specification_fails_before_engine(search_request, invalid):
    search_request["specification"].update(invalid)
    with pytest.raises(ValueError):
        run_search(
            **search_request, evaluate=lambda *args, **kwargs: pytest.fail("must not evaluate")
        )


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "unreachable",
        "noninteger",
        "infeasible",
        "duplicate",
        "zero",
        "inactive_trailing",
        "inactive_hold",
        "too_many",
        "big_grid",
    ],
)
def test_invalid_candidate_unions_fail_before_data_download(search_request, change):
    first, second = search_request["strategies"]
    if change == "unknown":
        first["search"]["cash"] = {"min": 1, "max": 2, "step": 1}
    elif change == "unreachable":
        first["search"]["target_pct"] = {"min": 2, "max": 5, "step": 2}
    elif change == "noninteger":
        first["search"]["hold_sessions"] = {"min": 1, "max": 3, "step": 0.5}
    elif change == "infeasible":
        first["allocation_pct"] = 100
    elif change == "duplicate":
        second["id"] = first["id"]
    elif change == "zero":
        for strategy in search_request["strategies"]:
            strategy.update(allocation_pct=0, search={})
    elif change == "inactive_trailing":
        first["search"]["trailing_pct"] = {"min": 1, "max": 3, "step": 1}
    elif change == "inactive_hold":
        first["search"]["hold_minutes"] = {"min": 5, "max": 15, "step": 5}
    elif change == "too_many":
        first["search"]["target_pct"] = {"min": 1, "max": 500, "step": 0.000001}
    else:
        for strategy in search_request["strategies"]:
            strategy["search"] = {
                "target_pct": {"min": 1, "max": 500, "step": 1},
                "stop_pct": {"min": 1, "max": 99, "step": 1},
            }
    with pytest.raises(ValueError):
        validate_specification(search_request["specification"], search_request["strategies"])


def test_checkpoint_bound_and_version_check(search_request, monkeypatch):
    import research.connectors.optuna_portfolio as adapter

    monkeypatch.setattr(adapter, "MAX_CHECKPOINT_BYTES", 50)
    with pytest.raises(ValueError, match="storage limit"):
        run_search(
            **search_request,
            evaluate=engine,
            checkpoint=lambda *args: pytest.fail("must not save oversized checkpoint"),
        )
    assert MAX_CHECKPOINT_BYTES == 32 * 1024 * 1024
    monkeypatch.setattr(optuna, "__version__", "changed")
    with pytest.raises(ValueError, match="tested Optuna 5.0.0"):
        run_search(**search_request, evaluate=engine)


def test_optional_optuna_not_imported_by_planner_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import research.connectors.optuna_portfolio; assert 'optuna' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_decimal_steps_keep_grid_endpoints_and_allocation_limits_exact(search_request):
    search_request["strategies"][0]["allocation_pct"] = 99.7
    search_request["strategies"][0]["search"] = {
        "target_pct": {"min": 0.1, "max": 0.3, "step": 0.1}
    }
    search_request["strategies"][1]["search"] = {
        "allocation_pct": {"min": 0.1, "max": 0.3, "step": 0.1}
    }
    report = run_search(**search_request, evaluate=engine)
    assert report["experiment"]["counts"]["evaluated_this_pass"] == 9
    assert report["experiment"]["counts"]["rejected_allocations"] == 0
    assert {
        row["strategies"][0]["config"]["target_pct"] for row in report["experiment"]["rows"]
    } == {0.1, 0.2, 0.3}


def test_inactive_session_search_and_invalid_objective_rejected(search_request):
    search_request["strategies"][0]["config"]["trade_horizon"] = "intraday"
    search_request["strategies"][0]["search"] = {"hold_sessions": {"min": 1, "max": 3, "step": 1}}
    with pytest.raises(ValueError, match="unused sessions"):
        validate_specification(search_request["specification"], search_request["strategies"])
    search_request["strategies"][0]["search"] = {}
    search_request["specification"]["objective"] = []
    with pytest.raises(ValueError, match="supported portfolio objective"):
        validate_specification(search_request["specification"], search_request["strategies"])


def test_finite_metrics_that_overflow_objective_are_rejected(search_request):
    def overflowing(*args, **kwargs):
        report = engine(*args, **kwargs)
        report["summary"].update(net_return_pct=-1e308, max_drawdown_pct=1e308)
        return report

    with pytest.raises(ValueError, match="score is non-finite"):
        run_search(**search_request, evaluate=overflowing)


def test_full_scalar_metrics_and_real_timings_survive_resume(search_request):
    checkpoints = []

    def measured(*args, **kwargs):
        report = engine(*args, **kwargs)
        report["analysis"] = {
            "version": "research-analysis-v1",
            "metrics": {"account_sharpe": 1.5, "account_beta": None},
            "unavailable": {"account_beta": "An aligned benchmark is required"},
            "catalog": [{"key": "account_sharpe", "label": "Sharpe ratio"}],
            "charts": [{"id": "equity", "figure": {"data": []}}],
            "basis": ["Daily"],
        }
        return report

    complete = run_search(
        **search_request,
        evaluate=measured,
        record_timing=True,
        checkpoint=lambda state, _: checkpoints.append(state),
    )
    experiment = complete["experiment"]
    assert len(experiment["analysis_catalog"]) == 1
    assert all(row["analysis"]["metrics"]["account_sharpe"] == 1.5 for row in experiment["rows"])
    assert all(
        "charts" not in row["analysis"] and "catalog" not in row["analysis"]
        for row in experiment["rows"]
    )
    assert all(t["datetime_start"] <= t["datetime_complete"] for t in experiment["trials"])
    restored = run_search(
        **search_request,
        evaluate=lambda *a, **k: pytest.fail("Finished trial rerun"),
        record_timing=True,
        saved=checkpoints[-1],
    )
    assert restored == complete


def test_portfolio_capital_is_one_account_and_does_not_mutate_input(search_request):
    search_request["capital"] = 200000
    before = copy.deepcopy(search_request)

    def single_account(strategies, snapshot, capital, **kwargs):
        assert all(strategy["config"]["initial_capital"] == capital for strategy in strategies)
        return engine(strategies, snapshot, capital, **kwargs)

    result = run_search(**search_request, evaluate=single_account)
    assert all(strategy["config"]["initial_capital"] == 200000 for strategy in result["strategies"])
    assert search_request == before


@pytest.mark.timeout(240)
def test_real_vectorbt_joint_search_and_exact_selected_rerun():
    pytest.importorskip("vectorbt")
    from research.connectors.vectorbt_portfolio import evaluate

    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    strategies = [
        {
            "id": identity,
            "name": identity,
            "allocation_pct": 50,
            "config": {
                **config_defaults(),
                "order_size_pct": 100,
                "cost_bps": 0,
                "stop_pct": 50,
                "hold_sessions": 1,
            },
            "signals": [{"symbol": symbol, "date": days[0], "row": 2}],
            "search": {"target_pct": {"min": 5, "max": 10, "step": 5}}
            if identity == "first"
            else {},
        }
        for identity, symbol in [("first", "AAA"), ("second", "BBB")]
    ]
    snapshot = {
        "sessions": days,
        "provenance": {
            "provider": "deterministic-test",
            "exchange": "NSE",
            "interval": "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "explicit-test-sessions",
            "synthetic": True,
        },
        "bars": {
            symbol: {
                day: {
                    "open": price,
                    "high": final if day == days[-1] else price,
                    "low": price,
                    "close": final if day == days[-1] else price,
                }
                for day in days
            }
            for symbol, price, final in [("AAA", 100, 110), ("BBB", 200, 205)]
        },
    }
    report = run_search(
        strategies,
        snapshot,
        1000,
        {"sampler": "grid"},
        evaluate=evaluate,
        execution={"engine": "vectorbt", "engine_version": "0.28.5", "optimizer": "optuna"},
    )
    assert report["summary"]["final_equity"] == pytest.approx(1060)
    assert report["summary"]["closed_trades"] == 2
    assert report["experiment"]["counts"]["evaluated_this_pass"] == 2
    selected = [
        {**strategy, **settings}
        for strategy, settings in zip(
            strategies, report["experiment"]["selected_strategies"], strict=True
        )
    ]
    rerun = evaluate(selected, snapshot, 1000)
    assert (
        rerun == report["experiment"]["selected_reports"][report["experiment"]["recommendation_id"]]
    )
