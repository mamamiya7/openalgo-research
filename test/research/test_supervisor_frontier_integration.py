"""Checked-branch adoption and explicit resume metadata, with no engine/provider.

Native conclusions/catalogues are explicit contract fixtures here. Existing
worker tests own scientific verification; these checks test ownership and debit
binding against a small isolated metadata database.
"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from database.research_db import (
    ResearchDecisionJob,
    ResearchJourney,
    ResearchStore,
    ResearchSupervisor,
)
from research.engine import config_defaults
from research.execution_policy import normalize_policy
from research.report_contract import fingerprint, settings_identity
from research.user_flow import normalize
from services import research_experiments
from services import research_supervisor as supervisor

OWNER, CONVERSATION = "frontier-owner", "frontier-conversation"
ROOT, SUPERVISOR, STUDY = "0" * 32, "5" * 32, "6" * 32
ARTIFACT = "a" * 64


@pytest.fixture
def case(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "frontier-contract")
    store.initialize()
    config = config_defaults()
    config.update(
        initial_capital=100000.0,
        hold_sessions=82,
        order_size_pct=1.5,
        trailing_enabled=True,
        trailing_pct=8.75,
    )
    base = [
        {
            "id": "scan",
            "name": "Saved scan",
            "allocation_pct": 100,
            "config": config,
            "execution_policy": normalize_policy({"version": "research-execution-aware-v1"}),
        }
    ]
    recipe = {
        "version": "automatic-hypotheses-v16",
        "validation_mode": "exploratory_only",
        "engine": "vectorbt",
    }
    reports, jobs, verified, catalog_reads = {}, {}, [], []
    for index, tag in enumerate(("root", "funded", "trailing", "latest")):
        rows = deepcopy(base)
        if index:
            rows[0]["config"]["initial_capital"] = 200000.0
        if index >= 2:
            rows[0]["config"].update(trailing_enabled=False, trailing_pct=0)
        if index == 3:
            rows[0]["config"]["hold_sessions"] = 34
        digest = chr(ord("a") + index) * 64
        job_id = str(index) * 32
        jobs[job_id] = SimpleNamespace(
            id=job_id, owner=OWNER, status="completed", result_artifact=digest
        )
        reports[digest] = {
            "tag": tag,
            "strategies": rows,
            "portfolio": {"engine": "vectorbt"},
            "summary": {"initial_capital": rows[0]["config"]["initial_capital"]},
            "supervisor_validation": {"outcome": "rejected"},
            "automatic_research": {"recipe": recipe},
        }
    proposals = [
        {
            "id": f"proposal{i}",
            "job_id": str(i) * 32,
            "outcome": "rejected",
            "remedy": "funding_and_size" if i == 1 else "temporal_stability",
            "spent": spent,
            "completed": spent,
            "reserved_accounts": 17 if i == 1 else 14,
            "settings": deepcopy(reports[chr(ord("a") + i) * 64]["strategies"]),
        }
        for i, spent in enumerate((17, 14, 12), start=1)
    ]
    stop = {
        "remedy": "stop",
        "selection": "model",
        "reason": "The current branch is exhausted",
        "seed_artifact": "d" * 64,
    }
    state = {
        "version": supervisor.VERSION,
        "objective": "robust_improvement",
        "status": "stopped",
        "max_experiments": 24,
        "original_attempts": 399,
        "original_simulations": 399,
        "proposals": proposals,
        "validation_mode": "exploratory_only",
        "experiment_policy": supervisor.EXPERIMENT_POLICY,
        "continuation_policy": supervisor.ADAPTIVE_POLICY,
        "decision_catalog_version": "executable-development-remedies-v4",
        "latest_seed_job_id": "3" * 32,
        "latest_source_result_artifact": "d" * 64,
        "accepted_job_id": None,
        "saved_result": None,
        "stop_decision": {k: stop[k] for k in ("selection", "reason", "seed_artifact")},
        "stopping_reason": stop["reason"],
        "automation": {
            "calls_spent": 4,
            "max_model_calls": 11,
            "model_id": 1,
            "decisions": [{"remedy": "temporal_stability"}] * 3 + [stop],
        },
        "diagnosis": {"missing_evidence": False, "permitted_remedies": []},
        "research_context": {
            "experiment_id": STUDY,
            "inputs_artifact": "f" * 64,
            "contract": {"id": "e" * 64},
            "flow": normalize(),
            "baseline_settings": deepcopy(base),
        },
        "study_memory": {"entries": []},
        "frozen_exclusions": {"fixture": "strict source proof owned by caller"},
    }
    with store.sessions.begin() as db:
        db.add(
            ResearchSupervisor(
                id=SUPERVISOR,
                owner=OWNER,
                root_job_id=ROOT,
                root_artifact=ARTIFACT,
                conversation_id=CONVERSATION,
                request_id="original-frontier-grant",
                state=json.dumps(state),
                updated_at=0,
            )
        )
        db.add(
            ResearchDecisionJob(
                supervisor_id=SUPERVISOR, owner=OWNER, phase="stopped", updated_at=0
            )
        )
        db.add(
            ResearchJourney(
                experiment_id=STUDY,
                owner=OWNER,
                phase="none",
                state=json.dumps(
                    {
                        "supervisor_id": SUPERVISOR,
                        "result": {"job_id": "saved-old-result", "accepted": False},
                        "error": None,
                    }
                ),
                updated_at=0,
            )
        )

    def parent(_store, owner, identifier):
        assert owner == OWNER and identifier == ROOT
        report = deepcopy(reports[ARTIFACT])
        return jobs[ROOT], {"result": report, "inputs_artifact": "f" * 64}, report, deepcopy(recipe)

    def get_job(_store, owner, identifier):
        assert owner == OWNER
        return jobs[identifier]

    def checked(report, supervisor_id, proposal_id, root_id, *_):
        assert supervisor_id == SUPERVISOR and root_id == ROOT
        verified.append((report["tag"], proposal_id))
        return {
            "outcome": report["supervisor_validation"]["outcome"],
            **(
                {"no_effect": report["supervisor_validation"]["no_effect"]}
                if "no_effect" in report["supervisor_validation"]
                else {}
            ),
        }

    def diagnosis(report, *_args, **_kwargs):
        return {
            "missing_evidence": report.get("missing", False),
            "permitted_remedies": ["temporal_stability"],
            "tag": report["tag"],
        }

    def catalog(report, _recipe, current):
        catalog_reads.append((report["tag"], current["source_result_artifact"]))
        rows = deepcopy(report["strategies"])
        rows[0]["config"]["hold_sessions"] = 22
        options = []
        if report["tag"] != "latest" and not report.get("missing"):
            options = [
                {
                    "id": fingerprint(
                        {
                            "version": research_experiments.VERSION,
                            "source_artifact": current["source_result_artifact"],
                            "remedy": "temporal_stability",
                            "settings_id": settings_identity(rows),
                        }
                    ),
                    "remedy": "temporal_stability",
                    "settings": rows,
                    "change": {"axis": "hold_sessions", "to": 22},
                    "reason": "Recorded neighboring check failed",
                    "expected_effect": "Prediction, not a result: test a distinct holding cap",
                }
            ]
        return {
            "options": options,
            "unavailable": [],
            "feedback": {"evidence_status": {"missing_required": report.get("missing", False)}},
            "stop_reason": "Required evidence unavailable" if report.get("missing") else None,
        }

    monkeypatch.setattr(supervisor, "_parent", parent)
    monkeypatch.setattr(supervisor.service, "get_job", get_job)
    monkeypatch.setattr(
        supervisor.service,
        "read_artifact",
        lambda _store, digest: {"result": deepcopy(reports[digest])},
    )
    monkeypatch.setattr(supervisor, "_verified_conclusion", checked)
    monkeypatch.setattr(supervisor, "_reviewed_diagnosis", diagnosis)
    monkeypatch.setattr(supervisor, "_experiment_catalog", catalog)
    monkeypatch.setattr("services.research_playbook.validate_settings", lambda *_: None)
    monkeypatch.setattr("services.research_memory.matching", lambda *_: [])
    try:
        yield SimpleNamespace(
            store=store,
            state=state,
            reports=reports,
            jobs=jobs,
            verified=verified,
            catalog_reads=catalog_reads,
        )
    finally:
        store.close()


def current(case):
    return supervisor.status(case.store, OWNER, SUPERVISOR, conversation_id=CONVERSATION)


def stored(case):
    with case.store.sessions() as db:
        return json.loads(db.get(ResearchSupervisor, SUPERVISOR).state)


def resume(case, request_id="explicit-frontier-review"):
    return supervisor.resume_frontier_review(
        case.store, OWNER, SUPERVISOR, conversation_id=CONVERSATION, request_id=request_id
    )


def test_status_unions_all_checked_origins_including_untried_original_capital_after_funding(case):
    before = stored(case)
    case.reports["d" * 64]["missing"] = True
    value = current(case)
    assert value["remaining_accounts"] == 70
    assert {o["origin_job_id"] for o in value["experiment_options"]} == {ROOT, "1" * 32, "2" * 32}
    assert {b["job_id"] for b in value["experiment_frontier"]["branches"]} == set(case.jobs)
    assert value["diagnosis"]["active_branch_missing_evidence"] is True
    assert value["diagnosis"]["missing_evidence"] is False
    assert value["experiment_frontier"]["feedback"]["complete"] is True
    assert "options" not in value["experiment_frontier"]
    assert "report" not in value["experiment_frontier"]["branches"][0]
    assert {tag for tag, _ in case.verified} == {"funded", "trailing", "latest"}
    assert stored(case) == before


def test_proposing_earlier_branch_binds_full_settings_and_adopts_that_immutable_source(case):
    value = resume(case)
    option = next(o for o in value["experiment_options"] if o["origin_job_id"] == "1" * 32)
    result = supervisor.propose(
        case.store,
        OWNER,
        SUPERVISOR,
        option["remedy"],
        "Test this checked earlier branch",
        conversation_id=CONVERSATION,
        candidate_id=option["id"],
    )
    proposal = result["proposal"]
    assert proposal["seed_job_id"] == proposal["source_job_id"] == "1" * 32
    assert proposal["source_result_artifact"] == "b" * 64
    assert proposal["settings"] == option["settings"]
    assert proposal["settings"][0]["config"]["initial_capital"] == 200000
    assert proposal["settings"][0]["config"]["trailing_enabled"] is True
    saved = stored(case)
    assert saved["latest_seed_job_id"] == "1" * 32
    assert saved["latest_source_result_artifact"] == "b" * 64
    assert saved["diagnosis"]["tag"] == "funded"
    assert saved["automation"]["calls_spent"] == 4
    assert supervisor._remaining(saved) == 56
    assert ("funded", "b" * 64) in case.catalog_reads


def test_original_lower_capital_alternative_is_bound_to_original_not_grafted_funding(case):
    value = resume(case)
    option = next(o for o in value["experiment_options"] if o["origin_job_id"] == ROOT)
    proposal = supervisor.propose(
        case.store,
        OWNER,
        SUPERVISOR,
        option["remedy"],
        "Evaluate the untried lower-capital configuration",
        conversation_id=CONVERSATION,
        candidate_id=option["id"],
    )["proposal"]
    assert proposal["seed_job_id"] == ROOT
    assert proposal["source_result_artifact"] == ARTIFACT
    assert proposal["settings"][0]["config"]["initial_capital"] == 100000


def test_changed_origin_artifact_fails_closed_before_source_can_be_adopted(case):
    value = current(case)
    case.jobs["1" * 32].result_artifact = "f" * 64
    with pytest.raises(ValueError, match="artifact changed"):
        supervisor._checked_branch(
            case.store,
            OWNER,
            SUPERVISOR,
            value,
            case.jobs[ROOT],
            case.reports[ARTIFACT],
            "1" * 32,
            "b" * 64,
        )
    assert stored(case)["latest_seed_job_id"] == "3" * 32


@pytest.mark.parametrize("native_status", ["failed", "cancelled"])
def test_incomplete_blocked_attempt_is_unavailable_history_not_a_checked_configuration(
    case, native_status
):
    with case.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, SUPERVISOR)
        state = json.loads(row.state)
        state["proposals"][-1]["outcome"] = "blocked"
        state.update(latest_seed_job_id="2" * 32, latest_source_result_artifact="c" * 64)
        row.state = json.dumps(state)
    case.jobs["3" * 32].status = native_status
    case.jobs["3" * 32].result_artifact = None
    before = stored(case)
    value = current(case)
    branch = next(
        branch
        for branch in value["experiment_frontier"]["branches"]
        if branch["job_id"] == "3" * 32
    )
    assert branch["settings_id"] is None
    assert branch["source_eligibility"] == "incomplete_native_attempt"
    assert branch["scorecard"] == {"eligible": False, "scope": "incomplete_native_attempt"}
    assert value["experiment_frontier"]["feedback"]["incomplete_native_attempts"] == 1
    assert all(option["origin_job_id"] != "3" * 32 for option in value["experiment_options"])
    assert value["experiment_options"] and stored(case) == before


def test_explicit_review_archives_stop_and_requeues_without_resetting_any_budget(case):
    before = stored(case)
    value = resume(case)
    saved = stored(case)
    assert value["status"] == "awaiting_plan"
    assert saved["original_attempts"] == 399
    assert saved["proposals"] == before["proposals"]
    assert saved["max_experiments"] == before["max_experiments"]
    assert saved["automation"]["calls_spent"] == 4
    assert saved["automation"]["max_model_calls"] == 11
    assert supervisor._remaining(saved) == 70
    assert "stop_decision" not in saved
    assert (
        saved["automation"]["decisions"][-1]["superseded_by_review"] == "explicit-frontier-review"
    )
    archive = saved["frontier_reviews"][0]
    assert archive["prior_stop_decision"] == before["stop_decision"]
    assert archive["prior_model_stop"] == before["automation"]["decisions"][-1]
    assert archive["attempts_already_spent"] == 442
    assert archive["prior_decision_phase"] == "stopped"
    assert archive["prior_journey_result"] == {"job_id": "saved-old-result", "accepted": False}
    with case.store.sessions() as db:
        assert db.get(ResearchDecisionJob, SUPERVISOR).phase == "queued"
        journey = db.get(ResearchJourney, STUDY)
        assert journey.phase == "researching" and json.loads(journey.state)["result"] is None
    assert resume(case)["status"] == "awaiting_plan"
    assert len(stored(case)["frontier_reviews"]) == 1


def test_background_frontier_read_cannot_revoke_a_model_stop(case):
    before = stored(case)
    value = supervisor.resume_frontier(case.store, OWNER, SUPERVISOR, conversation_id=CONVERSATION)
    assert value["status"] == "stopped" and value["stop_decision"] == before["stop_decision"]
    assert stored(case) == before


def test_explicit_resume_rolls_back_if_the_owned_journey_binding_changed(case):
    with case.store.sessions.begin() as db:
        db.get(ResearchJourney, STUDY).owner = "another-owner"
    before = stored(case)
    with pytest.raises(ValueError, match="owned saved-study journey"):
        resume(case)
    assert stored(case) == before
    with case.store.sessions() as db:
        assert db.get(ResearchDecisionJob, SUPERVISOR).phase == "stopped"


def test_no_effect_probe_stays_in_history_but_cannot_supply_a_fully_checked_branch(case):
    with case.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, SUPERVISOR)
        state = json.loads(row.state)
        state["proposals"][1]["no_effect"] = {"status": "unchanged"}
        row.state = json.dumps(state)
    case.reports["c" * 64]["supervisor_validation"]["no_effect"] = {"status": "unchanged"}
    value = current(case)
    assert "2" * 32 not in {option["origin_job_id"] for option in value["experiment_options"]}
    branch = next(
        branch
        for branch in value["experiment_frontier"]["branches"]
        if branch["job_id"] == "2" * 32
    )
    assert branch["scorecard"]["source_eligibility"] == "no_effect_probe_only"
    assert branch["available_options"] == 0
    assert any(row.get("reason_code") == "no_effect_probe_only" for row in branch["unavailable"])
    with pytest.raises(ValueError, match="not a fully checked"):
        supervisor._checked_branch(
            case.store,
            OWNER,
            SUPERVISOR,
            value,
            case.jobs[ROOT],
            case.reports[ARTIFACT],
            "2" * 32,
            "c" * 64,
        )


def test_latest_blocked_required_gap_does_not_abort_valid_earlier_frontier_review(
    case, monkeypatch
):
    with case.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, SUPERVISOR)
        state = json.loads(row.state)
        state.update(
            status="running",
            latest_seed_job_id="2" * 32,
            latest_source_result_artifact="c" * 64,
            diagnosis={"missing_evidence": True, "permitted_remedies": []},
        )
        state.pop("stop_decision", None)
        state["proposals"][-1]["outcome"] = None
        row.state = json.dumps(state)
    case.reports["d" * 64].update(missing=True)
    case.reports["d" * 64]["supervisor_validation"]["outcome"] = "blocked"
    monkeypatch.setattr(
        supervisor, "planning_report", lambda _store, _owner, _job, report: deepcopy(report)
    )
    monkeypatch.setattr(
        supervisor,
        "_recovery_record",
        lambda _store, _owner, job, report, _proposal: {
            "job_id": job.id,
            "artifact": job.result_artifact,
            "unresolved_constraints": {"unavailable_evidence": 1},
        },
    )
    value = supervisor.advance(case.store, OWNER, SUPERVISOR, conversation_id=CONVERSATION)
    assert stored(case)["status"] == "awaiting_plan"
    assert value["status"] == "awaiting_plan"
    assert value["diagnosis"]["missing_evidence"] is False
    assert value["diagnosis"]["active_branch_missing_evidence"] is True
    assert value["experiment_options"]
    assert all(option["origin_job_id"] != "3" * 32 for option in value["experiment_options"])
    assert stored(case)["proposals"][-1]["outcome"] == "blocked"
    assert stored(case)["accepted_job_id"] is None


def test_exhausted_blocked_branch_exposes_only_verified_feasibility_counts(case, monkeypatch):
    with case.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, SUPERVISOR)
        state = json.loads(row.state)
        state["proposals"][-1]["outcome"] = "blocked"
        state.update(latest_seed_job_id="2" * 32, latest_source_result_artifact="c" * 64)
        row.state = json.dumps(state)
    report = case.reports["d" * 64]
    report["summary"].update(net_pnl=999999)
    report["supervisor_validation"].update(
        outcome="blocked",
        feasibility={
            "version": "research-feasibility-v1",
            "scope": "feasibility_only",
            "source_settings_id": settings_identity(report["strategies"]),
            "counts": {"admitted_requests": 5, "percentage_policy": 41},
            "unresolved_constraints": {"percentage_policy": 41},
        },
    )
    monkeypatch.setattr(
        supervisor, "planning_report", lambda _store, _owner, _job, report: deepcopy(report)
    )
    value = current(case)
    branch = next(
        branch
        for branch in value["experiment_frontier"]["branches"]
        if branch["job_id"] == "3" * 32
    )
    card = branch["scorecard"]
    assert card["source_eligibility"] == "feasibility_only"
    assert card["feasibility"] == report["supervisor_validation"]["feasibility"]
    assert card["summary"] == {} and "999999" not in json.dumps(card)
    assert branch["available_options"] == 0
    report["supervisor_validation"]["feasibility"]["source_settings_id"] = "wrong-config"
    changed = current(case)
    card = next(
        branch["scorecard"]
        for branch in changed["experiment_frontier"]["branches"]
        if branch["job_id"] == "3" * 32
    )
    assert card["feasibility_unavailable"] is True and "feasibility" not in card


@pytest.mark.parametrize("blocker", ["calls", "accounts", "pending", "operation"])
def test_explicit_resume_cannot_reset_exhausted_or_active_authority(case, blocker):
    with case.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, SUPERVISOR)
        value = json.loads(row.state)
        if blocker == "calls":
            value["automation"]["calls_spent"] = 11
        elif blocker == "accounts":
            value["original_attempts"] = 469
        elif blocker == "pending":
            value["proposals"][-1]["outcome"] = None
        else:
            db.get(ResearchDecisionJob, SUPERVISOR).phase = "deciding"
        row.state = json.dumps(value)
    before = stored(case)
    with pytest.raises(ValueError):
        resume(case)
    assert stored(case) == before
