"""Independent causal trigger/exposure/identity regressions for policy v2."""

import copy
import random
import unittest
from datetime import date, timedelta

from research.engine import convert_legacy_config, evaluate, prepare_evaluation, validate_config
from research.signals import normalize_csv


def scenario(counts):
    days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(counts) + 2)]
    signals = [
        {"symbol": f"S{n:02}", "date": days[i], "row": i * 100 + n + 2}
        for i, count in enumerate(counts)
        for n in range(count)
    ]
    bars = {
        s["symbol"]: {d: {"open": 100, "high": 101, "low": 99, "close": 100} for d in days}
        for s in signals
    }
    snap = {
        "sessions": days,
        "bars": bars,
        "provenance": {
            "provider": "independent-fixture",
            "exchange": "NSE",
            "interval": "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "explicit-test-sessions",
            "synthetic": True,
        },
    }
    return signals, snap


class ExecutionOptionsTest(unittest.TestCase):
    def test_causal_trough_uses_seven_prior_counts(self):
        signals, snap = scenario([6, 6, 6, 6, 6, 6, 6, 2, 3])
        result = evaluate(signals, snap, {"modes": ["Bottom Fishing"], "cost_bps": 0})
        qualified = [r for r in result["ledger"] if r.get("trigger_modes")]
        self.assertEqual(len(qualified), 2)
        self.assertTrue(all(r["signal_date"] == snap["sessions"][7] for r in qualified))
        self.assertTrue(all(r["entry_date"] == snap["sessions"][8] for r in qualified))

    def test_trough_strict_half_mean_and_three_thresholds(self):
        for counts in ([4] * 7 + [2], [10] * 7 + [3], [6] * 6 + [1]):
            signals, snap = scenario(counts)
            result = evaluate(signals, snap, {"modes": ["Bottom Fishing"]})
            self.assertFalse(any(r.get("trigger_modes") for r in result["ledger"]))

    def test_zero_and_uptick_current_signal_enters_next_open(self):
        signals, snap = scenario([0, 1, 0, 2])
        for mode in ("Zero Only", "Uptick"):
            result = evaluate(signals, snap, {"modes": [mode]})
            self.assertTrue(all(r.get("trigger_modes") == [mode] for r in result["ledger"]))
            self.assertEqual(result["ledger"][0]["entry_date"], snap["sessions"][2])

    def test_uptick_after_prior_trough_and_future_counts_cannot_change_past(self):
        signals, snap = scenario([6] * 7 + [2, 3, 1])
        baseline = evaluate(signals, snap, {"modes": ["Uptick"]})
        last = snap["sessions"][9]
        changed = signals + [{"symbol": f"F{n}", "date": last, "row": 999 + n} for n in range(100)]
        observed = evaluate(changed, snap, {"modes": ["Uptick"]})
        cutoff = snap["sessions"][8]

        def before(result):
            return [
                (r["symbol"], r["signal_date"], r.get("trigger_modes"))
                for r in result["ledger"]
                if r["signal_date"] <= cutoff
            ]

        self.assertEqual(before(baseline), before(observed))
        self.assertEqual(
            sum(
                r["signal_date"] == cutoff and bool(r.get("trigger_modes"))
                for r in baseline["ledger"]
            ),
            3,
        )

    def test_canonical_modes_bypass_and_explicit_trailing_zero(self):
        self.assertEqual(validate_config({"modes": ["Uptick", "Bypass"]})["modes"], ["Bypass"])
        self.assertEqual(
            validate_config({"modes": ["Uptick", "Zero Only", "Uptick"]})["modes"],
            ["Zero Only", "Uptick"],
        )
        self.assertTrue(validate_config({"trailing_pct": 5})["trailing_enabled"])
        self.assertFalse(
            validate_config({"trailing_pct": 5, "trailing_enabled": False})["trailing_enabled"]
        )
        signals, snap = scenario([1])
        disabled = evaluate(signals, snap, {"trailing_enabled": False, "trailing_pct": 0})
        enabled = evaluate(signals, snap, {"trailing_enabled": True, "trailing_pct": 0})
        self.assertEqual(disabled["ledger"][0]["status"], "pending")
        self.assertEqual(enabled["ledger"][0]["outcome"], "trailing_stop")
        self.assertEqual(enabled["ledger"][0]["exit_timing"], "open")

    def test_exposure_strict_versus_remaining_and_zero(self):
        signals, snap = scenario([2])
        base = {
            "initial_capital": 1000,
            "cost_bps": 0,
            "order_size_pct": 40,
            "max_exposure_pct": 60,
        }
        strict = evaluate(signals, snap, base)
        remaining = evaluate(signals, snap, {**base, "exposure_fill_mode": "remaining"})
        self.assertEqual([r["quantity"] for r in strict["ledger"]], [4, 0])
        self.assertEqual([r["quantity"] for r in remaining["ledger"]], [4, 2])
        self.assertEqual(remaining["equity_curve"][1]["cash"], 400)
        none = evaluate(signals, snap, {**base, "max_exposure_pct": 0})
        self.assertEqual(none["summary"]["accepted_trades"], 0)

    def test_priority_all_four_and_seed_deterministic(self):
        signals, snap = scenario([5])
        signals.reverse()
        config = {"initial_capital": 1000, "cost_bps": 0, "order_size_pct": 100}
        expected = {"csv": "S04", "reversed": "S00", "alphabetical": "S00"}
        shuffled = [s["symbol"] for s in signals]
        random.Random(f"77:{snap['sessions'][1]}").shuffle(shuffled)
        expected["shuffle"] = shuffled[0]
        original = copy.deepcopy(signals)
        for priority, symbol in expected.items():
            result = evaluate(
                signals, snap, {**config, "entry_priority": priority, "priority_seed": 77}
            )
            self.assertEqual([r["symbol"] for r in result["ledger"] if r["quantity"]], [symbol])
        self.assertEqual(signals, original)

    def test_complete_identity_legacy_units_and_validation(self):
        cfg = convert_legacy_config(
            {
                "tp": 0.11,
                "slippage": 0.001,
                "order_size_pct": 0.05,
                "cost_bps": 7,
                "trailing_sl_enabled": True,
                "trailing_sl": 0,
                "entry_priority": "reverse",
                "exposure_fill_mode": "use_remaining",
            },
            source_version="eod_next_open_marked_v2",
        )
        self.assertEqual(
            (cfg["target_pct"], cfg["slippage_bps"], cfg["order_size_pct"], cfg["cost_bps"]),
            (11, 10, 5, 7),
        )
        self.assertTrue(cfg["trailing_enabled"])
        self.assertEqual(cfg["trailing_pct"], 0)
        self.assertEqual(cfg["entry_priority"], "reversed")
        self.assertEqual(cfg["exposure_fill_mode"], "remaining")
        with self.assertRaises(ValueError):
            convert_legacy_config({}, source_version="unknown")
        for invalid in (
            {"modes": []},
            {"modes": "Bypass"},
            {"trailing_enabled": 1},
            {"priority_seed": 1.5},
            {"hold_sessions": 253},
            {"max_exposure_pct": 101},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_config(invalid)
        self.assertEqual(validate_config({"hold_sessions": 252})["hold_sessions"], 252)

    def test_preparation_and_summary_use_identical_evaluator(self):
        signals, snap = scenario([2, 0, 3, 1])
        prepared = prepare_evaluation(signals, snap)
        config = {
            "modes": ["Zero Only"],
            "entry_priority": "reversed",
            "trailing_enabled": True,
            "trailing_pct": 2,
        }
        full = evaluate(signals, snap, config)
        summary = evaluate(signals, snap, config, prepared=prepared, summary_only=True)
        self.assertEqual(full["summary"], summary["summary"])
        self.assertEqual(full["config"], summary["config"])
        self.assertEqual(summary["ledger"], [])
        with self.assertRaises(ValueError):
            evaluate(list(signals), snap, config, prepared=prepared)

    def test_representative_18310_signal_csv_is_supported(self):
        content = "Date,Symbol\n" + "\n".join(f"2026-01-05,S{i}" for i in range(18310))
        normalized = normalize_csv(content.encode())
        self.assertEqual(normalized["receipt"]["signal_count"], 18310)
        self.assertEqual(normalized["receipt"]["duplicates_removed"], 0)

    def test_history_warms_trigger_without_trading_earlier_signals(self):
        history, snap = scenario([6] * 7 + [2, 3])
        signals = [s for s in history if s["date"] == snap["sessions"][7]]
        config = {"modes": ["Bottom Fishing"]}
        without_history = evaluate(signals, snap, config)
        prepared = prepare_evaluation(signals, snap, signal_history=history)
        result = evaluate(signals, snap, config, prepared=prepared)
        self.assertEqual(without_history["summary"]["accepted_trades"], 0)
        self.assertEqual(result["summary"]["accepted_trades"], 2)
        self.assertEqual(len(result["ledger"]), 2)
        self.assertEqual(result["equity_curve"][6]["open_positions"], 0)
        # Arbitrary future scanner rows outside the snapshot cannot alter counts.
        mutated = history + [{"symbol": "FUTURE", "date": "2099-01-01"}]
        repeat = evaluate(signals, snap, config, signal_history=mutated)
        self.assertEqual(result["summary"], repeat["summary"])
        self.assertEqual(result["ledger"], repeat["ledger"])

    def test_remaining_cash_and_exposure_do_not_use_intraday_proceeds(self):
        signals, snap = scenario([1, 1])
        snap["bars"]["S00"][snap["sessions"][2]] = {
            "open": 100,
            "high": 120,
            "low": 99,
            "close": 110,
        }
        result = evaluate(
            signals,
            snap,
            {
                "initial_capital": 1000,
                "cost_bps": 0,
                "order_size_pct": 100,
                "max_exposure_pct": 100,
                "exposure_fill_mode": "remaining",
            },
        )
        self.assertEqual(result["ledger"][0]["outcome"], "target")
        self.assertEqual(result["ledger"][1]["quantity"], 0)
        self.assertEqual(result["ledger"][1]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
