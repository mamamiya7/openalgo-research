"""Research onboarding uses native broker keys without granting trading access."""

import ast
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request, session
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy.orm import scoped_session, sessionmaker

from blueprints import broker_credentials as credentials
from blueprints.broker_config import broker_config_bp
from database import auth_db, broker_keys_db
from database.engine_factory import create_db_engine
from utils import broker_keys, broker_login
from utils import session as native_session

USER = "research-test-trader"
JSON = {"Accept": "application/json"}
KEY = "research-fixture-app-100"
SECRET = "research-fixture-secret"


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Use actual native auth/key tables, isolated from every installed database."""
    env = tmp_path / ".env"
    env.write_text("APP_KEY='preserved'\nBROKER_API_KEY=''\nBROKER_API_SECRET=''\n")
    monkeypatch.setattr(broker_keys, "env_file_path", lambda: env)
    monkeypatch.setattr(broker_keys, "_STARTUP_VALUES", dict.fromkeys(broker_keys.ENV_VARS.values()))
    for key in broker_keys.ENV_VARS.values():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VALID_BROKERS", "fyers,zerodha,dhan")
    monkeypatch.setenv("HOST_SERVER", "http://127.0.0.1:5247")
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'openalgo.db').as_posix()}")
    swapped = []
    for module in (auth_db, broker_keys_db):
        scoped = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
        previous_query = module.Base.__dict__["query"]
        monkeypatch.setattr(module, "engine", engine)
        monkeypatch.setattr(module, "db_session", scoped)
        module.Base.query = scoped.query_property()
        swapped.append((module, scoped, previous_query))
        module.Base.metadata.create_all(engine)
    auth_db.auth_cache.clear()
    auth_db._session_revocation_cache.invalidate()
    broker_keys.forget_logged_in_broker()
    broker_login.clear_pending_logins()

    flask_app = Flask(__name__)
    flask_app.config.update(TESTING=True, SECRET_KEY="test-private-key")
    CSRFProtect(flask_app)
    flask_app.register_blueprint(credentials.broker_credentials_bp)
    flask_app.register_blueprint(broker_config_bp)
    flask_app.add_url_rule("/csrf", "csrf", lambda: jsonify(token=generate_csrf()))

    @flask_app.before_request
    def drop_ended_sessions():
        native_session.end_session_if_signed_out()

    try:
        yield flask_app
    finally:
        auth_db.auth_cache.clear()
        auth_db._session_revocation_cache.invalidate()
        broker_keys.forget_logged_in_broker()
        broker_login.clear_pending_logins()
        for module, scoped, previous_query in reversed(swapped):
            module.Base.query = previous_query
            scoped.remove()
        engine.dispose()


def account(client, **overrides):
    with client.session_transaction() as current:
        current.clear()
        if "pending_totp_user" in overrides:
            current.update(pending_totp_started_at=datetime.now(UTC).isoformat(), **overrides)
        else:
            current.update(user=USER, auth_at=datetime.now(UTC).isoformat())
            current.update(overrides)


def put(client, data, *, path="/api/broker/credentials/fyers"):
    token = client.get("/csrf").json["token"]
    return client.put(path, json=data, headers={**JSON, "X-CSRFToken": token})


@pytest.mark.parametrize("path", ["/api/broker/list", "/api/broker/credentials"])
def test_anonymous_and_pending_totp_cannot_read_configuration(app, path):
    client = app.test_client()
    assert client.get(path, headers=JSON).status_code == 401
    account(client, pending_totp_user=USER)
    assert client.get(path, headers=JSON).status_code == 401


@pytest.mark.parametrize("marker_case", ["missing", "malformed", "before_rollover"])
def test_account_setup_uses_native_sign_in_lifetime(app, marker_case):
    marker = {
        "missing": None,
        "malformed": "invalid",
        "before_rollover": (native_session.last_rollover_boundary() - timedelta(seconds=1)).isoformat(),
    }[marker_case]
    client = app.test_client()
    account(client, auth_at=marker)
    assert client.get("/api/broker/list", headers=JSON).status_code == 401
    with client.session_transaction() as current:
        assert "user" not in current


def test_ended_account_sign_in_cannot_configure_broker(app):
    client = app.test_client()
    account(client)
    auth_db.db_session.add(auth_db.SessionRevocation(username=USER, revoked_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1)))
    auth_db.db_session.commit()
    auth_db._session_revocation_cache.invalidate()
    assert put(client, {"api_key": KEY, "api_secret": SECRET}).status_code == 401
    assert broker_keys_db.get_broker_keys("fyers") is None


def test_verified_account_can_save_first_broker_without_trading_authentication(app):
    client = app.test_client()
    account(client)
    path = broker_keys.env_file_path()
    before = path.read_bytes()
    response = put(client, {"api_key": KEY, "api_secret": SECRET})
    assert response.status_code == 200 and response.json["signed_out"] is False
    assert not response.json.get("restart_required")
    stored = broker_keys_db.get_broker_keys("fyers")
    assert (stored.api_key, stored.api_secret) == (KEY, SECRET)
    assert path.read_bytes() == before
    with client.session_transaction() as current:
        assert current["user"] == USER
        assert not current.get("logged_in") and not current.get("broker")
    assert client.get("/api/broker/capabilities", headers=JSON).status_code == 401
    with client.session_transaction() as current:
        assert current["user"] == USER


def test_saved_keys_and_secrets_are_not_returned_or_logged(app, caplog):
    client = app.test_client()
    account(client)
    assert put(client, {"api_key": KEY, "api_secret": SECRET}).status_code == 200
    response = client.get("/api/broker/list", headers=JSON)
    body = response.get_data(as_text=True)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert KEY not in body and SECRET not in body
    assert KEY not in caplog.text and SECRET not in caplog.text
    fyers = next(row for row in response.json["data"]["brokers"] if row["id"] == "fyers")
    assert fyers["configured"] and not fyers["active"]
    assert {field["key"]: field["saved"] for field in fyers["fields"]}["api_secret"] == "********"
    assert response.json["data"]["logged_in"] is None


def test_pending_totp_cannot_save_even_with_csrf(app):
    client = app.test_client()
    account(client, pending_totp_user=USER)
    assert put(client, {"api_key": KEY, "api_secret": SECRET}).status_code == 401
    assert broker_keys_db.get_broker_keys("fyers") is None


def test_saving_keys_requires_csrf(app):
    client = app.test_client()
    account(client)
    response = client.put("/api/broker/credentials/fyers", json={"api_key": KEY, "api_secret": SECRET})
    assert response.status_code == 400
    assert broker_keys_db.get_broker_keys("fyers") is None


def test_old_server_settings_endpoint_does_not_accept_broker_keys(app):
    client = app.test_client()
    account(client)
    token = client.get("/csrf").json["token"]
    path = broker_keys.env_file_path()
    before = path.read_bytes()
    response = client.post("/api/broker/credentials", json={"broker_api_key": KEY, "broker_api_secret": SECRET}, headers={"X-CSRFToken": token})
    assert response.status_code == 400
    assert path.read_bytes() == before and broker_keys_db.get_broker_keys("fyers") is None


def test_existing_broker_session_still_uses_native_validity_guard(app, monkeypatch):
    client = app.test_client()
    account(client, logged_in=True, broker="fyers")
    monkeypatch.setattr(native_session, "is_session_valid", lambda: True)
    assert client.get("/api/broker/credentials", headers=JSON).status_code == 200
    monkeypatch.setattr(native_session, "is_session_valid", lambda: False)
    revoked = []
    monkeypatch.setattr(native_session, "revoke_user_tokens", lambda: revoked.append(True))
    assert client.get("/api/broker/credentials", headers=JSON).status_code == 401
    assert revoked == [True]


@pytest.mark.parametrize("value", ["key\nFLASK_DEBUG=True", "key\0extra", "key\rFLASK_DEBUG=True"])
def test_setup_rejects_control_characters_without_writing(app, value):
    client = app.test_client()
    account(client)
    before = broker_keys.env_file_path().read_bytes()
    assert put(client, {"api_key": value, "api_secret": SECRET}).status_code == 400
    assert broker_keys.env_file_path().read_bytes() == before
    assert broker_keys_db.get_broker_keys("fyers") is None


@pytest.mark.parametrize("payload", [None, [], [1], "text", 1, True])
def test_setup_requires_an_object_body(app, payload):
    client = app.test_client()
    account(client)
    token = client.get("/csrf").json["token"]
    response = client.put("/api/broker/credentials/fyers", data=json.dumps(payload), content_type="application/json", headers={"X-CSRFToken": token})
    assert response.status_code == 400
    assert broker_keys_db.get_broker_keys("fyers") is None


@pytest.mark.parametrize("requires_totp, valid_code", [(False, True), (True, True), (True, False)])
def test_native_password_and_totp_stamp_only_completed_account_login(app, monkeypatch, requires_totp, valid_code):
    """Execute native function bodies; isolate password/broker I/O, not auth stamps."""
    source = Path(__file__).resolve().parents[2] / "blueprints/auth.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    monkeypatch.setattr(auth_db, "log_login_attempt", lambda *_, **__: None)
    user = SimpleNamespace(is_totp_required_for=lambda _: requires_totp, verify_totp=lambda _: valid_code)
    namespace = {
        "session": session, "request": request, "jsonify": jsonify,
        "logger": logging.getLogger(__name__), "datetime": datetime, "UTC": UTC,
        "find_user_by_username": lambda: user, "find_user_by_exact_username": lambda _: user,
        "authenticate_user": lambda *_: True, "get_real_ip": lambda: "127.0.0.1",
        "_try_resume_broker_session": lambda _: None,
        "_utcnow_iso": lambda: datetime.now(UTC).isoformat(),
        "_pending_totp_is_fresh": lambda: True,
        "_clear_pending_totp": lambda: session.pop("pending_totp_user", None),
        "mark_signed_in": native_session.mark_signed_in,
    }
    for name in ("login", "login_totp"):
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        function.decorator_list = []
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    with app.test_request_context("/auth/login", method="POST", data={"username": USER, "password": "fixture-password"}):
        namespace["login"]()
        assert bool(session.get("auth_at")) is not requires_totp
        assert bool(session.get("account_authenticated_at")) is not requires_totp
        assert not session.get("logged_in")
        if not requires_totp:
            assert session["account_authenticated_at"] == session["auth_at"]
        pending = dict(session)
    if requires_totp:
        with app.test_request_context("/auth/login/totp", method="POST", json={"totp_code": "123456"}):
            session.update(pending)
            namespace["login_totp"]()
            assert bool(session.get("auth_at")) is valid_code
            assert bool(session.get("account_authenticated_at")) is valid_code
            assert bool(session.get("user")) is valid_code
            assert not session.get("logged_in")
            if valid_code:
                assert session["account_authenticated_at"] == session["auth_at"]
