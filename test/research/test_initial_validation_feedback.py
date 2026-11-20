"""Recorded initial failures inform decisions without replacing full validation."""

# ruff: noqa: F811 -- shared controlled native-report fixture

from copy import deepcopy

import pytest
from test_validation_scorecard import native  # noqa: F401

from research.automatic_decision import descriptor
from research.report_contract import fingerprint, settings_identity
from services import research_automation as automation
from services.research_validation_scorecard import build, dominates, rank


def seal(value):
    value["id"] = fingerprint({k: v for k, v in value.items() if k != "id"})
    return value


@pytest.fixture
def initial(native):
    result = deepcopy(native)
    operating = result.pop("supervisor_validation")["operating_validation"]
    decision = deepcopy(operating["decision"])
    decision["selected_config_id"] = settings_identity(result["strategies"])
    decision["historical_candidate"] = False
    decision["reasons"] = ["Nearby settings did not retain adequate support and improvement"]
    neighbor = next(r for r in decision["checks"] if r["kind"] == "neighboring_settings")
    neighbor.update(passed=False, passing=2, tested=4)
    seal(decision)
    recipe = seal(
        {
            "version": "automatic-hypotheses-v16",
            "validation_mode": "exploratory_only",
            "automatic_decision": descriptor(),
            "budget": {"proposals": 408, "max_simulations": 512},
        }
    )
    result["automatic_research"] = {
        "version": recipe["version"],
        "recipe": recipe,
        "decision": decision,
        "selected_config_id": decision["selected_config_id"],
        "independently_validated": False,
        "final": None,
    }
    return result


def test_initial_neighbor_failure_keeps_actual_measurements_but_cannot_rank(initial, native):
    original = deepcopy(initial)
    card = build(initial)
    assert card["feedback_available"] and not card["eligible"]
    assert card["evidence_kind"] == "initial_search_challenges"
    check = next(r for r in card["checks"] if r["key"] == "neighboring_settings")
    assert check["passed"] is False
    assert check["facts"]["passing"] == 2 and check["facts"]["tested"] == 4
    assert check["thresholds"]["minimum_neighbor_pass_fraction"] == 0.75
    assert card["failed_gate_keys"] == ["neighboring_settings"]
    assert rank(initial) is None and not dominates(initial, native)
    assert not dominates(native, initial) and initial == original


@pytest.mark.parametrize(
    "mutation", ["seal", "policy", "config", "later", "cost", "neighbor", "child"]
)
def test_unknown_or_changed_initial_evidence_stays_unavailable(initial, mutation):
    value = deepcopy(initial)
    findings = value["automatic_research"]
    if mutation == "seal":
        findings["decision"]["checks"][0]["passed"] = False
    if mutation == "policy":
        findings["recipe"]["automatic_decision"]["minimum_neighbor_pass_fraction"] = 0.5
    if mutation == "config":
        value["strategies"][0]["config"]["hold_sessions"] = 99
    if mutation == "later":
        findings["final"] = {"private_later_return": 99}
    if mutation == "cost":
        findings["decision"]["checks"][0].pop("summary")
        seal(findings["decision"])
    if mutation == "neighbor":
        findings["decision"]["checks"][1]["passing"] = 5
        seal(findings["decision"])
    if mutation == "child":
        value["supervisor_validation"] = {"outcome": "blocked"}
    card = build(value)
    assert not card["eligible"] and not card.get("feedback_available") and not card["checks"]


def test_current_root_failed_checks_reach_complete_model_evidence(initial, monkeypatch):
    monkeypatch.setattr(
        automation.supervisor.service, "read_artifact", lambda *a: {"result": initial}
    )
    current = {
        "experiment_policy": automation.supervisor.EXPERIMENT_POLICY,
        "source_result_artifact": "a" * 64,
        "proposals": [],
        "goal": "Robust improvement",
        "remaining_accounts": 337,
        "validation_mode": "exploratory_only",
        "diagnosis": {
            "permitted_remedies": ["funding_and_size"],
            "evidence_status": {"excluded_requests": 4},
        },
        "experiment_feedback": {},
        "experiment_options": [
            {"id": "b" * 64, "remedy": "funding_and_size", "settings": initial["strategies"]}
        ],
    }
    evidence = automation._evidence(None, "test-owner", current)
    feedback = evidence["post_validation_feedback"]
    assert feedback["measurements_available"]
    assert feedback["failed_gate_keys"] == ["neighboring_settings"]
    assert feedback["source_result_artifact"] == "a" * 64
    assert feedback["excluded_data"]["excluded_requests"] == 4
    assert evidence["validation_scorecard"]["checks"][1]["facts"]["passing"] == 2


def test_failed_initial_admission_does_not_invent_unrun_challenge_results(initial):
    decision = initial["automatic_research"]["decision"]
    decision["checks"] = []
    decision["reasons"] = ["Candidate did not satisfy sample and distribution support"]
    seal(decision)
    card = build(initial)
    assert card["feedback_available"] and not card["eligible"]
    assert card["failed_gate_keys"] == ["initial_candidate_support"]
    assert card["checks"][0]["facts"]["challenges_run"] is False
    assert not any(r["key"] in ("higher_costs", "neighboring_settings") for r in card["checks"])
