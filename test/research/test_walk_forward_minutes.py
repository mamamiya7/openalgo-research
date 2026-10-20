"""Actual native clock unions remain causal across all eight v8 periods."""

from copy import deepcopy

import pytest
from test_automatic_walk_forward import prepared
from test_joint_stock_market import freeze_context
from test_timing_search import frozen, source

from research.automatic_protocol import WALK_FORWARD_VERSION, automatic_portfolio, slice_period
from research.timing_search import freeze_cohort

pytestmark = pytest.mark.timeout(180)


def test_minute_clock_union_purges_next_session_entries_at_every_period_boundary():
    days = prepared()["snapshot"]["sessions"]
    # Controlled short recorded sessions keep this boundary fixture inside the
    # unchanged native matrix bound; no production capacity limit is patched.
    value = source(config={"hold_minutes": 5}, times=("09:33:30",))
    value["portfolio"]["automatic_research"] = {"version": WALK_FORWARD_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    row = value["strategies"][0]
    row.update(deepcopy(value["portfolio"]["strategies"][0]))
    row["signals"] = [
        {"symbol": "AAA", "date": day, "timestamp": f"{day}T09:33:30+05:30", "row": i + 2}
        for i, day in enumerate(days[:160])
    ]
    value["signals"] = [dict(signal, strategy_id=row["id"]) for signal in row["signals"]]
    native = {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": "09:34"} for day in days},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }
    value = frozen(value, native)
    value["frozen_prices"] = True
    value["timing_cohort"] = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    value = freeze_context(value)
    original = deepcopy(value)
    plan = value["automatic_recipe"]
    assert plan["walk_forward"]["status"] == "ready"
    assert plan["timing_research"]["interval"] == "1m"
    assert len(plan["timing_research"]["strategies"][0]["branches"]) > 1
    assert plan["budget"]["proposals"] == 192
    assert plan["research_plan"]["budget"]["max_neighbors"] == 0
    assert plan["research_plan"]["budget"]["max_finalists"] == 1
    assert "48 total" in plan["timing_research"]["screening"]
    windows = {
        (entry["strategy_id"], entry["signal_index"]): entry["windows"]
        for entry in value["timing_union"]["opportunities"]
    }
    source_indices = {
        signal["row"]: i for i, signal in enumerate(value["strategies"][0]["signals"])
    }
    for name, bounds in plan["periods"].items():
        current = slice_period(value, name)
        assert current["automatic_period"]["purged_signals"] >= 1
        assert current["automatic_period"]["eligible_signal_dates"] >= 5
        for signal in current["strategies"][0]["signals"]:
            if signal.get("research_exclusion"):
                continue
            for branch in windows[row["id"], source_indices[signal["row"]]].values():
                if branch["state"] == "scheduled":
                    assert bounds["from"] <= branch["entry"][:10] <= bounds["to"]
                    assert branch["deadline"][:10] <= bounds["to"]
        assert current["snapshot"]["provenance"]["interval"] == "1m"
        assert current["snapshot"]["sessions"][-1] == bounds["to"]
    assert value == original
