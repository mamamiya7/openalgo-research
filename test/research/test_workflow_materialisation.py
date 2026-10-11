"""A completed automatic choice gets one verified reusable report, without new data."""

# ruff: noqa: F811
import time

import pytest
from test_automatic_workflow import calculate, request_and_prices
from test_decisions import body
from test_jobs import app, client
from test_library import OWNER, create

from database.research_db import ResearchJob, ResearchLibraryJob
from services import research_candidates as candidates
from services import research_decisions as decisions
from services import research_validation as validation
from services import scanner_research_service as service


@pytest.mark.timeout(600)
def test_automatic_baseline_materialises_once_and_keeps_original_evidence(app, client, monkeypatch):
    request = request_and_prices(client, monkeypatch)
    response = client.post(
        "/scanner-research/api/portfolio/jobs",
        json={
            "portfolio": request,
            "request_id": "materialisation-parent",
        },
    )
    assert response.status_code == 202, response.json
    store = app.extensions["research_store"]
    parent_id = response.json["id"]
    calculate(store)
    parent = service.get_job(store, OWNER, parent_id)
    assert parent.status == "completed", parent.error
    original = service.read_artifact(store, parent.result_artifact)["result"]
    config_id = original["experiment"]["recommendation_id"]
    assert original["automatic_research"]["selected_is_baseline"]
    assert original["experiment"]["diagnostics"]["status"] == "available"
    assert not original["experiment"].get("study_analysis")
    experiment = create(client)
    with store.sessions.begin() as db:
        db.add(
            ResearchLibraryJob(
                experiment_id=experiment["id"], job_id=parent_id, role="run", created_at=time.time()
            )
        )
    export = client.get(f"/scanner-research/api/jobs/{parent_id}/export").data
    available = candidates.availability(store, OWNER, parent_id)
    selected = next(row for row in available["candidates"] if row["config_id"] == config_id)
    assert selected["status"] == "available" and selected["is_selected"]
    assert selected["trial_number"] is None  # separate reference, not an invented trial
    with pytest.raises(ValueError, match="exact backtest"):
        validation.canonical_source(store, OWNER, experiment["id"], parent_id)
    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_sources.resolve_broker_session",
        "research.connectors.optuna_portfolio.run_search",
    ):
        monkeypatch.setattr(
            target, lambda *a, **kw: pytest.fail("Materialisation downloaded or searched")
        )
    queued = candidates.prepare(store, OWNER, parent_id, config_id)
    assert queued["status"] == "queued"
    assert candidates.prepare(store, OWNER, parent_id, config_id) == queued
    assert (
        service.job_receipt(store, service.get_job(store, OWNER, queued["report_job_id"]))[
            "queue_position"
        ]
        == 1
    )
    # Numerical jobs outrank optional diagnostics already queued by completion.
    calculate(store)
    child = service.get_job(store, OWNER, queued["report_job_id"])
    assert child.status == "completed", child.error
    fixed = service.read_artifact(store, child.result_artifact)["result"]
    assert fixed["summary"] == original["summary"]
    assert fixed["equity_curve"] == original["equity_curve"]
    assert fixed["ledger"] == original["ledger"]
    assert fixed["candidate_report"]["verification"]["summary"] == "matched"
    source, actual = validation.canonical_source(store, OWNER, experiment["id"], child.id)
    assert (source.id, actual) == (child.id, config_id)
    saved = decisions.save_direct_decision(
        store, OWNER, experiment["id"], child.id, body(token="materialisation-keep")
    )
    assert saved["event"]["state"] == "keep"
    ready = candidates.prepare(store, OWNER, parent_id, config_id)
    assert ready["status"] == "ready" and ready["report_job_id"] == child.id
    assert client.get(f"/scanner-research/api/jobs/{parent_id}/export").data == export
    with store.sessions.begin() as db:
        db.get(ResearchJob, parent_id).result_artifact = "f" * 64
    with pytest.raises(ValueError, match="original saved result changed"):
        validation.canonical_source(store, OWNER, experiment["id"], child.id)
