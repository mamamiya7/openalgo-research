"""Saved selected allocation survives the native service and cache-only replay."""

# ruff: noqa: F811 -- isolated application fixtures

from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_library import OWNER
from test_portfolio_allocation_recipe import prepared
from test_portfolio_capacity_replay import fixed_input, seed

from database.research_db import ResearchStore
from research.automatic_protocol import compile_recipe
from research.execution_features import prepare as prepare_atr
from research.portfolio import normalize
from research.portfolio_constraint_evidence import assess
from research.portfolio_execution_features import prepare as prepare_features
from research.report_contract import settings_identity
from services import research_portfolio as portfolio
from services import scanner_research_service as service

pytestmark = pytest.mark.timeout(180)


@pytest.fixture(scope="module")
def retained(tmp_path_factory):
    value = prepared()
    value["receipt"] = {
        "input_type": "portfolio",
        "signal_count": len(value["signals"]),
        "symbol_count": len({row["symbol"] for row in value["signals"]}),
        "date_from": min(row["date"] for row in value["signals"]),
        "date_to": max(row["date"] for row in value["signals"]),
        "name": "Controlled v16 saved allocation",
        "warnings": [],
    }
    hours = value["automatic_stock_market_context"]["market"]["calendar"]["session_hours"]
    value["snapshot"]["session_hours"] = {
        day: deepcopy(hours[day]) for day in value["snapshot"]["sessions"]
    }
    volume = value.pop("portfolio_execution_features")["volume_records"]
    value.pop("execution_features")
    value = prepare_features(
        prepare_atr(value, value["automatic_stock_market_context"]),
        value["automatic_stock_market_context"],
        volume,
    )
    value["automatic_recipe"] = compile_recipe(value)
    fixed = fixed_input(deepcopy(value))
    for key in (fixed["strategies"], fixed["portfolio"]["strategies"]):
        for row in key:
            row["execution_policy"]["allocation_mode"] = "prior60_cluster_inverse_vol"
    fixed["portfolio"] = normalize(fixed["portfolio"])
    store = ResearchStore(tmp_path_factory.mktemp("native-allocation-replay"))
    store.initialize()
    try:
        report, _ = portfolio.run(
            store,
            OWNER,
            fixed,
            {"portfolio": fixed["portfolio"], "versions": fixed["versions"]},
            checkpoint=lambda *a: None,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
        assert report["policy_version"] == "vectorbt-joint-causal-bars-v6"
        assert assess(report, value["portfolio_execution_features"])["status"] == "supported"
        identity = settings_identity(report["strategies"])
        report["automatic_research"] = {
            "version": "automatic-hypotheses-v16",
            "selected_config_id": identity,
        }
        report["experiment"] = {
            "rows": [
                {
                    "config_id": identity,
                    "trial_number": 7,
                    "strategies": deepcopy(report["strategies"]),
                    "summary": deepcopy(report["summary"]),
                }
            ],
            "recommendation_id": identity,
            "objective_winner_id": identity,
        }
        return value, report
    finally:
        store.close()


def test_selected_native_allocation_replays_without_broker_or_search(
    app, client, retained, monkeypatch
):
    store, job_id, _, report, artifact = seed(app, retained)
    original = service.read_artifact(store, artifact)

    def forbidden(*args, **kwargs):
        raise AssertionError("Exact saved replay cannot download prices or run a search")

    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_sources.resolve_broker_session",
        "services.research_historify.NativeHistorifyArchive.read_many",
        "services.research_native_prices.native_history",
        "research.connectors.optuna_portfolio.run_search",
    ):
        monkeypatch.setattr(target, forbidden)
    _, replay = portfolio.replay_inputs(store, OWNER, job_id)
    assert "automatic_research" not in replay["portfolio"]
    assert all(
        row["execution_policy"]["allocation_mode"] == "prior60_cluster_inverse_vol"
        for row in replay["strategies"]
    )
    repeated, frozen = portfolio.run(
        store,
        OWNER,
        replay,
        {"portfolio": replay["portfolio"], "versions": replay["versions"]},
        checkpoint=lambda *a: None,
        progress=lambda *a: None,
        cancelled=lambda: None,
    )
    assert repeated["summary"] == report["summary"]
    assert repeated["ledger"] == report["ledger"]
    assert repeated["engine_records"]["orders"] == report["engine_records"]["orders"]
    assert frozen["portfolio_execution_features"] == replay["portfolio_execution_features"]
    assert assess(repeated, frozen["portfolio_execution_features"])["status"] == "supported"
    assert service.read_artifact(store, artifact) == original


def test_trial_and_reserved_period_preserve_selected_allocation(app, client, retained):
    store, job_id, _, report, _ = seed(app, retained)
    for options in (
        {"trial_id": settings_identity(report["strategies"])},
        {"period": "evaluation"},
    ):
        _, replay = portfolio.replay_inputs(store, OWNER, job_id, **options)
        assert replay["versions"]["adapter_version"] == "vectorbt-portfolio-adapter-v6"
        assert all(
            row["execution_policy"]["allocation_mode"] == "prior60_cluster_inverse_vol"
            for row in replay["strategies"]
        )
        assert replay["portfolio"]["risk_limits"] == report["execution"]["risk_limits"]


@pytest.mark.timeout(900)
def test_full_v16_service_histories_seed_vetoes_and_recovery(app, client, monkeypatch):
    # Deliberately controlled account maths/audit; genuine native fills have
    # separate prefix, full-procedure and exact saved replay qualification.
    from test_portfolio_sensitivity_integration import exercise_full_service

    exercise_full_service(app, client, monkeypatch, prepared())
