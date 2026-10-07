"""Report/candidate identity and period boundaries are independent of presentation."""

import copy

import pytest

from research.connectors.optuna_portfolio import _fingerprint
from research.report_contract import present_report, settings_identity


def saved_report():
    strategies = [
        {"id": "a", "name": "Named strategy", "allocation_pct": 100, "config": {"hold_sessions": 2}}
    ]
    identity = settings_identity(strategies)
    return {
        "strategies": strategies,
        "summary": {"net_return_pct": 0},
        "coverage": {"date_from": "2026-01-05", "date_to": "2026-01-12"},
        "analysis": {"version": "research-analysis-v2"},
        "experiment": {
            "rows": [{"config_id": identity, "trial_number": 66}],
            "recommendation_id": identity,
        },
    }


def view(report, digest="b" * 64):
    return present_report(report, job_id="a" * 32, result_artifact=digest, inputs_artifact="c" * 64)


def test_identity_matches_actual_optuna_settings_not_display_names():
    report = saved_report()
    original = copy.deepcopy(report)
    context = view(report)["report_context"]
    assert context["config_id"] == _fingerprint(report["strategies"])
    assert context["candidate"]["trial_number"] == 66
    assert context["candidate"]["is_objective_winner"] is True
    assert report == original
    assert view(report)["report_context"]["report_id"] == context["report_id"]
    assert view(report, "d" * 64)["report_context"]["report_id"] != context["report_id"]
    report["experiment"]["rows"][0]["config_id"] = "other"
    assert "candidate" not in view(report)["report_context"]


def test_selection_later_and_analysis_versions_are_separate_identities():
    report = saved_report()
    later = copy.deepcopy({k: v for k, v in report.items() if k != "experiment"})
    later["coverage"] = {"date_from": "2026-01-13", "date_to": "2026-01-20"}
    report["validation"] = {"result": later}
    response = view(report)
    selection = response["report_context"]
    evaluation = response["validation"]["result"]["report_context"]
    assert selection["period"] == "selection" and evaluation["period"] == "evaluation"
    assert selection["report_id"] != evaluation["report_id"]
    assert evaluation["dates"] == {"from": "2026-01-13", "to": "2026-01-20"}
    assert selection["candidate"] == evaluation["candidate"]
    report["report_context"] = {"analysis_artifact": "e" * 64}
    refreshed = view(report)["report_context"]
    assert refreshed["report_id"] == selection["report_id"]
    assert refreshed["analysis_artifact"] == "e" * 64


def test_old_missing_evidence_does_not_invent_candidate_or_dates():
    context = view({"summary": {"closed_trades": 0}})["report_context"]
    assert context["config_id"] is None and "candidate" not in context
    assert context["dates"] == {"from": None, "to": None}
    assert context["period_label"] == "Full period"
    assert context["evaluation_basis"] == {
        "version": "research-evaluation-basis-v1",
        "status": "unverified",
    }


def test_saved_basis_is_presented_without_rebuilding_or_changing_original_evidence():
    report = saved_report()
    report["evaluation_basis"] = {
        "version": "research-evaluation-basis-v1",
        "status": "verified",
        "evidence_id": "f" * 64,
        "comparison": {"id": "e" * 64},
    }
    original = copy.deepcopy(report)
    response = view(report)
    assert response["report_context"]["evaluation_basis"] == report["evaluation_basis"]
    assert report == original


@pytest.mark.parametrize("winner", [False, True])
def test_candidate_replay_keeps_original_trial_and_immediate_parent(winner):
    report = saved_report()
    report.pop("experiment")
    report["replay_origin"] = {
        "parent_job_id": "d" * 32,
        "study_job_id": "e" * 32,
        "trial_number": 99,
        "config_id": settings_identity(report["strategies"]),
        "is_objective_winner": winner,
        "period": "evaluation",
    }
    context = view(report)["report_context"]
    assert context["parent_job_id"] == "d" * 32
    assert context["candidate"]["study_job_id"] == "e" * 32
    assert context["candidate"]["trial_number"] == 99
    assert context["candidate"]["is_objective_winner"] is winner
    assert context["period"] == "evaluation"
