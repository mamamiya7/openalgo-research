"""Bounded local batching keeps exact independently acquired stock evidence."""

import gc
from copy import deepcopy

import pytest
from test_historify_scope import native  # noqa: F401 -- real isolated native fixture
from test_stock_feature_preparation import archive, prepare  # noqa: F401 -- artifact fixture
from test_stock_features import bundle, execution, record

from research.market_series import normalize_descriptor
from research.stock_features import fingerprint
from services import research_benchmarks, research_native_calendar, research_stock_features
from services.research_historify import NativeHistorifyArchive
from services.research_market_series import StockFeaturePreparationScope


@pytest.fixture
def cached(native, archive, monkeypatch, tmp_path):  # noqa: F811
    import pandas as pd

    database, path, counts = native
    stocks = {f"S{i:03}": record(f"S{i:03}") for i in range(17)}
    calendar = deepcopy(stocks["S000"]["calendar"])
    database.init_database()
    frame = pd.DataFrame(
        [
            {**bar, "symbol": symbol, "exchange": "NSE", "interval": "D", "volume": 0, "oi": 0}
            for symbol, stock in stocks.items()
            for bar in stock["evidence"]["bars"].values()
        ]
    )
    with database.get_connection() as connection:
        connection.register("controlled_stock_prices", frame)
        try:
            connection.execute(
                "INSERT INTO market_data(symbol,exchange,interval,timestamp,open,high,low,close,volume,oi) "
                "SELECT symbol,exchange,interval,timestamp,open,high,low,close,volume,oi "
                "FROM controlled_stock_prices"
            )
        finally:
            connection.unregister("controlled_stock_prices")
    calls = []

    def window(first, last, *, warmup_sessions):
        calls.append((first, last, warmup_sessions))
        return deepcopy(calendar)

    monkeypatch.setattr(research_native_calendar, "native_calendar_window", window)
    monkeypatch.setattr(
        "services.research_sources.acquisition_paths", lambda store: (path, tmp_path / "receipts")
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *args: pytest.fail("Complete cached feature prices cannot require a login"),
    )
    monkeypatch.setattr(
        "services.research_native_prices.native_history",
        lambda **kwargs: pytest.fail("Complete cached feature prices cannot request history"),
    )
    return stocks, execution(*stocks.values()), calendar, calls, path, counts


def test_scoped_and_independent_acquisition_keep_exact_leaf_bundle_and_receipt_identities(cached):
    stocks, evidence, _, calendars, _, counts = cached
    sessions = evidence["snapshot"]["sessions"]
    capital = evidence["portfolio"]["capital"]
    bounds = {
        "summary": {"initial_capital": capital},
        "equity_curve": [{"date": day, "equity": capital} for day in sessions],
    }
    before = counts["opened"]
    original_leaves = [
        research_benchmarks.prepare(
            object(), "alice", bounds, stock["evidence"]["descriptor"], warmup_sessions=100
        )
        for stock in stocks.values()
    ]
    assert counts["opened"] - before == 17 and len(calendars) == 17
    expected = bundle(*original_leaves)
    calendars.clear()
    before = counts["opened"]
    result, states = prepare(evidence)
    assert result["automatic_stock_features"] == expected
    assert counts["opened"] - before == 2 and len(calendars) == 1
    assert {
        symbol: fingerprint(leaf)
        for symbol, leaf in result["automatic_stock_features"]["series"].items()
    } == {leaf["evidence"]["descriptor"]["symbol"]: fingerprint(leaf) for leaf in original_leaves}
    before = counts["opened"]
    repeated, _ = prepare(evidence)
    assert repeated == result and counts["opened"] - before == 2
    assert states[-1]["complete"] and counts["active"] == 0


def test_interrupted_batch_discards_borrowed_rows_and_resumes_from_original_calendar(
    cached, monkeypatch
):
    _, evidence, _, calendars, _, counts = cached
    saved, scopes, windows = [], [], []
    original_read = NativeHistorifyArchive.read_many

    class RecordedScope(StockFeaturePreparationScope):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            scopes.append(self)

    def read(self, requests, **kwargs):
        windows.append(deepcopy(requests))
        return original_read(self, requests, **kwargs)

    monkeypatch.setattr(
        "services.research_market_series.StockFeaturePreparationScope", RecordedScope
    )
    monkeypatch.setattr(NativeHistorifyArchive, "read_many", read)

    def stop(state, _):
        saved.append(deepcopy(state))
        if state["completed_symbols"]:
            raise RuntimeError("pause after first committed stock")

    with pytest.raises(RuntimeError, match="committed stock"):
        prepare(evidence, checkpoint=stop)
    assert scopes[-1]._closed and not scopes[-1]._rows and scopes[-1]._calendar is None
    assert len(windows[-1]) == 16 and len(calendars) == 1
    before = counts["opened"]
    windows.clear()
    monkeypatch.setattr(
        research_native_calendar,
        "native_calendar_window",
        lambda *a, **kw: pytest.fail("Resume must use its verified original calendar"),
    )
    result, _ = prepare(evidence, saved=saved[-1])
    assert counts["opened"] - before == 1
    assert len(windows) == 1 and all(row[0] != "S000" for row in windows[0])
    assert scopes[-1]._closed and not scopes[-1]._rows and counts["active"] == 0
    assert len(result["automatic_stock_features"]["series"]) == 17


def test_resume_rejects_genuinely_different_active_calendar_before_new_archive_work(
    cached,
    archive,  # noqa: F811 -- isolated immutable-artifact fixture
    monkeypatch,
):
    _, evidence, calendar, _, _, counts = cached
    saved = []

    def stop(state, _):
        saved.append(deepcopy(state))
        if (
            state["current_symbol"] == "S001"
            and state["market_checkpoint"] is not None
            and state["market_checkpoint"].get("reference_artifact")
        ):
            raise RuntimeError("pause active acquisition")

    # Produce the individual cached checkpoint retained by older installations;
    # the normal grouped reader must still reject its changed saved calendar.
    monkeypatch.setattr(research_stock_features, "_CACHE_CHECKPOINT_BATCH_SIZE", 1)
    with pytest.raises(RuntimeError, match="active acquisition"):
        prepare(evidence, checkpoint=stop)
    monkeypatch.setattr(research_stock_features, "_CACHE_CHECKPOINT_BATCH_SIZE", 16)
    resumed = deepcopy(saved[-1])
    changed = deepcopy(calendar)
    changed["session_hours"][calendar["sessions"][0]]["close"] = "15:00"
    archive[fingerprint(changed)] = changed
    resumed["market_checkpoint"]["reference_artifact"] = fingerprint(changed)
    resumed["state_id"] = fingerprint({k: v for k, v in resumed.items() if k != "state_id"})
    before = counts["opened"]
    with pytest.raises(ValueError, match="calendars or session hours differ") as failure:
        prepare(evidence, saved=resumed)
    assert counts["opened"] == before and counts["active"] == 0
    assert failure.value.receipt["code"] == "stock_feature_calendar_mismatch"
    assert failure.value.receipt["retryable"] is False
    assert "resume" not in failure.value.receipt["actions"]
    assert failure.value.receipt["details"] == {
        "first_affected_session": calendar["sessions"][0],
        "expected_hours": {"open": "09:15", "close": "15:30"},
        "actual_hours": {"open": "09:15", "close": "15:00"},
        "mismatch_count": 1,
    }


def test_active_completed_leaf_recovery_does_not_reread_its_prices(cached, monkeypatch):
    _, evidence, _, _, _, counts = cached
    saved = []

    def stop(state, _):
        saved.append(deepcopy(state))
        if state["market_checkpoint"] and state["market_checkpoint"].get("inputs_artifact"):
            raise RuntimeError("pause after native leaf saved")

    # Existing saved native leaf references remain valid after cache grouping.
    monkeypatch.setattr(research_stock_features, "_CACHE_CHECKPOINT_BATCH_SIZE", 1)
    with pytest.raises(RuntimeError, match="leaf saved"):
        prepare(evidence, checkpoint=stop)
    monkeypatch.setattr(research_stock_features, "_CACHE_CHECKPOINT_BATCH_SIZE", 16)
    assert not saved[-1]["completed_symbols"]
    before = counts["opened"]
    recovered, _ = prepare(evidence, saved=saved[-1])
    assert counts["opened"] - before == 1  # Sixteen remaining stocks fit one new batch.
    assert len(recovered["automatic_stock_features"]["series"]) == 17


@pytest.mark.parametrize("change", ["hours", "warmup", "sessions", "provenance"])
def test_each_saved_calendar_must_keep_exact_hours_warmup_sessions_and_source(change):
    original = record()["calendar"]
    altered = deepcopy(original)
    if change == "hours":
        altered["session_hours"][original["sessions"][0]]["close"] = "15:00"
    elif change == "warmup":
        altered["provenance"]["warmup_sessions"] = 99
    elif change == "sessions":
        altered["sessions"].pop(100)
    else:
        altered["provenance"]["calendar_basis"] = "another-calendar"
    with StockFeaturePreparationScope(["AAA"], original["sessions"][100:]) as scope:
        scope.calendar(original["sessions"][100], original["sessions"][-1], 100, recorded=original)
        with pytest.raises(ValueError, match="changed|differ"):
            scope.calendar(
                original["sessions"][100], original["sessions"][-1], 100, recorded=altered
            )
    assert original == record()["calendar"]


def test_exact_subwindows_are_sliced_without_unrequested_rows_and_window_escape_rejected(cached):
    stocks, evidence, calendar, _, path, counts = cached
    days = calendar["sessions"]
    descriptor = stocks["S000"]["evidence"]["descriptor"]
    before = counts["opened"]
    with StockFeaturePreparationScope(list(stocks), evidence["snapshot"]["sessions"]) as scope:
        scope.calendar(days[100], days[-1], 100, recorded=calendar)
        archive = NativeHistorifyArchive(path, interval="D")
        rows = scope.read_many(archive, descriptor, calendar, [("S000", days[110], days[115])])[0]
        assert len(rows) == 6
        assert counts["opened"] - before == 1 and len(scope._rows) == 16
        second = scope.read_many(archive, descriptor, calendar, [("S000", days[116], days[120])])[0]
        assert len(second) == 5 and counts["opened"] - before == 1
        with pytest.raises(ValueError, match="escaped"):
            scope.read_many(archive, descriptor, calendar, [("S000", "2024-01-01", days[0])])
    assert scope._closed and not scope._rows


@pytest.mark.parametrize("failure", ["cancel", "query"])
def test_cancellation_or_failed_prefetch_releases_native_connection_and_all_rows(
    cached, failure, monkeypatch
):
    from services import research_historify

    stocks, evidence, calendar, _, path, counts = cached
    archive = NativeHistorifyArchive(path, interval="D")  # noqa: F811 -- artifact fixture above
    original_read = research_historify._read_window
    queried = []

    def read(*args):
        queried.append(True)
        if failure == "query" and len(queried) == 2:
            raise RuntimeError("controlled native query failure")
        return original_read(*args)

    def check():
        if failure == "cancel" and queried:
            raise RuntimeError("controlled cancellation")

    monkeypatch.setattr(research_historify, "_read_window", read)
    with pytest.raises(RuntimeError, match="controlled"):
        with StockFeaturePreparationScope(list(stocks), evidence["snapshot"]["sessions"]) as scope:
            scope.read_many(
                archive,
                stocks["S000"]["evidence"]["descriptor"],
                calendar,
                [("S000", calendar["sessions"][0], calendar["sessions"][-1])],
                check=check,
            )
    assert counts["active"] == 0 and counts["opened"] == counts["closed"]
    assert scope._closed and scope._calendar is None and not scope._rows


def test_scope_cannot_mix_archive_instrument_order_or_more_than_sixteen_windows(cached, tmp_path):
    stocks, evidence, calendar, _, path, _ = cached
    days = calendar["sessions"]
    descriptor = stocks["S000"]["evidence"]["descriptor"]
    with StockFeaturePreparationScope(list(stocks), evidence["snapshot"]["sessions"]) as scope:
        archive = NativeHistorifyArchive(path, interval="D")
        windows = [("S000", days[0], days[-1])]
        with pytest.raises(ValueError, match="bound"):
            scope.read_many(archive, descriptor, calendar, windows * 17)
        wrong = normalize_descriptor({**descriptor, "exchange": "NSE_INDEX", "role": "benchmark"})
        with pytest.raises(ValueError, match="instrument"):
            scope.read_many(archive, wrong, calendar, windows)
        scope.read_many(archive, descriptor, calendar, windows)
        with pytest.raises(ValueError, match="native archive"):
            scope.read_many(
                NativeHistorifyArchive(tmp_path / "other.db"), descriptor, calendar, windows
            )
    with pytest.raises(ValueError, match="closed"):
        scope.read_many(archive, descriptor, calendar, windows)


def test_missing_and_rejected_rows_download_only_their_windows_and_verify_durable_archive(
    cached,
    monkeypatch,
):
    from database import historify_db as database

    stocks, evidence, calendar, _, _, counts = cached
    missing, rejected = calendar["sessions"][115:117]
    with database.get_connection() as connection:
        connection.execute(
            "DELETE FROM market_data WHERE symbol=? AND timestamp=?",
            ["S000", stocks["S000"]["evidence"]["bars"][missing]["timestamp"]],
        )
        connection.execute(
            "UPDATE market_data SET low=high+1 WHERE symbol=? AND timestamp=?",
            ["S001", stocks["S001"]["evidence"]["bars"][rejected]["timestamp"]],
        )
    calls, verified = [], []
    original_read = NativeHistorifyArchive.read

    def durable(self, *request):
        verified.append(request)
        return original_read(self, *request)

    def history(**request):
        assert counts["active"] == 0
        calls.append((request["symbol"], request["start_date"], request["end_date"]))
        return (
            True,
            {
                "data": [
                    {**bar, "volume": 0, "oi": 0}
                    for day, bar in stocks[request["symbol"]]["evidence"]["bars"].items()
                    if request["start_date"] <= day <= request["end_date"]
                ]
            },
            200,
        )

    monkeypatch.setattr(NativeHistorifyArchive, "read", durable)
    monkeypatch.setattr("services.research_native_prices.native_history", history)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda owner: {"broker": "controlled", "auth_token": "test-only"},
    )
    result, _ = prepare(evidence)
    assert calls == [("S000", missing, missing), ("S001", rejected, rejected)]
    assert verified == calls  # Post-download verification does not read borrowed stale rows.
    for symbol, day in [("S000", missing), ("S001", rejected)]:
        assert (
            result["automatic_stock_features"]["series"][symbol]["evidence"]["bars"][day]
            == (stocks[symbol]["evidence"]["bars"][day])
        )
    calls.clear()
    before = counts["opened"]
    repeated, _ = prepare(evidence)
    assert not calls and counts["opened"] - before == 2
    assert (
        repeated["automatic_stock_features"]["series"]["S000"]["evidence"]["bars"]
        == (result["automatic_stock_features"]["series"]["S000"]["evidence"]["bars"])
    )
    assert counts["active"] == 0


def test_repeated_success_cancel_and_exception_scopes_keep_handles_and_memory_bounded(cached):
    import tracemalloc

    import psutil

    stocks, evidence, calendar, _, path, counts = cached
    process = psutil.Process()
    gc.collect()
    handles = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    tracemalloc.start()
    initial_memory = tracemalloc.take_snapshot()
    try:
        for i in range(100):
            scope = StockFeaturePreparationScope(list(stocks), evidence["snapshot"]["sessions"])
            try:
                with scope:
                    scope.read_many(
                        NativeHistorifyArchive(path, interval="D"),
                        stocks["S000"]["evidence"]["descriptor"],
                        calendar,
                        [("S000", calendar["sessions"][0], calendar["sessions"][-1])],
                    )
                    assert len(scope._rows) <= 16
                    assert sum(len(rows) for rows in scope._rows.values()) <= 16 * 3000
                    if i % 3:
                        raise RuntimeError("controlled scope exit")
            except RuntimeError:
                pass
            assert scope._closed and not scope._rows and scope._calendar is None
        gc.collect()
        retained_bytes = sum(
            row.size_diff
            for row in tracemalloc.take_snapshot().compare_to(initial_memory, "filename")
            if row.traceback[0].filename.endswith("research_market_series.py")
        )
        assert retained_bytes <= 64 * 1024
    finally:
        tracemalloc.stop()
    final = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    assert final <= handles + 3
    assert counts["active"] == 0 and counts["opened"] == counts["closed"]
