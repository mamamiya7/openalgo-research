"""Exact financial evidence parity against the retained Python callback policy."""

from copy import deepcopy

import pytest
from test_execution_aware_native import atr_feature, controlled, policy
from test_priority_native import competing
from test_turnover_native import rows as turnover_rows
from test_vectorbt_portfolio import DAYS, candle, signal, snapshot, strategy

from research.connectors import vectorbt_portfolio as adapter
from research.portfolio import fingerprint
from research.prepared_calculation import prepared_calculation

pytestmark = pytest.mark.timeout(240)


def compare(rows, prices, capital=1000):
    original = deepcopy((rows, prices))
    with adapter.python_reference():
        reference = adapter.evaluate(rows, prices, capital)
    with prepared_calculation(prices) as market:
        compiled = adapter.evaluate(rows, market.snapshot, capital)
    assert compiled == reference
    assert fingerprint(compiled) == fingerprint(reference)
    assert (rows, prices) == original
    return compiled


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize("priority", ["source", "reversed_source", "lower_stop_risk"])
def test_shared_cash_priority_native_records_and_full_receipts(priority, minute):
    prices = snapshot(minute=minute)
    compare(competing(priority, minute=minute, costs=(11, 13)), prices)


@pytest.mark.parametrize("minute", [False, True])
def test_turnover_and_cost_friction_are_exact(minute):
    current = turnover_rows(minute=minute, limit=33, order_size=80)
    for row in current:
        row["config"].update(cost_bps=13, slippage_bps=17, trailing_enabled=True, trailing_pct=3)
    compare(current, snapshot(minute=minute))


@pytest.mark.parametrize("sizing", ["allocation", "stop_risk"])
def test_atr_protection_and_binding_stop_risk_caps(sizing):
    event = signal()
    event["execution_atr"] = atr_feature()
    current = [
        controlled(
            execution=policy(
                atr=True, sizing=sizing, max_trade_risk_pct=1, max_total_stop_risk_pct=2
            ),
            signals=[event],
            cost_bps=13,
            slippage_bps=17,
        )
    ]
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 105, 99, 104)
    prices["bars"]["AAA"][DAYS[2]] = candle(104, 105, 90, 93)
    compare(current, prices)


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize("large_union", [False, True])
def test_real_sealed_correlation_cluster_allocation_receipts(minute, large_union):
    from test_portfolio_wide_allocation import account

    _, attached, _, _ = account(minute=minute)
    if large_union:
        prices = attached["snapshot"]["bars"]
        for i in range(201):
            prices[f"ZZ{i:03}"] = deepcopy(prices["AAA"])
    compare(attached["strategies"], attached["snapshot"], 10000)


def test_cancel_exception_restores_progress_owner_and_next_account():
    rows, prices = [strategy()], snapshot()
    seen = []

    def stop(i, total):
        seen.append(i)
        if i == 1:
            raise RuntimeError("cancelled at native bar boundary")

    with pytest.raises(RuntimeError, match="cancelled at native bar boundary"):
        adapter.evaluate(rows, prices, 1000, progress=stop)
    assert 1 in seen
    compare(rows, prices)


def test_legacy_pending_and_sparse_marks_are_exact():
    prices = snapshot()
    prices["bars"]["BBB"] = {}
    compare([strategy(signals=[signal(), signal(day=3)])], prices)


def test_unknown_symbol_with_no_future_entry_keeps_pending_evidence():
    compare([strategy(signals=[signal("ABSENT", day=3)])], snapshot())


@pytest.mark.parametrize("minute", [False, True])
def test_discarded_report_native_array_kernels_match_public_portfolio_methods(minute):
    with adapter.discard_trial_analysis():
        compare(turnover_rows(minute=minute), snapshot(minute=minute))
