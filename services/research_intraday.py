"""Historify-first minute observations; never substitute daily candles for gaps."""

import copy
import hashlib
import inspect
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

from research.data import MAX_BARS, validate_snapshot
from research.evidence_import import IMPORT_VERSION, MANIFEST_SHA256
from services.research_acquisition import IST, _save_receipt, native_history

MAX_RECEIPT_BYTES = 128 * 1024**2


def minute_stamp(value):
    if isinstance(value, (int, float)):
        point = datetime.fromtimestamp(value, IST)
    else:
        point = datetime.fromisoformat(value)
        if point.tzinfo is None:
            raise ValueError("Minute prices require an explicit timezone")
        point = point.astimezone(IST)
    if point.second or point.microsecond:
        raise ValueError("Prices must use minute-open timestamps")
    return point.isoformat()


def windows(stamps, positions=None):
    """Bound each native call/read to at most five calendar dates."""
    current = []
    for stamp in sorted(stamps):
        if current and (
            (datetime.fromisoformat(stamp).date() - datetime.fromisoformat(current[0]).date()).days
            >= 5
            or (
                positions is not None
                and stamp[:10] != current[-1][:10]
                and positions[stamp] != positions[current[-1]] + 1
            )
        ):
            yield current[0], current[-1]
            current = []
        current.append(stamp)
    if current:
        yield current[0], current[-1]


def acquire_intraday(
    signals,
    plan,
    reference_snapshot,
    *,
    reader,
    writer,
    archive_dir,
    credentials=None,
    history=None,
    prior=None,
    checkpoint=None,
    progress=None,
    cancelled=None,
    max_requests=500,
    max_seconds=1800,
):
    if plan.get("interval") != "1m" or not 1 <= max_requests <= 500 or not 1 <= max_seconds <= 1800:
        raise ValueError("Unsupported or unbounded minute acquisition")
    timeline = [minute_stamp(stamp) for stamp in plan.get("timeline", [])]
    if not timeline or timeline != sorted(set(timeline)) or len(timeline) > MAX_BARS:
        raise ValueError("A bounded ordered expected minute timeline is required")
    by_day = {}
    for stamp in timeline:
        by_day.setdefault(stamp[:10], []).append(stamp)
    for day, scheduled in by_day.items():
        hours = plan.get("session_hours", {}).get(day, {"open": "09:15", "close": "15:30"})
        opening = datetime.fromisoformat(day + "T" + hours["open"]).replace(tzinfo=IST)
        closing = datetime.fromisoformat(day + "T" + hours["close"]).replace(tzinfo=IST)
        if closing <= opening or closing - opening > timedelta(hours=8):
            raise ValueError("Invalid planned exchange session hours")
        expected = []
        while opening < closing:
            expected.append(opening.isoformat())
            opening += timedelta(minutes=1)
        if scheduled != expected:
            raise ValueError(
                "Minute timeline must retain the full scheduled grid, including missing observations"
            )
    required = {
        symbol: [minute_stamp(stamp) for stamp in stamps]
        for symbol, stamps in plan.get("required_timestamps", {}).items()
    }
    if not required or sum(map(len, required.values())) > MAX_BARS:
        raise ValueError("A bounded per-symbol minute plan is required")
    allowed = set(timeline)
    required_sets = {symbol: set(stamps) for symbol, stamps in required.items()}
    for stamps in required.values():
        if not stamps or stamps != sorted(set(stamps)) or not set(stamps) <= allowed:
            raise ValueError("Required minutes must be an ordered subset of the expected timeline")
    reference = reference_snapshot
    validate_snapshot(reference, signals)
    p = reference.get("provenance", {})
    if p.get("import_version") != IMPORT_VERSION or p.get("manifest_sha256") != MANIFEST_SHA256:
        raise ValueError("Minute qualification requires the checked daily exchange reference")
    sessions = list(reference["sessions"])
    if not {stamp[:10] for stamp in timeline} <= set(sessions):
        raise ValueError("Minute plan contains dates outside the reviewed exchange calendar")
    identity = hashlib.sha256(
        json.dumps(
            {
                "signals": signals,
                "plan": plan,
                "reference": reference,
                "mode": "historify-minute-v1",
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    if prior and prior.get("identity") != identity:
        raise ValueError("Minute checkpoint belongs to different immutable inputs")
    state = (
        copy.deepcopy(prior)
        if prior
        else {
            "identity": identity,
            "mode": "historify-minute-v1",
            "receipts": [],
            "bars": {s: {} for s in required},
            "raw_bars": {s: {} for s in required},
            "findings": [],
            "completed_windows": [],
        }
    )
    root = Path(archive_dir)
    if not root.is_absolute():
        raise ValueError("An explicit absolute receipt directory is required")
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    observed = 0
    receipt_bytes = sum(
        (root / (item["sha256"] + ".json")).stat().st_size for item in state["receipts"]
    )
    login = None
    state.setdefault("transport_attempts", [])
    state["batch_pending"] = False
    required_count = sum(map(len, required.values()))
    covered_count = sum(map(len, state["bars"].values()))
    baseline = max(
        500,
        int(9000 * covered_count / required_count),
        state.get("progress", {}).get("completed", 0),
    )
    baseline = min(9500, baseline)

    def report(units, phase):
        done = max(state.get("progress", {}).get("completed", 0), min(9900, int(units)))
        state["progress"] = {
            "completed": done,
            "total": 10000,
            "covered_minutes": covered_count,
            "required_minutes": required_count,
            "phase": phase,
        }
        if progress:
            progress(done, 10000)

    def check():
        if cancelled and cancelled():
            raise InterruptedError("Minute acquisition cancelled")
        if time.monotonic() - started >= max_seconds:
            raise TimeoutError("Minute acquisition deadline reached")

    def save():
        if checkpoint:
            checkpoint(state)

    def qualify(symbol, rows, first, last):
        nonlocal observed
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError("Minute response exceeds the bounded row limit")
        observed += len(rows)
        if observed > MAX_BARS:
            raise ValueError("Minute observations exceed the aggregate row bound")
        accepted, seen, rejected = {}, set(), []
        for row in rows:
            stamp = first
            try:
                stamp = minute_stamp(row["timestamp"])
                if stamp in seen:
                    accepted.pop(stamp, None)
                    raise ValueError("duplicate_timestamp")
                seen.add(stamp)
                if not first <= stamp <= last or stamp not in required_sets[symbol]:
                    raise ValueError("outside_required_minutes")
                bar = {key: float(row[key]) for key in ("open", "high", "low", "close")}
                volume = float(row.get("volume", 0))
                if (
                    not all(math.isfinite(v) and v > 0 for v in bar.values())
                    or not math.isfinite(volume)
                    or volume < 0
                    or not bar["low"]
                    <= min(bar["open"], bar["close"])
                    <= max(bar["open"], bar["close"])
                    <= bar["high"]
                ):
                    raise ValueError("malformed_minute")
                day = stamp[:10]
                daily = reference.get("raw_bars", {}).get(symbol, {}).get(day)
                identity_row = p.get("symbol_identities", {}).get(symbol, {}).get(day)
                if (
                    not daily
                    or not identity_row
                    or day not in reference.get("bars", {}).get(symbol, {})
                ):
                    raise ValueError("unverified_daily_identity_or_basis")
                tolerance = max(0.02, daily["high"] * 2e-5)
                if bar["low"] < daily["low"] - tolerance or bar["high"] > daily["high"] + tolerance:
                    raise ValueError("minute_outside_daily_range")
                accepted[stamp] = {
                    "timestamp": int(datetime.fromisoformat(stamp).timestamp()),
                    **bar,
                    "volume": volume,
                    "oi": 0,
                }
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                reason = str(exc)
                if reason not in {
                    "duplicate_timestamp",
                    "outside_required_minutes",
                    "malformed_minute",
                    "unverified_daily_identity_or_basis",
                    "minute_outside_daily_range",
                }:
                    reason = "malformed_minute"
                rejected.append(
                    {"kind": "quarantined", "symbol": symbol, "timestamp": stamp, "reason": reason}
                )
        return accepted, rejected

    def admit(symbol, rows):
        nonlocal covered_count
        for stamp, row in rows.items():
            if stamp in state["bars"][symbol]:
                continue
            raw = {key: row[key] for key in ("open", "high", "low", "close")}
            adjusted = dict(raw)
            for action in p.get("actions", {}).get(symbol, []):
                if stamp[:10] < action["ex_date"] <= p.get("actions_as_of", sessions[-1]):
                    adjusted = {
                        key: value * action["backward_price_factor"]
                        for key, value in adjusted.items()
                    }
            state["raw_bars"][symbol][stamp] = raw
            state["bars"][symbol][stamp] = adjusted
            covered_count += 1

    def receipt(
        symbol,
        first,
        last,
        rows,
        admitted,
        rejected,
        *,
        broker=None,
        outcome="archive_read",
        evidence=None,
    ):
        nonlocal receipt_bytes
        item = {
            "version": "openalgo-minute-receipt-v1",
            "provider": broker or "OpenAlgo Historify",
            "broker": broker,
            "original_provider": "unknown" if broker is None else broker,
            "interval": "1m",
            "symbol": symbol,
            "requested_from": first,
            "requested_to": last,
            "outcome": outcome,
            "history_evidence": evidence,
            "observations": [
                {
                    k: str(v) if isinstance(v, float) and not math.isfinite(v) else v
                    for k, v in row.items()
                }
                if isinstance(row, dict)
                else str(row)
                for row in rows
            ],
            "admitted_timestamps": sorted(admitted),
            "rejected_observations": rejected,
        }
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if receipt_bytes + len(encoded) > MAX_RECEIPT_BYTES:
            raise ValueError("Minute receipts exceed the aggregate byte bound")
        digest = _save_receipt(root, item)
        if not any(old["sha256"] == digest for old in state["receipts"]):
            state["receipts"].append(
                {
                    "sha256": digest,
                    "symbol": symbol,
                    "date": last[:10],
                    "broker": broker,
                    "outcome": outcome,
                    "rows": len(rows),
                    "admitted_rows": len(admitted),
                }
            )
            receipt_bytes += len(encoded)

    try:
        reads = [
            (symbol, first, last)
            for symbol, stamps in required.items()
            for first, last in windows(
                [stamp for stamp in stamps if stamp not in state["bars"][symbol]]
            )
        ]
        # Cache reads consume no broker requests. Their range, returned rows,
        # aggregate observations and elapsed time have independent bounds.
        report(baseline, "stored_prices")
        cache_end = baseline + (9500 - baseline) // 10
        for index, (symbol, first, last) in enumerate(reads):
            check()
            rows = reader(symbol, first, last)
            accepted, rejected = qualify(symbol, rows, first, last)
            admit(symbol, accepted)
            state["findings"].extend(rejected)
            report(baseline + (cache_end - baseline) * (index + 1) / len(reads), "stored_prices")
            if rows:
                receipt(symbol, first, last, rows, accepted, rejected)
                save()
        missing = {
            symbol: [stamp for stamp in stamps if stamp not in state["bars"][symbol]]
            for symbol, stamps in required.items()
        }
        requests = [
            (symbol, first, last)
            for symbol, stamps in missing.items()
            for first, last in windows(stamps)
        ]
        state["planned_windows"] = [list(item) for item in requests]
        state["total_windows"] = len(state["completed_windows"]) + len(requests)
        download_start = max(cache_end, int(9000 * covered_count / required_count))
        report(download_start, "broker_prices")
        save()
        completed_this_pass = 0
        for index, (symbol, first, last) in enumerate(requests):
            check()
            if index >= max_requests:
                # Continue automatically only after successful bounded progress.
                # Missing or rejected observations require an actionable failure,
                # never an endless cycle of calls for the same unavailable data.
                state["batch_pending"] = completed_this_pass == max_requests
                save()
                break
            if login is None:
                if credentials is None:
                    raise ValueError(
                        "Stored minute prices are missing; connect a broker to download them"
                    )
                login = credentials()
                if not login.get("auth_token") or not login.get("broker"):
                    raise ValueError("Connect a broker to download missing minute prices")
            call = history or native_history
            extra = {}

            def attempt(item, symbol=symbol, first=first, last=last):
                if len(state["transport_attempts"]) >= 10000:
                    raise ValueError("Minute transport attempts exceed the bounded limit")
                state["transport_attempts"].append(
                    {"symbol": symbol, "first": first, "last": last, **item}
                )
                save()

            if call is native_history or "evidence_mode" in inspect.signature(call).parameters:
                extra["request_control"] = {
                    "check": check,
                    "clock": time.monotonic,
                    "deadline": started + max_seconds,
                    "on_attempt": attempt,
                }
                if call is not native_history:
                    extra["evidence_mode"] = True
            success, response, status = call(
                symbol=symbol,
                exchange="NSE",
                interval="1m",
                start_date=first[:10],
                end_date=last[:10],
                source="api",
                **login,
                **extra,
            )
            rows = response.get("data", []) if success or response.get("history_evidence") else []
            accepted, rejected = qualify(symbol, rows, first, last)
            accepted = {
                stamp: row for stamp, row in accepted.items() if stamp not in state["bars"][symbol]
            }
            receipt(
                symbol,
                first,
                last,
                rows,
                accepted,
                rejected,
                broker=login["broker"],
                outcome="success" if success else "download_failed",
                evidence=response.get("history_evidence"),
            )
            if accepted:
                check()
                writer(symbol, list(accepted.values()))
                check()
                stored, _ = qualify(symbol, reader(symbol, first, last), first, last)
                if any(
                    stamp not in stored
                    or any(
                        stored[stamp][key] != row[key]
                        for key in ("open", "high", "low", "close", "volume")
                    )
                    for stamp, row in accepted.items()
                ):
                    raise ValueError(
                        "Stored minute prices do not exactly match the downloaded observations"
                    )
                admit(symbol, {stamp: stored[stamp] for stamp in accepted})
            state["findings"].extend(rejected)
            if not success:
                state["findings"].append(
                    {
                        "kind": "auth_expired" if status in (401, 403) else "download_failed",
                        "symbol": symbol,
                        "from": first,
                        "to": last,
                        "http_status": status,
                    }
                )
            if all(
                stamp in state["bars"][symbol]
                for stamp in missing[symbol]
                if first <= stamp <= last
            ):
                state["completed_windows"].append([symbol, first, last])
                if success:
                    completed_this_pass += 1
            # Progress measures processed work; coverage is retained separately.
            # A sparse or rejected response still completes this request attempt.
            report(
                download_start + (9500 - download_start) * (index + 1) / len(requests),
                "broker_prices",
            )
            save()
            if status in (401, 403):
                break
    except (InterruptedError, TimeoutError):
        save()
        raise
    missing = {
        symbol: [stamp for stamp in stamps if stamp not in state["bars"][symbol]]
        for symbol, stamps in required.items()
    }
    pending = [
        [symbol, first, last]
        for symbol, stamps in missing.items()
        for first, last in windows(stamps)
    ]
    if not pending:
        report(9700, "checking_prices")
    provenance = {
        **p,
        "interval": "1m",
        "temporal_version": "minute-open-v1",
        "history_source": "historify",
        "provider": "OpenAlgo Historify minute observations",
        "cache_provider": "unknown",
        "download_brokers": sorted({r["broker"] for r in state["receipts"] if r.get("broker")}),
        "acquisition_mode": "historify-minute-v1",
        "acquisition_status": "partial" if pending else "complete",
        "batch_pending": bool(pending and state["batch_pending"]),
        "pending_windows": pending,
        "broker_receipts": state["receipts"],
        "broker_transport_attempts": state["transport_attempts"],
        "quality_findings": list(p.get("quality_findings", [])) + state["findings"],
        "minute_price_verification": "Daily identity and high/low consistency only; not an independently verified exchange minute tape.",
        "limitations": list(p.get("limitations", []))
        + [
            "Minute observations are checked against daily identity and price bounds; an independent exchange minute tape is unavailable."
        ],
    }
    return {
        "sessions": sessions,
        "timeline": timeline,
        "required_timestamps": required,
        "session_hours": plan.get("session_hours", {}),
        "bars": state["bars"],
        "raw_bars": state["raw_bars"],
        "provenance": provenance,
        "coverage": {
            "status": "blocked" if pending else "ready",
            "warnings": ["Required minute observations remain missing."] if pending else [],
            "missing_minutes": missing,
        },
        "acquisition_checkpoint": state,
    }
