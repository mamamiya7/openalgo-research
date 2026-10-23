"""Large native receipts keep exact seals without Python token streaming."""

import hashlib
import json
from copy import deepcopy
from io import StringIO

import pytest

from research import canonical_json
from research import portfolio_execution_features as features


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def sealed(payload):
    return {**payload, "id": hashlib.sha256(encoded(payload)).hexdigest()}


@pytest.fixture(scope="module")
def many_records():
    payload = {
        "version": features.VERSION,
        "capacity": {
            f"STOCK{index % 146:03}|entry{index:05}": {
                "feature": {"observations": 20, "mean_daily_shares": 1000 + index},
                "assessment": {
                    "status": "available",
                    "window": [
                        {"session": f"session{day:03}", "volume": 1000 + day, "close": 101.25}
                        for day in range(20)
                    ],
                    "source_id": f"{index:064x}",
                },
            }
            for index in range(4000)
        },
    }
    raw = encoded(payload)
    receipt = {**payload, "id": hashlib.sha256(raw).hexdigest()}
    return receipt, raw


def test_node_heavy_receipt_uses_fragmented_exact_seal_and_capture(many_records, monkeypatch):
    receipt, raw = many_records
    payload = {key: value for key, value in receipt.items() if key != "id"}
    assert canonical_json.bounded_canonical_bytes(payload, max_bytes=features.MAX_BYTES) is None
    original = json.JSONEncoder.iterencode

    def require_c_encoder(self, value, _one_shot=False):
        if not _one_shot:
            pytest.fail("large plain receipt fell back to Python token streaming")
        return original(self, value, _one_shot=True)

    monkeypatch.setattr(json.JSONEncoder, "iterencode", require_c_encoder)
    monkeypatch.setattr(features, "_json_size", lambda *args: pytest.fail("seal rescanned"))
    monkeypatch.setattr(features, "fingerprint", lambda *args: pytest.fail("seal rehashed"))
    with StringIO() as capture:
        assert features._sealed_identity(receipt, len(raw) + 72, _capture=capture)
        assert capture.getvalue().encode("ascii") == raw


def test_large_receipt_late_mutation_is_rejected(many_records):
    receipt, _ = many_records
    changed = deepcopy(receipt)
    last = next(reversed(changed["capacity"]))
    changed["capacity"][last]["assessment"]["window"][-1]["close"] += 1
    assert features._sealed_identity(changed, features.MAX_BYTES) is False


def test_payload_over_medium_byte_ceiling_keeps_complete_bound_and_digest():
    payload = {
        "volume_records": {
            f"STOCK{index:03}": {"native_receipts": "x" * (1024 * 1024), "volume": index}
            for index in range(34)
        }
    }
    raw = encoded(payload)
    assert len(raw) > canonical_json.MAX_MEDIUM_ASCII_BYTES
    receipt = {**payload, "id": hashlib.sha256(raw).hexdigest()}
    assert features._sealed_identity(receipt, len(raw) + 72)
    with pytest.raises(ValueError, match="bounded byte allowance"):
        features._sealed_identity(receipt, len(raw) + 71)


@pytest.mark.parametrize("maximum", [100, 10000])
@pytest.mark.parametrize("bad", [float("nan"), object()])
def test_fragment_failure_keeps_original_error_precedence(maximum, bad):
    receipt = {"a": "x" * 500, "z": bad, "id": "0" * 64}
    with pytest.raises((TypeError, ValueError)) as expected:
        features._json_size(receipt, maximum)
    with pytest.raises(type(expected.value)) as actual:
        features._sealed_identity(receipt, maximum)
    assert str(actual.value) == str(expected.value)


def test_custom_container_and_tuple_keep_original_path(monkeypatch):
    class Custom(dict):
        pass

    for payload in ({"source": Custom(value=1)}, {"source": (1, 2)}):
        receipt = sealed(payload)
        assert features._bounded_sealed_identity(receipt, 10000) is None
        assert features._sealed_identity(receipt, 10000)
