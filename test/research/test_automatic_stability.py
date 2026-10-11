"""Bounded local challenges, chronology and committed-work recovery with real Optuna."""

from copy import deepcopy

import pytest
from test_automatic_research import Engine, execute, prepared

from research.automatic_protocol import (
    STABILITY_VERSION,
    automatic_portfolio,
    check_signal_span,
    compile_recipe,
)
from research.automatic_stability import neighbors


def evidence():
    value = prepared()
    value["portfolio"]["automatic_research"] = {"version": STABILITY_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_impossible_signal_span_fails_before_data_acquisition_without_guessing_sessions():
    value = evidence()["portfolio"]
    with pytest.raises(ValueError, match="100 trading sessions.*10 calendar days"):
        check_signal_span(value, {"date_from": "2026-01-01", "date_to": "2026-01-10"}, interval="D")
    value["strategies"][0]["config"]["hold_sessions"] = 45
    value = automatic_portfolio(value)
    with pytest.raises(ValueError, match="allowed 82-session hold"):
        check_signal_span(value, {"date_from": "2026-01-21", "date_to": "2026-09-11"}, interval="D")
    # Timestamped/intraday deadlines aren't rejected based on an unused daily hold.
    check_signal_span(value, {"date_from": "2026-01-21", "date_to": "2026-09-11"}, interval="1m")
    value["automatic_research"]["version"] = "automatic-trade-management-v1"
    check_signal_span(value, {"date_from": "2026-01-01", "date_to": "2026-01-10"}, interval="D")


def test_adjacent_grid_is_exact_bounded_and_never_changes_risk_or_original_settings():
    value = evidence()
    settings = deepcopy(value["automatic_recipe"]["baseline"])
    for row in settings:
        for key, domain in value["portfolio"]["strategies"][0]["search"].items():
            row["config"][key] = domain["min"]
    original = deepcopy(settings)
    result = neighbors(settings, value["automatic_recipe"]["ranges"])
    assert result["neighbors"] and len(result["neighbors"]) <= 8
    assert settings == original
    for neighbor in result["neighbors"]:
        expected = deepcopy(original)
        expected[0]["config"][neighbor["axis"]] = neighbor["to"]
        assert neighbor["strategies"] == expected
        assert neighbor["to"] > neighbor["from"]


def test_decimal_steps_do_not_accumulate_float_error_and_off_grid_is_rejected():
    settings = [{"id": "one", "name": "One", "allocation_pct": 100, "config": {"target_pct": 0.3}}]
    ranges = [{"id": "one", "search": {"target_pct": {"min": 0.1, "max": 0.5, "step": 0.1}}}]
    result = neighbors(settings, ranges)
    assert [row["to"] for row in result["neighbors"]] == [0.2, 0.4]
    settings[0]["config"]["target_pct"] = 0.35
    with pytest.raises(ValueError, match="frozen search grid"):
        neighbors(settings, ranges)


def test_subset_and_singleton_are_explicit_and_finite_validation_is_enforced():
    settings = [
        {"id": str(i), "name": str(i), "allocation_pct": 10, "config": {"target_pct": 2}}
        for i in range(8)
    ]
    ranges = [
        {"id": row["id"], "search": {"target_pct": {"min": 1, "max": 3, "step": 1}}}
        for row in settings
    ]
    result = neighbors(settings, ranges)
    assert result["available"] == 16 and result["truncated"]
    assert len(result["neighbors"]) == 8
    assert len({row["strategy_id"] for row in result["neighbors"]}) == 8
    for row in ranges:
        row["search"]["target_pct"] = {"min": 2, "max": 2, "step": 1}
    assert neighbors(settings, ranges)["neighbors"] == []
    ranges[0]["search"]["target_pct"]["step"] = float("nan")
    with pytest.raises(ValueError, match="finite ordered"):
        neighbors(settings, ranges)


def test_multi_strategy_subset_balances_parameters_and_directions_before_repeats():
    settings = [
        {
            "id": str(i),
            "name": str(i),
            "allocation_pct": 10,
            "config": {"target_pct": 2, "stop_pct": 2, "hold_sessions": 2},
        }
        for i in range(8)
    ]
    ranges = [
        {
            "id": row["id"],
            "search": {
                axis: {"min": 1, "max": 3, "step": 1}
                for axis in ("target_pct", "stop_pct", "hold_sessions")
            },
        }
        for row in settings
    ]
    result = neighbors(settings, ranges)
    assert len({row["strategy_id"] for row in result["neighbors"]}) == 8
    assert {row["axis"] for row in result["neighbors"]} == {
        "target_pct",
        "stop_pct",
        "hold_sessions",
    }
    assert {row["to"] > row["from"] for row in result["neighbors"]} == {False, True}
    assert result == neighbors(settings, ranges)


def test_smooth_candidate_passes_and_final_loss_cannot_rerank_stability_selection():
    value = evidence()
    good, state = execute(value, Engine(value))
    bad, _ = execute(value, Engine(value, final_loss=True))
    finding = good["automatic_research"]
    assert finding["version"] == STABILITY_VERSION
    assert finding["status"] == "supported"
    assert finding["selected_config_id"] == bad["automatic_research"]["selected_config_id"]
    assert bad["automatic_research"]["status"] == "not_supported"
    assert state["simulations"] <= 118
    eligible = [row for row in finding["checks"] if row["eligible"]]
    assert eligible and all(row["stability"]["status"] == "stable" for row in eligible)
    assert all(row["stability"]["pass_fraction"] >= 0.75 for row in eligible)
    assert "Neighbouring-parameter stability" not in finding["unsupported_families"]
    assert all(
        window["period"] != "final"
        for row in eligible
        for neighbor in row["stability"]["neighbors"]
        for window in neighbor["windows"]
    )


class FragileEngine(Engine):
    def __call__(self, rows, snapshot, capital, progress=None):
        result = super().__call__(rows, snapshot, capital, progress)
        # A sharp peak wins the initial search but its target neighbors collapse.
        target = rows[0]["config"]["target_pct"]
        result["summary"]["net_return_pct"] = 20 if target == 6 else (5 if target == 4 else -10)
        return result


def test_sharp_peak_falls_back_to_baseline_instead_of_promoting_an_unstable_winner():
    value = evidence()
    result, _ = execute(value, FragileEngine(value))
    finding = result["automatic_research"]
    assert finding["selected_is_baseline"]
    fragile = [
        row for row in finding["checks"] if (row.get("stability") or {}).get("status") == "fragile"
    ]
    assert fragile and all(not row["eligible"] for row in fragile)


def test_interrupted_neighborhood_resumes_committed_simulations_without_repeating_work():
    value = evidence()
    engine = Engine(value)
    partial, saved = execute(
        value,
        engine,
        stop=lambda state: state["selection"] is None and len(state["evaluations"]) >= 8,
    )
    assert partial is None and saved["version"] == STABILITY_VERSION
    resumed, finished = execute(value, engine, saved=saved)
    reference_engine = Engine(value)
    reference, _ = execute(value, reference_engine)
    assert engine.calls == reference_engine.calls
    assert resumed["automatic_research"] == reference["automatic_research"]
    assert finished["simulations"] == len(engine.calls)
    execute(value, engine, saved=finished)
    assert len(engine.calls) == finished["simulations"]
