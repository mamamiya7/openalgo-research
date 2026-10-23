"""Overlapping preparation shares work while retaining exact frozen receipts."""

from copy import deepcopy
from datetime import date, timedelta

import numpy as np
import pytest
from test_portfolio_correlation_reuse import original_correlation
from test_portfolio_execution_features import source
from test_volume_provenance import calendar, sources

from research import execution_features
from research import portfolio_execution_features as features
from research import volume_provenance as volume_features


def record(count=180):
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(count)]
    random = np.random.default_rng(19)
    closes = (100 * np.cumprod(1 + random.normal(0, 0.01, count))).tolist()
    return {
        "calendar": {
            "sessions": days,
            "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        },
        "evidence": {
            "bars": {
                day: {
                    "timestamp": i,
                    "open": value,
                    "high": value + 1,
                    "low": value - 1,
                    "close": value,
                }
                for i, (day, value) in enumerate(zip(days, closes, strict=True))
            },
            "available_at": {day: day + "T15:30:00+05:30" for day in days},
        },
    }


@pytest.mark.parametrize("problem", [None, "missing", "late", "constant", "overflow"])
def test_indexed_windows_match_original_every_date_and_seal(problem):
    value = record()
    days = value["calendar"]["sessions"]
    if problem == "missing":
        del value["evidence"]["bars"][days[85]]
    elif problem == "late":
        value["evidence"]["available_at"][days[85]] = days[110] + "T10:00:00+05:30"
    elif problem == "constant":
        for bar in value["evidence"]["bars"].values():
            bar["close"] = 100
    elif problem == "overflow":
        value["evidence"]["bars"][days[85]]["close"] = 1e-308
        value["evidence"]["bars"][days[86]]["close"] = 1e308
    before = deepcopy(value)
    windows = features._ReturnWindows(value)
    for day in days:
        assert windows.window(day) == features._returns(value, day)
    assert value == before


def test_canonical_bar_encoding_occurs_once_per_source_bar(monkeypatch):
    value = record()
    original = features.bounded_canonical_bytes
    calls = []

    def counted(item, **kwargs):
        calls.append(item)
        return original(item, **kwargs)

    monkeypatch.setattr(features, "bounded_canonical_bytes", counted)
    windows = features._ReturnWindows(value)
    for day in value["calendar"]["sessions"]:
        assert windows.window(day) == features._returns(value, day)
    # Twenty overlapping windows no longer serialize the same bar twenty times.
    assert len(calls) == len(value["calendar"]["sessions"])


def test_non_plain_bar_values_preserve_original_json_fallback():
    value = record()
    days = value["calendar"]["sessions"]
    value["evidence"]["bars"][days[90]]["extra"] = ("native", 7)
    windows = features._ReturnWindows(value)
    for day in days[91:153]:
        assert windows.window(day) == features._returns(value, day)


def test_ordered_pair_reuse_matches_original_without_symmetry_rounding(monkeypatch):
    random = np.random.default_rng(7)
    dates = [str(i) for i in range(61)]
    prepared = {}
    for i in range(15):
        returns = (random.normal(size=60) * 10 ** (i / 2 - 4)).tolist()
        prepared[str(i)] = {
            "dates": dates,
            "returns": returns,
            "available_at": "2026-10-21T15:30:00+05:30",
            "source_id": features.fingerprint(returns),
        }
    expected = {symbol: original_correlation(symbol, "2026-10-22", prepared) for symbol in prepared}
    original = np.corrcoef
    calls = []

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(np, "corrcoef", counted)
    inputs, pairs = features._correlation_inputs(prepared), {}
    for symbol in reversed(prepared):
        actual = features._correlation(
            symbol, "2026-10-22", {}, prepared, _inputs=inputs, _pairs=pairs
        )
        assert actual == expected[symbol]
    assert len(calls) == len(prepared) * (len(prepared) - 1) // 2


class OriginalWindows:
    def __init__(self, value):
        self.record = value
        self.sessions = value["calendar"]["sessions"]

    def window(self, day):
        return features._returns(self.record, day)


def test_complete_receipt_matches_original_derivation_and_rejects_changed_saved_source(monkeypatch):
    evidence, context, volume = source(symbols=("AAA", "BBB", "CCC", "DDD", "EEE"))
    evidence = execution_features.prepare(evidence, context)
    before = deepcopy((evidence, context, volume))
    actual = features.prepare(evidence, context, volume)
    with monkeypatch.context() as legacy:
        legacy.setattr(features, "_ReturnWindows", OriginalWindows)
        legacy.setattr(volume_features._ObservedVolume, "admit", lambda records: None)
        legacy.setattr(
            features,
            "_correlation",
            lambda symbol, day, series, prepared, **kwargs: original_correlation(
                symbol, day, prepared
            ),
        )
        expected = features.prepare(evidence, context, volume)
    assert actual == expected
    assert features.prepare(actual, context) == actual
    assert (evidence, context, volume) == before
    changed = deepcopy(actual)
    changed["portfolio_execution_features"]["counts"]["capacity_available"] += 1
    with pytest.raises(ValueError, match="do not reproduce their frozen source"):
        features.prepare(changed, context)


def test_next_preparation_reads_changed_bar_again():
    value = record()
    day = value["calendar"]["sessions"][100]
    original = features._ReturnWindows(value).window(day)
    value["evidence"]["bars"][value["calendar"]["sessions"][90]]["close"] += 1
    changed = features._ReturnWindows(value).window(day)
    assert changed == features._returns(value, day)
    assert original["source_id"] != changed["source_id"]


def observed_assessment(owner, records, market_calendar, index):
    days = market_calendar["sessions"]
    day = days[index]
    prior = days[index - 20 : index]
    bars = {key: row["candle"] for key, row in records.items()}
    return owner.assess(prior, bars, market_calendar, "AAA", day, day + "T09:15:00+05:30")


def test_volume_overlap_reuses_each_observation_validation_and_exact_result(monkeypatch):
    market_calendar, records = calendar(), sources()
    expected = []
    for index in range(20, 30):
        day = market_calendar["sessions"][index]
        prior = market_calendar["sessions"][index - 20 : index]
        expected.append(
            volume_features.assess_observed(
                {key: records[key] for key in prior},
                market_calendar,
                "AAA",
                day,
                day + "T09:15:00+05:30",
            )
        )
    calls = []
    original = volume_features.matches

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(volume_features, "matches", counted)
    owner = volume_features._ObservedVolume.admit({"AAA": records})
    assert owner is not None
    for index, result in zip(range(20, 30), expected, strict=True):
        assert observed_assessment(owner, records, market_calendar, index) == result
    assert len(calls) == 29  # Ten windows retain all checks without 200 repeated seals.
    released = owner.release()
    assert released == {"AAA": records}
    released["AAA"][market_calendar["sessions"][0]]["id"] = "changed"
    with pytest.raises(ValueError, match="have been released"):
        observed_assessment(owner, records, market_calendar, 20)


@pytest.mark.parametrize(
    "problem", ["identity", "profile", "quantity", "calendar", "hours", "oversized"]
)
def test_prepared_volume_keeps_malformed_and_calendar_rejections_exact(problem):
    market_calendar, records = calendar(), sources()
    day = market_calendar["sessions"][20]
    if problem == "identity":
        records[market_calendar["sessions"][4]]["id"] = "changed"
    elif problem == "profile":
        item = records[market_calendar["sessions"][4]]
        item["reviewed_profile"] = "unreviewed"
        item["id"] = volume_features.fingerprint(
            {key: val for key, val in item.items() if key != "id"}
        )
    elif problem == "quantity":
        records[market_calendar["sessions"][4]]["candle"]["volume"] = 1.5
    elif problem == "calendar":
        market_calendar["provenance"]["calendar_basis"] = "unqualified"
    elif problem == "hours":
        market_calendar["session_hours"][day]["open"] = "10:00"
    elif problem == "oversized":
        records[market_calendar["sessions"][4]]["padding"] = "x" * (2 * 1024**2)
    prior = market_calendar["sessions"][:20]
    expected = volume_features.assess_observed(
        {key: records[key] for key in prior},
        market_calendar,
        "AAA",
        day,
        day + "T09:15:00+05:30",
    )
    assert expected["status"] == "unavailable"
    owner = volume_features._ObservedVolume.admit({"AAA": records})
    assert owner is not None
    assert observed_assessment(owner, records, market_calendar, 20) == expected


def test_volume_source_is_detached_and_next_preparation_validates_changed_content():
    market_calendar, records = calendar(), sources()
    owner = volume_features._ObservedVolume.admit({"AAA": records})
    initial = observed_assessment(owner, records, market_calendar, 20)
    records[market_calendar["sessions"][4]]["id"] = "changed"
    assert observed_assessment(owner, records, market_calendar, 20) == initial
    assert owner.release()["AAA"] != records
    changed = volume_features._ObservedVolume.admit({"AAA": records})
    result = observed_assessment(changed, records, market_calendar, 20)
    assert result["status"] == "unavailable"
    assert result["reason_codes"] == ["prior_volume_missing_or_changed"]


def test_volume_window_rechecks_calendar_after_observation_seals_are_shared():
    market_calendar, records = calendar(), sources()
    owner = volume_features._ObservedVolume.admit({"AAA": records})
    assert observed_assessment(owner, records, market_calendar, 20)["status"] == "available"
    market_calendar["provenance"]["calendar_basis"] = "changed"
    result = observed_assessment(owner, records, market_calendar, 20)
    assert result["status"] == "unavailable"
    assert result["reason_codes"] == ["calendar_unqualified"]


def test_large_scanner_detaches_per_symbol_within_aggregate_bound():
    row = next(iter(sources().values()))
    records = {
        f"STOCK{symbol:03}": {f"slot{day}": row for day in range(80)} for symbol in range(146)
    }
    # The combined tree exceeds the optional encoder's 524,288-node fast path.
    assert volume_features.bounded_canonical_snapshot(records, max_bytes=64 * 1024**2) is None
    owner = volume_features._ObservedVolume.admit(records)
    assert owner is not None
    detached = owner.release()
    assert detached == records
    row["id"] = "changed"
    assert detached != records
