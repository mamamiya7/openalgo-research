"""Reviewed user choices, exact replay and approval fences; no model/scanner runs."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import json
import time
from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_library import BASE, OWNER
from test_reviewed_journey import ready  # noqa: F401

from database.research_db import ResearchJob, ResearchJourney
from research.report_contract import fingerprint
from services import research_journey as journey
from services import scanner_research_service as service


def flow(intent="improve", involvement="guided", adjustable=None):
    return {
        "version": "research-user-flow-v1",
        "intent": intent,
        "involvement": involvement,
        "adjustable": ["hold_sessions", "stop_pct"] if adjustable is None else adjustable,
    }


def test_flow_change_is_reviewed_hashed_and_persisted_outside_financial_schema(ready):
    store, state = ready
    identifier = state["experiment"]["id"]
    old_id = state["plan"]["id"]
    changed = journey.edit_plan(
        store, OWNER, identifier, {"revision": state["revision"], "flow": flow()}
    )
    assert changed["plan"]["id"] != old_id
    assert changed["flow"] == changed["plan"]["flow"] == journey._flow(flow())
    assert changed["plan"]["budget"] == {"proposals": 408, "max_simulations": 512}
    assert "flow" not in changed["experiment"]["draft"]["portfolio"]
    assert changed["plan"]["id"] == fingerprint(
        {k: v for k, v in changed["plan"].items() if k != "id"}
    )
    assert journey.status(store, OWNER, identifier)["flow"] == changed["flow"]


def test_hands_on_improvement_rejected_before_settings_are_changed(ready):
    store, state = ready
    with pytest.raises(ValueError, match="replay|hands.on|Hands.on"):
        journey.edit_plan(
            store,
            OWNER,
            state["experiment"]["id"],
            {"revision": state["revision"], "capital": 99999, "flow": flow(involvement="hands_on")},
        )
    assert journey.status(store, OWNER, state["experiment"]["id"])["revision"] == state["revision"]


def test_replay_has_exact_settings_and_one_native_account_without_optimization(ready):
    store, state = ready
    original = deepcopy(state["experiment"]["draft"]["portfolio"])
    changed = journey.edit_plan(
        store,
        OWNER,
        state["experiment"]["id"],
        {"revision": state["revision"], "flow": flow("replay", "hands_on", [])},
    )
    draft = changed["experiment"]["draft"]
    assert not draft["optimizing"]
    assert not ({"automatic_research", "validation", "optimization"} & draft["portfolio"].keys())
    for actual, prior in zip(draft["portfolio"]["strategies"], original["strategies"], strict=True):
        assert actual["config"] == prior["config"]
        assert actual["execution_policy"] == prior["execution_policy"]
        assert actual["allocation_pct"] == prior["allocation_pct"]
        assert actual["search"] == {}
    assert changed["plan"]["objective"]["id"] == "exact_replay"
    assert changed["plan"]["budget"] == {"proposals": 1, "max_simulations": 1}


def test_legacy_unmarked_review_retains_identity_on_no_change_prepare(ready):
    store, state = ready
    identifier = state["experiment"]["id"]
    with store.sessions.begin() as db:
        row, saved = journey._owned(db, OWNER, identifier)
        saved.pop("flow")
        saved["plan"].pop("flow")
        saved["plan"]["id"] = fingerprint({k: v for k, v in saved["plan"].items() if k != "id"})
        legacy_id = saved["plan"]["id"]
        journey._put(row, saved)
    again = journey.prepare(store, OWNER, identifier, {"revision": state["revision"]})
    assert again["plan"]["id"] == legacy_id and "flow" not in again["plan"]


def test_replay_completion_saves_reopens_without_supervisor_or_winner_claim(ready, monkeypatch):
    from services import research_supervisor

    store, state = ready
    identifier, job_id = state["experiment"]["id"], "r" * 32
    strategies = deepcopy(state["experiment"]["draft"]["portfolio"]["strategies"])
    report = {"strategies": strategies, "summary": {"initial_capital": 20000, "net_pnl": 12}}
    artifact = service.save_artifact(
        store, {"job_id": job_id, "kind": "portfolio_backtest", "result": report}
    )
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=job_id,
                owner=OWNER,
                source_id=strategies[0]["source_id"],
                config="{}",
                status="completed",
                progress=100,
                created_at=time.time(),
                updated_at=time.time(),
                result_artifact=artifact,
            )
        )
        row, saved = journey._owned(db, OWNER, identifier)
        saved.update(
            job_id=job_id,
            approved_plan={**saved["plan"], "flow": journey._flow(flow("replay", "hands_on", []))},
        )
        journey._put(row, saved, "researching")
    monkeypatch.setattr(
        research_supervisor,
        "start_automatic",
        lambda *a, **k: pytest.fail("Replay invoked AI continuation"),
    )
    assert journey._advance(store, OWNER, identifier)
    reopened = journey.status(store, OWNER, identifier)
    assert reopened["phase"] == "complete"
    assert reopened["result"]["kind"] == "replay"
    assert reopened["result"]["accepted"] is False
    assert reopened["result"]["headline"] == "Exact replay saved"
    assert reopened["result"]["verification"]["matched"]
    assert reopened["result"]["verification"]["new_calculations"] == 0
    with store.sessions() as db:
        assert (
            json.loads(db.get(ResearchJourney, identifier).state)["history"][-1]["outcome"]
            == "replay"
        )


@pytest.mark.parametrize("stale", [False, True])
def test_approval_api_is_revision_and_pending_request_bound(ready, client, monkeypatch, stale):
    from services import research_hands_off

    store, state = ready
    identifier = state["experiment"]["id"]
    with store.sessions.begin() as db:
        row, saved = journey._owned(db, OWNER, identifier)
        saved.update(
            job_id="j" * 32, pending_approval={"id": "a" * 64, "job_id": "j" * 32, "kind": "search"}
        )
        journey._put(row, saved, "awaiting_approval")
    calls = []
    monkeypatch.setattr(
        research_hands_off, "resolve_approval", lambda *args: calls.append(args), raising=False
    )
    monkeypatch.setattr(journey, "_response", lambda *a: {"phase": "researching"})
    response = client.post(
        f"{BASE}/experiments/{identifier}/journey/approve",
        json={
            "revision": state["revision"],
            "approval_id": ("b" if stale else "a") * 64,
            "decision": "approve",
        },
    )
    assert response.status_code == (400 if stale else 200)
    assert len(calls) == (0 if stale else 1)
