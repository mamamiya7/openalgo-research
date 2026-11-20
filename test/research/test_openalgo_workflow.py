# ruff: noqa: F811 -- shared isolated HTTP fixtures
"""Actual optional-engine jobs, unchanged Optuna, immutable save and read-only reopen."""

import io

import pytest
from test_acquisition import reference
from test_jobs import app, client
from test_native_price_workflow import prepare_archive

from research.connectors import openalgo_runtime
from services import scanner_research_worker as worker

pytestmark = pytest.mark.skipif(
    not openalgo_runtime.status()["available"], reason="optional SDK overlay not installed"
)


@pytest.mark.parametrize("optimize", [False, True])
def test_optional_engine_native_job_save_export_and_reopen(
    app, client, monkeypatch, tmp_path, optimize
):
    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("Saved Historify coverage must not connect to a broker"),
    )
    monkeypatch.setattr(
        "services.research_instruments.native_instruments",
        lambda symbols, **kw: {
            s: {
                "currency": "INR",
                "lot_size": 1,
                "tick_size": "0.05",
                "price_precision": 2,
                "source": "controlled engineering fixture",
            }
            for s in symbols
        },
    )
    source = client.post(
        "/scanner-research/api/portfolio/inputs",
        data={"file": (io.BytesIO(b"Date,Symbol\n2026-01-05,AAA\n"), "small.csv")},
    ).json["id"]
    request = {
        "name": "Optional SDK",
        "capital": 10000,
        "engine": "openalgo",
        "strategies": [
            {
                "id": "a",
                "name": "A",
                "source_id": source,
                "allocation_pct": 100,
                "config": {
                    "cost_bps": 0,
                    "slippage_bps": 0,
                    "hold_sessions": 1,
                    "order_size_pct": 50,
                },
            }
        ],
    }
    if optimize:
        request["strategies"][0]["search"] = {"target_pct": {"min": 5, "max": 7, "step": 1}}
        request["optimization"] = {
            "sampler": "grid",
            "trials": 3,
            "seed": 0,
            "objective": "balanced",
        }
    preview = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert preview.status_code == 200, preview.json
    response = client.post(
        "/scanner-research/api/portfolio/jobs",
        json={"portfolio": request, "request_id": f"optional-{optimize}"},
    )
    assert response.status_code == 202, response.json
    job = response.json["id"]
    store = app.extensions["research_store"]
    worker.acquire(store, "optional-sdk-test")
    try:
        assert worker.run_one(store, "optional-sdk-test")
    finally:
        worker.release(store, "optional-sdk-test")
    completed = client.get(f"/scanner-research/api/jobs/{job}").json
    assert completed["status"] == "completed", completed
    result = completed["result"]
    assert result["execution"]["engine"] == "openalgo"
    assert result["ledger"][0]["quantity"] > 0
    if optimize:
        assert len(result["experiment"]["rows"]) == 3
    exported = client.get(f"/scanner-research/api/jobs/{job}/export")
    assert exported.status_code == 200
    monkeypatch.setattr(
        openalgo_runtime, "evaluate", lambda *a, **kw: pytest.fail("Reopen must never recalculate")
    )
    reopened = client.get(f"/scanner-research/api/jobs/{job}").json
    assert reopened["result"] == result
    assert client.get(f"/scanner-research/api/jobs/{job}/export").data == exported.data


def test_automatic_programme_rejects_optional_engine_without_changing_budget():
    from research.automatic_hypotheses import policy_for_version
    from research.automatic_protocol import normalize_auto

    with pytest.raises(ValueError, match="engine"):
        normalize_auto({"version": "automatic-hypotheses-v16"}, engine="openalgo")
    before = policy_for_version("automatic-hypotheses-v16")
    assert before["proposals"] == 408
    assert before["max_simulations"] == 512


def test_existing_chronological_validation_runs_on_selected_optional_settings(tmp_path):
    import copy

    import pandas as pd
    from test_vectorbt_portfolio import snapshot, strategy

    from database.research_db import ResearchStore
    from research.portfolio import execution_versions, normalize
    from services.research_portfolio import run

    days = pd.bdate_range("2026-01-05", periods=20).strftime("%Y-%m-%d").tolist()
    signals = [{"symbol": "AAA", "date": days[i], "row": i + 2} for i in (0, 3, 6, 9, 12, 15)]
    prices = snapshot(symbols=("AAA",), days=days)
    prices["instruments"] = {
        "AAA": {"currency": "INR", "lot_size": 1, "tick_size": "0.05", "price_precision": 2}
    }
    raw = {
        "engine": "openalgo",
        "capital": 10000,
        "strategies": [
            {
                **{k: v for k, v in strategy(allocation=100).items() if k != "signals"},
                "source_id": "a" * 32,
                "search": {"target_pct": {"min": 5, "max": 6, "step": 1}},
            }
        ],
        "optimization": {"sampler": "grid", "trials": 2, "seed": 0},
        "validation": {"train_pct": 70},
    }
    portfolio = normalize(raw)
    rows = [{**portfolio["strategies"][0], "signals": signals}]
    evidence = {
        "portfolio": portfolio,
        "strategies": rows,
        "signals": signals,
        "snapshot": prices,
        "versions": execution_versions(portfolio),
        "frozen_prices": True,
    }
    original = copy.deepcopy(evidence)
    store = ResearchStore(tmp_path / "isolated")
    try:
        store.initialize()
        result, _ = run(
            store,
            "owner",
            evidence,
            {"portfolio": portfolio, "versions": evidence["versions"]},
            checkpoint=lambda *a: None,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    finally:
        store.close()
    assert evidence == original
    assert len(result["experiment"]["rows"]) == 2
    later = result["validation"]["result"]
    assert later["execution"]["engine"] == "openalgo"
    assert later["summary"]["initial_capital"] == 10000
    assert later["strategies"] == result["strategies"]
    assert max(r["signal_date"] for r in result["ledger"]) < min(
        r["signal_date"] for r in later["ledger"]
    )
