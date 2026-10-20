"""Outcome-free expanding-fit chronology and explicit insufficient admission."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from research.report_contract import fingerprint
from research.walk_forward_protocol import build, verify


def evidence(count=180, *, minute=False, hold=2):
    days, cursor = [], date(2025, 1, 1)
    while len(days) < count:
        # Deliberately irregular observed calendar, not an invented Mon-Fri feed.
        if cursor.weekday() < 5 and cursor.day not in (6, 19):
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    config = {"hold_sessions": hold, "initial_capital": 100000, "cost_bps": 5}
    row = {
        "id": "scanner",
        "config": config,
        "search": {"hold_sessions": {"min": 1, "max": hold * 2, "step": 1}},
    }
    snapshot = {"sessions": days, "provenance": {"interval": "1m" if minute else "D"}}
    if minute:
        config.update(trade_horizon="intraday", hold_minutes=2)
        row["search"]["hold_minutes"] = {"min": 1, "max": 10, "step": 1}
        snapshot["timeline"] = [
            f"{day}T09:{15 + index:02d}:00+05:30"
            for n, day in enumerate(days)
            for index in range(3 if n % 11 == 0 else 5)
        ]
    return {
        "portfolio": {"capital": 100000, "engine": "vectorbt", "strategies": [row]},
        "snapshot": snapshot,
        "signals": [{"date": day, "symbol": "AAA"} for day in days],
    }


@pytest.mark.parametrize("minute", [False, True])
def test_three_expanding_fits_are_before_distinct_tests_and_reserved_final(minute):
    value = evidence(minute=minute)
    before = deepcopy(value)
    plan = build(value)
    assert value == before
    assert plan["status"] == "ready"
    assert len(plan["folds"]) == 3
    assert len(plan["periods"]) == 8
    days = value["snapshot"]["sessions"]
    assert plan["periods"]["fit1"]["sessions"] == 72
    assert plan["periods"]["search"]["sessions"] == 144
    assert plan["periods"]["final"]["sessions"] == 36
    seen = set()
    prior = None
    for fold in plan["folds"]:
        fit, test = fold["fit"], fold["test"]
        assert fit["from"] == days[0]
        assert fit["to"] < test["from"]
        assert test["to"] < plan["periods"]["final"]["from"]
        actual = {day for day in days if test["from"] <= day <= test["to"]}
        assert not seen.intersection(actual)
        seen.update(actual)
        if prior:
            assert fit["to"] == prior["test"]["to"]
            assert fit["sessions"] > prior["fit"]["sessions"]
        prior = fold
    assert plan["final_fit"]["fit"]["to"] == plan["folds"][-1]["test"]["to"]
    assert plan["final_fit"]["fit"]["to"] < plan["final_fit"]["test"]["from"]
    assert verify(plan, evidence=value)
    assert plan["id"] == fingerprint({key: item for key, item in plan.items() if key != "id"})


def test_no_account_outcome_or_future_price_changes_the_plan():
    value = evidence()
    original = build(value)
    value["snapshot"]["bars"] = {"AAA": {"future": {"close": float("nan")}}}
    value["ledger"] = [{"profit": 999999999}]
    value["result"] = {"score": float("inf")}
    value["automatic_recipe"] = {"periods": {"final": "forged"}}
    assert build(value) == original
    assert verify(original, evidence=value)


@pytest.mark.parametrize("count,hold", [(90, 1), (180, 20), (180, 100), (1, 1)])
def test_insufficient_history_is_explicit_and_never_overlaps_or_shortens_hold(count, hold):
    value = evidence(count, hold=hold)
    before = deepcopy(value)
    plan = build(value)
    assert plan["status"] == "insufficient_history"
    assert plan["periods"] == {}
    assert plan["folds"] == []
    assert plan["final_fit"] is None
    assert "longer signal history" in plan["reason"]
    assert value == before
    assert verify(plan, evidence=value)


def test_minute_holding_uses_sparse_observed_session_counts():
    value = evidence(minute=True)
    plan = build(value)
    assert plan["holding"]["minimum_observed_minutes_per_session"] == 3
    assert plan["holding"]["max_sessions"] == 4
    value["portfolio"]["strategies"][0]["search"]["hold_minutes"]["max"] = 90
    plan = build(value)
    assert plan["holding"]["max_sessions"] == 30
    assert plan["status"] == "insufficient_history"


def test_minute_multiday_hold_keeps_session_deadline_and_intraday_close_needs_no_guess():
    value = evidence(minute=True)
    row = value["portfolio"]["strategies"][0]
    row["config"].pop("hold_minutes")
    row["config"]["trade_horizon"] = "multiday"
    row["search"]["hold_sessions"]["max"] = 30
    assert build(value)["holding"]["max_sessions"] == 30
    row["config"]["trade_horizon"] = "intraday"
    assert build(value)["holding"]["max_sessions"] == 1


def test_original_deadline_remains_part_of_maximum_comparison_horizon():
    value = evidence()
    row = value["portfolio"]["strategies"][0]
    row["config"]["hold_sessions"] = 50
    row["search"]["hold_sessions"]["max"] = 2
    assert build(value)["holding"]["max_sessions"] == 50


def test_daily_window_floor_preserves_five_complete_next_session_entry_dates():
    value = evidence(120, hold=5)
    plan = build(value)
    assert plan["status"] == "ready"
    assert plan["holding"]["max_sessions"] == 10
    assert plan["holding"]["min_window_sessions"] == 16
    for fold in plan["folds"]:
        assert fold["test"]["sessions"] == 16
        assert plan["complete_signal_date_support"][fold["test_period"]] == 5
    short = evidence(113, hold=5)
    rejected = build(short)
    assert rejected["status"] == "insufficient_history"
    assert rejected["reason_code"] == "short_history"
    assert short["portfolio"]["strategies"][0]["search"]["hold_sessions"]["max"] == 10


def test_raw_dates_clustered_in_purged_suffix_do_not_trigger_an_expensive_futile_fit():
    value = evidence()
    original = build(value)
    bounds = original["periods"]["check2"]
    dates = [day for day in value["snapshot"]["sessions"] if bounds["from"] <= day <= bounds["to"]]
    keep = set(dates[-5:])
    for row in value["signals"]:
        if bounds["from"] <= row["date"] <= bounds["to"] and row["date"] not in keep:
            row["research_exclusion"] = "Unavailable source opportunity"
    plan = build(value)
    assert plan["signal_date_support"]["check2"] == 5
    assert plan["complete_signal_date_support"]["check2"] == 0
    assert plan["status"] == "insufficient_history"
    assert plan["reason_code"] == "sparse_dates"


def test_calendar_is_recorded_signal_span_only_not_execution_tail_or_wall_clock():
    value = evidence()
    original = build(value)
    value["snapshot"]["sessions"].append("2027-01-01")
    assert build(value) == original


def test_budget_reserves_four_fits_and_bounded_retries_without_infinite_work():
    budget = build(evidence())["budget"]
    assert budget["fits"] == 4
    assert budget["proposals_per_fit"] == 48
    assert budget["screening_proposals_per_fit"] + budget["refinement_proposals_per_fit"] == 48
    assert budget["proposals"] == 192
    assert budget["proposals"] + budget["nonproposal_simulation_reserve"] == 256
    assert budget["max_nominal_simulations"] + budget["retry_reservations"] == 320
    assert budget["total_proposals"] == budget["proposals"]
    assert budget["max_clock_probes_per_fit"] == 8
    assert budget["family_screening_per_fit"] == 32
    assert budget["final_reserved_calls"] == 2
    assert budget["reconstruction_reserved_calls"] == 1


@pytest.mark.parametrize("window", ["fit1", "check1", "check2", "check3", "final"])
def test_sparse_signal_dates_are_not_certified_as_independent_repeated_symbols(window):
    value = evidence()
    ready = build(value)
    bounds = ready["periods"][window]
    in_window = [row for row in value["signals"] if bounds["from"] <= row["date"] <= bounds["to"]]
    threshold = 20 if window == "fit1" else 5
    keep_dates = {row["date"] for row in in_window[: threshold - 1]}
    # Retain source endpoints to keep the recorded span fixed. Mark the other
    # dates unavailable; duplicating their remaining symbols cannot add support.
    for row in in_window:
        if row["date"] not in keep_dates:
            row["research_exclusion"] = "Missing source evidence"
    value["signals"] += [deepcopy(in_window[0]) for _ in range(300)]
    plan = build(value)
    assert plan["status"] == "insufficient_history"
    assert plan["reason_code"] == "sparse_dates"
    assert plan["periods"] == {}
    assert plan["signal_date_support"][window] == threshold - 1
    assert verify(plan, evidence=value)


@pytest.mark.parametrize("change", ["hash", "period", "setting", "calendar"])
def test_verification_rejects_edits_or_frozen_input_changes(change):
    value = evidence()
    plan = build(value)
    if change == "hash":
        plan["id"] = "0" * 64
    elif change == "period":
        plan["periods"]["fit1"]["to"] = plan["periods"]["final"]["to"]
        plan["id"] = fingerprint({key: item for key, item in plan.items() if key != "id"})
    elif change == "setting":
        value["portfolio"]["capital"] = 123456
    else:
        value["snapshot"]["sessions"].pop(10)
    with pytest.raises(ValueError, match="differs"):
        verify(plan, evidence=value)


@pytest.mark.parametrize("problem", ["duplicate", "reorder", "bad_date", "too_many", "bad_hold"])
def test_invalid_calendar_or_horizon_cannot_be_admitted(problem):
    value = evidence()
    if problem == "duplicate":
        value["snapshot"]["sessions"].insert(2, value["snapshot"]["sessions"][2])
    elif problem == "reorder":
        value["snapshot"]["sessions"][0:2] = reversed(value["snapshot"]["sessions"][0:2])
    elif problem == "bad_date":
        value["snapshot"]["sessions"][0] = "20250101"
    elif problem == "too_many":
        value["signals"] *= 200
    else:
        value["portfolio"]["strategies"][0]["search"]["hold_sessions"]["max"] = True
    with pytest.raises(ValueError):
        build(value)


@pytest.mark.parametrize("problem", ["duplicate", "naive", "empty", "reorder"])
def test_minute_timeline_requires_observed_ordered_aware_sessions(problem):
    value = evidence(minute=True)
    slots = value["snapshot"]["timeline"]
    if problem == "duplicate":
        slots.insert(2, slots[2])
    elif problem == "naive":
        slots[0] = slots[0].split("+")[0]
    elif problem == "empty":
        value["snapshot"]["timeline"] = []
    else:
        slots[0:2] = reversed(slots[0:2])
    with pytest.raises(ValueError):
        build(value)


def test_minute_execution_union_can_start_after_first_signal_calendar_date():
    value = evidence(minute=True)
    first = value["snapshot"]["sessions"][0]
    value["snapshot"]["timeline"] = [
        stamp for stamp in value["snapshot"]["timeline"] if stamp[:10] != first
    ]
    plan = build(value)
    assert plan["status"] == "ready"
    assert plan["calendar"]["date_from"] == first
    assert plan["holding"]["minimum_observed_minutes_per_session"] == 3
    assert all(not stamp.startswith(first) for stamp in value["snapshot"]["timeline"])
    assert verify(plan, evidence=value)


def test_metadata_bound_prevents_unbounded_setting_identity_or_forged_plan():
    value = evidence()
    plan = build(value)
    value["portfolio"]["padding"] = "x" * 70000
    with pytest.raises(ValueError, match="metadata limit"):
        build(value)
    plan["padding"] = "x" * 70000
    with pytest.raises(ValueError, match="metadata limit"):
        verify(plan, evidence=evidence())
