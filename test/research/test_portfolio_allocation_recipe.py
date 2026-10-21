"""Versioned bounded portfolio allocation recipe and exact recovery contracts."""

from copy import deepcopy

import pytest

from research.automatic_hypotheses import policy_for_version
from research.automatic_protocol import ALLOCATION_VERSION, SEED_VERSION, compile_recipe
from research.automatic_walk_forward import fit_names
from research.execution_search import ALLOCATION_AXIS, apply_choices, axes, original_sizing, probes
from research.portfolio import execution_versions, normalize
from research.procedure_seed_calibration import seed_plan, verify_seed_plan


def prepared():
    from test_recommendation_seeds import prepared as previous

    from research.portfolio_execution_features import prepare

    value = previous()
    value["portfolio"]["automatic_research"] = {"version": ALLOCATION_VERSION}
    for row in value["portfolio"]["strategies"]:
        row["execution_policy"].update(
            version="research-execution-aware-v5",
            allocation_mode="original",
            capacity_mode="disabled",
        )
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["versions"] = execution_versions(value["portfolio"])
    value.pop("automatic_recipe")
    value.pop("portfolio_execution_features")
    value = prepare(value, value["automatic_stock_market_context"])
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_new_recipe_retains_six_fit_budget_and_fixed_baseline_risk():
    value = prepared()
    recipe = value["automatic_recipe"]
    assert recipe["version"] == ALLOCATION_VERSION
    assert recipe["budget"]["proposals"] == 408
    assert recipe["budget"]["max_simulations"] == 512
    assert recipe["budget"]["fits"] == 6
    assert fit_names(ALLOCATION_VERSION) == fit_names(SEED_VERSION)
    assert policy_for_version(ALLOCATION_VERSION)["proposals_per_fit"] == 68
    assert recipe["execution_research"]["version"] == "research-execution-search-v5"
    assert recipe["execution_research"]["risk_limits"] == value["portfolio"]["risk_limits"]
    assert all(
        row["execution_policy"]["allocation_mode"] == "original" for row in recipe["baseline"]
    )
    assert value["versions"]["adapter_version"] == "vectorbt-portfolio-adapter-v6"
    assert value["versions"]["policy_version"] == "vectorbt-joint-causal-bars-v6"
    assert recipe["search_seeds"]["screening_proposals_per_search"] <= 50


def test_allocation_axis_is_accountwide_and_replaces_one_screen():
    value = prepared()
    spec = value["automatic_recipe"]["execution_research"]
    domain = axes(spec)
    assert domain[ALLOCATION_AXIS]["choices"] == ["original", "prior60_cluster_inverse_vol"]
    assert not any(key.endswith(".allocation_mode") and key != ALLOCATION_AXIS for key in domain)
    screened = probes({}, spec)
    assert len(screened) == 10
    cluster = [item for item in screened if item[ALLOCATION_AXIS] == "prior60_cluster_inverse_vol"]
    assert len(cluster) == 1
    settings = apply_choices(value["strategies"], cluster[0], spec)
    for original, candidate in zip(value["strategies"], settings, strict=True):
        assert candidate["execution_policy"]["allocation_mode"] == "prior60_cluster_inverse_vol"
        assert candidate["allocation_pct"] == original["allocation_pct"]
        assert candidate["config"]["order_size_pct"] == original["config"]["order_size_pct"]
        assert (
            candidate["execution_policy"]["risk_limits"]
            == original["execution_policy"]["risk_limits"]
        )
    control = original_sizing(settings, spec)
    assert all(row["execution_policy"]["allocation_mode"] == "original" for row in control)
    assert all(row["execution_policy"]["risk_limits"] == spec["risk_limits"] for row in control)


def test_new_recipe_cannot_normalize_an_already_selected_allocation_as_reference():
    value = prepared()
    request = deepcopy(value["portfolio"])
    request["strategies"][0]["execution_policy"]["allocation_mode"] = "prior60_cluster_inverse_vol"
    with pytest.raises(ValueError, match="fixed backtest|Custom search"):
        normalize(request)


def test_new_numerical_contract_refuses_old_recorded_versions():
    value = prepared()
    recorded = deepcopy(value["versions"])
    recorded["adapter_version"] = "vectorbt-portfolio-adapter-v5"
    with pytest.raises(ValueError, match="original engine versions"):
        execution_versions(value["portfolio"], recorded)


def test_offline_seed_qualification_is_explicitly_recipe_bound():
    original = seed_plan(0)
    declared = seed_plan(0, recipe_version=ALLOCATION_VERSION)
    assert "recipe_version" not in original
    assert declared["recipe_version"] == ALLOCATION_VERSION
    assert original["id"] != declared["id"]
    assert verify_seed_plan(declared) == declared
    assert declared["fit_sampler_seeds"] == original["fit_sampler_seeds"]


@pytest.mark.timeout(300)
def test_v16_exact_prefix_resume_and_cross_recipe_seed_rejection():
    from test_procedure_seed_recovery import Prefix

    value = prepared()
    runner = Prefix(value)
    plan = seed_plan(1, recipe_version=ALLOCATION_VERSION)
    runner.prefix(plan, 2)
    saved = deepcopy(runner.latest)
    calls = len(runner.engine.calls)
    runner.prefix(plan, 3, saved=saved)
    assert runner.latest["fits"]["fit1"]["trials"][:2] == saved["fits"]["fit1"]["trials"]
    assert len(runner.engine.calls) - calls <= 1
    with pytest.raises(ValueError, match="different recipe"):
        runner.prefix(seed_plan(1), 4, saved=runner.latest)


@pytest.mark.timeout(600)
def test_native_allocation_screen_resume_keeps_actual_fills_and_histories():
    from test_automatic_walk_forward import Stopped
    from test_procedure_seed_recovery import Prefix

    from research.automatic_timing import axes as timing_axes
    from research.automatic_trailing import axes as trailing_axes
    from research.automatic_walk_forward import _screening_program, run
    from research.connectors.optuna_portfolio import _prepare
    from research.connectors.vectorbt_portfolio import evaluate

    value = prepared()
    recipe = value["automatic_recipe"]
    _, numerical = _prepare(value["strategies"])
    dimensions = {
        **numerical,
        **trailing_axes(recipe["trailing_research"]),
        **timing_axes(recipe["timing_research"]),
        **axes(recipe["execution_research"]),
    }
    screens = _screening_program(
        ALLOCATION_VERSION,
        recipe["hypothesis_catalog"],
        dimensions,
        recipe["timing_research"],
        recipe["execution_research"],
    )
    boundary = next(
        i + 1
        for i, params in enumerate(screens)
        if params[ALLOCATION_AXIS] == "prior60_cluster_inverse_vol"
    )
    retention = Prefix(value)
    latest = None
    calls = []
    cluster_reports = []

    def native(rows, snapshot, capital, progress=None):
        result = evaluate(rows, snapshot, capital, progress=progress)
        calls.append(len(calls) + 1)
        if rows[0]["execution_policy"]["allocation_mode"] == "prior60_cluster_inverse_vol":
            cluster_reports.append(result)
        return result

    def checkpoint(state, counts):
        nonlocal latest
        latest = deepcopy(state)
        if len(state["fits"].get("fit1", {}).get("trials", [])) >= boundary:
            raise Stopped()

    common = {
        "evaluate": native,
        "checkpoint": checkpoint,
        "progress": lambda *args: None,
        "cancelled": lambda: None,
        "defer_study_analysis": True,
        "retain_evaluation": retention.retain,
        "read_evaluation": lambda key: deepcopy(retention.shards.get(key)),
    }
    with pytest.raises(Stopped):
        run(value, **common)
    prefix = deepcopy(latest)
    histories = {key: shard["id"] for key, shard in retention.shards.items()}
    call_count = len(calls)
    assert cluster_reports
    assert any(row["quantity"] > 0 for report in cluster_reports for row in report["ledger"])
    assert all(
        report["portfolio_constraint_evidence"]["status"] == "supported"
        for report in cluster_reports
    )
    with pytest.raises(Stopped):
        run(value, saved=prefix, **common)
    assert calls[call_count:] == []
    assert latest["fits"]["fit1"]["trials"] == prefix["fits"]["fit1"]["trials"]
    assert {key: shard["id"] for key, shard in retention.shards.items()} == histories
