"""Structured failure hypotheses stay bound to exact checked configurations."""

from copy import deepcopy

import pytest

from services import research_failure_diagnosis as diagnosis
from services.agent import research_decision as decision


def evidence(*, funding=False, blocked=False):
    remedy = "funding_and_size" if funding else "lower_turnover"
    card = {
        "checks": [
            {"key": "higher_costs", "passed": False},
            {"key": "neighboring_settings", "passed": False},
            {"key": "sample_and_distribution", "passed": True},
            {"key": "operating_capacity", "passed": False},
        ]
    }
    if blocked:
        card = {
            "checks": [],
            "feasibility": {
                "version": "research-feasibility-v1",
                "scope": "feasibility_only",
                "unresolved_constraints": {"cash_shortfall": 3},
            },
        }
    value = {
        "failure_diagnosis_version": diagnosis.VERSION,
        "diagnosis": {"permitted_remedies": [remedy]},
        "experiment_options": [
            {
                "id": "candidate",
                "remedy": remedy,
                "origin_job_id": "source",
                "origin_result_artifact": "artifact-source",
                "change": {"axis": "native_funding_and_size" if funding else "trailing_choice"},
            }
        ],
        "experiment_frontier": {
            "version": "research-checked-frontier-v1",
            "id": "sealed-frontier",
            "feedback": {"complete": True, "checked_branches": 2},
            "branches": [
                {"job_id": "source", "result_artifact": "artifact-source", "scorecard": card},
                {
                    "job_id": "other",
                    "result_artifact": "artifact-other",
                    "scorecard": {"checks": [{"key": "higher_costs", "passed": False}]},
                },
            ],
        },
    }
    value["failure_diagnosis_context"] = diagnosis.context(value)
    return value


def hypothesis():
    return {
        "target_ref": "b1/higher_costs",
        "cause": "exit_churn",
        "prediction": "Removing trailing exits may reduce premature re-entry and improve the unchanged matched-cost comparison.",
        "falsifier": "If native cost-selected trades and the failed baseline comparison remain unchanged, abandon this exit-churn explanation.",
    }


def chosen(value=None):
    return {
        "remedy": "lower_turnover",
        "candidate_id": "candidate",
        "reason": "Test a specific failed cost mechanism, with unchanged validation.",
        "diagnosis": hypothesis() if value is None else value,
    }


def test_choice_requires_failed_origin_target_prediction_and_disconfirmation():
    value = evidence()
    selected = decision.validate_decision(chosen(), value)
    h = selected["diagnosis"]
    assert h["candidate_id"] == "candidate" and h["origin_job_id"] == "source"
    assert h["context_key"] == value["failure_diagnosis_context"]["key"]
    assert decision.decision_value(selected, concrete=True) == chosen()
    with pytest.raises(diagnosis.InvalidFailureDiagnosis, match="diagnose"):
        decision.validate_decision({k: v for k, v in chosen().items() if k != "diagnosis"}, value)


@pytest.mark.parametrize(
    "case",
    [
        "passing",
        "wrong_branch",
        "wrong_cause",
        "missing_prediction",
        "short_falsifier",
        "same_prediction",
        "arbitrary_fields",
    ],
)
def test_unsupported_or_unfalsifiable_hypotheses_cannot_authorize_a_test(case):
    h = hypothesis()
    if case == "passing":
        h["target_ref"] = "b1/sample_and_distribution"
    if case == "wrong_branch":
        h["target_ref"] = "b2/higher_costs"
    if case == "wrong_cause":
        h["cause"] = "weaken_validation"
    if case == "missing_prediction":
        h.pop("prediction")
    if case == "short_falsifier":
        h["falsifier"] = "Try again"
    if case == "same_prediction":
        h["falsifier"] = h["prediction"]
    if case == "arbitrary_fields":
        h["settings"] = {"capital": 100000000}
    with pytest.raises(diagnosis.InvalidFailureDiagnosis):
        decision.validate_decision(chosen(h), evidence())


def test_stale_context_or_origin_artifact_is_rejected():
    value = evidence()
    changed = deepcopy(value)
    changed["experiment_frontier"]["id"] = "new-checked-results"
    with pytest.raises(diagnosis.InvalidFailureDiagnosis, match="stale"):
        decision.validate_decision(chosen(), changed)
    changed = deepcopy(value)
    changed["experiment_options"][0]["origin_result_artifact"] = "wrong-artifact"
    with pytest.raises(diagnosis.InvalidFailureDiagnosis, match="artifact"):
        diagnosis.context(changed)


@pytest.mark.parametrize("blocked", [False, True])
def test_native_funding_only_diagnoses_verified_affordability(blocked):
    value = evidence(funding=True, blocked=blocked)
    picked = decision.native_decision(value)
    h = picked["diagnosis"]
    assert picked["provider_called"] is False
    assert h["cause"] == "affordability_and_sizing"
    assert h["target_ref"] == ("b1/feasibility" if blocked else "b1/operating_capacity")
    invalid = {**hypothesis(), "cause": "affordability_and_sizing"}
    with pytest.raises(diagnosis.InvalidFailureDiagnosis, match="failed check"):
        diagnosis.validate(invalid, value, "candidate")


def test_fresh_execution_revalidates_hypothesis_and_passes_it_to_admission(monkeypatch):
    from services import research_supervisor as supervisor

    value = {**evidence(), "status": "awaiting_plan"}
    captured = {}
    monkeypatch.setattr(supervisor, "status", lambda *a, **kw: value)
    monkeypatch.setattr(supervisor, "propose", lambda *a, **kw: captured.update(kw))
    monkeypatch.setattr(supervisor, "advance", lambda *a, **kw: {"queued": True})
    with pytest.raises(diagnosis.InvalidFailureDiagnosis, match="diagnose"):
        decision.execute_plan(
            None,
            "owner",
            "plan",
            conversation_id="saved",
            decision={k: v for k, v in chosen().items() if k != "diagnosis"},
        )
    assert not captured
    selected = decision.validate_decision(chosen(), value)
    assert decision.execute_plan(
        None, "owner", "plan", conversation_id="saved", decision=selected
    ) == {"queued": True}
    assert captured["diagnosis"] == selected["diagnosis"]
    value["experiment_frontier"]["branches"][0]["scorecard"]["checks"][0]["passed"] = True
    with pytest.raises(diagnosis.InvalidFailureDiagnosis):
        decision.execute_plan(None, "owner", "plan", conversation_id="saved", decision=selected)


def test_stop_keeps_its_existing_per_option_review_contract():
    value = evidence()
    stopped = {
        "remedy": "stop",
        "candidate_id": None,
        "reason": "No supported next mechanism is justified by the checked account.",
        "option_reviews": [
            {
                "option": 1,
                "evidence_ref": "b1/higher_costs",
                "reason": "The registered exit configuration lacks distinct support against its recorded failed higher-cost comparison.",
            }
        ],
    }
    assert decision.validate_decision(stopped, value)["stop_review"]["complete"]
    assert decision.MAX_OUTPUT_TOKENS == 600 and decision.MAX_PROMPT_CHARS == 64000
