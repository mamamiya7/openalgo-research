"""Bounded explicit native-history acquisition with evidence sidecars.

No auth lookup, broker call, schema import or write occurs at module import.
Callers select the native archive; credentials are needed only for missing prices.
Bare candles are observations, never proof of ISIN/calendar/adjustment identity.
"""

import copy
import hashlib
import inspect
import json
import math
import os
import tempfile
import time
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from research.data import MAX_BARS, validate_snapshot
from services.research_historify import (
    native_historify_read,  # noqa: F401 -- public archive adapter
)

IST = timezone(timedelta(hours=5, minutes=30))
MAX_ARCHIVE_ROWS = MAX_BARS
MAX_RECEIPT_BYTES = 128 * 1024**2


def trading_date(timestamp):
    """Epoch seconds and aware ISO stamps use IST on both Windows and Linux."""
    if isinstance(timestamp, (int, float)):
        if not math.isfinite(timestamp):
            raise ValueError("Invalid timestamp")
        return datetime.fromtimestamp(timestamp, UTC).astimezone(IST).date().isoformat()
    parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Broker timestamps must carry a timezone or epoch seconds")
    return parsed.astimezone(IST).date().isoformat()


def native_history(**request):
    from services.history_service import get_history

    # Native symbol validation and shared 0.35s history pacing apply here.
    return get_history(**request, evidence_mode=True)


def native_historify_ingest(symbol, rows, expected_path):
    """Ingest into the explicitly selected canonical native Historify path."""
    expected = Path(expected_path).resolve()
    import pandas as pd

    from database import historify_db

    if Path(historify_db.get_db_path()).resolve() != expected:
        raise ValueError(
            "Historify was initialized with a different HISTORIFY_DATABASE_PATH; restart with matching configuration"
        )
    historify_db.init_database()
    count = historify_db.upsert_market_data(pd.DataFrame(rows), symbol, "NSE", "D")
    if count != len(rows):
        raise ValueError("Historify ingestion did not acknowledge all rows")
    return count


def _save_receipt(root, receipt):
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 16 * 1024 * 1024:
        raise ValueError("Acquisition receipt exceeds size bound")
    digest = hashlib.sha256(encoded).hexdigest()
    destination = root / (digest + ".json")
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError("Existing acquisition receipt failed integrity check")
        return digest
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return digest


def acquisition_receipts(snapshot, archive_dir):
    """Read exact hash-checked sidecars for embedding in immutable source evidence."""
    result, total = [], 0
    for receipt in snapshot["provenance"].get("broker_receipts", []):
        digest = receipt["sha256"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid acquisition receipt identity")
        path = Path(archive_dir) / (digest + ".json")
        size = path.stat().st_size
        total += size
        if size > 16 * 1024 * 1024 or total > 128 * 1024 * 1024:
            raise ValueError("Acquisition evidence exceeds export bound")
        encoded = path.read_bytes()
        if hashlib.sha256(encoded).hexdigest() != digest:
            raise ValueError("Acquisition receipt integrity check failed")
        result.append({"sha256": digest, "receipt": json.loads(encoded)})
    return result


def acquire_history(
    signals,
    *,
    auth_token=None,
    archive_dir,
    reference_snapshot=None,
    history=None,
    broker="fyers",
    feed_token=None,
    broker_mappings=None,
    end_date=None,
    progress=None,
    cancelled=None,
    writer=None,
    max_requests=500,
    max_seconds=1800,
    clock=time.monotonic,
    prior=None,
    on_checkpoint=None,
    reader=None,
    credentials=None,
    required_dates=None,
):
    """Acquire one snapshot, not once per evaluated configuration.

    reader(symbol, first, last) enables stored-price-first acquisition. Credentials
    are resolved lazily and writer(symbol, normalized_rows) must persist downloads
    before the reader confirms their exact stored values. The frozen result admits
    only rows matching hash-checked public raw evidence AND recorded identity.
    Unmatched fresh observations persist in receipts but remain quarantined.
    Native adapter cancellation remains cooperative between bounded adapter calls.
    required_dates scopes new daily plans to their exact per-symbol holding dates;
    omitted scope preserves the original saved daily acquisition contract.
    """
    if reader is None and not auth_token and credentials is None:
        raise ValueError("A valid explicitly supplied broker session is required")
    if (
        not signals
        or len(signals) > 25000
        or not 1 <= max_requests <= 500
        or not 1 <= max_seconds <= 1800
    ):
        raise ValueError("Acquisition exceeds bounded signal/request/time limits")
    root = Path(archive_dir)
    if not root.is_absolute():
        raise ValueError("Acquisition requires an absolute isolated archive directory")
    root.mkdir(parents=True, exist_ok=True)
    starts = {}
    for signal in signals:
        starts[signal["symbol"]] = min(starts.get(signal["symbol"], signal["date"]), signal["date"])
    reference = reference_snapshot or {}
    if reference:
        validate_snapshot(reference, signals)
        from research.evidence_import import IMPORT_VERSION, MANIFEST_SHA256

        if (
            reference.get("provenance", {}).get("import_version") != IMPORT_VERSION
            or reference["provenance"].get("manifest_sha256") != MANIFEST_SHA256
        ):
            raise ValueError("Acquisition comparison requires the checked public-evidence importer")
    sessions = reference.get("sessions", [])
    end = end_date or (sessions[-1] if sessions else max(signal["date"] for signal in signals))
    date.fromisoformat(end)
    scoped = required_dates is not None
    if scoped:
        if not isinstance(required_dates, dict) or set(required_dates) != set(starts):
            raise ValueError("Required daily dates must identify every signal symbol")
        session_set = set(sessions)
        total_required = 0
        for symbol, days in required_dates.items():
            if (
                not isinstance(days, list)
                or any(not isinstance(day, str) for day in days)
                or days != sorted(set(days))
                or set(days) - session_set
                or any(day < starts[symbol] or day > end for day in days)
            ):
                raise ValueError("Required daily dates must be sorted unique recorded sessions")
            total_required += len(days)
        if total_required > MAX_BARS:
            raise ValueError("Required daily prices exceed the aggregate row bound")
        required_dates = copy.deepcopy(required_dates)
        required_sets = {symbol: set(days) for symbol, days in required_dates.items()}
    positions = {day: index for index, day in enumerate(sessions)}

    def date_windows(days):
        current = []
        for day in days:
            if current and (
                positions[day] != positions[current[-1]] + 1
                or (date.fromisoformat(day) - date.fromisoformat(current[0])).days >= 300
            ):
                yield current[0], current[-1]
                current = []
            current.append(day)
        if current:
            yield current[0], current[-1]

    requests = []
    for symbol, first in sorted(starts.items()) if reader is None and not scoped else []:
        cursor = date.fromisoformat(first)
        if cursor > date.fromisoformat(end):
            raise ValueError("Acquisition end precedes a requested signal")
        while cursor <= date.fromisoformat(end):
            if len(requests) >= max_requests:
                raise ValueError(f"Acquisition exceeds its {max_requests} request limit")
            last = min(cursor + timedelta(days=299), date.fromisoformat(end))
            requests.append((symbol, cursor.isoformat(), last.isoformat()))
            cursor = last + timedelta(days=1)
    identity_payload = {"signals": signals, "end": end, "reference": reference, "broker": broker}
    if reader is not None:
        identity_payload.update(broker=None, mode="historify-first-v1")
    if scoped:
        identity_payload.update(
            required_dates=required_dates, daily_scope_version="holding-dates-v1"
        )
    identity_hash = hashlib.sha256(
        json.dumps(
            identity_payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    if prior and prior.get("identity") != identity_hash:
        raise ValueError("Acquisition checkpoint belongs to different immutable inputs")
    state = (
        copy.deepcopy(prior)
        if prior
        else {
            "identity": identity_hash,
            "receipts": [],
            "findings": [],
            "bars": {symbol: {} for symbol in starts},
            "raw_bars": {symbol: {} for symbol in starts},
            "completed_windows": [],
        }
    )
    started, receipts, findings = clock(), state["receipts"], state["findings"]
    state.setdefault("transport_attempts", [])
    bars, raw = state["bars"], state["raw_bars"]
    if scoped:
        state["required_dates"] = required_dates
        state["batch_pending"] = False
        if reader is None:
            requests = [
                (symbol, first, last)
                for symbol, days in sorted(required_dates.items())
                for first, last in date_windows([day for day in days if day not in bars[symbol]])
            ]
        covered_count = sum(len(series) for series in bars.values())
        baseline = max(
            500,
            min(9500, int(state.get("progress", {}).get("completed", 0))),
            min(9500, int(9000 * covered_count / max(1, total_required))),
        )

    def report(value, phase):
        if not scoped:
            return
        completed = min(9700, max(int(value), state.get("progress", {}).get("completed", 0)))
        state["progress"] = {
            "completed": completed,
            "total": 10000,
            "phase": phase,
            "covered_dates": sum(len(series) for series in bars.values()),
            "required_dates": total_required,
        }
        if progress:
            invoke_callback(progress, completed, 10000)

    expiry = False
    callback_error = None

    def invoke_callback(callback, *args):
        nonlocal callback_error
        try:
            return callback(*args)
        except Exception as error:
            callback_error = error
            raise

    def check():
        if cancelled and invoke_callback(cancelled):
            raise InterruptedError("Research acquisition cancelled")
        if clock() - started >= max_seconds:
            raise TimeoutError("Research acquisition deadline reached")

    if reader is not None or scoped:
        receipt_sizes = {
            item["sha256"]: (root / (item["sha256"] + ".json")).stat().st_size for item in receipts
        }
        receipt_bytes = sum(receipt_sizes.values())
        if receipt_bytes > MAX_RECEIPT_BYTES:
            raise ValueError("Acquisition receipts exceed the 128 MiB aggregate bound")
    if reader is not None:
        from services.research_historify import checked_archive_rows

        state["mode"] = "historify-first-v1"
        cached_count = 0
        cache_windows = (
            [
                (symbol, first, last)
                for symbol, days in sorted(required_dates.items())
                for first, last in date_windows([day for day in days if day not in bars[symbol]])
            ]
            if scoped
            else [(symbol, first, end) for symbol, first in sorted(starts.items())]
        )
        if scoped:
            report(baseline, "stored_prices")
            cache_end = baseline + (9500 - baseline) // 10
        for cache_index, (symbol, first, cache_last) in enumerate(cache_windows):
            check()
            if progress and not scoped:
                invoke_callback(progress, cache_index, len(starts))
            if first > end:
                raise ValueError("Acquisition end precedes a requested signal")
            cached_rows = reader(symbol, first, cache_last)
            if not isinstance(cached_rows, list):
                raise ValueError("Historify reader must return daily rows")
            cached_count += len(cached_rows)
            if cached_count > MAX_ARCHIVE_ROWS:
                raise ValueError("Historify cached observations exceed the aggregate row bound")
            admitted, adjusted, observations, rejected = checked_archive_rows(
                cached_rows, symbol, first, cache_last, reference
            )
            findings.extend(rejected)
            new_dates = set(admitted) - set(bars[symbol])
            if scoped:
                new_dates &= required_sets[symbol]
            for day in new_dates:
                raw[symbol][day], bars[symbol][day] = admitted[day], adjusted[day]
            if observations or rejected:
                cached_receipt = {
                    "version": "openalgo-historify-receipt-v1",
                    "provider": "OpenAlgo Historify",
                    "original_provider": "unknown",
                    "raw_observations": [
                        {
                            key: str(value)
                            if isinstance(value, float) and not math.isfinite(value)
                            else value
                            for key, value in row.items()
                        }
                        if isinstance(row, dict)
                        else str(row)
                        for row in cached_rows
                    ],
                    "symbol": symbol,
                    "requested_from": first,
                    "requested_to": cache_last,
                    "observations": observations,
                    "rejected_observations": rejected,
                    "admitted_dates": sorted(new_dates),
                }
                receipt_encoding = json.dumps(
                    cached_receipt, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                receipt_size = len(receipt_encoding)
                expected_digest = hashlib.sha256(receipt_encoding).hexdigest()
                if (
                    receipt_bytes + (0 if expected_digest in receipt_sizes else receipt_size)
                    > MAX_RECEIPT_BYTES
                ):
                    raise ValueError("Acquisition receipts exceed the 128 MiB aggregate bound")
                digest = _save_receipt(root, cached_receipt)
                if digest not in receipt_sizes:
                    receipt_sizes[digest] = receipt_size
                    receipt_bytes += receipt_size
                if not any(item["sha256"] == digest for item in receipts):
                    receipts.append(
                        {
                            "sha256": digest,
                            "date": cache_last,
                            "symbol": symbol,
                            "provider": "OpenAlgo Historify",
                            "outcome": "archive_read",
                            "rows": len(cached_rows),
                            "admitted_rows": len(new_dates),
                        }
                    )
            if scoped:
                report(
                    baseline + (cache_end - baseline) * (cache_index + 1) / len(cache_windows),
                    "stored_prices",
                )
                if on_checkpoint and new_dates:
                    invoke_callback(on_checkpoint, state)
        for symbol, first in sorted(starts.items()):
            needed = (
                required_dates[symbol]
                if scoped
                else [
                    day
                    for day in sessions
                    if first <= day <= end and day in reference.get("bars", {}).get(symbol, {})
                ]
            )
            requests.extend(
                (symbol, first, last)
                for first, last in date_windows([day for day in needed if day not in bars[symbol]])
            )
        if len(requests) > max_requests and not scoped:
            raise ValueError(f"Acquisition exceeds its {max_requests} missing-window request limit")
        state["planned_windows"] = [list(window) for window in requests]
        state["total_windows"] = len(requests) + len(state["completed_windows"])
        if on_checkpoint:
            invoke_callback(on_checkpoint, state)
        if requests and writer is None:
            raise ValueError(
                "Missing Historify prices require an archive writer before downloading"
            )

    completed_this_pass = 0
    if scoped:
        state["planned_windows"] = [list(window) for window in requests]
        state["total_windows"] = len(requests) + len(state["completed_windows"])
        download_start = max(
            state.get("progress", {}).get("completed", baseline),
            int(9000 * sum(len(series) for series in bars.values()) / max(1, total_required)),
        )
        report(download_start, "broker_prices")
    for index, (symbol, first, last) in enumerate(requests):
        if reader is None and [symbol, first, last] in state["completed_windows"]:
            continue
        if scoped and index >= max_requests:
            state["batch_pending"] = completed_this_pass == max_requests
            break
        if cancelled and cancelled():
            raise InterruptedError("Research acquisition cancelled")
        if clock() - started >= max_seconds:
            findings.append({"kind": "deadline", "symbol": symbol, "date": first})
            break
        if progress and not scoped:
            progress(index, len(requests))
        if not auth_token:
            if credentials is None:
                raise ValueError(
                    "Historify has missing prices. Connect a broker to download the missing sessions."
                )
            supplied = invoke_callback(credentials)
            auth_token, broker = supplied.get("auth_token"), supplied.get("broker")
            feed_token = supplied.get("feed_token")
            if not auth_token or not broker:
                raise ValueError("Connect a broker to download missing Historify prices.")
        if history is None:
            from database.token_db import get_br_symbol

            broker_mappings = {symbol: get_br_symbol(symbol, "NSE") for symbol in starts}
            history = native_history
        try:
            extra = {}
            if (
                history is native_history
                or "evidence_mode" in inspect.signature(history).parameters
            ):

                def record_attempt(
                    attempt, own_symbol=symbol, window_first=first, window_last=last
                ):
                    if len(state["transport_attempts"]) >= 10000:
                        raise InterruptedError("Acquisition retry evidence limit reached")
                    state["transport_attempts"].append(
                        {"symbol": own_symbol, "start": window_first, "end": window_last, **attempt}
                    )
                    if on_checkpoint:
                        invoke_callback(on_checkpoint, state)

                extra["request_control"] = {
                    "check": check,
                    "clock": clock,
                    "deadline": started + max_seconds,
                    "on_attempt": record_attempt,
                }
                if history is not native_history:
                    extra["evidence_mode"] = True
            success, response, status = history(
                symbol=symbol,
                exchange="NSE",
                interval="D",
                start_date=first,
                end_date=last,
                auth_token=auth_token,
                feed_token=feed_token,
                broker=broker,
                source="api",
                **extra,
            )
        except InterruptedError:
            if on_checkpoint:
                on_checkpoint(state)
            raise
        except TimeoutError:
            findings.append({"kind": "deadline", "symbol": symbol, "date": first})
            if on_checkpoint:
                on_checkpoint(state)
            break
        except Exception as error:
            if error is callback_error:
                raise
            success, response, status = False, {"message": "Native history request failed"}, 500
        response_hash = hashlib.sha256(
            json.dumps(response, sort_keys=True, default=str).encode()
        ).hexdigest()
        message = str(response.get("message", "")).lower()
        expiry = status in (401, 403) or any(
            word in message for word in ("expired", "invalid token", "invalid access token")
        )
        kind = "auth_expired" if expiry else "rate_limited" if status == 429 else "download_failed"
        rows = response.get("data", []) if success or response.get("history_evidence") else []
        if not isinstance(rows, list) or len(rows) > 1000:
            rows, success, kind = [], False, "malformed_response"
        receipt = {
            "version": "openalgo-history-receipt-v2",
            "provider": f"{(broker or 'broker').title()} via OpenAlgo history",
            "broker": broker,
            "symbol": symbol,
            "broker_mapping": (broker_mappings or {}).get(symbol),
            "exchange": "NSE",
            "interval": "D",
            "timezone": "Asia/Kolkata",
            "requested_from": first,
            "requested_to": last,
            "fetched_at": datetime.now(UTC).isoformat(),
            "http_status": status,
            "source_response_sha256": response_hash,
            "outcome": "success" if success else kind,
            "observations": [],
            "rejected_observations": [],
            "admitted_dates": [],
            "history_evidence": response.get("history_evidence"),
        }
        normalized_rows = []
        seen = set()
        for row in rows:
            try:
                day = trading_date(row["timestamp"])
                candle = {key: float(row[key]) for key in ("open", "high", "low", "close")}
                if scoped and day not in required_sets[symbol]:
                    receipt["rejected_observations"].append(
                        {
                            "date": day,
                            "source_timestamp": row["timestamp"],
                            "reason": "outside_required_dates",
                        }
                    )
                    continue
                if day in seen:
                    receipt["rejected_observations"].append(
                        {
                            "date": day,
                            "source_timestamp": row["timestamp"],
                            **{
                                key: value if math.isfinite(value) else str(value)
                                for key, value in candle.items()
                            },
                            "reason": "duplicate_timestamp",
                        }
                    )
                    bars[symbol].pop(day, None)
                    raw[symbol].pop(day, None)
                    if day in receipt["admitted_dates"]:
                        receipt["admitted_dates"].remove(day)
                    normalized_rows = [
                        r for r in normalized_rows if trading_date(r["timestamp"]) != day
                    ]
                    raise ValueError("Duplicate candle is ambiguous")
                if not first <= day <= last:
                    raise ValueError("Duplicate or out-of-window candle")
                seen.add(day)
                if (
                    not all(math.isfinite(v) and v > 0 for v in candle.values())
                    or not candle["low"]
                    <= min(candle["open"], candle["close"])
                    <= max(candle["open"], candle["close"])
                    <= candle["high"]
                ):
                    raise ValueError("Malformed candle")
                normalized = {
                    "timestamp": int(
                        datetime.combine(
                            date.fromisoformat(day), datetime.min.time(), IST
                        ).timestamp()
                    ),
                    **candle,
                    "volume": max(0, float(row.get("volume", 0))),
                    "oi": 0,
                }
                if not math.isfinite(normalized["volume"]):
                    raise ValueError("Malformed volume")
                normalized_rows.append(normalized)
                receipt["observations"].append(
                    {
                        "date": day,
                        "source_timestamp": row["timestamp"],
                        **candle,
                        "volume": normalized["volume"],
                    }
                )
                official = reference.get("raw_bars", {}).get(symbol, {}).get(day)
                identity = (
                    reference.get("provenance", {})
                    .get("symbol_identities", {})
                    .get(symbol, {})
                    .get(day)
                )
                if (
                    official
                    and identity
                    and day in reference.get("bars", {}).get(symbol, {})
                    and all(
                        math.isclose(candle[key], official[key], rel_tol=2e-5, abs_tol=0.02)
                        for key in candle
                    )
                ):
                    raw[symbol][day] = candle
                    adjusted = dict(candle)
                    for action in reference["provenance"].get("actions", {}).get(symbol, []):
                        if (
                            day
                            < action["ex_date"]
                            <= reference["provenance"].get("actions_as_of", end)
                        ):
                            adjusted = {
                                key: value * action["backward_price_factor"]
                                for key, value in adjusted.items()
                            }
                    bars[symbol][day] = adjusted
                    receipt["admitted_dates"].append(day)
                else:
                    findings.append(
                        {
                            "kind": "quarantined",
                            "reason": "unverified_identity_or_basis",
                            "symbol": symbol,
                            "date": day,
                        }
                    )
            except (KeyError, TypeError, ValueError, OverflowError):
                findings.append({"kind": "malformed", "symbol": symbol, "date": first})
        if not success:
            findings.append({"kind": kind, "symbol": symbol, "date": first})
        # Preserve the response receipt before optional mutable archive ingestion.
        if scoped:
            receipt_encoding = json.dumps(
                receipt, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            receipt_size = len(receipt_encoding)
            expected_digest = hashlib.sha256(receipt_encoding).hexdigest()
            if (
                receipt_bytes + (0 if expected_digest in receipt_sizes else receipt_size)
                > MAX_RECEIPT_BYTES
            ):
                raise ValueError("Acquisition receipts exceed the 128 MiB aggregate bound")
        digest = _save_receipt(root, receipt)
        if scoped and digest not in receipt_sizes:
            receipt_sizes[digest] = receipt_size
            receipt_bytes += receipt_size
        if writer and normalized_rows:
            if cancelled and cancelled():
                raise InterruptedError("Research acquisition cancelled before archive ingestion")
            if reader is not None:
                existing_stamps = {}
                for stored_row in reader(symbol, first, last):
                    existing_stamps.setdefault(trading_date(stored_row["timestamp"]), []).append(
                        stored_row["timestamp"]
                    )
                for normalized in normalized_rows:
                    stamps = existing_stamps.get(trading_date(normalized["timestamp"]), [])
                    if len(stamps) == 1:
                        # Update native daily rows in place even when that broker's
                        # archive timestamp uses UTC midnight or market open.
                        normalized["timestamp"] = int(stamps[0])
            writer(symbol, normalized_rows)
            if reader is not None:
                persisted = reader(symbol, first, last)
                by_day = {}
                for row in persisted:
                    day = trading_date(row["timestamp"])
                    if day in by_day:
                        raise ValueError(
                            "Historify persistence contains ambiguous duplicate daily rows"
                        )
                    by_day[day] = row
                for expected_row in normalized_rows:
                    day = trading_date(expected_row["timestamp"])
                    stored = by_day.get(day)
                    if not stored or any(
                        float(stored[key]) != float(expected_row[key])
                        for key in ("open", "high", "low", "close", "volume")
                    ):
                        raise ValueError(
                            "Historify persistence did not retain the downloaded prices exactly"
                        )
                persisted_raw, persisted_adjusted, _, _ = checked_archive_rows(
                    persisted, symbol, first, last, reference
                )
                for day in receipt["admitted_dates"]:
                    if day not in persisted_raw:
                        raise ValueError(
                            "Stored Historify prices failed evidence checks after download"
                        )
                    raw[symbol][day], bars[symbol][day] = (
                        persisted_raw[day],
                        persisted_adjusted[day],
                    )
        receipts.append(
            {
                "sha256": digest,
                "broker": broker,
                "date": last,
                "symbol": symbol,
                "outcome": receipt["outcome"],
                "rows": len(normalized_rows),
                "admitted_rows": len(receipt["admitted_dates"]),
            }
        )
        if success:
            for finding in findings:
                if (
                    finding.get("symbol") == symbol
                    and finding.get("date") == first
                    and finding.get("kind")
                    in {"auth_expired", "rate_limited", "deadline", "download_failed"}
                ):
                    finding["resolved_by_later_attempt"] = True
            expected_dates = (
                required_dates[symbol] if scoped else reference.get("bars", {}).get(symbol, {})
            )
            if (reader is None and not scoped) or all(
                day in bars[symbol] for day in expected_dates if first <= day <= last
            ):
                state["completed_windows"].append([symbol, first, last])
                completed_this_pass += 1
        if scoped:
            report(
                download_start + (9500 - download_start) * (index + 1) / len(requests),
                "broker_prices",
            )
        if on_checkpoint:
            on_checkpoint(state)
        if expiry:
            break
    if progress and not scoped:
        progress(
            len([window for window in requests if list(window) in state["completed_windows"]]),
            len(requests),
        )
    pending = [
        list(window) for window in requests if list(window) not in state["completed_windows"]
    ]
    if scoped:
        pending = [
            [symbol, first, last]
            for symbol, days in sorted(required_dates.items())
            for first, last in date_windows([day for day in days if day not in bars[symbol]])
        ]
        if not pending:
            report(9700, "checking_prices")
        if on_checkpoint:
            invoke_callback(on_checkpoint, state)
    if reader is not None:
        # A successful transport with partial/empty data still leaves cache holes.
        pending = (
            pending
            if scoped
            else [
                window
                for window in state["planned_windows"]
                if any(
                    window[1] <= day <= window[2] and day not in bars[window[0]]
                    for day in reference.get("bars", {}).get(window[0], {})
                )
            ]
        )
        if not pending:
            for finding in findings:
                if finding.get("kind") in {
                    "auth_expired",
                    "rate_limited",
                    "deadline",
                    "download_failed",
                }:
                    finding["resolved_by_later_attempt"] = True
    provenance = dict(reference.get("provenance", {}))
    provenance.update(
        {
            "provider": "OpenAlgo Historify, checked against official NSE raw records"
            if reader is not None
            else f"{(broker or 'broker').title()} via OpenAlgo history, compared to official NSE raw records",
            "acquisition_mode": "historify-first-v1" if reader is not None else "native-history-v1",
            "history_source": "historify" if reader is not None else "broker",
            "cache_provider": "unknown" if reader is not None else None,
            "broker": broker if reader is None else None,
            "download_brokers": sorted({item["broker"] for item in receipts if item.get("broker")}),
            "exchange": "NSE",
            "interval": "D",
            "timezone": "Asia/Kolkata",
            "synthetic": False,
            "broker_receipts": receipts,
            "broker_transport_attempts": state["transport_attempts"],
            "acquisition_identity": identity_hash,
            "acquisition_status": "complete" if not pending else "partial",
            "pending_windows": pending,
            "quality_findings": provenance.get("quality_findings", []) + findings,
        }
    )
    snapshot = {"sessions": sessions, "bars": bars, "raw_bars": raw, "provenance": provenance}
    if scoped:
        provenance["batch_pending"] = bool(pending and state["batch_pending"])
        snapshot["required_dates"] = required_dates
    if not reference or (not any(bars.values()) and not (scoped and total_required == 0)):
        provenance.update({"identity_verified": False, "adjustment_basis": "unverified"})
        snapshot["coverage"] = {
            "status": "blocked",
            "warnings": [
                "Broker observations were saved, but no rows have verified instrument identity, calendar and adjustment basis. Import matching official evidence before calculation."
            ],
            "symbols": [],
            "provenance": provenance,
        }
    else:
        snapshot["coverage"] = validate_snapshot(snapshot, signals)
    messages = {
        "auth_expired": "Broker authentication expired; sign in again before requesting new history. Saved verified evidence remains usable.",
        "rate_limited": "The broker rate limited acquisition; retained chunks remain saved. Retry the missing window later.",
        "deadline": "Acquisition reached its bounded deadline; unrequested windows remain missing.",
        "download_failed": "One or more native history requests failed; successful chunks do not prove complete coverage.",
    }
    for kind in sorted(
        {finding["kind"] for finding in findings if not finding.get("resolved_by_later_attempt")}
        & messages.keys()
    ):
        snapshot["coverage"]["warnings"].append(messages[kind])
    snapshot["acquisition_checkpoint"] = state
    return snapshot
