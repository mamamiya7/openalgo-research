"""Bounded fast path for the existing canonical JSON byte representation.

Large or unusual inputs remain the caller's streaming encoder's responsibility.
The copied tree prevents a mutable caller from growing the C encoder's input
after the bound has been checked. No input or encoded output is retained.
"""

import json
import math
import sys
from contextlib import contextmanager
from contextvars import ContextVar

MAX_NODES = 4096
MAX_DEPTH = 64
MAX_ASCII_BYTES = 64 * 1024
MAX_MEDIUM_NODES = 524_288
MAX_MEDIUM_ASCII_BYTES = 32 * 1024**2
MAX_PREPARED_SUBTREE_BYTES = 32 * 1024**2
_REGISTERED_SIGNAL_LISTS = ContextVar("prepared_canonical_signal_lists", default=None)


def register_prepared_signal_list(value):
    """Append one exact immutable list to the bounded active gate-byte scope."""
    from research.owned_entry_features import owned_json_bounds

    retained = _REGISTERED_SIGNAL_LISTS.get()
    if retained is None or not isinstance(value, list) or owned_json_bounds(value) is None:
        return False
    previous = retained.get(id(value))
    if previous is not None:
        return previous[0] is value
    if len(retained) >= 8:
        return False
    size = sum(len(item[1]) for item in retained.values())

    def encode():
        pieces = prepared_canonical_fragments(
            value,
            max_bytes=64 * 1024**2 - size,
            allow_native_float64=True,
            _memoize_owned=False,
        )
        if pieces is None:
            return None
        return pieces[0] if len(pieces) == 1 else b"".join(pieces)

    get_owner = getattr(
        sys.modules.get("research.prepared_calculation"), "active_prepared_market", None
    )
    owner = get_owner() if get_owner is not None else None
    raw = owner.memoize_canonical_signal_list(value, encode) if owner is not None else encode()
    if raw is None:
        return False
    if size + len(raw) > 64 * 1024**2:
        return False
    retained[id(value)] = (value, raw)
    return True


@contextmanager
def prepared_signal_lists(values):
    """Pre-encode at most eight exact immutable signal lists for one gate call."""
    retained = {}
    token = _REGISTERED_SIGNAL_LISTS.set(retained)
    try:
        for index, value in enumerate(values):
            if index >= 8:
                break
            register_prepared_signal_list(value)
        yield
    finally:
        retained.clear()
        _REGISTERED_SIGNAL_LISTS.reset(token)


def small_canonical_bytes(value):
    """Return exact canonical bytes for small plain trees, otherwise ``None``.

    This is an optional optimization, not a validator. In particular, returning
    ``None`` for non-finite numbers, custom types or cycles leaves the original
    streaming encoder's behavior and errors unchanged.
    """
    return _canonical_bytes(value, MAX_NODES, MAX_ASCII_BYTES)


def bounded_canonical_bytes(
    value, *, max_bytes=None, json_types_only=False, allow_native_float64=False
):
    """Optionally encode a detached medium tree within the caller's byte limit.

    Conservative pre-encoding bounds cap both the copy and C-encoder allocation.
    Inputs that cannot fit, including unusual types, return to the original
    streaming path, which retains its early size/error precedence.
    ``json_types_only`` also excludes tuples when byte equality is used to
    recognize a previously validated JSON schema (a tuple encodes like a list).
    ``allow_native_float64`` admits only the pinned runtime's exact immutable
    NumPy float64 type, retaining its type in snapshots for strict validators.
    """
    if type(allow_native_float64) is not bool:
        return None
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        return None
    maximum = (
        MAX_MEDIUM_ASCII_BYTES if max_bytes is None else min(max_bytes, MAX_MEDIUM_ASCII_BYTES)
    )
    return _canonical_bytes(
        value,
        MAX_MEDIUM_NODES,
        maximum,
        allow_tuples=not json_types_only,
        allow_native_float64=allow_native_float64,
    )


def bounded_default_json_bytes(value, *, max_bytes):
    """Optional bounded C encoding with JSONEncoder's default whitespace/order.

    This keeps byte-limit validators' exact existing representation. Unusual,
    oversized or concurrently changed inputs still use their original encoder.
    """
    if type(max_bytes) is not int or max_bytes < 0:
        return None
    return _canonical_bytes(
        value,
        MAX_MEDIUM_NODES,
        min(max_bytes, MAX_MEDIUM_ASCII_BYTES),
        _default_format=True,
    )


def bounded_canonical_snapshot(
    value, *, max_bytes=None, allow_native_float64=False, _preserve_owned=False
):
    """Return an owned plain-JSON tree and its exact bytes, or ``None``.

    Checkpoint publishers can hash this same detached tree and then hand it to
    their callback without making another full copy. Bounds/fallback are the
    same as medium encoding, including the explicit native float64 opt-in.
    Tuples and custom values remain the caller's
    responsibility, preserving their original deepcopy and encoding semantics.
    No input, snapshot or encoded bytes survive this call except its result.

    The private native checkpoint sink may preserve exact immutable owned
    subtrees. Its mutable outer containers are still detached; ordinary/public
    callers always receive fresh plain dict/list descendants.
    """
    if type(allow_native_float64) is not bool:
        return None
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        return None
    maximum = (
        MAX_MEDIUM_ASCII_BYTES if max_bytes is None else min(max_bytes, MAX_MEDIUM_ASCII_BYTES)
    )
    captured = []
    raw = _canonical_bytes(
        value,
        MAX_MEDIUM_NODES,
        maximum,
        allow_tuples=False,
        _capture=captured,
        allow_native_float64=allow_native_float64,
        _preserve_owned=_preserve_owned,
    )
    return (captured[0], raw) if raw is not None else None


def _exclusive_canonical_bytes(value, *, max_bytes, _checked_projection=None):
    """Private native ownership seam: validate originals without copying them."""
    checked_bounds = None
    if _checked_projection is not None:
        from research.native_report_ownership import checked_projection_bounds

        checked_bounds = checked_projection_bounds(_checked_projection, value)
    return _canonical_bytes(
        value,
        MAX_MEDIUM_NODES,
        min(max_bytes, MAX_MEDIUM_ASCII_BYTES),
        allow_tuples=False,
        allow_native_float64=True,
        _borrow=True,
        _checked_bounds=checked_bounds,
    )


def bounded_canonical_fragments(value, *, max_bytes, allow_native_float64=False):
    """Exact compact JSON for large plain trees, in bounded detached pieces.

    This optional path keeps the existing per-piece copier/node/type guards.
    A large scanner may exceed those node counts in aggregate while each symbol
    or entry is small. Aggregate bytes, fragment count, container length and
    recursion are all bounded; unsupported content returns to the caller's
    original streaming validation and its error precedence. No data is cached.
    """
    if type(max_bytes) is not int or max_bytes < 0 or type(allow_native_float64) is not bool:
        return None
    maximum = min(max_bytes, 64 * 1024**2)
    pieces, active, size = [], set(), 0
    owned_bounds = getattr(
        sys.modules.get("research.owned_entry_features"), "owned_json_bounds", None
    )

    def append(raw):
        nonlocal size
        size += len(raw)
        if size > maximum or len(pieces) >= 65_536:
            raise ValueError
        pieces.append(raw)

    def visit(item, depth):
        if depth > MAX_DEPTH:
            raise ValueError
        raw = _canonical_bytes(
            item,
            MAX_MEDIUM_NODES,
            8 * 1024**2,
            allow_tuples=False,
            allow_native_float64=allow_native_float64,
            _maximum_depth=MAX_DEPTH - depth,
        )
        if raw is not None:
            append(raw)
            return
        kind = type(item)
        if owned_bounds is not None and owned_bounds(item) is not None:
            # An admitted immutable subtree may exceed one piece's conservative
            # allowance. Split it just like ordinary JSON instead of falling
            # back to the slow whole-document streaming encoder.
            kind = dict if isinstance(item, dict) else list
        if kind not in (dict, list) or len(item) > MAX_MEDIUM_NODES:
            raise ValueError
        identity = id(item)
        if identity in active:
            raise ValueError
        active.add(identity)
        try:
            if kind is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError
                keys = sorted(item)
                append(b"{")
                for index, key in enumerate(keys):
                    if index:
                        append(b",")
                    visit(key, depth + 1)
                    append(b":")
                    visit(item[key], depth + 1)
                append(b"}")
            else:
                append(b"[")
                for index, child in enumerate(item):
                    if index:
                        append(b",")
                    visit(child, depth + 1)
                append(b"]")
        finally:
            active.remove(identity)

    try:
        visit(value, 0)
        return tuple(pieces)
    except (KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        return None
    finally:
        # The recursive closure can survive until cyclic GC. Release its list
        # immediately; only a successful caller's immutable tuple owns bytes.
        pieces.clear()
        active.clear()


def prepared_canonical_fragments(
    value, *, max_bytes, allow_native_float64=False, _memoize_owned=True
):
    """Bounded detached outer JSON with cached immutable leaves and C encoding.

    Mutable outer containers are always traversed afresh. Only validated private
    immutable JSON subtrees and the current owner's immutable snapshot can reuse
    bytes. This is encoding reuse, never financial admission. Unsupported types
    or bounds fall back to the original encoder.
    """
    get_owner = getattr(
        sys.modules.get("research.prepared_calculation"), "active_prepared_market", None
    )
    owner = get_owner() if get_owner is not None else None
    if (
        owner is None
        or type(max_bytes) is not int
        or max_bytes < 0
        or type(allow_native_float64) is not bool
    ):
        return None
    from research.owned_entry_features import is_owned_entry, owned_json_bounds

    maximum = min(max_bytes, 64 * 1024**2)
    nodes, remaining_bytes = 4_000_000, maximum
    active, raw_leaves = set(), {}
    registered_lists = _REGISTERED_SIGNAL_LISTS.get()
    native_float64 = (
        getattr(sys.modules.get("numpy"), "float64", None) if allow_native_float64 else None
    )

    def copy(item, depth):
        nonlocal nodes, remaining_bytes
        if depth > MAX_DEPTH:
            raise ValueError
        nodes -= 1
        if nodes < 0:
            raise ValueError
        bounds = owned_json_bounds(item)
        registered = registered_lists.get(id(item)) if registered_lists is not None else None
        retained_raw = (
            registered[1]
            if registered is not None and registered[0] is item
            else owner.lookup_canonical_signal_list(item)
            if bounds is not None and isinstance(item, list)
            else None
        )
        if bounds is not None and retained_raw is not None:
            count, height, _ = bounds
            nodes -= count - 1
            remaining_bytes -= len(retained_raw)
            if nodes < 0 or remaining_bytes < 0 or depth + height > MAX_DEPTH:
                raise ValueError
            identity = id(item)
            if identity not in raw_leaves:
                from simplejson.raw_json import RawJSON

                raw_leaves[identity] = RawJSON(retained_raw.decode("ascii"))
            return raw_leaves[identity]
        if bounds is not None and (
            item is owner.snapshot
            or isinstance(item, dict)
            and is_owned_entry(item, item.get("version"))
        ):
            count, height, byte_allowance = bounds
            nodes -= count - 1
            if nodes < 0 or depth + height > MAX_DEPTH:
                raise ValueError
            if item is owner.snapshot:
                raw = owner.canonical_snapshot_bytes()
                if raw is None:
                    raise ValueError
            else:
                # Bound the whole-feature C encoder before allocating its
                # output. Conservative rejection retains the original bounded
                # or streaming fallback, even when actual escaped bytes fit.
                if byte_allowance > min(32 * 1024**2, remaining_bytes):
                    raise ValueError
                raw = owner.memoize_canonical_feature(
                    item,
                    lambda: json.dumps(
                        item,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                        ensure_ascii=True,
                        check_circular=False,
                    ).encode("ascii"),
                )
            remaining_bytes -= len(raw)
            if remaining_bytes < 0:
                raise ValueError
            identity = id(item)
            if identity not in raw_leaves:
                from simplejson.raw_json import RawJSON

                raw_leaves[identity] = RawJSON(raw.decode("ascii"))
            return raw_leaves[identity]
        if (
            bounds is not None
            and _memoize_owned
            and bounds[0] >= 256
            and bounds[0] - 1 <= nodes
            and depth + bounds[1] <= MAX_DEPTH
            and bounds[2] <= min(MAX_PREPARED_SUBTREE_BYTES, remaining_bytes)
        ):
            count, height, byte_allowance = bounds

            # Encode at most the highest immutable subtree in this traversal.
            # Its first encoding still uses admitted financial RawJSON leaves,
            # avoiding repeated correlation-float conversion. Do not retain all
            # overlapping ancestors/descendants of one new subtree.
            def encode_owned():
                fragments = prepared_canonical_fragments(
                    item,
                    max_bytes=min(MAX_PREPARED_SUBTREE_BYTES, remaining_bytes),
                    allow_native_float64=allow_native_float64,
                    _memoize_owned=False,
                )
                return None if fragments is None else b"".join(fragments)

            raw = owner.memoize_canonical_json(item, encode_owned)
            if raw is not None:
                nodes -= count - 1
                remaining_bytes -= len(raw)
                if remaining_bytes < 0:
                    raise ValueError
                identity = id(item)
                if identity not in raw_leaves:
                    from simplejson.raw_json import RawJSON

                    raw_leaves[identity] = RawJSON(raw.decode("ascii"))
                return raw_leaves[identity]
            # Optional whole-subtree admission may be more conservative than
            # child encoding with cached financial leaves. Keep that original
            # path rather than forcing a costly whole-document fallback.
        kind = type(item)
        if bounds is not None:
            kind = dict if isinstance(item, dict) else list
        if item is None:
            allowance = 4
        elif kind is bool:
            allowance = 5
        elif kind is str:
            allowance = (6 if item.isascii() else 12) * len(item) + 2
        elif kind is int:
            allowance = item.bit_length() // 3 + 2
        elif kind is float or kind is native_float64 and isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError
            allowance = 32
            if kind is native_float64:
                item = float(item)
        elif kind in (dict, list, tuple):
            identity = id(item)
            if identity in active or len(item) > MAX_MEDIUM_NODES:
                raise ValueError
            remaining_bytes -= 2 + len(item) * (2 if kind is dict else 1)
            if remaining_bytes < 0 or len(item) > nodes:
                raise ValueError
            active.add(identity)
            try:
                if kind is dict:
                    result = {}
                    for key, child in item.items():
                        if type(key) is not str:
                            raise ValueError
                        nodes -= 1
                        remaining_bytes -= (6 if key.isascii() else 12) * len(key) + 2
                        if nodes < 0 or remaining_bytes < 0 or depth + 1 > MAX_DEPTH:
                            raise ValueError
                        result[key] = copy(child, depth + 1)
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
        detached = copy(value, 0)
        encoder = json
        if raw_leaves:
            import simplejson

            if simplejson.__version__ != "3.20.2":
                return None
            encoder = simplejson
        raw = encoder.dumps(
            detached,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=True,
            check_circular=False,
        ).encode("ascii")
        return (raw,) if len(raw) <= maximum else None
    except (KeyError, TypeError, ValueError, RuntimeError, OverflowError, ImportError):
        return None
    finally:
        raw_leaves.clear()
        active.clear()


def _canonical_bytes(
    value,
    maximum_nodes,
    maximum_bytes,
    *,
    allow_tuples=True,
    _capture=None,
    _default_format=False,
    allow_native_float64=False,
    _maximum_depth=MAX_DEPTH,
    _borrow=False,
    _preserve_owned=False,
    _checked_bounds=None,
):
    remaining_nodes = maximum_nodes
    remaining_bytes = maximum_bytes
    active = set()
    # No eager NumPy import. This exact immutable scalar is already a float
    # subclass in the pinned runtime and is encoded by JSON as its native value.
    # Retain its type in the detached tree so downstream strict schemas still
    # see the original value; arbitrary float subclasses remain ineligible.
    native_float64 = (
        getattr(sys.modules.get("numpy"), "float64", None) if allow_native_float64 else None
    )
    # Entry-feature owners may supply privately detached immutable JSON. Reuse
    # only their exact registered types, with precomputed subtree bounds; normal
    # caller dictionaries and subclasses retain the original copy/validation.
    owned_bounds = getattr(
        sys.modules.get("research.owned_entry_features"), "owned_json_bounds", None
    )

    def copy(item, depth):
        nonlocal remaining_nodes, remaining_bytes
        remaining_nodes -= 1
        if remaining_nodes < 0 or depth > _maximum_depth:
            raise ValueError
        if _borrow and _checked_bounds is not None and type(item) is dict:
            checked = _checked_bounds(item)
            if checked is not None:
                nodes, height, byte_allowance = checked
                remaining_nodes -= nodes - 1
                remaining_bytes -= byte_allowance
                if remaining_nodes < 0 or remaining_bytes < 0 or depth + height > _maximum_depth:
                    raise ValueError
                return item
        kind = type(item)
        if owned_bounds is not None and kind not in (dict, list, tuple, str, int, float, bool):
            bounds = owned_bounds(item)
            if bounds is not None:
                nodes, height, byte_allowance = bounds
                if _capture is None or _preserve_owned:
                    remaining_nodes -= nodes - 1
                    remaining_bytes -= byte_allowance
                    if (
                        remaining_nodes < 0
                        or remaining_bytes < 0
                        or depth + height > _maximum_depth
                    ):
                        raise ValueError
                    return item
                # Snapshot capture promises a fresh ordinary JSON tree; never
                # expose a shared immutable capability as its mutable result.
                kind = dict if isinstance(item, dict) else list
        if item is None:
            allowance = 4
        elif kind is bool:
            allowance = 5
        elif kind is str:
            # One non-BMP code point becomes two six-byte surrogate escapes.
            allowance = (6 if item.isascii() else 12) * len(item) + 2
        elif kind is int:
            allowance = item.bit_length() // 3 + 2
        elif kind is float or (kind is native_float64 and isinstance(item, float)):
            if not math.isfinite(item):
                raise ValueError
            allowance = 32
        elif kind in (dict, list) or (allow_tuples and kind is tuple):
            identity = id(item)
            if identity in active:
                raise ValueError
            # Punctuation and a lower bound on child nodes, before iteration.
            punctuation = (
                (4 if kind is dict else 2) if _default_format else (2 if kind is dict else 1)
            )
            allowance = 2 + len(item) * punctuation
            if allowance > remaining_bytes or len(item) > remaining_nodes:
                raise ValueError
            remaining_bytes -= allowance
            active.add(identity)
            try:
                if kind is dict:
                    result = None if _borrow else {}
                    for key, child in item.items():
                        if type(key) is not str:
                            raise ValueError
                        # Keys are already exact immutable strings. Admit their
                        # node/depth/byte cost directly instead of recursively
                        # copying each key in every checkpoint and fingerprint.
                        remaining_nodes -= 1
                        remaining_bytes -= (6 if key.isascii() else 12) * len(key) + 2
                        if remaining_nodes < 0 or depth + 1 > _maximum_depth or remaining_bytes < 0:
                            raise ValueError
                        owned_child = copy(child, depth + 1)
                        if not _borrow:
                            result[key] = owned_child
                    return item if _borrow else result
                if _borrow:
                    for child in item:
                        copy(child, depth + 1)
                    return item
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
        raw = json.dumps(
            bounded,
            sort_keys=not _default_format,
            separators=None if _default_format else (",", ":"),
            allow_nan=False,
            ensure_ascii=True,
            # The detached copier has already rejected every cycle. Its fresh
            # containers cannot be changed through the caller's source tree.
            check_circular=False,
        ).encode("ascii")
        if _capture is not None:
            _capture.append(bounded)
        return raw
    except (ValueError, TypeError, RuntimeError, OverflowError):
        return None
