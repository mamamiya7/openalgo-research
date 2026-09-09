import copy
import hashlib

import pytest

from database.research_db import ResearchExperiment, ResearchJob, ResearchSource, ResearchStore
from services import research_checkpoint as recovery
from services.research_acquisition import _save_receipt
from services.research_storage import backup_store, restore_store
from services.scanner_research_service import encoded, read_artifact, save_artifact


@pytest.fixture
def store(tmp_path):
    value = ResearchStore(tmp_path / "research")
    value.initialize()
    try:
        yield value
    finally:
        value.close()


def state():
    rows = {
        str(i): {"open": i + 1.0, "high": i + 2.0, "low": i + 0.5, "close": i + 1.5}
        for i in range(1500)
    }
    return {
        "identity": "immutable-input-identity",
        "mode": "historify-minute-v1",
        "bars": {f"S{i}": copy.deepcopy(rows) for i in range(12)},
        "raw_bars": {f"S{i}": copy.deepcopy(rows) for i in range(12)},
        "findings": [{"kind": "gap", "index": i} for i in range(700)],
        "receipts": [],
        "transport_attempts": [],
        "completed_windows": [],
    }


def test_only_changed_symbol_and_new_receipt_are_serialized(store, monkeypatch):
    directory = store.root / "receipts"
    directory.mkdir()
    current = state()
    writer = recovery.MinuteCheckpointWriter(store, directory)
    first = writer.pack(current)
    sizes = []
    original = recovery.save_artifact

    def tracked(target, value):
        sizes.append(len(encoded(value)))
        return original(target, value)

    monkeypatch.setattr(recovery, "save_artifact", tracked)
    current["transport_attempts"].append({"attempt": 1, "status": 200})
    writer.pack(current)
    assert sizes == []  # A transport update never walks/serializes old prices or receipts.
    current["bars"]["S0"]["1500"] = current["bars"]["S0"]["1499"]
    current["raw_bars"]["S0"]["1500"] = current["raw_bars"]["S0"]["1499"]
    raw = {"provider": "controlled", "observations": [{"close": 1500.5}]}
    digest = _save_receipt(directory, raw)
    current["receipts"].append({"sha256": digest})
    packed = writer.pack(current)
    assert len(sizes) == 2 and sum(sizes) < len(encoded(current)) / 5
    assert packed["symbols"]["S1"] == first["symbols"]["S1"]
    reference = save_artifact(store, {"daily": "checked reference"})
    saved = {"reference_artifact": reference, "acquisition_manifest": packed}
    reopened = recovery.unpack_checkpoint(store, saved)
    assert reopened["acquisition"] == current
    assert reopened["acquisition_receipts"] == [{"sha256": digest, "receipt": raw}]
    assert hashlib.sha256(encoded(raw)).hexdigest() == digest
    sizes.clear()
    recovery.MinuteCheckpointWriter(store, directory, saved).pack(current)
    assert sizes == []  # Restart reuses stored indexes as well as stored prices.


def test_manifest_backup_restore_retains_prices_and_receipts_without_sidecars(store, tmp_path):
    directory = store.root / "receipts"
    directory.mkdir()
    current = state()
    raw = {"provider": "controlled", "rows": [1, 2, 3]}
    current["receipts"] = [{"sha256": _save_receipt(directory, raw)}]
    manifest = recovery.MinuteCheckpointWriter(store, directory).pack(current)
    source = save_artifact(store, {"signals": [{"symbol": "AAA"}]})
    saved = {"reference_artifact": source, "acquisition_manifest": manifest}
    checkpoint = save_artifact(store, {"state": saved})
    with store.sessions.begin() as db:
        db.add(ResearchSource(id="source", owner="test", artifact=source, created_at=0))
        db.add(
            ResearchJob(
                id="job",
                owner="test",
                source_id="source",
                config="{}",
                status="interrupted",
                progress=50,
                created_at=0,
                updated_at=0,
            )
        )
        db.add(
            ResearchExperiment(
                job_id="job",
                kind="acquire",
                specification="{}",
                identity="identity",
                checkpoint=checkpoint,
            )
        )
    backup_store(store, tmp_path / "backup")
    restore_store(tmp_path / "backup", tmp_path / "restored")
    restored = ResearchStore(tmp_path / "restored")
    try:
        after = recovery.unpack_checkpoint(restored, read_artifact(restored, checkpoint)["state"])
        assert after["acquisition"] == current
        assert after["acquisition_receipts"][0]["receipt"] == raw
        assert not (restored.root / "receipts").exists()
    finally:
        restored.close()


def test_inline_checkpoints_remain_unchanged_and_oversized_manifest_fails_early(store):
    old = {"reference_artifact": "x", "acquisition": state(), "acquisition_receipts": []}
    assert recovery.unpack_checkpoint(store, old) is old
    broken = {
        "version": recovery.VERSION,
        "metadata": {},
        "symbols": {"AAA": {"count": 2000001, "inputs_artifact": "missing"}},
    }
    with pytest.raises(ValueError, match="price limit"):
        recovery.unpack_checkpoint(store, {"acquisition_manifest": broken})
