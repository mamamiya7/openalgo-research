import importlib
import json
import sys
import types
from contextlib import contextmanager

import httpx
import pytest
from test_acquisition import candle, reference

from services.research_acquisition import acquire_history


@pytest.fixture
def native_chain(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "synthetic-app-key")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    tokens = types.ModuleType("database.token_db")
    tokens.get_token = lambda *_: "fake-symbol"
    tokens.get_br_symbol = lambda symbol, exchange: f"{exchange}:{symbol}-EQ"
    tokens.get_oa_symbol = lambda *_: "AAA"
    auth = types.ModuleType("database.auth_db")
    auth.get_auth_token_broker = lambda *_, **__: (None, None, None)
    monkeypatch.setitem(sys.modules, "database.token_db", tokens)
    monkeypatch.setitem(sys.modules, "database.auth_db", auth)
    fyers = importlib.import_module("broker.fyers.api.data")
    history = importlib.import_module("services.history_service")
    monkeypatch.setattr(history, "get_token", tokens.get_token)
    monkeypatch.setattr(fyers, "get_br_symbol", tokens.get_br_symbol)
    monkeypatch.setattr(fyers, "apply_rate_limit", lambda: None)
    monkeypatch.setattr(history, "_enforce_rate_limit", lambda: None)

    class Clock:
        now = 0.0

        def time(self):
            return self.now

        def sleep(self, duration):
            self.now += duration

    clock = Clock()
    monkeypatch.setattr(fyers.time, "sleep", clock.sleep)
    calls = []

    def install(replies):
        class Client:
            @contextmanager
            def stream(self, method, url, **kwargs):
                calls.append({"url": url, **kwargs})
                clock.now += 0.01
                status, payload = replies[min(len(calls) - 1, len(replies) - 1)]
                response = httpx.Response(status, json=payload, request=httpx.Request(method, url))
                try:
                    yield response
                finally:
                    response.close()

        monkeypatch.setattr(fyers, "get_httpx_client", lambda: Client())

    return fyers, history, clock, calls, install


def invoke(tmp_path, chain, **kwargs):
    _, history, clock, _, _ = chain
    return acquire_history(
        [{"symbol": s, "date": "2026-01-05", "row": i + 2} for i, s in enumerate(["AAA", "BBB"])],
        auth_token="NOT_A_REAL_SECRET",
        archive_dir=tmp_path,
        history=history.get_history,
        clock=clock.time,
        **kwargs,
    )


def test_raw_fyers_expiry_stops_next_symbol_and_never_persists_secret(tmp_path, native_chain):
    _, _, _, calls, install = native_chain
    install([(200, {"s": "error", "code": -16, "message": "Access token expired"})])
    snapshot = invoke(tmp_path, native_chain)
    assert len(calls) == 1
    assert snapshot["provenance"]["broker_receipts"][0]["outcome"] == "auth_expired"
    assert any("expired" in text for text in snapshot["coverage"]["warnings"])
    assert all("NOT_A_REAL_SECRET" not in path.read_text() for path in tmp_path.glob("*.json"))
    assert calls[0]["timeout"] <= 15


@pytest.mark.parametrize("status", [429, 503])
def test_exhausted_native_network_failures_never_become_empty_success(
    tmp_path, native_chain, status
):
    _, _, _, calls, install = native_chain
    install([(status, {"s": "error", "message": "unavailable"})])
    result = invoke(tmp_path, native_chain)
    assert len(calls) == 6  # Three attempts per symbol, no nested retry multiplication.
    assert all(r["outcome"] != "success" for r in result["provenance"]["broker_receipts"])
    outcomes = [
        json.loads(path.read_text())["history_evidence"]["chunks"][0]["attempts"]
        for path in tmp_path.glob("*.json")
    ]
    assert all(len(attempts) == 3 for attempts in outcomes)


def test_legitimate_empty_native_window_and_missing_only_retry(tmp_path, native_chain):
    _, _, _, calls, install = native_chain
    install([(200, {"s": "ok", "candles": []}), (401, {"s": "error", "code": -16})])
    checkpoints = []
    first = invoke(
        tmp_path,
        native_chain,
        on_checkpoint=lambda state: checkpoints.append(json.loads(json.dumps(state))),
    )
    assert first["provenance"]["broker_receipts"][0]["outcome"] == "success"
    assert len(first["acquisition_checkpoint"]["completed_windows"]) == 1
    before = len(calls)
    install([(200, {"s": "ok", "candles": []})])
    second = invoke(tmp_path, native_chain, prior=checkpoints[-1])
    assert len(calls) == before + 1
    assert "BBB" in calls[-1]["url"]
    assert len(second["acquisition_checkpoint"]["completed_windows"]) == 2
    assert second["provenance"]["acquisition_status"] == "complete"
    assert not second["provenance"]["pending_windows"]
    assert not any(
        "authentication expired" in warning for warning in second["coverage"]["warnings"]
    )


def test_conflicting_raw_duplicates_reach_research_quarantine(tmp_path, native_chain):
    _, history, clock, calls, install = native_chain
    own = candle()
    raw = [own[k] for k in ("timestamp", "open", "high", "low", "close", "volume")]
    conflict = list(raw)
    conflict[4] = 100
    install([(200, {"s": "ok", "candles": [raw, conflict]})])
    result = acquire_history(
        [{"symbol": "AAA", "date": "2026-01-05", "row": 2}],
        auth_token="fake",
        archive_dir=tmp_path,
        reference_snapshot=reference(),
        history=history.get_history,
        clock=clock.time,
    )
    assert len(calls) == 1
    assert not result["bars"]["AAA"]
    assert result["coverage"]["status"] == "blocked"
    receipt = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert receipt["history_evidence"]["chunks"][0]["observed_rows"] == 2


def test_native_retry_boundaries_honor_cancel_and_deadline(tmp_path, native_chain):
    _, _, clock, calls, install = native_chain
    install([(429, {"s": "error"})])
    with pytest.raises(InterruptedError):
        invoke(tmp_path, native_chain, cancelled=lambda: len(calls) >= 1)
    assert len(calls) == 1
    calls.clear()
    clock.now = 0
    result = invoke(tmp_path, native_chain, max_seconds=1)
    assert len(calls) == 1
    assert any(item["kind"] == "deadline" for item in result["provenance"]["quality_findings"])


def test_legacy_dataframe_contract_is_unchanged(native_chain, monkeypatch):
    fyers, history, _, _, _ = native_chain
    own = candle()
    raw = [own[k] for k in ("timestamp", "open", "high", "low", "close", "volume")]
    monkeypatch.setattr(fyers, "get_api_response", lambda *_, **__: {"s": "ok", "candles": [raw]})
    ok, response, status = history.get_history(
        "AAA", "NSE", "D", "2026-01-05", "2026-01-07", auth_token="fake", broker="fyers"
    )
    assert ok and status == 200 and response["data"][0]["close"] == 101
    assert "history_evidence" not in response


def test_checkpoint_fencing_exception_is_not_reclassified_as_broker_failure(tmp_path, native_chain):
    _, _, _, calls, install = native_chain
    install([(429, {"s": "error"})])

    class LeaseLost(Exception):
        pass

    def fenced(state):
        assert state["transport_attempts"][0]["outcome"] == "rate_limited"
        raise LeaseLost("worker ownership changed")

    with pytest.raises(LeaseLost):
        invoke(tmp_path, native_chain, on_checkpoint=fenced)
    assert len(calls) == 1


def test_actual_native_partial_chunks_keep_both_rows_and_failures(native_chain):
    _, history, clock, calls, install = native_chain
    own = candle()
    raw = [own[key] for key in ("timestamp", "open", "high", "low", "close", "volume")]
    install([(200, {"s": "ok", "candles": [raw]}), (503, {"s": "error"})])
    ok, response, status = history.get_history(
        "AAA",
        "NSE",
        "D",
        "2026-01-05",
        "2026-12-31",
        auth_token="fake",
        broker="fyers",
        evidence_mode=True,
        request_control={"clock": clock.time, "deadline": 100},
    )
    assert not ok and status == 502 and len(calls) == 4
    assert response["data"][0]["close"] == 101
    assert [chunk["outcome"] for chunk in response["history_evidence"]["chunks"]] == [
        "success",
        "download_failed",
    ]
