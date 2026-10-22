"""Saved identities and evidence limits remain exact across both encoder paths."""

import hashlib
import json

import pytest

from research.portfolio import fingerprint
from research.stock_features import _json_size


@pytest.mark.parametrize(
    "value",
    [
        {"receipt": {"symbol": "भारत 😀", "values": [0.0, -0.0, 1e-323]}},
        {"bars": [[i, 1.25, 1.5, 1.0, 1.2] for i in range(5000)]},
        {1: "existing integer key"},
    ],
)
def test_saved_identity_and_byte_limits_match_original_stream(value):
    raw = b"".join(
        part.encode()
        for part in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value)
    )
    assert fingerprint(value) == hashlib.sha256(raw).hexdigest()
    assert _json_size(value, len(raw)) == len(raw)
    with pytest.raises(ValueError, match="bounded byte allowance"):
        _json_size(value, len(raw) - 1)


def test_large_invalid_tail_preserves_limit_error_before_encoding_error():
    value = ["x" * 100_000, float("nan")]
    with pytest.raises(ValueError, match="bounded byte allowance"):
        _json_size(value, 10)
    with pytest.raises(ValueError, match="Out of range"):
        fingerprint(value)


def test_invalid_values_keep_original_error_behavior():
    cyclic = []
    cyclic.append(cyclic)
    for value, error in ((cyclic, ValueError), (object(), TypeError), (float("nan"), ValueError)):
        with pytest.raises(error):
            fingerprint(value)
        with pytest.raises(error):
            _json_size(value, 1_000_000)
