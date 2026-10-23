"""Exact byte-limit/identity admission across fast and strict bundle paths."""

import json
from copy import deepcopy

import pytest
from test_stock_features import bundle, record

from research import stock_features as features


@pytest.mark.parametrize("fallback", [False, True])
def test_exact_bundle_byte_boundary_and_sha_remain_enforced(monkeypatch, fallback):
    value = bundle(record(count=5))
    size = len(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    if fallback:
        monkeypatch.setattr(features, "bounded_canonical_bytes", lambda *a, **kw: None)
        monkeypatch.setattr(features, "bounded_canonical_fragments", lambda *a, **kw: None)
    monkeypatch.setattr(features, "MAX_BUNDLE_BYTES", size)
    assert features.validate_bundle(value) is value
    changed = deepcopy(value)
    changed["id"] = "0" * 64
    with pytest.raises(ValueError, match="bundle changed"):
        features.validate_bundle(changed)
    monkeypatch.setattr(features, "MAX_BUNDLE_BYTES", size - 1)
    with pytest.raises(ValueError, match="bounded byte allowance"):
        features.validate_bundle(value)


def test_custom_bundle_keeps_strict_schema_and_hash_path():
    class Bundle(dict):
        pass

    value = Bundle(bundle(record(count=5)))
    assert features.validate_bundle(value) is value
    value["series"]["AAA"]["evidence"]["bars"]["2025-01-01"]["close"] += 0.5
    with pytest.raises(ValueError, match="evidence changed"):
        features.validate_bundle(value)


def test_identifier_changed_during_encoding_is_rejected(monkeypatch):
    value = bundle(record(count=5))
    original = features.bounded_canonical_bytes

    def encoding(payload, **kwargs):
        result = original(payload, **kwargs)
        if payload.get("version") == features.VERSION and "series" in payload:
            value["id"] = "0" * 64
        return result

    monkeypatch.setattr(features, "bounded_canonical_bytes", encoding)
    with pytest.raises(ValueError, match="bundle changed"):
        features.validate_bundle(value)
