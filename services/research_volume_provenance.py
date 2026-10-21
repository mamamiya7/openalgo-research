"""Bounded research-owned cache for exact native candle volume provenance.

Historify remains untouched. This mutable acceleration index is not the saved
research evidence: callers freeze the returned, sealed records in their normal
immutable artifacts. Replaced Historify candles fail the exact-match lookup.
"""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timezone
from pathlib import Path

from research import volume_provenance as contract

MAX_CACHE_BYTES = 64 * 1024**2
MAX_CACHE_FILES = 2048


class NativeVolumeProvenance:
    """Short, context-managed file operations; no connections or retained cache.

    The single native research calculation worker serialises writes. Atomic
    replacement protects readers/restarts; a competing external writer can cause
    an unavailable cache observation, never an unbound volume admission.
    """

    __slots__ = ("root", "archive_id")

    def __init__(self, root, archive_id):
        self.root = Path(root)
        if not self.root.is_absolute() or not isinstance(archive_id, str) or len(archive_id) != 64:
            raise ValueError("Volume cache needs an absolute research path and archive identity")
        if any(char not in "0123456789abcdef" for char in archive_id):
            raise ValueError("Volume cache archive identity must be SHA256")
        self.archive_id = archive_id

    def _path(self, symbol, interval, exchange):
        if not isinstance(symbol, str) or len(symbol) > 100:
            raise ValueError("Volume cache requires a bounded symbol")
        key = contract.fingerprint([self.archive_id, exchange, interval, symbol])
        return self.root / (key + ".json")

    def _read(self, symbol, interval, exchange):
        path = self._path(symbol, interval, exchange)
        if not path.exists():
            return {}
        if path.is_symlink():
            raise ValueError("Volume cache cannot follow symbolic links")
        with path.open("rb") as handle:
            raw = handle.read(contract.MAX_BYTES + 1)
        if len(raw) > contract.MAX_BYTES:
            raise ValueError("Volume cache exceeds its bounded file size")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.get("version") != contract.VERSION
            or value.get("archive_id") != self.archive_id
            or value.get("symbol") != symbol
            or value.get("interval") != interval
            or value.get("exchange") != exchange
            or not isinstance(value.get("records"), dict)
            or len(value["records"]) > contract.MAX_ROWS
        ):
            raise ValueError("Volume cache identity or row bound differs")
        return value["records"]

    @staticmethod
    def _adapter_digest(broker):
        if broker != "fyers":
            return None
        path = Path(__file__).resolve().parents[1] / "broker" / "fyers" / "api" / "data.py"
        with path.open("rb") as handle:
            raw = handle.read(1024**2 + 1)
        if len(raw) > 1024**2:
            return None
        return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()

    def capture(
        self, symbol, original_rows, accepted_by_slot, *, broker, interval="D", exchange="NSE"
    ):
        if interval != "D" or exchange != "NSE":
            return {}
        records = contract.capture_rows(
            symbol,
            original_rows,
            accepted_by_slot,
            broker=broker,
            adapter_sha256=self._adapter_digest(broker),
            captured_at=datetime.now(UTC).isoformat(),
        )
        if not records:
            return {}
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            cached = self._read(symbol, interval, exchange)
        except (ValueError, OSError, TypeError):
            cached = {}  # Corrupt indices never qualify a candle; native source can repair them.
        merged = {**cached, **records}
        merged = {day: merged[day] for day in sorted(merged)[-contract.MAX_ROWS :]}
        value = {
            "version": contract.VERSION,
            "archive_id": self.archive_id,
            "symbol": symbol,
            "interval": interval,
            "exchange": exchange,
            "records": merged,
        }
        encoded = contract.encoded(value)
        while len(encoded) > contract.MAX_BYTES and len(merged) > len(records):
            # Retain the fresh source; evict oldest reusable observations first.
            victim = next((day for day in merged if day not in records), None)
            if victim is None:
                break
            del merged[victim]
            encoded = contract.encoded({**value, "records": merged})
        if len(encoded) > contract.MAX_BYTES:
            raise ValueError("Volume cache capture exceeds its file-size bound")
        if len(encoded) > MAX_CACHE_BYTES:
            raise ValueError("Native volume cache reached its bounded storage allocation")
        destination = self._path(symbol, interval, exchange)
        total, count = 0, 0
        for entry in self.root.iterdir():
            if entry.is_symlink():
                raise ValueError("Volume cache cannot contain symbolic links")
            # Include orphaned temporary files from an abrupt process exit in
            # the allocation. Normal success and exception paths remove them.
            if entry.is_file() and entry != destination:
                total += entry.stat().st_size
                count += 1
            if count >= MAX_CACHE_FILES or total + len(encoded) > MAX_CACHE_BYTES:
                raise ValueError("Native volume cache reached its bounded storage allocation")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return records

    def lookup(self, symbol, first, last, archive_rows, *, interval="D", exchange="NSE"):
        if interval != "D" or exchange != "NSE":
            return {}
        if not isinstance(archive_rows, list) or len(archive_rows) > contract.MAX_ROWS:
            raise ValueError("Volume cache lookup exceeds its row bound")
        try:
            records = self._read(symbol, interval, exchange)
        except (ValueError, OSError, TypeError):
            return {}
        result, duplicates, seen = {}, set(), set()
        for row in archive_rows:
            try:
                day = datetime.fromtimestamp(row["timestamp"], contract.IST).date().isoformat()
                if not first <= day <= last:
                    continue
                if day in seen:
                    duplicates.add(day)
                seen.add(day)
                record = records.get(day)
                if contract.matches(record, row, symbol):
                    result[day] = record
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
        return {day: row for day, row in result.items() if day not in duplicates}
