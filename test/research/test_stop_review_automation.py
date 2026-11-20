"""Durable option-level stop review with a small saved-report ledger only."""

# ruff: noqa: F811 -- reusable isolated metadata ledger fixtures
import json
import time
from copy import deepcopy

import pytest
from test_automatic_continuation import start
from test_research_supervisor import CONVERSATION, OWNER, ledger  # noqa: F401

from database.research_db import (
    ResearchJourney,
    ResearchLibraryExperiment,
    ResearchStore,
    ResearchSupervisor,
)
from services import research_automation as automation
from services import research_stop_review as review
from services import research_supervisor as supervisor


def setup_case(ledger, monkeypatch, *, calls=3, options=2):
    """Use saved report metadata; a provider/engine would be a test failure."""
    plan = start(ledger, monkeypatch, model_id=1, max_model_calls=calls)
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        value = json.loads(row.state)
        value["continuation_policy"] = supervisor.ADAPTIVE_POLICY
        supervisor._put(row, value)
    original = supervisor.status
    candidates = [
        {"id": f"candidate-{number}", "remedy": "temporal_stability"}
        for number in range(1, options + 1)
    ]
    frontier = {
        "version": "research-checked-frontier-v1",
        "id": "f" * 64,
        "branches": [
            {
                "job_id": "checked-branch",
                "scorecard": {
                    "failed_gate_keys": ["neighboring_settings"],
                    "checks": [
                        {
                            "key": "neighboring_settings",
                            "passed": False,
                            "facts": {"tested": 4, "passing": 0},
                        }
                    ],
                },
            }
        ],
        "feedback": {"complete": True, "checked_branches": 1},
    }

    def status(*args, **kwargs):
        current = original(*args, **kwargs)
        current["experiment_options"] = deepcopy(candidates)
        current["experiment_frontier"] = deepcopy(frontier)
        current["diagnosis"] = {"permitted_remedies": ["temporal_stability"]}
        return current

    def evidence(_store, _owner, current):
        value = {
            "diagnosis": current["diagnosis"],
            "remaining_accounts": current["remaining_accounts"],
            "experiment_frontier": deepcopy(frontier),
            "experiment_options": deepcopy(candidates),
        }
        value["stop_review_context"] = review.context(
            value, current["automation"].get("stop_review")
        )
        return value

    monkeypatch.setattr(supervisor, "status", status)
    monkeypatch.setattr(automation, "_evidence", evidence)
    return plan


def current(ledger, plan):
    return supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)


def stopping_choice(*numbers):
    return {
        "remedy": "stop",
        "candidate_id": None,
        "reason": "The unchanged failed neighboring checks do not support these hypotheses.",
        "option_reviews": [
            {
                "option": number,
                "evidence_ref": "b1/neighboring_settings",
                "reason": f"Option {number} changes the holding mechanism without support from any neighboring check.",
            }
            for number in numbers
        ],
        "selection": "model",
        "provider_called": True,
        "usage": {},
    }


def test_partial_review_is_not_a_stop_and_completes_on_the_same_grant(ledger, monkeypatch):
    plan = setup_case(ledger, monkeypatch)
    seen = []

    def choose(evidence, _model_id):
        seen.append(deepcopy(evidence["stop_review_context"]))
        return stopping_choice(len(seen))

    assert automation.run_one(ledger.store, "worker-one", choose=choose)
    first = current(ledger, plan)
    assert first["status"] != "stopped" and not first.get("stop_decision")
    assert first["automation"]["calls_spent"] == 1
    assert first["automation"]["stop_review"]["complete"] is False
    assert first["automation"]["stop_review"]["unreviewed_options"] == [2]
    assert automation.run_one(ledger.store, "worker-one", choose=choose)
    final = current(ledger, plan)
    assert seen[1]["unreviewed_options"] == [2]
    assert final["stopping"]["kind"] == "model_stop"
    assert final["stopping"]["model_budget_exhausted"] is False
    assert final["stop_decision"]["stop_review"]["complete"] is True
    assert final["stopping"]["unreviewed_option_ids"] == []
    assert final["automation"]["calls_spent"] == 2 and final["original_attempts"] == 400
    assert not ledger.submissions


def test_complete_model_stop_on_last_call_retains_its_actual_cause(ledger, monkeypatch):
    plan = setup_case(ledger, monkeypatch, calls=1)
    assert automation.run_one(ledger.store, "worker-one", choose=lambda *_: stopping_choice(1, 2))
    final = current(ledger, plan)
    assert final["stopping"]["kind"] == "model_stop"
    assert final["stopping"]["decision_source"] == "model"
    assert final["stopping"]["model_budget_exhausted"] is True
    assert "ChatGPT decided" in final["stopping_reason"]
    assert final["automation"]["calls_spent"] == final["automation"]["max_model_calls"] == 1
    assert len(final["stop_decision"]["stop_review"]["reviews"]) == 2


def test_legacy_stop_without_a_checked_frontier_is_confirmed_only_after_execution(
    ledger, monkeypatch
):
    plan = start(ledger, monkeypatch, model_id=1, max_model_calls=1)
    choice = {
        "remedy": "stop",
        "reason": "The available saved evidence does not support another registered remedy.",
        "selection": "model",
        "provider_called": True,
    }
    assert automation.run_one(ledger.store, "worker-one", choose=lambda *_: choice)
    final = current(ledger, plan)
    assert final["stopping"]["kind"] == "model_stop"
    assert final["stopping"]["model_budget_exhausted"] is True
    assert final["stop_decision"]["selection"] == "model"
    assert final["stop_decision"]["reason"] == choice["reason"]
    assert final["automation"]["calls_spent"] == 1 and not ledger.submissions


def test_partial_review_at_limit_is_a_budget_stop_not_a_model_stop(ledger, monkeypatch):
    plan = setup_case(ledger, monkeypatch, calls=1)
    assert automation.run_one(ledger.store, "worker-one", choose=lambda *_: stopping_choice(1))
    assert automation.run_one(
        ledger.store,
        "worker-one",
        choose=lambda *_: pytest.fail("No call beyond the frozen budget"),
    )
    final = current(ledger, plan)
    assert final["stopping"]["kind"] == "model_budget_exhausted"
    assert final["stopping"]["decision_source"] == "controller"
    assert final["stopping"]["model_budget_exhausted"] is True
    assert final["stopping"]["unreviewed_option_ids"] == ["candidate-2"]
    assert not final.get("stop_decision")
    assert final["automation"]["stop_review"]["complete"] is False
    assert "not a ChatGPT conclusion" in final["stopping_reason"]
    assert final["automation"]["calls_spent"] == 1 and not ledger.submissions


def test_unaudited_model_stop_is_attention_and_its_call_stays_charged(ledger, monkeypatch):
    plan = setup_case(ledger, monkeypatch)
    invalid = stopping_choice(1)
    invalid.pop("option_reviews")
    assert automation.run_one(ledger.store, "worker-one", choose=lambda *_: invalid)
    final = current(ledger, plan)
    assert final["stopping"]["kind"] == "stop_review_incomplete"
    assert final["automation"].get("attention")
    assert not final.get("stop_decision") and not ledger.submissions
    assert final["stopping"]["unreviewed_option_ids"] == ["candidate-1", "candidate-2"]
    assert final["stopping"]["model_calls_spent"] == final["automation"]["calls_spent"] == 1


def test_stale_partial_review_cannot_claim_new_options_were_reviewed(ledger, monkeypatch):
    plan = setup_case(ledger, monkeypatch)
    current_state = current(ledger, plan)
    prior = {
        "frontier_id": "old-frontier",
        "option_ids": ["candidate-1"],
        "unreviewed_options": [],
        "reviews": [],
        "complete": True,
    }
    current_state["automation"]["stop_review"] = prior
    stopping = automation._stopping(current_state, "model_budget_exhausted", "Budget spent")
    assert stopping["stop_review"] is None
    assert stopping["unreviewed_option_ids"] == ["candidate-1", "candidate-2"]


def test_native_budget_exhaustion_is_classified_from_the_full_validation_reserve():
    value = {
        "experiment_options": [],
        "remaining_accounts": 13,
        "experiment_frontier": {
            "unavailable": [{"reason_code": "full_validation_budget", "required_accounts": 14}],
        },
    }
    kind = automation._controller_stop_kind(value)
    assert kind == "native_budget_exhausted"
    assert (
        automation._stopping(value, kind, "Full validation cannot fit")["decision_source"]
        == "controller"
    )


def test_nine_options_are_reviewed_in_two_bounded_calls_without_extending_the_grant(
    ledger, monkeypatch
):
    plan = setup_case(ledger, monkeypatch, calls=2, options=9)
    batches = []

    def choose(evidence, _model_id):
        context = evidence["stop_review_context"]
        batch = context["unreviewed_options"][: context["batch_limit"]]
        batches.append(batch)
        return stopping_choice(*batch)

    assert automation.run_one(ledger.store, "worker-one", choose=choose)
    assert automation.run_one(ledger.store, "worker-one", choose=choose)
    final = current(ledger, plan)
    assert batches == [[1, 2, 3, 4, 5, 6], [7, 8, 9]]
    assert final["stopping"]["kind"] == "model_stop"
    assert final["stopping"]["model_budget_exhausted"] is True
    assert len(final["stop_decision"]["stop_review"]["reviews"]) == 9
    assert final["automation"]["calls_spent"] == final["automation"]["max_model_calls"] == 2
    assert final["original_attempts"] == 400 and not ledger.submissions


def test_partial_review_projection_keeps_its_own_pending_progress(ledger, monkeypatch):
    from services import research_journey as journey

    plan = setup_case(ledger, monkeypatch)
    assert automation.run_one(ledger.store, "worker-one", choose=lambda *_: stopping_choice(1))
    saved = current(ledger, plan)
    decision = saved["automation"]["decisions"][0]
    projected = journey._decision_display(ledger.store, OWNER, saved, decision, {})
    assert projected["stop_review"]["complete"] is False
    assert len(projected["stop_review"]["reviews"]) == 1
    assert projected["stop_review"]["option_count"] == 2
    assert projected["stop_review"]["unreviewed_options"] == [2]
    projected["stop_review"]["reviews"].clear()
    assert len(decision["stop_review"]["reviews"]) == 1


def test_saving_and_reopening_retains_the_stop_cause_and_all_option_reviews(ledger, monkeypatch):
    from services import research_journey as journey

    plan = setup_case(ledger, monkeypatch)
    assert automation.run_one(ledger.store, "worker-one", choose=lambda *_: stopping_choice(1, 2))
    final = current(ledger, plan)
    study = "9" * 32
    now = time.time()
    with ledger.store.sessions.begin() as db:
        db.get(ResearchSupervisor, plan["id"]).conversation_id = "journey:" + study
        db.add(
            ResearchLibraryExperiment(
                id=study,
                owner=OWNER,
                name="Saved stopped study",
                draft="{}",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            ResearchJourney(
                experiment_id=study,
                owner=OWNER,
                phase="researching",
                updated_at=now,
                state=json.dumps({"supervisor_id": plan["id"]}),
            )
        )
    result = {"stopping_reason": final["stopping_reason"], "stopping": final["stopping"]}
    journey._finish(ledger.store, OWNER, study, result)
    reopened_store = ResearchStore(ledger.store.root)
    try:
        reopened = journey.status(reopened_store, OWNER, study)
        assert reopened["phase"] == "none"
        assert reopened["result"]["accepted"] is False
        assert reopened["result"]["stopping"] == result["stopping"]
        assert reopened["stopping"] == result["stopping"]
        assert reopened["stop_review"]["reviews"] == result["stopping"]["stop_review"]["reviews"]
        assert reopened["decisions"][0]["stop_review"]["complete"] is True
        assert len(reopened["decisions"][0]["stop_review"]["reviews"]) == 2
        assert not ledger.submissions
    finally:
        reopened_store.close()
