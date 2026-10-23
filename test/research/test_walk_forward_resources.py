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
    execution_owners = []

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
        from research import execution_features
        from research.portfolio import fingerprint

        receipt = {"version": FEATURE_VERSION}
        evidence["portfolio_execution_features"] = {**receipt, "id": fingerprint(receipt)}
        execution_receipt = {
            "version": execution_features.VERSION,
            "recipe": execution_features.recipe(),
            "source": {
                "context_id": "a" * 64, "timing_id": "b" * 64,
                "interval": "D", "schedule_days": {"2026-01-05": []},
            },
            "features": {}, "opportunities": {},
            "counts": {"opportunities": 0, "common_excluded": 0, "derived": 0, "available": 0},
        }
        evidence["execution_features"] = {
            **execution_receipt, "id": fingerprint(execution_receipt),
        }

        class ExecutionOwner(execution_features._PreparedExecutionFeatures):
            def __init__(self, receipt):
                super().__init__(receipt)
                self.closed = False
                execution_owners.append(self)

            def close(self):
                assert not self.closed
                super().close()
                self.closed = True

        monkeypatch.setattr(worker, "_PreparedExecutionFeatures", ExecutionOwner)
    outcome = None

    def calculate(*_args, **_kwargs):
        assert _kwargs["version"] == version
        assert _kwargs["prepared_gate"] is owners[-1] and not owners[-1].closed
        if version == worker.ALLOCATION_VERSION:
            assert _kwargs["prepared_execution"] is execution_owners[-1]
            assert not execution_owners[-1].closed
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
            if version == worker.ALLOCATION_VERSION:
                assert execution_owners[-1].closed
                with pytest.raises(ValueError, match="released"):
                    execution_owners[-1].attach(evidence)
    assert len(owners) == 300 and all(owner.closed for owner in owners)
    if version == worker.ALLOCATION_VERSION:
        assert len(execution_owners) == 300 and all(owner.closed for owner in execution_owners)


def test_already_cancelled_job_does_not_copy_or_validate_history(monkeypatch):
    monkeypatch.setattr(worker, "prepare_gate", lambda _: pytest.fail("Copied cancelled history"))

    def cancelled():
        raise Stopped()

    with pytest.raises(Stopped):
        worker.run({}, evaluate=None, checkpoint=None, progress=None, cancelled=cancelled)


@pytest.mark.parametrize("cancel_at", [None, 3])
def test_engine_ticks_keep_live_cancellation_without_acknowledging_pause_mid_account(
    monkeypatch, cancel_at
):
    value = prepared()
    monkeypatch.setattr(worker, "prepare_gate", synthetic_prepare_gate)
    in_account, ticks, checks, boundaries, saved = False, 0, 0, 0, []

    def boundary():
        nonlocal boundaries
        assert not in_account, "Pause acknowledgement must not interrupt an unfinished account"
        boundaries += 1

    def cancelled():
        nonlocal checks
        if in_account:
            checks += 1
            if checks == cancel_at:
                raise Stopped()

    def engine(*_args, progress, **_kwargs):
        nonlocal in_account, ticks
        in_account = True
        for tick in range(10):
            ticks += 1
            progress(tick + 1, 10)
        raise Stopped()

    with pytest.raises(Stopped):
        worker.run(
            value,
            evaluate=engine,
            checkpoint=lambda state, _: saved.append(deepcopy(state)),
            progress=lambda *_: None,
            cancelled=cancelled,
            boundary=boundary,
        )
    assert boundaries > 0
    assert ticks == checks == (cancel_at or 10)
    assert saved[-1]["attempts"] == 1 and saved[-1]["simulations"] == 0
