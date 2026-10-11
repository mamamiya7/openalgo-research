"""Capability admission and holding-feasible chronology before observing outcomes."""

from copy import deepcopy

import pytest
from test_automatic_protocol import evidence

from research.automatic_protocol import (
    HYPOTHESIS_VERSION,
    automatic_portfolio,
    compile_recipe,
    slice_period,
)
from research.intelligent_plan import build_plan, chronology
from research.portfolio import fingerprint


def smart(**kwargs):
    value = evidence(**kwargs)
    value["portfolio"] = automatic_portfolio(
        {**value["portfolio"], "automatic_research": {"version": HYPOTHESIS_VERSION}}
    )
    for row in value["strategies"]:
        canonical = next(s for s in value["portfolio"]["strategies"] if s["id"] == row["id"])
        row["config"] = canonical["config"]
        row["search"] = canonical["search"]
    return value


def test_plan_is_finite_capability_driven_and_does_not_read_results():
    value = smart(config={"hold_sessions": 1})
    before = deepcopy(value)
    plan = build_plan(value["portfolio"], interval="D")
    assert value == before
    assert plan["id"] == fingerprint({k: v for k, v in plan.items() if k != "id"})
    assert len(plan["hypotheses"]) == 7
    assert plan["budget"] == {
        "proposals": 60,
        "screening_proposals": 30,
        "refinement_proposals": 30,
        "max_simulations": 180,
        "max_finalists": 3,
        "max_neighbors": 4,
    }
    statuses = {r["id"]: r["status"] for r in plan["methods"]}
    assert statuses["stock_trend"] == statuses["stock_momentum"] == "admitted"
    assert statuses["original_scanner"] == statuses["liquidity"] == "unsupported"
    assert statuses["other_indicators"] == statuses["market_conditions"] == "deferred"
    assert statuses["portfolio_risk"] == "fixed"
    with pytest.raises(ValueError):
        build_plan({**value["portfolio"], "engine": "nautilus"}, interval="D")


def test_long_holding_history_is_exploratory_without_shrinking_requested_horizon():
    value = smart(config={"hold_sessions": 45})
    original = deepcopy(value["portfolio"])
    recipe = compile_recipe(value)
    assert recipe["validation_mode"] == "exploratory_only"
    assert len({(p["from"], p["to"]) for p in recipe["periods"].values()}) == 1
    assert value["portfolio"] == original
    assert value["portfolio"]["strategies"][0]["config"]["hold_sessions"] == 45
    value["automatic_recipe"] = recipe
    assert slice_period(value, "search")["automatic_period"]["eligible_signal_dates"] > 5


def test_normal_hold_has_distinct_feasible_windows_and_observed_intraday_counts():
    value = smart(config={"hold_sessions": 1})
    recipe = compile_recipe(value)
    assert recipe["validation_mode"] == "chronological"
    periods = list(recipe["periods"].values())
    assert all(a["to"] < b["from"] for a, b in zip(periods, periods[1:], strict=False))
    minute = smart(minute=True, config={"hold_sessions": 1})
    assert chronology(minute["portfolio"], 160, "1m", minute["snapshot"])[1] == "chronological"
    row = minute["portfolio"]["strategies"][0]
    row["search"]["hold_minutes"] = {"min": 2, "max": 200, "step": 1}
    # Only five observed minutes per date: do not invent a 375-minute session.
    assert chronology(minute["portfolio"], 160, "1m", minute["snapshot"])[1] == "exploratory_only"


def test_expanded_plan_admits_only_qualified_families_and_pins_native_math():
    value = smart(config={"hold_sessions": 1})
    value["portfolio"]["automatic_research"]["version"] = "automatic-hypotheses-v3"
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    plan = build_plan(value["portfolio"], interval="D")
    assert len(plan["hypotheses"]) == 18
    assert len({row["family"] for row in plan["hypotheses"]}) == 7
    assert plan["budget"]["proposals"] == 84
    assert plan["budget"]["max_simulations"] == 220
    assert plan["budget"]["screening_proposals"] == 56
    assert plan["data"]["warmup_sessions"] == 100
    statuses = {row["id"]: row["status"] for row in plan["methods"]}
    assert all(
        statuses[name] == "admitted" for name in ("stock_rsi", "bollinger", "atr", "filter_stacks")
    )
    assert statuses["market_conditions"] == statuses["other_indicators"] == "deferred"
    recipe = compile_recipe(value)
    assert recipe["budget"]["proposals"] == 84
    assert recipe["indicator_recipe"] == plan["indicator_recipe"]
    assert recipe["id"] == fingerprint({k: v for k, v in recipe.items() if k != "id"})


def test_relative_strength_plan_declares_one_benchmark_without_reinterpreting_v3():
    value = smart(config={"hold_sessions": 1})
    value["portfolio"]["automatic_research"]["version"] = "automatic-hypotheses-v4"
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    plan = build_plan(value["portfolio"], interval="1m")
    assert len(plan["hypotheses"]) == 23
    assert len({row["family"] for row in plan["hypotheses"]}) == 8
    assert plan["budget"]["proposals"] == 96
    assert plan["budget"]["screening_proposals"] == 64
    assert plan["budget"]["max_simulations"] == 240
    assert plan["data"]["benchmark"] == {
        "symbol": "NIFTY",
        "exchange": "NSE_INDEX",
        "interval": "D",
    }
    assert plan["data"]["benchmark_warmup_sessions"] == 100
    assert (
        next(row for row in plan["methods"] if row["id"] == "relative_strength")["status"]
        == "admitted"
    )
    assert (
        next(row for row in plan["methods"] if row["id"] == "market_conditions")["status"]
        == "deferred"
    )
    recipe = compile_recipe(value)
    assert recipe["indicator_recipe"] == plan["indicator_recipe"]
    assert recipe["budget"]["max_simulations"] == 240
    assert (
        "not stock return minus benchmark return"
        in recipe["indicator_recipe"]["relative_strength"]["interpretation"]
    )
