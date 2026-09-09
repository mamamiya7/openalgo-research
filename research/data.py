"""Checked immutable snapshot boundary. Never downloads or assumes archive provenance."""

import hashlib
import math
from datetime import UTC, date, timedelta

MAX_BARS = 2_000_000
NATIVE_PRICE_POLICY = "openalgo-native-history-v1"
NATIVE_CALENDAR_BASIS = "openalgo-market-calendar-v1"


def validate_snapshot(snapshot: dict, signals=None) -> dict:
    if snapshot.get("coverage", {}).get("status") == "blocked":
        raise ValueError(
            "Snapshot is blocked: " + "; ".join(snapshot["coverage"].get("warnings", []))
        )
    sessions = snapshot.get("sessions", [])
    if not sessions or len(sessions) > 3000 or sessions != sorted(set(sessions)):
        raise ValueError("Snapshot needs 1–3000 sorted unique exchange sessions")
    for day in sessions:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("Sessions must use ISO dates")
    provenance = snapshot.get("provenance", {})
    for key in ("provider", "exchange", "interval", "adjustment_basis", "calendar_basis"):
        if not provenance.get(key):
            raise ValueError(f"Missing snapshot provenance: {key}")
    if provenance["exchange"] != "NSE" or provenance["interval"] not in ("D", "1m"):
        raise ValueError("Only NSE daily or one-minute equity snapshots are supported")
    native_policy = provenance.get("native_price_policy")
    if native_policy is not None and native_policy != NATIVE_PRICE_POLICY:
        raise ValueError("Unsupported native historical price policy")
    if native_policy == NATIVE_PRICE_POLICY:
        if (
            provenance["adjustment_basis"] != "provider-native"
            or provenance["calendar_basis"] != NATIVE_CALENDAR_BASIS
            or provenance.get("independent_verification") is not False
            or provenance.get("identity_verified") is True
            or provenance.get("synthetic")
        ):
            raise ValueError("Native history must retain its provider basis and native calendar")
    elif not provenance.get("synthetic") and (
        provenance["adjustment_basis"] in ("unknown", "unverified")
        or not provenance.get("identity_verified")
        or not provenance.get("calendar_verified")
    ):
        raise ValueError(
            "Broker archive requires verified instrument identity, provider adjustment basis and exchange calendar"
        )
    minute = provenance["interval"] == "1m"
    if minute:
        from .intraday import validate_timeline

        validate_timeline(snapshot)
    bars = snapshot.get("bars", {})
    if sum(len(series) for series in bars.values()) > MAX_BARS:
        raise ValueError("Snapshot exceeds 2000000 bars")
    session_set = set(snapshot["timeline"] if minute else sessions)
    required_dates = snapshot.get("required_dates") if not minute else None
    if required_dates is not None:
        if not isinstance(required_dates, dict) or len(required_dates) > 25000:
            raise ValueError("Required daily dates must be a bounded symbol map")
        total_required = 0
        for symbol, days in required_dates.items():
            if (
                not isinstance(symbol, str)
                or not isinstance(days, list)
                or any(not isinstance(day, str) for day in days)
                or days != sorted(set(days))
                or set(days) - session_set
            ):
                raise ValueError("Required daily dates must be ordered unique recorded sessions")
            total_required += len(days)
        if total_required > MAX_BARS:
            raise ValueError("Required daily dates exceed the snapshot bound")
    for symbol, series in bars.items():
        for day, bar in series.items():
            if day not in session_set:
                raise ValueError(f"{symbol}: bar outside recorded sessions")
            try:
                opening, high, low, closing = [
                    float(bar[k]) for k in ("open", "high", "low", "close")
                ]
            except (TypeError, KeyError, ValueError) as exc:
                raise ValueError(f"{symbol} {day}: incomplete OHLC") from exc
            if (
                not all(math.isfinite(v) and v > 0 for v in (opening, high, low, closing))
                or not low <= min(opening, closing) <= max(opening, closing) <= high
            ):
                raise ValueError(f"{symbol} {day}: malformed OHLC")
    findings = []
    starts = {}
    for signal in signals or []:
        starts[signal["symbol"]] = min(starts.get(signal["symbol"], signal["date"]), signal["date"])
    quality = provenance.get("quality_findings", [])
    for symbol, start in sorted(starts.items()):
        available = bars.get(symbol, {})
        required_slots = (
            snapshot.get("required_timestamps", {}).get(symbol, snapshot.get("timeline", sessions))
            if minute
            else required_dates.get(symbol, [])
            if required_dates is not None
            else sessions
        )
        if minute and (not isinstance(required_slots, list) or set(required_slots) - session_set):
            raise ValueError("Required minute timestamps must belong to the recorded timeline")
        missing = [d for d in required_slots if d[:10] >= start and d not in available]
        first_bar, last_bar = (
            min(available, default=sessions[-1]),
            max(available, default=sessions[0]),
        )
        findings.append(
            {
                "symbol": symbol,
                "missing_sessions": missing,
                "available_bars": len(available),
                "required_from": start,
                "head_missing": [d for d in missing if not available or d < first_bar],
                "tail_missing": [d for d in missing if available and d > last_bar],
                "internal_missing": [
                    d for d in missing if available and first_bar <= d <= last_bar
                ],
                "quality_findings": [f for f in quality if f.get("symbol") == symbol],
            }
        )
    warnings = []
    if provenance.get("synthetic"):
        warnings.append(
            "Synthetic deterministic prices and weekday calendar; not downloaded NSE prices or a verified exchange calendar."
        )
    if any(f["missing_sessions"] for f in findings):
        warnings.append(
            "Missing candles can leave accepted positions pending; later candles cannot resolve unseen exits."
        )
    if quality:
        warnings.append(
            "Rejected, no-trade or quarantined observations are retained in the quality evidence; they cannot supply fills."
        )
    warnings.extend(provenance.get("limitations", []))
    if provenance.get("available_through"):
        warnings.append(
            f"Frozen prices end at {provenance['available_through']}; positions whose hold horizon extends beyond that date remain pending."
        )
    return {
        "status": "warning" if warnings else "ready",
        "warnings": warnings,
        "symbols": findings,
        "session_count": len(sessions),
        "date_from": sessions[0],
        "date_to": sessions[-1],
        "provenance": provenance,
    }


def fixture_snapshot(signals: list) -> dict:
    if not signals or len(signals) > 25000:
        raise ValueError("Supply 1–25000 normalized signals")
    start = date.fromisoformat(min(s["date"] for s in signals))
    warmup = []
    cursor = start - timedelta(days=1)
    while len(warmup) < 7:
        if cursor.weekday() < 5:
            warmup.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    end = date.fromisoformat(max(s["date"] for s in signals)) + timedelta(days=45)
    if (end - start).days > 3700:
        raise ValueError("Fixture supports at most ten years of signals")
    sessions = sorted(warmup)
    while start <= end:
        if start.weekday() < 5:
            sessions.append(start.isoformat())
        start += timedelta(days=1)
    starts = {}
    for signal in signals:
        starts[signal["symbol"]] = min(starts.get(signal["symbol"], signal["date"]), signal["date"])
    if sum(sum(day >= first for day in sessions) for first in starts.values()) > MAX_BARS:
        raise ValueError("Fixture exceeds 2000000 bars; use fewer symbols or a shorter window")
    bars = {}
    for symbol in sorted({s["symbol"] for s in signals}):
        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
        base = 80 + seed % 220
        series = {}
        for i, day in enumerate(sessions):
            if day < starts[symbol]:
                continue
            op = round(base * (1 + 0.0008 * i + 0.025 * math.sin(i / 3 + seed % 11)), 2)
            close = round(op * (1 + 0.009 * math.sin(i + seed % 7)), 2)
            series[day] = {
                "open": op,
                "high": round(max(op, close) * 1.012, 2),
                "low": round(min(op, close) * 0.988, 2),
                "close": close,
            }
        bars[symbol] = series
    snapshot = {
        "sessions": sessions,
        "bars": bars,
        "provenance": {
            "provider": "synthetic-fixture",
            "exchange": "NSE",
            "interval": "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "synthetic-weekdays",
            "synthetic": True,
            "generator_version": "scanner-fixture-v2",
            "warmup_sessions": 7,
        },
    }
    snapshot["coverage"] = validate_snapshot(snapshot, signals)
    return snapshot


def checked_historify_snapshot(signals, sessions, *, provenance, reader):
    """Use an injected native Historify reader, whose connection lifetime it owns.

    The archive schema has no provider/adjustment/ISIN lineage. Callers must
    supply independently verified provenance; archive candles alone cannot run.
    reader(symbol, exchange, interval, start, end) returns ISO-keyed OHLC.
    """
    snapshot = {"sessions": sessions, "provenance": provenance, "bars": {}}
    validate_snapshot(snapshot, signals)  # Reject unverifiable lineage before reading.
    for symbol in sorted({s["symbol"] for s in signals}):
        snapshot["bars"][symbol] = reader(symbol, "NSE", "D", sessions[0], sessions[-1])
    snapshot["coverage"] = validate_snapshot(snapshot, signals)
    return snapshot


def historify_snapshot(signals):
    """Inspect the native archive read-only; absent lineage blocks calculation.

    The existing market_data schema has no per-bar provider, adjustment basis
    or verified ISIN lineage. Even a full row range is not verified coverage.
    No credentials, live databases, history downloads or schema creation occur.
    An explicit isolated HISTORIFY_DATABASE_PATH is required for development.
    """
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    warnings = [
        "Historify candles do not retain verified provider, adjustment basis, instrument identity or exchange calendar. Calculation is blocked until those are established."
    ]
    provenance = {
        "provider": "historify-archive-unverified",
        "exchange": "NSE",
        "interval": "D",
        "adjustment_basis": "unknown",
        "calendar_basis": "unverified",
        "synthetic": False,
    }
    snapshot = {
        "sessions": [],
        "bars": {},
        "provenance": provenance,
        "coverage": {
            "status": "blocked",
            "warnings": warnings,
            "symbols": [],
            "provenance": provenance,
            "session_count": 0,
            "date_from": None,
            "date_to": None,
        },
    }
    configured = os.environ.get("HISTORIFY_DATABASE_PATH")
    if not configured:
        warnings.append(
            "No isolated Historify archive is configured. Set HISTORIFY_DATABASE_PATH to an isolated research archive."
        )
        return snapshot
    path = Path(configured)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    if not path.is_file():
        warnings.append("The configured Historify archive does not exist; no database was created.")
        return snapshot
    connection = None
    try:
        import duckdb

        # Native DuckDB convention, with guaranteed close on success or error.
        # read_only avoids schema changes and accidental archive creation.
        connection = duckdb.connect(
            str(path), read_only=True, config={"memory_limit": "128MB", "threads": "1"}
        )
        first = min(s["date"] for s in signals)
        last = (
            date.fromisoformat(max(s["date"] for s in signals)) + timedelta(days=45)
        ).isoformat()
        start_ts = int(datetime.fromisoformat(first).replace(tzinfo=UTC).timestamp()) - 86400
        end_ts = int(datetime.fromisoformat(last).replace(tzinfo=UTC).timestamp()) + 86400
        for symbol in sorted({s["symbol"] for s in signals}):
            count, minimum, maximum = connection.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM market_data WHERE symbol=? AND exchange='NSE' AND interval='D' AND timestamp BETWEEN ? AND ?",
                [symbol, start_ts, end_ts],
            ).fetchone()
            snapshot["coverage"]["symbols"].append(
                {
                    "symbol": symbol,
                    "available_bars": count,
                    "first_timestamp": minimum,
                    "last_timestamp": maximum,
                    "missing_sessions": [],
                    "coverage_verified": False,
                }
            )
        warnings.append(
            "Archive row counts and timestamp ranges are observations only; missing sessions remain unverified."
        )
    except Exception:
        warnings.append(
            "The isolated Historify archive could not be inspected read-only; it may be unavailable, incompatible or locked."
        )
    finally:
        if connection is not None:
            connection.close()
    return snapshot


def cutoff_snapshot(snapshot, cutoff):
    """Copy a strictly causal view, rebuilding adjusted prices at its cutoff.

    Quarantined rows stay excluded. Raw admitted records are replayed with only
    actions effective by cutoff, so future splits cannot alter whole-share sizing.
    Synthetic and unadjusted snapshots without raw_bars are simply truncated.
    """
    import copy

    date.fromisoformat(cutoff)
    result = {
        "sessions": [day for day in snapshot["sessions"] if day[:10] <= cutoff],
        "bars": {},
        "provenance": copy.deepcopy(snapshot["provenance"]),
    }
    if "timeline" in snapshot:
        result["timeline"] = [stamp for stamp in snapshot["timeline"] if stamp[:10] <= cutoff]
        result["session_hours"] = {
            day: value
            for day, value in snapshot.get("session_hours", {}).items()
            if day[:10] <= cutoff
        }
    if "required_timestamps" in snapshot:
        result["required_timestamps"] = {
            symbol: [stamp for stamp in slots if stamp[:10] <= cutoff]
            for symbol, slots in snapshot["required_timestamps"].items()
        }
    if "required_dates" in snapshot:
        result["required_dates"] = {
            symbol: [day for day in days if day <= cutoff]
            for symbol, days in snapshot["required_dates"].items()
        }
    provenance = result["provenance"]
    if snapshot.get("raw_bars"):
        result["raw_bars"] = {}
    actions = provenance.get("actions", {})
    for symbol, series in snapshot["bars"].items():
        result["bars"][symbol] = {}
        if "raw_bars" in result:
            result["raw_bars"][symbol] = {
                day: dict(bar)
                for day, bar in snapshot["raw_bars"].get(symbol, {}).items()
                if day[:10] <= cutoff
            }
        for day, bar in series.items():
            if day[:10] > cutoff:
                continue
            raw = snapshot.get("raw_bars", {}).get(symbol, {}).get(day)
            if raw:
                revised = dict(raw)
                for action in actions.get(symbol, []):
                    if day[:10] < action["ex_date"] <= cutoff:
                        revised = {
                            key: value * action["backward_price_factor"]
                            for key, value in revised.items()
                        }
            else:
                revised = dict(bar)
                for action in actions.get(symbol, []):
                    if day[:10] < action["ex_date"] and cutoff < action[
                        "ex_date"
                    ] <= provenance.get("actions_as_of", cutoff):
                        revised = {
                            key: value / action["backward_price_factor"]
                            for key, value in revised.items()
                        }
            result["bars"][symbol][day] = revised
    if actions:
        provenance["actions"] = {
            symbol: [a for a in own if a["ex_date"] <= cutoff] for symbol, own in actions.items()
        }
        provenance["actions_as_of"] = cutoff
    if "source_receipts" in provenance:
        provenance["source_receipts"] = [
            r for r in provenance["source_receipts"] if r.get("date", cutoff) <= cutoff
        ]
    if "quality_findings" in provenance:
        provenance["quality_findings"] = [
            f for f in provenance["quality_findings"] if f.get("date", cutoff) <= cutoff
        ]
    if "symbol_identities" in provenance:
        provenance["symbol_identities"] = {
            symbol: {day: item for day, item in own.items() if day[:10] <= cutoff}
            for symbol, own in provenance["symbol_identities"].items()
        }
    if "available_through" in provenance:
        provenance["available_through"] = min(provenance["available_through"], cutoff)
    provenance["causal_cutoff"] = cutoff
    # Existing coverage belongs to the full horizon, never copy it to training.
    result["coverage"] = validate_snapshot(result)
    return result
