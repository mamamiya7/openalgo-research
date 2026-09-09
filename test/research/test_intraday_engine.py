import copy
from datetime import date, timedelta

import pytest

from research.data import cutoff_snapshot, validate_snapshot
from research.engine import config_defaults, evaluate, validate_config
from research.experiments import run_search, run_sensitivity, validate_search, validate_variants
from research.intraday import POLICY_VERSION


def example(days=3):
    sessions = [(date(2026, 1, 5) + timedelta(days=i)).isoformat() for i in range(days)]
    timeline = [f"{day}T09:{minute}:00+05:30" for day in sessions for minute in range(15, 20)]
    bars = {t: {"open": 100, "high": 100.5, "low": 99.5, "close": 100} for t in timeline}
    snapshot = {
        "sessions": sessions,
        "timeline": timeline,
        "bars": {"AAA": bars},
        "session_hours": {day: {"open": "09:15", "close": "09:20"} for day in sessions},
        "provenance": {
            "provider": "synthetic-minute-test",
            "synthetic": True,
            "exchange": "NSE",
            "interval": "1m",
            "temporal_version": "minute-open-v1",
            "adjustment_basis": "synthetic",
            "calendar_basis": "explicit-test",
        },
    }
    signals = [{"date": sessions[0], "timestamp": timeline[0], "symbol": "AAA", "row": 2}]
    return signals, snapshot


def config(**changes):
    return {
        "cost_bps": 0,
        "order_size_pct": 100,
        "target_pct": 10,
        "stop_pct": 5,
        "trade_horizon": "intraday",
        **changes,
    }


def test_strictly_later_open_and_elapsed_minutes():
    signals, snapshot = example()
    snapshot["bars"]["AAA"][snapshot["timeline"][0]]["high"] = 500
    result = evaluate(signals, snapshot, config(hold_minutes=2))
    row = result["ledger"][0]
    assert row["entry_timestamp"] == snapshot["timeline"][1]
    assert row["exit_timestamp"] == snapshot["timeline"][3]
    assert row["exit_timing"] == "open" and row["outcome"] == "time"
    assert row["pnl"] == 0
    assert result["policy_version"] == POLICY_VERSION
    assert result["metric_basis"] == "minute_marked"


def test_target_before_later_stop_is_not_daily_stop_first():
    signals, snapshot = example()
    snapshot["bars"]["AAA"][snapshot["timeline"][1]]["high"] = 111
    snapshot["bars"]["AAA"][snapshot["timeline"][2]]["low"] = 90
    row = evaluate(signals, snapshot, config())["ledger"][0]
    assert row["outcome"] == "target" and row["pnl"] == 10000


def test_same_minute_ambiguity_stop_first():
    signals, snapshot = example()
    snapshot["bars"]["AAA"][snapshot["timeline"][1]].update(high=111, low=90)
    row = evaluate(signals, snapshot, config())["ledger"][0]
    assert row["outcome"] == "stop" and row["pnl"] == -5000


def test_trailing_next_minute_then_gap_open():
    signals, snapshot = example()
    snapshot["bars"]["AAA"][snapshot["timeline"][1]].update(high=108, low=99)
    snapshot["bars"]["AAA"][snapshot["timeline"][2]].update(open=102, high=103, low=101, close=102)
    row = evaluate(signals, snapshot, config(target_pct=50, trailing_pct=3, trailing_enabled=True))[
        "ledger"
    ][0]
    assert row["outcome"] == "trailing_stop" and row["exit_raw_price"] == 102
    assert row["exit_timestamp"] == snapshot["timeline"][2]


def test_intraday_flat_and_multiday_overnight_hold():
    signals, snapshot = example()
    intraday = evaluate(signals, snapshot, config())["ledger"][0]
    overnight = evaluate(signals, snapshot, config(trade_horizon="multiday", hold_sessions=1))[
        "ledger"
    ][0]
    assert intraday["exit_date"] == snapshot["sessions"][0]
    assert overnight["exit_date"] == snapshot["sessions"][1]
    assert overnight["exit_timing"] == "close"


def test_date_only_enters_next_exchange_session():
    signals, snapshot = example()
    signals[0].pop("timestamp")
    row = evaluate(signals, snapshot, config())["ledger"][0]
    assert row["entry_timestamp"] == snapshot["timeline"][5]


def test_entry_exit_clock_and_after_exit_exclusion():
    signals, snapshot = example()
    row = evaluate(signals, snapshot, config(entry_time="09:17", exit_time="09:18"))["ledger"][0]
    assert row["entry_timestamp"] == snapshot["timeline"][2]
    assert row["exit_timestamp"] == snapshot["timeline"][3]
    signals[0]["timestamp"] = snapshot["timeline"][3]
    assert (
        evaluate(signals, snapshot, config(exit_time="09:18"))["ledger"][0]["status"] == "excluded"
    )


@pytest.mark.parametrize("missing", [1, 2, 4])
def test_missing_entry_or_intervening_or_session_close_pending(missing):
    signals, snapshot = example()
    del snapshot["bars"]["AAA"][snapshot["timeline"][missing]]
    result = evaluate(signals, snapshot, config())
    assert result["ledger"][0]["status"] == "pending"
    assert result["summary"]["closed_trades"] == 0


def test_truncated_tail_not_forced_flat_and_missing_timeline_rejected():
    signals, snapshot = example(1)
    snapshot["timeline"] = snapshot["timeline"][:3]
    snapshot["bars"]["AAA"] = {t: snapshot["bars"]["AAA"][t] for t in snapshot["timeline"]}
    assert evaluate(signals, snapshot, config())["ledger"][0]["status"] == "pending"
    snapshot["timeline"].pop(1)
    with pytest.raises(ValueError, match="omit"):
        validate_snapshot(snapshot)


def test_future_same_day_signals_do_not_change_earlier_trigger():
    signals, snapshot = example(10)
    day = snapshot["sessions"][8]
    target = {"date": day, "timestamp": f"{day}T09:15:00+05:30", "symbol": "AAA", "row": 2}
    history = [
        {"date": d, "timestamp": f"{d}T09:15:00+05:30", "symbol": "AAA", "row": 10 + i}
        for d in snapshot["sessions"][:8]
        for i in range(6)
    ] + [target]
    baseline = evaluate(
        [target], snapshot, config(modes=["Bottom Fishing"]), signal_history=history
    )
    future = history + [
        {**target, "timestamp": f"{day}T09:19:00+05:30", "row": 100 + i} for i in range(50)
    ]
    changed = evaluate([target], snapshot, config(modes=["Bottom Fishing"]), signal_history=future)
    assert baseline["ledger"] == changed["ledger"]
    assert baseline["ledger"][0]["quantity"] > 0


def test_cutoff_preserves_cutoff_day_minutes_and_excludes_future():
    signals, snapshot = example()
    cut = cutoff_snapshot(snapshot, snapshot["sessions"][0])
    assert len(cut["timeline"]) == 5 and len(cut["bars"]["AAA"]) == 5
    expected = evaluate(signals, cut, config())
    future = copy.deepcopy(snapshot)
    for t, bar in future["bars"]["AAA"].items():
        if t[:10] > snapshot["sessions"][0]:
            bar.update(open=900, high=901, low=899, close=900)
    assert evaluate(signals, cutoff_snapshot(future, snapshot["sessions"][0]), config()) == expected


def test_optimizer_minute_hold_axis_and_sensitivity_share_evaluator():
    signals, snapshot = example()
    snapshot["bars"]["AAA"][snapshot["timeline"][3]].update(open=102, high=103, low=101, close=102)
    cfg = validate_config(config(hold_minutes=1))
    spec = validate_search(
        {"mode": "exhaustive", "axes": {"hold_minutes": {"min": 1, "max": 2, "step": 1}}}, cfg
    )
    report = run_search(signals, snapshot, cfg, spec)
    assert report["config"]["hold_minutes"] == 2
    variants = validate_variants([{"name": "Longer", "changes": {"hold_minutes": 2}}], cfg)
    sensitive = run_sensitivity(signals, snapshot, cfg, {"variants": variants})
    assert (
        sensitive["experiment"]["variants"][0]["report"]["summary"]
        == evaluate(signals, snapshot, {**cfg, "hold_minutes": 2})["summary"]
    )


def test_daily_default_config_identity_unchanged():
    assert validate_config({}) == config_defaults()
    assert "trade_horizon" not in validate_config({})


@pytest.mark.parametrize(
    "changes",
    [
        {"hold_minutes": 0},
        {"hold_minutes": 1.5},
        {"hold_minutes": float("inf")},
        {"entry_time": "9:15"},
        {"exit_time": "25:00"},
        {"trade_horizon": "daily"},
    ],
)
def test_invalid_timing_rejected(changes):
    with pytest.raises(ValueError):
        validate_config(changes)


def test_later_period_dispatch_and_future_cutoff_invariance():
    from research.experiments import run_research, validate_research

    _, snapshot = example(12)
    signals = [
        {"date": day, "timestamp": f"{day}T09:15:00+05:30", "symbol": "AAA", "row": i + 2}
        for i, day in enumerate(snapshot["sessions"])
    ]
    cfg = validate_config(config())
    spec = validate_research(
        {
            "train_end": snapshot["sessions"][4],
            "test_end": snapshot["sessions"][9],
            "gap_sessions": 1,
            "variants": [{"name": "Fee", "changes": {"cost_bps": 1}}],
        },
        cfg,
        {"signals": signals, "snapshot": snapshot},
    )
    result = run_research(signals, snapshot, cfg, spec)
    assert result["policy_version"] == POLICY_VERSION
    folds = result["experiment"]["folds"]
    assert folds[0]["test_report"]["metric_basis"] == "minute_marked"
    future = copy.deepcopy(snapshot)
    for time, bar in future["bars"]["AAA"].items():
        if time[:10] > snapshot["sessions"][9]:
            bar.update(open=500, high=501, low=499, close=500)
    changed = run_research(signals, future, cfg, spec)
    assert folds == changed["experiment"]["folds"]


def test_missing_first_eligible_grid_open_cannot_delay_fill():
    signals, snapshot = example(1)
    snapshot["timeline"] = snapshot["timeline"][2:]
    snapshot["bars"]["AAA"] = {t: snapshot["bars"]["AAA"][t] for t in snapshot["timeline"]}
    result = evaluate(signals, snapshot, config())
    assert result["ledger"][0]["quantity"] == 0
    assert result["ledger"][0]["entry_timestamp"].endswith("09:16:00+05:30")


def test_minute_core_fees_and_opening_exit_fund_following_order():
    signals, snapshot = example(1)
    signals.append(
        {
            "date": signals[0]["date"],
            "timestamp": snapshot["timeline"][1],
            "symbol": "AAA",
            "row": 3,
        }
    )
    result = evaluate(signals, snapshot, config(hold_minutes=1, cost_bps=10))
    first, second = result["ledger"]
    assert first["quantity"] == 999
    assert first["pnl"] == -199.8
    assert second["quantity"] == 997  # First position's opening time exit finances this order.
    assert first["exit_timestamp"] == second["entry_timestamp"]


def test_planner_and_entry_share_special_session_clock():
    from research.requirements import build_plan

    signals, snapshot = example(3)
    snapshot["session_hours"][snapshot["sessions"][1]] = {"open": "09:15", "close": "09:17"}
    signals[0].pop("timestamp")
    plan = build_plan(signals, {"config": config(entry_time="09:18")}, snapshot)
    required = plan["required_timestamps"]["AAA"]
    assert all(t[:10] == snapshot["sessions"][2] for t in required)


def test_planner_covers_search_and_later_variant_product():
    from research.requirements import build_plan

    signals, snapshot = example(8)
    request = {
        "kind": "research",
        "config": config(trade_horizon="multiday", hold_minutes=1, hold_sessions=1),
        "specification": {
            "search": {
                "mode": "exhaustive",
                "axes": {"hold_sessions": {"min": 1, "max": 5, "step": 1}},
            },
            "variants": [{"name": "No minute cap", "changes": {"hold_minutes": None}}],
        },
    }
    plan = build_plan(signals, request, snapshot)
    assert max(t[:10] for t in plan["required_timestamps"]["AAA"]) == snapshot["sessions"][5]


def test_minute_coverage_only_requires_planned_symbol_slots():
    signals, snapshot = example(3)
    required = snapshot["timeline"][:5]
    snapshot["required_timestamps"] = {"AAA": required}
    snapshot["bars"]["AAA"] = {t: snapshot["bars"]["AAA"][t] for t in required}
    assert validate_snapshot(snapshot, signals)["symbols"][0]["missing_sessions"] == []
    assert evaluate(signals, snapshot, config())["ledger"][0]["status"] == "closed"
