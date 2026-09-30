"""Bounded cache reads preserve exact OHLC windows without holding the archive."""

from contextlib import contextmanager
from datetime import datetime, time, timedelta

import duckdb
import pytest

from services.research_historify import IST, NativeHistorifyArchive, native_historify_read


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from database import historify_db

    path = tmp_path / "historify.duckdb"
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(path))
    with historify_db.get_connection() as connection:
        connection.execute(
            "CREATE TABLE market_data(symbol VARCHAR, exchange VARCHAR, interval VARCHAR, "
            "timestamp BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, "
            "volume BIGINT, oi BIGINT)"
        )
        for symbol, exchange, interval in (
            ("AAA", "NSE", "D"),
            ("BBB", "NSE", "D"),
            ("AAA", "NSE_INDEX", "D"),
            ("AAA", "NSE", "1m"),
        ):
            for day in ("2026-01-05", "2026-01-06", "2026-01-07"):
                stamp = int(
                    datetime.combine(datetime.fromisoformat(day), time.min, IST).timestamp()
                )
                connection.execute(
                    "INSERT INTO market_data VALUES (?,?,?,?,100,102,99,101,1000,0)",
                    [symbol, exchange, interval, stamp],
                )
    return NativeHistorifyArchive(path), path


def test_many_exact_windows_match_individual_reads_and_use_one_connection(archive, monkeypatch):
    from database import historify_db

    scope, path = archive
    requests = [
        ("aaa", "2026-01-05", "2026-01-06"),
        ("BBB", "2026-01-06", "2026-01-07"),
        ("AAA", "2026-01-07", "2026-01-07"),
        ("UNKNOWN", "2026-01-05", "2026-01-07"),
    ]
    expected = [native_historify_read(*request, path) for request in requests]
    original = historify_db.get_connection
    calls, active = [], []

    @contextmanager
    def counted():
        calls.append(True)
        with original() as connection:
            active.append(True)
            try:
                yield connection
            finally:
                active.pop()

    monkeypatch.setattr(historify_db, "get_connection", counted)
    assert scope.read_many(requests) == expected
    assert len(calls) == 1 and not active
    assert [len(rows) for rows in expected] == [2, 2, 1, 0]


def test_sparse_or_malformed_candles_are_not_assumed_covered(archive):
    from database import historify_db

    scope, path = archive
    with historify_db.get_connection() as connection:
        connection.execute("DELETE FROM market_data WHERE symbol='BBB'")
        connection.execute("UPDATE market_data SET open=-1 WHERE symbol='AAA'")
    rows = scope.read_many(
        [("AAA", "2026-01-05", "2026-01-07"), ("BBB", "2026-01-05", "2026-01-07")]
    )
    assert rows[0] == native_historify_read("AAA", "2026-01-05", "2026-01-07", path)
    assert rows[0][0]["open"] == -1  # Existing acquisition qualification rejects it.
    assert rows[1] == []


def test_missing_database_is_not_created(archive):
    scope, path = archive
    path.unlink()
    assert scope.read_many([("AAA", "2026-01-05", "2026-01-07")]) == [[]]
    assert not path.exists()


def test_empty_batch_does_not_open_database(archive, monkeypatch):
    from database import historify_db

    scope, _ = archive
    monkeypatch.setattr(historify_db, "get_connection", lambda: pytest.fail("Unneeded connection"))
    assert scope.read_many([]) == []


@pytest.mark.parametrize(
    "requests",
    [
        [("AAA", "2026-01-05", "2026-01-07")] * 17,
        [("AAA", "2026-01-07", "2026-01-05")],
        [("AAA", "2000-01-01", "2026-01-05")],
        [("AAA", "2026-01-05")],
        [("", "2026-01-05", "2026-01-07")],
    ],
)
def test_invalid_batch_fails_before_connection(archive, monkeypatch, requests):
    from database import historify_db

    scope, _ = archive
    monkeypatch.setattr(
        historify_db, "get_connection", lambda: pytest.fail("Invalid read opened DB")
    )
    with pytest.raises(ValueError):
        scope.read_many(requests)


def test_cancellation_closes_connection_before_error_escapes(archive, monkeypatch):
    from database import historify_db

    scope, _ = archive
    original = historify_db.get_connection
    retained, checks = [], []

    @contextmanager
    def counted():
        with original() as connection:
            retained.append(connection)
            yield connection

    def cancelled():
        checks.append(True)
        if len(checks) == 3:
            raise InterruptedError("Cancelled between archive queries")

    monkeypatch.setattr(historify_db, "get_connection", counted)
    with pytest.raises(InterruptedError):
        scope.read_many([("AAA", "2026-01-05", "2026-01-07")] * 2, check=cancelled)
    with pytest.raises(duckdb.ConnectionException):
        retained[0].execute("SELECT 1")
    with original() as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_data").fetchone()[0] == 12


def test_query_failure_releases_connection(archive, monkeypatch):
    from database import historify_db

    scope, _ = archive
    original, retained = historify_db.get_connection, []
    with original() as connection:
        connection.execute("ALTER TABLE market_data DROP COLUMN oi")

    @contextmanager
    def counted():
        with original() as connection:
            retained.append(connection)
            yield connection

    monkeypatch.setattr(historify_db, "get_connection", counted)
    with pytest.raises(duckdb.BinderException):
        scope.read_many([("AAA", "2026-01-05", "2026-01-07")])
    with pytest.raises(duckdb.ConnectionException):
        retained[0].execute("SELECT 1")


def test_row_limit_is_per_window(archive):
    from database import historify_db

    scope, _ = archive
    stamp = int(datetime(2026, 1, 5, tzinfo=IST).timestamp())
    with historify_db.get_connection() as connection:
        connection.execute("DELETE FROM market_data")
        connection.execute(
            "INSERT INTO market_data SELECT 'AAA','NSE','D',?+i,100,102,99,101,1000,0 "
            "FROM range(3001) AS records(i)",
            [stamp],
        )
    with pytest.raises(ValueError, match="rows exceed"):
        scope.read_many([("AAA", "2026-01-05", "2026-01-05")])


def test_minute_aware_bounds_and_exchange_isolation(archive):
    _, path = archive
    scope = NativeHistorifyArchive(path, interval="1m")
    first = datetime(2026, 1, 5, tzinfo=IST)
    requests = [("AAA", first.isoformat(), (first + timedelta(minutes=1)).isoformat())]
    assert scope.read_many(requests) == [native_historify_read(*requests[0], path, interval="1m")]
    assert len(scope.read_many(requests)[0]) == 1
    with pytest.raises(ValueError, match="aware"):
        scope.read_many([("AAA", "2026-01-05", "2026-01-05")])
    index_scope = NativeHistorifyArchive(path, exchange="NSE_INDEX")
    assert len(index_scope.read_many([("AAA", "2026-01-05", "2026-01-07")])[0]) == 3
