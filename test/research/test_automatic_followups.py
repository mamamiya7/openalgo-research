# ruff: noqa: F811 -- shared isolated native Flask fixtures
"""Automatic findings become ordinary decision evidence through exact replay."""

import time

import pytest
from test_automatic_workflow import calculate, request_and_prices
from test_decisions import body
from test_jobs import app, client
from test_library import OWNER, create

from database.research_db import ResearchJob, ResearchLibraryJob
from services import research_decisions as decisions
from services import research_shortlist as shortlist
from services import research_validation as validation
from services import scanner_research_service as service


@pytest.mark.timeout(180)
def test_baseline_finding_requires_exact_backtest_then_supports_real_saved_decision(
    app, client, monkeypatch
):
    request = request_and_prices(client, monkeypatch)
    response = client.post(
        "/scanner-research/api/portfolio/jobs",
        json={"portfolio": request, "request_id": "automatic-followup-parent"},
    )
    assert response.status_code == 202, response.json
    store = app.extensions["research_store"]
    job_id = response.json["id"]
    calculate(store, "automatic-followup-parent")
    original = service.get_job(store, OWNER, job_id)
    assert original.status == "completed", original.error
    result = service.read_artifact(store, original.result_artifact)["result"]
    assert result["automatic_research"]["selected_is_baseline"]
    experiment = create(client)
    with store.sessions.begin() as db:
        db.add(
            ResearchLibraryJob(
                experiment_id=experiment["id"],
                job_id=job_id,
                role="run",
                created_at=time.time(),
            )
        )
    with pytest.raises(ValueError, match="Open an exact backtest"):
        validation.canonical_source(store, OWNER, experiment["id"], job_id)
    with pytest.raises(ValueError, match="Open an exact backtest"):
        shortlist._evidence(store, original, result["automatic_research"]["selected_config_id"])
    replay = client.post(
        f"/scanner-research/api/library/experiments/{experiment['id']}/replay",
        json={
            "revision": experiment["revision"],
            "request_id": "automatic-followup-fixed",
            "job_id": job_id,
            "period": "selection",
        },
    )
    assert replay.status_code == 202, replay.json
    child_id = replay.json["job"]["id"]
    calculate(store, "automatic-followup-fixed")
    child = service.get_job(store, OWNER, child_id)
    assert child.status == "completed", child.error
    source, config_id = validation.canonical_source(store, OWNER, experiment["id"], child_id)
    assert source.id == child_id
    assert config_id == result["automatic_research"]["selected_config_id"]
    fixed = service.read_artifact(store, child.result_artifact)["result"]
    assert fixed["summary"] == result["summary"]
    assert not fixed.get("automatic_research")
    context = decisions.direct_context(store, OWNER, experiment["id"], child_id)
    assert context["target"] == {"job_id": child_id, "config_id": config_id}
    saved = decisions.save_direct_decision(
        store, OWNER, experiment["id"], child_id, body(token="automatic-fixed-decision")
    )
    assert saved["event"]["state"] == "keep"
    assert saved["event"]["config_id"] == config_id
    book = shortlist.save_candidate(
        store,
        OWNER,
        experiment["id"],
        {"job_id": child_id, "config_id": config_id},
    )
    assert book["candidate"]["origin_kind"] == "backtest"
    assert book["candidate"]["trial_number"] is None
    # The bridge still verifies its original parent's artifact and ownership.
    with store.sessions.begin() as db:
        parent = db.get(ResearchJob, job_id)
        parent.result_artifact = "f" * 64
    with pytest.raises(ValueError, match="original saved result changed"):
        validation.canonical_source(store, OWNER, experiment["id"], child_id)
