"""Automatic capital sizing separates reference amounts from fixed hard risk limits."""

from copy import deepcopy

import pytest

from research.execution_policy import normalize_policy
from research.execution_search import (
    ALLOCATION_AXIS,
    CAPITAL_SEARCH_VERSION,
    PRIORITY_AXIS,
    apply_choices,
    axes,
    descriptor,
    original_sizing,
    probes,
)
from research.report_contract import fingerprint


def evidence(*, design=True):
    policy = normalize_policy(
        {
            "version": "research-execution-aware-v5",
            "allocation_mode": "original",
            "capacity_mode": "prior20_broker_reported",
            "correlation_mode": "prior60_positive_080",
            "entry_priority": "source",
        }
    )
    auto = {"version": "automatic-hypotheses-v16", "history_policy": "available_history"}
    if design:
        auto["design_policy"] = "automatic_design_v1"
    return {
        "portfolio": {"automatic_research": auto, "risk_limits": deepcopy(policy["risk_limits"])},
        "strategies": [
            {
                "id": "scan",
                "allocation_pct": 80.0,
                "config": {"order_size_pct": 3.0, "target_pct": 40.0, "stop_pct": 30.0},
                "execution_policy": policy,
            }
        ],
    }


def choice(value, state):
    spec = descriptor(value)
    params = {
        "scan.execution_choice": "percent",
        "scan.portfolio_choice": state,
        PRIORITY_AXIS: "source",
        ALLOCATION_AXIS: "original",
    }
    return apply_choices(value["strategies"], params, spec)[0]


@pytest.mark.parametrize("state,fraction", [("allocation_75", 0.75), ("allocation_50", 0.5)])
def test_new_reduction_scales_cash_once_but_legacy_contract_is_unchanged(state, fraction):
    new = choice(evidence(), state)
    old = choice(evidence(design=False), state)
    assert new["allocation_pct"] == 80
    assert new["config"]["order_size_pct"] == 3 * fraction
    assert old["allocation_pct"] == 80 * fraction
    assert old["config"]["order_size_pct"] == 3 * fraction
    assert new["allocation_pct"] * new["config"]["order_size_pct"] == 240 * fraction
    assert old["allocation_pct"] * old["config"]["order_size_pct"] == 240 * fraction**2


@pytest.mark.parametrize("state,target", [("stop_risk", 0.5), ("stop_risk_50", 0.25)])
def test_risk_targets_can_use_original_allocation_and_all_hard_limits_are_frozen(state, target):
    value = evidence()
    before = deepcopy(value)
    candidate = choice(value, state)
    assert candidate["allocation_pct"] == 80
    assert candidate["config"]["order_size_pct"] == 100
    assert candidate["execution_policy"]["risk_budget_fraction"] == target
    assert candidate["execution_policy"]["risk_limits"] == value["portfolio"]["risk_limits"]
    assert candidate["execution_policy"]["capacity_mode"] == "prior20_broker_reported"
    assert candidate["execution_policy"]["correlation_mode"] == "prior60_positive_080"
    assert candidate["execution_policy"]["version"] == "research-execution-aware-v5"
    restored = original_sizing([candidate], descriptor(value))[0]
    assert restored == value["strategies"][0]
    assert value == before


def test_baseline_stays_original_and_program_keeps_same_dimensions_and_work():
    value = evidence()
    assert choice(value, "original") == value["strategies"][0]
    spec = descriptor(value)
    assert spec["version"] == CAPITAL_SEARCH_VERSION
    assert axes(spec) == axes(descriptor(evidence(design=False)))
    assert len(probes({}, spec)) == 10
    assert any(row["scan.portfolio_choice"] == "stop_risk_50" for row in probes({}, spec))


@pytest.mark.parametrize(
    "damage",
    ["target", "ceilings", "policy", "extra", "choice", "allocation", "priority", "metadata"],
)
def test_resealed_malformed_capital_descriptor_is_rejected(damage):
    spec = descriptor(evidence())
    if damage == "target":
        spec["stop_risk_target"] = "Unbounded"
    elif damage == "ceilings":
        spec["ceilings"] = "Ignore limits"
    elif damage == "policy":
        spec["design_policy"] = "automatic_design_v2"
    elif damage == "extra":
        spec["unknown"] = True
    elif damage == "choice":
        spec["strategies"][0]["portfolio_choices"][4]["risk_budget_fraction"] = 1
    elif damage == "allocation":
        spec["allocation_modes"].append("future")
    elif damage == "priority":
        spec["entry_priorities"].append("future")
    else:
        spec["sizing_control"] = "No matched baseline"
    spec["id"] = fingerprint({key: value for key, value in spec.items() if key != "id"})
    with pytest.raises(ValueError):
        axes(spec)


@pytest.mark.parametrize("field", ["history_policy", "design_policy"])
def test_capital_design_requires_explicit_available_history_opt_in(field):
    value = evidence()
    value["portfolio"]["automatic_research"].pop(field)
    assert descriptor(value)["version"] == "research-execution-search-v5"


def test_capital_metadata_cannot_be_relabelled_as_legacy():
    spec = descriptor(evidence(design=False))
    spec["design_policy"] = "automatic_design_v1"
    spec["id"] = fingerprint({key: value for key, value in spec.items() if key != "id"})
    with pytest.raises(ValueError, match="version"):
        axes(spec)


@pytest.mark.parametrize("state,target", [("stop_risk", 0.5), ("stop_risk_50", 0.25)])
def test_native_fills_can_exceed_reference_cash_without_exceeding_stop_risk(state, target):
    from test_portfolio_wide_allocation import account

    from research.connectors.vectorbt_portfolio import evaluate

    _, attached, _, _ = account(mode="original")
    value = evidence()
    row = attached["strategies"][0]
    row["id"] = "scan"
    row["config"].update(order_size_pct=3, stop_pct=5, cost_bps=0, slippage_bps=0)
    row["execution_policy"]["risk_limits"]["max_trade_risk_pct"] = 1
    policy = row["execution_policy"]
    value["strategies"] = [row]
    value["portfolio"]["risk_limits"] = deepcopy(policy["risk_limits"])
    selected = choice(value, state)
    report = evaluate([selected], attached["snapshot"], 10000)
    original = evaluate([row], attached["snapshot"], 10000)
    fill, reference = report["ledger"][0], original["ledger"][0]
    assert fill["quantity"] > reference["quantity"] > 0
    assert fill["execution_details"]["initial_stop_risk"] <= 10000 * 0.01 * target
    assert selected["execution_policy"]["risk_limits"] == policy["risk_limits"]


def test_saved_capital_study_restores_exact_declared_dimensions_without_new_accounts(monkeypatch):
    from research.study_analysis import reconstruct_study

    monkeypatch.setattr(
        "research.connectors.vectorbt_portfolio.evaluate",
        lambda *args, **kwargs: pytest.fail("Study reconstruction must not calculate accounts"),
    )
    spec = descriptor(evidence())
    experiment = {
        "experiment_version": "automatic-hypotheses-v16",
        "execution_research": spec,
        "search_space": {"axes": axes(spec)},
        "trials": [],
    }
    before = deepcopy(experiment)
    _, distributions = reconstruct_study(experiment)
    assert set(distributions) == set(axes(spec))
    assert experiment == before


@pytest.mark.parametrize("damage", ["missing_policy", "older_programme", "legacy_relabelled"])
def test_saved_capital_study_requires_exact_explicit_design_contract(damage):
    from research.study_analysis import reconstruct_study

    spec = descriptor(evidence())
    experiment = {
        "experiment_version": "automatic-hypotheses-v16",
        "execution_research": spec,
        "search_space": {"axes": axes(spec)},
        "trials": [],
    }
    if damage == "missing_policy":
        spec.pop("design_policy")
    elif damage == "older_programme":
        experiment["experiment_version"] = "automatic-hypotheses-v12"
    else:
        spec = descriptor(evidence(design=False))
        spec["design_policy"] = "automatic_design_v1"
        experiment["execution_research"] = spec
    spec["id"] = fingerprint({key: value for key, value in spec.items() if key != "id"})
    with pytest.raises(ValueError, match="execution"):
        reconstruct_study(experiment)
