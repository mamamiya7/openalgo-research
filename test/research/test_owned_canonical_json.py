"""Private immutable entry JSON preserves exact bytes and bounded captures."""

import json

import pytest

from research import canonical_json as canonical
from research.owned_entry_features import _capture, _OwnedDict, owned_json_bounds


def test_owned_capture_does_not_mint_trust_for_non_json_or_custom_keys():
    class CustomString(str):
        pass

    for key in (1, True, None, CustomString("pretend")):
        with pytest.raises(ValueError, match="JSON object keys"):
            _capture({"nested": {key: "value"}})


@pytest.mark.parametrize(
    "value",
    [
        {"name": "NSE", "weights": {"AAA": 0.0, "BBB": 1}, "rows": [True, None, -0.0]},
        {"unicode": "₹中😀", "nested": [[{"escaped": '\n\t\\"'}]]},
        {"correlations": {str(i): i / 100 for i in range(146)}},
    ],
)
def test_owned_subtree_keeps_exact_canonical_and_default_bytes(value):
    frozen = _capture(value)
    source = {"feature": frozen, "fresh": {"capital": 100000}}
    expected = json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "ascii"
    )
    assert canonical.bounded_canonical_bytes(source) == expected
    assert canonical.bounded_default_json_bytes(source, max_bytes=1024 * 1024) == json.dumps(
        source
    ).encode("ascii")
    captured, raw = canonical.bounded_canonical_snapshot(source)
    assert raw == expected and captured == source
    assert type(captured["feature"]) is dict
    assert type(captured["feature"].get("rows", [])) is list
    captured["feature"]["changed"] = True
    assert "changed" not in frozen


def test_owned_bounds_enforce_byte_node_and_depth_limits():
    frozen = _capture({"label": "😀" * 100, "items": [{"value": 1}]})
    nodes, depth, bound = owned_json_bounds(frozen)
    raw = json.dumps(frozen, ensure_ascii=True).encode("ascii")
    assert bound >= len(raw)
    assert canonical._canonical_bytes(frozen, nodes - 1, bound) is None
    assert canonical._canonical_bytes(frozen, nodes, bound - 1) is None
    assert canonical._canonical_bytes(frozen, nodes, bound, _maximum_depth=depth - 1) is None
    assert canonical._canonical_bytes(frozen, nodes, bound) is not None


def test_unregistered_custom_subclass_cannot_claim_owned_encoder_admission():
    class CallerDict(dict):
        _bounds = (1, 0, 0)

    assert owned_json_bounds(CallerDict()) is None
    assert owned_json_bounds(_OwnedDict()) is None
    assert canonical.bounded_canonical_bytes(CallerDict(feature="untrusted")) is None


def test_owned_subtree_can_split_when_one_piece_exceeds_its_allowance(monkeypatch):
    frozen = _capture({"AAA": {"returns": [0.1, 0.2]}, "BBB": {"returns": [-0.1, 0.0]}})
    original = canonical._canonical_bytes

    def limited(value, *args, **kwargs):
        return None if value is frozen else original(value, *args, **kwargs)

    monkeypatch.setattr(canonical, "_canonical_bytes", limited)
    fragments = canonical.bounded_canonical_fragments(frozen, max_bytes=4096)
    assert fragments is not None
    assert b"".join(fragments) == json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
