"""Provider-observed historical volume: an explicit simulation assumption.

This is deliberately separate from volume_admission's common-quantity-basis
attestation. It never turns retrospectively adjusted broker candles into
point-in-time corporate-action evidence or executable liquidity.
"""

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone

from research.canonical_json import bounded_default_json_bytes

VERSION = "research-native-volume-provenance-v1"
MODEL_VERSION = "research-provider-observed-participation-v1"
PROFILE = "fyers-nse-cash-equity-observed-shares-v1"
ADAPTER = "broker.fyers.api.data.BrokerData.get_history"
REVIEWED_ADAPTER_SHA256 = "0e38f1a3e96e04fe4159e753319837f7bc6db84880ed982564721d1c42daa3c2"
MAX_ROWS = 3000
MAX_BYTES = 2 * 1024**2
MAX_SHARES = 2**53 - 1
LOOKBACK = 20
IST = timezone(timedelta(hours=5, minutes=30))
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
LIMITATIONS = [
    "Historical participation is a simulation assumption in provider-returned coordinates.",
    "Not point-in-time corporate-action or executable-liquidity proof.",
    "Observed volume does not establish opening depth, spread or market impact.",
]


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _bounded(value):
    if bounded_default_json_bytes(value, max_bytes=MAX_BYTES) is not None:
        return
    total = 0
    for part in json.JSONEncoder(allow_nan=False).iterencode(value):
        total += len(part.encode())
        if total > MAX_BYTES:
            raise ValueError("Native volume evidence exceeds its byte bound")


def candle(row):
    """Canonical numeric identity, with no missing-field normalisation."""
    if not isinstance(row, dict) or type(row.get("timestamp")) is not int:
        raise ValueError("Volume identity requires an integer native timestamp")
    values = {key: row[key] for key in FIELDS}
    if any(type(values[key]) not in (int, float) for key in FIELDS[1:]):
        raise ValueError("Volume identity requires finite numeric fields")
    if any(not math.isfinite(values[key]) for key in FIELDS[1:]):
        raise ValueError("Volume identity requires finite numeric fields")
    values = {
        key: (int(values[key]) if key == "timestamp" else float(values[key])) for key in FIELDS
    }
    if (
        any(values[key] <= 0 for key in FIELDS[1:5])
        or not values["low"]
        <= min(values["open"], values["close"])
        <= max(values["open"], values["close"])
        <= values["high"]
        or values["volume"] < 0
    ):
        raise ValueError("Volume identity requires a valid OHLCV candle")
    return values


def capture_rows(symbol, original_rows, accepted, *, broker, adapter_sha256, captured_at):
    """Trusted native producer helper; not a user-supplied admission endpoint.

    Mask refers to the returned adapter field, not a claim about raw transport.
    Only the reviewed Fyers producer has a declared share-volume profile. Other
    brokers retain honest unknown-unit evidence and cannot activate this model.
    """
    if (
        not isinstance(symbol, str)
        or not re.fullmatch(r"[A-Z0-9][A-Z0-9 &._-]{0,99}", symbol)
        or not isinstance(broker, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", broker)
        or not isinstance(original_rows, list)
        or len(original_rows) > MAX_ROWS
        or not isinstance(accepted, dict)
        or len(accepted) > MAX_ROWS
    ):
        raise ValueError("Invalid or oversized native volume capture")
    _bounded(original_rows)
    stamp = datetime.fromisoformat(captured_at)
    if stamp.tzinfo is None:
        raise ValueError("Volume capture requires an aware observation time")
    originals, duplicates = {}, set()
    for row in original_rows:
        if not isinstance(row, dict) or type(row.get("timestamp")) is not int:
            continue
        key = row["timestamp"]
        if key in originals:
            duplicates.add(key)
        originals[key] = row
    profile = PROFILE if broker == "fyers" and adapter_sha256 == REVIEWED_ADAPTER_SHA256 else None
    result = {}
    for day, admitted in accepted.items():
        values = candle(admitted)
        if datetime.fromtimestamp(values["timestamp"], IST).date().isoformat() != day:
            raise ValueError("Native volume date differs from its candle")
        raw = originals.get(values["timestamp"])
        if raw is None or values["timestamp"] in duplicates:
            continue
        supplied = "volume" in raw and raw["volume"] is not None
        # Equality is checked against original values; normalised absent volume
        # may be retained as unknown, but never declared observed zero.
        try:
            original_values = candle(raw)
        except (KeyError, TypeError, ValueError, OverflowError):
            if supplied:
                continue
            original_values = candle({**raw, "volume": 0})
        if original_values != values:
            continue
        payload = {
            "version": VERSION,
            "instrument": {"symbol": symbol, "exchange": "NSE", "asset_class": "cash_equity"},
            "interval": "D",
            "session": day,
            "provider": broker,
            "adapter": f"broker.{broker}.api.data.BrokerData.get_history",
            "adapter_sha256": adapter_sha256,
            "reviewed_profile": profile,
            "candle": values,
            "candle_id": fingerprint(values),
            "volume_supplied": supplied,
            "volume_unit": "provider_returned_shares" if profile else "unknown",
            "basis": "provider_returned_historical_coordinates",
            "adjustment": "provider_adjusted_or_unspecified",
            "captured_at": stamp.isoformat(),
            "availability_assumption": "completed_native_session",
        }
        result[day] = {**payload, "id": fingerprint(payload)}
    _bounded(result)
    return result


def matches(record, row, symbol):
    try:
        payload = {key: value for key, value in record.items() if key != "id"}
        return (
            record["version"] == VERSION
            and record["id"] == fingerprint(payload)
            and record["instrument"]
            == {"symbol": symbol, "exchange": "NSE", "asset_class": "cash_equity"}
            and record["candle"] == candle(row)
            and record["candle_id"] == fingerprint(candle(row))
            and record["session"]
            == datetime.fromtimestamp(row["timestamp"], IST).date().isoformat()
            and record["interval"] == "D"
        )
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return False


def validate_observation(record, symbol, slot):
    """Validate integrity and reviewed profile, never external authenticity."""
    return (
        isinstance(record, dict)
        and record.get("session") == slot
        and matches(record, record.get("candle"), symbol)
        and record.get("reviewed_profile") == PROFILE
        and record.get("provider") == "fyers"
        and record.get("adapter") == ADAPTER
        and record.get("adapter_sha256") == REVIEWED_ADAPTER_SHA256
        and record.get("volume_supplied") is True
        and record.get("volume_unit") == "provider_returned_shares"
        and record.get("basis") == "provider_returned_historical_coordinates"
        and record.get("adjustment") == "provider_adjusted_or_unspecified"
        and record.get("availability_assumption") == "completed_native_session"
        and 0 <= record["candle"]["volume"] <= MAX_SHARES
        and int(record["candle"]["volume"]) == record["candle"]["volume"]
    )


def assess(records, calendar, entry_session, decision_at, *, symbol):
    """Seal a causal twenty-session provider-observed participation assumption.

    No current bar is consulted. Capture time is an audit time, not historical
    availability: the report explicitly assumes completed historical bars were
    available and does not assert point-in-time adjustments. Strict admission v1
    remains the stronger, separate contract.
    """

    def result(reason, window=None, mean=None):
        payload = {
            "version": MODEL_VERSION,
            "status": "unavailable" if reason else "modelled",
            "reason_codes": [reason] if reason else [],
            "window": window,
            "mean_daily_shares": mean,
            "basis": "provider_returned_historical_coordinates",
            "adjustment": "provider_adjusted_or_unspecified",
            "limitations": list(LIMITATIONS),
        }
        return {**payload, "id": fingerprint(payload)}

    try:
        from research.volume_admission import _hours

        if not isinstance(records, dict) or len(records) > MAX_ROWS:
            return result("volume_source_oversized_or_missing")
        _bounded(records)
        sessions = calendar["sessions"]
        if (
            not isinstance(sessions, list)
            or not 1 <= len(sessions) <= MAX_ROWS
            or sessions != sorted(set(sessions))
            or calendar.get("provenance", {}).get("calendar_basis") != "openalgo-market-calendar-v1"
            or calendar.get("provenance", {}).get("exchange", "NSE") != "NSE"
        ):
            return result("calendar_unqualified")
        decision = datetime.fromisoformat(decision_at)
        opening, closing = _hours(calendar, entry_session)
        if decision.utcoffset() != IST.utcoffset(None) or not opening <= decision <= closing:
            return result("entry_decision_outside_session")
        index = sessions.index(entry_session)
        if index < LOOKBACK:
            return result("insufficient_prior_sessions")
        days = sessions[index - LOOKBACK : index]
        observations = []
        for day in days:
            record = records.get(day)
            if not isinstance(record, dict) or not matches(record, record.get("candle"), symbol):
                return result("prior_volume_missing_or_changed")
            if (
                record.get("reviewed_profile") != PROFILE
                or record.get("provider") != "fyers"
                or record.get("adapter") != ADAPTER
                or record.get("adapter_sha256") != REVIEWED_ADAPTER_SHA256
                or record.get("volume_unit") != "provider_returned_shares"
            ):
                return result("volume_producer_profile_unqualified")
            if record.get("volume_supplied") is not True:
                return result("volume_supplied_field_unknown")
            if (
                record.get("basis") != "provider_returned_historical_coordinates"
                or record.get("adjustment") != "provider_adjusted_or_unspecified"
                or record.get("availability_assumption") != "completed_native_session"
            ):
                return result("volume_coordinate_basis_unqualified")
            volume = record["candle"]["volume"]
            if not 0 <= volume <= MAX_SHARES or int(volume) != volume:
                return result("volume_quantity_invalid")
            if _hours(calendar, day)[1] >= decision:
                return result("prior_session_not_completed")
            observations.append({"session": day, "volume": int(volume), "source_id": record["id"]})
        window = {
            "entry_session": entry_session,
            "decision_at": decision.isoformat(),
            "from": days[0],
            "to": days[-1],
            "sessions": days,
            "observations": LOOKBACK,
            "observations_id": fingerprint(observations),
            "available_at": _hours(calendar, days[-1])[1].isoformat(),
            "calendar_id": fingerprint(
                {day: calendar["session_hours"][day] for day in [*days, entry_session]}
            ),
        }
        window["id"] = fingerprint(window)
        return result(None, window, sum(row["volume"] for row in observations) / LOOKBACK)
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        AttributeError,
        OSError,
        RecursionError,
    ):
        return result("volume_source_or_calendar_malformed")


def assess_observed(records_by_slot, calendar, symbol, entry_session, decision_at):
    """Stable application interface for the explicit provider-observed MODEL."""
    safe_symbol = (
        symbol
        if isinstance(symbol, str) and re.fullmatch(r"[A-Z0-9][A-Z0-9 &._-]{0,99}", symbol)
        else None
    )
    assessed = assess(records_by_slot, calendar, entry_session, decision_at, symbol=safe_symbol)
    payload = {key: value for key, value in assessed.items() if key != "id"}
    payload.update(
        status="available" if assessed["status"] == "modelled" else "unavailable",
        symbol=safe_symbol,
        source_id=(assessed.get("window") or {}).get("observations_id"),
        qualification="historical_simulation_assumption_only",
    )
    return {**payload, "id": fingerprint(payload)}
