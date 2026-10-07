"""Real temporary DuckDB cache first; only missing sessions require a broker."""

import copy
import json

import pytest
from test_acquisition import candle, reference

from services.research_acquisition import acquire_history, native_historify_ingest
from services.research_historify import native_historify_read

SIGNALS = [{"symbol": "AAA", "date": "2026-01-05", "row": 2}]


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from database import historify_db

    path = tmp_path / "historify.duckdb"
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(path))
    return path


def options(archive):
    return {
        "archive_dir": str(archive.parent / "receipts"),
        "reference_snapshot": reference(),
        "broker": None,
        "reader": lambda symbol, first, last: native_historify_read(symbol, first, last, archive),
        "writer": lambda symbol, rows: native_historify_ingest(symbol, rows, archive),
    }


def forbidden(**kwargs):
    raise AssertionError("No broker or credentials needed for complete stored data")


def test_complete_cache_no_broker_credentials_or_download(archive):
    rows = [candle(day) for day in reference()["sessions"]]
    native_historify_ingest("AAA", rows, archive)
    result = acquire_history(SIGNALS, history=forbidden, credentials=forbidden, **options(archive))
    assert result["raw_bars"] == reference()["raw_bars"]
    assert result["provenance"]["acquisition_status"] == "complete"
    assert result["provenance"]["history_source"] == "historify"
    assert result["provenance"]["cache_provider"] == "unknown"
    assert result["provenance"]["download_brokers"] == []
    assert result["acquisition_checkpoint"]["planned_windows"] == []


@pytest.mark.parametrize("broker", ["zerodha", "dhan", "fyers"])
def test_only_hole_downloaded_then_frozen_from_native_database(archive, broker):
    native_historify_ingest("AAA", [candle("2026-01-05"), candle("2026-01-07")], archive)
    calls, logins = [], []

    def credentials():
        logins.append(True)
        return {"broker": broker, "auth_token": "not-for-evidence"}

    def history(**request):
        calls.append(request)
        return True, {"data": [candle("2026-01-06", 101.01)]}, 200

    result = acquire_history(SIGNALS, history=history, credentials=credentials, **options(archive))
    assert len(calls) == len(logins) == 1
    assert calls[0]["start_date"] == calls[0]["end_date"] == "2026-01-06"
    assert calls[0]["broker"] == broker
    stored = native_historify_read("AAA", "2026-01-06", "2026-01-06", archive)
    assert result["raw_bars"]["AAA"]["2026-01-06"]["close"] == stored[0]["close"] == 101.01
    assert result["provenance"]["download_brokers"] == [broker]
    assert "not-for-evidence" not in json.dumps(result)
    assert result["provenance"]["acquisition_status"] == "complete"


def test_unusable_cached_value_is_replaced_without_duplicate_daily_timestamp(archive):
    native_historify_ingest(
        "AAA",
        [candle(day, 100.5 if day == "2026-01-06" else 101) for day in reference()["sessions"]],
        archive,
    )
    result = acquire_history(
        SIGNALS,
        auth_token="test",
        broker="other",
        history=lambda **_: (True, {"data": [candle()]}, 200),
        **{k: v for k, v in options(archive).items() if k != "broker"},
    )
    assert result["raw_bars"]["AAA"]["2026-01-06"]["close"] == 101
    assert len(native_historify_read("AAA", "2026-01-06", "2026-01-06", archive)) == 1


def test_known_reference_quarantine_is_not_downloaded_or_admitted(archive):
    native_historify_ingest("AAA", [candle(day) for day in reference()["sessions"]], archive)
    opts = options(archive)
    opts["reference_snapshot"]["bars"]["AAA"].pop("2026-01-06")
    result = acquire_history(SIGNALS, history=forbidden, credentials=forbidden, **opts)
    assert "2026-01-06" not in result["bars"]["AAA"]
    assert any(item["kind"] == "quarantined" for item in result["provenance"]["quality_findings"])


def test_missing_file_read_does_not_create_database(archive):
    assert native_historify_read("AAA", "2026-01-05", "2026-01-07", archive) == []
    assert not archive.exists()


def test_persistence_mismatch_fails_closed(archive):
    opts = options(archive)
    opts["writer"] = lambda symbol, rows: None
    with pytest.raises(ValueError, match="persistence"):
        acquire_history(
            SIGNALS,
            auth_token="test",
            history=lambda **_: (True, {"data": [candle()]}, 200),
            **opts,
        )


def test_empty_success_remains_missing_and_retry_requests_same_window(archive):
    opts = options(archive)
    first = acquire_history(
        SIGNALS, auth_token="test", history=lambda **_: (True, {"data": []}, 200), **opts
    )
    assert first["provenance"]["acquisition_status"] == "partial"
    calls = []

    def history(**request):
        calls.append(request)
        return True, {"data": [candle(day) for day in reference()["sessions"]]}, 200

    second = acquire_history(
        SIGNALS, auth_token="test", history=history, prior=first["acquisition_checkpoint"], **opts
    )
    assert len(calls) == 1 and second["provenance"]["acquisition_status"] == "complete"


def test_retry_reuses_partial_persisted_prices_and_only_fetches_remaining_hole(archive):
    opts = options(archive)
    first = acquire_history(
        SIGNALS,
        auth_token="test",
        history=lambda **_: (True, {"data": [candle("2026-01-05")]}, 200),
        **opts,
    )
    calls = []

    def history(**request):
        calls.append(request)
        return True, {"data": [candle("2026-01-06"), candle("2026-01-07")]}, 200

    result = acquire_history(
        SIGNALS,
        auth_token="test",
        history=history,
        prior=copy.deepcopy(first["acquisition_checkpoint"]),
        **opts,
    )
    assert calls[0]["start_date"] == "2026-01-06"
    assert result["provenance"]["acquisition_status"] == "complete"


def test_read_failure_closes_native_connection(archive):
    import duckdb

    from database import historify_db

    with historify_db.get_connection() as db:
        db.execute("CREATE TABLE market_data (wrong INTEGER)")
    with pytest.raises(duckdb.BinderException):
        native_historify_read("AAA", "2026-01-05", "2026-01-07", archive)
    # A fresh connection succeeds after the failed query; no held file owner.
    with historify_db.get_connection() as db:
        db.execute("DROP TABLE market_data")


def test_separate_holes_and_changed_broker_keep_download_lineage(archive):
    native_historify_ingest("AAA", [candle("2026-01-06")], archive)
    calls = []

    def first_history(**request):
        calls.append((request["start_date"], request["end_date"]))
        if request["start_date"] == "2026-01-07":
            return False, {"message": "expired"}, 401
        return True, {"data": [candle("2026-01-05")]}, 200

    first = acquire_history(
        SIGNALS,
        credentials=lambda: {"broker": "zerodha", "auth_token": "first-test-token"},
        history=first_history,
        **options(archive),
    )
    assert calls == [("2026-01-05", "2026-01-05"), ("2026-01-07", "2026-01-07")]
    calls.clear()

    def second_history(**request):
        calls.append((request["start_date"], request["end_date"]))
        return True, {"data": [candle("2026-01-07")]}, 200

    second = acquire_history(
        SIGNALS,
        credentials=lambda: {"broker": "dhan", "auth_token": "second-test-token"},
        history=second_history,
        prior=first["acquisition_checkpoint"],
        **options(archive),
    )
    assert calls == [("2026-01-07", "2026-01-07")]
    assert second["provenance"]["download_brokers"] == ["dhan", "zerodha"]
    assert second["provenance"]["acquisition_status"] == "complete"
    assert second["acquisition_checkpoint"]["total_windows"] == 2
    assert not any(
        "authentication expired" in message for message in second["coverage"]["warnings"]
    )


def test_aggregate_cached_rows_are_bounded_across_symbols(archive, monkeypatch):
    from services import research_acquisition

    for symbol in ("AAA", "BBB"):
        native_historify_ingest(symbol, [candle(day) for day in reference()["sessions"]], archive)
    opts = options(archive)
    snapshot = opts["reference_snapshot"]
    for field in ("bars", "raw_bars"):
        snapshot[field]["BBB"] = copy.deepcopy(snapshot[field]["AAA"])
    snapshot["provenance"]["symbol_identities"]["BBB"] = copy.deepcopy(
        snapshot["provenance"]["symbol_identities"]["AAA"]
    )
    monkeypatch.setattr(research_acquisition, "MAX_ARCHIVE_ROWS", 4)
    with pytest.raises(ValueError, match="aggregate row bound"):
        acquire_history(
            SIGNALS + [{"symbol": "BBB", "date": "2026-01-05", "row": 3}],
            credentials=forbidden,
            **opts,
        )


def test_cached_receipt_budget_checked_before_publishing(archive, monkeypatch):
    from services import research_acquisition

    native_historify_ingest("AAA", [candle(day) for day in reference()["sessions"]], archive)
    monkeypatch.setattr(research_acquisition, "MAX_RECEIPT_BYTES", 100)
    with pytest.raises(ValueError, match="aggregate bound"):
        acquire_history(SIGNALS, credentials=forbidden, **options(archive))
    assert not list((archive.parent / "receipts").glob("*.json"))
