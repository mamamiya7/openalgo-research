"""Only discarded native trials omit their unused full-recipe report copy."""

from copy import deepcopy

import pytest
from test_automatic_walk_forward import AccountEngine, Stopped, prepared, synthetic_prepare_gate

from research import automatic_walk_forward as controller
from research.checkpoint_bytes import _OwnedCheckpointSink


def prefix(value, monkeypatch, sink_kind):
    monkeypatch.setattr(controller, "prepare_gate", synthetic_prepare_gate)
    publications, reports = [], []

    def checkpoint(state, counts):
        publications.append(deepcopy(state))
        if state["simulations"] >= 3:
            raise Stopped()

    def owned(artifact, counts, publish=None):
        checkpoint(artifact.value, counts)

    class CustomSink(_OwnedCheckpointSink):
        pass

    sink = checkpoint
    if sink_kind == "native":
        sink = _OwnedCheckpointSink(checkpoint, owned)
    elif sink_kind == "custom":
        sink = CustomSink(checkpoint, owned)

    def retain(report, **kwargs):
        reports.append(deepcopy(report))

    with pytest.raises(Stopped):
        controller.run(
            value,
            evaluate=AccountEngine(value),
            checkpoint=sink,
            progress=lambda *args: None,
            cancelled=lambda: None,
            retain_evaluation=retain,
            defer_study_analysis=True,
        )
    return publications, reports


@pytest.mark.timeout(120)
@pytest.mark.parametrize("sink_kind", ["native", "custom"])
def test_trial_recipe_copy_does_not_change_scientific_publication(sink_kind, monkeypatch):
    value = prepared()
    baseline_state, baseline_reports = prefix(value, monkeypatch, "generic")
    state, reports = prefix(value, monkeypatch, sink_kind)
    assert state == baseline_state
    assert len(reports) == len(baseline_reports) == 3
    for actual, expected in zip(reports, baseline_reports, strict=True):
        assert expected["automatic_recipe"] == value["automatic_recipe"]
        if sink_kind == "native":
            expected = {
                key: member for key, member in expected.items() if key != "automatic_recipe"
            }
            assert "automatic_recipe" not in actual
        else:
            assert actual["automatic_recipe"] == value["automatic_recipe"]
        assert actual == expected
