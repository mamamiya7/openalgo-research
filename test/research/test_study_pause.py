"""Cooperative native study pause preserves the exact completed-proposal boundary."""

# ruff: noqa: F811 -- isolated shared Flask fixtures
import copy
import json
import time

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from test_jobs import app, client
from test_study_continuation import body, setup

from blueprints.scanner_research import scanner_research_bp
from database.research_db import ResearchExperiment, ResearchJob, ResearchWorker
from research.connectors import vectorbt_portfolio
from services import research_study_activity as activity
from services import research_study_continuation as continuation
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_storage import backup_store, restore_store

OWNER = "research-test"


def path(job_id, action="pause"):
    return f"/scanner-research/api/jobs/{job_id}/{action}"


def run(store, token="pause-test"):
    worker.acquire(store, token)
    try:
        assert worker.run_one(store, token)
    finally:
        worker.release(store, token)


def extend(store, experiment, parent, token="pause-child", additional=4):
    return continuation.extend(
        store,
        OWNER,
        experiment,
        parent,
        body(store, experiment, parent, additional=additional, token=token),
    )["job"]["id"]


def normalized_trials(result):
    return [
        {k: v for k, v in row.items() if not k.startswith("datetime_")}
        for row in result["experiment"]["trials"]
    ]


@pytest.mark.parametrize("sampler", ["grid", "tpe"])
def test_inflight_pause_commits_trial_then_resumes_exactly_without_recalculation(
    app, client, monkeypatch, sampler, tmp_path_factory
):
    store, experiment, parent = setup(app, client, monkeypatch, sampler=sampler)
    parent_job = service.get_job(store, OWNER, parent)
    parent_bytes = service.read_artifact(store, parent_job.result_artifact)
    child = extend(store, experiment, parent)
    assert not service.job_receipt(store, service.get_job(store, OWNER, child))["pausable"]
    actual = vectorbt_portfolio.evaluate
    calls = []

    def pause_inflight(*args, **kwargs):
        calls.append(copy.deepcopy(args[0]))
        assert service.job_receipt(store, service.get_job(store, OWNER, child))["pausable"]
        response = client.post(path(child))
        assert response.status_code == 200 and response.json["status"] == "pausing"
        assert client.post(path(child)).json["status"] == "pausing"
        # This real engine's progress calls must finish despite the pause request.
        return actual(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", pause_inflight)
    run(store)
    assert len(calls) == 1
    paused = service.job_receipt(store, service.get_job(store, OWNER, child))
    assert paused["status"] == "paused" and paused["resumable"] and not paused["pausable"]
    assert paused["evidence_id"] is None
    with store.sessions() as db:
        checkpoint_id = db.get(ResearchExperiment, child).checkpoint
    checkpoint = service.read_artifact(store, checkpoint_id)
    trials = checkpoint["state"]["calculation"]["trials"]
    assert len(trials) == paused["activity"]["trials"]["completed"]
    assert paused["activity"]["trials"]["active_trial"] is None
    observed = activity.read(store, OWNER, child)
    assert observed["executions"][0]["state"] == "paused"
    assert observed["executions"][0]["finished_at"] is not None
    assert observed["executions"][0]["reason_code"] == "pause_requested"
    assert all(row["checkpointed"] for row in observed["rows"])
    assert all(row["state"] != "running" for row in observed["rows"])

    # Paused state is at rest and remains valid in backup/restore, including its
    # execution metadata and immutable continuation parent.
    outside = tmp_path_factory.mktemp("pause-backup")
    backup_store(store, outside / "archive")
    restore_store(outside / "archive", outside / "restored")

    def counted(*args, **kwargs):
        calls.append(copy.deepcopy(args[0]))
        return actual(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", counted)
    resumed = client.post(path(child, "resume"))
    assert resumed.json["status"] == "queued" and resumed.json["pausable"]
    assert client.post(path(child, "resume")).json["status"] == "queued"
    run(store, "pause-resume")
    job = service.get_job(store, OWNER, child)
    assert job.status == "completed", job.error
    result = service.read_artifact(store, job.result_artifact)["result"]
    parent_rows = parent_bytes["result"]["experiment"]["rows"]
    assert len(calls) == len(result["experiment"]["rows"]) - len(parent_rows)
    assert len({service.encoded(settings) for settings in calls}) == len(calls)
    assert service.read_artifact(store, checkpoint_id) == checkpoint
    assert service.read_artifact(store, parent_job.result_artifact) == parent_bytes
    assert result["reserved_evaluation"] == parent_bytes["result"]["reserved_evaluation"]

    control = extend(store, experiment, parent, token="uninterrupted-child")
    monkeypatch.setattr(vectorbt_portfolio, "evaluate", actual)
    run(store, "uninterrupted")
    complete = service.read_artifact(store, service.get_job(store, OWNER, control).result_artifact)
    assert normalized_trials(result) == normalized_trials(complete["result"])
    assert result["experiment"]["rows"] == complete["result"]["experiment"]["rows"]
    assert result["summary"] == complete["result"]["summary"]


def frozen_checkpoint(app, client, monkeypatch):
    store, experiment, parent = setup(app, client, monkeypatch)
    child = extend(store, experiment, parent)
    original = worker.save_artifact

    def interrupt_after_frozen(*args, **kwargs):
        value = args[1]
        if value.get("state", {}).get("calculation") is not None:
            raise worker.Interrupted()
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, "save_artifact", interrupt_after_frozen)
    run(store)
    monkeypatch.setattr(worker, "save_artifact", original)
    with store.sessions() as db:
        checkpoint_id = db.get(ResearchExperiment, child).checkpoint
    saved = service.read_artifact(store, checkpoint_id)
    assert saved["state"]["phase"] == "calculation"
    assert saved["state"]["calculation"] is None
    return store, child, checkpoint_id


def test_ready_queued_resume_can_pause_without_worker_or_new_evaluation(app, client, monkeypatch):
    store, child, checkpoint_id = frozen_checkpoint(app, client, monkeypatch)
    service.resume(store, OWNER, child)
    assert client.post(path(child)).json["status"] == "paused"
    assert client.post(path(child)).json["status"] == "paused"
    worker.acquire(store, "idle-paused")
    try:
        assert worker.run_one(store, "idle-paused") is False
    finally:
        worker.release(store, "idle-paused")
    with store.sessions() as db:
        assert db.get(ResearchExperiment, child).checkpoint == checkpoint_id
    service.resume(store, OWNER, child)
    run(store, "ready-resume")
    assert service.get_job(store, OWNER, child).status == "completed"


def test_pause_at_existing_boundary_admits_no_new_proposal(app, client, monkeypatch):
    store, child, checkpoint_id = frozen_checkpoint(app, client, monkeypatch)
    from services import research_portfolio

    native = research_portfolio.run

    def request_at_boundary(*args, boundary, **kwargs):
        def pause_then_check():
            assert service.pause(store, OWNER, child)["status"] == "pausing"
            boundary()

        return native(*args, boundary=pause_then_check, **kwargs)

    monkeypatch.setattr(research_portfolio, "run", request_at_boundary)
    monkeypatch.setattr(
        vectorbt_portfolio, "evaluate", lambda *a, **k: pytest.fail("new evaluation")
    )
    service.resume(store, OWNER, child)
    run(store, "before-proposal")
    assert service.get_job(store, OWNER, child).status == "paused"
    with store.sessions() as db:
        assert db.get(ResearchExperiment, child).checkpoint == checkpoint_id


@pytest.mark.parametrize("race", ["cancel", "lease", "shutdown"])
def test_stop_or_worker_loss_during_pending_pause_preserves_fences(app, client, monkeypatch, race):
    store, experiment, parent = setup(app, client, monkeypatch)
    child = extend(store, experiment, parent)
    actual = vectorbt_portfolio.evaluate

    def stop_after_pause(*args, **kwargs):
        service.pause(store, OWNER, child)
        if race == "cancel":
            assert service.cancel(store, OWNER, child)["status"] == "cancelling"
            assert service.pause(store, OWNER, child)["status"] == "cancelling"
        elif race == "lease":
            with store.sessions.begin() as db:
                db.get(ResearchWorker, 1).heartbeat = time.time() - 121
            worker.acquire(store, "successor")
        else:
            raise worker.Interrupted()
        return actual(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", stop_after_pause)
    run(store)
    expected = "cancelled" if race == "cancel" else "interrupted"
    assert service.get_job(store, OWNER, child).status == expected
    assert service.get_job(store, OWNER, child).result_artifact is None
    if race == "lease":
        worker.release(store, "successor")
    observed = activity.read(store, OWNER, child)
    assert not any(row["state"] == "running" for row in observed["rows"])


def test_pause_auth_csrf_capability_integrity_and_completed_retry(app, client, monkeypatch):
    store, experiment, parent = setup(app, client, monkeypatch)
    child = extend(store, experiment, parent)
    assert app.test_client().post(path(child)).status_code == 401
    with pytest.raises(LookupError):
        service.pause(store, "other-owner", child)
    assert client.post(path(child)).status_code == 400  # No frozen checkpoint yet.
    assert client.post(path(parent)).json["status"] == "completed"
    protected = Flask("pause-csrf")
    protected.config.update(SECRET_KEY="pause-test", RESEARCH_DATA_DIR=str(store.root))
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    browser = protected.test_client()
    with browser.session_transaction() as session:
        session["user"] = OWNER
    try:
        response = browser.post(path(child))
        assert response.status_code == 400 and b"csrf" in response.data.lower()
    finally:
        protected.extensions["research_store"].close()


def test_paused_stop_and_forged_calculation_metadata_cannot_bypass_checkpoint(
    app, client, monkeypatch
):
    store, child, checkpoint_id = frozen_checkpoint(app, client, monkeypatch)
    service.resume(store, OWNER, child)
    saved = service.read_artifact(store, checkpoint_id)
    saved["state"]["phase"] = "prices"
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, child).checkpoint = service.save_artifact(store, saved)
    assert client.post(path(child)).status_code == 400
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, child).checkpoint = checkpoint_id
    assert service.pause(store, OWNER, child)["status"] == "paused"
    assert service.cancel(store, OWNER, child)["status"] == "cancelled"
    assert service.pause(store, OWNER, child)["status"] == "cancelled"


def test_completion_can_win_final_pause_race_without_rewriting_evidence(app, client, monkeypatch):
    store, experiment, parent = setup(app, client, monkeypatch)
    child = extend(store, experiment, parent)
    native = worker.save_artifact

    def pause_before_result(*args, **kwargs):
        value = args[1]
        if value.get("job_id") == child and "result" in value:
            assert service.pause(store, OWNER, child)["status"] == "pausing"
        return native(*args, **kwargs)

    monkeypatch.setattr(worker, "save_artifact", pause_before_result)
    run(store)
    job = service.get_job(store, OWNER, child)
    assert job.status == "completed", job.error
    digest = job.result_artifact
    assert service.pause(store, OWNER, child)["status"] == "completed"
    assert service.get_job(store, OWNER, child).result_artifact == digest


@pytest.mark.parametrize("failure,expected", [("artifact", "failed"), ("coverage", "interrupted")])
def test_failed_pause_publication_never_claims_the_trial_is_saved(
    app, client, monkeypatch, failure, expected
):
    store, experiment, parent = setup(app, client, monkeypatch)
    child = extend(store, experiment, parent)
    actual = vectorbt_portfolio.evaluate
    native_save = worker.save_artifact
    native_coverage = activity.StudyObserver.checkpoint

    def request(*args, **kwargs):
        service.pause(store, OWNER, child)
        return actual(*args, **kwargs)

    def fail_artifact(*args, **kwargs):
        if args[1].get("state", {}).get("calculation") is not None:
            raise OSError("controlled pause publication failure")
        return native_save(*args, **kwargs)

    def fail_coverage(self, db, state):
        if state.get("calculation") is not None:
            raise activity.ObservationFailed("controlled pause coverage failure")
        return native_coverage(self, db, state)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", request)
    if failure == "artifact":
        monkeypatch.setattr(worker, "save_artifact", fail_artifact)
    else:
        monkeypatch.setattr(activity.StudyObserver, "checkpoint", fail_coverage)
    run(store)
    receipt = service.job_receipt(store, service.get_job(store, OWNER, child))
    assert receipt["status"] == expected and receipt["resumable"]
    with store.sessions() as db:
        checkpoint = service.read_artifact(store, db.get(ResearchExperiment, child).checkpoint)
    assert checkpoint["state"]["calculation"] is None
    observed = activity.read(store, OWNER, child)
    assert observed["rows"][0]["state"] == "evaluated"
    assert not observed["rows"][0]["checkpointed"]
    assert observed["executions"][0]["state"] != "paused"


@pytest.mark.parametrize("validation", [{"train_pct": 70}, {"train_pct": 70, "mode": "evaluate"}])
def test_legacy_embedded_validation_does_not_offer_pause(app, client, monkeypatch, validation):
    store, child, _ = frozen_checkpoint(app, client, monkeypatch)
    service.resume(store, OWNER, child)
    with store.sessions.begin() as db:
        experiment = db.get(ResearchExperiment, child)
        specification = json.loads(experiment.specification)
        specification["portfolio"]["validation"] = validation
        experiment.specification = service.encoded(specification).decode()
    assert not service.job_receipt(store, service.get_job(store, OWNER, child))["pausable"]
    assert client.post(path(child)).status_code == 400
