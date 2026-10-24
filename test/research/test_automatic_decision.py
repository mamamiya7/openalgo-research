"""Decision policy qualification; controlled examples establish no market edge."""

from copy import deepcopy

import pytest
from test_automatic_design import BINS, OPPORTUNITY_DATES, balanced

from research.automatic_decision import conclude, descriptor, feedback
from research.automatic_design import assess
from research.automatic_design import descriptor as design_policy
from research.automatic_protocol import normalize_auto
from research.report_contract import fingerprint


def account(profit=5):
    report = balanced()
    report["summary"]["net_return_pct"] = profit
    return {
        "summary": report["summary"],
        "entry_dates": 30,
        "config_id": "chosen",
        "automatic_design": assess(
            report, BINS, design_policy(), opportunity_dates=OPPORTUNITY_DATES
        ),
        "portfolio_constraint_evidence": {"status": "supported"},
    }


def decision(**changes):
    inputs = {
        "policy": descriptor(),
        "selected": account(),
        "baseline": account(-1),
        "stress": account(3),
        "stress_baseline": account(-2),
        "neighbors": [account(4), account(4), account(4), account(4)],
        "prefixes": [],
        "seed_choices": [{"config_id": "chosen"}] * 3,
        "is_baseline": False,
    }
    inputs.update(changes)
    return conclude(**inputs)


def test_feasibility_is_named_and_fed_separately_from_profit():
    row, reference = account(), account(-1)
    assert feedback(row, reference, design_policy()) == {
        "support": 0,
        "sample_design": 0,
        "native_constraints": 0,
    }
    row["portfolio_constraint_evidence"]["status"] = "incomplete"
    assert feedback(row, reference, design_policy())["native_constraints"] == 1
    row["summary"]["net_return_pct"] = -1
    assert feedback(row, reference, design_policy())["support"] > 0


def test_design_receipts_cannot_be_forged_to_teach_the_optimizer():
    row = account()
    row["automatic_design"]["qualified"] = False
    with pytest.raises(ValueError, match="changed"):
        feedback(row, account(-1), design_policy())


def test_positive_historical_challenges_still_require_independent_evidence():
    result = decision()
    assert result["outcome"] == "insufficient_evidence"
    assert result["historical_candidate"] is True
    assert result["independently_validated"] is False
    assert result["seed_diagnostics"]["independent_confirmations"] == 0
    assert result["id"] == fingerprint({k: v for k, v in result.items() if k != "id"})


def test_lower_profit_cannot_meet_the_declared_net_improvement_goal():
    reference, selected = account(10), account(1)
    reference["summary"]["max_drawdown_pct"] = 20
    selected["summary"]["max_drawdown_pct"] = 0
    assert feedback(selected, reference, design_policy())["support"] > 0
    assert decision(selected=selected, baseline=reference)["outcome"] == "no_useful_improvement"


def test_near_cash_profit_that_flips_under_costs_is_rejected():
    result = decision(selected=account(0.0066), stress=account(-0.04))
    assert result["outcome"] == "no_useful_improvement"
    assert not result["historical_candidate"]
    assert result["checks"][0]["passed"] is False
    assert result["selected_config_id"] == "chosen"


@pytest.mark.parametrize("neighbors", [[], [account()], [account(-1)] * 4, [account(0.1)] * 4])
def test_insufficient_or_fragile_neighborhood_is_not_promoted(neighbors):
    assert decision(neighbors=neighbors)["outcome"] == "no_useful_improvement"


def test_exact_three_quarters_neighbor_pass_and_edge_are_required():
    assert decision(neighbors=[account(4)] * 3 + [account(-1)])["historical_candidate"]
    assert not decision(neighbors=[account(4)] * 2 + [account(-1)] * 2)["historical_candidate"]


@pytest.mark.parametrize("field", ["automatic_design", "portfolio_constraint_evidence"])
def test_missing_cost_challenge_checks_fail_closed(field):
    stress = account()
    stress.pop(field)
    assert not decision(stress=stress)["historical_candidate"]


def test_baseline_fallback_does_not_need_extra_cost_accounts():
    result = decision(is_baseline=True, stress=None, stress_baseline=None, neighbors=[])
    assert result["outcome"] == "no_useful_improvement"
    assert result["checks"] == []


def test_independent_verification_cannot_be_inferred_from_repeatability():
    assert (
        decision(independently_validated=True, validation_passed=False)["outcome"]
        == "no_useful_improvement"
    )
    assert (
        decision(independently_validated=True, validation_passed=True)["outcome"]
        == "supported_candidate"
    )


def test_missing_cost_pair_or_changed_frozen_policy_is_rejected():
    with pytest.raises(ValueError, match="cost challenge"):
        decision(stress=None)
    changed = deepcopy(descriptor())
    changed["minimum_neighbor_pass_fraction"] = 0.1
    with pytest.raises(ValueError, match="policy changed"):
        decision(policy=changed)


def test_policy_is_explicit_and_legacy_request_remains_unchanged():
    old = {
        "version": "automatic-hypotheses-v16",
        "history_policy": "available_history",
        "design_policy": "automatic_design_v1",
    }
    assert normalize_auto(old) == old
    new = {**old, "decision_policy": "automatic_decision_v1"}
    assert normalize_auto(new) == new
    for field in ("history_policy", "design_policy"):
        bad = dict(new)
        bad.pop(field)
        with pytest.raises(ValueError):
            normalize_auto(bad)
