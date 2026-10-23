"""Finite causal native indicators; no acquisition, scheduling or retained state.

The shared stock-feature gate owns scheduling and immutable bundle validation.
Native indicator windows end before the entry session and are reseeded inside
the recorded finite window, never evaluated against future or filled prices.
"""

from bisect import bisect_left
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from importlib import metadata
from math import isclose, isfinite
from threading import get_ident

from research import stock_features as legacy
from research.market_series import fingerprint

FEATURE_VERSION = "research-stock-indicators-v1"
ENTRY_VERSION = "research-stock-entry-conditions-v2"
TESTED_SDK_VERSION = "2.0.5"
SMOOTHING_OBSERVATIONS = 100
_runtime_pin = ContextVar("research_owned_indicator_runtime", default=None)


def runtime_version():
    """Fail closed before calculation when the recorded numerical API changed."""
    pin = _runtime_pin.get()
    if pin is not None and not pin["closed"] and pin["owner"] == get_ident():
        return pin["version"]
    return _installed_runtime_version()


def _installed_runtime_version():
    try:
        installed = metadata.version("openalgo")
    except metadata.PackageNotFoundError as error:
        raise ValueError("Stock indicators require OpenAlgo SDK 2.0.5") from error
    if installed != TESTED_SDK_VERSION:
        raise ValueError("Stock indicators require their recorded OpenAlgo SDK 2.0.5")
    return installed


@contextmanager
def _pinned_runtime():
    """Verify one calculation's native SDK before entering its private scope.

    Public calls and other threads still inspect their own installed runtime.
    This capability is released on every exit, including copied contexts that
    survive an interrupted calculation. No process-wide version cache exists.
    """
    pin = {"version": _installed_runtime_version(), "owner": get_ident(), "closed": False}
    token = _runtime_pin.set(pin)
    try:
        yield
    finally:
        pin["closed"] = True
        _runtime_pin.reset(token)


def catalog():
    result = legacy.catalog()
    result.extend(
        {
            "id": f"rsi_{period}_{operator}{threshold}",
            "family": "rsi",
            "label": f"RSI {period} {'above' if operator == 'gt' else 'below'} {threshold}",
            "lookback": period,
            "threshold": float(threshold),
            "operator": operator,
        }
        for period, operator, threshold in (
            (14, "gt", 50),
            (14, "gt", 60),
            (14, "lt", 30),
            (7, "lt", 30),
        )
    )
    result.extend(
        {
            "id": f"bbands_20_{operator}_{band}2",
            "family": "bollinger",
            "label": f"Close {'above' if operator == 'gt' else 'below'} {band} Bollinger Band (20, 2)",
            "lookback": 20,
            "threshold": 0.0,
            "operator": operator,
            "std_dev": 2.0,
        }
        for operator, band in (("gt", "upper"), ("lt", "lower"))
    )
    result.extend(
        {
            "id": f"atr_14_pct_{operator}{threshold}",
            "family": "atr",
            "label": f"ATR 14 {'below' if operator == 'lt' else 'above'} {threshold}% of price",
            "lookback": 14,
            "threshold": float(threshold),
            "operator": operator,
        }
        for operator, threshold in (("lt", 2), ("lt", 4), ("gt", 2))
    )
    result.extend(
        [
            {
                "id": "ema20_and_rsi14_gt50",
                "family": "conjunction",
                "label": "Above EMA 20 and RSI 14 above 50",
                "lookback": 20,
                "threshold": None,
                "operator": "and",
                "components": ["ema_above_20", "rsi_14_gt50"],
            },
            {
                "id": "momentum5_and_atr14_lt4",
                "family": "conjunction",
                "label": "Positive 5-session momentum and ATR 14 below 4%",
                "lookback": 14,
                "threshold": None,
                "operator": "and",
                "components": ["momentum_5_gt0", "atr_14_pct_lt4"],
            },
        ]
    )
    return result


def normalize_hypothesis(value):
    if not isinstance(value, dict):
        raise ValueError("Choose a recorded stock-indicator hypothesis")
    from research.prepared_stock_catalogue import normalize_if_owned

    admitted, selected = normalize_if_owned("indicator", value)
    if admitted:
        if selected is not None:
            return selected
        raise ValueError("The stock-indicator hypothesis is not in the recorded catalog")
    if not isinstance(value, dict):
        raise ValueError("Choose a recorded stock-indicator hypothesis")
    for expected in catalog():
        if value.get("id") == expected["id"] and fingerprint(value) == fingerprint(expected):
            return expected
    raise ValueError("The stock-indicator hypothesis is not in the recorded catalog")


def recipe():
    return {
        "version": FEATURE_VERSION,
        "dependency": {"package": "openalgo", "version": runtime_version()},
        "legacy_features": legacy.recipe(),
        "rsi": {
            "api": "openalgo.ta.rsi",
            "periods": [7, 14],
            "observations": SMOOTHING_OBSERVATIONS,
            "seed": "mean gains and losses of first period differences; Wilder smoothing thereafter",
            "flat_prices": "Native SDK returns 100 when average loss is zero, including a completely flat window; value is retained, not interpreted as evidence of strength",
        },
        "bollinger": {
            "api": "openalgo.ta.bbands",
            "period": 20,
            "standard_deviations": 2.0,
            "variance": "population variance (ddof=0)",
            "value": "completed close minus the selected native band, in price units",
            "degenerate": "zero or numerically indistinguishable band width is unknown",
        },
        "atr": {
            "api": "openalgo.ta.atr",
            "period": 14,
            "observations": SMOOTHING_OBSERVATIONS,
            "seed": "first true range is high minus low; mean first period ranges, then Wilder smoothing",
            "value": "100 * native ATR / last completed close",
            "zero": "zero ATR is a defined observation",
        },
        "smoothing": "Restart within the same finite 100-observation window at each decision; not an infinite-history indicator",
        "conjunction": "Only the two declared pairs; all components must be known and true. Any unknown component excludes the condition, even when another is false",
        "availability": "Only prior session dates with recorded daily close available_at <= actual entry decision",
        "missing": "No filling or skipping required sessions",
        "threshold_equality": "Strict above/below; relative or absolute tolerance 1e-12 counts as equal and does not pass",
        "warmup_sessions": SMOOTHING_OBSERVATIONS,
    }


def markers(settings, hypothesis, bundle_id=None):
    selected = normalize_hypothesis(hypothesis)
    rows = deepcopy(settings)
    for row in rows:
        row.pop("entry_condition", None)
        if selected["family"] != "none":
            row["entry_condition"] = {"version": ENTRY_VERSION, "hypothesis": deepcopy(selected)}
    return rows


def _compare(feature, hypothesis):
    operator = hypothesis.get("operator", "gt")
    if operator == "and":
        return feature["passed"] is True
    value, threshold = feature["value"], hypothesis["threshold"]
    if isclose(value, threshold, rel_tol=1e-12, abs_tol=1e-12):
        return False
    return value > threshold if operator == "gt" else value < threshold


def _window(record, decision, count):
    prepared = legacy._prepared_window(record, decision, count)
    if prepared is not None:
        if prepared["problem"] is not None:
            return None, legacy._window_problem(prepared, "indicator")
        return list(prepared["required"]), None
    series, calendar = record["evidence"], record["calendar"]
    day = decision.date().isoformat()
    dates = calendar["sessions"]
    if day not in calendar["session_hours"] or day not in dates:
        return None, "Entry session is absent from the saved stock calendar"
    end = bisect_left(dates, day)
    if end < count:
        return None, f"Stock indicator needs {count} earlier consecutive session closes"
    required = dates[end - count : end]
    for prior in required:
        if prior not in series["bars"]:
            return None, f"Stock indicator is missing the {prior} session close"
        if datetime.fromisoformat(series["available_at"][prior]) > decision:
            return None, f"The {prior} stock close was not available before entry"
    return required, None


def _feature(record, decision, hypothesis):
    family = hypothesis["family"]
    if family in ("ema_trend", "momentum"):
        return legacy._feature(record, decision, hypothesis)
    if family == "conjunction":
        from research.prepared_stock_catalogue import component_if_owned

        registered = None
        parts = []
        for identifier in hypothesis["components"]:
            component = component_if_owned("indicator", identifier)
            if component is None:
                if registered is None:
                    registered = {row["id"]: row for row in catalog()}
                component = registered[identifier]
            feature, reason = _feature(record, decision, component)
            if feature is None:
                return None, f"{component['label']}: {reason}"
            parts.append(
                {
                    "hypothesis_id": identifier,
                    "passed": _compare(feature, component),
                    "feature": feature,
                }
            )
        passed = all(part["passed"] for part in parts)
        value = {
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
        value["basis_id"] = fingerprint(
            {
                "version": FEATURE_VERSION,
                "hypothesis": hypothesis,
                "components": [part["feature"]["basis_id"] for part in parts],
            }
        )
        return value, None
    count = hypothesis["lookback"] if family == "bollinger" else SMOOTHING_OBSERVATIONS
    required, reason = _window(record, decision, count)
    if required is None:
        return None, reason
    from openalgo import ta

    series = record["evidence"]
    prepared = legacy._prepared_window(record, decision, count)
    if prepared is not None:
        closes = list(prepared["closes"])
        highs, lows = prepared["highs"], prepared["lows"]
    else:
        bars = [series["bars"][day] for day in required]
        closes = [bar["close"] for bar in bars]
        highs, lows = (tuple(bar[field] for bar in bars) for field in ("high", "low"))
    result = {
        "lookback": hypothesis["lookback"],
        "threshold": hypothesis["threshold"],
        "observations": count,
        "from": required[0],
        "to": required[-1],
        "available_at": series["available_at"][required[-1]],
        "close": closes[-1],
    }
    if family == "rsi":
        result["value"] = float(ta.rsi(closes, period=hypothesis["lookback"])[-1])
        result["flat_window"] = all(close == closes[0] for close in closes)
    elif family == "atr":
        atr = float(
            ta.atr(
                list(highs),
                list(lows),
                closes,
                period=hypothesis["lookback"],
            )[-1]
        )
        result.update(atr=atr, value=100 * atr / closes[-1])
    else:
        upper, middle, lower = (
            float(values[-1])
            for values in ta.bbands(
                closes, period=hypothesis["lookback"], std_dev=hypothesis["std_dev"]
            )
        )
        if not all(isfinite(value) for value in (upper, middle, lower)):
            return None, "Native Bollinger Bands are outside finite calculation bounds"
        if upper <= lower or isclose(upper, lower, rel_tol=1e-12, abs_tol=1e-12):
            return None, "Bollinger Bands have no usable width in the required window"
        band = upper if hypothesis["operator"] == "gt" else lower
        result.update(upper=upper, middle=middle, lower=lower, value=closes[-1] - band)
    if not isfinite(result["value"]):
        return None, "Native stock indicator is outside finite calculation bounds"
    result["basis_id"] = legacy._basis_id(
        record, decision, required, hypothesis, FEATURE_VERSION, TESTED_SDK_VERSION
    )
    return result, None


def apply(evidence, bundle, hypothesis):
    selected = normalize_hypothesis(hypothesis)
    feature_recipe = recipe()  # Pins the native runtime before any opportunity is gated.
    return legacy._apply(
        evidence,
        bundle,
        selected,
        feature_fn=_feature,
        markers_fn=markers,
        compare_fn=_compare,
        calculation_version=FEATURE_VERSION,
        feature_recipe=feature_recipe,
    )
