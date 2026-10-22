"""Frequent calculation progress stays fenced without repeated full-store scans."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
from types import SimpleNamespace

import pytest
from test_jobs import app, client  # noqa: F401
from test_portfolio_workflow import portfolio

from database.research_db import ResearchJob
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def submitted(app, client):
    response = client.post(
        "/scanner-research/api/portfolio/jobs", json={"portfolio": portfolio(client)}
    )
    assert response.status_code == 202, response.json
    return app.extensions["research_store"], response.json["id"]


def run_worker(store):
    worker.acquire(store, "progress-storage-test")
    try:
        assert worker.run_one(store, "progress-storage-test")
    finally:
        worker.release(store, "progress-storage-test")


def clock(monkeypatch):
    current = [10000.0]
    original = worker.time
    monkeypatch.setattr(
        worker,
        "time",
        SimpleNamespace(monotonic=lambda: current[0], time=original.time, sleep=original.sleep),
    )
    return current


def test_twenty_seconds_of_frequent_progress_scans_storage_only_three_times(
    app, client, monkeypatch
):
    store, job_id = submitted(app, client)
    current = clock(monkeypatch)
    scans = []
    monkeypatch.setattr(worker, "ensure_storage_capacity", lambda _store: scans.append(current[0]))

    def calculate(_store, _owner, _evidence, _spec, **kwargs):
        for tick in range(81):
            current[0] = 10000.0 + tick * 0.25
            kwargs["progress"](tick + 1, 1000)
        raise worker.Interrupted("Controlled progress boundary; no account simulated")

    monkeypatch.setattr(research_portfolio, "run", calculate)
    run_worker(store)
    assert scans == [10000.0, 10010.0, 10020.0]
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        assert job.status == "interrupted"
        assert job.progress == 8  # Progress metadata continued between quota scans.


@pytest.mark.parametrize("reason", ["cancel", "replaced_worker"])
def test_cancellation_and_worker_fence_stay_live_while_storage_is_throttled(
    app, client, monkeypatch, reason
):
    store, job_id = submitted(app, client)
    current = clock(monkeypatch)
    scans = []
    monkeypatch.setattr(worker, "ensure_storage_capacity", lambda _store: scans.append(current[0]))

    def calculate(_store, _owner, _evidence, _spec, **kwargs):
        kwargs["progress"](1, 100)
        current[0] += 1
        if reason == "cancel":
            service.cancel(store, "research-test", job_id)
        else:
            with store.sessions.begin() as db:
                db.get(ResearchJob, job_id).worker = "replacement-worker"
        with pytest.raises(worker.Cancelled):
            kwargs["progress"](2, 100)
        raise worker.Cancelled("Controlled cancellation acknowledged")

    monkeypatch.setattr(research_portfolio, "run", calculate)
    run_worker(store)
    assert scans == [10000.0]
    job = service.get_job(store, "research-test", job_id)
    assert job.status == ("cancelled" if reason == "cancel" else "running")
    if reason == "replaced_worker":
        assert job.worker == "replacement-worker"  # A stale worker cannot publish a terminal state.


def test_new_artifact_quota_is_enforced_between_periodic_progress_scans(app, client, monkeypatch):
    store, job_id = submitted(app, client)
    current = clock(monkeypatch)
    scans = []
    monkeypatch.setattr(worker, "ensure_storage_capacity", lambda _store: scans.append(current[0]))
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "1")

    def calculate(_store, _owner, _evidence, _spec, **kwargs):
        kwargs["progress"](1, 100)
        current[0] += 1
        kwargs["progress"](2, 100)
        # Publication must measure its own current space, independent of the
        # advisory progress sampler. No large file or financial result is needed.
        monkeypatch.setattr(service, "_managed_storage_snapshot", lambda _store: (1024**2, 10))
        with pytest.raises(ValueError, match="storage is full"):
            service.save_artifact(store, {"new": "must not publish within the throttle interval"})
        raise worker.Interrupted("Controlled quota boundary")

    before = set((store.root / "artifacts").iterdir())
    monkeypatch.setattr(research_portfolio, "run", calculate)
    run_worker(store)
    assert scans == [10000.0]
    assert set((store.root / "artifacts").iterdir()) == before
    assert service.get_job(store, "research-test", job_id).status == "interrupted"
