"""Operational trial observations never change exact Optuna scientific evidence."""

# ruff: noqa: F811 -- isolated shared pytest fixtures
import copy
import json

import pytest
from test_optuna_portfolio import engine, search_request  # noqa: F401

from research.connectors.optuna_portfolio import run_search


@pytest.mark.parametrize("sampler", ["grid", "tpe"])
def test_observing_native_search_preserves_report_and_checkpoint_bytes(search_request, sampler):
    search_request["specification"].update(sampler=sampler, trials=25)
    search_request["strategies"][0]["search"]["allocation_pct"] = {"min": 0, "max": 100, "step": 25}
    expected_checkpoints, actual_checkpoints, events = [], [], []
    expected = run_search(
        **search_request,
        evaluate=engine,
        checkpoint=lambda state, counts: expected_checkpoints.append((state, counts)),
    )
    actual = run_search(
        **search_request,
        evaluate=engine,
        observe=events.append,
        checkpoint=lambda state, counts: actual_checkpoints.append((state, counts)),
    )
    assert actual == expected
    assert json.dumps(actual_checkpoints, sort_keys=True, allow_nan=False) == json.dumps(
        expected_checkpoints, sort_keys=True, allow_nan=False
    )
    trials = actual["experiment"]["trials"]
    assert events[0] == {"kind": "search_started", "proposal_budget": len(trials), "replayed": 0}
    assert len(events) == 1 + 2 * len(trials)
    for trial, start, finish in zip(trials, events[1::2], events[2::2], strict=True):
        assert start == {
            "kind": "proposal_started",
            "number": trial["number"],
            "config_id": trial["config_id"],
            "params": trial["params"],
            "reused": trial["reused"],
        }
        assert finish == {
            "kind": "proposal_finished",
            "number": trial["number"],
            "state": trial["state"],
            "value": trial["value"],
            "reused": trial["reused"],
        }


def test_resume_records_only_fresh_proposals_and_preserves_adaptive_sequence(search_request):
    search_request["specification"].update(sampler="tpe", trials=19)
    checkpoints = []
    expected = run_search(
        **search_request, evaluate=engine, checkpoint=lambda state, _: checkpoints.append(state)
    )
    for completed in (1, 12, 19):
        events = []
        actual = run_search(
            **search_request,
            evaluate=engine,
            saved=checkpoints[completed - 1],
            observe=events.append,
        )
        assert actual == expected
        assert events[0] == {"kind": "search_started", "proposal_budget": 19, "replayed": completed}
        assert [event["number"] for event in events if event["kind"] == "proposal_started"] == list(
            range(completed, 19)
        )


def test_known_active_parameters_survive_engine_failure_without_invented_score(search_request):
    events = []

    def fail(*args, **kwargs):
        assert events[-1]["kind"] == "proposal_started"
        raise RuntimeError("controlled engine failure")

    with pytest.raises(RuntimeError, match="controlled engine failure"):
        run_search(**search_request, evaluate=fail, observe=events.append)
    assert [event["kind"] for event in events] == ["search_started", "proposal_started"]
    assert events[-1]["number"] == 0 and events[-1]["params"]
    assert "value" not in events[-1]


def test_observer_cannot_mutate_parameters_or_scientific_checkpoint(search_request):
    def mutate(event):
        if event["kind"] == "proposal_started":
            event["params"].clear()
        event.clear()

    expected = run_search(**search_request, evaluate=engine)
    assert run_search(**search_request, evaluate=engine, observe=mutate) == expected


def test_rejected_replay_never_announces_a_new_execution(search_request):
    checkpoints = []
    run_search(
        **search_request, evaluate=engine, checkpoint=lambda state, _: checkpoints.append(state)
    )
    saved = copy.deepcopy(checkpoints[0])
    saved["binding"] = "changed"
    events = []
    with pytest.raises(ValueError, match="checkpoint inputs"):
        run_search(**search_request, evaluate=engine, saved=saved, observe=events.append)
    assert events == []


def test_finish_is_observed_before_checkpoint_publication_failure(search_request):
    events = []

    def unavailable_checkpoint(*args):
        assert events[-1]["kind"] == "proposal_finished"
        raise OSError("controlled checkpoint publication failure")

    with pytest.raises(OSError, match="controlled checkpoint"):
        run_search(
            **search_request,
            evaluate=engine,
            checkpoint=unavailable_checkpoint,
            observe=events.append,
        )
    assert events[-1]["state"] == "complete"
    assert isinstance(events[-1]["value"], (int, float))


def test_start_capture_failure_never_claims_a_failed_engine_calculation(search_request):
    activity = []

    def unavailable(event):
        if event["kind"] == "proposal_started":
            raise OSError("controlled activity storage failure")

    with pytest.raises(OSError, match="activity storage failure"):
        run_search(
            **search_request,
            evaluate=lambda *a, **k: pytest.fail("Unobserved calculation started"),
            observe=unavailable,
            activity=activity.append,
        )
    assert all(event["trials"]["failed"] == 0 for event in activity)
