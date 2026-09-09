"""Native history contracts without importing broker clients or opening network/DBs."""

import ast
import importlib.util
import logging
import sys
import types
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def history(monkeypatch):
    auth = types.ModuleType("database.auth_db")
    auth.get_auth_token_broker = lambda *a, **k: ("auth", "feed", "example")
    token = types.ModuleType("database.token_db")
    token.get_token = lambda *a: "123"
    monkeypatch.setitem(sys.modules, "database.auth_db", auth)
    monkeypatch.setitem(sys.modules, "database.token_db", token)
    spec = importlib.util.spec_from_file_location("isolated_history", "services/history_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_enforce_rate_limit", lambda: None)
    return module


def candle():
    return {
        "timestamp": 1767571200,
        "open": 100,
        "high": 102,
        "low": 99,
        "close": 101,
        "volume": 25,
    }


def invoke(history, handler, **kw):
    history.import_broker_module = lambda broker: types.SimpleNamespace(BrokerData=handler)
    return history.get_history(
        symbol="AAA",
        exchange="NSE",
        interval="D",
        start_date="2026-01-05",
        end_date="2026-01-06",
        auth_token="secret",
        feed_token="feed-secret",
        broker="example",
        **kw,
    )


def handler_for(result):
    class Handler:
        timeframe_map = {"D": "day"}
        closed = 0

        def __init__(self, auth_token, feed_token=None, user_id=None):
            assert (auth_token, feed_token, user_id) == ("secret", "feed-secret", None)

        def get_history(self, *args):
            if isinstance(result, Exception):
                raise result
            return result

        def close(self):
            type(self).closed += 1

    return Handler


def test_generic_native_table_and_feed_token_contract(history):
    frame = pd.DataFrame([{**candle(), "private_metadata": "do not export"}])
    handler = handler_for(frame)
    success, body, status = invoke(history, handler, evidence_mode=True)
    assert success and status == 200
    assert body["data"] == [{**candle(), "oi": 0}]
    assert "oi" not in frame
    evidence = body["history_evidence"]
    assert evidence["evidence_level"] == "native_adapter"
    assert evidence["broker"] == "example"
    assert evidence["raw_transport_captured"] is False
    assert "http_status" not in evidence["chunks"][0]
    assert handler.closed == 1


@pytest.mark.parametrize(
    "result,status,outcome",
    [
        (pd.DataFrame(), 404, "empty_or_unreported_failure"),
        ({"status": "error"}, 502, "malformed_response"),
        (pd.DataFrame([{"open": 100}]), 502, "malformed_response"),
        (RuntimeError("secret URL bearer feed-secret"), 502, "adapter_failed"),
        (NotImplementedError(), 501, "unsupported_history"),
    ],
)
def test_explicit_outcomes_never_empty_success_or_secret(history, result, status, outcome):
    handler = handler_for(result)
    ok, body, code = invoke(history, handler, evidence_mode=True)
    assert not ok and code == status
    assert body["history_evidence"]["chunks"][0]["outcome"] == outcome
    assert "secret" not in str(body)
    assert handler.closed == 1


def test_normal_api_still_returns_empty_success(history):
    ok, body, status = invoke(history, handler_for(pd.DataFrame()), evidence_mode=False)
    assert ok and status == 200 and body == {"status": "success", "data": []}


def test_rich_contract_preferred_and_unchanged(history):
    sentinel = (False, {"status": "partial", "history_evidence": {"version": "rich"}}, 401)

    class Rich:
        def __init__(self, auth):
            assert auth == "secret"

        def get_history(self, *args):
            pytest.fail("must prefer rich evidence")

        def get_history_evidence(self, *args, request_control=None):
            assert request_control["marker"] == 1
            return sentinel

    assert invoke(history, Rich, evidence_mode=True, request_control={"marker": 1}) is sentinel


def test_cancel_before_network_and_after_opaque_call(history):
    calls = []

    def cancelled():
        raise InterruptedError

    with pytest.raises(InterruptedError):
        invoke(
            history,
            handler_for(pd.DataFrame([candle()])),
            evidence_mode=True,
            request_control={"check": cancelled},
        )

    class Handler:
        closed = False

        def __init__(self, auth):
            pass

        def get_history(self, *args):
            calls.append(True)
            return pd.DataFrame([candle()])

        def close(self):
            Handler.closed = True

    def check():
        if calls:
            raise InterruptedError

    with pytest.raises(InterruptedError):
        invoke(history, Handler, evidence_mode=True, request_control={"check": check})
    assert calls == [True] and Handler.closed


def test_deadline_and_date_bounds(history):
    with pytest.raises(TimeoutError):
        invoke(
            history,
            handler_for(pd.DataFrame()),
            evidence_mode=True,
            request_control={"clock": lambda: 5, "deadline": 5},
        )
    handler = handler_for(pd.DataFrame())("secret", "feed-secret")
    result = history._native_history_evidence(
        handler, "example", "AAA", "NSE", "D", "2000-01-01", "2026-01-01", None
    )
    assert result[2] == 400


def test_oversized_native_response_rejected_before_record_materialization(history, monkeypatch):
    frame = pd.DataFrame({name: [1] * 200001 for name in candle()})
    monkeypatch.setattr(frame, "to_dict", lambda **kw: pytest.fail("must bound before conversion"))
    assert invoke(history, handler_for(frame), evidence_mode=True)[2] == 502


def test_shared_pacing_and_api_feed_lookup_preserved(history):
    paced = []
    history._enforce_rate_limit = lambda: paced.append(True)

    class Handler:
        def __init__(self, auth, feed):
            assert (auth, feed) == ("auth", "feed")

        def get_history(self, *args):
            return pd.DataFrame([candle()])

    history.import_broker_module = lambda name: types.SimpleNamespace(BrokerData=Handler)
    result = history.get_history(
        symbol="AAA",
        exchange="NSE",
        interval="D",
        start_date="2026-01-05",
        end_date="2026-01-06",
        api_key="not-real",
        evidence_mode=True,
    )
    assert result[0] and paced == [True]


def native_methods(broker, names):
    """Execute exact native method ASTs; isolate imports/clients, not method logic."""
    path = Path("broker") / broker / "api/data.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BrokerData")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {"pd": pd, "logger": logging.getLogger("native-contract"), "timedelta": timedelta}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("broker", ["kotak", "paytm", "pocketful", "hdfcsecurities"])
def test_actual_unsupported_native_handlers(history, broker):
    ns = native_methods(broker, {"get_history"})

    class Handler:
        timeframe_map = {}
        get_history = ns["get_history"]

        def __init__(self, auth):
            pass

    # Empty interval map is native intervals_service's unsupported declaration.
    ok, body, status = invoke(history, Handler, evidence_mode=True)
    assert not ok and status == 501
    assert body["history_evidence"]["chunks"][0]["outcome"] == "unsupported_interval"


def test_actual_motilal_past_range_is_empty_not_historical_support(history):
    ns = native_methods("motilal", {"get_history"})

    class Handler:
        timeframe_map = {"D": "D"}
        DAILY_INTERVALS = {"D"}
        get_history = ns["get_history"]

        def __init__(self, auth):
            pass

        def _today_ist(self):
            return date(2026, 9, 6)

        def _today_bar(self, *args):
            pytest.fail("past range cannot request live snapshot")

    assert invoke(history, Handler, evidence_mode=True)[2] == 404


def test_actual_zerodha_daily_normalization_with_controlled_transport(history):
    ns = native_methods("zerodha", {"__init__", "get_history"})
    acquired = []

    class Query:
        def query(self, *args):
            return self

        def filter(self, *args):
            return self

        def first(self):
            return types.SimpleNamespace(token="123::::456", brexchange="NSE")

    @contextmanager
    def session():
        acquired.append("open")
        try:
            yield Query()
        finally:
            acquired.append("closed")

    requests = []

    def response(endpoint, auth):
        requests.append(endpoint)
        return {
            "status": "success",
            "data": {"candles": [["2026-01-05T00:00:00+0530", 100, 102, 99, 101, 25, 0]]},
        }

    ns.update(
        db_session=session,
        SymToken=types.SimpleNamespace(exchange="NSE", brsymbol="AAA"),
        get_br_symbol=lambda *a: "AAA",
        _kite_quote_exchange=lambda *a: "NSE",
        get_api_response=response,
        ZerodhaPermissionError=PermissionError,
        ZerodhaAPIError=RuntimeError,
    )
    Handler = type("Handler", (), {k: ns[k] for k in ("__init__", "get_history")})
    ok, body, status = invoke(history, Handler, evidence_mode=True)
    assert ok and status == 200 and body["data"] == [{**candle(), "oi": 0}]
    assert acquired == ["open", "closed"]
    assert len(requests) == 1 and "/historical/123/day?" in requests[0]


@pytest.mark.parametrize(
    "broker",
    [
        "compositedge",
        "fivepaisaxts",
        "ibulls",
        "iifl",
        "iiflcapital",
        "jainamxts",
        "rmoney",
        "wisdom",
    ],
)
def test_actual_feed_token_constructors(history, broker):
    ns = native_methods(broker, {"__init__"})
    seen = []

    def get_history(self, *args):
        seen.append((self.auth_token, self.feed_token, self.user_id))
        return pd.DataFrame([candle()])

    Handler = type("Handler", (), {"__init__": ns["__init__"], "get_history": get_history})
    assert invoke(history, Handler, evidence_mode=True)[0]
    assert seen == [("secret", "feed-secret", None)]
