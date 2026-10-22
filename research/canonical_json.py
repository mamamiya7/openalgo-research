"""Bounded fast path for the existing canonical JSON byte representation.

Large or unusual inputs remain the caller's streaming encoder's responsibility.
The copied tree prevents a mutable caller from growing the C encoder's input
after the bound has been checked. No input or encoded output is retained.
"""

import json
import math

MAX_NODES = 4096
MAX_DEPTH = 64
MAX_ASCII_BYTES = 64 * 1024
MAX_MEDIUM_NODES = 524_288
MAX_MEDIUM_ASCII_BYTES = 32 * 1024**2


def small_canonical_bytes(value):
    """Return exact canonical bytes for small plain trees, otherwise ``None``.

    This is an optional optimization, not a validator. In particular, returning
    ``None`` for non-finite numbers, custom types or cycles leaves the original
    streaming encoder's behavior and errors unchanged.
    """
    return _canonical_bytes(value, MAX_NODES, MAX_ASCII_BYTES)


def bounded_canonical_bytes(value, *, max_bytes=None, json_types_only=False):
    """Optionally encode a detached medium tree within the caller's byte limit.

    Conservative pre-encoding bounds cap both the copy and C-encoder allocation.
    Inputs that cannot fit, including unusual types, return to the original
    streaming path, which retains its early size/error precedence.
    ``json_types_only`` also excludes tuples when byte equality is used to
    recognize a previously validated JSON schema (a tuple encodes like a list).
    """
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        return None
    maximum = (
        MAX_MEDIUM_ASCII_BYTES if max_bytes is None else min(max_bytes, MAX_MEDIUM_ASCII_BYTES)
    )
    return _canonical_bytes(value, MAX_MEDIUM_NODES, maximum, allow_tuples=not json_types_only)


def _canonical_bytes(value, maximum_nodes, maximum_bytes, *, allow_tuples=True):
    remaining_nodes = maximum_nodes
    remaining_bytes = maximum_bytes
    active = set()

    def copy(item, depth):
        nonlocal remaining_nodes, remaining_bytes
        remaining_nodes -= 1
        if remaining_nodes < 0 or depth > MAX_DEPTH:
            raise ValueError
        kind = type(item)
        if item is None:
            allowance = 4
        elif kind is bool:
            allowance = 5
        elif kind is str:
            # One non-BMP code point becomes two six-byte surrogate escapes.
            allowance = 12 * len(item) + 2
        elif kind is int:
            allowance = item.bit_length() // 3 + 2
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError
            allowance = 32
        elif kind in (dict, list) or (allow_tuples and kind is tuple):
            identity = id(item)
            if identity in active:
                raise ValueError
            # Punctuation and a lower bound on child nodes, before iteration.
            allowance = 2 + len(item) * (2 if kind is dict else 1)
            if allowance > remaining_bytes or len(item) > remaining_nodes:
                raise ValueError
            remaining_bytes -= allowance
            active.add(identity)
            try:
                if kind is dict:
                    result = {}
                    for key, child in item.items():
                        if type(key) is not str:
                            raise ValueError
                        copied_key = copy(key, depth + 1)
                        result[copied_key] = copy(child, depth + 1)
                    return result
                return [copy(child, depth + 1) for child in item]
            finally:
                active.remove(identity)
        else:
            raise ValueError
        remaining_bytes -= allowance
        if remaining_bytes < 0:
            raise ValueError
        return item

    try:
        bounded = copy(value, 0)
        return json.dumps(
            bounded, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True
        ).encode("ascii")
    except (ValueError, TypeError, RuntimeError, OverflowError):
        return None
