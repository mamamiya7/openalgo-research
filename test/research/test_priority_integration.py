"""Native gates/ATR, real Optuna and durable service recovery over fake accounts.

These controlled account economics qualify orchestration and exact replay. The
separate native execution tests qualify actual VectorBT fills and hard caps.
No broker, live database or order endpoint is used.
"""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import json
import time
from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_priority_research import PriorityAccountEngine as ExecutionAccountEngine
from test_priority_research import prepared as research_prepared

from database.research_db import ResearchJob
from research import execution_features
from research.automatic_protocol import PRIORITY_VERSION as EXECUTION_VERSION
from research.automatic_protocol import compile_recipe
from research.execution_policy import normalize_policy
from research.portfolio import execution_versions, normalize
from research.report_contract import settings_identity

OWNER = "research-test"
JOB = "f" * 32
pytestmark = pytest.mark.timeout(300)


def prepared():
    value = research_prepared()
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value = execution_features.prepare(value, value["automatic_stock_market_context"])
    value["versions"] = execution_versions(value["portfolio"])
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_fixed_percentage_risk_caps_need_no_atr_context_or_attachment(app, client, monkeypatch):
    from test_vectorbt_portfolio import signal, snapshot

    from research.execution_policy import normalize_limits
    from services.research_portfolio import run

    limits = normalize_limits({"max_symbol_exposure_pct": 25, "max_trade_risk_pct": 100})
    portfolio = normalize(
        {
            "engine": "vectorbt",
            "capital": 10000,
            "risk_limits": limits,
            "strategies": [
                {
                    "id": "a",
                    "name": "Simple cash account",
                    "source_id": "1" * 32,
                    "allocation_pct": 100,
                    "config": {
                        "order_size_pct": 100,
                        "cost_bps": 0,
                        "target_pct": 20,
                        "stop_pct": 5,
                        "hold_sessions": 1,
                    },
                }
            ],
        }
    )
    portfolio["strategies"][0]["execution_policy"] = normalize_policy(
        {
            **portfolio["strategies"][0]["execution_policy"],
            "version": "research-execution-aware-v2",
            "entry_priority": "lower_stop_risk",
        }
    )
    rows = [{**portfolio["strategies"][0], "signals": [signal()]}]
    evidence = {
        "portfolio": portfolio,
        "versions": execution_versions(portfolio),
        "strategies": rows,
        "signals": [dict(signal(), strategy_id="a")],
        "snapshot": snapshot(),
        "frozen_prices": True,
    }

    def forbidden(*_a, **_k):
        pytest.fail("Plain percentage hard caps requested unnecessary ATR or auxiliary context")

    monkeypatch.setattr("services.research_stock_market_context.prepare_context", forbidden)
    monkeypatch.setattr("services.research_stock_features.prepare_context", forbidden)
    monkeypatch.setattr("research.execution_features.prepare", forbidden)
    monkeypatch.setattr("research.execution_features.attach", forbidden)
    report, retained = run(
        app.extensions["research_store"],
        OWNER,
        evidence,
        {"portfolio": portfolio, "versions": evidence["versions"]},
        checkpoint=lambda *_a: None,
        progress=lambda *_a: None,
        cancelled=lambda: None,
    )
    assert "execution_features" not in retained
    assert "automatic_stock_market_context" not in retained
    assert report["ledger"][0]["quantity"] == 25
    assert "symbol_exposure" in report["ledger"][0]["execution_details"]["constraints"]
    assert report["execution"]["adapter_version"] == "vectorbt-portfolio-adapter-v3"


def test_full_service_durable_resume_and_chosen_fixed_replay_keep_policy_and_financials(
    app, client, monkeypatch
):
    from research.expanded_stock_market import PreparedJointContext
    from services import scanner_research_service as artifacts
    from services.research_portfolio import replay_inputs, run

    value = prepared()
    original = deepcopy(value)
    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: None)
    contexts = []

    def real_context(bundle):
        context = PreparedJointContext(bundle)
        contexts.append(context)
        return context

    monkeypatch.setattr("research.automatic_walk_forward.prepare_method_gate", real_context)
    engine = ExecutionAccountEngine(value)
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", engine)
    saved = None

    class Paused(Exception):
        pass

    def pause(state, counts):
        nonlocal saved
        saved = state
        calculation = state.get("calculation") or {}
        fit = calculation.get("fits", {}).get("fit1", {})
        if len(fit.get("trials", [])) == 41:
            raise Paused

    kwargs = {
        "progress": lambda *a: None,
        "cancelled": lambda: None,
        "job_id": JOB,
        "defer_study_analysis": True,
    }
    spec = {"portfolio": value["portfolio"], "versions": value["versions"]}
    with pytest.raises(Paused):
        run(store, OWNER, value, spec, checkpoint=pause, **kwargs)
    partial = deepcopy(saved)
    prefix_calls = deepcopy(engine.calls)
    assert partial["development_evidence"]["entries"]

    def checkpoint(state, counts):
        nonlocal saved
        saved = state

    result, retained = run(
        store, OWNER, value, spec, saved=partial, checkpoint=checkpoint, **kwargs
    )
    assert value == original
    assert engine.calls[: len(prefix_calls)] == prefix_calls
    assert len(engine.calls) == saved["calculation"]["simulations"]
    assert result["development_evidence"]["status"] == "complete"
    assert len(saved["calculation"]["fits"]) == 4
    assert all(len(fit["trials"]) == 68 for fit in saved["calculation"]["fits"].values())
    assert all(
        row["period"] != "final" for row in saved["development_evidence"]["entries"].values()
    )
    assert all(
        row["status"] == "retained" for row in saved["development_evidence"]["entries"].values()
    )
    assert result["strategies"][0]["execution_policy"]["sizing"] == "stop_risk"
    assert result["strategies"][0]["execution_policy"]["exit_mode"] == "atr"
    assert result["strategies"][0]["execution_policy"]["entry_priority"] in {
        "source",
        "reversed_source",
        "lower_stop_risk",
    }
    assert (
        result["automatic_research"]["execution_research"]["selected_entry_priority"]
        == result["strategies"][0]["execution_policy"]["entry_priority"]
    )
    assert result["automatic_research"]["counts"]["proposals"] == 272
    complete_calls = len(engine.calls)
    repeated, _ = run(
        store, OWNER, value, spec, saved=deepcopy(saved), checkpoint=checkpoint, **kwargs
    )
    assert repeated == result
    assert len(engine.calls) == complete_calls
    assert contexts
    for context in contexts:
        with pytest.raises(ValueError, match="released"):
            context.apply({}, value["automatic_stock_market_context"], {})

    # Exercise the actual saved-job selection adapter, not a hand-built replay.
    digest = artifacts.save_artifact(
        store,
        {
            "kind": "portfolio_optimize",
            "inputs_artifact": saved["inputs_artifact"],
            "result": result,
        },
    )
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=JOB,
                owner=OWNER,
                source_id="1" * 64,
                config=json.dumps({}),
                status="completed",
                created_at=time.time(),
                updated_at=time.time(),
                result_artifact=digest,
            )
        )
    _, replay = replay_inputs(store, OWNER, JOB)
    assert replay["execution_features"] == retained["execution_features"]
    assert (
        replay["strategies"][0]["execution_policy"] == result["strategies"][0]["execution_policy"]
    )
    rerun, _ = run(
        store,
        OWNER,
        replay,
        {"portfolio": replay["portfolio"], "versions": replay["versions"]},
        checkpoint=lambda *a: None,
        progress=lambda *a: None,
        cancelled=lambda: None,
    )
    assert settings_identity(rerun["strategies"]) == settings_identity(result["strategies"])
    assert rerun["summary"] == result["summary"]
    assert rerun["equity_curve"] == result["equity_curve"]
    assert (
        rerun["automatic_stock_condition"]["counts"]
        == result["automatic_stock_condition"]["counts"]
    )
    assert rerun["replay_origin"]["config_id"] == settings_identity(result["strategies"])
