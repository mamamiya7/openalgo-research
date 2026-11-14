"""Small native-shaped evidence cases for versioned ATR capital recovery.

No prices are downloaded and no search/provider is run. The numbers isolate
whole-share feasibility, not profitable strategies or relaxed validation.
"""

from copy import deepcopy

import pytest
from test_capital_planning import prepared

from research import capital_planning as capital
from research.report_contract import fingerprint, settings_identity

PRICE, RISK = 6242.0, 203.55809619816227


def selected():
    rows = prepared()["selected"]
    rows[0]["name"] = "Scanner"
    rows[0]["config"].update(order_size_pct=100.0, stop_pct=45.0, cost_bps=10, slippage_bps=0)
    rows[0]["execution_policy"].update(
        sizing="stop_risk",
        risk_budget_fraction=0.5,
        exit_mode="atr",
        atr_period=14,
        atr_stop_multiple=1.0,
        atr_target_multiple=3.0,
    )
    rows[0]["execution_policy"]["risk_limits"].update(
        max_symbol_exposure_pct=25.0,
        max_correlated_exposure_pct=25.0,
        max_trade_risk_pct=1.0,
        max_total_stop_risk_pct=5.0,
        max_open_positions=10,
    )
    return rows


def row(settings, *, quantity=0, risk=RISK, equity=100000.0, tags=("risk_sizing_target",)):
    policy = settings[0]["execution_policy"]
    return {
        "strategy_id": settings[0]["id"],
        "symbol": "AAA",
        "signal_date": "2026-03-11",
        "source_row": 1,
        "quantity": quantity,
        "status": "closed" if quantity else "skipped",
        "reason": "close" if quantity else "Execution limits cannot admit one whole share",
        "execution_details": {
            "requested_quantity": 15,
            "constraints": list(tags),
            "sizing": "stop_risk",
            "opening_equity": equity,
            "initial_risk_per_share": risk,
            "target_stop_risk_pct": policy["risk_limits"]["max_trade_risk_pct"]
            * policy["risk_budget_fraction"],
            "exit_mode": "atr",
            "atr": {
                "version": "research-entry-atr-v1",
                "symbol": "AAA",
                "entry_session": "2026-03-12",
                "available_at": "2026-03-11T15:30:00+05:30",
            },
        },
    }


def make_plan(*, version=capital.ATR_VERSION, with_risk=True, observed_risk=RISK):
    settings = selected()
    source = {"strategies": settings, "ledger": [row(settings, risk=observed_risk)]}
    extra = (
        {"request_risks": capital.selected_account_risks(source, settings)}
        if with_risk and version == capital.ATR_VERSION
        else {}
    )
    return capital.selected_account_plan(
        settings,
        100000,
        {"scan": [PRICE]},
        version=version,
        request_prices={capital.request_identity(source["ledger"][0]): PRICE},
        **extra,
    )


def receipt(value, index, *, quantity=0, risk=RISK, equity=None, tags=("risk_sizing_target",)):
    settings = capital.selected_account_settings(value, index)
    amount = value["options"][index]["capital"]
    native = {
        "strategies": settings,
        "summary": {"initial_capital": amount},
        "ledger": [
            row(
                settings,
                quantity=quantity,
                risk=risk,
                equity=amount if equity is None else equity,
                tags=tags,
            )
        ],
    }
    return capital.selected_account_receipt(native, value, index)


def test_atr_unit_risk_funds_the_actual_reduced_target_with_unchanged_limits():
    old, value = make_plan(version=capital.CAUSAL_VERSION), make_plan()
    assert old["options"][2]["capital"] == 100000
    assert value["options"][2]["capital"] == 167000
    before = deepcopy(value["selected"])
    for index in (1, 2, 3):
        settings = capital.selected_account_settings(value, index)[0]
        option = value["options"][index]
        target = (
            settings["execution_policy"]["risk_limits"]["max_trade_risk_pct"]
            / 100
            * settings["execution_policy"]["risk_budget_fraction"]
        )
        assert (option["capital"] * 0.99 - PRICE * 0.001) * target >= RISK
        assert (
            settings["execution_policy"]["risk_limits"]
            == before[0]["execution_policy"]["risk_limits"]
        )
        assert settings["allocation_pct"] == before[0]["allocation_pct"]
    first = receipt(value, 0, quantity=1, tags=("shared_cash", "risk_sizing_target"))
    assert capital.selected_account_next(value, [first]) == 2
    second = receipt(value, 2, quantity=1)
    assert second["verified_sufficient"]
    result = capital.selected_account_finish(value, [first, second])
    assert result["operating_point"]["capital"] == 167000
    assert result["performance_validated"] is False  # Full native validation is still required.
    assert len(value["options"]) == value["max_accounts"] == 4
    assert value["selected"] == before


def test_reduction_then_observed_whole_share_failure_can_route_to_larger_frozen_capital():
    value = make_plan(observed_risk=201.0)
    first = receipt(value, 0, quantity=1, tags=("shared_cash", "risk_sizing_target"))
    # A later opportunity's native observation establishes a larger unit risk.
    # It is evidence for trying a registered account, not a promised pass.
    reduced = receipt(value, 2, risk=203.125)
    assert reduced["counts"]["whole_share_affordability"] == 1
    assert capital.selected_account_next(value, [first, reduced]) == 3
    assert value["options"][3]["capital"] > reduced["capital"]
    assert (
        capital.selected_account_next(
            value, [first, reduced, receipt(value, 3, quantity=1, risk=203.125)]
        )
        is None
    )
    assert capital._selected_route(value, reduced, tried={0, 2, 3}) is None


@pytest.mark.parametrize("problem", ["missing_price", "position", "liquidity", "too_much_risk"])
def test_unavailable_or_fixed_constraints_do_not_become_capital_permission(problem):
    value = make_plan()
    first = receipt(value, 0, quantity=1, tags=("shared_cash", "risk_sizing_target"))
    kwargs = {"risk": 210.0}
    if problem == "position":
        kwargs["tags"] = ("open_positions", "risk_sizing_target")
    elif problem == "liquidity":
        kwargs["tags"] = ("daily_volume_participation", "risk_sizing_target")
    elif problem == "too_much_risk":
        kwargs["risk"] = 1e9
    if problem == "missing_price":
        value["request_prices"] = {}
        value["id"] = fingerprint({k: v for k, v in value.items() if k != "id"})
        # Missing opening price means unavailable intent; no invented floor.
        blocked = receipt(value, 2, **kwargs)
        assert blocked["counts"]["unavailable_sizing_intent"] == 1
        assert capital._selected_route(value, blocked, tried={0, 2}) is None
    else:
        blocked = receipt(value, 2, **kwargs)
        assert capital.selected_account_next(value, [first, blocked]) is None


def test_duplicate_and_future_atr_observations_are_never_used_and_profit_is_irrelevant():
    settings = selected()
    source = {
        "strategies": settings,
        "ledger": [row(settings)],
        "summary": {"net_return_pct": -999},
    }
    original = capital.selected_account_risks(source, settings)
    source["summary"]["net_return_pct"] = 999
    assert capital.selected_account_risks(source, settings) == original
    source["ledger"] *= 2
    assert capital.selected_account_risks(source, settings)["observations"] == {}
    source["ledger"] = [row(settings)]
    source["ledger"][0]["execution_details"]["atr"]["available_at"] = "2026-03-12T15:30:00+05:30"
    assert capital.selected_account_risks(source, settings)["observations"] == {}
    source["ledger"][0]["execution_details"].pop("atr")
    assert capital.selected_account_risks(source, settings)["observations"] == {}


def test_risk_evidence_is_exactly_bound_and_legacy_policy_cannot_silently_use_it():
    settings = selected()
    evidence = capital.selected_account_risks(
        {"strategies": settings, "ledger": [row(settings)]}, settings
    )
    changed = deepcopy(settings)
    changed[0]["execution_policy"]["atr_stop_multiple"] = 2.0
    with pytest.raises(ValueError, match="settings binding"):
        capital.selected_account_plan(
            changed, 100000, {"scan": [PRICE]}, version=capital.ATR_VERSION, request_risks=evidence
        )
    with pytest.raises(ValueError, match="v8"):
        capital.selected_account_plan(
            settings,
            100000,
            {"scan": [PRICE]},
            version=capital.CAUSAL_VERSION,
            request_risks=evidence,
        )
    unknown = make_plan(with_risk=False)
    assert unknown["request_risks"]["observations"] == {}
    assert unknown["options"][2]["capital"] == 100000


def test_v7_wire_identity_and_historical_exhaustion_are_unchanged():
    old = make_plan(version=capital.CAUSAL_VERSION)
    assert old["id"] == "cf2e9457b58a379a8e7e41421aca548c94730a3d0601146e85ac033861ccc59e"
    assert "request_risks" not in old
    first = receipt(old, 0, quantity=1, tags=("shared_cash", "risk_sizing_target"))
    blocked = receipt(old, 2)
    assert capital.selected_account_next(old, [first]) == 2
    assert capital.selected_account_next(old, [first, blocked]) is None
    assert "whole_share_observations" not in blocked
    assert settings_identity(capital.selected_account_settings(old, 0)) == first["settings_id"]


@pytest.mark.timeout(240)
def test_native_atr_account_reduced_size_admits_one_share_using_recorded_prior_risk():
    from test_vectorbt_portfolio import DAYS, candle, signal, snapshot

    from research.connectors import vectorbt_portfolio as adapter
    from research.execution_policy import normalize_policy

    settings = selected()
    settings[0]["config"].update(hold_sessions=1)
    feature = {
        "version": "research-entry-atr-v1",
        "symbol": "AAA",
        "entry_session": DAYS[1],
        "atr_period": 14,
        "value": 200.0,
        "observations": 100,
        "from": "2025-01-01",
        "to": DAYS[0],
        "available_at": DAYS[0] + "T15:30:00+05:30",
        "basis_id": "a" * 64,
        "recipe_id": "b" * 64,
    }
    feature["id"] = fingerprint(feature)
    settings[0]["signals"] = [{**signal(), "execution_atr": feature}]
    # Use the native supported ATR contract to isolate this one-share mechanism.
    # This is an account-engine test, never full strategy acceptance evidence.
    settings[0]["execution_policy"] = normalize_policy(
        {
            "sizing": "stop_risk",
            "risk_budget_fraction": 0.5,
            "exit_mode": "atr",
            "atr_stop_multiple": 1,
            "atr_target_multiple": 3,
            "risk_limits": {
                "max_symbol_exposure_pct": 25,
                "max_trade_risk_pct": 1,
                "max_total_stop_risk_pct": 5,
                "max_open_positions": 10,
            },
        }
    )
    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"] = {day: candle(PRICE) for day in DAYS}
    baseline = adapter.evaluate(settings, prices, 100000)
    selected_settings, *_ = adapter.validate(settings, prices, 100000)
    key = capital.request_identity(baseline["ledger"][0])
    kwargs = {"request_prices": {key: PRICE}}
    old = capital.selected_account_plan(
        selected_settings,
        100000,
        {"scan": [PRICE]},
        version=capital.CAUSAL_VERSION,
        **kwargs,
    )
    new = capital.selected_account_plan(
        selected_settings,
        100000,
        {"scan": [PRICE]},
        version=capital.ATR_VERSION,
        request_risks=capital.selected_account_risks(baseline, selected_settings),
        **kwargs,
    )
    previous = adapter.evaluate(
        capital.selected_account_settings(old, 2), prices, old["options"][2]["capital"]
    )
    repaired = adapter.evaluate(
        capital.selected_account_settings(new, 2), prices, new["options"][2]["capital"]
    )
    assert previous["ledger"][0]["quantity"] == 0
    assert repaired["ledger"][0]["quantity"] >= 1
    assert (
        capital.selected_account_receipt(previous, old, 2)["counts"]["whole_share_affordability"]
        == 1
    )
    accepted = capital.selected_account_receipt(repaired, new, 2)
    assert accepted["verified_sufficient"]
    assert not accepted["performance_validated"]
    assert new["risk_limits"] == old["risk_limits"]
