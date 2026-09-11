# ruff: noqa: F811
"""Saved analysis admission, ownership, cancellation and immutable source evidence."""

import copy
import json
import time

import pytest
from test_jobs import app, client, source  # noqa: F401
from test_study_analysis import experiment

from database.research_db import ResearchExperiment, ResearchJob
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def completed_parent(app, client):
    store = app.extensions["research_store"]
    source_id = source(client)
    submitted = service.submit(store, "research-test", source_id, {})
    curve = [
        {
            "date": f"2026-01-{day:02d}",
            "equity": 10000 + day * 10,
            "cash": 9000,
            "drawdown_pct": 0,
            "open_positions": 1,
        }
        for day in range(5, 15)
    ]
    result = {
        "config": {"initial_capital": 10000},
        "summary": {
            "initial_capital": 10000,
            "final_equity": 10140,
            "net_return_pct": 1.4,
            "max_drawdown_pct": 0,
            "closed_trades": 1,
            "net_pnl": 140,
        },
        "equity_curve": curve,
        "ledger": [
            {
                "status": "closed",
                "quantity": 10,
                "pnl": 100,
                "entry_date": "2026-01-05",
                "exit_date": "2026-01-10",
                "entry_price": 100,
                "exit_price": 110,
            }
        ],
        "execution": {"engine": "vectorbt", "engine_version": "0.28.5", "interval": "D"},
        "strategies": [],
        "per_strategy": [],
    }
    old_study = {
        **experiment(),
        "rows": [{"config_id": "winner", "trial_number": 0, "summary": result["summary"]}],
        "selected_reports": {"winner": copy.deepcopy(result)},
    }
    result["experiment"] = old_study
    artifact = service.save_artifact(store, {"result": result, "kind": "portfolio_optimize"})
    with store.sessions.begin() as db:
        job = db.get(ResearchJob, submitted["id"])
        job.status, job.result_artifact, job.updated_at = "completed", artifact, time.time()
        db.get(ResearchExperiment, job.id).kind = "portfolio_optimize"
    return submitted["id"], artifact


def test_old_analysis_is_saved_reopened_exported_without_changing_original(
    app, client, monkeypatch
):
    parent_id, artifact = completed_parent(app, client)
    store = app.extensions["research_store"]
    original = service.read_artifact(store, artifact)
    base = f"/scanner-research/api/portfolio/jobs/{parent_id}/analysis"
    assert client.get(base).json["status"] == "missing"
    response = client.post(base, json={})
    assert response.status_code == 202, response.json
    analysis_id = response.json["analysis_job_id"]
    assert client.post(base, json={}).json["analysis_job_id"] == analysis_id
    monkeypatch.setattr(
        "services.research_portfolio.run",
        lambda *a, **k: pytest.fail("Analysis reran portfolio/prices"),
    )
    worker.acquire(store, "analysis-test")
    try:
        worker.run_one(store, "analysis-test")
    finally:
        worker.release(store, "analysis-test")
    response = client.get(base)
    assert response.json["status"] == "complete", response.json
    result = response.json["job"]["result"]
    assert result["analysis"]["catalog"]
    assert len(result["experiment"]["study_analysis"]["charts"]) == 12
    assert result["experiment"]["rows"][0]["analysis"]["metrics"]
    assert service.read_artifact(store, artifact) == original
    assert service.get_job(store, "research-test", parent_id).result_artifact == artifact
    reopened = client.get(f"/scanner-research/api/jobs/{parent_id}").json["result"]
    assert reopened == result
    assert client.get(base + "/export").json["parent_result_artifact"] == artifact
    assert client.post(base, json={}).json["status"] == "complete"
    assert (
        "analysis"
        not in client.get(f"/scanner-research/api/jobs/{parent_id}/export").json["result"]
    )
    json.dumps(result, allow_nan=False)


def test_analysis_ownership_and_inputs(app, client):
    parent_id, _ = completed_parent(app, client)
    base = f"/scanner-research/api/portfolio/jobs/{parent_id}/analysis"
    assert client.post(base, json={"parameters": ["not-recorded"]}).status_code == 400
    with client.session_transaction() as session:
        session["user"] = "different-owner"
    assert client.get(base).status_code == 404
    assert client.post(base, json={}).status_code == 404
    assert client.get(base + "/export").status_code == 404
    assert app.test_client().get(base).status_code == 401


def test_validation_symbol_uses_its_own_frozen_period(app, client):
    parent_id, artifact = completed_parent(app, client)
    store = app.extensions["research_store"]
    bundle = service.read_artifact(store, artifact)
    result = bundle["result"]
    result["ledger"][0]["symbol"] = "AAA"
    later = copy.deepcopy({k: v for k, v in result.items() if k != "experiment"})
    later["equity_curve"] = [
        {**p, "date": p["date"].replace("2026-01", "2026-02")} for p in later["equity_curve"]
    ]
    later["ledger"] = [
        {
            **result["ledger"][0],
            "symbol": symbol,
            "entry_date": "2026-02-05",
            "exit_date": "2026-02-10",
        }
        for symbol in ("BBB", "CCC")
    ]
    result["validation"] = {"result": later}
    dates = [p["date"] for report in (result, later) for p in report["equity_curve"]]
    snapshot = {
        "sessions": dates,
        "provenance": {"interval": "D"},
        "bars": {
            symbol: {day: {"open": 100, "high": 115, "low": 95, "close": 110} for day in dates}
            for symbol in ("AAA", "BBB", "CCC")
        },
    }
    bundle["inputs_artifact"] = service.save_artifact(store, {"snapshot": snapshot})
    artifact = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, parent_id).result_artifact = artifact
    base = f"/scanner-research/api/portfolio/jobs/{parent_id}/analysis"
    assert client.post(base, json={"symbol": "CCC"}).status_code == 400
    response = client.post(base, json={"symbol": "CCC", "period": "validation"})
    assert response.status_code == 202, response.json
    worker.acquire(store, "analysis-validation")
    try:
        worker.run_one(store, "analysis-validation")
    finally:
        worker.release(store, "analysis-validation")
    response = client.get(base).json
    assert response["status"] == "complete", response
    result = response["job"]["result"]
    assert result["analysis"]["price_symbol"] == "AAA"
    assert result["validation"]["result"]["analysis"]["price_symbol"] == "CCC"
    for report, prefix in ((result, "2026-01"), (result["validation"]["result"], "2026-02")):
        chart = next(c for c in report["analysis"]["charts"] if c["id"] == "bars-with-fills")
        assert all(day.startswith(prefix) for day in chart["figure"]["data"][0]["x"])


def test_cancelled_analysis_can_be_requested_again(app, client):
    parent_id, artifact = completed_parent(app, client)
    base = f"/scanner-research/api/portfolio/jobs/{parent_id}/analysis"
    first = client.post(base, json={}).json["analysis_job_id"]
    client.post(f"/scanner-research/api/jobs/{first}/cancel")
    assert client.get(base).json["status"] == "failed"
    second = client.post(base, json={}).json["analysis_job_id"]
    assert first != second
    assert (
        service.get_job(
            app.extensions["research_store"], "research-test", parent_id
        ).result_artifact
        == artifact
    )


def test_v1_report_upgrade_reconciles_full_winner_but_preserves_compact_legacy_trials(app, client):
    from research.analytics import build_analysis

    parent_id, old_artifact = completed_parent(app, client)
    store = app.extensions["research_store"]
    bundle = service.read_artifact(store, old_artifact)
    result = bundle["result"]
    legacy = build_analysis(result)
    legacy["version"] = "research-analysis-v1"
    legacy["metrics"]["account_sharpe_ratio"] = -999
    legacy.pop("report_depth", None)
    result["analysis"] = copy.deepcopy(legacy)
    result["experiment"]["selected_reports"]["winner"]["analysis"] = copy.deepcopy(legacy)
    compact = {key: copy.deepcopy(legacy[key]) for key in ("version", "metrics", "unavailable")}
    result["experiment"]["rows"][0]["analysis"] = copy.deepcopy(compact)
    result["experiment"]["rows"].append(
        {"config_id": "old-compact-only", "trial_number": 999, "analysis": compact}
    )
    artifact = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, parent_id).result_artifact = artifact
    export = client.get(f"/scanner-research/api/jobs/{parent_id}/export").data
    base = f"/scanner-research/api/portfolio/jobs/{parent_id}/analysis"
    response = client.post(base, json={})
    assert response.status_code == 202, response.json
    worker.acquire(store, "upgrade-analysis-test")
    try:
        worker.run_one(store, "upgrade-analysis-test")
    finally:
        worker.release(store, "upgrade-analysis-test")
    response = client.get(base).json
    assert response["status"] == "complete", response
    updated = response["job"]["result"]
    winner, old_trial = updated["experiment"]["rows"]
    assert updated["analysis"]["version"] == winner["analysis"]["version"] == "research-analysis-v2"
    assert (
        updated["analysis"]["metrics"]["account_sharpe_ratio"]
        == winner["analysis"]["metrics"]["account_sharpe_ratio"]
    )
    assert updated["analysis"]["metrics"]["account_sharpe_ratio"] != -999
    assert updated["analysis"]["report_depth"]["version"] == "research-report-depth-v1"
    assert old_trial["analysis"]["version"] == "research-analysis-v1"
    assert client.get(f"/scanner-research/api/jobs/{parent_id}/export").data == export
    assert service.read_artifact(store, artifact) == bundle
