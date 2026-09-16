"""Authenticated condition tests stay atomically linked to their saved research."""

# ruff: noqa: F811 -- isolated saved-worker fixture

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, select
from test_condition_replay import (
    run,
    saved,  # noqa: F401
)

from blueprints.scanner_research import scanner_research_bp
from database.research_db import ResearchJob, ResearchLibraryExperiment, ResearchLibraryJob
from services import research_library as library
from services import scanner_research_service as service


def browser(store, *, csrf=False):
    app = Flask(__name__)
    app.config.update(
        TESTING=True, SECRET_KEY="isolated-condition-route", RESEARCH_DATA_DIR=str(store.root)
    )
    if csrf:
        CSRFProtect(app)
    app.register_blueprint(scanner_research_bp)
    app.extensions["research_store"].close()
    app.extensions["research_store"] = store
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = "owner"
    return app, client


def path(parent):
    return f"/scanner-research/api/portfolio/jobs/{parent.id}/condition-replay"


def count(store):
    with store.sessions() as db:
        return db.scalar(select(func.count()).select_from(ResearchJob))


@pytest.mark.timeout(180)
def test_route_saves_linked_native_job_and_repeat_is_idempotent(saved):
    store, parent, request = saved
    app, client = browser(store)
    before = count(store)
    data = {**request, "request_id": "condition-http-first"}
    response = client.post(path(parent), json=data)
    assert response.status_code == 202, response.json
    identifier = response.json["id"]
    with store.sessions() as db:
        links = db.scalars(
            select(ResearchLibraryJob).where(ResearchLibraryJob.job_id == identifier)
        ).all()
        assert len(links) == 1 and links[0].version_id
        parent_link = db.get(ResearchLibraryJob, (links[0].experiment_id, parent.id))
        assert parent_link is not None
        experiment_id = links[0].experiment_id
    assert count(store) == before + 1
    experiment = library.get_experiment(store, "owner", experiment_id)
    with pytest.raises(ValueError, match="Exact replay"):
        library.restore_version(
            store, "owner", experiment_id, links[0].version_id, {"revision": experiment["revision"]}
        )
    run(store)
    report = service.get_job(store, "owner", identifier)
    assert report.status == "completed", report.error
    with pytest.raises(ValueError, match="Exact replay"):
        library.from_job(
            store,
            "owner",
            {"job_id": identifier, "mode": "optimize", "request_id": "condition-copy-blocked"},
        )
    from services.research_shortlist import _evidence

    with pytest.raises(ValueError, match="not yet supported"):
        _evidence(store, report)
    assert client.post(path(parent), json=data).json["id"] == identifier
    assert count(store) == before + 1
    saved_version = library.get_version(store, "owner", experiment_id, links[0].version_id)
    assert saved_version["parent_job_id"] == parent.id
    assert saved_version["parent_result_artifact"] == parent.result_artifact
    altered = {**data, "regime": "down"}
    assert client.post(path(parent), json=altered).status_code == 400
    assert count(store) == before + 1


@pytest.mark.timeout(180)
def test_route_authentication_csrf_and_archive_guard_create_no_jobs(saved):
    store, parent, request = saved
    app, client = browser(store)
    before = count(store)
    data = {**request, "request_id": "condition-http-guard"}
    assert app.test_client().post(path(parent), json=data).status_code == 401
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    assert client.post(path(parent), json=data).status_code == 404
    _, protected = browser(store, csrf=True)
    assert protected.post(path(parent), json=data).status_code == 400
    with client.session_transaction() as session:
        session["user"] = "owner"
    with store.sessions.begin() as db:
        link = db.scalar(select(ResearchLibraryJob).where(ResearchLibraryJob.job_id == parent.id))
        db.get(ResearchLibraryExperiment, link.experiment_id).archived = True
    rejected = client.post(path(parent), json=data)
    assert rejected.status_code == 400 and "Restore" in rejected.json["message"]
    assert count(store) == before


@pytest.mark.timeout(180)
def test_atomic_admission_rechecks_archive_after_input_preparation(saved, monkeypatch):
    store, parent, request = saved
    _, client = browser(store)
    before = count(store)
    original = library._enqueue

    def race(*args, **kwargs):
        with store.sessions.begin() as db:
            db.get(ResearchLibraryExperiment, args[2]).archived = True
        return original(*args, **kwargs)

    monkeypatch.setattr(library, "_enqueue", race)
    response = client.post(path(parent), json={**request, "request_id": "condition-http-race"})
    assert response.status_code == 400
    assert count(store) == before
    assert service.get_job(store, "owner", parent.id).status == "completed"
