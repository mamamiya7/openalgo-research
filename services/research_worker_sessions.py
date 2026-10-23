"""One bounded metadata connection for sequential sessions in a native worker.

The engine's NullPool and transaction settings are unchanged. A fresh ORM
Session owns each unit of work; overlapping sessions and other threads always
use the original independent connection factory.
"""

import sqlite3
from contextlib import contextmanager
from threading import get_ident
from weakref import ref

from sqlalchemy.exc import InvalidRequestError, MultipleResultsFound
from sqlalchemy.orm import Session, sessionmaker


def _sqlite_fence(driver, sql, parameters):
    """Execute one fresh bounded read; every invocation owns its own cursor."""
    cursor = driver.cursor()
    try:
        cursor.execute(sql, parameters)
        rows = cursor.fetchmany(2)
        if len(rows) > 1:
            raise MultipleResultsFound("Multiple rows were found when one or none was required")
        return rows[0] if rows else None
    finally:
        cursor.close()


def fence_reader(connection, live_fence):
    """Compile an immutable SQLite live fence once, never its observed result.

    Only a native pysqlite connection explicitly configured as AUTOCOMMIT is
    eligible. Other drivers, transaction modes or dynamic/expanding bind
    parameters retain SQLAlchemy's ordinary execution path. No transaction,
    isolation setting, connection ownership or callback frequency is changed.
    """
    owner = get_ident()
    driver = None
    dialect = connection.dialect
    if (
        dialect.name == "sqlite"
        and dialect.driver == "pysqlite"
        and dialect.paramstyle == "qmark"
        and connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
    ):
        from database import _CooperativeConnection

        candidate = connection.connection.driver_connection
        if (
            type(candidate) in (sqlite3.Connection, _CooperativeConnection)
            and candidate.isolation_level is None
            and candidate.row_factory is None
        ):
            compiled = live_fence.compile(dialect=dialect)
            if (
                not compiled.post_compile_params
                and not compiled.literal_execute_params
                and not any(bind.callable for bind in compiled.binds.values())
                and not any(
                    column.type._cached_result_processor(dialect, None) is not None
                    for column in compiled._result_columns
                )
            ):
                values = compiled.construct_params()
                processors = compiled._bind_processors
                parameters = tuple(
                    processors[name](values[name]) if name in processors else values[name]
                    for name in compiled.positiontup
                )
                driver, sql = candidate, str(compiled)

    def read():
        if get_ident() != owner:
            raise InvalidRequestError("A live fence reader cannot leave its owning thread")
        if connection.closed:
            raise InvalidRequestError("A live fence reader cannot outlive its connection")
        if driver is not None:
            return _sqlite_fence(driver, sql, parameters)
        with connection.execute(live_fence) as result:
            return result.one_or_none()

    return read


class _OwnedSession(Session):
    def __init__(self, *, _scope, **kwargs):
        self._scope = ref(_scope)
        super().__init__(**kwargs)

    def close(self):
        try:
            super().close()
        finally:
            scope = self._scope()
            if scope is not None and scope.active is self:
                scope.active = None


class _SessionScope:
    def __init__(self, store):
        self.original = store.sessions
        self.engine = store.engine
        self.owner = get_ident()
        self.connection = None
        self.active = None
        self.closed = False

    def __call__(self, **kwargs):
        # Explicit per-call options may override binds or transaction semantics.
        # Preserve their original behavior rather than guessing about sharing.
        if self.closed or get_ident() != self.owner or self.active is not None or kwargs:
            return self.original(**kwargs)
        if self.connection is None:
            self.connection = self.engine.connect()
        session = _OwnedSession(
            _scope=self,
            **{**self.original.kw, "bind": self.connection, "close_resets_only": False},
        )
        self.active = session
        return session

    @contextmanager
    def begin(self):
        with self() as session:
            with session.begin():
                yield session

    def close(self):
        self.closed = True
        try:
            if self.active is not None:
                self.active.close()
        finally:
            if self.connection is not None:
                self.connection.close()
                self.connection = None


def _supported(store):
    factory = store.sessions
    return (
        isinstance(factory, sessionmaker)
        and factory.class_.__bases__ == (Session,)
        and factory.kw.get("bind") is store.engine
        and set(factory.kw) <= {"bind", "autoflush", "expire_on_commit"}
    )


@contextmanager
def scoped_metadata_sessions(store):
    """Reuse one owner-thread connection while retaining per-session commits.

    Only the ordinary ResearchStore session factory is admitted. Custom or
    already-wrapped factories keep their original behavior. No connection is
    opened until the owner actually creates a session, and all exits restore
    the factory and close both any unfinished Session and its connection.
    """
    if not _supported(store):
        yield
        return
    scope = _SessionScope(store)
    store.sessions = scope
    try:
        yield
    finally:
        store.sessions = scope.original
        scope.close()
