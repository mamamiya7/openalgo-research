"""Hand-checkable real VectorBT joint-account and timestamp execution cases."""

import copy
import importlib.util
import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from research.connectors import vectorbt_portfolio as adapter

pytestmark = [
    pytest.mark.timeout(240),
    pytest.mark.skipif(
        not importlib.util.find_spec("vectorbt"), reason="optional VectorBT is not installed"
    ),
]

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


def candle(op=100, high=None, low=None, close=None):
    close = op if close is None else close
    return {
        "open": op,
        "high": max(op, close) if high is None else high,
        "low": min(op, close) if low is None else low,
        "close": close,
    }


def signal(symbol="AAA", day=0, row=2, timestamp=None):
    value = {"symbol": symbol, "date": DAYS[day], "row": row}
    if timestamp:
        value["timestamp"] = f"{DAYS[day]}T{timestamp}:00+05:30"
    return value


def strategy(identity="a", allocation=50, signals=None, **config):
    return {
        "id": identity,
        "name": identity.upper(),
        "allocation_pct": allocation,
        "signals": [signal()] if signals is None else signals,
        "config": {"order_size_pct": 100, "cost_bps": 0, "hold_sessions": 1, **config},
    }


def snapshot(symbols=("AAA", "BBB"), *, minute=False, days=None):
    days = DAYS if days is None else days
    result = {
        "sessions": days,
        "provenance": {
            "provider": "deterministic-test",
            "exchange": "NSE",
            "interval": "1m" if minute else "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "explicit-test-sessions",
            "synthetic": True,
        },
    }
    keys = days
    if minute:
        result["provenance"]["temporal_version"] = "minute-open-v1"
        result["session_hours"] = {day: {"open": "09:15", "close": "09:20"} for day in days}
        keys = []
        for day in days:
            start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
            keys.extend((start + timedelta(minutes=i)).isoformat() for i in range(5))
        result["timeline"] = keys
    result["bars"] = {symbol: {key: candle() for key in keys} for symbol in symbols}
    return result


def assert_reconciles(result):
    summary = result["summary"]
    assert sum(s["net_pnl"] for s in result["per_strategy"]) == pytest.approx(
        summary["final_equity"] - summary["initial_capital"]
    )
    assert sum(s["contribution_pct"] for s in result["per_strategy"]) == pytest.approx(
        summary["net_return_pct"]
    )
    for i, point in enumerate(result["equity_curve"]):
        assert sum(
            s["equity_curve"][i]["net_pnl"] for s in result["per_strategy"]
        ) == pytest.approx(point["equity"] - summary["initial_capital"])
    json.dumps(result, allow_nan=False)


def test_same_symbol_same_source_row_independent_strategy_exits():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 111, 99, 108)
    prices["bars"]["AAA"][DAYS[2]] = candle(108, 109, 104, 105)
    result = adapter.evaluate(
        [
            strategy("a", target_pct=10, stop_pct=20),
            strategy("b", target_pct=30, stop_pct=20),
        ],
        prices,
        1000,
    )
    first, second = result["ledger"]
    assert (first["strategy_id"], second["strategy_id"]) == ("a", "b")
    assert first["source_row"] == second["source_row"] == 2
    assert [row["quantity"] for row in result["ledger"]] == [5, 5]
    assert (first["exit_date"], first["pnl"]) == (DAYS[1], pytest.approx(50))
    assert (second["exit_date"], second["pnl"]) == (DAYS[2], pytest.approx(25))
    assert result["summary"]["final_equity"] == pytest.approx(1075)
    assert result["execution"]["shared_cash"] is True
    assert result["execution"]["flexible_orders"] is True
    assert len(result["engine_records"]["orders"]) == 4
    assert_reconciles(result)


def test_allocations_are_caps_on_one_cash_account_not_independent_backtests():
    prices = snapshot()
    # Each strategy is permitted up to 60%; both cannot deploy it simultaneously.
    result = adapter.evaluate([strategy("a", 60), strategy("b", 60)], prices, 1000)
    assert [row["quantity"] for row in result["ledger"]] == [6, 0]
    assert "Insufficient opening cash" in result["ledger"][1]["reason"]
    assert result["equity_curve"][1]["cash"] == 400
    assert_reconciles(result)


def test_strategy_cap_limits_repeated_signals_even_with_other_strategy_cash_idle():
    result = adapter.evaluate(
        [
            strategy("a", 50, signals=[signal(row=2), signal(row=3)], order_size_pct=60),
            strategy("b", 50, signals=[signal(day=3)]),
        ],
        snapshot(),
        1000,
    )
    assert [row["quantity"] for row in result["ledger"]] == [3, 0, 0]
    assert result["ledger"][1]["reason"] == "Strategy allocation is already in use"
    assert result["equity_curve"][1]["cash"] == 700


def test_fees_and_slippage_reconcile_native_fills_for_each_strategy():
    result = adapter.evaluate(
        [
            strategy("a", cost_bps=100, slippage_bps=100),
            strategy("b", cost_bps=200, slippage_bps=50),
        ],
        snapshot(),
        1000,
    )
    a, b = result["ledger"]
    assert (a["quantity"], a["entry_price"], a["exit_price"], a["fees"]) == (4, 101, 99, 8)
    assert a["pnl"] == -16
    assert b["quantity"] == 4
    assert b["pnl"] == pytest.approx(-20)
    assert result["summary"]["final_equity"] == pytest.approx(964)
    assert_reconciles(result)


@pytest.mark.parametrize("opening, expected_quantity", [(100, 0), (111, 6)])
def test_only_opening_exits_can_fund_other_strategy_opening_entries(opening, expected_quantity):
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(opening, 111, 99, 105)
    result = adapter.evaluate(
        [
            strategy("a", 60, signals=[signal()], hold_sessions=2, target_pct=10),
            strategy("b", 60, signals=[signal("BBB", day=1)]),
        ],
        prices,
        1000,
    )
    assert result["ledger"][1]["quantity"] == expected_quantity
    assert result["ledger"][0]["exit_timing"] == ("open" if opening == 111 else "intraday")
    assert_reconciles(result)


def test_entry_day_exit_cannot_retrofund_a_later_csv_entry():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 115, 99, 110)
    result = adapter.evaluate(
        [
            strategy("a", 60, target_pct=10),
            strategy("b", 60, signals=[signal("BBB")]),
        ],
        prices,
        1000,
    )
    assert [r["quantity"] for r in result["ledger"]] == [6, 0]
    assert result["ledger"][0]["exit_date"] == DAYS[1]


def test_unrealized_marks_increase_opening_budget_without_close_lookahead():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(200, 205, 99, 100)
    result = adapter.evaluate(
        [
            strategy("a", 50, target_pct=500, stop_pct=99, hold_sessions=2),
            strategy("b", 50, signals=[signal("BBB", day=1)], order_size_pct=50),
        ],
        prices,
        1000,
    )
    assert result["ledger"][1]["requested_budget"] == 375
    assert result["ledger"][1]["quantity"] == 3
    assert_reconciles(result)


def test_daily_entry_bar_is_protected_under_new_version_only():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 115, 90, 100)
    result = adapter.evaluate([strategy("a", 100)], prices, 1000)
    row = result["ledger"][0]
    assert (row["entry_date"], row["exit_date"], row["outcome"], row["exit_price"]) == (
        DAYS[1],
        DAYS[1],
        "stop",
        95,
    )
    assert result["policy_version"] != "vectorbt-daily-next-open-v1"


def test_completed_bar_trailing_applies_only_next_bar():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 120, 99, 115)
    prices["bars"]["AAA"][DAYS[2]] = candle(113, 115, 110, 112)
    result = adapter.evaluate(
        [
            strategy("a", 100, target_pct=50, stop_pct=20, trailing_pct=5),
        ],
        prices,
        1000,
    )
    assert result["ledger"][0]["exit_price"] == 113
    assert result["ledger"][0]["outcome"] == "trailing_stop"


def test_intraday_strict_next_minute_entry_and_session_close():
    prices = snapshot(minute=True, days=DAYS[:1])
    result = adapter.evaluate(
        [
            strategy("a", 100, signals=[signal(timestamp="09:15")], trade_horizon="intraday"),
        ],
        prices,
        1000,
    )
    row = result["ledger"][0]
    assert row["entry_timestamp"] == f"{DAYS[0]}T09:16:00+05:30"
    assert row["exit_timestamp"] == f"{DAYS[0]}T09:20:00+05:30"
    assert row["exit_timing"] == "close"
    assert result["metric_basis"] == "minute_marked"
    assert_reconciles(result)


def test_minute_hold_deadline_opens_before_next_strategy_entry():
    prices = snapshot(minute=True, days=DAYS[:1])
    result = adapter.evaluate(
        [
            strategy("a", 100, signals=[signal(timestamp="09:15")], hold_minutes=2),
            strategy(
                "b", 100, signals=[signal("BBB", timestamp="09:17")], trade_horizon="intraday"
            ),
        ],
        prices,
        1000,
    )
    a, b = result["ledger"]
    assert (a["exit_timestamp"], b["entry_timestamp"]) == (
        f"{DAYS[0]}T09:18:00+05:30",
        f"{DAYS[0]}T09:18:00+05:30",
    )
    assert [a["quantity"], b["quantity"]] == [10, 10]
    assert_reconciles(result)


def test_date_only_and_timestamped_signals_share_minute_account():
    prices = snapshot(minute=True, days=DAYS[:2])
    result = adapter.evaluate(
        [
            strategy("a", 50, signals=[signal()], trade_horizon="intraday"),
            strategy("b", 50, signals=[signal(day=1, timestamp="09:15")], trade_horizon="intraday"),
        ],
        prices,
        1000,
    )
    assert [r["entry_timestamp"] for r in result["ledger"]] == [
        f"{DAYS[1]}T09:15:00+05:30",
        f"{DAYS[1]}T09:16:00+05:30",
    ]
    assert_reconciles(result)


def test_configured_entry_and_exit_clocks_and_ineligible_late_signal():
    prices = snapshot(minute=True, days=DAYS[:1])
    result = adapter.evaluate(
        [
            strategy(
                "a", 50, signals=[signal(timestamp="09:15")], entry_time="09:17", exit_time="09:19"
            ),
            strategy("b", 50, signals=[signal(timestamp="09:18")], exit_time="09:19"),
        ],
        prices,
        1000,
    )
    assert result["ledger"][0]["entry_timestamp"] == f"{DAYS[0]}T09:17:00+05:30"
    assert result["ledger"][0]["exit_timestamp"] == f"{DAYS[0]}T09:19:00+05:30"
    assert result["ledger"][1]["status"] == "excluded"


def test_multiday_minute_holding_counts_exchange_sessions():
    result = adapter.evaluate(
        [
            strategy("a", 100, signals=[signal(timestamp="09:15")], hold_sessions=1),
        ],
        snapshot(minute=True, days=DAYS[:2]),
        1000,
    )
    assert result["ledger"][0]["exit_timestamp"] == f"{DAYS[1]}T09:20:00+05:30"


def test_snapshot_tail_is_pending_not_invented_session_close():
    prices = snapshot(minute=True, days=DAYS[:1])
    prices["timeline"] = prices["timeline"][:3]
    for symbol in prices["bars"]:
        prices["bars"][symbol] = {
            key: bar for key, bar in prices["bars"][symbol].items() if key in prices["timeline"]
        }
    result = adapter.evaluate(
        [
            strategy("a", 100, signals=[signal(timestamp="09:15")], trade_horizon="intraday"),
        ],
        prices,
        1000,
    )
    assert result["summary"]["pending_trades"] == 1
    assert result["ledger"][0]["exit_date"] is None
    assert len(result["engine_records"]["orders"]) == 1
    assert_reconciles(result)


def test_daily_future_entry_and_open_position_remain_pending():
    result = adapter.evaluate(
        [
            strategy("a", 50, signals=[signal(day=2)], hold_sessions=5),
            strategy("b", 50, signals=[signal(day=3)]),
        ],
        snapshot(),
        1000,
    )
    assert result["summary"]["pending_trades"] == 1
    assert result["summary"]["unfunded_pending"] == 1
    assert_reconciles(result)


def test_sparse_unrelated_flat_periods_require_no_invented_prices():
    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"] = {
        key: bar for key, bar in prices["bars"]["AAA"].items() if key in DAYS[1:3]
    }
    result = adapter.evaluate([strategy("a", 100)], prices, 1000)
    assert result["summary"]["closed_trades"] == 1
    assert_reconciles(result)


def test_zero_allocation_and_idle_cash_are_accounted_without_nan():
    result = adapter.evaluate([strategy("a", 0), strategy("b", 50)], snapshot(), 1000)
    assert [r["quantity"] for r in result["ledger"]] == [0, 5]
    assert result["per_strategy"][0]["summary"]["net_return_pct"] is None
    assert_reconciles(result)


@pytest.mark.parametrize("minute", [False, True])
def test_missing_potential_holding_bar_rejected_before_engine_import(minute):
    prices = snapshot(minute=minute)
    key = prices["timeline"][7] if minute else DAYS[2]
    del prices["bars"]["AAA"][key]
    with pytest.raises(ValueError, match=re.escape(f"missing AAA at {key}")):
        adapter.validate([strategy("a", 100)], prices, 1000)


def test_minute_requirements_cannot_silently_use_daily_prices():
    with pytest.raises(ValueError, match="one-minute"):
        adapter.validate([strategy(signals=[signal(timestamp="09:15")])], snapshot(), 1000)


def test_invalid_strategy_ids_and_matrix_bound_rejected_before_allocation():
    with pytest.raises(ValueError, match="unique"):
        adapter.validate([strategy(), strategy()], snapshot(), 1000)
    with (
        patch.object(adapter, "MAX_MATRIX_CELLS", 3),
        pytest.raises(ValueError, match="matrix limit"),
    ):
        adapter.validate([strategy()], snapshot(), 1000)


def test_inputs_unchanged_and_exact_strategy_configs_reported():
    strategies, prices = [strategy()], snapshot()
    before = copy.deepcopy((strategies, prices))
    result = adapter.evaluate(strategies, prices, 1000)
    assert (strategies, prices) == before
    assert result["strategies"][0]["config"]["initial_capital"] == 1000
    assert "signals" not in result["strategies"][0]


def test_cancellation_propagates_inside_native_simulation():
    seen = []

    def progress(done, total):
        seen.append(done)
        if done == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.evaluate([strategy()], snapshot(), 1000, progress=progress)
    assert seen[-1] == 2


def test_daily_and_minute_ambiguity_use_their_actual_bar_resolution():
    daily = snapshot(days=DAYS[:2])
    daily["bars"]["AAA"][DAYS[1]] = candle(100, 112, 90, 100)
    minutes = snapshot(minute=True, days=DAYS[:2])
    minutes["bars"]["AAA"][f"{DAYS[1]}T09:15:00+05:30"] = candle(100, 112, 99, 111)
    minutes["bars"]["AAA"][f"{DAYS[1]}T09:16:00+05:30"] = candle(111, 111, 90, 100)
    strategies = [strategy("a", 100)]
    daily_result = adapter.evaluate(strategies, daily, 1000)
    minute_result = adapter.evaluate(strategies, minutes, 1000)
    # Daily OHLC cannot prove whether high or low came first; minute bars can.
    assert daily_result["ledger"][0]["outcome"] == "stop"
    assert minute_result["ledger"][0]["outcome"] == "target"
    assert daily_result["summary"]["final_equity"] == pytest.approx(950)
    assert minute_result["summary"]["final_equity"] == pytest.approx(1100)
    assert daily_result["execution"]["interval"] == "D"
    assert minute_result["execution"]["interval"] == "1m"


def test_opening_target_precedes_later_stop_in_same_bar():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(112, 115, 90, 100)
    result = adapter.evaluate([strategy("a", 100)], prices, 1000)
    row = result["ledger"][0]
    assert (row["outcome"], row["exit_timing"], row["exit_price"]) == ("target", "open", 112)


def test_joint_execution_never_calls_legacy_accounting():
    with (
        patch("research.engine.evaluate", side_effect=AssertionError("legacy accounting")),
        patch("research.engine._evaluate_grid", side_effect=AssertionError("legacy accounting")),
    ):
        result = adapter.evaluate([strategy()], snapshot(), 1000)
    assert result["summary"]["closed_trades"] == 1
