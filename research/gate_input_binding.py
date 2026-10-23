"""Exact account-input hashes shared only within one immutable native gate call."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from research.canonical_json import (
    bounded_canonical_fragments,
    prepared_canonical_fragments,
    prepared_signal_lists,
    register_prepared_signal_list,
)
from research.owned_entry_features import _capture, is_immutable_json
from research.prepared_calculation import prepared_market

FIELDS = ("portfolio", "strategies", "signals", "snapshot", "versions")
_BINDING = ContextVar("native_gate_input_binding", default=None)


def copy_gate_signal(signal):
    """Gate exclusions mutate only a detached signal's outer mapping."""
    return deepcopy(dict(signal)) if is_immutable_json(signal) else deepcopy(signal)


def freeze_gate_rows(rows):
    """Share fresh gate rows only inside the native immutable binding scope."""
    if _BINDING.get() is None or not isinstance(rows, list):
        return rows
    try:
        owned = _capture(rows)
    except (TypeError, ValueError, RecursionError):
        return rows
    register_prepared_signal_list(owned)
    return owned


freeze_gate_signals = freeze_gate_rows


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _update(digest, value, *, _memoize_owned=True):
    fragments = prepared_canonical_fragments(
        value, max_bytes=64 * 1024**2, _memoize_owned=_memoize_owned
    )
    if fragments is None:
        fragments = bounded_canonical_fragments(value, max_bytes=64 * 1024**2)
    if fragments is not None:
        for part in fragments:
            digest.update(part)
        return
    for part in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(
        value
    ):
        digest.update(part.encode())


@contextmanager
def prepared_gate_inputs(evidence):
    """Detach mutable account fields; retain already immutable market/features."""
    market = prepared_market(evidence["snapshot"])
    if market is None:
        yield evidence
        return
    try:
        inputs = _capture({key: evidence[key] for key in FIELDS})
    except ValueError:
        # Optional plain-JSON optimization must retain unusual legacy types'
        # original validators and encoder behavior rather than reject them.
        yield evidence
        return
    with prepared_signal_lists(row["signals"] for row in inputs["strategies"]):
        digest = hashlib.sha256()
        digest.update(b"{")
        for index, key in enumerate(sorted(FIELDS)):
            if index:
                digest.update(b",")
            digest.update(_encoded(key))
            digest.update(b":")
            cached = market.canonical_snapshot_bytes() if key == "snapshot" else None
            if cached is not None:
                digest.update(cached)
            else:
                # This strategy wrapper was just captured for this candidate
                # and is hashed once. Its registered signal children already
                # reuse bytes; retaining the new ancestor only duplicates them.
                _update(digest, inputs[key], _memoize_owned=key != "strategies")
        digest.update(b"}")
        token = _BINDING.set((inputs, digest.hexdigest()))
        try:
            yield {**evidence, **inputs}
        finally:
            _BINDING.reset(token)


def execution_inputs_id(evidence):
    retained = _BINDING.get()
    if retained is not None and all(evidence[key] is retained[0][key] for key in FIELDS):
        return retained[1]
    from research.portfolio import fingerprint

    return fingerprint({key: evidence[key] for key in FIELDS})
