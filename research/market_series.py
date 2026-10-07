"""Immutable auxiliary daily prices, separate from executable equity snapshots."""

import copy
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone

VERSION = "research-market-series-v1"
MAX_SESSIONS = 3000
IST = timezone(timedelta(hours=5, minutes=30))
FIELDS = ("open", "high", "low", "close")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def normalize_descriptor(value):
    if not isinstance(value, dict) or set(value) not in (
        {"symbol", "exchange", "interval", "role"},
        {"version", "symbol", "exchange", "interval", "role"},
    ):
        raise ValueError("Choose a named market series, exchange, interval and role")
    if value.get("version", VERSION) != VERSION:
        raise ValueError("Unsupported market-series version")
    symbol = value.get("symbol")
    if not isinstance(symbol, str) or not re.fullmatch(
        r"[A-Z0-9][A-Z0-9 &._-]{0,99}", symbol.strip().upper()
    ):
        raise ValueError("Choose a valid native market-series symbol")
    if (
        any(not isinstance(value[key], str) for key in ("exchange", "interval", "role"))
        or value.get("interval") != "D"
        or (value.get("exchange"), value.get("role"))
        not in {
            ("NSE_INDEX", "benchmark"),
            ("NSE", "feature_warmup"),
        }
    ):
        raise ValueError("Market series supports daily NSE index benchmarks or NSE stock warmup")
    return {"version": VERSION, **value, "symbol": symbol.strip().upper()}


def _dates(values):
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= MAX_SESSIONS
        or any(not isinstance(day, str) for day in values)
        or values != sorted(set(values))
    ):
        raise ValueError("Market series needs 1–3000 ordered unique session dates")
    try:
        if any(date.fromisoformat(day).isoformat() != day for day in values):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Market-series dates must be ISO dates") from exc
    return list(values)


def plan_series(descriptor, required_dates, calendar):
    descriptor = normalize_descriptor(descriptor)
    required_dates = _dates(required_dates)
    if not isinstance(calendar, dict):
        raise ValueError("Market series requires a recorded native calendar")
    sessions = _dates(calendar.get("sessions"))
    if (
        calendar.get("provenance", {}).get("calendar_basis") != "openalgo-market-calendar-v1"
        or calendar.get("provenance", {}).get("exchange", "NSE") != "NSE"
        or set(required_dates) - set(sessions)
    ):
        raise ValueError("Market series requires matching recorded NSE calendar sessions")
    for day in required_dates:
        _close_at(day, calendar.get("session_hours", {}).get(day))
    payload = {
        "version": VERSION,
        "descriptor": descriptor,
        "interval": "D",
        "required_dates": {descriptor["symbol"]: required_dates},
    }
    return {**payload, "request_key": fingerprint(payload)}


def _close_at(day, hours):
    try:
        opening = datetime.fromisoformat(day + "T" + hours["open"] + ":00+05:30")
        closing = datetime.fromisoformat(day + "T" + hours["close"] + ":00+05:30")
        if opening >= closing or closing.date().isoformat() != day:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Market series needs recorded session opening and closing times") from exc
    return closing.isoformat()


def _coverage(required_dates, bars):
    missing = [day for day in required_dates if day not in bars]
    return {
        "status": "missing" if missing else "complete",
        "required": len(required_dates),
        "available": len(bars),
        "missing_dates": missing,
    }


def freeze_series(descriptor, required_dates, calendar, bars):
    plan = plan_series(descriptor, required_dates, calendar)
    descriptor = plan["descriptor"]
    dates = plan["required_dates"][descriptor["symbol"]]
    payload = {
        "version": VERSION,
        "descriptor": descriptor,
        "required_dates": dates,
        "bars": copy.deepcopy(bars),
        "available_at": {day: _close_at(day, calendar["session_hours"][day]) for day in dates},
        "provenance": {
            "provider": "OpenAlgo Historify",
            "exchange": descriptor["exchange"],
            "interval": "D",
            "calendar_exchange": "NSE",
            "calendar_basis": "openalgo-market-calendar-v1",
            "native_price_policy": "openalgo-native-history-v1",
            "adjustment_basis": "provider-native",
            "return_basis": "provider-native prices; total-return status unverified",
            "timestamp_basis": "original native epoch seconds; completed daily candle available at recorded session close",
            "observed_fields": list(FIELDS),
            "unavailable_fields": ["volume", "oi"],
            "independent_verification": False,
            "synthetic": False,
        },
        "coverage": _coverage(dates, bars),
    }
    result = {**payload, "id": fingerprint(payload)}
    return validate_series(result)


def validate_series(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "version",
            "id",
            "descriptor",
            "required_dates",
            "bars",
            "available_at",
            "provenance",
            "coverage",
        }
        or value.get("version") != VERSION
    ):
        raise ValueError("Invalid frozen market-series evidence")
    descriptor = normalize_descriptor(value["descriptor"])
    dates = _dates(value["required_dates"])
    if (
        descriptor != value["descriptor"]
        or not isinstance(value["bars"], dict)
        or set(value["bars"]) - set(dates)
    ):
        raise ValueError("Market-series prices do not match their declared scope")
    if not isinstance(value["available_at"], dict) or set(value["available_at"]) != set(dates):
        raise ValueError("Market-series availability must cover the recorded sessions")
    for day in dates:
        try:
            available = datetime.fromisoformat(value["available_at"][day])
            if (
                available.utcoffset() != timedelta(hours=5, minutes=30)
                or available.date().isoformat() != day
            ):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("Market-series availability needs dated IST session closes") from exc
    for day, bar in value["bars"].items():
        if not isinstance(bar, dict) or set(bar) != {"timestamp", *FIELDS}:
            raise ValueError("Market series requires exact native OHLC and timestamps")
        stamp = bar["timestamp"]
        try:
            valid_stamp = (
                type(stamp) is int and datetime.fromtimestamp(stamp, IST).date().isoformat() == day
            )
        except (ValueError, OverflowError, OSError):
            valid_stamp = False
        if not valid_stamp:
            raise ValueError("Market-series timestamp does not identify its session")
        if any(
            type(bar[key]) not in (int, float) or not math.isfinite(bar[key]) or bar[key] <= 0
            for key in FIELDS
        ):
            raise ValueError("Market series needs finite positive OHLC prices")
        if (
            not bar["low"]
            <= min(bar["open"], bar["close"])
            <= max(bar["open"], bar["close"])
            <= bar["high"]
        ):
            raise ValueError("Market-series OHLC range is invalid")
    provenance = value["provenance"]
    if not isinstance(provenance, dict) or any(
        provenance.get(key) != expected
        for key, expected in {
            "exchange": descriptor["exchange"],
            "interval": "D",
            "calendar_exchange": "NSE",
            "calendar_basis": "openalgo-market-calendar-v1",
            "native_price_policy": "openalgo-native-history-v1",
            "adjustment_basis": "provider-native",
            "independent_verification": False,
            "synthetic": False,
            "observed_fields": list(FIELDS),
            "unavailable_fields": ["volume", "oi"],
        }.items()
    ):
        raise ValueError("Market series must retain native source and field provenance")
    if value["coverage"] != _coverage(dates, value["bars"]):
        raise ValueError("Market-series coverage does not match its prices")
    if value["id"] != fingerprint({key: item for key, item in value.items() if key != "id"}):
        raise ValueError("Frozen market-series evidence changed")
    return value


def slice_series(value, first, last, *, warmup_sessions=0):
    """Select a causal window; later prices and their identities cannot leak into it."""
    validate_series(value)
    _dates([first] if first == last else [first, last])
    if type(warmup_sessions) is not int or not 0 <= warmup_sessions <= 252:
        raise ValueError("Choose at most 252 warmup sessions")
    earlier = [day for day in value["required_dates"] if day < first]
    selected = [day for day in value["required_dates"] if first <= day <= last]
    if not selected or len(earlier) < warmup_sessions:
        raise ValueError("The recorded market series has insufficient window or warmup history")
    dates = earlier[-warmup_sessions:] + selected if warmup_sessions else selected
    result = copy.deepcopy(value)
    result["required_dates"] = dates
    result["bars"] = {
        day: copy.deepcopy(value["bars"][day]) for day in dates if day in value["bars"]
    }
    result["available_at"] = {day: value["available_at"][day] for day in dates}
    result["coverage"] = _coverage(dates, result["bars"])
    result["id"] = fingerprint({key: item for key, item in result.items() if key != "id"})
    return validate_series(result)
