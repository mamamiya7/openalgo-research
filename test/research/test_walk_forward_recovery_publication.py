"""Verified fit recovery keeps live boundaries without republishing old evidence."""

from copy import deepcopy

import pytest
from test_automatic_walk_forward import Stopped
from test_execution_aware_research import ExecutionAccountEngine, prepared
from test_expanded_walk_forward import synthetic_prepare_method_gate

from research import automatic_walk_forward as worker
from research.connectors.optuna_portfolio import MAX_CHECKPOINT_BYTES, _fingerprint


@pytest.fixture(autouse=True)
def isolate_membership(monkeypatch):
    monkeypatch.setattr(worker, "prepare_method_gate", synthetic_prepare_method_gate)


def run(value, engine, checkpoint, *, saved=None, boundary=None):
    return worker.run(
        value,
        evaluate=engine,
        saved=saved,
        checkpoint=lambda state, _counts: checkpoint(state),
        progress=lambda *_: None,
        cancelled=lambda: None,
        boundary=boundary,
        defer_study_analysis=True,
    )


def seal(state):
    state["checkpoint_id"] = _fingerprint(
        {key: value for key, value in state.items() if key != "checkpoint_id"},
        max_bytes=MAX_CHECKPOINT_BYTES,
    )
    return state


@pytest.fixture(scope="module")
def completed_fit():
    value = prepared()
    engine = ExecutionAccountEngine(value)
    checkpoints = []

    def checkpoint(state):
        if state["fits"].get("fit1", {}).get("sizing_control") is not None:
            checkpoints.append(state)
            raise Stopped()

    # Module fixtures run before the function-scoped gate patch.
    from unittest.mock import patch

    with patch.object(worker, "prepare_method_gate", synthetic_prepare_method_gate):
        with pytest.raises(Stopped):
            run(value, engine, checkpoint)
    saved = checkpoints[-1]
    assert len(saved["fits"]["fit1"]["trials"]) == 64
    assert saved["fits"]["fit1"]["selection"] is not None
    assert not saved["folds"]
    return value, saved, engine.calls


def test_verified_fit_replay_publishes_only_the_next_new_attempt(completed_fit):
    value, saved, calls = completed_fit
    original = deepcopy(saved)
    publications, boundaries = [], []

    def checkpoint(state):
        publications.append(state)
        # The first new next-window account retains its pre-engine reservation.
        assert state["attempts"] == saved["attempts"] + 1
        assert state["simulations"] == saved["simulations"]
        assert state["fits"]["fit1"] == saved["fits"]["fit1"]
        raise Stopped()

    with pytest.raises(Stopped):
        run(
            value,
            lambda *_args, **_kwargs: pytest.fail("Reservation must precede the next engine"),
            checkpoint,
            saved=saved,
            boundary=lambda: boundaries.append(True),
        )
    assert len(publications) == 1
    assert len(boundaries) >= 66  # fit entry, every replayed proposal, next account
    assert saved == original
    assert len(calls) == saved["simulations"]


def test_pause_during_verified_replay_never_republishes_or_calls_engine(completed_fit):
    value, saved, _calls = completed_fit
    original = deepcopy(saved)
    boundaries = 0

    def boundary():
        nonlocal boundaries
        boundaries += 1
        if boundaries == 10:
            raise Stopped()

    with pytest.raises(Stopped):
        run(
            value,
            lambda *_args, **_kwargs: pytest.fail("Replay must not call the engine"),
            lambda _state: pytest.fail("Verified replay must not publish"),
            saved=saved,
            boundary=boundary,
        )
    assert boundaries == 10
    assert saved == original


def test_resumed_next_fold_matches_uninterrupted_checkpoint_and_engine_calls(completed_fit):
    value, saved, previous_calls = completed_fit

    def first_fold(engine, saved=None):
        final = []

        def checkpoint(state):
            if state["folds"]:
                final.append(state)
                raise Stopped()

        with pytest.raises(Stopped):
            run(value, engine, checkpoint, saved=saved)
        assert len(final) == 1
        return final[0]

    resumed_engine, uninterrupted_engine = (
        ExecutionAccountEngine(value),
        ExecutionAccountEngine(value),
    )
    resumed = first_fold(resumed_engine, saved)
    uninterrupted = first_fold(uninterrupted_engine)
    assert resumed == uninterrupted
    assert previous_calls + resumed_engine.calls == uninterrupted_engine.calls


@pytest.mark.parametrize("field", ["selection", "sizing_control"])
def test_verified_recovery_still_rejects_changed_choice_or_control(completed_fit, field):
    value, saved, _calls = completed_fit
    changed = deepcopy(saved)
    key = "score" if field == "selection" else "score_delta"
    changed["fits"]["fit1"][field][key] += 1
    seal(changed)
    with pytest.raises(ValueError, match="selection changed|sizing.*control changed"):
        run(
            value,
            lambda *_args, **_kwargs: pytest.fail("Changed evidence must not call the engine"),
            lambda _state: pytest.fail("Changed evidence must not publish"),
            saved=changed,
        )


@pytest.mark.parametrize("field", ["selection", "sizing_control"])
def test_missing_choice_or_control_is_published_before_new_engine_work(completed_fit, field):
    value, saved, _calls = completed_fit
    partial = deepcopy(saved)
    partial["fits"]["fit1"][field] = None
    seal(partial)
    publications = []

    def checkpoint(state):
        publications.append(state)
        assert state == saved
        raise Stopped()

    with pytest.raises(Stopped):
        run(
            value,
            lambda *_args, **_kwargs: pytest.fail("Saved account must be reused"),
            checkpoint,
            saved=partial,
        )
    assert len(publications) == 1
