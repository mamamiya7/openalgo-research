# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Readiness checks inspect isolated native metadata, without fetching or decrypting."""

import json
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import Boolean, Column, Integer, String, Text
from sqlalchemy.orm import Session, declarative_base
from test_jobs import app, client  # noqa: F401

from database.engine_factory import create_db_engine
from services import research_sources


@pytest.fixture
def readiness(app, monkeypatch, tmp_path):
    store = app.extensions["research_store"]
    archive = store.root / "isolated.duckdb"
    monkeypatch.setenv("RESEARCH_HISTORIFY_DATABASE_PATH", str(archive))
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", str(archive))
    monkeypatch.setenv("RESEARCH_REQUIRE_ISOLATED_ARCHIVE", "true")
    monkeypatch.setenv("BROKER_API_KEY", "isolated-app-key")
    monkeypatch.setenv("BROKER_API_SECRET", "isolated-app-secret")
    monkeypatch.setenv("RESEARCH_PUBLIC_EVIDENCE_DIR", str(tmp_path / "reference"))
    monkeypatch.setattr("research.evidence_import.official_bundle_status", lambda *args: {})
    base = declarative_base()

    class Auth(base):
        __tablename__ = "auth"
        id = Column(Integer, primary_key=True)
        name = Column(String)
        broker = Column(String)
        is_revoked = Column(Boolean)
        auth = Column(Text)

    class Master(base):
        __tablename__ = "master_contract_status"
        broker = Column(String, primary_key=True)
        status = Column(String)
        is_ready = Column(Boolean)
        total_symbols = Column(String)

    engine = create_db_engine(f"sqlite:///{tmp_path / 'isolated-native.db'}")
    base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            Auth(
                id=1,
                name="research-test",
                broker="fyers",
                is_revoked=False,
                auth="never-return-token",
            )
        )
        db.add(Master(broker="fyers", status="success", is_ready=True, total_symbols="2000"))
        db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("Status must not decrypt credentials or fetch history")

    monkeypatch.setitem(
        sys.modules,
        "database.auth_db",
        SimpleNamespace(Auth=Auth, engine=engine, decrypt_token=forbidden),
    )
    monkeypatch.setitem(
        sys.modules,
        "database.master_contract_status_db",
        SimpleNamespace(MasterContractStatus=Master, engine=engine),
    )
    monkeypatch.setattr(research_sources, "resolve_broker_session", forbidden)
    try:
        yield store, engine, Auth, Master
    finally:
        engine.dispose()


def status(readiness, **kwargs):
    return research_sources.source_capabilities(
        store=readiness[0],
        owner="research-test",
        broker_session=True,
        **kwargs,
    )["broker"]


def test_preview_never_ready_or_offers_broker_loop(readiness):
    result = status(readiness, preview=True)
    assert result["state"] == "preview"
    assert not result["configured"] and not result["connected"]
    assert "action_url" not in result


@pytest.mark.parametrize(
    "setting,value",
    [
        ("HISTORIFY_DATABASE_PATH", "elsewhere.duckdb"),
        ("RESEARCH_HISTORIFY_DATABASE_PATH", "outside.duckdb"),
    ],
)
def test_invalid_installation_never_available(readiness, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    result = status(readiness)
    assert result["state"] == "setup_required" and not result["configured"]
    assert "action_url" not in result


@pytest.mark.parametrize("field,value", [("is_revoked", True), ("broker", "other"), ("auth", "")])
def test_revoked_wrong_broker_and_empty_auth_require_login(readiness, field, value):
    _, engine, Auth, _ = readiness
    with Session(engine) as db:
        setattr(db.get(Auth, 1), field, value)
        db.commit()
    result = status(readiness)
    assert result["state"] == "sign_in_required" and not result["connected"]


@pytest.mark.parametrize(
    "master_status,count", [("downloading", "2000"), ("error", "2000"), ("success", "0")]
)
def test_missing_or_failed_symbol_readiness(readiness, master_status, count):
    _, engine, _, Master = readiness
    with Session(engine) as db:
        master = db.get(Master, "fyers")
        master.status, master.total_symbols = master_status, count
        db.commit()
    assert status(readiness)["state"] == "preparing_symbols"


def test_native_prices_do_not_require_public_reference(readiness, monkeypatch):
    monkeypatch.delenv("RESEARCH_PUBLIC_EVIDENCE_DIR")
    assert status(readiness)["state"] == "ready_to_download"


def test_matching_paths_outside_store_are_not_configured(readiness, monkeypatch, tmp_path):
    outside = tmp_path.parent / "not-this-research-store.duckdb"
    monkeypatch.setenv("RESEARCH_HISTORIFY_DATABASE_PATH", str(outside))
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", str(outside))
    assert status(readiness)["state"] == "setup_required"


def test_archive_directory_is_not_a_configured_file(readiness, monkeypatch):
    directory = str(readiness[0].root)
    monkeypatch.setenv("RESEARCH_HISTORIFY_DATABASE_PATH", directory)
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", directory)
    assert status(readiness)["state"] == "setup_required"


def test_missing_master_and_database_failure_release_connections(readiness):
    from sqlalchemy import event

    _, engine, _, Master = readiness
    outstanding = []
    event.listen(engine, "checkout", lambda *args: outstanding.append(1))
    event.listen(engine, "checkin", lambda *args: outstanding.pop())
    with Session(engine) as db:
        db.delete(db.get(Master, "fyers"))
        db.commit()
    assert status(readiness)["state"] == "preparing_symbols"
    Master.__table__.drop(engine)
    assert status(readiness)["state"] == "setup_required"
    assert not outstanding


def test_ready_is_metadata_only_and_does_not_expose_secrets(readiness):
    result = status(readiness)
    assert result["state"] == "ready_to_download"
    assert result["configured"] and result["connected"]
    assert not any(
        secret in json.dumps(result)
        for secret in ("never-return-token", "isolated-app-secret", "isolated-app-key")
    )


@pytest.mark.parametrize("provider", ["fyers", "zerodha", "dhan", "angel", "upstox"])
def test_readiness_follows_native_broker_without_assuming_app_credentials(
    readiness, monkeypatch, provider
):
    _, engine, Auth, Master = readiness
    monkeypatch.delenv("BROKER_API_KEY")
    monkeypatch.delenv("BROKER_API_SECRET")
    with Session(engine) as db:
        db.get(Auth, 1).broker = provider
        db.get(Master, "fyers").broker = provider
        db.commit()
    result = status(readiness, broker_name=provider)
    assert result["provider"] == provider
    assert result["state"] == "ready_to_download"


def test_complete_archive_can_be_checked_without_connected_broker(readiness):
    store, _, _, _ = readiness
    (store.root / "isolated.duckdb").touch()
    result = research_sources.source_capabilities(
        store=store, owner="research-test", broker_session=False
    )
    assert result["history"]["can_prepare"]
    assert not result["broker"]["connected"]


def test_native_archive_outside_research_store_is_supported_outside_development(
    readiness, monkeypatch, tmp_path
):
    monkeypatch.delenv("RESEARCH_REQUIRE_ISOLATED_ARCHIVE")
    archive = tmp_path.parent / "native-openalgo" / "historify.duckdb"
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", str(archive))
    monkeypatch.delenv("RESEARCH_HISTORIFY_DATABASE_PATH")
    assert research_sources.acquisition_paths(readiness[0])[0] == archive.resolve()


def test_session_and_auth_broker_must_agree(readiness):
    result = status(readiness, broker_name="zerodha")
    assert not result["connected"] and result["state"] == "sign_in_required"


@pytest.mark.parametrize("logged_in,valid", [(False, True), (True, False), (True, True)])
def test_route_uses_native_session_validity(readiness, app, client, monkeypatch, logged_in, valid):
    monkeypatch.setitem(
        sys.modules, "utils.session", SimpleNamespace(is_session_valid=lambda: valid)
    )
    with client.session_transaction() as session:
        session.update(broker="fyers", logged_in=logged_in)
    response = client.get("/scanner-research/api/source-capabilities")
    assert response.status_code == 200
    assert response.json["broker"]["state"] == (
        "ready_to_download" if logged_in and valid else "sign_in_required"
    )
