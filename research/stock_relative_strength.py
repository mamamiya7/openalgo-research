"""Causal stock/benchmark ratios over aligned native daily observations.

No downloads, global caches or runtime resources live here. Every calculation
uses only completed sessions before the actual scheduled entry, and missing
benchmark observations remain unknown rather than changing the comparison.
"""

from copy import deepcopy
from datetime import datetime
from math import isfinite

from research import stock_features as legacy
from research import stock_indicators as indicators
from research.market_series import _close_at, fingerprint, plan_series, validate_series

VERSION = "research-stock-context-v2"
FEATURE_VERSION = "research-stock-relative-strength-v1"
ENTRY_VERSION = "research-stock-entry-conditions-v3"
BENCHMARK_DESCRIPTOR = {
    "symbol": "NIFTY",
    "exchange": "NSE_INDEX",
    "interval": "D",
    "role": "benchmark",
}


def runtime_version():
    return indicators.runtime_version()


def catalog():
    rows = indicators.catalog()
    rows.extend(
        {
            "id": f"relative_{lookback}_gt{threshold}",
            "family": "relative_strength",
            "label": f"{lookback}-session strength vs Nifty above {threshold}%",
            "lookback": lookback,
            "threshold": float(threshold),
            "operator": "gt",
        }
        for lookback, threshold in ((5, 0), (20, 0), (20, 2), (60, 0))
    )
    rows.append(
        {
            "id": "ema20_and_relative20_gt0",
            "family": "conjunction",
            "label": "Above EMA 20 and positive 20-session strength vs Nifty",
            "lookback": 20,
            "threshold": None,
            "operator": "and",
            "components": ["ema_above_20", "relative_20_gt0"],
        }
    )
    return rows


def normalize_hypothesis(value):
    if isinstance(value, dict):
        for expected in catalog():
            if value.get("id") == expected["id"] and fingerprint(value) == fingerprint(expected):
                return expected
    raise ValueError("The relative-strength hypothesis is not in the recorded catalog")


def recipe():
    return {
        "version": FEATURE_VERSION,
        "dependency": {"package": "openalgo", "version": runtime_version()},
        "stock_indicators": indicators.recipe(),
        "benchmark": deepcopy(BENCHMARK_DESCRIPTOR),
        "relative_strength": {
            "lookbacks": [5, 20, 60],
            "formula": "100 * ((stock_last / stock_first) / (benchmark_last / benchmark_first) - 1)",
            "interpretation": "Relative price ratio return; not stock return minus benchmark return, RSI or total return",
            "observations": "lookback + 1 consecutive aligned recorded sessions for both instruments",
        },
        "availability": "Only prior completed session closes with available_at <= actual entry decision",
        "missing": "Unknown if either instrument lacks any required observed session; never fill, skip or substitute",
        "conjunction": "The declared EMA 20 and relative-strength pair requires all components known and true, including when another is false",
        "threshold_equality": "Strict above; relative or absolute tolerance 1e-12 counts as equal and does not pass",
        "warmup_sessions": legacy.WARMUP_SESSIONS,
    }


def markers(settings, hypothesis, bundle_id=None):
    selected = normalize_hypothesis(hypothesis)
    rows = deepcopy(settings)
    for row in rows:
        row.pop("entry_condition", None)
        if selected["family"] != "none":
            row["entry_condition"] = {"version": ENTRY_VERSION, "hypothesis": deepcopy(selected)}
    return rows


def validate_benchmark(record):
    if not isinstance(record, dict) or set(record) != {
        "evidence",
        "calendar",
        "acquisition_receipts",
    }:
        raise ValueError(
            "Relative strength requires retained benchmark prices, calendar and receipts"
        )
    series = validate_series(record["evidence"])
    descriptor = {"version": series["version"], **BENCHMARK_DESCRIPTOR}
    if series["descriptor"] != descriptor:
        raise ValueError("Relative strength requires the exact native NIFTY benchmark instrument")
    calendar = record["calendar"]
    plan_series(descriptor, series["required_dates"], calendar)
    if series["required_dates"] != calendar["sessions"] or any(
        series["available_at"][day] != _close_at(day, calendar["session_hours"][day])
        for day in series["required_dates"]
    ):
        raise ValueError("Benchmark calendar or completed-close availability changed")
    receipts = record["acquisition_receipts"]
    if not isinstance(receipts, list) or len(receipts) > 1000:
        raise ValueError("Benchmark acquisition receipts exceed their bound")
    total = 0
    for item in receipts:
        if not isinstance(item, dict) or set(item) != {"sha256", "receipt"}:
            raise ValueError("Benchmark acquisition receipt is invalid")
        total += legacy._json_size(item["receipt"], min(legacy.MAX_RECEIPT_BYTES, 16 * 1024**2))
        if total > legacy.MAX_RECEIPT_BYTES or fingerprint(item["receipt"]) != item["sha256"]:
            raise ValueError("Benchmark acquisition receipt integrity changed")
        receipt = item["receipt"]
        if (
            receipt.get("version") != "openalgo-market-series-receipt-v1"
            or receipt.get("descriptor") != descriptor
            or receipt.get("symbol") != "NIFTY"
            or receipt.get("exchange") != "NSE_INDEX"
            or receipt.get("interval") != "D"
        ):
            raise ValueError("Benchmark acquisition receipt belongs to another instrument")
    return record


def validate_bundle(bundle):
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {"version", "series", "benchmark", "id"}
        or bundle["version"] != VERSION
        or not isinstance(bundle["series"], dict)
        or not 1 <= len(bundle["series"]) <= legacy.MAX_SYMBOLS
    ):
        raise ValueError(
            "Relative-strength bundle needs a supported version and bounded symbol set"
        )
    benchmark = validate_benchmark(bundle["benchmark"])
    count = len(benchmark["evidence"]["required_dates"])
    for symbol, stock in bundle["series"].items():
        legacy.validate_record(symbol, stock)
        if stock["calendar"] != benchmark["calendar"] or (
            stock["evidence"]["required_dates"] != benchmark["evidence"]["required_dates"]
        ):
            raise ValueError("Stock and benchmark need identical recorded session calendars")
        count += len(stock["evidence"]["required_dates"])
        if count > legacy.MAX_FEATURE_BARS:
            raise ValueError("Relative-strength history exceeds its total session-bar limit")
    legacy._json_size(bundle, legacy.MAX_BUNDLE_BYTES)
    if bundle["id"] != fingerprint({key: value for key, value in bundle.items() if key != "id"}):
        raise ValueError("Saved relative-strength bundle changed")
    return bundle


def _observations(series, required):
    return [
        {"date": day, "bar": series["bars"][day], "available_at": series["available_at"][day]}
        for day in required
    ]


def _feature(stock, benchmark, decision, hypothesis):
    if hypothesis["id"] == "ema20_and_relative20_gt0":
        registered = {row["id"]: row for row in catalog()}
        parts = []
        for identifier in hypothesis["components"]:
            component = registered[identifier]
            feature, reason = _feature(stock, benchmark, decision, component)
            if feature is None:
                return None, f"{component['label']}: {reason}"
            parts.append(
                {
                    "hypothesis_id": identifier,
                    "passed": indicators._compare(feature, component),
                    "feature": feature,
                }
            )
        passed = all(part["passed"] for part in parts)
        feature = {
            "lookback": hypothesis["lookback"],
            "threshold": None,
            "observations": max(part["feature"]["observations"] for part in parts),
            "from": min(part["feature"]["from"] for part in parts),
            "to": max(part["feature"]["to"] for part in parts),
            "available_at": max(part["feature"]["available_at"] for part in parts),
            "close": parts[0]["feature"]["close"],
            "components": parts,
            "passed": passed,
            "value": int(passed),
        }
        feature["basis_id"] = fingerprint(
            {
                "version": FEATURE_VERSION,
                "hypothesis": hypothesis,
                "components": [part["feature"]["basis_id"] for part in parts],
            }
        )
        return feature, None
    if hypothesis["family"] != "relative_strength":
        return indicators._feature(stock, decision, hypothesis)
    required, reason = indicators._window(stock, decision, hypothesis["lookback"] + 1)
    if required is None:
        return None, reason
    other = benchmark["evidence"]
    if any(day not in benchmark["calendar"]["sessions"] for day in required):
        return None, "Benchmark is missing an aligned recorded session"
    for day in required:
        if day not in other["bars"]:
            return None, f"Nifty benchmark is missing the {day} session close"
        if datetime.fromisoformat(other["available_at"][day]) > decision:
            return None, f"The {day} Nifty close was not available before entry"
    series = stock["evidence"]
    first, last = required[0], required[-1]
    stock_first, stock_last = (series["bars"][day]["close"] for day in (first, last))
    benchmark_first, benchmark_last = (other["bars"][day]["close"] for day in (first, last))
    try:
        stock_growth, benchmark_growth = stock_last / stock_first, benchmark_last / benchmark_first
        value = 100 * (stock_growth / benchmark_growth - 1)
        stock_return, benchmark_return = 100 * (stock_growth - 1), 100 * (benchmark_growth - 1)
    except (OverflowError, ZeroDivisionError):
        return None, "Relative strength is outside finite calculation bounds"
    if not all(isfinite(item) for item in (value, stock_return, benchmark_return)):
        return None, "Relative strength is outside finite calculation bounds"
    feature = {
        "lookback": hypothesis["lookback"],
        "threshold": hypothesis["threshold"],
        "observations": len(required),
        "from": first,
        "to": last,
        "available_at": max(series["available_at"][last], other["available_at"][last]),
        "close": stock_last,
        "stock_first_close": stock_first,
        "benchmark_first_close": benchmark_first,
        "benchmark_close": benchmark_last,
        "stock_return_pct": stock_return,
        "benchmark_return_pct": benchmark_return,
        "value": value,
    }
    feature["basis_id"] = fingerprint(
        {
            "version": FEATURE_VERSION,
            "dependency_version": indicators.TESTED_SDK_VERSION,
            "hypothesis": hypothesis,
            "stock": {
                "descriptor": series["descriptor"],
                "observations": _observations(series, required),
            },
            "benchmark": {
                "descriptor": other["descriptor"],
                "observations": _observations(other, required),
            },
        }
    )
    return feature, None


def apply(evidence, bundle, hypothesis):
    selected = normalize_hypothesis(hypothesis)
    feature_recipe = recipe()  # Validate the exact native dependency before gating.
    return legacy._apply(
        evidence,
        bundle,
        selected,
        feature_fn=lambda stock, decision, rule: _feature(
            stock, bundle["benchmark"], decision, rule
        ),
        markers_fn=markers,
        compare_fn=indicators._compare,
        calculation_version=FEATURE_VERSION,
        feature_recipe=feature_recipe,
        bundle_validator=validate_bundle,
    )
