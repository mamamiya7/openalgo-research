# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Native research MCP: real saved runs plus transport identity/scope boundaries."""

import json
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import insert
from test_acquisition import reference
from test_jobs import app, client  # noqa: F401
from test_native_price_workflow import prepare_archive
from test_portfolio_workflow import portfolio

from services import research_mcp as api
from services import scanner_research_worker as worker


def test_saved_portfolio_runs_and_exact_replay_through_research_actions(
    app, client, monkeypatch, tmp_path
):
    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    request = portfolio(client, optimize=True)
    store = app.extensions["research_store"]
    owner = "research-test"
    def call(name, **args):
        return api.dispatch(store, owner, "research_" + name, args)
    uploaded = call("upload_csv", csv_text="Date,Symbol\n2026-01-05,AAA\n")
    assert uploaded["id"] in {row["id"] for row in call("list_sources")["items"]}
    assert call("list_runs")["items"] == []
    assert call("preview_portfolio", portfolio=request)["interval"] == "D"
    assert call("list_runs")["items"] == []
    queued = call("run_portfolio", portfolio=request, request_id="mcp-run-0001")
    assert call("run_portfolio", portfolio=request, request_id="mcp-run-0001")["id"] == queued["id"]
    assert call("resume_run", job_id=queued["id"])["status"] == "queued"
    worker.acquire(store, "mcp-test")
    try:
        assert worker.run_one(store, "mcp-test")
    finally:
        worker.release(store, "mcp-test")
    result = call("get_run", job_id=queued["id"], curve_points=1)
    assert result["status"] == "completed", result
    assert result["summary"]["initial_capital"] == 20000
    assert len(result["equity_curve"]) == 1
    assert len(result["per_strategy"]) == 2
    trades = call("get_trades", job_id=queued["id"], limit=1)
    assert len(trades["items"]) == 1 and trades["next_offset"] == 1
    filtered = call("get_trades", job_id=queued["id"], strategy_id="second")
    assert {row["strategy_id"] for row in filtered["items"]} == {"second"}
    trial = result["trials"][-1]
    definition = call(
        "export_strategy", job_id=queued["id"], strategy_id="second", trial_id=trial["config_id"]
    )
    assert definition["input"]["source_id"] == request["strategies"][1]["source_id"]
    assert definition["input"]["csv_sha256"]
    assert definition["execution_enabled"] is False
    assert "original_csv" not in json.dumps(definition)
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda *a, **kw: pytest.fail("Replay must use frozen prices"),
    )
    replay = call(
        "rerun_trial",
        job_id=queued["id"],
        trial_id=trial["config_id"],
        request_id="mcp-replay-0001",
    )
    worker.acquire(store, "mcp-replay")
    try:
        assert worker.run_one(store, "mcp-replay")
    finally:
        worker.release(store, "mcp-replay")
    assert call("get_run", job_id=replay["id"])["summary"] == trial["summary"]
    cancel = call("run_portfolio", portfolio=request, request_id="mcp-cancel-0001")
    assert call("cancel_run", job_id=cancel["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="no resumable checkpoint"):
        call("resume_run", job_id=cancel["id"])
    for name, arguments in (
        ("get_run", {"job_id": queued["id"]}),
        ("get_trades", {"job_id": queued["id"]}),
        ("cancel_run", {"job_id": queued["id"]}),
        ("export_strategy", {"job_id": queued["id"], "strategy_id": "second"}),
        ("preview_portfolio", {"portfolio": request}),
    ):
        with pytest.raises(LookupError):
            api.dispatch(store, "other-account", "research_" + name, arguments)
    assert api.list_sources(store, "other-account")["items"] == []
    assert api.list_runs(store, "other-account")["items"] == []


@pytest.fixture
def identities(monkeypatch, tmp_path):
    from sqlalchemy.orm import scoped_session, sessionmaker

    from database import auth_db, user_db
    from database.engine_factory import create_db_engine

    engine = create_db_engine("sqlite:///" + (tmp_path / "identities.db").as_posix())
    user_db.User.__table__.create(engine)
    auth_db.ApiKeys.__table__.create(engine)
    sessions = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setattr(user_db, "engine", engine)
    monkeypatch.setattr(auth_db, "db_session", sessions)
    monkeypatch.setattr(auth_db.ApiKeys, "query", sessions.query_property())
    monkeypatch.setattr(auth_db, "verified_api_key_cache", {})
    monkeypatch.setattr(auth_db, "invalid_api_key_cache", {})
    with engine.begin() as db:
        db.execute(
            insert(user_db.User),
            [
                {
                    "id": 71,
                    "username": "research-test",
                    "email": "owner@example.test",
                    "password_hash": "unused",
                    "totp_secret": "unused",
                },
                {
                    "id": 72,
                    "username": "other-account",
                    "email": "other@example.test",
                    "password_hash": "unused",
                    "totp_secret": "unused",
                },
            ],
        )
        db.execute(
            insert(auth_db.ApiKeys),
            {
                "user_id": "research-test",
                "api_key_hash": auth_db.ph.hash("test-native-api-key" + auth_db.PEPPER),
                "api_key_encrypted": "unused",
            },
        )
    try:
        yield
    finally:
        sessions.remove()
        engine.dispose()


def test_identity_uses_exact_native_user_and_native_api_key(identities):
    assert api.owner_from_claims({"sub": "71", "username": "other-account"}) == "research-test"
    assert api.owner_from_claims({"sub": "72"}) == "other-account"
    for claims in (
        None,
        {},
        {"sub": ""},
        {"sub": "999"},
        {"sub": "research-test"},
        {"sub": 71},
        {"sub": "-1"},
    ):
        with pytest.raises(PermissionError):
            api.owner_from_claims(claims)
    assert api.owner_from_api_key("test-native-api-key") == "research-test"
    with pytest.raises(PermissionError):
        api.owner_from_api_key("")


@pytest.fixture
def transports(app, identities, monkeypatch):
    # The repository package must not shadow the installed OpenAlgo SDK.
    root = Path(__file__).resolve().parents[2]
    parent = str(root.parent)
    while parent in sys.path:
        sys.path.remove(parent)
    shadow = sys.modules.get("openalgo")
    if shadow is not None and not hasattr(shadow, "api"):
        del sys.modules["openalgo"]
    monkeypatch.setenv("OPENALGO_MCP_HTTP_BOOT", "1")
    from blueprints import mcp_http
    from utils.mcp_tool_registry import _load_mcpserver_module

    server = _load_mcpserver_module()
    assert server is not None
    app.register_blueprint(mcp_http.mcp_http_bp)
    app.register_blueprint(mcp_http.research_mcp_api_bp)
    monkeypatch.setattr(mcp_http, "init_http_transport", lambda: None)
    monkeypatch.setattr(mcp_http, "_within_scope_quota", lambda **kwargs: True)
    monkeypatch.setattr(mcp_http, "_audit_log", lambda row: None)
    monkeypatch.setattr(
        mcp_http,
        "_notify_pre_write",
        lambda **kwargs: pytest.fail("Research must not send trading notifications"),
    )
    monkeypatch.setattr(mcp_http, "verify_access_token", lambda token: json.loads(token))
    return mcp_http, server


def rpc(client, name, arguments=None, scope="read:research", sub="71"):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        headers={
            "Authorization": "Bearer " + json.dumps({"sub": sub, "scope": scope, "jti": "test-jti"})
        },
    ).json


def test_remote_scopes_and_subject_bind_every_research_action(app, client, transports):
    _, server = transports
    uploaded = rpc(
        client,
        "research_upload_csv",
        {"csv_text": "Date,Symbol\n2026-01-05,AAA\n"},
        "write:research",
    )
    assert "result" in uploaded, uploaded
    source = json.loads(uploaded["result"]["content"][0]["text"])[server.DATA_KEY]
    for scope in ("read:market read:account", "read:research", "write:orders"):
        denied = rpc(
            client, "research_upload_csv", {"csv_text": "Date,Symbol\n2026-01-05,AAA\n"}, scope
        )
        assert denied["error"]["message"] == "insufficient_scope"
    own = rpc(client, "research_list_sources")
    assert source["id"] in {
        row["id"]
        for row in json.loads(own["result"]["content"][0]["text"])[server.DATA_KEY]["items"]
    }
    other = rpc(client, "research_list_sources", sub="72")
    assert json.loads(other["result"]["content"][0]["text"])[server.DATA_KEY]["items"] == []
    for subject in ("", "999"):
        assert "error" in rpc(client, "research_list_sources", sub=subject)
    assert "error" in rpc(client, "research_list_sources", {"owner": "other-account"})
    assert (
        rpc(client, "place_order", scope="read:research write:research")["error"]["message"]
        == "insufficient_scope"
    )
    # No verified context exists outside dispatcher, even when browser session exists.
    with app.test_request_context():
        with pytest.raises(PermissionError):
            server.research_list_sources()


def test_native_api_bridge_requires_api_key_and_obeys_read_only(
    app, client, transports, monkeypatch
):
    payload = {
        "apikey": "test-native-api-key",
        "name": "research_upload_csv",
        "arguments": {"csv_text": "Date,Symbol\n2026-01-05,AAA\n"},
    }
    assert client.post("/api/v1/research/tool", json={**payload, "apikey": ""}).status_code == 401
    assert (
        client.post("/api/v1/research/tool", json={**payload, "name": "place_order"}).status_code
        == 400
    )
    assert client.post("/api/v1/research/tool", json=payload).status_code == 200
    monkeypatch.setenv("OPENALGO_MCP_READ_ONLY", "1")
    assert client.post("/api/v1/research/tool", json=payload).status_code == 403
    assert (
        client.post(
            "/api/v1/research/tool",
            json={**payload, "name": "research_list_sources", "arguments": {}},
        ).status_code
        == 200
    )
    monkeypatch.setenv("OPENALGO_MCP_TOOLSETS", "account,marketdata")
    assert client.post("/api/v1/research/tool", json=payload).status_code == 403


@pytest.mark.parametrize(
    "name,args",
    [
        ("research_list_sources", {"limit": 101}),
        ("research_list_sources", {"limit": True}),
        ("research_get_trades", {"job_id": "a" * 32, "limit": 201}),
        ("research_get_run", {"job_id": "a" * 32, "curve_points": 301}),
        ("research_run_portfolio", {"portfolio": {}, "request_id": ""}),
        ("research_upload_csv", {"csv_text": "x" * (api.MAX_CSV_BYTES + 1)}),
        ("research_list_runs", {"owner": "other-account"}),
    ],
)
def test_bounds_reject_before_any_resource_lookup(name, args):
    with pytest.raises(ValueError):
        api.dispatch(None, "research-test", name, args)


def test_existing_oauth_grants_do_not_gain_new_research_scopes(transports):
    from blueprints.mcp_oauth import _supported_scopes
    from utils.mcp_tool_registry import list_tools_for_scopes

    assert {"read:research", "write:research"} <= set(_supported_scopes())
    assert (
        not set(list_tools_for_scopes(["read:market", "read:account", "write:orders"])) & api.TOOLS
    )
    assert set(list_tools_for_scopes(["read:research"])) == api.READ_TOOLS
    assert set(list_tools_for_scopes(["write:research"])) == api.WRITE_TOOLS


def test_stdio_closes_client_and_stream_on_success_and_oversized_response(transports, monkeypatch):
    _, server = transports
    monkeypatch.setenv("OPENALGO_MCP_HTTP_BOOT", "0")
    monkeypatch.setattr(server, "host", "http://research.example.test")
    monkeypatch.setattr(server, "api_key", "test-native-api-key")
    real_client = httpx.Client
    clients = []
    content = [b'{"items":[]}']

    def client_factory(**kwargs):
        client = real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content[0])),
            **kwargs,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(server.httpx, "Client", client_factory)
    assert json.loads(server._research_call("research_list_sources", {})) == {"items": []}
    assert clients[-1].is_closed
    content[0] = b"x" * (api.MAX_RESPONSE_BYTES + 1)
    assert "too large" in server._research_call("research_list_sources", {})
    assert clients[-1].is_closed
