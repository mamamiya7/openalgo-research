"""V7 frozen native clocks use one cohort, bounded probes and deterministic replay."""

from copy import deepcopy
from datetime import date, timedelta

import pytest
from test_automatic_hypotheses import Engine, execute
from test_automatic_trailing import prepared as trailing_prepared
from test_joint_stock_market import freeze_context
from test_timing_search import frozen, source

from research import automatic_timing as timing
from research.automatic_hypotheses import TIMING_VERSION, _screening, policy_for_version
from research.automatic_protocol import automatic_portfolio, compile_recipe, slice_period
from research.automatic_trailing import axes as trailing_axes
from research.connectors.optuna_portfolio import _prepare, _settings
from research.joint_stock_market import catalog
from research.timing_search import _seal, freeze_cohort

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(autouse=True)
def no_optional_plots(monkeypatch):
    monkeypatch.setattr(
        "research.study_analysis.build_study_analysis", lambda *a, **k: {"charts": []}
    )


def prepared(*, minute=False, count=1):
    if not minute:
        value = trailing_prepared(sessions=160)
    else:
        value = source(times=("09:15:30",), count=count)
        days, cursor = [], date(2026, 1, 5)
        while len(days) < 25:
            if cursor.weekday() < 5:
                days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        for row in value["strategies"]:
            row["signals"] = [
                {
                    "symbol": "AAA",
                    "date": day,
                    "timestamp": f"{day}T09:15:30+05:30",
                    "row": index + 2,
                }
                for index, day in enumerate(days)
            ]
        value["signals"] = [
            dict(signal, strategy_id=row["id"])
            for row in value["strategies"]
            for signal in row["signals"]
        ]
    value["portfolio"]["automatic_research"] = {"version": TIMING_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    for row, cfg in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(cfg))
    if minute:
        native = {
            "sessions": days,
            "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
            "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
        }
        value = frozen(value, native)
        value["frozen_prices"] = True
        value["timing_cohort"] = freeze_cohort(
            value, value["timing_descriptor"], value["timing_union"]
        )
        return freeze_context(value)
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_daily_recipe_keeps_daily_data_and_original_timing_with_exact_baseline():
    value = prepared()
    spec = timing.descriptor(value, strict=True)
    assert spec["interval"] == "D" and spec["union_id"] is None
    assert timing.axes(spec)["scanner.timing_choice"]["choices"] == ["original"]
    assert (
        timing.apply_choices(value["strategies"], {"scanner.timing_choice": "original"}, spec)
        == value["strategies"]
    )
    original = deepcopy(value)
    result, saved = execute(value, Engine(value))
    assert len(saved["trials"]) == 128
    assert saved["simulations"] <= 320
    assert all(row["params"]["scanner.timing_choice"] == "original" for row in saved["trials"])
    assert result["automatic_recipe"]["baseline"] == original["automatic_recipe"]["baseline"]
    from research.study_analysis import reconstruct_study

    assert result["experiment"]["timing_research"] == spec
    study, _ = reconstruct_study(result["experiment"])
    assert study.trials[0].distributions["scanner.timing_choice"].choices == ("original",)
    assert value == original


def test_minute_independent_clock_probes_and_off_low_high_screens_fit_budget():
    value = prepared(minute=True, count=2)
    spec = timing.descriptor(value, strict=True)
    _, axes = _prepare(value["strategies"])
    axes.update(trailing_axes(value["automatic_recipe"]["trailing_research"]))
    axes.update(timing.axes(spec))
    screens = _screening(catalog(), axes, policy=policy_for_version(TIMING_VERSION))
    assert len(screens) == 96
    assert all(row["s0.timing_choice"] == row["s1.timing_choice"] == "original" for row in screens)
    probes = timing.probes(axes, spec)
    assert len(probes) == 6
    for row in probes:
        assert sum(row[f"s{i}.timing_choice"] != "original" for i in range(2)) == 1
    assert len(screens) + len(probes) <= 120
    largest = {
        **spec,
        "strategies": [{**deepcopy(spec["strategies"][0]), "id": f"s{i}"} for i in range(8)],
    }
    largest_axes = timing.axes(largest)
    largest_probes = timing.probes(largest_axes, largest)
    assert len(largest_probes) == 24
    assert 128 - len(screens) - len(largest_probes) == 8
    assert all(
        sum(row[f"s{i}.timing_choice"] != "original" for i in range(8)) == 1
        for row in largest_probes
    )
    original = deepcopy(value["strategies"])
    chosen = timing.apply_choices(
        value["strategies"],
        {"s0.timing_choice": "entry_1015", "s1.timing_choice": "exit_1515"},
        spec,
    )
    assert chosen[0]["config"]["entry_time"] == "10:15"
    assert chosen[1]["config"]["exit_time"] == "15:15"
    assert value["strategies"] == original
    neighbors = timing.stability_neighbors(
        _settings(chosen),
        value["automatic_recipe"]["ranges"],
        spec,
        value["automatic_recipe"]["trailing_research"],
    )
    assert neighbors["neighbors"][0]["axis"] == "timing_choice"
    assert any(row["axis"] == "trailing_enabled" for row in neighbors["neighbors"][:4])


def test_minute_run_pause_resume_preserves_clock_parameters_and_bounded_calculations():
    value = prepared(minute=True)
    original = deepcopy(value)
    reference = Engine(value)

    def engine(rows, *args, **kwargs):
        report = reference(rows, *args, **kwargs)
        if rows[0]["config"].get("entry_time") == "10:15":
            report["summary"]["net_return_pct"] += 50
        return report

    _, partial = execute(value, engine, stop=lambda state: len(state["trials"]) == 100)
    result, saved = execute(value, engine, saved=partial)
    assert saved["trials"][:100] == partial["trials"]
    assert len(saved["trials"]) == 128 and saved["simulations"] <= 320
    assert sum(row["phase"] == "timing_probe" for row in saved["trials"]) == 3
    assert {row["params"]["s0.timing_choice"] for row in saved["trials"]} >= {
        "original",
        "entry_0930",
        "entry_1015",
        "exit_1515",
    }
    assert result["strategies"][0]["config"]["entry_time"] == "10:15"
    assert (
        result["experiment"]["timing_research"] == original["automatic_recipe"]["timing_research"]
    )
    assert result["automatic_recipe"]["baseline"][0]["config"]["entry_time"] is None
    repeated, _ = execute(
        value, lambda *a, **k: pytest.fail("Completed resume recalculated"), saved=saved
    )
    assert repeated == result and value == original


def test_changed_frozen_union_or_prices_rejected_before_engine():
    value = prepared(minute=True)
    value["timing_union"] = _seal(
        {
            **{key: item for key, item in value["timing_union"].items() if key != "id"},
            "opportunities": [],
        }
    )
    with pytest.raises(ValueError, match="cohort|union"):
        timing.descriptor(value, strict=True)


def test_union_period_cohort_common_exclusions_and_purge_retained():
    value = prepared(minute=True)
    spec = timing.descriptor(value, strict=True)
    period = slice_period(value, "search")
    assert period["automatic_recipe"]["timing_research"] == spec
    assert [signal["row"] for signal in period["strategies"][0]["signals"]] == [
        signal["row"] for signal in value["strategies"][0]["signals"]
    ]
    assert period["automatic_period"]["purged_signals"] == 0


def test_missing_minute_descriptor_is_blocked_before_any_score():
    value = prepared(minute=True)
    for key in ("timing_descriptor", "timing_union", "timing_cohort"):
        value.pop(key)
    with pytest.raises(ValueError, match="frozen native timing descriptor"):
        timing.descriptor(value, strict=True)


def test_forged_daily_prices_cannot_silently_reinterpret_timed_signals():
    value = prepared(minute=True)
    for key in ("timing_descriptor", "timing_union", "timing_cohort"):
        value.pop(key)
    value["snapshot"]["provenance"]["interval"] = "D"
    with pytest.raises(ValueError, match="source-required interval"):
        timing.descriptor(value, strict=True)


def test_later_clock_maximum_deadline_purges_original_and_candidate_together():
    from test_timing_search import calendar

    value = source(
        config={"trade_horizon": "multiday", "hold_sessions": 2, "hold_minutes": 1804},
        times=("09:15:30",),
    )
    value = frozen(value, calendar())
    value["timing_cohort"] = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    windows = value["timing_union"]["opportunities"][0]["windows"]
    assert windows["original"]["deadline"][:10] == "2026-01-06"
    assert windows["entry_1015"]["deadline"][:10] == "2026-01-07"
    rows, purged = timing.period_strategies(value, "2026-01-05", "2026-01-06", "check1")
    assert purged == 1
    assert "Maximum timing union" in rows[0]["signals"][0]["research_exclusion"]
    assert not value["strategies"][0]["signals"][0].get("research_exclusion")


def test_minute_no_effect_domains_keep_original_and_require_no_union():
    from research.portfolio_coverage import prepare
    from research.timing_search import propose

    value = prepared(minute=True)
    for key in ("automatic_recipe", "timing_descriptor", "timing_union", "timing_cohort"):
        value.pop(key)
    value["portfolio"]["strategies"][0]["config"]["hold_minutes"] = 2
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["strategies"][0].update(deepcopy(value["portfolio"]["strategies"][0]))
    for signal in value["strategies"][0]["signals"]:
        signal["timestamp"] = signal["date"] + "T14:30:00+05:30"
    value["signals"] = [
        dict(signal, strategy_id="s0") for signal in value["strategies"][0]["signals"]
    ]
    value = prepare(value)
    value["timing_descriptor"] = propose(value["portfolio"], value["strategies"], value["snapshot"])
    spec = timing.descriptor(value, strict=True)
    assert value["timing_descriptor"]["status"] == "not_applicable"
    assert spec["union_id"] is None and spec["cohort_id"] is None
    assert timing.axes(spec)["s0.timing_choice"]["choices"] == ["original"]


@pytest.mark.parametrize("original", [{}, {"entry_time": "09:30"}])
def test_registered_neighbors_reset_absent_and_partial_original_clocks(original):
    from research.automatic_trailing import descriptor as trailing_descriptor
    from research.engine import validate_config

    cfg = validate_config({"hold_sessions": 1, "trailing_pct": 2, **original})
    if original:
        cfg.pop("exit_time")
    settings = [{"id": "s", "name": "Scanner", "allocation_pct": 100, "config": cfg}]
    spec = {
        "strategies": [
            {
                "id": "s",
                "original": original,
                "branches": [
                    {"id": "original", "label": "Original timing", "changes": {}},
                    {
                        "id": "entry_1015",
                        "label": "Entry from 10:15",
                        "changes": {"entry_time": "10:15"},
                    },
                    {
                        "id": "exit_1515",
                        "label": "Exit by 15:15",
                        "changes": {"exit_time": "15:15"},
                    },
                ],
            }
        ]
    }
    chosen = timing.apply_choices(settings, {"s.timing_choice": "entry_1015"}, spec)
    assert chosen[0]["config"] == validate_config(chosen[0]["config"])
    assert timing.apply_choices(chosen, {"s.timing_choice": "original"}, spec) == settings
    result = timing.stability_neighbors(
        chosen, [{"id": "s", "search": {}}], spec, trailing_descriptor(settings)
    )
    clock_neighbors = {
        row["to"]: row for row in result["neighbors"] if row["axis"] == "timing_choice"
    }
    assert "original" in clock_neighbors
    assert clock_neighbors["original"]["strategies"] == settings
    exit_cfg = clock_neighbors["exit_1515"]["strategies"][0]["config"]
    assert exit_cfg["entry_time"] == original.get("entry_time")
    assert exit_cfg["exit_time"] == "15:15"


def test_csv_only_minute_candidate_configuration_matches_native_vectorbt_report():
    from test_timing_search import calendar

    from research.connectors.vectorbt_portfolio import evaluate
    from research.engine import validate_config

    value = source(times=("09:15:30",))
    cfg = validate_config({"initial_capital": 10000, "hold_sessions": 1, "order_size_pct": 25})
    value["portfolio"]["strategies"][0]["config"] = cfg
    value["strategies"][0]["config"] = deepcopy(cfg)
    value = frozen(value, calendar())
    value["timing_cohort"] = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    spec = timing.descriptor(value, strict=True)
    assert spec["strategies"][0]["original"] == {}
    selected = timing.apply_choices(value["strategies"], {"s0.timing_choice": "entry_1015"}, spec)
    report = evaluate(selected, value["snapshot"], 10000)
    assert report["strategies"][0]["config"] == selected[0]["config"]
    assert report["ledger"][0]["entry_timestamp"].endswith("T10:15:00+05:30")
    assert (
        timing.apply_choices(selected, {"s0.timing_choice": "original"}, spec)[0]["config"] == cfg
    )
