import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_evidence_import import archive, row

from research import evidence_import as importer
from research.data import cutoff_snapshot
from research.engine import config_defaults, evaluate


def tiny_bundle(root, days, isins=None):
    folder = root / "nse_daily"
    folder.mkdir(parents=True)
    (root / "exchange_evidence").mkdir()
    entries = {}
    for index, day in enumerate(days):
        record = row()
        record[0] = day
        if isins:
            record[3] = isins[index]
        payload = archive([record])
        name = day.replace("-", "") + ".zip"
        (folder / name).write_bytes(payload)
        entries[name] = {
            "date": day,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "source_url": importer.archive_url(day),
        }
    manifest = {"coverage_start": days[0], "coverage_end": days[-1], "archives": entries}
    content = json.dumps(manifest).encode()
    (folder / "manifest.json").write_bytes(content)
    docs = b'{"documents":{}}'
    (root / "exchange_evidence" / "manifest.json").write_bytes(docs)
    return patch.multiple(
        importer,
        MANIFEST_SHA256=hashlib.sha256(content).hexdigest(),
        DOCUMENTS_SHA256=hashlib.sha256(docs).hexdigest(),
    )


@pytest.mark.parametrize(
    "last_isin,admitted",
    [("INE999A01017", ["2026-01-05"]), ("INE123A01016", ["2026-01-05", "2026-01-07"])],
)
def test_unreviewed_identity_segment_and_return_to_established_security(
    tmp_path, last_isin, admitted
):
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    with tiny_bundle(tmp_path, days, ["INE123A01016", "INE999A01017", last_isin]):
        signals = [
            {"symbol": "AAA", "date": day, "row": index + 2} for index, day in enumerate(days[:2])
        ]
        snapshot = importer.public_snapshot(signals, tmp_path)
        assert list(snapshot["bars"]["AAA"]) == admitted
        assert list(cutoff_snapshot(snapshot, days[-1])["bars"]["AAA"]) == admitted
        if last_isin != "INE123A01016":
            report = evaluate(signals, snapshot, config_defaults())
            assert report["summary"]["accepted_trades"] == 0


def test_unrelated_action_is_not_an_identity_mapping(tmp_path):
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    rule = {
        "symbol": "AAA",
        "sources": [],
        "actions": [{"ex_date": days[1], "backward_price_factor": 1}],
    }
    with (
        tiny_bundle(tmp_path, days, ["INE123A01016", "INE999A01017", "INE999A01017"]),
        patch.object(importer, "REVIEWED_RULES", {"securities": [rule]}),
    ):
        snapshot = importer.public_snapshot(
            [{"symbol": "AAA", "date": days[0], "row": 2}], tmp_path
        )
        assert list(snapshot["bars"]["AAA"]) == [days[0]]


def test_explicit_reviewed_transition_keeps_boundary_gap_for_existing_holdings(tmp_path):
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    with tiny_bundle(tmp_path, days, ["INE123A01016", "INE999A01017", "INE999A01017"]):
        doc = b"reviewed synthetic transition evidence"
        digest = hashlib.sha256(doc).hexdigest()
        (tmp_path / "exchange_evidence" / "transition.pdf").write_bytes(doc)
        documents = json.dumps(
            {
                "documents": {
                    "transition.pdf": {
                        "sha256": digest,
                        "source_url": "https://nsearchives.nseindia.com/content/circulars/transition.pdf",
                    }
                }
            }
        ).encode()
        (tmp_path / "exchange_evidence" / "manifest.json").write_bytes(documents)
        source = {
            "sha256": digest,
            "url": "https://nsearchives.nseindia.com/content/circulars/transition.pdf",
        }
        rule = {
            "symbol": "AAA",
            "sources": [source],
            "actions": [],
            "identity_mappings": [
                {
                    "from_isin": "INE123A01016",
                    "to_isin": "INE999A01017",
                    "effective_date": days[1],
                    "sources": [source],
                }
            ],
        }
        with (
            patch.object(importer, "DOCUMENTS_SHA256", hashlib.sha256(documents).hexdigest()),
            patch.object(importer, "REVIEWED_RULES", {"securities": [rule]}),
        ):
            result = importer.public_snapshot(
                [{"symbol": "AAA", "date": days[0], "row": 2}], tmp_path
            )
            assert list(result["bars"]["AAA"]) == [days[0], days[2]]
            assert (
                result["provenance"]["quality_findings"][0]["reason"]
                == "reviewed_identity_boundary"
            )


def test_official_extension_verifies_overlap_and_preserves_old_snapshot(tmp_path):
    base, extended = tmp_path / "base", tmp_path / "extension"
    days = ["2026-09-03", "2026-09-04"]
    with tiny_bundle(base, days):
        before = importer.public_snapshot([{"symbol": "AAA", "date": days[0], "row": 2}], base)
        unchanged = copy.deepcopy(before)

        def fetch(url):
            day = url.split("_")[-3]
            iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            if iso in days:
                return (base / "nse_daily" / f"{day}.zip").read_bytes()
            record = row()
            record[0] = iso
            return archive([record])

        receipt = importer.extend_official_bundle(base, extended, "2026-09-07", fetch=fetch)
        assert receipt["date_to"] == "2026-09-07"
        updated = importer.public_snapshot(
            [{"symbol": "AAA", "date": "2026-09-07", "row": 2}], base, extension_dir=extended
        )
        assert updated["bars"]["AAA"]["2026-09-07"]["close"] == 101
        assert updated["provenance"]["identity_anchors"]["AAA"]["date"] == "2026-09-04"
        assert before == unchanged
        assert (
            importer.public_snapshot([{"symbol": "AAA", "date": days[0], "row": 2}], base) == before
        )
        original_pointer = (extended / "current.json").read_bytes()
        with pytest.raises(ValueError, match="overlap"):
            importer.extend_official_bundle(
                base, extended, "2026-09-08", fetch=lambda url: fetch(url) + b"conflict"
            )
        assert (extended / "current.json").read_bytes() == original_pointer
        with pytest.raises(ValueError, match="2025"):
            importer.extend_official_bundle(base, extended, "2027-01-04", fetch=fetch)


def test_extension_new_isin_cannot_establish_its_own_overlap_identity(tmp_path):
    base, extended = tmp_path / "base", tmp_path / "extension"
    with tiny_bundle(base, ["2026-09-03", "2026-09-04"]):

        def fetch(url):
            day = url.split("_")[-3]
            if day in ("20260903", "20260904"):
                return (base / "nse_daily" / f"{day}.zip").read_bytes()
            record = row()
            record[0] = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            record[3] = "INE999A01017"
            return archive([record])

        importer.extend_official_bundle(base, extended, "2026-09-08", fetch=fetch)
        snapshot = importer.public_snapshot(
            [{"symbol": "AAA", "date": "2026-09-08", "row": 2}], base, extension_dir=extended
        )
        assert snapshot["coverage"]["status"] == "blocked"
        assert not snapshot["bars"]["AAA"]


def test_extension_cannot_write_into_its_read_only_baseline(tmp_path):
    with tiny_bundle(tmp_path, ["2026-09-03", "2026-09-04"]):
        for destination in (tmp_path, tmp_path / "new"):
            with pytest.raises(ValueError, match="separate"):
                importer.extend_official_bundle(
                    tmp_path,
                    destination,
                    "2026-09-07",
                    fetch=lambda _: pytest.fail("No request permitted"),
                )
        assert not (tmp_path / "current.json").exists()
