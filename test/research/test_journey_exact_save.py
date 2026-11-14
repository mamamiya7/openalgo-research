"""Controlled worker outputs exercise real exact-report/Keep/save/reopen gates.

No provider, scanner or optimisation runs execute. Acceptance fixtures are
engineering receipts, not evidence of market performance.
"""

import json
import time
import uuid
from copy import deepcopy

import pytest
from sqlalchemy import func, select
from test_research_acceptance import accepted
from test_research_playbook import reviewed

from database.research_db import (
    ResearchCandidateReport,
    ResearchChosenSetup,
    ResearchDecisionEvent,
    ResearchExperiment,
    ResearchJob,
    ResearchJourney,
    ResearchLibraryJob,
    ResearchStore,
)
from research.automatic_protocol import compile_recipe
from research.evaluation_basis import build_evaluation_basis
from research.portfolio import execution_versions
from research.report_contract import fingerprint
from services import research_candidates as candidates
from services import research_journey as journey
from services import research_library as library
from services import research_mcp as mcp
from services import scanner_research_service as service

OWNER = "exact-save-owner"


@pytest.fixture
def case(tmp_path):
    store = ResearchStore(tmp_path / "exact-save")
    store.initialize()
    evidence = reviewed()
    evidence["receipt"] = {
        "input_type": "portfolio",
        "name": "Controlled exact-account fixture",
        "signal_count": len(evidence["signals"]),
        "symbol_count": len(evidence["snapshot"]["bars"]),
        "date_from": min(row["date"] for row in evidence["signals"]),
        "date_to": max(row["date"] for row in evidence["signals"]),
        "warnings": [],
    }
    evidence.update(frozen_prices=True, versions=execution_versions(evidence["portfolio"]))
    report, contract = accepted(changed=False)
    report["portfolio"] = deepcopy(evidence["portfolio"])
    report["automatic_research"]["version"] = "automatic-hypotheses-v16"
    evidence["automatic_recipe"] = compile_recipe(evidence)
    report["automatic_research"]["recipe"] = deepcopy(evidence["automatic_recipe"])
    basis = build_evaluation_basis(evidence, period="selection")
    assert basis["status"] == "verified"
    report["evaluation_basis"] = basis
    config_id = report["experiment"]["recommendation_id"]
    report["experiment"]["selected_reports"][config_id]["evaluation_basis"] = deepcopy(basis)
    report["experiment"]["rows"] = []
    _, source = service.register_source(store, OWNER, evidence)
    native = report["experiment"]["selected_reports"][config_id]
    for rows in (
        evidence["portfolio"]["strategies"],
        evidence["strategies"],
        report["strategies"],
        report["experiment"]["selected_strategies"],
        native["strategies"],
    ):
        for row in rows:
            row["source_id"] = source["id"]
    evidence["automatic_recipe"] = compile_recipe(evidence)
    report["automatic_research"]["recipe"] = deepcopy(evidence["automatic_recipe"])
    report["portfolio"] = deepcopy(evidence["portfolio"])
    report["evaluation_basis"] = build_evaluation_basis(evidence, period="selection")
    native["evaluation_basis"] = deepcopy(report["evaluation_basis"])
    audit = native["portfolio_constraint_evidence"]
    audit["report_evidence_id"] = fingerprint(
        {
            "policy_version": native["policy_version"],
            "ledger": native["ledger"],
            "strategies": native["strategies"],
            "execution": native["execution"],
            "orders": [],
        }
    )
    audit["id"] = fingerprint({key: value for key, value in audit.items() if key != "id"})
    report["portfolio_constraint_evidence"] = deepcopy(audit)
    draft = library.fresh_draft()
    draft.update(portfolio=deepcopy(evidence["portfolio"]), optimizing=True)
    for strategy in draft["portfolio"]["strategies"]:
        strategy["source_id"] = source["id"]
    study = library.create_experiment(
        store, OWNER, {"name": "Controlled exact save", "draft": draft}
    )
    job_id = uuid.uuid4().hex
    inputs_id = service.save_artifact(store, evidence)
    bundle = {
        "schema_version": 2,
        "job_id": job_id,
        "kind": "portfolio_optimize",
        "inputs_artifact": inputs_id,
        "result": report,
        "specification": {"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    }
    artifact = service.save_artifact(store, bundle)
    plan = {
        "id": fingerprint(contract),
        "contract": contract,
        "budget": {"proposals": 408, "max_simulations": 512},
        "inputs_artifact": inputs_id,
    }
    state = {
        "version": journey.VERSION,
        "draft_id": fingerprint(study["draft"]),
        "approved_plan": plan,
        "plan": plan,
        "revision": study["revision"],
        "job_id": job_id,
        "start_request": "original-reviewed-start",
        "history": [],
        "result": None,
        "supervisor_id": None,
        "error": None,
    }
    now = time.time()
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=job_id,
                owner=OWNER,
                source_id=source["id"],
                config="{}",
                status="completed",
                progress=100,
                created_at=now,
                updated_at=now,
                result_artifact=artifact,
            )
        )
        db.add(
            ResearchExperiment(
                job_id=job_id,
                kind="portfolio_optimize",
                identity=fingerprint(bundle),
                specification=service.encoded(bundle["specification"]).decode(),
                counts="{}",
            )
        )
        db.add(
            ResearchLibraryJob(experiment_id=study["id"], job_id=job_id, role="run", created_at=now)
        )
        db.add(
            ResearchJourney(
                experiment_id=study["id"],
                owner=OWNER,
                phase="researching",
                state=service.encoded(state).decode(),
                updated_at=now,
            )
        )
    try:
        yield store, study, state, service.get_job(store, OWNER, job_id), bundle
    finally:
        store.close()


def advance(case):
    store, study, state, job, bundle = case
    return journey._save_search_result(store, OWNER, study["id"], state, job, bundle)


def state_of(case):
    store, study, *_ = case
    with store.sessions() as db:
        row = db.get(ResearchJourney, study["id"])
        return row.phase, json.loads(row.state)


def finish_controlled_account(case):
    """Model one worker result, retaining native reconstruction and lineage checks."""
    store, _, _, root, bundle = case
    _, state = state_of(case)
    child = service.get_job(store, OWNER, state["exact_account"]["job_id"])
    frozen = service.source_for(store, OWNER, child.source_id)
    # The controlled engine result has its own ordinary-account evidence; never
    # pass the automatic aggregate to Keep or bypass verify_reconstruction.
    result = deepcopy(
        bundle["result"]["experiment"]["selected_reports"][state["exact_account"]["config_id"]]
    )
    result["portfolio"] = deepcopy(frozen["portfolio"])
    result["replay_origin"] = deepcopy(frozen["replay_origin"])
    result["coverage"] = {
        "date_from": min(s["date"] for s in frozen["signals"]),
        "date_to": frozen["snapshot"]["sessions"][-1],
    }
    candidates.verify_reconstruction(frozen, result)
    artifact = service.save_artifact(
        store,
        {
            "schema_version": 2,
            "job_id": child.id,
            "kind": "portfolio_backtest",
            "inputs_artifact": service.save_artifact(store, frozen),
            "result": result,
        },
    )
    with store.sessions.begin() as db:
        row = db.get(ResearchJob, child.id)
        row.status, row.progress, row.result_artifact = "completed", 100, artifact
    assert root.result_artifact == case[3].result_artifact
    return child.id


def test_validated_full_search_queues_one_exact_account_then_native_keep_save_and_reopen(case):
    store, study, _, root, _ = case
    original = root.result_artifact
    assert advance(case) is False
    phase, state = state_of(case)
    assert phase == "finalizing"
    assert state["exact_account"]["reserved_accounts"] == 1
    assert state["exact_account"]["parent_reserved"] == 440
    child = finish_controlled_account(case)
    assert advance(case) is True
    phase, state = state_of(case)
    assert phase == "complete" and state["result"]["accepted"]
    assert state["result"]["job_id"] == child != root.id
    assert state["exact_account"]["completed_accounts"] == 1
    opened = mcp.reopen_chosen_result(store, OWNER, study["id"], state["result"]["choice_id"])
    assert opened["verification"]["matched"] and opened["verification"]["new_calculations"] == 0
    assert service.get_job(store, OWNER, root.id).result_artifact == original
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 1
        assert db.scalar(select(func.count()).select_from(ResearchDecisionEvent)) == 1


def test_crash_after_native_candidate_admission_reuses_one_job_and_one_budget_debit(
    case, monkeypatch
):
    prepare = candidates.prepare
    calls = []

    def interrupted(*args, **kwargs):
        result = prepare(*args, **kwargs)
        calls.append(result["report_job_id"])
        raise RuntimeError("Simulated process interruption after native queue admission")

    monkeypatch.setattr(candidates, "prepare", interrupted)
    with pytest.raises(RuntimeError, match="process interruption"):
        advance(case)
    assert state_of(case)[1]["exact_account"]["job_id"] is None
    monkeypatch.setattr(candidates, "prepare", prepare)
    assert advance(case) is False
    assert state_of(case)[1]["exact_account"]["job_id"] == calls[0]
    store = case[0]
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchCandidateReport)) == 1
    finish_controlled_account(case)
    assert advance(case) is True
    assert state_of(case)[1]["exact_account"]["reserved_accounts"] == 1


def test_no_exact_account_can_overrun_the_original_512_allowance(case):
    case[4]["result"]["automatic_research"]["counts"].update(attempts_reserved=512, simulations=512)
    artifact = service.save_artifact(case[0], case[4])
    case[3].result_artifact = artifact
    with case[0].sessions.begin() as db:
        db.get(ResearchJob, case[3].id).result_artifact = artifact
    with pytest.raises(ValueError, match="512-account allowance"):
        advance(case)
    assert "exact_account" not in state_of(case)[1]
    with case[0].sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchCandidateReport)) == 0


def test_only_the_prior_missing_exact_stop_can_resume_after_native_validation(case):
    store, study, *_ = case
    with store.sessions.begin() as db:
        row = db.get(ResearchJourney, study["id"])
        state = json.loads(row.state)
        state["error"] = journey.EXACT_SAVE_REQUIRED
        journey._put(row, state, "needs_action")
    assert journey._resume_exact_search_save(store, OWNER, study["id"])
    assert state_of(case)[0] == "finalizing"
    assert state_of(case)[1]["exact_account"]["reserved_accounts"] == 1
    with store.sessions.begin() as db:
        row = db.get(ResearchJourney, study["id"])
        state = json.loads(row.state)
        state["error"] = "Native financial validation failed"
        journey._put(row, state, "needs_action")
    assert not journey._resume_exact_search_save(store, OWNER, study["id"])
    assert state_of(case)[0] == "needs_action"


def test_crash_after_keep_reuses_the_native_event_and_choice(case, monkeypatch):
    assert advance(case) is False
    finish_controlled_account(case)
    original = mcp.save_chosen_result
    monkeypatch.setattr(
        mcp, "save_chosen_result", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("After Keep"))
    )
    with pytest.raises(RuntimeError, match="After Keep"):
        advance(case)
    with case[0].sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchDecisionEvent)) == 1
        assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 0
    monkeypatch.setattr(mcp, "save_chosen_result", original)
    assert advance(case) is True
    with case[0].sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchDecisionEvent)) == 1
        assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 1
    assert state_of(case)[1]["exact_account"]["reserved_accounts"] == 1


def test_mutated_or_failed_search_cannot_be_admitted_as_an_exact_saved_choice(case):
    case[4]["result"]["automatic_research"]["counts"]["simulations"] += 1
    with pytest.raises(ValueError):
        advance(case)
    assert "exact_account" not in state_of(case)[1]
