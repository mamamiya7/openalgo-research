"""CSV and execution timing determine bounded native price requirements."""

import copy
from datetime import date, timedelta

import pytest

from research import requirements
from research.engine import POLICY_VERSION, evaluate
from research.requirements import build_plan, covers_plan, select_interval
from research.signals import normalize_csv


@pytest.fixture
def reference():
    cursor = date(2026, 1, 5)
    sessions = []
    while len(sessions) < 15:
        if cursor.weekday() < 5:
            sessions.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return {"sessions": sessions}


@pytest.fixture
def signals():
    return normalize_csv(b"Date,Symbol\n2026-01-05,AAA\n")["signals"]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"trade_horizon": "multiday"},
        {"trade_horizon": "multiday", "hold_sessions": 10},
        {"stop_pct": 5, "target_pct": 10, "hold_sessions": 5},
        {"trailing_enabled": True, "trailing_pct": 2},
        {"trailing_enabled": True, "trailing_pct": 0},
        {"entry_time": None, "exit_time": None, "hold_minutes": None},
    ],
)
def test_date_only_defaults_and_daily_risk_rules_select_eod(signals, reference, config):
    request = {"config": config}
    assert select_interval(signals, request) == "D"
    plan = build_plan(signals, request, reference)
    assert plan["interval"] == "D"
    assert "timeline" not in plan
    assert "required_timestamps" not in plan


@pytest.mark.parametrize(
    "raw",
    [
        b"Timestamp,Symbol\n2026-01-05T09:20:35+05:30,AAA\n",
        b"Date,Time,Symbol\n2026-01-05,10:30,AAA\n",
        b"Date,Symbol\n2026-01-05 14:00,AAA\n",
        b"Date,Symbol\n2026-01-05,AAA\n2026-01-05 14:00,BBB\n",
    ],
)
def test_actual_csv_timestamps_select_minutes(raw, reference):
    signals = normalize_csv(raw)["signals"]
    request = {"config": {"trade_horizon": "multiday"}}
    assert select_interval(signals, request) == "1m"
    plan = build_plan(signals, request, reference)
    assert plan["interval"] == "1m"
    assert plan["reasons"] == ["timestamped_signals"]


@pytest.mark.parametrize(
    "config",
    [
        {"trade_horizon": "intraday"},
        {"hold_minutes": 60},
        {"entry_time": "10:00"},
        {"exit_time": "15:00"},
        {"trade_horizon": "multiday", "entry_time": "09:20", "hold_sessions": 10},
    ],
)
def test_explicit_intraday_timing_requires_minutes_for_dated_csv(signals, reference, config):
    request = {"config": config}
    assert select_interval(signals, request) == "1m"
    plan = build_plan(signals, request, reference)
    assert plan["interval"] == "1m"
    assert plan["reasons"] == ["timed_execution"]


def test_daily_optimizer_risk_axes_and_trailing_choices_stay_daily(signals, reference):
    request = {
        "kind": "optimize",
        "specification": {
            "axes": {
                "target_pct": {"min": 5, "max": 20, "step": 5},
                "stop_pct": {"min": 2, "max": 10, "step": 2},
                "trailing_pct": {"min": 0, "max": 4, "step": 2},
                "hold_sessions": {"min": 1, "max": 8, "step": 1},
            },
            "trailing_choices": [{"enabled": True, "pct": 0}, {"enabled": True, "pct": 2}],
        },
    }
    assert select_interval(signals, request) == "D"
    plan = build_plan(signals, request, reference)
    assert plan["required_dates"] == {"AAA": reference["sessions"][1:10]}


@pytest.mark.parametrize("kind", ["optimize", "research"])
def test_minute_holding_axis_is_included_in_candidate_union(signals, reference, kind):
    search = {"axes": {"hold_minutes": {"min": 15, "max": 60, "step": 15}}}
    request = {
        "kind": kind,
        "specification": search if kind == "optimize" else {"search": search},
    }
    assert select_interval(signals, request) == "1m"
    assert build_plan(signals, request, reference)["interval"] == "1m"


@pytest.mark.parametrize("kind", ["sensitivity", "research"])
@pytest.mark.parametrize(
    "changes", [{"trade_horizon": "intraday"}, {"entry_time": "10:00"}, {"hold_minutes": 30}]
)
def test_a_timed_variant_requires_minutes_for_whole_experiment(signals, reference, kind, changes):
    request = {
        "kind": kind,
        "specification": {"variants": [{"name": "Timed setup", "changes": changes}]},
    }
    assert select_interval(signals, request) == "1m"
    assert build_plan(signals, request, reference)["interval"] == "1m"


def test_daily_plan_contains_only_union_of_actual_holding_dates(signals, reference):
    sessions = reference["sessions"]
    signals += [
        {"date": sessions[8], "symbol": "AAA"},
        {"date": sessions[3], "symbol": "BBB"},
    ]
    plan = build_plan(signals, {"config": {"hold_sessions": 1}}, reference)
    assert plan["required_dates"] == {
        "AAA": sessions[1:3] + sessions[9:11],
        "BBB": sessions[4:6],
    }
    assert sessions[0] not in plan["required_dates"]["AAA"]
    assert sessions[5] not in plan["required_dates"]["AAA"]


def test_daily_search_and_variants_cover_longest_candidate_holding(signals, reference):
    request = {
        "kind": "research",
        "config": {"hold_sessions": 1},
        "specification": {
            "search": {"axes": {"hold_sessions": {"min": 1, "max": 3, "step": 1}}},
            "variants": [{"name": "Longer hold", "changes": {"hold_sessions": 5}}],
        },
    }
    plan = build_plan(signals, request, reference)
    assert plan["interval"] == "D"
    assert plan["required_dates"] == {"AAA": reference["sessions"][1:7]}


def test_daily_holding_scope_clips_to_recorded_calendar(reference):
    signals = [{"date": reference["sessions"][-2], "symbol": "AAA"}]
    plan = build_plan(signals, {}, reference)
    assert plan["required_dates"] == {"AAA": reference["sessions"][-1:]}


def test_scoped_daily_prices_preserve_existing_daily_stop_and_target_policy(signals, reference):
    config = {"hold_sessions": 1, "trailing_enabled": True, "trailing_pct": 5}
    plan = build_plan(signals, {"config": config}, reference)
    sessions = reference["sessions"]
    snapshot = {
        "sessions": sessions,
        "provenance": {
            "interval": "D",
            "exchange": "NSE",
            "provider": "test",
            "calendar_basis": "fixture",
            "adjustment_basis": "raw",
            "synthetic": True,
        },
        "bars": {
            "AAA": {
                day: {"open": 100, "high": 112, "low": 90, "close": 100}
                for day in sessions
            }
        },
    }
    original = evaluate(signals, snapshot, config)
    scoped = copy.deepcopy(snapshot)
    scoped["required_dates"] = plan["required_dates"]
    scoped["bars"]["AAA"] = {
        day: scoped["bars"]["AAA"][day] for day in plan["required_dates"]["AAA"]
    }
    result = evaluate(signals, scoped, config)
    assert result["policy_version"] == POLICY_VERSION == "scanner-eod-next-open-marked-v3"
    assert result["ledger"][0]["entry_date"] == sessions[1]
    assert result["ledger"][0]["outcome"] == "stop"
    assert result["ledger"][0]["exit_raw_price"] == 95
    assert result["summary"] == original["summary"]
    assert result["ledger"] == original["ledger"]
    assert result["equity_curve"] == original["equity_curve"]


def test_daily_scope_is_bounded_before_allocating_full_plan(signals, reference, monkeypatch):
    monkeypatch.setattr(requirements, "MAX_BARS", 2)
    with pytest.raises(ValueError, match="daily prices"):
        build_plan(signals, {}, reference)


def test_daily_coverage_checks_holding_expansion_and_requires_recorded_scope(signals, reference):
    short = build_plan(signals, {"config": {"hold_sessions": 1}}, reference)
    long = build_plan(signals, {"config": {"hold_sessions": 5}}, reference)
    snapshot = {"provenance": {"interval": "D"}, "data_requirements": short}
    before = copy.deepcopy(snapshot)
    assert covers_plan(snapshot, short)
    assert not covers_plan(snapshot, long)
    assert snapshot == before
    snapshot["data_requirements"] = {"version": "research-price-requirements-v1"}
    assert not covers_plan(snapshot, short)


def test_existing_minute_scopes_remain_compatible_only_for_minute_requests(signals, reference):
    minute = build_plan(signals, {"config": {"entry_time": "10:00"}}, reference)
    saved = {**copy.deepcopy(minute), "version": "research-price-requirements-v1"}
    snapshot = {"provenance": {"interval": "1m"}, "data_requirements": saved}
    before = copy.deepcopy(snapshot)
    assert covers_plan(snapshot, minute)
    assert not covers_plan(snapshot, build_plan(signals, {}, reference))
    assert snapshot == before


def test_invalid_timing_is_rejected_at_selection_before_any_download(signals):
    with pytest.raises(ValueError, match="HH:MM"):
        select_interval(signals, {"config": {"entry_time": "not a time"}})
