"""Exact quota inventory and no-follow rejection with promptly closed scans."""
from types import SimpleNamespace

import pytest

from services import scanner_research_service as service


def test_regular_files_and_subdirectories_keep_exact_inventory(tmp_path):
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "one.json.gz").write_bytes(b"1234567")
    (tmp_path / "artifacts" / "two.json").write_bytes(b"abc")
    (tmp_path / "research.db").write_bytes(b"metadata")
    assert service._managed_storage_snapshot(SimpleNamespace(root=tmp_path)) == (18, 4)


def test_symlinks_never_enter_regular_file_branch_and_scan_closes(monkeypatch):
    class Scan:
        closed = False

        def __enter__(self):
            return iter([SimpleNamespace(is_file=lambda **kw: False, is_symlink=lambda: True)])

        def __exit__(self, *args):
            self.closed = True

    scan = Scan()
    monkeypatch.setattr(service, "os", SimpleNamespace(scandir=lambda _: scan))
    with pytest.raises(ValueError, match="must not contain symlinks"):
        service._managed_storage_snapshot(SimpleNamespace(root="unused"))
    assert scan.closed


def test_entry_bound_counts_every_entry_and_closes_scan(monkeypatch):
    class Scan:
        closed = False

        def __enter__(self):
            file = SimpleNamespace(
                is_file=lambda **kw: True,
                stat=lambda **kw: SimpleNamespace(st_size=4),
            )
            return iter([file, file, file])

        def __exit__(self, *args):
            self.closed = True

    scan = Scan()
    monkeypatch.setattr(service, "MAX_MANAGED_ENTRIES", 2)
    monkeypatch.setattr(service, "os", SimpleNamespace(scandir=lambda _: scan))
    with pytest.raises(ValueError, match="management bound"):
        service._managed_storage_snapshot(SimpleNamespace(root="unused"))
    assert scan.closed
