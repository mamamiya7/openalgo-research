"""Bounded display metadata, separate from immutable calculation evidence."""

import math
from copy import deepcopy

STAGES = {
    "csv",
    "planning",
    "cache",
    "download",
    "verify",
    "initializing",
    "optimizing",
    "backtest",
    "validation",
    "saving",
    "complete",
}
COUNTERS = {
    "inputs": {"files", "signals", "symbols", "excluded_rows"},
    "prices": {
        "total_symbols",
        "checked_symbols",
        "covered_symbols",
        "required_candles",
        "cached_candles",
        "downloaded_candles",
        "available_candles",
        "missing_candles",
        "unavailable_candles",
        "pending_windows",
    },
    "trials": {"total", "completed", "evaluated", "reused", "rejected", "failed"},
}


def initial_activity(receipt, portfolio, now):
    """CSV parsing is complete on submission; no invented per-row progress."""
    inputs = {
        "files": len({s["source_id"] for s in portfolio.get("strategies", [])}),
        "signals": receipt.get("signal_count", 0),
        "symbols": receipt.get("symbol_count", 0),
    }
    return {"version": 1, "stage": "csv", "started_at": now, "updated_at": now, "inputs": inputs}


def merge_activity(current, event, now):
    """Accept only display fields; never expose engine inputs or adapter errors."""
    result = deepcopy(current)
    result.update(version=1, updated_at=now)
    if "stage" in event:
        if event["stage"] not in STAGES:
            raise ValueError("Unknown research activity stage")
        result["stage"] = event["stage"]
    for group, fields in COUNTERS.items():
        if group not in event:
            continue
        incoming = event[group]
        section = result.setdefault(group, {})
        for field in fields & incoming.keys():
            value = incoming[field]
            if value is None:
                section.pop(field, None)
            elif type(value) is not int or not 0 <= value <= 1_000_000_000:
                raise ValueError("Invalid research activity count")
            else:
                section[field] = value
        if group == "prices":
            if "interval" in incoming:
                if incoming["interval"] not in {"D", "1m"}:
                    raise ValueError("Invalid research activity interval")
                section["interval"] = incoming["interval"]
            if "cache_complete" in incoming:
                section["cache_complete"] = incoming["cache_complete"] is True
            if "current_symbol" in incoming:
                symbol = incoming["current_symbol"]
                if symbol is not None and (not isinstance(symbol, str) or len(symbol) > 100):
                    raise ValueError("Invalid research activity symbol")
                section["current_symbol"] = symbol
        if group == "trials":
            if "active_trial" in incoming:
                value = incoming["active_trial"]
                if value is not None and (type(value) is not int or not 1 <= value <= 1000):
                    raise ValueError("Invalid active research trial")
                section["active_trial"] = value
            if "history" in incoming:
                history = incoming["history"]
                if not isinstance(history, list) or len(history) > 100:
                    raise ValueError("Research activity history exceeds its bound")
                points = []
                for point in history:
                    trial, score = point["trial"], point["score"]
                    if (
                        type(trial) is not int
                        or not 1 <= trial <= 1000
                        or type(score) not in (int, float)
                        or not math.isfinite(score)
                    ):
                        raise ValueError("Invalid research activity history")
                    points.append({"trial": trial, "score": score})
                section["history"] = points
    return result
