"""Isolated checkpoint plumbing; no price acquisition or account calculation."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
from contextlib import contextmanager

import pytest
from test_jobs import app, client  # noqa: F401
from test_portfolio_workflow import portfolio

from database.research_db import ResearchExperiment, ResearchJob, ResearchWorker
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker

TOKEN = "checkpoint-publication-fences"
OWNER = "research-test"


def submit(app, client):
    response = client.post(
        "/scanner-research/api/portfolio/jobs", json={"portfolio": portfolio(client, optimize=True)}
    )
    assert response.status_code == 202, response.json
    return app.extensions["research_store"], response.json["id"]


def fence(store, job_id, reason):
    if reason == "cancel":
        assert service.cancel(store, OWNER, job_id)["status"] == "cancelling"
    else:
        with store.sessions.begin() as db:
            if reason == "lease":
                db.get(ResearchWorker, 1).token = "successor"
            elif reason == "job_worker":
                db.get(ResearchJob, job_id).worker = "successor"
            else:
                raise AssertionError("Unknown controlled fence")


def run(store, *, stop_requested=None):
    worker.acquire(store, TOKEN)
    try:
        assert worker.run_one(store, TOKEN, stop_requested=stop_requested)
    finally:
        worker.release(store, TOKEN)


@pytest.mark.parametrize("reason", ["cancel", "lease", "job_worker", "stop"])
def test_checkpoint_preflight_stops_before_artifact_io(app, client, monkeypatch, reason):
    store, job_id = submit(app, client)
    stop = [False]
    reached = []

    def forbidden(*args, **kwargs):
        pytest.fail("A failed checkpoint preflight entered artifact publication")

    def calculate(_store, _owner, _evidence, _spec, **callbacks):
        reached.append(True)
        if reason == "stop":
            stop[0] = True
            expected = worker.Interrupted
        else:
            fence(store, job_id, reason)
            expected = worker.Cancelled
        with pytest.raises(expected):
            callbacks["checkpoint"]({"phase": "calculation"}, {"completed": 37, "total": 100})
        raise expected("Controlled preflight fence")

    monkeypatch.setattr(worker, "save_artifact", forbidden)
    monkeypatch.setattr(research_portfolio, "run", calculate)
    before = {path.name for path in (store.root / "artifacts").iterdir()}
    run(store, stop_requested=lambda: stop[0])
    assert reached == [True]
    assert {path.name for path in (store.root / "artifacts").iterdir()} == before
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        assert db.get(ResearchExperiment, job_id).checkpoint is None
        assert job.progress == 0 and job.result_artifact is None
        if reason == "stop":
            assert job.status == "interrupted"
        elif reason == "cancel":
            assert job.status == "cancelled"
        else:
            assert job.status == "running"
            if reason == "job_worker":
                assert job.worker == "successor"
            else:
                assert db.get(ResearchWorker, 1).token == "successor"


@pytest.mark.parametrize("reason", ["cancel", "lease", "job_worker"])
def test_fence_changed_during_artifact_io_leaves_only_an_unreferenced_checkpoint(
    app, client, monkeypatch, reason
):
    store, job_id = submit(app, client)
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    original = worker.save_artifact
    original_scope = worker.artifact_publication
    written = []
    state = {"phase": "calculation", "padding": ["checkpoint fixture" * 30] * 10}

    def publish(selected, value):
        assert service._compact_checkpoint_publication.get() is not None
        key = original(selected, value)
        written.append(key)
        return key

    @contextmanager
    def publication(selected):
        with original_scope(selected) as scope:
            yield scope
        # The real publication guard serializes competing metadata writes.
        # Inject the race after it releases, immediately before the final fence.
        assert service._artifact_publication.get() is None
        with store.sessions() as db:
            assert db.get(ResearchExperiment, job_id).checkpoint is None
            assert db.get(ResearchJob, job_id).progress == 0
        fence(store, job_id, reason)

    def calculate(_store, _owner, _evidence, _spec, **callbacks):
        callbacks["checkpoint"](state, {"completed": 37, "total": 100})
        pytest.fail("A lost publication fence returned to calculation")

    monkeypatch.setattr(worker, "save_artifact", publish)
    monkeypatch.setattr(worker, "artifact_publication", publication)
    monkeypatch.setattr(research_portfolio, "run", calculate)
    run(store)
    assert len(written) == 1
    assert service.read_artifact(store, written[0])["state"] == state
    assert service.artifact_dependencies(store, written[0]) == set()
    with store.sessions() as db:
        assert db.get(ResearchExperiment, job_id).checkpoint is None
        job = db.get(ResearchJob, job_id)
        assert job.progress == 0 and job.result_artifact is None
        assert job.status == ("cancelled" if reason == "cancel" else "running")
    assert service._artifact_publication.get() is None
    assert service._compact_checkpoint_publication.get() is None


def test_pause_after_artifact_io_commits_checkpoint_progress_and_acknowledgement_together(
    app, client, monkeypatch
):
    store, job_id = submit(app, client)
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    original = worker.save_artifact
    original_scope = worker.artifact_publication
    written = []
    state = {"phase": "calculation", "padding": ["checkpoint fixture" * 30] * 10}

    def publish(selected, value):
        key = original(selected, value)
        written.append(key)
        return key

    @contextmanager
    def publication(selected):
        with original_scope(selected) as scope:
            yield scope
        # No separate progress-only commit may precede the checkpoint. The
        # artifact writer's transaction must also be closed before this request.
        assert service._artifact_publication.get() is None
        with store.sessions.begin() as db:
            assert db.get(ResearchExperiment, job_id).checkpoint is None
            job = db.get(ResearchJob, job_id)
            assert job.progress == 0 and job.worker == TOKEN
            job.status = "pausing"

    def calculate(_store, _owner, _evidence, _spec, **callbacks):
        callbacks["checkpoint"](state, {"completed": 37, "total": 100})
        pytest.fail("Acknowledged pause returned to calculation")

    monkeypatch.setattr(worker, "save_artifact", publish)
    monkeypatch.setattr(worker, "artifact_publication", publication)
    monkeypatch.setattr(research_portfolio, "run", calculate)
    run(store)
    assert len(written) == 1
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        receipt = db.get(ResearchExperiment, job_id)
        assert job.status == "paused" and job.worker is None
        assert job.progress == 37 and job.result_artifact is None
        assert receipt.checkpoint == written[0]
    assert service.read_artifact(store, written[0])["state"] == state
    assert service.artifact_dependencies(store, written[0]) == set()
    assert service._artifact_publication.get() is None
    assert service._compact_checkpoint_publication.get() is None
