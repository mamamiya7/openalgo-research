"""The historical artifact limit must not become a per-chunk allocation."""

import gc
import gzip
import hashlib
import json
import tracemalloc
from types import SimpleNamespace

import psutil
import pytest

from services import scanner_research_service as service


def physical(tmp_path, value, *, compressed=True, digest=None):
    raw = service.encoded(value)
    digest = digest or hashlib.sha256(raw).hexdigest()
    directory = tmp_path / "artifacts"
    directory.mkdir(exist_ok=True)
    path = directory / (digest + (".json.gz" if compressed else ".json"))
    path.write_bytes(gzip.compress(raw) if compressed else raw)
    return SimpleNamespace(root=tmp_path), digest, path


@pytest.mark.parametrize("compressed", [False, True])
def test_default_reader_preserves_exact_boundary_and_rejects_one_extra_byte(
    tmp_path, monkeypatch, compressed
):
    value = {"rows": [i * 0.125 for i in range(20000)], "label": "Exact evidence"}
    store, digest, _ = physical(tmp_path, value, compressed=compressed)
    size = len(service.encoded(value))
    monkeypatch.setattr(service, "MAX_ARTIFACT_BYTES", size)
    assert service.read_artifact(store, digest) == value
    monkeypatch.setattr(service, "MAX_ARTIFACT_BYTES", size - 1)
    with pytest.raises(ValueError, match="integrity check"):
        service.read_artifact(store, digest)


def test_default_reader_keeps_historical_manifest_layout(tmp_path):
    value = [1]
    digest = hashlib.sha256(service.encoded(value)).hexdigest()
    # Default reads have always accepted arbitrary JSON member order and a
    # manifest tree before the scalar envelope, unlike bounded preamble reads.
    manifest = {
        "tree": {"array": [{"value": 1}]},
        "logical_sha256": digest,
        "format": service.CHUNK_FORMAT,
        "decoded_bytes": len(service.encoded(value)),
    }
    _, _, path = physical(tmp_path, value, digest=digest)
    path.write_bytes(gzip.compress(json.dumps(manifest).encode()))
    assert service.read_artifact(SimpleNamespace(root=tmp_path), digest) == value


def test_default_reader_rejects_truncated_compressed_evidence(tmp_path):
    store, digest, path = physical(tmp_path, {"rows": list(range(40000))})
    path.write_bytes(path.read_bytes()[:-8])
    with pytest.raises(ValueError, match="missing or damaged"):
        service.read_artifact(store, digest)


def test_repeated_default_reads_release_handles_and_use_payload_sized_memory(
    tmp_path, record_property
):
    value = {"rows": list(range(1000))}
    store, digest, _ = physical(tmp_path, value)
    _, invalid, _ = physical(tmp_path, value, digest="0" * 64)
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds

    def exercise():
        assert service.read_artifact(store, digest) == value
        with pytest.raises(ValueError, match="integrity check"):
            service.read_artifact(store, invalid)

    for _ in range(10):
        exercise()
    gc.collect()
    before_handles = handles()
    tracemalloc.start()
    try:
        initial = tracemalloc.get_traced_memory()[0]
        for _ in range(100):
            exercise()
        gc.collect()
        retained, peak = tracemalloc.get_traced_memory()
        after_handles = handles()
        record_property("iterations", 100)
        record_property("handles_before", before_handles)
        record_property("handles_after", after_handles)
        record_property("retained_bytes", retained - initial)
        record_property("peak_bytes", peak)
        assert after_handles == before_handles
        assert retained - initial < 256 * 1024
        assert peak < 2 * 1024 * 1024
    finally:
        tracemalloc.stop()
