# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Joint portfolio HTTP -> native Historify -> real engines -> saved evidence."""

import copy
import io
import json

import pytest
from test_acquisition import candle, reference
from test_jobs import app, client  # noqa: F401
from test_native_price_workflow import prepare_archive

from services import scanner_research_service as service
from services import scanner_research_worker as worker


def uploaded(client, raw=b"Date,Symbol\n2026-01-05,AAA\n"):
    response = client.post(
        "/scanner-research/api/portfolio/inputs", data={"file": (io.BytesIO(raw), "strategy.csv")}
    )
    assert response.status_code == 201, response.json
    assert "preparation_job" not in response.json
    return response.json["id"]


def portfolio(client, *, optimize=False, timed=False):
    source = uploaded(client)
    other = (
        uploaded(client, b"Timestamp,Symbol\n2026-01-05T15:29:00+05:30,AAA\n") if timed else source
    )
    result = {
        "name": "Two strategies",
        "capital": 20000,
        "strategies": [
            {
                "id": "first",
                "name": "First",
                "source_id": source,
                "allocation_pct": 50,
                "config": {
                    "order_size_pct": 100,
                    "hold_sessions": 1,
                    "cost_bps": 0,
                    "target_pct": 10,
                },
            },
            {
                "id": "second",
                "name": "Second",
                "source_id": other,
                "allocation_pct": 50,
                "config": {
                    "order_size_pct": 100,
                    "hold_sessions": 1,
                    "cost_bps": 0,
                    "target_pct": 1,
                },
            },
        ],
    }
    if timed:
        result["strategies"][1]["config"].update(trade_horizon="intraday", hold_minutes=30)
    if optimize:
        result["strategies"][1]["search"] = {"target_pct": {"min": 1, "max": 3, "step": 1}}
        result["optimization"] = {"sampler": "tpe", "trials": 3, "objective": "balanced", "seed": 0}
    return result


@pytest.mark.parametrize("optimize", [False, True])
def test_one_job_prepares_daily_prices_runs_and_reopens_exactly(
    app, client, monkeypatch, tmp_path, optimize
):
    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("Complete Historify coverage must need no broker login"),
    )
    request = portfolio(client, optimize=optimize)
    preview = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert preview.status_code == 200, preview.json
    assert preview.json["interval"] == "D"
    assert preview.json["receipt"]["signal_count"] == 2
    assert client.get("/scanner-research/api/jobs").json == []
    response = client.post(
        "/scanner-research/api/portfolio/jobs",
        json={"portfolio": request, "request_id": "portfolio-run-1"},
    )
    assert response.status_code == 202, response.json
    job_id = response.json["id"]
    repeated = client.post(
        "/scanner-research/api/portfolio/jobs",
        json={"portfolio": request, "request_id": "portfolio-run-1"},
    )
    assert repeated.json["id"] == job_id
    store = app.extensions["research_store"]
    worker.acquire(store, "portfolio-test")
    try:
        assert worker.run_one(store, "portfolio-test")
    finally:
        worker.release(store, "portfolio-test")
    completed = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert completed["status"] == "completed", completed
    result = completed["result"]
    assert result["execution"]["engine"] == "vectorbt"
    assert {row["strategy_id"] for row in result["ledger"]} == {"first", "second"}
    assert len(result["per_strategy"]) == 2
    assert result["source"]["interval"] == "D"
    assert result["summary"]["initial_capital"] == 20000
    exported = client.get(f"/scanner-research/api/jobs/{job_id}/export")
    bundle = json.loads(exported.data)
    assert (
        bundle["inputs"]["snapshot"]["provenance"]["native_price_policy"]
        == "openalgo-native-history-v1"
    )
    assert bundle["inputs"]["strategies"][0]["original_csv_sha256"]
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == exported.data
    if optimize:
        assert result["experiment"]["optimizer"]["sampler"] == "TPESampler"
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda *a, **kw: pytest.fail("An exact rerun must never replace saved prices"),
    )
    trial = result.get("experiment", {}).get("rows", [None])[-1]
    rerun = client.post(
        f"/scanner-research/api/portfolio/jobs/{job_id}/rerun",
        json={"trial_id": trial["config_id"] if trial else None, "request_id": "exact-replay-1"},
    )
    assert rerun.status_code == 202, rerun.json
    worker.acquire(store, "portfolio-replay")
    try:
        assert worker.run_one(store, "portfolio-replay")
    finally:
        worker.release(store, "portfolio-replay")
    replayed = client.get(f"/scanner-research/api/jobs/{rerun.json['id']}").json
    assert replayed["status"] == "completed", replayed
    expected = trial["summary"] if trial else result["summary"]
    assert replayed["result"]["summary"] == expected
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == exported.data


def test_mixed_strategies_use_one_native_minute_plan(app, client, monkeypatch, tmp_path):
    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("Stored minute coverage must be reused"),
    )
    request = portfolio(client, timed=True)
    preview = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert preview.status_code == 200, preview.json
    assert preview.json["interval"] == "1m"
    response = client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request})
    assert response.status_code == 202, response.json
    store = app.extensions["research_store"]
    worker.acquire(store, "portfolio-minute")
    try:
        assert worker.run_one(store, "portfolio-minute")
    finally:
        worker.release(store, "portfolio-minute")
    completed = client.get(f"/scanner-research/api/jobs/{response.json['id']}").json
    assert completed["status"] == "completed", completed
    assert completed["result"]["source"]["interval"] == "1m"
    assert len(completed["result"]["per_strategy"]) == 2


@pytest.mark.parametrize("change", ["overallocated", "same-id", "bad-config", "unknown-engine"])
def test_invalid_portfolio_is_rejected_before_any_job_or_download(app, client, change):
    request = portfolio(client)
    if change == "overallocated":
        request["strategies"][0]["allocation_pct"] = 80
    elif change == "same-id":
        request["strategies"][1]["id"] = "first"
    elif change == "bad-config":
        request["strategies"][0]["config"] = "bad"
    else:
        request["engine"] = "not-an-engine"
    response = client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request})
    assert response.status_code == 400, response.json
    assert client.get("/scanner-research/api/jobs").json == []


def test_portfolio_sources_and_jobs_are_owner_scoped(client):
    request = portfolio(client)
    with client.session_transaction() as session:
        session["user"] = "someone-else"
    response = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert response.status_code == 404
    with client.session_transaction() as session:
        session.clear()
    assert client.post("/scanner-research/api/portfolio/preflight", json=request).status_code == 401


def test_upload_does_not_schedule_premature_data_download(client):
    uploaded(client)
    assert client.get("/scanner-research/api/jobs").json == []


def test_saved_input_picker_includes_pending_csvs_but_not_combined_portfolios(client):
    request = portfolio(client)
    source_id = request["strategies"][0]["source_id"]
    result = client.get("/scanner-research/api/sources?portfolio_inputs=1&limit=1&offset=0")
    assert result.status_code == 200, result.json
    assert result.json["items"][0]["id"] == source_id
    assert result.json["next_offset"] is None
    submitted = client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request})
    assert submitted.status_code == 202, submitted.json
    assert len(client.get("/scanner-research/api/sources?portfolio_inputs=1").json["items"]) == 1
    with client.session_transaction() as session:
        session["user"] = "another-owner"
    assert client.get("/scanner-research/api/sources?portfolio_inputs=1").json["items"] == []
