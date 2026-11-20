"""A failed diagnosis cannot spend native accounts or replay an admitted intent."""

# ruff: noqa: F811 -- shared isolated metadata fixtures
import json

from test_research_supervisor import ledger  # noqa: F401
from test_stop_review_automation import setup_case, current

from database.research_db import ResearchSupervisor
from services import research_automation as automation
from services import research_failure_diagnosis as diagnosis
from services import research_supervisor as supervisor


def test_fresh_diagnosis_rejection_preserves_charges_and_marks_intent_unreplayable(
    ledger, monkeypatch
):
    plan = setup_case(ledger, monkeypatch)
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        state = json.loads(row.state)
        state["failure_diagnosis_version"] = diagnosis.VERSION
        supervisor._put(row, state)
    before = current(ledger, plan)
    assert automation.run_one(
        ledger.store,
        "worker-one",
        choose=lambda *_: {
            "remedy": "temporal_stability",
            "candidate_id": "candidate-1",
            "reason": "Legacy response has no origin-bound causal hypothesis",
            "selection": "model",
            "provider_called": True,
        },
    )
    after = current(ledger, plan)
    assert after["stopping"]["kind"] == "diagnosis_incomplete"
    assert after["stopping"]["decision_source"] == "controller"
    assert after["automation"]["calls_spent"] == 1
    assert after["remaining_accounts"] == before["remaining_accounts"]
    assert after["proposals"] == before["proposals"] and ledger.submissions == []
    assert after["automation"]["decisions"][-1]["diagnosis_rejected"]
    assert not after.get("stop_decision")
