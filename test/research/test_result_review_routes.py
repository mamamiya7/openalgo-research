"""Native HTTP journey: report -> reserved evaluation -> direct retained decision."""

# ruff: noqa: F811 -- shared isolated native fixtures

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, select
from test_decisions import body, native_setup
from test_jobs import app, client
from test_library import OWNER
from test_matched_baselines import setup as matching_setup
from test_report_period_workflow import run_worker

from blueprints.scanner_research import scanner_research_bp
from database.research_db import (
    ResearchComparison,
    ResearchDecisionEvent,
    ResearchEvidenceOpen,
    ResearchJob,
)


def test_matching_baseline_http_flow_preserves_study_and_baseline(app, client, monkeypatch):
    store, experiment, study, baseline = matching_setup(app, client, monkeypatch)
    path = f"/scanner-research/api/library/experiments/{experiment}/studies/{study}/baselines/{baseline}/match"
    assert app.test_client().get(path).status_code == 401
    response = client.get(path)
    assert response.status_code == 200, response.json
    view = response.json
    assert view["action"]["kind"] == "prepare"
    payload = {
        "revision": view["revision"],
        "request_id": "http-matched-baseline",
        "study_result_artifact": view["study"]["result_artifact"],
        "baseline_result_artifact": view["baseline"]["result_artifact"],
    }
    submitted = client.post(path, json=payload)
    assert submitted.status_code == 201, submitted.json
    matched = submitted.json["job"]["id"]
    assert client.post(path, json=payload).json["job"]["id"] == matched
    run_worker(store)
    opened = client.get(f"/scanner-research/api/jobs/{matched}")
    assert opened.json["status"] == "completed", opened.json
    assert opened.json["display"]["role"] == "matched_baseline"
    assert opened.json["result"]["report_context"]["period"] == "selection"
    assert client.get(path).json["action"] == {"kind": "open", "job_id": matched}
    assert client.get(path + "?extra=1").status_code == 400
    assert client.post(path, json=[]).status_code == 400
    with client.session_transaction() as session:
        session["user"] = "another-owner"
    assert client.get(path).status_code == 404


def counts(store):
    with store.sessions() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (
                ResearchJob,
                ResearchComparison,
                ResearchDecisionEvent,
                ResearchEvidenceOpen,
            )
        )


def test_exact_report_validation_decision_and_reopening_over_native_routes(
    app, client, monkeypatch
):
    experiment, _, members, study, child, base = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    report = f"{base}/results/{child}"
    before = counts(store)
    response = client.get(report + "/validation")
    assert response.status_code == 200, response.json
    review = response.json
    assert review["candidate"]["source_job_id"] == study
    assert review["candidate"]["config_id"] == members[1]["config_id"]
    assert review["candidate"]["report_job_id"] == child
    assert counts(store) == before
    payload = {
        "revision": review["revision"],
        "source_result_artifact": review["candidate"]["source_result_artifact"],
        "request_id": "direct-route-later",
    }
    response = client.post(report + "/validation", json=payload)
    assert response.status_code == 201, response.json
    later = response.json["job"]["id"]
    retry = client.post(report + "/validation", json=payload)
    assert retry.status_code == 200 and retry.json["job"]["id"] == later
    run_worker(store)
    review = client.get(report + "/validation").json
    assert review["action"]["kind"] == "open"
    assert review["action"]["job_id"] == later
    exact = review["action"]["evidence_id"]
    context = client.get(report + "/decision").json
    assert context["current"] is None and context["target"]["config_id"] == members[1]["config_id"]
    assert counts(store)[-1] == before[-1]
    saved = client.post(report + "/decisions", json=body(evaluation_id=exact))
    assert saved.status_code == 201, saved.json
    decision = saved.json["decision"]
    assert decision["current"]["comparison_id"] is None
    assert client.get(report + "/validation").json["current"]["id"] == decision["id"]
    selection = client.get(
        f"{base}/decisions/{decision['id']}/events/{saved.json['event']['id']}/report"
    )
    assert selection.status_code == 200 and selection.json["job"]["id"] == child
    assert counts(store)[1] == before[1]
    assert counts(store)[-1] == before[-1]
    target = {
        "kind": "direct_report",
        "job_id": study,
        "config_id": members[1]["config_id"],
        "evaluation_id": exact,
    }
    opened = client.post(
        base + "/evidence/opened", json={"request_id": "explicit-later-open", "target": target}
    )
    assert opened.status_code == 200, opened.json
    assert client.get(report + "/validation").json["evidence_use"]["later_opened_at"] is not None
    assert counts(store)[-1] == before[-1] + 1


def test_direct_routes_reject_ambiguous_queries_foreign_account_and_missing_csrf(
    app, client, monkeypatch
):
    _, _, _, _, child, base = native_setup(app, client, monkeypatch)
    report = f"{base}/results/{child}"
    before = counts(app.extensions["research_store"])
    for suffix in (
        "/decision?config_id=",
        "/decision?limit=1&limit=2",
        "/validation?config_id=x&config_id=y",
        "/validation?unknown=1",
    ):
        assert client.get(report + suffix).status_code == 400
    assert (
        client.post(
            report + "/validation?config_id=anything", json={"config_id": "twice"}
        ).status_code
        == 400
    )
    for malformed in (None, [], "text"):
        assert (
            client.post(report + "/validation?config_id=anything", json=malformed).status_code
            == 400
        )
    assert counts(app.extensions["research_store"]) == before
    for suffix in ("/decision", "/validation"):
        assert app.test_client().get(report + suffix).status_code == 401
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    for suffix in ("/decision", "/validation"):
        assert client.get(report + suffix).status_code == 404
    assert client.post(report + "/decisions", json=body()).status_code == 404
    protected = Flask(__name__)
    protected.config.update(
        SECRET_KEY="controlled-csrf", RESEARCH_DATA_DIR=str(app.extensions["research_store"].root)
    )
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    browser = protected.test_client()
    with browser.session_transaction() as session:
        session["user"] = OWNER
    try:
        for suffix in ("/decisions", "/validation"):
            response = browser.post(report + suffix, json=body())
            assert response.status_code == 400 and b"csrf" in response.data.lower()
    finally:
        protected.extensions["research_store"].close()
