"""Cache batching preserves immutable evidence and cooperative recovery."""

import copy
from datetime import datetime

import pytest

from services.research_native_prices import acquire_native_prices

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07"]


def fixture(count=33):
    symbols = [f"TEST{index:03}" for index in range(count)]
    signals = [
        {"symbol": symbol, "date": DAYS[0], "row": index + 2}
        for index, symbol in enumerate(symbols)
    ]
    plan = {"interval": "D", "required_dates": dict.fromkeys(symbols, DAYS[1:])}
    calendar = {"sessions": DAYS, "provenance": {"calendar_basis": "openalgo-market-calendar-v1"}}
    rows = [
        {
            "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 100.0,
            "oi": 0.0,
        }
        for day in DAYS[1:]
    ]
    return signals, plan, calendar, rows


def forbidden(*args, **kwargs):
    raise AssertionError("Complete native cache must not contact broker")


def options(root, rows):
    return {
        "reader": lambda *args: copy.deepcopy(rows),
        "writer": forbidden,
        "credentials": forbidden,
        "history": forbidden,
        "archive_dir": root,
    }


def test_batch_and_sequential_reads_freeze_identical_evidence_and_count_once(tmp_path):
    signals, plan, calendar, rows = fixture()
    batches, saved = [], []

    def many(requests, *, check):
        batches.append(list(requests))
        check()
        return [copy.deepcopy(rows) for _ in requests]

    sequential = acquire_native_prices(
        signals, plan, calendar, **options(tmp_path / "single", rows)
    )
    batched = acquire_native_prices(
        signals,
        plan,
        calendar,
        **options(tmp_path / "batch", rows),
        reader_many=many,
        checkpoint=lambda state: saved.append(copy.deepcopy(state)),
    )
    assert sequential == batched
    assert list(map(len, batches)) == [16, 16, 1]
    assert len(saved) == 5  # three cache groups, before downloads, final verification
    assert batched["acquisition_checkpoint"]["activity"]["prices"]["cached_candles"] == 66


def test_cancellation_mid_admission_saves_completed_cache_units_and_resume_does_not_download(
    tmp_path,
):
    signals, plan, calendar, rows = fixture(20)
    saved, events, batches = [], [], []

    def many(requests, *, check):
        batches.append(list(requests))
        check()
        return [copy.deepcopy(rows) for _ in requests]

    def cancelled():
        return bool(events and events[-1].get("prices", {}).get("available_candles", 0) >= 8)

    with pytest.raises(InterruptedError):
        acquire_native_prices(
            signals,
            plan,
            calendar,
            **options(tmp_path / "receipts", rows),
            reader_many=many,
            cancelled=cancelled,
            activity=events.append,
            checkpoint=lambda state: saved.append(copy.deepcopy(state)),
        )
    assert sum(map(len, saved[-1]["bars"].values())) == 8
    assert len(saved[-1]["cache_checked_windows"]) == 4
    result = acquire_native_prices(
        signals,
        plan,
        calendar,
        **options(tmp_path / "receipts", rows),
        reader_many=many,
        prior=saved[-1],
    )
    assert len(batches[-1]) == 16
    assert sum(map(len, result["bars"].values())) == 40
    assert result["provenance"]["download_brokers"] == []


def test_slow_cache_batch_yields_after_admission_and_resumes_without_rechecking(tmp_path):
    signals, plan, calendar, rows = fixture(20)
    now, saved, requests_seen = [0.0], [], []

    def many(requests, *, check):
        requests_seen.extend(requests)
        check()
        now[0] += 2
        check()  # cancellation check; timing yields after an admitted window
        return [copy.deepcopy(rows) for _ in requests]

    first = acquire_native_prices(
        signals,
        plan,
        calendar,
        **options(tmp_path / "receipts", rows),
        reader_many=many,
        clock=lambda: now[0],
        max_seconds=1,
        checkpoint=lambda state: saved.append(copy.deepcopy(state)),
    )
    assert first["provenance"]["batch_pending"] is True
    assert len(saved[-1]["cache_checked_windows"]) == 1
    requests_seen.clear()
    result = acquire_native_prices(
        signals,
        plan,
        calendar,
        **options(tmp_path / "receipts", rows),
        reader_many=many,
        prior=saved[-1],
        clock=lambda: now[0],
    )
    assert all(request[0] != "TEST000" for request in requests_seen)
    assert sum(map(len, result["bars"].values())) == 40


@pytest.mark.parametrize("returned", [[], None, [[]] * 17])
def test_bad_batch_shape_cannot_mark_windows_checked(tmp_path, returned):
    signals, plan, calendar, rows = fixture(16)
    with pytest.raises(ValueError, match="batch does not match"):
        acquire_native_prices(
            signals,
            plan,
            calendar,
            **options(tmp_path / "receipts", rows),
            reader_many=lambda *args, **kwargs: returned,
        )
