"""Bounded native OpenAlgo prices, without a second provider's price approval.

Historify remains the mutable archive. This layer records the ordinary history
adapter's candles and freezes exact stored OHLC for the scanner calculation.
"""

import copy
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from research.data import MAX_BARS, validate_snapshot
from services.research_acquisition import IST, _save_receipt, trading_date
from services.research_intraday import minute_stamp

POLICY = "openalgo-native-history-v1"
MODE = "historify-native-prices-v1"
MAX_RECEIPT_BYTES = 128 * 1024**2
MAX_JOURNAL = 100000
MAX_FINDINGS = 1000000
PRICE_FIELDS = ("open", "high", "low", "close", "volume", "oi")


class NativePriceNoProgress(ValueError):
    """A local batch made no durable progress and must not requeue forever."""


def native_history(**request):
    """Exactly the ordinary service used by Historify, Portfolio and SIP."""
    from services.history_service import get_history

    return get_history(**request)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _epoch(value):
    if isinstance(value, bool):
        raise ValueError("Invalid native timestamp")
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value != int(value):
            raise ValueError("Native timestamps must be whole epoch seconds")
        return int(value)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.microsecond:
        raise ValueError("Native timestamps must be timezone-aware")
    return int(parsed.timestamp())


def _windows(slots, positions, interval):
    first = previous = None
    for slot in slots:
        if first is not None and (
            (
                positions[slot] != positions[previous] + 1
                and (interval == "D" or slot[:10] != previous[:10])
            )
            or (date.fromisoformat(slot[:10]) - date.fromisoformat(first[:10])).days
            >= (300 if interval == "D" else 5)
        ):
            yield first, previous
            first = None
        if first is None:
            first = slot
        previous = slot
    if first is not None:
        yield first, previous


def acquire_native_prices(
    signals,
    plan,
    calendar_snapshot,
    *,
    reader,
    writer,
    credentials,
    archive_dir,
    prior=None,
    checkpoint=None,
    progress=None,
    activity=None,
    cancelled=None,
    history=None,
    max_requests=500,
    max_seconds=1800,
    clock=time.monotonic,
):
    """Read required native prices, download holes, and publish truthful gaps.

    ``reader``/``writer`` are already bound to the selected native D/1m archive.
    Successful responses complete a request even when some candles are absent;
    those gaps remain in the snapshot so the engine can retain pending outcomes.
    A symbol absent from OpenAlgo's master is recorded as a gap. Other failed
    requests block publication. Continuations do not repeat completed empty or
    unavailable-symbol responses. An admitted candle is immutable on resume.
    ``activity`` receives real stage/candle counters, also retained in the
    checkpoint. The local time budget yields only between durable units of work;
    a broker response already received is persisted and verified before yielding.
    """
    interval = plan.get("interval")
    if (
        interval not in {"D", "1m"}
        or not signals
        or len(signals) > 25000
        or not 1 <= max_requests <= 500
        or not 1 <= max_seconds <= 1800
    ):
        raise ValueError("Invalid or unbounded native price request")
    sessions = calendar_snapshot.get("sessions", [])
    if (
        not sessions
        or len(sessions) > 3000
        or sessions != sorted(set(sessions))
        or calendar_snapshot.get("provenance", {}).get("calendar_basis")
        != "openalgo-market-calendar-v1"
    ):
        raise ValueError("Native prices require a bounded OpenAlgo calendar snapshot")
    for day in sessions:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("Native calendar must use ISO dates")
    field = "required_dates" if interval == "D" else "required_timestamps"
    grid = sessions if interval == "D" else plan.get("timeline", [])
    if not grid or len(grid) > MAX_BARS or grid != sorted(set(grid)):
        raise ValueError("Native prices need an ordered bounded calendar grid")
    symbols = {signal["symbol"] for signal in signals}
    provided = plan.get(field)
    if not isinstance(provided, dict) or set(provided) - symbols:
        raise ValueError("Native price scope must name only uploaded symbols")
    required = {symbol: list(provided.get(symbol, [])) for symbol in sorted(symbols)}
    allowed = set(grid)
    if sum(map(len, required.values())) > MAX_BARS:
        raise ValueError("Native price scope exceeds the aggregate price bound")
    for slots in required.values():
        if slots != sorted(set(slots)) or set(slots) - allowed:
            raise ValueError("Required native prices must be an ordered calendar subset")
    if interval == "1m":
        if any(minute_stamp(stamp) != stamp or stamp[:10] not in sessions for stamp in grid):
            raise ValueError("Native minute grid must use scheduled IST timestamps")
    required_sets = {symbol: set(slots) for symbol, slots in required.items()}
    positions = {slot: index for index, slot in enumerate(grid)}
    identity = hashlib.sha256(
        _encoded(
            {"signals": signals, "plan": plan, "calendar": calendar_snapshot, "policy": POLICY}
        )
    ).hexdigest()
    if prior and (prior.get("identity") != identity or prior.get("mode") != MODE):
        raise ValueError("Native price checkpoint belongs to different immutable inputs")
    state = (
        copy.deepcopy(prior)
        if prior
        else {
            "identity": identity,
            "mode": MODE,
            "receipts": [],
            "findings": [],
            "transport_attempts": [],
            "completed_windows": [],
            "bars": {symbol: {} for symbol in required},
            "raw_bars": {symbol: {} for symbol in required},
        }
    )
    root = Path(archive_dir)
    if not root.is_absolute():
        raise ValueError("Native receipts require an absolute directory")
    root.mkdir(parents=True, exist_ok=True)
    receipt_sizes = {}
    for item in state["receipts"]:
        digest = item["sha256"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid native receipt identity")
        receipt_sizes[digest] = (root / (digest + ".json")).stat().st_size
    receipt_bytes = sum(receipt_sizes.values())
    if receipt_bytes > MAX_RECEIPT_BYTES or len(state["receipts"]) > MAX_JOURNAL:
        raise ValueError("Native receipt journal exceeds its bound")
    for symbol, bars in state["bars"].items():
        if (
            symbol not in required_sets
            or set(bars) - required_sets[symbol]
            or bars != state["raw_bars"].get(symbol)
        ):
            raise ValueError("Native price recovery does not match its immutable scope")
    state["batch_pending"] = False
    state["hard_failures"] = []
    state.pop("continuation_reason", None)
    started, observed, login = clock(), 0, None
    covered = sum(map(len, state["bars"].values()))
    total = sum(map(len, required.values()))
    baseline = min(9500, max(500, state.get("progress", {}).get("completed", 0)))

    # These journals describe acquisition work, never the immutable input identity.
    # Empty archive checks must survive a time-limited pass too, or a slow cache
    # scan can keep restarting before the first broker request is reached.
    if prior and not prior.get("batch_pending"):
        # A user retry after an actual failure can reuse prices which arrived in
        # Historify since that attempt. Automatic segments retain their scan.
        state["cache_checked_windows"] = []
    cache_windows = state.setdefault("cache_checked_windows", [])
    for symbol, first, last in cache_windows:
        if symbol not in required or first not in allowed or last not in allowed or first > last:
            raise ValueError("Invalid native cache-check checkpoint")
    if len(cache_windows) > MAX_JOURNAL:
        raise ValueError("Native cache-check journal exceeds its bound")
    if prior and prior.get("batch_pending") and not cache_windows and "planned_windows" in prior:
        cache_windows.extend(
            [symbol, first, last]
            for symbol, slots in required.items()
            for first, last in _windows(slots, positions, interval)
        )
        if len(cache_windows) > MAX_JOURNAL:
            raise ValueError("Native cache-check journal exceeds its bound")

    source_counts = state.get("source_counts")
    if source_counts is None:
        source_counts = {symbol: {"cached": 0, "downloaded": 0} for symbol in required}
        # Older checkpoints have exact receipt observations but no source counts.
        # Recover first admission attribution; do not relabel prior downloads as
        # cache reuse merely because the worker was restarted.
        attributed = {symbol: set() for symbol in required}
        for item in state["receipts"]:
            if item.get("outcome") not in {"archive_read", "success"}:
                continue
            with (root / (item["sha256"] + ".json")).open("rb") as handle:
                encoded = handle.read(MAX_RECEIPT_BYTES + 1)
            if hashlib.sha256(encoded).hexdigest() != item["sha256"]:
                raise ValueError("Native receipt failed integrity check while recovering sources")
            evidence = json.loads(encoded)
            symbol = evidence["symbol"]
            if symbol not in required:
                raise ValueError("Native receipt belongs to a different symbol scope")
            accepted_slots = set(evidence["accepted_slots"])
            for row in evidence["observations"]:
                slot = row["slot"]
                if (
                    slot in accepted_slots
                    and slot in state["bars"][symbol]
                    and slot not in attributed[symbol]
                    and all(
                        state["bars"][symbol][slot][key] == row[key]
                        for key in ("open", "high", "low", "close")
                    )
                ):
                    source_counts[symbol]["downloaded" if evidence["broker"] else "cached"] += 1
                    attributed[symbol].add(slot)
        if any(len(attributed[symbol]) != len(state["bars"][symbol]) for symbol in required):
            raise ValueError("Native candle sources cannot be recovered from saved receipts")
        state["source_counts"] = source_counts
    if set(source_counts) != set(required) or any(
        set(counts) != {"cached", "downloaded"}
        or any(type(value) is not int or value < 0 for value in counts.values())
        or sum(counts.values()) != len(state["bars"][symbol])
        for symbol, counts in source_counts.items()
    ):
        raise ValueError("Native candle counters do not match admitted prices")
    durable_start = covered + len(cache_windows) + len(state["completed_windows"])

    class BatchTimeLimit(Exception):
        """Only our cooperative segment budget, never an external timeout."""

    def save():
        if checkpoint:
            checkpoint(state)

    def check(*, boundary=True):
        if cancelled and cancelled():
            raise InterruptedError("Native price acquisition cancelled")
        if boundary and clock() - started >= max_seconds:
            durable_now = covered + len(cache_windows) + len(state["completed_windows"])
            if durable_now <= durable_start:
                raise NativePriceNoProgress(
                    "Price preparation reached its time limit without saving progress. "
                    "Resume to retry."
                )
            raise BatchTimeLimit

    def report(units, phase):
        done = min(9700, max(int(units), state.get("progress", {}).get("completed", 0)))
        state["progress"] = {
            "completed": done,
            "total": 10000,
            "phase": phase,
            "covered_dates" if interval == "D" else "covered_minutes": covered,
            "required_dates" if interval == "D" else "required_minutes": total,
        }
        if progress:
            progress(done, 10000)

    def missing_requests(symbol, *, cache=False):
        # Completed windows include sparse responses and unavailable symbols.
        # A single forward scan avoids a comparison for every slot/window pair.
        ranges = sorted(
            (first, last)
            for own_symbol, first, last in (
                state["completed_windows"] + (cache_windows if cache else [])
            )
            if own_symbol == symbol
        )
        cursor, missing = 0, []
        for slot in required[symbol]:
            if slot in state["bars"][symbol]:
                continue
            while cursor < len(ranges) and ranges[cursor][1] < slot:
                cursor += 1
            if cursor < len(ranges) and ranges[cursor][0] <= slot <= ranges[cursor][1]:
                continue
            missing.append(slot)
        return missing

    def pending_requests(*, cache=False):
        requests = []
        for symbol in required:
            requests.extend(
                (symbol, first, last)
                for first, last in _windows(
                    missing_requests(symbol, cache=cache), positions, interval
                )
            )
        if len(requests) > MAX_JOURNAL:
            raise ValueError("Native price request plan exceeds its bounded journal")
        return requests

    symbol_counts = {}

    def refresh_counts(symbol):
        unresolved = missing_requests(symbol)
        count = len(state["bars"][symbol])
        symbol_counts[symbol] = {
            "checked": not missing_requests(symbol, cache=True),
            "covered": count == len(required[symbol]),
            "unavailable": len(required[symbol]) - count - len(unresolved),
            "pending": sum(1 for _ in _windows(unresolved, positions, interval)),
        }

    for symbol in required:
        refresh_counts(symbol)

    def emit(stage, symbol=None):
        checked = sum(counts["checked"] for counts in symbol_counts.values())
        state["activity"] = {
            "stage": stage,
            "prices": {
                "interval": interval,
                "total_symbols": len(required),
                "checked_symbols": checked,
                "covered_symbols": sum(counts["covered"] for counts in symbol_counts.values()),
                "required_candles": total,
                "cached_candles": sum(counts["cached"] for counts in source_counts.values()),
                "downloaded_candles": sum(
                    counts["downloaded"] for counts in source_counts.values()
                ),
                "available_candles": covered,
                "missing_candles": total - covered,
                "unavailable_candles": sum(
                    counts["unavailable"] for counts in symbol_counts.values()
                ),
                "pending_windows": sum(counts["pending"] for counts in symbol_counts.values()),
                "current_symbol": symbol,
                "cache_complete": checked == len(required),
            },
        }
        if activity:
            activity(copy.deepcopy(state["activity"]))

    def qualify(symbol, rows, first, last):
        nonlocal observed
        if not isinstance(rows, list) or len(rows) > (3000 if interval == "D" else 10000):
            raise ValueError("Native price response exceeds its row bound")
        observed += len(rows)
        if observed > MAX_BARS * 4:
            raise ValueError("Native price reads exceed their aggregate observation bound")
        accepted, rejected, observations, seen = {}, [], [], set()
        for row in rows:
            slot = first
            try:
                timestamp = _epoch(row["timestamp"])
                slot = trading_date(timestamp) if interval == "D" else minute_stamp(timestamp)
                if not first <= slot <= last or slot not in required_sets[symbol]:
                    continue
                values = {key: float(row[key]) for key in ("open", "high", "low", "close")}
                values.update(volume=float(row.get("volume", 0)), oi=float(row.get("oi", 0)))
                if (
                    not all(math.isfinite(value) for value in values.values())
                    or any(values[key] <= 0 for key in ("open", "high", "low", "close"))
                    or values["volume"] < 0
                    or values["oi"] < 0
                    or not values["low"]
                    <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"])
                    <= values["high"]
                ):
                    raise ValueError("malformed_native_candle")
                observations.append({"slot": slot, "source_timestamp": row["timestamp"], **values})
                if slot in seen:
                    accepted.pop(slot, None)
                    raise ValueError("duplicate_native_candle")
                seen.add(slot)
                accepted[slot] = {"timestamp": timestamp, **values}
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                reason = str(error)
                if reason not in {"duplicate_native_candle", "malformed_native_candle"}:
                    reason = "malformed_native_candle"
                rejected.append({"kind": reason, "symbol": symbol, "date": slot[:10], "slot": slot})
        return accepted, rejected, observations

    def admit(symbol, rows, *, source):
        nonlocal covered
        for slot, row in rows.items():
            if slot not in state["bars"][symbol]:
                values = {key: row[key] for key in ("open", "high", "low", "close")}
                state["bars"][symbol][slot] = values
                state["raw_bars"][symbol][slot] = dict(values)
                covered += 1
                source_counts[symbol][source] += 1

    def receipt(
        symbol,
        first,
        last,
        observations,
        rejected,
        accepted,
        *,
        broker=None,
        outcome="archive_read",
        status=200,
    ):
        nonlocal receipt_bytes
        item = {
            "version": "openalgo-native-price-receipt-v1",
            "native_price_policy": POLICY,
            "provider": f"{broker} via OpenAlgo history" if broker else "OpenAlgo Historify",
            "broker": broker,
            "symbol": symbol,
            "exchange": "NSE",
            "interval": interval,
            "requested_from": first,
            "requested_to": last,
            "outcome": outcome,
            "http_status": status,
            "observations": observations,
            "rejected_observations": rejected,
            "accepted_slots": sorted(accepted),
        }
        encoded = _encoded(item)
        digest = hashlib.sha256(encoded).hexdigest()
        if digest not in receipt_sizes:
            if (
                receipt_bytes + len(encoded) > MAX_RECEIPT_BYTES
                or len(state["receipts"]) >= MAX_JOURNAL
            ):
                raise ValueError("Native receipts exceed their bounded storage allowance")
            if _save_receipt(root, item) != digest:
                raise ValueError("Native receipt identity changed while saving")
            receipt_sizes[digest] = len(encoded)
            receipt_bytes += len(encoded)
            state["receipts"].append(
                {
                    "sha256": digest,
                    "broker": broker,
                    "symbol": symbol,
                    "date": last[:10],
                    "outcome": outcome,
                    "rows": len(observations),
                    "admitted_rows": len(accepted),
                }
            )

    try:
        report(baseline, "stored_prices")
        emit("planning")
        reads = pending_requests(cache=True)
        cache_end = baseline + (9500 - baseline) // 10
        for index, (symbol, first, last) in enumerate(reads):
            check()
            emit("cache", symbol)
            check()
            rows = reader(symbol, first, last)
            accepted, rejected, observations = qualify(symbol, rows, first, last)
            admit(symbol, accepted, source="cached")
            if len(state["findings"]) + len(rejected) > MAX_FINDINGS:
                raise ValueError("Native price findings exceed their bound")
            state["findings"].extend(rejected)
            if observations or rejected:
                receipt(symbol, first, last, observations, rejected, accepted)
            if len(cache_windows) >= MAX_JOURNAL:
                raise ValueError("Native cache-check journal exceeds its bound")
            cache_windows.append([symbol, first, last])
            refresh_counts(symbol)
            report(baseline + (cache_end - baseline) * (index + 1) / len(reads), "stored_prices")
            emit("cache", symbol)
            save()
        requests = pending_requests()
        state["planned_windows"] = [list(window) for window in requests]
        state["total_windows"] = len(requests) + len(state["completed_windows"])
        download_start = max(cache_end, int(9000 * covered / max(1, total)))
        report(download_start, "broker_prices")
        if requests:
            emit("download")
        save()
        for index, (symbol, first, last) in enumerate(requests):
            check()
            if index >= max_requests:
                state["batch_pending"] = True
                state["continuation_reason"] = "request_budget"
                break
            if login is None:
                if credentials is None:
                    raise ValueError("Connect your broker in OpenAlgo to download missing prices.")
                supplied = credentials()
                login = {key: supplied.get(key) for key in ("auth_token", "feed_token", "broker")}
                if not login["auth_token"] or not login["broker"]:
                    raise ValueError("Connect your broker in OpenAlgo to download missing prices.")
            check()
            emit("download", symbol)
            check()
            try:
                success, response, status = (history or native_history)(
                    symbol=symbol,
                    exchange="NSE",
                    interval=interval,
                    start_date=first[:10],
                    end_date=last[:10],
                    source="api",
                    **login,
                )
            except (InterruptedError, TimeoutError):
                raise
            except Exception:
                success, response, status = False, {}, 502
            # Finish an already returned response across our local deadline.
            # Cancellation and external timeouts still retain their own outcome.
            check(boundary=False)
            if not isinstance(response, dict):
                success, response, status = False, {}, 502
            # Never persist adapter error messages, which may contain request credentials.
            message = str(response.get("message", "")).lower()
            outcome = (
                "success"
                if success
                else "symbol_unavailable"
                if status == 400 and response.get("error_code") == "symbol_unavailable"
                else (
                    "auth_expired"
                    if status in (401, 403)
                    or any(
                        word in message
                        for word in ("expired", "invalid token", "invalid access token")
                    )
                    else "rate_limited"
                    if status == 429
                    else "download_failed"
                )
            )
            rows = response.get("data", []) if success else []
            try:
                accepted, rejected, observations = qualify(symbol, rows, first, last)
            except ValueError:
                success, outcome = False, "malformed_response"
                accepted, rejected, observations = {}, [], []
            accepted = {
                slot: row for slot, row in accepted.items() if slot not in state["bars"][symbol]
            }
            receipt(
                symbol,
                first,
                last,
                observations,
                rejected,
                accepted,
                broker=login["broker"],
                outcome=outcome,
                status=status,
            )
            save()  # Preserve exact source observations even if mutable ingestion fails.
            if accepted:
                check(boundary=False)
                emit("verify", symbol)
                writer(symbol, list(accepted.values()))
                check(boundary=False)
                stored, _, _ = qualify(symbol, reader(symbol, first, last), first, last)
                if any(
                    slot not in stored
                    or any(stored[slot][key] != row[key] for key in ("timestamp", *PRICE_FIELDS))
                    for slot, row in accepted.items()
                ):
                    raise ValueError("Stored OpenAlgo prices differ from the downloaded candles.")
                admit(symbol, {slot: stored[slot] for slot in accepted}, source="downloaded")
            if len(state["findings"]) + len(rejected) + int(not success) > MAX_FINDINGS:
                raise ValueError("Native price findings exceed their bound")
            state["findings"].extend(rejected)
            if success or outcome == "symbol_unavailable":
                if len(state["completed_windows"]) >= MAX_JOURNAL:
                    raise ValueError("Native completed request journal exceeds its bound")
                state["completed_windows"].append([symbol, first, last])
                if outcome == "symbol_unavailable":
                    state["findings"].append(
                        {
                            "kind": outcome,
                            "symbol": symbol,
                            "date": first[:10],
                            "from": first,
                            "to": last,
                            "http_status": status,
                        }
                    )
            else:
                failure = {
                    "kind": outcome,
                    "symbol": symbol,
                    "date": first[:10],
                    "from": first,
                    "to": last,
                    "http_status": status,
                }
                state["findings"].append(failure)
                state["hard_failures"].append(failure)
            refresh_counts(symbol)
            report(
                download_start + (9500 - download_start) * (index + 1) / len(requests),
                "broker_prices",
            )
            emit("download", symbol)
            save()
            if not success and outcome != "symbol_unavailable":
                break
    except BatchTimeLimit:
        state["batch_pending"] = True
        state["continuation_reason"] = "time_budget"
        save()
    except (InterruptedError, TimeoutError, NativePriceNoProgress):
        save()
        raise

    missing = {
        symbol: [slot for slot in slots if slot not in state["bars"][symbol]]
        for symbol, slots in required.items()
    }
    pending = [
        [symbol, first, last]
        for symbol, slots in missing.items()
        for first, last in _windows(slots, positions, interval)
    ]
    if not state["batch_pending"] and not state["hard_failures"]:
        report(9700, "checking_prices")
        emit("verify")
    save()
    provenance = {
        "provider": "OpenAlgo Historify",
        "exchange": "NSE",
        "interval": interval,
        "native_price_policy": POLICY,
        "acquisition_mode": MODE,
        "adjustment_basis": "provider-native",
        "calendar_basis": "openalgo-market-calendar-v1",
        "identity_verified": False,
        "independent_verification": False,
        "calendar_verified": False,
        "synthetic": False,
        "history_source": "historify",
        "cache_provider": "unknown",
        "download_brokers": sorted(
            {item["broker"] for item in state["receipts"] if item.get("broker")}
        ),
        "acquisition_identity": identity,
        "acquisition_status": "failed"
        if state["hard_failures"]
        else "partial"
        if pending
        else "complete",
        "batch_pending": state["batch_pending"],
        "hard_failures": state["hard_failures"],
        "pending_windows": pending,
        "broker_receipts": state["receipts"],
        "quality_findings": state["findings"],
        "timezone": "Asia/Kolkata",
    }
    snapshot = {
        "sessions": list(sessions),
        "bars": state["bars"],
        "raw_bars": state["raw_bars"],
        "provenance": provenance,
        field: required,
        "data_requirements": copy.deepcopy(plan),
        "acquisition_checkpoint": state,
    }
    if "calendar_admission" in calendar_snapshot.get("provenance", {}):
        provenance["calendar_admission"] = calendar_snapshot["provenance"]["calendar_admission"]
    if interval == "1m":
        snapshot.update(
            timeline=list(grid), session_hours=copy.deepcopy(plan.get("session_hours", {}))
        )
        provenance["temporal_version"] = "minute-open-v1"
    snapshot["coverage"] = validate_snapshot(snapshot, signals)
    return snapshot
