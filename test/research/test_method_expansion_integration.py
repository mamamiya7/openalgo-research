"""Expanded method admission, native service wiring and truthful saved coverage.

Prices and account outcomes are synthetic; real gates, seeded Optuna and durable
history records are exercised without a broker or the user's databases.
"""

# ruff: noqa: F811 -- shared isolated Flask fixtures
from copy import deepcopy

import pytest
from test_automatic_walk_forward import AccountEngine
from test_automatic_walk_forward import prepared as previous_prepared
from test_jobs import app, client  # noqa: F401
from test_method_coverage_service import JOB, OWNER, path, seed

from research.automatic_protocol import METHOD_VERSION, automatic_portfolio, compile_recipe
from research.expanded_stock_market import catalog
from research.method_coverage import build
from research.method_registry import catalog as method_registry
from research.report_contract import fingerprint


def prepared():
    value = previous_prepared()
    value["portfolio"]["automatic_research"] = {"version": METHOD_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_expansion_is_versioned_and_budget_does_not_grow():
    old, new = previous_prepared(), prepared()
    before = fingerprint(old)
    recipe = new["automatic_recipe"]
    assert recipe["hypothesis_catalog"][:92] == old["automatic_recipe"]["hypothesis_catalog"]
    assert len(recipe["hypothesis_catalog"]) == 108
    assert recipe["budget"] == old["automatic_recipe"]["budget"]
    assert recipe["periods"] == old["automatic_recipe"]["periods"]
    # The protocol binds the requested version in its source settings hash.
    # Its dates, horizon admission and work bounds remain identical.
    assert {
        key: value
        for key, value in recipe["walk_forward"].items()
        if key not in ("id", "settings_id")
    } == {
        key: value
        for key, value in old["automatic_recipe"]["walk_forward"].items()
        if key not in ("id", "settings_id")
    }
    assert recipe["id"] != old["automatic_recipe"]["id"]
    assert recipe == compile_recipe(new)
    assert automatic_portfolio(new["portfolio"]) == new["portfolio"]
    assert fingerprint(old) == before


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_preflight_reports_new_methods_and_reserves_the_refinement_budget(interval):
    from test_automatic_catalog import request

    from services.research_automatic_catalog import build_plan

    plan = build_plan(request(version=METHOD_VERSION), interval=interval)
    assert plan["summary"]["condition_presets"] == 108
    assert plan["summary"]["max_simulations"] == 320
    budget = plan["search"]["budget"]
    assert budget["proposals"] == 192 and budget["proposals_per_fit"] == 48
    assert budget["momentum_probe_limit_per_fit"] == 4
    assert budget["timing_probe_limit_per_fit"] == 4
    assert budget["min_joint_refinement_proposals_per_fit"] == 8
    assert budget["screening_proposals"] + budget["refinement_proposals"] == 192
    assert plan["data"]["stock_warmup_sessions"] == 100
    assert plan["data"]["benchmark_warmup_sessions"] == 160
    assert all(
        item["status"] == "fixed"
        for row in plan["strategies"]
        for item in row["parameters"]
        if item["id"] in ("initial_capital", "allocation_pct", "cost_bps", "slippage_bps")
    )


def test_registry_never_reinterprets_the_saved_v8_domain():
    old, new = method_registry(), method_registry(METHOD_VERSION)
    assert old["hypothesis_count"] == 92 and new["hypothesis_count"] == 108
    assert old["catalog_id"] != new["catalog_id"]
    assert old["family_count"] == new["family_count"] == 32
    for old_family, new_family in zip(old["families"], new["families"], strict=True):
        assert old_family["id"] == new_family["id"]
        assert set(old_family["registered_hypothesis_ids"]) <= set(
            new_family["registered_hypothesis_ids"]
        )
    momentum = next(row for row in new["methods"] if row["id"] == "momentum")
    assert "5,10,20 or60" in momentum["observations"]
    assert method_registry() == old


def test_execution_versions_bind_the_expanded_gate_in_new_and_saved_conditions():
    from research.portfolio import execution_versions

    value = prepared()
    versions = execution_versions(value["portfolio"])
    assert versions["entry_conditions_version"] == "research-joint-entry-conditions-v2"
    assert versions["joint_stock_market_version"] == "research-expanded-stock-market-features-v1"
    fixed = deepcopy(value["portfolio"])
    fixed.pop("automatic_research")
    fixed.pop("optimization")
    fixed["entry_conditions"] = {
        "version": "research-joint-entry-conditions-v2",
        "hypothesis": catalog()[92],
    }
    assert (
        execution_versions(fixed)["joint_stock_market_version"]
        == versions["joint_stock_market_version"]
    )
    assert (
        execution_versions(previous_prepared()["portfolio"])["entry_conditions_version"]
        == "research-joint-entry-conditions-v1"
    )


@pytest.mark.timeout(300)
def test_expanded_service_records_all_fits_and_reads_only_saved_metadata(app, client, monkeypatch):
    from services import scanner_research_service as artifacts
    from services.research_portfolio import run

    value = prepared()
    store = app.extensions["research_store"]
    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *a: None)
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: None)
    engine = AccountEngine(value)
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", engine)
    saved = None

    def checkpoint(state, counts):
        nonlocal saved
        saved = state

    result, _ = run(
        store,
        OWNER,
        value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        checkpoint=checkpoint,
        progress=lambda *a: None,
        cancelled=lambda: None,
        job_id=JOB,
        defer_study_analysis=True,
    )
    assert result["development_evidence"]["status"] == "complete"
    finished = deepcopy(saved)
    finished_calls = len(engine.calls)
    resumed, _ = run(
        store,
        OWNER,
        value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        saved=finished,
        checkpoint=checkpoint,
        progress=lambda *a: None,
        cancelled=lambda: None,
        job_id=JOB,
        defer_study_analysis=True,
    )
    assert resumed == result
    assert len(engine.calls) == finished_calls, "Completed recovery repeated accounting"
    metadata = saved["research_mandate"], saved["calculation"], saved["development_evidence"]
    coverage = build(*metadata)
    assert coverage["registry"]["hypothesis_count"] == 108
    assert coverage["source"]["recipe_version"] == METHOD_VERSION
    assert coverage["totals"]["proposals"] == 192
    assert coverage["totals"]["retained_histories"] == coverage["totals"]["evaluated"]
    expected = {
        "momentum_10_gt0__all",
        "momentum_10_gt2__all",
        "momentum_60_gt0__all",
        "momentum_60_gt2__all",
    }
    for fit in saved["calculation"]["fits"].values():
        assert expected <= {row["params"]["hypothesis"] for row in fit["trials"]}
    store, envelope, digest = seed(app, metadata, status="completed", recipe=METHOD_VERSION)
    original = artifacts.read_artifact
    calls = len(engine.calls)

    def only_metadata(actual_store, actual_digest, **kwargs):
        assert actual_digest == digest, "Inspection tried reading candles or a history shard"
        return original(actual_store, actual_digest, **kwargs)

    monkeypatch.setattr(artifacts, "read_artifact", only_metadata)
    response = client.get(path())
    assert response.status_code == 200 and response.json["available"], response.json
    assert response.json["coverage"] == coverage
    assert len(engine.calls) == calls
