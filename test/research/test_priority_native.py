"""Real native admission order and actual-fill activity; legacy evidence retained."""

import gc
import time
from copy import deepcopy

import pytest
from test_execution_aware_native import atr_feature, controlled, policy
from test_vectorbt_portfolio import DAYS, assert_reconciles, candle, signal, snapshot, strategy

from research.connectors import vectorbt_portfolio as native
from research.execution_activity import build as activity
from research.execution_policy import PRIORITY_VERSION, VERSION, normalize_policy
from research.portfolio import fingerprint

pytestmark = pytest.mark.timeout(240)


def priority_policy(priority="source", **limits):
    return normalize_policy(
        {**policy(**limits), "version": PRIORITY_VERSION, "entry_priority": priority}
    )


def competing(priority="source", *, costs=(0, 0), stops=(10, 2), minute=False):
    execution = priority_policy(priority, max_open_positions=1)
    return [
        controlled(
            identity,
            100,
            execution=execution,
            signals=[signal(symbol, timestamp="09:15" if minute else None)],
            stop_pct=stop,
            cost_bps=cost,
            target_pct=500,
            hold_sessions=3,
            **({"trade_horizon": "intraday", "hold_minutes": 3} if minute else {}),
        )
        for identity, symbol, stop, cost in zip(
            ("a", "b"), ("AAA", "BBB"), stops, costs, strict=True
        )
    ]


def test_policy_priority_is_explicit_opt_in_and_old_policy_remains_exact():
    old = policy()
    assert normalize_policy(old) == old
    assert normalize_policy({"version": PRIORITY_VERSION})["entry_priority"] == "source"
    for priority in ("source", "reversed_source", "lower_stop_risk"):
        assert priority_policy(priority)["entry_priority"] == priority
    for value in (
        {**old, "entry_priority": "source"},
        {**old, "version": PRIORITY_VERSION, "entry_priority": "future_return"},
        {**old, "version": PRIORITY_VERSION, "priority_seed": 1},
        {**old, "version": "unregistered"},
    ):
        with pytest.raises(ValueError):
            normalize_policy(value)


@pytest.mark.parametrize("mismatch", ["version", "priority"])
def test_mixed_account_policy_versions_or_priorities_are_rejected(mismatch):
    rows = competing()
    rows[1]["execution_policy"] = (
        policy(max_open_positions=1)
        if mismatch == "version"
        else priority_policy("reversed_source", max_open_positions=1)
    )
    with pytest.raises(ValueError, match="same execution policy version and entry priority"):
        native.evaluate(rows, snapshot(), 1000)


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize(
    "priority,quantities,ranks",
    [
        ("source", [10, 0], [1, 2]),
        ("reversed_source", [0, 10], [2, 1]),
        ("lower_stop_risk", [0, 10], [2, 1]),
    ],
)
def test_actual_priority_changes_only_same_opening_competition(minute, priority, quantities, ranks):
    rows, prices = competing(priority, minute=minute), snapshot(minute=minute)
    before = deepcopy((rows, prices))
    report = native.evaluate(rows, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == quantities
    assert [row["strategy_id"] for row in report["ledger"]] == ["a", "b"]
    assert [row["execution_details"]["entry_rank"] for row in report["ledger"]] == ranks
    assert all(row["execution_details"]["entry_batch_size"] == 2 for row in report["ledger"])
    assert report["execution"]["entry_priority"] == priority
    assert report["execution"]["adapter_version"] == native.PRIORITY_ADAPTER_VERSION
    assert report["policy_version"] == native.PRIORITY_POLICY_VERSION
    assert (rows, prices) == before
    assert_reconciles(report)


@pytest.mark.parametrize("costs,winner", [((0, 0), "b"), ((0, 500), "a")])
def test_risk_priority_includes_both_fee_sides_not_just_stop_distance(costs, winner):
    report = native.evaluate(
        competing("lower_stop_risk", costs=costs, stops=(5, 2)), snapshot(), 1000
    )
    entered = [row for row in report["ledger"] if row["quantity"]]
    assert len(entered) == 1 and entered[0]["strategy_id"] == winner
    for row in report["ledger"]:
        cost = costs[0 if row["strategy_id"] == "a" else 1] / 10000
        stop = 0.05 if row["strategy_id"] == "a" else 0.02
        expected = (100 * (1 + cost) - 100 * (1 - stop) * (1 - cost)) / (100 * (1 + cost))
        assert row["execution_details"]["stop_risk_fraction"] == pytest.approx(expected)


def test_risk_priority_includes_slippage_and_ties_keep_source_index():
    rows = competing("lower_stop_risk", stops=(2, 2))
    equal = native.evaluate(rows, snapshot(), 1000)
    assert [row["quantity"] for row in equal["ledger"]] == [10, 0]
    rows[0]["config"]["slippage_bps"] = 100
    changed = native.evaluate(rows, snapshot(), 1000)
    assert [row["quantity"] for row in changed["ledger"]] == [0, 10]
    assert changed["ledger"][0]["execution_details"]["stop_risk_fraction"] > 0.02


def test_priority_does_not_read_entry_bar_future_high_low_or_close():
    rows = competing("lower_stop_risk")
    first = native.evaluate(rows, snapshot(), 1000)
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 200, 1, 150)
    prices["bars"]["BBB"][DAYS[1]] = candle(100, 300, 1, 250)
    changed = native.evaluate(rows, prices, 1000)
    assert [row["quantity"] for row in changed["ledger"]] == [
        row["quantity"] for row in first["ledger"]
    ]
    for old, new in zip(first["ledger"], changed["ledger"], strict=True):
        assert {
            key: old["execution_details"][key]
            for key in ("entry_rank", "stop_risk_fraction", "entry_batch_size")
        } == {
            key: new["execution_details"][key]
            for key in ("entry_rank", "stop_risk_fraction", "entry_batch_size")
        }


def test_invalid_protection_is_recorded_without_aborting_other_entries():
    rows = competing("lower_stop_risk")
    rows[0]["execution_policy"] = deepcopy(rows[0]["execution_policy"])
    rows[0]["execution_policy"].update(
        exit_mode="atr", atr_period=14, atr_stop_multiple=2, atr_target_multiple=3
    )
    rows[0]["signals"][0]["execution_atr"] = atr_feature(value=100)
    report = native.evaluate(rows, snapshot(), 1000)
    bad, good = report["ledger"]
    assert bad["status"] == "skipped" and "positive stop" in bad["reason"]
    assert bad["execution_details"]["stop_risk_fraction"] is None
    assert bad["execution_details"]["entry_rank"] == 2
    assert good["quantity"] == 10


def test_atr_priority_uses_frozen_prior_feature_and_actual_slipped_fill_anchor():
    rows = competing("lower_stop_risk", stops=(10, 5))
    rows[0]["execution_policy"] = deepcopy(rows[0]["execution_policy"])
    rows[0]["execution_policy"].update(
        exit_mode="atr", atr_period=14, atr_stop_multiple=2, atr_target_multiple=3
    )
    rows[0]["signals"][0]["execution_atr"] = atr_feature(value=2)
    rows[0]["config"]["slippage_bps"] = 100
    report = native.evaluate(rows, snapshot(), 1000)
    first = report["ledger"][0]
    assert first["quantity"] > 0
    assert first["execution_details"]["stop_risk_fraction"] == pytest.approx(
        (101 - 97 * 0.99) / 101
    )


@pytest.mark.parametrize("opening,expected", [(100, 0), (111, 11)])
def test_priority_keeps_opening_exits_before_entries_and_later_proceeds_unavailable(
    opening, expected
):
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(opening, 111, 99, 105)
    execution = priority_policy("lower_stop_risk", max_open_positions=1)
    rows = [
        controlled("a", 100, execution=execution, stop_pct=20, target_pct=10, hold_sessions=2),
        controlled(
            "b",
            100,
            execution=execution,
            signals=[signal("BBB", day=1)],
            stop_pct=2,
            target_pct=500,
        ),
    ]
    report = native.evaluate(rows, prices, 1000)
    assert report["ledger"][1]["quantity"] == expected
    assert report["ledger"][0]["exit_timing"] == ("open" if opening == 111 else "intraday")
    assert_reconciles(report)


def test_priority_keeps_same_bar_exits_after_all_entry_attempts():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 115, 99, 110)
    rows = competing("source", stops=(20, 20))
    rows[0]["config"]["target_pct"] = 10
    report = native.evaluate(rows, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == [10, 0]
    assert report["ledger"][0]["exit_date"] == DAYS[1]
    measured = report["execution"]["activity"]
    assert measured["buy_notional"] == 1000
    assert measured["sell_notional"] == pytest.approx(1100)
    assert measured["total_traded_notional"] == pytest.approx(2100)
    assert measured["traded_notional_to_starting_capital_pct"] == pytest.approx(210)
    assert measured["total_fills"] == 2
    assert len(measured["daily"]) == 1


def test_activity_uses_actual_slipped_fills_fees_open_positions_and_rejections():
    rows = competing("source")
    rows[0]["config"].update(cost_bps=100, slippage_bps=100)
    report = native.evaluate(rows, snapshot(), 1000)
    measured, entered = report["execution"]["activity"], report["ledger"][0]
    assert entered["status"] == "pending" and entered["quantity"] == 9
    assert measured["buy_notional"] == pytest.approx(9 * 101)
    assert measured["sell_notional"] == 0
    assert measured["fees"] == pytest.approx(9 * 101 * 0.01)
    assert measured["buy_fills"] == measured["total_fills"] == 1
    assert measured["sell_fills"] == 0
    assert measured["total_traded_notional"] == sum(
        order["size"] * order["price"] for order in report["engine_records"]["orders"]
    )


def test_same_symbol_lots_keep_distinct_source_and_admission_ranks():
    execution = priority_policy("reversed_source", max_open_positions=1)
    rows = [
        controlled(
            "a", execution=execution, signals=[signal(row=2), signal(row=3)], order_size_pct=50
        )
    ]
    report = native.evaluate(rows, snapshot(), 1000)
    assert [row["source_row"] for row in report["ledger"]] == [2, 3]
    assert [row["quantity"] for row in report["ledger"]] == [0, 5]
    assert [row["execution_details"]["entry_rank"] for row in report["ledger"]] == [2, 1]


@pytest.mark.parametrize("priority", ["source", "reversed_source", "lower_stop_risk"])
def test_each_priority_still_clips_to_hard_exposure_and_risk_limits(priority):
    rows = competing(priority)
    for row in rows:
        row["execution_policy"] = priority_policy(
            priority,
            max_open_positions=1,
            max_gross_exposure_pct=60,
            max_symbol_exposure_pct=50,
            max_trade_risk_pct=1,
        )
    report = native.evaluate(rows, snapshot(), 1000)
    entered = [row for row in report["ledger"] if row["quantity"]]
    assert len(entered) == 1
    expected = 1 if priority == "source" else 5
    assert entered[0]["quantity"] == expected
    assert entered[0]["execution_details"]["initial_stop_risk"] <= 10 + 1e-8
    assert report["equity_curve"][1]["cash"] >= 500
    assert_reconciles(report)


@pytest.mark.parametrize("minute", [False, True])
def test_daily_activity_groups_actual_fill_dates_across_multiple_sessions(minute):
    prices = snapshot(minute=minute)
    execution = priority_policy()
    signals = [
        signal(day=0, timestamp="09:15" if minute else None),
        signal("BBB", day=1, row=3, timestamp="09:15" if minute else None),
    ]
    row = controlled(
        execution=execution,
        signals=signals,
        order_size_pct=40,
        stop_pct=20,
        target_pct=500,
        hold_sessions=1,
        **({"trade_horizon": "intraday", "hold_minutes": 1} if minute else {}),
    )
    report = native.evaluate([row], prices, 1000)
    measured = report["execution"]["activity"]
    expected = {}
    keys = prices["timeline"] if minute else prices["sessions"]
    for order in report["engine_records"]["orders"]:
        day = keys[order["idx"]][:10]
        expected[day] = expected.get(day, 0) + order["size"] * order["price"]
    assert {row["date"]: row["total_traded_notional"] for row in measured["daily"]} == expected
    assert measured["total_fills"] == 4
    assert sum(row["total_fills"] for row in measured["daily"]) == 4


@pytest.mark.parametrize("mutation", ["duplicate", "bad_side", "bad_index", "bad_fee", "bad_size"])
def test_activity_rejects_malformed_native_order_evidence(mutation):
    orders = [{"id": 0, "idx": 1, "side": 0, "size": 2.0, "price": 100.0, "fees": 1.0}]
    if mutation == "duplicate":
        orders *= 2
    else:
        key, value = {
            "bad_side": ("side", 2),
            "bad_index": ("idx", 100),
            "bad_fee": ("fees", float("nan")),
            "bad_size": ("size", -1),
        }[mutation]
        orders[0][key] = value
    with pytest.raises(ValueError, match="invalid or duplicate"):
        activity(orders, DAYS, 1000)


def test_no_fills_activity_is_explicit_zero_not_invented_daily_observation():
    result = activity([], DAYS, 1000)
    assert result["daily"] == [] and result["total_fills"] == 0
    assert result["traded_notional_to_starting_capital_pct"] == 0


def test_legacy_whole_native_report_and_v10_financial_evidence_stay_exact():
    legacy = native.evaluate([strategy("a", 100)], snapshot(), 1000)
    assert fingerprint(legacy) == "4fd83f134673813401dda09ac23345fdbd53e613bda248b50ea516386954f27a"
    old = native.evaluate([controlled()], snapshot(), 1000)
    new = native.evaluate([controlled(execution=priority_policy())], snapshot(), 1000)
    assert old["strategies"][0]["execution_policy"]["version"] == VERSION
    assert "activity" not in old["execution"]
    for key in ("summary", "equity_curve", "per_strategy", "engine_records", "analysis"):
        assert new[key] == old[key]
    for row in old["ledger"]:
        assert "entry_priority" not in row["execution_details"]


def test_100_native_priority_accounts_release_handles_and_memory(record_property):
    import psutil

    rows, prices = competing("lower_stop_risk"), snapshot()
    expected = native.evaluate(rows, prices, 1000)
    for _ in range(5):
        assert native.evaluate(rows, prices, 1000) == expected
    gc.collect()
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    before, rss = handles(), process.memory_info().rss
    started, samples = time.perf_counter(), []
    for index in range(100):
        actual = native.evaluate(rows, prices, 1000)
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
