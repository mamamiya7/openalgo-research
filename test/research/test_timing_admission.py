"""Timing receipts explain native semantics without changing saved experiments."""

from copy import deepcopy
from datetime import timedelta

import pytest

from research.data import NATIVE_CALENDAR_BASIS
from research.engine import validate_config
from research.intraday import entry_open, stamp
from research.timing_admission import (
    _minute_deadline,
    _minute_schedule,
    _native_calendar,
    build_admission,
)

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]


def calendar():
    hours = {day: {"open": "09:15", "close": "15:30"} for day in DAYS}
    hours[DAYS[1]] = {"open": "13:45", "close": "14:45"}
    hours[DAYS[2]] = {"open": "09:15", "close": "09:20"}
    return {
        "sessions": DAYS,
        "session_hours": hours,
        "provenance": {"exchange": "NSE", "calendar_basis": NATIVE_CALENDAR_BASIS},
    }


def inputs(config=None, signals=None, *, search=None):
    cfg = validate_config({"hold_sessions": 1, "trade_horizon": "multiday", **(config or {})})
    row = {
        "id": "scanner",
        "name": "Scanner",
        "source_id": "a" * 32,
        "allocation_pct": 100,
        "config": cfg,
        "search": search or {},
        "signals": signals or [{"symbol": "AAA", "date": DAYS[0], "row": 2}],
    }
    portfolio = {
        "version": "research-portfolio-v1",
        "name": "Timing",
        "engine": "vectorbt",
        "capital": 100000,
        "strategies": [row],
    }
    if search:
        portfolio["optimization"] = {
            "sampler": "tpe",
            "trials": 10,
            "seed": 0,
            "objective": "balanced",
        }
    return portfolio, [row]


def minute_snapshot():
    value = calendar()
    timeline = []
    for day in value["sessions"]:
        hours = value["session_hours"][day]
        cursor, close = (
            stamp(f"{day}T{hours['open']}:00+05:30"),
            stamp(f"{day}T{hours['close']}:00+05:30"),
        )
        while cursor < close:
            timeline.append(cursor.isoformat())
            cursor += timedelta(minutes=1)
    return {**value, "timeline": timeline}


@pytest.mark.parametrize(
    "config,reason",
    [
        ({}, "Dated signals use next-session entry and session holding"),
        ({"trade_horizon": "intraday"}, "Intraday holding requires minute prices"),
        ({"hold_minutes": 15}, "Minute holding limit requires minute prices"),
        ({"entry_time": "10:00"}, "Entry/exit clocks require minute prices"),
        ({"exit_time": "14:30"}, "Entry/exit clocks require minute prices"),
    ],
)
def test_pre_acquisition_receipt_has_concrete_interval_reason_and_pending_native_calendar(
    config, reason
):
    portfolio, rows = inputs(config)
    source = deepcopy((portfolio, rows))
    receipt = build_admission(portfolio, rows)
    assert receipt["status"] == "pending_calendar"
    assert receipt["strategies"][0]["reason"] == reason
    assert receipt["required_interval"] == ("D" if not config else "1m")
    assert receipt["calendar_counts"] is None
    assert receipt["timing_search"]["status"] == "not_searched"
    assert receipt["price_coverage"] == "not_checked"
    assert (portfolio, rows) == source


def test_timestamp_keeps_multiday_horizon_and_mixed_signal_identity():
    signals = [
        {"symbol": "AAA", "date": DAYS[0], "timestamp": f"{DAYS[0]}T09:15:59+05:30", "row": 2},
        {"symbol": "BBB", "date": DAYS[0], "row": 3},
    ]
    portfolio, rows = inputs(signals=signals)
    result = build_admission(portfolio, rows)
    assert result["intent"] == "mixed" and result["required_interval"] == "1m"
    assert result["signals"] == {"total": 2, "timed": 1, "date_only": 1}
    assert rows[0]["config"]["trade_horizon"] == "multiday"
    assert result["strategies"][0]["reason"] == "Timestamped signals require minute prices"
    assert result["strategies"][0]["constraints"][0]["parameter"] == "hold_sessions"


def test_competing_deadlines_only_mark_intraday_sessions_dormant():
    portfolio, rows = inputs({"hold_minutes": 45, "hold_sessions": 2, "exit_time": "14:00"})
    mixed = build_admission(portfolio, rows)["strategies"][0]
    assert mixed["dormant_parameters"] == []
    assert [item["parameter"] for item in mixed["constraints"]] == [
        "hold_minutes",
        "hold_sessions",
        "exit_time",
    ]
    rows[0]["config"]["trade_horizon"] = "intraday"
    intraday = build_admission(portfolio, rows)["strategies"][0]
    assert intraday["dormant_parameters"] == ["hold_sessions"]
    assert [item["parameter"] for item in intraday["constraints"]] == [
        "hold_minutes",
        "session_close",
        "exit_time",
    ]


def test_mixed_book_uses_one_native_interval_and_explains_daily_strategy():
    portfolio, rows = inputs()
    other = deepcopy(rows[0])
    other.update(id="timed", allocation_pct=50)
    other["config"]["hold_minutes"] = 10
    rows[0]["allocation_pct"] = 50
    rows.append(other)
    result = build_admission(portfolio, rows)
    assert result["intent"] == "mixed" and result["required_interval"] == "1m"
    assert result["strategies"][0]["required_interval"] == "D"
    assert all(row["effective_interval"] == "1m" for row in result["strategies"])
    assert (
        result["strategies"][0]["reason"]
        == "Dated signals use next-session entry and session holding"
    )
    mismatch = build_admission(portfolio, rows, interval="D")
    assert mismatch["status"] == "blocked"
    assert "require 1m" in mismatch["reason"]


def test_calendar_uses_recorded_hours_and_does_not_invent_missing_or_unverified_sessions():
    portfolio, rows = inputs()
    native = calendar()
    assert build_admission(portfolio, rows, calendar=native)["status"] == "ready"
    del native["session_hours"][DAYS[1]]
    missing = build_admission(portfolio, rows, calendar=native)
    assert missing["status"] == "pending_calendar"
    assert "hours" in missing["reason"]
    native = calendar()
    native["provenance"]["calendar_basis"] = "assumed-weekdays"
    assert build_admission(portfolio, rows, calendar=native)["status"] == "pending_calendar"


def test_outside_date_is_precise_and_partial_exclusions_do_not_claim_all_signals_are_admitted():
    portfolio, rows = inputs(
        signals=[
            {"symbol": "BAD", "date": "2026-01-04", "row": 2},
            {"symbol": "AAA", "date": DAYS[0], "row": 3},
        ]
    )
    result = build_admission(portfolio, rows, calendar=calendar())
    assert result["status"] == "ready"
    assert result["calendar_counts"] == {"scheduled": 1, "excluded": 1, "incomplete": 0}
    issue = result["strategies"][0]["calendar"]["first_issue"]
    assert issue["symbol"] == "BAD" and issue["signal_date"] == "2026-01-04"
    assert issue["calendar_from"] == DAYS[0] and issue["calendar_to"] == DAYS[-1]
    rows[0]["signals"] = rows[0]["signals"][:1]
    assert build_admission(portfolio, rows, calendar=calendar())["status"] == "blocked"


def test_maximum_search_deadline_is_checked_before_a_short_baseline_is_called_ready():
    portfolio, rows = inputs(search={"hold_sessions": {"min": 1, "max": 4, "step": 1}})
    result = build_admission(portfolio, rows, calendar=calendar())
    assert result["status"] == "pending_calendar"
    assert result["calendar_counts"]["incomplete"] == 1
    assert result["strategies"][0]["searched_deadline"] == "hold_sessions"
    assert result["strategies"][0]["constraints"][0]["maximum"] == 4


@pytest.mark.parametrize(
    "config",
    [
        {"hold_sessions": 2},
        {"trade_horizon": "intraday"},
        {"hold_minutes": 5},
        {"hold_minutes": 400, "hold_sessions": 10},
        {"hold_minutes": 100000, "hold_sessions": 1},
        {"entry_time": "10:00"},
        {"entry_time": "16:00"},
        {"exit_time": "08:00"},
        {"exit_time": "14:00"},
        {"exit_time": "16:00"},
        {"entry_time": "13:50", "exit_time": "14:00", "hold_minutes": 60},
    ],
)
def test_optimized_receipt_schedule_and_deadline_match_native_functions(config):
    from research.automatic_protocol import _complete_deadline
    from research.connectors.vectorbt_portfolio import _deadline, _schedule

    snapshot = minute_snapshot()
    native, _ = _native_calendar(snapshot)
    timeline = snapshot["timeline"]
    index = {slot: ordinal for ordinal, slot in enumerate(timeline)}
    cfg = validate_config(config)
    entry_days = [
        day
        for day in DAYS
        if not cfg.get("entry_time") or cfg["entry_time"] < snapshot["session_hours"][day]["close"]
    ]
    exit_days = [
        day
        for day in DAYS
        if cfg.get("exit_time") and cfg["exit_time"] < snapshot["session_hours"][day]["close"]
    ]
    for day in DAYS:
        for timestamp in (
            None,
            "09:15:00",
            "09:15:59",
            "09:19:59",
            "14:44:00",
            "15:29:59",
            "23:59:59",
        ):
            signal = {"symbol": "AAA", "date": day, "row": 2}
            if timestamp:
                signal["timestamp"] = f"{day}T{timestamp}+05:30"
            direct = entry_open(signal, snapshot, cfg)
            planned, issue = _minute_schedule(signal, cfg, native, entry_days)
            entered, reason = _schedule(signal, snapshot, cfg, timeline, index, True)
            if reason:
                assert issue == reason and planned is None
                continue
            assert planned == direct
            if entered < 0:
                continue
            deadline = _minute_deadline(planned, cfg, native, exit_days)
            end = _deadline(entered, snapshot, cfg, timeline, True)
            if deadline is None:
                assert not _complete_deadline(entered, end, snapshot, cfg, timeline, True)
            else:
                assert deadline.isoformat() == timeline[end]


def test_receipt_ignores_raw_csv_private_receipts_and_ohlc_but_binds_normalized_timing():
    portfolio, rows = inputs()
    native = calendar()
    native["bars"] = {"AAA": {DAYS[0]: {"close": 100}}}
    rows[0]["original_csv_base64"] = "private original bytes"
    rows[0]["acquisition_receipts"] = [{"not": "timing input"}]
    source = deepcopy((portfolio, rows, native))
    original = build_admission(portfolio, rows, calendar=native)
    assert (portfolio, rows, native) == source
    rows[0]["original_csv_base64"] = "different private bytes"
    rows[0]["acquisition_receipts"] = []
    native["bars"]["AAA"][DAYS[0]]["close"] = 9000
    assert build_admission(portfolio, rows, calendar=native) == original
    rows[0]["config"]["hold_sessions"] = 2
    assert build_admission(portfolio, rows, calendar=native)["inputs_id"] != original["inputs_id"]


def test_bounds_are_enforced_before_timing_loops_or_fingerprints():
    portfolio, rows = inputs()
    rows[0]["signals"] *= 25001
    with pytest.raises(ValueError, match="25000"):
        build_admission(portfolio, rows)
    with pytest.raises(ValueError, match="eight"):
        build_admission(portfolio, [])
