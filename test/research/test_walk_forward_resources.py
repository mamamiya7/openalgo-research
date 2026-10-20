"""Bounded calculation reservations and prepared-owner cleanup on every exit."""

from copy import deepcopy

import pytest
from test_automatic_walk_forward import (
    AccountEngine,
    Stopped,
    execute,
    prepared,
    synthetic_prepare_gate,
)

from research import automatic_walk_forward as worker
from research.connectors.optuna_portfolio import MAX_CHECKPOINT_BYTES, _fingerprint


@pytest.mark.parametrize("attempts,simulations", [(318, 318), (64, 0)])
def test_final_calls_and_lost_attempts_remain_reserved(monkeypatch, attempts, simulations):
    value = prepared()
    monkeypatch.setattr(worker, "prepare_gate", synthetic_prepare_gate)
    engine = AccountEngine(value)
    _, saved = execute(value, engine, stop=lambda state: state["attempts"] == 1)
    assert not engine.calls
    saved["attempts"], saved["simulations"] = attempts, simulations
    saved.pop("checkpoint_id")
    saved["checkpoint_id"] = _fingerprint(saved, max_bytes=MAX_CHECKPOINT_BYTES)
    original = deepcopy(saved)
    with pytest.raises(ValueError, match="calculation limit"):
        execute(value, engine, saved=saved)
    assert not engine.calls and saved == original


def test_prepared_owner_closes_on_success_cancellation_and_failure_100_times(monkeypatch):
    owners = []

    class Owner:
        def __enter__(self):
            self.closed = False
            owners.append(self)
            return self

        def __exit__(self, *_error):
            assert not self.closed
            self.closed = True

    monkeypatch.setattr(worker, "prepare_gate", lambda _: Owner())
    outcome = None

    def calculate(*_args, **_kwargs):
        if outcome:
            raise outcome
        return {"finished": True}

    monkeypatch.setattr(worker, "_run", calculate)
    for _ in range(100):
        for outcome in (None, Stopped(), ValueError("calculation failed")):
            arguments = {
                "evaluate": None,
                "checkpoint": None,
                "progress": None,
                "cancelled": lambda: None,
            }
            if outcome is None:
                assert worker.run({}, **arguments) == {"finished": True}
            else:
                with pytest.raises(type(outcome)):
                    worker.run({}, **arguments)
            assert owners[-1].closed
    assert len(owners) == 300 and all(owner.closed for owner in owners)


def test_already_cancelled_job_does_not_copy_or_validate_history(monkeypatch):
    monkeypatch.setattr(worker, "prepare_gate", lambda _: pytest.fail("Copied cancelled history"))

    def cancelled():
        raise Stopped()

    with pytest.raises(Stopped):
        worker.run({}, evaluate=None, checkpoint=None, progress=None, cancelled=cancelled)
