"""V13 native constraints, frozen recipe and complete saved service recovery.

All broker-labelled observations here are controlled fixtures, never a broker
download. Full native calculations qualify orchestration, not trading edge.
"""

# ruff: noqa: F811
from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_turnover_integration import prepared as previous_prepared

from research.automatic_protocol import CAPACITY_VERSION, compile_recipe
from research.execution_policy import normalize_limits, normalize_policy
from research.execution_search import apply_choices, axes, descriptor, original_sizing
from research.intelligent_plan import build_plan
from research.portfolio import execution_versions, normalize
from research.portfolio_execution_features import prepare as prepare_features
from research.report_contract import settings_identity
from research.volume_provenance import REVIEWED_ADAPTER_SHA256, capture_rows

pytestmark = pytest.mark.timeout(600)


def prepared():
    value = previous_prepared()
    portfolio = value["portfolio"]
    portfolio["automatic_research"] = {"version": CAPACITY_VERSION}
    limits = normalize_limits({**portfolio["risk_limits"], "version": "research-risk-limits-v3"})
    portfolio["risk_limits"] = limits
    for row in portfolio["strategies"]:
        row["execution_policy"] = normalize_policy(
            {
                **row["execution_policy"],
                "version": "research-execution-aware-v4",
                "risk_limits": limits,
                "capacity_mode": "prior20_broker_reported",
                "correlation_mode": "prior60_positive_080",
            }
        )
    value["portfolio"] = normalize(portfolio)
    for row, original in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(original))
    value["versions"] = execution_versions(value["portfolio"])
    context = value["automatic_stock_market_context"]
    records = {}
    for symbol, record in context["series"].items():
        rows = {
            day: {**deepcopy(bar), "volume": 100_000, "oi": 0}
            for day, bar in record["evidence"]["bars"].items()
        }
        records[symbol] = capture_rows(
            symbol,
            list(rows.values()),
            rows,
            broker="fyers",
            adapter_sha256=REVIEWED_ADAPTER_SHA256,
            captured_at="2026-10-21T00:00:00+00:00",
        )
    value = prepare_features(value, context, records)
    value.pop("automatic_recipe", None)
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_new_recipe_freezes_features_limits_and_unchanged_search_budget():
    value = prepared()
    spec = descriptor(value)
    assert spec["version"] == "research-execution-search-v4"
    params = {key: domain["choices"][-1] for key, domain in axes(spec).items()}
    changed = apply_choices(value["strategies"], params, spec)
    control = original_sizing(changed, spec)
    assert all(
        row["execution_policy"]["version"] == "research-execution-aware-v4" for row in control
    )
    for row in changed:
        assert row["execution_policy"]["capacity_mode"] == "prior20_broker_reported"
        assert row["execution_policy"]["correlation_mode"] == "prior60_positive_080"
        assert row["execution_policy"]["risk_limits"] == value["portfolio"]["risk_limits"]
    recipe = value["automatic_recipe"]
    assert recipe["budget"]["proposals"] == 272
    assert recipe["budget"]["max_simulations"] == 384
    assert recipe["portfolio_execution_features_id"] == value["portfolio_execution_features"]["id"]
    assert recipe == compile_recipe(value)
    preflight = build_plan(value["portfolio"], interval="D")
    assert preflight["budget"]["proposals"] == 272
    assert preflight["budget"]["max_simulations"] == 384
    methods = {row["id"]: row for row in preflight["methods"]}
    assert methods["liquidity"]["status"] == "conditional"
    assert methods["correlated_exposure"]["status"] == "conditional"
    assert preflight["data"]["correlation_prior_closes"] == 61


def test_full_native_service_records_constraints_before_final_and_recovers(
    app, client, monkeypatch
):
    from services.research_portfolio import run

    value = prepared()
    original = deepcopy(value)
    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: None)
    saved = None
    kwargs = {
        "progress": lambda *a: None,
        "cancelled": lambda: None,
        "job_id": "a" * 32,
        "defer_study_analysis": True,
    }

    class Paused(Exception):
        pass

    def pause(state, counts):
        nonlocal saved
        saved = state
        if (
            len((state.get("calculation") or {}).get("fits", {}).get("fit1", {}).get("trials", []))
            == 41
        ):
            raise Paused

    spec = {"portfolio": value["portfolio"], "versions": value["versions"]}
    with pytest.raises(Paused):
        run(store, "research-test", value, spec, checkpoint=pause, **kwargs)
    partial = deepcopy(saved)

    def checkpoint(state, counts):
        nonlocal saved
        saved = state
        calculation = state.get("calculation") or {}
        if calculation.get("final_report") is not None:
            assert calculation.get("recommendation_checks", {}).get("portfolio_constraints")

    result, _ = run(
        store, "research-test", value, spec, saved=partial, checkpoint=checkpoint, **kwargs
    )
    assert value == original
    findings = result["automatic_research"]
    assert findings["version"] == CAPACITY_VERSION
    assert findings["counts"]["proposals"] == 272
    assert findings["counts"]["simulations"] <= 384
    assert findings["recommendation_checks"]["portfolio_constraints"]["development_accounts"] == 16
    assert (
        findings["execution_research"]["portfolio_features_id"]
        == value["portfolio_execution_features"]["id"]
    )
    assert result["development_evidence"]["status"] == "complete"
    assert settings_identity(result["strategies"]) == findings["selected_config_id"]
    before = deepcopy(saved)
    repeated, _ = run(
        store, "research-test", value, spec, saved=deepcopy(saved), checkpoint=checkpoint, **kwargs
    )
    assert repeated == result
    assert saved["calculation"]["simulations"] == before["calculation"]["simulations"]
