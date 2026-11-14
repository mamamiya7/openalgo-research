"""Crash recovery of real Keep/save/reopen operations, without a new search.

The existing controlled account engine supplies the numerical fixture; the real
operating validator runs every frozen challenge. This is lifecycle evidence,
not a simulation of market edge or a replacement for numerical engine tests.
"""

# ruff: noqa: F811 -- shared isolated HTTP fixtures are injected by pytest

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from test_automatic_design import balanced
from test_jobs import app, client, source, submit  # noqa: F401 -- isolated HTTP fixtures
from test_operating_validation import Engine, audit, plan

from database.research_db import (
    ResearchChosenSetup,
    ResearchDecisionJob,
    ResearchExperiment,
    ResearchJob,
    ResearchJourney,
    ResearchStore,
    ResearchSupervisor,
    ResearchWorker,
)
from research.engine import config_defaults
from research.execution_policy import normalize_policy
from research.operating_validation import run as validate
from research.portfolio import normalize
from research.report_contract import fingerprint
from services import research_automation as automation
from services import research_mcp, research_notifications, research_supervisor_program
from services import research_supervisor as supervisor
from services import scanner_research_service as service
from services import scanner_research_worker as worker

OWNER, SUPERVISOR, JOB = "research-test", "c" * 32, "d" * 32
CONVERSATION = "controlled-finalization"


class ProcessDeath(BaseException):
    """Bypass ordinary Exception handling, as abrupt worker loss would."""


@pytest.fixture
def accepted(tmp_path):
    store = ResearchStore(tmp_path / "controlled-finalization")
    store.initialize()
    try:
        raw = "Date,Symbol\n" + "".join(
            f"{row['entry_date']},{row['symbol']}\n" for row in balanced()["ledger"]
        )
        source_row = service.create_source(store, OWNER, raw.encode(), "fixture")
        source_id = source_row["id"]
        policy = normalize_policy({})
        portfolio = normalize(
            {
                "name": "Controlled saved account",
                "engine": "vectorbt",
                "capital": 200000,
                "risk_limits": policy["risk_limits"],
                "strategies": [
                    {
                        "id": "scan",
                        "name": "Controlled signals",
                        "source_id": source_id,
                        "allocation_pct": 100,
                        "config": config_defaults(),
                        "execution_policy": policy,
                    }
                ],
            }
        )
        settings = deepcopy(portfolio["strategies"])
        nearby = []
        for target in (9, 11, 12, 13):
            rows = deepcopy(settings)
            rows[0]["config"]["target_pct"] = target
            nearby.append({"strategies": rows})
        frozen = plan(
            operating_point={"capital": portfolio["capital"], "settings": settings},
            baseline_settings=deepcopy(settings),
            neighbors=nearby,
        )
        accounts = Engine()
        selected = accounts(
            frozen["selected_settings"], frozen["inputs"]["hypothesis"], "search", False
        )
        recipe = {"version": "automatic-hypotheses-v16", "validation_mode": "exploratory_only"}
        selected.update(
            portfolio=portfolio,
            ledger=balanced()["ledger"],
            equity_curve=balanced()["equity_curve"],
            coverage={"date_from": "2026-03-01", "date_to": "2026-05-10"},
            automatic_recipe=recipe,
        )
        conclusion, checkpoint = validate(
            frozen,
            selected_report=selected,
            evaluate=accounts,
            audit=audit,
        )
        assert conclusion["outcome"] == "validated"
        assert len(accounts.calls) == 14 and len(checkpoint["reports"]) == 13
        assert all(row["passed"] for row in conclusion["diagnostics"])
        assert all(row["passed"] for row in conclusion["decision"]["checks"])
        selected = research_supervisor_program._validated_report(
            selected,
            conclusion,
            recipe,
            {"supervisor_id": SUPERVISOR, "proposal_id": "e" * 64},
            "f" * 64,
        )
        inputs = {"portfolio": portfolio, "strategies": settings}
        inputs_id = service.save_artifact(store, inputs)
        artifact = service.save_artifact(
            store,
            {
                "kind": "portfolio_backtest",
                "job_id": JOB,
                "inputs_artifact": inputs_id,
                "result": selected,
            },
        )
        now = time.time()
        state = {
            "version": supervisor.VERSION,
            "status": "accepted",
            "accepted_job_id": JOB,
            "saved_result": None,
            "proposals": [],
            "original_attempts": 14,
            "original_simulations": 14,
            "validation_mode": "exploratory_only",
            "automation": {
                "version": automation.VERSION,
                "calls_spent": 0,
                "max_model_calls": 3,
                "decisions": [],
                "reopened": None,
            },
        }
        with store.sessions.begin() as db:
            db.add(
                ResearchJob(
                    id=JOB,
                    owner=OWNER,
                    source_id=source_id,
                    config="{}",
                    status="completed",
                    progress=100,
                    created_at=now,
                    updated_at=now,
                    result_artifact=artifact,
                )
            )
            db.add(
                ResearchExperiment(
                    job_id=JOB,
                    kind="portfolio_backtest",
                    specification=json.dumps({"portfolio": portfolio}),
                    identity=fingerprint({"controlled": JOB}),
                    counts=json.dumps({"completed": 14}),
                )
            )
            db.add(
                ResearchSupervisor(
                    id=SUPERVISOR,
                    owner=OWNER,
                    root_job_id=JOB,
                    root_artifact=artifact,
                    conversation_id=CONVERSATION,
                    request_id="controlled-accepted-finalization",
                    state=json.dumps(state),
                    updated_at=now,
                )
            )
            db.add(
                ResearchDecisionJob(
                    supervisor_id=SUPERVISOR,
                    owner=OWNER,
                    phase="waiting",
                    updated_at=now,
                )
            )
            lease = db.get(ResearchWorker, 1)
            lease.token, lease.heartbeat = "before-crash", now
        yield SimpleNamespace(
            store=store, artifact=artifact, selected=selected, accounts=accounts, root=store.root
        )
    finally:
        store.close()


def test_process_death_after_save_recovers_exactly_without_another_account(accepted, monkeypatch):
    store = accepted.store
    original = supervisor.reopen_result
    save = supervisor.save_result

    def checked_save(*args, **kwargs):
        try:
            return save(*args, **kwargs)
        except Exception as error:
            pytest.fail(f"Native save did not reach the crash boundary: {error}")

    def die_before_reopening(*args, **kwargs):
        with store.sessions() as db:
            state = json.loads(db.get(ResearchSupervisor, SUPERVISOR).state)
            assert state["status"] == "saved" and state["saved_result"]["choice_id"]
            assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 1
        raise ProcessDeath()

    def forbidden(*args, **kwargs):
        pytest.fail("Finalization admitted a provider, price request or numerical account")

    monkeypatch.setattr(supervisor, "reopen_result", die_before_reopening)
    monkeypatch.setattr(supervisor, "save_result", checked_save)
    monkeypatch.setattr(service, "submit", forbidden)
    monkeypatch.setattr("services.agent.research_decision.select_decision", forbidden)
    monkeypatch.setattr("services.research_sources.resolve_broker_session", forbidden)
    try:
        automation.run_one(store, "before-crash", choose=forbidden)
    except ProcessDeath:
        pass
    else:
        pytest.fail(str(supervisor.status(store, OWNER, SUPERVISOR, conversation_id=CONVERSATION)))
    with store.sessions() as db:
        state = json.loads(db.get(ResearchSupervisor, SUPERVISOR).state)
        saved = deepcopy(state["saved_result"])
        assert db.get(ResearchDecisionJob, SUPERVISOR).phase == "finalizing"
        assert state["automation"]["reopened"] is None
    monkeypatch.setattr(supervisor, "reopen_result", original)
    # Dispose and reopen the isolated metadata store, rather than retaining an
    # identity-map/session from the interrupted worker.
    store.close()
    restarted = ResearchStore(accepted.root)
    restarted.initialize()
    try:
        with restarted.sessions.begin() as db:
            lease = db.get(ResearchWorker, 1)
            lease.token, lease.heartbeat = "after-restart", time.time()
        assert automation.run_one(restarted, "after-restart", choose=forbidden)
        finished = supervisor.status(restarted, OWNER, SUPERVISOR, conversation_id=CONVERSATION)
        assert finished["saved_result"] == saved
        assert finished["automation"]["calls_spent"] == 0
        assert finished["original_attempts"] == finished["original_simulations"] == 14
        assert finished["remaining_accounts"] == 498
        assert finished["automation"]["reopened"]["matched"] is True
        assert finished["automation"]["reopened"]["new_calculations"] == 0
        opened = research_mcp.reopen_chosen_result(
            restarted, OWNER, saved["experiment_id"], saved["choice_id"]
        )
        assert opened["verification"]["result_artifact"] == accepted.artifact
        assert (
            opened["verification"]["financial_report_id"]
            == saved["verification"]["financial_report_id"]
        )
        assert opened["verification"]["config_id"] == saved["verification"]["config_id"]
        assert opened["summary"] == accepted.selected["summary"]
        assert fingerprint(
            service.read_artifact(restarted, accepted.artifact)["result"]
        ) == fingerprint(accepted.selected)
        assert len(accepted.accounts.calls) == 14
        with restarted.sessions() as db:
            assert db.get(ResearchDecisionJob, SUPERVISOR).phase == "completed"
            assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 1
        assert not automation.run_one(restarted, "after-restart", choose=forbidden)
    finally:
        restarted.close()


def test_watching_notification_does_not_starve_an_actual_queued_job(app, client, monkeypatch):
    job_id = submit(client, source(client))
    store = app.extensions["research_store"]
    with store.sessions.begin() as db:
        db.add(
            ResearchJourney(
                experiment_id="a" * 32,
                owner=OWNER,
                phase="review",
                state=json.dumps({"start_request": "watching-another-study"}),
                updated_at=time.time(),
            )
        )
        research_notifications.register(db, OWNER, "a" * 32, "watching-another-study", ["slack"])
    monkeypatch.setattr(
        research_notifications,
        "send_native",
        lambda *a: pytest.fail("Active study sent a notification"),
    )
    worker.acquire(store, "queued-job-worker")
    try:
        assert worker.run_one(store, "queued-job-worker")
    finally:
        worker.release(store, "queued-job-worker")
    assert service.get_job(store, OWNER, job_id).status == "completed"
    with store.sessions() as db:
        notifications = research_notifications.status(db, OWNER, "a" * 32, "watching-another-study")
    assert len(notifications) == 1 and notifications[0]["status"] == "watching"
