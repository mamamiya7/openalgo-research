"""Finite causal regime context never consumes a decision's current/future close."""

import json
from copy import deepcopy
from datetime import datetime, timedelta

import numpy as np
import pytest

from research.market_series import fingerprint, freeze_series
from research.regimes import REQUIRED_SESSIONS, VERSION, classify, recipe


def evidence(prices, *, missing=(), closing="15:30"):
    # Explicit deterministic schedule; these fixtures make no real-calendar claim.
    days = [
        (datetime(2024, 1, 1) + timedelta(days=i)).date().isoformat() for i in range(len(prices))
    ]
    calendar = {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": closing} for day in days},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }
    descriptor = {"symbol": "NIFTY", "exchange": "NSE_INDEX", "interval": "D", "role": "benchmark"}
    bars = {
        day: {
            "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
            "open": float(value),
            "high": float(value) * 1.01,
            "low": float(value) * 0.99,
            "close": float(value),
        }
        for i, (day, value) in enumerate(zip(days, prices, strict=True))
        if i not in missing
    }
    return freeze_series(descriptor, days, calendar, bars)


def after(value, index=-1, clock="15:30:00.000001"):
    return value["required_dates"][index] + "T" + clock + "+05:30"


def growth(returns):
    return [100.0, *(100 * np.cumprod(1 + np.asarray(returns))).tolist()]


def row(value, index=-1):
    return classify(value, [after(value, index)])["timeline"][0]


def test_fixed_recipe_uses_standard_native_sma_and_bounded_descriptive_rules():
    first = recipe()
    assert first["version"] == VERSION
    assert first["id"] == fingerprint({k: v for k, v in first.items() if k != "id"})
    assert first["required_sessions"] == REQUIRED_SESSIONS == 150 <= 252
    assert first["moving_averages"] == {"windows": [20, 60], "ewm": False}
    assert first["percentiles"]["strictly_prior_observations"] == 90
    assert first["volatility"]["high_at_or_above_percentile"] == 90
    assert first["purpose"] == "descriptive market context"
    first["moving_averages"]["windows"][0] = 500
    assert recipe()["moving_averages"]["windows"] == [20, 60]


@pytest.mark.timeout(180)
def test_rows_and_fingerprints_are_identical_after_prefix_slice_or_future_perturbation():
    prices = growth([0.003 + 0.002 * np.sin(i) for i in range(299)])
    full = evidence(prices)
    before = deepcopy(full)
    decisions = [after(full, 149), after(full, 179), after(full, 199)]
    expected = classify(full, decisions)
    assert all(item["status"] == "available" for item in expected["timeline"])
    assert classify(evidence(prices[:200]), decisions) == expected
    revised = prices[:200] + [price * (2 if i % 2 else 0.1) for i, price in enumerate(prices[200:])]
    assert classify(evidence(revised, missing=(210, 220)), decisions) == expected
    assert classify(full, decisions + [after(full, 250)])["timeline"][:3] == expected["timeline"]
    assert full == before
    assert expected["id"] == fingerprint({k: v for k, v in expected.items() if k != "id"})


def test_scoped_window_has_same_values_and_identity_without_older_unused_history():
    prices = growth([0.002 + 0.001 * np.cos(i) for i in range(259)])
    full = evidence(prices)
    from research.market_series import slice_series

    selected = slice_series(full, full["required_dates"][-150], full["required_dates"][-1])
    assert classify(full, [after(full)]) == classify(selected, [after(full)])


@pytest.mark.parametrize("count", [1, 59, 60, 149])
def test_short_warmup_never_invents_classification(count):
    current = row(evidence([100 + i for i in range(count)]))
    assert current["status"] == "unknown"
    assert current["trend"] == current["volatility"] == current["stress"] == "unknown"
    assert current["history"]["recorded_sessions"] == count
    assert current["history"]["available_sessions"] == count
    assert current["features"]["volatility_percentile"] is None
    assert "150 completed sessions" in current["reasons"][0]


def test_missing_session_blocks_until_entire_required_history_is_valid_again():
    value = evidence([100 + i for i in range(300)], missing=(149,))
    timeline = classify(value, [after(value, 149), after(value, 298), after(value, 299)])[
        "timeline"
    ]
    for item in timeline[:2]:
        assert item["status"] == "unknown"
        assert item["history"]["missing_dates"] == [value["required_dates"][149]]
        assert item["trend"] == item["volatility"] == item["stress"] == "unknown"
    assert timeline[0]["observed_through"] == value["required_dates"][149]
    assert timeline[0]["features"]["close"] is None  # Never silently use yesterday's price.
    assert timeline[-1]["status"] == "available"
    assert timeline[-1]["history"]["missing_dates"] == []


def test_available_at_is_strict_and_respects_recorded_close_and_timezone():
    prices = [100 + i for i in range(151)]
    value = evidence(prices, closing="16:00")
    day = value["required_dates"][-1]
    first, exact, after_close = classify(
        value, [day + "T15:45:00+05:30", day + "T16:00:00+05:30", day + "T16:00:00.000001+05:30"]
    )["timeline"]
    assert first["observed_through"] == exact["observed_through"] == value["required_dates"][-2]
    assert first["features"]["close"] == exact["features"]["close"] == prices[-2]
    assert after_close["observed_through"] == day
    assert after_close["features"]["close"] == prices[-1]
    assert classify(value, [day + "T10:30:00Z"]) == classify(value, [day + "T16:00:00+05:30"])
    assert first["input_id"] == exact["input_id"] != after_close["input_id"]


def test_before_first_close_has_no_observation_or_future_identity_dependency():
    value = evidence([100] * 150)
    instant = value["required_dates"][0] + "T09:15:00+05:30"
    result = classify(value, [instant])
    early = result["timeline"][0]
    assert early["status"] == "unknown" and early["observed_through"] is None
    assert early["history"]["recorded_sessions"] == 0
    assert all(item is None for item in early["features"].values())
    assert classify(evidence([999] * 150), [instant]) == result


def test_flat_denominators_produce_zero_efficiency_and_neutral_tied_ranks():
    result = classify(evidence([100] * 160), [after(evidence([100] * 160))])
    current = result["timeline"][0]
    assert current["status"] == "available"
    assert (current["trend"], current["volatility"], current["stress"]) == (
        "range",
        "normal",
        "normal",
    )
    for key in ("momentum20_pct", "realized_vol20_pct", "efficiency20", "drawdown60_pct"):
        assert current["features"][key] == 0
    assert current["features"]["volatility_percentile"] == 50
    assert current["features"]["drawdown_percentile"] == 50
    assert current["reasons"] == ["No daily return variation in the latest 20 sessions."]
    json.dumps(result, allow_nan=False)


def test_uptrend_and_high_volatility_coexist_with_native_sma_math():
    returns = [0.001 + 0.00005 * np.sin(i) for i in range(159)] + [0.035, -0.007] * 10
    prices = growth(returns)
    current = row(evidence(prices))
    assert current["status"] == "available"
    assert current["trend"] == "up" and current["volatility"] == "high"
    assert current["stress"] == "normal"
    values = current["features"]
    assert values["sma20"] == pytest.approx(np.mean(prices[-20:]))
    assert values["sma60"] == pytest.approx(np.mean(prices[-60:]))
    assert values["momentum20_pct"] == pytest.approx((prices[-1] / prices[-21] - 1) * 100)
    assert values["realized_vol20_pct"] == pytest.approx(
        np.std(returns[-20:], ddof=1) * np.sqrt(252) * 100
    )


def test_stress_requires_corroborating_large_drawdown_and_high_relative_volatility():
    falling = growth([0.001 + 0.00002 * np.sin(i) for i in range(159)] + [-0.035, 0.005] * 10)
    stress = row(evidence(falling))
    assert (stress["trend"], stress["volatility"], stress["stress"]) == ("down", "high", "elevated")
    assert stress["features"]["drawdown60_pct"] >= 5
    assert stress["features"]["drawdown_percentile"] >= 90
    steady_decline = row(evidence(growth([0.001] * 99 + [-0.002] * 200)))
    assert steady_decline["features"]["drawdown60_pct"] >= 5
    assert steady_decline["volatility"] == "normal" and steady_decline["stress"] == "normal"


def test_percentile_excludes_current_feature_and_does_not_rank_flat_ties_as_extreme():
    current = row(evidence([100] * 149 + [105]))
    assert current["features"]["volatility_percentile"] == 100
    assert current["features"]["drawdown_percentile"] == 50
    assert current["volatility"] == "high"
    assert current["stress"] == "normal"  # Volatility alone is not stress evidence.


def test_mixed_trend_does_not_force_a_bullish_bearish_or_range_label():
    # Efficient but small price drift clears neither direction's fixed price gates.
    current = row(evidence(growth([0.0001] * 199)))
    assert current["status"] == "available" and current["trend"] == "unknown"
    assert current["features"]["efficiency20"] == pytest.approx(1)
    assert "Trend measures do not agree clearly." in current["reasons"]


@pytest.mark.parametrize(
    "instants",
    [
        [],
        None,
        [None],
        [False],
        [123],
        ["2024-05-30"],
        ["2024-05-30T15:30:00"],
        ["not-a-time"],
        ["2024-05-30T10:00:00Z", "2024-05-30T15:30:00+05:30"],
        ["2024-05-31T09:15:00+05:30", "2024-05-30T09:15:00+05:30"],
        ["2024-05-30T09:15:00+05:30"] * 3001,
    ],
)
def test_decision_instants_fail_closed_on_ambiguous_or_unbounded_requests(instants):
    with pytest.raises(ValueError, match="Regime"):
        classify(evidence([100] * 150), instants)


def test_rejects_stock_role_corruption_and_wrong_native_runtime(monkeypatch):
    value = evidence([100] * 150)
    stock = deepcopy(value)
    stock["descriptor"].update(exchange="NSE", role="feature_warmup")
    stock["provenance"]["exchange"] = "NSE"
    stock["id"] = fingerprint({k: v for k, v in stock.items() if k != "id"})
    with pytest.raises(ValueError, match="index benchmark"):
        classify(stock, [after(stock)])
    broken = deepcopy(value)
    broken["bars"][value["required_dates"][-1]]["close"] += 1
    with pytest.raises(ValueError, match="changed"):
        classify(broken, [after(broken)])
    import vectorbt as vbt

    monkeypatch.setattr(vbt, "__version__", "future")
    with pytest.raises(ValueError, match="0.28.5"):
        classify(value, [after(value)])
