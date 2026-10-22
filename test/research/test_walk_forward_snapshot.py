"""One owned copy preserves checkpoint bytes, callback isolation and fallback."""

from copy import deepcopy

import pytest
from test_automatic_walk_forward import AccountEngine, Stopped, prepared, synthetic_prepare_gate
from test_walk_forward_publication import prefix

from research import automatic_walk_forward as worker


@pytest.fixture(autouse=True)
def isolate_gate(monkeypatch):
    monkeypatch.setattr(worker, "prepare_gate", synthetic_prepare_gate)


def test_snapshot_and_original_fallback_preserve_exact_checkpoints_and_account_calls(monkeypatch):
    value = prepared()
    fast_engine = AccountEngine(value)
    fast_rows = []
    expected = prefix(value, fast_engine, checkpoints=fast_rows)
    monkeypatch.setattr(worker, "bounded_canonical_snapshot", lambda *a, **kw: None)
    fallback_engine = AccountEngine(value)
    fallback_rows = []
    actual = prefix(value, fallback_engine, checkpoints=fallback_rows)
    assert actual == expected
    assert fallback_rows == fast_rows
    assert fallback_engine.calls == fast_engine.calls


def test_publisher_may_retain_or_mutate_snapshot_without_changing_the_next_account():
    value = prepared()
    expected_engine = AccountEngine(value)
    expected = prefix(value, expected_engine)
    engine = AccountEngine(value)
    latest = []

    def checkpoint(snapshot, _counts):
        worker._validate_state(snapshot)
        latest[:] = [deepcopy(snapshot)]
        if len(snapshot.get("fits", {}).get("fit1", {}).get("trials", [])) == 3:
            raise Stopped()
        snapshot["evaluations"].clear()
        snapshot["fits"].clear()

    with pytest.raises(Stopped):
        worker.run(
            value,
            evaluate=engine,
            checkpoint=checkpoint,
            progress=lambda *_: None,
            cancelled=lambda: None,
            defer_study_analysis=True,
        )
    assert latest[0] == expected
    assert engine.calls == expected_engine.calls
