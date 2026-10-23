"""Supplied history selects honest research scope without changing trade rules."""

from copy import deepcopy

import pytest
from test_automatic_protocol import evidence

from research.automatic_protocol import (
    ALLOCATION_VERSION,
    check_signal_span,
    compile_recipe,
    normalize_auto,
)
from research.portfolio import normalize
from services.research_library import portfolio_payload


def available(count=160, hold=45):
    value = evidence(signal_sessions=count, tail=90, config={"hold_sessions": hold})
    value["portfolio"]["automatic_research"] = {
        "version": ALLOCATION_VERSION,
        "history_policy": "available_history",
    }
    value["portfolio"] = normalize(value["portfolio"])
    value["strategies"] = [
        {**original, **canonical}
        for original, canonical in zip(
            value["strategies"], value["portfolio"]["strategies"], strict=True
        )
    ]
    return value


def test_160_sessions_keep_45_day_baseline_and_all_408_proposals():
    value = available()
    before = deepcopy(value)
    recipe = compile_recipe(value)
    assert value == before
    assert recipe["validation_mode"] == "exploratory_only"
    assert recipe["history_availability"]["fit_prefixes"] == {
        "fit1": 103,
        "fit2": 122,
        "fit3": 141,
        "search": 160,
    }
    assert recipe["history_availability"]["holding"]["max_sessions"] == 82
    assert recipe["baseline"][0]["config"]["hold_sessions"] == 45
    assert recipe["ranges"][0]["search"]["hold_sessions"]["max"] == 82
    assert recipe["budget"]["proposals"] == 408
    assert recipe["budget"]["max_simulations"] == 512
    assert recipe["budget"]["fits"] == 6
    assert recipe["budget"]["max_final_verification_evaluations"] == 0
    assert "walk_forward" not in recipe
    assert not {"check1", "check2", "check3", "final"}.intersection(recipe["periods"])


@pytest.mark.parametrize("count", [1, 3, 10, 30, 80])
def test_small_valid_files_do_not_invent_sessions_or_shorten_holding(count):
    value = available(count)
    check_signal_span(
        value["portfolio"], {"date_from": "2026-01-01", "date_to": "2026-01-01"}, interval="D"
    )
    recipe = compile_recipe(value)
    assert recipe["validation_mode"] == "exploratory_only"
    assert recipe["history_availability"]["session_count"] == count
    assert all(
        1 <= size <= count for size in recipe["history_availability"]["fit_prefixes"].values()
    )
    assert recipe["baseline"][0]["config"]["hold_sessions"] == 45


def test_longer_history_retains_all_later_checks_as_historical_comparisons():
    recipe = compile_recipe(available(200, hold=2))
    assert recipe["validation_mode"] == "historical_only"
    assert len(recipe["walk_forward"]["folds"]) == 3
    assert recipe["history_availability"]["independently_validated"] is False
    assert recipe["budget"]["proposals"] == 408


def test_strict_saved_v16_recipe_is_not_silently_changed():
    value = available()
    value["portfolio"]["automatic_research"] = {"version": ALLOCATION_VERSION}
    with pytest.raises(ValueError, match="more signal history"):
        compile_recipe(value)
    assert normalize_auto({"version": ALLOCATION_VERSION}) == {"version": ALLOCATION_VERSION}


def test_available_history_still_rejects_invalid_or_reversed_source_dates():
    portfolio = available()["portfolio"]
    for first, last in (("invalid", "2026-01-01"), ("2026-02-01", "2026-01-01")):
        with pytest.raises(ValueError):
            check_signal_span(portfolio, {"date_from": first, "date_to": last}, interval="D")


@pytest.mark.parametrize("policy", [True, None, "skip_checks", 1])
def test_undeclared_policy_cannot_bypass_validation(policy):
    with pytest.raises(ValueError, match="available-history"):
        normalize_auto({"version": ALLOCATION_VERSION, "history_policy": policy})


def test_new_editable_library_launch_uses_available_history_without_mutating_draft():
    value = available()
    draft = {"portfolio": value["portfolio"], "optimizing": True, "optimization": {}}
    draft["portfolio"]["automatic_research"] = {"version": ALLOCATION_VERSION}
    before = deepcopy(draft)
    payload = portfolio_payload(draft)
    assert draft == before
    assert payload["automatic_research"] == {
        "version": ALLOCATION_VERSION,
        "history_policy": "available_history",
        "design_policy": "automatic_design_v1",
    }
