"""Large repeated checkpoints share storage without changing exact evidence."""

import gzip
import hashlib
import json
import random
from types import SimpleNamespace

import pytest

from database.research_db import ResearchStore
from services import scanner_research_service as service


@pytest.fixture
def store(tmp_path):
    instance = ResearchStore(tmp_path / "research")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


def test_growing_checkpoints_share_unchanged_prices_and_keep_exact_hashes(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 4096)
    rng = random.Random(731)
    state = {"bars": {}, "receipts": [], "done": 0}
    old_stored_bytes = 0
    for i in range(40):
        bars = {str(n): [rng.random() for _ in range(4)] for n in range(160)}
        state["bars"][f"S{i:04}"] = bars
        state["receipts"].append({"symbol": f"S{i:04}", "observations": bars})
        state["done"] = i + 1
        exact = service.encoded(state)
        digest = service.save_artifact(store, state)
        old_stored_bytes += len(gzip.compress(exact, compresslevel=3, mtime=0))
        assert digest == hashlib.sha256(exact).hexdigest()
    files = list((store.root / "artifacts").iterdir())
    actual_bytes = sum(path.stat().st_size for path in files)
    assert actual_bytes < old_stored_bytes / 5
    assert service.read_artifact(store, digest) == state
    assert service.artifact_dependencies(store, digest)
    assert len(files) < 1000


def test_legacy_and_chunked_layout_decode_identically(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    value = {"rows": [{"day": i, "close": i * 0.1} for i in range(800)]}
    digest = service.save_artifact(store, value)
    assert service.read_artifact(store, digest) == value
    legacy = store.root / "legacy"
    (legacy / "artifacts").mkdir(parents=True)
    (legacy / "artifacts" / f"{digest}.json").write_bytes(service.encoded(value))
    assert service.read_artifact(SimpleNamespace(root=legacy), digest) == value


def test_root_never_publishes_when_chunk_write_fails(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    original = service._write_artifact_bytes
    writes = 0

    def fail_second(*args):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("controlled interrupted write")
        return original(*args)

    monkeypatch.setattr(service, "_write_artifact_bytes", fail_second)
    value = {"one": list(range(300)), "two": list(range(300, 600))}
    digest = hashlib.sha256(service.encoded(value)).hexdigest()
    with pytest.raises(OSError, match="interrupted write"):
        service.save_artifact(store, value)
    assert not (store.root / "artifacts" / f"{digest}.json.gz").exists()
    assert not list((store.root / "artifacts").glob("*.tmp"))


@pytest.mark.parametrize("tree", [{"artifact": 3}, {"artifact": {}}, {"bad": []}])
def test_malformed_manifest_references_fail_cleanly(store, tree):
    digest = "a" * 64
    directory = store.root / "artifacts"
    directory.mkdir()
    value = {
        "format": service.CHUNK_FORMAT,
        "logical_sha256": digest,
        "decoded_bytes": 20,
        "tree": tree,
    }
    (directory / f"{digest}.json.gz").write_bytes(gzip.compress(json.dumps(value).encode()))
    with pytest.raises(ValueError):
        service.read_artifact(store, digest)


def test_chunk_cycle_and_expansion_limit_fail_closed(store, monkeypatch):
    digest = "b" * 64
    directory = store.root / "artifacts"
    directory.mkdir()
    manifest = {
        "format": service.CHUNK_FORMAT,
        "logical_sha256": digest,
        "decoded_bytes": 20,
        "tree": {"artifact": digest},
    }
    path = directory / f"{digest}.json.gz"
    path.write_bytes(gzip.compress(service.encoded(manifest)))
    with pytest.raises(ValueError, match="Cyclic"):
        service.read_artifact(store, digest)
    leaf = service.save_artifact(store, "x" * 50)
    manifest["tree"] = {"array": [{"artifact": leaf}] * 10}
    path.write_bytes(gzip.compress(service.encoded(manifest)))
    monkeypatch.setattr(service, "MAX_ARTIFACT_BYTES", 450)
    with pytest.raises(ValueError):
        service.read_artifact(store, digest)
