"""One run's detached, checked market arrays; never a mutable-input cache.

The context owns immutable snapshot evidence. Only that exact frozen snapshot
can reuse validation; an ordinary dictionary always follows normal admission.
Candidate configuration, schedules and holding coverage remain connector work.
"""

import copy
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

import numpy as np

from research.data import MAX_BARS, validate_snapshot
from research.owned_entry_features import _capture

MAX_MARKET_CELLS = MAX_BARS
MAX_NUMERIC_FEATURES = 25_000
MAX_NUMERIC_FEATURE_BYTES = 32 * 1024**2
MAX_CANONICAL_FEATURES = 16_384
MAX_CANONICAL_FEATURE_BYTES = 32 * 1024**2
MAX_CANONICAL_LISTS = 32
MAX_CANONICAL_LIST_BYTES = 64 * 1024**2
_CURRENT = ContextVar("prepared_calculation", default=None)
_MISSING_WARNING = "Missing candles can leave accepted positions pending; later candles cannot resolve unseen exits."


def _thaw(value):
    if isinstance(value, (dict, MappingProxyType)):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


class PreparedCalculation:
    """Bounded worker-local market data with no writable array backing store."""

    def __init__(self, snapshot):
        # Detach first: later caller mutation cannot change admitted prices.
        detached = copy.deepcopy(snapshot)
        self._base_coverage = validate_snapshot(detached)
        minute = detached["provenance"]["interval"] == "1m"
        timeline = detached["timeline" if minute else "sessions"]
        symbols = sorted(detached["bars"])
        if len(timeline) * len(symbols) > MAX_MARKET_CELLS:
            raise ValueError("Prepared market matrix exceeds its bounded cell limit")
        values = np.full((len(timeline), len(symbols), 4), np.nan, dtype=np.float64)
        self.time_index = MappingProxyType({key: i for i, key in enumerate(timeline)})
        self.symbol_index = MappingProxyType({symbol: i for i, symbol in enumerate(symbols)})
        for symbol, series in detached["bars"].items():
            column = self.symbol_index[symbol]
            for key, bar in series.items():
                values[self.time_index[key], column] = [
                    float(bar[field]) for field in ("open", "high", "low", "close")
                ]
        # bytes backing prevents setflags(write=True), unlike a read-only owned ndarray.
        self.ohlc = np.frombuffer(values.tobytes(), dtype=np.float64).reshape(values.shape)
        self.snapshot = _capture(detached)
        self.timeline = tuple(timeline)
        self.symbols = tuple(symbols)
        self._minute = minute
        self._coverage_rows = {
            symbol: self._coverage_row(symbol)
            for symbol in set(symbols)
            | set(detached.get("required_dates", {}))
            | set(detached.get("required_timestamps", {}))
        }
        self._active = True
        self._evaluation_identities = OrderedDict()
        self._numeric_features = OrderedDict()
        self._numeric_feature_bytes = 0
        self._canonical_snapshot_bytes = None
        self._canonical_features = OrderedDict()
        self._canonical_feature_bytes = 0
        self._canonical_lists = OrderedDict()
        self._canonical_list_bytes = 0

    def lookup_canonical_signal_list(self, value):
        """Borrow existing exact immutable list bytes without admitting a miss."""
        from research.owned_entry_features import owned_json_bounds

        self._check_active()
        if not isinstance(value, list) or owned_json_bounds(value) is None:
            return None
        retained = self._canonical_lists.get(id(value))
        if retained is None or retained[0] is not value:
            return None
        self._canonical_lists.move_to_end(id(value))
        return retained[1]

    def memoize_canonical_signal_list(self, value, encode):
        """Keep exact bytes of bounded immutable lists only in this market owner."""
        from research.owned_entry_features import owned_json_bounds

        self._check_active()
        if not isinstance(value, list) or owned_json_bounds(value) is None:
            return encode()
        key = id(value)
        retained = self._canonical_lists.get(key)
        if retained is not None:
            self._canonical_lists.move_to_end(key)
            return retained[1]
        raw = encode()
        if raw is None or len(raw) > MAX_CANONICAL_LIST_BYTES or MAX_CANONICAL_LISTS <= 0:
            return raw
        while self._canonical_lists and (
            len(self._canonical_lists) >= MAX_CANONICAL_LISTS
            or self._canonical_list_bytes + len(raw) > MAX_CANONICAL_LIST_BYTES
        ):
            _, (_, removed) = self._canonical_lists.popitem(last=False)
            self._canonical_list_bytes -= len(removed)
        self._canonical_lists[key] = (value, raw)
        self._canonical_list_bytes += len(raw)
        return raw

    def memoize_canonical_feature(self, value, encode):
        """Exact immutable feature bytes, never approval by mutable identity."""
        from research.owned_entry_features import is_owned_entry

        self._check_active()
        if not isinstance(value, dict) or not is_owned_entry(value, value.get("version")):
            return encode()
        return self.memoize_canonical_json(value, encode)

    def memoize_canonical_json(self, value, encode):
        """Exact immutable subtree bytes sharing the existing bounded budget.

        This is an encoding cache, never financial validation admission. Strong
        references prevent object-id reuse, and close/eviction clears the owner.
        """
        from research.owned_entry_features import owned_json_bounds

        self._check_active()
        if owned_json_bounds(value) is None:
            return encode()
        key = id(value)
        retained = self._canonical_features.get(key)
        if retained is not None:
            self._canonical_features.move_to_end(key)
            return retained[1]
        raw = encode()
        if (
            raw is not None
            and len(raw) <= MAX_CANONICAL_FEATURE_BYTES
            and MAX_CANONICAL_FEATURES > 0
        ):
            while self._canonical_features and (
                len(self._canonical_features) >= MAX_CANONICAL_FEATURES
                or self._canonical_feature_bytes + len(raw) > MAX_CANONICAL_FEATURE_BYTES
            ):
                _, (_, removed) = self._canonical_features.popitem(last=False)
                self._canonical_feature_bytes -= len(removed)
            self._canonical_features[key] = (value, raw)
            self._canonical_feature_bytes += len(raw)
        return raw

    def canonical_snapshot_bytes(self):
        """Retain bounded exact canonical bytes of this immutable market only."""
        from research.canonical_json import bounded_canonical_fragments

        self._check_active()
        if self._canonical_snapshot_bytes is None:
            fragments = bounded_canonical_fragments(self.snapshot, max_bytes=64 * 1024**2)
            if fragments is None:
                return None
            self._canonical_snapshot_bytes = b"".join(fragments)
        return self._canonical_snapshot_bytes

    def memoize_entry_feature(self, feature, kind, factory):
        """Cache numeric packing only for exact admitted immutable entry evidence."""
        from research.owned_entry_features import is_owned_entry

        self._check_active()
        if not isinstance(feature, dict) or not is_owned_entry(feature, feature.get("version")):
            return factory()
        key = (id(feature), kind)
        retained = self._numeric_features.get(key)
        if retained is not None:
            self._numeric_features.move_to_end(key)
            return retained[1]
        value = factory()

        def freeze_numeric(item):
            if isinstance(item, np.ndarray):
                if item.dtype.hasobject:
                    raise ValueError("Numeric feature packing cannot retain object arrays")
                result = np.frombuffer(item.tobytes(), dtype=item.dtype).reshape(item.shape)
                return result, result.nbytes
            if isinstance(item, tuple):
                frozen = [freeze_numeric(part) for part in item]
                return tuple(part[0] for part in frozen), sum(part[1] for part in frozen)
            if item is None or type(item) in (str, int, float, bool):
                return item, len(item) * 4 if isinstance(item, str) else 32
            raise ValueError("Numeric feature packing needs immutable numeric rows and metadata")

        value, size = freeze_numeric(value)
        if size > MAX_NUMERIC_FEATURE_BYTES:
            return value
        while self._numeric_features and (
            len(self._numeric_features) >= MAX_NUMERIC_FEATURES
            or self._numeric_feature_bytes + size > MAX_NUMERIC_FEATURE_BYTES
        ):
            _, (_, _, previous_size) = self._numeric_features.popitem(last=False)
            self._numeric_feature_bytes -= previous_size
        # The retained feature prevents object-id reuse. No caller dictionary or
        # independently mutable array is used as an admission/cache capability.
        self._numeric_features[key] = (feature, value, size)
        self._numeric_feature_bytes += size
        return value

    def evaluation_identities(self, symbols):
        """Reuse only canonical identities of this exact immutable market view."""
        from research.evaluation_basis import _snapshot_identities

        self._check_active()
        key = tuple(sorted(set(symbols)))
        if key not in self._evaluation_identities:
            # Existing canonical encoding admits ordinary JSON only. This local
            # detached projection is discarded after computing identical hashes.
            identities = _snapshot_identities(_thaw(self.snapshot), key)
            if len(self._evaluation_identities) >= 2:
                self._evaluation_identities.popitem(last=False)
            self._evaluation_identities[key] = identities
        self._evaluation_identities.move_to_end(key)
        return dict(self._evaluation_identities[key])

    def _coverage_row(self, symbol):
        snapshot = self.snapshot
        sessions = snapshot["sessions"]
        available = snapshot["bars"].get(symbol, {})
        required = (
            snapshot.get("required_timestamps", {}).get(symbol, self.timeline)
            if self._minute
            else snapshot["required_dates"].get(symbol, ())
            if snapshot.get("required_dates") is not None
            else sessions
        )
        invalid = self._minute and (
            not isinstance(required, (list, tuple)) or bool(set(required) - self.time_index.keys())
        )
        return {
            "invalid": invalid,
            "missing": tuple(day for day in required if day not in available)
            if not invalid
            else (),
            "first": min(available, default=sessions[-1]),
            "last": max(available, default=sessions[0]),
            "count": len(available),
            "quality": tuple(
                row
                for row in snapshot["provenance"].get("quality_findings", ())
                if row.get("symbol") == symbol
            ),
        }

    def coverage(self, signals):
        """Signal-dependent findings, retaining validate_snapshot's public contract."""
        self._check_active()
        snapshot = self.snapshot
        starts = {}
        for signal in signals:
            symbol, day = signal["symbol"], signal["date"]
            starts[symbol] = min(starts.get(symbol, day), day)
        findings = []
        for symbol, start in sorted(starts.items()):
            row = self._coverage_rows.get(symbol)
            if row is None:
                row = self._coverage_row(symbol)
            # Minute required-slot validation is signal-dependent in the legacy boundary.
            if row["invalid"]:
                raise ValueError("Required minute timestamps must belong to the recorded timeline")
            missing = [day for day in row["missing"] if day[:10] >= start]
            first, last = row["first"], row["last"]
            findings.append(
                {
                    "symbol": symbol,
                    "missing_sessions": missing,
                    "available_bars": row["count"],
                    "required_from": start,
                    "head_missing": [day for day in missing if not row["count"] or day < first],
                    "tail_missing": [day for day in missing if row["count"] and day > last],
                    "internal_missing": [
                        day for day in missing if row["count"] and first <= day <= last
                    ],
                    "quality_findings": [_thaw(item) for item in row["quality"]],
                }
            )
        result = copy.deepcopy(self._base_coverage)
        result["symbols"] = findings
        if any(row["missing_sessions"] for row in findings):
            result["warnings"].insert(
                int(bool(snapshot["provenance"].get("synthetic"))), _MISSING_WARNING
            )
        result["status"] = "warning" if result["warnings"] else "ready"
        return result

    def close_for(self, lots):
        self._check_active()
        columns = [self.symbol_index.get(signal["symbol"], -1) for _, signal in lots]
        result = np.full((len(self.timeline), len(columns)), np.nan)
        for column, symbol_column in enumerate(columns):
            if symbol_column >= 0:
                result[:, column] = self.ohlc[:, symbol_column, 3]
        return result

    def _check_active(self):
        if not self._active:
            raise RuntimeError("Prepared calculation owner is closed")

    @contextmanager
    def activate(self):
        """Borrow this admitted period owner without changing its lifetime."""
        self._check_active()
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    def close(self):
        self._active = False
        self.snapshot = None
        self.ohlc = None
        self.timeline = ()
        self.symbols = ()
        self.time_index = MappingProxyType({})
        self.symbol_index = MappingProxyType({})
        self._base_coverage = None
        self._coverage_rows = None
        self._evaluation_identities.clear()
        self._numeric_features.clear()
        self._numeric_feature_bytes = 0
        self._canonical_snapshot_bytes = None
        self._canonical_features.clear()
        self._canonical_feature_bytes = 0
        self._canonical_lists.clear()
        self._canonical_list_bytes = 0


def prepared_market(snapshot):
    """Return the active owner only for its exact detached immutable snapshot."""
    owner = _CURRENT.get()
    return owner if owner is not None and owner._active and snapshot is owner.snapshot else None


def active_prepared_market():
    owner = _CURRENT.get()
    return owner if owner is not None and owner._active else None


class PreparedCalculationWindows:
    """Own at most two explicitly admitted private period snapshots."""

    def __init__(self):
        self._owners = OrderedDict()

    def admit(self, period, snapshot):
        if period in self._owners:
            raise ValueError("A prepared period must be evicted before new admission")
        if len(self._owners) >= 2:
            _, previous = self._owners.popitem(last=False)
            if previous is not None:
                previous.close()
        # Sparse snapshots can satisfy native bar/lot bounds while their dense
        # unique-symbol projection exceeds this owner's separate memory bound.
        # Decline reuse only for that shape; native admission still checks the
        # original evidence on every account. Never swallow validation errors.
        interval = snapshot.get("provenance", {}).get("interval")
        timeline = snapshot.get("timeline" if interval == "1m" else "sessions", ())
        dense_cells = len(timeline) * len(snapshot.get("bars", {}))
        owner = PreparedCalculation(snapshot) if dense_cells <= MAX_MARKET_CELLS else None
        self._owners[period] = owner
        return owner

    def get(self, period):
        owner = self._owners[period]
        self._owners.move_to_end(period)
        return owner

    def evict(self, period):
        owner = self._owners.pop(period, None)
        if owner is not None:
            owner.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        while self._owners:
            _, owner = self._owners.popitem()
            if owner is not None:
                owner.close()


@contextmanager
def prepared_calculation(snapshot):
    owner = PreparedCalculation(snapshot)
    try:
        with owner.activate():
            yield owner
    finally:
        owner.close()
