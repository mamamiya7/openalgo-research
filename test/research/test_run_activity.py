# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Truthful persisted stages, bounded traces and continuation queue ordering."""

import copy
import json

import pytest
from test_acquisition import reference
from test_jobs import app, client  # noqa: F401
from test_native_price_workflow import prepare_archive
from test_optuna_portfolio import engine, search_request  # noqa: F401
from test_portfolio_workflow import portfolio, uploaded

from research.connectors.optuna_portfolio import run_search
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_activity import initial_activity, merge_activity
from services.research_sources import AcquisitionBatchPending


def test_trial_activity_is_real_and_does_not_change_results_or_checkpoints(search_request):
    search_request["specification"].update(sampler="tpe", trials=12)
    events, checkpoints, baseline = [], [], []
    expected = run_search(
        **search_request, evaluate=engine, checkpoint=lambda *x: baseline.append(x)
    )
    result = run_search(
        **search_request,
        evaluate=engine,
        checkpoint=lambda *x: checkpoints.append(x),
        activity=events.append,
    )
    assert result == expected and checkpoints == baseline
    final = events[-1]["trials"]
    trials = result["experiment"]["trials"]
    assert final["completed"] == 12
    assert final["evaluated"] == len(result["experiment"]["rows"])
    assert final["reused"] == sum(t["reused"] for t in trials)
    assert final["rejected"] == sum(t["state"] == "pruned" for t in trials)
    assert final["evaluated"] + final["reused"] + final["rejected"] == final["completed"]
    assert final["history"] == [
        {"trial": t["number"] + 1, "score": t["value"]} for t in trials if t["state"] == "complete"
    ]
    assert any(e["stage"] == "initializing" and e["trials"]["active_trial"] for e in events)
    restored = []
    again = run_search(
        **search_request,
        evaluate=lambda *a, **k: pytest.fail("No repeated calculations on completed resume"),
        saved=checkpoints[-1][0],
        activity=restored.append,
    )
    assert again == result and restored[-1]["trials"] == final


def test_failed_trial_is_terminal_but_never_a_scored_calculation(search_request):
    events = []

    def fail(*args, **kwargs):
        raise RuntimeError("Controlled engine failure")

    with pytest.raises(RuntimeError, match="Controlled engine failure"):
        run_search(**search_request, evaluate=fail, activity=events.append)
    trials = events[-1]["trials"]
    assert trials["completed"] == trials["failed"] == 1
    assert trials["evaluated"] == trials["reused"] == trials["rejected"] == 0
    assert trials["history"] == []


def test_trace_is_bounded_and_only_uses_actual_trial_scores(search_request):
    search_request["specification"].update(sampler="tpe", trials=130)
    events = []
    result = run_search(**search_request, evaluate=engine, activity=events.append)
    actual = {
        t["number"] + 1: t["value"]
        for t in result["experiment"]["trials"]
        if t["state"] == "complete"
    }
    assert len(events[-1]["trials"]["history"]) <= 100
    assert events[-1]["trials"]["history"][-1]["trial"] == 130
    for event in events:
        assert all(actual[p["trial"]] == p["score"] for p in event["trials"]["history"])


def test_activity_merge_is_bounded_and_preserves_other_stages():
    activity = initial_activity(
        {"signal_count": 3, "symbol_count": 2},
        {"strategies": [{"source_id": "one"}, {"source_id": "one"}]},
        1,
    )
    assert activity["inputs"] == {"files": 1, "signals": 3, "symbols": 2}
    updated = merge_activity(
        activity,
        {
            "stage": "download",
            "prices": {"required_candles": 30, "cached_candles": 10, "current_symbol": "AAA"},
            "credentials": "do not expose",
        },
        2,
    )
    assert "prices" not in activity and "credentials" not in updated
    finished = merge_activity(
        updated, {"stage": "optimizing", "trials": {"completed": 1, "active_trial": 2}}, 3
    )
    assert finished["prices"] == updated["prices"] and finished["inputs"] == activity["inputs"]
    with pytest.raises(ValueError):
        merge_activity(updated, {"trials": {"history": [{"trial": 1, "score": 0}] * 101}}, 3)
    with pytest.raises(ValueError):
        merge_activity(updated, {"prices": {"downloaded_candles": -1}}, 3)


def test_worker_persists_counts_and_publishes_complete_only_with_result(
    app, client, monkeypatch, tmp_path
):
    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("No broker for complete archive"),
    )
    monkeypatch.setattr(
        "research.connectors.vectorbt_portfolio.evaluate",
        lambda strategies, snapshot, capital, progress=None: engine(
            strategies, {**snapshot, "id": "controlled"}, capital, progress
        ),
    )
    request = portfolio(client, optimize=True)
    response = client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request})
    job_id = response.json["id"]
    store = app.extensions["research_store"]
    assert response.json["activity"]["inputs"] == {"files": 1, "signals": 2, "symbols": 1}
    observations = []
    original = research_portfolio.run

    def observe(*args, activity=None, **kwargs):
        def emit(event):
            activity(event)
            observations.append(client.get(f"/scanner-research/api/jobs/{job_id}").json)

        return original(*args, activity=emit, **kwargs)

    monkeypatch.setattr(research_portfolio, "run", observe)
    saved = service.save_artifact

    def save(store, value):
        if isinstance(value, dict) and "job_id" in value and "result" in value:
            pending = client.get(f"/scanner-research/api/jobs/{job_id}").json
            assert pending["activity"]["stage"] == "saving" and pending["status"] == "running"
        return saved(store, value)

    monkeypatch.setattr(worker, "save_artifact", save)
    worker.acquire(store, "activity")
    try:
        assert worker.run_one(store, "activity")
    finally:
        worker.release(store, "activity")
    result = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert result["status"] == "completed", result
    assert result["activity"]["stage"] == "complete"
    assert result["activity"]["trials"]["completed"] == 3
    assert result["activity"]["prices"]["interval"] == "D"
    assert result["activity"]["prices"]["downloaded_candles"] == 0
    # Existing jobs already carry signal dates separately from the later entry/
    # holding candles. The progress UI can use them without a schema migration.
    assert response.json["source_summary"]["date_from"] == "2026-01-05"
    assert response.json["source_summary"]["date_to"] == "2026-01-05"
    cache_start = next(o for o in observations if o["activity"]["stage"] == "cache")
    assert cache_start["activity"]["prices"]["required_candles"] == 2
    assert cache_start["activity"]["prices"]["cache_complete"] is False
    assert cache_start["source_summary"] == response.json["source_summary"]
    assert all(o["activity"]["stage"] != "complete" for o in observations)
    exported = client.get(f"/scanner-research/api/jobs/{job_id}/export").data
    assert client.get(f"/scanner-research/api/jobs/{job_id}").json["activity"] == result["activity"]
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == exported


def test_queued_progress_dates_describe_selected_signals_not_entire_source(client):
    request = portfolio(client)
    source = uploaded(client, b"Date,Symbol\n2026-01-05,AAA\n2026-01-06,AAA\n2026-01-07,BBB\n")
    for strategy in request["strategies"]:
        strategy["source_id"] = source
    request.update(date_from="2026-01-06", date_to="2026-01-07")
    preview = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert preview.status_code == 200, preview.json
    response = client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request})
    assert response.status_code == 202, response.json
    job = response.json
    assert job["source_summary"]["date_from"] == "2026-01-06"
    assert job["source_summary"]["date_to"] == "2026-01-07"
    assert job["source_summary"] == preview.json["receipt"]
    assert job["activity"]["inputs"]["signals"] == 4  # two included signals per strategy
    assert "prices" not in job["activity"]  # no denominator before the real plan exists
    assert (
        client.get(f"/scanner-research/api/jobs/{job['id']}").json["source_summary"]
        == job["source_summary"]
    )


def test_automatic_continuation_preserves_activity_and_yields_to_next_job(app, client, monkeypatch):
    request = portfolio(client, optimize=True)
    jobs = [
        client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request}).json["id"]
        for _ in range(2)
    ]
    store = app.extensions["research_store"]
    calls = []

    def run(
        store,
        owner,
        evidence,
        spec,
        *,
        saved,
        checkpoint,
        progress,
        cancelled,
        activity,
        observe=None,
    ):
        calls.append(saved)
        if len(calls) == 1:
            activity(
                {"stage": "download", "prices": {"required_candles": 10, "downloaded_candles": 3}}
            )
            checkpoint({"test_checkpoint": True}, {"completed": 30, "total": 100})
            raise AcquisitionBatchPending()
        return {"summary": {}, "ledger": [], "equity_curve": []}, {
            **evidence,
            "snapshot": reference(),
        }

    monkeypatch.setattr(research_portfolio, "run", run)
    worker.acquire(store, "batches")
    try:
        worker.run_one(store, "batches")
        first = client.get(f"/scanner-research/api/jobs/{jobs[0]}").json
        assert first["status"] == "queued" and first["activity"]["batch_count"] == 1
        assert first["activity"]["prices"]["downloaded_candles"] == 3
        worker.run_one(store, "batches")
        assert client.get(f"/scanner-research/api/jobs/{jobs[1]}").json["status"] == "completed"
        assert client.get(f"/scanner-research/api/jobs/{jobs[0]}").json["status"] == "queued"
        worker.run_one(store, "batches")
    finally:
        worker.release(store, "batches")
    assert calls[-1] == {"test_checkpoint": True}
    final = client.get(f"/scanner-research/api/jobs/{jobs[0]}").json
    assert final["status"] == "completed" and final["activity"]["prices"]["downloaded_candles"] == 3
    assert len(client.get("/scanner-research/api/jobs").json) == 2


def test_symbol_transition_is_persisted_before_a_long_request(app, client, monkeypatch):
    request = portfolio(client, optimize=True)
    job_id = client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request}).json[
        "id"
    ]
    store = app.extensions["research_store"]

    def run(
        store,
        owner,
        evidence,
        spec,
        *,
        saved,
        checkpoint,
        progress,
        cancelled,
        activity,
        observe=None,
    ):
        for symbol in ("AAA", "BBB"):
            activity({"stage": "download", "prices": {"current_symbol": symbol}})
            shown = client.get(f"/scanner-research/api/jobs/{job_id}").json["activity"]
            assert shown["prices"]["current_symbol"] == symbol
        activity({"prices": {"cache_complete": True}})
        shown = client.get(f"/scanner-research/api/jobs/{job_id}").json["activity"]
        assert shown["prices"]["cache_complete"] is True
        return {"summary": {}, "ledger": [], "equity_curve": []}, {
            **evidence,
            "snapshot": reference(),
        }

    monkeypatch.setattr(research_portfolio, "run", run)
    worker.acquire(store, "symbol-activity")
    try:
        worker.run_one(store, "symbol-activity")
    finally:
        worker.release(store, "symbol-activity")
    assert client.get(f"/scanner-research/api/jobs/{job_id}").json["status"] == "completed"
