import pytest

from research.signals import normalize_csv


def test_clock_precision_timezone_and_duplicate_identity():
    intake = normalize_csv(
        b"Timestamp,Symbol\n2026-01-05T04:00:12Z,AAA\n2026-01-05T09:30:12+05:30,AAA\n2026-01-05T09:31:00,AAA\n"
    )
    assert [s["timestamp"] for s in intake["signals"]] == [
        "2026-01-05T09:30:12+05:30",
        "2026-01-05T09:31:00+05:30",
    ]
    assert intake["receipt"]["duplicates_removed"] == 1


def test_separate_date_time_and_combined_date_preserve_exchange_observation():
    for raw in [
        b"Date,Time,Symbol\n05-01-2026,09:30,AAA\n",
        b"Date,Symbol\n2026-01-05 09:30,AAA\n",
    ]:
        assert normalize_csv(raw)["signals"][0]["timestamp"] == "2026-01-05T09:30:00+05:30"


def test_date_only_intake_identity_is_unchanged():
    assert normalize_csv(b"Date,Symbol\n2026-01-05,AAA\n")["signals"] == [
        {"date": "2026-01-05", "symbol": "AAA", "row": 2, "sector": "", "marketcapname": ""}
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"Timestamp,Symbol\n2026-01-05,AAA\n",
        b"Date,Time,Symbol\n2026-01-05,,AAA\n",
        b"Date,Timestamp,Symbol\n2026-01-05,2026-01-06T09:30,AAA\n",
        b"Date,Timestamp,Symbol\ninvalid,2026-01-06T09:30,AAA\n",
        b"Timestamp,Time,Symbol\n2026-01-05T09:30,09:31,AAA\n",
        b"Date,Time,Symbol\n2026-01-05,24:10,AAA\n",
    ],
)
def test_missing_or_conflicting_times_are_not_silently_dropped(raw):
    with pytest.raises(ValueError):
        normalize_csv(raw)
