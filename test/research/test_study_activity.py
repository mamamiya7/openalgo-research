"""Durable actual study observations never replace scientific replay evidence."""

# ruff: noqa: F811 -- isolated shared Flask fixtures

import copy
import json
import time

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.pool import NullPool
from test_jobs import app, client  # noqa: F401
from test_portfolio_validation import inputs

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchStore,
    ResearchStudyExecution,
    ResearchStudyProposal,
    ResearchWorker,
)
from research.portfolio import execution_versions, normalize
from research.portfolio_coverage import prepare
from services import research_storage as storage
from services import research_study_activity as activity
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_storage import backup_store, inspect_storage, restore_store

OWNER = "research-test"
TOKEN = "study-activity-test"


def path(job_id):
    return f"/scanner-research/api/portfolio/jobs/{job_id}/activity"


def seed(app, *, optimize=True):
    store = app.extensions["research_store"]
    evidence = inputs()
    evidence["portfolio"].pop("validation")
    if optimize:
        evidence["portfolio"]["optimization"].update(sampler="grid", trials=3)
    else:
        evidence["portfolio"].pop("optimization")
    evidence["portfolio"] = normalize(evidence["portfolio"])
    evidence["strategies"] = [
        {**row, **evidence["portfolio"]["strategies"][index]}
        for index, row in enumerate(evidence["strategies"])
    ]
    evidence["versions"] = execution_versions(evidence["portfolio"])
    evidence["receipt"] = {
        "input_type": "portfolio",
        "signal_count": len(evidence["signals"]),
        "symbol_count": 1,
        "date_from": "2026-01-05",
        "date_to": "2026-01-14",
        "name": "Activity fixture",
        "warnings": [],
    }
    evidence = prepare(evidence)
    _, receipt = service.register_source(store, OWNER, evidence)
    job = service.submit(
        store,
        OWNER,
        receipt["id"],
        {"initial_capital": evidence["portfolio"]["capital"]},
        kind="portfolio_optimize" if optimize else "portfolio_backtest",
        specification={"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    )
    return store, job["id"]


def claimed(app, *, budget=3, replayed=0):
    store, job_id = seed(app)
    worker.acquire(store, TOKEN)
    with store.sessions.begin() as db:
        job = db.get(ResearchJob, job_id)
        job.status, job.worker = "running", TOKEN
    observer = activity.StudyObserver(store, TOKEN, job_id)
    observer({"kind": "search_started", "proposal_budget": budget, "replayed": replayed})
    return store, job_id, observer


def start(observer, number=0, *, reused=False):
    item = {
        "kind": "proposal_started",
        "number": number,
        "params": {"a.target_pct": number + 1},
        "config_id": f"{number:064x}",
        "reused": reused,
    }
    observer(item)
    return item


def complete(observer, number=0, *, reused=False, rejected=False):
    item = {
        "kind": "proposal_finished",
        "number": number,
        "state": "pruned" if rejected else "complete",
        "value": None if rejected else 1.25,
        "reused": reused,
    }
    observer(item)
    return item


def checkpoint(started, finished):
    return {
        "phase": "calculation",
        "calculation": {
            "trials": [
                {
                    **{key: value for key, value in started.items() if key != "kind"},
                    **{key: value for key, value in finished.items() if key != "kind"},
                }
            ]
        },
    }


def finalize(store, job_id, observer, state="completed", reason=None):
    with store.sessions.begin() as db:
        observer.finish(db, state, reason)
        db.get(ResearchJob, job_id).status = state
    worker.release(store, TOKEN)


def run_worker(store, token=TOKEN):
    worker.acquire(store, token)
    try:
        assert worker.run_one(store, token)
    finally:
        worker.release(store, token)


def test_old_and_not_yet_started_studies_have_no_invented_observations(app, client):
    store, job_id = seed(app)
    response = client.get(path(job_id))
    assert response.status_code == 200
    result = response.json
    assert result["version"] == activity.VERSION
    assert result["available"] is False and result["reason"] == "not_recorded"
    assert result["executions"] == result["rows"] == []
    assert result["counts"]["recorded"] == 0
    with store.sessions.begin() as db:
        db.get(ResearchJob, job_id).status = "failed"
    assert client.get(path(job_id)).json["rows"] == []


def test_owned_bounded_api_and_stable_cursor_counts(app, client):
    store, job_id, observer = claimed(app, budget=6)
    for number in range(6):
        start(observer, number, reused=number == 1)
        complete(observer, number, reused=number == 1, rejected=number == 2)
    first = client.get(path(job_id) + "?limit=2").json
    assert [row["number"] for row in first["rows"]] == [5, 4]
    assert first["counts"]["recorded"] == 6 and first["counts"]["evaluated"] == 4
    assert first["counts"]["reused"] == first["counts"]["allocation_rejected"] == 1
    assert "worker" not in first["executions"][0] and "owner" not in first["executions"][0]
    second = client.get(path(job_id) + f"?limit=2&before={first['next_before']}").json
    assert [row["number"] for row in second["rows"]] == [3, 2]
    assert second["counts"] == first["counts"]
    assert app.test_client().get(path(job_id)).status_code == 401
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    assert client.get(path(job_id)).status_code == 404
    finalize(store, job_id, observer)


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=2.5",
        "limit=true",
        "limit=1&limit=2",
        "before=-1",
        "before=0",
        "before=9223372036854775808",
        "execution=bad",
        "unexpected=1",
    ],
)
def test_activity_query_bounds(app, client, query):
    _, job_id = seed(app)
    assert client.get(path(job_id) + "?" + query).status_code == 400


def test_execution_filter_retains_selector_and_bounded_old_execution_context(app, client):
    store, job_id, observer = claimed(app, budget=1)
    old_id = observer.execution_id
    with store.sessions.begin() as db:
        observer.finish(db, "completed")
    for _ in range(21):
        observer = activity.StudyObserver(store, TOKEN, job_id)
        observer({"kind": "search_started", "proposal_budget": 1, "replayed": 1})
        with store.sessions.begin() as db:
            observer.finish(db, "completed")
    response = client.get(path(job_id) + f"?execution={old_id}").json
    assert response["executions_truncated"]
    assert len(response["executions"]) == 21
    assert old_id in {item["id"] for item in response["executions"]}
    assert response["rows"] == [] and response["counts"]["recorded"] == 0
    assert client.get(path(job_id) + "?execution=" + "a" * 32).status_code == 404
    finalize(store, job_id, observer)


def test_atomic_checkpoint_coverage_and_observed_finish_before_publication(app, client):
    store, job_id, observer = claimed(app)
    started, finished = start(observer), complete(observer)
    original = client.get(path(job_id)).json["rows"][0]
    assert original["state"] == "evaluated" and original["finished_at"] is not None
    assert original["checkpointed"] is False
    state = checkpoint(started, finished)
    artifact = service.save_artifact(store, {"state": state})
    with pytest.raises(RuntimeError, match="rollback"):
        with store.sessions.begin() as db:
            observer.checkpoint(db, state)
            db.get(ResearchExperiment, job_id).checkpoint = artifact
            raise RuntimeError("rollback")
    with store.sessions() as db:
        assert db.get(ResearchExperiment, job_id).checkpoint is None
    assert client.get(path(job_id)).json["rows"][0]["checkpointed"] is False
    with store.sessions.begin() as db:
        observer.checkpoint(db, state)
        db.get(ResearchExperiment, job_id).checkpoint = artifact
    assert client.get(path(job_id)).json["rows"][0]["checkpointed"] is True
    finalize(store, job_id, observer, "failed", "calculation_failed")
    after = client.get(path(job_id)).json
    assert after["rows"][0]["state"] == "evaluated"
    assert after["rows"][0]["finished_at"] == original["finished_at"]
    assert after["executions"][0]["state"] == "failed"


def test_mismatched_checkpoint_cannot_claim_recoverable_observation(app):
    store, job_id, observer = claimed(app)
    started, finished = start(observer), complete(observer)
    state = checkpoint(started, finished)
    state["calculation"]["trials"][0]["value"] = 2
    with pytest.raises(activity.ObservationFailed):
        with store.sessions.begin() as db:
            observer.checkpoint(db, state)
    assert activity.read(store, OWNER, job_id)["rows"][0]["checkpointed"] is False
    finalize(store, job_id, observer)


def test_fenced_successor_records_loss_without_invented_finish_or_stale_mutation(app, client):
    store, job_id, observer = claimed(app)
    start(observer)
    with store.sessions.begin() as db:
        db.get(ResearchWorker, 1).heartbeat = time.time() - 121
    worker.acquire(store, "successor")
    receipt = client.get(path(job_id)).json
    assert receipt["job_status"] == "interrupted"
    for row in (receipt["rows"][0], receipt["executions"][0]):
        assert row["state"] == "interrupted" and row["reason_code"] == "worker_lost"
        assert row["finished_at"] is None and row["observed_at"] >= row["started_at"]
    with pytest.raises(activity.ObservationLeaseLost):
        complete(observer)
    with pytest.raises(activity.ObservationLeaseLost):
        with store.sessions.begin() as db:
            observer.finish(db, "failed", "calculation_failed")
    worker.release(store, TOKEN)
    assert client.get(path(job_id)).json == receipt
    worker.release(store, "successor")


@pytest.mark.parametrize(
    "params", [{"a": True}, {"a": float("nan")}, {"a": "1"}, {"a": {}}, {"a" * 161: 1}]
)
def test_capture_rejects_unbounded_or_non_native_parameters(app, params):
    store, job_id, observer = claimed(app)
    with pytest.raises(activity.ObservationFailed):
        observer(
            {
                "kind": "proposal_started",
                "number": 0,
                "params": params,
                "config_id": "a" * 64,
                "reused": False,
            }
        )
    assert activity.read(store, OWNER, job_id)["rows"] == []
    finalize(store, job_id, observer)


def test_row_capacity_and_storage_scans_are_bounded(app, monkeypatch):
    calls = []
    monkeypatch.setattr(activity, "ensure_storage_capacity", lambda *args: calls.append(args[1]))
    store, job_id, observer = claimed(app)
    monkeypatch.setattr(activity, "MAX_METADATA_ROWS", 1)
    start(observer)
    complete(observer)
    with pytest.raises(activity.ObservationFailed, match="could not be recorded"):
        start(observer, 1)
    assert len(calls) == 1 and calls[0] <= 3 * (activity.MAX_PARAMS_BYTES + 4096) + 8192
    assert activity.read(store, OWNER, job_id)["counts"]["recorded"] == 1
    finalize(store, job_id, observer)


def test_native_worker_records_recoverable_proposals_and_exact_read_only_evidence(
    app, client, monkeypatch
):
    store, job_id = seed(app)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session", lambda *args: pytest.fail("No broker")
    )
    run_worker(store)
    job = service.get_job(store, OWNER, job_id)
    assert job.status == "completed", job.error
    original = client.get(f"/scanner-research/api/jobs/{job_id}/export").data
    result = client.get(path(job_id)).json
    assert result["executions"][0]["state"] == "completed"
    assert result["counts"]["recorded"] == 3
    assert all(row["state"] == "evaluated" and row["checkpointed"] for row in result["rows"])
    assert [row["number"] for row in result["rows"]] == [2, 1, 0]
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == original


def test_complete_checkpoint_resume_observes_no_new_proposals(app, client, monkeypatch):
    store, job_id = seed(app)
    original = worker.save_artifact

    def interrupt_before_publication(store, value):
        if isinstance(value, dict) and "job_id" in value and "result" in value:
            raise worker.Interrupted("controlled shutdown before result publication")
        return original(store, value)

    monkeypatch.setattr(worker, "save_artifact", interrupt_before_publication)
    run_worker(store)
    before = client.get(path(job_id)).json
    assert before["job_status"] == "interrupted"
    assert before["counts"]["recorded"] == 3
    assert all(row["checkpointed"] for row in before["rows"])
    monkeypatch.setattr(worker, "save_artifact", original)
    service.resume(store, OWNER, job_id)
    run_worker(store, "resume-native")
    again = client.get(path(job_id)).json
    assert again["job_status"] == "completed"
    assert again["executions"][0]["replayed"] == 3
    assert again["counts"]["recorded"] == 3 and len(again["executions"]) == 2


@pytest.mark.parametrize(
    "reason,expected,code",
    [
        ("failure", "failed", "calculation_failed"),
        ("cancel", "cancelled", "cancellation_requested"),
        ("shutdown", "interrupted", "worker_shutdown"),
    ],
)
def test_worker_closes_only_actual_open_proposal(app, client, monkeypatch, reason, expected, code):
    store, job_id = seed(app)

    def fail(*args, **kwargs):
        if reason == "cancel":
            service.cancel(store, OWNER, job_id)
            raise worker.Cancelled("controlled cancellation")
        if reason == "shutdown":
            raise worker.Interrupted("controlled shutdown")
        raise RuntimeError("private engine error must not enter activity")

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", fail)
    run_worker(store)
    result = client.get(path(job_id)).json
    assert result["job_status"] == expected
    assert result["counts"]["recorded"] == 1
    row = result["rows"][0]
    assert row["state"] == expected and row["reason_code"] == code
    assert row["finished_at"] is not None and row["value"] is None and not row["checkpointed"]
    assert "private engine" not in json.dumps(result)


def test_worker_capture_failure_is_not_engine_failure(app, client, monkeypatch):
    store, job_id = seed(app)
    original = activity.StudyObserver._record

    def fail(self, event):
        if event["kind"] == "proposal_finished":
            raise OSError("controlled observation write failure")
        return original(self, event)

    monkeypatch.setattr(activity.StudyObserver, "_record", fail)
    run_worker(store)
    result = client.get(path(job_id)).json
    assert result["job_status"] == "interrupted"
    assert result["executions"][0]["state"] == "observation_failed"
    assert result["rows"][0]["state"] == "interrupted"
    assert result["rows"][0]["reason_code"] == "observation_failed"
    assert result["rows"][0]["finished_at"] is None
    assert result["counts"]["failed"] == 0


@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
@pytest.mark.parametrize("outcome", ["evaluated", "reused", "allocation_rejected"])
def test_known_native_finish_precedes_simultaneous_stop_boundary(
    app, client, monkeypatch, stop, outcome
):
    store, job_id = seed(app)
    stopping = False

    def run(*args, observe=None, progress=None, **kwargs):
        nonlocal stopping
        reused = outcome == "reused"
        observe({"kind": "search_started", "proposal_budget": 3, "replayed": 0})
        observe(
            {
                "kind": "proposal_started",
                "number": 0,
                "config_id": "a" * 64,
                "params": {"a.target_pct": 1},
                "reused": reused,
            }
        )
        # Native completion has happened; cancellation wins only the following
        # checkpoint/progress boundary, never the already-known outcome.
        if stop == "cancel":
            service.cancel(store, OWNER, job_id)
        else:
            stopping = True
        observe(
            {
                "kind": "proposal_finished",
                "number": 0,
                "state": "pruned" if outcome == "allocation_rejected" else "complete",
                "value": None if outcome == "allocation_rejected" else 1.25,
                "reused": reused,
            }
        )
        progress(1, 3)
        pytest.fail("Stop was ignored at the following progress boundary")

    monkeypatch.setattr("services.research_portfolio.run", run)
    worker.acquire(store, TOKEN)
    try:
        worker.run_one(store, TOKEN, stop_requested=lambda: stopping)
    finally:
        worker.release(store, TOKEN)
    result = client.get(path(job_id)).json
    assert result["job_status"] == ("cancelled" if stop == "cancel" else "interrupted")
    row = result["rows"][0]
    assert row["state"] == outcome and row["reason_code"] is None
    assert row["finished_at"] is not None and row["checkpointed"] is False
    assert row["value"] == (None if outcome == "allocation_rejected" else 1.25)


@pytest.mark.parametrize("search_started", [False, True])
def test_failure_before_actual_proposal_never_fabricates_a_trial(
    app, client, monkeypatch, search_started
):
    store, job_id = seed(app)

    def fail(*args, observe=None, **kwargs):
        if search_started:
            observe({"kind": "search_started", "proposal_budget": 3, "replayed": 0})
        raise RuntimeError("controlled pre-proposal failure")

    monkeypatch.setattr("research.connectors.optuna_portfolio.run_search", fail)
    run_worker(store)
    result = client.get(path(job_id)).json
    assert result["job_status"] == "failed" and result["counts"]["recorded"] == 0
    assert result["rows"] == []
    assert len(result["executions"]) == int(search_started)


def test_native_failed_proposal_and_same_job_resume_retain_separate_attempts(
    app, client, monkeypatch
):
    from research.connectors import vectorbt_portfolio

    store, job_id = seed(app)
    original = vectorbt_portfolio.evaluate
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled second-proposal failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", fail_second)
    run_worker(store)
    before = client.get(path(job_id)).json
    assert before["job_status"] == "failed" and before["counts"]["failed"] == 1
    assert before["rows"][0]["number"] == 1 and before["rows"][1]["checkpointed"]
    monkeypatch.setattr(vectorbt_portfolio, "evaluate", original)
    service.resume(store, OWNER, job_id)
    run_worker(store, "native-failed-resume")
    result = client.get(path(job_id)).json
    assert result["job_status"] == "completed"
    assert result["executions"][0]["replayed"] == 1
    assert result["counts"]["recorded"] == 4 and result["counts"]["failed"] == 1
    assert result["counts"]["evaluated"] == 3
    assert sum(row["number"] == 0 for row in result["rows"]) == 1
    repeated = [row for row in result["rows"] if row["number"] == 1]
    assert len(repeated) == 2 and repeated[0]["config_id"] == repeated[1]["config_id"]


def test_native_finish_survives_checkpoint_publication_failure_then_exact_resume(
    app, client, monkeypatch
):
    store, job_id = seed(app)
    original = worker.save_artifact

    def fail(store, value):
        calculation = value.get("state", {}).get("calculation") if isinstance(value, dict) else None
        if calculation and calculation.get("trials"):
            raise OSError("controlled checkpoint publication failure")
        return original(store, value)

    monkeypatch.setattr(worker, "save_artifact", fail)
    run_worker(store)
    result = client.get(path(job_id)).json
    assert result["job_status"] == "failed"
    assert result["rows"][0]["state"] == "evaluated" and not result["rows"][0]["checkpointed"]
    assert result["counts"]["failed"] == 0
    monkeypatch.setattr(worker, "save_artifact", original)
    service.resume(store, OWNER, job_id)
    run_worker(store, "checkpoint-resume")
    result = client.get(path(job_id)).json
    assert result["job_status"] == "completed" and len(result["executions"]) == 2
    assert result["executions"][0]["replayed"] == 0
    assert result["counts"]["recorded"] == 4
    assert sum(row["checkpointed"] for row in result["rows"]) == 3


def test_backtest_does_not_create_optimization_execution(app, client):
    store, job_id = seed(app, optimize=False)
    run_worker(store)
    assert service.get_job(store, OWNER, job_id).status == "completed"
    with store.sessions() as db:
        assert db.scalar(select(ResearchStudyExecution.id).limit(1)) is None
    assert client.get(path(job_id)).status_code == 400


def test_populated_schema_upgrade_backup_restore_and_integrity(app, client, tmp_path_factory):
    store, job_id = seed(app)
    with store.engine.begin() as db:
        db.execute(text("DROP TABLE research_study_proposals"))
        db.execute(text("DROP TABLE research_study_executions"))
    store.initialize()
    assert {"research_study_executions", "research_study_proposals"}.issubset(
        inspect(store.engine).get_table_names()
    )
    assert service.get_job(store, OWNER, job_id).status == "queued"
    run_worker(store)
    before = copy.deepcopy(client.get(path(job_id)).json)
    tmp_path = tmp_path_factory.mktemp("study-activity-maintenance")
    backup_store(store, tmp_path / "activity-backup")
    restore_store(tmp_path / "activity-backup", tmp_path / "activity-restored")
    restored = ResearchStore(tmp_path / "activity-restored")
    try:
        restored.initialize()
        assert activity.read(restored, OWNER, job_id) == before
        with restored.sessions.begin() as db:
            db.get(ResearchStudyExecution, before["executions"][0]["id"]).owner = "foreign"
        with pytest.raises(ValueError, match="foreign metadata"):
            inspect_storage(restored)
    finally:
        restored.close()


def test_nullpool_connections_return_after_repeated_reads_and_errors(app, client):
    store, job_id, observer = claimed(app)
    start(observer)
    connections = {"active": 0, "peak": 0}

    def opened(*_):
        connections["active"] += 1
        connections["peak"] = max(connections.values())

    def closed(*_):
        connections["active"] -= 1

    event.listen(store.engine, "checkout", opened)
    event.listen(store.engine, "checkin", closed)
    try:
        assert isinstance(store.engine.pool, NullPool)
        for _ in range(100):
            assert client.get(path(job_id)).status_code == 200
            assert client.get(path(job_id) + "?execution=" + "f" * 32).status_code == 404
            assert connections["active"] == 0
        assert connections["peak"] == 1
    finally:
        event.remove(store.engine, "checkout", opened)
        event.remove(store.engine, "checkin", closed)
        finalize(store, job_id, observer, "interrupted", "worker_shutdown")


def test_maintenance_rejects_oversized_parameter_cells_and_row_limit(app, monkeypatch):
    store, job_id, observer = claimed(app)
    for number in range(2):
        start(observer, number)
        complete(observer, number)
    finalize(store, job_id, observer)
    with monkeypatch.context() as limited:
        limited.setattr(storage, "MAX_FILES", 1)
        with pytest.raises(ValueError, match="maintenance bound"):
            inspect_storage(store)
    with store.sessions.begin() as db:
        row = db.scalar(select(ResearchStudyProposal).limit(1))
        row.params = "x" * (activity.MAX_PARAMS_BYTES + 1)
    with pytest.raises(ValueError, match="proposal references invalid"):
        inspect_storage(store)


def test_maintenance_accepts_legacy_missing_tables_but_rejects_partial_upgrade(app):
    store, _ = seed(app)
    with store.engine.begin() as db:
        db.execute(text("DROP TABLE research_study_proposals"))
    with pytest.raises(ValueError, match="activity metadata tables are incomplete"):
        inspect_storage(store)
    with store.engine.begin() as db:
        db.execute(text("DROP TABLE research_study_executions"))
    assert inspect_storage(store)["database_bytes"] > 0
