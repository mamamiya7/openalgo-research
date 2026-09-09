import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from research.evidence_import import IMPORT_VERSION, MANIFEST_SHA256
from services.research_acquisition import acquire_history, native_historify_ingest, trading_date


def reference():
    bar = {"open": 100, "high": 102, "low": 98, "close": 101}
    return {
        "sessions": ["2026-01-05", "2026-01-06", "2026-01-07"],
        "bars": {
            "AAA": {"2026-01-05": dict(bar), "2026-01-06": dict(bar), "2026-01-07": dict(bar)}
        },
        "raw_bars": {
            "AAA": {"2026-01-05": dict(bar), "2026-01-06": dict(bar), "2026-01-07": dict(bar)}
        },
        "provenance": {
            "provider": "NSE",
            "exchange": "NSE",
            "interval": "D",
            "adjustment_basis": "raw",
            "synthetic": False,
            "identity_verified": True,
            "calendar_verified": True,
            "calendar_basis": "reviewed",
            "import_version": IMPORT_VERSION,
            "manifest_sha256": MANIFEST_SHA256,
            "symbol_identities": {
                "AAA": {
                    day: {"isin": "INE123A01016", "series": "EQ"}
                    for day in ["2026-01-05", "2026-01-06", "2026-01-07"]
                }
            },
        },
    }


def candle(day="2026-01-06", close=101):
    return {
        "timestamp": int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()),
        "open": 100,
        "high": 102,
        "low": 98,
        "close": close,
        "volume": 100,
    }


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.options = {
            "auth_token": "TEST_ONLY_DO_NOT_PERSIST",
            "archive_dir": self.temporary.name,
            "reference_snapshot": reference(),
        }
        self.signals = [{"symbol": "AAA", "date": "2026-01-05", "row": 2}]

    def test_partial_native_results_preserve_exact_receipt_and_missing_coverage(self):
        calls, writes = [], []

        def history(**request):
            calls.append(request)
            return True, {"data": [candle()]}, 200

        snapshot = acquire_history(
            self.signals,
            history=history,
            writer=lambda symbol, rows: writes.append((symbol, rows)),
            **self.options,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["source"], "api")
        self.assertEqual(len(writes), 1)
        self.assertEqual(snapshot["coverage"]["status"], "warning")
        self.assertEqual(
            snapshot["coverage"]["symbols"][0]["missing_sessions"], ["2026-01-05", "2026-01-07"]
        )
        stored = next(Path(self.temporary.name).glob("*.json")).read_text()
        self.assertNotIn(self.options["auth_token"], stored)
        receipt = json.loads(stored)
        self.assertEqual(receipt["admitted_dates"], ["2026-01-06"])
        self.assertEqual(receipt["observations"][0]["close"], 101)

    def test_expired_auth_is_actionable_and_stops_further_requests(self):
        calls = []

        def history(**request):
            calls.append(request)
            return False, {"message": "Access token expired"}, 401

        snapshot = acquire_history(
            self.signals + [{"symbol": "BBB", "date": "2026-01-05", "row": 3}],
            history=history,
            **self.options,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(snapshot["provenance"]["broker_receipts"][0]["outcome"], "auth_expired")
        self.assertEqual(snapshot["coverage"]["status"], "blocked")

    def test_bare_candles_and_mismatched_basis_cannot_be_certified(self):
        self.options["reference_snapshot"] = None
        snapshot = acquire_history(
            self.signals,
            history=lambda **_: (True, {"data": [candle("2026-01-05")]}, 200),
            **self.options,
        )
        self.assertEqual(snapshot["coverage"]["status"], "blocked")
        self.options["reference_snapshot"] = reference()
        snapshot = acquire_history(
            self.signals,
            history=lambda **_: (
                True,
                {"data": [{**candle(), "open": 10, "high": 12, "low": 8, "close": 11}]},
                200,
            ),
            **self.options,
        )
        self.assertEqual(snapshot["coverage"]["status"], "blocked")
        self.assertEqual(
            snapshot["provenance"]["quality_findings"][0]["reason"], "unverified_identity_or_basis"
        )

    def test_rate_limits_and_cancellation_are_bounded(self):
        snapshot = acquire_history(
            self.signals, history=lambda **_: (False, {"message": "limited"}, 429), **self.options
        )
        self.assertEqual(snapshot["provenance"]["broker_receipts"][0]["outcome"], "rate_limited")
        with self.assertRaises(InterruptedError):
            acquire_history(
                self.signals,
                cancelled=lambda: True,
                history=lambda **_: self.fail("unexpected call"),
                **self.options,
            )
        with self.assertRaisesRegex(ValueError, "limit"):
            acquire_history(self.signals, max_requests=0, **self.options)

    def test_duplicate_candles_cannot_choose_arbitrary_first_row(self):
        snapshot = acquire_history(
            self.signals,
            history=lambda **_: (True, {"data": [candle(), candle(close=100)]}, 200),
            **self.options,
        )
        self.assertFalse(snapshot["bars"]["AAA"])
        self.assertEqual(snapshot["coverage"]["status"], "blocked")

    def test_comparison_retains_actual_broker_prices_within_tolerance(self):
        snapshot = acquire_history(
            self.signals,
            history=lambda **_: (True, {"data": [candle(close=101.01)]}, 200),
            **self.options,
        )
        self.assertEqual(snapshot["bars"]["AAA"]["2026-01-06"]["close"], 101.01)

    def test_request_chunk_budget_and_new_window_remain_unverified(self):
        calls = []

        def history(**request):
            calls.append(request)
            return True, {"data": []}, 200

        acquire_history(self.signals, history=history, end_date="2026-12-31", **self.options)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["end_date"], "2026-10-31")
        with self.assertRaisesRegex(ValueError, "request limit"):
            acquire_history(
                self.signals, history=history, end_date="2026-12-31", max_requests=1, **self.options
            )

    def test_timezone_is_explicit_and_host_independent(self):
        self.assertEqual(trading_date("2026-01-04T19:00:00+00:00"), "2026-01-05")
        self.assertEqual(
            trading_date(1767553200),
            datetime.fromtimestamp(1767553200, UTC)
            .astimezone(__import__("services.research_acquisition", fromlist=["IST"]).IST)
            .date()
            .isoformat(),
        )
        with self.assertRaisesRegex(ValueError, "timezone"):
            trading_date("2026-01-05T00:00:00")

    def test_historify_never_falls_back_to_live_archive(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "HISTORIFY_DATABASE_PATH"):
                native_historify_ingest(
                    "AAA", [candle()], Path(self.temporary.name) / "archive.duckdb"
                )
        self.assertFalse((Path(self.temporary.name) / "archive.duckdb").exists())

    def test_actual_isolated_native_historify_ingestion_and_readback(self):
        # Isolate native module initialization and dotenv from the rest of the suite.
        expected = Path(self.temporary.name) / "archive.duckdb"
        environment = dict(
            os.environ,
            HISTORIFY_DATABASE_PATH=str(expected),
            PYTHON_DOTENV_DISABLED="1",
            LOG_DIR=self.temporary.name,
            LOG_FORMAT="%(levelname)s %(message)s",
        )
        script = """
import os, duckdb
from services.research_acquisition import native_historify_ingest
rows = [{'timestamp':1767657600,'open':100.,'high':102.,'low':98.,'close':101.,'volume':100.,'oi':0}]
assert native_historify_ingest('AAA', rows, os.environ['HISTORIFY_DATABASE_PATH']) == 1
with duckdb.connect(os.environ['HISTORIFY_DATABASE_PATH'], read_only=True) as db:
    assert db.execute("SELECT close FROM market_data WHERE symbol='AAA' AND interval='D'").fetchone()[0] == 101.
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(expected.is_file())


if __name__ == "__main__":
    unittest.main()
