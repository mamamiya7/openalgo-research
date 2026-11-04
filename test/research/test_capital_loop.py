"""Small capital-loop cases: frozen per-size floors and one coherent choice."""

from copy import deepcopy
from math import ceil

import pytest
from test_capital_planning import prepared
from test_selected_account_actions import native

from research import capital_planning as capital
from research.report_contract import fingerprint


def plan(*, selected=None, price=10000, request_prices=None, max_accounts=4):
    return capital.selected_account_plan(
        prepared()["selected"] if selected is None else selected,
        100000,
        {"scan": [price]},
        max_accounts=max_accounts,
        version=capital.CLOSED_LOOP_VERSION,
        request_prices=request_prices,
    )


def receipt(value, index, **kwargs):
    return capital.selected_account_receipt(native(value, index, **kwargs), value, index)


def test_each_actual_size_has_its_own_buffered_capital_floor_without_quarter_overfunding():
    value = plan()
    assert value["option_estimates"] == [100000, 337000, 674000, 1347000]
    assert [row["capital"] for row in value["options"]] == value["option_estimates"]
    old = capital.selected_account_plan(
        prepared()["selected"], 100000, {"scan": [10000]}, version=capital.INTENDED_VERSION
    )
    assert old["options"][1]["capital"] == 1347000
    assert "option_estimates" not in old and "request_prices" not in old


def test_per_option_floor_uses_actual_risk_fraction_costs_and_unchanged_hard_limits():
    selected = prepared()["selected"]
    selected[0]["config"].update(order_size_pct=100, cost_bps=13, slippage_bps=17)
    selected[0]["execution_policy"].update(sizing="stop_risk", risk_budget_fraction=0.5)
    value = plan(selected=selected)
    cash = 10000 * 1.0017 * 1.0013
    loss = cash - 10000 * 1.0017 * 0.95 * 0.9983 * 0.9987
    target = selected[0]["execution_policy"]["risk_limits"]["max_trade_risk_pct"] / 100 * 0.5
    for index, multiplier in enumerate((1, 0.5, 0.25), 1):
        expected = max(
            100000, ceil((loss / (target * multiplier) + cash - 10000) / 0.99 / 1000) * 1000
        )
        assert value["option_estimates"][index] == expected
        settings = capital.selected_account_settings(value, index)[0]
        assert (
            settings["execution_policy"]["risk_limits"]
            == selected[0]["execution_policy"]["risk_limits"]
        )


def test_rank_uses_unique_requests_not_overlapping_tags_or_profit_and_all_fields_agree():
    value = plan(max_accounts=3)
    first = receipt(value, 0, quantity=5, tags=["shared_cash", "symbol_exposure"], profit=-900)
    second = receipt(value, 2, quantity=5, tags=["shared_cash"], profit=999999)
    result = capital.selected_account_finish(value, [first, second])
    assert first["counts"]["constrained_requests"] == second["counts"]["constrained_requests"] == 1
    assert result["operating_point"]["option_index"] == 0
    assert result["feasible_capital"] == first["capital"]
    assert result["estimated_capital"] == first["capital"]
    assert result["diagnosis"]["categories"] == first["counts"]
    assert result["diagnosis"]["constraint_counts"] == first["constraint_counts"]
    assert result["recommended_capital"] is None and not result["performance_validated"]


def test_verified_receipt_outranks_incomplete_evidence_and_recommendation_is_same_choice():
    value = plan(max_accounts=3)
    first_native = native(value, 0, quantity=5, tags=["shared_cash"])
    extra = deepcopy(first_native["ledger"][0])
    extra["source_row"] = 2
    extra["execution_details"].pop("requested_quantity")
    first_native["ledger"].append(extra)
    first = capital.selected_account_receipt(first_native, value, 0)
    second_native = native(value, 2)
    extra = deepcopy(second_native["ledger"][0])
    extra["source_row"] = 2
    second_native["ledger"].append(extra)
    second = capital.selected_account_receipt(second_native, value, 2)
    result = capital.selected_account_finish(value, [first, second])
    assert result["operating_point"]["option_index"] == 2
    assert (
        result["recommended_capital"]
        == result["verified_capital"]
        == result["feasible_capital"]
        == second["capital"]
    )
    assert result["recommended_settings"] == result["operating_point"]["settings"]
    assert result["diagnosis"]["categories"] == second["counts"]
    assert result["diagnosis"]["action"] == "funding_verified"


@pytest.mark.parametrize(
    "observations", [{"bad": 100}, {"a" * 64: float("nan")}, {"a" * 64: 0}, {"a" * 64: True}, []]
)
def test_request_price_map_requires_exact_bounded_identities_and_positive_prices(observations):
    with pytest.raises(ValueError):
        plan(request_prices=observations)


def test_price_map_is_detached_bounded_identity_bound_and_rebuilt_on_restore(monkeypatch):
    observations = {"a" * 64: 100}
    value = plan(request_prices=observations)
    observations["a" * 64] = 999999
    assert value["request_prices"] == {"a" * 64: 100.0}
    assert plan(request_prices={"a" * 64: 100}) == value
    assert plan(request_prices={"b" * 64: 100})["id"] != value["id"]
    broken = deepcopy(value)
    broken["request_prices"]["a" * 64] = 200
    with pytest.raises(ValueError, match="changed"):
        capital.selected_account_settings(broken, 0)
    monkeypatch.setattr(capital, "MAX_REQUEST_PRICES", 1)
    with pytest.raises(ValueError, match="bounds"):
        plan(request_prices={"a" * 64: 100, "b" * 64: 100})


def test_gap_counter_counts_one_request_even_when_multiple_fields_are_missing():
    selected = prepared()["selected"]
    selected[0]["execution_policy"].update(sizing="stop_risk", risk_budget_fraction=0.5)
    value = plan(selected=selected)
    current = native(value, 0)
    current["ledger"][0]["execution_details"].pop("requested_quantity")
    result = capital.selected_account_receipt(current, value, 0)
    assert result["counts"]["evidence_gap_requests"] == 1
    assert not result["verified_sufficient"]


@pytest.mark.timeout(240)
def test_small_native_zero_risk_share_case_uses_exact_frozen_open_and_retests_sufficient_capital():
    from test_vectorbt_portfolio import DAYS, candle, snapshot, strategy

    from research.connectors import vectorbt_portfolio as adapter
    from research.execution_policy import normalize_policy

    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"] = {day: candle(10000) for day in DAYS}
    original = strategy("scan", 100, order_size_pct=100, target_pct=40, stop_pct=5)
    original["execution_policy"] = normalize_policy(
        {"sizing": "stop_risk", "risk_budget_fraction": 0.001}
    )
    selected, *_ = adapter.validate([original], prices, 100000)
    initial = adapter.evaluate(selected, prices, 100000)
    row = initial["ledger"][0]
    assert not row["quantity"] and row.get("entry_raw_price") is None
    observations = {capital.request_identity(row): 10000}
    value = plan(selected=selected, request_prices=observations)
    first = capital.selected_account_receipt(initial, value, 0)
    assert first["counts"]["whole_share_affordability"] == 1
    assert first["counts"]["evidence_gap_requests"] == 0
    assert capital.selected_account_next(value, [first]) == 1
    settings = capital.selected_account_settings(value, 1)
    corrected = adapter.evaluate(settings, prices, value["options"][1]["capital"])
    second = capital.selected_account_receipt(corrected, value, 1)
    result = capital.selected_account_finish(value, [first, second])
    assert result["diagnosis"]["action"] == "funding_verified"
    assert result["operating_point"]["option_index"] == 1
    assert not result["performance_validated"]
    missing = plan(selected=selected)
    unknown = capital.selected_account_receipt(initial, missing, 0)
    assert unknown["counts"]["evidence_gap_requests"] == 1
    assert not unknown["verified_sufficient"]
