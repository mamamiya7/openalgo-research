"""Capacity shortages are measured against native intent, not a cash ceiling."""

from copy import deepcopy
from math import floor

import pytest
from test_capital_planning import prepared
from test_selected_account_actions import native

from research.capital_planning import (
    INTENDED_VERSION,
    SELECTED_VERSION,
    selected_account_descriptor,
    selected_account_finish,
    selected_account_next,
    selected_account_plan,
    selected_account_receipt,
)


def programme(version=INTENDED_VERSION):
    selected = prepared()["selected"]
    selected[0]["execution_policy"].update(sizing="stop_risk", risk_budget_fraction=0.5)
    return selected_account_plan(selected, 100000, {"scan": [100]}, version=version)


def report(value, *, quantity=None, requested=100, tags=("risk_sizing_target",)):
    policy = value["selected"][0]["execution_policy"]
    target = policy["risk_limits"]["max_trade_risk_pct"] / 100 * 0.5
    intended = min(requested, floor(100000 * target / 50 + 1e-9))
    result = native(
        value,
        0,
        quantity=intended if quantity is None else quantity,
        requested=requested,
        tags=tags,
    )
    row = result["ledger"][0]
    row["entry_raw_price"] = 100.0
    row["execution_details"].update(
        sizing="stop_risk",
        opening_equity=100000.0,
        initial_risk_per_share=50.0,
        target_stop_risk_pct=target * 100,
    )
    return result


def test_fulfilled_native_risk_target_is_not_a_capital_shortage_and_stops_retesting():
    value = programme()
    current = selected_account_receipt(report(value), value, 0)
    assert current["counts"]["fulfilled_risk_targets"] == 1
    assert not current["clipped_requests"]
    assert current["verified_sufficient"]
    assert current["constraint_counts"] == {"risk_sizing_target": 1}
    assert selected_account_next(value, [current]) is None
    final = selected_account_finish(value, [current])
    assert final["version"] == final["policy"]["version"] == INTENDED_VERSION
    assert final["diagnosis"]["action"] == "funding_verified"
    assert final["performance_validated"] is False
    assert final["operating_point"]["remaining_exclusions"] == {}


def test_v3_saved_semantics_remain_cash_ceiling_based_and_v4_is_explicit():
    old, new = programme(SELECTED_VERSION), programme()
    before = deepcopy(old)
    current = selected_account_receipt(report(old), old, 0)
    assert current["clipped_requests"] == 1
    assert not current["verified_sufficient"]
    assert selected_account_descriptor()["version"] == SELECTED_VERSION
    assert old == before
    assert old["id"] != new["id"]
    with pytest.raises(ValueError, match="version"):
        programme("unknown")


def test_cash_ceiling_can_bind_before_risk_target_without_becoming_a_shortage():
    value = programme()
    current = selected_account_receipt(report(value, requested=1), value, 0)
    assert current["verified_sufficient"]
    assert not current["clipped_requests"]


def test_fill_above_frozen_risk_intent_is_rejected_and_mismatched_target_is_unavailable():
    value = programme()
    current = report(value)
    current["ledger"][0]["quantity"] += 1
    with pytest.raises(ValueError, match="sizing intent"):
        selected_account_receipt(current, value, 0)
    current = report(value)
    current["ledger"][0]["execution_details"]["target_stop_risk_pct"] *= 2
    receipt = selected_account_receipt(current, value, 0)
    assert receipt["counts"]["unavailable_sizing_intent"] == 1
    assert not receipt["verified_sufficient"]


@pytest.mark.parametrize(
    "tag,category",
    [
        ("shared_cash", "cash_shortfall"),
        ("symbol_exposure", "percentage_policy"),
        ("open_positions", "position_limit"),
        ("daily_volume_participation", "liquidity_limit"),
    ],
)
def test_real_shortage_below_risk_intent_keeps_hard_limits_and_cash_causes(tag, category):
    value = programme()
    current = selected_account_receipt(
        report(value, quantity=1, tags=[tag, "risk_sizing_target"]), value, 0
    )
    assert current["clipped_requests"] == current["counts"][category] == 1
    assert not current["verified_sufficient"]
    if category in ("cash_shortfall", "percentage_policy"):
        assert selected_account_next(value, [current]) == 2
    else:
        assert selected_account_next(value, [current]) is None


@pytest.mark.parametrize(
    "field", ["entry_raw_price", "opening_equity", "initial_risk_per_share", "target_stop_risk_pct"]
)
def test_missing_native_sizing_evidence_cannot_establish_sufficiency(field):
    value = programme()
    current = report(value)
    row = current["ledger"][0]
    (row if field == "entry_raw_price" else row["execution_details"]).pop(field)
    receipt = selected_account_receipt(current, value, 0)
    assert not receipt["verified_sufficient"]
    assert receipt["counts"]["unavailable_sizing_intent"] == 1


def test_zero_risk_target_without_exact_raw_price_is_unavailable_not_false_funding():
    value = programme()
    current = report(value, quantity=0)
    current["ledger"][0].pop("entry_raw_price")
    receipt = selected_account_receipt(current, value, 0)
    assert receipt["counts"]["unavailable_sizing_intent"] == 1
    assert not receipt["verified_sufficient"]
    cash_zero = selected_account_receipt(report(value, quantity=0, requested=0), value, 0)
    assert cash_zero["counts"]["whole_share_affordability"] == 1


def test_known_zero_risk_units_are_affordability_failure_not_a_fulfilled_target():
    value = programme()
    current = report(value, quantity=0)
    current["ledger"][0]["execution_details"]["initial_risk_per_share"] = 1000000.0
    receipt = selected_account_receipt(current, value, 0)
    assert receipt["counts"]["whole_share_affordability"] == 1
    assert not receipt["counts"].get("percentage_policy")
    assert not receipt["verified_sufficient"]
    # No larger capital was frozen for this price cohort; do not invent one.
    assert selected_account_next(value, [receipt]) is None


@pytest.mark.timeout(240)
@pytest.mark.parametrize("cost,slip,symbol_ceiling", [(0, 0, 100), (13, 17, 100), (0, 0, 1)])
def test_real_native_report_distinguishes_fulfilled_target_from_hard_cap(
    cost, slip, symbol_ceiling
):
    from test_vectorbt_portfolio import DAYS, candle, snapshot, strategy

    from research.connectors import vectorbt_portfolio as adapter
    from research.execution_policy import normalize_policy

    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"] = {day: candle(1000) for day in DAYS}
    original = strategy(
        "scan", 100, order_size_pct=100, target_pct=40, stop_pct=5, cost_bps=cost, slippage_bps=slip
    )
    original["execution_policy"] = normalize_policy(
        {
            "sizing": "stop_risk",
            "risk_budget_fraction": 0.5,
            "risk_limits": {"max_symbol_exposure_pct": symbol_ceiling},
        }
    )
    selected, *_ = adapter.validate([original], prices, 100000)
    value = selected_account_plan(selected, 100000, {"scan": [1000]}, version=INTENDED_VERSION)
    current = adapter.evaluate(selected, prices, 100000)
    receipt = selected_account_receipt(current, value, 0)
    assert (
        current["ledger"][0]["quantity"]
        < current["ledger"][0]["execution_details"]["requested_quantity"]
    )
    assert receipt["verified_sufficient"] is (symbol_ceiling == 100)
    if symbol_ceiling == 100:
        assert receipt["counts"]["fulfilled_risk_targets"] == 1
        assert selected_account_next(value, [receipt]) is None
    else:
        assert receipt["counts"]["percentage_policy"] == 1
        assert selected_account_next(value, [receipt]) == 2
