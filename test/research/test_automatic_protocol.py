"""Automatic recipe identity, unchanged risk, chronology and causal window limits."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from research.automatic_protocol import (
    VERSION,
    automatic_portfolio,
    compile_recipe,
    normalize_auto,
    slice_period,
)
from research.connectors.optuna_portfolio import validate_specification
from research.engine import validate_config
from research.portfolio_coverage import prepare


def evidence(*, signal_sessions=160, tail=20, minute=False, config=None):
    days, cursor = [], date(2026, 1, 1)
    while len(days) < signal_sessions + tail:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    cfg = validate_config(config)
    if minute:
        cfg = validate_config({**cfg, "trade_horizon": "intraday", "hold_minutes": 2})
    row = {
        "id": "scanner",
        "name": "Scanner",
        "source_id": "a" * 32,
        "allocation_pct": 100,
        "config": cfg,
        "search": {},
    }
    portfolio = automatic_portfolio(
        {
            "version": "research-portfolio-v1",
            "name": "Test automatic research",
            "capital": 100000,
            "engine": "vectorbt",
            "strategies": [row],
            "automatic_research": {"version": VERSION},
        }
    )
    signals = [
        {"symbol": "AAA", "date": day, "row": i + 2} for i, day in enumerate(days[:signal_sessions])
    ]
    snapshot = {
        "sessions": days,
        "provenance": {
            "provider": "deterministic-test",
            "exchange": "NSE",
            "interval": "1m" if minute else "D",
            "adjustment_basis": "synthetic",
            "calendar_basis": "test-recorded-sessions",
            "synthetic": True,
        },
    }
    slots = days
    if minute:
        snapshot["provenance"]["temporal_version"] = "minute-open-v1"
        snapshot["session_hours"] = {day: {"open": "09:15", "close": "09:20"} for day in days}
        slots = [f"{day}T09:{minute:02d}:00+05:30" for day in days for minute in range(15, 20)]
        snapshot["timeline"] = slots
        for signal in signals:
            signal["timestamp"] = signal["date"] + "T09:15:00+05:30"
    snapshot["bars"] = {
        "AAA": {slot: {"open": 100, "high": 101, "low": 99, "close": 100} for slot in slots}
    }
    return prepare(
        {
            "portfolio": portfolio,
            "strategies": [{**portfolio["strategies"][0], "signals": signals}],
            "signals": [dict(signal, strategy_id="scanner") for signal in signals],
            "snapshot": snapshot,
            "versions": {"engine": "vectorbt", "policy": "test"},
            "frozen_prices": True,
        }
    )


def test_automatic_ranges_preserve_baseline_cost_capital_and_risk_and_are_idempotent():
    value = evidence()["portfolio"]
    original = deepcopy(value)
    value["strategies"][0]["search"] = {"allocation_pct": {"min": 1, "max": 99, "step": 1}}
    value["optimization"] = {"sampler": "grid", "trials": 999, "objective": "return"}
    result = automatic_portfolio(value)
    assert result == original
    assert automatic_portfolio(result) == result
    assert result["strategies"][0]["config"] == value["strategies"][0]["config"]
    assert result["strategies"][0]["allocation_pct"] == 100
    assert "allocation_pct" not in result["strategies"][0]["search"]
    assert "order_size_pct" not in result["strategies"][0]["search"]
    assert value["optimization"]["sampler"] == "grid"
    validate_specification(result["optimization"], result["strategies"])


def test_automatic_baseline_identity_uses_authoritative_shared_account_capital():
    from research.report_contract import settings_identity

    value = evidence()["portfolio"]
    value["capital"] = 12345
    value["strategies"][0]["config"]["initial_capital"] = 999
    normalized = automatic_portfolio(value)
    assert normalized["strategies"][0]["config"]["initial_capital"] == 12345.0
    engine_rows = deepcopy(normalized["strategies"])
    engine_rows[0]["config"]["initial_capital"] = float(normalized["capital"])
    assert settings_identity(normalized["strategies"]) == settings_identity(engine_rows)


@pytest.mark.parametrize(
    "raw", [None, True, {}, {"version": "future"}, {"version": VERSION, "trials": 500}]
)
def test_only_named_automatic_recipe_can_be_submitted(raw):
    with pytest.raises(ValueError, match="supported automatic"):
        normalize_auto(raw)


def test_manual_portfolio_is_unchanged_and_conflicting_holdout_is_rejected():
    portfolio = evidence()["portfolio"]
    manual = deepcopy(portfolio)
    manual.pop("automatic_research")
    assert automatic_portfolio(manual) == manual
    portfolio["validation"] = {"train_pct": 80, "mode": "reserve"}
    with pytest.raises(ValueError, match="own chronological"):
        automatic_portfolio(portfolio)


def test_cost_stress_must_fit_engine_limits_without_silent_clamping():
    with pytest.raises(ValueError, match="base transaction costs up to 250"):
        evidence(config={"cost_bps": 251})
    assert (
        evidence(config={"cost_bps": 250})["portfolio"]["strategies"][0]["config"]["cost_bps"]
        == 250
    )


def test_very_small_enabled_trailing_distance_still_has_valid_positive_search():
    portfolio = evidence(config={"trailing_enabled": True, "trailing_pct": 0.0001})["portfolio"]
    validate_specification(portfolio["optimization"], portfolio["strategies"])
    assert portfolio["strategies"][0]["config"]["trailing_pct"] == 0.0001
    assert portfolio["strategies"][0]["search"]["trailing_pct"]["min"] > 0


@pytest.mark.parametrize(
    "cfg,expected,absent",
    [
        (
            {"trade_horizon": "intraday"},
            {"target_pct", "stop_pct"},
            {"hold_sessions", "hold_minutes"},
        ),
        ({"hold_minutes": 30}, {"hold_minutes"}, {"hold_sessions", "trailing_pct"}),
        (
            {"trailing_enabled": True, "trailing_pct": 3},
            {"hold_sessions", "trailing_pct"},
            {"hold_minutes"},
        ),
    ],
)
def test_only_active_holding_and_trailing_controls_are_searched(cfg, expected, absent):
    portfolio = evidence(config=cfg)["portfolio"]
    search = portfolio["strategies"][0]["search"]
    assert expected <= search.keys()
    assert not absent & search.keys()
    validate_specification(portfolio["optimization"], portfolio["strategies"])


@pytest.mark.parametrize(
    "target,stop,hold", [(0.01, 0.01, 1), (499.999, 99, 252), (3.14159, 2.6543, 19)]
)
def test_preset_ranges_stay_valid_at_engine_limits(target, stop, hold):
    base = evidence()["portfolio"]
    base["strategies"][0]["config"].update(target_pct=target, stop_pct=stop, hold_sessions=hold)
    portfolio = automatic_portfolio(base)
    validate_specification(portfolio["optimization"], portfolio["strategies"])
    for axis in portfolio["strategies"][0]["search"].values():
        assert (axis["max"] - axis["min"]) / axis["step"] <= 6.000000001


def test_calendar_sessions_set_boundaries_not_signal_density_or_tail():
    value = evidence()
    # A sparse scanner and a crowded scanner keep the same calendar split.
    sparse = deepcopy(value)
    sparse["strategies"][0]["signals"] = (
        value["strategies"][0]["signals"][::3] + value["strategies"][0]["signals"][-1:]
    )
    sparse["signals"] = [
        dict(row, strategy_id="scanner") for row in sparse["strategies"][0]["signals"]
    ]
    first, second = compile_recipe(value), compile_recipe(sparse)
    assert first["periods"] == second["periods"]
    assert first["id"] != second["id"]
    assert [row["sessions"] for row in first["periods"].values()] == [96, 16, 16, 32]
    assert first["periods"]["final"]["to"] == value["signals"][-1]["date"]
    assert first["budget"]["max_simulations"] == 70
    assert first["selection"]["min_signal_dates"] == 5


def test_short_history_and_holding_window_too_long_are_clear_failures():
    with pytest.raises(ValueError, match="100 trading sessions"):
        compile_recipe(evidence(signal_sessions=99))
    with pytest.raises(ValueError, match="10 trading sessions.*10-session hold"):
        compile_recipe(evidence(signal_sessions=100))


def test_recipe_binds_inputs_and_rejects_tampered_saved_protocol():
    value = evidence()
    recipe = compile_recipe(value)
    value["automatic_recipe"] = deepcopy(recipe)
    assert compile_recipe(value) == recipe
    value["automatic_recipe"]["selection"]["max_drawdown_pct"] = 99
    with pytest.raises(ValueError, match="recipe or frozen inputs changed"):
        slice_period(value, "search")
    value["automatic_recipe"] = recipe
    day = value["snapshot"]["sessions"][-1]
    value["snapshot"]["bars"]["AAA"][day]["high"] = 105
    with pytest.raises(ValueError, match="recipe or frozen inputs changed"):
        slice_period(value, "search")


@pytest.mark.parametrize("name", ["search", "check1", "check2", "final"])
def test_each_period_excludes_maximum_horizon_crossings_and_all_other_prices(name):
    value = evidence()
    before = deepcopy(value)
    sliced = slice_period(value, name)
    period = compile_recipe(value)["periods"][name]
    assert min(sliced["snapshot"]["bars"]["AAA"]) == period["from"]
    assert max(sliced["snapshot"]["bars"]["AAA"]) == period["to"]
    assert sliced["automatic_period"]["purged_signals"] == 11
    assert sliced["automatic_period"]["eligible_signal_dates"] == period["sessions"] - 11
    assert sliced["portfolio"]["capital"] == 100000
    assert sliced["signal_coverage"]["excluded_signals"] == 11
    assert all(
        "Maximum holding window" in row["research_exclusion"]
        for row in sliced["strategies"][0]["signals"][-11:]
    )
    assert value == before


def test_original_exclusion_is_preserved_for_baseline_and_every_candidate():
    value = evidence()
    day = compile_recipe(value)["periods"]["check1"]["from"]
    signal = next(row for row in value["strategies"][0]["signals"] if row["date"] == day)
    signal["research_exclusion"] = "Recorded broker candle unavailable"
    result = slice_period(value, "check1")
    assert (
        result["strategies"][0]["signals"][0]["research_exclusion"] == signal["research_exclusion"]
    )
    assert result["automatic_period"]["eligible_signal_dates"] == 4
    assert result["signal_coverage"]["excluded_signals"] == 12


def test_final_boundary_is_purged_even_without_native_tail():
    value = evidence(tail=0)
    result = slice_period(value, "final")
    assert result["automatic_period"]["purged_signals"] == 11
    assert result["signal_coverage"]["pending_signals"] == 0


def test_minute_checks_retain_only_completed_intraday_observations():
    value = evidence(minute=True)
    final = slice_period(value, "final")
    assert final["automatic_period"]["purged_signals"] == 0
    assert final["automatic_period"]["eligible_signal_dates"] == 32
    assert all(
        final["automatic_period"]["from"] <= key[:10] <= final["automatic_period"]["to"]
        for key in final["snapshot"]["timeline"]
    )
    assert final["signal_coverage"]["pending_signals"] == 0


def test_later_ohlc_cannot_change_earlier_window_inputs():
    value = evidence()
    changed = deepcopy(value)
    start = compile_recipe(value)["periods"]["final"]["from"]
    for day, bar in changed["snapshot"]["bars"]["AAA"].items():
        if day >= start:
            bar["high"] = 10000
    first, second = slice_period(value, "search"), slice_period(changed, "search")
    assert first["snapshot"] == second["snapshot"]
    assert first["strategies"] == second["strategies"]
    assert first["automatic_recipe"]["id"] != second["automatic_recipe"]["id"]


def test_period_with_no_complete_outcomes_or_no_signals_is_not_scored_as_cash():
    value = evidence()
    recipe = compile_recipe(value)
    first, last = recipe["periods"]["check1"]["from"], recipe["periods"]["check1"]["to"]
    for row in value["strategies"][0]["signals"]:
        if first <= row["date"] <= last:
            row["research_exclusion"] = "Unavailable broker history"
    with pytest.raises(ValueError, match="no complete signal outcomes"):
        slice_period(value, "check1")
    value["strategies"][0]["signals"] = [
        row for row in value["strategies"][0]["signals"] if not first <= row["date"] <= last
    ]
    with pytest.raises(ValueError, match="no scanner signals"):
        slice_period(value, "check1")
