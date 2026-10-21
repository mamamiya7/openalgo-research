"""Pure admission of explicitly attested daily share-volume observations.

This is a separate evidence boundary, not a downloader or capacity model. Native
legacy candles cannot establish units, actual supplied fields or a compatible
quantity basis. A caller must supply a retained, hash-bound receipt and a reviewed
attestation; sealing a dictionary is integrity checking, not independent proof
that a provider's semantic claims are true. No source is upgraded implicitly.
"""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from re import fullmatch

VERSION = "research-volume-admission-v1"
SOURCE_VERSION = "research-volume-source-v1"
RECEIPT_VERSION = "research-volume-observations-v1"
ATTESTATION_VERSION = "research-volume-basis-attestation-v1"
LOOKBACK = 20
MAX_ROWS = 3000
MAX_BYTES = 2 * 1024**2
MAX_SHARES = 2**53 - 1
IST = timezone(timedelta(hours=5, minutes=30))


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _bounded(value):
    size = 0
    for part in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(
        value
    ):
        size += len(part.encode())
        if size > MAX_BYTES:
            raise ValueError("Volume admission exceeds its byte bound")


def _digest(value):
    return isinstance(value, str) and fullmatch(r"[0-9a-f]{64}", value) is not None


def _text(value, maximum=160):
    return isinstance(value, str) and 1 <= len(value) <= maximum and value == value.strip()


def _day(value):
    return isinstance(value, str) and date.fromisoformat(value).isoformat() == value


def _aware(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(hours=5, minutes=30):
        raise ValueError("Volume timestamps need explicit IST offsets")
    return result


def _hours(calendar, day):
    hours = calendar["session_hours"][day]
    if set(hours) != {"open", "close"} or any(
        not isinstance(hours[key], str) or not fullmatch(r"\d{2}:\d{2}", hours[key])
        for key in ("open", "close")
    ):
        raise ValueError("Volume admission needs recorded session hours")
    opening = _aware(day + "T" + hours["open"] + ":00+05:30")
    closing = _aware(day + "T" + hours["close"] + ":00+05:30")
    if opening >= closing:
        raise ValueError("Volume admission needs ordered session hours")
    return opening, closing


def _calendar_id(calendar):
    return _fingerprint(
        {
            "sessions": calendar["sessions"],
            "session_hours": {
                day: calendar["session_hours"].get(day) for day in calendar["sessions"]
            },
            "calendar_basis": "openalgo-market-calendar-v1",
            "exchange": "NSE",
        }
    )


def _result(codes, *, window=None, source=None, mean=None):
    payload = {
        "version": VERSION,
        "status": "unavailable" if codes else "qualified",
        "reason_codes": sorted(set(codes)),
        "window": window,
        "source": source,
        "mean_daily_shares": mean,
        "qualification_basis": "declared_producer_contract_only",
        "limitations": [
            "Producer attestation is required; content hashes do not authenticate the provider or verify its unit claims.",
            "Admission supports a historical volume assumption, not executable liquidity, spread, depth or impact.",
        ],
    }
    return {**payload, "id": _fingerprint(payload)}


def assess(source, calendar, entry_session, decision_at):
    """Assess exactly twenty prior expected sessions, with no current-bar volume.

    ``source`` has version, receipt, receipt_sha256 and attestation. A receipt has
    version, instrument (symbol/exchange/asset_class), provider (name/adapter),
    interval, native OHLCV rows, per-date boolean ``volume_supplied`` and per-date
    ``available_at``. The attestation binds the receipt, shares unit, compatible
    raw/raw or split_adjusted/split_adjusted bases, recorded calendar identity,
    quantity-comparable date range,
    a reviewed basis_evidence_id and attested_by. The reviewed basis evidence must
    establish a common share quantity basis throughout that range, including the
    entry date, with explicit compatible_quantity_basis=True and the exact
    instrument_id and adapter. These claims must never be inferred merely from
    numeric candles or matching basis labels. Receipt ownership and reviewed
    profile provenance are the caller's trusted internal responsibility; this
    function is not an endpoint for admitting arbitrary user declarations.

    Invalid, unknown or incompatible evidence returns unavailable. The output is
    sealed and bounded; it contains only the causal window, not original rows.
    ``window.id`` ignores later rows; the full result also binds the source receipt.
    """
    try:
        if (
            not isinstance(calendar, dict)
            or not isinstance(calendar.get("sessions"), list)
            or not 1 <= len(calendar["sessions"]) <= MAX_ROWS
            or not isinstance(calendar.get("session_hours"), dict)
            or len(calendar["session_hours"]) > MAX_ROWS
            or any(not _day(day) for day in calendar["sessions"])
            or calendar["sessions"] != sorted(set(calendar["sessions"]))
            or calendar.get("provenance", {}).get("calendar_basis") != "openalgo-market-calendar-v1"
            or calendar.get("provenance", {}).get("exchange", "NSE") != "NSE"
            or not _day(entry_session)
            or entry_session not in calendar["sessions"]
        ):
            return _result(["calendar_unqualified"])
        decision = _aware(decision_at)
        opening, closing = _hours(calendar, entry_session)
        if not opening <= decision <= closing:
            return _result(["entry_decision_outside_session"])
        index = calendar["sessions"].index(entry_session)
        if index < LOOKBACK:
            return _result(["insufficient_prior_sessions"])
        dates = calendar["sessions"][index - LOOKBACK : index]
        closes = {day: _hours(calendar, day)[1] for day in dates}
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return _result(["calendar_or_decision_malformed"])

    try:
        if (
            not isinstance(source, dict)
            or set(source) != {"version", "receipt", "receipt_sha256", "attestation"}
            or source["version"] != SOURCE_VERSION
        ):
            return _result(["source_contract_unqualified"])
        receipt = source["receipt"]
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {
                "version",
                "instrument",
                "provider",
                "interval",
                "rows",
                "volume_supplied",
                "available_at",
            }
            or receipt["version"] != RECEIPT_VERSION
            or not isinstance(receipt["rows"], list)
            or len(receipt["rows"]) > MAX_ROWS
            or not isinstance(receipt["volume_supplied"], dict)
            or len(receipt["volume_supplied"]) > MAX_ROWS
            or not isinstance(receipt["available_at"], dict)
            or len(receipt["available_at"]) > MAX_ROWS
        ):
            return _result(["receipt_contract_unqualified"])
        _bounded(source)
        if not _digest(source["receipt_sha256"]) or source["receipt_sha256"] != _fingerprint(
            receipt
        ):
            return _result(["receipt_identity_mismatch"])
        instrument = receipt["instrument"]
        if (
            not isinstance(instrument, dict)
            or set(instrument) != {"symbol", "exchange", "asset_class"}
            or not _text(instrument["symbol"], 100)
            or not fullmatch(r"[A-Z0-9][A-Z0-9 &._-]{0,99}", instrument["symbol"])
            or instrument["exchange"] != "NSE"
            or instrument["asset_class"] != "cash_equity"
            or receipt["interval"] != "D"
        ):
            return _result(["instrument_or_interval_unqualified"])
        provider = receipt["provider"]
        if (
            not isinstance(provider, dict)
            or set(provider) != {"name", "adapter"}
            or not all(_text(provider[key]) for key in ("name", "adapter"))
            or any(
                provider[key].lower() in {"unknown", "historify", "openalgo historify"}
                for key in provider
            )
        ):
            return _result(["volume_source_unknown"])
        attestation = source["attestation"]
        if (
            not isinstance(attestation, dict)
            or set(attestation)
            != {
                "version",
                "receipt_sha256",
                "volume_unit",
                "price_adjustment_basis",
                "quantity_adjustment_basis",
                "quantity_comparable_from",
                "quantity_comparable_through",
                "basis_evidence_id",
                "attested_by",
                "calendar_id",
                "instrument_id",
                "adapter",
                "compatible_quantity_basis",
            }
            or attestation["version"] != ATTESTATION_VERSION
            or attestation["receipt_sha256"] != source["receipt_sha256"]
            or not _digest(attestation["basis_evidence_id"])
            or not _text(attestation["attested_by"])
            or attestation["instrument_id"] != _fingerprint(instrument)
            or attestation["adapter"] != provider["adapter"]
        ):
            return _result(["basis_attestation_unqualified"])
        if attestation["calendar_id"] != _calendar_id(calendar):
            return _result(["calendar_identity_mismatch"])
        codes = []
        if attestation["volume_unit"] != "shares":
            codes.append("volume_unit_unqualified")
        if (
            attestation["compatible_quantity_basis"] is not True
            or attestation["price_adjustment_basis"] not in {"raw", "split_adjusted"}
            or attestation["quantity_adjustment_basis"] != attestation["price_adjustment_basis"]
            or not _day(attestation["quantity_comparable_from"])
            or not _day(attestation["quantity_comparable_through"])
            or not attestation["quantity_comparable_from"] <= dates[0]
            or not entry_session <= attestation["quantity_comparable_through"]
        ):
            codes.append("quantity_basis_unqualified")
        if codes:
            return _result(codes)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError, RecursionError):
        return _result(["source_malformed_or_oversized"])

    wanted = set(dates)
    observed = {}
    try:
        for row in receipt["rows"]:
            if not isinstance(row, dict) or type(row.get("timestamp")) is not int:
                return _result(["observation_timestamp_invalid"])
            day = datetime.fromtimestamp(row["timestamp"], IST).date().isoformat()
            if day not in wanted:
                continue
            if day in observed:
                return _result(["duplicate_prior_observation"])
            if set(row) != {"timestamp", "open", "high", "low", "close", "volume"}:
                return _result(["observation_fields_invalid"])
            if receipt["volume_supplied"].get(day) is not True:
                return _result(["volume_supplied_field_unknown"])
            volume = row["volume"]
            if (
                type(volume) not in (int, float)
                or not isfinite(volume)
                or not 0 <= volume <= MAX_SHARES
                or int(volume) != volume
            ):
                return _result(["volume_quantity_invalid"])
            if (
                any(
                    type(row[key]) not in (int, float) or not isfinite(row[key]) or row[key] <= 0
                    for key in ("open", "high", "low", "close")
                )
                or not row["low"]
                <= min(row["open"], row["close"])
                <= max(row["open"], row["close"])
                <= row["high"]
            ):
                return _result(["observation_ohlc_invalid"])
            available = _aware(receipt["available_at"][day])
            if available < closes[day] or available > decision:
                return _result(["completed_observation_unavailable"])
            observed[day] = {
                "session": day,
                "timestamp": row["timestamp"],
                "volume": int(volume),
                "available_at": available.isoformat(),
            }
    except (KeyError, TypeError, ValueError, OverflowError, OSError, AttributeError):
        return _result(["observation_malformed"])
    if set(observed) != wanted:
        return _result(["missing_prior_session"])
    values = [observed[day] for day in dates]
    window = {
        "entry_session": entry_session,
        "decision_at": decision.isoformat(),
        "from": dates[0],
        "to": dates[-1],
        "observations": LOOKBACK,
        "sessions": dates,
        "observations_id": _fingerprint(values),
        "calendar_id": _fingerprint(
            {day: calendar["session_hours"][day] for day in [*dates, entry_session]}
        ),
        "available_at": max(row["available_at"] for row in values),
    }
    window["id"] = _fingerprint(window)
    identity = {
        "instrument": dict(instrument),
        "provider": dict(provider),
        "receipt_sha256": source["receipt_sha256"],
        "attestation_id": _fingerprint(attestation),
    }
    return _result(
        [], window=window, source=identity, mean=sum(row["volume"] for row in values) / LOOKBACK
    )


def explain_legacy_snapshot(snapshot_or_receipt):
    """Describe legacy limitations without walking or retaining candle arrays.

    Native snapshots and old normalized receipts have no explicit supplied-field
    mask, share-unit attestation or compatible quantity basis. Even positive
    volume is not a proof of these semantics. This helper intentionally cannot
    return qualified or calculate an average.
    """
    codes = [
        "volume_supplied_field_unknown",
        "volume_unit_unqualified",
        "quantity_basis_unqualified",
    ]
    if not isinstance(snapshot_or_receipt, dict):
        return _result(["source_contract_unqualified", *codes])
    provenance = snapshot_or_receipt.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    unavailable = provenance.get("unavailable_fields")
    if (
        isinstance(unavailable, (list, tuple))
        and len(unavailable) <= 32
        and "volume" in unavailable
    ):
        codes.append("volume_not_retained")
    broker = snapshot_or_receipt.get("broker")
    if not _text(broker, 80) or broker.lower() == "unknown":
        codes.append("volume_source_unknown")
    return _result(codes)
