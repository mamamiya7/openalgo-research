"""Requested reports retain exact study values, ownership and frozen inputs."""

# ruff: noqa: F811 -- isolated shared Flask fixtures

import copy
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import event, func, inspect, select
from sqlalchemy.pool import NullPool
from test_jobs import app, client  # noqa: F401
from test_library import OWNER, create
from test_portfolio_validation import inputs
from test_report_period_workflow import run_worker

from blueprints.scanner_research import scanner_research_bp
from database.research_db import (
    ResearchCandidateReport,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchStore,
)
from research.portfolio_coverage import prepare as prepare_coverage
from services import research_candidates as candidates
from services import scanner_research_service as service
from services.research_portfolio import run
from services.research_storage import backup_store, maintenance, restore_store


def path(job_id, config_id=None):
    base = f"/scanner-research/api/portfolio/jobs/{job_id}/candidates"
    return f"{base}/{config_id}/report" if config_id else base


@pytest.fixture(scope="module")
def frozen_studies(tmp_path_factory):
    """Real VBT + Optuna evidence, calculated once; every test has separate storage."""
    store = ResearchStore(tmp_path_factory.mktemp("candidate-native-evidence"))
    store.initialize()
    reports = {}
    try:
        for reserved in (True, False):
            evidence = inputs()
            evidence["portfolio"]["optimization"].update(sampler="grid", trials=3)
            if reserved:
                evidence["portfolio"]["validation"]["mode"] = "reserve"
            else:
                evidence["portfolio"].pop("validation")
            evidence["receipt"] = {
                "input_type": "portfolio",
                "signal_count": len(evidence["signals"]),
                "symbol_count": 1,
                "date_from": "2026-01-05",
                "date_to": "2026-01-14",
                "name": "Candidate test",
                "warnings": [],
            }
            evidence = prepare_coverage(evidence)
            result, frozen = run(
                store,
                OWNER,
                evidence,
                {"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
                checkpoint=lambda *_: None,
                progress=lambda *_: None,
                cancelled=lambda: None,
            )
            assert len(result["experiment"]["rows"]) == 3
            reports[reserved] = (frozen, result)
        return reports
    finally:
        store.close()


def seed(app, client, frozen_studies, reserved=True):
    store = app.extensions["research_store"]
    evidence, result = copy.deepcopy(frozen_studies[reserved])
    _, receipt = service.register_source(store, OWNER, evidence)
    submitted = service.submit(
        store,
        OWNER,
        receipt["id"],
        {"initial_capital": evidence["portfolio"]["capital"]},
        kind="portfolio_optimize",
        specification={"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    )
    artifact = service.save_artifact(
        store,
        {
            "kind": "portfolio_optimize",
            "inputs_artifact": service.save_artifact(store, evidence),
            "result": result,
        },
    )
    experiment = create(client)
    with store.sessions.begin() as db:
        parent = db.get(ResearchJob, submitted["id"])
        parent.status, parent.result_artifact = "completed", artifact
        db.add(
            ResearchLibraryJob(
                experiment_id=experiment["id"], job_id=parent.id, role="run", created_at=time.time()
            )
        )
    row = next(
        row
        for row in result["experiment"]["rows"]
        if row["config_id"] != result["experiment"]["recommendation_id"]
    )
    return parent.id, row, experiment["id"]


@pytest.mark.parametrize("reserved", [True, False])
def test_prepare_exact_native_candidate_once_without_search_or_download(
    app, client, frozen_studies, monkeypatch, reserved
):
    store = app.extensions["research_store"]
    parent, row, experiment = seed(app, client, frozen_studies, reserved)
    export = client.get(f"/scanner-research/api/jobs/{parent}/export").data
    status = client.get(path(parent)).json
    assert status["period"] == ("selection" if reserved else "full")
    assert len(status["candidates"]) == 3
    winner = next(item for item in status["candidates"] if item["is_objective_winner"])
    assert winner["status"] == "ready" and winner["report_job_id"] == parent
    assert client.post(path(parent, winner["config_id"]), json={}).json == winner
    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_sources.resolve_broker_session",
        "research.connectors.optuna_portfolio.run_search",
    ):
        monkeypatch.setattr(
            target, lambda *a, **k: pytest.fail("Candidate requested data or search")
        )
    response = client.post(path(parent, row["config_id"]), json={})
    assert response.status_code == 202, response.json
    queued = response.json
    assert queued["status"] == "queued"
    assert client.post(path(parent, row["config_id"]), json={}).json == queued
    run_worker(store)
    status = client.get(path(parent)).json
    candidate = next(item for item in status["candidates"] if item["config_id"] == row["config_id"])
    assert candidate["status"] == "ready", candidate
    report = client.get(f"/scanner-research/api/jobs/{candidate['report_job_id']}").json["result"]
    assert report["summary"] == row["summary"]
    assert report["report_context"]["candidate"]["config_id"] == row["config_id"]
    assert report["report_context"]["candidate"]["study_job_id"] == parent
    assert report["report_context"]["period"] == status["period"]
    assert report["candidate_report"]["verification"] == {"summary": "matched", "basis": "matched"}
    assert client.post(path(parent, row["config_id"]), json={}).json == candidate
    assert client.get(f"/scanner-research/api/jobs/{parent}/export").data == export
    library = client.get(f"/scanner-research/api/library/experiments/{experiment}").json
    link = next(job for job in library["jobs"] if job["id"] == candidate["report_job_id"])
    assert link["role"] == "candidate" and link["version_id"] is None
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchCandidateReport)) == 1


def test_concurrent_preparation_reuses_one_durable_job(app, client, frozen_studies):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(
            pool.map(lambda _: candidates.prepare(store, OWNER, parent, row["config_id"]), range(4))
        )
    assert len({item["report_job_id"] for item in receipts}) == 1
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == 2
        assert db.scalar(select(func.count()).select_from(ResearchCandidateReport)) == 1


@pytest.mark.parametrize("changed", ["summary", "basis", "statistics"])
def test_mismatch_is_explicit_and_original_export_is_unchanged(
    app, client, frozen_studies, monkeypatch, changed
):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    export = client.get(f"/scanner-research/api/jobs/{parent}/export").data
    from research.connectors.vectorbt_portfolio import evaluate
    from research.evaluation_basis import build_evaluation_basis

    def different_result(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        if changed == "summary":
            result["summary"]["net_return_pct"] += 0.0000001
        elif changed == "statistics":
            result["analysis"]["metrics"]["account_net_return_pct"] = 999
        return result

    def different_basis(*args, **kwargs):
        result = build_evaluation_basis(*args, **kwargs)
        result["prices_id"] = "0" * 64
        return result

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", different_result)
    if changed == "basis":
        monkeypatch.setattr("research.evaluation_basis.build_evaluation_basis", different_basis)
    queued = client.post(path(parent, row["config_id"]), json={}).json
    run_worker(store)
    receipt = client.post(path(parent, row["config_id"]), json={}).json
    assert receipt["status"] == "mismatch", receipt
    assert receipt["report_job_id"] == queued["report_job_id"]
    assert candidates.MISMATCH in receipt["error"]
    assert client.get(f"/scanner-research/api/jobs/{parent}/export").data == export
    assert service.get_job(store, OWNER, queued["report_job_id"]).result_artifact is None


def test_owner_auth_and_mutation_body_validation(app, client, frozen_studies):
    parent, row, _ = seed(app, client, frozen_studies)
    assert app.test_client().get(path(parent)).status_code == 401
    assert app.test_client().post(path(parent, row["config_id"]), json={}).status_code == 401
    with client.session_transaction() as session:
        session["user"] = "someone-else"
    assert client.get(path(parent)).status_code == 404
    assert client.post(path(parent, row["config_id"]), json={}).status_code == 404
    with client.session_transaction() as session:
        session["user"] = OWNER
    for payload in (None, [], {"period": "evaluation"}, {"config": {}}, {"owner": OWNER}):
        assert client.post(path(parent, row["config_id"]), json=payload).status_code == 400
    assert client.post(path(parent, "a" * 64), json={}).status_code == 400
    assert (
        client.post(
            path(parent, row["config_id"]),
            data='{"unexpected":"' + "x" * 2000 + '"}',
            content_type="application/json",
        ).status_code
        == 400
    )


def test_native_csrf(app, client, frozen_studies):
    parent, row, _ = seed(app, client, frozen_studies)
    protected = Flask("candidate-csrf")
    protected.config.update(
        SECRET_KEY="isolated", RESEARCH_DATA_DIR=str(app.extensions["research_store"].root)
    )
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)

    @protected.get("/csrf")
    def csrf():
        return {"token": generate_csrf()}

    try:
        browser = protected.test_client()
        with browser.session_transaction() as session:
            session["user"] = OWNER
        assert browser.post(path(parent, row["config_id"]), json={}).status_code == 400
        token = browser.get("/csrf").json["token"]
        assert (
            browser.post(
                path(parent, row["config_id"]), json={}, headers={"X-CSRFToken": token}
            ).status_code
            == 202
        )
    finally:
        protected.extensions["research_store"].close()


def test_archive_and_maintenance_prevent_new_preparations(app, client, frozen_studies):
    store = app.extensions["research_store"]
    parent, row, experiment = seed(app, client, frozen_studies)
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = True
    unavailable = client.post(path(parent, row["config_id"]), json={}).json
    assert unavailable["status"] == "unavailable" and "archive" in unavailable["error"]
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = False
    with maintenance(store):
        response = client.post(path(parent, row["config_id"]), json={})
        assert response.status_code == 400 and "maintenance" in response.json["message"]
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchCandidateReport)) == 0


def test_missing_frozen_inputs_has_specific_unavailable_state(app, client, frozen_studies):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    saved = service.get_job(store, OWNER, parent)
    bundle = service.read_artifact(store, saved.result_artifact)
    bundle.pop("inputs_artifact")
    changed = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, parent).result_artifact = changed
    response = client.post(path(parent, row["config_id"]), json={})
    assert response.status_code == 200
    assert response.json["status"] == "unavailable"
    assert "frozen price inputs" in response.json["error"]
    assert (
        next(
            item
            for item in client.get(path(parent)).json["candidates"]
            if item["is_objective_winner"]
        )["status"]
        == "ready"
    )


def test_populated_upgrade_reopen_backup_and_restore(app, client, frozen_studies, tmp_path):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    # Simulate the populated preceding release, then run the additive initializer.
    ResearchCandidateReport.__table__.drop(store.engine)
    store.initialize()
    assert "research_candidate_reports" in inspect(store.engine).get_table_names()
    queued = candidates.prepare(store, OWNER, parent, row["config_id"])
    run_worker(store)
    expected = candidates.prepare(store, OWNER, parent, row["config_id"])
    assert expected["status"] == "ready" and expected["report_job_id"] == queued["report_job_id"]
    reopened = ResearchStore(store.root)
    try:
        reopened.initialize()
        assert candidates.prepare(reopened, OWNER, parent, row["config_id"]) == expected
        backup_store(reopened, tmp_path.parent / (tmp_path.name + "-backup"))
    finally:
        reopened.close()
    backup = tmp_path.parent / (tmp_path.name + "-backup")
    restored_path = tmp_path.parent / (tmp_path.name + "-restored")
    restore_store(backup, restored_path)
    restored = ResearchStore(restored_path)
    try:
        restored.initialize()
        assert candidates.prepare(restored, OWNER, parent, row["config_id"]) == expected
    finally:
        restored.close()


def test_old_scalar_only_study_can_prepare_without_claiming_original_basis_verification(
    app, client, frozen_studies
):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    saved = service.get_job(store, OWNER, parent)
    bundle = service.read_artifact(store, saved.result_artifact)
    bundle["result"].pop("evaluation_basis")
    for trial in bundle["result"]["experiment"]["rows"]:
        trial.pop("analysis")
    changed = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, parent).result_artifact = changed
    receipt = candidates.prepare(store, OWNER, parent, row["config_id"])
    run_worker(store)
    child = service.get_job(store, OWNER, receipt["report_job_id"])
    assert child.status == "completed", child.error
    report = service.read_artifact(store, child.result_artifact)["result"]
    assert report["summary"] == row["summary"]
    assert report["candidate_report"]["verification"]["basis"] == "original-unverified"


def test_missing_engine_keeps_winner_readable_and_nonwinner_unavailable(
    app, client, frozen_studies, monkeypatch
):
    parent, row, _ = seed(app, client, frozen_studies)

    def unavailable(*args, **kwargs):
        raise ValueError("Install the recorded engine to prepare this report")

    monkeypatch.setattr("research.portfolio.execution_versions", unavailable)
    values = client.get(path(parent)).json["candidates"]
    assert next(item for item in values if item["is_objective_winner"])["status"] == "ready"
    receipt = client.post(path(parent, row["config_id"]), json={}).json
    assert receipt["status"] == "unavailable" and "recorded engine" in receipt["error"]


def test_full_queue_does_not_create_candidate_metadata(app, client, frozen_studies):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    parent_job = service.get_job(store, OWNER, parent)
    evidence = service.source_for(store, OWNER, parent_job.source_id)
    for number in range(4):
        service.submit(
            store,
            OWNER,
            parent_job.source_id,
            {"initial_capital": evidence["portfolio"]["capital"]},
            request_id=f"queue-slot-{number}",
            kind="portfolio_optimize",
            specification={"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
        )
    response = client.post(path(parent, row["config_id"]), json={})
    assert response.status_code == 400 and "queue is full" in response.json["message"]
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchCandidateReport)) == 0


def test_repeated_reads_and_idempotent_prepare_release_all_database_connections(
    app, client, frozen_studies
):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    assert isinstance(store.engine.pool, NullPool)
    balance = {"active": 0, "opened": 0}

    def checkout(*_):
        balance["active"] += 1
        balance["opened"] += 1

    def checkin(*_):
        balance["active"] -= 1

    event.listen(store.engine, "checkout", checkout)
    event.listen(store.engine, "checkin", checkin)
    try:
        receipt = candidates.prepare(store, OWNER, parent, row["config_id"])
        for _ in range(20):
            assert candidates.prepare(store, OWNER, parent, row["config_id"]) == receipt
            assert len(candidates.availability(store, OWNER, parent)["candidates"]) == 3
            assert balance["active"] == 0
        assert balance["opened"] > 20
    finally:
        event.remove(store.engine, "checkout", checkout)
        event.remove(store.engine, "checkin", checkin)


def test_archived_failed_candidate_cannot_resume_until_experiment_is_restored(
    app, client, frozen_studies, monkeypatch
):
    store = app.extensions["research_store"]
    parent, row, experiment = seed(app, client, frozen_studies)
    receipt = candidates.prepare(store, OWNER, parent, row["config_id"])
    from research.connectors.vectorbt_portfolio import evaluate

    def failed_engine(*args, **kwargs):
        raise ValueError("Test interruption after frozen-input checkpoint")

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", failed_engine)
    run_worker(store)
    child_id = receipt["report_job_id"]
    child = client.get(f"/scanner-research/api/jobs/{child_id}").json
    assert child["status"] == "failed" and child["resumable"]
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = True
    response = client.post(f"/scanner-research/api/jobs/{child_id}/resume")
    assert response.status_code == 400 and "archive" in response.json["message"]
    assert service.get_job(store, OWNER, child_id).status == "failed"
    assert candidates.prepare(store, OWNER, parent, row["config_id"])["report_job_id"] == child_id
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = False
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", evaluate)
    assert client.post(f"/scanner-research/api/jobs/{child_id}/resume").status_code == 200
    run_worker(store)
    assert service.get_job(store, OWNER, child_id).status == "completed"
    assert candidates.prepare(store, OWNER, parent, row["config_id"])["report_job_id"] == child_id
