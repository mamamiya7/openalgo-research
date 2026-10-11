# ruff: noqa: F811 -- imported pytest fixtures
"""V7 native acquisition, durable recovery and frozen replay integration."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_automatic_timing import prepared
from test_timing_union_integration import archive, no_history, seed, store  # noqa: F401

from research.portfolio import execution_versions
from research.report_contract import settings_identity
from services import research_portfolio

pytestmark = pytest.mark.timeout(180)


def _source():
    value = prepared(minute=True)
    value["versions"] = execution_versions(value["portfolio"])
    calendar = {
        key: deepcopy(value["snapshot"][key]) for key in ("sessions", "session_hours", "provenance")
    }
    calendar["bars"] = {}
    plan = deepcopy(value["timing_union"])
    for key in ("timing_descriptor", "timing_union", "timing_cohort", "automatic_recipe"):
        value.pop(key, None)
    value["snapshot"] = {"sessions": [], "bars": {}, "provenance": {"interval": "1m"}}
    return value, calendar, plan


def test_v7_native_union_once_resumes_without_broker_and_preserves_source(
    store, archive, monkeypatch
):
    value, calendar, plan = _source()
    original = deepcopy(value)
    seed(archive, plan)
    calendars = []

    def native(*args, **kwargs):
        calendars.append(kwargs)
        assert kwargs["completion_check"](calendar["sessions"], calendar["session_hours"])
        return deepcopy(calendar)

    monkeypatch.setattr("services.research_native_calendar.native_calendar_snapshot", native)
    monkeypatch.setattr("research.calendar_coverage.check_saved_calendar", lambda *a: None)
    monkeypatch.setattr("services.research_sources.resolve_broker_session", no_history)
    monkeypatch.setattr("services.research_native_prices.native_history", no_history)
    acquisitions = []
    from services.research_native_prices import acquire_native_prices

    def counted(*args, **kwargs):
        acquisitions.append(args[1]["id"])
        return acquire_native_prices(*args, **kwargs)

    monkeypatch.setattr("services.research_native_prices.acquire_native_prices", counted)
    checkpoints = []
    options = {
        "checkpoint": lambda state, counts: checkpoints.append((deepcopy(state), counts)),
        "progress": lambda *args: None,
        "cancelled": lambda: None,
    }
    result = research_portfolio._prepare_automatic_timing(
        store, "owner", value, saved=None, **options
    )
    assert acquisitions == [plan["id"]]
    assert len(calendars) == 1
    assert result["timing_cohort"]["counts"]["eligible"] == 25
    state, counts = checkpoints[-1]
    assert state["phase"] == "timing_union_ready"
    assert counts["completed"] == 60
    resumed = research_portfolio._prepare_automatic_timing(
        store, "owner", value, saved=state, **options
    )
    assert resumed == result
    assert acquisitions == [plan["id"]]
    assert len(calendars) == 1
    assert value == original


def test_daily_v7_keeps_regular_daily_preparation(monkeypatch):
    value = prepared()
    seen = []
    monkeypatch.setattr(
        research_portfolio, "_prepare_prices", lambda *a, **k: seen.append(a[2]) or value
    )
    monkeypatch.setattr("services.research_timing_prices.prepare", no_history)
    monkeypatch.setattr("services.research_native_calendar.native_calendar_snapshot", no_history)
    assert (
        research_portfolio._prepare_automatic_timing(
            None,
            "owner",
            value,
            saved=None,
            checkpoint=no_history,
            progress=no_history,
            cancelled=lambda: None,
        )
        is value
    )
    assert seen == [value]


def test_minute_no_effect_clock_inputs_keep_original_only_descriptor(monkeypatch):
    from research.automatic_protocol import automatic_portfolio

    value, calendar, _ = _source()
    value["portfolio"]["strategies"][0]["config"]["hold_minutes"] = 30
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["strategies"][0].update(deepcopy(value["portfolio"]["strategies"][0]))
    for row in value["strategies"]:
        for signal in row["signals"]:
            signal["timestamp"] = signal["date"] + "T11:00:30+05:30"
    value["signals"] = [
        dict(s, strategy_id=row["id"]) for row in value["strategies"] for s in row["signals"]
    ]
    # Existing hold-minute deadline finishes well before the alternative exit.
    monkeypatch.setattr(
        "services.research_native_calendar.native_calendar_snapshot", lambda *a, **k: calendar
    )
    monkeypatch.setattr("services.research_timing_prices.prepare", no_history)
    monkeypatch.setattr(
        research_portfolio, "_prepare_prices", lambda *a, **k: {**value, "snapshot": calendar}
    )
    result = research_portfolio._prepare_automatic_timing(
        None,
        "owner",
        value,
        saved=None,
        checkpoint=no_history,
        progress=no_history,
        cancelled=lambda: None,
    )
    assert result["timing_descriptor"]["status"] == "not_applicable"
    assert result["timing_descriptor"]["strategies"][0]["branches"] == [
        {"id": "original", "label": "Original timing", "changes": {}}
    ]


def test_exact_v7_candidate_replay_keeps_registered_clock_and_union_receipt(monkeypatch):
    from research.automatic_protocol import compile_recipe

    value = prepared(minute=True)
    value["versions"] = execution_versions(value["portfolio"])
    value["automatic_recipe"] = compile_recipe(value)
    original = deepcopy(value)
    selected = deepcopy(value["portfolio"]["strategies"])
    selected[0]["config"]["entry_time"] = "10:15"
    selected[0]["config"].update(trailing_enabled=False, trailing_pct=0)
    config_id = settings_identity(selected)
    result = {
        "strategies": selected,
        "automatic_research": {"version": "automatic-hypotheses-v7"},
        "experiment": {
            "rows": [{"config_id": config_id, "trial_number": 98, "strategies": selected}]
        },
    }
    job = SimpleNamespace(id="b" * 32, status="completed", result_artifact="c" * 64)
    bundle = {"kind": "portfolio_optimize", "inputs_artifact": "d" * 64, "result": result}
    monkeypatch.setattr(research_portfolio.service, "get_job", lambda *a: job)
    monkeypatch.setattr(
        research_portfolio.service,
        "read_artifact",
        lambda _, ref: deepcopy(bundle if ref == job.result_artifact else value),
    )
    _, replay = research_portfolio.replay_inputs(None, "owner", job.id, trial_id=config_id)
    assert replay["frozen_prices"] is True
    assert replay["strategies"][0]["config"]["entry_time"] == "10:15"
    assert replay["timing_cohort"] == original["timing_cohort"]
    assert replay["timing_union"] == original["timing_union"]
    assert replay["automatic_stock_market_context"] == original["automatic_stock_market_context"]
    assert settings_identity(replay["portfolio"]["strategies"]) == config_id
    assert value == original


def test_v7_native_worker_uses_joint_context_and_keeps_ready_receipts(monkeypatch):
    from research import automatic_hypotheses
    from research.portfolio import fingerprint

    value = prepared(minute=True)
    value["versions"] = execution_versions(value["portfolio"])
    from research.automatic_protocol import compile_recipe

    value["automatic_recipe"] = compile_recipe(value)
    original = deepcopy(value)
    calls, artifacts = [], {}

    def context(_store, _owner, evidence, **kwargs):
        calls.append("context")
        assert kwargs["saved"] is None
        return evidence

    def calculate(evidence, **kwargs):
        calls.append("calculate")
        assert evidence["timing_cohort"] == original["timing_cohort"]
        assert evidence["automatic_recipe"]["timing_research"]["interval"] == "1m"
        return {"automatic_research": {"version": "automatic-hypotheses-v7"}}

    def save(_, evidence):
        identifier = fingerprint(evidence)
        artifacts[identifier] = deepcopy(evidence)
        return identifier

    monkeypatch.setattr(research_portfolio, "_prepare_automatic_timing", lambda *a, **k: value)
    monkeypatch.setattr("services.research_stock_market_context.prepare_context", context)
    monkeypatch.setattr(
        "services.research_stock_features.prepare_context",
        lambda *a, **k: pytest.fail("V7 needs joint context"),
    )
    monkeypatch.setattr(automatic_hypotheses, "run", calculate)
    monkeypatch.setattr(research_portfolio.service, "save_artifact", save)
    result, retained = research_portfolio.run(
        None,
        "owner",
        value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        job_id="e" * 32,
        checkpoint=lambda *a: None,
        progress=lambda *a: None,
        cancelled=lambda: None,
    )
    assert calls == ["context", "calculate"]
    assert result["automatic_research"]["version"] == "automatic-hypotheses-v7"
    assert retained["timing_union"] == original["timing_union"]
    assert artifacts


def test_v7_paused_inspection_retains_clock_and_joint_input_binding(monkeypatch):
    from test_automatic_hypotheses import Engine, Stopped

    from research.automatic_hypotheses import run
    from services import research_partial_study

    value = prepared(minute=True)
    engine = Engine(value)
    checkpoints = []

    def stop(state, counts):
        checkpoints.append(deepcopy(state))
        if len(state["trials"]) == 3:
            raise Stopped

    with pytest.raises(Stopped):
        run(
            value,
            evaluate=engine,
            checkpoint=stop,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    saved = checkpoints[-1]
    calls = deepcopy(engine.calls)
    evidence, checked, scope, automatic, completed = research_partial_study._search_inputs(
        value, saved
    )
    rows, counts = research_partial_study._validated_rows(evidence, checked, completed)
    assert scope == "search" and automatic and not completed
    assert counts["proposed"] == 3
    assert rows and "entry_time" in rows[0]["strategies"][0]["config"]
    assert engine.calls == calls


def test_recorded_timing_axes_need_their_exact_frozen_contract():
    from test_study_analysis import trailing_experiment

    from research.automatic_timing import VERSION
    from research.study_analysis import reconstruct_study

    value = trailing_experiment()
    value["experiment_version"] = "automatic-hypotheses-v7"
    value["timing_research"] = {
        "version": VERSION,
        "interval": "1m",
        "strategies": [
            {
                "id": "a",
                "original": {"entry_time": None, "exit_time": None},
                "branches": [
                    {"id": "original", "label": "Original timing", "changes": {}},
                    {
                        "id": "entry_1015",
                        "label": "Entry from 10:15",
                        "changes": {"entry_time": "10:15"},
                    },
                ],
            }
        ],
    }
    value["search_space"]["axes"]["a.timing_choice"] = {
        "type": "categorical",
        "choices": ["original", "entry_1015"],
    }
    for trial in value["trials"]:
        trial["params"]["a.timing_choice"] = "original"
    study, axes = reconstruct_study(value)
    assert study.trials[0].distributions["a.timing_choice"].choices == ("original", "entry_1015")
    tampered = deepcopy(value)
    tampered["search_space"]["axes"]["a.timing_choice"]["choices"].append("entry_1200")
    with pytest.raises(ValueError, match="timing parameters differ"):
        reconstruct_study(tampered)
    value.pop("timing_research")
    with pytest.raises(ValueError, match="timing parameters need"):
        reconstruct_study(value)


@pytest.mark.parametrize("choice", ["original", "entry_0930", "entry_1015", "exit_1515"])
def test_operational_capture_accepts_bounded_native_clock_states(choice):
    import json

    from services.research_study_activity import _params

    value = {"scanner.timing_choice": choice, "scanner.target_pct": 5}
    assert json.loads(_params(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        {"scanner.timing_choice": "entry_2400"},
        {"scanner.timing_choice": "entry_1060"},
        {"scanner.timing_choice": "new_1015"},
        {"scanner.timing_choice": "10:15"},
        {"scanner.target_pct": "entry_1015"},
        {"scanner.timing_choice": True},
        {"scanner..timing_choice": "original"},
        {"x" * 65 + ".timing_choice": "original"},
    ],
)
def test_operational_capture_rejects_unregistered_grammar_and_non_native_values(value):
    from services.research_study_activity import _params

    with pytest.raises(ValueError, match="study activity"):
        _params(value)


def test_daily_v7_recorded_analysis_keeps_absent_clocks_absent():
    from test_study_analysis import trailing_experiment

    from research.automatic_timing import VERSION
    from research.study_analysis import reconstruct_study

    value = trailing_experiment()
    value["experiment_version"] = "automatic-hypotheses-v7"
    value["timing_research"] = {
        "version": VERSION,
        "interval": "D",
        "strategies": [
            {
                "id": "a",
                "original": {},
                "branches": [{"id": "original", "label": "Original timing", "changes": {}}],
            }
        ],
    }
    value["search_space"]["axes"]["a.timing_choice"] = {
        "type": "categorical",
        "choices": ["original"],
    }
    for trial in value["trials"]:
        trial["params"]["a.timing_choice"] = "original"
    study, axes = reconstruct_study(value)
    assert study.trials[0].distributions["a.timing_choice"].choices == ("original",)
    assert value["timing_research"]["strategies"][0]["original"] == {}
