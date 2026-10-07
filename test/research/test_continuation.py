import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO

import pytest
from flask import Flask
from sqlalchemy import select

from blueprints.scanner_research import scanner_research_bp
from database.research_db import ResearchExperiment, ResearchJob
from services import scanner_research_service as service
from services import scanner_research_worker as worker


@pytest.fixture
def context(tmp_path):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", TESTING=True, RESEARCH_DATA_DIR=str(tmp_path))
    app.register_blueprint(scanner_research_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = "owner"
    day, lines = date(2026, 1, 5), ["Date,Symbol"]
    while len(lines) < 51:
        if day.weekday() < 5:
            lines.append(f"{day.isoformat()},TEST")
        day += timedelta(days=1)
    raw = "\n".join(lines).encode()
    source = client.post(
        "/scanner-research/api/sources",
        data={"file": (BytesIO(raw), "test.csv"), "source": "fixture"},
    ).json
    yield app.extensions["research_store"], client, source, raw
    app.extensions["research_store"].close()


def test_lost_response_retries_reuse_one_job_and_conflicts_fail(context):
    store, client, source, _ = context
    payload = {"source_id": source["id"], "config": {}, "request_id": "same-request-key"}
    first = client.post("/scanner-research/api/jobs", json=payload)
    second = client.post("/scanner-research/api/jobs", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json["id"] == second.json["id"]
    conflict = client.post(
        "/scanner-research/api/jobs", json={**payload, "config": {"cost_bps": 90}}
    )
    assert conflict.status_code == 400
    with store.sessions() as db:
        assert len(db.scalars(select(ResearchJob)).all()) == 1


def test_concurrent_same_token_is_atomic(context):
    store, _, source, _ = context
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(
            pool.map(
                lambda _: service.submit(
                    store, "owner", source["id"], {}, request_id="same-concurrent"
                ),
                range(8),
            )
        )
    assert len({job["id"] for job in jobs}) == 1


def test_retry_token_does_not_bypass_owner_source_access(context):
    store, _, source, _ = context
    service.submit(store, "owner", source["id"], {}, request_id="scope-request")
    with pytest.raises(LookupError):
        service.submit(store, "another", source["id"], {}, request_id="scope-request")


def test_public_preparation_retry_and_result_source(context, monkeypatch):
    import research.evidence_import
    from research.data import fixture_snapshot

    store, client, _, raw = context
    monkeypatch.setenv("RESEARCH_PUBLIC_EVIDENCE_DIR", "test-only-injected-preparer")
    monkeypatch.setattr(
        research.evidence_import,
        "public_snapshot",
        lambda signals, directory, progress=None, extension_dir=None: fixture_snapshot(signals),
    )

    def upload():
        return client.post(
            "/scanner-research/api/sources",
            data={"file": (BytesIO(raw), "test.csv"), "source": "public"},
        ).json

    first, retry = upload(), upload()
    assert first["preparation_job"]["id"] == retry["preparation_job"]["id"]
    worker.acquire(store, "prepare")
    try:
        worker.run_one(store, "prepare")
    finally:
        worker.release(store, "prepare")
    job = client.get("/scanner-research/api/jobs/" + first["preparation_job"]["id"]).json
    assert job["status"] == "completed"
    prepared = job["result"]["prepared_source"]
    assert (
        prepared["provenance"]["synthetic"] is True
    )  # The injected preparer stays visibly synthetic.
    assert any(s["id"] == prepared["id"] for s in client.get("/scanner-research/api/sources").json)


def search_payload(source):
    return {
        "source_id": source["id"],
        "kind": "optimize",
        "request_id": "search-request",
        "config": {},
        "specification": {
            "mode": "exhaustive",
            "axes": {"target_pct": {"min": 2, "max": 7, "step": 1}},
        },
    }


def test_actual_persisted_search_resume_and_exact_export(context, monkeypatch):
    import research.experiments

    store, client, source, _ = context
    payload = search_payload(source)
    job_id = client.post("/scanner-research/api/jobs", json=payload).json["id"]
    original = research.experiments.run_search

    def interrupt(*args, **kwargs):
        actual_checkpoint = kwargs["checkpoint"]

        def checkpoint(state, counts):
            actual_checkpoint(state, counts)
            raise worker.Interrupted("Test supervisor shutdown after durable checkpoint")

        return original(*args, **{**kwargs, "checkpoint": checkpoint})

    monkeypatch.setattr(research.experiments, "run_search", interrupt)
    worker.acquire(store, "first")
    worker.run_one(store, "first")
    worker.release(store, "first")
    job = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert job["status"] == "interrupted" and job["resumable"]
    assert job["counts"]["completed"] == 5
    with store.sessions() as db:
        experiment = db.get(ResearchExperiment, job_id)
        checkpoint_hash = experiment.checkpoint
    assert len(service.read_artifact(store, checkpoint_hash)["state"]["rows"]) == 5
    monkeypatch.setattr(research.experiments, "run_search", original)
    assert client.post(f"/scanner-research/api/jobs/{job_id}/resume").json["id"] == job_id
    worker.acquire(store, "second")
    worker.run_one(store, "second")
    worker.release(store, "second")
    job = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert job["status"] == "completed" and not job["resumable"]
    assert len(job["result"]["experiment"]["rows"]) == 6
    assert client.post(f"/scanner-research/api/jobs/{job_id}/resume").status_code == 400
    first = client.get(f"/scanner-research/api/jobs/{job_id}/export")
    assert hashlib.sha256(first.data).hexdigest() == first.headers["X-Evidence-SHA256"]
    assert first.data == client.get(f"/scanner-research/api/jobs/{job_id}/export").data
    assert first.json["inputs"]["signals"]
    assert first.json["inputs_artifact"]


def test_research_known_overlap_survives_reordered_csv(context):
    store, client, source, raw = context
    spec = {
        "intent": "fixed_setup",
        "train_end": "2026-02-02",
        "test_end": "2026-03-06",
        "gap_sessions": 0,
        "prior_explored": False,
        "variants": [{"name": "Higher fees", "changes": {"cost_bps": 30}}],
    }
    jobs = []
    for index in range(2):
        if index:
            lines = raw.decode().splitlines()
            reordered = "\n".join([lines[0], *reversed(lines[1:])]).encode()
            source = client.post(
                "/scanner-research/api/sources",
                data={"file": (BytesIO(reordered), "reordered.csv"), "source": "fixture"},
            ).json
        payload = {
            "source_id": source["id"],
            "kind": "research",
            "request_id": f"research-{index}",
            "specification": spec,
        }
        response = client.post("/scanner-research/api/jobs", json=payload)
        assert response.status_code == 202, response.json
        job_id = response.json["id"]
        worker.acquire(store, f"research-{index}")
        worker.run_one(store, f"research-{index}")
        worker.release(store, f"research-{index}")
        job = client.get(f"/scanner-research/api/jobs/{job_id}").json
        assert job["status"] == "completed", job
        jobs.append(job)
    assert jobs[0]["result"]["experiment"]["exploration"]["known_overlap_jobs"] == []
    assert jobs[1]["result"]["experiment"]["exploration"]["known_overlap_jobs"] == [jobs[0]["id"]]


def test_preflight_exact_counts_and_blocked_data(context):
    _, client, source, _ = context
    payload = search_payload(source)
    preview = client.post("/scanner-research/api/preflight", json=payload)
    assert preview.json["planned_evaluations"] == 6
    changed = copy.deepcopy(payload)
    changed["specification"]["axes"]["target_pct"]["step"] = 0
    assert client.post("/scanner-research/api/preflight", json=changed).status_code == 400
    assert client.get("/scanner-research/api/health").json["status"] == "ok"


def test_maintenance_blocks_source_and_submission(context):
    from services.research_storage import maintenance

    store, client, source, raw = context
    with maintenance(store):
        assert (
            client.post("/scanner-research/api/jobs", json={"source_id": source["id"]}).status_code
            == 400
        )
        assert (
            client.post(
                "/scanner-research/api/sources",
                data={"file": (BytesIO(raw), "same.csv"), "source": "fixture"},
            ).status_code
            == 400
        )


def test_follow_up_indices_require_exact_owner_visible_parent(context):
    store, client, source, _ = context
    payload = search_payload(source)
    payload["specification"].update(mode="quick", budget=2)
    first = client.post("/scanner-research/api/jobs", json=payload).json
    worker.acquire(store, "parent")
    try:
        worker.run_one(store, "parent")
    finally:
        worker.release(store, "parent")
    job = client.get(f"/scanner-research/api/jobs/{first['id']}").json
    rows = job["result"]["experiment"]["rows"]
    spec = {
        **job["specification"],
        "parent_job_id": first["id"],
        "exclude_indices": sorted(r["grid_index"] for r in rows),
    }
    follow_up = {**payload, "request_id": "follow-up-request", "specification": spec}
    assert client.post("/scanner-research/api/preflight", json=follow_up).status_code == 200
    follow_response = client.post("/scanner-research/api/jobs", json=follow_up)
    assert follow_response.status_code == 202
    for changed in ({"parent_job_id": "0" * 32}, {"exclude_indices": [0]}, {"parent_job_id": None}):
        bad = {**follow_up, "request_id": "invalid-follow-up", "specification": {**spec, **changed}}
        assert client.post("/scanner-research/api/jobs", json=bad).status_code == 400
    old_export = client.get(f"/scanner-research/api/jobs/{first['id']}/export").data
    worker.acquire(store, "follow-up-worker")
    try:
        worker.run_one(store, "follow-up-worker")
    finally:
        worker.release(store, "follow-up-worker")
    continued = client.get(f"/scanner-research/api/jobs/{follow_response.json['id']}").json
    assert continued["status"] == "completed", continued
    result = continued["result"]["experiment"]
    assert result["counts"]["evaluated_all_passes"] == 4
    assert result["counts"]["evaluated_this_pass"] == 2
    assert {row["config_id"] for row in rows}.issubset({row["config_id"] for row in result["rows"]})
    assert client.get(f"/scanner-research/api/jobs/{first['id']}/export").data == old_export
    exported = client.get(f"/scanner-research/api/jobs/{follow_response.json['id']}/export").json
    assert exported["parent_result_artifact"] == job["evidence_id"]


def test_backtest_exploration_is_remembered_by_later_test(context):
    store, client, source, _ = context
    first = client.post("/scanner-research/api/jobs", json={"source_id": source["id"]}).json
    worker.acquire(store, "exploration")
    try:
        worker.run_one(store, "exploration")
        second = client.post(
            "/scanner-research/api/jobs",
            json={
                "source_id": source["id"],
                "kind": "research",
                "specification": {
                    "intent": "fixed_setup",
                    "train_end": "2026-02-02",
                    "test_end": "2026-03-06",
                    "gap_sessions": 0,
                    "prior_explored": False,
                },
            },
        ).json
        worker.run_one(store, "exploration")
    finally:
        worker.release(store, "exploration")
    result = client.get(f"/scanner-research/api/jobs/{second['id']}").json["result"]
    assert result["experiment"]["exploration"]["known_overlap_jobs"] == [first["id"]]


def test_source_receipts_do_not_duplicate_large_price_lineage():
    provenance = {
        "provider": "test",
        "source_receipts": [{"sha256": "a" * 64}] * 1000,
        "symbol_identities": {"TEST": {str(i): {"isin": "same"} for i in range(3000)}},
    }
    receipt = {
        "id": "source",
        "receipt": {"signal_count": 1},
        "provenance": provenance,
        "coverage": {
            "status": "warning",
            "provenance": provenance,
            "symbols": [{"symbol": "TEST", "missing_sessions": [str(i) for i in range(3000)]}],
        },
    }
    original = service.encoded(receipt)
    compact = service.compact_source_receipt(receipt)
    assert len(service.encoded(compact)) < 1500
    assert compact["provenance"]["source_receipts_count"] == 1000
    assert compact["coverage"]["symbols"][0]["missing_sessions_count"] == 3000
    assert len(compact["coverage"]["symbols"][0]["missing_sessions"]) == 10
    assert service.encoded(receipt) == original
    assert service.compact_source_receipt(compact) == compact
