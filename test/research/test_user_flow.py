"""User authority constrains candidates without rewriting measured diagnostics."""

from copy import deepcopy

import pytest
from test_capital_atr_affordability import PRICE, RISK, receipt, row, selected

from research import capital_planning as capital
from research.user_flow import FIELDS, enforce, locked_axes, normalize, permitted


def flow(*allowed):
    return normalize(
        {
            "version": "research-user-flow-v1",
            "intent": "improve",
            "involvement": "guided",
            "adjustable": list(allowed),
        }
    )


def test_default_is_all_supported_fields_and_replay_has_no_adjustable_settings():
    assert normalize()["adjustable"] == list(FIELDS)
    assert (
        normalize({**normalize(), "intent": "replay", "involvement": "hands_on"})["adjustable"]
        == []
    )
    with pytest.raises(ValueError, match="exact settings"):
        normalize({**normalize(), "involvement": "hands_on"})


@pytest.mark.parametrize("allowed", [(), ("hold_sessions",), tuple(FIELDS)])
def test_hard_risk_costs_and_slippage_are_locked_independently_of_ai_permissions(allowed):
    baseline = selected()
    candidate = deepcopy(baseline)
    candidate[0]["execution_policy"]["risk_limits"]["max_trade_risk_pct"] = 99
    candidate[0]["config"].update(cost_bps=0, slippage_bps=90)
    evidence = {
        "strategies": deepcopy(candidate),
        "diagnostics": {"rejections": ["risk"], "passed": False},
    }
    before = deepcopy((candidate, baseline, evidence))
    fixed = enforce(candidate, baseline, flow(*allowed))
    assert (
        fixed[0]["execution_policy"]["risk_limits"]
        == baseline[0]["execution_policy"]["risk_limits"]
    )
    assert fixed[0]["config"]["cost_bps"] == baseline[0]["config"]["cost_bps"]
    assert fixed[0]["config"]["slippage_bps"] == baseline[0]["config"]["slippage_bps"]
    assert (candidate, baseline, evidence) == before
    assert not permitted(candidate, baseline, flow(*allowed))


def test_sizing_lock_preserves_stop_risk_fraction_as_well_as_visible_ticket_percentage():
    baseline = selected()
    candidate = deepcopy(baseline)
    candidate[0]["config"]["order_size_pct"] = 7
    candidate[0]["execution_policy"].update(sizing="allocation", risk_budget_fraction=0.01)
    fixed = enforce(candidate, baseline, flow("stop_pct", "target_pct"))[0]
    assert fixed["config"]["order_size_pct"] == baseline[0]["config"]["order_size_pct"]
    assert fixed["execution_policy"]["sizing"] == "stop_risk"
    assert fixed["execution_policy"]["risk_budget_fraction"] == 0.5
    assert (
        enforce(candidate, baseline, flow("order_size_pct"))[0]["execution_policy"]["sizing"]
        == "allocation"
    )


@pytest.mark.parametrize(
    "unlocked,locked_axis,locked_multiplier",
    [
        ("stop_pct", "target_pct", "atr_target_multiple"),
        ("target_pct", "stop_pct", "atr_stop_multiple"),
    ],
)
def test_shared_atr_exit_mode_cannot_bypass_other_exit_permission(
    unlocked, locked_axis, locked_multiplier
):
    baseline = selected()
    candidate = deepcopy(baseline)
    candidate[0]["execution_policy"].update(
        exit_mode="percent", atr_period=99, atr_stop_multiple=9, atr_target_multiple=12
    )
    candidate[0]["config"].update(stop_pct=1, target_pct=2)
    fixed = enforce(candidate, baseline, flow(unlocked))[0]
    assert fixed["execution_policy"]["exit_mode"] == "atr"
    assert fixed["execution_policy"]["atr_period"] == 14
    assert (
        fixed["execution_policy"][locked_multiplier]
        == baseline[0]["execution_policy"][locked_multiplier]
    )
    assert fixed["config"][locked_axis] == baseline[0]["config"][locked_axis]


def test_locked_holding_trailing_capital_and_allocation_restore_complete_semantics():
    baseline = selected()
    candidate = deepcopy(baseline)
    candidate[0]["config"].update(
        initial_capital=999999,
        hold_sessions=1,
        hold_minutes=5,
        trade_horizon="intraday",
        entry_time="10:00",
        exit_time="15:00",
        trailing_enabled=True,
        trailing_pct=0.1,
    )
    candidate[0]["allocation_pct"] = 1
    candidate[0]["execution_policy"]["allocation_mode"] = "prior60_cluster_inverse_vol"
    fixed = enforce(candidate, baseline, flow())[0]
    assert fixed["config"] == baseline[0]["config"]
    assert fixed["allocation_pct"] == baseline[0]["allocation_pct"]
    assert (
        fixed["execution_policy"]["allocation_mode"]
        == baseline[0]["execution_policy"]["allocation_mode"]
    )
    assert "hold_minutes" in locked_axes(baseline, flow())[baseline[0]["id"]]
    assert "hold_sessions" not in locked_axes(baseline, flow("hold_sessions"))[baseline[0]["id"]]


@pytest.mark.parametrize("locked", ["capital", "order_size_pct"])
def test_cause_routed_capital_recovery_cannot_schedule_a_forbidden_account(locked):
    settings = selected()
    source = {"strategies": settings, "ledger": [row(settings, risk=RISK)]}
    permissions = {"capital": locked != "capital", "order_size_pct": locked != "order_size_pct"}
    plan = capital.selected_account_plan(
        settings,
        100000,
        {"scan": [PRICE]},
        version=capital.ATR_VERSION,
        request_prices={capital.request_identity(source["ledger"][0]): PRICE},
        request_risks=capital.selected_account_risks(source, settings),
        permissions=permissions,
    )
    before = deepcopy(plan)
    first = receipt(plan, 0, quantity=1, tags=("shared_cash", "risk_sizing_target"))
    unrestricted = capital.selected_account_plan(
        settings,
        100000,
        {"scan": [PRICE]},
        version=capital.ATR_VERSION,
        request_prices={capital.request_identity(source["ledger"][0]): PRICE},
        request_risks=capital.selected_account_risks(source, settings),
    )
    unrestricted_next = capital.selected_account_next(
        unrestricted,
        [receipt(unrestricted, 0, quantity=1, tags=("shared_cash", "risk_sizing_target"))],
    )
    assert unrestricted_next is not None
    assert unrestricted["options"][unrestricted_next]["capital"] != 100000
    assert unrestricted["options"][unrestricted_next]["size_multiplier"] != 1
    following = capital.selected_account_next(plan, [first])
    if following is not None:
        option = plan["options"][following]
        assert permissions["capital"] or option["capital"] == plan["original_capital"]
        assert permissions["order_size_pct"] or option["size_multiplier"] == 1
    assert plan == before
    assert first["performance_validated"] is False


def test_locked_cohort_cannot_be_reordered_or_replaced():
    baseline = selected()
    second = deepcopy(baseline[0])
    second["id"] = "other"
    baseline.append(second)
    with pytest.raises(ValueError, match="cohort"):
        enforce(list(reversed(baseline)), baseline, normalize())
