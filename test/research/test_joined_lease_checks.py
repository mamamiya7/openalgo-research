"""One fresh combined fence observation per native calculation callback."""

# ruff: noqa: F811 -- shared isolated fixture

import threading
import time

import pytest
from sqlalchemy import delete, event
from sqlalchemy.engine import Connection
from test_lease_read_checks import claimed  # noqa: F401 -- shared isolated fixture

from database.research_db import ResearchJob, ResearchWorker
from services import scanner_research_worker as worker


def test_each_callback_reads_both_live_fences_in_one_statement(claimed, monkeypatch):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(claimed.engine, "before_cursor_execute", record)
    try:
        with worker.network_lease(claimed, "owner", "job") as cancelled:
            for _ in range(100):
                assert cancelled() is False
            assert len(statements) == 100
            assert all(statement.startswith("SELECT") for statement in statements)
            assert all("research_worker" in sql and "research_jobs" in sql for sql in statements)
        # Returning from the context also performs its own uncached fence read.
        assert len(statements) == 101
    finally:
        event.remove(claimed.engine, "before_cursor_execute", record)
    assert not any(thread.name == "research-network-lease" for thread in threading.enumerate())


@pytest.mark.parametrize(
    "change", ["lease", "job_worker", "cancel", "missing_job", "missing_lease"]
)
def test_next_callback_rejects_each_changed_or_missing_live_fence(claimed, monkeypatch, change):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    with pytest.raises(worker.Cancelled, match="Cancellation requested or worker lease lost"):
        with worker.network_lease(claimed, "owner", "job") as cancelled:
            assert cancelled() is False
            with claimed.sessions.begin() as db:
                if change == "lease":
                    db.get(ResearchWorker, 1).token = "replacement"
                elif change == "job_worker":
                    db.get(ResearchJob, "job").worker = "replacement"
                elif change == "cancel":
                    db.get(ResearchJob, "job").status = "cancelling"
                elif change == "missing_job":
                    db.execute(delete(ResearchJob).where(ResearchJob.id == "job"))
                else:
                    db.execute(delete(ResearchWorker).where(ResearchWorker.id == 1))
            cancelled()
    assert not any(thread.name == "research-network-lease" for thread in threading.enumerate())


def test_pausing_remains_live_and_local_stop_precedes_database_query(claimed, monkeypatch):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    stopping = [False]
    with pytest.raises(worker.Interrupted, match="Worker shutdown requested"):
        with worker.network_lease(
            claimed, "owner", "job", stop_requested=lambda: stopping[0]
        ) as cancelled:
            with claimed.sessions.begin() as db:
                db.get(ResearchJob, "job").status = "pausing"
            assert cancelled() is False
            stopping[0] = True

            def unexpected_query(*args):
                pytest.fail("A local stop must be checked before accessing the database")

            event.listen(claimed.engine, "before_cursor_execute", unexpected_query)
            try:
                cancelled()
            finally:
                event.remove(claimed.engine, "before_cursor_execute", unexpected_query)
    assert not any(thread.name == "research-network-lease" for thread in threading.enumerate())


def test_owner_read_connection_sees_independent_commits_without_read_transaction(
    claimed, monkeypatch
):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    owner_thread = threading.get_ident()
    connections = []

    def record(connection, _cursor, statement, _parameters, _context, _many):
        if threading.get_ident() == owner_thread and statement.startswith(
            "SELECT research_worker.token"
        ):
            connections.append(connection.connection.dbapi_connection)

    event.listen(claimed.engine, "before_cursor_execute", record)
    try:
        with worker.network_lease(claimed, "owner", "job") as cancelled:
            for status in ("running", "pausing", "running"):
                with claimed.sessions.begin() as db:
                    db.get(ResearchJob, "job").status = status
                assert cancelled() is False
                assert connections[-1].in_transaction is False
            assert len({id(connection) for connection in connections}) == 1
            with claimed.sessions.begin() as db:
                db.get(ResearchWorker, 1).token = "successor"
            with pytest.raises(worker.Cancelled):
                cancelled()
            with claimed.sessions.begin() as db:
                db.get(ResearchWorker, 1).token = "owner"
    finally:
        event.remove(claimed.engine, "before_cursor_execute", record)


def test_parallel_callbacks_and_monitor_never_share_owner_connection(claimed, monkeypatch):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 0.01)
    owner_thread = threading.get_ident()
    owners = {}
    errors = []

    def record(connection, _cursor, _statement, _parameters, _context, _many):
        owners.setdefault(threading.get_ident(), set()).add(
            id(connection.connection.dbapi_connection)
        )

    event.listen(claimed.engine, "before_cursor_execute", record)
    try:
        with worker.network_lease(claimed, "owner", "job") as cancelled:
            assert cancelled() is False
            owner_connections = set(owners[owner_thread])
            gate = threading.Barrier(3)

            def other_caller():
                try:
                    gate.wait(timeout=3)
                    for _ in range(10):
                        assert cancelled() is False
                except BaseException as error:
                    errors.append(error)

            helpers = [threading.Thread(target=other_caller) for _ in range(2)]
            for helper in helpers:
                helper.start()
            gate.wait(timeout=3)
            for helper in helpers:
                helper.join(3)
                assert not helper.is_alive()
            assert not errors
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(owners) < 4:
                time.sleep(0.01)
            assert len(owners) >= 4  # Owner, two broker helpers, heartbeat monitor.
            for thread_id, connections in owners.items():
                if thread_id != owner_thread:
                    assert not (connections & owner_connections)
    finally:
        event.remove(claimed.engine, "before_cursor_execute", record)


def test_repeated_scopes_return_every_checked_out_connection(claimed, monkeypatch):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    live = set()
    counts = [0, 0]

    def checkout(connection, _record, _proxy):
        live.add(id(connection))
        counts[0] += 1

    def checkin(connection, _record):
        live.remove(id(connection))
        counts[1] += 1

    event.listen(claimed.engine, "checkout", checkout)
    event.listen(claimed.engine, "checkin", checkin)
    try:
        for _ in range(100):
            with worker.network_lease(claimed, "owner", "job") as cancelled:
                assert cancelled() is False
                assert len(live) == 1
            assert not live
        assert counts == [100, 100]
    finally:
        event.remove(claimed.engine, "checkout", checkout)
        event.remove(claimed.engine, "checkin", checkin)


@pytest.mark.parametrize("failure", ["body", "query", "thread_start", "autocommit_setup"])
def test_owner_connection_is_closed_on_each_error_path(claimed, monkeypatch, failure):
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 60)
    counts = [0, 0]

    def checkout(*args):
        counts[0] += 1

    def checkin(*args):
        counts[1] += 1

    def fail(*args, **kwargs):
        raise OSError("controlled lease failure")

    if failure == "thread_start":
        monkeypatch.setattr(threading.Thread, "start", fail)
    elif failure == "autocommit_setup":
        monkeypatch.setattr(Connection, "execution_options", fail)
    elif failure == "query":
        event.listen(claimed.engine, "before_cursor_execute", fail)
    event.listen(claimed.engine, "checkout", checkout)
    event.listen(claimed.engine, "checkin", checkin)
    try:
        with pytest.raises(OSError, match="controlled lease failure"):
            with worker.network_lease(claimed, "owner", "job") as cancelled:
                assert cancelled() is False
                if failure == "body":
                    fail()
        assert counts == [1, 1]
    finally:
        event.remove(claimed.engine, "checkout", checkout)
        event.remove(claimed.engine, "checkin", checkin)
        if failure == "query":
            event.remove(claimed.engine, "before_cursor_execute", fail)
    assert not any(thread.name == "research-network-lease" for thread in threading.enumerate())
