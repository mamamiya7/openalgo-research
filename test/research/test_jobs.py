"""Isolated HTTP, immutable evidence, worker fencing and cancellation tests."""

import base64
import hashlib
import os
import subprocess
import sys
import time

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect

from blueprints.scanner_research import scanner_research_bp
from database.research_db import ResearchJob, ResearchWorker
from services import scanner_research_service as service
from services import scanner_research_worker as worker


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="isolated-test-key", RESEARCH_DATA_DIR=str(tmp_path))
    app.register_blueprint(scanner_research_bp)
    yield app
    app.extensions["research_store"].close()


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = "research-test"
    return client


def source(client):
    from io import BytesIO

    response = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (BytesIO(b"Date,Symbol\n2026-01-05,TEST\n2026-01-06,OTHER\n"), "signals.csv"),
            "source": "fixture",
        },
    )
    assert response.status_code == 201, response.json
    assert response.json["provenance"]["synthetic"] is True
    return response.json["id"]


def submit(client, source_id):
    response = client.post(
        "/scanner-research/api/jobs", json={"source_id": source_id, "config": {}}
    )
    assert response.status_code == 202, response.json
    return response.json["id"]


def test_complete_reopen_exact_export_without_broker(app, client):
    job_id = submit(client, source(client))
    store = app.extensions["research_store"]
    worker.acquire(store, "test-worker")
    try:
        assert worker.run_one(store, "test-worker")
    finally:
        worker.release(store, "test-worker")
    response = client.get(f"/scanner-research/api/jobs/{job_id}")
    assert response.json["status"] == "completed"
    assert response.json["result"]["equity_curve"]
    first = client.get(f"/scanner-research/api/jobs/{job_id}/export")
    assert first.status_code == 200
    assert hashlib.sha256(first.data).hexdigest() == first.headers["X-Evidence-SHA256"]
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == first.data
    assert first.json["inputs"]["original_csv"].startswith("Date,Symbol")
    raw = base64.b64decode(first.json["inputs"]["original_csv_base64"])
    assert hashlib.sha256(raw).hexdigest() == first.json["inputs"]["original_csv_sha256"]


def test_auth_owner_and_csrf(app, client):
    job_id = submit(client, source(client))
    assert app.test_client().get("/scanner-research/api/jobs").status_code == 401
    with client.session_transaction() as session:
        session["user"] = "other-account"
    for suffix in ("", "/export"):
        assert client.get(f"/scanner-research/api/jobs/{job_id}{suffix}").status_code == 404
    assert client.post(f"/scanner-research/api/jobs/{job_id}/cancel").status_code == 404
    # Production installs CSRF globally; the blueprint must not exempt itself.
    protected = Flask("csrf-test")
    protected.config.update(SECRET_KEY="test", RESEARCH_DATA_DIR=str(store_root(app) / "csrf"))
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    try:
        assert (
            protected.test_client().post("/scanner-research/api/jobs", json={}).status_code == 400
        )
    finally:
        protected.extensions["research_store"].close()


def store_root(app):
    return app.extensions["research_store"].root


def test_queue_bound_cancel_and_restart(app, client):
    source_id = source(client)
    ids = [submit(client, source_id) for _ in range(4)]
    assert (
        client.post("/scanner-research/api/jobs", json={"source_id": source_id}).status_code == 400
    )
    assert client.post(f"/scanner-research/api/jobs/{ids[0]}/cancel").json["status"] == "cancelled"
    store = app.extensions["research_store"]
    worker.acquire(store, "old")
    with pytest.raises(RuntimeError):
        worker.acquire(store, "competing")
    with store.sessions.begin() as db:
        job = db.get(ResearchJob, ids[1])
        job.status, job.worker = "running", "old"
        db.get(ResearchWorker, 1).heartbeat = time.time() - 121
    worker.acquire(store, "new")
    assert client.get(f"/scanner-research/api/jobs/{ids[1]}").json["status"] == "interrupted"
    with pytest.raises(worker.Cancelled):
        worker.heartbeat(store, "old")
    worker.release(store, "new")


def test_running_cancel_at_publication(app, client, monkeypatch):
    import research.engine

    job_id = submit(client, source(client))
    store = app.extensions["research_store"]
    actual = research.engine.evaluate

    def evaluate(*args, **kwargs):
        result = actual(*args, **kwargs)
        service.cancel(store, "research-test", job_id)
        return result

    monkeypatch.setattr(research.engine, "evaluate", evaluate)
    worker.acquire(store, "cancel-worker")
    try:
        worker.run_one(store, "cancel-worker")
    finally:
        worker.release(store, "cancel-worker")
    assert client.get(f"/scanner-research/api/jobs/{job_id}").json["status"] == "cancelled"
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").status_code == 400


def test_integrity_failure_and_validation(app, client):
    assert client.post("/scanner-research/api/sources", data={}).status_code == 400
    source_id = source(client)
    assert (
        client.post(
            "/scanner-research/api/jobs", json={"source_id": source_id, "config": {"stop_pct": -1}}
        ).status_code
        == 400
    )
    store = app.extensions["research_store"]
    digest = service.save_artifact(store, {"evidence": "original"})
    (store.root / "artifacts" / f"{digest}.json").write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        service.read_artifact(store, digest)


def test_schema_idempotent_populated(app, client):
    job_id = submit(client, source(client))
    store = app.extensions["research_store"]
    store.initialize()
    store.initialize()
    assert client.get(f"/scanner-research/api/jobs/{job_id}").json["status"] == "queued"


def test_external_worker_process(app, client, tmp_path):
    job_id = submit(client, source(client))
    environment = {**os.environ, "RESEARCH_DATA_DIR": str(store_root(app))}
    with (tmp_path / "worker.log").open("w") as output:
        process = subprocess.run(
            [sys.executable, "-m", "services.scanner_research_worker", "--once"],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    assert process.returncode == 0, (tmp_path / "worker.log").read_text()
    assert client.get(f"/scanner-research/api/jobs/{job_id}").json["status"] == "completed"


def test_concurrent_admission_never_exceeds_four(app, client):
    from concurrent.futures import ThreadPoolExecutor

    source_id = source(client)
    store = app.extensions["research_store"]

    def attempt(_):
        try:
            service.submit(store, "research-test", source_id, {})
            return True
        except ValueError as error:
            assert "queue is full" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert sum(executor.map(attempt, range(8))) == 4


def test_historify_upload_queues_validation_before_allowing_calculation(app, client, monkeypatch):
    from io import BytesIO

    monkeypatch.delenv("HISTORIFY_DATABASE_PATH", raising=False)
    response = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (BytesIO(b"Date,Symbol\n2026-01-05,TEST\n"), "signals.csv"),
            "source": "historify",
        },
    )
    assert response.status_code == 201
    assert response.json["coverage"]["status"] == "preparing"
    assert response.json["provenance"]["acquisition_mode"] == "stored_only"
    assert response.json["preparation_job"]["kind"] == "acquire"
    assert (
        client.post(
            "/scanner-research/api/jobs", json={"source_id": response.json["id"]}
        ).status_code
        == 400
    )


def test_repeated_store_operations_release_handles(app, client):
    import psutil

    process = psutil.Process()
    measure = process.num_handles if sys.platform == "win32" else process.num_fds
    source_id = source(client)
    store = app.extensions["research_store"]
    for _ in range(10):
        service.source_for(store, "research-test", source_id)
    before = measure()
    for _ in range(150):
        service.source_for(store, "research-test", source_id)
        with pytest.raises(LookupError):
            service.get_job(store, "research-test", "missing")
        assert client.get("/scanner-research/api/jobs").status_code == 200
    assert measure() <= before + 3
