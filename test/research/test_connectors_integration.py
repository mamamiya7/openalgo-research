"""Native API/worker integration with the real optional engines and saved data."""

import copy
import json
import threading
from datetime import date, timedelta
from io import BytesIO

import pytest
from flask import Flask

from blueprints.scanner_research import scanner_research_bp
from research.connectors import registry
from research.engine import config_defaults
from research.requirements import build_plan, normalize_request, select_interval
from services import scanner_research_service as service
from services import scanner_research_worker as worker


@pytest.fixture
def context(tmp_path):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-connectors", TESTING=True, RESEARCH_DATA_DIR=str(tmp_path))
    app.register_blueprint(scanner_research_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = "owner"
    source = client.post(
        "/scanner-research/api/sources",
        data={
            "source": "fixture",
            "file": (BytesIO(b"Date,Symbol\n2026-01-05,TEST\n"), "signals.csv"),
        },
    ).json
    store = app.extensions["research_store"]
    yield store, client, source
    store.close()


def test_capability_catalog_requires_owner(context):
    _, client, _ = context
    response = client.get("/scanner-research/api/connectors")
    assert response.status_code == 200
    assert response.json["engines"][0]["id"] == "scanner"
    assert response.json["engines"][2]["available"] is False
    with client.session_transaction() as session:
        session.clear()
    assert client.get("/scanner-research/api/connectors").status_code == 401


def test_missing_engine_rejected_before_preparation(context, monkeypatch):
    store, client, source = context
    monkeypatch.setattr(
        registry,
        "package_status",
        lambda name: {
            "available": False,
            "tested_version": registry.TESTED_PACKAGES[name],
            "installed_version": None,
        },
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("Data planning/download must not run for unavailable engines")

    monkeypatch.setattr(service, "data_preparation_needed", unexpected)
    response = client.post(
        "/scanner-research/api/preflight",
        json={
            "source_id": source["id"],
            "kind": "backtest",
            "config": {},
            "specification": {"execution": {"engine": "vectorbt"}},
        },
    )
    assert response.status_code == 400
    assert "requires vectorbt" in response.json["message"]
    assert service.list_jobs(store, "owner")["items"] == []


def test_unknown_engine_and_timed_vectorbt_fail_before_data(context, monkeypatch):
    _, client, source = context
    for execution, config in [
        ({"engine": "nautilus"}, {}),
        ({"engine": "vectorbt"}, {"trade_horizon": "intraday"}),
    ]:
        response = client.post(
            "/scanner-research/api/preflight",
            json={
                "source_id": source["id"],
                "kind": "backtest",
                "config": config,
                "specification": {"execution": execution},
            },
        )
        assert response.status_code == 400


def test_legacy_auto_keeps_existing_request_identity():
    cfg = config_defaults()
    assert registry.validate_capabilities("backtest", {}, cfg) == {}
    assert registry.validate_capabilities("backtest", {"execution": {"engine": "auto"}}, cfg) == {}


@pytest.mark.skipif(
    not all(registry.package_status(p)["available"] for p in registry.TESTED_PACKAGES),
    reason="Optional connectors not installed",
)
class TestInstalledConnectors:
    def test_worker_interruption_resumes_completed_optuna_trials(self, context, monkeypatch):
        store, client, source = context
        payload = {
            "source_id": source["id"],
            "kind": "optimize",
            "specification": {
                "execution": {"engine": "scanner", "optimizer": "optuna"},
                "axes": {"target_pct": {"min": 5, "max": 10, "step": 5}},
                "budget": 2,
            },
        }
        submitted = client.post("/scanner-research/api/jobs", json=payload)
        assert submitted.status_code == 202, submitted.json
        job_id = submitted.json["id"]
        actual = registry.evaluate
        completed = []

        def interrupt_after_first(signals, snapshot, config, **kwargs):
            if completed:
                raise worker.Interrupted("Controlled worker shutdown after checkpoint")
            report = actual(signals, snapshot, config, **kwargs)
            completed.append(config["target_pct"])
            return report

        monkeypatch.setattr(registry, "evaluate", interrupt_after_first)
        worker.acquire(store, "interrupted-connector")
        try:
            worker.run_one(store, "interrupted-connector")
        finally:
            worker.release(store, "interrupted-connector")
        partial = client.get(f"/scanner-research/api/jobs/{job_id}").json
        assert partial["status"] == "interrupted" and partial["resumable"]
        assert not any(t.name == "research-network-lease" for t in threading.enumerate())

        def remaining_only(signals, snapshot, config, **kwargs):
            assert config["target_pct"] not in completed
            report = actual(signals, snapshot, config, **kwargs)
            completed.append(config["target_pct"])
            return report

        monkeypatch.setattr(registry, "evaluate", remaining_only)
        assert client.post(f"/scanner-research/api/jobs/{job_id}/resume").status_code == 200
        worker.acquire(store, "resumed-connector")
        try:
            worker.run_one(store, "resumed-connector")
        finally:
            worker.release(store, "resumed-connector")
        final = client.get(f"/scanner-research/api/jobs/{job_id}").json
        assert final["status"] == "completed", final
        assert sorted(completed) == [5, 10]
        assert len(final["result"]["experiment"]["rows"]) == 2
        assert not any(t.name == "research-network-lease" for t in threading.enumerate())

    def test_queued_connector_rejects_runtime_drift(self, context, monkeypatch):
        store, client, source = context
        submitted = client.post(
            "/scanner-research/api/jobs",
            json={
                "source_id": source["id"],
                "specification": {"execution": {"engine": "vectorbt"}},
            },
        )
        assert submitted.status_code == 202
        monkeypatch.setattr(
            registry,
            "package_status",
            lambda name: {
                "available": False,
                "installed_version": None,
                "tested_version": registry.TESTED_PACKAGES[name],
            },
        )
        worker.acquire(store, "drift-test")
        try:
            worker.run_one(store, "drift-test")
        finally:
            worker.release(store, "drift-test")
        failed = client.get(f"/scanner-research/api/jobs/{submitted.json['id']}").json
        assert failed["status"] == "failed"
        assert "requires vectorbt" in failed["error"]
        assert not failed.get("result")
        assert not any(t.name == "research-network-lease" for t in threading.enumerate())

    def test_daily_candidate_union_and_early_matrix_limit(self):
        signals = [{"date": "2026-01-05", "symbol": "TEST", "row": 2}]
        request = {
            "kind": "optimize",
            "config": {},
            "specification": {
                "execution": {"engine": "vectorbt", "optimizer": "optuna"},
                "axes": {"hold_sessions": {"min": 1, "max": 7, "step": 2}},
                "budget": 2,
            },
        }
        assert select_interval(signals, request) == "D"
        canonical, configs = normalize_request(request)
        assert max(cfg["hold_sessions"] for cfg in configs) == 7
        assert canonical["specification"]["execution"]["engine_version"] == "0.28.5"
        days = [(date(2026, 1, 5) + timedelta(days=i)).isoformat() for i in range(1000)]
        with pytest.raises(ValueError, match="matrix limit"):
            build_plan(signals * 501, request, {"sessions": days})

    def test_all_trailing_choices_checked_before_acquisition(self):
        with pytest.raises(ValueError, match="positive trailing"):
            registry.validate_capabilities(
                "optimize",
                {
                    "execution": {"engine": "vectorbt", "optimizer": "optuna"},
                    "trailing_choices": [{"pct": 0, "enabled": True}],
                    "budget": 1,
                },
                config_defaults(),
            )

    @pytest.mark.timeout(240)
    @pytest.mark.parametrize(
        "engine,kind", [("vectorbt", "backtest"), ("vectorbt", "optimize"), ("scanner", "optimize")]
    )
    def test_upload_preflight_worker_reopen_export(self, context, engine, kind):
        store, client, source = context
        original = copy.deepcopy(service.source_for(store, "owner", source["id"]))
        spec = {
            "execution": {
                "engine": engine,
                "optimizer": "optuna" if kind == "optimize" else "native",
            }
        }
        if kind == "optimize":
            spec.update(axes={"target_pct": {"min": 5, "max": 10, "step": 5}}, budget=2)
        payload = {"source_id": source["id"], "config": {}, "kind": kind, "specification": spec}
        review = client.post("/scanner-research/api/preflight", json=payload)
        assert review.status_code == 200, review.json
        payload["specification"] = review.json["specification"]
        payload["request_id"] = "connector-integration-request"
        submitted = client.post("/scanner-research/api/jobs", json=payload)
        assert submitted.status_code == 202, submitted.json
        job_id = submitted.json["id"]
        assert client.post("/scanner-research/api/jobs", json=payload).json["id"] == job_id
        worker.acquire(store, "connector-test")
        try:
            assert worker.run_one(store, "connector-test")
        finally:
            worker.release(store, "connector-test")
        complete = client.get(f"/scanner-research/api/jobs/{job_id}").json
        assert complete["status"] == "completed", complete
        assert complete["result"]["execution"]["engine"] == engine
        if kind == "optimize":
            assert len(complete["result"]["experiment"]["rows"]) == 2
            assert complete["result"]["experiment"]["optimizer"]["id"] == "optuna"
        if engine == "vectorbt":
            assert complete["result"]["policy_version"] == "vectorbt-daily-next-open-v1"
        exported = client.get(f"/scanner-research/api/jobs/{job_id}/export")
        assert exported.status_code == 200
        assert json.loads(exported.data)["result"] == complete["result"]
        assert (
            client.get(f"/scanner-research/api/jobs/{job_id}").json["result"] == complete["result"]
        )
        assert service.source_for(store, "owner", source["id"]) == original
        with client.session_transaction() as session:
            session["user"] = "another"
        assert client.get(f"/scanner-research/api/jobs/{job_id}/export").status_code == 404

    def test_reviewed_version_change_cannot_run(self, context):
        store, client, source = context
        spec = registry.validate_capabilities("backtest", {"execution": {"engine": "vectorbt"}}, {})
        spec["execution"]["adapter_version"] = "different"
        response = client.post(
            "/scanner-research/api/jobs",
            json={
                "source_id": source["id"],
                "specification": spec,
            },
        )
        assert response.status_code == 400
        assert "version changed" in response.json["message"]
        assert service.list_jobs(store, "owner")["items"] == []
