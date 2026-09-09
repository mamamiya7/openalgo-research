"""Actual native Nautilus fills and attribution in the isolated Linux runtime."""

import copy
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_vectorbt_portfolio import DAYS, candle, signal, strategy
from test_vectorbt_portfolio import snapshot as base_snapshot

from research.connectors import nautilus_portfolio as adapter

pytestmark = pytest.mark.timeout(120)
native = pytest.mark.skipif(
    sys.platform != "linux" or not importlib.util.find_spec("nautilus_trader"),
    reason="Nautilus native calculation requires its separate Linux runtime",
)


def snapshot(*args, **kwargs):
    result = base_snapshot(*args, **kwargs)
    result["instruments"] = {
        symbol: {"currency": "INR", "tick_size": "0.05", "lot_size": 1, "price_precision": 2}
        for symbol in result["bars"]
    }
    return result


def assert_reconciles(result):
    assert sum(row["net_pnl"] for row in result["per_strategy"]) == pytest.approx(
        result["summary"]["net_pnl"], abs=0.01
    )
    for i, point in enumerate(result["equity_curve"]):
        assert sum(
            row["equity_curve"][i]["net_pnl"] for row in result["per_strategy"]
        ) == pytest.approx(point["equity"] - result["summary"]["initial_capital"], abs=0.01)
    json.dumps(result, allow_nan=False)


def test_validation_requires_supplied_instrument_metadata():
    with pytest.raises(ValueError, match="instrument metadata"):
        adapter.validate([strategy()], base_snapshot(), 1000)


@pytest.mark.parametrize(
    "config, message",
    [({"slippage_bps": 10}, "zero slippage"), ({"trailing_pct": 5}, "trailing stops")],
)
def test_unsupported_config_rejected_explicitly(config, message):
    with pytest.raises(ValueError, match=message):
        adapter.validate([strategy(**config)], snapshot(), 1000)


def test_native_event_limit_checked_without_import():
    with patch.object(adapter, "MAX_EVENTS", 1), pytest.raises(ValueError, match="event limit"):
        adapter.validate([strategy()], snapshot(), 1000)


def test_metadata_precision_does_not_silently_round_broker_prices():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100.123)
    with pytest.raises(ValueError, match="precision"):
        adapter.validate([strategy()], prices, 1000)


def test_price_with_matching_decimal_precision_still_needs_supplied_tick_grid():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100.03)
    with pytest.raises(ValueError, match="tick size"):
        adapter.validate([strategy()], prices, 1000)


@pytest.mark.parametrize("value,tick,precision", [(1101.55, "0.1", 1), (9609.5, "1", 0)])
def test_real_historical_precision_differences_are_not_float_tolerances(value, tick, precision):
    item = {"currency": "INR", "lot_size": 1, "tick_size": tick, "price_precision": precision}
    bar = candle(value)
    before = copy.deepcopy(bar)
    assert "precision" in adapter.incompatible_price_reason("AAA", DAYS[1], bar, item)
    assert bar == before


@native
@pytest.mark.parametrize("minute", [False, True])
def test_native_mixed_windows_exclude_only_incompatible_lots_and_preserve_prices(monkeypatch, minute):
    from test_portfolio_coverage import evidence

    from services.research_instruments import prepare_nautilus_instruments

    prices = snapshot(minute=minute, days=DAYS[:1] if minute else DAYS + ["2026-01-09"])
    prices["instruments"]["AAA"].update(tick_size="0.1", price_precision=1)
    if minute:
        early = signal(timestamp="09:15")
        late = signal(day=0, row=7, timestamp="09:17")
        bad_key, last_key = prices["timeline"][1], prices["timeline"][4]
        config = {"hold_minutes": 1, "trade_horizon": "intraday"}
        other = signal("BBB", timestamp="09:15")
    else:
        early, late = signal(), signal(day=2, row=7)
        bad_key, last_key = DAYS[1], "2026-01-09"
        config = {"hold_sessions": 1}
        other = signal("BBB")
    prices["bars"]["AAA"][bad_key] = candle(100.15)
    prices["bars"]["AAA"][last_key] = candle(110)
    rows = [
        strategy("a", signals=[early, late], target_pct=200, stop_pct=50, **config),
        strategy("b", signals=[other], target_pct=200, stop_pct=50, **config),
    ]
    original = evidence(rows, prices)
    before = copy.deepcopy(original)
    monkeypatch.setattr(
        "services.research_instruments.native_instruments",
        lambda symbols, **kw: {s: prices["instruments"][s] for s in symbols},
    )
    frozen = prepare_nautilus_instruments(original)
    assert original == before
    assert frozen["snapshot"]["bars"] == prices["bars"]
    assert frozen["snapshot"]["instrument_eligibility"]["policy_version"] == (
        adapter.INSTRUMENT_ELIGIBILITY_VERSION
    )
    assert frozen["signal_coverage"]["excluded_signals"] == 1
    result = adapter.evaluate(frozen["strategies"], frozen["snapshot"], 1000)
    assert [r["status"] for r in result["ledger"]] == ["excluded", "closed", "closed"]
    assert [r["quantity"] for r in result["ledger"]] == [0, 5, 5]
    assert result["summary"]["final_equity"] == 1050
    assert result["ledger"][1]["source_row"] == 7
    assert len(result["equity_curve"]) == len(prices["timeline" if minute else "sessions"])
    assert len({r["strategy_id"] for r in result["engine_records"]["trades"]}) == 2
    assert result["execution"]["market_event_scope"] == "eligible_potential_holding_windows"
    assert_reconciles(result)


@native
def test_metadata_exclusions_use_maximum_search_window_in_every_native_trial(monkeypatch):
    from test_portfolio_coverage import evidence

    from services.research_instruments import prepare_nautilus_instruments

    prices = snapshot()
    prices["bars"]["AAA"][DAYS[3]] = candle(100.03)
    rows = [strategy("a"), strategy("b", signals=[signal("BBB")])]
    rows[0]["search"] = {"hold_sessions": {"min": 1, "max": 2, "step": 1}}
    monkeypatch.setattr(
        "services.research_instruments.native_instruments",
        lambda symbols, **kw: {s: prices["instruments"][s] for s in symbols},
    )
    frozen = prepare_nautilus_instruments(
        evidence(rows, prices, {"sampler": "tpe", "trials": 2})
    )
    assert "tick size" in frozen["strategies"][0]["signals"][0]["research_exclusion"]
    for holding in (1, 2):
        trial = copy.deepcopy(frozen["strategies"])
        trial[0]["config"]["hold_sessions"] = holding
        result = adapter.evaluate(trial, frozen["snapshot"], 1000)
        assert [r["status"] for r in result["ledger"]] == ["excluded", "closed"]
        assert result["per_strategy"][0]["net_pnl"] == 0
        assert all(r["instrument_id"] == "BBB.NSE" for r in result["engine_records"]["orders"])
        assert_reconciles(result)


def test_native_currency_precision_does_not_silently_round_capital():
    with pytest.raises(ValueError, match="two decimal places"):
        adapter.validate([strategy()], snapshot(), 1000.123)


@native
def test_two_real_strategies_same_instrument_keep_separate_native_positions():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 112, 99, 108)
    prices["bars"]["AAA"][DAYS[2]] = candle(108, 109, 104, 105)
    result = adapter.evaluate(
        [strategy("a", target_pct=10), strategy("b", target_pct=30)], prices, 1000
    )
    a, b = result["ledger"]
    assert (a["quantity"], b["quantity"], a["pnl"], b["pnl"]) == (5, 5, 50, 25)
    assert result["summary"]["final_equity"] == 1075
    positions = result["engine_records"]["trades"]
    assert len({p["strategy_id"] for p in positions}) == 2
    assert len({p["position_id"] for p in positions}) == 2
    assert len({p["instrument_id"] for p in positions}) == 1
    assert len({p["account_id"] for p in positions}) == 1
    assert result["execution"]["simulation_api"] == "BacktestEngine"
    assert_reconciles(result)


@native
def test_same_strategy_repeated_symbol_lots_remain_independent():
    result = adapter.evaluate(
        [strategy("a", 100, signals=[signal(), signal(day=1, row=3)], order_size_pct=40)],
        snapshot(),
        1000,
    )
    assert [r["quantity"] for r in result["ledger"]] == [4, 4]
    assert [r["exit_date"] for r in result["ledger"]] == [DAYS[2], DAYS[3]]
    assert len({p["position_id"] for p in result["engine_records"]["trades"]}) == 2
    assert_reconciles(result)


@native
def test_shared_cash_and_allocation_caps_are_enforced():
    result = adapter.evaluate([strategy("a", 60), strategy("b", 60)], snapshot(), 1000)
    assert [r["quantity"] for r in result["ledger"]] == [6, 0]
    assert "Insufficient opening cash" in result["ledger"][1]["reason"]
    capped = adapter.evaluate(
        [strategy("a", 50, signals=[signal(), signal(row=3)], order_size_pct=60)], snapshot(), 1000
    )
    assert [r["quantity"] for r in capped["ledger"]] == [3, 0]
    assert capped["ledger"][1]["reason"] == "Strategy allocation is already in use"


@native
def test_per_strategy_native_fees_and_account_reconcile():
    result = adapter.evaluate(
        [strategy("a", cost_bps=100), strategy("b", cost_bps=200)], snapshot(), 1000
    )
    assert [r["quantity"] for r in result["ledger"]] == [4, 4]
    assert [r["fees"] for r in result["ledger"]] == [8, 16]
    assert [r["pnl"] for r in result["ledger"]] == [-8, -16]
    assert result["summary"]["final_equity"] == 976
    assert sum(f["commission"] for f in result["engine_records"]["fills"]) == 24
    assert_reconciles(result)


@native
@pytest.mark.parametrize("opening, quantity", [(100, 0), (111, 6)])
def test_only_opening_proceeds_fund_new_entries(opening, quantity):
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(opening, 112, 99, 105)
    result = adapter.evaluate(
        [strategy("a", 60, hold_sessions=2), strategy("b", 60, signals=[signal("BBB", day=1)])],
        prices,
        1000,
    )
    assert result["ledger"][1]["quantity"] == quantity
    assert_reconciles(result)


@native
def test_native_stop_first_and_entry_bar_protection():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 115, 90, 100)
    result = adapter.evaluate([strategy("a", 100)], prices, 1000)
    row = result["ledger"][0]
    assert (row["entry_date"], row["exit_date"], row["outcome"]) == (DAYS[1], DAYS[1], "stop")
    assert row["exit_price"] == 90
    assert result["summary"]["final_equity"] == 900
    assert_reconciles(result)


@native
def test_timestamped_intraday_entry_and_scheduled_close():
    result = adapter.evaluate(
        [strategy("a", 100, signals=[signal(timestamp="09:15")], trade_horizon="intraday")],
        snapshot(minute=True, days=DAYS[:1]),
        1000,
    )
    row = result["ledger"][0]
    assert row["entry_timestamp"] == f"{DAYS[0]}T09:16:00+05:30"
    assert row["exit_timestamp"] == f"{DAYS[0]}T09:20:00+05:30"
    assert_reconciles(result)


@native
def test_minute_hold_opening_exit_can_fund_other_strategy():
    result = adapter.evaluate(
        [
            strategy("a", 100, signals=[signal(timestamp="09:15")], hold_minutes=2),
            strategy(
                "b", 100, signals=[signal("BBB", timestamp="09:17")], trade_horizon="intraday"
            ),
        ],
        snapshot(minute=True, days=DAYS[:1]),
        1000,
    )
    assert [r["quantity"] for r in result["ledger"]] == [10, 10]
    assert result["ledger"][0]["exit_timestamp"] == result["ledger"][1]["entry_timestamp"]


@native
def test_minute_multiday_date_only_signal_and_pending_tail():
    prices = snapshot(minute=True, days=DAYS[:2])
    result = adapter.evaluate(
        [
            strategy("a", 50, signals=[signal()], hold_sessions=4),
            strategy("b", 50, signals=[signal(day=1)]),
        ],
        prices,
        1000,
    )
    assert result["ledger"][0]["entry_timestamp"] == f"{DAYS[1]}T09:15:00+05:30"
    assert result["summary"]["pending_trades"] == 1
    assert result["summary"]["unfunded_pending"] == 1
    assert_reconciles(result)


@native
def test_zero_allocation_no_fill_and_inputs_unchanged():
    strategies, prices = [strategy("a", 0), strategy("b", 50)], snapshot()
    before = copy.deepcopy((strategies, prices))
    result = adapter.evaluate(strategies, prices, 1000)
    assert (strategies, prices) == before
    assert [r["quantity"] for r in result["ledger"]] == [0, 5]
    assert_reconciles(result)


@native
def test_cancellation_disposes_native_engine_and_subsequent_run_works():
    def progress(done, total):
        if done == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        adapter.evaluate([strategy()], snapshot(), 1000, progress=progress)
    assert adapter.evaluate([strategy()], snapshot(), 1000)["summary"]["closed_trades"] == 1


@native
def test_repeated_engine_disposal_does_not_accumulate_descriptors():
    adapter.evaluate([strategy()], snapshot(), 1000)
    before = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(5):
        adapter.evaluate([strategy()], snapshot(), 1000)
    after = len(list(Path("/proc/self/fd").iterdir()))
    assert after <= before + 2


@native
def test_native_runtime_never_calls_vectorbt_or_legacy_calculation():
    with (
        patch(
            "research.connectors.vectorbt_portfolio.evaluate",
            side_effect=AssertionError("wrong engine"),
        ),
        patch("research.engine.evaluate", side_effect=AssertionError("wrong engine")),
    ):
        result = adapter.evaluate([strategy()], snapshot(), 1000)
    assert result["summary"]["closed_trades"] == 1


@native
def test_parent_frozen_exclusion_has_no_orders_or_invented_metadata():
    prices = snapshot(symbols=("AAA",))
    excluded = {
        **signal("MISSING"),
        "research_exclusion": "Broker did not supply the required prices",
    }
    result = adapter.evaluate(
        [strategy("a", signals=[signal(), excluded]), strategy("b", signals=[excluded])],
        prices,
        1000,
    )
    assert [r["status"] for r in result["ledger"]] == ["closed", "excluded", "excluded"]
    assert result["per_strategy"][1]["net_pnl"] == 0
    assert all(r["instrument_id"] == "AAA.NSE" for r in result["engine_records"]["orders"])
    assert_reconciles(result)


@native
def test_period_slice_with_empty_strategy_retains_zero_attribution():
    result = adapter.evaluate([strategy("a", signals=[]), strategy("b")], snapshot(), 1000)
    assert result["per_strategy"][0]["net_pnl"] == 0
    assert result["per_strategy"][0]["summary"]["accepted_trades"] == 0
    assert result["summary"]["accepted_trades"] == 1
    assert_reconciles(result)


@native
def test_all_future_entries_need_no_invented_prices_or_instruments():
    prices = snapshot()
    prices.pop("instruments")
    result = adapter.evaluate([strategy("a", signals=[signal(day=3)])], prices, 1000)
    assert result["summary"]["unfunded_pending"] == 1
    assert result["summary"]["final_equity"] == 1000
    assert result["engine_records"]["orders"] == []
    assert_reconciles(result)
