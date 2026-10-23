"""Scoped immutable JSON byte reuse with ordinary mutable-input safeguards."""

import hashlib
import json
from unittest.mock import patch

import numpy as np
import pytest
from test_owned_entry_features import entry
from test_vectorbt_portfolio import snapshot

from research import canonical_json
from research.market_series import fingerprint as market_fingerprint
from research.owned_entry_features import _capture, admit_entry
from research.portfolio import fingerprint
from research.portfolio_execution_features import CORRELATION_VERSION
from research.prepared_calculation import prepared_calculation


def encoded(value, **kwargs):
    result = canonical_json.prepared_canonical_fragments(value, max_bytes=64 * 1024**2, **kwargs)
    return b"".join(result) if result is not None else None


def test_shared_features_encode_once_and_mutable_outer_fields_always_change_hash():
    owned = admit_entry(entry(CORRELATION_VERSION), CORRELATION_VERSION)
    value = {"rows": [{"feature": owned}, {"feature": owned}], "capital": 10000}
    original = json.dumps
    with prepared_calculation(snapshot()) as owner:
        with patch.object(canonical_json.json, "dumps", wraps=original) as dump:
            for capital in (10000, 20000):
                value["capital"] = capital
                expected = original(
                    value, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                assert encoded(value) == expected
                assert fingerprint(value) == hashlib.sha256(expected).hexdigest()
                assert market_fingerprint(value) == hashlib.sha256(expected).hexdigest()
            assert sum(call.args[0] is owned for call in dump.call_args_list) == 1
        assert len(owner._canonical_features) == 1
    assert not owner._canonical_features and owner._canonical_feature_bytes == 0


def test_generic_immutable_subtree_copied_encoded_once_with_fresh_mutable_outer():
    feature = admit_entry(entry(CORRELATION_VERSION), CORRELATION_VERSION)
    source = [{"symbol": f"S{i}", "value": i / 10, "feature": feature} for i in range(80)]
    source[0].update(label="₹😀\n", precise=[-0.0, 1e-7, 1.2e-200])
    immutable = _capture(source)
    original, original_dump = canonical_json.prepared_canonical_fragments, json.dumps
    calls = []

    def counted(value, **kwargs):
        if value is immutable and kwargs.get("_memoize_owned") is False:
            calls.append(value)
        return original(value, **kwargs)

    with prepared_calculation(snapshot()) as owner:
        with patch.object(canonical_json, "prepared_canonical_fragments", side_effect=counted):
            with patch.object(canonical_json.json, "dumps", wraps=original_dump) as dump:
                for capital in (10, 20, 30):
                    value = {"rows": immutable, "capital": capital}
                    expected = original_dump(value, sort_keys=True, separators=(",", ":")).encode()
                    assert encoded(value) == expected
                    assert fingerprint(value) == hashlib.sha256(expected).hexdigest()
                assert sum(call.args[0] is feature for call in dump.call_args_list) == 1
        assert len(calls) == 1
        assert id(immutable) in owner._canonical_features
        assert canonical_json.prepared_canonical_fragments(immutable, max_bytes=2) is None
        source[0]["value"] = 999
        assert encoded(source) != encoded(immutable)
    assert not owner._canonical_features and owner._canonical_feature_bytes == 0


def test_generic_owned_cache_bounds_failures_and_mutable_values_never_memoized():
    first = _capture(list(range(300)))
    second = _capture(list(range(301)))
    with prepared_calculation(snapshot()) as owner:
        with patch("research.prepared_calculation.MAX_CANONICAL_FEATURES", 1):
            assert encoded(first) == json.dumps(first, separators=(",", ":")).encode()
            assert encoded(second) == json.dumps(second, separators=(",", ":")).encode()
            assert list(owner._canonical_features) == [id(second)]
        before = owner._canonical_feature_bytes
        with patch("research.prepared_calculation.MAX_CANONICAL_FEATURE_BYTES", 1):
            assert encoded(first) is not None
            assert owner._canonical_feature_bytes == before
        assert encoded(_capture([float("inf")] * 300)) is None
        mutable, calls = list(range(300)), []

        def factory():
            calls.append(1)
            return b"[]"

        owner.memoize_canonical_json(mutable, factory)
        mutable[0] = 999
        owner.memoize_canonical_json(mutable, factory)
        assert len(calls) == 2 and id(mutable) not in owner._canonical_features


def test_generic_cache_ineligible_ancestor_keeps_financial_leaf_encoding(monkeypatch):
    feature = admit_entry(entry(CORRELATION_VERSION), CORRELATION_VERSION)
    immutable = _capture([{"row": i, "feature": feature} for i in range(80)])
    original = json.dumps
    # Scale the32MiB admission boundary down; the outer64MiB representation
    # remains eligible. This must skip caching, not reject Couter encoding.
    monkeypatch.setattr(canonical_json, "MAX_PREPARED_SUBTREE_BYTES", 1024)
    with prepared_calculation(snapshot()) as owner:
        with patch.object(canonical_json.json, "dumps", wraps=original) as dump:
            for capital in (10, 20):
                value = {"signals": immutable, "capital": capital}
                expected = original(value, sort_keys=True, separators=(",", ":")).encode()
                assert encoded(value) == expected
            assert sum(call.args[0] is feature for call in dump.call_args_list) == 1
        assert id(immutable) not in owner._canonical_features


def test_declined_subtree_factory_keeps_outer_child_path(monkeypatch):
    # Owned bounds use exact float representation; the generic bounded copier
    # reserves32 bytes per float. A narrow cache attempt can decline while the
    # original wider outer encoding remains valid.
    immutable = _capture([0.0] * 300)
    monkeypatch.setattr(canonical_json, "MAX_PREPARED_SUBTREE_BYTES", 6000)
    with prepared_calculation(snapshot()) as owner:
        assert encoded({"values": immutable}) == b'{"values":[' + b"0.0," * 299 + b"0.0]}"
        assert id(immutable) not in owner._canonical_features


@pytest.mark.parametrize(
    "value",
    [None, True, False, 17, -20, 1.2e-200, -0.0, "é😀\n", [1, 2], (1, 2), {"b": 3, "a": [4]}],
)
def test_supported_values_match_exact_default_canonical_bytes(value):
    with prepared_calculation(snapshot()):
        assert (
            encoded(value)
            == json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        )


def test_plain_mutation_bounds_cycles_and_custom_subclasses_never_gain_trust():
    class Pretender(dict):
        pass

    value = {"values": [1, 2]}
    with prepared_calculation(snapshot()):
        first = encoded(value)
        value["values"][0] = 91
        assert encoded(value) != first
        assert canonical_json.prepared_canonical_fragments(value, max_bytes=2) is None
        assert encoded(Pretender(value)) is None
        value["cycle"] = value
        assert encoded(value) is None
        assert encoded(float("nan")) is None


def test_feature_byte_cache_eviction_byte_budget_and_exception_cleanup():
    first = admit_entry(entry(CORRELATION_VERSION), CORRELATION_VERSION)
    other = entry(CORRELATION_VERSION)
    other["correlations"]["BBB"] = 0.1
    from research.portfolio import fingerprint as seal

    other["id"] = seal({key: value for key, value in other.items() if key != "id"})
    second = admit_entry(other, CORRELATION_VERSION)
    with pytest.raises(RuntimeError, match="stop"):
        with prepared_calculation(snapshot()) as owner:
            with patch("research.prepared_calculation.MAX_CANONICAL_FEATURES", 1):
                assert encoded(first) is not None
                assert encoded(second) is not None
                assert len(owner._canonical_features) == 1
            with patch("research.prepared_calculation.MAX_CANONICAL_FEATURE_BYTES", 1):
                assert encoded(first) is not None
                assert len(owner._canonical_features) == 1
            raise RuntimeError("stop")
    assert not owner._canonical_features and owner._canonical_feature_bytes == 0


def test_inactive_owner_has_no_fragment_or_cache_capability():
    assert encoded({"plain": 1}) is None


@pytest.mark.parametrize("value", [np.float64(1.25), np.float64(-0.0), np.float64(1e-200)])
def test_native_float64_requires_optin_and_retains_exact_canonical_bytes(value):
    with prepared_calculation(snapshot()):
        assert encoded(value) is None
        expected = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        assert encoded(value, allow_native_float64=True) == expected
        assert fingerprint(value) == hashlib.sha256(expected).hexdigest()
        assert market_fingerprint(value) == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize(
    "value", [np.float64(float("nan")), np.float64(float("inf")), np.float64(float("-inf"))]
)
def test_native_nonfinite_float64_never_receives_encoder_admission(value):
    with prepared_calculation(snapshot()):
        assert encoded(value) is None
        assert encoded(value, allow_native_float64=True) is None


def test_owned_feature_encoding_respects_conservative_preallocation_bound():
    owned = admit_entry(entry(CORRELATION_VERSION), CORRELATION_VERSION)
    from research.owned_entry_features import owned_json_bounds

    allowance = owned_json_bounds(owned)[2]
    with prepared_calculation(snapshot()):
        with patch.object(
            canonical_json.json, "dumps", side_effect=AssertionError("unbounded allocation")
        ):
            assert (
                canonical_json.prepared_canonical_fragments(owned, max_bytes=allowance - 1) is None
            )


@pytest.mark.parametrize(
    "number",
    [
        -0.0,
        1e-7,
        1e20,
        1e30,
        1.7976931348623157e308,
        2.2250738585072014e-308,
        np.float64(-0.0),
        np.float64(1e-7),
    ],
)
def test_c_outer_encoder_with_raw_features_retains_exact_unicode_float_and_integer_bytes(number):
    owned = admit_entry(entry(CORRELATION_VERSION), CORRELATION_VERSION)
    value = {"feature": owned, "value": number, "text": "₹😀\n", "integer": 2**100, "tuple": (1, 2)}
    expected = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    with prepared_calculation(snapshot()):
        assert encoded(value, allow_native_float64=True) == expected


def test_registered_signal_list_is_encoded_once_then_reused_with_fresh_metadata():
    from research.owned_entry_features import _capture

    feature = admit_entry(entry(CORRELATION_VERSION), CORRELATION_VERSION)
    owned = _capture([{"row": i, "feature": feature} for i in range(20)])
    original = canonical_json.prepared_canonical_fragments
    with prepared_calculation(snapshot()):
        with patch.object(canonical_json, "prepared_canonical_fragments", wraps=original) as encode:
            with canonical_json.prepared_signal_lists([owned]):
                for target in (4, 9):
                    value = {"signals": owned, "target": target}
                    expected = json.dumps(
                        value, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode()
                    assert encoded(value) == expected
                assert sum(call.args[0] is owned for call in encode.call_args_list) == 1
        assert canonical_json._REGISTERED_SIGNAL_LISTS.get() is None


def test_registered_lists_require_immutable_identity_have_bounds_and_close_on_error():
    from research.owned_entry_features import _capture

    owned = [_capture([{"row": index}]) for index in range(9)]
    mutable = [{"row": 1}]
    with prepared_calculation(snapshot()):
        with pytest.raises(RuntimeError, match="stop"):
            with canonical_json.prepared_signal_lists(owned):
                retained = canonical_json._REGISTERED_SIGNAL_LISTS.get()
                assert len(retained) == 8 and id(owned[-1]) not in retained
                assert sum(len(item[1]) for item in retained.values()) <= 64 * 1024**2
                raise RuntimeError("stop")
        assert not retained and canonical_json._REGISTERED_SIGNAL_LISTS.get() is None
        with canonical_json.prepared_signal_lists([mutable]):
            assert not canonical_json._REGISTERED_SIGNAL_LISTS.get()
            before = encoded({"signals": mutable})
            mutable[0]["row"] = 19
            assert encoded({"signals": mutable}) != before
        with canonical_json.prepared_signal_lists([owned[0]]):
            assert canonical_json.prepared_canonical_fragments(owned[0], max_bytes=1) is None


def test_immutable_signal_bytes_reuse_across_gate_scopes_only_within_market_owner():
    from research.owned_entry_features import _capture

    owned = _capture([{"row": 1}, {"row": 2}])
    original = canonical_json.prepared_canonical_fragments
    with patch.object(
        canonical_json, "prepared_canonical_fragments", wraps=original
    ) as encoded_list:
        for _ in range(2):
            with prepared_calculation(snapshot()) as market:
                for _ in range(2):
                    with canonical_json.prepared_signal_lists([owned]):
                        assert encoded({"signals": owned}) is not None
                assert len(market._canonical_lists) == 1
            assert not market._canonical_lists and market._canonical_list_bytes == 0
        assert sum(call.args[0] is owned for call in encoded_list.call_args_list) == 2


def test_conditioned_signal_bytes_reuse_after_gate_scope_without_new_cache_admission():
    owned = _capture([{"row": i, "label": "₹😀", "price": -0.0} for i in range(80)])
    original = canonical_json.prepared_canonical_fragments
    with prepared_calculation(snapshot()) as market:
        assert market.lookup_canonical_signal_list(owned) is None
        assert not market._canonical_lists
        with patch.object(canonical_json, "prepared_canonical_fragments", wraps=original) as encode:
            with canonical_json.prepared_signal_lists([owned]):
                assert encoded({"signals": owned, "target": 4}) is not None
            assert canonical_json._REGISTERED_SIGNAL_LISTS.get() is None
            for target in (4, 9):
                value = {"strategies": [{"signals": owned, "config": {"target": target}}]}
                expected = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                assert encoded(value) == expected
                assert fingerprint(value) == hashlib.sha256(expected).hexdigest()
            assert sum(call.args[0] is owned for call in encode.call_args_list) == 1
        assert id(owned) not in market._canonical_features
        assert len(market._canonical_lists) == 1
        assert market.lookup_canonical_signal_list(list(owned)) is None
        assert canonical_json.prepared_canonical_fragments(owned, max_bytes=1) is None
    assert not market._canonical_lists and market._canonical_list_bytes == 0
    with pytest.raises(RuntimeError, match="owner is closed"):
        market.lookup_canonical_signal_list(owned)


def test_market_signal_byte_cache_evicts_and_mutable_lists_never_gain_approval():
    from research.owned_entry_features import _capture

    first, second = _capture([{"row": 1}]), _capture([{"row": 2}])
    with prepared_calculation(snapshot()) as market:
        with patch("research.prepared_calculation.MAX_CANONICAL_LISTS", 1):
            for value in (first, second):
                with canonical_json.prepared_signal_lists([value]):
                    assert encoded(value) is not None
            assert len(market._canonical_lists) == 1 and id(first) not in market._canonical_lists
        with patch("research.prepared_calculation.MAX_CANONICAL_LIST_BYTES", 1):
            with canonical_json.prepared_signal_lists([first]):
                assert encoded(first) is not None
            assert id(first) not in market._canonical_lists
        mutable, calls = [{"row": 1}], []

        def encode_mutable():
            calls.append(1)
            return json.dumps(mutable).encode()

        previous = market.memoize_canonical_signal_list(mutable, encode_mutable)
        mutable[0]["row"] = 19
        assert market.memoize_canonical_signal_list(mutable, encode_mutable) != previous
        assert len(calls) == 2 and id(mutable) not in market._canonical_lists
