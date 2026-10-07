"""Minute cache acquisition against real temporary native DuckDB storage."""

import copy
from datetime import datetime, timedelta

import pytest
from test_acquisition import reference

from services.research_historify import native_historify_read, native_historify_write
from services.research_intraday import acquire_intraday

SIGNALS = [{"symbol": "AAA", "date": "2026-01-05", "row": 2}]
OPEN = datetime.fromisoformat("2026-01-05T09:15:00+05:30")
STAMPS = [(OPEN + timedelta(minutes=n)).isoformat() for n in range(3)]


def plan():
    return {
        "interval": "1m",
        "timeline": [(OPEN + timedelta(minutes=n)).isoformat() for n in range(375)],
        "required_timestamps": {"AAA": list(STAMPS)},
    }


def row(stamp, close=100.5):
    return {
        "timestamp": int(datetime.fromisoformat(stamp).timestamp()),
        "open": 100.2,
        "high": 101.0,
        "low": 100.0,
        "close": close,
        "volume": 100,
        "oi": 0,
    }


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from database import historify_db

    path = tmp_path / "minutes.duckdb"
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(path))
    return path


def options(path):
    return {
        "archive_dir": str(path.parent / "receipts"),
        "reader": lambda symbol, first, last: native_historify_read(
            symbol, first, last, path, interval="1m"
        ),
        "writer": lambda symbol, rows: native_historify_write(symbol, rows, path, interval="1m"),
    }


def forbidden(**kwargs):
    raise AssertionError("Complete cache must not require a broker")


def test_empty_cache_reads_do_not_rewrite_the_same_recovery_snapshot(tmp_path):
    signals, own_plan, own_reference = batch_fixture(["AAA", "BBB", "CCC"])
    opts, reads, _ = memory_archive(tmp_path)
    saved = []

    def unavailable():
        raise ValueError("Connect a broker")

    with pytest.raises(ValueError, match="Connect a broker"):
        acquire_intraday(
            signals,
            own_plan,
            own_reference,
            credentials=unavailable,
            checkpoint=lambda state: saved.append(copy.deepcopy(state)),
            **opts,
        )
    assert len(reads) == 3
    assert len(saved) == 1
    assert len(saved[0]["planned_windows"]) == 3


def test_complete_minute_cache_needs_no_auth_or_daily_substitution(archive):
    native_historify_write("AAA", [row(stamp) for stamp in STAMPS], archive)
    result = acquire_intraday(
        SIGNALS, plan(), reference(), credentials=forbidden, history=forbidden, **options(archive)
    )
    assert result["provenance"]["acquisition_status"] == "complete"
    assert result["sessions"] == reference()["sessions"]
    assert len(result["timeline"]) == 375
    assert result["raw_bars"]["AAA"][STAMPS[0]]["close"] == 100.5
    assert "not an independently verified" in result["provenance"]["minute_price_verification"]


def test_download_only_missing_minutes_and_reread_exact_persisted_values(archive):
    native_historify_write("AAA", [row(STAMPS[0]), row(STAMPS[2])], archive)
    writes, calls = [], []
    opts = options(archive)
    original = opts["writer"]

    def writer(symbol, rows):
        writes.extend(rows)
        return original(symbol, rows)

    def history(**request):
        calls.append(request)
        return True, {"data": [row(stamp, 100.6) for stamp in STAMPS]}, 200

    opts["writer"] = writer
    result = acquire_intraday(
        SIGNALS,
        plan(),
        reference(),
        credentials=lambda: {"broker": "zerodha", "auth_token": "private-test"},
        history=history,
        **opts,
    )
    assert len(calls) == len(writes) == 1
    assert calls[0]["interval"] == "1m" and calls[0]["broker"] == "zerodha"
    assert result["bars"]["AAA"][STAMPS[0]]["close"] == 100.5
    assert result["bars"]["AAA"][STAMPS[1]]["close"] == 100.6
    assert result["provenance"]["download_brokers"] == ["zerodha"]


@pytest.mark.parametrize(
    "response",
    [
        [row(STAMPS[0]), row(STAMPS[0])],
        [dict(row(STAMPS[0]), high=103)],
        [dict(row(STAMPS[0]), timestamp=int(OPEN.timestamp()) + 10)],
    ],
)
def test_duplicate_outside_daily_range_and_nonminute_rows_remain_partial(archive, response):
    result = acquire_intraday(
        SIGNALS,
        plan(),
        reference(),
        credentials=lambda: {"broker": "test", "auth_token": "private-test"},
        history=lambda **_: (True, {"data": response}, 200),
        **options(archive),
    )
    assert result["provenance"]["acquisition_status"] == "partial"
    assert result["bars"]["AAA"] == {}
    assert result["coverage"]["missing_minutes"]["AAA"] == STAMPS


def test_failed_auth_checkpoint_retries_only_remaining_data(archive):
    native_historify_write("AAA", [row(STAMPS[0]), row(STAMPS[1])], archive)
    first = acquire_intraday(
        SIGNALS,
        plan(),
        reference(),
        credentials=lambda: {"broker": "test", "auth_token": "expired"},
        history=lambda **_: (False, {"message": "expired"}, 401),
        **options(archive),
    )
    assert first["provenance"]["acquisition_status"] == "partial"
    second = acquire_intraday(
        SIGNALS,
        plan(),
        reference(),
        credentials=lambda: {"broker": "other", "auth_token": "new"},
        history=lambda **_: (True, {"data": [row(STAMPS[2])]}, 200),
        prior=first["acquisition_checkpoint"],
        **options(archive),
    )
    assert second["provenance"]["acquisition_status"] == "complete"
    assert second["raw_bars"]["AAA"][STAMPS[0]] == first["raw_bars"]["AAA"][STAMPS[0]]


def test_persistence_failure_cannot_publish_downloaded_candles(archive):
    opts = options(archive)
    opts["writer"] = lambda *_: None
    with pytest.raises(ValueError, match="exactly match"):
        acquire_intraday(
            SIGNALS,
            plan(),
            reference(),
            credentials=lambda: {"broker": "test", "auth_token": "token"},
            history=lambda **_: (True, {"data": [row(STAMPS[0])]}, 200),
            **opts,
        )


def test_plan_cannot_remove_an_expected_grid_slot(archive):
    broken = plan()
    broken["timeline"].pop(50)
    with pytest.raises(ValueError, match="full scheduled grid"):
        acquire_intraday(SIGNALS, broken, reference(), **options(archive))


def test_missing_minute_cache_never_reads_daily_rows(archive):
    native_historify_write("AAA", [row(STAMPS[0])], archive, interval="D")
    assert native_historify_read("AAA", STAMPS[0], STAMPS[-1], archive, interval="1m") == []


def test_checkpoint_rejects_changed_minute_requirements(archive):
    native_historify_write("AAA", [row(stamp) for stamp in STAMPS], archive)
    result = acquire_intraday(SIGNALS, plan(), reference(), **options(archive))
    changed = copy.deepcopy(plan())
    changed["required_timestamps"]["AAA"].pop()
    with pytest.raises(ValueError, match="different immutable"):
        acquire_intraday(
            SIGNALS,
            changed,
            reference(),
            prior=result["acquisition_checkpoint"],
            **options(archive),
        )


def test_cache_receipt_quota_is_checked_before_publication(archive, monkeypatch):
    from services import research_intraday

    native_historify_write("AAA", [row(stamp) for stamp in STAMPS], archive)
    monkeypatch.setattr(research_intraday, "MAX_RECEIPT_BYTES", 100)
    with pytest.raises(ValueError, match="byte bound"):
        acquire_intraday(SIGNALS, plan(), reference(), **options(archive))
    assert not list((archive.parent / "receipts").glob("*.json"))


def test_cancellation_preserves_frozen_cache_checkpoint(archive):
    native_historify_write("AAA", [row(STAMPS[0])], archive)
    saved = []

    def capture(state):
        saved.append(copy.deepcopy(state))

    with pytest.raises(InterruptedError):
        acquire_intraday(
            SIGNALS,
            plan(),
            reference(),
            checkpoint=capture,
            cancelled=lambda: bool(saved),
            **options(archive),
        )
    assert saved[-1]["raw_bars"]["AAA"][STAMPS[0]]["close"] == 100.5
    assert saved[-1]["planned_windows"]


def test_special_session_uses_explicit_scheduled_hours(archive):
    short = plan()
    short["session_hours"] = {"2026-01-05": {"open": "09:15", "close": "10:15"}}
    short["timeline"] = short["timeline"][:60]
    native_historify_write("AAA", [row(stamp) for stamp in STAMPS], archive)
    result = acquire_intraday(SIGNALS, short, reference(), **options(archive))
    assert len(result["timeline"]) == 60
    assert result["provenance"]["acquisition_status"] == "complete"


def batch_fixture(symbols):
    """Small normalized observations, with honest generated daily test reference."""
    own_plan = plan()
    own_plan["required_timestamps"] = {symbol: list(STAMPS) for symbol in symbols}
    own_reference = reference()
    for symbol in symbols:
        for key in ("bars", "raw_bars"):
            own_reference[key][symbol] = copy.deepcopy(own_reference[key]["AAA"])
        own_reference["provenance"]["symbol_identities"][symbol] = copy.deepcopy(
            own_reference["provenance"]["symbol_identities"]["AAA"]
        )
    signals = [
        {"symbol": symbol, "date": "2026-01-05", "row": i + 2} for i, symbol in enumerate(symbols)
    ]
    return signals, own_plan, own_reference


def memory_archive(tmp_path, initial=None):
    stored = copy.deepcopy(initial or {})
    reads, writes = [], []

    def reader(symbol, first, last):
        reads.append((symbol, first, last))
        return [
            copy.deepcopy(value)
            for stamp, value in sorted(stored.get(symbol, {}).items())
            if first <= stamp <= last
        ]

    def writer(symbol, rows):
        writes.append((symbol, copy.deepcopy(rows)))
        series = stored.setdefault(symbol, {})
        for value in rows:
            point = datetime.fromtimestamp(value["timestamp"], OPEN.tzinfo).isoformat()
            series[point] = copy.deepcopy(value)

    return (
        {"reader": reader, "writer": writer, "archive_dir": str(tmp_path / "receipts")},
        reads,
        writes,
    )


@pytest.mark.parametrize("symbol_count,budget", [(2, 1), (501, 500)])
def test_complete_cache_read_count_does_not_consume_broker_budget(tmp_path, symbol_count, budget):
    symbols = [f"S{i:03}" for i in range(symbol_count)]
    signals, own_plan, own_reference = batch_fixture(symbols)
    opts, reads, writes = memory_archive(
        tmp_path, {symbol: {stamp: row(stamp) for stamp in STAMPS} for symbol in symbols}
    )
    result = acquire_intraday(
        signals,
        own_plan,
        own_reference,
        max_requests=budget,
        credentials=forbidden,
        history=forbidden,
        **opts,
    )
    assert result["provenance"]["acquisition_status"] == "complete"
    assert len(reads) == symbol_count and not writes
    assert not result["provenance"].get("batch_pending", False)


def test_sparse_holes_across_dates_share_one_bounded_broker_call(tmp_path):
    own_plan = plan()
    next_open = OPEN + timedelta(days=1)
    next_stamps = [(next_open + timedelta(minutes=n)).isoformat() for n in range(375)]
    own_plan["timeline"] += next_stamps
    needed = [STAMPS[0], STAMPS[1], next_stamps[0], next_stamps[1]]
    own_plan["required_timestamps"] = {"AAA": needed}
    opts, _, writes = memory_archive(
        tmp_path, {"AAA": {STAMPS[0]: row(STAMPS[0]), next_stamps[0]: row(next_stamps[0])}}
    )
    calls = []

    def history(**request):
        calls.append(request)
        return True, {"data": [row(STAMPS[1]), row(next_stamps[1])]}, 200

    result = acquire_intraday(
        SIGNALS,
        own_plan,
        reference(),
        max_requests=1,
        credentials=lambda: {"broker": "zerodha", "auth_token": "test-only"},
        history=history,
        **opts,
    )
    assert result["provenance"]["acquisition_status"] == "complete"
    assert len(calls) == 1
    assert (calls[0]["start_date"], calls[0]["end_date"]) == ("2026-01-05", "2026-01-06")
    assert sum(len(rows) for _, rows in writes) == 2


def test_request_budget_checkpoint_resumes_remaining_symbol_without_redownload(tmp_path):
    signals, own_plan, own_reference = batch_fixture(["AAA", "BBB"])
    opts, reads, writes = memory_archive(tmp_path)
    calls, checkpoints = [], []

    def history(**request):
        calls.append(request["symbol"])
        return True, {"data": [row(stamp) for stamp in STAMPS]}, 200

    common = dict(
        max_requests=1,
        credentials=lambda: {"broker": "dhan", "auth_token": "test-only"},
        history=history,
        checkpoint=lambda state: checkpoints.append(copy.deepcopy(state)),
        **opts,
    )
    first = acquire_intraday(signals, own_plan, own_reference, **common)
    assert calls == ["AAA"]
    assert first["provenance"]["acquisition_status"] == "partial"
    assert first["provenance"]["batch_pending"] is True
    assert first["provenance"]["pending_windows"]
    assert checkpoints[-1] == first["acquisition_checkpoint"]
    preserved = copy.deepcopy(first["raw_bars"]["AAA"])
    reads.clear()
    second = acquire_intraday(
        signals, own_plan, own_reference, prior=first["acquisition_checkpoint"], **common
    )
    assert calls == ["AAA", "BBB"]
    assert all(symbol == "BBB" for symbol, _, _ in reads)
    assert second["provenance"]["acquisition_status"] == "complete"
    assert not second["provenance"].get("batch_pending", False)
    assert second["raw_bars"]["AAA"] == preserved
    assert [symbol for symbol, _ in writes] == ["AAA", "BBB"]


def test_progress_counts_processed_requests_separately_from_missing_prices(tmp_path):
    signals, own_plan, own_reference = batch_fixture(["AAA", "BBB"])
    opts, _, _ = memory_archive(tmp_path)
    updates = []

    def history(**request):
        return (
            True,
            {"data": [row(stamp) for stamp in STAMPS] if request["symbol"] == "AAA" else []},
            200,
        )

    first = acquire_intraday(
        signals,
        own_plan,
        own_reference,
        credentials=lambda: {"broker": "test", "auth_token": "test"},
        history=history,
        progress=lambda done, total: updates.append(done / total),
        **opts,
    )
    assert updates == sorted(updates)
    assert updates[-1] > updates[-2] and updates[-1] < 1
    assert first["provenance"]["acquisition_status"] == "partial"
    progress = first["acquisition_checkpoint"]["progress"]
    assert progress["covered_minutes"] == 3 and progress["required_minutes"] == 6
    assert len(first["acquisition_checkpoint"]["completed_windows"]) == 1
    resumed = []
    second = acquire_intraday(
        signals,
        own_plan,
        own_reference,
        credentials=lambda: {"broker": "test", "auth_token": "test"},
        history=lambda **_: (True, {"data": [row(stamp) for stamp in STAMPS]}, 200),
        prior=first["acquisition_checkpoint"],
        progress=lambda done, total: resumed.append(done / total),
        **opts,
    )
    assert resumed[0] >= updates[-1] and resumed == sorted(resumed)
    assert second["provenance"]["acquisition_status"] == "complete"


@pytest.mark.parametrize(
    "success,returned,status",
    [
        (False, [], 503),
        (True, [row(STAMPS[0])], 200),
        (False, [row(stamp) for stamp in STAMPS], 503),
        (True, [], 200),
    ],
)
def test_failed_or_incomplete_executed_window_never_auto_continues(
    tmp_path, success, returned, status
):
    signals, own_plan, own_reference = batch_fixture(["AAA", "BBB"])
    opts, _, _ = memory_archive(tmp_path)
    calls = []

    def history(**request):
        calls.append(request["symbol"])
        return (
            success,
            {"data": returned, "history_evidence": {"evidence_level": "native_adapter"}},
            status,
        )

    result = acquire_intraday(
        signals,
        own_plan,
        own_reference,
        max_requests=1,
        credentials=lambda: {"broker": "zerodha", "auth_token": "test-only"},
        history=history,
        **opts,
    )
    assert calls == ["AAA"]
    assert result["provenance"]["acquisition_status"] == "partial"
    assert result["provenance"].get("batch_pending", False) is False
