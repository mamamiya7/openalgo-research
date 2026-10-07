"""Frozen Optuna searches may gain a bounded budget without repeating simulations."""

import copy
import json

import pytest
from test_optuna_portfolio import engine
from test_optuna_portfolio import search_request as search_request

from research.connectors.optuna_portfolio import (
    SEARCH_IDENTITY_VERSION,
    _fingerprint,
    run_search,
    validate_checkpoint,
)


def saved_run(request, *, evaluate=engine, **kwargs):
    checkpoints = []
    result = run_search(
        **request,
        evaluate=evaluate,
        checkpoint=lambda state, _: checkpoints.append(state),
        **kwargs,
    )
    return result, checkpoints[-1]


def widen(request, sampler):
    request["specification"].update(sampler=sampler, trials=13)
    request["strategies"][0]["search"]["target_pct"] = {"min": 1, "max": 40, "step": 1}
    request["strategies"][0]["search"]["allocation_pct"] = {"min": 0, "max": 100, "step": 25}


def legacy(checkpoint):
    saved = copy.deepcopy(checkpoint)
    for key in ("search_identity_version", "search_identity", "specification", "proposal_budget"):
        saved.pop(key)
    resign(saved)
    return saved


def resign(saved):
    saved["checkpoint_id"] = _fingerprint(
        {key: value for key, value in saved.items() if key != "checkpoint_id"}
    )


@pytest.mark.parametrize("sampler", ["tpe", "grid"])
@pytest.mark.parametrize("legacy_checkpoint", [False, True])
def test_extension_matches_one_shot_without_repeating_prior_simulations(
    search_request, sampler, legacy_checkpoint
):
    widen(search_request, sampler)
    evaluated = []

    def calculate(strategies, *args, **kwargs):
        identity = _fingerprint(
            [
                {key: item[key] for key in ("id", "name", "config", "allocation_pct")}
                for item in strategies
            ]
        )
        assert identity not in evaluated, "Expensive portfolio simulation repeated"
        evaluated.append(identity)
        return engine(strategies, *args, **kwargs)

    original_spec = copy.deepcopy(search_request["specification"])
    parent, saved = saved_run(search_request, evaluate=calculate)
    original_saved = copy.deepcopy(saved)
    original_report = copy.deepcopy(parent)
    initial_calls = len(evaluated)
    search_request["specification"]["trials"] = 29
    expected, expected_checkpoint = saved_run(search_request)
    observations = []
    actual, final_checkpoint = saved_run(
        search_request,
        saved=json.loads(json.dumps(legacy(saved) if legacy_checkpoint else saved)),
        continuation_from=original_spec,
        evaluate=calculate,
        observe=observations.append,
    )
    assert actual == expected
    assert final_checkpoint == expected_checkpoint
    assert saved == original_saved and parent == original_report
    assert len(evaluated) == len(expected["experiment"]["rows"])
    assert final_checkpoint["binding"] != saved["binding"]
    assert final_checkpoint["search_identity"] == saved["search_identity"]
    assert final_checkpoint["search_identity_version"] == SEARCH_IDENTITY_VERSION
    assert observations[0]["kind"] == "search_started"
    assert observations[0]["replayed"] == 13
    assert observations[0]["restored_evaluations"] == initial_calls
    started = [event for event in observations if event["kind"] == "proposal_started"]
    assert [event["number"] for event in started] == list(range(13, 29))
    finished = [event for event in observations if event["kind"] == "proposal_finished"]
    assert sum(event["state"] == "complete" and not event["reused"] for event in finished) == (
        len(evaluated) - initial_calls
    )


@pytest.mark.parametrize("sampler", ["tpe", "grid"])
def test_interrupted_extension_resumes_its_own_checkpoint_exactly(search_request, sampler):
    widen(search_request, sampler)
    previous_spec = copy.deepcopy(search_request["specification"])
    _, parent_checkpoint = saved_run(search_request)
    search_request["specification"]["trials"] = 29
    expected, expected_checkpoint = saved_run(search_request)
    checkpoints = []

    def pause(done, total):
        if done >= 18:
            raise InterruptedError("controlled pause")

    with pytest.raises(InterruptedError, match="controlled pause"):
        run_search(
            **search_request,
            saved=parent_checkpoint,
            continuation_from=previous_spec,
            evaluate=engine,
            progress=pause,
            checkpoint=lambda state, _: checkpoints.append(state),
        )
    child_checkpoint = checkpoints[-1]
    assert len(child_checkpoint["trials"]) == 18
    assert child_checkpoint["proposal_budget"] == 29
    actual, final_checkpoint = saved_run(search_request, saved=child_checkpoint)
    assert actual == expected and final_checkpoint == expected_checkpoint


@pytest.mark.parametrize("sampler", ["tpe", "grid"])
def test_old_checkpoint_still_resumes_without_new_metadata(search_request, sampler):
    widen(search_request, sampler)
    result, saved = saved_run(search_request)
    old = legacy(saved)
    assert validate_checkpoint(old) == {
        "completed": 13,
        "evaluated": len(result["experiment"]["rows"]),
        "search_identity": None,
        "specification": None,
        "proposal_budget": None,
    }
    resumed = run_search(
        **search_request,
        saved=old,
        evaluate=lambda *a, **k: pytest.fail("A saved configuration must not be recalculated"),
    )
    assert resumed == result


@pytest.mark.parametrize(
    "change",
    ["objective", "seed", "sampler", "axis", "config", "signals", "capital", "prices", "engine"],
)
def test_continuation_rejects_every_changed_scientific_input_before_evaluation(
    search_request, change
):
    widen(search_request, "tpe")
    _, saved = saved_run(search_request)
    previous_spec = copy.deepcopy(search_request["specification"])
    search_request["specification"]["trials"] = 29
    if change in ("objective", "seed", "sampler"):
        search_request["specification"][change] = {
            "objective": "return",
            "seed": 20,
            "sampler": "grid",
        }[change]
    elif change == "axis":
        search_request["strategies"][0]["search"]["target_pct"]["max"] = 41
    elif change == "config":
        search_request["strategies"][0]["config"]["cost_bps"] += 1
    elif change == "signals":
        search_request["strategies"][0]["signals"][0]["date"] = "2026-01-06"
    elif change == "capital":
        search_request["capital"] += 1
    elif change == "prices":
        search_request["snapshot"]["bars"]["FIRST"]["2026-01-06"]["close"] += 1
    else:
        search_request["execution"]["engine_version"] = "2.0"
    with pytest.raises(ValueError, match="scientific|inputs, settings or engine versions changed"):
        run_search(
            **search_request,
            saved=saved,
            continuation_from=previous_spec,
            evaluate=lambda *a, **k: pytest.fail("Changed scientific inputs must not evaluate"),
        )


@pytest.mark.parametrize("trials", [1, 12, 13, 1001])
def test_extension_requires_bounded_larger_total_budget(search_request, trials):
    widen(search_request, "tpe")
    _, saved = saved_run(search_request)
    previous_spec = copy.deepcopy(search_request["specification"])
    search_request["specification"]["trials"] = trials
    with pytest.raises(ValueError, match="trial|Search trials"):
        run_search(
            **search_request,
            saved=saved,
            continuation_from=previous_spec,
            evaluate=lambda *a, **k: pytest.fail("Invalid total budget must not evaluate"),
        )


def test_continuation_is_explicit_and_requires_matching_parent_evidence(search_request):
    widen(search_request, "tpe")
    _, saved = saved_run(search_request)
    previous_spec = copy.deepcopy(search_request["specification"])
    search_request["specification"]["trials"] = 29
    with pytest.raises(ValueError, match="saved checkpoint"):
        run_search(**search_request, continuation_from=previous_spec, evaluate=engine)
    with pytest.raises(ValueError, match="inputs, settings or engine versions changed"):
        run_search(**search_request, saved=saved, evaluate=engine)
    previous_spec["trials"] = 12
    with pytest.raises(ValueError, match="inputs, settings or engine versions changed"):
        run_search(**search_request, saved=saved, continuation_from=previous_spec, evaluate=engine)


def test_exhausted_grid_does_not_offer_a_fake_budget_extension(search_request):
    _, saved = saved_run(search_request)
    previous_spec = copy.deepcopy(search_request["specification"])
    search_request["specification"]["trials"] = 100
    with pytest.raises(ValueError, match="no remaining proposals"):
        run_search(**search_request, saved=saved, continuation_from=previous_spec, evaluate=engine)


@pytest.mark.parametrize("damage", ["params", "summary", "ledger", "identity", "budget"])
def test_corrupted_checkpoint_cannot_be_extended(search_request, damage):
    widen(search_request, "tpe")
    _, saved = saved_run(search_request)
    previous_spec = copy.deepcopy(search_request["specification"])
    search_request["specification"]["trials"] = 29
    if damage == "params":
        saved["trials"][0]["params"]["breakout.target_pct"] = 999
    elif damage == "summary":
        saved["rows"][0]["summary"]["closed_trades"] = 999
    elif damage == "ledger":
        saved["winner_report"]["ledger"][0]["status"] = "changed"
    elif damage == "identity":
        saved["search_identity"] = "f" * 64
    else:
        saved["proposal_budget"] = 2
    with pytest.raises(ValueError, match="checkpoint"):
        run_search(
            **search_request,
            saved=saved,
            continuation_from=previous_spec,
            evaluate=lambda *a, **k: pytest.fail("Corrupted saved evidence must not evaluate"),
        )


@pytest.mark.parametrize(
    "damage", ["identity", "metadata", "budget", "sequence", "params", "winner_settings"]
)
def test_rehashed_inconsistent_checkpoint_still_rejected(search_request, damage):
    widen(search_request, "tpe")
    _, saved = saved_run(search_request)
    if damage == "identity":
        saved["search_identity"] = "f" * 64
    elif damage == "metadata":
        saved.pop("search_identity_version")
    elif damage == "budget":
        saved["proposal_budget"] = 12
    elif damage == "sequence":
        saved["trials"][0]["number"] = 1
    elif damage == "winner_settings":
        saved["winner_report"]["strategies"][0]["config"]["cost_bps"] += 1
    else:
        saved["trials"][0]["params"]["breakout.target_pct"] = 2
    resign(saved)
    with pytest.raises(ValueError, match="checkpoint"):
        run_search(
            **search_request,
            saved=saved,
            evaluate=lambda *a, **k: pytest.fail("Inconsistent saved evidence must not evaluate"),
        )
