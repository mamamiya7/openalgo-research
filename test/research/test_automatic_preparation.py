# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Automatic scope preparation and immutable minute-policy integration, without network."""

import base64
import copy
import hashlib
import json

import pytest
from sqlalchemy import func, select
from test_intraday_engine import config, example
from test_jobs import app, client  # noqa: F401

from database.research_db import ResearchExperiment, ResearchJob
from research.data import validate_snapshot
from research.engine import validate_config
from research.intraday import POLICY_VERSION
from research.requirements import build_plan, normalize_request
from research.signals import normalize_csv
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def saved_source(app, *, minute=True, native=True, cfg=None, source_kind="broker"):
    signals, snapshot = example(12)
    snapshot["provenance"].update(
        synthetic=False,
        identity_verified=True,
        calendar_verified=True,
        adjustment_basis="raw",
        acquisition_mode="historify-first-v1",
    )
    reference = {
        "sessions": snapshot["sessions"],
        "session_hours": snapshot["session_hours"],
        "bars": {
            "AAA": {
                day: {"open": 100, "high": 101, "low": 99, "close": 100}
                for day in snapshot["sessions"]
            }
        },
        "provenance": {**snapshot["provenance"], "interval": "D"},
    }
    raw = (
        b"Timestamp,Symbol\n2026-01-05T09:15:00+05:30,AAA\n"
        if minute
        else b"Date,Symbol\n2026-01-05,AAA\n"
    )
    normalized = normalize_csv(raw)
    store = app.extensions["research_store"]
    request = {"config": validate_config(cfg or config(hold_minutes=1))}
    if minute:
        plan = build_plan(normalized["signals"], request, reference)
        snapshot["data_requirements"] = plan
        snapshot["required_timestamps"] = plan["required_timestamps"]
    else:
        snapshot = copy.deepcopy(reference)
    snapshot["coverage"] = validate_snapshot(snapshot, normalized["signals"])
    evidence = {
        **normalized,
        "snapshot": snapshot,
        "original_csv": raw.decode(),
        "original_csv_base64": base64.b64encode(raw).decode(),
        "original_csv_sha256": hashlib.sha256(raw).hexdigest(),
    }
    evidence["data_source_kind"] = source_kind
    if native:
        evidence["data_request"] = normalize_request(request)[0]
        evidence["reference_artifact"] = service.save_artifact(store, reference)
    _, receipt = service.register_source(store, "research-test", evidence)
    return store, receipt["id"], evidence


def preflight(client, source_id, cfg, kind="backtest", spec=None):
    return client.post(
        "/scanner-research/api/preflight",
        json={"source_id": source_id, "config": cfg, "kind": kind, "specification": spec or {}},
    )


def test_covered_settings_reuse_source_without_preparation(app, client, monkeypatch):
    store, source, evidence = saved_source(app)

    def forbidden(*a, **k):
        pytest.fail("Covered fixed inputs require no source preparation")

    monkeypatch.setattr("services.research_sources.resolve_broker_session", forbidden)
    monkeypatch.setattr(service, "create_source", forbidden)
    result = preflight(client, source, config(hold_minutes=2))
    assert result.status_code == 200, result.json
    assert result.json["policy_version"] == POLICY_VERSION
    assert "preparation_job" not in result.json
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == 0
    assert service.source_for(store, "research-test", source) == evidence


def test_wider_holding_scope_automatically_queues_once_preserving_source(app, client):
    store, source, evidence = saved_source(app)
    cfg = config(trade_horizon="multiday", hold_sessions=4)
    first = preflight(client, source, cfg)
    assert first.status_code == 200 and first.json["status"] == "preparing", first.json
    second = preflight(client, source, cfg)
    assert second.json["preparation_job"]["id"] == first.json["preparation_job"]["id"]
    assert service.source_for(store, "research-test", source) == evidence
    job = service.get_job(store, "research-test", first.json["preparation_job"]["id"])
    queued = service.source_for(store, "research-test", job.source_id)
    assert queued["data_request"]["config"]["hold_sessions"] == 4
    assert queued["original_csv_sha256"] == evidence["original_csv_sha256"]
    assert job.source_id != source


@pytest.mark.parametrize(
    "kind,spec",
    [
        ("sensitivity", {"variants": [{"name": "Timed", "changes": {"hold_minutes": 2}}]}),
        ("optimize", {"mode": "quick", "axes": {"hold_minutes": {"min": 1, "max": 3, "step": 1}}}),
    ],
)
def test_daily_legacy_source_temporal_experiment_requests_minute_preparation(
    app, client, kind, spec
):
    _, source, _ = saved_source(app, minute=False, native=False)
    response = preflight(client, source, {}, kind, spec)
    assert response.status_code == 200, response.json
    assert response.json.get("status") == "preparing", response.json


def test_minute_optimizer_checkpoint_followup_identity_and_old_artifact(app, client):
    store, source, _ = saved_source(app)
    cfg = config(hold_minutes=1)
    spec = {"mode": "quick", "budget": 1, "axes": {"hold_minutes": {"min": 1, "max": 3, "step": 1}}}
    checked = preflight(client, source, cfg, "optimize", spec)
    assert checked.status_code == 200 and checked.json["policy_version"] == POLICY_VERSION
    submitted = client.post(
        "/scanner-research/api/jobs",
        json={"source_id": source, "config": cfg, "kind": "optimize", "specification": spec},
    )
    assert submitted.status_code == 202, submitted.json
    parent_id = submitted.json["id"]
    worker.acquire(store, "automatic-test")
    try:
        assert worker.run_one(store, "automatic-test")
        parent = service.get_job(store, "research-test", parent_id)
        assert parent.status == "completed", parent.error
        bundle = service.read_artifact(store, parent.result_artifact)
        old = service.encoded(bundle)
        with store.sessions() as db:
            experiment = db.get(ResearchExperiment, parent_id)
            checkpoint = service.read_artifact(store, experiment.checkpoint)
            assert checkpoint["policy_version"] == POLICY_VERSION
            assert checkpoint["identity"] == experiment.identity
            specification = json.loads(experiment.specification)
            expected = hashlib.sha256(
                service.encoded(
                    {
                        "source_id": source,
                        "config": validate_config(cfg),
                        "kind": "optimize",
                        "specification": specification,
                        "policy_version": POLICY_VERSION,
                    }
                )
            ).hexdigest()
            assert expected == experiment.identity
        followup = bundle["result"]["experiment"]["follow_up"]
        followup["budget"] = 1
        next_job = client.post(
            "/scanner-research/api/jobs",
            json={
                "source_id": source,
                "config": cfg,
                "kind": "optimize",
                "specification": followup,
            },
        )
        assert next_job.status_code == 202, next_job.json
        worker.run_one(store, "automatic-test")
        later = service.get_job(store, "research-test", next_job.json["id"])
        assert later.status == "completed", later.error
        result = service.read_artifact(store, later.result_artifact)["result"]
        assert result["policy_version"] == POLICY_VERSION
        assert result["experiment"]["counts"]["evaluated_all_passes"] == 2
        assert service.encoded(service.read_artifact(store, parent.result_artifact)) == old
    finally:
        worker.release(store, "automatic-test")


def test_stored_only_expansion_preserves_source_mode_and_minute_admission_policy(app, client):
    store, source, _ = saved_source(app, source_kind="historify")
    response = preflight(client, source, config(trade_horizon="multiday", hold_sessions=4))
    assert response.json["status"] == "preparing", response.json
    job = service.get_job(store, "research-test", response.json["preparation_job"]["id"])
    queued = service.source_for(store, "research-test", job.source_id)
    assert queued["data_source_kind"] == "historify"
    assert queued["snapshot"]["provenance"]["acquisition_mode"] == "stored_only"
    assert queued["snapshot"]["provenance"]["interval"] == "1m"
    with store.sessions() as db:
        experiment = db.get(ResearchExperiment, job.id)
        expected = hashlib.sha256(
            service.encoded(
                {
                    "source_id": job.source_id,
                    "config": json.loads(job.config),
                    "kind": "acquire",
                    "specification": json.loads(experiment.specification),
                    "policy_version": POLICY_VERSION,
                }
            )
        ).hexdigest()
        assert expected == experiment.identity


def test_automatic_research_scope_covers_search_and_timing_variant_union(app, client):
    from research.requirements import build_plan

    base = config(trade_horizon="multiday", hold_sessions=1, hold_minutes=1)
    store, source, evidence = saved_source(app, cfg=base)
    spec = {
        "train_end": evidence["snapshot"]["sessions"][4],
        "test_end": evidence["snapshot"]["sessions"][-1],
        "gap_sessions": 0,
        "search": {
            "mode": "exhaustive",
            "axes": {"hold_sessions": {"min": 1, "max": 5, "step": 1}},
        },
        "variants": [{"name": "Uncapped minutes", "changes": {"hold_minutes": None}}],
    }
    response = preflight(client, source, base, "research", spec)
    assert response.json["status"] == "preparing", response.json
    job = service.get_job(store, "research-test", response.json["preparation_job"]["id"])
    queued = service.source_for(store, "research-test", job.source_id)
    reference = service.read_artifact(store, evidence["reference_artifact"])
    plan = build_plan(queued["signals"], queued["data_request"], reference)
    assert max(t[:10] for t in plan["required_timestamps"]["AAA"]) == reference["sessions"][5]
