"""Durable, finite continuation of confirmed historical research commands.

The dedicated research worker makes bounded decisions outside DB transactions.
A debit precedes a provider request; an interrupted/uncertain request is never
reissued automatically. Native accounting, validation and saving own acceptance.
"""

import json
import time
import uuid
from copy import deepcopy

from sqlalchemy import select

from database.research_db import ResearchDecisionJob, ResearchSupervisor, ResearchWorker
from services import research_supervisor as supervisor

VERSION = "research-automatic-continuation-v1"
ACTIVE = ("queued", "deciding", "waiting", "applying", "finalizing")


def start(
    store,
    owner,
    job_id,
    *,
    conversation_id,
    request_id,
    max_experiments=3,
    model_id=None,
    max_model_calls=3,
    research_context=None,
    experiment_policy=None,
    continuation_policy=None,
):
    call_limit = (
        supervisor.ADAPTIVE_EXPERIMENT_LIMIT
        if continuation_policy == supervisor.ADAPTIVE_POLICY
        else 3
    )
    if type(max_model_calls) is not int or not 0 <= max_model_calls <= call_limit:
        raise ValueError("Automatic research exceeds its declared model decision limit")
    if model_id is not None and (type(model_id) is not int or model_id < 1):
        raise ValueError("Automatic research needs its configured model identity")
    # Resolve configuration only; this does not contact any provider.
    model_name = None
    if model_id is not None:
        from services.agent.builder import resolve_model

        model_name = resolve_model(model_id).litellm_id
    current = supervisor.open_supervisor(
        store,
        owner,
        job_id,
        conversation_id=conversation_id,
        request_id=request_id,
        max_experiments=max_experiments,
        research_context=research_context,
        experiment_policy=experiment_policy,
        continuation_policy=continuation_policy,
    )
    with store.sessions.begin() as db:
        supervisor.service.write_guard(db)
        row, state = supervisor._owned(db, owner, current["id"], conversation_id)
        existing = state.get("automation")
        if existing:
            if (existing["model_id"], existing["model"], existing["max_model_calls"]) != (
                model_id,
                model_name,
                max_model_calls,
            ):
                raise ValueError("This plan already has a different frozen model allowance")
            # An earlier catalogue stopped this command before choosing or
            # spending anything. Re-confirmation may admit the corrected
            # executable catalogue; it cannot reset a used experiment/call.
            if (
                state["status"] == "stopped"
                and not state["proposals"]
                and existing["calls_spent"] == 0
                and state.get("decision_catalog_version") != supervisor.DECISION_CATALOG_VERSION
            ):
                _, _, report, recipe = supervisor._parent(store, owner, job_id)
                state["diagnosis"] = supervisor._reviewed_diagnosis(
                    report, recipe, state.get("research_context"), state.get("study_memory")
                )
                state["decision_catalog_version"] = supervisor.DECISION_CATALOG_VERSION
                if state["diagnosis"]["permitted_remedies"]:
                    state["status"] = "planning"
                    state.pop("stopping_reason", None)
                    db.get(ResearchDecisionJob, row.id).phase = "queued"
                supervisor._put(row, state)
            if state["status"] in ("accepted", "saved") and not existing.get("reopened"):
                # Saving/reopening is deterministic and idempotent. Repairing
                # metadata never requires another decision or backtest budget.
                db.get(ResearchDecisionJob, row.id).phase = "finalizing"
            elif (
                state["status"] == "stopped"
                and not state["proposals"]
                and not existing["decisions"]
                and existing.get("attention")
                and existing["calls_spent"] < existing["max_model_calls"]
            ):
                # This is an explicit re-confirmed command, not a background
                # retry. Every previous request remains charged to the grant.
                existing["confirmed_resumptions"] = existing.get("confirmed_resumptions", 0) + 1
                state["status"] = "planning"
                state.pop("stopping_reason", None)
                existing.pop("attention", None)
                db.get(ResearchDecisionJob, row.id).phase = "queued"
                supervisor._put(row, state)
        else:
            state["automation"] = {
                "version": VERSION,
                "model_id": model_id,
                "model": model_name,
                "max_model_calls": max_model_calls,
                "calls_spent": 0,
                "decisions": [],
                "reopened": None,
            }
            supervisor._put(row, state)
            db.add(
                ResearchDecisionJob(
                    supervisor_id=row.id, owner=owner, phase="queued", updated_at=time.time()
                )
            )
    return supervisor.status(store, owner, current["id"], conversation_id=conversation_id)


def _write_job(
    store, identifier, claim, *, phase, decision=None, stop=None, reopened=None, attention=True
):
    with store.sessions.begin() as db:
        supervisor.service.write_guard(db)
        job = db.get(ResearchDecisionJob, identifier)
        if job is None or job.claim_token != claim:
            raise ValueError("Automatic research continuation lost its claim")
        row = db.get(ResearchSupervisor, identifier)
        state = json.loads(row.state)
        if decision is not None:
            state["automation"]["decisions"].append(deepcopy(decision))
            if decision["remedy"] == "stop":
                state["stop_decision"] = {
                    k: decision.get(k) for k in ("selection", "reason", "seed_artifact")
                }
        if stop and state["status"] not in ("accepted", "saved"):
            state.update(status="stopped", stopping_reason=stop[:1000])
        if stop and attention:
            state["automation"]["attention"] = stop[:1000]
        elif stop:
            state["automation"].pop("attention", None)
        if reopened is not None:
            state["automation"]["reopened"] = reopened
            state["automation"].pop("attention", None)
            state.pop("attention", None)
        job.phase, job.updated_at = phase, time.time()
        supervisor._put(row, state)


def _evidence(store, owner, current):
    from services.research_mcp import get_run

    concrete = current.get("experiment_policy") == supervisor.EXPERIMENT_POLICY
    report = (
        supervisor.service.read_artifact(store, current["source_result_artifact"])["result"]
        if concrete
        else get_run(store, owner, current["seed_job_id"], curve_points=1)
    )
    permitted = deepcopy(current["diagnosis"])
    tried = {p["remedy"] for p in current["proposals"]}
    if current.get("experiment_policy") != supervisor.EXPERIMENT_POLICY:
        permitted["permitted_remedies"] = [
            r for r in permitted["permitted_remedies"] if r not in tried
        ]
    evidence = {
        "goal": current["goal"],
        "diagnosis": permitted,
        "remaining_accounts": current["remaining_accounts"],
        "validation_mode": current["validation_mode"],
        "research_evidence": report.get("research_evidence"),
        "previous_experiments": [
            {k: p.get(k) for k in ("remedy", "outcome", "reasons", "no_effect")}
            for p in current["proposals"]
        ],
    }
    if current.get("experiment_policy") == supervisor.EXPERIMENT_POLICY:
        from research.report_contract import fingerprint
        from services.research_configuration_context import build
        from services.research_supervisor_program import _economic_execution

        history = []
        for proposal in current["proposals"][-24:]:
            settings = proposal["settings"]
            facts = {}
            if proposal.get("job_id") and proposal.get("outcome"):
                native_job = supervisor.service.get_job(store, owner, proposal["job_id"])
                if native_job.status == "completed" and native_job.result_artifact:
                    native = supervisor.service.read_artifact(store, native_job.result_artifact)[
                        "result"
                    ]
                    # Funding requests can differ from the actual operating point.
                    settings = native["strategies"]
                    economic = (
                        _economic_execution(native)
                        if proposal.get("outcome") != "blocked"
                        else None
                    )
                    if economic is not None:
                        facts["exact_execution_id"] = fingerprint(economic)
                        risks = []
                        for row in economic["ledger"]:
                            detail = row.get("execution_details", {})
                            risks.append(
                                detail.pop("total_stop_risk_before", None)
                                if isinstance(detail, dict)
                                else None
                            )
                        facts["observed_execution_id"] = fingerprint(economic)
                        facts["stop_risk_state_id"] = fingerprint(risks)
            history.append(
                {
                    "evidence_id": proposal["id"],
                    "settings": settings,
                    "outcome": proposal.get("outcome"),
                    "reasons": proposal.get("reasons", []),
                    **facts,
                }
            )
        evidence["configuration_context"] = build(
            report["strategies"], history, current["experiment_options"]
        )
        evidence["experiment_options"] = [
            {k: v for k, v in o.items() if k != "settings"} for o in current["experiment_options"]
        ]
        evidence["experiment_feedback"] = current["experiment_feedback"]
        evidence["continuation_stop_reason"] = current.get("continuation_stop_reason")
        evidence["continuation_policy"] = current.get("continuation_policy")
        # Keep every immutable report in storage; bound the model's working
        # context by recent mechanisms rather than duplicating all reports.
        evidence["previous_experiments"] = [
            {
                "remedy": p["remedy"],
                "outcome": p.get("outcome"),
                "change": p.get("candidate", {}).get("change"),
                "reasons": [r[:240] for r in p.get("reasons", [])[:3]],
                "no_effect": bool(p.get("no_effect")),
            }
            for p in current["proposals"][-8:]
        ]
        evidence["current_account"] = {
            k: report.get("summary", {}).get(k)
            for k in (
                "initial_capital",
                "net_return_pct",
                "max_drawdown_pct",
                "closed_trades",
                "net_pnl",
                "sample_adequacy",
            )
        }
        if current.get("feasibility_only"):
            evidence["current_account"] = {
                "initial_capital": report["summary"]["initial_capital"],
                "scope": "Feasibility only; blocked performance is not decision evidence",
            }
        # Complete numerical reports remain in the validator. The selector
        # needs compact failed checks and causal options, not duplicated tables.
        evidence.pop("research_evidence", None)
    if current.get("research_context"):
        from services.research_playbook import experiment

        evidence["reviewed_contract"] = deepcopy(current["research_context"]["contract"])
        evidence["experiments"] = {
            remedy: experiment(remedy, permitted, "Select only if the recorded problem applies")
            for remedy in permitted["permitted_remedies"]
        }
        evidence["study_memory"] = {
            "scope": "Prior same-study outcomes are context, never independent validation",
            "entries": [
                {
                    key: row.get(key)
                    for key in ("remedy", "outcome", "no_effect", "same_inputs", "retest_reason")
                }
                for row in current.get("study_memory", {}).get("entries", [])[:6]
            ],
        }
    if current.get("experiment_policy") == supervisor.EXPERIMENT_POLICY:
        evidence.pop("experiments", None)
    return evidence


def run_one(store, worker_token, *, stop_requested=None, choose=None):
    # Provider configuration uses the app's scoped metadata session. A research
    # worker has no Flask request teardown, so own its cleanup on every exit.
    from database import agent_db

    try:
        return _run_one(store, worker_token, stop_requested=stop_requested, choose=choose)
    finally:
        agent_db.db_session.remove()


def _run_one(store, worker_token, *, stop_requested=None, choose=None):
    """Perform one real-worker continuation; never block a web/eventlet hub.

    ``choose`` is an injection seam for small qualification, not user input or
    a persisted callable. Production always uses the pinned existing adapter.
    """
    from services.agent import research_decision as planner

    if stop_requested and stop_requested():
        return False
    now = time.time()
    with store.sessions() as db:
        lease = db.get(ResearchWorker, 1)
        if lease is None or lease.token != worker_token or now - lease.heartbeat > 120:
            raise ValueError("Automatic research requires the active calculation worker lease")
        rows = db.scalars(
            select(ResearchDecisionJob)
            .where(ResearchDecisionJob.phase.in_(ACTIVE))
            .order_by(ResearchDecisionJob.updated_at)
            .limit(32)
        ).all()
        pending = [(r.supervisor_id, r.owner, r.phase, r.claim_token) for r in rows]
    for identifier, owner, phase, old_claim in pending:
        with store.sessions() as db:
            row = db.get(ResearchSupervisor, identifier)
            if row is None or row.owner != owner:
                raise ValueError("Automatic research grant binding changed")
            conversation = row.conversation_id
        current = supervisor.status(store, owner, identifier, conversation_id=conversation)
        # A committed decision can be applied idempotently after interruption;
        # an uncertain upstream call cannot be repeated or refunded.
        own_prefix = worker_token + ":"
        if (
            phase in ("deciding", "applying", "finalizing")
            and old_claim
            and old_claim.startswith(own_prefix)
        ):
            # The same worker cannot concurrently resume an operation which
            # still owns its claim. A restarted worker has a new lease token.
            continue
        if phase == "deciding":
            if old_claim == worker_token:
                continue
            _write_job(
                store,
                identifier,
                old_claim,
                phase="stopped",
                stop="The model decision was interrupted; its allowance remains consumed",
            )
            return True
        if current["status"] == "running":
            unfinished = next((p for p in current["proposals"] if not p.get("outcome")), None)
            if unfinished and unfinished.get("job_id"):
                native_job = supervisor.service.get_job(store, owner, unfinished["job_id"])
                if native_job.status in supervisor.service.ACTIVE or native_job.status == "paused":
                    continue
                current = supervisor.advance(store, owner, identifier, conversation_id=conversation)
            else:
                continue
        claim = own_prefix + uuid.uuid4().hex
        with store.sessions.begin() as db:
            supervisor.service.write_guard(db)
            job = db.get(ResearchDecisionJob, identifier)
            lease = db.get(ResearchWorker, 1)
            if job.phase != phase or lease.token != worker_token:
                continue
            job.claim_token, job.claimed_at, job.updated_at = claim, now, now
            job.phase = "finalizing" if current["status"] in ("accepted", "saved") else "applying"
        try:
            if current["status"] in ("accepted", "saved"):
                supervisor.save_result(
                    store,
                    owner,
                    identifier,
                    "Historically checked research result",
                    conversation_id=conversation,
                )
                opened = supervisor.reopen_result(
                    store, owner, identifier, conversation_id=conversation
                )
                verification = opened.get("verification", {})
                if verification.get("matched") is not True:
                    raise ValueError("Exact saved research identity did not reopen")
                _write_job(store, identifier, claim, phase="completed", reopened=verification)
                return True
            if current["status"] == "stopped":
                _write_job(store, identifier, claim, phase="stopped")
                return True
            # Recover a durable proposal or decision without a new model call.
            unfinished = next((p for p in current["proposals"] if not p.get("outcome")), None)
            if unfinished:
                supervisor.advance(store, owner, identifier, conversation_id=conversation)
                _write_job(store, identifier, claim, phase="waiting")
                return True
            if current.get("continuation_policy") == supervisor.ADAPTIVE_POLICY and current.get(
                "continuation_stop_reason"
            ):
                refreshed = supervisor.resume_frontier(
                    store, owner, identifier, conversation_id=conversation
                )
                if refreshed["source_result_artifact"] != current["source_result_artifact"]:
                    _write_job(store, identifier, claim, phase="waiting")
                    return True
                _write_job(
                    store,
                    identifier,
                    claim,
                    phase="stopped",
                    stop=refreshed["continuation_stop_reason"],
                    attention=False,
                )
                return True
            automatic = current["automation"]
            decision = next(
                (
                    d
                    for d in automatic["decisions"]
                    if d.get("seed_artifact") == current["source_result_artifact"]
                    and not any(
                        p.get("outcome")
                        and (
                            p.get("candidate", {}).get("id") == d.get("candidate_id")
                            if d.get("candidate_id")
                            else p["remedy"] == d.get("remedy")
                        )
                        for p in current["proposals"]
                    )
                ),
                None,
            )
            evidence = _evidence(store, owner, current)
            if decision is None:
                decision = None if choose else planner.native_decision(evidence)
                if decision is None:
                    if (
                        current.get("continuation_policy") == supervisor.ADAPTIVE_POLICY
                        and automatic["calls_spent"] >= automatic["max_model_calls"]
                    ):
                        _write_job(
                            store,
                            identifier,
                            claim,
                            phase="stopped",
                            attention=False,
                            stop=f"The {automatic['max_model_calls']}-call model safety limit is exhausted; {current['remaining_accounts']} of512 native accounts remain. Untested settings were not validated.",
                        )
                        return True
                    with store.sessions.begin() as db:
                        supervisor.service.write_guard(db)
                        row, state = supervisor._owned(db, owner, identifier, conversation)
                        policy = state["automation"]
                        if (
                            policy["model_id"] is None
                            or policy["calls_spent"] >= policy["max_model_calls"]
                        ):
                            raise ValueError(
                                f"The pinned model is unavailable or its {policy['max_model_calls']}-call "
                                f"safety allowance is exhausted ({policy['calls_spent']} consumed); "
                                f"{current['remaining_accounts']} native accounts remain unspent"
                            )
                        policy["calls_spent"] += 1
                        supervisor._put(row, state)
                        db.get(ResearchDecisionJob, identifier).phase = "deciding"
                    if stop_requested and stop_requested():
                        raise ValueError(
                            "Automatic research stopped before its reserved model call"
                        )
                    from services.agent.builder import resolve_model

                    if resolve_model(automatic["model_id"]).litellm_id != automatic["model"]:
                        raise ValueError(
                            "The authorized research model changed; no fallback is permitted"
                        )
                    decision = (choose or planner.select_decision)(evidence, automatic["model_id"])
                # Persist the selected action BEFORE submission. Crashes after
                # this write replay only deterministic idempotent operations.
                planner.validate_decision(
                    {
                        k: decision[k]
                        for k in (
                            "remedy",
                            "reason",
                            *(["candidate_id"] if "experiment_options" in evidence else []),
                        )
                    },
                    evidence,
                )
                decision = {**decision, "seed_artifact": current["source_result_artifact"]}
                _write_job(store, identifier, claim, phase="applying", decision=decision)
            if stop_requested and stop_requested():
                _write_job(
                    store,
                    identifier,
                    claim,
                    phase="cancelled",
                    stop="Research continuation cancelled",
                )
                return True
            result = planner.execute_plan(
                store, owner, identifier, conversation_id=conversation, decision=decision
            )
            if result.get("explicit_stop"):
                _write_job(
                    store,
                    identifier,
                    claim,
                    phase="stopped",
                    stop=decision["reason"],
                    attention=False,
                )
            else:
                _write_job(store, identifier, claim, phase="waiting")
            return True
        except Exception as error:
            if isinstance(error, planner.DecisionAuthenticationExpired):
                with store.sessions.begin() as db:
                    supervisor.service.write_guard(db)
                    row, saved = supervisor._owned(db, owner, identifier, conversation)
                    allowance = saved["automation"]
                    can_refresh = (
                        allowance.get("authentication_refreshes", 0) == 0
                        and allowance["calls_spent"] < allowance["max_model_calls"]
                    )
                    if can_refresh:
                        allowance["authentication_refreshes"] = 1
                        supervisor._put(row, saved)
                if can_refresh:
                    from services.agent.chatgpt_oauth import refresh_access_token

                    try:
                        refreshed, _ = refresh_access_token()
                    except Exception:
                        refreshed = False
                    if refreshed:
                        _write_job(store, identifier, claim, phase="queued")
                        return True
            # Persist only a safe classification; arbitrary provider errors can
            # contain credentials. Never retry an uncertain request implicitly.
            _write_job(
                store,
                identifier,
                claim,
                phase="stopped",
                stop="Automatic research stopped safely: " + type(error).__name__,
            )
            return True
    return False
