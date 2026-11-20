"""Continuation authority must match both reviewed permission and frozen source."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_jobs import app, client  # noqa: F401
from test_library import OWNER
from test_reviewed_journey import ready  # noqa: F401

from database.research_db import ResearchJob, ResearchLibraryJob
from research.user_flow import normalize
from services import research_journey as journey
from services import research_library as library
from services import research_supervisor as supervisor
from services import scanner_research_service as service


@pytest.fixture
def authority(ready):
    store, view = ready
    identifier, job_id = view["experiment"]["id"], "f" * 32
    portfolio = library.portfolio_payload(view["experiment"]["draft"])
    inputs_id = service.save_artifact(store, {"portfolio": portfolio})
    flow = normalize({**normalize(), "adjustable": ["hold_sessions"], "involvement": "guided"})
    supplied = {
        "experiment_id": identifier,
        "inputs_artifact": inputs_id,
        "contract": deepcopy(view["plan"]["contract"]),
        "flow": flow,
        "baseline_settings": deepcopy(portfolio["strategies"]),
    }
    with store.sessions.begin() as db:
        db.add(
            ResearchLibraryJob(
                experiment_id=identifier, job_id=job_id, role="run", created_at=time.time()
            )
        )
        row, state = journey._owned(db, OWNER, identifier)
        state.update(
            job_id=job_id,
            approved_plan={
                **state["plan"],
                "flow": flow,
                "baseline_settings": deepcopy(portfolio["strategies"]),
            },
        )
        journey._put(row, state, "researching")
    return (
        store,
        SimpleNamespace(id=job_id, owner=OWNER),
        {"inputs_artifact": inputs_id},
        {"strategies": deepcopy(portfolio["strategies"])},
        supplied,
    )


def test_unchanged_reviewed_permissions_and_frozen_source_are_accepted_without_calculation(
    authority,
):
    store, job, bundle, report, supplied = authority
    original = deepcopy((bundle, report, supplied))
    context, memory = supervisor._research_context(store, OWNER, job, bundle, report, supplied)
    assert context == supplied and context is not supplied
    assert memory["entries"] == []
    assert (bundle, report, supplied) == original


@pytest.mark.parametrize("change", ["widen", "involvement", "baseline", "job"])
def test_continuation_cannot_widen_or_replace_approved_authority(authority, change):
    store, job, bundle, report, supplied = authority
    if change == "widen":
        supplied["flow"]["adjustable"].append("target_pct")
        supplied["flow"] = normalize(supplied["flow"])
    elif change == "involvement":
        supplied["flow"]["involvement"] = "hands_off"
    elif change == "baseline":
        supplied["baseline_settings"][0]["config"]["order_size_pct"] = 99
    else:
        with store.sessions.begin() as db:
            row, state = journey._owned(db, OWNER, supplied["experiment_id"])
            state["job_id"] = "x" * 32
            journey._put(row, state)
    with pytest.raises(ValueError, match="approved plan"):
        supervisor._research_context(store, OWNER, job, bundle, report, supplied)


def test_even_matching_review_metadata_cannot_replace_frozen_original_settings(authority):
    store, job, bundle, report, supplied = authority
    supplied["baseline_settings"][0]["config"]["initial_capital"] += 1
    with store.sessions.begin() as db:
        row, state = journey._owned(db, OWNER, supplied["experiment_id"])
        state["approved_plan"]["baseline_settings"] = deepcopy(supplied["baseline_settings"])
        journey._put(row, state)
    with pytest.raises(ValueError, match="original settings changed"):
        supervisor._research_context(store, OWNER, job, bundle, report, supplied)


def test_result_cannot_silently_change_a_locked_trading_setting(authority):
    store, job, bundle, report, supplied = authority
    report["strategies"][0]["config"]["order_size_pct"] += 1
    with pytest.raises(ValueError, match="fixed user setting"):
        supervisor._research_context(store, OWNER, job, bundle, report, supplied)


@pytest.mark.parametrize("decision", ["reject", "approve"])
def test_saved_user_stop_is_distinct_from_failed_validation(ready, decision):
    store, view = ready
    identifier, job_id = view["experiment"]["id"], "z" * 32
    strategies = deepcopy(view["experiment"]["draft"]["portfolio"]["strategies"])
    report = {"strategies": strategies, "summary": {"initial_capital": 20000, "net_pnl": 0}}
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
        row, state = journey._owned(db, OWNER, identifier)
        state.update(job_id=job_id, approval_decisions={"a" * 64: decision})
        journey._put(row, state, "researching")
    reason = "You declined the next experiment; its settings were not validated."
    journey._finish(
        store,
        OWNER,
        identifier,
        {
            "job_id": job_id,
            "experiment_id": identifier,
            "after": report["summary"],
            "verification": journey._reopen_native(store, OWNER, job_id),
            "stopping_reason": reason,
        },
    )
    reopened = journey.status(store, OWNER, identifier)
    assert reopened["phase"] == "none" and reopened["result"]["accepted"] is False
    assert reopened["result"]["verification"]["matched"]
    assert reopened["result"]["verification"]["new_calculations"] == 0
    with store.sessions() as db:
        _, saved = journey._owned(db, OWNER, identifier)
    history = saved["history"][-1]
    if decision == "reject":
        assert reopened["result"]["kind"] == "stopped"
        assert reopened["result"]["headline"] == "You stopped research"
        assert history["outcome"] == "stopped" and history["reasons"] == [reason]
        assert journey.NO_RESULT not in history["reasons"]
    else:
        assert reopened["result"]["headline"] == journey.NO_RESULT
        assert history["outcome"] == "rejected" and history["reasons"] == [journey.NO_RESULT]
