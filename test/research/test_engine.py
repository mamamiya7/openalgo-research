import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research import evaluate, fixture_snapshot, normalize_csv, validate_config, validate_snapshot
from research.data import checked_historify_snapshot, historify_snapshot


def candle(op=100, high=102, low=98, close=100):
    return {"open": op, "high": high, "low": low, "close": close}


def snapshot(series):
    sessions = [f"2026-01-{d:02}" for d in (5, 6, 7, 8, 9, 12, 13, 14)]
    return {
        "sessions": sessions,
        "bars": {s: {sessions[i]: b for i, b in bars.items()} for s, bars in series.items()},
        "provenance": {
            "provider": "test",
            "exchange": "NSE",
            "interval": "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "explicit-test-sessions",
            "synthetic": True,
        },
    }


def signal(symbol="AAA", day="2026-01-05"):
    return {"symbol": symbol, "date": day, "row": 2}


class EngineTest(unittest.TestCase):
    def run_engine(self, bars, signals=None, **cfg):
        config = {"initial_capital": 1000, "order_size_pct": 100, "cost_bps": 0, **cfg}
        return evaluate(signals or [signal()], snapshot(bars), config)

    def test_csv_dates_alias_bom_dedup_priority(self):
        normalized = normalize_csv(
            b"\xef\xbb\xbfSignal Date,Ticker\n05-01-2026,aaa\n2026-01-05,AAA\n2026-01-06,BBB\n"
        )
        self.assertEqual(normalized["receipt"]["duplicates_removed"], 1)
        self.assertEqual([s["row"] for s in normalized["signals"]], [2, 4])
        self.assertEqual(normalized["signals"][0]["date"], "2026-01-05")

    def test_csv_rejects_bad_rows_ambiguous_dates_and_headers(self):
        for content in (
            b"date,symbol\n01/02/2026,AAA",
            b"date,symbol\n2026-02-30,AAA",
            b"date,symbol,ticker\n2026-01-05,AAA,BBB",
            b"date,symbol\n2026-01-05,AAA,extra",
            b"date,symbol\n2026-01-05,=COMMAND",
            b"date,symbol\n",
        ):
            with self.subTest(content=content), self.assertRaises(ValueError):
                normalize_csv(content)

    def test_next_explicit_session_entry_stop_first(self):
        result = self.run_engine({"AAA": {0: candle(100, 200, 1), 1: candle(100, 120, 90)}})
        row = result["ledger"][0]
        self.assertEqual(row["entry_date"], "2026-01-06")
        self.assertEqual(row["outcome"], "stop")
        self.assertEqual(row["exit_price"], 95)
        self.assertEqual(result["summary"]["final_equity"], 950)
        self.assertEqual(result["summary"]["max_drawdown_pct"], 5)

    def test_opening_gaps_stop_and_target(self):
        for op, expected in ((80, "stop"), (120, "target")):
            result = self.run_engine({"AAA": {1: candle(), 2: candle(op, op + 1, op - 1, op)}})
            row = result["ledger"][0]
            self.assertEqual(
                (row["exit_price"], row["outcome"], row["exit_timing"]), (op, expected, "open")
            )

    def test_hold_elapsed_sessions_after_entry(self):
        result = self.run_engine({"AAA": {i: candle() for i in range(8)}}, hold_sessions=5)
        self.assertEqual(result["ledger"][0]["exit_date"], "2026-01-13")
        self.assertEqual(result["ledger"][0]["outcome"], "hold")

    def test_trailing_uses_completed_bar_only(self):
        result = self.run_engine(
            {"AAA": {1: candle(100, 120, 96, 115), 2: candle(113, 115, 110, 112)}},
            target_pct=50,
            stop_pct=20,
            trailing_pct=5,
        )
        row = result["ledger"][0]
        self.assertEqual(
            (row["exit_date"], row["exit_price"], row["outcome"]),
            ("2026-01-07", 113, "trailing_stop"),
        )

    def test_missing_intervening_bar_never_reconstructs_exit(self):
        result = self.run_engine({"AAA": {1: candle(), 3: candle(150, 155, 145, 150)}})
        row = result["ledger"][0]
        self.assertEqual((row["status"], row["quantity"]), ("pending", 10))
        self.assertIn("Missing intervening", row["reason"])
        self.assertEqual(result["summary"]["final_equity"], 1500)
        self.assertGreater(len(result["missing_marks"]), 0)

    def test_cash_only_opening_exits_fund_entries(self):
        signals = [signal(), signal("BBB", "2026-01-06")]
        for opening, funded in ((True, True), (False, False)):
            exit_bar = candle(120, 125, 115, 120) if opening else candle(100, 120, 98, 115)
            result = self.run_engine(
                {"AAA": {1: candle(), 2: exit_bar}, "BBB": {2: candle()}}, signals
            )
            self.assertEqual(result["ledger"][1]["quantity"] > 0, funded)
            if not funded:
                self.assertEqual(result["ledger"][1]["status"], "skipped")

    def test_costs_slippage_and_daily_marked_equity(self):
        result = self.run_engine(
            {"AAA": {1: candle(), 2: candle()}}, hold_sessions=1, cost_bps=100, slippage_bps=100
        )
        row = result["ledger"][0]
        self.assertEqual(row["quantity"], 9)
        self.assertAlmostEqual(row["fees"], 18)
        self.assertAlmostEqual(row["pnl"], -36)
        self.assertAlmostEqual(result["summary"]["final_equity"], 964)
        self.assertAlmostEqual(result["equity_curve"][1]["equity"], 981.91)

    def test_intentional_legacy_fee_anchor_and_rounding_difference(self):
        # Reference ff86bf1 bundles fees into Buy_Price=33.40, anchors TP=35.10,
        # then rounds net Exit_Price=35.03. This policy records executed prices
        # and fees separately: entry 33.36333, TP 35.06, exit 35.02494.
        result = self.run_engine(
            {"AAA": {1: candle(33.33, 36, 33, 35)}}, cost_bps=10, slippage_bps=10, target_pct=5.1
        )
        row = result["ledger"][0]
        self.assertEqual(row["entry_price"], 33.36333)
        self.assertEqual(row["exit_raw_price"], 35.06)
        self.assertEqual(row["exit_price"], 35.02494)
        expected = 29 * (35.06 * 0.999 * 0.999 - 33.33 * 1.001 * 1.001)
        self.assertAlmostEqual(row["pnl"], expected, places=5)

    def test_pending_marked_loss_counts_from_initial_capital(self):
        result = self.run_engine(
            {"AAA": {1: candle(), 2: candle(100, 102, 80, 80)}},
            stop_pct=50,
            target_pct=50,
            hold_sessions=30,
        )
        summary = result["summary"]
        self.assertEqual(summary["accepted_trades"], 1)
        self.assertEqual(summary["closed_trades"], 0)
        self.assertEqual(summary["pending_trades"], 1)
        self.assertAlmostEqual(summary["net_return_pct"], -20)
        self.assertEqual(summary["max_drawdown_pct"], 20)
        self.assertEqual(summary["realized_equity"], 1000)

    def test_csv_priority_controls_funding_of_simultaneous_candidates(self):
        bars = {"AAA": {1: candle(100, 120, 98, 110)}, "BBB": {1: candle(100, 102, 80, 90)}}
        for symbols, equity in ((("AAA", "BBB"), 1100), (("BBB", "AAA"), 950)):
            result = self.run_engine(bars, [signal(s) for s in symbols])
            self.assertEqual(result["ledger"][1]["status"], "skipped")
            self.assertEqual(result["summary"]["final_equity"], equity)

    def test_missing_entry_unfunded_and_final_signal_pending(self):
        result = self.run_engine({"AAA": {}}, [signal(), signal("AAA", "2026-01-14")])
        self.assertEqual(result["summary"]["unfunded_pending"], 2)
        self.assertEqual(result["summary"]["pending_trades"], 0)
        self.assertEqual(result["summary"]["final_equity"], 1000)

    def test_calendar_session_is_not_inferred_from_available_bar(self):
        snap = snapshot({"AAA": {2: candle()}})
        result = evaluate([signal()], snap)
        self.assertEqual(result["ledger"][0]["quantity"], 0)
        self.assertEqual(result["ledger"][0]["entry_date"], "2026-01-06")

    def test_validation_and_no_mutation(self):
        for cfg in (
            {"hold_sessions": 2.5},
            {"cost_bps": float("nan")},
            {"live": True},
            {"initial_capital": True},
        ):
            with self.assertRaises(ValueError):
                validate_config(cfg)
        snap = snapshot({"AAA": {1: candle()}})
        original = copy.deepcopy(snap)
        evaluate([signal()], snap)
        self.assertEqual(snap, original)
        snap["bars"]["AAA"]["2026-01-06"]["high"] = 90
        with self.assertRaises(ValueError):
            validate_snapshot(snap)

    def test_fixture_deterministic_explicit_and_cancellation_callback(self):
        signals = [signal()]
        snap = fixture_snapshot(signals)
        self.assertEqual(snap, fixture_snapshot(signals))
        self.assertTrue(snap["provenance"]["synthetic"])
        self.assertEqual(snap["coverage"]["status"], "warning")

        def cancelled(done, total):
            if done == 2:
                raise InterruptedError("cancelled")

        with self.assertRaises(InterruptedError):
            evaluate(signals, snap, progress=cancelled)

    def test_historify_blocks_unverified_data_without_reading(self):
        with patch.dict("os.environ", {"HISTORIFY_DATABASE_PATH": ""}):
            snap = historify_snapshot([signal()])
        self.assertEqual(snap["coverage"]["status"], "blocked")
        with self.assertRaises(ValueError):
            validate_snapshot(snap)

        def no_read(*args):
            self.fail("Unverified archive must not be read for evaluation")

        with self.assertRaises(ValueError):
            checked_historify_snapshot(
                [signal()],
                ["2026-01-05"],
                provenance={
                    "provider": "broker",
                    "exchange": "NSE",
                    "interval": "D",
                    "adjustment_basis": "unknown",
                    "calendar_basis": "unknown",
                },
                reader=no_read,
            )

    @unittest.skipUnless(
        importlib.util.find_spec("duckdb"), "DuckDB requires project development environment"
    )
    def test_historify_inspects_isolated_native_schema_read_only_and_closes(self):
        import duckdb

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "historify.duckdb"
            conn = duckdb.connect(str(path))
            try:
                conn.execute(
                    "CREATE TABLE market_data(symbol VARCHAR, exchange VARCHAR, interval VARCHAR, timestamp BIGINT)"
                )
                conn.execute("INSERT INTO market_data VALUES ('AAA', 'NSE', 'D', 1767657600)")
            finally:
                conn.close()
            original = path.read_bytes()
            with patch.dict(os.environ, {"HISTORIFY_DATABASE_PATH": str(path)}):
                for _ in range(100):
                    snap = historify_snapshot([signal()])
                    self.assertEqual(snap["coverage"]["symbols"][0]["available_bars"], 1)
                    self.assertEqual(snap["coverage"]["status"], "blocked")
            self.assertEqual(path.read_bytes(), original)
            moved = path.with_suffix(".moved")
            path.rename(moved)  # Windows also proves no remaining archive handle.
            moved.unlink()


if __name__ == "__main__":
    unittest.main()
