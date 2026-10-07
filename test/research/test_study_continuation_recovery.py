"""Native continuation recovery preserves committed work and frozen source evidence."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import copy

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from test_jobs import app, client
from test_library import OWNER
from test_report_period_workflow import run_worker
from test_study_continuation import body, setup

from blueprints.scanner_research import scanner_research_bp
from database.research_db import ResearchExperiment
from research.connectors import vectorbt_portfolio
from research.report_contract import fingerprint
from services import research_portfolio as portfolio_service
from services import research_study_continuation as continuation
from services import scanner_research_service as service
from services import scanner_research_worker as worker


@pytest.mark.parametrize("sampler", ["grid", "tpe"])
@pytest.mark.parametrize("boundary", ["frozen_inputs", "new_trial"])
def test_interrupted_child_resumes_committed_work_without_prices_or_parent_changes(
    app, client, monkeypatch, sampler, boundary
):
    # setup installs real VectorBT/Optuna over isolated synthetic prices, then
    # makes broker access and all subsequent price preparation fail the test.
    store, experiment, parent = setup(app, client, monkeypatch, sampler=sampler)
    parent_job = service.get_job(store, OWNER, parent)
    original = service.read_artifact(store, parent_job.result_artifact)
    with store.sessions() as db:
        original_checkpoint_id = db.get(ResearchExperiment, parent).checkpoint
    original_checkpoint = service.read_artifact(store, original_checkpoint_id)
    original_ids = {row["config_id"] for row in original["result"]["experiment"]["rows"]}
    calculated = []
    native_evaluate = vectorbt_portfolio.evaluate

    def evaluate_once(strategies, *args, **kwargs):
        settings = [
            {key: row[key] for key in ("id", "name", "allocation_pct", "config")}
            for row in strategies
        ]
        config_id = fingerprint(settings)
        assert config_id not in original_ids, "A parent's completed portfolio was recalculated"
        assert config_id not in calculated, "A child's committed portfolio was recalculated"
        calculated.append(config_id)
        return native_evaluate(strategies, *args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", evaluate_once)
    request = body(store, experiment, parent, additional=4)
    child = continuation.extend(store, OWNER, experiment, parent, request)["job"]["id"]
    native_run = portfolio_service.run
    stopped = []

    def stop_at_durable_boundary(*args, checkpoint, **kwargs):
        def persist_then_stop(state, counts):
            checkpoint(state, counts)
            calculation = state.get("calculation")
            reached = (
                calculation is None
                if boundary == "frozen_inputs"
                else isinstance(calculation, dict) and len(calculation["trials"]) == 3
            )
            if state.get("phase") == "calculation" and reached:
                stopped.append(copy.deepcopy(state))
                raise worker.Interrupted("controlled durable continuation boundary")

        return native_run(*args, checkpoint=persist_then_stop, **kwargs)

    monkeypatch.setattr(portfolio_service, "run", stop_at_durable_boundary)
    run_worker(store)
    paused = service.get_job(store, OWNER, child)
    assert paused.status == "interrupted", paused.error
    assert paused.result_artifact is None and len(stopped) == 1
    with store.sessions() as db:
        saved_id = db.get(ResearchExperiment, child).checkpoint
    durable = service.read_artifact(store, saved_id)["state"]
    assert durable == stopped[0]
    inputs = service.read_artifact(store, durable["inputs_artifact"])
    assert inputs["study_continuation"]["reference_artifact"] == original_checkpoint_id
    if boundary == "frozen_inputs":
        assert durable["calculation"] is None and not calculated
    else:
        assert len(durable["calculation"]["trials"]) == 3
        assert durable["calculation"]["specification"]["trials"] == 6

    monkeypatch.setattr(portfolio_service, "run", native_run)
    assert service.resume(store, OWNER, child)["status"] == "queued"
    run_worker(store)
    completed = service.get_job(store, OWNER, child)
    assert completed.status == "completed", completed.error
    result = service.read_artifact(store, completed.result_artifact)["result"]
    assert len(result["experiment"]["trials"]) == 6
    assert result["experiment"]["trials"][:2] == original["result"]["experiment"]["trials"]
    assert len(calculated) == len(result["experiment"]["rows"]) - len(original_ids)
    if boundary == "new_trial":
        assert result["experiment"]["trials"][:3] == durable["calculation"]["trials"]
    assert result["evaluation_basis"] == original["result"]["evaluation_basis"]
    assert result["reserved_evaluation"] == original["result"]["reserved_evaluation"]
    assert "validation" not in result
    assert service.get_job(store, OWNER, parent).result_artifact == parent_job.result_artifact
    assert service.read_artifact(store, parent_job.result_artifact) == original
    assert service.read_artifact(store, original_checkpoint_id) == original_checkpoint
    with store.sessions() as db:
        assert db.get(ResearchExperiment, parent).checkpoint == original_checkpoint_id


def test_fixed_space_tpe_cannot_admit_a_fake_extension(app, client, monkeypatch):
    import test_study_continuation as fixtures

    create = fixtures.create

    def create_fixed(client, value):
        fixed = copy.deepcopy(value)
        for row in fixed["portfolio"]["strategies"]:
            row["search"] = {}
        return create(client, fixed)

    monkeypatch.setattr(fixtures, "create", create_fixed)
    store, experiment, parent = setup(app, client, monkeypatch, sampler="tpe")
    view = continuation.context(store, OWNER, experiment, parent)
    assert view["current_proposed"] == view["max_total"] == 1
    assert view["available"] is False and "limit" in view["reason"]
    with pytest.raises(ValueError, match="at most 0"):
        continuation.extend(
            store,
            OWNER,
            experiment,
            parent,
            {
                "revision": view["revision"],
                "parent_result_artifact": view["parent_result_artifact"],
                "additional_trials": 1,
                "request_id": "fixed-space-no-extension",
            },
        )


def test_native_continuation_post_requires_csrf(app, client, monkeypatch):
    store, experiment, parent = setup(app, client, monkeypatch)
    protected = Flask(__name__)
    protected.config.update(
        SECRET_KEY="controlled-continuation-csrf",
        RESEARCH_DATA_DIR=str(store.root),
    )
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    browser = protected.test_client()
    with browser.session_transaction() as session:
        session["user"] = OWNER
    try:
        response = browser.post(
            f"/scanner-research/api/library/experiments/{experiment}/studies/{parent}/continue",
            json=body(store, experiment, parent),
        )
        assert response.status_code == 400 and b"csrf" in response.data.lower()
    finally:
        protected.extensions["research_store"].close()
