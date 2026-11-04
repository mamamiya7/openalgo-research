"""Blocked accounts restore feasibility without becoming performance evidence."""

# ruff: noqa: F811 -- shared isolated native-ledger fixtures
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_feedback_replan import stopped  # noqa: F401
from test_research_supervisor import CONVERSATION, OWNER, ROOT, complete, ledger  # noqa: F401

from database.research_db import ResearchSupervisor, ResearchWorker
from research.report_contract import fingerprint, settings_identity
from services import research_automation as automation
from services import research_supervisor as supervisor


def read(fixture, plan):
    return supervisor.status(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)


def change(fixture, plan, callback):
    with fixture.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        state = json.loads(row.state)
        callback(state)
        supervisor._put(row, state)


@pytest.fixture
def blocked(stopped, monkeypatch):
    from services import research_playbook

    fixture, plan = stopped
    supervisor.replan(
        fixture.store,
        OWNER,
        plan["id"],
        conversation_id=CONVERSATION,
        request_id="explicit-blocked-recovery",
        continuation_policy=supervisor.ADAPTIVE_POLICY,
    )
    monkeypatch.setattr(research_playbook, "validate_settings", lambda *a: None)
    change(
        fixture,
        plan,
        lambda state: state["research_context"]["contract"].update(id="fixed", engine="vectorbt"),
    )
    change(
        fixture, plan, lambda state: [row.setdefault("job_id", None) for row in state["proposals"]]
    )

    def catalogue(report, recipe, current):
        recovery = report.get("supervisor_validation", {}).get("outcome") == "blocked"
        settings = deepcopy(report["strategies"])
        if not recovery:
            settings[0]["config"]["hold_sessions"] = 40
            settings[0]["config"].update(trailing_enabled=False, trailing_pct=0.0)
        return {
            "options": [
                {
                    "id": ("f" if recovery else "d") * 64,
                    "remedy": "funding_and_size" if recovery else "lower_turnover",
                    "settings": settings,
                    "change": {"axis": "funding" if recovery else "trailing_choice"},
                    "reason": "Native feasibility counts require repair"
                    if recovery
                    else "Recorded exits justify a mechanism test",
                    "expected_effect": "Only native calculations and all checks can decide",
                }
            ],
            "feedback": {"mode": "feasibility_only"} if recovery else {},
            "unavailable": [],
        }

    monkeypatch.setattr(supervisor, "_experiment_catalog", catalogue)
    supervisor.propose(
        fixture.store,
        OWNER,
        plan["id"],
        "lower_turnover",
        "Recorded exit mechanism",
        conversation_id=CONVERSATION,
        candidate_id="d" * 64,
    )
    pending = supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    job_id = pending["proposals"][-1]["job_id"]
    change(fixture, plan, lambda state: state["proposals"][-1].update(spent=1, completed=1))
    complete(fixture, plan, job_id)
    job = fixture.jobs[job_id]
    report = fixture.artifacts[job.result_artifact]["result"]
    report["summary"].update(net_return_pct=999, net_pnl=999999, closed_trades=90)
    validation = report["supervisor_validation"]
    native = {key: value for key, value in report.items() if key != "supervisor_validation"}
    operating = validation["operating_validation"]
    operating.update(
        outcome="blocked",
        historically_validated=False,
        selected_report_id=fingerprint(native),
        reasons=["Native percentage sizing remains constrained"],
    )
    operating["id"] = fingerprint({k: v for k, v in operating.items() if k != "id"})
    validation.update(
        outcome="blocked",
        historically_validated=False,
        native_report_id=fingerprint(native),
        reasons=["Native percentage sizing remains constrained"],
    )
    validation["id"] = fingerprint({k: v for k, v in validation.items() if k != "id"})
    digest = fingerprint({"result": report})
    fixture.artifacts[digest] = {"result": report}
    job.result_artifact = digest
    feasibility = {
        "version": "research-feasibility-v1",
        "scope": "feasibility_only",
        "source_settings_id": settings_identity(report["strategies"]),
        "counts": {"admitted_requests": 21, "percentage_policy": 41},
        "unresolved_constraints": {"percentage_policy": 41},
    }
    record = {
        "job_id": job.id,
        "artifact": digest,
        "proposal_id": validation["proposal_id"],
        **{
            k: deepcopy(feasibility[k])
            for k in ("scope", "source_settings_id", "counts", "unresolved_constraints")
        },
    }
    monkeypatch.setattr(supervisor, "_recovery_record", lambda *a: deepcopy(record))

    def checked_planning(_store, _owner, seed, value):
        if value.get("supervisor_validation", {}).get("outcome") != "blocked":
            return value
        assert seed.status == "completed" and seed.result_artifact == digest
        result = deepcopy(value)
        result["supervisor_validation"]["feasibility"] = deepcopy(feasibility)
        return result

    monkeypatch.setattr(supervisor, "planning_report", checked_planning)
    return fixture, plan, job_id, record


def test_explicit_resume_migrates_an_old_stopped_block_without_resetting_work(blocked):
    from database.research_db import ResearchDecisionJob

    fixture, plan, _, record = blocked
    supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    def old_controller_state(state):
        state.pop("blocked_recovery", None)
        state.update(status="stopped", stopping_reason="Old generic exhaustion message")
    change(fixture, plan, old_controller_state)
    with fixture.store.sessions.begin() as db:
        db.get(ResearchDecisionJob, plan["id"]).phase = "stopped"
    before = read(fixture, plan)
    resumed = supervisor.replan(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION,
                                request_id="confirmed-old-block-resume", continuation_policy=supervisor.ADAPTIVE_POLICY)
    assert resumed["blocked_recovery"] == record
    assert resumed["feasibility_only"] is True and resumed["automatic_phase"] == "queued"
    assert [o["remedy"] for o in resumed["experiment_options"]] == ["funding_and_size"]
    assert resumed["remaining_accounts"] == before["remaining_accounts"]
    assert resumed["proposals"] == before["proposals"]
    assert resumed["automation"]["calls_spent"] == before["automation"]["calls_spent"]


def test_blocked_native_attempt_preserves_checked_seed_and_all_debits(blocked):
    fixture, plan, job_id, record = blocked
    before = read(fixture, plan)
    result = supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert result["status"] == "awaiting_plan" and result["feasibility_only"] is True
    assert result["blocked_recovery"] == record
    assert result["seed_job_id"] == job_id
    assert result["proposals"][-1]["outcome"] == "blocked"
    assert result["remaining_accounts"] == 94
    # The unspent reservation is released; the actual blocked account stays spent.
    assert result["remaining_accounts"] - before["remaining_accounts"] == 13
    assert [row["spent"] for row in result["proposals"]] == [16, 1, 1]
    assert result["original_attempts"] == 400
    with fixture.store.sessions() as db:
        saved = json.loads(db.get(ResearchSupervisor, plan["id"]).state)
    assert saved.get("latest_seed_job_id", ROOT) == ROOT
    assert "latest_conclusion" not in saved
    assert [row["remedy"] for row in result["experiment_options"]] == ["funding_and_size"]


def test_blocked_recovery_queues_native_funding_without_calling_model(blocked, monkeypatch):
    from services.agent import research_decision

    fixture, plan, job_id, _ = blocked
    supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    with fixture.store.sessions.begin() as db:
        lease = db.get(ResearchWorker, 1)
        lease.token, lease.heartbeat = "blocked-worker", time.time()
    monkeypatch.setattr(
        research_decision,
        "select_decision",
        lambda *a: pytest.fail(
            "Mechanical feasibility repair does not require a performance choice"
        ),
    )
    before = read(fixture, plan)
    assert automation.run_one(fixture.store, "blocked-worker")
    result = read(fixture, plan)
    assert result["status"] == "running"
    assert result["proposals"][-1]["remedy"] == "funding_and_size"
    assert result["proposals"][-1]["seed_job_id"] == job_id
    assert result["proposals"][-1]["reserved_accounts"] == 17
    assert result["remaining_accounts"] == before["remaining_accounts"] - 17
    assert result["automation"]["calls_spent"] == before["automation"]["calls_spent"]
    assert result["proposals"][-2]["spent"] == 1
    assert result["proposals"][-2]["outcome"] == "blocked"
    assert len(fixture.submissions) == 2


def test_blocked_performance_and_execution_hashes_are_not_model_evidence(blocked):
    fixture, plan, _, _ = blocked
    current = supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    evidence = automation._evidence(fixture.store, OWNER, current)
    assert evidence["current_account"] == {
        "initial_capital": 100000,
        "scope": "Feasibility only; blocked performance is not decision evidence",
    }
    assert "999999" not in json.dumps(evidence)
    assert "999" not in json.dumps(evidence["current_account"])
    for row in evidence["configuration_context"]["previous"]:
        if row.get("outcome") == "blocked":
            assert "observed_execution_id" not in row and "exact_execution_id" not in row


def test_incomplete_job_cannot_supply_a_recovery_or_performance_seed(blocked, monkeypatch):
    fixture, plan, job_id, _ = blocked
    fixture.jobs[job_id].status = "failed"
    fixture.jobs[job_id].result_artifact = None
    monkeypatch.setattr(
        supervisor,
        "_recovery_record",
        lambda *a: pytest.fail("An incomplete job cannot be diagnosed as checked feasibility"),
    )
    result = supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert result["status"] == "stopped"
    assert not result.get("blocked_recovery")
    assert result["seed_job_id"] == ROOT
    assert "Native attempt blocked" in result["stopping_reason"]
    assert "No supported unspent" not in result["stopping_reason"]


def test_explicit_model_stop_is_not_revived_by_a_feasibility_branch(blocked, monkeypatch):
    fixture, plan, _, _ = blocked
    supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    change(
        fixture,
        plan,
        lambda state: state.update(
            status="stopped",
            stop_decision={"reason": "No justified distinct test remains", "selection": "model"},
        ),
    )
    monkeypatch.setattr(
        supervisor,
        "_experiment_catalog",
        lambda *a: {"options": [], "feedback": {}, "unavailable": []},
    )
    before = read(fixture, plan)
    after = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert after["blocked_recovery"] == before["blocked_recovery"]
    assert after["stop_decision"] == before["stop_decision"]
    assert after["status"] == "stopped" and not after.get("frontier_transitions")


def test_exhausted_repair_reports_actual_constraints_and_remaining_budget(blocked, monkeypatch):
    fixture, plan, _, _ = blocked
    supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    monkeypatch.setattr(
        supervisor,
        "_experiment_catalog",
        lambda *a: {"options": [], "feedback": {}, "unavailable": []},
    )
    result = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert "percentage_policy=41" in result["continuation_stop_reason"]
    assert "blocked setup was not validated" in result["continuation_stop_reason"]
    assert result["remaining_accounts"] == 94
    assert result["proposals"][-1]["outcome"] == "blocked"


def test_exhausted_repair_can_return_to_a_fully_checked_branch_without_new_budget(
    blocked, monkeypatch
):
    fixture, plan, blocked_job_id, _ = blocked
    supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    checked_job_id = "c" * 32
    change(fixture, plan, lambda state: state["proposals"][0].update(job_id=checked_job_id))
    fixture.jobs[checked_job_id] = SimpleNamespace(
        id=checked_job_id, owner=OWNER, status="queued", result_artifact=None
    )
    complete(fixture, plan, checked_job_id)
    previous_catalogue = supervisor._experiment_catalog

    def exhausted_repair(report, recipe, current):
        if report.get("supervisor_validation", {}).get("outcome") == "blocked":
            return {"options": [], "feedback": {}, "unavailable": []}
        return previous_catalogue(report, recipe, current)

    monkeypatch.setattr(supervisor, "_experiment_catalog", exhausted_repair)
    before = read(fixture, plan)
    result = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert before["seed_job_id"] == blocked_job_id
    assert result["seed_job_id"] == checked_job_id
    assert result["status"] == "awaiting_plan" and not result.get("blocked_recovery")
    assert result["remaining_accounts"] == before["remaining_accounts"] == 94
    assert result["automation"]["calls_spent"] == before["automation"]["calls_spent"]
    assert result["proposals"][-1]["outcome"] == "blocked"
    assert result["latest_conclusion"]["outcome"] == "rejected"
    assert result["frontier_transitions"][-1]["attempts_already_spent"] == 418
