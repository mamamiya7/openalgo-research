"""A failing historical chronology still completes the full decision programme.

Controlled sparse account economics exercise orchestration and exact recovery;
they do not establish financial edge or qualify the native execution engine.
"""

from copy import deepcopy

import pytest
from test_automatic_design_runtime import NativeShapeAccountEngine
from test_automatic_walk_forward import execute
from test_available_history_runtime import install_controlled_gate_and_account_audit
from test_portfolio_allocation_recipe import prepared

from research.automatic_protocol import compile_recipe
from research.connectors.optuna_portfolio import _fingerprint
from research.execution_features import prepare as prepare_atr
from research.portfolio import execution_versions, normalize
from research.portfolio_execution_features import prepare as prepare_portfolio
from research.report_contract import fingerprint

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(scope="module")
def chronological():
    value = prepared()
    value["portfolio"]["automatic_research"].update(
        history_policy="available_history",
        design_policy="automatic_design_v1",
        decision_policy="automatic_decision_v1",
    )
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["versions"] = execution_versions(value["portfolio"])
    for key in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        value.pop(key, None)
    context = value["automatic_stock_market_context"]
    value = prepare_portfolio(prepare_atr(value, context), context)
    value["automatic_recipe"] = compile_recipe(value)
    assert value["automatic_recipe"]["validation_mode"] == "historical_only"
    assert len(value["automatic_recipe"]["walk_forward"]["folds"]) == 3
    return value


@pytest.fixture(autouse=True)
def controlled(monkeypatch):
    install_controlled_gate_and_account_audit(monkeypatch)


@pytest.fixture(scope="module")
def completed_chronological(request, chronological):
    patch = pytest.MonkeyPatch()
    install_controlled_gate_and_account_audit(patch)
    request.addfinalizer(patch.undo)
    engine = NativeShapeAccountEngine(chronological, lambda *args: None)
    events = []
    result, state = execute(chronological, engine, observe=events.append)
    return result, state, engine.calls, events


def test_failing_chronology_completes_all_six_fits_before_concluding(
    chronological, completed_chronological
):
    result, state, calls, events = completed_chronological
    findings = result["automatic_research"]
    assert findings["counts"]["proposals"] == 408
    assert set(state["fits"]) == {"fit1", "fit2", "fit3", "search", "search_seed1", "search_seed2"}
    assert all(len(fit["trials"]) == 68 for fit in state["fits"].values())
    assert [event["number"] for event in events if event["kind"] == "proposal_started"] == list(
        range(408)
    )
    assert len(calls) == state["simulations"] <= state["attempts"] <= 512
    assert state["selection"]["is_baseline"]
    assert findings["status"] == "historical"
    assert not findings["historical_checks_passed"]
    assert len(findings["walk_forward"]["folds"]) == 3
    assert all(not fold["eligible"] for fold in findings["walk_forward"]["folds"])
    assert findings["decision"]["outcome"] == "no_useful_improvement"
    assert findings["decision"]["seed_diagnostics"]["tested"] == 3
    assert findings["decision"]["independently_validated"] is False
    assert findings["decision"]["selected_config_id"] == state["selection"]["config_id"]
    assert findings["decision_preflight"] == chronological["automatic_recipe"]["decision_preflight"]
    selected_report = result["experiment"]["selected_reports"][findings["selected_config_id"]]
    assert selected_report["automatic_research"]["decision"] == findings["decision"]


def test_completed_failing_chronological_decision_reopens_without_more_accounts(
    chronological, completed_chronological
):
    expected, completed, _, _ = completed_chronological
    actual, state = execute(
        chronological,
        lambda *args, **kwargs: pytest.fail("Completed chronological recovery ran another account"),
        saved=completed,
    )
    assert actual == expected
    assert state == completed


def test_resealed_completed_decision_tampering_is_rejected_without_more_accounts(
    chronological, completed_chronological
):
    _, completed, _, _ = completed_chronological
    changed = deepcopy(completed)
    changed["decision"]["outcome"] = "supported_candidate"
    changed["decision"]["id"] = fingerprint(
        {key: value for key, value in changed["decision"].items() if key != "id"}
    )
    changed["checkpoint_id"] = _fingerprint(
        {key: value for key, value in changed.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="changed|differ"):
        execute(
            chronological,
            lambda *args, **kwargs: pytest.fail("Tampered completed decision ran another account"),
            saved=changed,
        )
