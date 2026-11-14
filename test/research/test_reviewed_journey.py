"""Reviewed-plan fences and persisted flow; no broker or full scanner calls."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_jobs import app, client  # noqa: F401
from test_library import BASE, OWNER, create, draft_for

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchJourney,
    ResearchPreparation,
    ResearchWorker,
)
from research.report_contract import fingerprint
from services import research_journey as journey
from services import research_library as library
from services import scanner_research_service as service


@pytest.fixture
def ready(app, client, monkeypatch):
    study = create(client, draft_for(client))
    store = app.extensions["research_store"]
    monkeypatch.setattr(journey.preparation, "start", lambda *a, **k: {})
    progress = {
        "job_id": "p" * 32,
        "status": "completed",
        "ready": True,
        "progress": 100,
        "activity": None,
        "error": None,
    }
    monkeypatch.setattr(journey.preparation, "status", lambda *a, **k: deepcopy(progress))

    def prepared(_store, _owner, identifier):
        draft = library.get_experiment(store, OWNER, identifier)["draft"]
        p = library.portfolio_payload(draft)
        return (
            deepcopy(progress),
            {
                "portfolio": p,
                "signals": [{"symbol": "ABC", "date": "2026-01-05"}],
                "signal_coverage": {
                    "eligible_signals": 1,
                    "exclusions": [{"symbol": "MISSING", "reason": "No price"}],
                },
                "automatic_recipe": {"budget": {"proposals": 408, "max_simulations": 512}},
            },
            "e" * 64,
        )

    monkeypatch.setattr(journey, "_prepared", prepared)
    result = journey.prepare(store, OWNER, study["id"], {"revision": study["revision"]})
    return store, result


def test_preparation_produces_editable_plan_without_starting_calculations(ready):
    store, state = ready
    assert state["phase"] == "review" and state["can_start"]
    assert state["plan"]["data"]["excluded"] == [{"symbol": "MISSING", "reason": "No price"}]
    assert state["plan"]["data"]["excluded_count"] == 1
    assert state["plan"]["settings"]["capital"] == 20000
    assert state["plan"]["contract"]["signal_policy"] == "fixed_signals"
    assert state["plan"]["budget"] == {"proposals": 408, "max_simulations": 512}
    with store.sessions() as db:
        assert db.query(ResearchJob).count() == 0


def test_start_rejects_missing_or_stale_reviewed_plan(ready):
    store, state = ready
    identifier = state["experiment"]["id"]
    with pytest.raises(ValueError, match="Review"):
        journey.start(
            store,
            OWNER,
            identifier,
            {"revision": state["revision"], "plan_id": "wrong", "request_id": "start-wrong-plan"},
        )
    with pytest.raises(library.RevisionConflict):
        journey.start(
            store,
            OWNER,
            identifier,
            {
                "revision": state["revision"] - 1,
                "plan_id": state["plan"]["id"],
                "request_id": "start-stale-plan",
            },
        )


def test_editing_style_preserves_risk_limits_and_invalidates_plan(ready):
    store, state = ready
    identifier = state["experiment"]["id"]
    limits = state["plan"]["contract"]["risk_limits"]
    old = state["plan"]["id"]
    new = journey.edit_plan(
        store,
        OWNER,
        identifier,
        {"revision": state["revision"], "style": "intraday", "capital": 45000},
    )
    assert new["plan"]["id"] != old and new["revision"] > state["revision"]
    assert new["plan"]["style"] == "intraday"
    assert new["plan"]["contract"]["risk_limits"] == limits
    assert new["plan"]["settings"]["capital"] == 45000
    with pytest.raises(ValueError, match="Review"):
        journey.start(
            store,
            OWNER,
            identifier,
            {"revision": new["revision"], "plan_id": old, "request_id": "old-plan-after-edit"},
        )


def test_current_review_idempotently_reuses_plan_and_history(ready):
    store, state = ready
    identifier = state["experiment"]["id"]
    again = journey.prepare(store, OWNER, identifier, {"revision": state["revision"]})
    assert again["plan"]["id"] == state["plan"]["id"] and again["revision"] == state["revision"]
    journey._finish(
        store, OWNER, identifier, {"job_id": None, "experiment_id": identifier, "after": {}}
    )
    final = journey.status(store, OWNER, identifier)
    assert final["phase"] == "none" and final["result"]["headline"] == journey.NO_RESULT
    with store.sessions() as db:
        history = json.loads(db.get(ResearchJourney, identifier).state)["history"]
        assert len(history) == 1 and history[0]["outcome"] == "rejected"


def test_metadata_rename_refreshes_review_without_repreparing_data(ready, monkeypatch):
    store, state = ready
    identifier = state["experiment"]["id"]
    renamed = library.update_experiment(
        store, OWNER, identifier, {"revision": state["revision"], "name": "Renamed study"}
    )

    def no_acquisition(*args, **kwargs):
        raise AssertionError("An unchanged draft must not start another preparation")

    monkeypatch.setattr(journey.preparation, "start", no_acquisition)
    refreshed = journey.prepare(store, OWNER, identifier, {"revision": renamed["revision"]})
    assert refreshed["phase"] == "review" and refreshed["can_start"]
    assert refreshed["plan"]["revision"] == renamed["revision"]
    assert refreshed["plan"]["id"] != state["plan"]["id"]
    for field in ("draft_id", "inputs_artifact", "settings", "contract", "budget"):
        assert refreshed["plan"][field] == state["plan"][field]
    with store.sessions() as db:
        assert db.query(ResearchJob).count() == 0


@pytest.mark.parametrize("completed", [False, True])
def test_replaced_preparation_refreshes_review_and_waits_for_current_data(
    ready, monkeypatch, completed
):
    store, state = ready
    identifier = state["experiment"]["id"]
    replacement_id = "q" * 32
    with store.sessions.begin() as db:
        db.add(
            ResearchPreparation(
                experiment_id=identifier, owner=OWNER, job_id=replacement_id,
                binding="b" * 64, revision=state["revision"],
            )
        )
    original_prepared = journey._prepared
    finished = [completed]

    def current_prepared(*args):
        progress, evidence, identity = original_prepared(*args)
        progress.update(job_id=replacement_id, ready=finished[0],
                        status="completed" if finished[0] else "running")
        return progress, evidence if finished[0] else None, "f" * 64 if finished[0] else None

    def no_acquisition(*args, **kwargs):
        raise AssertionError("The current replacement preparation must be reused")

    monkeypatch.setattr(journey, "_prepared", current_prepared)
    monkeypatch.setattr(journey.preparation, "start", no_acquisition)
    refreshed = journey.prepare(store, OWNER, identifier, {"revision": state["revision"]})
    if not completed:
        assert refreshed["phase"] == "preparing"
        assert refreshed["plan"] is None and not refreshed["can_start"]
        finished[0] = True
        assert journey._review_ready(store, OWNER, identifier)
        refreshed = journey.status(store, OWNER, identifier)
    assert refreshed["phase"] == "review" and refreshed["can_start"]
    assert refreshed["plan"]["preparation_job_id"] == replacement_id
    assert refreshed["plan"]["inputs_artifact"] == "f" * 64
    assert refreshed["plan"]["id"] != state["plan"]["id"]
    assert refreshed["plan"]["budget"] == state["plan"]["budget"]
    with pytest.raises(ValueError, match="Review"):
        journey.start(store, OWNER, identifier, {
            "revision": state["revision"], "plan_id": state["plan"]["id"],
            "request_id": "superseded-preparation-plan",
        })
    with store.sessions() as db:
        assert db.query(ResearchJob).count() == 0


@pytest.mark.parametrize("phase", ["researching", "finalizing", "complete", "none"])
def test_preparation_replacement_cannot_reset_admitted_or_finished_journey(
    ready, monkeypatch, phase
):
    store, state = ready
    identifier = state["experiment"]["id"]
    with store.sessions.begin() as db:
        row = db.get(ResearchJourney, identifier)
        row.phase = phase
        frozen = row.state
        db.add(ResearchPreparation(
            experiment_id=identifier, owner=OWNER, job_id="q" * 32,
            binding="b" * 64, revision=state["revision"],
        ))

    def no_acquisition(*args, **kwargs):
        raise AssertionError("An admitted or finished journey must not be prepared again")

    monkeypatch.setattr(journey.preparation, "start", no_acquisition)
    again = journey.prepare(store, OWNER, identifier, {"revision": state["revision"]})
    assert again["phase"] == phase
    with store.sessions() as db:
        assert db.get(ResearchJourney, identifier).state == frozen
        assert db.query(ResearchJob).count() == 0


def test_review_refresh_rejects_another_owners_preparation(ready):
    store, state = ready
    identifier = state["experiment"]["id"]
    with store.sessions.begin() as db:
        db.add(ResearchPreparation(
            experiment_id=identifier, owner="another-owner", job_id="q" * 32,
            binding="b" * 64, revision=state["revision"],
        ))
    with pytest.raises(LookupError, match="preparation"):
        journey.prepare(store, OWNER, identifier, {"revision": state["revision"]})
    with store.sessions() as db:
        row = db.get(ResearchJourney, identifier)
        assert row.phase == "review" and json.loads(row.state)["plan"] == state["plan"]


def test_review_and_state_are_owner_scoped(ready):
    store, state = ready
    with pytest.raises(LookupError):
        journey.status(store, "another-owner", state["experiment"]["id"])


def test_worker_requires_its_own_active_lease(ready):
    store, _ = ready
    with pytest.raises(ValueError, match="lease"):
        journey.run_one(store, "not-owner")
    with store.sessions.begin() as db:
        lease = db.get(ResearchWorker, 1)
        lease.token, lease.heartbeat = "worker", time.time()
    assert not journey.run_one(store, "worker")
    assert not journey.run_one(store, "worker", stop_requested=lambda: True)


def test_review_http_does_not_launch_and_unknown_fields_cannot_widen_plan(app, client, ready):
    _, state = ready
    path = f"{BASE}/experiments/{state['experiment']['id']}/journey"
    assert client.get(path).json["phase"] == "review"
    response = client.put(
        path + "/plan",
        json={"revision": state["revision"], "risk_limits": {"max_trade_risk_pct": 100}},
    )
    assert response.status_code == 400
    assert client.get(path).json["can_start"]


@pytest.mark.parametrize("channels", [[], ["slack"]])
def test_start_pins_exact_reviewed_snapshot_and_retry_does_not_reset_work(
    ready, monkeypatch, channels
):
    from services import research_notifications, research_portfolio
    from services.agent import builder

    store, state = ready
    identifier = state["experiment"]["id"]
    evidence = research_portfolio.resolve_inputs(
        store, OWNER, library.portfolio_payload(state["experiment"]["draft"])
    )
    inputs_id = service.save_artifact(store, evidence)
    bundle_id = service.save_artifact(store, {"inputs_artifact": inputs_id})
    # This queue/admission unit test does not manufacture numerical validation.
    validated = []
    monkeypatch.setattr(
        research_portfolio, "validate_prepared_inputs", lambda s, e, p: validated.append((e, p))
    )
    monkeypatch.setattr(
        builder, "resolve_model", lambda: SimpleNamespace(id=1, litellm_id="chatgpt/test")
    )
    source_id = state["experiment"]["draft"]["portfolio"]["strategies"][0]["source_id"]
    with store.sessions.begin() as db:
        row = db.get(ResearchJourney, identifier)
        stored = json.loads(row.state)
        stored["plan"]["inputs_artifact"] = inputs_id
        stored["plan"]["id"] = fingerprint({k: v for k, v in stored["plan"].items() if k != "id"})
        journey._put(row, stored)
        db.add(
            ResearchJob(
                id="p" * 32,
                owner=OWNER,
                source_id=source_id,
                config="{}",
                status="completed",
                progress=100,
                created_at=time.time(),
                updated_at=time.time(),
                result_artifact=bundle_id,
            )
        )
        db.add(
            ResearchPreparation(
                experiment_id=identifier,
                owner=OWNER,
                job_id="p" * 32,
                binding="b" * 64,
                revision=state["revision"],
            )
        )
    state = journey.status(store, OWNER, identifier)
    request = {
        "revision": state["revision"],
        "plan_id": state["plan"]["id"],
        "request_id": "reviewed-snapshot-start",
    }
    if channels:
        request["notification_channels"] = channels
    monkeypatch.setattr(research_notifications, "available", lambda channel: channel == "slack")
    started = journey.start(store, OWNER, identifier, request)
    assert started["phase"] == "researching" and len(validated) == 1
    job_id = started["run"]["job_id"]
    with store.sessions() as db:
        checkpoint = db.get(ResearchExperiment, job_id).checkpoint
        assert db.query(ResearchJob).count() == 2
    receipt = service.read_artifact(store, checkpoint)
    assert receipt["state"]["inputs_artifact"] == inputs_id
    with store.sessions() as db:
        assert receipt["identity"] == db.get(ResearchExperiment, job_id).identity
    assert receipt["policy_version"]
    again = journey.start(store, OWNER, identifier, request)
    assert again["run"]["job_id"] == job_id and len(validated) == 1
    assert started["notification_channels"] == channels
    assert len(again["notifications"]) == len(channels)
    if channels:
        changed_notifications = {k: v for k, v in request.items() if k != "notification_channels"}
        with pytest.raises(ValueError, match="notification choices"):
            journey.start(store, OWNER, identifier, changed_notifications)
    with store.sessions() as db:
        assert db.query(ResearchJob).count() == 2


def test_unavailable_preparation_does_not_start_a_numerical_job(ready):
    store, state = ready
    with pytest.raises(ValueError, match="preparation"):
        journey.start(
            store,
            OWNER,
            state["experiment"]["id"],
            {
                "revision": state["revision"],
                "plan_id": state["plan"]["id"],
                "request_id": "no-usable-preparation",
            },
        )
    with store.sessions() as db:
        assert db.query(ResearchJob).count() == 0


def test_native_experiment_trace_exposes_problem_and_expected_evidence(ready, monkeypatch):
    from services import research_supervisor

    store, state = ready
    identifier = state["experiment"]["id"]
    with store.sessions.begin() as db:
        row = db.get(ResearchJourney, identifier)
        saved = json.loads(row.state)
        saved["supervisor_id"] = "supervisor"
        journey._put(row, saved)
    monkeypatch.setattr(
        research_supervisor,
        "status",
        lambda *a, **k: {
            "proposals": [
                {
                    "remedy": "funding_and_size",
                    "outcome": "rejected",
                    "experiment": {
                        "problem": "Trades lacked cash",
                        "expected_evidence": "Exact settings must pass unchanged checks",
                    },
                }
            ],
            "automation": {
                "decisions": [{"remedy": "funding_and_size", "reason": "Repair cash constraints"}]
            },
        },
    )
    trace = journey.status(store, OWNER, identifier)["decisions"][0]
    assert trace["problem"] == "Trades lacked cash"
    assert trace["expected_evidence"] == "Exact settings must pass unchanged checks"


def test_reasoned_stop_saves_none_and_reopens_without_a_new_job_or_calculation(ready, monkeypatch):
    from services import research_supervisor

    store, state = ready
    identifier = state["experiment"]["id"]
    job_id = "d" * 32
    reason = "Cost and historical-prefix checks failed; no remaining complete configuration has a justified new mechanism."
    strategies = library.portfolio_payload(state["experiment"]["draft"])["strategies"]
    artifact = service.save_artifact(
        store, {"result": {"strategies": strategies, "summary": {"net_pnl": 12}}}
    )
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=job_id,
                owner=OWNER,
                source_id="s" * 64,
                config="{}",
                status="completed",
                progress=100,
                created_at=time.time(),
                updated_at=time.time(),
                result_artifact=artifact,
            )
        )
        row = db.get(ResearchJourney, identifier)
        saved = json.loads(row.state)
        saved.update(supervisor_id="stopped-supervisor", job_id=job_id)
        journey._put(row, saved, "researching")
    monkeypatch.setattr(
        research_supervisor,
        "status",
        lambda *a, **k: {
            "proposals": [],
            "automatic_phase": "stopped",
            "automation": {},
            "stopping_reason": reason,
        },
    )
    assert journey._advance(store, OWNER, identifier)
    with store.sessions() as db:
        row = db.get(ResearchJourney, identifier)
        reopened = json.loads(row.state)
        assert row.phase == "none" and not reopened["error"]
        assert reopened["result"]["headline"] == journey.NO_RESULT
        assert reopened["result"]["stopping_reason"] == reason
        assert reopened["result"]["accepted"] is False
        assert reopened["result"]["verification"]["matched"] is True
        assert reopened["result"]["verification"]["new_calculations"] == 0
        assert db.query(ResearchJob).count() == 1
    assert journey._reopen_native(store, OWNER, job_id)["result_artifact"] == artifact
    assert not journey._advance(store, OWNER, identifier)
