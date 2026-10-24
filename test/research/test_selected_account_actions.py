"""Cause-routed capital/size tests preserve limits, opportunity cohort and verdicts."""

from copy import deepcopy

import pytest
from test_capital_planning import prepared

from research.automatic_actions import descriptor, enabled, entry_prices
from research.capital_planning import (
    selected_account_finish,
    selected_account_next,
    selected_account_plan,
    selected_account_receipt,
    selected_account_settings,
)
from research.report_contract import fingerprint


def programme(*, capital=100000, price=10000, max_accounts=4):
    selected = prepared()["selected"]
    return selected_account_plan(selected, capital, {"scan": [price]}, max_accounts=max_accounts)


def native(value, index, *, tags=(), quantity=10, requested=10, extra=(), profit=123):
    return {
        "summary": {"initial_capital": value["options"][index]["capital"], "net_profit": profit},
        "ledger": [
            {
                "strategy_id": "scan",
                "symbol": "ABC",
                "signal_date": "2026-01-01",
                "source_row": 1,
                "quantity": quantity,
                "status": "closed" if quantity else "skipped",
                "reason": "close"
                if quantity
                else "Execution limits cannot admit one whole share: requested_cash_budget",
                "execution_details": {"requested_quantity": requested, "constraints": list(tags)},
            },
            *deepcopy(extra),
        ],
    }


def receipt(value, index, **kwargs):
    return selected_account_receipt(native(value, index, **kwargs), value, index)


def test_selected_percentage_sizes_do_not_revert_to_original_fixed_tickets():
    value = programme()
    before = deepcopy(value)
    first = selected_account_settings(value, 0)[0]
    higher = selected_account_settings(value, 1)[0]
    half = selected_account_settings(value, 2)[0]
    assert higher["config"]["initial_capital"] > first["config"]["initial_capital"]
    assert higher["config"]["order_size_pct"] == first["config"]["order_size_pct"]
    assert half["config"]["order_size_pct"] == first["config"]["order_size_pct"] / 2
    for index in range(4):
        chosen = selected_account_settings(value, index)[0]
        assert chosen["execution_policy"] == value["selected"][0]["execution_policy"]
        assert chosen["allocation_pct"] == value["selected"][0]["allocation_pct"]
    assert value == before


def test_expensive_whole_share_floor_respects_tight_unchanged_symbol_ceiling():
    selected = prepared()["selected"]
    selected[0]["execution_policy"]["risk_limits"]["max_symbol_exposure_pct"] = 0.01
    value = selected_account_plan(selected, 100000, {"scan": [10000]})
    assert value["estimated_capital"] >= 10000 / 0.0001 / 0.99
    settings = selected_account_settings(value, 1)
    assert settings[0]["execution_policy"]["risk_limits"]["max_symbol_exposure_pct"] == 0.01
    assert settings[0]["config"]["order_size_pct"] == 3


def test_whole_share_failure_automatically_tests_predeclared_capital_and_records_retest():
    value = programme()
    first = receipt(value, 0, quantity=0, requested=0)
    assert selected_account_next(value, [first]) == 1
    second = receipt(value, 1)
    assert selected_account_next(value, [first, second]) is None
    result = selected_account_finish(value, [first, second])
    assert result["verified_capital"] == value["options"][1]["capital"]
    assert result["recommended_settings"] == selected_account_settings(value, 1)
    assert result["attempts"] == 2
    assert result["performance_validated"] is False


def test_cash_shortfall_reduces_requested_size_instead_of_scaling_same_fraction():
    value = programme()
    first = receipt(value, 0, tags=["shared_cash"], quantity=5)
    assert selected_account_next(value, [first]) == 2
    second = receipt(value, 2)
    result = selected_account_finish(value, [first, second])
    assert result["verified_capital"] == value["options"][2]["capital"]
    assert result["operating_point"]["settings"][0]["config"]["order_size_pct"] == 1.5


def test_mixed_position_and_missing_records_do_not_prevent_other_cash_remedy_or_disappear():
    value = programme()
    excluded = {
        "strategy_id": "scan",
        "symbol": "MISSING",
        "signal_date": "2026-01-01",
        "source_row": 2,
        "status": "excluded",
        "quantity": 0,
        "reason": "Missing price",
    }
    first = receipt(value, 0, tags=["shared_cash", "open_positions"], quantity=5, extra=[excluded])
    assert selected_account_next(value, [first]) == 2
    second = receipt(value, 2, tags=["open_positions"], quantity=5, extra=[excluded])
    result = selected_account_finish(value, [first, second])
    assert result["verified_capital"] is None
    point = result["operating_point"]
    assert point["option_index"] == 2
    assert point["remaining_exclusions"]["position_limit"] == 1
    assert point["remaining_exclusions"]["unavailable_evidence"] == 1
    assert point["performance_validated"] is False


def test_hard_position_and_liquidity_limits_do_not_trigger_extra_funds():
    value = programme()
    for tag in ("open_positions", "daily_volume_participation"):
        first = receipt(value, 0, tags=[tag], quantity=5)
        assert selected_account_next(value, [first]) is None
        result = selected_account_finish(value, [first])
        assert result["attempts"] == 1
        assert result["verified_capital"] is None
        assert result["operating_point"]["capital"] == 100000


def test_budget_is_frozen_and_profit_cannot_choose_operating_point():
    value = programme(max_accounts=3)
    assert len(value["options"]) == 3
    first = receipt(value, 0, tags=["shared_cash"], quantity=5, profit=1000000)
    second = receipt(value, 2, tags=["shared_cash"], quantity=6, profit=-999999)
    result = selected_account_finish(value, [first, second])
    # Both accounts admitted one request with one cash constraint. The frozen
    # capacity tie-break prefers less capital, regardless of their P&L values.
    assert result["operating_point"]["option_index"] == 0
    assert result["verified_capital"] is None
    assert selected_account_next(value, [first, second]) is None
    with pytest.raises(ValueError, match="frozen"):
        selected_account_settings(value, 3)


def test_resealed_changed_plan_and_changed_cohort_are_rejected():
    value = programme()
    corrupt = deepcopy(value)
    corrupt["options"][1]["capital"] += 1000
    corrupt["id"] = fingerprint({key: item for key, item in corrupt.items() if key != "id"})
    with pytest.raises(ValueError, match="declared policy"):
        selected_account_settings(corrupt, 1)
    first = receipt(value, 0, tags=["shared_cash"], quantity=5)
    changed = native(value, 2)
    changed["ledger"] = []
    second = selected_account_receipt(changed, value, 2)
    with pytest.raises(ValueError, match="cohort"):
        selected_account_next(value, [first, second])


def test_entry_prices_include_skipped_zero_shares_from_actual_next_open_not_later_prices():
    strategies = prepared()["selected"]
    report = native(programme(), 0, quantity=0, requested=0)
    snapshot = {
        "provenance": {"interval": "D"},
        "sessions": ["2026-01-01", "2026-01-02", "2026-01-05"],
        "bars": {"ABC": {"2026-01-02": {"open": 12345}, "2026-01-05": {"open": 999999}}},
    }
    assert entry_prices(report, strategies) == {"scan": []}
    assert entry_prices(report, strategies, snapshot) == {"scan": [12345.0]}
    report["ledger"][0]["status"] = "excluded"
    assert entry_prices(report, strategies, snapshot) == {"scan": []}


def test_missing_prices_never_become_invented_capital_evidence_and_optin_is_explicit():
    selected = prepared()["selected"]
    value = selected_account_plan(selected, 100000, {"scan": []})
    assert value["missing_price_strategies"] == ["scan"]
    assert not enabled({"automatic_research": {"decision_policy": "automatic_decision_v1"}})
    assert enabled({"automatic_research": {"action_policy": "automatic_actions_v1"}})
    assert descriptor()["max_accounts"] == 4


@pytest.mark.timeout(240)
def test_real_native_zero_share_account_is_automatically_retested_and_funds_selected_size():
    from test_vectorbt_portfolio import DAYS, candle, snapshot, strategy

    from research.connectors import vectorbt_portfolio as adapter
    from research.execution_policy import normalize_policy

    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"] = {day: candle(10000) for day in DAYS}
    original = strategy("scan", 100, order_size_pct=3, target_pct=40, stop_pct=5)
    original["execution_policy"] = normalize_policy()
    selected, *_ = adapter.validate([original], prices, 100000)
    initial = adapter.evaluate(selected, prices, 100000)
    assert initial["ledger"][0]["quantity"] == 0
    value = selected_account_plan(selected, 100000, entry_prices(initial, selected, prices))
    first = selected_account_receipt(initial, value, 0)
    assert selected_account_next(value, [first]) == 1
    settings = selected_account_settings(value, 1)
    corrected = adapter.evaluate(settings, prices, value["options"][1]["capital"])
    second = selected_account_receipt(corrected, value, 1)
    result = selected_account_finish(value, [first, second])
    assert corrected["ledger"][0]["quantity"] >= 1
    assert result["verified_capital"] == corrected["summary"]["initial_capital"]
    assert settings[0]["config"]["order_size_pct"] == selected[0]["config"]["order_size_pct"]
    assert settings[0]["execution_policy"] == selected[0]["execution_policy"]
    assert result["performance_validated"] is False


@pytest.mark.timeout(240)
def test_native_risk_target_reduces_actual_shares_when_cash_ceiling_is_not_binding():
    from test_vectorbt_portfolio import DAYS, candle, snapshot, strategy

    from research.connectors import vectorbt_portfolio as adapter
    from research.execution_policy import normalize_policy

    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"] = {day: candle(10000) for day in DAYS}
    original = strategy("scan", 100, order_size_pct=100, target_pct=40, stop_pct=5)
    original["execution_policy"] = normalize_policy(
        {"sizing": "stop_risk", "risk_budget_fraction": 0.5}
    )
    selected, *_ = adapter.validate([original], prices, 100000)
    value = selected_account_plan(selected, 100000, {"scan": [10000]})
    full, half = (selected_account_settings(value, index) for index in (1, 2))
    full_report = adapter.evaluate(full, prices, value["options"][1]["capital"])
    half_report = adapter.evaluate(half, prices, value["options"][2]["capital"])
    assert half_report["ledger"][0]["quantity"] < full_report["ledger"][0]["quantity"]
    assert full[0]["execution_policy"]["risk_limits"] == half[0]["execution_policy"]["risk_limits"]
    assert full[0]["allocation_pct"] == half[0]["allocation_pct"]
    assert half[0]["execution_policy"]["risk_budget_fraction"] == 0.25
