"""Native market preparation owns resumable acquisition; exact replay stays offline."""

from copy import deepcopy

import pytest
from test_automatic_conditions import prepared

from database.research_db import ResearchStore
from services import research_regimes
from services import scanner_research_service as service
from services.research_entry_conditions import prepare_context


def test_checkpoint_reuses_original_inputs_and_resumes_market_acquisition(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path)
    store.initialize()
    full = prepared()
    evidence = deepcopy(full)
    market = evidence.pop("entry_condition_market")
    recorded = []
    marker = {"benchmark": "completed-snapshot"}

    def run_context(store, owner, bounds, *, saved, checkpoint, progress, cancelled):
        assert bounds["equity_curve"][0]["date"] == evidence["snapshot"]["sessions"][0]
        assert saved == marker
        cancelled()
        checkpoint(marker, {"symbol_count": 1})
        return market

    monkeypatch.setattr(research_regimes, "run_context", run_context)
    try:
        result = prepare_context(
            store,
            "owner",
            evidence,
            saved={"phase": "condition_prices", "market_checkpoint": marker},
            checkpoint=lambda state, counts: recorded.append((state, counts)),
            progress=lambda *args: None,
            cancelled=lambda: None,
        )
        assert result["entry_condition_market"] == market
        state, counts = recorded[0]
        assert state["phase"] == "condition_prices" and state["market_checkpoint"] == marker
        assert service.read_artifact(store, state["inputs_artifact"]) == evidence
        assert counts["stage"] == "prices"
    finally:
        store.close()


def test_frozen_replay_never_acquires_missing_or_changed_condition_prices(tmp_path, monkeypatch):
    full = prepared()

    def forbidden(*args, **kwargs):
        raise AssertionError("Exact condition replay must not download")

    monkeypatch.setattr(research_regimes, "run_context", forbidden)
    kwargs = {
        "saved": None,
        "checkpoint": forbidden,
        "progress": forbidden,
        "cancelled": lambda: None,
    }
    assert prepare_context(None, "owner", full, **kwargs) is full
    missing = deepcopy(full)
    missing.pop("entry_condition_market")
    missing.update(frozen_prices=True, replay_origin={"parent_job_id": "saved"})
    with pytest.raises(ValueError, match="original saved"):
        prepare_context(None, "owner", missing, **kwargs)
    changed = deepcopy(full)
    changed["entry_condition_market"]["context"] = {"changed": True}
    with pytest.raises(ValueError, match="inputs changed"):
        prepare_context(None, "owner", changed, **kwargs)
