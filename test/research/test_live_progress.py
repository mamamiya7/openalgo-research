"""Committed live development metrics do not read prices or resume research."""

# ruff: noqa: F811 -- shared isolated HTTP fixtures

import json
from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchStudyExecution,
    ResearchStudyProposal,
)
from research.automatic_walk_forward import MAX_CHECKPOINT_BYTES, _fingerprint
from research.report_contract import settings_identity
from services import research_live_progress as live
from services import scanner_research_service as service

JOB = "d" * 32
OWNER = "research-test"
URL = f"/scanner-research/api/portfolio/jobs/{JOB}/live-progress"


def row(number, net, drawdown=2):
    settings = [
        {
            "id": "scanner",
            "name": "Scanner",
            "allocation_pct": 100,
            "config": {"target_pct": number + 1},
        }
    ]
    return {
        "config_id": settings_identity(settings),
        "strategies": settings,
        "trial_number": number,
        "score": net - drawdown,
        "summary": {"net_return_pct": net, "max_drawdown_pct": drawdown, "closed_trades": 20},
    }


def seal(calculation):
    calculation.pop("checkpoint_id", None)
    calculation["checkpoint_id"] = _fingerprint(calculation, max_bytes=MAX_CHECKPOINT_BYTES)


@pytest.fixture
def saved(app):
    store = app.extensions["research_store"]
    candidates = [row(0, 3), row(1, 8), row(2, 5)]
    fit = {
        "candidates": {item["config_id"]: item for item in candidates},
        "trials": [
            {
                "number": n,
                "config_id": item["config_id"],
                "value": item["score"],
                "reused": False,
                "state": "complete",
                "phase": "family_screening",
            }
            for n, item in enumerate(candidates)
        ],
        "selection": None,
    }
    baseline = row(9, 1)
    calculation = {
        "version": "automatic-hypotheses-v16",
        "active_fit": "search_seed1",
        "attempts": 5,
        "simulations": 5,
        "evaluations": {
            "base": {
                "summary": baseline["summary"],
                "automatic_stock_condition": {"hypothesis": {"id": "none"}},
                "development_evidence": {
                    "config_id": baseline["config_id"],
                    "period": "search",
                    "stress": False,
                },
            },
            "poison-final": {
                "summary": row(8, 9999)["summary"],
                "development_evidence": {
                    "config_id": baseline["config_id"],
                    "period": "final",
                    "stress": False,
                },
            },
        },
        "fits": {"search_seed1": fit, "fit1": {"trials": [], "candidates": {}, "selection": None}},
        "folds": [],
        "selected_report": None,
        "final_report": {"summary": row(8, 9999)["summary"]},
    }
    seal(calculation)
    envelope = {
        "identity": "e" * 64,
        "state": {
            "phase": "calculation",
            "inputs_artifact": "f" * 64,
            "calculation": calculation,
            "research_mandate": {
                "economic_assumptions": {"settings": baseline["strategies"]},
                "validation": {
                    "periods": [{"name": "search", "from": "2026-01-05", "to": "2026-06-30"}]
                },
            },
        },
    }
    digest = service.save_artifact(store, envelope)
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=JOB,
                owner=OWNER,
                source_id="a" * 32,
                config="{}",
                status="running",
                progress=70,
                created_at=1,
                updated_at=2,
            )
        )
        db.add(
            ResearchExperiment(
                job_id=JOB,
                kind="portfolio_optimize",
                specification=json.dumps(
                    {"portfolio": {"automatic_research": {"version": calculation["version"]}}}
                ),
                identity=envelope["identity"],
                checkpoint=digest,
                counts="{}",
            )
        )
    return store, envelope, digest


def republish(store, envelope):
    digest = service.save_artifact(store, envelope)
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, JOB).checkpoint = digest
    return digest


def test_live_metrics_are_active_fit_only_owner_fenced_and_read_one_checkpoint(
    saved, client, app, monkeypatch
):
    store, envelope, digest = saved
    reads = []
    original = service.read_artifact

    def read(store, identity, **kwargs):
        reads.append(identity)
        assert identity == digest  # Frozen price inputs must never be opened.
        assert kwargs["max_bytes"] <= 33 * 1024**2
        return original(store, identity, **kwargs)

    monkeypatch.setattr(service, "read_artifact", read)
    response = client.get(URL)
    assert response.status_code == 200
    result = response.json
    assert response.headers["Cache-Control"] == "private, no-store"
    assert result["available"] is True and result["provisional"] is True
    assert result["fit"] == {
        "key": "search_seed1",
        "label": "Search stability check 1",
        "dates": {"from": "2026-01-05", "to": "2026-06-30"},
        "completed": 3,
        "budget": 68,
    }
    assert result["latest"]["trial_number"] == 2 and result["latest"]["return_pct"] == 5
    assert result["best"]["trial_number"] == 1 and result["best"]["return_pct"] == 8
    assert result["baseline"]["return_pct"] == 1
    assert "9999" not in json.dumps(result)
    assert result["pace"] == {"seconds_per_trial": None, "remaining_search_seconds_upper": None}
    assert reads == [digest]
    assert len(service.encoded(result)) <= live.MAX_RESPONSE_BYTES
    assert app.test_client().get(URL).status_code == 401
    assert client.get(URL + "?limit=1").status_code == 400
    with client.session_transaction() as session:
        session["user"] = "another-owner"
    assert client.get(URL).status_code == 404
    assert reads == [digest]
    assert store is not None


def test_pace_uses_only_committed_successful_nonreused_current_fit_timings(saved, client):
    store, envelope, _ = saved
    trials = envelope["state"]["calculation"]["fits"]["search_seed1"]["trials"]
    with store.sessions.begin() as db:
        db.add(
            ResearchStudyExecution(
                id="a" * 32,
                job_id=JOB,
                owner=OWNER,
                worker="worker",
                version="v1",
                state="running",
                proposal_budget=408,
                replayed=0,
                started_at=0,
                observed_at=100,
            )
        )
        db.add(
            ResearchStudyExecution(
                id="b" * 32,
                job_id=JOB,
                owner="another-owner",
                worker="worker",
                version="v1",
                state="running",
                proposal_budget=408,
                replayed=0,
                started_at=0,
                observed_at=100,
            )
        )
        for number, trial in enumerate(trials):
            for execution, duration in [("a" * 32, [10, 20, 90][number]), ("b" * 32, 999)]:
                db.add(
                    ResearchStudyProposal(
                        execution_id=execution,
                        job_id=JOB,
                        number=272 + number,
                        config_id=trial["config_id"],
                        params="{}",
                        state="evaluated",
                        reused=False,
                        started_at=100,
                        finished_at=100 + duration,
                        observed_at=100 + duration,
                    )
                )
        db.add(
            ResearchStudyProposal(
                execution_id="a" * 32,
                job_id=JOB,
                number=0,
                config_id=trials[0]["config_id"],
                params="{}",
                state="evaluated",
                reused=False,
                started_at=0,
                finished_at=9999,
                observed_at=9999,
            )
        )
    assert client.get(URL).json["pace"] == {
        "seconds_per_trial": 20,
        "remaining_search_seconds_upper": None,
    }
    with store.sessions.begin() as db:
        proposal = (
            db.query(ResearchStudyProposal).filter_by(execution_id="a" * 32, number=274).one()
        )
        proposal.reused = True
    assert client.get(URL).json["pace"]["seconds_per_trial"] is None


@pytest.mark.parametrize("change", ["hash", "identity", "trial", "orphan", "legacy"])
def test_unverified_or_unknown_checkpoints_show_no_invented_metrics(saved, client, change):
    store, envelope, _ = saved
    value = deepcopy(envelope)
    calc = value["state"]["calculation"]
    if change == "hash":
        calc["simulations"] += 1
    elif change == "identity":
        value["identity"] = "0" * 64
    elif change == "trial":
        calc["fits"]["search_seed1"]["trials"][0]["value"] += 1
        seal(calc)
    elif change == "orphan":
        item = row(7, 999)
        calc["fits"]["search_seed1"]["candidates"][item["config_id"]] = item
        seal(calc)
    else:
        calc["version"] = "automatic-hypotheses-v4"
        seal(calc)
    republish(store, value)
    result = client.get(URL).json
    assert result["available"] is False
    assert result["latest"] is result["best"] is result["baseline"] is None


def test_frozen_choice_and_reused_trial_explanations_are_saved_facts_only(saved, client):
    store, envelope, _ = saved
    calc = envelope["state"]["calculation"]
    fit = calc["fits"]["search_seed1"]
    fit["selection"] = deepcopy(next(iter(fit["candidates"].values())))
    fit["trials"].append({**fit["trials"][0], "number": 3, "reused": True})
    seal(calc)
    republish(store, envelope)
    result = client.get(URL).json
    assert result["stage"] == "checking"
    assert result["latest"]["trial_number"] == 3
    assert {event["kind"] for event in result["events"]} == {
        "committed",
        "reused",
        "objective",
        "frozen",
    }
    assert not any("rejected" in event["text"].lower() for event in result["events"])
    assert len(result["events"]) <= 5


def test_no_checkpoint_and_changed_publication_remain_read_only(saved, client, monkeypatch):
    store, envelope, digest = saved
    original = service.read_artifact

    def changed(store, identifier, **kwargs):
        value = original(store, identifier, **kwargs)
        with store.sessions.begin() as db:
            db.get(ResearchExperiment, JOB).checkpoint = None
        return value

    monkeypatch.setattr(service, "read_artifact", changed)
    assert client.get(URL).json["available"] is False
    assert client.get(URL).json["available"] is False


def test_completed_snapshot_explains_actual_later_check_and_retained_original_without_pending_claim(
    saved, client
):
    store, envelope, _ = saved
    calc = envelope["state"]["calculation"]
    fit = calc["fits"]["search_seed1"]
    fit["selection"] = deepcopy(next(iter(fit["candidates"].values())))
    calc["selection"] = {"is_baseline": True}
    calc["folds"] = [
        {"eligible": True, "reasons": []},
        {"eligible": True, "reasons": []},
        {
            "eligible": False,
            "reasons": ["Return after costs is not positive", "Second recorded reason"],
        },
    ]
    seal(calc)
    republish(store, envelope)
    with store.sessions.begin() as db:
        db.get(ResearchJob, JOB).status = "completed"
    result = client.get(URL).json
    assert result["available"] is True
    assert result["stage"] == "completed" and result["provisional"] is True
    assert result["events"][-2:] == [
        {
            "kind": "later_check",
            "text": "Completed later-window check 3: Return after costs is not positive.",
        },
        {
            "kind": "completed",
            "text": "Checks did not establish a reliable improvement; original settings retained.",
        },
    ]
    assert all(event["kind"] != "frozen" for event in result["events"])
    assert len(result["events"]) <= 5
    assert "independently validated" not in json.dumps(result)
    calc["selection"]["is_baseline"] = False
    calc["folds"][-1] = {"eligible": True, "reasons": []}
    seal(calc)
    republish(store, envelope)
    alternative = client.get(URL).json
    assert (
        alternative["events"][-2]["text"]
        == "Completed later-window check 3: met the recorded checks."
    )
    assert (
        alternative["events"][-1]["text"]
        == "Research completed. Open the final findings for the recorded outcome."
    )


def test_repeated_success_and_corrupt_reads_release_database_and_file_handles(saved, monkeypatch):
    import gc
    import os

    import psutil

    store, _, _ = saved
    original = service.read_artifact
    count = 0

    def alternating(*args, **kwargs):
        nonlocal count
        count += 1
        if count % 2 == 0:
            raise ValueError("corrupt test checkpoint")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "read_artifact", alternating)
    for _ in range(5):
        live.read(store, OWNER, JOB)
    gc.collect()
    process = psutil.Process(os.getpid())
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    before = handles()
    for _ in range(100):
        live.read(store, OWNER, JOB)
    gc.collect()
    assert handles() <= before + 2
