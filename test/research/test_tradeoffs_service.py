"""Owned, bounded saved alternatives with no input/history/runtime work."""

# ruff: noqa: F811
import gc
import time
import tracemalloc

import pytest
from sqlalchemy import event
from test_jobs import app, client  # noqa: F401
from test_research_tradeoffs import report_fixture

from database.research_db import ResearchJob
from services import research_tradeoffs as view
from services import scanner_research_service as artifacts

OWNER = "research-test"
JOB = "b" * 32


def path():
    return f"/scanner-research/api/portfolio/jobs/{JOB}/tradeoffs"


def seed(app, *, status="completed", result=None):
    store = app.extensions["research_store"]
    bundle = {
        "kind": "portfolio_optimize",
        "inputs_artifact": "f" * 64,
        "result": report_fixture() if result is None else result,
    }
    digest = artifacts.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=JOB,
                owner=OWNER,
                source_id="c" * 64,
                config="{}",
                status=status,
                result_artifact=digest,
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
    return store, digest, bundle


def test_owned_http_reads_only_the_exact_result_and_preserves_all_saved_facts(
    app, client, monkeypatch
):
    store, digest, original = seed(app)
    read = artifacts.read_artifact
    seen = []
    files = set((store.root / "artifacts").iterdir())

    def bounded(actual_store, pointer, **kwargs):
        assert pointer == digest  # Never the nonexistent inputs or history.
        assert kwargs["max_bytes"] == view.MAX_RESULT_BYTES
        seen.append(pointer)
        return read(actual_store, pointer, **kwargs)

    monkeypatch.setattr(artifacts, "read_artifact", bounded)
    response = client.get(path(), query_string={"expected_result": digest})
    assert response.status_code == 200 and response.json["available"], response.json
    assert response.headers["Cache-Control"] == "private, no-store"
    assert seen == [digest]
    assert response.json["analysis"]["counts"]["frontier"] > 0
    assert read(store, digest) == original
    assert set((store.root / "artifacts").iterdir()) == files
    with store.sessions() as db:
        assert len(db.query(ResearchJob).all()) == 1
        assert db.get(ResearchJob, JOB).result_artifact == digest


def test_authentication_and_ownership_precede_artifact_reads(app, client, monkeypatch):
    seed(app)
    monkeypatch.setattr(
        artifacts, "read_artifact", lambda *a, **k: pytest.fail("Unowned artifact read")
    )
    assert app.test_client().get(path()).status_code == 401
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    assert client.get(path()).status_code == 404


def test_unchanged_reference_remains_visible_when_it_beats_every_alternative(app, client):
    result = report_fixture()
    result["automatic_research"]["baseline"].update(
        net_return_pct=20, max_drawdown_pct=1, closed_trades=5
    )
    seed(app, result=result)
    response = client.get(path())
    assert response.status_code == 200 and response.json["available"], response.json
    analysis = response.json["analysis"]
    assert analysis["status"] == "no_eligible_alternatives" and analysis["choices"] == []
    assert analysis["baseline"]["metrics"]["net_return_pct"] == 20


@pytest.mark.parametrize(
    "query",
    [
        {"unexpected": "1"},
        {"expected_result": "wrong"},
        [("expected_result", "a" * 64), ("expected_result", "a" * 64)],
    ],
)
def test_query_admission_stops_before_artifact_reads(app, client, monkeypatch, query):
    seed(app)
    monkeypatch.setattr(
        artifacts, "read_artifact", lambda *a, **k: pytest.fail("Invalid query read")
    )
    assert client.get(path(), query_string=query).status_code == 400


def test_expected_pointer_conflict_does_not_read_a_replacement(app, client, monkeypatch):
    seed(app)
    monkeypatch.setattr(artifacts, "read_artifact", lambda *a, **k: pytest.fail("Stale query read"))
    response = client.get(path(), query_string={"expected_result": "d" * 64})
    assert response.status_code == 409 and response.json["code"] == "tradeoffs_changed"


@pytest.mark.parametrize("change", ["owner", "status", "result"])
def test_concurrent_publication_is_never_served_as_the_anchored_result(
    app, client, monkeypatch, change
):
    store, digest, _ = seed(app)
    read = artifacts.read_artifact

    def replaced(*args, **kwargs):
        bundle = read(*args, **kwargs)
        with store.sessions.begin() as db:
            job = db.get(ResearchJob, JOB)
            if change == "owner":
                job.owner = "another-owner"
            elif change == "status":
                job.status = "running"
            else:
                job.result_artifact = "e" * 64
        return bundle

    monkeypatch.setattr(artifacts, "read_artifact", replaced)
    assert client.get(path(), query_string={"expected_result": digest}).status_code == (
        404 if change == "owner" else 409
    )


def test_paused_study_does_not_expand_any_artifact(app, client, monkeypatch):
    seed(app, status="paused")
    monkeypatch.setattr(
        artifacts, "read_artifact", lambda *a, **k: pytest.fail("Paused result read")
    )
    response = client.get(path())
    assert response.status_code == 200 and not response.json["available"]


def test_unverifiable_result_remains_read_only_and_unavailable(app, client, monkeypatch):
    store, digest, _ = seed(app)
    monkeypatch.setattr(
        artifacts, "read_artifact", lambda *a, **k: {"kind": "portfolio_optimize", "result": {}}
    )
    response = client.get(path())
    assert response.status_code == 200 and not response.json["available"]
    with store.sessions() as db:
        assert db.get(ResearchJob, JOB).result_artifact == digest


def test_100_success_and_rejection_reads_release_files_sessions_and_memory(
    app, monkeypatch, record_property
):
    import psutil

    store, _, _ = seed(app)
    connected, closed = [], []
    event.listen(store.engine, "connect", lambda *a: connected.append(1))
    event.listen(store.engine, "close", lambda *a: closed.append(1))
    read = artifacts.read_artifact
    reject = False

    def actual(*args, **kwargs):
        value = read(*args, **kwargs)
        if reject:
            value["result"]["automatic_research"]["version"] = "unknown"
        return value

    monkeypatch.setattr(artifacts, "read_artifact", actual)
    assert view.read(store, OWNER, JOB)["available"]
    process = psutil.Process()
    handles = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(100):
        reject = False
        assert view.read(store, OWNER, JOB)["available"]
        reject = True
        assert not view.read(store, OWNER, JOB)["available"]
    gc.collect()
    retained = sum(
        max(0, row.size_diff) for row in tracemalloc.take_snapshot().compare_to(before, "lineno")
    )
    tracemalloc.stop()
    after = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    record_property("handles_before", handles)
    record_property("handles_after", after)
    record_property("retained_bytes", retained)
    assert len(connected) == len(closed)
    assert after <= handles + 1 and retained < 512 * 1024
