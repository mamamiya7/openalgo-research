"""Real VectorBT fills under opt-in ATR protection and marked risk controls."""

import gc
import time
import tracemalloc
from copy import deepcopy

import pytest
from test_vectorbt_portfolio import DAYS, assert_reconciles, candle, signal, snapshot, strategy

from research.connectors import vectorbt_portfolio as native
from research.execution_features import ENTRY_VERSION, _seal
from research.execution_policy import VERSION, normalize_limits, normalize_policy
from research.portfolio import fingerprint

pytestmark = pytest.mark.timeout(240)


def policy(*, atr=False, sizing="allocation", **limits):
    raw = {
        "version": VERSION,
        "exit_mode": "atr" if atr else "percent",
        "sizing": sizing,
        "risk_limits": normalize_limits(
            {
                "max_gross_exposure_pct": 100,
                "max_symbol_exposure_pct": 100,
                "max_open_positions": 10,
                "max_trade_risk_pct": 100,
                "max_total_stop_risk_pct": 100,
                **limits,
            }
        ),
    }
    if atr:
        raw.update(atr_period=14, atr_stop_multiple=2, atr_target_multiple=3)
    return normalize_policy(raw)


def atr_feature(*, symbol="AAA", entry_session=None, value=2.0):
    return _seal(
        {
            "version": ENTRY_VERSION,
            "symbol": symbol,
            "entry_session": entry_session or DAYS[1],
            "atr_period": 14,
            "value": value,
            "observations": 100,
            "from": "2025-08-01",
            "to": DAYS[0],
            "available_at": DAYS[0] + "T15:30:00+05:30",
            "basis_id": "a" * 64,
            "recipe_id": "b" * 64,
        }
    )


def controlled(identity="a", allocation=100, *, execution=None, signals=None, **cfg):
    row = strategy(identity, allocation, signals=signals, **cfg)
    row["execution_policy"] = execution or policy()
    return row


def test_policy_defaults_are_explicit_bounded_and_modes_are_exact():
    assert normalize_limits()["max_symbol_exposure_pct"] == 25
    assert normalize_limits()["max_trade_risk_pct"] == 1
    assert normalize_policy()["exit_mode"] == "percent"
    assert normalize_policy({"exit_mode": "atr"})["atr_stop_multiple"] == 2
    assert normalize_policy({"sizing": "stop_risk"})["risk_budget_fraction"] == 0.5
    for value in (
        {"max_trade_risk_pct": 0},
        {"max_open_positions": True},
        {"max_gross_exposure_pct": 101},
        {"extra": 1},
    ):
        with pytest.raises(ValueError):
            normalize_limits(value)
    for value in (
        {"exit_mode": "percent", "atr_period": 14},
        {"exit_mode": "atr", "atr_period": 10},
        {"exit_mode": "atr", "atr_stop_multiple": 0.5},
        {"sizing": "kelly"},
        {"sizing": "stop_risk", "risk_budget_fraction": 0},
        {"sizing": "stop_risk", "risk_budget_fraction": 1.1},
        {"sizing": "allocation", "risk_budget_fraction": 0.5},
    ):
        with pytest.raises(ValueError):
            normalize_policy(value)


def test_legacy_report_is_still_exact_and_v2_requires_all_rows_same_limits():
    report = native.evaluate([strategy("a", 100)], snapshot(), 1000)
    assert report["execution"]["adapter_version"] == native.ADAPTER_VERSION
    assert "execution_policy" not in report["strategies"][0]
    assert "execution_details" not in report["ledger"][0]
    # Actual previous adapter's entire report, including native records and
    # analysis; opt-in controls must not change any existing output bytes.
    assert fingerprint(report) == "4fd83f134673813401dda09ac23345fdbd53e613bda248b50ea516386954f27a"
    with pytest.raises(ValueError, match="every strategy"):
        native.evaluate([controlled("a", 50), strategy("b", 50)], snapshot(), 1000)
    with pytest.raises(ValueError, match="same risk limits"):
        native.evaluate(
            [controlled("a", 50), controlled("b", 50, execution=policy(max_open_positions=2))],
            snapshot(),
            1000,
        )


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize("low,outcome,exit_raw", [(96, "stop", 97), (99, "target", 107)])
def test_atr_anchors_actual_fill_stop_first_and_fees_slippage_reconcile(
    minute, low, outcome, exit_raw
):
    prices = snapshot(minute=minute)
    signals = [signal(timestamp="09:15")] if minute else [signal()]
    row = controlled(
        execution=policy(atr=True),
        signals=signals,
        cost_bps=100,
        slippage_bps=100,
        **({"trade_horizon": "intraday", "hold_minutes": 2} if minute else {}),
    )
    entry = prices["timeline"][1] if minute else DAYS[1]
    row["signals"][0]["execution_atr"] = atr_feature(entry_session=entry[:10])
    if minute:
        # The source feature must be strictly earlier than this entry date.
        row["signals"][0]["execution_atr"] = _seal(
            {
                **{
                    key: value
                    for key, value in row["signals"][0]["execution_atr"].items()
                    if key != "id"
                },
                "to": "2026-01-02",
                "available_at": "2026-01-02T15:30:00+05:30",
            }
        )
    prices["bars"]["AAA"][entry] = candle(100, 108, low, 100)
    report = native.evaluate([row], prices, 1000)
    trade = report["ledger"][0]
    assert trade["quantity"] == 9
    assert trade["entry_price"] == pytest.approx(101)
    assert trade["exit_raw_price"] == pytest.approx(exit_raw)
    assert trade["exit_price"] == pytest.approx(exit_raw * 0.99)
    assert trade["outcome"] == outcome
    details = trade["execution_details"]
    assert details["initial_stop"] == pytest.approx(97)
    assert details["initial_target"] == pytest.approx(107)
    assert details["initial_risk_per_share"] == pytest.approx(101 * 1.01 - 97 * 0.99 * 0.99)
    assert trade["pnl"] == pytest.approx(9 * (exit_raw * 0.99 * 0.99 - 101 * 1.01))
    if outcome == "stop":
        assert trade["pnl"] == pytest.approx(-details["initial_stop_risk"])
    assert report["policy_version"] == native.EXECUTION_POLICY_VERSION
    assert report["execution"]["adapter_version"] == native.EXECUTION_ADAPTER_VERSION
    assert report["strategies"][0]["execution_policy"] == row["execution_policy"]
    assert_reconciles(report)


def test_atr_gap_fill_can_exceed_planned_stop_risk_without_fabricated_fill():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(90, 92, 88, 91)
    row = controlled(execution=policy(atr=True), hold_sessions=3)
    row["signals"][0]["execution_atr"] = atr_feature()
    report = native.evaluate([row], prices, 1000)
    trade = report["ledger"][0]
    assert trade["exit_raw_price"] == 90 and trade["exit_timing"] == "open"
    assert trade["pnl"] < -trade["execution_details"]["initial_stop_risk"]
    assert_reconciles(report)


def test_atr_trailing_uses_only_previously_completed_bar_high():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 110, 99, 108)
    prices["bars"]["AAA"][DAYS[2]] = candle(105, 106, 98, 100)
    execution = policy(atr=True)
    execution["atr_target_multiple"] = 6.0
    row = controlled(execution=execution, hold_sessions=3, trailing_enabled=True, trailing_pct=10)
    row["signals"][0]["execution_atr"] = atr_feature()
    report = native.evaluate([row], prices, 1000)
    trade = report["ledger"][0]
    assert trade["exit_date"] == DAYS[2]
    assert trade["outcome"] == "trailing_stop" and trade["exit_raw_price"] == pytest.approx(99)
    assert trade["execution_details"]["initial_stop"] == 96
    assert_reconciles(report)


@pytest.mark.parametrize("problem", ["missing", "zero", "negative_stop", "future", "edited"])
def test_invalid_atr_and_nonpositive_protection_reject_the_entry(problem):
    row = controlled(execution=policy(atr=True))
    if problem != "missing":
        row["signals"][0]["execution_atr"] = atr_feature(
            value=0 if problem == "zero" else 100 if problem == "negative_stop" else 2
        )
    if problem == "future":
        feature = row["signals"][0]["execution_atr"]
        feature["available_at"] = DAYS[2] + "T15:30:00+05:30"
        feature["id"] = fingerprint({key: value for key, value in feature.items() if key != "id"})
    if problem == "edited":
        row["signals"][0]["execution_atr"]["value"] += 1
    report = native.evaluate([row], snapshot(), 1000)
    trade = report["ledger"][0]
    assert trade["quantity"] == 0 and trade["status"] == "skipped"
    assert "ATR" in trade["reason"] or "positive stop" in trade["reason"]


def test_same_symbol_exposure_aggregates_independent_lots_across_strategies():
    execution = policy(max_symbol_exposure_pct=25)
    report = native.evaluate(
        [controlled("a", 50, execution=execution), controlled("b", 50, execution=execution)],
        snapshot(),
        10000,
    )
    assert [row["quantity"] for row in report["ledger"]] == [25, 0]
    assert "symbol_exposure" in report["ledger"][1]["execution_details"]["constraints"]
    assert report["ledger"][0]["execution_details"]["clipped"] is True
    assert_reconciles(report)


def test_gross_exposure_and_position_count_are_hard_admission_caps():
    for execution, expected_constraint in [
        (policy(max_gross_exposure_pct=60), "gross_exposure"),
        (policy(max_open_positions=1), "open_positions"),
    ]:
        report = native.evaluate(
            [
                controlled("a", execution=execution),
                controlled("b", execution=execution, signals=[signal("BBB")]),
            ],
            snapshot(),
            10000,
        )
        assert report["ledger"][1]["quantity"] == 0
        assert expected_constraint in report["ledger"][1]["execution_details"]["constraints"]
        assert_reconciles(report)


@pytest.mark.parametrize("opening,new_quantity", [(100, 0), (111, 6)])
def test_only_opening_exits_release_cash_and_open_position_capacity(opening, new_quantity):
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(opening, 111, 99, 105)
    execution = policy(max_open_positions=1)
    report = native.evaluate(
        [
            controlled("a", 60, execution=execution, stop_pct=20, target_pct=10, hold_sessions=2),
            controlled("b", 60, execution=execution, signals=[signal("BBB", day=1)]),
        ],
        prices,
        1000,
    )
    assert report["ledger"][1]["quantity"] == new_quantity
    assert report["ledger"][0]["exit_timing"] == ("open" if opening == 111 else "intraday")
    assert_reconciles(report)


def test_per_trade_risk_and_total_stop_risk_clip_at_equality():
    single = controlled(execution=policy(max_trade_risk_pct=1), stop_pct=5)
    report = native.evaluate([single], snapshot(), 10000)
    assert report["ledger"][0]["quantity"] == 20
    assert report["ledger"][0]["execution_details"]["initial_stop_risk"] == pytest.approx(100)
    execution = policy(max_trade_risk_pct=3, max_total_stop_risk_pct=5)
    rows = [
        controlled(
            name,
            execution=execution,
            signals=[signal(symbol)],
            stop_pct=20,
            target_pct=500,
            hold_sessions=3,
        )
        for name, symbol in (("a", "AAA"), ("b", "BBB"), ("c", "CCC"))
    ]
    combined = native.evaluate(rows, snapshot(("AAA", "BBB", "CCC")), 10000)
    assert [row["quantity"] for row in combined["ledger"]] == [15, 10, 0]
    assert "total_stop_risk" in combined["ledger"][2]["execution_details"]["constraints"]
    assert_reconciles(combined)


def test_risk_loss_includes_both_fee_slippage_sides_and_post_fee_equity():
    row = controlled(
        execution=policy(max_trade_risk_pct=1), cost_bps=100, slippage_bps=100, stop_pct=5
    )
    report = native.evaluate([row], snapshot(), 10000)
    trade = report["ledger"][0]
    stop = 101 * 0.95
    loss = 101 * 1.01 - stop * 0.99 * 0.99
    expected = int(10000 * 0.01 / (loss + 0.01 * (101 * 1.01 - 100)))
    assert trade["quantity"] == expected
    assert trade["execution_details"]["initial_risk_per_share"] == pytest.approx(loss)
    assert_reconciles(report)


@pytest.mark.parametrize("same_symbol,cost_bps", [(False, 0), (True, 0), (False, 100), (True, 100)])
def test_simultaneous_slipped_fills_use_one_consistent_marked_equity_for_hard_caps(
    same_symbol, cost_bps
):
    execution = policy(
        **({"max_symbol_exposure_pct": 80} if same_symbol else {"max_gross_exposure_pct": 80})
    )
    row = controlled(
        execution=execution,
        signals=[signal("AAA", row=2), signal("AAA" if same_symbol else "BBB", row=3)],
        order_size_pct=60,
        slippage_bps=500,
        cost_bps=cost_bps,
        stop_pct=10,
        target_pct=50,
        hold_sessions=3,
    )
    report = native.evaluate([row], snapshot(), 10000)
    first, second = report["ledger"]
    assert first["quantity"] > 0 and second["quantity"] > 0
    # No close moves, stops or targets: the first entry-day native mark is
    # exactly cash plus all lots at their observed100 opening.
    point = report["equity_curve"][1]
    gross = sum(trade["quantity"] for trade in report["ledger"]) * 100
    assert gross <= point["equity"] * 0.80 + 1e-8
    if not cost_bps:
        assert [first["quantity"], second["quantity"]] == [57, 19]
    first_cash = first["quantity"] * first["entry_price"] * (1 + cost_bps / 10000)
    assert second["execution_details"]["opening_equity"] == pytest.approx(
        10000 - first_cash + first["quantity"] * 100
    )
    assert_reconciles(report)


@pytest.mark.parametrize("sizing", ["allocation", "stop_risk"])
def test_multiple_costly_fills_recheck_trade_and_total_stop_risk_on_marked_equity(sizing):
    execution = policy(sizing=sizing, max_trade_risk_pct=2, max_total_stop_risk_pct=5)
    row = controlled(
        execution=execution,
        signals=[
            signal(symbol, row=index + 2) for index, symbol in enumerate(("AAA", "BBB", "CCC"))
        ],
        order_size_pct=20,
        slippage_bps=500,
        cost_bps=100,
        stop_pct=10,
        target_pct=50,
        hold_sessions=3,
    )
    report = native.evaluate([row], snapshot(("AAA", "BBB", "CCC")), 10000)
    fee, slip = 0.01, 0.05
    cash, gross = 10000.0, 0.0
    for trade in report["ledger"]:
        details, quantity = trade["execution_details"], trade["quantity"]
        assert details["opening_equity"] == pytest.approx(cash + gross)
        cash -= quantity * 100 * (1 + slip) * (1 + fee)
        gross += quantity * 100
        marked = cash + gross
        assert details["initial_stop_risk"] <= marked * 0.02 + 1e-8
        assert (
            details["total_stop_risk_before"] + details["initial_stop_risk"] <= marked * 0.05 + 1e-8
        )
        if sizing == "stop_risk":
            assert details["initial_stop_risk"] <= marked * 0.01 + 1e-8
    assert_reconciles(report)


@pytest.mark.parametrize("sizing", ["allocation", "stop_risk"])
def test_requested_cash_budget_remains_a_ceiling_for_both_sizing_modes(sizing):
    row = controlled(execution=policy(sizing=sizing), order_size_pct=10, stop_pct=1)
    report = native.evaluate([row], snapshot(), 10000)
    assert report["ledger"][0]["quantity"] == 10
    assert report["ledger"][0]["requested_budget"] == 1000


@pytest.mark.parametrize("cost_bps,slippage_bps", [(0, 0), (100, 100)])
def test_stop_risk_has_an_explicit_target_and_changes_actual_quantity_and_costs(
    cost_bps, slippage_bps
):
    allocation = controlled(
        execution=policy(max_trade_risk_pct=1),
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
        stop_pct=5,
    )
    sized = deepcopy(allocation)
    sized["execution_policy"] = policy(sizing="stop_risk", max_trade_risk_pct=1)
    original = native.evaluate([allocation], snapshot(), 10000)
    target = native.evaluate([sized], snapshot(), 10000)
    first, second = original["ledger"][0], target["ledger"][0]
    assert second["requested_budget"] == first["requested_budget"] == 10000
    assert 0 < second["quantity"] < first["quantity"]
    assert second["execution_details"]["target_stop_risk_pct"] == 0.5
    assert second["execution_details"]["risk_budget_fraction"] == 0.5
    assert "risk_sizing_target" in second["execution_details"]["constraints"]
    if cost_bps:
        assert 0 < second["fees"] < first["fees"]
        assert target["summary"]["net_pnl"] > original["summary"]["net_pnl"]
    else:
        assert [first["quantity"], second["quantity"]] == [20, 10]
    assert_reconciles(original)
    assert_reconciles(target)
    sized["execution_policy"]["risk_budget_fraction"] = 1.0
    boundary = native.evaluate([sized], snapshot(), 10000)
    assert boundary["ledger"][0]["quantity"] == first["quantity"]


def test_appreciation_above_a_cap_prevents_new_entries_without_forced_sales():
    prices = snapshot()
    for day in DAYS[2:]:
        prices["bars"]["AAA"][day] = candle(200, 201, 199, 200)
    execution = policy(max_gross_exposure_pct=50)
    report = native.evaluate(
        [
            controlled(
                "a",
                execution=execution,
                order_size_pct=50,
                target_pct=500,
                stop_pct=99,
                hold_sessions=3,
            ),
            controlled("b", execution=execution, signals=[signal("BBB", day=1)]),
        ],
        prices,
        10000,
    )
    assert report["ledger"][0]["quantity"] == 50
    assert report["ledger"][0]["status"] == "pending"
    assert report["ledger"][1]["quantity"] == 0
    assert "gross_exposure" in report["ledger"][1]["execution_details"]["constraints"]
    assert_reconciles(report)


def test_repeated_native_calculation_reproduces_exact_financial_and_risk_evidence():
    prices = snapshot()
    row = controlled(execution=policy(max_trade_risk_pct=1), target_pct=20, stop_pct=5)
    before = deepcopy(row)
    first = native.evaluate([row], prices, 10000)
    second = native.evaluate([row], prices, 10000)
    assert second == first
    assert row == before


def test_100_actual_native_accounts_release_callbacks_arrays_and_handles(record_property):
    import psutil

    prices = snapshot()
    row = controlled(execution=policy(sizing="stop_risk", max_trade_risk_pct=1))
    expected = native.evaluate([row], prices, 10000)
    assert native.evaluate([row], prices, 10000) == expected
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    gc.collect()
    before, rss = handles(), process.memory_info().rss
    started = time.perf_counter()
    samples = []
    # Process RSS includes tracemalloc's own index. Measure the actual repeated
    # workload without that profiler first, then trace a separate small batch
    # to identify retained Python objects without confusing profiler overhead
    # with native callback ownership.
    for index in range(100):
        actual = native.evaluate([row], prices, 10000)
        assert actual == expected
        del actual
        if (index + 1) % 25 == 0:
            samples.append(process.memory_info().rss)
    gc.collect()
    after, rss_after = handles(), process.memory_info().rss
    record_property("accounts", 100)
    record_property("seconds", time.perf_counter() - started)
    record_property("handles_before", before)
    record_property("handles_after", after)
    record_property("rss_change_bytes", rss_after - rss)
    record_property("rss_batch_samples", samples)
    assert after <= before + 4
    assert rss_after - rss < 32 * 1024**2
    assert samples[-1] - samples[1] < 16 * 1024**2
    tracemalloc.start()
    try:
        baseline, _ = tracemalloc.get_traced_memory()
        for _ in range(10):
            actual = native.evaluate([row], prices, 10000)
            assert actual == expected
            del actual
        gc.collect()
        retained, peak = tracemalloc.get_traced_memory()
        record_property("traced_accounts", 10)
        record_property("retained_allocation_bytes", retained - baseline)
        record_property("peak_allocation_bytes", peak - baseline)
        assert retained - baseline < 2 * 1024**2
        assert peak - baseline < 32 * 1024**2
    finally:
        tracemalloc.stop()
