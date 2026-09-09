"""Ordinary Fyers history must distinguish confirmed emptiness from failure.

Execute the exact native methods while replacing transport, symbol lookup and
sleep. No broker session, database or network is used.
"""

import ast
import logging
import types
import urllib.parse
from pathlib import Path

import pandas as pd
import pytest

CANDLE = [1735776000, 100, 102, 99, 101, 25]
NO_DATA = {"s": "no_data", "code": 200, "candles": [], "message": "", "nextTime": 0}


@pytest.fixture
def native():
    path = Path(__file__).resolve().parents[2] / "broker/fyers/api/data.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BrokerData"
    )
    methods = [
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name in {"__init__", "get_history"}
    ]
    calls, sleeps = [], []
    namespace = {
        "pd": pd,
        "logger": logging.getLogger("native-fyers-history-test"),
        "urllib": urllib,
        "time": types.SimpleNamespace(sleep=sleeps.append),
        "get_br_symbol": lambda symbol, exchange: f"{exchange}:{symbol}-EQ",
    }
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    handler = type(
        "NativeFyers", (), {name: namespace[name] for name in ("__init__", "get_history")}
    )("synthetic-token")

    def invoke(replies, *, last="2025-01-03"):
        def response(endpoint, auth):
            assert auth == "synthetic-token"
            item = replies[min(len(calls), len(replies) - 1)]
            calls.append(endpoint)
            if isinstance(item, Exception):
                raise item
            return item

        namespace["get_api_response"] = response
        return handler.get_history("AAA", "NSE", "D", "2025-01-02", last)

    return invoke, calls, sleeps


@pytest.mark.parametrize("reply", [NO_DATA, {"s": "ok", "code": 200, "candles": []}])
def test_confirmed_empty_native_window_does_not_retry_or_sleep(native, reply):
    invoke, calls, sleeps = native
    result = invoke([reply])
    assert result.empty
    assert {"timestamp", "open", "high", "low", "close", "volume"} <= set(result.columns)
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "reply",
    [
        {"s": "error", "code": -16, "message": "Access token expired"},
        {"s": "error", "code": -17, "message": "Invalid token"},
        {"s": "error", "code": 200, "message": ""},
        {"s": "unknown", "code": 200, "candles": []},
        RuntimeError("synthetic transport failure"),
    ],
)
def test_exhausted_failure_never_returns_an_empty_dataframe(native, reply):
    invoke, calls, sleeps = native
    with pytest.raises(Exception, match="Error fetching historical data"):
        invoke([reply])
    assert len(calls) == 4
    assert sleeps == [2, 4, 6]


@pytest.mark.parametrize(
    "reply",
    [
        {"s": "no_data", "code": -16, "candles": []},
        {"s": "no_data", "code": 200, "candles": [CANDLE]},
        {"s": "no_data", "code": 200},
        {"s": "no_data", "code": 200, "candles": None},
        {"s": "ok", "code": 200},
        {"s": "ok", "code": 200, "candles": None},
        {"s": "ok", "code": 200, "candles": {}},
    ],
)
def test_contradictory_or_malformed_empty_response_is_a_failure(native, reply):
    invoke, calls, sleeps = native
    with pytest.raises(Exception, match="Error fetching historical data"):
        invoke([reply])
    assert len(calls) == 4
    assert sleeps == [2, 4, 6]


def test_successful_first_chunk_does_not_hide_failed_second_chunk(native):
    invoke, calls, sleeps = native
    with pytest.raises(Exception, match="Error fetching historical data"):
        invoke([{"s": "ok", "candles": [CANDLE]}, {"s": "error", "code": 503}], last="2025-11-03")
    assert len(calls) == 5
    assert len(set(calls)) == 2
    assert len(set(calls[1:])) == 1
    assert sleeps == [2, 4, 6]


def test_confirmed_empty_second_chunk_preserves_valid_first_chunk(native):
    invoke, calls, sleeps = native
    result = invoke([{"s": "ok", "candles": [CANDLE]}, NO_DATA], last="2025-11-03")
    assert result.to_dict(orient="records") == [
        {
            "timestamp": CANDLE[0],
            "open": 100,
            "high": 102,
            "low": 99,
            "close": 101,
            "volume": 25,
            "oi": 0,
        }
    ]
    assert len(calls) == 2
    assert sleeps == []
