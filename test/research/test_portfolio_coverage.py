"""Incomplete broker data must never silently change an optimizer's cohort."""

import copy

import pytest
from test_vectorbt_portfolio import DAYS, assert_reconciles, signal, snapshot, strategy

from research.connectors.vectorbt_portfolio import evaluate
from research.portfolio_coverage import prepare


def evidence(rows, prices, optimization=None):
    portfolio = {"capital": 10000, "strategies": rows}
    if optimization:
        portfolio["optimization"] = optimization
    return {
        "portfolio": portfolio,
        "strategies": rows,
        "snapshot": prices,
        "signals": [s for row in rows for s in row["signals"]],
    }


def test_missing_prices_exclude_only_affected_signal_and_preserve_original():
    rows = [strategy("a"), strategy("b", signals=[signal("BBB")])]
    prices = snapshot()
    prices["bars"].pop("BBB")
    original = evidence(rows, prices)
    frozen = prepare(original)
    assert frozen["signal_coverage"]["eligible_signals"] == 1
    assert frozen["signal_coverage"]["excluded_signals"] == 1
    assert "research_exclusion" not in original["strategies"][1]["signals"][0]
    result = evaluate(frozen["strategies"], frozen["snapshot"], 10000)
    assert result["summary"]["excluded_signals"] == 1
    assert result["ledger"][1]["status"] == "excluded"
    assert result["per_strategy"][1]["net_pnl"] == 0
    assert_reconciles(result)


def test_optimizer_uses_maximum_window_before_scoring_shorter_candidates():
    rows = [strategy("a"), strategy("b", signals=[signal("BBB")])]
    rows[1]["search"] = {"hold_sessions": {"min": 1, "max": 2, "step": 1}}
    prices = snapshot()
    prices["bars"]["BBB"].pop(DAYS[3])
    frozen = prepare(evidence(rows, prices, {"sampler": "tpe", "trials": 2}))
    assert frozen["signal_coverage"]["excluded_signals"] == 1
    for holding in (1, 2):
        trial = copy.deepcopy(frozen["strategies"])
        trial[1]["config"]["hold_sessions"] = holding
        result = evaluate(trial, prices, 10000)
        assert result["ledger"][1]["status"] == "excluded"


def test_no_complete_signal_fails_without_a_misleading_zero_return():
    prices = snapshot()
    prices["bars"] = {}
    with pytest.raises(ValueError, match="No signals have complete"):
        prepare(evidence([strategy("a")], prices))


def test_strategy_without_signals_in_a_period_keeps_zero_contribution():
    result = evaluate([strategy("a"), strategy("b", signals=[])], snapshot(), 10000)
    assert result["per_strategy"][1]["net_pnl"] == 0
    assert result["per_strategy"][1]["summary"]["accepted_trades"] == 0
    assert_reconciles(result)
