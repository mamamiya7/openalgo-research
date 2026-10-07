"""Read-only native trade charts from exact, content-addressed calculation inputs.

No archive, market-data client, worker, cache or recalculation belongs here.
Responses contain at most 2,000 observed candles. The existing integrity reader
still owns its 512 MiB decoded-artifact bound; paging does not imply selective
artifact decoding. Its context-managed files and owner lookup sessions close on
success and error, and all decoded objects are local to the request.
"""

import math
import re
from bisect import bisect_left, bisect_right
from datetime import UTC, date, datetime, timedelta, timezone

from research.report_contract import report_context
from services import scanner_research_service as service
from services.research_decisions import DecisionEvidenceChanged

VERSION = "research-trade-chart-v1"
DEFAULT_LIMIT = 1500
MAX_LIMIT = 2000
CONTEXT_BARS = 20


def _integer(value, default, maximum, label):
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r"0|[1-9][0-9]{0,8}", str(value)):
        raise ValueError(f"Choose a valid {label}")
    value = int(value)
    if value > maximum:
        raise ValueError(f"Choose a {label} no greater than {maximum:,}")
    return value


def _epoch(key, interval):
    if interval == "D":
        # A display coordinate, not a claim that trading occurred at midnight.
        day = date.fromisoformat(key)
        return int(datetime.combine(day, datetime.min.time(), UTC).timestamp())
    parsed = datetime.fromisoformat(key)
    if parsed.tzinfo is None or parsed.second or parsed.microsecond:
        raise ValueError("Saved minute prices need their recorded whole-minute timezone")
    return int(parsed.timestamp())


def _bounds(report, original, period):
    """Use published period evidence only, without re-running today's partition."""
    basis = report.get("evaluation_basis", {}).get("period", {})
    coverage = report.get("coverage", {})
    first = basis.get("from") or coverage.get("date_from")
    last = basis.get("to") or coverage.get("date_to")
    # Reports written before evaluation-basis metadata remain readable, but a
    # broad original input archive must never leak across a saved holdout fence.
    split = original.get("validation", {})
    reservation = original.get("reserved_evaluation", {})
    if report is original and period == "selection":
        start = split.get("train_from") or reservation.get("selection", {}).get("from")
        if start:
            first = max(first, start) if first else start
        fence = split.get("train_to") or reservation.get("selection", {}).get("to")
        if fence:
            last = min(last, fence + "T23:59:59.999999+05:30") if last else fence
    elif report is not original and period == "evaluation":
        fence = split.get("test_from")
        if fence:
            first = max(first, fence) if first else fence
        last = last or split.get("test_to")
    if not isinstance(first, str) or not isinstance(last, str) or first[:10] > last[:10]:
        raise ValueError("This saved report does not retain its chart period")
    date.fromisoformat(first[:10])
    date.fromisoformat(last[:10])
    return first, last


def _inside(key, first, last, interval):
    if not first[:10] <= key[:10] <= last[:10]:
        return False
    if interval == "1m":
        current = datetime.fromisoformat(key)
        if "T" in first and current < datetime.fromisoformat(first):
            return False
        if "T" in last and current > datetime.fromisoformat(last):
            return False
    return True


def _marker(row, kind, interval, engine):
    price, day = row.get(f"{kind}_price"), row.get(f"{kind}_date")
    if price is None or not day:
        return None
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
        raise ValueError("This trade does not retain a valid execution price")
    timestamp = row.get(f"{kind}_timestamp") if interval == "1m" else day
    if not timestamp:
        # Never move a timestamp-less minute fill to an arbitrary daily opening.
        return {"kind": kind, "timestamp": None, "price": price, "time": None}
    key = timestamp
    timing = "open" if kind == "entry" else row.get("exit_timing")
    if interval == "1m" and kind == "exit" and timing == "close":
        key = (datetime.fromisoformat(timestamp) - timedelta(minutes=1)).isoformat()
    basis = (
        "Recorded daily fill; UTC midnight is only the candle's display coordinate."
        if interval == "D"
        else "Recorded minute opening."
        if timing == "open"
        else "Recorded minute close, shown on its preceding observed candle."
        if timing == "close"
        else "Recorded OHLC crossing; exact time inside the minute is unknown."
    )
    if engine == "nautilus":
        basis += " Nautilus execution uses model-derived OHLC events, not observed ticks."
    return {
        "kind": kind,
        "time": _epoch(key, interval),
        "timestamp": timestamp,
        "price": price,
        "quantity": row.get("quantity"),
        "basis": basis,
    }


def read(
    store, owner, job_id, trade_index, *, expected_result_artifact, period, offset=None, limit=None
):
    """Return one original ledger row's bounded saved-price window, without writes."""
    if not isinstance(expected_result_artifact, str) or not re.fullmatch(
        r"[a-f0-9]{64}", expected_result_artifact
    ):
        raise ValueError("Open a saved report before viewing its trade chart")
    if period not in ("full", "selection", "evaluation"):
        raise ValueError("Choose the report's recorded period")
    trade_index = _integer(trade_index, None, 99_999_999, "saved trade index")
    if trade_index is None:
        raise ValueError("Choose a saved trade")
    offset = _integer(offset, 0, 99_999_999, "chart offset")
    limit = _integer(limit, DEFAULT_LIMIT, MAX_LIMIT, "chart page size")
    if limit < 1:
        raise ValueError("Choose a chart page size of at least one candle")
    job = service.get_job(store, owner, job_id)
    if job.status != "completed" or not job.result_artifact:
        raise ValueError("Choose a completed portfolio report")
    if job.result_artifact != expected_result_artifact:
        raise DecisionEvidenceChanged("This saved report changed. Reopen it before viewing trades.")
    bundle = service.read_artifact(store, job.result_artifact)
    if (
        bundle.get("kind") not in ("portfolio_backtest", "portfolio_optimize")
        or bundle.get("job_id") not in (None, job.id)
        or not isinstance(bundle.get("result"), dict)
    ):
        raise ValueError("Choose a saved native portfolio report")
    original = bundle["result"]
    report = original
    context = report_context(
        original,
        job_id=job.id,
        result_artifact=job.result_artifact,
        inputs_artifact=bundle.get("inputs_artifact"),
    )
    if period != context["period"]:
        report = original.get("validation", {}).get("result") if period == "evaluation" else None
        if not isinstance(report, dict):
            raise ValueError("This report has no published result for that period")
        context = report_context(
            report,
            job_id=job.id,
            result_artifact=job.result_artifact,
            inputs_artifact=bundle.get("inputs_artifact"),
            period="evaluation",
        )
    ledger = report.get("ledger", [])
    if not isinstance(ledger, list) or trade_index >= len(ledger):
        raise LookupError("Saved trade not found")
    trade = dict(ledger[trade_index])
    if (
        trade.get("status") in ("excluded", "skipped")
        or not trade.get("entry_date")
        or trade.get("entry_price") is None
        or not trade.get("quantity", 0) > 0
    ):
        raise ValueError("This signal has no executed trade to show")
    first, last = _bounds(report, original, period)
    engine = report.get("execution", {}).get("engine") or original.get("portfolio", {}).get(
        "engine"
    )
    inputs_artifact = bundle.get("inputs_artifact")
    if not inputs_artifact:
        raise ValueError("This saved report does not retain its price inputs")
    del ledger, report, original, bundle
    evidence = service.read_artifact(store, inputs_artifact)
    snapshot = evidence.get("snapshot", {})
    provenance = snapshot.get("provenance", {})
    interval = provenance.get("interval")
    if interval not in ("D", "1m"):
        raise ValueError("This chart needs saved daily or one-minute prices")
    symbol = trade.get("symbol")
    series = snapshot.get("bars", {}).get(symbol, {})
    if not isinstance(series, dict):
        raise ValueError("The saved price series is unavailable")
    # Only the recorded execution timeline is admitted. Extra raw/acquisition
    # observations and candles from another published/reserved period stay out.
    timeline = snapshot.get("timeline" if interval == "1m" else "sessions", [])
    allowed = set(timeline)
    indexed = sorted(
        (_epoch(key, interval), key)
        for key in series
        if key in allowed and _inside(key, first, last, interval)
    )
    if not indexed:
        raise ValueError("No saved candles are available for this trade's report period")
    times = [item[0] for item in indexed]
    if len(set(times)) != len(times):
        raise ValueError("Saved candle times are ambiguous")
    requested = [
        item for kind in ("entry", "exit") if (item := _marker(trade, kind, interval, engine))
    ]
    entry = next(item for item in requested if item["kind"] == "entry")
    exit_marker = next((item for item in requested if item["kind"] == "exit"), None)
    if entry["time"] is None:
        raise ValueError("This saved minute trade does not retain its entry timestamp")
    # Open positions include the retained report tail. A missing boundary candle
    # remains missing; bisect only selects context, never relocates a marker.
    start = max(0, bisect_left(times, entry["time"]) - CONTEXT_BARS)
    end = (
        min(len(times), bisect_right(times, exit_marker["time"]) + CONTEXT_BARS)
        if exit_marker and exit_marker["time"] is not None
        else len(times)
    )
    selected = indexed[start:end]
    if offset >= len(selected):
        raise ValueError("This chart page is outside the saved trade window")
    positions = {stamp: i for i, (stamp, _) in enumerate(selected)}
    candles = []
    for stamp, key in selected[offset : offset + limit]:
        bar = series[key]
        if not isinstance(bar, dict) or any(
            isinstance(bar.get(field), bool)
            or not isinstance(bar.get(field), (int, float))
            or not math.isfinite(bar[field])
            for field in ("open", "high", "low", "close")
        ):
            raise ValueError("A saved candle does not retain valid OHLC prices")
        candles.append(
            {
                "time": stamp,
                "timestamp": key,
                **{field: bar[field] for field in ("open", "high", "low", "close")},
                **({"source_timestamp": bar["timestamp"]} if "timestamp" in bar else {}),
                **(
                    {"volume": bar["volume"]}
                    if type(bar.get("volume")) in (int, float) and math.isfinite(bar["volume"])
                    else {}
                ),
            }
        )
    markers, unmapped = [], []
    for item in requested:
        if item["time"] in positions:
            markers.append({**item, "bar_index": positions[item["time"]]})
        else:
            unmapped.append(item["kind"])
    total = len(selected)
    return {
        "version": VERSION,
        "identity": {
            "job_id": job.id,
            "result_artifact": job.result_artifact,
            "inputs_artifact": inputs_artifact,
            "period": period,
            "trade_index": trade_index,
            "report_id": context["report_id"],
        },
        "symbol": symbol,
        "exchange": provenance.get("exchange", "NSE"),
        "interval": interval,
        "timezone": "Asia/Kolkata",
        "trade": trade,
        "candles": candles,
        "markers": markers,
        "unmapped_markers": unmapped,
        "window": {
            "offset": offset,
            "limit": limit,
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
            "previous_offset": max(0, offset - limit) if offset else None,
            "entry_index": positions.get(entry["time"]),
            "exit_index": positions.get(exit_marker["time"]) if exit_marker else None,
        },
        "notice": "A recorded fill has no matching saved candle; its marker is omitted."
        if unmapped
        else None,
    }
