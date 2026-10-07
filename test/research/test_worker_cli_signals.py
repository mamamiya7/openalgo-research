# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Terminal signals use the real worker's lease and durable-checkpoint path."""

import signal
import sys

import pytest
from test_continuation import context, search_payload  # noqa: F401

from database.research_db import ResearchExperiment, ResearchStore, ResearchWorker
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def install_store(monkeypatch, root):
    closed = []

    def factory():
        store = ResearchStore(root)
        actual_close = store.close

        def close():
            actual_close()
            closed.append(True)

        store.close = close
        return store

    monkeypatch.setattr(worker, "ResearchStore", factory)
    return closed


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_idle_cli_signal_releases_lease_and_restores_handlers(context, monkeypatch, signum):
    store, _, _, _ = context
    closed = install_store(monkeypatch, store.root)
    monkeypatch.setattr(sys, "argv", ["research-worker"])
    original = worker.run_one
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    calls = []

    def stop_when_idle(*args, **kwargs):
        worked = original(*args, **kwargs)
        assert worked is False
        signal.raise_signal(signum)
        assert kwargs["stop_requested"]() is True
        calls.append(True)
        return worked

    monkeypatch.setattr(worker, "run_one", stop_when_idle)
    worker.main()
    assert calls == [True] and closed == [True]
    with store.sessions() as db:
        lease = db.get(ResearchWorker, 1)
        assert lease.token is None and lease.heartbeat == 0
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


def test_in_progress_ctrl_c_keeps_checkpoint_and_resumes_exactly(context, monkeypatch):
    import research.experiments

    store, client, source, _ = context
    payload = search_payload(source)
    response = client.post("/scanner-research/api/jobs", json=payload)
    assert response.status_code == 202, response.json
    job_id = response.json["id"]
    closed = install_store(monkeypatch, store.root)
    monkeypatch.setattr(sys, "argv", ["research-worker"])
    original = research.experiments.run_search
    saved_hashes = []

    def interrupt_after_durable_work(*args, **kwargs):
        publish = kwargs["checkpoint"]

        def checkpoint(state, counts):
            publish(state, counts)
            with store.sessions() as db:
                saved_hashes.append(db.get(ResearchExperiment, job_id).checkpoint)
            signal.raise_signal(signal.SIGINT)

        return original(*args, **{**kwargs, "checkpoint": checkpoint})

    monkeypatch.setattr(research.experiments, "run_search", interrupt_after_durable_work)
    worker.main()
    partial = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert partial["status"] == "interrupted" and partial["resumable"] is True
    assert partial["counts"]["completed"] == 5
    assert len(saved_hashes) == 1
    checkpoint = service.read_artifact(store, saved_hashes[0])
    assert len(checkpoint["state"]["rows"]) == 5
    with store.sessions() as db:
        assert db.get(ResearchWorker, 1).token is None

    monkeypatch.setattr(research.experiments, "run_search", original)
    monkeypatch.setattr(sys, "argv", ["research-worker", "--once"])
    assert client.post(f"/scanner-research/api/jobs/{job_id}/resume").status_code == 200
    worker.main()
    resumed = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert resumed["status"] == "completed"
    assert service.read_artifact(store, saved_hashes[0]) == checkpoint

    response = client.post(
        "/scanner-research/api/jobs", json={**payload, "request_id": "uninterrupted-comparison"}
    )
    assert response.status_code == 202
    worker.main()
    clean = client.get(f"/scanner-research/api/jobs/{response.json['id']}").json
    assert clean["status"] == "completed"
    for key in ("summary", "ledger", "equity_curve", "config"):
        assert resumed["result"][key] == clean["result"][key], key
    assert len(closed) == 3


def test_cli_initialization_failure_closes_store_and_restores_handlers(tmp_path, monkeypatch):
    closed = []
    store = ResearchStore(tmp_path)
    actual_close = store.close

    def fail():
        raise OSError("controlled schema failure")

    def close():
        actual_close()
        closed.append(True)

    monkeypatch.setattr(store, "initialize", fail)
    monkeypatch.setattr(store, "close", close)
    monkeypatch.setattr(worker, "ResearchStore", lambda: store)
    monkeypatch.setattr(sys, "argv", ["research-worker"])
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    with pytest.raises(OSError, match="controlled schema failure"):
        worker.main()
    assert closed == [True]
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
