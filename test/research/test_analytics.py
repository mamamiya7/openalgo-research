"""Saved evidence and actual pinned-provider numerical analysis invariants."""

import copy
import importlib.util
import json
import math
import sys
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from research.analytics import MAX_CHART_POINTS, MAX_REPORT_ROWS, add_price_charts, build_analysis

pytestmark = pytest.mark.timeout(240)
vectorbt = pytest.mark.skipif(
    not importlib.util.find_spec("vectorbt"), reason="VectorBT optional runtime"
)
nautilus = pytest.mark.skipif(
    sys.platform != "linux" or not importlib.util.find_spec("nautilus_trader"),
    reason="Nautilus isolated Linux runtime",
)


def saved(values=(1100, 990, 1039.5), *, minute=False):
    peak = 1000
    curve = []
    for i, value in enumerate(values):
        day = (date(2026, 1, 5) + timedelta(days=i)).isoformat()
        if minute:
            curve.append(
                {
                    "date": day,
                    "timestamp": day + "T09:15:00+05:30",
                    "equity": value * 0.99,
                    "cash": 300,
                    "drawdown_pct": 0,
                }
            )
        peak = max(peak, value)
        curve.append(
            {
                "date": day,
                **({"timestamp": day + "T15:29:00+05:30"} if minute else {}),
                "equity": value,
                "cash": 300,
                "drawdown_pct": (peak - value) / peak * 100,
            }
        )
    return {
        "summary": {
            "initial_capital": 1000,
            "final_equity": values[-1] if values else 1000,
            "max_drawdown_pct": 10,
            "net_return_pct": 3.95,
            "profit_factor": None,
        },
        "equity_curve": curve,
        "ledger": [],
        "execution": {"interval": "1m" if minute else "D"},
    }


def assert_contract(analysis):
    json.dumps(analysis, allow_nan=False)
    keys = [item["key"] for item in analysis["catalog"]]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(analysis["metrics"])
    assert {key for key, value in analysis["metrics"].items() if value is None} == set(
        analysis["unavailable"]
    )


@pytest.mark.parametrize("minute", [False, True])
def test_actual_eod_returns_include_initial_mark_and_are_not_minute_annualized(minute):
    result = build_analysis(saved(minute=minute))
    metrics = result["metrics"]
    expected = [0.1, -0.1, 0.05]
    average = sum(expected) / 3
    stdev = math.sqrt(sum((value - average) ** 2 for value in expected) / 2)
    assert metrics["account_return_sessions"] == 3
    assert metrics["account_sharpe_ratio"] == pytest.approx(average / stdev * math.sqrt(252))
    assert metrics["account_annualized_return_pct"] == pytest.approx(
        ((1039.5 / 1000) ** 84 - 1) * 100
    )
    assert metrics["account_value_at_risk_pct"] == pytest.approx(-8.5)
    assert metrics["account_expected_shortfall_pct"] == pytest.approx(-10)
    assert_contract(result)


@pytest.mark.parametrize("values", [(1000, 1000, 1000), (1100, 1210, 1331), ()])
def test_constant_or_absent_returns_have_no_infinite_ratios(values):
    result = build_analysis(saved(values))
    assert result["metrics"]["account_sharpe_ratio"] is None
    assert result["metrics"]["account_sortino_ratio"] is None
    assert result["metrics"]["account_profit_factor"] is None
    assert result["metrics"]["account_trade_expectancy"] is None
    assert_contract(result)


def test_zero_equity_does_not_silently_remove_undefined_return():
    result = build_analysis(saved((0, 100)))
    assert result["metrics"]["account_return_sessions"] == 2
    assert result["metrics"]["account_sharpe_ratio"] is None
    assert_contract(result)


def chart_by_id(result, identity):
    return next(chart for chart in result["charts"] if chart["id"] == identity)


def test_v2_nonpositive_prior_equity_is_undefined_without_rewriting_v1_evidence():
    report = saved((-100, -200, 100))
    report["analysis"] = {
        "version": "research-analysis-v1",
        "metrics": {"account_sharpe_ratio": 7},
    }
    before = copy.deepcopy(report)
    result = build_analysis(report)
    assert result["version"] == "research-analysis-v2"
    assert report == before
    assert result["metrics"]["account_net_return_pct"] == before["summary"]["net_return_pct"]
    assert result["metrics"]["account_return_sessions"] == 3
    for key in (
        "sharpe_ratio",
        "sortino_ratio",
        "annualized_return_pct",
        "annualized_volatility_pct",
        "value_at_risk_pct",
    ):
        assert result["metrics"]["account_" + key] is None
        assert "prior account equity is nonpositive" in result["unavailable"]["account_" + key]
    assert result["report_depth"]["daily_return_quantiles"]["status"] == "unavailable"
    assert chart_by_id(result, "daily-return-distribution")["status"] == "unavailable"
    assert result["report_depth"]["drawdowns"]["rows"][0]["depth_pct"] == 120
    assert_contract(result)


def test_daily_return_quantiles_use_all_eod_observations_and_linear_interpolation():
    result = build_analysis(saved(minute=True))
    quantiles = result["report_depth"]["daily_return_quantiles"]
    assert quantiles["observations"] == 3
    assert quantiles["method"] == "linear"
    assert [row["percentile"] for row in quantiles["rows"]] == [0, 5, 25, 50, 75, 95, 100]
    assert [row["return_pct"] for row in quantiles["rows"]] == pytest.approx(
        [-10, -8.5, -2.5, 5, 7.5, 9.5, 10]
    )
    assert "report_depth" not in build_analysis(saved(), charts=False)


def test_drawdown_episodes_distinguish_initial_peak_recovery_and_ongoing_episode():
    report = saved((900, 800, 1000, 1100, 880, 990))
    result = build_analysis(report)
    episodes = result["report_depth"]["drawdowns"]
    assert episodes["total"] == 2
    first, second = episodes["rows"]
    assert first["peak_at"] is None
    assert first["start_at"] == "2026-01-05"
    assert first["trough_at"] == "2026-01-06"
    assert first["recovered_at"] == "2026-01-07"
    assert first["underwater_bars"] == first["underwater_sessions"] == 2
    assert first["recovery_sessions"] == first["recovery_days"] == 1
    assert first["depth_pct"] == second["depth_pct"] == 20
    assert second["status"] == "ongoing"
    assert second["end_at"] == "2026-01-10"
    assert second["recovered_at"] is second["recovery_sessions"] is second["recovery_days"] is None


def test_drawdowns_use_intraday_marks_even_when_session_close_fully_recovers():
    report = saved((1100,))
    report["execution"]["interval"] = "1m"
    report["equity_curve"] = [
        {
            "date": "2026-01-05",
            "timestamp": f"2026-01-05T{hour}:00:00+05:30",
            "equity": equity,
            "cash": 0,
            "drawdown_pct": 50 if equity == 550 else 0,
        }
        for hour, equity in (("09", 1000), ("10", 1100), ("11", 550), ("12", 1100))
    ]
    result = build_analysis(report)
    episode = result["report_depth"]["drawdowns"]["rows"][0]
    assert episode["peak_at"] == "2026-01-05T10:00:00+05:30"
    assert episode["trough_at"] == "2026-01-05T11:00:00+05:30"
    assert episode["depth_pct"] == 50
    assert episode["underwater_bars"] == episode["underwater_sessions"] == 1
    assert episode["recovery_sessions"] == 0
    assert episode["recovery_days"] == pytest.approx(1 / 24)
    assert result["report_depth"]["daily_return_quantiles"]["rows"][0][
        "return_pct"
    ] == pytest.approx(10)


@pytest.mark.parametrize("values,initial", [((), 1000), ((100,), 0), ((-100,), -1000)])
def test_drawdowns_require_dated_marks_and_positive_starting_capital(values, initial):
    report = saved(values)
    report["summary"]["initial_capital"] = initial
    result = build_analysis(report)
    assert result["report_depth"]["drawdowns"]["status"] == "unavailable"
    assert result["report_depth"]["drawdowns"]["total"] == 0
    assert_contract(result)


@pytest.mark.parametrize(
    "ledger",
    [[], [{"status": "open", "pnl": 20, "fees": 1}], [{"status": "closed", "pnl": 10, "fees": 1}]],
)
def test_risk_details_are_account_observations_not_trade_outcomes(ledger):
    report = saved((1000, 1000, 1000))
    report["ledger"] = ledger
    result = build_analysis(report)
    assert result["report_depth"]["drawdowns"]["rows"] == []
    assert result["report_depth"]["daily_return_quantiles"]["rows"] == [
        {"percentile": value, "return_pct": 0} for value in (0, 5, 25, 50, 75, 95, 100)
    ]
    assert_contract(result)


def equity_from_returns(returns):
    equity = 1000
    values = []
    for value in returns:
        equity *= 1 + value
        values.append(equity)
    return values


@pytest.mark.parametrize("window", [21, 63, 126])
def test_rolling_risk_uses_complete_session_windows_with_explicit_units(window):
    returns = [-0.01, 0.02, 0.005] * 50
    result = build_analysis(saved(equity_from_returns(returns)))
    tail = returns[-window:]
    average = sum(tail) / window
    std = math.sqrt(sum((value - average) ** 2 for value in tail) / (window - 1))
    downside = math.sqrt(sum(min(value, 0) ** 2 for value in tail) / window)
    expected = {
        "sharpe": average / std * math.sqrt(252),
        "volatility": std * math.sqrt(252) * 100,
        "sortino": average / downside * math.sqrt(252),
    }
    for key, value in expected.items():
        chart = chart_by_id(result, "rolling-" + key + (f"-{window}" if window != 21 else ""))
        series = chart["figure"]["data"][0]["y"]
        assert series[: window - 1] == [None] * (window - 1)
        assert series[-1] == pytest.approx(value)
        assert chart["figure"]["layout"]["meta"]["window_sessions"] == window
        assert chart["figure"]["layout"]["yaxis"]["title"] == (
            "Annualized %" if key == "volatility" else "Ratio"
        )


def test_short_constant_and_loss_free_rolling_windows_are_honest():
    short = build_analysis(saved(equity_from_returns([0.01] * 20)))
    assert all(
        chart["status"] == "unavailable"
        for chart in short["charts"]
        if chart["id"].startswith("rolling-")
    )
    constant = build_analysis(saved([1000] * 21))
    assert chart_by_id(constant, "rolling-volatility")["figure"]["data"][0]["y"][-1] == 0
    assert chart_by_id(constant, "rolling-sharpe")["status"] == "unavailable"
    all_win = build_analysis(saved(equity_from_returns([0.01, 0.02, 0.03] * 7)))
    assert chart_by_id(all_win, "rolling-sortino")["status"] == "unavailable"
    assert chart_by_id(all_win, "rolling-sharpe")["status"] == "available"
    assert chart_by_id(all_win, "rolling-sharpe-63")["status"] == "unavailable"


def test_rolling_invalid_observations_are_kept_until_a_complete_window_recovers():
    values = equity_from_returns([-0.01, 0.02, 0.005] * 25)
    values[25:27] = [-100, -200]
    result = build_analysis(saved(values))
    rolling = chart_by_id(result, "rolling-volatility")["figure"]["data"][0]["y"]
    # Index25 is a defined loss from positive capital; 26 and 27 have negative
    # prior equity. A new complete21-observation window first ends at index48.
    assert rolling[25] is not None
    assert rolling[26:48] == [None] * 22
    assert rolling[48] is not None
    assert result["metrics"]["account_annualized_volatility_pct"] is None
    assert result["report_depth"]["daily_return_quantiles"]["status"] == "unavailable"


def test_large_curve_retains_worst_episode_and_extrema_with_bounded_output():
    values = [1000 if i % 2 == 0 else 990 for i in range(5000)]
    values[4997] = -500
    report = saved(values)
    before = copy.deepcopy(report)
    result = build_analysis(report)
    episodes = result["report_depth"]["drawdowns"]
    assert episodes["total"] == 2500
    assert episodes["shown"] == MAX_REPORT_ROWS
    assert episodes["truncated"] is True
    assert episodes["rows"][0]["depth_pct"] == 150
    assert episodes["rows"][0]["trough_index"] == 4997
    plotted = chart_by_id(result, "account-equity")["figure"]["data"][0]
    assert -500 in plotted["y"]
    assert plotted["x"][-1] == report["equity_curve"][-1]["date"]
    table = chart_by_id(result, "drawdown-episodes")["figure"]["data"][0]
    assert len(table["cells"]["values"][0]) == 5
    for chart in result["charts"]:
        for trace in chart.get("figure", {}).get("data", []):
            if trace["type"] == "scatter":
                assert len(trace["x"]) <= MAX_CHART_POINTS
    assert report == before
    assert_contract(result)


def test_sampled_rolling_charts_do_not_bridge_undefined_equity_return_windows():
    values = equity_from_returns([-0.001, 0.002, 0.0005] * 1700)
    values[2750] = 0
    report = saved(values)
    result = build_analysis(report)
    line = chart_by_id(result, "rolling-volatility")["figure"]["data"][0]
    undefined_at = report["equity_curve"][2751]["date"] + "T00:00:00"
    assert line["y"][line["x"].index(undefined_at)] is None
    assert len(line["x"]) <= MAX_CHART_POINTS


def test_old_saved_report_is_immutable_and_needs_no_simulation_or_broker():
    report = saved()
    report["execution"]["engine"] = "nautilus"
    before = copy.deepcopy(report)
    result = build_analysis(report, charts=False)
    assert report == before
    assert len(result["metrics"]) == 88
    assert result["charts"] == []
    assert "replay" in result["unavailable"]["nautilus_pnl_avgwinner"]
    assert_contract(result)


def test_saved_native_values_are_preserved_when_enriching_again():
    report = saved()
    report["analysis"] = {
        "version": "research-analysis-v1",
        "catalog": [
            {
                "key": "nautilus_pnl_avgwinner",
                "label": "Average winner",
                "group": "Nautilus P&L",
                "format": "money",
                "source": "Native saved analyzer",
                "description": "Native meaning",
            }
        ],
        "metrics": {"nautilus_pnl_avgwinner": 42},
        "unavailable": {},
        "charts": [],
    }
    result = build_analysis(report)
    assert result["metrics"]["nautilus_pnl_avgwinner"] == 42
    assert report["analysis"]["metrics"] == {"nautilus_pnl_avgwinner": 42}
    assert_contract(result)


def test_return_charts_and_price_chart_are_bounded_without_changing_evidence():
    values = tuple(1000 + i % 30 for i in range(2500))
    report = saved(values)
    source = copy.deepcopy(report)
    analysis = build_analysis(report)
    for chart in analysis["charts"]:
        for trace in chart.get("figure", {}).get("data", []):
            if trace["type"] == "scatter":
                assert len(trace["x"]) <= MAX_CHART_POINTS
    prices = {
        point["date"]: {"open": 100, "high": 105, "low": 95, "close": 101}
        for point in report["equity_curve"]
    }
    frozen = {"bars": {"AAA": prices}, "provenance": {"interval": "D"}}
    result = add_price_charts(analysis, report, frozen)
    assert result["price_symbols"] == ["AAA"]
    assert result["price_symbol"] == "AAA"
    chart = next(item for item in result["charts"] if item["id"] == "bars-with-fills")
    assert len(chart["figure"]["data"][0]["x"]) <= MAX_CHART_POINTS
    assert chart["figure"]["data"][0]["x"][-1] == sorted(prices)[-1]
    assert report == source
    assert "price_symbol" not in analysis
    assert_contract(result)


def test_price_chart_uses_frozen_bars_and_real_recorded_fills():
    report = saved()
    report["ledger"] = [
        {
            "symbol": "AAA",
            "quantity": 2,
            "status": "closed",
            "entry_date": "2026-01-05",
            "exit_date": "2026-01-06",
            "entry_price": 101.5,
            "exit_price": 98.5,
        }
    ]
    frozen = {
        "bars": {"AAA": {"2026-01-05": {"open": 100, "high": 105, "low": 99, "close": 103}}},
        "provenance": {"interval": "D"},
    }
    result = add_price_charts(build_analysis(report), report, frozen)
    chart = next(item for item in result["charts"] if item["id"] == "bars-with-fills")
    candle, entry, exit = chart["figure"]["data"]
    assert candle["close"] == [103]
    assert entry["y"] == [101.5]
    assert exit["y"] == [98.5]
    with pytest.raises(ValueError, match="retained price"):
        add_price_charts(result, report, frozen, "INVALID")


@pytest.mark.parametrize("minute", [False, True])
def test_price_chart_is_clipped_to_report_partition_and_symbol_scope(minute):
    report = saved(minute=minute)
    report["ledger"] = [{"symbol": "AAA", "quantity": 0, "status": "pending"}]
    keys = [point.get("timestamp", point["date"]) for point in report["equity_curve"]]
    candle = {"open": 100, "high": 105, "low": 99, "close": 103}
    prices = dict.fromkeys(["2025-12-31", *keys, "2026-02-15"], candle)
    frozen = {
        "bars": {"AAA": prices, "LATER_ONLY": prices},
        "provenance": {"interval": "1m" if minute else "D"},
    }
    analysis = build_analysis(report)
    once = add_price_charts(analysis, report, frozen)
    twice = add_price_charts(once, report, frozen)
    assert once == twice
    assert twice["price_symbols"] == ["AAA"]
    chart = next(item for item in twice["charts"] if item["id"] == "bars-with-fills")
    assert chart["figure"]["data"][0]["x"] == keys
    with pytest.raises(ValueError, match="retained price"):
        add_price_charts(analysis, report, frozen, "LATER_ONLY")


@vectorbt
@pytest.mark.parametrize("minute", [False, True])
def test_vectorbt_full_registries_preserve_shared_account_and_daily_risk(minute):
    from test_vectorbt_portfolio import DAYS, adapter, candle, snapshot, strategy

    prices = snapshot(minute=minute)
    for key in prices["bars"]["AAA"]:
        if key.startswith(DAYS[1]):
            prices["bars"]["AAA"][key] = candle(100, 111, 99, 108)
    result = adapter.evaluate(
        [strategy("a", target_pct=10, stop_pct=20), strategy("b", target_pct=30, stop_pct=20)],
        prices,
        1000,
    )
    analysis = result["analysis"]
    metrics = analysis["metrics"]
    assert len(metrics) == 121  # 27 account + 28/20/25/21 native registry entries.
    assert metrics["vectorbt_portfolio_start_value"] == 1000
    assert metrics["vectorbt_portfolio_end_value"] == result["summary"]["final_equity"]
    assert metrics["vectorbt_trades_total_records"] == 2
    assert metrics["vectorbt_returns_period"] == 4
    assert metrics["vectorbt_returns_sharpe_ratio"] == pytest.approx(
        metrics["account_sharpe_ratio"]
    )
    assert metrics["vectorbt_portfolio_sharpe_ratio"] == pytest.approx(
        metrics["account_sharpe_ratio"]
    )
    assert metrics["vectorbt_returns_benchmark_return"] is None
    before = copy.deepcopy(result)
    del result["analysis"]
    with patch.object(adapter, "evaluate", side_effect=AssertionError("No replay")):
        restored = build_analysis(result)
    assert restored["metrics"]["vectorbt_returns_sharpe_ratio"] == pytest.approx(
        metrics["account_sharpe_ratio"]
    )
    assert restored["metrics"]["vectorbt_portfolio_start_value"] is None
    assert restored["metrics"]["vectorbt_trades_total_records"] == 2
    assert result == {key: value for key, value in before.items() if key != "analysis"}
    assert_contract(analysis)


@pytest.mark.parametrize("state", ["no-trade", "open", "all-win"])
@vectorbt
def test_vectorbt_zero_trade_open_and_all_winner_definitions(state):
    from test_vectorbt_portfolio import DAYS, adapter, candle, signal, snapshot, strategy

    prices = snapshot()
    if state == "all-win":
        prices["bars"]["AAA"][DAYS[1]] = candle(100, 111, 99, 108)
    spec = strategy(
        allocation=100,
        signals=[signal(day=3 if state == "no-trade" else 2 if state == "open" else 0)],
        hold_sessions=10 if state == "open" else 1,
        target_pct=10,
        stop_pct=20,
    )
    result = adapter.evaluate([spec], prices, 1000)
    metrics = result["analysis"]["metrics"]
    assert metrics["vectorbt_trades_total_open_trades"] == (1 if state == "open" else 0)
    assert metrics["vectorbt_trades_total_closed_trades"] == (1 if state == "all-win" else 0)
    assert metrics["vectorbt_trades_profit_factor"] is None
    if state != "all-win":
        assert metrics["account_sharpe_ratio"] is None
    assert_contract(result["analysis"])


@vectorbt
def test_vectorbt_constant_daily_return_ratio_is_undefined_not_floating_point_noise():
    report = saved((1100, 1210, 1331))
    report["execution"]["engine"] = "vectorbt"
    result = build_analysis(report)
    assert result["metrics"]["vectorbt_returns_sharpe_ratio"] is None
    assert result["metrics"]["vectorbt_portfolio_sharpe_ratio"] is None
    assert_contract(result)


@nautilus
@pytest.mark.parametrize("minute", [False, True])
def test_nautilus_full_registry_and_actual_marked_eod_returns(minute):
    from test_nautilus_portfolio import DAYS, adapter, candle, snapshot, strategy

    prices = snapshot(minute=minute)
    for key in prices["bars"]["AAA"]:
        if key.startswith(DAYS[1]):
            prices["bars"]["AAA"][key] = candle(100, 111, 99, 108)
    result = adapter.evaluate(
        [strategy("a", target_pct=10, stop_pct=20), strategy("b", target_pct=30, stop_pct=20)],
        prices,
        1000,
    )
    analysis = result["analysis"]
    metrics = analysis["metrics"]
    assert len(metrics) == 88  # 27 common + 61 keys from all 34 native statistic classes.
    assert metrics["nautilus_account_sharperatio"] == pytest.approx(metrics["account_sharpe_ratio"])
    assert metrics["nautilus_account_returnsvolatility"] == pytest.approx(
        metrics["account_annualized_volatility_pct"]
    )
    assert metrics["nautilus_account_valueatrisk"] == pytest.approx(
        metrics["account_value_at_risk_pct"]
    )
    assert metrics["nautilus_account_alpha"] is None
    assert metrics["nautilus_positions_long_ratio"] == 100
    assert any(chart["id"] == "nautilus-account-report" for chart in analysis["charts"])
    assert_contract(analysis)


@nautilus
@pytest.mark.parametrize("state", ["no-trade", "open", "all-win"])
def test_nautilus_zero_trade_open_and_all_winner_definitions(state):
    from test_nautilus_portfolio import DAYS, adapter, candle, signal, snapshot, strategy

    prices = snapshot()
    if state == "all-win":
        prices["bars"]["AAA"][DAYS[1]] = candle(100, 111, 99, 108)
    result = adapter.evaluate(
        [
            strategy(
                allocation=100,
                signals=[signal(day=3 if state == "no-trade" else 2 if state == "open" else 0)],
                hold_sessions=10 if state == "open" else 1,
                target_pct=10,
                stop_pct=20,
            )
        ],
        prices,
        1000,
    )
    metrics = result["analysis"]["metrics"]
    assert metrics["nautilus_positions_profitfactor"] is None
    if state != "all-win":
        assert metrics["account_sharpe_ratio"] is None
    assert_contract(result["analysis"])
