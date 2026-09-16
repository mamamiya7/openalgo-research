"""Account-only broker setup must not imply broker/trading authentication."""

import ast
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request, session
from flask_wtf.csrf import CSRFProtect, generate_csrf

from blueprints import broker_credentials as credentials
from utils import session as native_session


@pytest.fixture
def app(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "APP_KEY='preserved'\nBROKER_API_KEY=''\nBROKER_API_SECRET=''\nREDIRECT_URL='http://127.0.0.1:5247/fyers/callback'\n"
    )
    monkeypatch.setattr(credentials, "get_env_path", lambda: str(env))
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5247/fyers/callback")
    monkeypatch.setenv("VALID_BROKERS", "fyers,zerodha,dhan")
    for key in (
        "BROKER_API_KEY",
        "BROKER_API_SECRET",
        "BROKER_API_KEY_MARKET",
        "BROKER_API_SECRET_MARKET",
    ):
        monkeypatch.setenv(key, "")
    flask_app = Flask(__name__)
    flask_app.config.update(TESTING=True, SECRET_KEY="test-private-key")
    CSRFProtect(flask_app)
    flask_app.register_blueprint(credentials.broker_credentials_bp)
    flask_app.add_url_rule("/csrf", "csrf", lambda: jsonify(token=generate_csrf()))
    return flask_app


def account(client, **overrides):
    with client.session_transaction() as session:
        session.update(user="test-trader", account_authenticated_at=datetime.now(UTC).isoformat())
        session.update(overrides)


def post(client, data):
    token = client.get("/csrf").json["token"]
    return client.post("/api/broker/credentials", json=data, headers={"X-CSRFToken": token})


@pytest.mark.parametrize("path", ["/api/broker/credentials", "/api/broker/capabilities"])
def test_anonymous_and_pending_totp_cannot_read_settings(app, path):
    client = app.test_client()
    assert client.get(path).status_code == 401
    account(client, pending_totp_user="test-trader")
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "marker",
    [
        None,
        "invalid",
        (datetime.now(UTC) - timedelta(minutes=31)).isoformat(),
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    ],
)
def test_partial_login_requires_fresh_account_marker(app, marker):
    client = app.test_client()
    account(client, account_authenticated_at=marker)
    assert client.get("/api/broker/credentials").status_code == 401
    with client.session_transaction() as session:
        assert "user" not in session  # avoids the login -> broker -> login loop


def test_verified_account_can_configure_before_broker_and_preserves_other_settings(app):
    client = app.test_client()
    account(client)
    assert client.get("/api/broker/credentials").status_code == 200
    response = post(
        client,
        {
            "broker_api_key": "fixture-key",
            "broker_api_secret": "fixture-secret",
            "redirect_url": "http://127.0.0.1:5247/fyers/callback",
        },
    )
    assert response.status_code == 200 and response.json["restart_required"]
    content = Path(credentials.get_env_path()).read_text()
    assert "APP_KEY='preserved'" in content
    assert "BROKER_API_KEY='fixture-key'" in content
    with client.session_transaction() as session:
        assert not session.get("logged_in") and not session.get("broker")


def test_pending_totp_cannot_save_even_with_csrf(app):
    client = app.test_client()
    account(client, pending_totp_user="test-trader")
    assert post(client, {"broker_api_key": "fixture-key"}).status_code == 401


def test_post_requires_csrf_after_account_login(app):
    client = app.test_client()
    account(client)
    assert (
        client.post("/api/broker/credentials", json={"broker_api_key": "fixture-key"}).status_code
        == 400
    )
    assert "BROKER_API_KEY=''" in Path(credentials.get_env_path()).read_text()


def test_partial_setup_does_not_unlock_network_profile_fields(app):
    client = app.test_client()
    account(client)
    assert post(client, {"host_server": "http://example.com"}).status_code == 403
    assert post(client, {"ngrok_allow": True}).status_code == 403


def test_existing_broker_session_still_uses_native_validity_guard(app, monkeypatch):
    client = app.test_client()
    account(client, logged_in=True, broker="fyers", account_authenticated_at=None)
    monkeypatch.setattr(native_session, "is_session_valid", lambda: True)
    assert client.get("/api/broker/credentials").status_code == 200
    assert post(client, {"host_server": "http://127.0.0.1:5247"}).status_code == 200
    monkeypatch.setattr(native_session, "is_session_valid", lambda: False)
    revoked = []
    monkeypatch.setattr(native_session, "revoke_user_tokens", lambda: revoked.append(True))
    assert (
        client.get("/api/broker/credentials", headers={"Accept": "application/json"}).status_code
        == 401
    )
    assert revoked == [True]


def test_capabilities_use_configured_broker_before_oauth(app):
    client = app.test_client()
    account(client)
    response = client.get("/api/broker/capabilities")
    assert response.status_code == 200
    assert response.json["data"]["broker_name"] == "fyers"


def test_setup_rejects_multiline_credentials(app):
    client = app.test_client()
    account(client)
    assert post(client, {"broker_api_key": "key\nFLASK_DEBUG=True"}).status_code == 400


def test_credential_backslashes_remain_literal():
    assert (
        credentials.update_env_value("BROKER_API_KEY=''\n", "BROKER_API_KEY", r"key\g<1>")
        == "BROKER_API_KEY='key\\g<1>'\n"
    )


@pytest.mark.parametrize("requires_totp, valid_code", [(False, True), (True, True), (True, False)])
def test_native_password_and_totp_functions_stamp_only_completed_account_login(
    app, monkeypatch, requires_totp, valid_code
):
    """Execute the actual native function bodies with database/broker I/O stubbed."""
    source = Path(__file__).resolve().parents[2] / "blueprints/auth.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    monkeypatch.setitem(
        sys.modules, "database.auth_db", SimpleNamespace(log_login_attempt=lambda *_, **__: None)
    )
    user = SimpleNamespace(
        is_totp_required_for=lambda _: requires_totp, verify_totp=lambda _: valid_code
    )
    namespace = {
        "session": session,
        "request": request,
        "jsonify": jsonify,
        "logger": logging.getLogger(__name__),
        "datetime": datetime,
        "UTC": UTC,
        "find_user_by_username": lambda: user,
        "find_user_by_exact_username": lambda _: user,
        "authenticate_user": lambda *_: True,
        "get_real_ip": lambda: "127.0.0.1",
        "_try_resume_broker_session": lambda _: None,
        "_utcnow_iso": lambda: datetime.now(UTC).isoformat(),
        "_pending_totp_is_fresh": lambda: True,
        "_clear_pending_totp": lambda: session.pop("pending_totp_user", None),
    }
    for name in ("login", "login_totp"):
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
        )
        function.decorator_list = []
        module = ast.Module(body=[function], type_ignores=[])
        exec(compile(module, str(source), "exec"), namespace)
    with app.test_request_context(
        "/auth/login", method="POST", data={"username": "trader", "password": "fixture-password"}
    ):
        namespace["login"]()
        assert bool(session.get("account_authenticated_at")) is not requires_totp
        assert not session.get("logged_in")
        pending = dict(session)
    if requires_totp:
        with app.test_request_context(
            "/auth/login/totp", method="POST", json={"totp_code": "123456"}
        ):
            session.update(pending)
            namespace["login_totp"]()
            assert bool(session.get("account_authenticated_at")) is valid_code
            assert bool(session.get("user")) is valid_code
            assert not session.get("logged_in")
