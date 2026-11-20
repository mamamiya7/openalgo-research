"""Whole-frontier choices preserve exact settings and charge only provider calls."""

# ruff: noqa: F811 -- shared isolated worker ledger fixtures
import json
from copy import deepcopy

import pytest
from test_automatic_continuation import start, state
from test_configuration_context import reconstruct, settings
from test_research_supervisor import ledger  # noqa: F401

from services import research_automation as automation
from services import research_supervisor_program as program
from services.agent import research_decision as decision
from services.research_configuration_context import build


def frontier():
    return {
        "version": "research-checked-frontier-v1",
        "branches": [{"job_id": "saved"}],
        "feedback": {"complete": True, "checked_branches": 1},
    }


def test_all_two_hundred_configuration_choices_are_lossless_without_redundant_patches():
    base = settings()
    options = []
    for number in range(200):
        changed = deepcopy(base)
        changed[0]["config"].update(
            initial_capital=100000 + number * 1000, hold_sessions=number + 1
        )
        options.append({"id": str(number), "settings": changed})
    value = build(base, [], options, max_options=200, include_closest=False)
    assert len(value["candidates"]) == 200
    for option, candidate in zip(options, value["candidates"], strict=True):
        assert reconstruct(value, candidate["configuration_id"]) == option["settings"]
        assert candidate["closest_previous"] == []
    with pytest.raises(ValueError, match="bound"):
        build(base, [], options)


def test_candidate_after_the_old_eight_option_limit_is_selectable_only_on_complete_frontier():
    evidence = {
        "diagnosis": {"permitted_remedies": ["lower_turnover"]},
        "experiment_options": [{"id": str(i), "remedy": "lower_turnover"} for i in range(200)],
        "experiment_frontier": frontier(),
    }
    choice = {
        "remedy": "lower_turnover",
        "candidate_id": "199",
        "reason": "Distinct earlier branch",
    }
    assert decision.validate_decision(choice, evidence) == choice
    evidence.pop("experiment_frontier")
    with pytest.raises(ValueError, match="bound"):
        decision.validate_decision(choice, evidence)


@pytest.mark.parametrize("incomplete", ["flag", "branch", "count"])
def test_partial_frontier_cannot_claim_global_stop(incomplete):
    value = frontier()
    if incomplete == "flag":
        value["feedback"]["complete"] = False
    if incomplete == "branch":
        value["branches"] = []
    if incomplete == "count":
        value["feedback"]["checked_branches"] = 2
    evidence = {
        "diagnosis": {"permitted_remedies": ["lower_turnover"]},
        "experiment_options": [],
        "experiment_frontier": value,
    }
    with pytest.raises(ValueError, match="complete checked-branch"):
        decision.validate_decision(
            {"remedy": "stop", "candidate_id": None, "reason": "Unreviewed"}, evidence
        )


def test_oversized_review_does_not_spend_a_model_call_or_claim_exhaustion(ledger, monkeypatch):
    plan = start(ledger, monkeypatch, model_id=1)
    monkeypatch.setattr(
        automation,
        "_evidence",
        lambda *a: {
            "diagnosis": {"permitted_remedies": ["funding_and_size"]},
            "facts": "x" * decision.MAX_PROMPT_CHARS,
        },
    )
    assert automation.run_one(
        ledger.store,
        "worker-one",
        choose=lambda *a: pytest.fail("Provider received oversized review"),
    )
    final = state(ledger, plan)
    assert final["automation"]["calls_spent"] == 0 and ledger.submissions == []
    assert "unreviewed alternatives" in final["stopping_reason"]
    assert "exhausted" not in final["stopping_reason"]


def test_later_funding_keeps_the_selected_branch_capital_floor():
    report = {"summary": {"initial_capital": 199000.0}}
    original = {"portfolio": {"capital": 100000.0}}
    assert (
        program._funding_seed_capital(report, original, seed_id="funded", root_id="root")
        == 199000.0
    )
    assert (
        program._funding_seed_capital(report, original, seed_id="root", root_id="root") == 100000.0
    )


def test_large_complete_review_factors_identical_facts_without_losing_any_branch():
    card = {
        "failed_checks": [
            {"measurement": i, "explanation": "required native check " * 12} for i in range(30)
        ]
    }
    evidence = {"branches": [{"job_id": str(i), "scorecard": deepcopy(card)} for i in range(20)]}
    encoded = json.loads(decision.prepare_evidence(evidence))
    assert encoded["representation"] == "shared-evidence-v1"

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"shared_evidence_ref"}:
                return expand(encoded["shared_evidence"][value["shared_evidence_ref"]])
            return {key: expand(child) for key, child in value.items()}
        return [expand(child) for child in value] if isinstance(value, list) else value

    assert expand(encoded["evidence"]) == evidence
    assert len(encoded["evidence"]["branches"]) == 20


def test_existing_reference_shaped_data_is_never_reinterpreted():
    evidence = {"shared_evidence_ref": "user data", "facts": "x" * decision.MAX_PROMPT_CHARS}
    with pytest.raises(decision.DecisionEvidenceTooLarge):
        decision.prepare_evidence(evidence)


def test_repeated_identifiers_are_compacted_losslessly_with_the_same_prompt_limit():
    evidence = {"identifiers": ["a" * 64] * 1000}
    encoded = json.loads(decision.prepare_evidence(evidence))
    assert encoded["representation"] == "shared-evidence-v1"
    expanded = [
        encoded["shared_evidence"][row["shared_evidence_ref"]]
        for row in encoded["evidence"]["identifiers"]
    ]
    assert expanded == evidence["identifiers"]
    assert decision.MAX_PROMPT_CHARS == 64000
