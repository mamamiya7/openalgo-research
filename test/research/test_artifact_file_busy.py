# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Windows file contention must not discard a completed evidence write."""

import errno

import pytest
from test_jobs import app  # noqa: F401

from services import scanner_research_service as service


def busy():
    error = PermissionError(errno.EACCES, "Controlled sharing violation")
    error.winerror = 32
    return error


def test_short_sharing_lock_retries_closed_file_and_preserves_exact_bytes(app, monkeypatch):
    store = app.extensions["research_store"]
    replace = service.os.replace
    attempts, delays = [], []

    def contend(source, target):
        attempts.append(source)
        # Reopening proves our writer released its stream before publication.
        with open(source, "rb") as handle:
            assert handle.read(2) == b"\x1f\x8b"
        if len(attempts) < 3:
            raise busy()
        return replace(source, target)

    monkeypatch.setattr(service.os, "replace", contend)
    monkeypatch.setattr(service.time, "sleep", delays.append)
    value = {"prices": [100.1, 101.2], "exact": True}
    digest = service.save_artifact(store, value)
    assert service.read_artifact(store, digest) == value
    assert len(set(attempts)) == 1 and delays == [0.05, 0.1]
    assert not list((store.root / "artifacts").glob("*.tmp"))


def test_persistent_lock_is_bounded_and_cleans_unpublished_file(app, monkeypatch):
    store = app.extensions["research_store"]
    original = busy()
    attempts = []

    def fail(*args):
        attempts.append(args)
        raise original

    monkeypatch.setattr(service.os, "replace", fail)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError) as caught:
        service.save_artifact(store, {"case": "persistent"})
    assert caught.value is original and len(attempts) == 6
    assert not list((store.root / "artifacts").glob("*.tmp"))


def test_cleanup_failure_preserves_original_publication_error(app, monkeypatch):
    store = app.extensions["research_store"]
    original = OSError(errno.ENOSPC, "Controlled publication failure")

    def fail_replace(*args):
        raise original

    def fail_cleanup(*args):
        raise busy()

    with monkeypatch.context() as patch:
        patch.setattr(service.os, "replace", fail_replace)
        patch.setattr(service.os, "unlink", fail_cleanup)
        patch.setattr(service.time, "sleep", lambda _: None)
        with pytest.raises(OSError) as caught:
            service.save_artifact(store, {"case": "cleanup-locked"})
        assert caught.value is original
    temporary = list((store.root / "artifacts").glob("*.tmp"))
    assert len(temporary) == 1
    temporary[0].unlink()


def test_other_io_errors_are_not_retried(app, monkeypatch):
    store = app.extensions["research_store"]
    original = OSError(errno.ENOSPC, "Controlled disk full")

    def fail(*args):
        raise original

    monkeypatch.setattr(service.os, "replace", fail)
    monkeypatch.setattr(service.time, "sleep", lambda _: pytest.fail("Do not retry disk full"))
    with pytest.raises(OSError) as caught:
        service.save_artifact(store, {"case": "disk-full"})
    assert caught.value is original
    assert not list((store.root / "artifacts").glob("*.tmp"))
