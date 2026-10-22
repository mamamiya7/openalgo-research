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
from research.portfolio_execution_features import VERSION as FEATURE_VERSION


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


@pytest.mark.parametrize(
    "version,gate",
    [(worker.VERSION, "prepare_gate"), (worker.ALLOCATION_VERSION, "prepare_method_gate")],
)
def test_prepared_owner_closes_on_success_cancellation_and_failure_100_times(
    monkeypatch, version, gate
):
    owners = []

    class Owner:
        def __enter__(self):
            self.closed = False
            owners.append(self)
            return self

        def __exit__(self, *_error):
            assert not self.closed
            self.closed = True

    monkeypatch.setattr(worker, gate, lambda _: Owner())
    other_gate = "prepare_method_gate" if gate == "prepare_gate" else "prepare_gate"
    monkeypatch.setattr(worker, other_gate, lambda _: pytest.fail("Used the wrong prepared owner"))
    # The public wrapper dispatches the admitted recipe before the controlled
    # calculation double below. Exercise both legacy and current owner branches.
    evidence = {"portfolio": {"automatic_research": {"version": version}}}
    if version == worker.ALLOCATION_VERSION:
        from research.portfolio import fingerprint

        receipt = {"version": FEATURE_VERSION}
        evidence["portfolio_execution_features"] = {**receipt, "id": fingerprint(receipt)}
    outcome = None

    def calculate(*_args, **_kwargs):
        assert _kwargs["version"] == version
        assert _kwargs["prepared_gate"] is owners[-1] and not owners[-1].closed
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
                assert worker.run(evidence, **arguments) == {"finished": True}
            else:
                with pytest.raises(type(outcome)):
                    worker.run(evidence, **arguments)
            assert owners[-1].closed
    assert len(owners) == 300 and all(owner.closed for owner in owners)


def test_already_cancelled_job_does_not_copy_or_validate_history(monkeypatch):
    monkeypatch.setattr(worker, "prepare_gate", lambda _: pytest.fail("Copied cancelled history"))

    def cancelled():
        raise Stopped()

    with pytest.raises(Stopped):
        worker.run({}, evaluate=None, checkpoint=None, progress=None, cancelled=cancelled)
