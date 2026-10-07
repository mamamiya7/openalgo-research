"""Auxiliary evidence is scoped, causal and never executable equity evidence."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from research.market_series import (
    VERSION,
    fingerprint,
    freeze_series,
    normalize_descriptor,
    plan_series,
    slice_series,
    validate_series,
)

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


def descriptor(**changes):
    return {
        "symbol": "NIFTY",
        "exchange": "NSE_INDEX",
        "interval": "D",
        "role": "benchmark",
        **changes,
    }


def calendar(days=None):
    days = list(days or DAYS)
    return {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }


def candle(day, close=101):
    return {
        "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
        "open": 100.0,
        "high": max(103.0, close),
        "low": min(99.0, close),
        "close": float(close),
    }


def evidence():
    return freeze_series(
        descriptor(), DAYS, calendar(), {day: candle(day, 101 + i) for i, day in enumerate(DAYS)}
    )


def test_named_roles_are_separate_from_executable_equity_and_idempotent():
    index = normalize_descriptor(descriptor(symbol=" nifty "))
    assert index["symbol"] == "NIFTY" and index["version"] == VERSION
    assert normalize_descriptor(index) == index
    stock = normalize_descriptor(
        descriptor(symbol="RELIANCE", exchange="NSE", role="feature_warmup")
    )
    assert fingerprint(stock) != fingerprint(index)
    assert "signals" not in plan_series(index, DAYS, calendar())


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "../secret"},
        {"exchange": "NSE"},
        {"exchange": "BSE_INDEX"},
        {"interval": "1m"},
        {"role": "execution"},
        {"version": "future"},
        {"arbitrary": True},
    ],
)
def test_unsupported_descriptors_fail_closed(changes):
    with pytest.raises(ValueError):
        normalize_descriptor(descriptor(**changes))


@pytest.mark.parametrize(
    "days", [[], DAYS[::-1], [DAYS[0], DAYS[0]], ["2026-02-30"], ["2027-01-01"]]
)
def test_plan_requires_bounded_actual_calendar_subset(days):
    with pytest.raises(ValueError):
        plan_series(descriptor(), days, calendar())


def test_large_warmup_has_independent_bounded_date_scope():
    days = [(datetime(2020, 1, 1) + timedelta(days=i)).date().isoformat() for i in range(3001)]
    with pytest.raises(ValueError, match="3000"):
        plan_series(descriptor(), days, calendar(days))
    with pytest.raises(ValueError, match="252"):
        slice_series(evidence(), DAYS[1], DAYS[-1], warmup_sessions=253)


def test_missing_prices_remain_absent_and_volume_is_not_invented():
    value = freeze_series(descriptor(), DAYS, calendar(), {DAYS[0]: candle(DAYS[0])})
    assert value["coverage"] == {
        "status": "missing",
        "required": 4,
        "available": 1,
        "missing_dates": DAYS[1:],
    }
    assert list(value["bars"]) == [DAYS[0]]
    assert value["provenance"]["unavailable_fields"] == ["volume", "oi"]
    assert value["available_at"][DAYS[0]] == DAYS[0] + "T15:30:00+05:30"
    assert validate_series(value) == value


def test_window_excludes_future_prices_from_content_and_identity():
    original = evidence()
    before = deepcopy(original)
    selected = slice_series(original, DAYS[1], DAYS[2], warmup_sessions=1)
    changed = freeze_series(
        descriptor(),
        DAYS,
        calendar(),
        {
            **original["bars"],
            DAYS[3]: candle(DAYS[3], 9999),
        },
    )
    assert original["id"] != changed["id"]
    assert slice_series(changed, DAYS[1], DAYS[2], warmup_sessions=1) == selected
    assert selected["required_dates"] == DAYS[:3]
    assert set(selected["bars"]) == set(selected["available_at"]) == set(DAYS[:3])
    assert original == before
    assert slice_series(original, DAYS[1], DAYS[2])["id"] != selected["id"]


def test_warmup_counts_scheduled_observations_without_filling_missing_prices():
    value = freeze_series(descriptor(), DAYS, calendar(), {day: candle(day) for day in DAYS[1:]})
    selected = slice_series(value, DAYS[1], DAYS[2], warmup_sessions=1)
    assert selected["coverage"]["missing_dates"] == [DAYS[0]]
    with pytest.raises(ValueError, match="insufficient"):
        slice_series(value, DAYS[0], DAYS[1], warmup_sessions=1)


@pytest.mark.parametrize(
    "change", ["price", "timestamp", "availability", "coverage", "fields", "extra_bar"]
)
def test_frozen_evidence_rejects_corruption(change):
    value = evidence()
    if change == "price":
        value["bars"][DAYS[0]]["close"] = 102
    elif change == "timestamp":
        value["bars"][DAYS[0]]["timestamp"] += 86400
    elif change == "availability":
        value["available_at"][DAYS[0]] = DAYS[0] + "T15:30:00"
    elif change == "coverage":
        value["coverage"]["missing_dates"] = [DAYS[0]]
    elif change == "fields":
        value["provenance"]["observed_fields"].append("volume")
    else:
        value["bars"]["2027-01-01"] = candle("2027-01-01")
    with pytest.raises(ValueError):
        validate_series(value)
