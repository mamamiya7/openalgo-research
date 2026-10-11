"""Calculation display counts are completed calls, separate from recipe state."""

from copy import deepcopy

import pytest
from test_automatic_hypotheses import Engine, Stopped
from test_joint_stock_market import prepared

from research.automatic_hypotheses import run
from services.research_activity import merge_activity

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(autouse=True)
def isolate_plot_rendering(monkeypatch):
    monkeypatch.setattr(
        "research.study_analysis.build_study_analysis", lambda *a, **k: {"charts": []}
    )


def execute(value, engine, *, activity=None, saved=None):
    checkpoints = []
    result = run(
        value,
        evaluate=engine,
        checkpoint=lambda state, counts: checkpoints.append((deepcopy(state), deepcopy(counts))),
        progress=lambda *a: None,
        cancelled=lambda: None,
        activity=activity,
        saved=saved,
    )
    return result, checkpoints


def test_joint_calculation_activity_reports_later_work_without_changing_exact_evidence():
    value = prepared()
    reference, reference_states = execute(value, Engine(value))
    events = []
    engine = Engine(value)
    actual, states = execute(value, engine, activity=events.append)
    assert actual == reference
    assert states == reference_states
    start = next(event for event in events if event.get("stage") == "validation")
    assert start["trials"]["completed"] == 128
    assert start["calculations"]["completed"] < len(engine.calls)
    counts = [event["calculations"] for event in events if "calculations" in event]
    assert all(row["limit"] == 320 for row in counts)
    assert [row["completed"] for row in counts] == sorted(row["completed"] for row in counts)
    assert counts[0]["completed"] == 0
    assert counts[-1]["completed"] == len(engine.calls) == states[-1][0]["simulations"]
    assert counts[-1]["completed"] < counts[-1]["limit"]
    display = {}
    for event in events:
        display = merge_activity(display, event, 1)
    assert display["stage"] == "analysis"
    assert display["calculations"] == counts[-1]
    # Completed resume exposes the recorded successful calls without new work.
    resumed = []
    repeated, _ = execute(
        value,
        lambda *a, **k: pytest.fail("Completed resume must not recalculate"),
        saved=states[-1][0],
        activity=resumed.append,
    )
    assert repeated == reference
    assert resumed[-1]["stage"] == "analysis"
    assert (
        next(event["calculations"] for event in reversed(resumed) if "calculations" in event)
        == counts[-1]
    )


def test_failed_engine_attempt_is_not_displayed_as_completed_calculation():
    value = prepared()
    events, saved = [], []

    def failure(*args, **kwargs):
        raise Stopped("controlled calculation failure")

    with pytest.raises(Stopped):
        run(
            value,
            evaluate=failure,
            checkpoint=lambda state, counts: saved.append(deepcopy(state)),
            progress=lambda *a: None,
            cancelled=lambda: None,
            activity=events.append,
        )
    assert saved[-1]["attempts"] == 1
    assert saved[-1]["simulations"] == 0
    assert events[-1]["calculations"] == {"completed": 0, "limit": 320}


@pytest.mark.parametrize("value", [-1, True, 1.2, 1_000_000_001])
def test_calculation_activity_uses_the_existing_bounded_counter_contract(value):
    with pytest.raises(ValueError, match="Invalid research activity count"):
        merge_activity({}, {"calculations": {"completed": value}}, 1)
    with pytest.raises(ValueError, match="Invalid research activity count"):
        merge_activity({}, {"calculations": {"limit": value}}, 1)


def test_calculation_updates_preserve_phase_and_ignore_undeclared_internal_fields():
    old = {"stage": "validation", "trials": {"completed": 128}}
    updated = merge_activity(
        old,
        {"calculations": {"completed": 150, "limit": 320, "attempts": 151, "parameters": []}},
        2,
    )
    assert updated["stage"] == "validation"
    assert updated["calculations"] == {"completed": 150, "limit": 320}
    assert old == {"stage": "validation", "trials": {"completed": 128}}
