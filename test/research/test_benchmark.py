"""Hand-checkable aligned context, absent candles and strictly separate periods."""

from copy import deepcopy

import numpy as np
import pytest
from test_market_series import calendar, candle, descriptor

from research.benchmark import add_benchmark, comparison
from research.market_series import freeze_series

DAYS = [f"2026-01-{day:02d}" for day in range(5, 12)]


def inputs(account_returns=(0.01, -0.02, 0.03, 0.01), market_returns=(0.005, -0.01, 0.015, 0.005)):
    days = DAYS[: len(account_returns) + 1]
    account = 10000 * np.cumprod(1 + np.array(account_returns))
    market = np.r_[100.0, 100 * np.cumprod(1 + np.array(market_returns))]
    report = {
        "summary": {
            "initial_capital": 10000,
            "net_return_pct": float((account[-1] / 10000 - 1) * 100),
        },
        "execution": {"engine": "vectorbt", "interval": "D"},
        "equity_curve": [
            {"date": day, "equity": float(value)}
            for day, value in zip(days[1:], account, strict=True)
        ],
        "experiment": {"recommendation_id": "original", "rows": [{"score": 77}]},
    }
    evidence = freeze_series(
        descriptor(),
        days,
        calendar(days),
        {day: candle(day, float(value)) for day, value in zip(days, market, strict=True)},
    )
    return report, evidence


def test_native_beta_alpha_and_aligned_returns_are_hand_checkable():
    report, market = inputs()
    details, traces = comparison(report, market)
    assert details["status"] == "available" and details["observations"] == 4
    metrics = details["metrics"]
    assert metrics["beta"] == pytest.approx(2)
    assert metrics["alpha_pct"] == pytest.approx(0, abs=1e-8)
    assert metrics["correlation"] == pytest.approx(1)
    assert metrics["portfolio_return_pct"] == pytest.approx((1.01 * 0.98 * 1.03 * 1.01 - 1) * 100)
    assert metrics["benchmark_return_pct"] == pytest.approx(
        (1.005 * 0.99 * 1.015 * 1.005 - 1) * 100
    )
    active = np.array([0.005, -0.01, 0.015, 0.005])
    assert metrics["tracking_error_pct"] == pytest.approx(
        np.std(active, ddof=1) * np.sqrt(252) * 100
    )
    assert metrics["information_ratio"] == pytest.approx(
        np.mean(active) / np.std(active, ddof=1) * np.sqrt(252)
    )
    assert traces[0]["x"][0] == DAYS[0] and traces[0]["y"][0] == 0


def test_missing_middle_price_never_bridges_or_forward_fills_and_earliest_tie_wins():
    report, market = inputs(
        (0.01, 0.02, -0.01, 0.03, 0.04, -0.02), (0.01, 0.01, 0.01, 0.01, 0.01, 0.01)
    )
    bars = {day: bar for day, bar in market["bars"].items() if day != DAYS[3]}
    missing = freeze_series(descriptor(), DAYS, calendar(DAYS), bars)
    details, traces = comparison(report, missing)
    assert details["status"] == "partial"
    assert details["observations"] == 2 and details["omitted_sessions"] == 4
    assert details["dates"] == {"from": DAYS[1], "to": DAYS[2]}
    assert traces[0]["x"] == DAYS[:3]
    assert details["metrics"]["portfolio_return_pct"] == pytest.approx(3.02)


def test_report_and_series_future_data_cannot_change_earlier_comparison():
    report, market = inputs()
    report["equity_curve"] = report["equity_curve"][:2]
    expected = comparison(report, market)
    changed = freeze_series(
        descriptor(),
        market["required_dates"],
        calendar(market["required_dates"]),
        {**market["bars"], DAYS[4]: candle(DAYS[4], 10000)},
    )
    assert comparison(report, changed) == expected


def test_partial_intraday_final_mark_cannot_use_later_index_close():
    report, market = inputs()
    report["execution"]["interval"] = "1m"
    for point in report["equity_curve"]:
        point["timestamp"] = point["date"] + "T15:29:00+05:30"
    report["equity_curve"][-1]["timestamp"] = DAYS[4] + "T10:00:00+05:30"
    details, _ = comparison(report, market)
    assert details["status"] == "partial" and details["observations"] == 3
    assert details["dates"]["to"] == DAYS[3]


def test_partial_previous_day_cannot_supply_overnight_return_endpoint():
    report, market = inputs()
    report["execution"]["interval"] = "1m"
    for point in report["equity_curve"]:
        point["timestamp"] = point["date"] + "T15:29:00+05:30"
    report["equity_curve"][0]["timestamp"] = DAYS[1] + "T10:00:00+05:30"
    details, _ = comparison(report, market)
    assert details["observations"] == 2 and details["dates"]["from"] == DAYS[3]


def test_identical_paths_have_zero_tracking_error_and_undefined_information_ratio():
    report, market = inputs((0.01, -0.02, 0.03, 0.01), (0.01, -0.02, 0.03, 0.01))
    details, _ = comparison(report, market)
    assert details["metrics"]["excess_return_pct"] == pytest.approx(0)
    assert details["metrics"]["tracking_error_pct"] == pytest.approx(0)
    assert details["metrics"]["information_ratio"] is None


def test_sparse_or_flat_reference_has_honest_unavailable_metrics():
    report, market = inputs(market_returns=(0, 0, 0, 0))
    details, _ = comparison(report, market)
    assert details["metrics"]["beta"] is None and details["metrics"]["correlation"] is None
    missing = freeze_series(
        descriptor(), market["required_dates"], calendar(market["required_dates"]), {}
    )
    details, traces = comparison(report, missing)
    assert details["status"] == "unavailable" and traces is None
    assert all(value is None for value in details["metrics"].values())


def test_overlay_preserves_original_account_statistics_and_search_scores():
    report, market = inputs()
    analysis = {
        "version": "research-analysis-v2",
        "catalog": [],
        "metrics": {"account_return": 77},
        "charts": [{"id": "benchmark-comparison", "status": "unavailable"}],
        "unavailable": {},
        "basis": [],
    }
    before = deepcopy((report, market, analysis))
    result = add_benchmark(analysis, report, market)
    assert (report, market, analysis) == before
    assert result["metrics"]["account_return"] == 77
    assert len([row for row in result["charts"] if row["id"] == "benchmark-comparison"]) == 1
    assert result["benchmark"]["metrics"]["portfolio_return_pct"] != 77
    assert add_benchmark(result, report, market) == result


def test_missing_account_observation_breaks_comparison_instead_of_spanning_gap():
    report, market = inputs(
        (0.01, 0.02, -0.01, 0.03, 0.04, -0.02), (0.01, 0.02, 0.01, 0.02, 0.01, 0.02)
    )
    report["equity_curve"] = [row for row in report["equity_curve"] if row["date"] != DAYS[3]]
    details, _ = comparison(report, market)
    assert details["observations"] == 2
    assert details["dates"] == {"from": DAYS[1], "to": DAYS[2]}
