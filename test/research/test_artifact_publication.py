# ruff: noqa: F811 -- pytest fixtures imported for injection
"""One guarded quota scan, strict accounting and unchanged immutable artifacts."""

import base64
import gzip
import hashlib
import random
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
from threading import Barrier

import pytest
from test_chunked_artifacts import store  # noqa: F401

from database.research_db import ResearchStore, ResearchWorker
from services import scanner_research_service as service
from services.research_storage import maintenance


def compressed_bytes(value):
    return len(gzip.compress(service.encoded(value), compresslevel=3, mtime=0))


def digest(value):
    return hashlib.sha256(service.encoded(value)).hexdigest()


def test_nested_publication_shares_admission_and_reconciles_once(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    measure = service._managed_storage_snapshot
    scans = []

    def measured(selected):
        result = measure(selected)
        scans.append(result)
        return result

    monkeypatch.setattr(service, "_managed_storage_snapshot", measured)
    values = [{"symbol": i, "bars": list(range(i, i + 200))} for i in range(4)]
    with service.artifact_publication(store) as publication:
        identifiers = [service.save_artifact(store, value) for value in values]
        with service.artifact_publication(store) as nested:
            assert nested is publication
            assert service.save_artifact(store, values[0]) == identifiers[0]
        paths = list((store.root / "artifacts").iterdir())
        assert len(paths) > len(values)  # chunk leaves and their manifests
        assert publication.used == scans[0][0] + sum(path.stat().st_size for path in paths)
        assert publication.entries == scans[0][1] + len(paths)
    assert len(scans) == 2  # one admission and one multi-file reconciliation
    assert identifiers == [digest(value) for value in values]
    assert [service.read_artifact(store, key) for key in identifiers] == values
    assert [service.save_artifact(store, value) for value in values] == identifiers
    assert len(scans) == 2  # no write means no admission/reconciliation scan
    assert service._artifact_publication.get() is None


def test_chunked_artifact_also_shares_one_admission_and_reconciliation(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    measure = service._managed_storage_snapshot
    scans = []
    monkeypatch.setattr(
        service, "_managed_storage_snapshot", lambda selected: scans.append(1) or measure(selected)
    )
    value = [{"row": i, "prices": list(range(i, i + 50))} for i in range(15)]
    key = service.save_artifact(store, value)
    assert len(list((store.root / "artifacts").iterdir())) > 10
    assert scans == [1, 1] and service.read_artifact(store, key) == value


def test_one_file_scope_keeps_one_admission_scan(store, monkeypatch):
    measure = service._managed_storage_snapshot
    scans = []
    monkeypatch.setattr(
        service, "_managed_storage_snapshot", lambda selected: scans.append(1) or measure(selected)
    )
    with service.artifact_publication(store):
        key = service.save_artifact(store, {"one": True})
        assert service.save_artifact(store, {"one": True}) == key
    assert scans == [1]


def test_multi_file_reconciliation_rejects_external_receipt_growth(store, monkeypatch):
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "1")
    with pytest.raises(ValueError, match="storage is full"):
        with service.artifact_publication(store):
            first = service.save_artifact(store, {"first": True})
            second = service.save_artifact(store, {"second": True})
            # This models native raw sidecars, which do not take the artifact
            # SQL guard; detection must happen before caller metadata commits.
            (store.root / "new-receipt.json").write_bytes(b"x" * 1024**2)
        pytest.fail("A quota-overrun batch must not return success to the metadata publisher")
    assert service.read_artifact(store, first) == {"first": True}
    assert service.read_artifact(store, second) == {"second": True}
    assert (store.root / "new-receipt.json").exists()
    assert service._artifact_publication.get() is None


def test_multi_file_reconciliation_rejects_new_unrelated_symlink_metadata(store, monkeypatch):
    scandir = service.os.scandir

    class Entry:
        def __init__(self, entry):
            self.entry = entry

        def __getattr__(self, key):
            return getattr(self.entry, key)

        def is_symlink(self):
            return self.entry.name == "late-link" or self.entry.is_symlink()

    @contextmanager
    def entries(path):
        with scandir(path) as original:
            yield (Entry(entry) for entry in original)

    # Inject the OS symlink bit rather than require Windows administrator
    # privileges. Separate tests below exercise actual links where permitted.
    monkeypatch.setattr(service.os, "scandir", entries)
    with pytest.raises(ValueError, match="symlinks"):
        with service.artifact_publication(store):
            service.save_artifact(store, {"first": True})
            service.save_artifact(store, {"second": True})
            (store.root / "late-link").touch()
    assert len(list((store.root / "artifacts").glob("*.json.gz"))) == 2
    assert service._artifact_publication.get() is None


def test_existing_large_manifest_validates_path_before_early_reuse(store, monkeypatch):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    value = {"rows": list(range(300))}
    key = service.save_artifact(store, value)
    target = store.root / "artifacts" / f"{key}.json.gz"
    original_bytes, is_symlink = target.read_bytes(), Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or is_symlink(path))
    with pytest.raises(ValueError, match="Unsafe research artifact path"):
        service.save_artifact(store, value)
    assert target.read_bytes() == original_bytes


def test_failed_scope_leaves_counted_orphans_and_releases_guard(store, monkeypatch):
    first, second = {"case": "first"}, {"case": "second"}
    quota = 1024**2
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "1")
    # Controlled initial remaining space: either artifact fits, both do not.
    monkeypatch.setattr(
        service,
        "_managed_storage_snapshot",
        lambda _: (quota - compressed_bytes(first) - compressed_bytes(second) + 1, 5),
    )
    with pytest.raises(ValueError, match="storage is full"):
        with service.artifact_publication(store):
            key = service.save_artifact(store, first)
            service.save_artifact(store, second)
    assert service.read_artifact(store, key) == first
    assert not (store.root / "artifacts" / f"{digest(second)}.json.gz").exists()
    assert service._artifact_publication.get() is None
    # The failed transaction did not delete its immutable orphan. A fresh scope
    # scans actual disk state and can reuse or later prune it normally.
    monkeypatch.undo()
    measured = service.managed_storage_bytes(store)
    assert measured >= (store.root / "artifacts" / f"{key}.json.gz").stat().st_size
    assert service.read_artifact(store, service.save_artifact(store, second)) == second


def test_failed_temporary_file_remains_reserved_if_caller_catches_error(store, monkeypatch):
    value = {"case": "retained temporary"}
    size, quota = compressed_bytes(value), 1024**2
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "1")
    monkeypatch.setattr(service, "_managed_storage_snapshot", lambda _: (quota - 2 * size + 1, 5))
    original_replace, original_unlink = service.os.replace, service.os.unlink

    def fail(*_):
        raise OSError("controlled publication/cleanup failure")

    with service.artifact_publication(store) as publication:
        monkeypatch.setattr(service.os, "replace", fail)
        monkeypatch.setattr(service.os, "unlink", fail)
        with pytest.raises(OSError, match="controlled"):
            service.save_artifact(store, value)
        assert publication.used == quota - size + 1
        monkeypatch.setattr(service.os, "replace", original_replace)
        monkeypatch.setattr(service.os, "unlink", original_unlink)
        with pytest.raises(ValueError, match="storage is full"):
            service.save_artifact(store, value)
    temporary = list((store.root / "artifacts").glob("*.tmp"))
    assert len(temporary) == 1 and temporary[0].stat().st_size == size
    monkeypatch.undo()
    assert service.managed_storage_bytes(store) >= size
    # A new admission scan includes the leftover file; success publishes exact
    # evidence separately and never mistakes the temporary file for evidence.
    assert service.read_artifact(store, service.save_artifact(store, value)) == value


@pytest.mark.parametrize("same_value", [False, True])
def test_concurrent_publishers_serialize_quota_and_duplicate_admission(
    store, monkeypatch, same_value
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 2 * 1024**2)
    # Include SQLite's live write journal in the baseline, just as admission
    # does. Schema growth must not turn this into two individually oversized
    # artifacts before the concurrent quota/duplicate checks can run.
    with store.sessions.begin() as db:
        service.write_guard(db)
        baseline = service.managed_storage_bytes(store)
    quota_mb = (baseline + 1024**2 - 1) // 1024**2 + 1
    quota = quota_mb * 1024**2
    monkeypatch.setenv("RESEARCH_QUOTA_MB", str(quota_mb))
    payload_bytes = (quota - baseline) * 2 // 3
    rng = random.Random(1904)
    first = base64.b64encode(rng.randbytes(payload_bytes)).decode("ascii")
    second = first if same_value else base64.b64encode(rng.randbytes(payload_bytes)).decode("ascii")
    sizes = compressed_bytes(first), compressed_bytes(second)
    assert baseline + max(sizes) <= quota < baseline + sum(sizes)
    # Both callers pass the initial absent-file check before either acquires the
    # SQL guard. This exercises the second existence check after lock admission.
    barrier, compress = Barrier(2), service.gzip.compress

    def together(*args, **kwargs):
        result = compress(*args, **kwargs)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(service.gzip, "compress", together)
    other = ResearchStore(store.root)

    def publish(selected, value):
        try:
            return "ok", service.save_artifact(selected, value)
        except ValueError as error:
            return "error", str(error)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(publish, selected, value)
                for selected, value in ((store, first), (other, second))
            ]
            results = [future.result(timeout=20) for future in futures]
    finally:
        other.close()
    if same_value:
        assert results == [("ok", digest(first)), ("ok", digest(first))]
    else:
        assert sorted(status for status, _ in results) == ["error", "ok"]
        assert "storage is full" in next(value for status, value in results if status == "error")
    files = list((store.root / "artifacts").iterdir())
    assert len(files) == 1
    assert service.managed_storage_bytes(store) <= quota
    for status, key in results:
        if status == "ok":
            assert service.read_artifact(store, key) in (first, second)


def test_maintenance_refuses_new_publication_and_failure_resets_context(store):
    with maintenance(store):
        with pytest.raises(ValueError, match="maintenance"):
            with service.artifact_publication(store):
                service.save_artifact(store, {"blocked": True})
        assert service._artifact_publication.get() is None
        with store.sessions() as db:
            assert db.get(ResearchWorker, 1).token.startswith("maintenance:")
    assert service.read_artifact(store, service.save_artifact(store, {"released": True})) == {
        "released": True
    }


def test_store_thread_and_expired_scope_ownership(store, tmp_path):
    other = ResearchStore(tmp_path / "other")
    other.initialize()
    try:
        with service.artifact_publication(store) as publication:
            with service.artifact_publication(store) as same:
                assert same is publication
            with pytest.raises(ValueError, match="different stores"):
                service.save_artifact(other, {"forbidden": "different store"})
            copied = copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(copied.run, service.save_artifact, store, {"thread": 2})
                with pytest.raises(ValueError, match="owning context"):
                    future.result(timeout=10)
            key = service.save_artifact(store, {"valid": True})
        with pytest.raises(ValueError, match="owning context"):
            publication.publish(digest({}), service.encoded({}))
        assert service.read_artifact(store, key) == {"valid": True}
        assert not (other.root / "artifacts").exists()
    finally:
        other.close()


def test_scope_accounts_for_every_new_entry(store, monkeypatch):
    monkeypatch.setattr(
        service, "_managed_storage_snapshot", lambda _: (0, service.MAX_MANAGED_ENTRIES - 1)
    )
    with service.artifact_publication(store):
        key = service.save_artifact(store, {"last allowed": True})
        assert service.save_artifact(store, {"last allowed": True}) == key
        with pytest.raises(ValueError, match="entry management bound"):
            service.save_artifact(store, {"too many": True})
    assert len(list((store.root / "artifacts").iterdir())) == 1


def test_scope_rechecks_symlinks_after_initial_admission(store, tmp_path):
    destination = tmp_path / "outside.json.gz"
    destination.write_bytes(b"preserve outside")
    target = store.root / "artifacts" / f"{digest({'second': True})}.json.gz"
    with service.artifact_publication(store):
        service.save_artifact(store, {"first": True})
        try:
            target.symlink_to(destination)
        except OSError as error:
            pytest.skip(f"This Windows host cannot create test symlinks: {error}")
        with pytest.raises(ValueError, match="Unsafe research artifact path"):
            service.save_artifact(store, {"second": True})
    assert destination.read_bytes() == b"preserve outside"


def test_initial_scan_rejects_unrelated_symlink(store, tmp_path):
    link = store.root / "unrelated"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"This Windows host cannot create test symlinks: {error}")
    with pytest.raises(ValueError, match="symlinks"):
        service.save_artifact(store, {"never": "published"})
    assert not list((store.root / "artifacts").glob("*.json.gz"))


def test_failed_admission_cannot_reenter_held_guard(store, monkeypatch):
    def failed(_):
        raise OSError("controlled scan failure")

    monkeypatch.setattr(service, "_managed_storage_snapshot", failed)
    with service.artifact_publication(store):
        with pytest.raises(OSError, match="scan failure"):
            service.save_artifact(store, {"first": True})
        with pytest.raises(ValueError, match="leave its scope"):
            service.save_artifact(store, {"second": True})
    assert service._artifact_publication.get() is None
    monkeypatch.undo()
    assert service.save_artifact(store, {"after": "failure"})


def test_repeated_publications_release_sessions_and_files(store):
    psutil = pytest.importorskip("psutil")
    process = psutil.Process()
    handles = getattr(process, "num_handles", None) or process.num_fds
    service.save_artifact(store, {"warmup": True})
    before = handles()
    for number in range(100):
        with service.artifact_publication(store):
            value = {"number": number}
            key = service.save_artifact(store, value)
            assert service.read_artifact(store, key) == value
        assert service._artifact_publication.get() is None
    assert handles() <= before + 2
    assert not list((store.root / "artifacts").glob("*.tmp"))


def test_stream_creation_failure_closes_raw_descriptor_and_preserves_error(store, monkeypatch):
    opened = []
    original = OSError("controlled stream creation failure")

    def fail(fd, *_):
        opened.append(fd)
        raise original

    monkeypatch.setattr(service.os, "fdopen", fail)
    with pytest.raises(OSError) as caught:
        service.save_artifact(store, {"stream": "cannot open"})
    assert caught.value is original and len(opened) == 1
    with pytest.raises(OSError):
        service.os.fstat(opened[0])
    assert not list((store.root / "artifacts").glob("*.tmp"))
    assert service._artifact_publication.get() is None
