import copy
import csv
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from research.data import cutoff_snapshot, fixture_snapshot, validate_snapshot
from research.evidence_import import parse_archive, public_snapshot, reviewed_sessions

COLUMNS = [
    "TradDt",
    "TckrSymb",
    "SctySrs",
    "ISIN",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "TtlTradgVol",
    "TtlNbOfTxsExctd",
]


def archive(rows):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("daily.csv", text.getvalue())
    return buffer.getvalue()


def row(symbol="AAA", series="EQ", volume=100, trades=10):
    return ["2026-02-01", symbol, series, "INE123A01016", 100, 102, 98, 101, volume, trades]


def signal(symbol="AAA", day="2026-01-05"):
    return {"symbol": symbol, "date": day, "row": 2}


class EvidenceTests(unittest.TestCase):
    def test_real_calendar_special_sessions_and_unsupported_year(self):
        self.assertEqual(
            reviewed_sessions("2026-01-30", "2026-02-02"),
            ["2026-01-30", "2026-02-01", "2026-02-02"],
        )
        self.assertEqual(reviewed_sessions("2026-01-15", "2026-01-15"), [])
        with self.assertRaisesRegex(ValueError, "2025"):
            reviewed_sessions("2027-01-01", "2027-01-02")

    def test_admits_only_active_unambiguous_normal_series(self):
        payload = archive(
            [row(), row("ZERO", volume=0), row("BLOCK", "BL"), row("AMBIG"), row("AMBIG", "BE")]
        )
        admitted, findings = parse_archive(payload, "2026-02-01", ["AAA", "ZERO", "BLOCK", "AMBIG"])
        self.assertEqual(set(admitted), {"AAA"})
        self.assertEqual(admitted["AAA"]["isin"], "INE123A01016")
        self.assertEqual(
            {f["kind"] for f in findings}, {"no_trade", "excluded_series", "quarantined"}
        )

    def test_malformed_ohlc_and_wrong_day(self):
        broken = row()
        broken[5] = 90
        admitted, findings = parse_archive(archive([broken]), "2026-02-01", ["AAA"])
        self.assertFalse(admitted)
        self.assertEqual(findings[0]["kind"], "malformed")
        with self.assertRaisesRegex(ValueError, "date"):
            parse_archive(archive([row()]), "2026-02-02", ["AAA"])

    def test_snapshot_warmup_does_not_demand_symbol_warmup_prices(self):
        signals = [signal(), signal("LATE", "2026-02-02")]
        snapshot = fixture_snapshot(signals)
        self.assertEqual(len([d for d in snapshot["sessions"] if d < "2026-01-05"]), 7)
        self.assertEqual(min(snapshot["bars"]["LATE"]), "2026-02-02")
        self.assertFalse(validate_snapshot(snapshot, signals)["symbols"][1]["missing_sessions"])

    def test_gap_classification_and_first_signal_boundary(self):
        signals = [signal()]
        snapshot = fixture_snapshot(signals)
        own = snapshot["bars"]["AAA"]
        days = sorted(own)
        for index in (0, 5, len(days) - 1):
            del own[days[index]]
        result = validate_snapshot(snapshot, signals)["symbols"][0]
        self.assertEqual(result["head_missing"], [days[0]])
        self.assertEqual(result["internal_missing"], [days[5]])
        self.assertEqual(result["tail_missing"], [days[-1]])

    def test_causal_cutoff_reverses_future_actions_and_preserves_quarantine(self):
        snapshot = fixture_snapshot([signal()])
        snapshot["raw_bars"] = copy.deepcopy(snapshot["bars"])
        for bar in snapshot["bars"]["AAA"].values():
            for key in bar:
                bar[key] *= 0.1
        snapshot["provenance"].update(
            {
                "actions": {"AAA": [{"ex_date": "2026-04-01", "backward_price_factor": 0.1}]},
                "actions_as_of": "2026-05-01",
            }
        )
        quarantined = "2026-01-06"
        del snapshot["bars"]["AAA"][quarantined]
        earlier = cutoff_snapshot(snapshot, "2026-01-30")
        self.assertEqual(
            earlier["bars"]["AAA"]["2026-01-05"], snapshot["raw_bars"]["AAA"]["2026-01-05"]
        )
        self.assertNotIn(quarantined, earlier["bars"]["AAA"])
        mutated = copy.deepcopy(snapshot)
        mutated["provenance"]["actions"]["AAA"][0]["backward_price_factor"] = 0.99
        mutated["bars"]["AAA"]["2026-02-02"]["close"] = 999999
        self.assertEqual(earlier["bars"], cutoff_snapshot(mutated, "2026-01-30")["bars"])
        self.assertTrue(all(day <= "2026-01-30" for day in earlier["sessions"]))

    def test_edited_manifest_cannot_certify_arbitrary_prices(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "nse_daily"
            target.mkdir()
            (target / "manifest.json").write_text(
                json.dumps({"archives": {}, "identity_verified": True})
            )
            with self.assertRaisesRegex(ValueError, "checksum"):
                public_snapshot([signal()], root)

    def test_repeated_archives_release_handles(self):
        import psutil

        process = psutil.Process()
        count = process.num_handles if os.name == "nt" else process.num_fds
        payload = archive([row()])
        before = count()
        for _ in range(150):
            self.assertEqual(len(parse_archive(payload, "2026-02-01", ["AAA"])[0]), 1)
            with self.assertRaises(ValueError):
                parse_archive(payload, "2026-02-02", ["AAA"])
        self.assertLessEqual(count(), before + 3)

    @unittest.skipUnless(
        os.environ.get("RESEARCH_PUBLIC_EVIDENCE_DIR"), "Explicit public seed directory required"
    )
    def test_actual_reviewed_public_archives_and_rights_cutoff(self):
        signals = [signal("RELIANCE"), signal("SWANDEF"), signal("MAHAPEXLTD", "2026-02-02")]
        snapshot = public_snapshot(signals, os.environ["RESEARCH_PUBLIC_EVIDENCE_DIR"])
        self.assertFalse(snapshot["provenance"]["synthetic"])
        self.assertEqual(snapshot["provenance"]["warmup_sessions"], 7)
        self.assertGreater(len(snapshot["bars"]["RELIANCE"]), 150)
        self.assertTrue(
            any(
                f["kind"] == "restricted_no_trade"
                for f in snapshot["provenance"]["quality_findings"]
            )
        )
        self.assertAlmostEqual(snapshot["bars"]["MAHAPEXLTD"]["2026-03-19"]["close"], 68.45)
        self.assertAlmostEqual(
            cutoff_snapshot(snapshot, "2026-03-19")["bars"]["MAHAPEXLTD"]["2026-03-19"]["close"],
            126.90,
        )
        self.assertTrue(snapshot["provenance"]["source_documents"])


if __name__ == "__main__":
    unittest.main()
