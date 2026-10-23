"""Optional fresh Windows quota inventory using documented batched metadata.

No file contents, inventory cache or background watcher are involved. Unsupported
platforms/filesystems/reparse semantics return to the portable strict walk;
permission errors and malformed records remain fatal. Each directory handle is
synchronously owned and closed before another directory is opened.
"""

import ctypes
import os
import struct
import sys

BUFFER_BYTES = 64 * 1024
HEADER = struct.Struct("<IIqqqqqqIIII16s")
DIRECTORY, REPARSE, SYMLINK = 0x10, 0x400, 0xA000000C
_api = None


class _UnsupportedInventory(Exception):
    pass


def _cloud(tag):
    return tag & 0xFFFF0FFF == 0x9000001A


class _DirectoryAPI:
    """Function pointers only; no native handle survives a call to snapshot."""

    def __init__(self):
        from ctypes import wintypes

        library = ctypes.WinDLL("kernel32", use_last_error=True)
        self.create = library.CreateFileW
        self.create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.create.restype = wintypes.HANDLE
        self.query = library.GetFileInformationByHandleEx
        self.query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.query.restype = wintypes.BOOL
        self.close = library.CloseHandle
        self.close.argtypes = [wintypes.HANDLE]
        self.close.restype = wintypes.BOOL

    def open_directory(self, path):
        # FILE_LIST_DIRECTORY, all sharing, OPEN_EXISTING, BACKUP_SEMANTICS |
        # OPEN_REPARSE_POINT. The last flag prevents following a swapped link.
        handle = self.create(path, 1, 7, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def check_directory(self, handle):
        tag = ctypes.create_string_buffer(8)
        if not self.query(handle, 9, tag, len(tag)):  # FileAttributeTagInfo
            self.error()
        attributes, reparse_tag = struct.unpack("<II", tag.raw)
        if attributes & REPARSE:
            if reparse_tag == SYMLINK:
                raise ValueError("Research storage must not contain symlinks")
            if not _cloud(reparse_tag):
                raise _UnsupportedInventory
        if not attributes & DIRECTORY:
            raise NotADirectoryError("Research inventory needs a directory")

    @staticmethod
    def error():
        error = ctypes.get_last_error()
        if error in (1, 50, 87):  # Unsupported function/info class/filesystem
            raise _UnsupportedInventory
        raise ctypes.WinError(error)

    def read(self, handle, buffer):
        ctypes.memset(buffer, 0, len(buffer))
        # FileIdExtdDirectoryInfo: NextEntryOffset, logical EOF, attributes,
        # filename length and reparse tag are returned in one bounded buffer.
        if not self.query(handle, 0x13, buffer, len(buffer)):
            if ctypes.get_last_error() == 18:  # ERROR_NO_MORE_FILES only
                return None
            self.error()
        return buffer.raw

    def close_directory(self, handle):
        if not self.close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _name(raw):
    name = raw.decode("utf-16-le", "surrogatepass")
    if any(character in name for character in ("\\", "/", "\0", ":")):
        raise ValueError("Unsafe research inventory name")
    return name


def _uniform_batch(data, directory, np):
    """Prove a complete uniform offset chain; otherwise use the scalar parser."""
    if len(data) < HEADER.size:
        raise ValueError("Malformed research inventory record")
    stride = struct.unpack_from("<I", data)[0]
    if not stride or stride < HEADER.size or stride % 8 or stride + HEADER.size > len(data):
        return None
    metadata = np.dtype({
        "names": ["next", "size", "attributes", "name_bytes", "tag"],
        "formats": ["<u4", "<i8", "<u4", "<u4", "<u4"],
        "offsets": [0, 40, 56, 60, 68], "itemsize": HEADER.size,
    })
    capacity = 1 + (len(data) - HEADER.size) // stride
    records = np.ndarray((capacity,), dtype=metadata, buffer=data, strides=(stride,))
    stops = np.flatnonzero(records["next"] == 0)
    if not len(stops):
        return None
    length = int(stops[0]) + 1
    rows = records[:length]
    if np.any(rows["next"][:-1] != stride):
        return None
    names = rows["name_bytes"]
    last_available = len(data) - (length - 1) * stride - HEADER.size
    if (np.any(names == 0) or np.any(names % 2)
            or np.any(names[:-1] > stride - HEADER.size)
            or int(names[-1]) > last_available or np.any(rows["size"] < 0)):
        raise ValueError("Malformed research inventory bounds")
    attributes = rows["attributes"]
    reparses = attributes & REPARSE != 0
    clouds = ((attributes & DIRECTORY) == 0) & ((rows["tag"] & 0xFFFF0FFF) == 0x9000001A)
    if np.any(reparses & ~clouds):
        return None
    included = np.ones(length, dtype=bool)
    for index in np.flatnonzero(names <= 4):
        start = int(index) * stride + HEADER.size
        if data[start:start + int(names[index])] in (b".\0", b".\0.\0"):
            included[index] = False
    directories = attributes & DIRECTORY != 0
    pending = []
    for index in np.flatnonzero(directories & included):
        start = int(index) * stride + HEADER.size
        pending.append(os.path.join(directory, _name(data[start:start + int(names[index])])))
    sizes = rows["size"][included & ~directories]
    if len(sizes) and int(sizes.max()) > ((1 << 64) - 1) // len(sizes):
        used = sum(int(size) for size in sizes)
    else:
        used = int(sizes.sum(dtype=np.uint64))
    return used, int(np.count_nonzero(included)), pending


def _scalar_batch(data, directory):
    offset, used, count, pending = 0, 0, 0, []
    while True:
        if offset + HEADER.size > len(data):
            raise ValueError("Malformed research inventory record")
        record = HEADER.unpack_from(data, offset)
        following, size, attributes, name_bytes, tag = (
            record[0], record[6], record[8], record[9], record[11]
        )
        end = offset + HEADER.size + name_bytes
        if (name_bytes == 0 or name_bytes % 2 or end > len(data) or size < 0
                or (following and (following % 8 or following < HEADER.size + name_bytes
                                  or offset + following + HEADER.size > len(data)))):
            raise ValueError("Malformed research inventory bounds")
        raw_name = data[offset + HEADER.size:end]
        if raw_name not in (b".\0", b".\0.\0"):
            count += 1
            if attributes & REPARSE:
                if tag == SYMLINK:
                    raise ValueError("Research storage must not contain symlinks")
                if attributes & DIRECTORY or not _cloud(tag):
                    raise _UnsupportedInventory
            if attributes & DIRECTORY:
                pending.append(os.path.join(directory, _name(raw_name)))
            else:
                used += size
        if not following:
            return used, count, pending
        offset += following


def _snapshot(root, maximum_entries, api, np):
    pending, used, count = [os.fspath(root)], 0, 0
    buffer = ctypes.create_string_buffer(BUFFER_BYTES)
    while pending:
        directory = pending.pop()
        handle = api.open_directory(directory)
        try:
            api.check_directory(handle)
            while True:
                data = api.read(handle, buffer)
                if data is None:
                    break
                batch = _uniform_batch(data, directory, np)
                if batch is None:
                    batch = _scalar_batch(data, directory)
                addition, entries, directories = batch
                count += entries
                if count > maximum_entries:
                    raise ValueError("Research storage exceeds the 100000-entry management bound")
                used += addition
                pending.extend(directories)
        finally:
            api.close_directory(handle)
    return used, count


def managed_storage_snapshot(root, maximum_entries):
    """Return a full exact inventory, or None for the portable implementation."""
    if sys.platform != "win32":
        return None
    try:
        import numpy as np

        global _api
        if _api is None:
            _api = _DirectoryAPI()
    except (ImportError, AttributeError):
        return None
    try:
        return _snapshot(root, maximum_entries, _api, np)
    except _UnsupportedInventory:
        return None
