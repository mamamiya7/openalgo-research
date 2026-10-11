"""Consumed dates and their immutable inputs survive pruning and restoration."""

import gc
import os
import time

import pytest
from sqlalchemy import event, select
from test_automatic_conditions import prepared

from database.research_db import ResearchAutomaticPeriodUse, ResearchStore
from services import scanner_research_service as service
from services.research_automatic_periods import claim
from services.research_storage import (
    _references,
    backup_store,
    inspect_storage,
    prune_orphans,
    restore_store,
)


@pytest.fixture
def store(tmp_path):
    value = ResearchStore(tmp_path / "period-store")
    value.initialize()
    try:
        yield value
    finally:
        value.close()


def retained_claim(store):
    evidence = prepared()
    digest = service.save_artifact(store, evidence)
    claim(store, "owner", digest, evidence, job_id="job-a")
    return evidence, digest


def test_orphan_claim_preserves_inputs_prune_backup_restore_and_idempotent_resume(store, tmp_path):
    evidence, digest = retained_claim(store)
    orphan = service.save_artifact(store, {"unrelated": True})
    for path in (store.root / "artifacts").iterdir():
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    assert digest in _references(store.engine)
    inspected = inspect_storage(store)
    assert inspected["referenced_artifacts"] >= 1 and inspected["orphan_files"] == 1
    prune_orphans(store, apply=True)
    assert service.read_artifact(store, digest) == evidence
    with pytest.raises(ValueError, match="unavailable|missing"):
        service.read_artifact(store, orphan)
    backup_store(store, tmp_path / "period-backup")
    restore_store(tmp_path / "period-backup", tmp_path / "period-restored")
    restored = ResearchStore(tmp_path / "period-restored")
    try:
        restored.initialize()
        claim(restored, "owner", digest, evidence, job_id="job-a")
        with pytest.raises(ValueError, match="already used"):
            claim(restored, "owner", "b" * 64, evidence, job_id="job-b")
        with pytest.raises(ValueError, match="already used"):
            claim(restored, "owner", digest, evidence, job_id="job-b")
        with restored.sessions() as db:
            rows = db.scalars(select(ResearchAutomaticPeriodUse)).all()
            assert len(rows) == 160 and sum(row.role == "final" for row in rows) == 32
        assert service.read_artifact(restored, digest) == evidence
    finally:
        restored.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", ""),
        ("day", "2026-99-99"),
        ("role", "unseen"),
        ("inputs_artifact", "x" * 64),
        ("job_id", ""),
        ("created_at", float("inf")),
    ],
)
def test_invalid_consumed_period_metadata_blocks_maintenance(store, field, value):
    retained_claim(store)
    with store.sessions.begin() as db:
        row = db.scalar(select(ResearchAutomaticPeriodUse).limit(1))
        setattr(row, field, value)
    with pytest.raises(ValueError, match="period history"):
        inspect_storage(store)


@pytest.mark.parametrize("change", ["role", "date", "recipe"])
def test_consumed_period_claim_cannot_change_original_chronology_or_recipe(store, change):
    evidence, digest = retained_claim(store)
    with store.sessions.begin() as db:
        row = db.scalar(
            select(ResearchAutomaticPeriodUse)
            .where(ResearchAutomaticPeriodUse.role == "final")
            .limit(1)
        )
        if change == "role":
            row.role = "development"
        elif change == "date":
            row.day = "2026-12-31"
        else:
            evidence["automatic_recipe"]["periods"]["final"]["from"] = "2026-01-01"
            altered = service.save_artifact(store, evidence)
            row.inputs_artifact = altered
    with pytest.raises(ValueError, match="chronology|recipe"):
        inspect_storage(store)


def test_missing_claim_input_fails_closed_and_legacy_absent_table_remains_supported(store):
    retained_claim(store)
    with store.sessions.begin() as db:
        row = db.scalar(select(ResearchAutomaticPeriodUse).limit(1))
        row.inputs_artifact = "f" * 64
    with pytest.raises(ValueError, match="unsafe or missing"):
        inspect_storage(store)
    with store.engine.begin() as db:
        db.exec_driver_sql("DROP TABLE research_automatic_period_use")
    assert inspect_storage(store)["referenced_artifacts"] == 0


def test_period_metadata_bound_blocks_maintenance(store, monkeypatch):
    retained_claim(store)
    monkeypatch.setattr("services.research_automatic_periods.MAX_ROWS", 2)
    with pytest.raises(ValueError, match="maintenance bound"):
        inspect_storage(store)


def test_repeated_period_inventory_closes_connections_and_bounds_handles(store):
    import psutil

    retained_claim(store)
    active = set()

    def opened(connection, _record):
        active.add(id(connection))

    def closed(connection, _record):
        active.discard(id(connection))

    event.listen(store.engine, "connect", opened)
    event.listen(store.engine, "close", closed)
    process = psutil.Process()
    measure = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    gc.collect()
    before = measure(), process.memory_info().rss
    try:
        for _ in range(100):
            assert inspect_storage(store)["referenced_artifacts"] >= 1
            assert not active
        gc.collect()
        assert measure() <= before[0] + 3
        assert process.memory_info().rss <= before[1] + 16 * 1024**2
    finally:
        event.remove(store.engine, "connect", opened)
        event.remove(store.engine, "close", closed)
