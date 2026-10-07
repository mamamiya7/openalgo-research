"""Finite causal daily stock features applied to original scanner opportunities.

No acquisition, numerical runtime, global cache or trade-ledger filtering lives
here. Native account engines receive the complete original opportunity set with
explicit entry exclusions. Every feature window ends before its entry session.
"""

import json
from bisect import bisect_left
from copy import deepcopy
from datetime import datetime
from math import isclose, isfinite

from research.market_series import IST, _close_at, fingerprint, plan_series, validate_series

VERSION = "research-stock-features-v1"
ENTRY_VERSION = "research-stock-entry-conditions-v1"
WARMUP_SESSIONS = 100
MAX_SYMBOLS = 200
MAX_FEATURE_BARS = 200_000
MAX_SIGNALS = 25_000
MAX_BUNDLE_BYTES = 64 * 1024**2
MAX_RECEIPT_BYTES = 32 * 1024**2


def catalog():
    values = [
        {
            "id": "none",
            "family": "none",
            "label": "No stock filter",
            "lookback": 0,
            "threshold": None,
        }
    ]
    values.extend(
        {
            "id": f"ema_above_{n}",
            "family": "ema_trend",
            "label": f"Above {n}-session EMA",
            "lookback": n,
            "threshold": 0.0,
        }
        for n in (10, 20)
    )
    values.extend(
        {
            "id": f"momentum_{n}_gt{threshold}",
            "family": "momentum",
            "label": f"{n}-session momentum above {threshold}%",
            "lookback": n,
            "threshold": float(threshold),
        }
        for n in (5, 20)
        for threshold in (0, 2)
    )
    return values


def normalize_hypothesis(value):
    if not isinstance(value, dict):
        raise ValueError("Choose a recorded stock-feature hypothesis")
    for expected in catalog():
        if value.get("id") == expected["id"] and fingerprint(value) == fingerprint(expected):
            return expected
    raise ValueError("The stock-feature hypothesis is not in the recorded catalog")


def recipe():
    return {
        "version": VERSION,
        "ema": {
            "windows": [10, 20],
            "continuous_closes": "5 * lookback",
            "seed": "arithmetic mean of the first lookback closes in the finite window",
            "alpha": "2 / (lookback + 1)",
            "recurrence": "alpha * close + (1 - alpha) * previous_ema",
            "initialization": "restart the declared finite window at each decision; not an infinite-history EMA",
        },
        "momentum": {
            "windows": [5, 20],
            "formula": "100 * (last_close / close_lookback_sessions_ago - 1)",
        },
        "availability": "Prior session dates only; recorded daily close available_at <= actual entry decision",
        "missing": "unknown if any expected session in the required window lacks observed OHLC",
        "threshold_equality": "within relative tolerance 1e-12 or absolute tolerance 1e-12 is not above",
        "warmup_sessions": WARMUP_SESSIONS,
    }


def _json_size(value, maximum):
    size = 0
    for part in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(
        value
    ):
        size += len(part.encode())
        if size > maximum:
            raise ValueError("Stock-feature evidence exceeds its bounded byte allowance")
    return size


def validate_record(symbol, record):
    if not isinstance(record, dict) or set(record) != {
        "evidence",
        "calendar",
        "acquisition_receipts",
    }:
        raise ValueError(
            "Stock features require retained prices, calendar and acquisition receipts"
        )
    series = validate_series(record["evidence"])
    descriptor, calendar = series["descriptor"], record["calendar"]
    if descriptor != {
        "version": series["version"],
        "symbol": symbol,
        "exchange": "NSE",
        "interval": "D",
        "role": "feature_warmup",
    }:
        raise ValueError("Stock features require the exact NSE stock warmup instrument")
    plan_series(descriptor, series["required_dates"], calendar)
    if series["required_dates"] != calendar["sessions"] or any(
        series["available_at"][day] != _close_at(day, calendar["session_hours"][day])
        for day in series["required_dates"]
    ):
        raise ValueError("Stock-feature calendar or completed-close availability changed")
    receipts = record["acquisition_receipts"]
    if not isinstance(receipts, list) or len(receipts) > 1000:
        raise ValueError("Stock-feature acquisition receipts exceed their bound")
    total = 0
    for item in receipts:
        if not isinstance(item, dict) or set(item) != {"sha256", "receipt"}:
            raise ValueError("Stock-feature acquisition receipt is invalid")
        total += _json_size(item["receipt"], min(MAX_RECEIPT_BYTES, 16 * 1024**2))
        if total > MAX_RECEIPT_BYTES or fingerprint(item["receipt"]) != item["sha256"]:
            raise ValueError("Stock-feature acquisition receipt integrity changed")
        if (
            item["receipt"].get("version") != "openalgo-market-series-receipt-v1"
            or item["receipt"].get("descriptor") != descriptor
            or item["receipt"].get("symbol") != symbol
            or item["receipt"].get("exchange") != "NSE"
            or item["receipt"].get("interval") != "D"
        ):
            raise ValueError("Stock-feature acquisition receipt belongs to another instrument")
    return record


def validate_bundle(bundle):
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {"version", "series", "id"}
        or bundle["version"] != VERSION
        or not isinstance(bundle["series"], dict)
        or not 1 <= len(bundle["series"]) <= MAX_SYMBOLS
    ):
        raise ValueError("Stock-feature bundle needs a supported version and bounded symbol set")
    count = 0
    for symbol, record in bundle["series"].items():
        validate_record(symbol, record)
        count += len(record["evidence"]["required_dates"])
        if count > MAX_FEATURE_BARS:
            raise ValueError("Stock-feature history exceeds its total session-bar limit")
    _json_size(bundle, MAX_BUNDLE_BYTES)
    if bundle["id"] != fingerprint({key: value for key, value in bundle.items() if key != "id"}):
        raise ValueError("Saved stock-feature bundle changed")
    return bundle


def markers(settings, hypothesis, bundle_id=None):
    """Portable rule identity excludes data; the gate receipt binds exact evidence."""
    selected = normalize_hypothesis(hypothesis)
    result = deepcopy(settings)
    for row in result:
        row.pop("entry_condition", None)
        if selected["family"] != "none":
            row["entry_condition"] = {"version": ENTRY_VERSION, "hypothesis": deepcopy(selected)}
    return result


def _feature(record, decision, hypothesis):
    series, calendar = record["evidence"], record["calendar"]
    day = decision.date().isoformat()
    dates = calendar["sessions"]
    if day not in calendar["session_hours"] or day not in dates:
        return None, "Entry session is absent from the saved stock calendar"
    end = bisect_left(dates, day)
    lookback = hypothesis["lookback"]
    count = 5 * lookback if hypothesis["family"] == "ema_trend" else lookback + 1
    if end < count:
        return None, f"Stock feature needs {count} earlier consecutive session closes"
    required = dates[end - count : end]
    for prior in required:
        if prior not in series["bars"]:
            return None, f"Stock feature is missing the {prior} session close"
        if datetime.fromisoformat(series["available_at"][prior]) > decision:
            return None, f"The {prior} stock close was not available before entry"
    closes = [series["bars"][prior]["close"] for prior in required]
    result = {
        "lookback": lookback,
        "threshold": hypothesis["threshold"],
        "observations": count,
        "from": required[0],
        "to": required[-1],
        "available_at": series["available_at"][required[-1]],
        "close": closes[-1],
    }
    if hypothesis["family"] == "ema_trend":
        ema = sum(close / lookback for close in closes[:lookback])
        alpha = 2 / (lookback + 1)
        for close in closes[lookback:]:
            ema = alpha * close + (1 - alpha) * ema
        result.update(ema=ema, value=100 * (closes[-1] / ema - 1))
    else:
        result["value"] = 100 * (closes[-1] / closes[0] - 1)
    if not isfinite(result["value"]):
        return None, "Stock feature is outside finite calculation bounds"
    result["basis_id"] = fingerprint(
        {
            "version": VERSION,
            "hypothesis": hypothesis,
            "descriptor": series["descriptor"],
            "observations": [
                {
                    "date": prior,
                    "bar": series["bars"][prior],
                    "available_at": series["available_at"][prior],
                }
                for prior in required
            ],
        }
    )
    return result, None


def apply(evidence, bundle, hypothesis):
    """Return gated original opportunities and a reproducible causal membership receipt."""
    from research.connectors.vectorbt_portfolio import _schedule

    return _apply(
        evidence, bundle, normalize_hypothesis(hypothesis), feature_fn=_feature, markers_fn=markers
    )


def _apply(
    evidence,
    bundle,
    selected,
    *,
    feature_fn,
    markers_fn,
    compare_fn=None,
    calculation_version=VERSION,
    feature_recipe=None,
    bundle_validator=None,
):
    """Shared native schedule and receipt contract for versioned stock features."""
    from research.connectors.vectorbt_portfolio import _schedule

    (bundle_validator or validate_bundle)(bundle)
    if not evidence.get("frozen_prices"):
        raise ValueError("Stock feature gates require frozen native execution prices")
    if evidence["portfolio"]["engine"] != "vectorbt":
        raise ValueError("Stock feature gates currently require the VectorBT portfolio engine")
    total = sum(len(row["signals"]) for row in evidence["strategies"])
    if not 1 <= total <= MAX_SIGNALS:
        raise ValueError("Stock features need a bounded original scanner opportunity set")
    symbols = {signal["symbol"] for row in evidence["strategies"] for signal in row["signals"]}
    if symbols - set(bundle["series"]):
        raise ValueError("Saved stock features do not include every requested signal symbol")
    snapshot = evidence["snapshot"]
    interval = snapshot["provenance"]["interval"]
    if interval not in ("D", "1m"):
        raise ValueError("Stock conditions require daily or minute execution inputs")
    minute = interval == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    counts = dict.fromkeys(("allowed", "filtered", "unknown", "original_excluded", "pending"), 0)
    strategies, membership, cached = [], [], {}
    for strategy in evidence["strategies"]:
        signals = []
        for i, original in enumerate(strategy["signals"]):
            signal = deepcopy(original)
            entry, reason = _schedule(
                original, snapshot, strategy["config"], timeline, index, minute
            )
            feature, decision, scheduled_at = None, None, None
            if reason:
                state = "original_excluded"
            elif entry < 0:
                state = "pending"
            elif selected["family"] == "none":
                state = "allowed"
            else:
                day = timeline[entry][:10]
                record = bundle["series"][original["symbol"]]
                hours = record["calendar"]["session_hours"].get(day)
                if hours:
                    opening = datetime.fromisoformat(f"{day}T{hours['open']}:00+05:30")
                    closing = datetime.fromisoformat(f"{day}T{hours['close']}:00+05:30")
                    scheduled_at = timeline[entry] if minute else opening.isoformat()
                    at = datetime.fromisoformat(scheduled_at)
                    if at.utcoffset() == IST.utcoffset(None) and opening <= at < closing:
                        decision = at
                if decision is None:
                    reason = "Stock feature entry is outside its recorded session hours"
                else:
                    key = (original["symbol"], decision.isoformat())
                    if key not in cached:
                        cached[key] = feature_fn(record, decision, selected)
                    feature, reason = cached[key]
                if feature is None:
                    state = "unknown"
                    signal["research_exclusion"] = reason
                elif not (
                    compare_fn(feature, selected)
                    if compare_fn
                    else feature["value"] > selected["threshold"]
                    and not isclose(
                        feature["value"], selected["threshold"], rel_tol=1e-12, abs_tol=1e-12
                    )
                ):
                    state = "filtered"
                    signal["research_exclusion"] = f"Stock condition not met: {selected['label']}"
                else:
                    state = "allowed"
            counts[state] += 1
            membership.append(
                {
                    "strategy_id": strategy["id"],
                    "signal_index": i,
                    "symbol": original["symbol"],
                    "signal_date": original["date"],
                    "source_row": original.get("row"),
                    "scheduled_at": scheduled_at,
                    "decision_at": decision.isoformat() if decision else None,
                    "state": state,
                    "feature": deepcopy(feature),
                    "reason": reason,
                }
            )
            signals.append(signal)
        strategies.append({**deepcopy(strategy), "signals": signals})
    strategies = markers_fn(strategies, selected)
    receipt = {
        "version": VERSION,
        "hypothesis": selected,
        "bundle_id": bundle["id"],
        "execution_inputs_id": fingerprint(
            {
                key: evidence[key]
                for key in ("portfolio", "strategies", "signals", "snapshot", "versions")
            }
        ),
        "counts": counts,
        "membership": membership,
        "conditioned_strategies_id": fingerprint(strategies),
        "feature_recipe": recipe() if feature_recipe is None else feature_recipe,
        "timing": "Only completed daily observations from sessions before the actual entry decision",
        "unknown_policy": "Exclude unknown features; preserve original exclusions and pending entries",
    }
    if calculation_version != VERSION:
        receipt["calculation_version"] = calculation_version
    return strategies, {**receipt, "id": fingerprint(receipt)}
