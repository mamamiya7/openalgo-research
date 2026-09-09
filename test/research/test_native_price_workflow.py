# ruff: noqa: F811 -- pytest fixtures imported for injection
"""HTTP → queued worker → real temporary Historify → immutable saved calculation."""

import copy
import io
import sys
import types
from datetime import datetime, timedelta

import pandas as pd
import pytest
from test_acquisition import candle, reference
from test_generic_history import history  # noqa: F401
from test_intraday_acquisition import row
from test_jobs import app, client  # noqa: F401

from services import research_acquisition as acquisition
from services import research_native_prices as intraday
from services import scanner_research_worker as worker
from services.research_historify import native_historify_read, native_historify_write


@pytest.mark.parametrize("cancel_at_batch_end", [False, True])
def test_minute_batches_continue_same_job_without_user_retry(
    app, client, monkeypatch, tmp_path, cancel_at_batch_end
):
    from database import historify_db
    from database.research_db import ResearchExperiment, ResearchJob

    target = prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    checked = reference()
    for field in ("bars", "raw_bars"):
        checked[field]["BBB"] = copy.deepcopy(checked[field]["AAA"])
    checked["provenance"]["symbol_identities"]["BBB"] = copy.deepcopy(
        checked["provenance"]["symbol_identities"]["AAA"]
    )
    monkeypatch.setattr(
        "research.evidence_import.public_snapshot", lambda *a, **kw: copy.deepcopy(checked)
    )
    all_rows = native_historify_read(
        "AAA",
        "2026-01-05T09:15:00+05:30",
        "2026-01-07T15:29:00+05:30",
        target,
        interval="1m",
    )
    native_historify_write("BBB", all_rows, target)
    gap = "2026-01-06T09:35:00+05:30"
    with historify_db.get_connection() as connection:
        connection.execute(
            "DELETE FROM market_data WHERE interval='1m' AND timestamp=?",
            [int(datetime.fromisoformat(gap).timestamp())],
        )
    calls = []

    def history(**request):
        calls.append(request["symbol"])
        return True, {"data": [row(gap)]}, 200

    monkeypatch.setattr(intraday, "native_history", history)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: {"broker": "controlled", "auth_token": "NOT_A_REAL_AUTH_SECRET"},
    )
    actual = intraday.acquire_native_prices
    store = app.extensions["research_store"]

    def one_request_pass(*args, **kwargs):
        result = actual(*args, **kwargs, max_requests=1)
        if cancel_at_batch_end and result["provenance"].get("batch_pending"):
            with store.sessions.begin() as db:
                db.get(ResearchJob, job_id).status = "cancelling"
        return result

    monkeypatch.setattr(intraday, "acquire_native_prices", one_request_pass)
    response = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (
                io.BytesIO(
                    b"Timestamp,Symbol\n2026-01-05T15:29:00+05:30,AAA\n"
                    b"2026-01-05T15:29:00+05:30,BBB\n"
                ),
                "signals.csv",
            ),
            "source": "broker",
        },
    )
    assert response.status_code == 201, response.json
    job_id = response.json["preparation_job"]["id"]
    worker.acquire(store, "batch-test")
    try:
        assert worker.run_one(store, "batch-test")
        with store.sessions() as db:
            job = db.get(ResearchJob, job_id)
            assert job.status == ("cancelled" if cancel_at_batch_end else "queued")
            assert job.error is None and job.result_artifact is None
            assert db.get(ResearchExperiment, job_id).checkpoint
        assert calls == ["AAA"]
        if cancel_at_batch_end:
            assert not worker.run_one(store, "batch-test")
        else:
            assert worker.run_one(store, "batch-test")
            complete = client.get(f"/scanner-research/api/jobs/{job_id}").json
            assert complete["status"] == "completed", complete
            assert complete["result"]["prepared_source"]["provenance"]["interval"] == "1m"
            assert calls == ["AAA", "BBB"]
    finally:
        worker.release(store, "batch-test")


def prepare_archive(monkeypatch, tmp_path, days):
    target = tmp_path / "native-historify.duckdb"
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", str(target))
    monkeypatch.setenv("RESEARCH_HISTORIFY_DATABASE_PATH", str(target))
    monkeypatch.setenv("RESEARCH_PUBLIC_EVIDENCE_DIR", "controlled-reference-no-network")
    from database import historify_db

    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(target))
    monkeypatch.setattr(
        "research.evidence_import.public_snapshot", lambda *a, **kw: copy.deepcopy(reference())
    )

    def calendar(*args, **kwargs):
        return {
            "sessions": reference()["sessions"],
            "session_hours": {
                day: {"open": "09:15", "close": "15:30"} for day in reference()["sessions"]
            },
            "bars": {},
            "provenance": {
                "calendar_basis": "openalgo-market-calendar-v1",
                "calendar_source": "controlled-native-calendar",
                "calendar_admission": "nse-calendar-admission-2025-2026-v1",
            },
        }

    monkeypatch.setattr("services.research_native_calendar.native_calendar_snapshot", calendar)
    acquisition.native_historify_ingest("AAA", [candle(day) for day in days], target)
    minute_rows = [
        row((datetime.fromisoformat(day + "T09:15:00+05:30") + timedelta(minutes=n)).isoformat())
        for day in days
        for n in range(375)
    ]
    native_historify_write("AAA", minute_rows, target)
    return target


def upload(client):
    response = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (
                io.BytesIO(b"Timestamp,Symbol\n2026-01-05T15:29:00+05:30,AAA\n"),
                "scanner.csv",
            ),
            "source": "broker",
        },
    )
    assert response.status_code == 201, response.json
    assert response.json["preparation_job"]["status"] == "queued"
    return response.json["preparation_job"]["id"]


def run_and_export(client, store, preparation_id, monkeypatch):
    assert worker.run_one(store, "native-price-test")
    preparation = client.get(f"/scanner-research/api/jobs/{preparation_id}").json
    assert preparation["status"] == "completed", preparation
    source = preparation["result"]["prepared_source"]
    assert source["coverage"]["status"] != "blocked"
    assert source["provenance"]["acquisition_mode"] == "historify-native-prices-v1"
    assert source["provenance"]["interval"] == "1m"

    def revoked(*args, **kwargs):
        pytest.fail("Saved source evaluation/reopen/export must never resolve credentials")

    monkeypatch.setattr("services.research_sources.resolve_broker_session", revoked)
    monkeypatch.setattr(intraday, "native_history", revoked)
    monkeypatch.setattr(acquisition, "native_historify_read", revoked)
    response = client.post(
        "/scanner-research/api/jobs",
        json={"source_id": source["id"], "config": {"hold_sessions": 1}},
    )
    assert response.status_code == 202, response.json
    job_id = response.json["id"]
    assert worker.run_one(store, "native-price-test")
    saved = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert saved["status"] == "completed", saved
    assert saved["result"]["ledger"][0]["entry_date"] == "2026-01-06"
    assert saved["result"]["equity_curve"]
    first = client.get(f"/scanner-research/api/jobs/{job_id}/export")
    assert first.status_code == 200
    assert b"scanner-minute-causal-v1" in first.data
    # A fresh browser session still has the OpenAlgo user, but no broker session.
    with client.session_transaction() as session:
        session.clear()
        session["user"] = "research-test"
    assert client.get(f"/scanner-research/api/jobs/{job_id}").json["status"] == "completed"
    second = client.get(f"/scanner-research/api/jobs/{job_id}/export")
    assert second.data == first.data
    assert b"NOT_A_REAL_AUTH_SECRET" not in first.data
    assert b"NOT_A_REAL_FEED_SECRET" not in first.data
    return source, first.json


def test_http_full_native_archive_needs_no_broker_credentials(app, client, monkeypatch, tmp_path):
    target = prepare_archive(monkeypatch, tmp_path, reference()["sessions"])

    def no_broker(*args, **kwargs):
        pytest.fail("Fully covered native archive must not resolve auth or call a broker")

    monkeypatch.setattr("services.research_sources.resolve_broker_session", no_broker)
    monkeypatch.setattr(intraday, "native_history", no_broker)
    preparation_id = upload(client)
    store = app.extensions["research_store"]
    worker.acquire(store, "native-price-test")
    try:
        source, bundle = run_and_export(client, store, preparation_id, monkeypatch)
    finally:
        worker.release(store, "native-price-test")
    assert source["provenance"]["download_brokers"] == []
    assert source["provenance"]["cache_provider"] == "unknown"
    receipts = bundle["inputs"]["acquisition_receipts"]
    assert receipts and all(
        item["receipt"]["provider"] == "OpenAlgo Historify" for item in receipts
    )
    assert len(native_historify_read("AAA", "2026-01-05", "2026-01-07", target)) == 3


@pytest.mark.parametrize("broker", ["zerodha", "dhan", "fyers"])
def test_http_downloads_only_native_archive_hole_and_persists_it(
    app, client, history, monkeypatch, tmp_path, broker
):
    target = prepare_archive(monkeypatch, tmp_path, ["2026-01-05", "2026-01-07"])
    # Native daily candles exist for the missing date, but cannot substitute for minutes.
    acquisition.native_historify_ingest("AAA", [candle("2026-01-06")], target)
    first = datetime.fromisoformat("2026-01-06T09:15:00+05:30")
    native_historify_write(
        "AAA",
        [row((first + timedelta(minutes=n)).isoformat()) for n in range(375) if n != 20],
        target,
    )
    resolutions, calls, dispatches = [], [], []

    def credentials(owner):
        resolutions.append(owner)
        return {
            "broker": broker,
            "auth_token": "NOT_A_REAL_AUTH_SECRET",
            "feed_token": "NOT_A_REAL_FEED_SECRET",
        }

    class Handler:
        timeframe_map = {"1m": "minute", "D": "day"}

        def __init__(self, auth, feed=None):
            assert (auth, feed) == ("NOT_A_REAL_AUTH_SECRET", "NOT_A_REAL_FEED_SECRET")

        def get_history_evidence(self, *args, **kwargs):
            pytest.fail("Daily-only rich evidence must fall back to native minute history")

        def get_history(self, symbol, exchange, interval, first, last):
            calls.append((symbol, exchange, interval, first, last))
            return pd.DataFrame(
                [
                    row(
                        (
                            datetime.fromisoformat("2026-01-06T09:15:00+05:30")
                            + timedelta(minutes=n)
                        ).isoformat()
                    )
                    for n in range(375)
                ]
            )

    def importer(name):
        dispatches.append(name)
        return types.SimpleNamespace(BrokerData=Handler)

    monkeypatch.setattr("services.research_sources.resolve_broker_session", credentials)
    monkeypatch.setattr(history, "import_broker_module", importer)
    monkeypatch.setattr(
        sys.modules["database.token_db"],
        "get_br_symbol",
        lambda symbol, exchange: symbol,
        raising=False,
    )
    monkeypatch.setattr(
        intraday,
        "native_history",
        lambda **request: history.get_history(**request),
    )
    preparation_id = upload(client)
    assert resolutions == calls == []  # HTTP admits work; only the worker acquires prices.
    store = app.extensions["research_store"]
    worker.acquire(store, "native-price-test")
    try:
        source, bundle = run_and_export(client, store, preparation_id, monkeypatch)
    finally:
        worker.release(store, "native-price-test")
    assert resolutions == ["research-test"]
    assert dispatches == [broker]
    assert calls == [("AAA", "NSE", "1m", "2026-01-06", "2026-01-06")]
    assert source["provenance"]["download_brokers"] == [broker]
    rows = native_historify_read(
        "AAA", "2026-01-06T09:15:00+05:30", "2026-01-06T15:29:00+05:30", target, interval="1m"
    )
    assert len(rows) == 375 and all(item["close"] == 100.5 for item in rows)
    receipt = next(
        item["receipt"]
        for item in bundle["inputs"]["acquisition_receipts"]
        if item["receipt"].get("broker") == broker
    )
    assert receipt["broker"] == broker
    assert "history_evidence" not in receipt
