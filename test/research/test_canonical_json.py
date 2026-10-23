"""The bounded optimization must preserve existing evidence bytes exactly."""

import hashlib
import json
import math
from decimal import Decimal

import pytest

from research.canonical_json import (
    MAX_ASCII_BYTES,
    MAX_DEPTH,
    MAX_NODES,
    bounded_canonical_bytes,
    small_canonical_bytes,
)


def streamed(value):
    return b"".join(
        part.encode()
        for part in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value)
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        -(2**255),
        2**255,
        0.0,
        -0.0,
        1e-323,
        math.nextafter(0.0, 1.0),
        math.nextafter(float("inf"), 0.0),
        1e20,
        1e21,
        "",
        'quotes " slash \\ newline\n\t\b\f\r',
        "भारत café 世界 😀",
        "\ud800\udfff",
        {"z": [1, (True, None, -0.0)], "a": {"😀": "\ud800"}},
        ({"a": 1}, {"b": 2}),
        dict(zip(("\udfff", "😀", "a", "\ud800"), range(4), strict=True)),
    ],
)
def test_exact_bytes_hash_and_size(value):
    actual = small_canonical_bytes(value)
    expected = streamed(value)
    assert actual == expected
    assert len(actual) == len(expected)
    assert hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest()


def test_shared_children_are_not_cycles():
    shared = {"number": [1, 2, 3]}
    value = [shared, shared]
    assert small_canonical_bytes(value) == streamed(value)


@pytest.mark.parametrize("factory", [list, dict])
def test_cycles_return_to_original_encoder(factory):
    value = factory()
    if factory is list:
        value.append(value)
    else:
        value["self"] = value
    assert small_canonical_bytes(value) is None
    with pytest.raises(ValueError, match="Circular reference"):
        streamed(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), {"n": float("nan")}])
def test_nonfinite_returns_to_original_error(value):
    assert small_canonical_bytes(value) is None
    with pytest.raises(ValueError, match="Out of range"):
        streamed(value)


@pytest.mark.parametrize("value", [{1: "integer key"}, {None: "null key"}, {True: "bool key"}])
def test_nonstring_keys_fall_back_even_when_original_can_encode(value):
    assert small_canonical_bytes(value) is None
    assert streamed(value)


@pytest.mark.parametrize("value", [b"bytes", {1, 2}, Decimal("1.1"), object()])
def test_unsupported_values_return_to_original_error(value):
    assert small_canonical_bytes(value) is None
    with pytest.raises(TypeError):
        streamed(value)


def test_subclasses_are_rejected_without_invoking_custom_methods():
    class HostileList(list):
        def __iter__(self):
            raise AssertionError("custom iterator was invoked")

    class HostileDict(dict):
        def items(self):
            raise AssertionError("custom items was invoked")

    class HostileStr(str):
        def __len__(self):
            raise AssertionError("custom length was invoked")

    class HostileInt(int):
        def bit_length(self):
            raise AssertionError("custom bit_length was invoked")

    class HostileFloat(float):
        pass

    for value in (
        HostileList([1]),
        HostileDict(a=1),
        HostileStr("a"),
        HostileInt(1),
        HostileFloat(1),
    ):
        assert small_canonical_bytes(value) is None
        assert small_canonical_bytes({"nested": value}) is None
        assert bounded_canonical_bytes(value) is None
        assert bounded_canonical_bytes({"nested": value}) is None
    assert small_canonical_bytes({HostileStr("key"): 1}) is None
    assert bounded_canonical_bytes({HostileStr("key"): 1}) is None


def test_depth_boundary_is_bounded():
    value = None
    for _ in range(MAX_DEPTH):
        value = [value]
    assert small_canonical_bytes(value) == streamed(value)
    assert small_canonical_bytes([value]) is None


def test_node_boundary_is_bounded():
    value = [None] * (MAX_NODES - 1)
    assert small_canonical_bytes(value) == streamed(value)
    assert small_canonical_bytes(value + [None]) is None


def test_conservative_ascii_byte_bound():
    value = "😀" * ((MAX_ASCII_BYTES - 2) // 12)
    assert small_canonical_bytes(value) == streamed(value)
    assert len(streamed(value)) <= MAX_ASCII_BYTES
    assert small_canonical_bytes(value + "😀") is None
    assert small_canonical_bytes("a" * MAX_ASCII_BYTES) is None


def test_very_large_int_does_not_enter_encoder():
    assert small_canonical_bytes(1 << (MAX_ASCII_BYTES * 3)) is None


def test_c_encoder_receives_an_independent_bounded_tree(monkeypatch):
    value = {"a": [1, 2], "b": {"c": 3}}
    original_dumps = json.dumps

    def mutate_then_encode(copied, **kwargs):
        value["a"].extend(range(MAX_NODES * 2))
        value["b"]["new"] = "a" * (MAX_ASCII_BYTES * 2)
        value["new"] = value
        assert copied is not value
        assert copied["a"] is not value["a"]
        assert copied["b"] is not value["b"]
        return original_dumps(copied, **kwargs)

    monkeypatch.setattr(json, "dumps", mutate_then_encode)
    assert small_canonical_bytes(value) == b'{"a":[1,2],"b":{"c":3}}'


def test_rapidly_mutated_dict_falls_back(monkeypatch):
    # An interruption between items can invalidate the dict iterator. The
    # optional optimization must release its bounded partial copy and fall back.
    original_isfinite = math.isfinite
    value = {"first": 1.0, "second": 2.0}

    def mutate(number):
        value["third"] = 3.0
        return original_isfinite(number)

    monkeypatch.setattr(math, "isfinite", mutate)
    assert small_canonical_bytes(value) is None


def medium_evidence():
    return {
        "trials": [
            {"number": index, "score": index / 7, "label": "भारत 😀", "params": (True, None)}
            for index in range(1500)
        ]
    }


def test_medium_tree_has_exact_original_bytes_and_keeps_small_limits():
    value = medium_evidence()
    expected = streamed(value)
    assert len(expected) > MAX_ASCII_BYTES
    assert small_canonical_bytes(value) is None
    assert bounded_canonical_bytes(value) == expected


def test_medium_encoder_owns_a_detached_tree(monkeypatch):
    value = medium_evidence()
    expected = streamed(value)
    original = json.dumps

    def mutate_then_encode(copied, **kwargs):
        value["trials"].append(value)
        value["trials"][0]["label"] = "changed"
        assert copied is not value
        assert copied["trials"][0] is not value["trials"][0]
        return original(copied, **kwargs)

    monkeypatch.setattr(json, "dumps", mutate_then_encode)
    assert bounded_canonical_bytes(value) == expected


@pytest.mark.parametrize("bound", [0, 1, 32, 1024])
def test_medium_byte_budget_is_checked_before_encoding(monkeypatch, bound):
    def forbidden(*args, **kwargs):
        raise AssertionError("over-budget tree entered the C encoder")

    monkeypatch.setattr(json, "dumps", forbidden)
    assert bounded_canonical_bytes(medium_evidence(), max_bytes=bound) is None


def test_medium_node_and_depth_budgets_remain_hard_bounds(monkeypatch):
    from research import canonical_json

    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_NODES", 32)
    assert bounded_canonical_bytes([None] * 31) == streamed([None] * 31)
    assert bounded_canonical_bytes([None] * 32) is None
    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_NODES", 524_288)
    value = None
    for _ in range(MAX_DEPTH + 1):
        value = [value]
    assert bounded_canonical_bytes(value) is None


def test_medium_byte_cap_cannot_be_raised_by_a_caller(monkeypatch):
    from research import canonical_json

    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_ASCII_BYTES", 64)
    assert bounded_canonical_bytes("😀" * 6, max_bytes=1024**3) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "key"}, Decimal("1.1")])
def test_medium_unusual_inputs_fall_back(value):
    assert bounded_canonical_bytes(value) is None


def test_medium_cycle_falls_back():
    value = [medium_evidence()]
    value.append(value)
    assert bounded_canonical_bytes(value) is None


def test_plain_json_mode_does_not_collapse_tuple_and_list_schemas():
    value = {"dates": ["2026-10-22"], "nested": {"n": 1.0}}
    assert bounded_canonical_bytes(value, json_types_only=True) == streamed(value)
    tuple_value = {**value, "dates": tuple(value["dates"])}
    assert bounded_canonical_bytes(tuple_value) == streamed(value)
    assert bounded_canonical_bytes(tuple_value, json_types_only=True) is None


def test_checkpoint_snapshot_and_bytes_own_the_same_detached_plain_tree():
    from research.canonical_json import bounded_canonical_snapshot

    value = {"returns": [0.0, -0.0, 1e-323], "params": {"label": "भारत 😀", "n": 7}}
    original = streamed(value)
    snapshot, raw = bounded_canonical_snapshot(value)
    assert raw == original == streamed(snapshot)
    assert snapshot is not value and snapshot["params"] is not value["params"]
    value["returns"].clear()
    value["params"]["n"] = 9
    assert streamed(snapshot) == original
    snapshot["params"]["n"] = 8
    assert value["params"]["n"] == 9


@pytest.mark.parametrize("value", [(1, 2), {"a": (1, 2)}, float("nan"), {1: "key"}])
def test_checkpoint_snapshot_leaves_non_plain_schema_and_errors_to_original_path(value):
    from research.canonical_json import bounded_canonical_snapshot

    assert bounded_canonical_snapshot(value) is None


def test_snapshot_byte_node_and_depth_bounds_are_the_existing_medium_limits(monkeypatch):
    from research import canonical_json

    assert canonical_json.bounded_canonical_snapshot({"a": [1]}, max_bytes=1) is None
    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_NODES", 3)
    assert canonical_json.bounded_canonical_snapshot({"a": [1]}) is None
    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_NODES", 524_288)
    deep = None
    for _ in range(MAX_DEPTH + 1):
        deep = [deep]
    assert canonical_json.bounded_canonical_snapshot(deep) is None
    cycle = []
    cycle.append(cycle)
    assert canonical_json.bounded_canonical_snapshot(cycle) is None


def test_snapshot_hash_is_of_its_owned_tree_when_the_caller_mutates_during_encoding(monkeypatch):
    from research.canonical_json import bounded_canonical_snapshot

    value = {"rows": [{"number": i} for i in range(20)]}
    expected = streamed(value)
    original = json.dumps

    def change_source(copied, **kwargs):
        value["rows"].append(value)
        return original(copied, **kwargs)

    monkeypatch.setattr(json, "dumps", change_source)
    snapshot, raw = bounded_canonical_snapshot(value)
    assert raw == expected
    assert len(snapshot["rows"]) == 20


def test_medium_size_and_optuna_digest_preserve_exact_values_and_error_precedence():
    from research.connectors.optuna_portfolio import _fingerprint
    from research.stock_features import _json_size

    value = medium_evidence()
    expected = streamed(value)
    assert _json_size(value, 32 * 1024**2) == len(expected)
    assert _fingerprint(value) == hashlib.sha256(expected).hexdigest()
    assert _fingerprint(value, max_bytes=len(expected)) == hashlib.sha256(expected).hexdigest()
    for invalid in (value, {"a": float("nan")}):
        with pytest.raises(ValueError, match="bounded byte allowance"):
            _json_size(invalid, 2)
        with pytest.raises(ValueError, match="storage limit"):
            _fingerprint(invalid, max_bytes=2)
    with pytest.raises(ValueError, match="Out of range"):
        _json_size({"a": float("nan")}, 1024)
    with pytest.raises(ValueError, match="Out of range"):
        _fingerprint({"a": float("nan")}, max_bytes=1024)


@pytest.mark.parametrize("value", [
    {"z": [1, True, None, {"a": "unicode \u0905 \U0001f600"}], "a": -0.0},
    [None] * 100,
    {"": {"": 1, "later": (2, 3)}},
])
def test_default_format_keeps_exact_spacing_order_and_ownership(value, monkeypatch):
    from research.canonical_json import bounded_default_json_bytes
    expected = json.dumps(value, allow_nan=False).encode()
    original = json.dumps
    def encode(detached, **kwargs):
        if isinstance(value, (dict, list)):
            assert detached is not value
        return original(detached, **kwargs)
    monkeypatch.setattr(json, "dumps", encode)
    assert bounded_default_json_bytes(value, max_bytes=64000) == expected


def test_default_format_checks_whitespace_budget_before_encoding(monkeypatch):
    from research.canonical_json import bounded_default_json_bytes
    def forbidden(*args, **kwargs):
        raise AssertionError("over-budget default JSON entered C encoder")
    monkeypatch.setattr(json, "dumps", forbidden)
    assert bounded_default_json_bytes([0] * 20, max_bytes=42) is None


@pytest.mark.parametrize("value", [float("nan"), {1: "nonstring"}, Decimal("2"), object()])
def test_default_format_unusual_inputs_still_fall_back(value):
    from research.canonical_json import bounded_default_json_bytes
    assert bounded_default_json_bytes(value, max_bytes=64000) is None
