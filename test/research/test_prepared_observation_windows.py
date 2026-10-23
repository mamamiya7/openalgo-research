"""A private causal window is shared across hypotheses without changing SHA."""

from datetime import datetime

import pytest
from test_prepared_stock_feature_reuse import hypothesis, private_cache, views
from test_stock_market_context import bundle

from research import stock_features as stock
from research import stock_indicators as indicators
from research import stock_market_context as context
from research import stock_relative_strength as relative
from research.portfolio import fingerprint


def test_threshold_and_indicator_family_share_one_exact_private_window():
    source, evidence = bundle()
    projected = context.stock_view(source)
    identifiers = ("rsi_14_gt50", "rsi_14_gt60", "atr_14_pct_lt2")
    expected = {key: relative.apply(evidence, projected, hypothesis(key)) for key in identifiers}
    with views(source) as owner:
        cache, _ = private_cache(owner)
        first_windows = None
        for key in identifiers:
            result = owner.apply_stock(evidence, hypothesis(key))
            assert result == expected[key]
            assert fingerprint(result) == fingerprint(expected[key])
            windows = {
                key: id(value[0]) for key, value in cache._PreparedFeatureCache__windows.items()
            }
            if first_windows is None:
                first_windows = windows
            else:
                assert windows == first_windows
        ids = [expected[key][1]["membership"][0]["feature"]["basis_id"] for key in identifiers]
        assert len(set(ids)) == 3
        assert len(cache._PreparedFeatureCache__entries) == 3
    assert stock._observation_cache.get() is None
    assert not cache._PreparedFeatureCache__windows
    assert not cache._PreparedFeatureCache__records


def test_every_stock_catalogue_rule_retains_full_hypothesis_basis_bytes():
    source, evidence = bundle()
    projected = context.stock_view(source)
    with views(source) as owner:
        for rule in relative.catalog():
            expected = relative.apply(evidence, projected, rule)
            actual = owner.apply_stock(evidence, rule)
            assert actual == expected, rule["id"]
            assert fingerprint(actual) == fingerprint(expected), rule["id"]


def test_window_lru_and_byte_bound_return_fresh_identical_calculations(monkeypatch):
    source, evidence = bundle()
    monkeypatch.setattr(stock, "MAX_PREPARED_FEATURES", 0)
    monkeypatch.setattr(stock, "MAX_PREPARED_WINDOWS", 1)
    projected = context.stock_view(source)
    with views(source) as owner:
        cache, _ = private_cache(owner)
        for key in ("rsi_14_gt50", "momentum_5_gt0", "rsi_14_gt50"):
            assert owner.apply_stock(evidence, hypothesis(key)) == relative.apply(
                evidence, projected, hypothesis(key)
            )
            assert len(cache._PreparedFeatureCache__windows) == 1
        cache.clear()
        monkeypatch.setattr(stock, "MAX_PREPARED_WINDOW_BYTES", 1)
        assert owner.apply_stock(evidence, hypothesis()) == relative.apply(
            evidence, projected, hypothesis()
        )
        assert not cache._PreparedFeatureCache__windows
        assert cache._PreparedFeatureCache__window_bytes == 0


def test_public_mutable_record_always_reads_current_observations():
    source, _ = bundle()
    record = context.stock_view(source)["series"]["AAA"]
    day = record["calendar"]["sessions"][-1]
    at = datetime.fromisoformat(day + "T09:15:00+05:30")
    rule = hypothesis("rsi_14_gt50")
    first = indicators._feature(record, at, rule)
    prior = record["calendar"]["sessions"][-2]
    record["evidence"]["bars"][prior]["close"] += 7
    second = indicators._feature(record, at, rule)
    assert first[0]["basis_id"] != second[0]["basis_id"]
    assert stock._observation_cache.get() is None


def test_scope_exception_releases_new_window_and_existing_feature_state():
    source, evidence = bundle()
    with views(source) as owner:
        cache, _ = private_cache(owner)
        with pytest.raises(RuntimeError, match="cancelled"):
            with stock._prepared_feature_scope(cache):
                owner.apply_stock(evidence, hypothesis("rsi_14_gt50"))
                assert cache._PreparedFeatureCache__windows
                raise RuntimeError("cancelled")
        assert not cache._PreparedFeatureCache__windows
        assert not cache._PreparedFeatureCache__entries
        assert stock._observation_cache.get() is None
