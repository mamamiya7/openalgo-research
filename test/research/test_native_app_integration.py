"""Run the actual app.py factory and global guards with live services disabled.

Unrelated blueprints, initializers and outbound/background services are controlled
dependencies. Research, React routing, Flask signed sessions, global expiry and
global Flask-WTF CSRF execute their real application code. No scheduler starts.
"""

import ast
import importlib.util
import sys
import threading
import types
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Blueprint
from flask_wtf.csrf import generate_csrf


@pytest.fixture
def native_app(tmp_path, monkeypatch):
    import blueprints.react_app as react
    import blueprints.scanner_research  # noqa: F401
    import utils.session as app_session

    root = Path(__file__).resolve().parents[2]
    source = (root / "app.py").read_text(encoding="utf-8")
    document = ast.parse(source)
    keep = {
        "flask",
        "flask_wtf.csrf",
        "blueprints.react_app",
        "blueprints.scanner_research",
        "utils.logging",
    }
    modules = {}
    bp_by_attribute = {}
    for node in document.body:
        if not isinstance(node, ast.ImportFrom) or node.module in keep:
            continue
        module = modules.setdefault(node.module, types.ModuleType(node.module))
        for item in node.names:
            if item.name.endswith("_bp"):
                name = {"auth_bp": "auth", "brlogin_bp": "brlogin", "flow_bp": "flow"}.get(
                    item.name, item.name
                )
                value = Blueprint(name, __name__)
                bp_by_attribute[item.name] = value
            else:
                value = MagicMock(name=f"disabled:{node.module}.{item.name}")
            setattr(module, item.name, value)
    endpoints = {
        "chartink_bp": ["webhook"],
        "strategy_module_bp": ["webhook"],
        "postback_bp": ["broker_postback"],
        "flow_bp": ["trigger_webhook", "trigger_webhook_with_symbol"],
        "brlogin_bp": ["broker_callback"],
        "health_bp": ["simple_health", "detailed_health_check"],
        "auth_bp": ["login"],
    }
    for attribute, names in endpoints.items():
        for name in names:
            bp_by_attribute[attribute].add_url_rule(
                f"/__disabled/{attribute}/{name}", endpoint=name, view_func=lambda: "disabled"
            )
    bp_by_attribute["auth_bp"].add_url_rule(
        "/auth/csrf-token", endpoint="csrf_token", view_func=lambda: {"csrf_token": generate_csrf()}
    )
    for name, attributes in {
        "subscribers": ["register_all"],
        "portfolio": ["warm_analytics"],
        "services.order_update_service": ["start_order_update_adapters_on_boot"],
        "utils.precompress_assets": ["ensure_precompressed_assets"],
        "utils.ngrok_manager": ["setup_ngrok_handlers"],
        "utils.db_sessions": ["remove_all_scoped_sessions"],
    }.items():
        module = modules.setdefault(name, types.ModuleType(name))
        for attribute in attributes:
            setattr(module, attribute, MagicMock(name=f"disabled:{name}.{attribute}"))
    # The optional API-key bridge is imported inside create_app, so the top-level
    # import scan above cannot see it. Keep it controlled like other unrelated
    # transports rather than importing real routes while their limiter is mocked.
    # Its actual owner/auth/scope behavior runs in test_portfolio_mcp.py.
    mcp_module = types.ModuleType("blueprints.mcp_http")
    mcp_module.research_mcp_api_bp = Blueprint("research_mcp_api", __name__)
    modules["blueprints.mcp_http"] = mcp_module
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("APP_KEY", "isolated-research-app-key" * 3)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "native-account.db"))
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path / "research"))
    monkeypatch.setenv("APP_MODE", "standalone")
    monkeypatch.setenv("CSRF_ENABLED", "TRUE")
    monkeypatch.setenv("MCP_OAUTH_ENABLED", "false")
    monkeypatch.setenv("DISABLE_SESSION_EXPIRY", "false")
    monkeypatch.setenv("SESSION_EXPIRY_TIME", "00:00")
    monkeypatch.setenv("HOST_SERVER", "http://127.0.0.1:5127")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<title>Isolated native OpenAlgo research</title>")
    monkeypatch.setattr(react, "FRONTEND_DIST", frontend)
    starts = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: starts.append(self.name))
    revoked = MagicMock()
    monkeypatch.setattr(app_session, "revoke_user_tokens", revoked)
    spec = importlib.util.spec_from_file_location("isolated_native_app", root / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.app
    app.config["TESTING"] = True
    app.db_ready.set()
    try:
        yield app, revoked, starts
    finally:
        app.extensions["research_store"].close()


def test_actual_native_registration_account_boundary_and_csrf(native_app):
    app, revoked, starts = native_app
    assert starts  # startup requested threads; the harness suppressed their execution
    assert "check_session_expiry" in {
        function.__name__ for function in app.before_request_funcs[None]
    }
    client = app.test_client()
    assert client.get("/scanner-research/api/jobs").status_code == 401
    assert (
        client.get("/scanner-research").status_code == 200
    )  # public SPA shell; data remains authenticated
    with client.session_transaction() as session:
        session["user"] = "account-without-broker"
    assert client.get("/scanner-research").status_code == 200
    assert client.get("/scanner-research/api/jobs").status_code == 200
    assert (
        client.get("/dashboard").status_code == 200
    )  # native SPA applies its existing trading route guard
    denied = client.post("/scanner-research/api/jobs", json={"source_id": "missing"})
    assert denied.status_code == 400 and "CSRF" in denied.json["error"]
    token = client.get("/auth/csrf-token").json["csrf_token"]
    validated = client.post(
        "/scanner-research/api/jobs", json={"source_id": "missing"}, headers={"X-CSRFToken": token}
    )
    assert validated.status_code == 404 and "Source not found" in validated.json["message"]
    assert not revoked.called


def test_actual_native_expired_app_session_is_not_exempted(native_app):
    app, revoked, _ = native_app
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(
            user="expired-account",
            logged_in=True,
            login_time=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
        )
    assert client.get("/scanner-research/api/jobs").status_code == 401
    revoked.assert_called_once_with(revoke_db_tokens=True)
    with client.session_transaction() as session:
        assert "user" not in session


def test_actual_native_valid_account_retains_saved_access_with_expired_broker(native_app):
    app, revoked, _ = native_app
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(
            user="valid-account",
            logged_in=True,
            login_time=datetime.now(UTC).isoformat(),
            broker="fyers",
        )
    assert client.get("/scanner-research/api/jobs").status_code == 200
    assert not revoked.called  # no broker request/token validity check is needed for saved research
