"""Complete comparisons must distinguish interaction changes from scalar repeats."""

from copy import deepcopy

import pytest

from research.report_contract import settings_identity
from services.research_configuration_context import build, difference


def settings():
    return [
        {
            "id": "one",
            "name": "Saved signals",
            "allocation_pct": 100,
            "config": {
                "initial_capital": 491000.0,
                "order_size_pct": 1.5,
                "hold_sessions": 23,
                "trailing_enabled": True,
                "trailing_pct": 9.4,
            },
            "execution_policy": {"exit_mode": "percent", "risk_limits": {"max_symbol_pct": 25}},
        }
    ]


def reconstruct(context, key):
    node = context["configurations"][key]
    if "settings" in node:
        return deepcopy(node["settings"])
    result = reconstruct(context, node["base"])
    for change in node["operations"]:
        parent = result
        for part in change["path"][:-1]:
            parent = parent[part]
        if change["op"] == "remove":
            del parent[change["path"][-1]]
        else:
            parent[change["path"][-1]] = change["value"]
    return result


def test_same_horizon_does_not_hide_different_exit_risk_capital_and_size():
    base = settings()
    old = deepcopy(base)
    old[0]["config"].update(initial_capital=100000, order_size_pct=3)
    option = deepcopy(base)
    option[0]["execution_policy"].update(
        exit_mode="atr", atr_period=14, atr_stop_multiple=2, atr_target_multiple=4
    )
    option[0]["config"].update(trailing_enabled=False, trailing_pct=0.0)
    context = build(
        base,
        [{"evidence_id": "failed-percent", "settings": old, "outcome": "rejected"}],
        [{"id": "atr-option", "settings": option}],
    )
    candidate = context["candidates"][0]
    assert reconstruct(context, context["current"]) == base
    assert reconstruct(context, candidate["configuration_id"]) == option
    prior = context["previous"][0]
    assert reconstruct(context, prior["configuration_id"]) == old
    paths = {tuple(row["path"]) for row in candidate["closest_previous"][0]["operations"]}
    assert (0, "config", "initial_capital") in paths
    assert (0, "config", "order_size_pct") in paths
    assert (0, "execution_policy", "exit_mode") in paths
    assert (0, "config", "trailing_enabled") in paths
    assert candidate["configuration_id"] != prior["configuration_id"]


def test_patch_preserves_removals_additions_and_numeric_identity_without_mutating_inputs():
    base = settings()
    original = deepcopy(base)
    changed = deepcopy(base)
    changed[0]["config"]["initial_capital"] = 491000
    del changed[0]["config"]["trailing_pct"]
    changed[0]["execution_policy"]["new_control"] = False
    context = build(base, [], [{"id": "changed", "settings": changed}])
    assert reconstruct(context, context["candidates"][0]["configuration_id"]) == changed
    assert settings_identity(
        reconstruct(context, context["candidates"][0]["configuration_id"])
    ) == settings_identity(changed)
    assert base == original
    assert difference(base, original) == []


def test_duplicate_complete_config_is_stored_once_but_each_evidence_identity_is_retained():
    base = settings()
    context = build(
        base,
        [{"evidence_id": "prior", "settings": deepcopy(base), "outcome": "rejected"}],
        [{"id": "same", "settings": deepcopy(base)}],
    )
    assert len(context["configurations"]) == 1
    assert context["previous"][0]["evidence_id"] == "prior"
    assert context["candidates"][0]["closest_previous"][0]["operations"] == []


@pytest.mark.parametrize("history,options", [(25, 0), (0, 9)])
def test_comparison_history_is_bounded(history, options):
    with pytest.raises(ValueError, match="bound"):
        build(settings(), [{}] * history, [{}] * options)
