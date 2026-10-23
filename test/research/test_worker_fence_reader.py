"""Every optimized fence invocation still executes the live bounded SQL read."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, bindparam, select
from sqlalchemy.exc import InvalidRequestError, MultipleResultsFound

from database.engine_factory import create_db_engine
from services import research_worker_sessions as scopes


@pytest.fixture
def fence(tmp_path):
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'fence.db').as_posix()}")
    metadata = MetaData()
    table = Table("fence", metadata, Column("id", Integer, primary_key=True), Column("token", String), Column("status", String))
    metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(table.insert(), [{"id": 1, "token": "owner", "status": "running"}])
    try:
        yield engine, table
    finally:
        engine.dispose()


def test_native_fence_same_rows_every_callback_fresh_external_control(fence, monkeypatch):
    engine, table = fence
    calls = []
    original = scopes._sqlite_fence
    def measured(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(scopes, "_sqlite_fence", measured)
    statement = select(table.c.token, table.c.status).where(table.c.id == 1)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        read = scopes.fence_reader(connection, statement)
        for status in ("running", "pausing", "cancelling", "running"):
            with engine.begin() as writer:
                writer.execute(table.update().where(table.c.id == 1).values(status=status))
            with connection.execute(statement) as result:
                expected = tuple(result.one())
            assert read() == expected
            assert read() == ("owner", status)
        with engine.begin() as writer:
            writer.execute(table.update().where(table.c.id == 1).values(token="other-worker"))
        assert read() == ("other-worker", "running")
        with engine.begin() as writer:
            writer.execute(table.delete().where(table.c.id == 1))
        assert read() is None
        assert len(calls) == 10
    with pytest.raises(InvalidRequestError, match="outlive"):
        read()


def test_fence_rejects_multiple_rows_and_cross_thread_use(fence):
    engine, table = fence
    with engine.begin() as writer:
        writer.execute(table.insert().values(id=2, token="another", status="running"))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        read = scopes.fence_reader(connection, select(table.c.token, table.c.status))
        with pytest.raises(MultipleResultsFound):
            read()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read)
            with pytest.raises(InvalidRequestError, match="owning thread"):
                future.result(timeout=10)


def test_fence_bound_values_never_become_sql(fence):
    engine, table = fence
    attack = "owner' OR 1=1 --"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        read = scopes.fence_reader(connection, select(table.c.token).where(table.c.token == attack))
        assert read() is None


def test_fallback_for_transaction_mode_and_dynamic_parameters(fence, monkeypatch):
    engine, table = fence
    def unexpected(*args):
        raise AssertionError("Unsupported execution must use SQLAlchemy")
    monkeypatch.setattr(scopes, "_sqlite_fence", unexpected)
    with engine.connect() as connection:
        read = scopes.fence_reader(connection, select(table.c.token).where(table.c.id == 1))
        assert tuple(read()) == ("owner",)
    value = [1]
    dynamic = select(table.c.token).where(table.c.id == bindparam("identity", callable_=lambda: value[0]))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        read = scopes.fence_reader(connection, dynamic)
        assert tuple(read()) == ("owner",)
        value[0] = 99
        assert read() is None


def test_non_sqlite_fallback_keeps_result_context_cleanup():
    class Result:
        closed = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.closed = True
        def one_or_none(self):
            return ("fallback",)
    result = Result()
    statement = object()
    seen = []
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="other"),
        closed=False,
        execute=lambda query: (seen.append(query), result)[1],
    )
    assert scopes.fence_reader(connection, statement)() == ("fallback",)
    assert seen == [statement]
    assert result.closed


def test_custom_sqlite_driver_uses_sqlalchemy_fallback(fence, monkeypatch):
    engine, table = fence
    class CustomConnection(sqlite3.Connection):
        pass
    custom_driver = sqlite3.connect(":memory:", factory=CustomConnection, isolation_level=None)
    statement = select(table.c.token).where(table.c.id == 1)
    def unexpected(*args):
        raise AssertionError("A custom driver must retain SQLAlchemy execution")
    monkeypatch.setattr(scopes, "_sqlite_fence", unexpected)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as actual:
            # Delegate execution to the real connection, presenting a custom
            # driver only for the capability check. It must never be used.
            wrapper = SimpleNamespace(
                dialect=actual.dialect,
                get_execution_options=actual.get_execution_options,
                connection=SimpleNamespace(driver_connection=custom_driver),
                closed=False,
                execute=actual.execute,
            )
            assert tuple(scopes.fence_reader(wrapper, statement)()) == ("owner",)
    finally:
        custom_driver.close()


@pytest.mark.parametrize("failure", [None, "execute", "fetch", "multiple"])
def test_native_cursor_closes_on_every_exit_and_does_not_accumulate(failure):
    class Cursor:
        def __init__(self, driver):
            self.driver = driver
            driver.open += 1
        def execute(self, sql, params):
            assert (sql, params) == ("select ?", (1,))
            if failure == "execute":
                raise sqlite3.OperationalError("syntax error")
        def fetchmany(self, count):
            assert count == 2
            if failure == "fetch":
                raise sqlite3.OperationalError("read failure")
            return [(1,), (2,)] if failure == "multiple" else []
        def close(self):
            self.driver.open -= 1
    driver = SimpleNamespace(open=0)
    driver.cursor = lambda: Cursor(driver)
    for _ in range(100):
        if failure:
            with pytest.raises((sqlite3.OperationalError, MultipleResultsFound)):
                scopes._sqlite_fence(driver, "select ?", (1,))
        else:
            assert scopes._sqlite_fence(driver, "select ?", (1,)) is None
        assert driver.open == 0
