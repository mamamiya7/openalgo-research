"""Completed fit accounts and proposals share one fenced publication."""

import inspect
from copy import deepcopy

import pytest
from test_automatic_walk_forward import AccountEngine, Stopped, prepared, synthetic_prepare_gate

from research import automatic_walk_forward as worker
from research.connectors.optuna_portfolio import MAX_CHECKPOINT_BYTES, _fingerprint


def prefix(value, engine, *, saved=None, checkpoints=None, events=None, limit=3):
    checkpoints = [] if checkpoints is None else checkpoints
    events = [] if events is None else events

    def checkpoint(state, _counts):
        checkpoints.append(state)
        if len(state.get("fits", {}).get("fit1", {}).get("trials", [])) == limit:
            raise Stopped()

    with pytest.raises(Stopped):
        worker.run(
            value,
            evaluate=engine,
            saved=saved,
            checkpoint=checkpoint,
            progress=lambda *_: None,
            cancelled=lambda: None,
            observe=events.append,
            defer_study_analysis=True,
        )
    return checkpoints[-1]


@pytest.fixture(autouse=True)
def synthetic_membership(monkeypatch):
    monkeypatch.setattr(worker, "prepare_gate", synthetic_prepare_gate)


def test_new_fit_account_and_proposal_are_published_together_and_pause_replays():
    value = prepared()
    engine, checkpoints, events = AccountEngine(value), [], []
    saved = prefix(value, engine, checkpoints=checkpoints, events=events)
    assert saved["simulations"] == len(engine.calls) == 4
    completed = [
        state
        for before, state in zip(checkpoints, checkpoints[1:], strict=False)
        if state["simulations"] > max(1, before["simulations"])
    ]
    assert [len(state["fits"]["fit1"]["trials"]) for state in completed] == [1, 2, 3]
    assert [state["simulations"] for state in completed] == [2, 3, 4]
    assert saved["checkpoint_id"] == _fingerprint(
        {key: item for key, item in saved.items() if key != "checkpoint_id"},
        max_bytes=MAX_CHECKPOINT_BYTES,
    )
    # A publication/pause exception must not announce a finished proposal before
    # its scientific checkpoint commits; the earlier two were already observed.
    assert [event["number"] for event in events if event["kind"] == "proposal_finished"] == [0, 1]
    original = deepcopy(saved)
    replayed = prefix(value, engine, saved=saved, limit=4)
    reference_engine = AccountEngine(value)
    expected = prefix(value, reference_engine, limit=4)
    assert replayed == expected and saved == original
    assert engine.calls == reference_engine.calls


class ConstructionStopped(BaseException):
    pass


@pytest.mark.parametrize("failure", ["score", "copy", "append"])
def test_construction_failure_retains_account_only_and_recovery_reuses_it(monkeypatch, failure):
    value = prepared()
    expected_engine = AccountEngine(value)
    expected = prefix(value, expected_engine)
    engine, checkpoints = AccountEngine(value), []
    score, copy = worker._score, worker.deepcopy
    fired = False

    class FailingAppend(list):
        def append(self, item):
            super().append(item)
            nonlocal fired
            if not fired:
                fired = True
                raise ConstructionStopped()

    def fail_score(summary):
        nonlocal fired
        caller = inspect.currentframe().f_back
        if caller.f_code.co_name == "fit" and len(engine.calls) == 2 and not fired:
            if failure == "score":
                fired = True
                raise ConstructionStopped()
            if failure == "append":
                current = caller.f_locals["current"]
                current["trials"] = FailingAppend(current["trials"])
        return score(summary)

    def fail_copy(item):
        nonlocal fired
        if (
            failure == "copy"
            and not fired
            and len(engine.calls) == 2
            and inspect.currentframe().f_back.f_code.co_name == "fit"
        ):
            fired = True
            raise ConstructionStopped()
        return copy(item)

    monkeypatch.setattr(worker, "_score", fail_score)
    monkeypatch.setattr(worker, "deepcopy", fail_copy)
    with pytest.raises(ConstructionStopped):
        prefix(value, engine, checkpoints=checkpoints)
    assert fired
    saved = checkpoints[-1]
    assert saved["simulations"] == len(engine.calls) == 2
    assert len(saved["evaluations"]) == 2
    assert len(saved["fits"]["fit1"]["trials"]) == 0
    assert len(saved["fits"]["fit1"]["candidates"]) == 0
    worker._validate_state(saved)
    monkeypatch.setattr(worker, "_score", score)
    monkeypatch.setattr(worker, "deepcopy", copy)
    resumed = prefix(value, engine, saved=saved)
    assert resumed == expected
    assert engine.calls == expected_engine.calls
