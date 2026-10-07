"""Numerical connector checks against the real optional VectorBT package."""

import copy
import importlib.util
import json
import unittest
from unittest.mock import patch

import pytest

from research.connectors import vectorbt_adapter as adapter

# The first real call compiles upstream Numba kernels; keep this allowance local.
pytestmark = pytest.mark.timeout(240)

SESSIONS = [f"2026-01-{d:02}" for d in (5, 6, 7, 8, 9, 12, 13, 14)]


def candle(op=100, high=101, low=99, close=100):
    return {"open": op, "high": high, "low": low, "close": close}


def snapshot(series=None):
    series = series or {"AAA": {i: candle() for i in range(8)}}
    return {
        "sessions": SESSIONS,
        "bars": {
            symbol: {SESSIONS[i]: bar for i, bar in rows.items()} for symbol, rows in series.items()
        },
        "provenance": {
            "provider": "deterministic-test",
            "exchange": "NSE",
            "interval": "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "explicit-test-sessions",
            "synthetic": True,
        },
    }


def signal(symbol="AAA", i=0, row=2):
    return {"symbol": symbol, "date": SESSIONS[i], "row": row}


def evaluate(prices=None, signals=None, **cfg):
    return adapter.evaluate(
        signals or [signal()],
        prices or snapshot(),
        {"initial_capital": 1000, "order_size_pct": 100, "cost_bps": 0, **cfg},
    )


class VectorBTValidationTest(unittest.TestCase):
    def test_capability_rejections_happen_before_engine_import(self):
        for config in (
            {"modes": ["Uptick"]},
            {"entry_priority": "alphabetical"},
            {"max_exposure_pct": 50},
            {"exposure_fill_mode": "remaining"},
            {"trade_horizon": "intraday"},
            {"priority_seed": 1},
        ):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "VectorBT"):
                adapter.validate_config(config)
        with self.assertRaisesRegex(ValueError, "date-only"):
            adapter.validate_config({}, [{**signal(), "timestamp": "2026-01-05T10:00:00+05:30"}])

    def test_rejects_incomplete_possible_holding_window(self):
        prices = snapshot()
        del prices["bars"]["AAA"][SESSIONS[3]]
        with self.assertRaisesRegex(ValueError, "complete daily prices"):
            adapter.validate([signal()], prices)

    def test_matrix_bound_before_array_allocation(self):
        with patch.object(adapter, "MAX_MATRIX_CELLS", 7):
            with self.assertRaisesRegex(ValueError, "matrix limit"):
                adapter.validate([signal()], snapshot())


@unittest.skipUnless(importlib.util.find_spec("vectorbt"), "optional VectorBT is not installed")
class VectorBTNumericalTest(unittest.TestCase):
    def test_next_open_native_time_exit_and_no_mutation(self):
        prices = snapshot()
        prices["bars"]["AAA"][SESSIONS[0]] = candle(1, 2, 1, 1)
        prices["bars"]["AAA"][SESSIONS[3]] = candle(100, 105, 99, 104)
        before = copy.deepcopy(prices)
        result = evaluate(prices, hold_sessions=2)
        row = result["ledger"][0]
        self.assertEqual(
            (row["entry_date"], row["entry_price"], row["quantity"]), (SESSIONS[1], 100, 10)
        )
        self.assertEqual(
            (row["exit_date"], row["exit_price"], row["outcome"]), (SESSIONS[3], 104, "hold")
        )
        self.assertEqual(result["summary"]["final_equity"], 1040)
        self.assertEqual(result["engine_records"]["trades"][0]["pnl"], row["pnl"])
        self.assertEqual(result["execution"]["simulation_api"], "Portfolio.from_order_func")
        self.assertEqual(prices, before)
        json.dumps(result, allow_nan=False)

    def test_native_entry_day_difference_is_explicit(self):
        prices = snapshot()
        prices["bars"]["AAA"][SESSIONS[1]] = candle(100, 120, 80, 100)
        result = evaluate(prices, hold_sessions=1)
        row = result["ledger"][0]
        self.assertEqual((row["exit_date"], row["outcome"], row["pnl"]), (SESSIONS[2], "hold", 0))
        self.assertTrue(any("entry-day" in item for item in result["limits"]))

    def test_ambiguous_bar_stop_precedes_target(self):
        prices = snapshot()
        prices["bars"]["AAA"][SESSIONS[2]] = candle(100, 120, 80, 100)
        row = evaluate(prices)["ledger"][0]
        self.assertEqual((row["outcome"], row["exit_price"], row["pnl"]), ("stop", 95, -50))

    def test_opening_target_precedes_later_low(self):
        prices = snapshot()
        prices["bars"]["AAA"][SESSIONS[2]] = candle(120, 125, 80, 100)
        row = evaluate(prices)["ledger"][0]
        self.assertEqual(
            (row["outcome"], row["exit_price"], row["exit_timing"]), ("target", 120, "open")
        )

    def test_intraday_exit_cannot_finance_earlier_opening(self):
        for exit_bar, expected in (
            (candle(100, 120, 99, 110), "skipped"),
            (candle(120, 121, 119, 120), "closed"),
        ):
            prices = snapshot(
                {symbol: {i: candle() for i in range(8)} for symbol in ("AAA", "BBB")}
            )
            prices["bars"]["AAA"][SESSIONS[2]] = exit_bar
            result = evaluate(prices, [signal(), signal("BBB", 1, 3)], hold_sessions=2)
            self.assertEqual(result["ledger"][1]["status"], expected)
            self.assertGreaterEqual(min(point["cash"] for point in result["equity_curve"]), 0)

    def test_closing_exit_cannot_finance_earlier_opening(self):
        prices = snapshot({symbol: {i: candle() for i in range(8)} for symbol in ("AAA", "BBB")})
        result = evaluate(prices, [signal(), signal("BBB", 1, 3)], hold_sessions=1)
        self.assertEqual(result["ledger"][0]["exit_timing"], "close")
        self.assertEqual(result["ledger"][1]["status"], "skipped")

    def test_repeated_symbol_is_distinct_funded_lots(self):
        result = evaluate(
            signals=[signal(), signal(i=1, row=3)], order_size_pct=40, hold_sessions=2
        )
        self.assertEqual([r["source_row"] for r in result["ledger"]], [2, 3])
        self.assertEqual([r["quantity"] for r in result["ledger"]], [4, 4])
        self.assertEqual([r["exit_date"] for r in result["ledger"]], [SESSIONS[3], SESSIONS[4]])
        self.assertEqual(result["equity_curve"][2]["open_positions"], 2)

    def test_position_budget_uses_current_opening_equity_without_close_lookahead(self):
        prices = snapshot({symbol: {i: candle() for i in range(8)} for symbol in ("AAA", "BBB")})
        prices["bars"]["AAA"][SESSIONS[2]] = candle(200, 201, 99, 100)
        result = evaluate(
            prices,
            [signal(), signal("BBB", 1, 3)],
            order_size_pct=40,
            target_pct=500,
            stop_pct=99,
            hold_sessions=2,
        )
        self.assertEqual(result["ledger"][1]["requested_budget"], 560)
        self.assertEqual(result["ledger"][1]["quantity"], 5)

    def test_same_opening_budget_and_csv_priority_share_one_cash_account(self):
        prices = snapshot({symbol: {i: candle() for i in range(8)} for symbol in ("AAA", "BBB")})
        result = evaluate(
            prices,
            [signal("BBB"), signal("AAA", row=3)],
            order_size_pct=60,
            hold_sessions=1,
        )
        self.assertEqual([row["quantity"] for row in result["ledger"]], [6, 0])
        self.assertEqual([row["requested_budget"] for row in result["ledger"]], [600, 600])
        self.assertEqual(result["ledger"][1]["status"], "skipped")

    def test_no_next_session_produces_no_orders_or_invented_prices(self):
        result = evaluate(signals=[signal(i=7)])
        self.assertEqual(result["summary"]["unfunded_pending"], 1)
        self.assertEqual(result["engine_records"]["orders"], [])
        self.assertEqual(result["summary"]["final_equity"], 1000)
        json.dumps(result, allow_nan=False)

    def test_trailing_changes_start_next_bar(self):
        prices = snapshot()
        prices["bars"]["AAA"][SESSIONS[1]] = candle(100, 120, 99, 115)
        prices["bars"]["AAA"][SESSIONS[2]] = candle(113, 115, 110, 112)
        row = evaluate(prices, target_pct=50, stop_pct=20, trailing_pct=5)["ledger"][0]
        self.assertEqual((row["outcome"], row["exit_price"]), ("trailing_stop", 113))

    def test_costs_and_slippage_come_from_native_accounting(self):
        row = evaluate(hold_sessions=1, cost_bps=100, slippage_bps=100)["ledger"][0]
        self.assertEqual((row["quantity"], row["entry_price"], row["exit_price"]), (9, 101, 99))
        self.assertAlmostEqual(row["fees"], 18)
        self.assertAlmostEqual(row["pnl"], -36)

    def test_sparse_flat_periods_need_no_fabricated_prices(self):
        prices = snapshot({"AAA": {1: candle(), 2: candle()}})
        result = evaluate(prices, hold_sessions=1)
        self.assertEqual(result["summary"]["final_equity"], 1000)
        self.assertEqual(len(result["engine_records"]["orders"]), 2)

    def test_pending_positions_not_forced_closed(self):
        result = evaluate(signals=[signal(i=6)], hold_sessions=5)
        self.assertEqual(result["summary"]["pending_trades"], 1)
        self.assertEqual(result["summary"]["closed_trades"], 0)
        self.assertIsNone(result["ledger"][0]["pnl"])

    def test_cancellation_inside_simulation(self):
        visited = []

        def progress(done, total):
            visited.append(done)
            if done == 2:
                raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            adapter.evaluate([signal()], snapshot(), {}, progress)
        self.assertEqual(visited[-1], 2)


if __name__ == "__main__":
    unittest.main()
