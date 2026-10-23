"""Capital estimates stay distinct from verified, bounded native funding checks."""

from copy import deepcopy

import pytest
from test_automatic_capital_design import evidence

from research.capital_planning import account_receipt, descriptor, finish, plan, settings_for
from research.report_contract import fingerprint


def prepared(*, max_accounts=4):
    value = evidence()
    original = value["strategies"]
    original[0]["allocation_pct"] = 100
    original[0]["config"]["initial_capital"] = 100000
    selected = deepcopy(original)
    selected[0]["config"].update(stop_pct=5, cost_bps=0, slippage_bps=0)
    return plan(original, selected, 100000, max_accounts=max_accounts)


def report(
    value,
    *,
    quantity=30,
    requested=30,
    constraints=None,
    status="closed",
    reason="hold; close exit",
):
    return {
        "summary": {
            "initial_capital": value["candidate_capitals"][0],
            "closed_trades": int(status == "closed"),
        },
        "ledger": [
            {
                "quantity": quantity,
                "status": status,
                "reason": reason,
                "execution_details": {
                    "requested_quantity": requested,
                    "constraints": constraints or [],
                },
            }
        ],
    }


def test_conservative_seed_can_be_below_reference_and_is_frozen_before_results():
    value = prepared()
    assert value["seed_capital"] == 30000
    assert value["candidate_capitals"] == [30000, 38000, 45000, 60000]
    assert value["tickets"] == [{"strategy_id": "scan", "initial_ticket": 3000}]
    assert value["max_accounts"] == descriptor()["max_accounts"] == 4
    assert prepared() == value


def test_strict_plan_uses_only_three_reserved_accounts():
    value = prepared(max_accounts=3)
    assert value["candidate_capitals"] == prepared()["candidate_capitals"][:3]
    assert value["id"] != prepared()["id"]


def test_starting_rupee_ticket_is_fixed_and_all_selected_rules_and_risk_limits_survive():
    value = prepared()
    before = deepcopy(value)
    for capital in value["candidate_capitals"]:
        row = settings_for(capital, value["baseline"], value["selected"], original_capital=100000)[
            0
        ]
        assert capital * row["allocation_pct"] / 100 * row["config"][
            "order_size_pct"
        ] / 100 == pytest.approx(3000)
        assert row["config"]["stop_pct"] == 5
        assert row["config"]["initial_capital"] == capital
        assert row["execution_policy"] == value["selected"][0]["execution_policy"]
    assert value == before


def test_atr_seed_uses_full_ticket_risk_without_inventing_atr():
    value = prepared()
    value["selected"][0]["execution_policy"]["exit_mode"] = "atr"
    changed = plan(value["baseline"], value["selected"], 100000)
    assert changed["seed_capital"] == 600000


def test_zero_fee_stop_risk_can_dominate_gross_exposure_seed():
    value = prepared()
    value["selected"][0]["config"]["cost_bps"] = 0
    value["selected"][0]["config"]["stop_pct"] = 30
    changed = plan(value["baseline"], value["selected"], 100000)
    assert changed["seed_capital"] == 180000


def test_seed_respects_selected_trade_risk_target_and_native_missing_receipts_fail_closed():
    value = prepared()
    chosen = deepcopy(value["selected"])
    chosen[0]["execution_policy"].update(sizing="stop_risk", risk_budget_fraction=0.25)
    chosen[0]["config"]["stop_pct"] = 30
    changed = plan(value["baseline"], chosen, 100000)
    assert changed["seed_capital"] == 360000
    native = report(value)
    native["ledger"][0].pop("execution_details")
    receipt = account_receipt(native, value, value["candidate_capitals"][0])
    assert receipt["blockers"] == {"unavailable_execution_evidence": 1}
    assert receipt["verified_sufficient"] is False


def test_zero_native_fill_with_requested_shares_is_never_sufficient():
    value = prepared()
    native = report(value, quantity=0, requested=30)
    receipt = account_receipt(native, value, value["candidate_capitals"][0])
    assert receipt["blockers"] == {"other_execution_limit": 1}
    assert receipt["verified_sufficient"] is False


def test_verified_amount_is_tested_sufficiency_never_a_minimum():
    value = prepared()
    receipt = account_receipt(report(value), value, value["candidate_capitals"][0])
    result = finish(value, [receipt])
    assert result["status"] == "verified_sufficient"
    assert result["verified_capital"] == 30000
    assert result["estimated_capital"] == 30000
    assert result["original_capital"] == 100000
    assert result["reference_tickets"] == value["tickets"]
    assert result["tested_range"] == [30000, 30000]
    assert result["independently_validated"] is False
    assert "not minimum capital" in result["scope"]
    assert "not selected-setup replay" in result["policy"]["scope"]


@pytest.mark.parametrize(
    "constraint,category",
    [
        ("open_positions", "position_count"),
        ("daily_volume_participation", "liquidity"),
        ("shared_cash", "funding_or_percentage_limit"),
        ("symbol_exposure", "funding_or_percentage_limit"),
        ("total_stop_risk", "funding_or_percentage_limit"),
    ],
)
def test_clipping_prevents_sufficient_claim_and_classifies_binding_limit(constraint, category):
    value = prepared()
    receipt = account_receipt(report(value, quantity=15, constraints=[constraint]), value, 30000)
    assert receipt["blockers"] == {category: 1}
    assert receipt["clipped_requests"] == 1
    assert finish(value, [receipt])["status"] == "no_verified_amount"


@pytest.mark.parametrize("capital_index", range(4))
def test_cash_cannot_remove_position_count_cap_at_any_tested_amount(capital_index):
    value = prepared()
    native = report(
        value, quantity=0, requested=30, constraints=["open_positions"], status="skipped"
    )
    capital = value["candidate_capitals"][capital_index]
    native["summary"]["initial_capital"] = capital
    receipt = account_receipt(native, value, capital)
    assert receipt["blockers"] == {"position_count": 1}
    assert receipt["verified_sufficient"] is False


def test_missing_features_prevent_sufficiency_but_selected_filter_exclusions_do_not():
    value = prepared()
    native = report(value)
    native["ledger"].append(
        {"quantity": 0, "status": "excluded", "reason": "Joint entry condition not met: Trend"}
    )
    assert account_receipt(native, value, 30000)["verified_sufficient"]
    native["ledger"].append(
        {"quantity": 0, "status": "excluded", "reason": "allocation_unavailable: prior returns"}
    )
    assert account_receipt(native, value, 30000)["blockers"] == {"unavailable_evidence": 1}


def test_expensive_share_cannot_be_funded_by_raising_capital_at_fixed_ticket():
    value = prepared()
    native = report(
        value,
        quantity=0,
        requested=0,
        status="skipped",
        reason="Execution limits cannot admit one whole share: requested_cash_budget",
    )
    receipt = account_receipt(native, value, 30000)
    assert receipt["blockers"] == {"fixed_ticket_cannot_fund_share": 1}


def test_finish_requires_exact_prefix_and_stops_after_first_success():
    value = prepared()
    receipt = account_receipt(report(value), value, 30000)
    with pytest.raises(ValueError, match="sequence"):
        finish(value, [receipt, receipt])
    forged = deepcopy(receipt)
    forged["capital"] = 38000
    forged["id"] = fingerprint({key: item for key, item in forged.items() if key != "id"})
    with pytest.raises(ValueError, match="sequence"):
        finish(value, [forged])
    assert finish(value, [receipt]) == finish(value, [receipt])


def test_plan_and_receipt_tampering_fail_closed():
    value = prepared()
    receipt = account_receipt(report(value), value, 30000)
    damaged = deepcopy(value)
    damaged["candidate_capitals"].append(1000000)
    damaged["id"] = fingerprint({key: item for key, item in damaged.items() if key != "id"})
    with pytest.raises(ValueError, match="frozen capital plan"):
        finish(damaged, [])
    receipt["verified_sufficient"] = False
    with pytest.raises(ValueError, match="sequence"):
        finish(value, [receipt])


def test_real_native_planning_account_uses_smaller_capital_and_actual_ticket_quantities():
    from test_portfolio_wide_allocation import account

    from research.connectors.vectorbt_portfolio import evaluate

    _, attached, _, _ = account(mode="original")
    baseline = deepcopy(attached["strategies"])
    baseline[0]["config"].update(
        initial_capital=10000, order_size_pct=3, cost_bps=0, slippage_bps=0
    )
    selected = deepcopy(baseline)
    selected[0]["config"]["stop_pct"] = 5
    planned = plan(baseline, selected, 10000)
    capital = planned["candidate_capitals"][0]
    assert capital < 10000
    settings = settings_for(capital, baseline, selected, original_capital=10000)
    native = evaluate(settings, attached["snapshot"], capital)
    receipt = account_receipt(native, planned, capital)
    assert receipt["admitted_requests"] > 0
    assert receipt["verified_sufficient"]
    assert finish(planned, [receipt])["verified_capital"] == capital


def test_unrepresentable_capital_keeps_estimate_without_changing_tickets_or_running_accounts():
    value = prepared()
    baseline, selected = deepcopy(value["baseline"]), deepcopy(value["selected"])
    baseline[0]["config"]["order_size_pct"] = 0.01
    selected[0]["config"]["stop_pct"] = 30
    for row in (baseline[0], selected[0]):
        row["execution_policy"]["risk_limits"]["max_trade_risk_pct"] = 0.000001
    planned = plan(baseline, selected, 100000)
    assert planned["candidate_capitals"] == []
    assert planned["representation_limited"]
    assert planned["tickets"][0]["initial_ticket"] == 10
    result = finish(planned, [])
    assert result["estimated_capital"] > 100000
    assert result["status"] == "no_verified_amount"
    assert result["verified_capital"] is None
    assert result["tested_range"] is None
    assert result["representation_limited"]
    assert "no funding account was tested" in result["representation_reason"]


def test_zero_allocation_remains_inactive_without_distorting_seed_or_order_representation():
    value = prepared()
    baseline, selected = deepcopy(value["baseline"]), deepcopy(value["selected"])
    inactive = deepcopy(baseline[0])
    inactive.update(id="inactive", allocation_pct=0)
    inactive["config"]["order_size_pct"] = 100
    baseline.append(inactive)
    selected.append(deepcopy(inactive))
    planned = plan(baseline, selected, 100000)
    assert planned["seed_capital"] == 30000
    assert planned["candidate_capitals"] == value["candidate_capitals"]
    assert planned["tickets"][1] == {"strategy_id": "inactive", "initial_ticket": 0}
    rows = settings_for(30000, baseline, selected, original_capital=100000)
    assert rows[1]["allocation_pct"] == 0
    assert rows[1]["config"]["order_size_pct"] == 100
    native = report(planned)
    native["ledger"].append({"strategy_id": "inactive", "status": "skipped", "quantity": 0})
    assert account_receipt(native, planned, 30000)["verified_sufficient"]


def test_all_zero_allocations_are_not_a_fundable_portfolio():
    value = prepared()
    baseline, selected = deepcopy(value["baseline"]), deepcopy(value["selected"])
    baseline[0]["allocation_pct"] = selected[0]["allocation_pct"] = 0
    with pytest.raises(ValueError, match="positive initial ticket"):
        plan(baseline, selected, 100000)


def test_estimate_above_native_capital_ceiling_is_retained_without_running_invalid_account():
    value = prepared()
    baseline, selected = deepcopy(value["baseline"]), deepcopy(value["selected"])
    for row in (baseline[0], selected[0]):
        row["config"]["order_size_pct"] = 100
        row["execution_policy"]["risk_limits"].update(
            max_open_positions=2,
            max_symbol_exposure_pct=100,
            max_trade_risk_pct=100,
            max_total_stop_risk_pct=100,
        )
    planned = plan(baseline, selected, 1_000_000_000)
    assert planned["seed_capital"] == 2_000_000_000
    assert planned["candidate_capitals"] == []
    assert finish(planned, [])["representation_limited"]
    with pytest.raises(ValueError, match="native account range"):
        settings_for(2_000_000_000, baseline, selected, original_capital=1_000_000_000)


@pytest.mark.parametrize("field", ["cost_bps", "slippage_bps"])
@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), True, 10000])
def test_invalid_execution_costs_fail_before_funding_estimate(field, amount):
    value = prepared()
    selected = deepcopy(value["selected"])
    selected[0]["config"][field] = amount
    with pytest.raises(ValueError):
        plan(value["baseline"], selected, 100000)


@pytest.mark.parametrize("capital", [float("inf"), float("nan"), -1, 0, True])
def test_invalid_capital_is_rejected(capital):
    value = prepared()
    with pytest.raises(ValueError):
        plan(value["baseline"], value["selected"], capital)
