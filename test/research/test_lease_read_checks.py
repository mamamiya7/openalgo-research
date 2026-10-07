"""Frequent calculation cancellation checks must not become disk writes."""

import time

import pytest
from sqlalchemy import event

from database.research_db import ResearchJob, ResearchStore, ResearchWorker
from services import scanner_research_worker as worker


@pytest.fixture
def claimed(tmp_path):
    store = ResearchStore(tmp_path)
    store.initialize()
    with store.sessions.begin() as db:
        lease = db.get(ResearchWorker, 1)
        lease.token, lease.heartbeat = "owner", time.time()
        db.add(
            ResearchJob(
                id="job",
                owner="test",
                source_id="source",
                status="running",
                worker="owner",
                created_at=time.time(),
                updated_at=time.time(),
                config="{}",
            )
        )
    try:
        yield store
    finally:
        store.close()


def test_cancellation_reads_do_not_write_and_notice_changed_fence(claimed, monkeypatch):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    writes = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            writes.append(statement)

    event.listen(claimed.engine, "before_cursor_execute", record)
    try:
        with worker.network_lease(claimed, "owner", "job") as cancelled:
            for _ in range(100):
                assert cancelled() is False
            assert writes == []
            with claimed.sessions.begin() as db:
                db.get(ResearchWorker, 1).token = "replacement"
            with pytest.raises(worker.Cancelled):
                cancelled()
            # The context also fences its final return.
            with claimed.sessions.begin() as db:
                db.get(ResearchWorker, 1).token = "owner"
    finally:
        event.remove(claimed.engine, "before_cursor_execute", record)


def test_periodic_monitor_refreshes_heartbeat_without_callbacks(claimed, monkeypatch):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 0.02)
    with claimed.sessions.begin() as db:
        db.get(ResearchWorker, 1).heartbeat = 1
    with worker.network_lease(claimed, "owner", "job"):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with claimed.sessions() as db:
                if db.get(ResearchWorker, 1).heartbeat > 1:
                    break
            time.sleep(0.01)
        else:
            pytest.fail("The background heartbeat did not refresh the lease")


def test_local_stop_is_still_immediate(claimed, monkeypatch):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    with pytest.raises(worker.Interrupted):
        with worker.network_lease(
            claimed, "owner", "job", stop_requested=lambda: True
        ) as cancelled:
            cancelled()
