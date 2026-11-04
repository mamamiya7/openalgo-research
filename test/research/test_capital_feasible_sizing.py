"""New supervisor sizing fits frozen capacities; old capital contracts stay fixed."""

from copy import deepcopy

import pytest
from test_capital_planning import prepared

from research import capital_planning as capital
from research.connectors import vectorbt_portfolio as adapter
from research.execution_policy import normalize_policy


def build(
    selected=None,
    *,
    price=1000,
    max_accounts=4,
    request_prices=None,
    version=capital.FEASIBLE_VERSION,
):
    selected = prepared()["selected"] if selected is None else selected
    return capital.selected_account_plan(
        selected,
        100000,
        {row["id"]: [price] for row in selected},
        max_accounts=max_accounts,
        version=version,
        request_prices=request_prices,
    )


def native_case(*, signals=None, sizing="allocation"):
    from test_vectorbt_portfolio import DAYS, candle, snapshot, strategy

    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"] = {day: candle(1000) for day in DAYS}
    row = strategy(
        "scan",
        100,
        signals=signals,
        order_size_pct=10,
        stop_pct=2,
        target_pct=4,
        cost_bps=10,
    )
    row["execution_policy"] = normalize_policy(
        {
            "sizing": sizing,
            **({"risk_budget_fraction": 1} if sizing == "stop_risk" else {}),
            "risk_limits": {"max_symbol_exposure_pct": 1},
        }
    )
    selected, *_ = adapter.validate([row], prices, 100000)
    return selected, prices


def test_feasible_sizes_derive_from_exact_limits_and_buffer_instead_of_fixed_quarters():
    selected, _ = native_case()
    before = deepcopy(selected)
    value = build(selected)
    factors = [o["size_multiplier"] for o in value["options"]]
    assert factors[:2] == [1, 1]
    assert 0 < factors[3] < factors[2] < 0.1
    assert len(value["options"]) == value["max_accounts"] == 4
    for index in (2, 3):
        settings = capital.selected_account_settings(value, index)
        assert settings[0]["config"]["order_size_pct"] < 1
        assert settings[0]["execution_policy"] == selected[0]["execution_policy"]
        assert settings[0]["allocation_pct"] == selected[0]["allocation_pct"]
    assert selected == before
    assert value == build(selected)
    assert build(selected, max_accounts=3)["options"] == value["options"][:3]


def test_v5_frozen_plan_and_descriptor_remain_exactly_legacy():
    old = capital.selected_account_plan(
        prepared()["selected"], 100000, {"scan": [10000]}, version=capital.CLOSED_LOOP_VERSION
    )
    assert old["option_estimates"] == [100000, 337000, 674000, 1347000]
    assert [o["size_multiplier"] for o in old["options"]] == [1, 1, 0.5, 0.25]
    descriptor = capital.selected_account_descriptor(capital.CLOSED_LOOP_VERSION)
    assert descriptor["size_multipliers"] == [1, 0.5, 0.25]
    assert "feasible_sizing" not in descriptor
    assert capital.selected_account_settings(old, 3)[0]["config"]["order_size_pct"] == 0.75


def test_native_minimum_representation_is_not_rounded_up_above_hard_cap():
    selected, _ = native_case()
    selected[0]["config"]["order_size_pct"] = 0.01
    selected[0]["execution_policy"]["risk_limits"]["max_symbol_exposure_pct"] = 0.001
    value = build(selected)
    assert len(value["options"]) == 2
    assert value["options"][0]["size_multiplier"] == value["options"][1]["size_multiplier"] == 1
    assert value["risk_limits"] == selected[0]["execution_policy"]["risk_limits"]


def test_zero_allocation_does_not_create_a_below_minimum_ticket_or_block_active_sizing():
    selected, _ = native_case()
    inactive = deepcopy(selected[0])
    inactive.update(id="inactive", name="Inactive", allocation_pct=0)
    inactive["config"]["order_size_pct"] = 0.01
    selected.append(inactive)
    value = build(selected)
    settings = capital.selected_account_settings(value, 2)
    assert settings[0]["config"]["order_size_pct"] < 1
    assert settings[1]["config"]["order_size_pct"] == 0.01
    assert settings[1]["allocation_pct"] == 0
    assert settings[1]["execution_policy"] == inactive["execution_policy"]


@pytest.mark.timeout(240)
@pytest.mark.parametrize("sizing", ["allocation", "stop_risk"])
def test_small_native_clipping_case_chooses_capital_and_size_that_fit_unchanged_limits(sizing):
    selected, prices = native_case(sizing=sizing)
    baseline = adapter.evaluate(selected, prices, 100000)
    value = build(
        selected,
        request_prices={capital.request_identity(row): 1000 for row in baseline["ledger"]},
    )
    first = capital.selected_account_receipt(baseline, value, 0)
    assert first["counts"].get("percentage_policy", 0) > 0
    assert capital.selected_account_next(value, [first]) == 2
    chosen = capital.selected_account_settings(value, 2)
    result = adapter.evaluate(chosen, prices, value["options"][2]["capital"])
    second = capital.selected_account_receipt(result, value, 2)
    assert second["verified_sufficient"]
    final = capital.selected_account_finish(value, [first, second])
    assert final["operating_point"]["option_index"] == 2
    assert final["verified_capital"] == result["summary"]["initial_capital"]
    assert final["recommended_settings"] == chosen
    assert chosen[0]["config"]["initial_capital"] > 100000
    assert chosen[0]["config"]["order_size_pct"] < 1
    assert (
        chosen[0]["execution_policy"]["risk_limits"]
        == selected[0]["execution_policy"]["risk_limits"]
    )
    if sizing == "stop_risk":
        assert chosen[0]["execution_policy"]["risk_budget_fraction"] < 1
    assert not final["performance_validated"]


@pytest.mark.timeout(240)
def test_native_repeated_same_symbol_requests_use_concurrent_reserve_in_existing_four_accounts():
    from test_vectorbt_portfolio import signal

    selected, prices = native_case(signals=[signal(row=2), signal(row=3)])
    value = build(selected)
    receipts = []
    while (index := capital.selected_account_next(value, receipts)) is not None:
        settings = capital.selected_account_settings(value, index)
        report = adapter.evaluate(settings, prices, value["options"][index]["capital"])
        receipts.append(capital.selected_account_receipt(report, value, index))
    assert [r["option_index"] for r in receipts] == [0, 2, 3]
    assert not receipts[1]["verified_sufficient"]
    assert receipts[-1]["verified_sufficient"]
    assert receipts[-1]["counts"]["admitted_requests"] == 2
    final = capital.selected_account_finish(value, receipts)
    assert final["verified_capital"] == value["options"][3]["capital"]
    assert (
        final["operating_point"]["settings"][0]["execution_policy"]["risk_limits"]
        == value["risk_limits"]
    )
    assert final["attempts"] == 3 <= value["max_accounts"]
    assert not final["performance_validated"]


def test_saved_b_fixed_position_projection_preserves_all57_exclusions_and_old_v6_semantics():
    """Observed saved-B projection: 20 admitted, 57 zero fills at 10/10 positions.

    The private checkpoint was separately financially audited without any new
    accounts. This unit regression freezes its causal overlap, not a new native
    profitability or performance-validation claim.
    """
    from test_selected_account_actions import native

    selected, _ = native_case()
    old = build(selected, price=100)
    new = build(selected, price=100, version=capital.CAUSAL_VERSION)
    assert old["options"] == new["options"]
    assert new["options"][3]["capital"] == 103000
    blocked = {
        "strategy_id": "scan",
        "symbol": "ABC",
        "signal_date": "2026-01-15",
        "source_row": 12,
        "quantity": 0,
        "status": "skipped",
        "reason": "Execution limits cannot admit one whole share: symbol_exposure, open_positions",
        "execution_details": {
            "requested_quantity": 1,
            "open_positions_before": 10,
            "opening_equity": 102998.99999999994,
            "constraints": ["symbol_exposure", "open_positions"],
        },
    }
    extra = [{**deepcopy(blocked), "source_row": i + 12} for i in range(57)]
    extra.extend(
        {
            "strategy_id": "scan",
            "symbol": "ABC",
            "signal_date": "2026-01-01",
            "source_row": i + 100,
            "quantity": 1,
            "status": "closed",
            "reason": "close",
            "execution_details": {"requested_quantity": 1, "constraints": []},
        }
        for i in range(19)
    )
    report = native(new, 3, quantity=1, requested=1, extra=extra)
    before = capital.selected_account_receipt(report, old, 3)
    after = capital.selected_account_receipt(report, new, 3)
    assert before["counts"]["percentage_policy"] == 57
    assert after["counts"].get("percentage_policy", 0) == 0
    assert after["counts"]["position_limit"] == after["counts"]["constrained_requests"] == 57
    assert after["counts"]["eligible_requests"] == 77
    assert after["counts"]["admitted_requests"] == 20
    assert (
        after["constraint_counts"]
        == before["constraint_counts"]
        == {
            "symbol_exposure": 57,
            "open_positions": 57,
        }
    )
    assert not before["verified_sufficient"] and not after["verified_sufficient"]
    assert after["performance_validated"] is False
    assert "constraint_diagnosis" not in capital.selected_account_descriptor(
        capital.FEASIBLE_VERSION
    )
    assert "constraint_diagnosis" in capital.selected_account_descriptor(capital.CAUSAL_VERSION)


@pytest.mark.parametrize("positions", [None, 9, 10.5, True, "10"])
def test_unproved_position_saturation_does_not_hide_simultaneous_funding_or_percentage_causes(
    positions,
):
    from test_selected_account_actions import native

    value = build(version=capital.CAUSAL_VERSION)
    report = native(
        value, 0, quantity=0, requested=1, tags=["open_positions", "symbol_exposure", "shared_cash"]
    )
    report["ledger"][0]["execution_details"]["open_positions_before"] = positions
    result = capital.selected_account_receipt(report, value, 0)
    assert result["counts"]["percentage_policy"] == result["counts"]["cash_shortfall"] == 1
    assert result["counts"]["position_limit"] == 1
    assert not result["verified_sufficient"]


def test_nonfinite_position_evidence_cannot_become_a_valid_saved_receipt():
    from test_selected_account_actions import native

    value = build(version=capital.CAUSAL_VERSION)
    report = native(value, 0, quantity=0, requested=1, tags=["open_positions", "symbol_exposure"])
    report["ledger"][0]["execution_details"]["open_positions_before"] = float("nan")
    with pytest.raises(ValueError, match="JSON compliant"):
        capital.selected_account_receipt(report, value, 0)


def test_mixed_native_causes_on_different_requests_remain_independently_actionable():
    from test_selected_account_actions import native

    value = build(version=capital.CAUSAL_VERSION)
    report = native(
        value, 0, quantity=0, requested=1, tags=["open_positions", "symbol_exposure", "shared_cash"]
    )
    report["ledger"][0]["execution_details"]["open_positions_before"] = 10
    second = deepcopy(report["ledger"][0])
    second["source_row"] = 2
    second["execution_details"].update(open_positions_before=0, constraints=["shared_cash"])
    report["ledger"].append(second)
    result = capital.selected_account_receipt(report, value, 0)
    assert result["counts"]["cash_shortfall"] == 1
    assert result["counts"].get("percentage_policy", 0) == 0
    assert result["counts"]["position_limit"] == 1
    assert result["counts"]["constrained_requests"] == 2
    assert result["constraint_counts"] == {
        "open_positions": 1,
        "symbol_exposure": 1,
        "shared_cash": 2,
    }
    assert capital.selected_account_next(value, [result]) == 2
    assert not result["verified_sufficient"]
