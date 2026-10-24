"""Small native accounts prove inactive-universe names do not dilute tickets."""

from copy import deepcopy

import pytest
from test_portfolio_wide_allocation import account

from research.connectors import vectorbt_portfolio as native
from research.portfolio_allocation import ACTIVE_MODE, active_weights
from research.portfolio_constraint_evidence import assess

pytestmark = pytest.mark.timeout(180)


def test_active_subset_preserves_inverse_vol_and_occupied_cluster_budgets():
    feature = {"weights": {"A": 0.1, "B": 0.4, "C": 0.5}, "clusters": [["A", "B"], ["C"]]}
    weights, groups = active_weights(feature, ["A", "A", "C"])
    assert weights == {"A": 0.5, "C": 0.5}
    assert groups == [["A"], ["C"]]
    assert active_weights(feature, ["A"])[0] == {"A": 1}
    with pytest.raises(ValueError):
        active_weights(feature, ["MISSING"])


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize("symbols", [("AAA",), ("CCC",), ("AAA", "BBB")])
def test_actual_fills_use_active_universe_with_compiled_reference_parity(symbols, minute):
    prepared, attached, _, _ = account(mode=ACTIVE_MODE, minute=minute)
    for row in attached["strategies"]:
        row["signals"] = [signal for signal in row["signals"] if signal["symbol"] in symbols]
    compiled = native.evaluate(attached["strategies"], attached["snapshot"], 10000)
    with native.python_reference():
        reference = native.evaluate(attached["strategies"], attached["snapshot"], 10000)
    assert compiled["ledger"] == reference["ledger"]
    filled = [row for row in compiled["ledger"] if row["quantity"]]
    assert filled
    for row in filled:
        details = row["execution_details"]
        assert details["allocation_active_symbols"] == list(symbols)
        assert details["allocation_weight"] == pytest.approx(1 / len(symbols))
        assert details["allocation_cluster"] == list(symbols)
    assert assess(compiled, prepared["portfolio_execution_features"])["status"] == "supported"
    forged = deepcopy(compiled)
    forged["ledger"][0]["execution_details"]["allocation_weight"] = 0.01
    assert assess(forged, prepared["portfolio_execution_features"])["status"] == "invalid"


@pytest.mark.parametrize(
    "kind", ["excluded", "unaffordable", "unaffordable_other_cluster", "duplicate"]
)
def test_ineligible_and_duplicate_signals_do_not_dilute_active_budget(kind):
    prepared, attached, _, _ = account(mode=ACTIVE_MODE)
    signals = attached["strategies"][0]["signals"]
    signals[:] = [signal for signal in signals if signal["symbol"] == "AAA"]
    if kind == "duplicate":
        signals.append(deepcopy(signals[0]))
    else:
        extra = deepcopy(signals[0])
        extra["symbol"] = "CCC" if kind == "unaffordable_other_cluster" else "BBB"
        if kind == "excluded":
            extra["research_exclusion"] = "Test source rule exclusion"
        else:
            for bar in attached["snapshot"]["bars"][extra["symbol"]].values():
                bar.update(open=1e6, high=1e6 + 1, low=1e6 - 1, close=1e6)
        signals.append(extra)
    report = native.evaluate(attached["strategies"], attached["snapshot"], 10000)
    filled = [row for row in report["ledger"] if row["quantity"]]
    assert filled
    assert all(row["execution_details"]["allocation_weight"] == 1 for row in filled)
    assert all(row["execution_details"]["allocation_active_symbols"] == ["AAA"] for row in filled)
    if not kind.startswith("unaffordable"):
        assert assess(report, prepared["portfolio_execution_features"])["status"] == "supported"


def test_omitting_an_active_symbol_is_rejected_even_if_weight_and_caps_are_resealed():
    prepared, attached, _, _ = account(mode=ACTIVE_MODE)
    report = native.evaluate(attached["strategies"], attached["snapshot"], 10000)
    forged = deepcopy(report)
    details = next(row["execution_details"] for row in forged["ledger"] if row["quantity"])
    details["allocation_active_symbols"] = ["AAA"]
    assert assess(forged, prepared["portfolio_execution_features"])["status"] == "invalid"


def test_held_symbol_keeps_budget_when_a_later_symbol_arrives():
    from test_portfolio_execution_features import prepare

    from research import execution_features as atr
    from research import portfolio_execution_features as features

    original, _, context, records = account(mode=ACTIVE_MODE)
    evidence = {
        key: deepcopy(value)
        for key, value in original.items()
        if key not in ("execution_features", "portfolio_execution_features")
    }
    signals = evidence["strategies"][0]["signals"]
    signals[:] = [signal for signal in signals if signal["symbol"] in ("AAA", "BBB")]
    days = evidence["snapshot"]["sessions"]
    first = days.index(signals[0]["date"])
    next(signal for signal in signals if signal["symbol"] == "BBB")["date"] = days[first + 1]
    evidence["strategies"][0]["config"].update(order_size_pct=25, hold_sessions=3)
    prepared = prepare(evidence, context, records)
    attached = features.attach(atr.attach(prepared))
    compiled = native.evaluate(attached["strategies"], attached["snapshot"], 10000)
    with native.python_reference():
        reference = native.evaluate(attached["strategies"], attached["snapshot"], 10000)
    assert compiled["ledger"] == reference["ledger"]
    later = next(row for row in compiled["ledger"] if row["symbol"] == "BBB")
    assert later["quantity"] > 0
    assert later["execution_details"]["allocation_active_symbols"] == ["AAA", "BBB"]
    assert later["execution_details"]["allocation_weight"] == pytest.approx(0.5)
    assert assess(compiled, prepared["portfolio_execution_features"])["status"] == "supported"
