# ruff: noqa: F811 -- shared isolated native Flask fixtures
"""Upload, worker, real VectorBT/Optuna, frozen context and exact follow-up."""

from copy import deepcopy

import pytest
from test_automatic_conditions import prepared
from test_automatic_workflow import calculate, request_and_prices
from test_jobs import app, client

from research.automatic_protocol import CONDITION_VERSION
from services import research_entry_conditions, research_portfolio
from services import scanner_research_service as service

pytestmark = pytest.mark.timeout(600)


def test_native_condition_research_uses_frozen_context_then_exact_offline_replay(app, client, monkeypatch):
    request = request_and_prices(client, monkeypatch)
    actual_prices = research_portfolio._prepare_prices
    actual_context = research_entry_conditions.prepare_context
    request["automatic_research"] = {"version": CONDITION_VERSION}
    context = prepared()["entry_condition_market"]
    calls = []

    def market(store, owner, value, **kwargs):
        calls.append(1)
        return {**value, "entry_condition_market": deepcopy(context)}

    monkeypatch.setattr(research_entry_conditions, "prepare_context", market)
    response = client.post("/scanner-research/api/portfolio/jobs", json={
        "portfolio": request, "request_id": "conditional-native-parent",
    })
    assert response.status_code == 202, response.json
    job_id = response.json["id"]
    store = app.extensions["research_store"]
    calculate(store, "conditional-native-parent")
    completed = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert completed["status"] == "completed", completed
    result = completed["result"]
    assert result["automatic_research"]["version"] == CONDITION_VERSION
    assert result["automatic_research"]["counts"]["proposals"] == 50
    assert result["automatic_research"]["counts"]["condition_candidates"] <= 16
    assert result["automatic_research"]["counts"]["simulations"] <= 100
    assert result["execution"]["engine"] == "vectorbt"
    assert result["experiment"]["optimizer"]["version"] == "5.0.0"
    original = client.get(f"/scanner-research/api/jobs/{job_id}/export").data
    assert calls == [1]

    def forbidden(*args, **kwargs):
        raise AssertionError("Exact replay must not prepare prices or market context")

    monkeypatch.setattr(research_portfolio, "_prepare_prices", forbidden)
    monkeypatch.setattr(research_entry_conditions, "prepare_context", actual_context)
    from services import research_regimes

    monkeypatch.setattr(research_regimes, "run_context", forbidden)
    # No-filter fallback is exact too; if selected gates exist, service checks
    # the already frozen context without calling an acquisition service.
    replay = research_portfolio.rerun(store, "research-test", job_id, request_id="conditional-fixed")
    calculate(store, "conditional-fixed")
    replayed = service.get_job(store, "research-test", replay["id"])
    assert replayed.status == "completed", replayed.error
    fixed = service.read_artifact(store, replayed.result_artifact)["result"]
    assert fixed["summary"] == result["summary"]
    assert fixed["strategies"] == result["strategies"]
    assert fixed.get("entry_conditions") == result.get("entry_conditions")
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == original
    # A new independent job cannot repeat the claimed final dates.
    monkeypatch.setattr(research_entry_conditions, "prepare_context", market)
    monkeypatch.setattr(research_portfolio, "_prepare_prices", actual_prices)
    later = client.post("/scanner-research/api/portfolio/jobs", json={
        "portfolio": request, "request_id": "conditional-second-parent",
    })
    assert later.status_code == 202, later.json
    calculate(store, "conditional-second-parent")
    rejected = service.get_job(store, "research-test", later.json["id"])
    assert rejected.status == "failed" and "already used" in rejected.error
