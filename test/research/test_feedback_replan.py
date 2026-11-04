"""Replanning changes experiment selection, never completed evidence or budgets."""

# ruff: noqa: F811 -- shared isolated ledger fixture
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_research_supervisor import CONVERSATION, OWNER, ROOT, ledger

from database.research_db import ResearchDecisionJob, ResearchSupervisor
from services import research_automation as auto
from services import research_supervisor as supervisor


@pytest.fixture
def stopped(ledger, monkeypatch):
    from services import research_memory
    from services.agent import builder

    monkeypatch.setattr(
        builder, "resolve_model", lambda *a: SimpleNamespace(litellm_id="test/model")
    )
    plan = auto.start(
        ledger.store,
        OWNER,
        ROOT,
        conversation_id=CONVERSATION,
        request_id="closed-feedback-case",
        max_experiments=2,
        model_id=1,
    )
    settings = deepcopy(ledger.report["strategies"])
    option = {
        "id": "c" * 64,
        "remedy": "temporal_stability",
        "settings": settings,
        "change": {"hold_sessions": 3},
        "expected_effect": "A recorded long trade may change",
        "reason": "Recorded horizon intersected",
    }
    monkeypatch.setattr(
        supervisor,
        "_experiment_catalog",
        lambda *a: {"options": [deepcopy(option)], "feedback": {"prior_no_effect": True}},
    )
    monkeypatch.setattr(research_memory, "read", lambda *a, **k: {"entries": []})
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        state = json.loads(row.state)
        state.update(
            status="stopped",
            research_context={
                "experiment_id": "study",
                "inputs_artifact": "e" * 64,
                "contract": {},
            },
        )
        state["proposals"] = [
            {
                "id": "1" * 64,
                "remedy": "funding_and_size",
                "settings": settings,
                "outcome": "rejected",
                "reserved_accounts": 17,
                "spent": 16,
                "completed": 16,
            },
            {
                "id": "2" * 64,
                "remedy": "lower_turnover",
                "settings": settings,
                "outcome": "rejected",
                "reserved_accounts": 14,
                "spent": 1,
                "completed": 1,
            },
        ]
        state["automation"]["calls_spent"] = 1
        supervisor._put(row, state)
        db.get(ResearchDecisionJob, plan["id"]).phase = "stopped"
    return ledger, plan


def test_confirmed_replan_preserves_spending_and_old_proposals_and_is_idempotent(stopped):
    ledger, plan = stopped
    previous = supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    result = supervisor.replan(
        ledger.store,
        OWNER,
        plan["id"],
        conversation_id=CONVERSATION,
        request_id="explicit-feedback-replan",
    )
    assert (
        result["max_experiments"] == 3
        and result["experiment_policy"] == supervisor.EXPERIMENT_POLICY
    )
    assert result["remaining_accounts"] == previous["remaining_accounts"] == 95
    assert result["automation"]["calls_spent"] == 1
    assert result["proposals"] == previous["proposals"] and result["original_attempts"] == 400
    assert result["automatic_phase"] == "queued" and result["status"] == "awaiting_plan"
    again = supervisor.replan(
        ledger.store,
        OWNER,
        plan["id"],
        conversation_id=CONVERSATION,
        request_id="explicit-feedback-replan",
    )
    assert again["replans"] == result["replans"] and len(again["replans"]) == 1
    assert not ledger.submissions


def test_new_policy_has_one_native_and_three_model_slots_without_relaxing_legacy(
    ledger, monkeypatch
):
    monkeypatch.setattr(
        supervisor, "_research_context", lambda *a: ({"contract": {}}, {"entries": []})
    )
    monkeypatch.setattr(
        supervisor,
        "_reviewed_diagnosis",
        lambda *a: {"permitted_remedies": [], "missing_evidence": False},
    )
    monkeypatch.setattr(
        supervisor,
        "_experiment_catalog",
        lambda *a: {"options": [{"id": "c" * 64, "remedy": "lower_turnover"}], "feedback": {}},
    )
    with pytest.raises(ValueError, match="bounded experiment"):
        supervisor.open_supervisor(
            ledger.store,
            OWNER,
            ROOT,
            conversation_id=CONVERSATION,
            request_id="legacy-cannot-expand",
            max_experiments=4,
        )
    plan = supervisor.open_supervisor(
        ledger.store,
        OWNER,
        ROOT,
        conversation_id=CONVERSATION,
        request_id="new-feedback-four-slots",
        max_experiments=4,
        experiment_policy=supervisor.EXPERIMENT_POLICY,
        research_context={"contract": {}},
    )
    assert plan["status"] == "planning" and plan["max_experiments"] == 4
    assert plan["original_attempts"] == 400 and plan["remaining_accounts"] == 112
    assert plan["diagnosis"]["permitted_remedies"] == ["lower_turnover"]


@pytest.mark.parametrize("blocked", ["missing", "used_slots", "calls", "running"])
def test_replan_cannot_relax_evidence_authority_or_account_limits(stopped, blocked):
    ledger, plan = stopped
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        state = json.loads(row.state)
        if blocked == "missing":
            state["diagnosis"]["missing_evidence"] = True
        if blocked == "used_slots":
            state["proposals"].append(deepcopy(state["proposals"][-1]))
            state["proposals"].append(deepcopy(state["proposals"][-1]))
        if blocked == "calls":
            state["automation"]["calls_spent"] = 3
        if blocked == "running":
            state["status"] = "running"
        supervisor._put(row, state)
    with pytest.raises(ValueError, match="cannot admit"):
        supervisor.replan(
            ledger.store,
            OWNER,
            plan["id"],
            conversation_id=CONVERSATION,
            request_id="explicit-blocked-replan",
        )
    assert not ledger.submissions
