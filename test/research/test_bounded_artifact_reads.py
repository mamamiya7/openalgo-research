"""Caller-specific evidence limits must apply before decode and across chunks."""

import gc
import gzip
import hashlib
import tracemalloc
from types import SimpleNamespace

import psutil
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


def physical(store, value, *, digest=None, compressed=False):
    raw = service.encoded(value)
    digest = digest or hashlib.sha256(raw).hexdigest()
    directory = store.root / "artifacts"
    directory.mkdir(exist_ok=True)
    path = directory / (digest + (".json.gz" if compressed else ".json"))
    path.write_bytes(gzip.compress(raw) if compressed else raw)
    return digest


@pytest.mark.parametrize("compressed", [False, True])
def test_oversized_leaf_rejected_before_json_decode(store, monkeypatch, compressed):
    digest = physical(store, {"observations": "x" * 50000}, compressed=compressed)
    monkeypatch.setattr(service.json, "loads", lambda *_: pytest.fail("Oversized leaf parsed"))
    with pytest.raises(ValueError, match="decoded size limit"):
        service.read_artifact(store, digest, max_bytes=100)


@pytest.mark.parametrize("compressed", [False, True])
def test_exact_boundary_preserves_hash_and_default_layout(store, compressed):
    value = {"observations": [1.2, 2.3], "identity": "unchanged"}
    digest = physical(store, value, compressed=compressed)
    assert service.read_artifact(store, digest, max_bytes=len(service.encoded(value))) == value
    assert service.read_artifact(store, digest) == value
    assert (
        hashlib.sha256(service.encoded(service.read_artifact(store, digest))).hexdigest() == digest
    )


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("padding", [0, 5000])
def test_small_payload_with_large_caller_limit_does_not_allocate_the_limit(
    store, compressed, padding
):
    value = {"observations": [1.2, 2.3], "identity": "unchanged", "padding": "x" * padding}
    digest = physical(store, value, compressed=compressed)
    tracemalloc.start()
    try:
        assert service.read_artifact(store, digest, max_bytes=24 * 1024 * 1024) == value
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 1024 * 1024
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("limit", [True, False, -1, 0, 1.5, "100", service.MAX_ARTIFACT_BYTES + 1])
def test_invalid_limits_fail_before_filesystem_access(limit):
    with pytest.raises(ValueError, match="positive integer"):
        service.read_artifact(SimpleNamespace(), "a" * 64, max_bytes=limit)


def manifest(store, digest, size, tree, *, compressed=True):
    return physical(
        store,
        {
            "format": service.CHUNK_FORMAT,
            "logical_sha256": digest,
            "decoded_bytes": size,
            "tree": tree,
        },
        digest=digest,
        compressed=compressed,
    )


def test_manifest_declared_size_rejected_before_parse_or_children(store, monkeypatch):
    digest = manifest(store, "a" * 64, 10000, {"artifact": "b" * 64})
    monkeypatch.setattr(service.json, "loads", lambda *_: pytest.fail("Oversized manifest parsed"))
    with pytest.raises(ValueError, match="decoded size limit"):
        service.read_artifact(store, digest, max_bytes=100)


def test_native_chunked_evidence_and_small_manifest_overhead(store, monkeypatch):
    # Only alter chunk granularity, never the global admission limit.
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    value = {"rows": [{"day": i, "price": i + 0.25} for i in range(200)]}
    digest = service.save_artifact(store, value)
    size = len(service.encoded(value))
    assert service.read_artifact(store, digest, max_bytes=size) == value
    with pytest.raises(ValueError, match="decoded size limit"):
        service.read_artifact(store, digest, max_bytes=size - 1)
    tiny = [1]
    tiny_digest = hashlib.sha256(service.encoded(tiny)).hexdigest()
    manifest(store, tiny_digest, 3, {"array": [{"value": 1}]})
    assert service.read_artifact(store, tiny_digest, max_bytes=3) == tiny


def test_forged_manifest_cannot_expand_repeated_children_past_budget(store):
    child = physical(store, "x" * 60, compressed=True)
    digest = manifest(store, "c" * 64, 50, {"array": [{"artifact": child}] * 30})
    with pytest.raises(ValueError, match="decoded size limit"):
        service.read_artifact(store, digest, max_bytes=100)


def test_oversized_child_cannot_hide_under_forged_declared_size(store, monkeypatch):
    child = physical(store, "x" * 50000, compressed=True)
    digest = manifest(store, "d" * 64, 50, {"artifact": child})
    actual_decode = service.json.loads
    decoded = []

    def decode(raw):
        decoded.append(len(raw))
        return actual_decode(raw)

    monkeypatch.setattr(service.json, "loads", decode)
    with pytest.raises(ValueError, match="decoded size limit"):
        service.read_artifact(store, digest, max_bytes=100)
    assert len(decoded) == 1  # Only the admitted root envelope, never its oversized child.


def test_final_reconstructed_size_and_hash_are_both_required(store):
    digest = "e" * 64
    manifest(store, digest, 2, {"object": {"long-key" * 10: {"value": 1}}})
    with pytest.raises(ValueError, match="integrity check"):
        service.read_artifact(store, digest, max_bytes=20)
    manifest(store, digest, 3, {"array": [{"value": 1}]})
    with pytest.raises(ValueError, match="integrity check"):
        service.read_artifact(store, digest, max_bytes=20)


def test_bounded_cycle_and_tampering_preserve_existing_security(store):
    digest = manifest(store, "f" * 64, 20, {"artifact": "f" * 64})
    with pytest.raises(ValueError, match="Cyclic"):
        service.read_artifact(store, digest, max_bytes=100)
    digest = physical(store, {"changed": True}, digest="0" * 64)
    with pytest.raises(ValueError, match="integrity check"):
        service.read_artifact(store, digest, max_bytes=100)


def test_repeated_success_and_rejection_release_handles_and_memory(store, record_property):
    valid = physical(store, {"rows": list(range(100))}, compressed=True)
    invalid = physical(store, "x" * 50000, compressed=True)
    process = psutil.Process()
    count = process.num_handles if hasattr(process, "num_handles") else process.num_fds

    def exercise():
        assert service.read_artifact(store, valid, max_bytes=1024)["rows"][-1] == 99
        with pytest.raises(ValueError):
            service.read_artifact(store, invalid, max_bytes=100)

    for _ in range(10):
        exercise()
    gc.collect()
    before_handles = count()
    tracemalloc.start()
    try:
        before_memory = tracemalloc.get_traced_memory()[0]
        for _ in range(150):
            exercise()
        gc.collect()
        retained, peak = tracemalloc.get_traced_memory()
        after_handles = count()
        record_property("iterations", 150)
        record_property("handles_before", before_handles)
        record_property("handles_after", after_handles)
        record_property("retained_bytes", retained - before_memory)
        record_property("peak_bytes", peak)
        assert after_handles == before_handles
        assert retained - before_memory < 256 * 1024
        assert peak < 2 * 1024 * 1024
    finally:
        tracemalloc.stop()
