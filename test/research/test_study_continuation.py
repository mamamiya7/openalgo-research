"""Native study extension retains original evidence, prices and completed work."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import copy

import pytest
from sqlalchemy import func, select
from test_jobs import app, client
from test_library import OWNER, create, run
from test_portfolio_validation import inputs
from test_report_period_workflow import draft, run_worker

from database.research_db import ResearchExperiment, ResearchJob
from research.portfolio_coverage import prepare
from services import research_library as library
from services import research_study_continuation as continuation
from services import scanner_research_service as service
from services.research_storage import backup_store, restore_store


def setup(app, client, monkeypatch, sampler="grid", reserved=True):
    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda _store, _owner, evidence, **_: prepare(
            {**evidence, "snapshot": copy.deepcopy(inputs()["snapshot"]), "frozen_prices": True}
        ),
    )
    value = draft(client, True)
    value["optimization"].update(sampler=sampler, trials=2)
    value["portfolio"]["strategies"][0]["search"] = {"target_pct": {"min": 1, "max": 8, "step": 1}}
    if not reserved:
        value["portfolio"].pop("validation")
    experiment = create(client, value)
    parent = run(client, experiment)["job"]["id"]
    run_worker(store)
    job = service.get_job(store, OWNER, parent)
    assert job.status == "completed", job.error
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda *a, **k: pytest.fail("Continuation requested prices"),
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a, **k: pytest.fail("Continuation requested broker access"),
    )
    return store, experiment["id"], parent


def body(store, experiment, parent, additional=2, token="continue-once"):
    view = continuation.context(store, OWNER, experiment, parent)
    assert view["available"], view
    return {
        "revision": view["revision"],
        "parent_result_artifact": view["parent_result_artifact"],
        "additional_trials": additional,
        "request_id": token,
    }


@pytest.mark.parametrize("sampler,reserved", [("grid", True), ("tpe", False)])
def test_native_extension_is_linked_immutable_and_only_evaluates_new_work(
    app, client, monkeypatch, sampler, reserved
):
    store, experiment, parent = setup(app, client, monkeypatch, sampler, reserved)
    before = library.get_experiment(store, OWNER, experiment)
    parent_job = service.get_job(store, OWNER, parent)
    original = service.read_artifact(store, parent_job.result_artifact)
    with store.sessions() as db:
        checkpoint_id = db.get(ResearchExperiment, parent).checkpoint
    checkpoint = service.read_artifact(store, checkpoint_id)
    from research.connectors import vectorbt_portfolio

    evaluate = vectorbt_portfolio.evaluate
    calls = []

    def counted(*args, **kwargs):
        calls.append(args)
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", counted)
    request = body(store, experiment, parent)
    path = f"/scanner-research/api/library/experiments/{experiment}/studies/{parent}/continue"
    assert app.test_client().get(path).status_code == 401
    response = client.post(path, json=request)
    assert response.status_code == 200, response.json
    child = response.json["job"]["id"]
    assert child != parent and response.json["version"]["parent_job_id"] == parent
    assert response.json["version"]["draft"]["optimizing"] is True
    assert client.post(path, json=request).json["job"]["id"] == child
    assert library.get_experiment(store, OWNER, experiment)["draft"] == before["draft"]
    run_worker(store)
    job = service.get_job(store, OWNER, child)
    assert job.status == "completed", job.error
    result = service.read_artifact(store, job.result_artifact)["result"]
    assert result["experiment"]["counts"]["proposed"] == 4
    assert result["experiment"]["trials"][:2] == original["result"]["experiment"]["trials"]
    assert len(calls) == len(result["experiment"]["rows"]) - len(
        original["result"]["experiment"]["rows"]
    )
    assert result["evaluation_basis"] == original["result"]["evaluation_basis"]
    if reserved:
        assert result["reserved_evaluation"] == original["result"]["reserved_evaluation"]
        assert "validation" not in result
    assert result["study_continuation"]["parent_job_id"] == parent
    assert service.get_job(store, OWNER, parent).result_artifact == parent_job.result_artifact
    assert service.read_artifact(store, parent_job.result_artifact) == original
    assert service.read_artifact(store, checkpoint_id) == checkpoint
    # Continuing a continuation retains lineage and the parent's sampler state.
    next_request = body(store, experiment, child, token="continue-next")
    next_job = continuation.extend(store, OWNER, experiment, child, next_request)["job"]["id"]
    run_worker(store)
    assert service.get_job(store, OWNER, next_job).status == "completed"


def test_bounds_owner_revision_evidence_and_request_identity(app, client, monkeypatch):
    store, experiment, parent = setup(app, client, monkeypatch)
    request = body(store, experiment, parent)
    for additional in (True, 0, -1, 1.5, 1001, 7):
        with pytest.raises(ValueError):
            continuation.extend(
                store, OWNER, experiment, parent, {**request, "additional_trials": additional}
            )
    with pytest.raises(LookupError):
        continuation.context(store, "someone-else", experiment, parent)
    with pytest.raises(ValueError, match="changed"):
        continuation.extend(
            store, OWNER, experiment, parent, {**request, "parent_result_artifact": "a" * 64}
        )
    with pytest.raises(library.RevisionConflict):
        continuation.extend(store, OWNER, experiment, parent, {**request, "revision": 1})
    child = continuation.extend(store, OWNER, experiment, parent, request)["job"]["id"]
    assert continuation.extend(store, OWNER, experiment, parent, request)["job"]["id"] == child
    with pytest.raises(ValueError, match="request identity"):
        continuation.extend(store, OWNER, experiment, parent, {**request, "additional_trials": 3})
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == 2


def test_missing_checkpoint_is_actionable_and_does_not_launch(app, client, monkeypatch):
    store, experiment, parent = setup(app, client, monkeypatch)
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, parent).checkpoint = None
    view = continuation.context(store, OWNER, experiment, parent)
    assert not view["available"] and "no saved search checkpoint" in view["reason"]


def test_pinned_parent_checkpoint_is_in_backup_closure(app, client, monkeypatch, tmp_path_factory):
    store, experiment, parent = setup(app, client, monkeypatch)
    child = continuation.extend(store, OWNER, experiment, parent, body(store, experiment, parent))[
        "job"
    ]["id"]
    run_worker(store)
    # The child source pins the old checkpoint even after losing its mutable
    # metadata reference. Restore must keep the immutable continuation seed.
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, parent).checkpoint = None
    outside = tmp_path_factory.mktemp("continuation-backup")
    archive = outside / "backup"
    backup_store(store, archive)
    restored_path = outside / "restored"
    restore_store(archive, restored_path)
    from database.research_db import ResearchStore

    restored = ResearchStore(restored_path)
    try:
        restored.initialize()
        saved = service.get_job(restored, OWNER, child)
        assert saved.status == "completed", saved.error
        result = service.read_artifact(restored, saved.result_artifact)["result"]
        calculation, previous = continuation.seed(restored, result["study_continuation"])
        assert len(calculation["trials"]) == previous["trials"] == 2
    finally:
        restored.close()
