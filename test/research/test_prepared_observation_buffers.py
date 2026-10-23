"""Canonical daily members are encoded once across overlapping causal windows."""

import json
from copy import deepcopy
from datetime import datetime

import pytest
from test_prepared_stock_feature_reuse import private_cache, views
from test_stock_features import record
from test_stock_market_context import bundle

from research import stock_features as stock


def test_each_daily_member_is_encoded_once_across_window_counts_and_dates(monkeypatch):
    source, _ = bundle()
    encoded, original = [], stock.bounded_canonical_bytes

    def count(value, **kwargs):
        if type(value) is dict and set(value) == {"available_at", "bar", "date"}:
            encoded.append(value["date"])
        return original(value, **kwargs)

    monkeypatch.setattr(stock, "bounded_canonical_bytes", count)
    monkeypatch.setattr(stock, "MAX_PREPARED_WINDOWS", 0)  # force overlapping window misses
    with views(source) as owner:
        cache, projected = private_cache(owner)
        selected = projected["series"]["AAA"]
        dates, series = selected["calendar"]["sessions"], selected["evidence"]
        for day in dates[-3:]:
            at = datetime.fromisoformat(day + "T09:15:00+05:30")
            for count in (6, 20, 21, 50, 60, 61, 100):
                actual = cache.observation(selected, at, count)
                required = dates[dates.index(day) - count : dates.index(day)]
                assert actual["problem"] is None
                assert (
                    actual["observations"]
                    == json.dumps(
                        [
                            {
                                "date": prior,
                                "bar": series["bars"][prior],
                                "available_at": series["available_at"][prior],
                            }
                            for prior in required
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                )
                for field in ("close", "high", "low"):
                    assert actual[field + "s"] == tuple(
                        series["bars"][prior][field] for prior in required
                    )
        assert encoded == dates  # serialization is per record day, never per window
        assert len(cache._PreparedFeatureCache__buffers) == 1
        assert not cache._PreparedFeatureCache__windows
        assert cache._PreparedFeatureCache__buffer_bytes <= stock.MAX_PREPARED_RECORD_BUFFER_BYTES
    assert not cache._PreparedFeatureCache__buffers
    assert cache._PreparedFeatureCache__buffer_bytes == 0


@pytest.mark.parametrize("first", ["missing", "late"])
def test_buffer_preserves_first_causal_problem_in_chronological_order(first):
    selected = record()
    dates, series = selected["calendar"]["sessions"], selected["evidence"]
    decision = datetime.fromisoformat(dates[120] + "T09:15:00+05:30")
    missing, late = (117, 118) if first == "missing" else (118, 117)
    del series["bars"][dates[missing]]
    series["available_at"][dates[late]] = dates[120] + "T10:00:00+05:30"
    rule = next(row for row in stock.catalog() if row["id"] == "momentum_5_gt0")
    expected = stock._feature(selected, decision, rule)
    cache = stock._PreparedFeatureCache({"series": {"AAA": selected}})
    try:
        window = cache.observation(selected, decision, 6)
        assert window["problem"] == (first, dates[min(missing, late)])
        assert (None, stock._window_problem(window, "feature")) == expected
    finally:
        cache.close()


def test_malformed_future_member_declines_buffer_without_changing_requested_window():
    selected = record()
    dates, series = selected["calendar"]["sessions"], selected["evidence"]
    decision = datetime.fromisoformat(dates[120] + "T09:15:00+05:30")
    series["available_at"][dates[-1]] = "not a timestamp"
    assert stock._record_observation_buffer(selected) is None
    cache = stock._PreparedFeatureCache({"series": {"AAA": selected}})
    try:
        actual = cache.observation(selected, decision, 6)
        assert actual["problem"] is None
        assert cache._PreparedFeatureCache__buffers[id(selected)][0] is None
        required = dates[114:120]
        assert (
            actual["observations"]
            == json.dumps(
                [
                    {
                        "date": day,
                        "bar": series["bars"][day],
                        "available_at": series["available_at"][day],
                    }
                    for day in required
                ],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        )
    finally:
        cache.close()


def test_buffer_count_byte_bounds_and_exception_cleanup(monkeypatch):
    first, second = record(), deepcopy(record())
    cache = stock._PreparedFeatureCache({"series": {"AAA": first, "BBB": second}})
    day = first["calendar"]["sessions"][-1]
    decision = datetime.fromisoformat(day + "T09:15:00+05:30")
    monkeypatch.setattr(stock, "MAX_PREPARED_RECORD_BUFFERS", 1)
    monkeypatch.setattr(stock, "MAX_PREPARED_WINDOWS", 0)
    try:
        before = cache.observation(first, decision, 100)
        cache.observation(second, decision, 100)
        assert id(first) not in cache._PreparedFeatureCache__buffers
        assert len(cache._PreparedFeatureCache__buffers) == 1
        assert cache.observation(first, decision, 100) == before
        cache.clear()
        monkeypatch.setattr(stock, "MAX_PREPARED_RECORD_BUFFER_BYTES", 1)
        assert cache.observation(first, decision, 100) == before
        assert not cache._PreparedFeatureCache__buffers
        monkeypatch.setattr(stock, "MAX_PREPARED_RECORD_BUFFER_BYTES", 64 * 1024**2)
        with pytest.raises(RuntimeError, match="cancelled"):
            with stock._prepared_feature_scope(cache):
                cache.observation(first, decision, 100)
                assert cache._PreparedFeatureCache__buffers
                raise RuntimeError("cancelled")
        assert not cache._PreparedFeatureCache__buffers
        assert cache._PreparedFeatureCache__buffer_bytes == 0
        cache.observation(first, decision, 100)
        cache.disable()
        assert not cache._PreparedFeatureCache__buffers
        assert cache.observation(first, decision, 100) is None
    finally:
        cache.close()
