"""Large native trees keep exact JSON bytes without Python token streaming."""

import hashlib
import json

import pytest

from research import canonical_json, portfolio, stock_features


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@pytest.fixture(scope="module")
def large():
    # Beyond the existing whole-tree node ceiling; individually bounded leaves.
    return {f"S{i:03}": [{"close": 12.5, "slot": j} for j in range(800)] for i in range(146)}


def test_large_node_tree_has_exact_fragments_and_hash(large):
    assert canonical_json.bounded_canonical_bytes(large) is None
    raw = compact(large)
    pieces = canonical_json.bounded_canonical_fragments(large, max_bytes=len(raw))
    assert pieces is not None and len(pieces) > 1
    assert b"".join(pieces) == raw
    assert portfolio.fingerprint(large) == hashlib.sha256(raw).hexdigest()
    assert stock_features._json_size(large, len(raw)) == len(raw)


def test_fragmented_bound_preserves_rejection(large):
    maximum = len(compact(large)) - 1
    assert canonical_json.bounded_canonical_fragments(large, max_bytes=maximum) is None
    with pytest.raises(ValueError, match="bounded byte allowance"):
        stock_features._json_size(large, maximum)


def test_small_unicode_and_order_are_exact():
    value = {"z": ["☃", "😀", -0.0, True, None], "a": {"x": 1e-15}}
    assert b"".join(canonical_json.bounded_canonical_fragments(value, max_bytes=1000)) == compact(value)


def test_late_mutation_changes_fragmented_identity(large):
    before = portfolio.fingerprint(large)
    last = large["S145"][-1]
    old = last["close"]
    try:
        last["close"] = 999.0
        assert portfolio.fingerprint(large) != before
    finally:
        last["close"] = old


@pytest.mark.parametrize("value", [{1: "nonstring"}, (1, 2), {"a": float("nan")}, {"a": object()}])
def test_unsupported_content_retains_original_fallback(value):
    assert canonical_json.bounded_canonical_fragments(value, max_bytes=1024) is None


def test_cycles_and_excess_depth_return_to_streaming():
    cyclic = []
    cyclic.append(cyclic)
    assert canonical_json.bounded_canonical_fragments(cyclic, max_bytes=1024) is None
    deep = []
    for _ in range(canonical_json.MAX_DEPTH + 1):
        deep = [deep]
    assert canonical_json.bounded_canonical_fragments(deep, max_bytes=1024) is None
