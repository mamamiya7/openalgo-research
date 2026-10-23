"""Early acquisition uses the bounded ordinary worker, never financial history."""

# ruff: noqa: F811 -- shared isolated fixtures

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, select
from test_jobs import app, client  # noqa: F401
from test_library import BASE, OWNER, create, draft_for, run

from database.research_db import (
    ResearchExperiment,
    ResearchHistory,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchPreparation,
    ResearchSetupVersion,
    ResearchWorker,
)
from services import research_preparation as preparation
from services import scanner_research_service as service


def start(client, experiment):
    return client.post(
        f"{BASE}/experiments/{experiment['id']}/preparation",
        json={"revision": experiment["revision"]},
    )


def count(store, model):
    with store.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_landing_queues_once_without_version_history_or_draft_change(app, client):
    experiment = create(client, draft_for(client))
    store = app.extensions["research_store"]
    first = start(client, experiment)
    assert first.status_code == 202, first.json
    assert first.json["status"] == "queued" and not first.json["ready"]
    assert start(client, experiment).json == first.json
    assert client.get(f"{BASE}/experiments/{experiment['id']}/preparation").json == first.json
    with store.sessions() as db:
        row = db.get(ResearchLibraryExperiment, experiment["id"])
        assert (
            row.revision == experiment["revision"] and json.loads(row.draft) == experiment["draft"]
        )
        assert db.get(ResearchExperiment, first.json["job_id"]).kind == "portfolio_prepare"
    assert count(store, ResearchPreparation) == count(store, ResearchJob) == 1
    assert (
        count(store, ResearchSetupVersion)
        == count(store, ResearchLibraryJob)
        == count(store, ResearchHistory)
        == 0
    )


def test_concurrent_starts_share_single_current_pointer(app, client):
    experiment = create(client, draft_for(client))
    store = app.extensions["research_store"]
    barrier = Barrier(2)

    def begin():
        barrier.wait()
        return preparation.start(
            store, OWNER, experiment["id"], {"revision": experiment["revision"]}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: begin(), range(2)))
    assert responses[0]["job_id"] == responses[1]["job_id"]
    assert count(store, ResearchJob) == count(store, ResearchPreparation) == 1


def test_auth_owner_revision_preview_and_csrf(app, client, tmp_path):
    experiment = create(client, draft_for(client))
    url = f"{BASE}/experiments/{experiment['id']}/preparation"
    assert app.test_client().get(url).status_code == 401
    assert client.post(url, json={"revision": True}).status_code == 400
    assert client.post(url, json={"revision": 99}).status_code == 409
    assert client.post(url, json={"revision": 1, "unknown": "field"}).status_code == 400
    app.config["RESEARCH_PREVIEW"] = True
    assert start(client, experiment).status_code == 400
    app.config["RESEARCH_PREVIEW"] = False
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    assert client.get(url).status_code == client.post(url, json={"revision": 1}).status_code == 404
    protected = Flask("preparation-csrf")
    protected.config.update(SECRET_KEY="fixture", RESEARCH_DATA_DIR=str(tmp_path / "csrf"))
    CSRFProtect(protected)
    from blueprints.scanner_research import scanner_research_bp

    protected.register_blueprint(scanner_research_bp)
    try:
        assert protected.test_client().post(url, json={"revision": 1}).status_code == 400
    finally:
        protected.extensions["research_store"].close()


def test_queue_full_and_maintenance_are_fenced(app, client):
    store = app.extensions["research_store"]
    experiments = [create(client, draft_for(client)) for _ in range(5)]
    for item in experiments[:4]:
        assert start(client, item).status_code == 202
    assert start(client, experiments[4]).status_code == 400
    assert count(store, ResearchPreparation) == count(store, ResearchJob) == 4
    with store.sessions.begin() as db:
        db.get(ResearchWorker, 1).token = "maintenance:fixture"
    assert start(client, experiments[0]).status_code == 400


def test_changed_settings_cancel_obsolete_queued_and_replace_bounded_pointer(app, client):
    experiment = create(client, draft_for(client))
    first = start(client, experiment).json
    draft = copy.deepcopy(experiment["draft"])
    draft["portfolio"]["strategies"][0]["config"]["target_pct"] = 12
    response = client.put(
        f"{BASE}/experiments/{experiment['id']}/draft", json={"revision": 1, "draft": draft}
    )
    assert response.status_code == 200
    second = start(client, response.json)
    assert second.status_code == 202, second.json
    assert second.json["job_id"] != first["job_id"]
    store = app.extensions["research_store"]
    assert service.get_job(store, OWNER, first["job_id"]).status == "cancelled"
    assert count(store, ResearchPreparation) == 1
    assert count(store, ResearchSetupVersion) == count(store, ResearchLibraryJob) == 0


def completed(app, client, monkeypatch, *, optimize=False):
    from services import research_portfolio

    experiment = create(client, draft_for(client, optimize=optimize))
    prep = start(client, experiment).json
    store = app.extensions["research_store"]
    with store.sessions() as db:
        prep_job = db.get(ResearchJob, prep["job_id"])
        evidence = service.source_for(store, OWNER, prep_job.source_id)
        spec = json.loads(db.get(ResearchExperiment, prep_job.id).specification)
    inputs_id = service.save_artifact(store, {**evidence, "fixture_prepared": True})
    monkeypatch.setattr(preparation, "_footprint", lambda *_: {"archive": [1, 2, 3]})
    monkeypatch.setattr(preparation, "_fresh_calendars", lambda *_: True)
    monkeypatch.setattr(
        preparation, "_calendar_observation", lambda *_: {"sessions": ["2026-01-05"]}
    )
    monkeypatch.setattr(research_portfolio, "validate_prepared_inputs", lambda *_: None)
    result_id = service.save_artifact(
        store,
        {
            "schema_version": 2,
            "kind": preparation.KIND,
            "job_id": prep_job.id,
            "source_id": prep_job.source_id,
            "specification": spec,
            "inputs_artifact": inputs_id,
            "preparation": preparation.completed_receipt(store, evidence, evidence, spec),
        },
    )
    with store.sessions.begin() as db:
        row = db.get(ResearchJob, prep_job.id)
        row.status, row.result_artifact = "completed", result_id
    launched = run(client, experiment)
    return experiment, prep, store, evidence, spec, inputs_id, launched["job"]["id"]


def test_complete_matching_preparation_seeds_run_without_new_history(app, client, monkeypatch):
    _, _, store, evidence, spec, inputs_id, main = completed(app, client, monkeypatch)
    assert preparation.reuse_for_run(store, OWNER, main, evidence, spec) == {
        "phase": "calculation",
        "inputs_artifact": inputs_id,
        "calculation": None,
    }
    assert preparation.reuse_for_run(store, "other-owner", main, evidence, spec) is None
    assert count(store, ResearchHistory) == 0
    assert count(store, ResearchLibraryJob) == count(store, ResearchSetupVersion) == 1


@pytest.mark.parametrize(
    "change", ["footprint", "calendar", "validation", "spec", "source", "result", "pointer"]
)
def test_changed_native_or_saved_evidence_never_reuses(app, client, monkeypatch, change):
    from services import research_portfolio

    _, prep, store, evidence, spec, _, main = completed(app, client, monkeypatch)
    if change == "footprint":
        monkeypatch.setattr(preparation, "_footprint", lambda *_: {"archive": [9]})
    elif change == "calendar":
        monkeypatch.setattr(preparation, "_fresh_calendars", lambda *_: False)
    elif change == "validation":

        def invalid(*_):
            raise ValueError("receipt mismatch")

        monkeypatch.setattr(research_portfolio, "validate_prepared_inputs", invalid)
    else:
        with store.sessions.begin() as db:
            if change == "spec":
                db.get(ResearchExperiment, prep["job_id"]).specification = "{}"
            elif change == "source":
                db.get(ResearchJob, prep["job_id"]).source_id = "wrong-source"
            elif change == "result":
                db.get(ResearchJob, prep["job_id"]).result_artifact = "0" * 64
            else:
                pointer = db.scalar(select(ResearchPreparation))
                pointer.binding = "0" * 64
    assert preparation.reuse_for_run(store, OWNER, main, evidence, spec) is None


def test_failed_preparation_can_explicitly_retry_without_new_draft_version(app, client):
    experiment = create(client, draft_for(client))
    prep = start(client, experiment).json
    store = app.extensions["research_store"]
    with store.sessions.begin() as db:
        db.get(ResearchJob, prep["job_id"]).status = "failed"
    assert start(client, experiment).json["job_id"] != prep["job_id"]
    assert count(store, ResearchPreparation) == 1
    assert count(store, ResearchSetupVersion) == count(store, ResearchLibraryJob) == 0


def test_completed_landing_reuses_only_fresh_inputs(app, client, monkeypatch):
    experiment, prep, store, _, _, _, _ = completed(app, client, monkeypatch)
    current = client.get(f"{BASE}/experiments/{experiment['id']}").json
    assert start(client, current).json["job_id"] == prep["job_id"]
    monkeypatch.setattr(preparation, "_fresh_calendars", lambda *_: False)
    refreshed = start(client, current)
    assert refreshed.status_code == 202, refreshed.json
    assert refreshed.json["job_id"] != prep["job_id"]
    assert count(store, ResearchPreparation) == 1


def test_native_footprint_tracks_archive_wal_and_relevant_volume_sidecars(
    app, client, monkeypatch, tmp_path
):
    from services import research_sources
    from services.research_portfolio import resolve_inputs
    from services.research_volume_provenance import NativeVolumeProvenance

    store = app.extensions["research_store"]
    experiment = create(client, draft_for(client))
    evidence = resolve_inputs(
        store, OWNER, preparation.library.portfolio_payload(experiment["draft"])
    )
    evidence["strategies"][0]["execution_policy"] = {"capacity_mode": "prior20_broker_reported"}
    target = tmp_path / "native.duckdb"
    target.write_bytes(b"native")
    monkeypatch.setattr(
        research_sources, "acquisition_paths", lambda _: (target, tmp_path / "receipts")
    )
    baseline = preparation._footprint(store, evidence)
    target.with_suffix(".duckdb.wal").write_bytes(b"write-ahead")
    assert preparation._footprint(store, evidence) != baseline
    baseline = preparation._footprint(store, evidence)
    target.write_bytes(b"changed native archive")
    assert preparation._footprint(store, evidence) != baseline
    baseline = preparation._footprint(store, evidence)
    root = store.root / "volume-provenance"
    root.mkdir()
    archive_id = preparation.hashlib.sha256(service.encoded(str(target.resolve()))).hexdigest()
    cache = NativeVolumeProvenance(root, archive_id)
    symbol = evidence["signals"][0]["symbol"]
    cache._path(symbol, "D", "NSE").write_bytes(b"changed provenance")
    assert preparation._footprint(store, evidence) != baseline
    assert preparation._footprint(store, evidence) == preparation._footprint(store, evidence)


def test_native_calendar_recheck_groups_stock_windows_and_rejects_hours_changes(
    app, client, monkeypatch
):
    from services import research_native_calendar as calendars
    from services.research_portfolio import resolve_inputs

    store = app.extensions["research_store"]
    experiment = create(client, draft_for(client))
    evidence = resolve_inputs(
        store, OWNER, preparation.library.portfolio_payload(experiment["draft"])
    )
    sessions = ["2026-01-05", "2026-01-06"]
    execution = {
        "sessions": sessions,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in sessions},
        "provenance": {"warmup_sessions": 0},
    }
    stock = {**copy.deepcopy(execution), "provenance": {"warmup_sessions": 100}}
    market = {**copy.deepcopy(execution), "provenance": {"warmup_sessions": 160}}
    calls = []
    snapshot_calls = []

    def snapshot(signals, **kwargs):
        snapshot_calls.append(kwargs)
        assert signals == evidence["signals"]
        return copy.deepcopy(execution)

    def window(first, last, *, warmup_sessions):
        calls.append((first, last, warmup_sessions))
        return copy.deepcopy(stock if warmup_sessions == 100 else market)

    monkeypatch.setattr(calendars, "native_calendar_snapshot", snapshot)
    monkeypatch.setattr(calendars, "native_calendar_window", window)
    prepared = {
        **evidence,
        "snapshot": {"sessions": sessions},
        "reference_artifact": service.save_artifact(store, execution),
        "automatic_stock_market_context": {
            "series": {f"STOCK{i}": {"calendar": copy.deepcopy(stock)} for i in range(146)},
            "market": {"calendar": market},
        },
    }
    assert preparation._fresh_calendars(store, evidence, prepared)
    assert [row[2] for row in calls] == [100, 160]
    assert snapshot_calls[0]["warmup_sessions"] == 0
    assert snapshot_calls[0]["tail_sessions"] >= 1
    prepared["automatic_stock_market_context"]["series"]["STOCK145"]["calendar"]["session_hours"][
        sessions[0]
    ]["close"] = "13:00"
    assert not preparation._fresh_calendars(store, evidence, prepared)


def test_repeated_status_and_idempotent_start_release_every_database_connection(app, client):
    from sqlalchemy import event

    experiment = create(client, draft_for(client))
    store = app.extensions["research_store"]
    assert start(client, experiment).status_code == 202
    observed = {"opened": 0, "closed": 0, "live": 0, "maximum": 0}

    def opened(*_):
        observed["opened"] += 1
        observed["live"] += 1
        observed["maximum"] = max(observed["maximum"], observed["live"])

    def closed(*_):
        observed["closed"] += 1
        observed["live"] -= 1

    event.listen(store.engine, "connect", opened)
    event.listen(store.engine, "close", closed)
    try:
        for _ in range(100):
            assert preparation.status(store, OWNER, experiment["id"])["status"] == "queued"
        for _ in range(10):
            assert start(client, experiment).status_code == 202
    finally:
        event.remove(store.engine, "connect", opened)
        event.remove(store.engine, "close", closed)
    assert observed["opened"] >= 100
    assert observed["opened"] == observed["closed"] and observed["live"] == 0
    assert observed["maximum"] <= 2
    assert count(store, ResearchPreparation) == count(store, ResearchJob) == 1


@pytest.mark.parametrize("yield_once", [False, True])
def test_real_worker_prepares_without_accounts_and_next_queued_run_uses_saved_inputs(
    app, client, monkeypatch, tmp_path, yield_once
):
    from test_acquisition import reference
    from test_native_price_workflow import prepare_archive

    from services import research_portfolio
    from services import scanner_research_worker as worker

    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("Cached preparation must need no broker login"),
    )
    experiment = create(client, draft_for(client))
    prep = start(client, experiment).json
    launched = run(client, experiment)
    main = launched["job"]["id"]
    store = app.extensions["research_store"]
    original_run = research_portfolio.run
    original_prepare = research_portfolio._prepare_prices
    original_inputs = research_portfolio.prepare_inputs
    calls = []
    preparations = []

    def prepare(*args, **kwargs):
        preparations.append(True)
        if yield_once and len(preparations) == 1:
            from services.research_sources import AcquisitionBatchPending

            raise AcquisitionBatchPending()
        return original_inputs(*args, **kwargs)

    def no_account(*args, **kwargs):
        pytest.fail("Landing preparation must never run an account")

    monkeypatch.setattr(research_portfolio, "run", no_account)
    monkeypatch.setattr(research_portfolio, "prepare_inputs", prepare)
    monkeypatch.setattr("research.engine.evaluate", no_account)
    worker.acquire(store, "early-preparation-test")
    try:
        assert worker.run_one(store, "early-preparation-test")
        if yield_once:
            assert service.get_job(store, OWNER, prep["job_id"]).status == "queued"
            assert service.get_job(store, OWNER, main).status == "queued"
            assert worker.run_one(store, "early-preparation-test")
        prep_job = service.get_job(store, OWNER, prep["job_id"])
        assert prep_job.status == "completed", prep_job.error
        bundle = service.read_artifact(store, prep_job.result_artifact)
        assert bundle["schema_version"] == 2 and bundle["kind"] == preparation.KIND
        assert bundle["job_id"] == prep_job.id and bundle["source_id"] == prep_job.source_id
        assert bundle["result"] == {"prepared": True}
        assert bundle["preparation"]["freshness"]["archive"] is not None
        prepared = service.read_artifact(store, bundle["inputs_artifact"])
        assert prepared["frozen_prices"] is True
        assert count(store, ResearchHistory) == 0
        assert count(store, ResearchLibraryJob) == count(store, ResearchSetupVersion) == 1

        def observed_run(*args, **kwargs):
            calls.append(kwargs.get("saved"))
            return original_run(*args, **kwargs)

        monkeypatch.setattr(research_portfolio, "run", observed_run)
        monkeypatch.setattr(
            research_portfolio,
            "_prepare_prices",
            lambda *a, **kw: pytest.fail(
                "Fresh completed preparation must bypass repeated acquisition"
            ),
        )
        assert worker.run_one(store, "early-preparation-test")
        main_job = service.get_job(store, OWNER, main)
        assert main_job.status == "completed", main_job.error
        assert calls == [
            {
                "phase": "calculation",
                "inputs_artifact": bundle["inputs_artifact"],
                "calculation": None,
            }
        ]
        assert count(store, ResearchHistory) == 1
    finally:
        monkeypatch.setattr(research_portfolio, "_prepare_prices", original_prepare)
        worker.release(store, "early-preparation-test")


def test_existing_cancel_endpoint_and_experiment_delete_clean_preparation_pointer(app, client):
    experiment = create(client, draft_for(client))
    prep = start(client, experiment).json
    store = app.extensions["research_store"]
    response = client.post(f"/scanner-research/api/jobs/{prep['job_id']}/cancel")
    assert response.status_code == 200, response.json
    assert service.get_job(store, OWNER, prep["job_id"]).status == "cancelled"
    assert (
        client.delete(f"{BASE}/experiments/{experiment['id']}", json={"revision": 1}).status_code
        == 200
    )
    assert count(store, ResearchPreparation) == 0
    assert count(store, ResearchLibraryExperiment) == 0
    assert count(store, ResearchHistory) == count(store, ResearchSetupVersion) == 0


def test_unsupported_exact_reuse_keeps_fresh_cache_preparation_on_remount(app, client, monkeypatch):
    experiment, prep, store, evidence, spec, _, main = completed(
        app, client, monkeypatch, optimize=True
    )
    current = client.get(f"{BASE}/experiments/{experiment['id']}").json
    reused = start(client, current)
    assert reused.status_code == 202, reused.json
    assert reused.json["job_id"] == prep["job_id"]
    assert reused.json["ready"] is True and reused.json["reuse_mode"] == "cache"
    assert preparation.reuse_for_run(store, OWNER, main, evidence, spec) is None
    monkeypatch.setattr(
        preparation, "_calendar_observation", lambda *_: {"sessions": ["2026-01-05", "2026-01-06"]}
    )
    refreshed = start(client, current)
    assert refreshed.json["job_id"] != prep["job_id"]
    assert count(store, ResearchPreparation) == 1


def test_active_idempotent_start_does_not_republish_source(app, client, monkeypatch):
    experiment = create(client, draft_for(client))
    first = start(client, experiment)
    assert first.status_code == 202
    monkeypatch.setattr(
        service,
        "register_source",
        lambda *a, **kw: pytest.fail("Unchanged queued preparation must not republish its source"),
    )
    assert start(client, experiment).json["job_id"] == first.json["job_id"]


def test_changed_settings_then_immediate_run_cancels_obsolete_preparation(app, client):
    experiment = create(client, draft_for(client))
    prep = start(client, experiment).json
    draft = copy.deepcopy(experiment["draft"])
    draft["portfolio"]["strategies"][0]["config"]["target_pct"] = 12
    current = client.put(
        f"{BASE}/experiments/{experiment['id']}/draft", json={"revision": 1, "draft": draft}
    ).json
    launched = run(client, current)
    store = app.extensions["research_store"]
    assert service.get_job(store, OWNER, prep["job_id"]).status == "cancelled"
    assert service.get_job(store, OWNER, launched["job"]["id"]).status == "queued"


def test_cache_only_landing_rechecks_auxiliary_calendar_corrections(app, client, monkeypatch):
    experiment, prep, _, _, _, _, _ = completed(app, client, monkeypatch, optimize=True)
    current = client.get(f"{BASE}/experiments/{experiment['id']}").json
    monkeypatch.setattr(preparation, "_fresh_auxiliary_calendars", lambda *_: False)
    refreshed = start(client, current)
    assert refreshed.status_code == 202
    assert refreshed.json["job_id"] != prep["job_id"]
