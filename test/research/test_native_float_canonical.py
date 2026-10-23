"""Native float64 evidence keeps exact bytes, types and bounded admission."""

import hashlib
import json

import numpy as np
import pytest

from research import canonical_json as canonical
from research.portfolio import fingerprint
from research.stock_features import _json_size


@pytest.mark.parametrize(
    "number", [0.0, -0.0, 1.25, 1e-300, 1e300, float.fromhex("0x1.fffffffffffffp+1023")]
)
def test_exact_native_float_bytes_and_owned_snapshot_type(number):
    scalar = np.float64(number)
    source = {"native": scalar, "nested": [scalar]}
    expected = json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert canonical.bounded_canonical_bytes(source) is None
    assert canonical.bounded_canonical_snapshot(source) is None
    actual = canonical.bounded_canonical_bytes(source, allow_native_float64=True)
    assert actual == expected
    owned, raw = canonical.bounded_canonical_snapshot(source, allow_native_float64=True)
    assert raw == expected and owned is not source and owned["nested"] is not source["nested"]
    assert type(owned["native"]) is np.float64
    assert owned["native"] is scalar
    source["nested"].append(source)
    assert owned["nested"] == [scalar]
    assert fingerprint(owned) == hashlib.sha256(expected).hexdigest()
    assert _json_size(owned, len(expected)) == len(expected)
    with pytest.raises(ValueError, match="bounded byte allowance"):
        _json_size(owned, len(expected) - 1)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_native_nonfinite_keeps_original_rejection(number):
    value = {"native": np.float64(number)}
    assert canonical.bounded_canonical_bytes(value, allow_native_float64=True) is None
    assert canonical.bounded_canonical_snapshot(value, allow_native_float64=True) is None
    with pytest.raises(ValueError, match="Out of range"):
        fingerprint(value)


def test_native_opt_in_does_not_admit_custom_float_subclasses():
    class Custom(float):
        def __float__(self):
            raise AssertionError("custom numeric conversion invoked")

    class CustomNative(np.float64):
        pass

    for value in (Custom(1), CustomNative(1), np.int64(1), np.float32(1)):
        assert canonical.bounded_canonical_bytes(value, allow_native_float64=True) is None
        assert canonical.bounded_canonical_snapshot(value, allow_native_float64=True) is None


def test_native_opt_in_keeps_node_depth_byte_cycle_and_tuple_guards(monkeypatch):
    monkeypatch.setattr(canonical, "MAX_MEDIUM_NODES", 4)
    assert (
        canonical.bounded_canonical_snapshot([np.float64(1)] * 4, allow_native_float64=True) is None
    )
    monkeypatch.setattr(canonical, "MAX_MEDIUM_NODES", 524_288)
    value = np.float64(1)
    for _ in range(canonical.MAX_DEPTH + 1):
        value = [value]
    assert canonical.bounded_canonical_snapshot(value, allow_native_float64=True) is None
    assert (
        canonical.bounded_canonical_snapshot(
            {"x": np.float64(1)}, max_bytes=1, allow_native_float64=True
        )
        is None
    )
    cyclic = [np.float64(1)]
    cyclic.append(cyclic)
    assert canonical.bounded_canonical_snapshot(cyclic, allow_native_float64=True) is None
    assert (
        canonical.bounded_canonical_snapshot([(np.float64(1),)], allow_native_float64=True) is None
    )
