"""The real queued worker preserves durable portfolio identity across resume.

The service handoff is stopped before price acquisition or account simulation;
these tests cover worker plumbing, not numerical optimization qualification.
"""

# ruff: noqa: F811 -- shared isolated Flask fixtures
from copy import deepcopy

import pytest
from test_automatic_workflow import request_and_prices
from test_jobs import app, client  # noqa: F401
from test_portfolio_workflow import portfolio

from database.research_db import ResearchExperiment
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def queued(client, monkeypatch, mode):
    if mode in ("fixed", "custom"):
        request = portfolio(client, optimize=mode == "custom")
    else:
        request = request_and_prices(client, monkeypatch)
        request["automatic_research"]["version"] = mode
    response = client.post("/scanner-research/api/portfolio/jobs", json={"portfolio": request})
    assert response.status_code == 202, response.json
    return response.json["id"]


def run_worker(store):
    token = "durable-portfolio-handoff"
    worker.acquire(store, token)
    try:
        assert worker.run_one(store, token)
    finally:
        worker.release(store, token)


@pytest.mark.parametrize(
    "mode", ["fixed", "custom", "automatic-hypotheses-v8", "automatic-hypotheses-v16"]
)
def test_worker_passes_own_job_identity_on_initial_and_resumed_handoff(
    app, client, monkeypatch, mode
):
    job_id = queued(client, monkeypatch, mode)
    store = app.extensions["research_store"]
    saved_state = {"phase": "handoff-check", "durable_job_id": job_id}
    calls = []

    def handoff(_store, owner, evidence, spec, **kwargs):
        assert _store is store and owner == "research-test"
        assert kwargs.get("job_id") == job_id
        assert kwargs["defer_study_analysis"] is True
        assert spec["portfolio"] == evidence["portfolio"]
        if not calls:
            assert kwargs["saved"] is None
            kwargs["checkpoint"](saved_state, {"completed": 0, "total": 1})
        else:
            assert kwargs["saved"] == saved_state
        calls.append(kwargs["job_id"])
        # Preserve a genuine worker checkpoint without inventing financial output.
        raise worker.Interrupted("Controlled handoff boundary")

    monkeypatch.setattr(research_portfolio, "run", handoff)
    run_worker(store)
    stopped = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert stopped["status"] == "interrupted" and stopped["resumable"], stopped
    with store.sessions() as db:
        experiment = db.get(ResearchExperiment, job_id)
        checkpoint_id = experiment.checkpoint
        receipt = service.read_artifact(store, checkpoint_id)
        assert receipt["identity"] == experiment.identity
        assert receipt["state"] == saved_state
    resumed = client.post(f"/scanner-research/api/jobs/{job_id}/resume")
    assert resumed.status_code == 200 and resumed.json["id"] == job_id, resumed.json
    run_worker(store)
    assert calls == [job_id, job_id]
    with store.sessions() as db:
        assert db.get(ResearchExperiment, job_id).checkpoint == checkpoint_id


def test_worker_rejects_wrong_checkpoint_identity_before_service_handoff(app, client, monkeypatch):
    job_id = queued(client, monkeypatch, "automatic-hypotheses-v16")
    store = app.extensions["research_store"]
    calls = []

    def interrupted(_store, _owner, _evidence, _spec, **kwargs):
        calls.append(kwargs.get("job_id"))
        assert calls == [job_id]
        kwargs["checkpoint"]({"phase": "handoff-check"}, {"completed": 0, "total": 1})
        raise worker.Interrupted("Controlled handoff boundary")

    monkeypatch.setattr(research_portfolio, "run", interrupted)
    run_worker(store)
    with store.sessions.begin() as db:
        experiment = db.get(ResearchExperiment, job_id)
        corrupted = deepcopy(service.read_artifact(store, experiment.checkpoint))
        corrupted["identity"] = "0" * 64
        experiment.checkpoint = service.save_artifact(store, corrupted)
    rejected = client.post(f"/scanner-research/api/jobs/{job_id}/resume")
    assert rejected.status_code == 400, rejected.json
    assert calls == [job_id]
    assert client.get(f"/scanner-research/api/jobs/{job_id}").json["status"] == "interrupted"


def test_pre_flag_v16_resume_uses_frozen_gates_without_rewriting_saved_inputs(app, monkeypatch):
    """Real stock gating and returned evidence stay exact without an account fit."""
    from test_portfolio_allocation_recipe import prepared

    from research.automatic_hypotheses import policy_for_version
    from research.joint_stock_market import apply, catalog
    from research.portfolio import fingerprint
    from research.research_mandate import build, verify
    from services.research_development_evidence import DevelopmentEvidenceWriter

    store = app.extensions["research_store"]
    value = prepared()
    value.pop("frozen_prices", None)
    original = deepcopy(value)
    artifact = service.save_artifact(store, value)
    original_bytes = service.encoded(value)
    mandate = build(value, inputs_artifact=artifact)
    writer = DevelopmentEvidenceWriter(
        store,
        value,
        binding={
            "inputs_artifact": artifact,
            "search_identity": fingerprint(
                {
                    "recipe": value["automatic_recipe"],
                    "stock": value["automatic_stock_market_context"],
                }
            ),
        },
        max_evaluations=policy_for_version("automatic-hypotheses-v16")["max_simulations"],
    )
    calculation = {"evaluations": {}}
    saved = {
        "phase": "calculation",
        "inputs_artifact": artifact,
        "calculation": calculation,
        "development_evidence": writer.snapshot(),
        "research_mandate": mandate,
    }
    validated = []
    checkpoints = []
    claims = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("Saved calculation inputs cannot reacquire prices or run an account")

    # This fixture already retains each context. Keep preparation leaves narrow;
    # the actual immutable-artifact, mandate, writer and stock-gate checks run.
    monkeypatch.setattr(research_portfolio, "_prepare_prices", forbidden)
    monkeypatch.setattr(research_portfolio, "_prepare_automatic_timing", forbidden)
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", forbidden)
    monkeypatch.setattr("services.research_sources.resolve_broker_session", forbidden)
    monkeypatch.setattr(
        "research.calendar_coverage.check_saved_calendar",
        lambda snapshot: validated.append(deepcopy(snapshot)),
    )
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("research.execution_features.prepare", lambda evidence, *_a: evidence)
    monkeypatch.setattr(
        "services.research_portfolio_features.prepare", lambda _store, evidence, **_kw: evidence
    )
    monkeypatch.setattr(
        "services.research_automatic_periods.claim",
        lambda _store, _owner, inputs_id, _evidence, job_id: claims.append((inputs_id, job_id)),
    )

    def at_stock_gate(evidence, **kwargs):
        assert evidence["frozen_prices"] is True
        assert kwargs["saved"] == calculation
        assert verify(mandate, evidence=evidence, inputs_artifact=artifact)
        _, receipt = apply(evidence, evidence["automatic_stock_market_context"], catalog()[0])
        assert sum(receipt["counts"].values()) == len(evidence["signals"])
        return {}  # Explicit plumbing sentinel; no financial report is fabricated.

    monkeypatch.setattr("research.automatic_walk_forward.run", at_stock_gate)
    result, returned = research_portfolio.run(
        store,
        "research-test",
        value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        saved=saved,
        checkpoint=lambda state, _counts: checkpoints.append(deepcopy(state)),
        progress=lambda *_a: None,
        cancelled=lambda: None,
        job_id="original-durable-job",
        defer_study_analysis=True,
    )
    assert validated == [original["snapshot"]]
    assert claims == [(artifact, "original-durable-job")]
    assert checkpoints and all(state["inputs_artifact"] == artifact for state in checkpoints)
    assert all(state["research_mandate"] == mandate for state in checkpoints)
    assert service.encoded(service.read_artifact(store, artifact)) == original_bytes
    assert service.encoded(returned) == original_bytes
    assert service.save_artifact(store, returned) == artifact
    assert result["research_mandate"] == mandate
    assert result["research_mandate"]["source"]["inputs_artifact"] == artifact
    assert "frozen_prices" not in returned
    assert value == original and "frozen_prices" not in value
