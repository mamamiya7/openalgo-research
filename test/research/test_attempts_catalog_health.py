# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Attempt recovery, owner-scoped pagination and actionable worker health."""

import io
import time

import pytest
from test_jobs import app, client, source, submit  # noqa: F401

from database.research_db import ResearchExperiment, ResearchJob, ResearchWorker
from services import scanner_research_service as service
from services import scanner_research_worker as worker


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "interrupted"])
def test_new_attempt_is_linked_idempotent_and_owner_scoped(app, client, terminal):
    first = submit(client, source(client))
    store = app.extensions["research_store"]
    with store.sessions.begin() as db:
        db.get(ResearchJob, first).status = terminal
    route = f"/scanner-research/api/jobs/{first}/retry"
    retry = client.post(route, json={"request_id": "new-attempt-key"})
    assert retry.status_code == 202
    assert retry.json["id"] != first
    assert retry.json["previous_attempt_id"] == first
    assert client.post(route, json={"request_id": "new-attempt-key"}).json["id"] == retry.json["id"]
    assert client.post(f"/scanner-research/api/jobs/{first}/resume").status_code == 400
    with client.session_transaction() as session:
        session["user"] = "different-owner"
    assert client.post(route, json={"request_id": "new-attempt-key"}).status_code == 404


def test_preparation_repair_and_successful_reuse(app, client, monkeypatch):
    from research.data import fixture_snapshot

    store = app.extensions["research_store"]

    def upload():
        return client.post(
            "/scanner-research/api/sources",
            data={
                "file": (io.BytesIO(b"Date,Symbol\n2026-01-05,TEST\n"), "signals.csv"),
                "source": "public",
            },
        ).json

    monkeypatch.delenv("RESEARCH_PUBLIC_EVIDENCE_DIR", raising=False)
    first = upload()["preparation_job"]["id"]
    worker.acquire(store, "attempt-worker")
    try:
        worker.run_one(store, "attempt-worker")
        assert service.get_job(store, "research-test", first).status == "failed"
        assert upload()["preparation_job"]["id"] == first  # lost response reuses the same attempt
        monkeypatch.setenv("RESEARCH_PUBLIC_EVIDENCE_DIR", "controlled-repaired-fixture")
        monkeypatch.setattr(
            "research.evidence_import.public_snapshot",
            lambda signals, *a, **kw: fixture_snapshot(signals),
        )
        second = client.post(
            f"/scanner-research/api/jobs/{first}/retry", json={"request_id": "repaired-attempt"}
        ).json["id"]
        worker.run_one(store, "attempt-worker")
        assert service.get_job(store, "research-test", second).status == "completed"
        assert upload()["preparation_job"]["id"] == second
        assert (
            client.post(
                f"/scanner-research/api/jobs/{second}/retry",
                json={"request_id": "unneeded-attempt"},
            ).status_code
            == 400
        )
    finally:
        worker.release(store, "attempt-worker")


def test_paginated_catalog_can_reopen_export_oldest_beyond_100(app, client):
    store = app.extensions["research_store"]
    oldest = submit(client, source(client))
    worker.acquire(store, "catalog-worker")
    try:
        worker.run_one(store, "catalog-worker")
    finally:
        worker.release(store, "catalog-worker")
    original = service.get_job(store, "research-test", oldest)
    with store.sessions.begin() as db:
        for index in range(135):
            identity = f"{index:032x}"
            db.add(
                ResearchJob(
                    id=identity,
                    owner="research-test" if index < 125 else "other",
                    source_id=original.source_id,
                    config=original.config,
                    status="cancelled",
                    progress=0,
                    created_at=time.time() + index,
                    updated_at=time.time(),
                )
            )
            db.add(
                ResearchExperiment(
                    job_id=identity,
                    kind="backtest",
                    specification="{}",
                    identity="a" * 64,
                    counts="{}",
                )
            )
    items, cursor = [], None
    while True:
        page = client.get(
            "/scanner-research/api/jobs",
            query_string={"page_size": 20, **({"cursor": cursor} if cursor else {})},
        ).json
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(items) == 126 and len({item["id"] for item in items}) == 126
    assert items[-1]["id"] == oldest
    assert "2026-01-05" in items[-1]["title"]
    assert client.get(f"/scanner-research/api/jobs/{oldest}").json["result"]
    assert client.get(f"/scanner-research/api/jobs/{oldest}/export").status_code == 200
    assert (
        len(client.get("/scanner-research/api/jobs?page_size=20&status=completed").json["items"])
        == 1
    )
    assert len(client.get(f"/scanner-research/api/jobs?page_size=20&q={oldest}").json["items"]) == 1
    assert client.get("/scanner-research/api/jobs?page_size=101").status_code == 400


@pytest.mark.parametrize(
    "token,age,state",
    [
        (None, 0, "offline"),
        ("worker", 0, "online"),
        ("worker", 200, "stale"),
        ("maintenance:test", 0, "maintenance"),
    ],
)
def test_worker_health_states(app, client, token, age, state):
    with app.extensions["research_store"].sessions.begin() as db:
        lease = db.get(ResearchWorker, 1)
        lease.token, lease.heartbeat = token, time.time() - age
    result = client.get("/scanner-research/api/health").json
    assert result["worker_state"] == state
    assert result["worker_online"] == (state == "online")


def test_old_queued_policy_requires_explicit_new_attempt(app, client):
    first = submit(client, source(client))
    store = app.extensions["research_store"]
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, first).identity = "0" * 64
    worker.acquire(store, "policy-worker")
    try:
        worker.run_one(store, "policy-worker")
        saved = client.get(f"/scanner-research/api/jobs/{first}").json
        assert saved["status"] == "failed" and "policy changed" in saved["error"]
        fresh = client.post(
            f"/scanner-research/api/jobs/{first}/retry",
            json={"request_id": "corrected-policy-attempt"},
        ).json
        worker.run_one(store, "policy-worker")
        assert client.get(f"/scanner-research/api/jobs/{fresh['id']}").json["status"] == "completed"
    finally:
        worker.release(store, "policy-worker")


def test_quota_counts_isolated_acquisition_files(app, monkeypatch):
    from services.research_storage import inspect_storage

    store = app.extensions["research_store"]
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "1")
    (store.root / "isolated.duckdb").write_bytes(b"x" * 1024 * 1024)
    with pytest.raises(ValueError, match="Research storage"):
        service.save_artifact(store, {"checked": True})
    receipt = inspect_storage(store)
    assert receipt["over_quota"] and receipt["acquisition_and_other_bytes"] == 1024 * 1024
