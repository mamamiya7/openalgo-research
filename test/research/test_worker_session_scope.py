"""Owner-scoped connection reuse preserves independent transactions and cleanup."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, String, event, select, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from database.engine_factory import create_db_engine
from services.research_worker_sessions import scoped_metadata_sessions

Base = declarative_base()


class Item(Base):
    __tablename__ = "scope_items"
    id = Column(Integer, primary_key=True)
    value = Column(String, nullable=False)


@pytest.fixture
def store(tmp_path):
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'sessions.db').as_posix()}")
    Base.metadata.create_all(engine)
    value = SimpleNamespace(engine=engine, sessions=sessionmaker(bind=engine, expire_on_commit=False))
    try:
        yield value
    finally:
        engine.dispose()


def read(store, identity):
    with store.engine.connect() as connection:
        return connection.execute(select(Item.value).where(Item.id == identity)).scalar_one_or_none()


def physical(session):
    return session.connection().connection.driver_connection


def test_sequential_commit_visibility_and_fresh_identity_map(store):
    original = store.sessions
    with scoped_metadata_sessions(store):
        with store.sessions.begin() as first:
            assert isinstance(first, Session)
            one = Item(id=1, value="one")
            first.add(one)
            connection = physical(first)
        assert read(store, 1) == "one"
        with store.sessions.begin() as second:
            assert second is not first
            assert physical(second) is connection
            reread = second.get(Item, 1)
            assert reread is not one
            reread.value = "two"
        assert read(store, 1) == "two"
        with store.sessions() as plain:
            plain.add(Item(id=2, value="explicit"))
            plain.commit()
        assert read(store, 2) == "explicit"
    assert store.sessions is original


def test_failed_and_uncommitted_sessions_do_not_poison_next_transaction(store):
    with scoped_metadata_sessions(store):
        with store.sessions.begin() as db:
            db.add(Item(id=1, value="original"))
        with pytest.raises(IntegrityError):
            with store.sessions.begin() as db:
                db.add(Item(id=1, value="duplicate"))
        with store.sessions() as db:
            db.add(Item(id=2, value="uncommitted"))
            db.flush()
        with store.sessions.begin() as db:
            assert db.get(Item, 2) is None
            db.add(Item(id=3, value="next"))
        assert read(store, 1) == "original"
        assert read(store, 2) is None
        assert read(store, 3) == "next"


def test_next_session_sees_external_control_write(store):
    original = store.sessions
    with original.begin() as db:
        db.add(Item(id=1, value="running"))
    with scoped_metadata_sessions(store):
        with store.sessions() as db:
            previous = db.get(Item, 1)
            assert previous.value == "running"
            connection = physical(db)
        # An independent request updates control state between worker polls.
        with original.begin() as external:
            external.get(Item, 1).value = "cancelled"
        with store.sessions() as db:
            assert physical(db) is connection
            assert db.get(Item, 1).value == "cancelled"
            assert db.get(Item, 1) is not previous


def test_overlapping_sessions_and_helper_threads_have_independent_connections(store):
    original = store.sessions
    with scoped_metadata_sessions(store):
        with store.sessions() as outer:
            owner_connection = physical(outer)
            with store.sessions() as inner:
                assert physical(inner) is not owner_connection
            with ThreadPoolExecutor(max_workers=1) as executor:
                def helper():
                    with store.sessions() as db:
                        assert type(db) is original.class_
                        return physical(db) is owner_connection
                assert not executor.submit(helper).result(timeout=10)
        with store.sessions() as next_session:
            assert physical(next_session) is owner_connection


def test_scope_exception_closes_retained_session_and_connection(store):
    original = store.sessions
    retained = None
    with pytest.raises(RuntimeError, match="scope failed"):
        with scoped_metadata_sessions(store):
            retained = store.sessions()
            connection = retained.connection()
            retained.add(Item(id=1, value="must rollback"))
            retained.flush()
            raise RuntimeError("scope failed")
    assert store.sessions is original
    assert connection.closed
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        retained.execute(select(Item))
    assert read(store, 1) is None
    with store.sessions.begin() as db:
        db.add(Item(id=2, value="factory restored"))
    assert read(store, 2) == "factory restored"


def test_explicit_close_releases_owner_slot_and_escaped_factory_is_safe(store):
    with scoped_metadata_sessions(store):
        factory = store.sessions
        first = factory()
        connection = physical(first)
        first.close()
        with factory() as second:
            assert physical(second) is connection
    with factory() as outside:
        assert type(outside) is store.sessions.class_
        assert outside.execute(select(Item)).all() == []


def test_custom_factory_and_per_call_options_delegate(store):
    original = store.sessions
    with scoped_metadata_sessions(store):
        with store.sessions(expire_on_commit=True) as custom:
            assert type(custom) is original.class_
    class CustomSession(Session):
        pass
    store.sessions = sessionmaker(bind=store.engine, class_=CustomSession)
    custom_factory = store.sessions
    with scoped_metadata_sessions(store):
        assert store.sessions is custom_factory
        with store.sessions() as db:
            assert isinstance(db, CustomSession)


def test_repeated_scopes_close_every_checkout_and_preserve_pragmas(store):
    assert isinstance(store.engine.pool, NullPool)
    counts = {"checkout": 0, "checkin": 0, "connect": 0, "close": 0}
    def checkout(*args):
        counts["checkout"] += 1
    def checkin(*args):
        counts["checkin"] += 1
    def connect(*args):
        counts["connect"] += 1
    def close(*args):
        counts["close"] += 1
    for name, callback in (("checkout", checkout), ("checkin", checkin), ("connect", connect), ("close", close)):
        event.listen(store.engine, name, callback)
    with store.engine.connect() as db:
        before = [db.execute(text(f"pragma {name}")).scalar() for name in ("journal_mode", "synchronous")]
    for _identity in range(100):
        with scoped_metadata_sessions(store):
            for _ in range(3):
                with store.sessions() as db:
                    assert db.execute(select(Item)).all() == []
        assert counts["checkout"] == counts["checkin"]
        assert counts["connect"] == counts["close"]
    assert counts["connect"] == 101  # one per scope, not per Session.
    with store.engine.connect() as db:
        after = [db.execute(text(f"pragma {name}")).scalar() for name in ("journal_mode", "synchronous")]
    assert before == after
    assert counts["checkout"] == counts["checkin"]
    assert counts["connect"] == counts["close"]
