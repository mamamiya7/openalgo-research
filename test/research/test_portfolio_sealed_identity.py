"""One streaming traversal preserves sealed portfolio receipt bounds and IDs."""

import json
from copy import deepcopy

import pytest

from research import portfolio_execution_features as features
from research.portfolio import fingerprint
from research.stock_features import _json_size


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"a": 1},
        {"z": [None, True, False, "NIFTY"]},
        {"before": "भारत 📈", "later": "\ud800"},
        {"floats": [-0.0, 1e-200, 1e200, 1.2345678901234567]},
        {"nested": {"values": [{"symbol": "AAA", "close": 123.5}] * 5}},
    ],
)
def test_exact_canonical_digest_and_full_byte_boundary(payload):
    receipt = {**payload, "id": fingerprint(payload)}
    original = deepcopy(receipt)
    full = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert len(full) == len(raw) + 71 + bool(payload)
    assert features._sealed_identity(receipt, len(full)) is True
    assert features._sealed_identity(receipt, len(full) + 1) is True
    with pytest.raises(ValueError, match="bounded byte allowance"):
        features._sealed_identity(receipt, len(full) - 1)
    assert receipt == original


def test_tampered_payload_or_digest_is_not_accepted():
    payload = {"nested": {"values": [1, 2, 3]}}
    receipt = {**payload, "id": fingerprint(payload)}
    receipt["nested"]["values"][0] = 99
    assert features._sealed_identity(receipt, 10000) is False
    receipt["id"] = "0" * 64
    assert features._sealed_identity(receipt, 10000) is False


def test_large_receipt_uses_one_streaming_traversal_without_small_object_shortcut(monkeypatch):
    payload = {"observations": [{"symbol": f"S{i}", "close": i + 0.25} for i in range(5000)]}
    receipt = {**payload, "id": fingerprint(payload)}
    expected_size = _json_size(receipt, 1024**2)
    calls = []
    original = json.JSONEncoder.iterencode

    def counted(self, value, *args, **kwargs):
        calls.append(value)
        return original(self, value, *args, **kwargs)

    monkeypatch.setattr(json.JSONEncoder, "iterencode", counted)
    monkeypatch.setattr(features, "_json_size", lambda *a: pytest.fail("valid fast path rescanned"))
    monkeypatch.setattr(features, "fingerprint", lambda *a: pytest.fail("valid fast path rehashed"))
    assert features._sealed_identity(receipt, expected_size)
    assert len(calls) == 1
    assert "id" not in calls[0]
    assert calls[0]["observations"] is payload["observations"]


@pytest.mark.parametrize("item", [float("nan"), float("inf"), -float("inf"), object()])
def test_malformed_or_nonfinite_payloads_keep_original_error(item):
    receipt = {"value": item, "id": "0" * 64}
    with pytest.raises((TypeError, ValueError)) as original:
        _json_size(receipt, 10000)
    with pytest.raises(type(original.value)) as changed:
        features._sealed_identity(receipt, 10000)
    assert str(changed.value) == str(original.value)


@pytest.mark.parametrize("maximum", [10, 1000])
def test_bound_and_nonfinite_error_order_matches_original(maximum):
    receipt = {"a": "x" * 500, "z": float("nan"), "id": "0" * 64}
    with pytest.raises(ValueError) as original:
        _json_size(receipt, maximum)
    with pytest.raises(ValueError) as changed:
        features._sealed_identity(receipt, maximum)
    assert str(changed.value) == str(original.value)


def test_custom_dictionary_keeps_original_validation_path(monkeypatch):
    class Custom(dict):
        pass

    receipt = Custom({"data": {"price": 4}, "id": fingerprint({"data": {"price": 4}})})
    calls = []
    original = features._json_size

    def counted(value, maximum):
        calls.append(value)
        return original(value, maximum)

    monkeypatch.setattr(features, "_json_size", counted)
    assert features._sealed_identity(receipt, 10000)
    assert calls == [receipt]


@pytest.mark.parametrize("value", [None, [], {}, {"id": "invalid"}, {1: 2, "id": "0" * 64}])
def test_malformed_root_never_acquires_an_intact_identity(value):
    try:
        _json_size(value, 10000)
    except (TypeError, ValueError) as original:
        with pytest.raises(type(original)):
            features._sealed_identity(value, 10000)
    else:
        assert features._sealed_identity(value, 10000) is False


def test_cyclic_payload_keeps_original_rejection():
    receipt = {"id": "0" * 64}
    receipt["cycle"] = receipt
    with pytest.raises(ValueError, match="Circular reference"):
        features._sealed_identity(receipt, 10000)
