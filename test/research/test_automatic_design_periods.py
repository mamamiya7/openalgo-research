"""Automatic date planning uses complete opportunities, never profitable dates."""

from copy import deepcopy

import pytest
from test_automatic_protocol import evidence
from test_available_history_protocol import available

from research.available_history_protocol import (
    DESIGN_POLICY,
    DESIGN_VERSION,
    entry_bins,
    opportunity_calendar,
    plan,
)
from research.walk_forward_protocol import build


def designed(count=160, hold=45):
    value = available(count, hold)
    value["portfolio"]["automatic_research"]["design_policy"] = DESIGN_POLICY
    return value


def planned(value):
    return plan(value, build(value))


def test_new_design_keeps_original_hold_and_uses_longer_training_prefixes():
    value = designed()
    before = deepcopy(value)
    metadata, periods = planned(value)
    assert value == before
    assert metadata["version"] == DESIGN_VERSION
    assert metadata["design_policy"] == DESIGN_POLICY
    assert metadata["holding"]["max_sessions"] == 82
    assert value["portfolio"]["strategies"][0]["config"]["hold_sessions"] == 45
    assert metadata["fit_prefixes"] == {"fit1": 142, "fit2": 148, "fit3": 154, "search": 160}
    assert metadata["prefix_evidence"] == "expanding_prefixes"
    assert metadata["temporal_evidence"] == "overlapping_historical_fits"
    assert metadata["independently_validated"] is False
    assert set(periods) == {"fit1", "fit2", "fit3", "search"}


def test_opportunity_dates_exclude_complete_holding_tail_and_empty_calendar():
    value = designed()
    # Only actual source opportunities count; weekends and no-signal sessions
    # must not dilute temporal coverage. Frozen exclusions remain excluded.
    row = value["strategies"][0]
    row["signals"] = [signal for index, signal in enumerate(row["signals"]) if index % 2 == 0]
    row["signals"][0]["research_exclusion"] = "Recorded coverage exclusion"
    value["signals"] = [dict(signal, strategy_id=row["id"]) for signal in row["signals"]]
    metadata, _ = planned(value)
    sessions = value["snapshot"]["sessions"]
    # Last signal is now index158: entry+82 must fit wholly within that end.
    expected = [sessions[index + 1] for index in range(2, 76, 2)]
    assert metadata["usable_entry_dates"] == expected
    assert metadata["usable_entry_session_count"] == len(expected)
    assert sum(item["sessions"] for item in metadata["entry_date_bins"]) == len(expected)
    assert metadata["entry_date_bins"][-1]["to"] == expected[-1]
    assert metadata["entry_date_bins"][-1]["to"] < sessions[158]


def test_160_session_bin_calendar_has_only_77_complete_entry_sessions():
    metadata, _ = planned(designed())
    assert metadata["usable_entry_session_count"] == 77
    assert [item["sessions"] for item in metadata["entry_date_bins"]] == [26, 26, 25]
    assert [item["name"] for item in metadata["entry_date_bins"]] == ["early", "middle", "late"]


def test_prices_or_profitable_dates_cannot_choose_periods_or_bins():
    value = designed()
    expected = planned(value)
    value["summary"] = {"net_return_pct": 9999}
    for index, bar in enumerate(value["snapshot"]["bars"]["AAA"].values()):
        bar.update(open=index + 1, high=index + 100, low=index + 1, close=index + 99)
    assert planned(value) == expected


@pytest.mark.parametrize("count", [1, 3, 30, 80, 100, 142])
def test_short_history_is_used_fully_and_repeated_prefixes_are_declared(count):
    metadata, periods = planned(designed(count))
    assert metadata["fit_prefixes"] == dict.fromkeys(("fit1", "fit2", "fit3", "search"), count)
    assert metadata["prefix_evidence"] == "limited_overlapping_prefixes"
    assert len({period["to"] for period in periods.values()}) == 1
    assert metadata["independently_validated"] is False
    if count <= 83:
        assert metadata["usable_entry_session_count"] == 0
        assert metadata["entry_date_bins"] == []


@pytest.mark.parametrize("count", [143, 144])
def test_partial_prefix_growth_still_records_repeated_training_periods(count):
    metadata, _ = planned(designed(count))
    assert len(set(metadata["fit_prefixes"].values())) < 4
    assert metadata["prefix_evidence"] == "limited_overlapping_prefixes"


def test_old_available_history_plan_remains_exact():
    value = available()
    metadata, periods = planned(value)
    assert metadata["version"] == "research-available-history-v1"
    assert metadata["fit_prefixes"] == {"fit1": 103, "fit2": 122, "fit3": 141, "search": 160}
    assert not {
        "design_policy",
        "usable_entry_session_count",
        "usable_entry_dates",
        "entry_date_bins",
        "temporal_evidence",
        "prefix_evidence",
    }.intersection(metadata)
    assert [item["sessions"] for item in periods.values()] == [103, 122, 141, 160]


def test_long_history_preserves_recorded_chronological_periods():
    value = designed(200, hold=2)
    attempted = build(value)
    metadata, periods = plan(value, attempted)
    assert metadata["version"] == DESIGN_VERSION
    assert metadata["mode"] == "historical_only"
    assert periods == attempted["periods"]
    assert "fit_prefixes" not in metadata
    assert metadata["independently_validated"] is False


def test_entry_bins_partition_unique_dates_and_do_not_invent_empty_bins():
    assert entry_bins([]) == []
    assert entry_bins(["2026-01-05", "2026-01-05"]) == [
        {"name": "early", "from": "2026-01-05", "to": "2026-01-05", "sessions": 1}
    ]
    assert [item["sessions"] for item in entry_bins(["2026-01-07", "2026-01-05"])] == [1, 1]


def test_opportunity_calendar_accepts_already_clipped_period_without_tail():
    value = designed()
    value["snapshot"]["sessions"] = value["snapshot"]["sessions"][:142]
    value["strategies"][0]["signals"] = value["strategies"][0]["signals"][:142]
    dates = opportunity_calendar(value)
    assert len(dates) == 59
    assert dates == value["snapshot"]["sessions"][1:60]


def test_timing_union_uses_original_signal_identity_after_period_slicing():
    value = designed(100)
    signals = deepcopy(value["strategies"][0]["signals"][:3])
    days = value["snapshot"]["sessions"]
    value["timing_cohort"] = {"strategies": [{"id": "scanner", "signals": signals}]}
    value["timing_union"] = {
        "opportunities": [
            {
                "strategy_id": "scanner",
                "signal_index": index,
                "windows": {
                    "original": {
                        "state": "scheduled",
                        "entry": days[index + 1],
                        "deadline": days[index + 83],
                    }
                },
            }
            for index in range(3)
        ]
    }
    # A sliced period starts at source row1; enumerating sliced rows as original
    # indices would wrongly use row0's clock and admit the wrong entry date.
    value["strategies"][0]["signals"] = signals[1:]
    value["snapshot"]["sessions"] = days[1:85]
    assert opportunity_calendar(value) == [days[2]]
    value["timing_cohort"]["strategies"][0]["signals"][1]["research_exclusion"] = (
        "Timing evidence unavailable"
    )
    assert opportunity_calendar(value) == []


def test_minute_opportunities_count_actual_entry_dates_once():
    value = evidence(signal_sessions=20, tail=2, minute=True)
    assert opportunity_calendar(value) == value["snapshot"]["sessions"][:20]
    assert sum(item["sessions"] for item in entry_bins(opportunity_calendar(value))) == 20
