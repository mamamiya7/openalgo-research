"""Durable guided plans pause, approve and replay without another provider call."""

# ruff: noqa: F811 -- shared isolated fixtures
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_hands_off_runtime import case, invoke, proposal  # noqa: F401
from test_jobs import app, client  # noqa: F401
from test_library import OWNER
from test_reviewed_journey import ready  # noqa: F401

from database.research_db import (
    ResearchDecisionJob,
    ResearchJob,
    ResearchJourney,
    ResearchLibraryJob,
)
from research.hands_off_plan import compile_plan
from research.user_flow import normalize
from services import research_hands_off as guided
from services import research_journey as journey
from services import scanner_research_service as service


def guided_flow():
    return {**normalize(), "involvement": "guided"}


@pytest.fixture
def bound(ready, monkeypatch):
    store, view = ready
    identifier, job_id = view["experiment"]["id"], "g" * 32
    source = view["experiment"]["draft"]["portfolio"]["strategies"][0]["source_id"]
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=job_id,
                owner=OWNER,
                source_id=source,
                config="{}",
                status="running",
                progress=10,
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        db.add(
            ResearchLibraryJob(
                experiment_id=identifier, job_id=job_id, role="run", created_at=time.time()
            )
        )
        row, saved = journey._owned(db, OWNER, identifier)
        saved.update(
            job_id=job_id,
            model_id=1,
            model_name="configured/test",
            approved_plan={**saved["plan"], "flow": guided_flow()},
        )
        journey._put(row, saved, "researching")

    def response(*_args):
        with store.sessions() as db:
            row, saved = journey._owned(db, OWNER, identifier)
            return {"phase": row.phase, **saved}

    monkeypatch.setattr(journey, "_response", response)
    return store, identifier, job_id


def set_pending(bound, *, status="paused", kind="search", approval="a" * 64):
    store, identifier, job_id = bound
    with store.sessions.begin() as db:
        db.get(ResearchJob, job_id).status = status
        row, saved = journey._owned(db, OWNER, identifier)
        saved["pending_approval"] = {
            "id": approval,
            "kind": kind,
            "job_id": job_id,
            "reason": "Test holding-driven occupancy",
            "hypothesis": "Shorter holdings",
            "domains": {},
        }
        journey._put(row, saved, "awaiting_approval")
    return {"approval_id": approval, "decision": "approve"}


def test_gate_without_acknowledged_pause_cannot_return_executable_plan(case):
    calls = []

    def choose(evidence):
        calls.append(evidence)
        return compile_plan(proposal(), evidence["planning_context"])

    choose.approval_gate = lambda record: None
    original_trials = deepcopy(case[1]["trials"])
    with pytest.raises(ValueError, match="pause.*approval"):
        invoke(case, choose)
    assert len(calls) == 1
    assert case[1]["trials"] == original_trials
    book = case[0]["hands_off_planning"]
    assert book["calls_spent"] == 1 and book["active"] is None
    assert book["records"]["fit1:2"]["phase"] == "completed"


def test_approved_compiled_plan_replays_with_zero_additional_provider_calls(
    bound, case, monkeypatch
):
    from database import agent_db
    from services.agent import builder, research_planning

    store, identifier, job_id = bound
    provider_calls, pauses, resumes = [], [], []
    monkeypatch.setattr(
        builder, "resolve_model", lambda *_: SimpleNamespace(litellm_id="configured/test")
    )
    monkeypatch.setattr(agent_db.db_session, "remove", lambda: None)

    def select(evidence, _model):
        provider_calls.append(deepcopy(evidence))
        return compile_plan(proposal(), evidence["planning_context"])

    monkeypatch.setattr(research_planning, "select_plan", select)

    def pause(*_args):
        pauses.append(True)
        with store.sessions.begin() as db:
            db.get(ResearchJob, job_id).status = "paused"

    def resume(*_args):
        resumes.append(True)
        with store.sessions.begin() as db:
            db.get(ResearchJob, job_id).status = "queued"

    monkeypatch.setattr(service, "pause", pause)
    monkeypatch.setattr(service, "resume", resume)
    callback = guided.planner_for(store, OWNER, job_id)
    prefix = service.save_artifact(store, {"state": {"calculation": case[0]}})
    callback.feedback_reference = lambda: {"checkpoint": prefix, "job_id": job_id}
    with pytest.raises(ValueError, match="pause.*approval"):
        invoke(case, callback)
    with store.sessions() as db:
        saved = json.loads(db.get(ResearchJourney, identifier).state)
    pending = saved["pending_approval"]
    assert pending["id"] == case[0]["hands_off_planning"]["records"]["fit1:2"]["plan"]["id"]
    guided.resolve_approval(
        store, OWNER, identifier, {"approval_id": pending["id"], "decision": "approve"}
    )
    replayed, _ = invoke(case, callback)
    assert replayed["plan"]["id"] == pending["id"]
    assert len(provider_calls) == len(pauses) == len(resumes) == 1
    assert case[0]["hands_off_planning"]["calls_spent"] == 1


@pytest.mark.parametrize("status", ["running", "pausing", "queued"])
def test_early_approval_waits_for_safe_pause_without_granting_or_clearing_request(
    bound, monkeypatch, status
):
    store, identifier, _ = bound
    request = set_pending(bound, status=status)
    monkeypatch.setattr(service, "resume", lambda *a: pytest.fail("Unsafe resume"))
    with pytest.raises(ValueError, match="safe pause"):
        guided.resolve_approval(store, OWNER, identifier, request)
    with store.sessions() as db:
        row, saved = journey._owned(db, OWNER, identifier)
    assert row.phase == "awaiting_approval"
    assert saved["pending_approval"]["id"] == request["approval_id"]
    assert not saved.get("approval_decisions")


def test_approval_retry_after_resume_failure_keeps_grant_and_finishes_handoff_once(
    bound, monkeypatch
):
    from services import research_library

    store, identifier, job_id = bound
    request = set_pending(bound)
    calls = []

    def resume(*_args):
        calls.append(True)
        with store.sessions.begin() as db:
            db.get(ResearchJob, job_id).status = "queued"
        raise ValueError("Crash after durable native resume")

    monkeypatch.setattr(service, "resume", resume)
    with pytest.raises(ValueError, match="Crash"):
        guided.resolve_approval(store, OWNER, identifier, request)
    completed = guided.resolve_approval(store, OWNER, identifier, request)
    duplicate = guided.resolve_approval(store, OWNER, identifier, request)
    assert completed["phase"] == duplicate["phase"] == "researching"
    assert duplicate["pending_approval"] is None
    assert duplicate["approval_decisions"][request["approval_id"]] == "approve"
    assert len(calls) == 1
    repeated_api = journey.approve_plan(
        store,
        OWNER,
        identifier,
        {
            "revision": research_library.get_experiment(store, OWNER, identifier)["revision"],
            **request,
        },
    )
    assert repeated_api["phase"] == "researching" and len(calls) == 1
    with pytest.raises(ValueError, match="stale|different"):
        guided.resolve_approval(store, OWNER, identifier, {**request, "decision": "reject"})


def test_guided_continuation_parks_exact_decision_then_reuses_approval(bound):
    store, identifier, job_id = bound
    supervisor = "s" * 32
    with store.sessions.begin() as db:
        db.add(
            ResearchDecisionJob(
                supervisor_id=supervisor, owner=OWNER, phase="applying", updated_at=time.time()
            )
        )
        row, saved = journey._owned(db, OWNER, identifier)
        saved["supervisor_id"] = supervisor
        journey._put(row, saved)
    current = {
        "research_context": {"experiment_id": identifier, "flow": guided_flow()},
        "experiment_options": [
            {
                "id": "candidate",
                "hypothesis": "Fewer overlapping positions",
                "settings": [{"id": "scan"}],
            }
        ],
    }
    decision = {
        "remedy": "lower_turnover",
        "reason": "Measured position congestion",
        "candidate_id": "candidate",
    }
    assert guided.continuation_gate(store, OWNER, supervisor, current, decision) is None
    with store.sessions() as db:
        row, saved = journey._owned(db, OWNER, identifier)
        assert row.phase == "awaiting_approval"
        assert db.get(ResearchDecisionJob, supervisor).phase == "approval"
    assert saved["pending_approval"]["job_id"] == job_id
    assert saved["pending_approval"]["settings"] == [{"id": "scan"}]
    guided.resolve_approval(
        store,
        OWNER,
        identifier,
        {"approval_id": saved["pending_approval"]["id"], "decision": "approve"},
    )
    assert guided.continuation_gate(store, OWNER, supervisor, current, decision) == "approve"
    with store.sessions() as db:
        assert db.get(ResearchDecisionJob, supervisor).phase == "queued"


def test_continuation_worker_parks_saved_decision_and_never_executes_before_approval(
    bound, monkeypatch
):
    from database.research_db import ResearchSupervisor, ResearchWorker
    from services import research_automation as automation
    from services import research_supervisor as supervisor_service
    from services.agent import research_decision

    store, identifier, job_id = bound
    supervisor_id = "w" * 32
    decision = {
        "remedy": "lower_turnover",
        "reason": "Measured congestion",
        "seed_artifact": "e" * 64,
    }
    current = {
        "status": "ready",
        "source_result_artifact": "e" * 64,
        "proposals": [],
        "automation": {"decisions": [decision]},
        "research_context": {"experiment_id": identifier, "flow": guided_flow()},
    }
    with store.sessions.begin() as db:
        lease = db.get(ResearchWorker, 1)
        lease.token, lease.heartbeat = "guided-worker", time.time()
        db.add(
            ResearchSupervisor(
                id=supervisor_id,
                owner=OWNER,
                root_job_id=job_id,
                root_artifact="e" * 64,
                conversation_id="guided-test",
                request_id="guided-saved-decision",
                state=json.dumps(current),
                updated_at=time.time(),
            )
        )
        db.add(
            ResearchDecisionJob(
                supervisor_id=supervisor_id, owner=OWNER, phase="queued", updated_at=time.time()
            )
        )
        row, saved = journey._owned(db, OWNER, identifier)
        saved["supervisor_id"] = supervisor_id
        journey._put(row, saved)
    monkeypatch.setattr(supervisor_service, "status", lambda *a, **k: deepcopy(current))
    monkeypatch.setattr(automation, "_evidence", lambda *a: {})
    monkeypatch.setattr(
        research_decision,
        "select_decision",
        lambda *a: pytest.fail("Saved decision called provider"),
    )
    monkeypatch.setattr(
        research_decision,
        "execute_plan",
        lambda *a, **k: pytest.fail("Unapproved experiment executed"),
    )
    assert automation.run_one(store, "guided-worker")
    with store.sessions() as db:
        assert db.get(ResearchDecisionJob, supervisor_id).phase == "approval"
        row, saved = journey._owned(db, OWNER, identifier)
        assert row.phase == "awaiting_approval"
        assert saved["pending_approval"]["kind"] == "continuation"
    assert not automation.run_one(store, "guided-worker")
