"""Account presentation choices survive recovery without changing saved evidence."""

# ruff: noqa: F811 -- shared isolated fixtures

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import func, inspect, select
from sqlalchemy.pool import NullPool
from test_jobs import app, client, source, submit  # noqa: F401

from blueprints.scanner_research import scanner_research_bp
from database.research_db import ResearchJob, ResearchReportPreferences, ResearchStore
from services import research_preferences as preferences
from services import scanner_research_service as evidence_service
from services import scanner_research_worker as worker
from services.research_storage import backup_store, maintenance, restore_store

PATH = "/scanner-research/api/library/report-preferences"
OWNER = "research-test"


def save(client, changes, revision=0):
    return client.patch(PATH, json={"revision": revision, "changes": changes})


def test_default_read_creates_no_row_and_owner_choices_survive_new_session(app, client):
    store = app.extensions["research_store"]
    defaults = client.get(PATH)
    assert defaults.status_code == 200
    assert defaults.json == {
        "version": preferences.VERSION,
        "revision": 0,
        "preferences": preferences.default_preferences(),
        "updated_at": None,
    }
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchReportPreferences)) == 0
    changed = save(
        client,
        {
            "headline_metrics": ["account_net_return_pct", "vectorbt_sharpe_ratio"],
            "statistic_metrics": [],
            "performance_view": "equity",
            "log_equity": True,
            "rolling_window": 126,
            "expanded_sections": ["drawdowns", "engine_records"],
        },
    )
    assert changed.status_code == 200
    assert changed.json["revision"] == 1
    assert changed.json["updated_at"] > 0
    second = app.test_client()
    with second.session_transaction() as session:
        session["user"] = OWNER
    assert second.get(PATH).json == changed.json
    with second.session_transaction() as session:
        session["user"] = "other-account"
    assert second.get(PATH).json == defaults.json
    other = save(second, {"headline_metrics": ["nautilus_profit_factor"]})
    assert other.status_code == 200
    assert client.get(PATH).json == changed.json
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchReportPreferences)) == 2


def test_preferences_auth_and_native_csrf(app, tmp_path):
    anonymous = app.test_client()
    assert anonymous.get(PATH).status_code == 401
    assert save(anonymous, {"log_equity": True}).status_code == 401
    protected = Flask("report-preferences-csrf")
    protected.config.update(SECRET_KEY="isolated", RESEARCH_DATA_DIR=str(tmp_path / "csrf"))
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)

    @protected.get("/csrf")
    def csrf():
        return {"token": generate_csrf()}

    try:
        browser = protected.test_client()
        with browser.session_transaction() as session:
            session["user"] = OWNER
        assert browser.get(PATH).status_code == 200
        assert save(browser, {"log_equity": True}).status_code == 400
        token = browser.get("/csrf").json["token"]
        accepted = browser.patch(
            PATH,
            json={"revision": 0, "changes": {"log_equity": True}},
            headers={"X-CSRFToken": token},
        )
        assert accepted.status_code == 200
    finally:
        protected.extensions["research_store"].close()


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"revision": 0},
        {"revision": 0, "changes": {}, "owner": "other-account"},
        {"revision": True, "changes": {"log_equity": True}},
        {"revision": 0.0, "changes": {"log_equity": True}},
        {"revision": -1, "changes": {"log_equity": True}},
        {"revision": preferences.MAX_REVISION, "changes": {"log_equity": True}},
        {"revision": 0, "changes": {}},
        {"revision": 0, "changes": {"owner": "other-account"}},
        {"revision": 0, "changes": {"headline_metrics": []}},
        {"revision": 0, "changes": {"headline_metrics": [f"metric_{i}" for i in range(7)]}},
        {"revision": 0, "changes": {"headline_metrics": ["net_return", "net_return"]}},
        {"revision": 0, "changes": {"headline_metrics": "account_net_return_pct"}},
        {"revision": 0, "changes": {"headline_metrics": ["<script>alert(1)</script>"]}},
        {"revision": 0, "changes": {"headline_metrics": ["x" * 129]}},
        {"revision": 0, "changes": {"headline_metrics": [{"key": "net_return"}]}},
        {"revision": 0, "changes": {"headline_metrics": [False]}},
        {"revision": 0, "changes": {"statistic_metrics": [f"metric_{i}" for i in range(25)]}},
        {"revision": 0, "changes": {"statistic_metrics": ["x", "x"]}},
        {"revision": 0, "changes": {"performance_view": "log"}},
        {"revision": 0, "changes": {"performance_view": []}},
        {"revision": 0, "changes": {"log_equity": 1}},
        {"revision": 0, "changes": {"rolling_window": True}},
        {"revision": 0, "changes": {"rolling_window": 21.0}},
        {"revision": 0, "changes": {"rolling_window": 42}},
        {"revision": 0, "changes": {"expanded_sections": ["unknown"]}},
        {"revision": 0, "changes": {"expanded_sections": ["annual", "annual"]}},
        {"revision": 0, "changes": {"expanded_sections": {"annual": True}}},
    ],
)
def test_rejects_invalid_or_unbounded_choices_without_writing(client, body):
    before = client.get(PATH).json
    response = client.patch(PATH, data=json.dumps(body), content_type="application/json")
    assert response.status_code == 400
    assert client.get(PATH).json == before


@pytest.mark.parametrize("raw", [b"{broken", b"\xff", b"[" * 1200 + b"]" * 1200])
def test_malformed_or_overdeep_json_is_a_bounded_client_error(client, raw):
    assert client.patch(PATH, data=raw, content_type="application/json").status_code == 400


def test_transport_limits_and_full_supported_bounds(client):
    assert client.patch(PATH, data="preferences").status_code == 400
    assert (
        client.patch(
            PATH, data=b" " * (preferences.MAX_BODY_BYTES + 1), content_type="application/json"
        ).status_code
        == 400
    )
    changed = save(
        client,
        {
            "headline_metrics": [f"metric_{i}" for i in range(6)],
            "statistic_metrics": [f"metric_{i}" for i in range(24)],
            "expanded_sections": sorted(preferences.SECTIONS),
        },
    )
    assert changed.status_code == 200
    assert len(changed.json["preferences"]["statistic_metrics"]) == 24


def test_stale_revision_returns_latest_receipt_and_explicit_merge_preserves_other_changes(client):
    initial = save(client, {"log_equity": True})
    stale = save(client, {"rolling_window": 63})
    assert stale.status_code == 409
    assert stale.json["code"] == "report_preferences_conflict"
    assert stale.json["current"] == initial.json
    assert client.get(PATH).json == initial.json
    merged = save(client, {"rolling_window": 63}, stale.json["current"]["revision"])
    assert merged.status_code == 200
    assert merged.json["revision"] == 2
    assert merged.json["preferences"]["rolling_window"] == 63
    assert merged.json["preferences"]["log_equity"] is True


@pytest.mark.parametrize("initial_revision", [0, 1])
def test_simultaneous_initial_and_existing_writes_have_one_winner(app, initial_revision):
    store = app.extensions["research_store"]
    if initial_revision:
        preferences.update_preferences(
            store, OWNER, {"revision": 0, "changes": {"log_equity": True}}
        )
    gate = Barrier(2)

    def update_choice(window):
        gate.wait()
        try:
            return preferences.update_preferences(
                store, OWNER, {"revision": initial_revision, "changes": {"rolling_window": window}}
            )
        except preferences.PreferencesConflict as error:
            return {"conflict": error.current}

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(update_choice, (63, 126)))
    conflicts = [item["conflict"] for item in result if "conflict" in item]
    winners = [item for item in result if "conflict" not in item]
    assert len(conflicts) == len(winners) == 1
    assert conflicts[0] == winners[0]
    assert preferences.get_preferences(store, OWNER) == winners[0]
    assert winners[0]["revision"] == initial_revision + 1


def test_additive_initialization_reopen_and_backup_preserve_old_evidence(
    app, client, tmp_path_factory
):
    store = app.extensions["research_store"]
    source_id = source(client)
    job_id = submit(client, source_id)
    worker.acquire(store, "preferences-test")
    try:
        assert worker.run_one(store, "preferences-test")
    finally:
        worker.release(store, "preferences-test")
    original = client.get(f"/scanner-research/api/jobs/{job_id}/export").data
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        original_identity = (job.config, job.result_artifact, job.updated_at)
    # A populated pre-feature store has no preference table. Initialization only
    # adds that table; its earlier result and timestamps remain unchanged.
    ResearchReportPreferences.__table__.drop(store.engine)
    assert "research_report_preferences" not in inspect(store.engine).get_table_names()
    store.initialize()
    saved = save(client, {"performance_view": "equity", "rolling_window": 63}).json
    assert saved["revision"] == 1
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == original
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        assert (job.config, job.result_artifact, job.updated_at) == original_identity
    reopened = ResearchStore(store.root)
    try:
        reopened.initialize()
        assert isinstance(reopened.engine.pool, NullPool)
        assert preferences.get_preferences(reopened, OWNER) == saved
    finally:
        reopened.close()
    recovery_root = tmp_path_factory.mktemp("report-preferences-recovery")
    backup_store(store, recovery_root / "backup")
    restore_store(recovery_root / "backup", recovery_root / "restored")
    restored = ResearchStore(recovery_root / "restored")
    try:
        restored.initialize()
        assert preferences.get_preferences(restored, OWNER) == saved
        restored_job = evidence_service.get_job(restored, OWNER, job_id)
        assert restored_job.result_artifact == original_identity[1]
        bundle = evidence_service.read_artifact(restored, restored_job.result_artifact)
        bundle["inputs"] = evidence_service.read_artifact(restored, bundle["inputs_artifact"])
        assert evidence_service.encoded(bundle) == original
    finally:
        restored.close()


def test_maintenance_fence_rejects_preference_writes_but_allows_reads(app, client):
    store = app.extensions["research_store"]
    before = save(client, {"log_equity": True}).json
    with maintenance(store):
        assert client.get(PATH).json == before
        rejected = save(client, {"rolling_window": 63}, 1)
        assert rejected.status_code == 400
        assert "maintenance" in rejected.json["message"]
    assert client.get(PATH).json == before
    assert save(client, {"rolling_window": 63}, 1).status_code == 200
