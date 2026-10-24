"""Real seeded decision orchestration over explicitly controlled account economics.

These sparse native-shaped fixtures establish no trading edge or native fill
correctness. They exercise the real bounded sampler, decisions and exact resume.
"""

# ruff: noqa: F811 -- shared isolated research fixtures

from copy import deepcopy

import optuna
import pytest
from test_automatic_design_runtime import (
    NativeShapeAccountEngine,
    designed,  # noqa: F401
)
from test_automatic_walk_forward import execute
from test_available_history_runtime import (
    available,  # noqa: F401
    install_controlled_gate_and_account_audit,
)

from research.automatic_decision import descriptor, preflight
from research.automatic_protocol import compile_recipe
from research.capital_planning import DECISION_VERSION
from research.execution_features import prepare as prepare_atr
from research.portfolio import execution_versions, normalize
from research.portfolio_execution_features import prepare as prepare_portfolio
from research.report_contract import fingerprint

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(scope="module")
def deciding(designed):
    value = deepcopy(designed)
    value["portfolio"]["automatic_research"]["decision_policy"] = "automatic_decision_v1"
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["versions"] = execution_versions(value["portfolio"])
    for name in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        value.pop(name, None)
    context = value["automatic_stock_market_context"]
    value = prepare_portfolio(prepare_atr(value, context), context)
    value["automatic_recipe"] = compile_recipe(value)
    return value


@pytest.fixture(autouse=True)
def controlled_gate_and_account_audit(monkeypatch):
    install_controlled_gate_and_account_audit(monkeypatch)


@pytest.fixture(scope="module")
def completed_decision(request, deciding):
    patch = pytest.MonkeyPatch()
    install_controlled_gate_and_account_audit(patch)
    request.addfinalizer(patch.undo)
    constraints = []
    original = optuna.trial.Trial.set_constraint

    def tracked(trial, name, value):
        constraints.append((trial.number, name, value))
        return original(trial, name, value)

    patch.setattr(optuna.trial.Trial, "set_constraint", tracked)
    engine = NativeShapeAccountEngine(deciding, lambda *args: None)
    events = []
    result, state = execute(deciding, engine, observe=events.append)
    return result, state, engine.calls, events, constraints


def test_pre_outcome_intent_and_original_full_search_budget_are_recorded(deciding):
    recipe = deciding["automatic_recipe"]
    assert recipe["automatic_decision"] == descriptor()
    assert recipe["capital_planning"]["version"] == DECISION_VERSION
    assert recipe["budget"]["proposals"] == 408
    assert recipe["budget"]["max_simulations"] == 512
    assert recipe["decision_preflight"] == preflight(deciding, recipe)
    assert recipe["decision_preflight"]["stage"] == "pre_outcome"
    assert recipe["decision_preflight"]["independent_later_period_available"] is False
    assert recipe["decision_preflight"]["signal_count"] == len(deciding["signals"])


def test_complete_sparse_programme_automatically_retains_baseline_and_explains_stop(
    deciding, completed_decision
):
    result, state, calls, events, _ = completed_decision
    findings = result["automatic_research"]
    decision = findings["decision"]
    assert findings["counts"]["proposals"] == 408
    assert all(len(fit["trials"]) == 68 for fit in state["fits"].values())
    assert [event["number"] for event in events if event["kind"] == "proposal_started"] == list(
        range(408)
    )
    assert len(calls) == state["simulations"] <= state["attempts"] <= 512
    assert state["selection"]["is_baseline"] and findings["selected_is_baseline"]
    assert all(
        not candidate["automatic_design"]["qualified"]
        for fit in state["fits"].values()
        for candidate in fit["candidates"].values()
    )
    assert decision["outcome"] == "no_useful_improvement"
    assert decision["historical_candidate"] is False
    assert decision["independently_validated"] is False
    assert any("retain original settings" in reason.lower() for reason in decision["reasons"])
    assert decision["selected_config_id"] == state["selection"]["config_id"]
    assert decision["challenge_plan"]["selection_changed"] is False
    assert decision["checks"] == []
    assert findings["decision_preflight"] == deciding["automatic_recipe"]["decision_preflight"]
    assert result["experiment"]["next_action"] == decision["next_action"]
    assert decision["capital_diagnosis"] == findings["capital_planning"]["diagnosis"]
    assert decision["id"] == fingerprint(
        {key: value for key, value in decision.items() if key != "id"}
    )


def test_every_real_proposal_sets_raw_named_feasibility_constraints(completed_decision):
    _, state, _, _, constraints = completed_decision
    assert len(constraints) == 408 * 3
    assert {name for _, name, _ in constraints} == {
        "support",
        "sample_design",
        "native_constraints",
    }
    assert all(type(value) is float and value >= 0 for _, _, value in constraints)
    assert all(value > 0 for _, name, value in constraints if name == "sample_design")
    assert all(value == 0 for _, name, value in constraints if name == "native_constraints")
    assert sum(len(fit["trials"]) for fit in state["fits"].values()) == 408


def test_completed_decision_resume_runs_zero_new_accounts(deciding, completed_decision):
    expected, complete, _, _, _ = completed_decision
    result, state = execute(
        deciding,
        lambda *args, **kwargs: pytest.fail("Completed decision replay ran another account"),
        saved=complete,
    )
    assert result == expected
    assert state == complete


@pytest.mark.parametrize("field", ["objective", "max_additional_accounts", "goal"])
def test_changed_recorded_decision_policy_is_rejected_before_accounting(deciding, field):
    value = deepcopy(deciding)
    value["automatic_recipe"]["automatic_decision"][field] = "changed"
    with pytest.raises(ValueError, match="recipe"):
        execute(
            value, lambda *args, **kwargs: pytest.fail("Changed policy reached account execution")
        )
