"""Fresh batch inventory parity, strict bounds/fallback and native-handle exits."""

import os
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from services import research_windows_inventory as inventory
from services import scanner_research_service as service


def sample(*, sizes=(10, 20, 30), attributes=(0, 0, 0), tags=(0, 0, 0), names=None):
    names = names or [str(index) * 64 + ".json.gz" for index in range(3)]
    data = bytearray(inventory.BUFFER_BYTES)
    for index, (size, attribute, tag, name) in enumerate(zip(sizes, attributes, tags, names, strict=True)):
        raw = name.encode("utf-16-le")
        fields = (232 if index < 2 else 0, 0, 0, 0, 0, 0, size, 0,
                  attribute, len(raw), 0, tag, b"\0" * 16)
        inventory.HEADER.pack_into(data, index * 232, *fields)
        data[index * 232 + 88:index * 232 + 88 + len(raw)] = raw
    return data


def test_uniform_bytes_count_cloud_tags_and_non_wrapping_large_file_sizes():
    for tag in (0, 0x9000001A, 0x9000101A, 0x9000F01A):
        data = sample(attributes=(inventory.REPARSE if tag else 0, 0, 0), tags=(tag, 0, 0))
        assert inventory._uniform_batch(data, "unused", np) == (60, 3, [])
        assert inventory._scalar_batch(data, "unused") == (60, 3, [])
    enormous = (1 << 63) - 1
    assert inventory._uniform_batch(sample(sizes=(enormous,) * 3), "unused", np) == (
        3 * enormous, 3, []
    )


def test_dots_are_excluded_directories_counted_and_queued_without_file_name_decoding():
    data = sample(names=[".", "subdirectory", "ordinary"],
                  attributes=(inventory.DIRECTORY, inventory.DIRECTORY, 0))
    expected = (30, 2, [os.path.join("unused", "subdirectory")])
    assert inventory._uniform_batch(data, "unused", np) == expected
    assert inventory._scalar_batch(data, "unused") == expected


@pytest.mark.parametrize("tag", [inventory.SYMLINK, 0xA0000003, 0x12345678])
def test_non_cloud_reparses_never_enter_vectorized_file_admission(tag):
    data = sample(attributes=(inventory.REPARSE, 0, 0), tags=(tag, 0, 0))
    assert inventory._uniform_batch(data, "unused", np) is None
    if tag == inventory.SYMLINK:
        with pytest.raises(ValueError, match="symlinks"):
            inventory._scalar_batch(data, "unused")
    else:
        with pytest.raises(inventory._UnsupportedInventory):
            inventory._scalar_batch(data, "unused")


def test_cloud_directory_uses_portable_reparse_semantics():
    data = sample(attributes=(inventory.REPARSE | inventory.DIRECTORY, 0, 0),
                  tags=(0x9000001A, 0, 0))
    assert inventory._uniform_batch(data, "unused", np) is None
    with pytest.raises(inventory._UnsupportedInventory):
        inventory._scalar_batch(data, "unused")


@pytest.mark.parametrize("offset,fmt,value", [(40, "<q", -1), (60, "<I", 1), (60, "<I", 1000)])
def test_uniform_and_scalar_bounds_reject_malformed_metadata(offset, fmt, value):
    data = sample()
    struct.pack_into(fmt, data, offset, value)
    for parse in (lambda: inventory._uniform_batch(data, "unused", np),
                  lambda: inventory._scalar_batch(data, "unused")):
        with pytest.raises(ValueError, match="bounds"):
            parse()


@pytest.mark.parametrize("following", [0, 1, 88, 240, 65536])
def test_irregular_next_offset_chain_is_not_assumed_uniform(following):
    data = sample()
    struct.pack_into("<I", data, 232, following)
    if following == 0:
        # A legitimate shorter returned batch terminates exactly here.
        assert inventory._uniform_batch(data, "unused", np) == (30, 2, [])
    else:
        assert inventory._uniform_batch(data, "unused", np) is None
        with pytest.raises(ValueError):
            inventory._scalar_batch(data, "unused")


class FakeAPI:
    def __init__(self, *, failure=None, data=None):
        self.failure = failure
        self.data = sample() if data is None else data
        self.opened, self.closed, self.reads = [], [], 0

    def open_directory(self, path):
        if self.failure == "open":
            raise PermissionError("controlled access denied")
        self.opened.append(17)
        return 17

    def check_directory(self, handle):
        if self.failure == "attributes":
            raise PermissionError("controlled access denied")

    def read(self, handle, buffer):
        if self.failure == "read":
            raise PermissionError("controlled access denied")
        self.reads += 1
        return self.data if self.reads == 1 else None

    def close_directory(self, handle):
        self.closed.append(handle)


@pytest.mark.parametrize("failure", ["open", "attributes", "read", "malformed", "count", None])
def test_every_native_directory_handle_closes_on_success_and_all_failure_paths(failure):
    data = sample()
    if failure == "malformed":
        struct.pack_into("<I", data, 60, 1)
    api = FakeAPI(failure=failure, data=data)
    if failure in ("open", "attributes", "read"):
        with pytest.raises(PermissionError, match="access denied"):
            inventory._snapshot("unused", 10, api, np)
    elif failure in ("malformed", "count"):
        with pytest.raises(ValueError):
            inventory._snapshot("unused", 2 if failure == "count" else 10, api, np)
    else:
        assert inventory._snapshot("unused", 10, api, np) == (60, 3)
    assert api.opened == api.closed


def test_optional_backend_never_hides_permission_failures(monkeypatch):
    monkeypatch.setattr(inventory.sys, "platform", "win32")
    api = FakeAPI(failure="read")
    monkeypatch.setattr(inventory, "_api", api)
    with pytest.raises(PermissionError, match="access denied"):
        inventory.managed_storage_snapshot("unused", 100)
    assert api.opened == api.closed == [17]


def test_unsupported_platform_and_reparse_fall_back_only_after_handle_cleanup(monkeypatch):
    monkeypatch.setattr(inventory.sys, "platform", "unsupported")
    assert inventory.managed_storage_snapshot("unused", 100) is None
    monkeypatch.setattr(inventory.sys, "platform", "win32")
    api = FakeAPI(data=sample(attributes=(inventory.REPARSE, 0, 0), tags=(0x12345678, 0, 0)))
    monkeypatch.setattr(inventory, "_api", api)
    assert inventory.managed_storage_snapshot("unused", 100) is None
    assert api.opened == api.closed == [17]


def test_service_fallback_preserves_custom_scandir_and_portable_result(tmp_path, monkeypatch):
    store = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(inventory, "managed_storage_snapshot", lambda *args: None)
    monkeypatch.setattr(service, "_portable_managed_storage_snapshot", lambda selected: (73, 11))
    assert service._managed_storage_snapshot(store) == (73, 11)
    monkeypatch.setattr(
        inventory, "managed_storage_snapshot", lambda *args: pytest.fail("custom provider")
    )
    monkeypatch.setattr(service, "os", SimpleNamespace(scandir=lambda *args: None))
    assert service._managed_storage_snapshot(store) == (73, 11)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API qualification")
def test_real_native_inventory_matches_portable_and_has_no_repeated_handle_growth(tmp_path):
    psutil = pytest.importorskip("psutil")
    directory = tmp_path / "nested"
    directory.mkdir()
    (directory / "empty").mkdir()
    (directory / "unicode-\u0905.json.gz").write_bytes(b"1234567")
    (tmp_path / "research.db").write_bytes(b"123")
    store = SimpleNamespace(root=tmp_path)
    expected = service._portable_managed_storage_snapshot(store)
    assert expected == (10, 4)
    assert inventory.managed_storage_snapshot(tmp_path, 100000) == expected
    process = psutil.Process()
    before = process.num_handles()
    for _ in range(30):
        assert inventory.managed_storage_snapshot(tmp_path, 100000) == expected
    assert process.num_handles() <= before + 1
