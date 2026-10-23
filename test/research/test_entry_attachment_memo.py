"""Primitive identity reuse never trusts mutable signal identity or ordering."""

from copy import deepcopy

from research import execution_features as features


def test_repeated_identity_hashes_are_reused_and_duplicate_counters_remain_fresh(monkeypatch):
    signal = {"symbol": "AAA", "date": "2026-01-05", "row": 2}
    signals = [signal, deepcopy(signal), {**signal, "row": 3}]
    expected = list(features._opportunity_keys(signals))
    original, calls, memo = features.fingerprint, [], {}

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(features, "fingerprint", counted)
    assert list(features._opportunity_keys(signals, memo)) == expected
    assert list(features._opportunity_keys(signals, memo)) == expected
    assert len(calls) == 2
    assert expected[0][0].endswith(":0") and expected[1][0].endswith(":1")
    signals[0]["row"] = 4
    changed = list(features._opportunity_keys(signals, memo))
    assert changed == list(features._opportunity_keys(signals))
    assert changed[0][0] != expected[0][0]


def test_cache_bound_falls_back_without_rejecting_additional_identities(monkeypatch):
    monkeypatch.setattr(features, "MAX_SIGNALS", 1)
    signals = [{"symbol": "AAA", "date": "2026-01-05", "row": row} for row in (2, 3)]
    expected = list(features._opportunity_keys(signals))
    memo = {}
    assert list(features._opportunity_keys(signals, memo)) == expected
    assert list(features._opportunity_keys(signals, memo)) == expected
    assert len(memo) == 1


def test_unusual_identity_values_keep_original_hash_semantics():
    signals = [
        {"symbol": "AAA", "date": "2026-01-05", "row": True},
        {"symbol": "AAA", "date": "2026-01-05", "row": 1.0},
        {"symbol": "AAA", "date": "2026-01-05", "row": 2**100},
    ]
    memo = {}
    assert list(features._opportunity_keys(signals, memo)) == list(
        features._opportunity_keys(signals)
    )
    assert memo == {}
