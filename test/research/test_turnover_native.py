"""Actual-fill daily buy budgets with causal frozen equity and exact old reports."""

import gc
import time
from copy import deepcopy

import pytest
from test_execution_aware_native import atr_feature, controlled, policy
from test_priority_native import competing, priority_policy
from test_vectorbt_portfolio import DAYS, assert_reconciles, candle, signal, snapshot, strategy

from research.connectors import vectorbt_portfolio as native
from research.execution_policy import (
    PRIORITY_VERSION,
    TURNOVER_RISK_VERSION,
    TURNOVER_VERSION,
    normalize_limits,
    normalize_policy,
)
from research.portfolio import fingerprint

pytestmark = pytest.mark.timeout(240)


def turnover_policy(limit=50, priority="source", **limits):
    previous = policy()
    return normalize_policy(
        {
            **previous,
            "version": TURNOVER_VERSION,
            "entry_priority": priority,
            "risk_limits": {
                **previous["risk_limits"],
                "version": TURNOVER_RISK_VERSION,
                "max_daily_buy_turnover_pct": limit,
                **limits,
            },
        }
    )


def rows(*, limit=50, priority="source", minute=False, order_size=100, **limits):
    execution = turnover_policy(limit, priority, **limits)
    return [
        controlled(
            identity,
            100,
            execution=deepcopy(execution),
            signals=[signal(symbol, timestamp="09:15" if minute else None)],
            stop_pct=20 if identity == "a" else 10,
            target_pct=500,
            hold_sessions=3,
            order_size_pct=order_size,
        )
        for identity, symbol in (("a", "AAA"), ("b", "BBB"))
    ]


def daily(report, day):
    return next(item for item in report["execution"]["turnover"]["daily"] if item["date"] == day)


@pytest.mark.parametrize(
    "value", [None, True, False, 0, -1, 1000.001, "50", float("nan"), float("inf")]
)
def test_invalid_turnover_bounds_are_rejected(value):
    with pytest.raises(ValueError):
        turnover_policy(value)


@pytest.mark.parametrize("value", [0.01, 100, 1000])
def test_turnover_limits_are_versioned_and_positive_bounded(value):
    result = turnover_policy(value)
    assert result["risk_limits"]["max_daily_buy_turnover_pct"] == value
    assert (
        normalize_policy({"version": TURNOVER_VERSION})["risk_limits"]["max_daily_buy_turnover_pct"]
        == 100
    )
    assert normalize_limits({"version": TURNOVER_RISK_VERSION})["max_daily_buy_turnover_pct"] == 100
    assert (
        normalize_policy({"version": PRIORITY_VERSION})["risk_limits"]["version"]
        == "research-risk-limits-v1"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "old_limits",
        "new_limits",
        "unversioned_new_limits",
        "unknown_limit",
        "mixed_limits",
        "mixed_policy",
        "mixed_priority",
    ],
)
def test_account_and_policy_limits_must_match(mutation):
    current = rows()
    if mutation == "old_limits":
        current[0]["execution_policy"]["risk_limits"] = policy()["risk_limits"]
    elif mutation == "new_limits":
        current[0]["execution_policy"]["version"] = PRIORITY_VERSION
    elif mutation == "unversioned_new_limits":
        del current[0]["execution_policy"]["risk_limits"]["version"]
    elif mutation == "unknown_limit":
        current[0]["execution_policy"]["risk_limits"]["max_turnover"] = 1
    elif mutation == "mixed_limits":
        current[0]["execution_policy"]["risk_limits"]["max_daily_buy_turnover_pct"] = 75
    elif mutation == "mixed_policy":
        current[0]["execution_policy"] = priority_policy()
    else:
        current[0]["execution_policy"]["entry_priority"] = "reversed_source"
    with pytest.raises(ValueError):
        native.evaluate(current, snapshot(), 1000)


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize(
    "priority,expected",
    [("source", [5, 0]), ("reversed_source", [0, 5]), ("lower_stop_risk", [0, 5])],
)
def test_turnover_is_shared_across_strategies_under_actual_priority(minute, priority, expected):
    current, prices = rows(priority=priority, minute=minute), snapshot(minute=minute)
    before = deepcopy((current, prices))
    report = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == expected
    filled_day = DAYS[0] if minute else DAYS[1]
    receipt = daily(report, filled_day)
    assert receipt["opening_equity"] == 1000 and receipt["buy_budget"] == 500
    assert receipt["buy_notional"] == 500 and receipt["buy_fills"] == 1
    assert receipt["buy_turnover_pct"] == 50 and receipt["remaining_buy_budget"] == 0
    assert report["policy_version"] == native.TURNOVER_POLICY_VERSION
    assert report["execution"]["adapter_version"] == native.TURNOVER_ADAPTER_VERSION
    assert (
        "daily_buy_turnover"
        in report["ledger"][expected.index(0)]["execution_details"]["constraints"]
    )
    assert (current, prices) == before
    assert_reconciles(report)


def test_actual_buy_notional_uses_slipped_price_but_excludes_fees():
    current = rows(limit=50)
    current[0]["config"].update(cost_bps=100, slippage_bps=100)
    report = native.evaluate(current, snapshot(), 1000)
    first = report["ledger"][0]
    assert first["quantity"] == 4 and first["entry_price"] == 101
    assert first["execution_details"]["daily_buy_capacity_shares"] == pytest.approx(500 / 101)
    receipt = daily(report, DAYS[1])
    assert receipt["buy_notional"] == 404
    assert receipt["remaining_buy_budget"] == 96
    assert receipt["buy_turnover_pct"] == pytest.approx(40.4)
    assert report["execution"]["activity"]["fees"] > 0
    assert report["ledger"][1]["quantity"] == 0
    assert_reconciles(report)


def test_shared_remaining_budget_does_not_reserve_unfilled_cash_request():
    current = rows(limit=70, order_size=30)
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 3]
    assert daily(report, DAYS[1])["buy_notional"] == 600
    assert daily(report, DAYS[1])["remaining_buy_budget"] == 100
    assert report["ledger"][1]["execution_details"]["daily_buy_notional_before"] == 300


def test_same_bar_protective_exit_is_unrestricted_and_never_replenishes_daily_budget():
    prices = snapshot(minute=True)
    prices["bars"]["AAA"][prices["timeline"][1]] = candle(100, 110, 70, 100)
    current = rows(limit=50, minute=True)
    current[1]["signals"] = [signal("BBB", timestamp="09:16")]
    report = native.evaluate(current, prices, 1000)
    first, second = report["ledger"]
    assert first["quantity"] == 5 and first["outcome"] == "stop"
    assert first["entry_timestamp"] == first["exit_timestamp"]
    assert second["quantity"] == 0 and second["status"] == "skipped"
    receipt = daily(report, DAYS[0])
    assert receipt["buy_notional"] == 500 and receipt["buy_budget"] == 500
    assert report["execution"]["activity"]["sell_notional"] == 400
    assert_reconciles(report)


def test_opening_exit_releases_cash_but_not_a_second_buy_budget_on_same_date():
    prices = snapshot(minute=True)
    prices["bars"]["AAA"][prices["timeline"][2]] = candle(200)
    current = rows(limit=50, minute=True)
    current[0]["config"]["target_pct"] = 50
    current[1]["signals"] = [signal("BBB", timestamp="09:16")]
    report = native.evaluate(current, prices, 1000)
    assert report["ledger"][0]["exit_timing"] == "open"
    assert report["ledger"][0]["exit_price"] == 200
    assert report["ledger"][1]["quantity"] == 0
    assert daily(report, DAYS[0])["opening_equity"] == 1000
    assert daily(report, DAYS[0])["buy_notional"] == 500


def test_daily_budget_resets_and_values_carried_positions_at_first_open_only():
    prices = snapshot(minute=True)
    for key in prices["timeline"]:
        if key[:10] == DAYS[1]:
            prices["bars"]["AAA"][key] = candle(200 if key[11:16] == "09:15" else 300)
    current = rows(limit=50, minute=True)
    current[0]["config"]["order_size_pct"] = 20
    current[1]["signals"] = [signal("BBB", day=1, timestamp="09:15")]
    report = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == [2, 6]
    receipt = daily(report, DAYS[1])
    assert receipt["opening_equity"] == 1200  # 800 cash + 2 carried shares at 200.
    assert receipt["buy_budget"] == 600 and receipt["buy_notional"] == 600
    assert receipt["opening_at"] == DAYS[1] + "T09:15:00+05:30"
    assert report["ledger"][1]["execution_details"]["opening_equity"] == 1400
    assert report["ledger"][1]["execution_details"]["daily_opening_equity"] == 1200
    assert_reconciles(report)


def test_final_prices_do_not_influence_frozen_daily_budget_or_entry_size():
    current, prices = rows(), snapshot()
    first = native.evaluate(current, prices, 1000)
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 500, 1, 250)
    prices["bars"]["BBB"][DAYS[1]] = candle(100, 500, 1, 250)
    changed = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in first["ledger"]] == [
        row["quantity"] for row in changed["ledger"]
    ]
    assert daily(first, DAYS[1]) == daily(changed, DAYS[1])


@pytest.mark.parametrize(
    "other_limit,expected",
    [("max_symbol_exposure_pct", 2), ("max_gross_exposure_pct", 2), ("max_trade_risk_pct", 1)],
)
def test_turnover_composes_with_other_hard_caps(other_limit, expected):
    value = 25 if other_limit != "max_trade_risk_pct" else 2
    current = rows(limit=50, **{other_limit: value})
    report = native.evaluate(current[:1], snapshot(), 1000)
    assert report["ledger"][0]["quantity"] == expected
    assert daily(report, DAYS[1])["buy_notional"] == expected * 100
    constraint = {
        "max_symbol_exposure_pct": "symbol_exposure",
        "max_gross_exposure_pct": "gross_exposure",
        "max_trade_risk_pct": "trade_stop_risk",
    }[other_limit]
    assert constraint in report["ledger"][0]["execution_details"]["constraints"]


def test_atr_stop_risk_sizing_remains_clipped_by_daily_buy_budget():
    current = rows(limit=50)
    current[0]["execution_policy"].update(
        exit_mode="atr",
        atr_period=14,
        atr_stop_multiple=2,
        atr_target_multiple=3,
        sizing="stop_risk",
        risk_budget_fraction=0.5,
    )
    current[0]["signals"][0]["execution_atr"] = atr_feature(value=2)
    report = native.evaluate(current[:1], snapshot(), 1000)
    filled = report["ledger"][0]
    assert filled["quantity"] == 5
    assert filled["execution_details"]["initial_stop"] == 96
    assert filled["execution_details"]["sizing"] == "stop_risk"
    assert "daily_buy_turnover" in filled["execution_details"]["constraints"]
    assert daily(report, DAYS[1])["buy_notional"] == 500


def test_budget_over_100_percent_allows_recycling_only_up_to_recorded_buy_limit():
    prices = snapshot(minute=True)
    current = []
    for index, clock in enumerate(("09:15", "09:16", "09:17")):
        current.append(
            controlled(
                str(index),
                100,
                execution=turnover_policy(150),
                signals=[signal("AAA", timestamp=clock)],
                stop_pct=20,
                target_pct=10,
                hold_sessions=3,
            )
        )
        key = prices["timeline"][index + 1]
        prices["bars"]["AAA"][key] = candle(100, 111, 99, 100)
    report = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == [10, 5, 0]
    assert daily(report, DAYS[0])["buy_budget"] == 1500
    assert daily(report, DAYS[0])["buy_notional"] == 1500
    assert daily(report, DAYS[0])["buy_turnover_pct"] == 150
    assert report["execution"]["activity"]["sell_notional"] == pytest.approx(1650)
    assert report["execution"]["activity"]["total_traded_notional"] == pytest.approx(3150)
    assert_reconciles(report)


def test_no_fills_leave_explicit_daily_zero_budget_consumption():
    current = rows(limit=0.01)
    report = native.evaluate(current, snapshot(), 1000)
    assert report["execution"]["activity"]["total_fills"] == 0
    assert len(report["execution"]["turnover"]["daily"]) == len(DAYS)
    for receipt in report["execution"]["turnover"]["daily"]:
        assert receipt["opening_equity"] == 1000
        assert receipt["buy_budget"] == pytest.approx(0.1)
        assert receipt["remaining_buy_budget"] == pytest.approx(0.1)
        assert receipt["buy_notional"] == receipt["buy_turnover_pct"] == receipt["buy_fills"] == 0


def test_nonfinite_daily_budget_is_rejected_before_any_new_orders():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(1e307)
    with pytest.raises(ValueError, match="finite opening buy budget"):
        native.evaluate(rows(limit=1000)[:1], prices, 1000)


def test_invalid_protection_consumes_no_budget_and_other_strategy_can_enter():
    current = rows()
    current[0]["execution_policy"].update(
        exit_mode="atr", atr_period=14, atr_stop_multiple=2, atr_target_multiple=3
    )
    current[0]["signals"][0]["execution_atr"] = atr_feature(value=100)
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [0, 5]
    assert daily(report, DAYS[1])["buy_notional"] == 500
    assert daily(report, DAYS[1])["buy_fills"] == 1


def test_native_rejected_order_does_not_consume_or_reserve_turnover(monkeypatch):
    from vectorbt.portfolio import nb

    original = nb.order_nb
    calls = []

    def reject_first(**kwargs):
        calls.append(kwargs)
        return original(**{**kwargs, "reject_prob": 1.0 if len(calls) == 1 else 0.0})

    monkeypatch.setattr(nb, "order_nb", reject_first)
    report = native.evaluate(rows(), snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [0, 5]
    assert "VectorBT did not fill" in report["ledger"][0]["reason"]
    assert daily(report, DAYS[1])["buy_fills"] == 1
    assert daily(report, DAYS[1])["buy_notional"] == 500


def test_full_legacy_v1_and_v2_native_report_hashes_remain_exact():
    cases = [
        (
            [strategy("a", 100)],
            snapshot(),
            "4fd83f134673813401dda09ac23345fdbd53e613bda248b50ea516386954f27a",
        ),
        (
            [controlled()],
            snapshot(),
            "ef024b88e90881bb6a554a3d963fcbf02035653092e6c012d5b8b30a95f4ee78",
        ),
        (
            competing("lower_stop_risk"),
            snapshot(),
            "e2093c3095ac35863d3da298e66d58eee84ad92262c2528eb2a5182cd5870a1e",
        ),
        (
            competing("reversed_source", minute=True, costs=(100, 200)),
            snapshot(minute=True),
            "321a7be7d52eb7147f30777f7dd7dadcf7f4e196dbcad24a74400ae2806b3e1e",
        ),
    ]
    for current, prices, expected in cases:
        report = native.evaluate(current, prices, 1000)
        assert fingerprint(report) == expected
        assert "turnover" not in report["execution"]


def test_100_native_turnover_accounts_release_handles_and_memory(record_property):
    import psutil

    current, prices = rows(priority="lower_stop_risk", minute=True), snapshot(minute=True)
    expected = native.evaluate(current, prices, 1000)
    for _ in range(5):
        assert native.evaluate(current, prices, 1000) == expected
    gc.collect()
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    before, rss = handles(), process.memory_info().rss
    started, samples = time.perf_counter(), []
    for index in range(100):
        actual = native.evaluate(current, prices, 1000)
        assert actual == expected
        del actual
        if (index + 1) % 25 == 0:
            gc.collect()
            samples.append(process.memory_info().rss)
    gc.collect()
    record_property("accounts", 100)
    record_property("seconds", time.perf_counter() - started)
    record_property("handles_before", before)
    record_property("handles_after", handles())
    record_property("rss_change_bytes", process.memory_info().rss - rss)
    record_property("rss_samples", samples)
    assert handles() <= before + 4
    assert samples[-1] - samples[1] < 16 * 1024**2
