"""Grouped scientific publication keeps per-account durable admission and recovery.

Synthetic accounts isolate orchestration, not execution economics. The real
service wrapper, seeded controller, history contracts and artifact store run.
"""

import json
from copy import deepcopy

import pytest
from test_automatic_walk_forward import AccountEngine, prepared, synthetic_prepare_gate

from database.research_db import ResearchExperiment, ResearchJob, ResearchStore, ResearchWorker
from research import automatic_walk_forward as controller
from services import research_development_evidence as history_module
from services import scanner_research_service as artifacts
from services.research_attempt_budget import KEY, attempt_reserver, merge_counts
from services.research_portfolio import run as service_run

IDENTITY = "a" * 64
POLICY = "test-batched-scientific-publication"


class StopAtPublication(Exception):
    pass


class CrashAfterObservation(Exception):
    pass


def trials(payload):
    state = payload.get("calculation") or {}
    return sum(len(fit["trials"]) for fit in state.get("fits", {}).values())


class Delivery:
    def __init__(self, store, *, batch, stop=16, pause_after=None, crash_after=None, saved=None):
        self.store, self.stop = store, stop
        self.pause_after, self.crash_after = pause_after, crash_after
        self.saved = saved
        self.publications, self.events, self.reservations = [], [], []
        self.reserver = None
        self.pause_requested = False
        self.publication_observation_counts = []

        def checkpoint(payload, display):
            self.publish(payload, display)

        if batch:
            checkpoint.publish_with_artifacts = self.publish
            checkpoint.reserve_attempt = self.reserve
        self.checkpoint = checkpoint
        self.boundary = lambda: self.pause_requested

    def reserve(self, expected, simulations, reserve, limit):
        if self.reserver is None:
            self.reserver = attempt_reserver(
                self.store, "owner", "job", "worker", IDENTITY, POLICY,
                checkpoint_attempts=expected, checkpoint_simulations=simulations, limit=limit,
            )
        actual = self.reserver(expected, simulations, reserve, limit)
        self.reservations.append(actual)
        return actual

    def publish(self, payload, display, publish=None):
        # This is the worker's publication protocol: flush actual history
        # shards and checkpoint artifacts, release that writer, then atomically
        # advance the metadata pointer. No checkpoint can reference pending RAM.
        with artifacts.artifact_publication(self.store), artifacts.compact_checkpoint_publication():
            if publish is not None:
                publish()
            for descriptor in payload.get("development_evidence", {}).get("entries", {}).values():
                if descriptor["status"] == "retained":
                    shard = artifacts.read_artifact(self.store, descriptor["inputs_artifact"])
                    history_module.contract.verify_shard(shard, descriptor=descriptor)
            digest = artifacts.save_artifact(self.store, payload)
        with self.store.sessions.begin() as db:
            receipt = db.get(ResearchExperiment, "job")
            calculation = payload.get("calculation")
            receipt.counts = json.dumps(merge_counts(
                receipt.counts, display, checkpoint_state=calculation,
            ))
            receipt.checkpoint = digest
        self.saved = deepcopy(payload)
        self.publications.append(self.saved)
        self.publication_observation_counts.append(len(self.finished))
        if trials(payload) == self.stop or (self.pause_requested and trials(payload) == self.pause_after):
            raise StopAtPublication()

    @property
    def finished(self):
        return [event for event in self.events if event["kind"] == "proposal_finished"]

    def observe(self, event):
        self.events.append(deepcopy(event))
        if event["kind"] == "proposal_finished":
            if len(self.finished) == self.crash_after:
                raise CrashAfterObservation()
            if len(self.finished) == self.pause_after:
                self.pause_requested = True

def initialized(path):
    store = ResearchStore(path)
    store.initialize()
    with store.sessions.begin() as db:
        db.get(ResearchWorker, 1).token = "worker"
        db.add(ResearchJob(
            id="job", owner="owner", source_id="source", config="{}", status="running",
            progress=0, created_at=1, updated_at=1, worker="worker",
        ))
        db.add(ResearchExperiment(
            job_id="job", kind="portfolio_optimize", specification="{}", identity=IDENTITY,
            counts="{}",
        ))
    return store


def execute(value, engine, delivery, monkeypatch, *, saved=None):
    monkeypatch.setattr(controller, "prepare_gate", synthetic_prepare_gate)
    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *args: None)
    monkeypatch.setattr("services.research_stock_features.prepare_context", lambda *a, **kw: a[2])
    monkeypatch.setattr("services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2])
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *args: None)

    def evaluate(*args, **kwargs):
        if hasattr(delivery.checkpoint, "reserve_attempt"):
            with delivery.store.sessions() as db:
                counter = json.loads(db.get(ResearchExperiment, "job").counts)[KEY]
            assert counter["attempts"] == delivery.reservations[-1]
        return engine(*args, **kwargs)

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", evaluate)
    return service_run(
        delivery.store, "owner", value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        job_id="job", saved=saved, checkpoint=delivery.checkpoint,
        progress=lambda *a: None, cancelled=lambda: None,
        observe=delivery.observe, boundary=delivery.boundary, defer_study_analysis=True,
    )


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    value = prepared()
    store = initialized(tmp_path_factory.mktemp("unbatched-reference"))
    delivery, engine = Delivery(store, batch=False), AccountEngine(value)
    try:
        with pytest.MonkeyPatch.context() as patch:
            with pytest.raises(StopAtPublication):
                execute(value, engine, delivery, patch)
        return value, delivery.saved, engine.calls
    finally:
        store.close()


def test_sixteen_proposals_share_one_publication_with_identical_scientific_state(reference, tmp_path, monkeypatch):
    value, expected, calls = reference
    store = initialized(tmp_path / "batched")
    delivery, engine = Delivery(store, batch=True), AccountEngine(value)
    try:
        with pytest.raises(StopAtPublication):
            execute(value, engine, delivery, monkeypatch)
        assert delivery.saved == expected
        assert engine.calls == calls
        assert delivery.saved["calculation"]["attempts"] == len(delivery.reservations)
        assert [trials(row) for row in delivery.publications if trials(row)] == [16]
        assert len(delivery.finished) == 15
        assert delivery.publication_observation_counts[-1] == 15
        assert delivery.boundary.checkpoint_pause is True
    finally:
        store.close()


def test_pause_forces_pending_three_proposals_and_shards_to_publish(reference, tmp_path, monkeypatch):
    value, _, _ = reference
    store = initialized(tmp_path / "paused")
    delivery, engine = Delivery(store, batch=True, pause_after=3), AccountEngine(value)
    try:
        with pytest.raises(StopAtPublication):
            execute(value, engine, delivery, monkeypatch)
        assert trials(delivery.saved) == len(delivery.finished) == 3
        assert delivery.saved["calculation"]["simulations"] == len(engine.calls)
        assert all(row["status"] == "retained" for row in delivery.saved["development_evidence"]["entries"].values())
        with store.sessions() as db:
            receipt = db.get(ResearchExperiment, "job")
            assert artifacts.read_artifact(store, receipt.checkpoint) == delivery.saved
            counter = json.loads(receipt.counts)[KEY]
        assert counter["checkpoint_attempts"] == counter["attempts"]
    finally:
        store.close()


def test_crash_replays_unpublished_proposals_and_keeps_durable_attempt_charge(reference, tmp_path, monkeypatch):
    value, expected, calls = reference
    store = initialized(tmp_path / "crash")
    delivery, engine = Delivery(store, batch=True, crash_after=3), AccountEngine(value)
    try:
        with pytest.raises(CrashAfterObservation):
            execute(value, engine, delivery, monkeypatch)
        assert len(delivery.finished) == 3
        assert trials(delivery.saved) == 0
        with store.sessions() as db:
            before = json.loads(db.get(ResearchExperiment, "job").counts)[KEY]
        lost = before["attempts"] - before["checkpoint_attempts"]
        assert lost == 3
        resumed = Delivery(store, batch=True, saved=delivery.saved)
        with pytest.raises(StopAtPublication):
            execute(value, engine, resumed, monkeypatch, saved=delivery.saved)
        actual = deepcopy(resumed.saved)
        # Lost calculations consume budget; all retained scientific outcomes,
        # proposals, exact histories and ranking evidence must reproduce.
        actual_state, expected_state = actual["calculation"], expected["calculation"]
        assert actual_state["attempts"] == expected_state["attempts"] + lost
        actual_state["attempts"] = expected_state["attempts"]
        actual_state["checkpoint_id"] = expected_state["checkpoint_id"]
        assert actual == expected
        assert engine.calls[:len(calls[:4])] == calls[:4]
        assert len(engine.calls) == len(calls) + lost
    finally:
        store.close()


def test_failed_shard_flush_cannot_advance_checkpoint_metadata(reference, tmp_path, monkeypatch):
    value, _, _ = reference
    store = initialized(tmp_path / "failed-flush")
    delivery, engine = Delivery(store, batch=True, pause_after=3), AccountEngine(value)
    original = history_module.save_artifact
    failed = []

    def fail_after_observed_prefix(selected_store, shard):
        if len(delivery.finished) == 3:
            failed.append(shard["evaluation_id"])
            raise OSError("controlled shard flush failure")
        return original(selected_store, shard)

    monkeypatch.setattr(history_module, "save_artifact", fail_after_observed_prefix)
    try:
        with pytest.raises(OSError, match="shard flush failure"):
            execute(value, engine, delivery, monkeypatch)
        assert failed
        assert trials(delivery.saved) == 0
        with store.sessions() as db:
            receipt = db.get(ResearchExperiment, "job")
            assert artifacts.read_artifact(store, receipt.checkpoint) == delivery.saved
            counter = json.loads(receipt.counts)[KEY]
        assert counter["attempts"] > counter["checkpoint_attempts"]
        assert counter["checkpoint_attempts"] == delivery.saved["calculation"]["attempts"]
    finally:
        store.close()
