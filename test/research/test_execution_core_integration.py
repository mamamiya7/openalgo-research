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
from test_execution_aware_research import ExecutionAccountEngine
from test_execution_aware_research import prepared as research_prepared
from test_jobs import app, client  # noqa: F401

from database.research_db import ResearchJob
from research import execution_features
from research.automatic_protocol import EXECUTION_VERSION, compile_recipe
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


def test_request_and_runtime_versions_keep_execution_controls_explicit():
    value = prepared()
    assert normalize(value["portfolio"]) == value["portfolio"]
    versions = execution_versions(value["portfolio"])
    assert versions["adapter_version"] == "vectorbt-portfolio-adapter-v2"
    assert versions["policy_version"] == "vectorbt-joint-causal-bars-v2"
    assert versions["execution_features_version"] == execution_features.VERSION
    assert versions["automatic_research_version"] == EXECUTION_VERSION
    changed = deepcopy(value["portfolio"])
    changed["engine"] = "nautilus"
    with pytest.raises(ValueError, match="VectorBT"):
        normalize(changed)
    changed = deepcopy(value["portfolio"])
    changed["strategies"][0]["execution_policy"]["risk_limits"]["max_open_positions"] += 1
    with pytest.raises(ValueError, match="same portfolio risk limits"):
        normalize(changed)


def test_chosen_policy_reuses_original_feature_receipt_on_frozen_sliced_replay():
    from research.automatic_protocol import slice_period

    value = prepared()
    chosen = slice_period(value, "search")
    chosen["portfolio"] = deepcopy(chosen["portfolio"])
    chosen["portfolio"].pop("automatic_research")
    chosen["portfolio"].pop("optimization")
    chosen["replay_origin"] = {"parent_job_id": JOB, "period": "selection"}
    chosen["strategies"] = deepcopy(chosen["strategies"])
    selected = chosen["strategies"][0]
    selected["config"].update(target_pct=19, stop_pct=7)
    selected["execution_policy"] = normalize_policy(
        {
            "exit_mode": "atr",
            "atr_stop_multiple": 2,
            "atr_target_multiple": 4,
            "sizing": "stop_risk",
            "risk_limits": value["portfolio"]["risk_limits"],
        }
    )
    result = execution_features.prepare(chosen, chosen["automatic_stock_market_context"])
    assert result["execution_features"] == value["execution_features"]
    attached = execution_features.attach(result)
    assert attached["strategies"][0]["execution_policy"] == selected["execution_policy"]
    assert any(
        "execution_atr" in row
        for row in attached["strategies"][0]["signals"]
        if not row.get("research_exclusion")
    )
    changed = deepcopy(chosen)
    receipt = changed["execution_features"]
    feature = next(
        row["feature"] for row in receipt["features"].values() if row["feature"] is not None
    )
    feature["value"] *= 1.5
    from research.portfolio import fingerprint

    feature["id"] = fingerprint({key: item for key, item in feature.items() if key != "id"})
    receipt["id"] = fingerprint({key: item for key, item in receipt.items() if key != "id"})
    with pytest.raises(ValueError, match="original recorded context"):
        execution_features.prepare(changed, changed["automatic_stock_market_context"])


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
    assert report["execution"]["adapter_version"] == "vectorbt-portfolio-adapter-v2"


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
    assert all(len(fit["trials"]) == 64 for fit in saved["calculation"]["fits"].values())
    assert all(
        row["period"] != "final" for row in saved["development_evidence"]["entries"].values()
    )
    assert all(
        row["status"] == "retained" for row in saved["development_evidence"]["entries"].values()
    )
    assert result["strategies"][0]["execution_policy"]["sizing"] == "stop_risk"
    assert result["strategies"][0]["execution_policy"]["exit_mode"] == "atr"
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
