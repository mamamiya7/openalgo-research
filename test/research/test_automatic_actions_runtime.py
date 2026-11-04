"""Full bounded orchestration with controlled economics, not fill qualification."""

# ruff: noqa: F811 -- explicitly reused isolated fixtures
from copy import deepcopy

import pytest
from test_automatic_decision_runtime import deciding  # noqa: F401
from test_automatic_design_runtime import NativeShapeAccountEngine, designed  # noqa: F401
from test_automatic_walk_forward import execute
from test_available_history_runtime import (
    available,  # noqa: F401
    install_controlled_gate_and_account_audit,
)

from research.automatic_protocol import compile_recipe, normalize_auto
from research.connectors.optuna_portfolio import _fingerprint
from research.execution_search import ACTION_SEARCH_VERSION, ACTIVE_MODES, axes, descriptor, probes
from research.portfolio import execution_versions, normalize
from research.report_contract import fingerprint


@pytest.fixture(scope="module")
def acting(deciding):
    value = deepcopy(deciding)
    value["portfolio"]["automatic_research"]["action_policy"] = "automatic_actions_v1"
    value["portfolio"] = normalize(value["portfolio"])
    value["versions"] = execution_versions(value["portfolio"])
    value.pop("automatic_recipe")
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_actions_are_explicit_and_old_frozen_recipes_keep_their_modes(acting, deciding):
    request = acting["portfolio"]["automatic_research"]
    assert normalize_auto(request) == request
    with pytest.raises(ValueError, match="decision"):
        normalize_auto({"version": request["version"], "action_policy": request["action_policy"]})
    new = acting["automatic_recipe"]
    old = deciding["automatic_recipe"]
    assert "automatic_actions" not in old
    assert new["execution_research"]["version"] == ACTION_SEARCH_VERSION
    assert new["execution_research"]["allocation_modes"] == list(ACTIVE_MODES)
    assert old["execution_research"]["allocation_modes"] == [
        "original",
        "prior60_cluster_inverse_vol",
    ]
    assert new["budget"] == old["budget"]
    assert len(probes({}, new["execution_research"])) == 10
    assert axes(new["execution_research"])["portfolio.allocation_mode"]["choices"] == list(
        ACTIVE_MODES
    )
    assert descriptor(acting) == new["execution_research"]


def test_intended_sizing_actions_are_versioned_without_changing_search_budget(acting):
    from research.automatic_actions import descriptor as action_descriptor

    value = deepcopy(acting)
    value["portfolio"]["automatic_research"]["action_policy"] = "automatic_actions_v2"
    value["portfolio"] = normalize(value["portfolio"])
    value["versions"] = execution_versions(value["portfolio"])
    value.pop("automatic_recipe")
    recipe = compile_recipe(value)
    assert recipe["capital_planning"]["version"] == "research-capital-planning-v4"
    assert recipe["automatic_actions"] == action_descriptor("automatic_actions_v2")
    assert recipe["budget"] == acting["automatic_recipe"]["budget"]
    assert recipe["execution_research"] == acting["automatic_recipe"]["execution_research"]
    assert (
        acting["automatic_recipe"]["capital_planning"]["version"] == "research-capital-planning-v3"
    )


def test_closed_loop_policy_reserves_checks_without_changing_search_or_old_recipes(acting):
    from research.automatic_walk_forward import _account_reserve
    from research.operating_validation import descriptor as operating_descriptor

    value = deepcopy(acting)
    value["portfolio"]["automatic_research"]["action_policy"] = "automatic_actions_v3"
    value["portfolio"] = normalize(value["portfolio"])
    value["versions"] = execution_versions(value["portfolio"])
    value.pop("automatic_recipe")
    recipe = compile_recipe(value)
    assert recipe["capital_planning"]["version"] == "research-capital-planning-v5"
    assert recipe["operating_validation"] == operating_descriptor()
    assert recipe["execution_research"] == acting["automatic_recipe"]["execution_research"]
    assert recipe["budget"]["proposals"] == 408
    assert recipe["budget"]["max_simulations"] == 512
    assert recipe["budget"]["max_operating_validation_accounts"] == 13
    assert _account_reserve(recipe, "search", planning=True) == 13
    assert _account_reserve(recipe, "search", operating=True) == 0
    assert _account_reserve(recipe, "search", decision=True) == 17
    assert _account_reserve(recipe, "search") == 26
    assert "operating_validation" not in acting["automatic_recipe"]
    assert 6 * 69 + 4 + 1 + 64 + 4 + 9 + 13 == 509 <= 512


@pytest.fixture(scope="module")
def completed(request, acting):
    patch = pytest.MonkeyPatch()
    install_controlled_gate_and_account_audit(patch)
    request.addfinalizer(patch.undo)
    engine = NativeShapeAccountEngine(acting, lambda *args: None)
    result, state = execute(acting, engine)
    return result, state, engine.calls


def test_complete_program_keeps_budget_validation_and_immutable_primary_choice(acting, completed):
    result, state, calls = completed
    findings = result["automatic_research"]
    assert findings["counts"]["proposals"] == 408
    assert all(len(fit["trials"]) == 68 for fit in state["fits"].values())
    assert len(calls) == state["simulations"] <= state["attempts"] <= 512
    assert findings["capital_planning"]["version"] == "research-capital-planning-v3"
    assert len(state["automatic_actions"]["accounts"]) <= 4
    assert findings["decision"]["selected_config_id"] == state["selection"]["config_id"]
    assert findings["decision"]["challenge_plan"]["selection_changed"] is False
    assert not findings["decision"]["independently_validated"]
    assert not findings["capital_planning"]["performance_validated"]
    assert findings["capital_planning"]["policy"] == acting["automatic_recipe"]["capital_planning"]


def test_completed_actions_reopen_identically_with_zero_new_accounts(
    acting, completed, monkeypatch
):
    install_controlled_gate_and_account_audit(monkeypatch)
    expected, complete, _ = completed
    result, state = execute(
        acting, lambda *a, **kw: pytest.fail("Reopening attempted a new account"), saved=complete
    )
    assert result == expected
    assert state == complete


def test_resealed_action_receipt_tampering_is_rejected(acting, completed, monkeypatch):
    install_controlled_gate_and_account_audit(monkeypatch)
    _, complete, _ = completed
    changed = deepcopy(complete)
    row = changed["automatic_actions"]["accounts"][0]["receipt"]
    row["verified_sufficient"] = not row["verified_sufficient"]
    row["id"] = fingerprint({k: v for k, v in row.items() if k != "id"})
    changed["checkpoint_id"] = _fingerprint(
        {k: v for k, v in changed.items() if k != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="action evidence"):
        execute(acting, lambda *a, **kw: pytest.fail("Tamper ran an account"), saved=changed)
