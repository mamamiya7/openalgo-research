"""Private synchronous transfer of already encoded, owned checkpoint trees.

Only native checkpoint sinks opt in. Generic callbacks never receive a byte
capability. Each canonical root is at most 32 MiB; composition briefly owns
the old and new roots, at most 64 MiB, without retaining a cross-save cache.
"""

import json
import re
from threading import get_ident

from research.canonical_json import bounded_canonical_bytes, bounded_canonical_snapshot

MAX_OWNED_BYTES = 32 * 1024**2


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _insert(fields, raw, key, member_raw):
    """Insert one member into exact canonical object bytes without re-encoding it."""
    if type(fields) is not dict or key in fields or any(type(k) is not str for k in fields):
        return None
    key_raw = _encoded(key) + b":"
    if len(raw) + len(key_raw) + len(member_raw) + bool(fields) > MAX_OWNED_BYTES:
        return None
    view = memoryview(raw)
    keys = sorted(fields)
    if not keys:
        return b"".join((b"{", key_raw, member_raw, b"}"))
    if key < keys[0]:
        return b"".join((b"{", key_raw, member_raw, b",", view[1:]))
    if key > keys[-1]:
        return b"".join((view[:-1], b",", key_raw, member_raw, b"}"))
    preceding = {k: fields[k] for k in keys if k < key}
    prefix_raw = bounded_canonical_bytes(preceding, max_bytes=64 * 1024, allow_native_float64=True)
    if prefix_raw is None:
        return None
    prefix = prefix_raw[:-1] + b","
    if not raw.startswith(prefix):
        return None
    boundary = len(prefix)
    del prefix, prefix_raw, preceding
    return b"".join((view[:boundary], key_raw, member_raw, b",", view[boundary:]))


class _OwnedCanonicalJSON:
    """One exact detached tree/byte pair, consumed only by native internal code."""

    __slots__ = ("__value", "__raw", "__owner", "__closed")

    def __init__(self, value, raw):
        # This private constructor accepts only the pair returned by the bounded
        # canonical snapshot or by this module's exact member composition.
        if type(value) is not dict or type(raw) is not bytes or len(raw) > MAX_OWNED_BYTES:
            raise ValueError("Owned checkpoint bytes exceed their private admission")
        self.__value, self.__raw = value, raw
        self.__owner, self.__closed = get_ident(), False

    def _check(self):
        if self.__closed or self.__owner != get_ident():
            raise ValueError("Owned checkpoint bytes cannot escape their synchronous owner")

    @property
    def value(self):
        self._check()
        return self.__value

    def with_checkpoint_id(self, identity):
        self._check()
        if type(identity) is not str or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
            return None
        raw = _insert(self.__value, self.__raw, "checkpoint_id", _encoded(identity))
        if raw is None:
            return None
        result = type(self)({**self.__value, "checkpoint_id": identity}, raw)
        self.close()
        return result

    def wrap(self, member, fields):
        self._check()
        if type(member) is not str or type(fields) is not dict or member in fields:
            return None
        # These small sibling fields have not previously been captured. Detach
        # them before encoding, retaining the same plain/native-float admission.
        overhead = len(_encoded(member)) + 1 + len(self.__raw) + bool(fields)
        captured = bounded_canonical_snapshot(
            fields, max_bytes=MAX_OWNED_BYTES - overhead, allow_native_float64=True
        )
        if captured is None:
            return None
        owned_fields, fields_raw = captured
        del captured
        raw = _insert(owned_fields, fields_raw, member, self.__raw)
        if raw is None:
            return None
        result = type(self)({**owned_fields, member: self.__value}, raw)
        self.close()
        return result

    def take(self):
        self._check()
        value, raw = self.__value, self.__raw
        self.close()
        return value, raw

    def close(self):
        self.__value = self.__raw = None
        self.__closed = True


class _OwnedCheckpointSink:
    """Explicit native capability; ordinary/decorated callbacks keep their API."""

    __slots__ = ("__callback", "__owned_callback", "__owner")

    def __init__(self, callback, owned_callback):
        self.__callback, self.__owned_callback = callback, owned_callback
        self.__owner = get_ident()

    def __call__(self, *args, **kwargs):
        return self.__callback(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.__callback, name)

    def publish_owned(self, owned, counts, publish=None):
        if self.__owner != get_ident() or type(owned) is not _OwnedCanonicalJSON:
            raise ValueError("Owned checkpoint sink requires its synchronous native payload")
        owned._check()
        try:
            return self.__owned_callback(owned, counts, publish)
        finally:
            owned.close()
