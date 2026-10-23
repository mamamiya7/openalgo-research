"""Bounded physical checkpoint compaction keeps logical evidence admission."""

# ruff: noqa: F811 -- imported pytest fixture
import gzip
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from test_chunked_artifacts import store  # noqa: F401

from database.research_db import ResearchStore
from services import scanner_research_service as service


def payload():
    return {"state": [{"number": i, "prices": list(range(i, i + 20))} for i in range(25)]}


def identity(value):
    return hashlib.sha256(service.encoded(value)).hexdigest()


def files(store):
    return list((store.root / "artifacts").glob("*.json*"))


@pytest.mark.parametrize("as_list", [False, True])
def test_compact_and_chunked_evidence_have_exact_logical_identity_and_physical_dependencies(
    store, tmp_path, monkeypatch, as_list
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    value = payload()
    if as_list:
        value = value["state"]
    other = ResearchStore(tmp_path / "chunked")
    other.initialize()
    try:
        ordinary = service.save_artifact(other, value)
        with service.compact_checkpoint_publication():
            compact = service.save_artifact(store, value)
        assert compact == ordinary == identity(value)
        limit = len(service.encoded(value))
        for selected in (store, other):
            assert service.read_artifact(selected, compact) == value
            assert service.read_artifact(selected, compact, max_bytes=limit) == value
            with pytest.raises(ValueError, match="decoded size limit"):
                service.read_artifact(selected, compact, max_bytes=limit - 1)
        assert len(files(store)) == 1 and len(files(other)) > 1
        assert service.artifact_dependencies(store, compact) == set()
        assert service.artifact_dependencies(other, compact)
        before = {p.name: p.read_bytes() for p in files(other)}
        with service.compact_checkpoint_publication():
            assert service.save_artifact(other, value) == ordinary
        assert {p.name: p.read_bytes() for p in files(other)} == before
    finally:
        other.close()


def test_compact_one_file_uses_original_strict_quota_scan_without_reconciliation(
    store, monkeypatch
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    original = service._managed_storage_snapshot
    calls = []
    monkeypatch.setattr(
        service, "_managed_storage_snapshot", lambda s: calls.append(1) or original(s)
    )
    with service.compact_checkpoint_publication():
        key = service.save_artifact(store, payload())
    assert calls == [1]
    assert service.read_artifact(store, key) == payload()


def test_safe_compressed_upper_bound_avoids_compressing_discarded_leaves_and_manifest(
    store, monkeypatch
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    original = service.gzip.compress
    calls = []

    def compress(raw, **kwargs):
        calls.append(raw)
        return original(raw, **kwargs)

    monkeypatch.setattr(service.gzip, "compress", compress)
    with service.compact_checkpoint_publication():
        key = service.save_artifact(store, payload())
    assert calls == [service.encoded(payload())]
    assert service.read_artifact(store, key) == payload()


def test_compact_reuses_root_bytes_but_encodes_identical_children_and_manifest(
    store, tmp_path, monkeypatch
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    other = ResearchStore(tmp_path / "ordinary-root-encoding")
    other.initialize()
    value = payload()
    ordinary, compact = [], []
    active = ordinary
    original = service.encoded

    def encode(item):
        raw = original(item)
        active.append(raw)
        return raw

    monkeypatch.setattr(service, "encoded", encode)
    try:
        expected = service.save_artifact(other, value)
        active = compact
        with service.compact_checkpoint_publication():
            actual = service.save_artifact(store, value)
        assert expected == actual
        assert ordinary[0] == ordinary[1] == compact[0] == original(value)
        # Only the second whole-root encoding disappears. Every admitted child
        # and the complete logical chunk manifest keep their original bytes.
        assert ordinary[:1] + ordinary[2:] == compact
        assert ordinary[-1] == compact[-1]
    finally:
        other.close()


def test_above_compact_bound_falls_back_to_ordinary_chunks(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    monkeypatch.setattr(service, "MAX_COMPACT_CHECKPOINT_BYTES", 256)
    with service.compact_checkpoint_publication():
        key = service.save_artifact(store, payload())
    assert service.artifact_dependencies(store, key)
    assert service.read_artifact(store, key) == payload()


def test_unusual_container_keeps_ordinary_chunk_path(store, monkeypatch):
    class CustomDict(dict):
        pass

    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    with service.compact_checkpoint_publication():
        key = service.save_artifact(store, CustomDict(payload()))
    assert service.artifact_dependencies(store, key)


@pytest.mark.parametrize("bound", ["nodes", "depth", "decoded", "compressed"])
def test_compact_preserves_structure_and_size_rejections_without_publishing_root(
    store, monkeypatch, bound
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 32)
    value = payload()
    message = "chunk structure limit"
    if bound == "nodes":
        monkeypatch.setattr(service, "MAX_ARTIFACT_NODES", 2)
    elif bound == "depth":
        value = "x" * 80
        for _ in range(34):
            value = {"nested": value}
    elif bound == "decoded":
        monkeypatch.setattr(service, "MAX_ARTIFACT_BYTES", 64)
        message = "decoded research size limit"
    else:
        monkeypatch.setattr(service, "MAX_COMPRESSED_BYTES", 5)
        message = "compressed research limit"
    with pytest.raises(ValueError, match=message):
        with service.compact_checkpoint_publication():
            service.save_artifact(store, value)
    assert not files(store)
    assert service._compact_checkpoint_publication.get() is None
    assert service._artifact_publication.get() is None


def test_compact_still_admits_the_logical_chunk_manifest_compressed_bound(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    monkeypatch.setattr(service, "MAX_COMPRESSED_BYTES", 1024)
    original = service.gzip.compress

    def compress(raw, **kwargs):
        if raw.startswith(b'{"decoded_bytes":'):
            return b"x" * 1025
        return original(raw, **kwargs)

    monkeypatch.setattr(service.gzip, "compress", compress)
    with pytest.raises(ValueError, match="compressed research limit"):
        with service.compact_checkpoint_publication():
            service.save_artifact(store, payload())
    assert not files(store)


@pytest.mark.parametrize("bound", ["quota", "entries"])
def test_compact_keeps_physical_quota_and_entry_guards(store, monkeypatch, bound):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "1")
    size = len(gzip.compress(service.encoded(payload()), compresslevel=3, mtime=0))
    used, entries = (
        (1024**2 - size + 1, 1) if bound == "quota" else (0, service.MAX_MANAGED_ENTRIES)
    )
    monkeypatch.setattr(service, "_managed_storage_snapshot", lambda s: (used, entries))
    message = "storage is full" if bound == "quota" else "management bound"
    with pytest.raises(ValueError, match=message):
        with service.compact_checkpoint_publication():
            service.save_artifact(store, payload())
    assert not files(store)


def test_shadow_leaf_paths_still_refuse_symlinks(store, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 64)
    value = {"first": "x" * 80, "second": "y" * 80}
    target = tmp_path / "outside"
    target.write_bytes(b"preserve")
    directory = store.root / "artifacts"
    directory.mkdir()
    try:
        (directory / (identity(value["first"]) + ".json.gz")).symlink_to(target)
    except OSError as error:
        pytest.skip(f"Windows cannot create test symlink: {error}")
    with pytest.raises(ValueError, match="Unsafe research artifact path"):
        with service.compact_checkpoint_publication():
            service.save_artifact(store, value)
    assert target.read_bytes() == b"preserve"
    assert not (directory / (identity(value) + ".json.gz")).exists()


def test_shadow_leaf_safety_failure_is_not_bypassed_when_leaves_are_not_written(
    store, monkeypatch
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 64)
    value = {"first": "x" * 80, "second": "y" * 80}
    forbidden = identity(value["first"])
    original = service._ArtifactPublication.paths
    checked = []

    def paths(self, digest):
        checked.append(digest)
        if digest == forbidden:
            raise ValueError("Unsafe research artifact path")
        return original(self, digest)

    monkeypatch.setattr(service._ArtifactPublication, "paths", paths)
    with pytest.raises(ValueError, match="Unsafe research artifact path"):
        with service.compact_checkpoint_publication():
            service.save_artifact(store, value)
    assert forbidden in checked and not files(store)


def test_flat_checkpoint_damage_still_fails_integrity(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    with service.compact_checkpoint_publication():
        key = service.save_artifact(store, payload())
    path = store.root / "artifacts" / (key + ".json.gz")
    path.write_bytes(gzip.compress(b'{"wrong":"evidence"}'))
    with pytest.raises(ValueError, match="integrity check failed"):
        service.read_artifact(store, key)


def test_nested_exception_expired_and_cross_thread_scopes_cannot_escape(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    with service.compact_checkpoint_publication():
        outer = service._compact_checkpoint_publication.get()
        with pytest.raises(RuntimeError):
            with service.compact_checkpoint_publication():
                raise RuntimeError("controlled")
        assert service._compact_checkpoint_publication.get() is outer
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            cross_thread = executor.submit(
                copied.run, service.save_artifact, store, payload()
            ).result(10)
        assert service.artifact_dependencies(store, cross_thread)
        expired = copy_context()
    assert outer.closed and service._compact_checkpoint_publication.get() is None
    changed = {"later": payload()}
    key = expired.run(service.save_artifact, store, changed)
    assert service.artifact_dependencies(store, key)
    assert service._artifact_publication.get() is None


def test_repeated_compact_scopes_release_files_sessions_and_context(store, monkeypatch):
    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    process = psutil.Process()
    handles = getattr(process, "num_handles", None) or process.num_fds
    with service.compact_checkpoint_publication():
        service.save_artifact(store, payload())
    before = handles()
    for number in range(20):
        value = {"number": number, **payload()}
        with service.compact_checkpoint_publication():
            key = service.save_artifact(store, value)
        assert service.read_artifact(store, key) == value
        assert service._compact_checkpoint_publication.get() is None
        assert service._artifact_publication.get() is None
    assert handles() <= before + 2
    assert not list((store.root / "artifacts").glob("*.tmp"))
