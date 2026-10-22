"""Replay boundaries retain live fences without taking SQLite's writer slot."""

import hashlib
import time

import pytest
from sqlalchemy import event

from database.research_db import ResearchExperiment, ResearchJob, ResearchStore, ResearchWorker
from research.connectors import registry
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker


@pytest.fixture
def invoke(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path)
    store.initialize()
    token, job_id, source_id = "boundary-worker", "boundary-job", "source"
    spec = {"portfolio": {}}
    identity = hashlib.sha256(
        service.encoded(
            {
                "source_id": source_id,
                "config": {},
                "kind": "portfolio_optimize",
                "specification": spec,
                "policy_version": "test-policy",
            }
        )
    ).hexdigest()
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=job_id,
                owner="test-owner",
                source_id=source_id,
                config="{}",
                status="queued",
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        db.add(
            ResearchExperiment(
                job_id=job_id,
                kind="portfolio_optimize",
                specification=service.encoded(spec).decode(),
                identity=identity,
                counts="{}",
            )
        )
    monkeypatch.setattr(registry, "policy_for_request", lambda *args: "test-policy")
    monkeypatch.setattr(worker, "source_for", lambda *args: {"snapshot": {}, "receipt": {}})

    def run(callback, stop_requested=None):
        def calculate(*args, boundary, **kwargs):
            callback(store, job_id, token, boundary)
            raise worker.Interrupted()

        monkeypatch.setattr(research_portfolio, "run", calculate)
        worker.acquire(store, token)
        try:
            assert worker.run_one(store, token, stop_requested=stop_requested)
        finally:
            worker.release(store, token)

    yield run
    store.close()


def test_running_replay_checks_each_live_fence_without_writes(invoke):
    def check(store, job_id, token, boundary):
        statements = []

        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(store.engine, "before_cursor_execute", record)
        try:
            for _ in range(10):
                boundary()
        finally:
            event.remove(store.engine, "before_cursor_execute", record)
        assert len(statements) == 10
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert all("research_worker" in sql and "research_jobs" in sql for sql in statements)
        with store.sessions.begin() as db:
            db.get(ResearchJob, job_id).status = "cancelling"
        with pytest.raises(worker.Cancelled):
            boundary()

    invoke(check)


@pytest.mark.parametrize("race", ["cancel", "lease", "worker", "shutdown", "running"])
def test_pause_rechecks_races_after_read_before_writer_reservation(invoke, race):
    stopping = [False]

    def check(store, job_id, token, boundary):
        with store.sessions.begin() as db:
            db.get(ResearchJob, job_id).status = "pausing"
        injections = []

        def intervene(connection, cursor, statement, parameters, context, executemany):
            if injections or not statement.startswith("UPDATE research_worker SET id="):
                return
            injections.append(True)
            if race == "shutdown":
                stopping[0] = True
                return
            # An independent connection commits between the optimistic read and
            # the worker's actual writer reservation. The read session is closed.
            with store.sessions.begin() as db:
                if race == "lease":
                    db.get(ResearchWorker, 1).token = "successor"
                elif race == "worker":
                    db.get(ResearchJob, job_id).worker = "successor"
                else:
                    db.get(ResearchJob, job_id).status = (
                        "running" if race == "running" else "cancelling"
                    )

        event.listen(store.engine, "before_cursor_execute", intervene)
        try:
            if race == "running":
                boundary()
            else:
                expected = worker.Interrupted if race == "shutdown" else worker.Cancelled
                with pytest.raises(expected):
                    boundary()
        finally:
            event.remove(store.engine, "before_cursor_execute", intervene)
        assert injections == [True]
        with store.sessions() as db:
            assert db.get(ResearchJob, job_id).status != "paused"

    invoke(check, stop_requested=lambda: stopping[0])


@pytest.mark.parametrize("failure", [False, True])
def test_pause_acknowledgement_and_observation_share_transaction(invoke, monkeypatch, failure):
    def finish(self, db, state, reason):
        assert state == "paused" and reason == "pause_requested"
        assert db.in_transaction()
        db.get(ResearchExperiment, self.job_id).counts = '{"pause_observed":true}'
        if failure:
            raise RuntimeError("observation write failed")

    monkeypatch.setattr(worker.study_activity.StudyObserver, "finish", finish)

    def check(store, job_id, token, boundary):
        with store.sessions.begin() as db:
            db.get(ResearchJob, job_id).status = "pausing"
            original_counts = db.get(ResearchExperiment, job_id).counts
        with pytest.raises(RuntimeError if failure else worker.Paused):
            boundary()
        with store.sessions() as db:
            job = db.get(ResearchJob, job_id)
            observation = db.get(ResearchExperiment, job_id).counts
            assert job.status == ("pausing" if failure else "paused")
            assert job.worker == (token if failure else None)
            assert observation == (original_counts if failure else '{"pause_observed":true}')
        # The enclosing harness uses interruption only to end its fake engine.
        monkeypatch.setattr(worker.study_activity.StudyObserver, "finish", lambda *a, **k: None)

    invoke(check)
