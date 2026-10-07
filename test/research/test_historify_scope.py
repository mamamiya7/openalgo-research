"""Job-scoped initialization with real isolated native persistence and cleanup."""

import gc
import weakref
from contextlib import contextmanager
from datetime import datetime

import pytest

from services.research_historify import (
    NativeHistorifyArchive,
    native_historify_read,
    native_historify_write,
)


def candle(day="2026-01-06", close=101.5):
    return {
        "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
        "open": 100.0,
        "high": 103.0,
        "low": 98.0,
        "close": close,
        "volume": 123,
        "oi": 17,
    }


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from database import historify_db

    path = tmp_path / "native.duckdb"
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(path))
    original_connection = historify_db.get_connection
    original_init = historify_db.init_database
    stats = {"opened": 0, "closed": 0, "active": 0, "initializations": 0}

    @contextmanager
    def connection(*args, **kwargs):
        entered = False
        try:
            with original_connection(*args, **kwargs) as database:
                entered = True
                stats["opened"] += 1
                stats["active"] += 1
                yield database
        finally:
            if entered:
                stats["active"] -= 1
                stats["closed"] += 1

    def initialize():
        stats["initializations"] += 1
        original_init()

    monkeypatch.setattr(historify_db, "get_connection", connection)
    monkeypatch.setattr(historify_db, "init_database", initialize)
    yield historify_db, path, stats
    assert stats["active"] == 0
    assert stats["opened"] == stats["closed"]


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_each_write_persists_exact_rows_and_catalog_but_initializes_only_once(native, interval):
    database, path, stats = native
    archive = NativeHistorifyArchive(path, interval=interval)
    first, second = candle(), candle("2026-01-07", 102.25)
    assert archive.write("aaa", [first]) == 1
    assert archive.write("AAA", [second]) == 1
    assert archive.write("BBB", [first]) == 1
    assert stats["initializations"] == 1
    assert stats["active"] == 0
    bounds = (
        ("2026-01-06", "2026-01-07")
        if interval == "D"
        else ("2026-01-06T09:15:00+05:30", "2026-01-07T09:15:00+05:30")
    )
    assert archive.read("aaa", *bounds) == [first, second]
    assert native_historify_read("AAA", *bounds, path, interval=interval) == [first, second]
    with database.get_connection() as connection:
        assert connection.execute(
            "SELECT symbol, exchange, interval, record_count FROM data_catalog ORDER BY symbol"
        ).fetchall() == [("AAA", "NSE", interval, 2), ("BBB", "NSE", interval, 1)]


def test_empty_writes_and_cache_reads_do_not_initialize_or_create_an_archive(native):
    _, path, stats = native
    archive = NativeHistorifyArchive(path)
    assert archive.read("AAA", "2026-01-06", "2026-01-07") == []
    assert archive.write("AAA", []) == 0
    assert stats == {"opened": 0, "closed": 0, "active": 0, "initializations": 0}
    assert not path.exists()


def test_initialization_is_local_to_each_scope_and_legacy_calls_still_initialize(native):
    _, path, stats = native
    first = NativeHistorifyArchive(path)
    first.write("AAA", [candle()])
    first.write("AAA", [candle(close=102)])
    second = NativeHistorifyArchive(path)
    assert second.read("AAA", "2026-01-06", "2026-01-06")[0]["close"] == 102
    assert stats["initializations"] == 1
    second.write("AAA", [candle()])
    assert stats["initializations"] == 2
    native_historify_write("AAA", [candle()], path)
    native_historify_write("AAA", [candle()], path)
    assert stats["initializations"] == 4
    assert native_historify_read(
        "AAA", "2026-01-06T09:15:00+05:30", "2026-01-06T09:15:00+05:30", path, interval="1m"
    ) == [candle()]


def test_read_and_write_exceptions_release_native_connections_and_allow_new_scope(native):
    import duckdb

    database, path, stats = native
    with database.get_connection() as connection:
        connection.execute("CREATE TABLE market_data (wrong INTEGER)")
    archive = NativeHistorifyArchive(path)
    with pytest.raises(duckdb.BinderException):
        archive.read("AAA", "2026-01-06", "2026-01-06")
    assert stats["active"] == 0
    with pytest.raises(duckdb.BinderException):
        archive.write("AAA", [candle()])
    assert stats["active"] == 0
    with database.get_connection() as connection:
        connection.execute("DROP TABLE market_data")
    recovered = NativeHistorifyArchive(path)
    recovered.write("AAA", [candle()])
    assert recovered.read("AAA", "2026-01-06", "2026-01-06") == [candle()]


def test_failed_initialization_is_not_repeated_within_scope_and_releases_connection(
    native, monkeypatch
):
    database, path, stats = native
    original_init = database.init_database
    attempts = []

    def failing_init():
        attempts.append(True)
        with database.get_connection() as connection:
            connection.execute("CREATE TABLE initialization_marker (value INTEGER)")
            raise RuntimeError("Initialization interrupted")

    monkeypatch.setattr(database, "init_database", failing_init)
    archive = NativeHistorifyArchive(path)
    with pytest.raises(RuntimeError, match="Initialization interrupted"):
        archive.write("AAA", [candle()])
    assert stats["active"] == 0
    with pytest.raises(RuntimeError, match="fresh acquisition"):
        archive.write("AAA", [candle()])
    assert len(attempts) == 1
    monkeypatch.setattr(database, "init_database", original_init)
    recovered = NativeHistorifyArchive(path)
    assert recovered.write("AAA", [candle()]) == 1
    assert recovered.read("AAA", "2026-01-06", "2026-01-06") == [candle()]


def test_write_requires_native_acknowledgement(native, monkeypatch):
    database, path, stats = native
    monkeypatch.setattr(database, "upsert_market_data", lambda *_: 0)
    archive = NativeHistorifyArchive(path)
    with pytest.raises(ValueError, match="acknowledge all"):
        archive.write("AAA", [candle()])
    assert archive.read("AAA", "2026-01-06", "2026-01-06") == []
    assert stats["initializations"] == 1


def test_every_operation_rechecks_native_path_after_successful_initialization(native, monkeypatch):
    database, path, stats = native
    archive = NativeHistorifyArchive(path.parent / "alias" / ".." / path.name)
    archive.write("AAA", [candle()])
    other = path.with_name("other.duckdb")
    monkeypatch.setattr(database, "HISTORIFY_DB_PATH", str(other))
    before = stats["opened"]
    with pytest.raises(ValueError, match="different archive"):
        archive.read("AAA", "2026-01-06", "2026-01-06")
    with pytest.raises(ValueError, match="different archive"):
        archive.write("AAA", [candle()])
    with pytest.raises(ValueError, match="different archive"):
        archive.write("AAA", [])
    assert stats["opened"] == before
    assert not other.exists()
    monkeypatch.setattr(database, "HISTORIFY_DB_PATH", str(path))
    assert archive.read("AAA", "2026-01-06", "2026-01-06") == [candle()]


def test_configuration_change_during_initialization_cannot_redirect_write(native, monkeypatch):
    database, path, stats = native
    original_init = database.init_database
    other = path.with_name("other.duckdb")

    def changed_init():
        original_init()
        monkeypatch.setattr(database, "HISTORIFY_DB_PATH", str(other))

    monkeypatch.setattr(database, "init_database", changed_init)
    archive = NativeHistorifyArchive(path)
    with pytest.raises(ValueError, match="different archive"):
        archive.write("AAA", [candle()])
    assert not other.exists()
    assert stats["initializations"] == 1
    monkeypatch.setattr(database, "HISTORIFY_DB_PATH", str(path))
    assert archive.read("AAA", "2026-01-06", "2026-01-06") == []


def test_invalid_or_oversized_work_is_rejected_before_io(native):
    _, path, stats = native
    with pytest.raises(ValueError, match="Unsupported"):
        NativeHistorifyArchive(path, interval="5m")
    archive = NativeHistorifyArchive(path)
    with pytest.raises(ValueError, match="oversized"):
        archive.write("AAA", [candle()] * 10001)
    with pytest.raises(ValueError, match="bounded daily"):
        archive.read("AAA", "2026-01-06", "2026-01-05")
    minute = NativeHistorifyArchive(path, interval="1m")
    with pytest.raises(ValueError, match="aware timestamps"):
        minute.read("AAA", "2026-01-06T09:15:00", "2026-01-06T09:16:00")
    assert stats["opened"] == 0
    assert stats["initializations"] == 0
    assert not path.exists()


def test_repeated_native_read_write_cycles_do_not_retain_frames_or_handles(native, monkeypatch):
    psutil = pytest.importorskip("psutil")
    database, path, stats = native
    process = psutil.Process()
    descriptor_count = getattr(process, "num_handles", None) or process.num_fds
    original_upsert = database.upsert_market_data
    frames = []

    def upsert(frame, *args):
        frames.append(weakref.ref(frame))
        return original_upsert(frame, *args)

    monkeypatch.setattr(database, "upsert_market_data", upsert)
    archive = NativeHistorifyArchive(path)
    archive.write("AAA", [candle()])
    archive.read("AAA", "2026-01-06", "2026-01-06")
    gc.collect()
    baseline = descriptor_count()
    for _ in range(100):
        assert archive.write("AAA", [candle()]) == 1
        assert archive.read("AAA", "2026-01-06", "2026-01-06") == [candle()]
        assert stats["active"] == 0
    gc.collect()
    after = descriptor_count()
    assert after <= baseline + 4, (baseline, after)
    assert all(frame() is None for frame in frames)
    assert stats["initializations"] == 1
    assert stats["opened"] == stats["closed"]
