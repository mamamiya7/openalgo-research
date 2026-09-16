"""Descriptive market context from completed, immutable daily index observations.

This module never requests data, scores a strategy or makes a trading decision.
Every classification uses a finite, contiguous window strictly before its as-of
instant. Trend, volatility and stress are deliberately separate dimensions.
"""

from bisect import bisect_left
from copy import deepcopy
from datetime import datetime
from math import isfinite

from research.market_series import IST, MAX_SESSIONS, fingerprint, validate_series

VERSION = "research-regimes-v1"
FEATURE_SESSIONS = 60
RANK_OBSERVATIONS = 90
REQUIRED_SESSIONS = FEATURE_SESSIONS + RANK_OBSERVATIONS
FEATURES = (
    "close",
    "sma20",
    "sma60",
    "momentum20_pct",
    "realized_vol20_pct",
    "efficiency20",
    "drawdown60_pct",
    "volatility_percentile",
    "drawdown_percentile",
)


def recipe():
    """Return a fresh, auditable fixed recipe; thresholds are not fitted evidence."""
    values = {
        "version": VERSION,
        "purpose": "descriptive market context",
        "engine": {"name": "VectorBT", "version": "0.28.5", "moving_average": "MA"},
        "required_sessions": REQUIRED_SESSIONS,
        "moving_averages": {"windows": [20, 60], "ewm": False},
        "momentum_return_sessions": 20,
        "realized_volatility": {"return_sessions": 20, "annual_sessions": 252, "ddof": 1},
        "efficiency": {"change_sessions": 20, "zero_path_value": 0.0},
        "drawdown_close_sessions": 60,
        "percentiles": {
            "strictly_prior_observations": RANK_OBSERVATIONS,
            "formula": "100 * (less + 0.5 * equal) / observations",
            "tie_relative_tolerance": 1e-9,
            "tie_absolute_tolerance": 1e-12,
        },
        "trend": {
            "minimum_sma_gap_pct": 0.5,
            "minimum_momentum_pct": 1.0,
            "minimum_directional_efficiency": 0.30,
            "maximum_range_efficiency": 0.20,
            "agreement": "SMA20/SMA60 direction and momentum must agree; otherwise unknown",
        },
        "volatility": {"high_at_or_above_percentile": 90.0},
        "stress": {
            "minimum_drawdown_pct": 5.0,
            "minimum_drawdown_percentile": 90.0,
            "requires_high_volatility": True,
        },
        "availability": "recorded daily session close strictly before decision time",
        "missing_values": "unknown until a complete contiguous required-session window exists",
    }
    return {**values, "id": fingerprint(values)}


def _decisions(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_SESSIONS:
        raise ValueError("Regime context needs 1–3000 decision timestamps")
    result = []
    for value in values:
        try:
            if not isinstance(value, str):
                raise ValueError
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError
            result.append(stamp.astimezone(IST))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Regime decisions require explicit timezone-aware timestamps") from exc
    if any(left >= right for left, right in zip(result, result[1:], strict=False)):
        raise ValueError("Regime decisions must be unique and chronological")
    return result


def _features(evidence):
    """Batch independent finite windows through the pinned native SMA indicator."""
    import numpy as np
    import vectorbt as vbt

    if vbt.__version__ != "0.28.5":
        raise ValueError("Regime context requires the pinned VectorBT 0.28.5 runtime")
    days, bars = evidence["required_dates"], evidence["bars"]
    result = [None] * len(days)
    if len(days) < FEATURE_SESSIONS:
        return result
    prices = np.asarray([bars[day]["close"] if day in bars else np.nan for day in days])
    windows = np.lib.stride_tricks.sliding_window_view(prices, FEATURE_SESSIONS)
    valid = np.flatnonzero(np.isfinite(windows).all(axis=1))
    if not len(valid):
        return result
    # Columns are independent 60-close windows, so an arbitrary archive start
    # cannot alter a day's SMA or the 90 earlier observations used to rank it.
    close = windows[valid].T.copy()
    fast = vbt.MA.run(close, window=20, ewm=False).ma.to_numpy()[-1]
    slow = vbt.MA.run(close, window=60, ewm=False).ma.to_numpy()[-1]
    recent = close[-21:]
    returns = recent[1:] / recent[:-1] - 1
    change = np.abs(recent[-1] - recent[0])
    path = np.abs(np.diff(recent, axis=0)).sum(axis=0)
    efficiency = np.divide(change, path, out=np.zeros_like(change), where=path > 0)
    momentum = (recent[-1] / recent[0] - 1) * 100
    volatility = np.std(returns, axis=0, ddof=1) * np.sqrt(252) * 100
    drawdown = np.maximum(0.0, (1 - close[-1] / np.max(close, axis=0)) * 100)
    for column, start in enumerate(valid):
        values = (
            close[-1, column],
            fast[column],
            slow[column],
            momentum[column],
            volatility[column],
            efficiency[column],
            drawdown[column],
        )
        if not all(isfinite(float(value)) for value in values):
            continue
        result[start + FEATURE_SESSIONS - 1] = dict(
            zip(FEATURES[:7], map(float, values), strict=True)
        )
    return result


def _percentile(value, earlier):
    import numpy as np

    observed = np.asarray(earlier)
    tied = np.isclose(observed, value, rtol=1e-9, atol=1e-12)
    less = (observed < value) & ~tied
    return float(100 * (np.count_nonzero(less) + 0.5 * np.count_nonzero(tied)) / len(observed))


def _input_id(evidence, days):
    # Deliberately omit the parent evidence id and all future observations.
    return fingerprint(
        {
            "version": evidence["version"],
            "descriptor": evidence["descriptor"],
            "required_dates": days,
            "bars": {day: evidence["bars"][day] for day in days if day in evidence["bars"]},
            "available_at": {day: evidence["available_at"][day] for day in days},
            "provenance": evidence["provenance"],
        }
    )


def classify(evidence, decision_times):
    """Classify each decision using only earlier completed recorded index closes.

    The supplied series can include later data. Neither a row's feature values
    nor its identity incorporates anything beyond the eligible 150 sessions.
    """
    validate_series(evidence)
    descriptor = evidence["descriptor"]
    if descriptor["exchange"] != "NSE_INDEX" or descriptor["role"] != "benchmark":
        raise ValueError("Market regime context requires a named daily NSE index benchmark")
    decisions = _decisions(decision_times)
    features = _features(evidence)
    days = evidence["required_dates"]
    available = [datetime.fromisoformat(evidence["available_at"][day]) for day in days]
    timeline = []
    for decision in decisions:
        index = bisect_left(available, decision) - 1
        selected = days[max(0, index + 1 - REQUIRED_SESSIONS) : index + 1]
        missing = [day for day in selected if day not in evidence["bars"]]
        row = {
            "decision_at": decision.isoformat(),
            "observed_through": days[index] if index >= 0 else None,
            "input_id": _input_id(evidence, selected),
            "status": "unknown",
            "trend": "unknown",
            "volatility": "unknown",
            "stress": "unknown",
            "features": dict.fromkeys(FEATURES),
            "history": {
                "required_sessions": REQUIRED_SESSIONS,
                "recorded_sessions": len(selected),
                "available_sessions": len(selected) - len(missing),
                "missing_dates": missing,
            },
            "reasons": [],
        }
        current = features[index] if index >= 0 else None
        if current:
            row["features"].update(current)
        if index < 0:
            row["reasons"].append("No recorded index session had closed before this decision.")
        elif len(selected) < REQUIRED_SESSIONS:
            row["reasons"].append(
                f"Needs {REQUIRED_SESSIONS} completed sessions; {len(selected)} are recorded."
            )
        if missing:
            row["reasons"].append(f"Missing prices for {len(missing)} required sessions.")
        if row["reasons"]:
            timeline.append(row)
            continue
        prior = features[index - RANK_OBSERVATIONS : index]
        if current is None or len(prior) != RANK_OBSERVATIONS or any(x is None for x in prior):
            row["reasons"].append("The required feature observations could not be calculated.")
            timeline.append(row)
            continue
        values = row["features"]
        values["volatility_percentile"] = _percentile(
            current["realized_vol20_pct"], [item["realized_vol20_pct"] for item in prior]
        )
        values["drawdown_percentile"] = _percentile(
            current["drawdown60_pct"], [item["drawdown60_pct"] for item in prior]
        )
        gap = (values["sma20"] / values["sma60"] - 1) * 100
        if values["efficiency20"] <= 0.20:
            row["trend"] = "range"
        elif values["efficiency20"] >= 0.30:
            if gap >= 0.5 and values["momentum20_pct"] >= 1.0:
                row["trend"] = "up"
            elif gap <= -0.5 and values["momentum20_pct"] <= -1.0:
                row["trend"] = "down"
        if row["trend"] == "unknown":
            row["reasons"].append("Trend measures do not agree clearly.")
        if values["realized_vol20_pct"] == 0:
            row["reasons"].append("No daily return variation in the latest 20 sessions.")
        row["volatility"] = "high" if values["volatility_percentile"] >= 90 else "normal"
        row["stress"] = (
            "elevated"
            if (
                row["volatility"] == "high"
                and values["drawdown60_pct"] >= 5
                and values["drawdown_percentile"] >= 90
            )
            else "normal"
        )
        row["status"] = "available"
        timeline.append(row)
    result = {
        "version": VERSION,
        "descriptor": deepcopy(descriptor),
        "recipe": recipe(),
        "timeline": timeline,
        "basis": [
            "Uses only index closes available strictly before each decision; no current or future close enters the label.",
            "VectorBT 0.28.5 SMA20/SMA60; 20-session close momentum, return volatility and path efficiency; 60-close drawdown.",
            "Volatility and drawdown ranks use 90 strictly earlier valid feature observations; tied values receive their midpoint rank.",
            "Requires 150 consecutive recorded sessions without missing prices. Volatility uses 252 sessions/year and sample standard deviation.",
            "Fixed descriptive thresholds are separate from strategy selection, trade rules and optimization scores.",
        ],
        "limitations": [
            "These labels describe recent prices; they are not forecasts or confidence estimates.",
            "Range does not establish mean reversion. An uptrend can also have high volatility.",
            "Elevated stress means drawdown and volatility coincide; it does not identify a certain crisis.",
            "Provider-native index prices and recorded session-close availability are retained assumptions; actual broker arrival time and total-return treatment are not independently verified.",
            "No automated orders, leverage advice or live market monitoring is produced.",
        ],
    }
    return {**result, "id": fingerprint(result)}
