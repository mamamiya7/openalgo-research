"""Financial completion cannot suppress deferred optional study diagnostics."""

# ruff: noqa: F811 -- shared isolated fixtures
import copy

import pytest
from test_jobs import app, client  # noqa: F401
from test_saved_analysis import completed_parent

from database.research_db import ResearchExperiment, ResearchJob
from research.analytics import build_analysis
from research.study_diagnostics import deferred
from services import research_analysis as analysis
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def modern_parent(app, client, *, study=True):
    parent, old = completed_parent(app, client)
    store = app.extensions["research_store"]
    bundle = service.read_artifact(store, old)
    result = bundle["result"]
    result["analysis"] = build_analysis(result)
    if study:
        result["experiment"]["diagnostics"] = deferred()
    else:
        result.pop("experiment")
    digest = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, parent).result_artifact = digest
        if not study:
            db.get(ResearchExperiment, parent).kind = "portfolio_backtest"
    return store, parent, digest, copy.deepcopy(bundle)


def test_financially_complete_study_queues_optional_diagnostics_and_keeps_original_findings(
    app, client, monkeypatch
):
    store, parent, digest, original = modern_parent(app, client)
    assert analysis.status(store, "research-test", parent)["status"] == "missing"
    initial = client.get(f"/scanner-research/api/jobs/{parent}").json
    assert initial["status"] == "completed" and initial["result"]["summary"]
    first = analysis.submit(store, "research-test", parent)
    assert first["status"] == "queued"
    assert (
        analysis.submit(store, "research-test", parent)["analysis_job_id"]
        == first["analysis_job_id"]
    )
    monkeypatch.setattr(
        "services.research_portfolio.run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Reran prices/portfolio")),
    )
    worker.acquire(store, "deferred-diagnostics-test")
    try:
        worker.run_one(store, "deferred-diagnostics-test")
    finally:
        worker.release(store, "deferred-diagnostics-test")
    updated = analysis.status(store, "research-test", parent)
    assert updated["status"] == "complete", updated
    assert len(updated["job"]["result"]["experiment"]["study_analysis"]["charts"]) == 12
    assert updated["job"]["result"]["experiment"]["diagnostics"]["status"] == "complete"
    assert updated["job"]["result"]["summary"] == initial["result"]["summary"]
    assert analysis.submit(store, "research-test", parent)["status"] == "complete"
    assert service.read_artifact(store, digest) == original


def test_financial_backtest_without_study_needs_no_optuna_job(app, client):
    store, parent, _, _ = modern_parent(app, client, study=False)
    assert analysis.status(store, "research-test", parent)["status"] == "complete"
    assert analysis.submit(store, "research-test", parent)["status"] == "complete"
    assert analysis.latest_job(store, service.get_job(store, "research-test", parent)) is None


def test_study_without_proposal_evidence_cannot_be_mistaken_for_complete_charts_or_queued(
    app, client
):
    store, parent, _, _ = modern_parent(app, client, study=False)
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, parent).kind = "portfolio_optimize"
    assert analysis.status(store, "research-test", parent)["status"] == "missing"
    with pytest.raises(ValueError, match="no recorded proposals"):
        analysis.submit(store, "research-test", parent)
    assert analysis.latest_job(store, service.get_job(store, "research-test", parent)) is None


def test_old_completed_metrics_only_overlay_does_not_suppress_missing_study_charts(app, client):
    store, parent, digest, original = modern_parent(app, client)
    first = analysis.submit(store, "research-test", parent)["analysis_job_id"]
    partial = service.save_artifact(
        store,
        {
            "kind": "portfolio_analysis",
            "parent_result_artifact": digest,
            "result": {"version": analysis.VERSION, "analysis": original["result"]["analysis"]},
        },
    )
    with store.sessions.begin() as db:
        child = db.get(ResearchJob, first)
        child.status, child.result_artifact = "completed", partial
    assert analysis.status(store, "research-test", parent)["status"] == "missing"
    second = analysis.submit(store, "research-test", parent)["analysis_job_id"]
    assert second != first
    assert service.get_job(store, "research-test", first).result_artifact == partial
    assert service.read_artifact(store, digest) == original


def test_optional_chart_failure_leaves_completed_findings_available_and_retry_is_separate(
    app, client, monkeypatch
):
    store, parent, digest, original = modern_parent(app, client)
    first = analysis.submit(store, "research-test", parent)["analysis_job_id"]

    def fail(*a, **k):
        raise ValueError("controlled optional diagnostics failure")

    monkeypatch.setattr("research.study_diagnostics.build", fail)
    worker.acquire(store, "failed-diagnostics-test")
    try:
        worker.run_one(store, "failed-diagnostics-test")
    finally:
        worker.release(store, "failed-diagnostics-test")
    assert analysis.status(store, "research-test", parent)["status"] == "failed"
    assert service.get_job(store, "research-test", parent).status == "completed"
    assert service.read_artifact(store, digest) == original
    second = analysis.submit(store, "research-test", parent)["analysis_job_id"]
    assert second != first
