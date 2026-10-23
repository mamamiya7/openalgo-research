"""Omit discarded component accounts only in the private expanded native gate."""

import sys
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from threading import get_ident

_mode = ContextVar("native_causal_gate_components", default=None)
MAX_COMPONENTS = 128
MAX_COMPONENT_BYTES = 64 * 1024**2
MAX_SNAPSHOT_BYTES = 128 * 1024**2


def components_only():
    owner = _mode.get()
    return (
        owner is not None
        and owner["active"]
        and not owner["closed"]
        and owner["thread"] == get_ident()
    )


@contextmanager
def causal_components_only():
    from research.gate_input_binding import _BINDING

    owner = {"active": _BINDING.get() is not None, "closed": False, "thread": get_ident()}
    token = _mode.set(owner)
    try:
        yield
    finally:
        owner["closed"] = True
        _mode.reset(token)


def _heap_bytes(value):
    from research.owned_entry_features import owned_json_bounds

    pending, seen, total = [value], set(), 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        bounds = owned_json_bounds(item)
        if bounds is not None:
            total += sys.getsizeof(bounds) + sum(sys.getsizeof(part) for part in bounds)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (tuple, list)):
            pending.extend(item)
    return total


class DailyComponents:
    """Only scientific components, never financial rows or opaque causal IDs.

    The owning expanded context supplies the validated detached price views.
    Identity keys accept only the current private immutable native binding;
    daily scheduling reads no candidate configuration fields. Every outer
    conjunction and account identity is rebuilt from the current full inputs.
    """

    def __init__(self):
        self.rows, self.sources, self.snapshots = OrderedDict(), {}, OrderedDict()
        self.bytes = self.snapshot_bytes = 0
        self.thread, self.closed = get_ident(), False

    def key(self, evidence, kind, descriptor):
        from research.gate_input_binding import _BINDING, FIELDS
        from research.owned_entry_features import is_immutable_json
        from research.portfolio import fingerprint
        from research.prepared_calculation import prepared_market

        binding = _BINDING.get()
        snapshot = evidence["snapshot"]
        if (
            self.closed
            or self.thread != get_ident()
            or not components_only()
            or binding is None
            or not all(evidence[field] is binding[0][field] for field in FIELDS)
            or prepared_market(snapshot) is None
            or snapshot["provenance"]["interval"] != "D"
            or evidence["portfolio"]["engine"] != "vectorbt"
            or not evidence.get("frozen_prices")
        ):
            return None
        rows = evidence["strategies"]
        if not all(
            type(row.get("id")) is str and is_immutable_json(row["signals"]) for row in rows
        ):
            return None
        return (
            id(snapshot),
            kind,
            fingerprint(descriptor),
            tuple((row["id"], id(row["signals"])) for row in rows),
        )

    def get(self, key, evidence):
        found = self.rows.get(key) if key is not None else None
        if found is None:
            return None
        snapshot = self.snapshots[key[0]][0]
        if snapshot is not evidence["snapshot"] or any(
            self.sources[identity][0] is not row["signals"]
            for (_, identity), row in zip(key[3], evidence["strategies"], strict=True)
        ):
            return None
        self.rows.move_to_end(key)
        self.snapshots.move_to_end(key[0])
        return found[0]

    def _drop(self, key):
        _, charge = self.rows.pop(key)
        self.bytes -= charge
        for _, identity in key[3]:
            source = self.sources[identity]
            source[2] -= 1
            if source[2] == 0:
                self.bytes -= source[1]
                del self.sources[identity]

    def put(self, key, evidence, receipt):
        from research.canonical_json import bounded_canonical_snapshot
        from research.owned_entry_features import _capture, owned_json_bounds

        if key is None:
            return receipt
        captured = bounded_canonical_snapshot(receipt, max_bytes=MAX_COMPONENT_BYTES)
        if captured is None:
            return receipt
        plain, raw = captured
        owned = _capture(plain)
        charge = len(raw) + _heap_bytes(owned) + _heap_bytes(key) + 256
        signals = {id(row["signals"]): row["signals"] for row in evidence["strategies"]}
        # Retained immutable signal trees can include large correlation maps.
        # Traverse each new tree once, rather than once per stock/market rule.
        source_charges = {
            identity: self.sources[identity][1] if identity in self.sources else _heap_bytes(source)
            for identity, source in signals.items()
        }
        added = sum(
            charge for identity, charge in source_charges.items() if identity not in self.sources
        )
        if charge + added > MAX_COMPONENT_BYTES:
            return receipt
        snapshot = evidence["snapshot"]
        bounds = owned_json_bounds(snapshot)
        snapshot_charge = bounds[2] + 64 * bounds[0]
        if snapshot_charge > MAX_SNAPSHOT_BYTES:
            return receipt
        if key[0] not in self.snapshots:
            while self.snapshots and (
                len(self.snapshots) >= 2
                or self.snapshot_bytes + snapshot_charge > MAX_SNAPSHOT_BYTES
            ):
                expired, (_, old_charge) = self.snapshots.popitem(last=False)
                self.snapshot_bytes -= old_charge
                for old_key in [item for item in self.rows if item[0] == expired]:
                    self._drop(old_key)
            self.snapshots[key[0]] = (snapshot, snapshot_charge)
            self.snapshot_bytes += snapshot_charge
        if key in self.rows:
            self._drop(key)

        # Eviction may remove the last reference to an incoming source too.
        def incoming():
            return sum(
                source_charges[identity] for identity in signals if identity not in self.sources
            )

        while self.rows and (
            len(self.rows) >= MAX_COMPONENTS
            or self.bytes + charge + incoming() > MAX_COMPONENT_BYTES
        ):
            self._drop(next(iter(self.rows)))
        if charge + incoming() > MAX_COMPONENT_BYTES:
            return receipt
        for identity, source in signals.items():
            if identity not in self.sources:
                source_charge = source_charges[identity]
                self.sources[identity] = [source, source_charge, 0]
                self.bytes += source_charge
        for _, identity in key[3]:
            self.sources[identity][2] += 1
        self.rows[key] = (owned, charge)
        self.bytes += charge
        return owned

    def close(self):
        self.rows.clear()
        self.sources.clear()
        self.snapshots.clear()
        self.bytes = self.snapshot_bytes = 0
        self.closed = True
