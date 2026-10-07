"""Native HTTP: retained trial -> named choice -> replace signals -> exact replay."""

# ruff: noqa: F811 -- shared isolated native fixtures

import io

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, select
from test_decisions import body, native_setup
from test_jobs import app, client
from test_library import OWNER
from test_report_period_workflow import run_worker

from blueprints.scanner_research import scanner_research_bp
from database.research_db import ResearchJob, ResearchSetupVersion


def test_chosen_rules_new_csv_and_exact_replay_are_separate_native_actions(
    app, client, monkeypatch
):
    experiment, _, members, study, child, base = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    before = client.get(base).json
    with store.sessions() as db:
        jobs_before = db.scalar(select(func.count()).select_from(ResearchJob))
    decision = client.post(f"{base}/results/{child}/decisions", json=body()).json
    target = {"decision_id": decision["decision"]["id"], "event_id": decision["event"]["id"]}
    path = base + "/chosen-setup"
    assert app.test_client().get(path).status_code == 401
    context = client.get(path, query_string=target)
    assert context.status_code == 200 and context.json["eligibility"]["available"], context.json
    payload = {
        **target,
        "revision": context.json["revision"],
        "name": "Momentum retained",
        "request_id": "http-choose-retained",
    }
    response = client.post(path, json=payload)
    assert response.status_code == 201, response.json
    choice = response.json["choice"]
    assert choice["source_job_id"] == study and choice["config_id"] == members[1]["config_id"]
    assert client.post(path, json=payload).json["choice"]["id"] == choice["id"]
    saved = client.get(base).json
    assert saved["draft"] == before["draft"]
    assert saved["chosen_setup"]["id"] == choice["id"]
    listed = client.get("/scanner-research/api/library/experiments").json["items"]
    assert (
        next(item for item in listed if item["id"] == experiment)["chosen_setup"]["id"]
        == choice["id"]
    )
    history = client.get(path + "/history").json
    assert history["total"] == 1 and history["items"][0]["id"] == choice["id"]
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == jobs_before
    uploaded = client.post(
        "/scanner-research/api/portfolio/inputs",
        data={"file": (io.BytesIO(b"Date,Symbol\n2026-08-03,AAA\n2026-08-04,AAA\n"), "newer.csv")},
    )
    assert uploaded.status_code == 201, uploaded.json
    strategy_id = choice["strategies"][0]["id"]
    spec = {
        "choice_id": choice["id"],
        "sources": {strategy_id: uploaded.json["id"]},
        "mode": "backtest",
    }
    review = client.post(path + "/preview", json=spec)
    assert review.status_code == 200, review.json
    prepared = review.json
    assert prepared["changes"]["rules_unchanged"]
    assert len(prepared["changes"]["sources"]) == 1
    rules = next(
        item for item in prepared["draft"]["portfolio"]["strategies"] if item["id"] == strategy_id
    )
    original = next(item for item in choice["strategies"] if item["id"] == strategy_id)
    assert (
        rules["config"] == original["config"]
        and rules["allocation_pct"] == original["allocation_pct"]
    )
    assert client.get(base).json["draft"] == before["draft"]
    use_data = {**spec, "revision": prepared["revision"], "request_id": "http-reuse-new-inputs"}
    reused = client.post(path + "/use", json=use_data)
    assert reused.status_code == 201, reused.json
    assert reused.json["preserved_version_id"]
    assert client.post(path + "/use", json=use_data).json["reused"]
    with store.sessions() as db:
        new_version = db.get(ResearchSetupVersion, reused.json["applied_version_id"])
        assert new_version.parent_version_id == choice["version"]["id"]
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == jobs_before
    assert client.get(path).json["current"]["id"] == choice["id"]
    replay_data = {
        "choice_id": choice["id"],
        "revision": reused.json["revision"],
        "request_id": "http-choice-exact-replay",
    }
    replay = client.post(path + "/replay", json=replay_data)
    assert replay.status_code == 201, replay.json
    replay_id = replay.json["job"]["id"]
    assert client.post(path + "/replay", json=replay_data).json["job"]["id"] == replay_id
    run_worker(store)
    result = client.get(f"/scanner-research/api/jobs/{replay_id}").json
    assert result["status"] == "completed", result
    assert result["result"]["report_context"]["config_id"] == choice["config_id"]
    assert result["display"]["role"] == "replay"
    current = client.get(base).json
    assert (
        next(
            item
            for item in current["draft"]["portfolio"]["strategies"]
            if item["id"] == strategy_id
        )["source_id"]
        == uploaded.json["id"]
    )
    assert client.get(path).json["current"]["id"] == choice["id"]


def test_chosen_setup_routes_bound_queries_authentication_and_csrf(app, client, monkeypatch):
    _, _, _, _, _, base = native_setup(app, client, monkeypatch)
    path = base + "/chosen-setup"
    for suffix in (
        "?decision_id=x",
        "?decision_id=x&event_id=",
        "?decision_id=x&event_id=y&event_id=z",
        "?unknown=1",
        "/history?limit=1&limit=2",
        "/history?limit=10000",
    ):
        assert client.get(path + suffix).status_code == 400
    for suffix in ("", "/preview", "/use", "/replay"):
        assert client.post(path + suffix + "?unknown=1", json={}).status_code == 400
        assert client.post(path + suffix, json=[]).status_code == 400
    with client.session_transaction() as session:
        session["user"] = "foreign-owner"
    assert client.get(path).status_code == 404
    assert client.get(path + "/history").status_code == 404
    protected = Flask(__name__)
    protected.config.update(
        SECRET_KEY="controlled-choice-csrf",
        RESEARCH_DATA_DIR=str(app.extensions["research_store"].root),
    )
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    browser = protected.test_client()
    with browser.session_transaction() as session:
        session["user"] = OWNER
    try:
        for suffix in ("", "/preview", "/use", "/replay"):
            response = browser.post(path + suffix, json={})
            assert response.status_code == 400 and b"csrf" in response.data.lower()
    finally:
        protected.extensions["research_store"].close()
