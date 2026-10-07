"""Native decision HTTP boundaries and resource lifetime on real saved evidence."""

# ruff: noqa: F811 -- shared isolated native fixtures

import pytest
from sqlalchemy import event, func, select
from test_candidate_reports import frozen_studies
from test_comparisons import create, ready
from test_jobs import app, client
from test_library import OWNER

from database.research_db import (
    ResearchDecision,
    ResearchDecisionEvent,
    ResearchDecisionRequest,
    ResearchEvidenceOpen,
    ResearchJob,
)
from services import scanner_research_service as service


def setup(app, client, frozen_studies):
    experiment, members, job, _ = ready(app, client, frozen_studies)
    comparison = create(client, experiment, members)
    base = f"/scanner-research/api/library/experiments/{experiment}"
    member = f"{base}/comparisons/{comparison['id']}/members/{members[1]['id']}"
    return base, member, job, comparison, members[1]


def request(token="boundary-decision", revision=0, **changes):
    return {
        "request_id": token,
        "revision": revision,
        "state": "keep",
        "reason": "Review alongside the later-period result.",
        **changes,
    }


def counts(store):
    with store.sessions() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (
                ResearchDecision,
                ResearchDecisionEvent,
                ResearchDecisionRequest,
                ResearchEvidenceOpen,
                ResearchJob,
            )
        )


def saved(client, member):
    response = client.post(member + "/decisions", json=request())
    assert response.status_code == 201, response.json
    return response.json["event"]


def test_reads_never_create_openings_or_jobs_and_preserve_export(
    app, client, frozen_studies, monkeypatch
):
    base, member, job, _, _ = setup(app, client, frozen_studies)
    store = app.extensions["research_store"]
    before = counts(store)
    export_url = f"/scanner-research/api/jobs/{job}/export"
    original = client.get(export_url).data
    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_sources.resolve_broker_session",
        "research.connectors.optuna_portfolio.run_search",
        "services.research_candidates.prepare",
        "services.research_analysis.submit",
    ):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail("Decision started work"))
    assert client.get(member + "/decision").status_code == 200
    assert client.get(member).status_code == 200
    assert counts(store) == before
    choice = saved(client, member)
    after = counts(store)
    assert after[0] == before[0] + 1 and after[1] == before[1] + 1
    assert after[3:] == before[3:]
    history = f"{base}/decisions/{choice['decision_id']}"
    for url in (
        base + "/decisions",
        member + "/decision",
        history + "/history",
        history + "/events/" + choice["id"],
        history + "/events/" + choice["id"] + "/report?evidence=selection",
    ):
        response = client.get(url)
        assert response.status_code == 200, response.json
    assert counts(store) == after
    assert client.get(export_url).data == original


def test_decision_list_history_and_filters_only_load_small_metadata(
    app, client, frozen_studies, monkeypatch
):
    base, member, *_ = setup(app, client, frozen_studies)
    choice = saved(client, member)
    response = client.post(
        member + "/decisions", json=request("boundary-revisit", 1, state="revisit")
    )
    assert response.status_code == 201, response.json
    monkeypatch.setattr(
        service, "read_artifact", lambda *a, **k: pytest.fail("List/history loaded a report")
    )
    current = client.get(base + "/decisions?state=revisit&limit=1").json
    assert current["total"] == 1 and current["items"][0]["current"]["state"] == "revisit"
    assert client.get(base + "/decisions?state=keep").json["total"] == 0
    history = f"{base}/decisions/{choice['decision_id']}/history"
    first = client.get(history + "?limit=1").json
    assert first["total"] == 2 and first["next_offset"] == 1
    second = client.get(history + "?limit=1&offset=1").json
    assert second["next_offset"] is None
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {
        choice["id"],
        response.json["event"]["id"],
    }


def test_malformed_decisions_and_queries_leave_no_partial_metadata(app, client, frozen_studies):
    base, member, *_ = setup(app, client, frozen_studies)
    store = app.extensions["research_store"]
    before = counts(store)
    for changes in (
        {"revision": True},
        {"revision": -1},
        {"state": "passed"},
        {"state": None},
        {"reason": "x" * 2001},
        {"reason": ["not text"]},
        {"request_id": "bad token"},
        {"evaluation_id": "not-an-evidence-identity"},
        {"evaluation_id": False},
        {"evaluation_id": 0},
        {"evaluation_id": ""},
        {"evaluation_id": []},
        {"source_job_id": "0" * 32},
    ):
        response = client.post(member + "/decisions", json=request(**changes))
        assert response.status_code == 400, (changes.keys(), response.json)
    assert (
        client.post(
            member + "/decisions",
            data='{"x":"' + "x" * 9000 + '"}',
            content_type="application/json",
        ).status_code
        == 400
    )
    for query in (
        "limit=51",
        "limit=0",
        "offset=-1",
        "offset=100001",
        "limit=x",
        "limit=1&limit=2",
        "other=1",
        "state=passed",
        "state=keep&state=reject",
    ):
        assert client.get(base + "/decisions?" + query).status_code == 400, query
    assert counts(store) == before


def test_decision_routes_scope_to_account_and_keep_native_csrf(app, client, frozen_studies):
    from flask import Flask
    from flask_wtf.csrf import CSRFProtect

    from blueprints.scanner_research import scanner_research_bp

    base, member, _, comparison, candidate = setup(app, client, frozen_studies)
    choice = saved(client, member)
    urls = (
        base + "/decisions",
        member + "/decision",
        f"{base}/decisions/{choice['decision_id']}/history",
        f"{base}/decisions/{choice['decision_id']}/events/{choice['id']}/report",
    )
    for url in urls:
        assert app.test_client().get(url).status_code == 401
    opened = {
        "request_id": "boundary-open",
        "target": {
            "kind": "comparison_member",
            "comparison_id": comparison["id"],
            "member_id": candidate["id"],
        },
    }
    with client.session_transaction() as session:
        session["user"] = "another-research-account"
    for url in urls:
        assert client.get(url).status_code == 404
    assert client.post(member + "/decisions", json=request("foreign-account", 1)).status_code == 404
    assert client.post(base + "/evidence/opened", json=opened).status_code == 404
    protected = Flask(__name__)
    protected.config.update(
        SECRET_KEY="isolated-decision-csrf",
        RESEARCH_DATA_DIR=str(app.extensions["research_store"].root),
    )
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    browser = protected.test_client()
    with browser.session_transaction() as session:
        session["user"] = OWNER
    try:
        assert browser.get(base + "/decisions").status_code == 200
        for response in (
            browser.post(member + "/decisions", json=request("csrf-decision", 1)),
            browser.post(base + "/evidence/opened", json=opened),
        ):
            assert response.status_code == 400 and b"csrf" in response.data.lower()
    finally:
        protected.extensions["research_store"].close()


def test_read_and_revision_conflict_connections_return_to_zero(app, client, frozen_studies):
    base, member, *_ = setup(app, client, frozen_studies)
    saved(client, member)
    store = app.extensions["research_store"]
    before = counts(store)
    active = [0]

    def connected(*_):
        active[0] += 1

    def closed(*_):
        active[0] -= 1

    event.listen(store.engine, "connect", connected)
    event.listen(store.engine, "close", closed)
    try:
        for _ in range(100):
            assert client.get(base + "/decisions").status_code == 200
            response = client.post(member + "/decisions", json=request("stale-retry", 99))
            assert response.status_code == 409, response.json
            assert response.json["code"] == "decision_revision_conflict"
            assert active[0] == 0
    finally:
        event.remove(store.engine, "connect", connected)
        event.remove(store.engine, "close", closed)
    assert counts(store) == before


def test_storage_full_rejects_decision_without_half_a_history(
    app, client, frozen_studies, monkeypatch
):
    _, member, *_ = setup(app, client, frozen_studies)
    store = app.extensions["research_store"]
    before = counts(store)

    def full(*_):
        raise ValueError("Research storage is full")

    monkeypatch.setattr(service, "ensure_storage_capacity", full)
    response = client.post(member + "/decisions", json=request())
    assert response.status_code == 400, response.json
    assert counts(store) == before
