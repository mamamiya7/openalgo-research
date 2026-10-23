"""Bounded daily attachment reuse over exact immutable calculation inputs.

Daily entry scheduling does not depend on candidate stop, hold or sizing fields.
The prepared attachment still checks its receipt/context on every call; only a
completed list with the same immutable inputs and effective feature modes reuses
its already checked entry availability. Minute and caller-owned inputs decline.
"""

from collections import OrderedDict

from research.owned_entry_features import _capture, is_immutable_json, owned_json_bounds

MAX_LISTS = 128
MAX_BYTES = 64 * 1024**2
MAX_SNAPSHOT_BYTES = 128 * 1024**2


class DailySignals:
    def __init__(self):
        self._rows = OrderedDict()
        self._bytes = 0
        self._snapshots = OrderedDict()
        self._snapshot_bytes = 0

    def key(self, snapshot, strategy, schedule, modes=()):
        signals = strategy.get("signals")
        if (
            not is_immutable_json(snapshot)
            or snapshot["provenance"]["interval"] != "D"
            or not is_immutable_json(signals)
            or type(strategy.get("id")) is not str
            or any(type(mode) not in (str, type(None)) for mode in modes)
        ):
            return None
        return (id(snapshot), id(signals), strategy["id"], schedule, *modes)

    def get(self, key, snapshot, signals):
        found = self._rows.get(key) if key is not None else None
        if found is None or found[0] is not snapshot or found[1] is not signals:
            return None
        self._rows.move_to_end(key)
        self._snapshots.move_to_end(id(snapshot))
        return found[2]

    def put(self, key, snapshot, source, result):
        if key is None:
            return result
        try:
            owned = _capture(result)
        except (ValueError, RecursionError):
            return result
        # Conservative ASCII bounds also cover retained Python container payload
        # for these JSON signal/entry shapes. Charge shared leaves per list.
        bounds = owned_json_bounds(owned)
        charge = bounds[2] + 64 * bounds[0]
        if charge > MAX_BYTES:
            return result
        identity = id(snapshot)
        snapshot_bounds = owned_json_bounds(snapshot)
        snapshot_charge = snapshot_bounds[2] + 64 * snapshot_bounds[0]
        if snapshot_charge > MAX_SNAPSHOT_BYTES:
            return result
        if identity not in self._snapshots:
            while self._snapshots and (
                len(self._snapshots) >= 2
                or self._snapshot_bytes + snapshot_charge > MAX_SNAPSHOT_BYTES
            ):
                expired, (_, expired_charge) = self._snapshots.popitem(last=False)
                self._snapshot_bytes -= expired_charge
                for expired_key in [key for key in self._rows if key[0] == expired]:
                    self._bytes -= self._rows.pop(expired_key)[3]
            self._snapshots[identity] = (snapshot, snapshot_charge)
            self._snapshot_bytes += snapshot_charge
        while self._rows and (len(self._rows) >= MAX_LISTS or self._bytes + charge > MAX_BYTES):
            _, previous = self._rows.popitem(last=False)
            self._bytes -= previous[3]
        old = self._rows.pop(key, None)
        if old is not None:
            self._bytes -= old[3]
        self._rows[key] = (snapshot, source, owned, charge)
        self._bytes += charge
        return owned

    def close(self):
        self._rows.clear()
        self._bytes = 0
        self._snapshots.clear()
        self._snapshot_bytes = 0
