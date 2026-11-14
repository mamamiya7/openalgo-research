"""Supervisor metadata survives maintenance without running any backtests."""

import json
import time

import pytest
from sqlalchemy import select

from database.research_db import (
    ResearchChosenSetup,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchRequest,
    ResearchSource,
    ResearchStore,
    ResearchSupervisor,
)
from research.report_contract import fingerprint
from services import research_storage as storage
from services import scanner_research_service as service


@pytest.fixture
def saved(tmp_path):
    store = ResearchStore(tmp_path / "supervisor-store")
    store.initialize()
    source = service.save_artifact(store, {"fixture": "saved source"})
    artifact = service.save_artifact(
        store,
        {
            "result": {
                "automatic_research": {
                    "counts": {"attempts_reserved": 100, "simulations": 90},
                    "recipe": {
                        "version": "automatic-hypotheses-v16",
                        "validation_mode": "exploratory_only",
                    },
                }
            }
        },
    )
    now = time.time()
    state = {
        "version": "research-supervisor-v1",
        "objective": "robust_improvement",
        "max_experiments": 3,
        "original_attempts": 100,
        "original_simulations": 90,
        "proposals": [],
        "status": "planning",
        "validation_mode": "exploratory_only",
        "accepted_job_id": None,
        "saved_result": None,
    }
    with store.sessions.begin() as db:
        db.add(ResearchSource(id="1" * 32, owner="alice", artifact=source, created_at=now))
        db.add(
            ResearchJob(
                id="2" * 32,
                owner="alice",
                source_id="1" * 32,
                config="{}",
                status="completed",
                created_at=now,
                updated_at=now,
                result_artifact=artifact,
            )
        )
        db.add(
            ResearchSupervisor(
                id="3" * 32,
                owner="alice",
                root_job_id="2" * 32,
                root_artifact=artifact,
                conversation_id="41",
                request_id="request-123",
                state=json.dumps(state),
                updated_at=now,
            )
        )
    try:
        yield store, state, artifact
    finally:
        store.close()


def write_state(saved):
    store, state, _ = saved
    with store.sessions.begin() as db:
        db.get(ResearchSupervisor, "3" * 32).state = json.dumps(state)


def proposal(saved, *, seed=None, artifact=None, remedy="funding_and_size"):
    _, _, root_artifact = saved
    settings = [
        {"id": "s", "name": "Saved rules", "allocation_pct": 100, "config": {"hold_sessions": 3}}
    ]
    source = artifact or root_artifact
    policy = saved[1].get("capital_policy_version")
    identity = fingerprint(
        {
            "remedy": remedy,
            "settings": settings,
            "source_result_artifact": source,
            "root_job_id": "2" * 32,
            **({"capital_policy_version": policy} if policy else {}),
        }
    )
    return {
        "id": identity,
        **({"capital_policy_version": policy} if policy else {}),
        "remedy": remedy,
        "rationale": "Native affordability evidence",
        "settings": settings,
        "seed_job_id": seed or "2" * 32,
        "source_result_artifact": source,
        "reserved_accounts": 17 if remedy == "funding_and_size" else 14,
        "spent": 0,
        "completed": 0,
        "job_id": None,
        "outcome": None,
    }


def add_child(saved, item, *, child="4" * 32, outcome="rejected"):
    store, _, _ = saved
    artifact = service.save_artifact(store, {"result": {"fixture": child}})
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=child,
                owner="alice",
                source_id="1" * 32,
                config="{}",
                status="completed",
                created_at=time.time(),
                updated_at=time.time(),
                result_artifact=artifact,
            )
        )
        db.add(
            ResearchRequest(
                owner="alice", token="supervisor_" + item["id"], job_id=child, payload_hash="e" * 64
            )
        )
    item.update(job_id=child, outcome=outcome, spent=2, completed=2)
    return artifact


def refs(store, *, artifacts=False):
    with store.engine.connect() as db:
        tables = set(
            db.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").scalars()
        )
        return storage._supervisor_references(db, tables, store=store if artifacts else None)


def test_plan_root_is_pinned_and_previous_store_without_additive_table_is_valid(saved):
    store, _, artifact = saved
    assert refs(store, artifacts=True) == {artifact}
    assert artifact in storage._references(store.engine, store=store)
    with store.engine.begin() as db:
        db.exec_driver_sql("DROP TABLE research_supervisors")
    assert refs(store) == set()
    assert artifact in storage._references(store.engine, store=store)


def test_rejected_child_can_seed_exact_next_development_proposal(saved):
    store, state, root_artifact = saved
    first = proposal(saved)
    child_artifact = add_child(saved, first)
    second = proposal(saved, seed=first["job_id"], artifact=child_artifact, remedy="lower_turnover")
    state.update(
        proposals=[first, second],
        latest_source_result_artifact=child_artifact,
        latest_seed_job_id=first["job_id"],
    )
    write_state(saved)
    assert refs(store, artifacts=True) == {root_artifact, child_artifact}


def test_real_open_and_propose_service_ledger_passes_maintenance_without_calculations(saved):
    from services import research_supervisor as supervisor

    store, _, original = saved
    root = service.read_artifact(store, original)
    root["result"]["strategies"] = proposal(saved)["settings"]
    root["result"]["automatic_research"]["decision"] = {
        "capital_diagnosis": {"cash_shortfall": 2},
        "checks": [],
    }
    artifact = service.save_artifact(store, root)
    with store.sessions.begin() as db:
        db.delete(db.get(ResearchSupervisor, "3" * 32))
        db.get(ResearchJob, "2" * 32).result_artifact = artifact
    opened = supervisor.open_supervisor(
        store, "alice", "2" * 32, conversation_id="41", request_id="actual-open-123"
    )
    assert opened["status"] == "planning"
    queued = supervisor.propose(
        store,
        "alice",
        opened["id"],
        "funding_and_size",
        "Cash shortfall from native evidence",
        conversation_id="41",
    )["proposal"]
    assert queued["seed_job_id"] == "2" * 32
    assert queued["source_result_artifact"] == artifact
    assert queued["spent"] == queued["completed"] == 0
    assert refs(store, artifacts=True) == {artifact}


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "attempts",
        "simulations",
        "size",
        "reservation",
        "spent",
        "completed",
        "proposal_identity",
        "seed",
        "source",
        "outcome_without_job",
        "accepted_without_pass",
        "saved_without_accept",
        "duplicate",
        "mode",
        "oversized",
    ],
)
def test_changed_or_unbounded_plan_fails_closed(saved, change):
    store, state, _ = saved
    item = proposal(saved)
    state["proposals"] = [item]
    if change == "version":
        state["version"] = "future-unchecked"
    elif change == "attempts":
        state["original_attempts"] = 513
    elif change == "simulations":
        state["original_simulations"] = 101
    elif change == "size":
        state["max_experiments"] = 4
    elif change == "reservation":
        item["reserved_accounts"] = 1
    elif change == "spent":
        item["spent"] = 18
    elif change == "completed":
        item["completed"] = 1
    elif change == "proposal_identity":
        item["settings"][0]["config"]["hold_sessions"] = 9
    elif change == "seed":
        item["seed_job_id"] = "f" * 32
    elif change == "source":
        item["source_result_artifact"] = "f" * 64
    elif change == "outcome_without_job":
        item["outcome"] = "validated"
    elif change == "accepted_without_pass":
        state.update(status="accepted", accepted_job_id="4" * 32)
    elif change == "saved_without_accept":
        state.update(status="saved", saved_result={})
    elif change == "duplicate":
        state["proposals"].append(dict(item))
    elif change == "mode":
        state["validation_mode"] = "skip_validation"
    elif change == "oversized":
        state["extra"] = "x" * 131073
    write_state(saved)
    with pytest.raises(ValueError, match="supervisor"):
        refs(store)


@pytest.mark.parametrize("change", ["owner", "root", "artifact", "conversation", "request", "date"])
def test_changed_grant_identity_fails_closed(saved, change):
    store, _, _ = saved
    with store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, "3" * 32)
        if change == "owner":
            row.owner = "mallory"
        elif change == "root":
            row.root_job_id = "f" * 32
        elif change == "artifact":
            row.root_artifact = "f" * 64
        elif change == "conversation":
            row.conversation_id = ""
        elif change == "request":
            row.request_id = "bad"
        elif change == "date":
            row.updated_at = -1
    with pytest.raises(ValueError, match="supervisor"):
        refs(store)


@pytest.mark.parametrize(
    "change", ["child_owner", "request_missing", "request_wrong_job", "request_owner", "root_count"]
)
def test_child_request_and_original_accounting_stay_bound(saved, change):
    store, state, _ = saved
    item = proposal(saved)
    add_child(saved, item)
    state["proposals"] = [item]
    write_state(saved)
    with store.sessions.begin() as db:
        request = db.get(ResearchRequest, ("alice", "supervisor_" + item["id"]))
        if change == "child_owner":
            db.get(ResearchJob, item["job_id"]).owner = "mallory"
        elif change == "request_missing":
            db.delete(request)
        elif change == "request_wrong_job":
            request.job_id = "2" * 32
        elif change == "request_owner":
            request.owner = "mallory"
        elif change == "root_count":
            state["original_attempts"] = 99
            db.get(ResearchSupervisor, "3" * 32).state = json.dumps(state)
    with pytest.raises(ValueError, match="supervisor"):
        refs(store, artifacts=True)


def test_optional_old_completed_counter_and_shared_512_ceiling(saved):
    store, state, _ = saved
    item = proposal(saved)
    item.pop("completed")
    state["proposals"] = [item]
    write_state(saved)
    assert refs(store)
    state["original_attempts"] = 500
    write_state(saved)
    with pytest.raises(ValueError, match="supervisor"):
        refs(store)


@pytest.mark.parametrize(
    "change", [None, "choice_owner", "experiment", "report", "validation_id", "latest_seed"]
)
def test_saved_reference_matches_exact_accepted_native_child(saved, change):
    store, state, _ = saved
    item = proposal(saved)
    artifact = add_child(saved, item, outcome="validated")
    state.update(
        proposals=[item],
        accepted_job_id=item["job_id"],
        status="saved",
        saved_result={
            "experiment_id": "5" * 32,
            "choice_id": "6" * 32,
            "supervisor_validation_id": "a" * 64,
        },
    )
    snapshot = {"report_job_id": item["job_id"], "report_result_artifact": artifact}
    with store.sessions.begin() as db:
        db.add(
            ResearchLibraryExperiment(
                id="5" * 32,
                owner="alice",
                name="Checked result",
                draft="{}",
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        db.add(
            ResearchChosenSetup(
                id="6" * 32,
                owner="mallory" if change == "choice_owner" else "alice",
                experiment_id="5" * 32,
                sequence=1,
                name="Checked result",
                decision_id="7" * 32,
                event_id="8" * 32,
                version_id="9" * 32,
                snapshot=json.dumps(snapshot),
                created_at=time.time(),
            )
        )
    if change == "experiment":
        state["saved_result"]["experiment_id"] = "f" * 32
    elif change == "report":
        with store.sessions.begin() as db:
            snapshot["report_job_id"] = "2" * 32
            db.get(ResearchChosenSetup, "6" * 32).snapshot = json.dumps(snapshot)
    elif change == "validation_id":
        state["saved_result"]["supervisor_validation_id"] = "invalid"
    elif change == "latest_seed":
        state.update(latest_seed_job_id="f" * 32, latest_source_result_artifact=artifact)
    write_state(saved)
    if change is None:
        assert artifact in refs(store, artifacts=True)
    else:
        with pytest.raises(ValueError, match="supervisor"):
            refs(store, artifacts=True)


@pytest.mark.parametrize(
    "policy", [None, "research-capital-planning-v7", "research-capital-planning-v8"]
)
def test_tiny_backup_restore_keeps_full_ledger_request_and_exact_artifacts(saved, tmp_path, policy):
    store, state, root = saved
    if policy:
        state["capital_policy_version"] = policy
    item = proposal(saved)
    child = add_child(saved, item)
    state.update(
        proposals=[item],
        latest_source_result_artifact=child,
        status="awaiting_plan",
        latest_conclusion={"outcome": "rejected", "reasons": ["Costs remain fragile"]},
    )
    write_state(saved)
    with store.sessions() as db:
        raw = db.get(ResearchSupervisor, "3" * 32).state
    storage.backup_store(store, tmp_path / "backup")
    storage.restore_store(tmp_path / "backup", tmp_path / "restored")
    restored = ResearchStore(tmp_path / "restored")
    try:
        with restored.sessions() as db:
            row = db.get(ResearchSupervisor, "3" * 32)
            assert row.state == raw and row.root_artifact == root and row.conversation_id == "41"
            request = db.scalar(select(ResearchRequest).where(ResearchRequest.owner == "alice"))
            assert request.token == "supervisor_" + item["id"] and request.job_id == item["job_id"]
        assert refs(restored, artifacts=True) == {root, child}
        assert service.read_artifact(restored, root) == service.read_artifact(store, root)
        assert service.read_artifact(restored, child) == service.read_artifact(store, child)
    finally:
        restored.close()
