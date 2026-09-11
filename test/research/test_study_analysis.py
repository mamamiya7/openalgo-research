"""Native Optuna plots from saved evidence, without resampling or simulation."""

import copy
import json

import pytest

pytest.importorskip("optuna")
from research.study_analysis import build_study_analysis, reconstruct_study


def experiment(*, timed=False):
    trials = []
    for i in range(12):
        row = {
            "number": i,
            "state": "complete" if i != 11 else "pruned",
            "params": {"a.target_pct": float(1 + i % 3), "a.hold_sessions": 1 + i // 3},
            "value": float(i * i - 2 * i) if i != 11 else None,
            "config_id": str(i),
            "reused": i == 10,
        }
        if timed:
            row.update(
                datetime_start=f"2026-09-11T10:{i:02d}:00",
                datetime_complete=f"2026-09-11T10:{i:02d}:01",
            )
        trials.append(row)
    return {
        "trials": trials,
        "search_space": {
            "axes": {
                "a.target_pct": {"min": 1, "max": 3, "step": 1},
                "a.hold_sessions": {"min": 1, "max": 4, "step": 1},
            },
            "objective_definition": "Net return less drawdown",
        },
    }


def test_native_plots_keep_all_proposals_and_do_not_optimize(monkeypatch):
    import optuna

    original = experiment(timed=True)
    before = copy.deepcopy(original)
    monkeypatch.setattr(
        optuna.study.Study, "optimize", lambda *a, **k: pytest.fail("Analysis ran optimizer")
    )
    study, _ = reconstruct_study(original)
    assert len(study.trials) == 12
    assert study.trials[10].user_attrs["reused"] is True
    assert study.trials[11].state == optuna.trial.TrialState.PRUNED
    assert study.trials[0].datetime_start.isoformat() == original["trials"][0]["datetime_start"]
    result = build_study_analysis(original)
    charts = {chart["id"]: chart for chart in result["charts"]}
    assert len(charts) == 12
    for key in ("history", "importance", "slice", "contour", "parallel", "rank", "edf", "timeline"):
        assert charts[key]["status"] == "available", charts[key]
        assert charts[key]["figure"]["data"]
    for key in ("intermediate", "pareto", "hypervolume", "terminator"):
        assert charts[key]["status"] == "unavailable"
    assert original == before
    json.dumps(result, allow_nan=False)


def test_older_study_never_displays_invented_dates():
    result = build_study_analysis(experiment())
    timeline = next(c for c in result["charts"] if c["id"] == "timeline")
    assert timeline["status"] == "unavailable"
    assert "figure" not in timeline
    assert "1970" not in json.dumps(result)


def test_sparse_study_explains_native_prerequisites():
    original = experiment()
    original["trials"] = original["trials"][:1]
    result = build_study_analysis(original, parameters=["a.target_pct"])
    charts = {c["id"]: c for c in result["charts"]}
    assert charts["history"]["status"] == "available"
    assert charts["importance"]["status"] == "unavailable"
    assert charts["contour"]["status"] == "unavailable"
    assert result["parameters"] == ["a.target_pct"]


@pytest.mark.parametrize("parameters", [["unknown"], ["a.target_pct"] * 2])
def test_parameter_selection_must_be_recorded(parameters):
    with pytest.raises(ValueError, match="Choose up to two"):
        build_study_analysis(experiment(), parameters=parameters)


def test_tampered_proposal_is_rejected():
    original = experiment()
    original["trials"][3]["value"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        reconstruct_study(original)


def test_analysis_checks_cancellation_between_native_charts():
    class Cancelled(Exception):
        pass

    def stop(done, total):
        if done == 2:
            raise Cancelled

    with pytest.raises(Cancelled):
        build_study_analysis(experiment(), progress=stop)
