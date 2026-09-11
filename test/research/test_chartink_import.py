"""Native Chartink intake: exact source, atomic library handoff and bounded retries."""

# ruff: noqa: F811 -- shared isolated fixtures

import base64
import copy
import gc
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import func, select
from test_jobs import app, client  # noqa: F401

from blueprints.scanner_research import scanner_research_bp
from database.research_db import (
    ResearchChartinkImport,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibrarySource,
    ResearchSource,
    ResearchSourceReceipt,
    ResearchStore,
    ResearchWorker,
)
from research.engine import config_defaults
from services import research_chartink as chartink
from services import research_library as library
from services import scanner_research_service as service
from services.research_storage import backup_store, inspect_storage, restore_store

URL = "/scanner-research/api/imports/chartink"
OWNER = "research-test"


def capture(**changes):
    value = {
        "version": 1,
        "request_id": str(uuid.uuid4()),
        "csv_text": "\ufeffDate,Symbol,Marketcapname,Sector\r\n22-01-2026,RELIANCE,Largecap,Energy\r\n23-01-2026,TCS,Largecap,IT\r\n",
        "source": {
            "url": "https://chartink.com/screener/demo-scanner",
            "title": "My Chartink scanner",
            "selected_period": "9 months",
            "captured_at": "2026-09-11T10:20:30.000Z",
            "repaints": None,
            "export_kind": "chartink_history_csv",
        },
    }
    value.update(changes)
    return value


def count(store, model):
    with store.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_import_opens_native_experiment_without_any_job_or_acquisition(app, client, monkeypatch):
    monkeypatch.setattr(
        service, "submit", lambda *a, **kw: pytest.fail("Import must not queue work")
    )
    data = capture()
    response = client.post(URL, json=data)
    assert response.status_code == 201, response.json
    receipt = response.json
    store = app.extensions["research_store"]
    assert receipt["protocol_version"] == 1 and receipt["reused"] is False
    assert receipt["url"] == f"/scanner-research?experiment={receipt['experiment_id']}&view=setup"
    assert receipt["receipt"]["receipt"]["chartink"] == data["source"]
    saved = library.get_experiment(store, OWNER, receipt["experiment_id"])
    assert saved["name"] == data["source"]["title"]
    draft = saved["draft"]
    strategy = draft["portfolio"]["strategies"][0]
    assert strategy["source_id"] == receipt["source_id"]
    assert strategy["allocation_pct"] == 100 and strategy["config"] == config_defaults()
    assert strategy["search"] == {} and draft["optimizing"] is False
    assert draft["optimization"] == {
        "sampler": "tpe",
        "objective": "balanced",
        "trials": 25,
        "seed": 0,
    }
    evidence = service.source_for(store, OWNER, receipt["source_id"])
    assert base64.b64decode(evidence["original_csv_base64"]) == data["csv_text"].encode("utf-8")
    assert evidence["snapshot"]["bars"] == {} and count(store, ResearchJob) == 0
    assert count(store, ResearchLibrarySource) == count(store, ResearchChartinkImport) == 1


def test_timestamps_and_distinct_same_day_signals_survive(app, client):
    response = client.post(
        URL,
        json=capture(
            csv_text="Date,Time,Symbol\n2026-01-22,09:15:10,RELIANCE\n2026-01-22,09:30:15,RELIANCE\n2026-01-22,09:30:15,RELIANCE\n"
        ),
    )
    assert response.status_code == 201, response.json
    evidence = service.source_for(
        app.extensions["research_store"], OWNER, response.json["source_id"]
    )
    assert [row["timestamp"] for row in evidence["signals"]] == [
        "2026-01-22T09:15:10+05:30",
        "2026-01-22T09:30:15+05:30",
    ]
    assert evidence["receipt"]["input_rows"] == 3 and evidence["receipt"]["duplicates_removed"] == 1
    assert evidence["snapshot"]["provenance"]["interval"] == "1m"


def test_retry_and_equivalent_capture_preserve_edited_draft(app, client):
    data = capture()
    first = client.post(URL, json=data).json
    store = app.extensions["research_store"]
    saved = library.get_experiment(store, OWNER, first["experiment_id"])
    saved["draft"]["portfolio"]["strategies"][0]["config"]["stop_pct"] = 8
    library.save_draft(
        store, OWNER, saved["id"], {"revision": saved["revision"], "draft": saved["draft"]}
    )
    retry = client.post(URL, json=data)
    assert retry.status_code == 200 and retry.json["reused"] is True
    data["request_id"] = str(uuid.uuid4())
    data["source"]["captured_at"] = "2026-09-11T11:20:30Z"
    equivalent = client.post(URL, json=data)
    assert equivalent.status_code == 200 and equivalent.json["experiment_id"] == saved["id"]
    assert (
        library.get_experiment(store, OWNER, saved["id"])["draft"]["portfolio"]["strategies"][0][
            "config"
        ]["stop_pct"]
        == 8
    )
    assert count(store, ResearchLibraryExperiment) == count(store, ResearchSource) == 1
    assert count(store, ResearchChartinkImport) == 2


def test_changed_payload_same_request_conflicts_before_saving(app, client):
    data = capture()
    assert client.post(URL, json=data).status_code == 201
    data["csv_text"] += "26-01-2026,INFY,Largecap,IT\n"
    response = client.post(URL, json=data)
    assert response.status_code == 409 and response.json["code"] == "chartink_import_conflict"
    assert count(app.extensions["research_store"], ResearchSource) == 1


def test_uuid_case_does_not_duplicate_the_capture(app, client):
    data = capture()
    data["request_id"] = data["request_id"].upper()
    first = client.post(URL, json=data)
    assert first.status_code == 201, first.json
    data["request_id"] = data["request_id"].lower()
    retry = client.post(URL, json=data)
    assert retry.status_code == 200
    assert retry.json["experiment_id"] == first.json["experiment_id"]
    assert count(app.extensions["research_store"], ResearchChartinkImport) == 1


def test_recapture_does_not_reuse_an_experiment_after_its_source_was_removed(app, client):
    data = capture()
    first = client.post(URL, json=data).json
    store = app.extensions["research_store"]
    parent = library.get_experiment(store, OWNER, first["experiment_id"])
    # Even a saved historical setup link does not make this the active input.
    frozen = library.save_version(store, OWNER, parent["id"], {"revision": 1, "name": "Original"})
    changed = copy.deepcopy(frozen["experiment"]["draft"])
    changed["portfolio"]["strategies"] = []
    changed["sources"] = {}
    before = library.save_draft(
        store,
        OWNER,
        parent["id"],
        {"revision": frozen["experiment"]["revision"], "draft": changed},
    )
    assert client.post(URL, json=data).json["experiment_id"] == parent["id"]
    data["request_id"] = str(uuid.uuid4())
    recaptured = client.post(URL, json=data)
    assert recaptured.status_code == 201, recaptured.json
    assert recaptured.json["experiment_id"] != parent["id"]
    assert recaptured.json["parent_experiment_id"] == parent["id"]
    assert library.get_experiment(store, OWNER, parent["id"]) == before


def test_changed_history_creates_related_child_without_overwriting_settings(app, client):
    data = capture()
    first = client.post(URL, json=data).json
    store = app.extensions["research_store"]
    parent = library.get_experiment(store, OWNER, first["experiment_id"])
    parent["draft"]["portfolio"]["strategies"][0]["config"]["stop_pct"] = 8
    library.save_draft(store, OWNER, parent["id"], {"revision": 1, "draft": parent["draft"]})
    before = library.get_experiment(store, OWNER, parent["id"])
    data["request_id"] = str(uuid.uuid4())
    data["csv_text"] += "27-01-2026,INFY,Largecap,IT\n"
    response = client.post(URL, json=data)
    assert response.status_code == 201 and response.json["parent_experiment_id"] == parent["id"]
    assert response.json["experiment_id"] != parent["id"]
    assert library.get_experiment(store, OWNER, parent["id"]) == before
    child = library.get_experiment(store, OWNER, response.json["experiment_id"])
    assert child["draft"]["portfolio"]["strategies"][0]["config"]["stop_pct"] == 8
    assert child["draft"]["portfolio"]["strategies"][0]["source_id"] != first["source_id"]


def test_archived_deleted_and_foreign_experiments_are_not_reused(app, client):
    data = capture()
    first = client.post(URL, json=data).json
    store = app.extensions["research_store"]
    library.update_experiment(
        store, OWNER, first["experiment_id"], {"revision": 1, "archived": True}
    )
    next_data = capture()
    second = client.post(URL, json=next_data).json
    assert first["experiment_id"] != second["experiment_id"]
    library.delete_experiment(store, OWNER, second["experiment_id"], {"revision": 1})
    assert client.post(URL, json=next_data).json["experiment_id"] != second["experiment_id"]
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    foreign = client.post(URL, json=data)
    assert foreign.status_code == 201 and foreign.json["source_id"] != first["source_id"]
    assert client.get(f"/scanner-research/api/sources/{first['source_id']}").status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "http://chartink.com/screener/test",
        "https://chartink.com.evil.test/screener/test",
        "https://evil.test/chartink.com/screener/test",
        "https://chartink.com@evil.test/screener/test",
        "https://chartink.com:443/screener/test",
        "https://chartink.com/screener/../test",
        "https://chartink.com/screener/test?other=1",
        "https://chartink.com/screener/test#token",
        "https://chartink.com/screener/%2e%2e",
        "https://chartink.com/dashboard/123",
    ],
)
def test_source_url_is_strictly_chartink_history_page(app, client, url):
    data = capture()
    data["source"]["url"] = url
    assert client.post(URL, json=data).status_code == 400
    assert count(app.extensions["research_store"], ResearchSource) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"request_id": "bad"},
        {"csv_text": 1},
        {"csv_text": "Symbol,Close\nRELIANCE,100\n"},
        {"extra": "unknown"},
        {"source": {}},
    ],
)
def test_invalid_schema_and_today_table_are_rejected(app, client, change):
    assert client.post(URL, json=capture(**change)).status_code == 400
    assert count(app.extensions["research_store"], ResearchChartinkImport) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("repaints", "false"),
        ("title", ""),
        ("selected_period", ""),
        ("captured_at", "2026-09-11T10:00:00"),
        ("export_kind", "chartink_current_csv"),
    ],
)
def test_invalid_source_metadata_is_rejected(client, field, value):
    data = capture()
    data["source"][field] = value
    assert client.post(URL, json=data).status_code == 400


def test_malformed_or_over_nested_transport_is_rejected_before_intake(app, client):
    for body in ("{", "[" * 2000 + "]" * 2000):
        assert client.post(URL, data=body, content_type="application/json").status_code == 400
    assert client.post(URL, data="not JSON").status_code == 400
    assert count(app.extensions["research_store"], ResearchChartinkImport) == 0


def test_size_storage_and_maintenance_bounds_leave_no_partial_metadata(app, client, monkeypatch):
    assert (
        client.post(URL, json=capture(csv_text="x" * (chartink.MAX_CSV_BYTES + 1))).status_code
        == 413
    )
    monkeypatch.setattr(chartink, "MAX_BODY_BYTES", 10)
    assert client.post(URL, json=capture()).status_code == 413
    monkeypatch.undo()
    store = app.extensions["research_store"]
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "0")
    assert client.post(URL, json=capture()).status_code == 400
    monkeypatch.delenv("RESEARCH_QUOTA_MB")
    with store.sessions.begin() as db:
        db.get(ResearchWorker, 1).token = "maintenance:test"
    assert client.post(URL, json=capture()).status_code == 400
    assert (
        count(store, ResearchSource)
        == count(store, ResearchLibraryExperiment)
        == count(store, ResearchChartinkImport)
        == 0
    )


def test_metadata_failure_rolls_back_source_experiment_and_request(app, client, monkeypatch):
    original = library._new_experiment

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated commit preparation failure")

    monkeypatch.setattr(library, "_new_experiment", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        client.post(URL, json=capture())
    store = app.extensions["research_store"]
    for model in (
        ResearchSource,
        ResearchSourceReceipt,
        ResearchLibraryExperiment,
        ResearchLibrarySource,
        ResearchChartinkImport,
    ):
        assert count(store, model) == 0
    assert inspect_storage(store)["orphan_files"] > 0


@pytest.mark.parametrize("same_request", [True, False])
def test_concurrent_imports_publish_one_experiment_and_source(app, same_request):
    store, gate, data = app.extensions["research_store"], Barrier(3), capture()

    def import_one(index):
        payload = copy.deepcopy(data)
        if not same_request:
            payload["request_id"] = str(uuid.uuid4())
        gate.wait()
        return chartink.import_capture(store, OWNER, payload)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(import_one, range(3)))
    assert len({row["experiment_id"] for row in results}) == 1
    assert sum(not row["reused"] for row in results) == 1
    assert count(store, ResearchSource) == count(store, ResearchLibraryExperiment) == 1
    assert count(store, ResearchChartinkImport) == (1 if same_request else 3)


def test_authentication_and_native_csrf_are_required(app, client, tmp_path):
    assert app.test_client().get(URL + "/capabilities").status_code == 401
    assert app.test_client().post(URL, json=capture()).status_code == 401
    assert client.get(URL + "/capabilities").json == {
        "protocol_version": 1,
        "max_csv_bytes": 8388608,
    }
    protected = Flask("chartink-csrf")
    protected.config.update(
        TESTING=True, SECRET_KEY="isolated-test-key", RESEARCH_DATA_DIR=str(tmp_path / "protected")
    )
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)

    @protected.get("/csrf")
    def token():
        return {"csrf": generate_csrf()}

    try:
        browser = protected.test_client()
        with browser.session_transaction() as session:
            session["user"] = OWNER
        assert browser.post(URL, json=capture()).status_code == 400
        csrf = browser.get("/csrf").json["csrf"]
        assert browser.post(URL, json=capture(), headers={"X-CSRFToken": csrf}).status_code == 201
    finally:
        protected.extensions["research_store"].close()


def test_backup_restore_and_parent_deletion_keep_native_graph_consistent(
    app, client, tmp_path_factory
):
    store, data = app.extensions["research_store"], capture()
    first = client.post(URL, json=data).json
    next_data = capture(csv_text=data["csv_text"] + "27-01-2026,INFY,Largecap,IT\n")
    second = client.post(URL, json=next_data).json
    root = tmp_path_factory.mktemp("chartink-backup")
    backup_store(store, root / "backup")
    restore_store(root / "backup", root / "restored")
    restored = ResearchStore(root / "restored")
    try:
        restored.initialize()
        retry = chartink.import_capture(restored, OWNER, data)
        assert retry["experiment_id"] == first["experiment_id"] and retry["reused"]
        library.delete_experiment(restored, OWNER, first["experiment_id"], {"revision": 1})
        assert chartink.import_capture(restored, OWNER, next_data)["parent_experiment_id"] is None
        inspect_storage(restored)
        assert library.get_experiment(restored, OWNER, second["experiment_id"])["draft"][
            "portfolio"
        ]["strategies"]
    finally:
        restored.close()


def test_repeated_acknowledgment_releases_database_handles(app):
    psutil = pytest.importorskip("psutil")
    store, data, process = app.extensions["research_store"], capture(), psutil.Process()
    chartink.import_capture(store, OWNER, data)
    gc.collect()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    before = handles()
    for _ in range(100):
        assert chartink.import_capture(store, OWNER, data)["reused"]
    gc.collect()
    assert handles() <= before + 5
    assert count(store, ResearchChartinkImport) == 1


def test_storage_rejects_import_with_foreign_source(app, client):
    data = capture()
    assert client.post(URL, json=data).status_code == 201
    store = app.extensions["research_store"]
    other = chartink.import_capture(store, "another-account", capture())
    with store.sessions.begin() as db:
        db.get(ResearchChartinkImport, (OWNER, data["request_id"])).source_id = other["source_id"]
    with pytest.raises(ValueError, match="Chartink import references missing or foreign research"):
        inspect_storage(store)


@pytest.mark.timeout(120)
def test_import_to_real_backtest_and_optuna_uses_native_prices_and_saves_exact_evidence(
    app, client, monkeypatch, tmp_path
):
    from test_acquisition import reference
    from test_native_price_workflow import prepare_archive

    from services import scanner_research_worker as worker

    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("Complete native Historify prices must not need a broker session"),
    )
    csv = "\ufeffDate,Symbol,Marketcapname,Sector\r\n05-01-2026,AAA,Largecap,IT\r\n"
    data = capture(csv_text=csv)
    imported = client.post(URL, json=data)
    assert imported.status_code == 201, imported.json
    store = app.extensions["research_store"]
    source_id, experiment_id = imported.json["source_id"], imported.json["experiment_id"]
    original_evidence = service.source_for(store, OWNER, source_id)
    assert count(store, ResearchJob) == 0
    endpoint = f"/scanner-research/api/library/experiments/{experiment_id}"
    experiment = client.get(endpoint).json
    draft = copy.deepcopy(experiment["draft"])
    strategy = draft["portfolio"]["strategies"][0]
    strategy_id = strategy["id"]
    # The controlled archive has Jan 5–7: use one-session holding, with prices
    # 100/102/98/101, to make the cash-account result independently checkable.
    strategy["config"].update(hold_sessions=1, order_size_pct=100, cost_bps=0)
    saved = client.put(endpoint + "/draft", json={"revision": 1, "draft": draft})
    assert saved.status_code == 200, saved.json
    preview = client.post(
        "/scanner-research/api/portfolio/preflight",
        json=library.portfolio_payload(saved.json["draft"]),
    )
    assert preview.status_code == 200 and preview.json["interval"] == "D", preview.json
    assert count(store, ResearchJob) == 0

    def run_saved(revision, request_id):
        launched = client.post(
            endpoint + "/run", json={"revision": revision, "request_id": request_id}
        )
        assert launched.status_code == 202, launched.json
        job_id = launched.json["job"]["id"]
        worker.acquire(store, "chartink-real-engine")
        try:
            assert worker.run_one(store, "chartink-real-engine")
        finally:
            worker.release(store, "chartink-real-engine")
        completed = client.get(f"/scanner-research/api/jobs/{job_id}").json
        assert completed["status"] == "completed", completed
        assert completed["result"]["execution"]["engine"] == "vectorbt"
        assert completed["result"]["source"]["interval"] == "D"
        exported = client.get(f"/scanner-research/api/jobs/{job_id}/export")
        assert exported.status_code == 200
        bundle = json.loads(exported.data)
        frozen = bundle["inputs"]["strategies"][0]
        assert base64.b64decode(frozen["original_csv_base64"]) == csv.encode("utf-8")
        assert frozen["original_csv_sha256"] == hashlib.sha256(csv.encode("utf-8")).hexdigest()
        assert frozen["source_receipt"]["chartink"] == data["source"]
        assert (
            bundle["inputs"]["snapshot"]["provenance"]["native_price_policy"]
            == "openalgo-native-history-v1"
        )
        return job_id, completed["result"], exported.data

    backtest_id, backtest, backtest_export = run_saved(saved.json["revision"], "chartink-backtest")
    assert backtest["summary"]["closed_trades"] == 1
    assert backtest["summary"]["final_equity"] == pytest.approx(101000)
    assert {trade["strategy_id"] for trade in backtest["ledger"]} == {strategy_id}

    reopened = client.get(endpoint).json
    study_draft = copy.deepcopy(reopened["draft"])
    study_draft["optimizing"] = True
    study_draft["optimization"] = {
        "sampler": "tpe",
        "trials": 3,
        "objective": "balanced",
        "seed": 0,
    }
    study_draft["portfolio"]["strategies"][0]["search"] = {
        "target_pct": {"min": 1, "max": 3, "step": 1}
    }
    study_setup = client.put(
        endpoint + "/draft",
        json={"revision": reopened["revision"], "draft": study_draft},
    )
    assert study_setup.status_code == 200, study_setup.json
    study_id, result, study_export = run_saved(study_setup.json["revision"], "chartink-study")
    assert result["experiment"]["optimizer"]["sampler"] == "TPESampler"
    assert len(result["experiment"]["trials"]) == 3
    assert all(trial["state"] == "complete" for trial in result["experiment"]["trials"])
    assert 1 <= len(result["experiment"]["rows"]) <= 3

    library_record = client.get(endpoint).json
    assert {job["id"] for job in library_record["jobs"]} == {backtest_id, study_id}
    assert len(library_record["versions"]) == 2
    assert library_record["draft"]["portfolio"]["strategies"][0]["source_id"] == source_id
    studies = client.get("/scanner-research/api/library/studies").json["items"]
    assert [study["id"] for study in studies] == [study_id]
    assert service.source_for(store, OWNER, source_id) == original_evidence
    assert client.get(f"/scanner-research/api/jobs/{backtest_id}/export").data == backtest_export
    assert client.get(f"/scanner-research/api/jobs/{study_id}/export").data == study_export
    assert client.post(URL, json=data).json["experiment_id"] == experiment_id
    assert client.get(endpoint).json == library_record
