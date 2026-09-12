# ruff: noqa: F811 -- shared isolated native Flask fixtures
"""Automatic research through native jobs, real VectorBT, activity and replay."""

from copy import deepcopy

import pytest
from test_automatic_protocol import evidence as sample
from test_jobs import app, client
from test_portfolio_workflow import uploaded

from research.automatic_protocol import VERSION
from research.portfolio_coverage import prepare
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def request_and_prices(client, monkeypatch):
    data = sample(config={"hold_sessions": 1, "target_pct": 4, "stop_pct": 2})
    dates = [row["date"] for row in data["signals"]]
    source = uploaded(client, ("Date,Symbol\n" + "\n".join(f"{day},AAA" for day in dates)).encode())
    request = {
        "name": "Automatic native test",
        "capital": 100000,
        "engine": "vectorbt",
        "automatic_research": {"version": VERSION},
        "strategies": [
            {
                "id": "scanner",
                "name": "Scanner",
                "source_id": source,
                "allocation_pct": 100,
                "config": {"hold_sessions": 1, "target_pct": 4, "stop_pct": 2},
                "search": {},
            }
        ],
    }

    def frozen(store, owner, value, **kwargs):
        return prepare({**value, "snapshot": deepcopy(data["snapshot"]), "frozen_prices": True})

    monkeypatch.setattr(research_portfolio, "_prepare_prices", frozen)
    return request


def calculate(store, token="automatic-test"):
    worker.acquire(store, token)
    try:
        assert worker.run_one(store, token)
    finally:
        worker.release(store, token)


@pytest.mark.timeout(180)
def test_automatic_job_has_real_engine_findings_and_exact_search_and_final_replay(
    app, client, monkeypatch
):
    request = request_and_prices(client, monkeypatch)
    before = deepcopy(request)
    preview = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert preview.status_code == 200, preview.json
    assert preview.json["portfolio"]["optimization"]["trials"] == 50
    assert preview.json["interval"] == "D"
    assert request == before
    response = client.post(
        "/scanner-research/api/portfolio/jobs",
        json={"portfolio": request, "request_id": "automatic-native"},
    )
    assert response.status_code == 202, response.json
    job_id = response.json["id"]
    store = app.extensions["research_store"]
    from research.connectors import vectorbt_portfolio

    actual = vectorbt_portfolio.evaluate
    calls = []

    def pause_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 8:
            paused = client.post(f"/scanner-research/api/jobs/{job_id}/pause")
            assert paused.status_code == 200 and paused.json["status"] == "pausing", paused.json
        return actual(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", pause_once)
    calculate(store)
    paused = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert paused["status"] == "paused" and paused["resumable"], paused
    assert len(calls) == 8
    resumed = client.post(f"/scanner-research/api/jobs/{job_id}/resume")
    assert resumed.status_code == 200, resumed.json
    calculate(store)
    completed = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert completed["status"] == "completed", completed
    result = completed["result"]
    assert result["execution"]["engine"] == "vectorbt"
    assert result["experiment"]["optimizer"]["version"] == "5.0.0"
    assert result["automatic_research"]["counts"]["proposals"] == 50
    assert result["automatic_research"]["counts"]["simulations"] <= 70
    assert result["automatic_research"]["counts"]["simulations"] == len(calls)
    assert result["automatic_research"]["selected_is_baseline"]  # Flat prices cannot beat costs.
    original = client.get(f"/scanner-research/api/jobs/{job_id}/export").data
    monkeypatch.setattr(
        research_portfolio,
        "_prepare_prices",
        lambda *a, **kw: pytest.fail("Replay cannot acquire prices"),
    )
    for period in ("selection", "evaluation"):
        replay = client.post(
            f"/scanner-research/api/portfolio/jobs/{job_id}/rerun",
            json={"period": period, "request_id": f"auto-replay-{period}"},
        )
        assert replay.status_code == 202, replay.json
        calculate(store, f"auto-replay-{period}")
        receipt = client.get(f"/scanner-research/api/jobs/{replay.json['id']}").json
        assert receipt["status"] == "completed", receipt
        expected = result if period == "selection" else result["validation"]["result"]
        assert receipt["result"]["summary"] == expected["summary"]
        assert receipt["result"]["evaluation_basis"] == expected["evaluation_basis"]
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == original


def test_automatic_validation_conflict_and_unknown_recipe_are_rejected(client, monkeypatch):
    request = request_and_prices(client, monkeypatch)
    request["validation"] = {"train_pct": 90, "mode": "evaluate"}
    assert client.post("/scanner-research/api/portfolio/preflight", json=request).status_code == 400
    request.pop("validation")
    request["automatic_research"]["version"] = "future"
    assert client.post("/scanner-research/api/portfolio/preflight", json=request).status_code == 400
