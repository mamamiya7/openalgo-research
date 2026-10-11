"""Executable preflight descriptions stay aligned with the frozen native recipes."""

import json
import subprocess
import sys
from copy import deepcopy
from math import prod

import pytest

from research.automatic_protocol import (
    CONDITION_VERSION,
    STABILITY_VERSION,
    VERSION,
    compile_recipe,
)
from research.connectors.optuna_portfolio import _prepare, _values, validate_specification
from research.portfolio import fingerprint, normalize
from services.research_automatic_catalog import CATALOG_VERSION, NEXT_RECIPE_VERSION, build_plan


def request(*, config=None, version=VERSION, engine="vectorbt", strategies=1):
    return {
        "engine": engine,
        "name": "Scanner research",
        "capital": 123456,
        "strategies": [
            {
                "id": f"scanner{i}",
                "name": f"Scanner {i}",
                "source_id": f"{i + 1:032x}",
                "allocation_pct": 100 / strategies,
                "config": deepcopy(config or {}),
            }
            for i in range(strategies)
        ],
        "automatic_research": {"version": version},
    }


def parameters(plan, i=0):
    return {row["id"]: row for row in plan["strategies"][i]["parameters"]}


def families(plan):
    return {row["id"]: row for row in plan["families"]}


@pytest.mark.parametrize(
    "version,cap", [(VERSION, 70), (CONDITION_VERSION, 100), (STABILITY_VERSION, 118)]
)
def test_recipe_plan_is_read_only_deterministic_and_independently_fingerprinted(version, cap):
    raw = request(version=version)
    original = deepcopy(raw)
    normalized = normalize(raw)
    plan = build_plan(raw, interval="D")
    assert raw == original
    assert normalize(raw) == normalized
    assert build_plan(normalized, interval="D") == plan
    assert build_plan(raw, interval="D") == plan
    assert plan["plan_id"] == fingerprint(
        {key: value for key, value in plan.items() if key != "plan_id"}
    )
    assert plan["version"] == CATALOG_VERSION
    assert plan["execution_recipe_version"] == version
    assert plan["summary"] == {
        "searched_parameters": 3,
        "condition_presets": 4 if version == CONDITION_VERSION else 0,
        "max_simulations": cap,
    }
    assert plan["data"]["coverage_verified"] is False
    assert plan["validation"]["status"] == "requires_frozen_evidence"
    assert plan["next_recipe"] == {
        **plan["next_recipe"],
        "status": "planned",
        "version": NEXT_RECIPE_VERSION,
    }
    # Every returned collection is per-call, including nested feature recipes.
    plan["families"][0]["status"] = "invented"
    assert build_plan(raw, interval="D")["families"][0]["status"] == "search"


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"target_pct": 0.014, "stop_pct": 0.01, "hold_sessions": 252},
        {"target_pct": 500, "stop_pct": 99, "trailing_enabled": True, "trailing_pct": 99},
        {
            "trade_horizon": "intraday",
            "hold_minutes": 1,
            "trailing_enabled": True,
            "trailing_pct": 0.01,
        },
    ],
)
def test_announced_domains_are_exact_native_optuna_axes(config):
    raw = request(config=config)
    canonical = normalize(raw)
    plan = build_plan(raw, interval="1m" if config.get("hold_minutes") else "D")
    assert (
        validate_specification(canonical["optimization"], canonical["strategies"])
        == canonical["optimization"]
    )
    _, real_axes = _prepare(canonical["strategies"])
    described = {
        f"{row['id']}.{item['id']}": item["domain"]
        for row in plan["strategies"]
        for item in row["parameters"]
        if item["status"] == "search"
    }
    assert set(described) == set(real_axes)
    for key, axis in real_axes.items():
        domain = described[key]
        assert domain["values"] == _values(axis)
        assert domain["count"] == len(_values(axis)) <= 7
        assert {field: domain[field] for field in ("min", "max", "step")} == axis
    assert plan["search"]["numeric_space_size"] == str(
        prod(len(_values(axis)) for axis in real_axes.values())
    )
    assert plan["search"]["exhaustive"] is False


def test_baseline_outside_rounded_grid_is_not_silently_replaced():
    raw = request(config={"target_pct": 0.014})
    p = parameters(build_plan(raw, interval="D"))["target_pct"]
    assert p["current"] == 0.014
    assert 0.014 not in p["domain"]["values"]
    assert normalize(raw)["strategies"][0]["config"]["target_pct"] == 0.014


@pytest.mark.parametrize(
    "config,active,inactive",
    [
        ({}, "hold_sessions", None),
        ({"hold_minutes": 45}, "hold_minutes", "hold_sessions"),
        ({"trade_horizon": "intraday"}, None, "hold_sessions"),
    ],
)
def test_only_active_holding_deadline_is_searched(config, active, inactive):
    p = parameters(build_plan(request(config=config), interval="1m" if config else "D"))
    if active:
        assert p[active]["status"] == "search"
    if inactive:
        assert p[inactive]["status"] == "unavailable"
        assert "domain" not in p[inactive]
    assert {
        key for key, value in p.items() if key.startswith("hold_") and value["status"] == "search"
    } == ({active} if active else set())


def test_trailing_activation_is_not_claimed_when_only_distance_can_be_searched():
    disabled = build_plan(request(), interval="D")
    assert parameters(disabled)["trailing_pct"]["status"] == "unavailable"
    assert parameters(disabled)["trailing_enabled"]["status"] == "fixed"
    assert families(disabled)["trailing_activation"]["status"] == "planned"
    enabled = build_plan(
        request(config={"trailing_enabled": True, "trailing_pct": 3}), interval="D"
    )
    assert parameters(enabled)["trailing_pct"]["status"] == "search"
    assert parameters(enabled)["trailing_enabled"]["status"] == "fixed"


def test_costs_and_account_risk_are_fixed_with_an_explicit_bounded_stress_case():
    raw = request(config={"cost_bps": 250, "slippage_bps": 7, "order_size_pct": 12})
    plan = build_plan(raw, interval="D")
    p = parameters(plan)
    for key in (
        "initial_capital",
        "order_size_pct",
        "allocation_pct",
        "max_exposure_pct",
        "cost_bps",
        "slippage_bps",
    ):
        assert p[key]["status"] == "fixed"
        assert "domain" not in p[key]
    assert p["initial_capital"]["current"] == 123456
    assert plan["validation"]["cost_stress"] == [
        {"strategy_id": "scanner0", "base_bps": 250, "stressed_bps": 500, "slippage_bps": 7}
    ]
    assert (
        build_plan(request(config={"cost_bps": 0}), interval="D")["validation"]["cost_stress"][0][
            "stressed_bps"
        ]
        == 5
    )
    with pytest.raises(ValueError, match="250 bps"):
        build_plan(request(config={"cost_bps": 251}), interval="D")


def test_readonly_plan_does_not_upgrade_legacy_recipe_or_frozen_recipe_hash():
    from test_automatic_protocol import evidence

    source = evidence()
    encoded = json.dumps(source, sort_keys=True)
    frozen = compile_recipe(source)
    build_plan(source["portfolio"], interval="D")
    assert compile_recipe(source) == frozen
    assert json.dumps(source, sort_keys=True) == encoded
    assert frozen["version"] == VERSION


@pytest.mark.parametrize("version", [VERSION, CONDITION_VERSION, STABILITY_VERSION])
def test_budget_and_support_gates_match_compiled_execution_recipe(version):
    from test_automatic_protocol import evidence

    source = evidence()
    source["portfolio"]["automatic_research"]["version"] = version
    compiled = compile_recipe(source)
    plan = build_plan(source["portfolio"], interval="D")
    assert plan["search"]["budget"]["max_simulations"] == compiled["budget"]["max_simulations"]
    assert plan["search"]["proposals"] == compiled["budget"]["proposals"]
    assert plan["validation"]["max_finalists"] == compiled["selection"]["max_finalists"]
    assert (
        plan["validation"]["min_closed_trades_per_check"]
        == compiled["selection"]["min_closed_trades"]
    )
    assert (
        plan["validation"]["min_distinct_entry_dates_per_check"]
        == compiled["selection"]["min_signal_dates"]
    )
    assert plan["validation"]["max_drawdown_pct"] == compiled["selection"]["max_drawdown_pct"]
    if version == STABILITY_VERSION:
        assert (
            plan["search"]["budget"]["max_neighbors_per_finalist"]
            == compiled["stability"]["max_neighbors"]
        )
        assert (
            plan["search"]["stability"]["required_pass_fraction"]
            == compiled["stability"]["min_pass_fraction"]
        )


def test_market_screen_is_exact_existing_catalog_and_auxiliary_history_is_not_scored():
    from research.automatic_conditions import catalog

    raw = request(version=CONDITION_VERSION)
    canonical = normalize(raw)
    plan = build_plan(raw, interval="1m")
    expected = []
    for row in catalog(canonical["strategies"]):
        rule = row["entry_conditions"]["rules"][0] if row["entry_conditions"] else {}
        expected.append(
            {
                "label": row["label"],
                "dimension": rule.get("dimension"),
                "regime": rule.get("regime"),
            }
        )
    assert plan["search"]["condition_presets"] == expected
    auxiliary = plan["data"]["auxiliary"][0]
    assert {
        key: auxiliary[key]
        for key in ("symbol", "exchange", "interval", "warmup_sessions", "scored")
    } == {
        "symbol": "NIFTY",
        "exchange": "NSE_INDEX",
        "interval": "D",
        "warmup_sessions": 160,
        "scored": False,
    }
    assert auxiliary["feature_recipe"]["required_sessions"] == 150
    assert plan["data"]["execution"]["interval"] == "1m"
    assert families(plan)["market_conditions"]["status"] == "search"
    assert plan["search"]["budget"]["max_condition_candidates"] == 16
    assert "miss settings" in plan["limitations"][-1]
    assert build_plan(request(), interval="D")["data"]["auxiliary"] == []


@pytest.mark.parametrize("engine", ["vectorbt", "nautilus"])
def test_future_families_have_explicit_admission_blockers_and_no_executable_domains(engine):
    plan = build_plan(request(engine=engine), interval="D")
    future = families(plan)
    for key in (
        "stock_trend",
        "stock_momentum",
        "stock_rsi",
        "relative_strength",
        "bollinger",
        "atr",
        "liquidity",
        "filter_stacks",
        "walk_forward",
        "portfolio_risk_search",
    ):
        assert future[key]["status"] == "planned"
        assert future[key]["requirements"] and future[key]["blockers"]
        assert "domain" not in future[key]
    assert future["market_conditions"]["status"] == "unavailable"
    assert "missing volume" in future["liquidity"]["requirements"][0].lower()
    assert "initialization" in future["stock_trend"]["requirements"][0]
    if engine == "nautilus":
        assert "Nautilus" in parameters(plan)["trailing_pct"]["reason"]
        assert plan["data"]["engine_limits"]["max_signals"] == 2500


def test_conditions_cannot_be_announced_for_an_unsupported_engine():
    with pytest.raises(ValueError, match="VectorBT"):
        build_plan(request(version=CONDITION_VERSION, engine="nautilus"), interval="D")


@pytest.mark.parametrize(
    "version,proposals,cap",
    [
        (VERSION, 50, 70),
        (STABILITY_VERSION, 50, 118),
        ("automatic-hypotheses-v4", 96, 240),
        ("automatic-hypotheses-v5", 128, 320),
        ("automatic-hypotheses-v6", 128, 320),
    ],
)
def test_decision_summary_matches_recipe_budget_and_never_certifies_pending_evidence(
    version, proposals, cap
):
    raw = request(version=version, config={"cost_bps": 17, "order_size_pct": 12})
    original = deepcopy(raw)
    canonical = normalize(raw)
    result = build_plan(raw, interval="D")
    decisions = result["decisions"]
    automatic = {row["id"]: row for row in decisions["automatic"]}
    assert automatic["interval"]["decision"] == "Daily candles"
    assert f"{proposals} proposals" in automatic["search"]["decision"]
    assert f"{cap} calculations" in automatic["search"]["decision"]
    assert decisions["scope"]["searched_parameters"] == result["summary"]["searched_parameters"]
    assert decisions["scope"]["exhaustive"] is False
    assert "no global optimum" in decisions["scope"]["recommendation_basis"]
    assert all(row["status"] == "pending" for row in decisions["prerequisites"])
    assert {row["id"] for row in decisions["prerequisites"]} == {
        "coverage",
        "calendar",
        "runtime",
        "validation",
    }
    assert {row["id"] for row in decisions["retained"]} == {"risk", "costs", "timing"}
    assert raw == original
    assert normalize(raw) == canonical
    assert result["execution_recipe_version"] == version
    if version.startswith("automatic-hypotheses-"):
        assert "exploratory" in automatic["checks"]["decision"]
    else:
        assert "fails admission" in automatic["checks"]["decision"]
    decisions["automatic"][0]["decision"] = "invented"
    assert build_plan(raw, interval="D")["decisions"]["automatic"][0]["decision"] == "Daily candles"


def test_minute_and_trailing_decisions_preserve_original_horizon_and_disabled_branch():
    raw = request(version="automatic-hypotheses-v5", config={"hold_minutes": 45})
    plan = build_plan(raw, interval="1m")
    automatic = {row["id"]: row for row in plan["decisions"]["automatic"]}
    omitted = {row["id"]: row for row in plan["decisions"]["not_searched"]}
    assert automatic["interval"]["decision"] == "One-minute candles"
    assert "already enabled" in omitted["trailing_activation"]["reason"]
    assert "qualified observed volume" in omitted["liquidity"]["reason"]
    assert parameters(plan)["hold_minutes"]["status"] == "search"
    assert parameters(plan)["hold_sessions"]["status"] == "unavailable"
    assert parameters(plan)["trailing_enabled"]["current"] is False
    assert "trailing_pct" not in normalize(raw)["strategies"][0]["search"]


def test_nautilus_decision_does_not_offer_unsupported_trailing_or_upgrade_recipe():
    plan = build_plan(request(engine="nautilus"), interval="D")
    omitted = {row["id"]: row for row in plan["decisions"]["not_searched"]}
    assert "does not support trailing stops" in omitted["trailing_activation"]["reason"]
    assert plan["execution_recipe_version"] == VERSION
    assert "liquidity" not in omitted


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"trailing_enabled": False, "trailing_pct": 4},
        {"trailing_enabled": True, "trailing_pct": 3},
    ],
)
def test_conditional_trailing_plan_matches_frozen_domain_and_keeps_original_settings(config):
    from research.automatic_trailing import recipe

    raw = request(version="automatic-hypotheses-v6", config=config)
    original = deepcopy(raw)
    canonical = normalize(raw)
    result = build_plan(raw, interval="D")
    described = parameters(result)
    frozen = recipe(canonical["strategies"])
    assert raw == original
    assert result["research_plan"]["trailing_research"] == frozen
    assert described["trailing_enabled"]["choices"] == [False, True]
    assert (
        described["trailing_enabled"]["current"]
        == canonical["strategies"][0]["config"]["trailing_enabled"]
    )
    assert (
        described["trailing_pct"]["current"] == canonical["strategies"][0]["config"]["trailing_pct"]
    )
    assert described["trailing_pct"]["condition"] == {"trailing_enabled": True}
    assert described["trailing_pct"]["domain"]["values"] == frozen["strategies"][0]["choices"][1:]
    assert "trailing_pct" not in canonical["strategies"][0]["search"]
    assert {described[key]["status"] for key in ("trailing_enabled", "trailing_pct")} == {"search"}
    assert "trailing_activation" not in {row["id"] for row in result["decisions"]["not_searched"]}
    assert families(result)["trailing_activation"]["status"] == "admitted"
    assert result["summary"]["max_simulations"] == 320
    assert result["research_plan"]["budget"]["screening_proposals"] == 96
    assert (
        "trailing off"
        in next(row for row in result["decisions"]["automatic"] if row["id"] == "ranges")[
            "decision"
        ]
    )


def test_stability_budget_and_causal_gates_are_explicit_and_version_specific():
    plan = build_plan(request(version=STABILITY_VERSION), interval="D")
    assert plan["search"]["budget"]["max_neighbor_simulations"] == 3 * 8 * 2
    assert plan["search"]["budget"]["max_simulations"] == 70 + 48
    assert plan["search"]["stability"]["required_pass_fraction"] == 0.75
    assert plan["search"]["stability"]["final_data_used"] is False
    assert families(plan)["parameter_stability"]["status"] == "validation"
    assert "stability" not in build_plan(request(), interval="D")["search"]
    assert (
        families(build_plan(request(), interval="D"))["parameter_stability"]["status"]
        == "unavailable"
    )


@pytest.mark.parametrize(
    "interval,config",
    [("5m", {}), (None, {}), ("D", {"hold_minutes": 2}), ("D", {"entry_time": "10:00"})],
)
def test_wrong_resolution_is_not_silently_treated_as_daily(interval, config):
    with pytest.raises(ValueError, match="interval"):
        build_plan(request(config=config), interval=interval)


def test_nonautomatic_or_unimplemented_recipe_is_not_announced_as_executable():
    raw = request()
    raw.pop("automatic_research")
    with pytest.raises(ValueError, match="automatic"):
        build_plan(raw, interval="D")
    with pytest.raises(ValueError, match="recipe"):
        build_plan(request(version=NEXT_RECIPE_VERSION), interval="D")


def test_eight_strategies_use_exact_string_space_size_and_do_not_multiply_run_budget():
    plan = build_plan(
        request(
            strategies=8,
            config={
                "target_pct": 12,
                "stop_pct": 6,
                "hold_sessions": 12,
                "trailing_enabled": True,
                "trailing_pct": 6,
            },
        ),
        interval="D",
    )
    assert plan["summary"]["searched_parameters"] == 32
    space = plan["search"]["numeric_space_size"]
    assert isinstance(space, str) and int(space) > 2**53
    assert plan["summary"]["max_simulations"] == 70
    assert len({row["id"] for row in plan["strategies"]}) == 8


def test_no_ad_hoc_grid_or_proposal_budget_can_change_automatic_plan():
    original = request()
    modified = deepcopy(original)
    modified["strategies"][0]["search"] = {"order_size_pct": {"min": 5, "max": 100, "step": 5}}
    modified["optimization"] = {"sampler": "grid", "trials": 1000}
    assert build_plan(original, interval="D") == build_plan(modified, interval="D")


@pytest.mark.parametrize(
    "version",
    [
        VERSION,
        STABILITY_VERSION,
        CONDITION_VERSION,
        "automatic-hypotheses-v4",
        "automatic-hypotheses-v5",
        "automatic-hypotheses-v6",
    ],
)
def test_plan_needs_no_numerical_runtime_broker_service_or_database_import(version):
    script = """
import importlib.abc, json, sys
class BlockResources(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ("vectorbt", "optuna", "nautilus_trader", "sqlalchemy", "database", "services.history_service", "services.scanner_research_service")
        if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
            raise AssertionError("Preflight loaded resources: " + fullname)
sys.meta_path.insert(0, BlockResources())
from services.research_automatic_catalog import build_plan
raw = json.loads(sys.stdin.read())
engines = ("vectorbt", "nautilus") if raw["automatic_research"]["version"] in ("automatic-trade-management-v1", "automatic-trade-management-v2") else ("vectorbt",)
for engine in engines:
    raw["engine"] = engine
    assert build_plan(raw, interval="D")["phase"] == "preflight"
print("pure")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(request(version=version)),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pure"
