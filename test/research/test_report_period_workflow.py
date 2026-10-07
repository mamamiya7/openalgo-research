"""Reserve before baseline, then evaluate an exact candidate on frozen later data."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import copy
import json

import pytest
from test_jobs import app, client  # noqa: F401
from test_library import BASE, OWNER, create, run
from test_portfolio_validation import inputs
from test_portfolio_workflow import uploaded

from database.research_db import ResearchHistory
from research.portfolio import normalize
from research.portfolio_coverage import prepare
from research.portfolio_validation import partition, period_plan
from services import research_library as library
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_portfolio import resolve_inputs


def draft(client, optimize):
    evidence = inputs()
    csv = "Date,Symbol\n" + "".join(f"{s['date']},AAA\n" for s in evidence["signals"])
    source_id = uploaded(client, csv.encode())
    request = copy.deepcopy(evidence["portfolio"])
    request["strategies"][0]["source_id"] = source_id
    request["validation"]["mode"] = "reserve"
    if not optimize:
        request.pop("optimization")
    value = library.fresh_draft()
    value.update(portfolio=normalize(request), optimizing=optimize, equalWeights=False)
    if optimize:
        value["optimization"] = value["portfolio"].pop("optimization")
    return value


def run_worker(store):
    token = "report-period-tests"
    worker.acquire(store, token)
    try:
        assert worker.run_one(store, token)
    finally:
        worker.release(store, token)


def test_boundary_is_price_independent_and_changes_are_rejected(app, client, monkeypatch):
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a: pytest.fail("preflight requested broker"),
    )
    store = app.extensions["research_store"]
    payload = library.portfolio_payload(draft(client, False))
    evidence = resolve_inputs(store, OWNER, payload)
    assert evidence["snapshot"]["sessions"] == []
    plan = evidence["period_plan"]
    assert plan["selection"]["to"] == "2026-01-11"
    assert plan["evaluation"]["from"] == "2026-01-12"
    duplicate = copy.deepcopy(evidence)
    duplicate["signals"] *= 3
    assert period_plan(duplicate) == plan  # Distinct dates, never CSV row percentages.
    evidence["snapshot"] = inputs()["snapshot"]
    evidence["period_plan"]["selection"]["to"] = "2026-01-10"
    with pytest.raises(ValueError, match="Saved evaluation dates"):
        partition(evidence)


def test_reserved_baseline_does_not_require_usable_later_prices():
    evidence = inputs()
    evidence["portfolio"]["validation"]["mode"] = "reserve"
    for symbol, bars in evidence["snapshot"]["bars"].items():
        evidence["snapshot"]["bars"][symbol] = {
            stamp: bar for stamp, bar in bars.items() if stamp < "2026-01-12"
        }
    earlier, later, _ = partition(evidence, prepare_period="selection")
    assert earlier["signal_coverage"]["eligible_signals"] > 0
    assert "signal_coverage" not in later
    with pytest.raises(ValueError, match="No signals"):
        partition(evidence, prepare_period="evaluation")


@pytest.mark.parametrize("optimize", [False, True])
def test_reserved_baseline_and_candidate_evaluation_survive_reopen_without_downloads(
    app, client, monkeypatch, optimize
):
    store = app.extensions["research_store"]
    preparations = []

    def frozen_prices(_store, _owner, evidence, **_kwargs):
        preparations.append(evidence["period_plan"])
        return prepare(
            {**evidence, "snapshot": copy.deepcopy(inputs()["snapshot"]), "frozen_prices": True}
        )

    monkeypatch.setattr("services.research_portfolio._prepare_prices", frozen_prices)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a: pytest.fail("frozen candidate requested broker"),
    )
    experiment = create(client, draft(client, optimize))
    submitted = run(client, experiment)
    parent_id = submitted["job"]["id"]
    run_worker(store)
    parent = client.get(f"/scanner-research/api/jobs/{parent_id}").json
    assert parent["status"] == "completed", parent
    result = parent["result"]
    assert "validation" not in result  # The later evaluator has not run.
    assert result["reserved_evaluation"]["status"] == "reserved"
    assert max(p["date"] for p in result["equity_curve"]) == "2026-01-11"
    assert result["report_context"]["period"] == "selection"
    with store.sessions() as db:
        history = db.get(ResearchHistory, parent_id)
        assert history.test_end == "2026-01-11"
        assert all(key[:10] <= history.test_end for key in json.loads(history.signal_keys))
    original_export = client.get(f"/scanner-research/api/jobs/{parent_id}/export").data
    trial_id = result["experiment"]["rows"][-1]["config_id"] if optimize else None
    latest = client.get(f"{BASE}/experiments/{experiment['id']}").json
    request = {
        "revision": latest["revision"],
        "job_id": parent_id,
        "request_id": "evaluate-reserved-candidate",
        "period": "evaluation",
        **({"trial_id": trial_id} if trial_id else {}),
    }
    path = f"{BASE}/experiments/{experiment['id']}/replay"
    response = client.post(path, json=request)
    assert response.status_code == 202, response.json
    evaluation_id = response.json["job"]["id"]
    assert client.post(path, json=request).json["job"]["id"] == evaluation_id
    run_worker(store)
    evaluated = client.get(f"/scanner-research/api/jobs/{evaluation_id}").json
    assert evaluated["status"] == "completed", evaluated
    report = evaluated["result"]
    assert report["report_context"]["period"] == "evaluation"
    assert report["report_context"]["parent_job_id"] == parent_id
    assert min(p["date"] for p in report["equity_curve"]) == "2026-01-12"
    assert report["summary"]["initial_capital"] == result["summary"]["initial_capital"]
    if trial_id:
        assert report["report_context"]["candidate"]["config_id"] == trial_id
        assert report["report_context"]["candidate"]["study_job_id"] == parent_id
    assert len(preparations) == 1
    assert client.get(f"/scanner-research/api/jobs/{parent_id}/export").data == original_export
    reopened = client.get(f"{BASE}/experiments/{experiment['id']}").json
    assert any(
        job["id"] == evaluation_id and job["role"] == "validation" for job in reopened["jobs"]
    )
    replay = client.post(
        f"/scanner-research/api/portfolio/jobs/{evaluation_id}/rerun",
        json={"request_id": "replay-later-result"},
    )
    assert replay.status_code == 202, replay.json
    run_worker(store)
    replayed = client.get(f"/scanner-research/api/jobs/{replay.json['id']}").json["result"]
    assert replayed["summary"] == report["summary"]
    assert replayed["report_context"]["period"] == "evaluation"
    if trial_id:
        assert replayed["report_context"]["candidate"] == report["report_context"]["candidate"]
    assert len(preparations) == 1
