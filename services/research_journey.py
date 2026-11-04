"""Reviewed, persisted research journey over the ordinary bounded native worker.

HTTP only prepares/reviews/queues. Numerical work and configured-model decisions
belong to the existing worker; navigation or a disconnected browser owns no work.
"""

import json
import re
import time
from copy import deepcopy

from sqlalchemy import select

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchJourney,
    ResearchLibraryJob,
    ResearchPreparation,
    ResearchWorker,
)
from research.report_contract import fingerprint, settings_identity
from services import research_library as library
from services import research_preparation as preparation
from services import scanner_research_service as service

VERSION = "reviewed-research-journey-v1"
NO_RESULT = "None of the tested setups passed validation."
ACTIVE_PHASES = ("preparing", "researching", "finalizing")
MAX_STATE_BYTES = 131072


def _put(row, state, phase=None):
    raw = service.encoded(state).decode()
    if len(raw.encode()) > MAX_STATE_BYTES:
        raise ValueError("Research journey metadata exceeds its supported size")
    row.state, row.updated_at = raw, time.time()
    if phase is not None:
        row.phase = phase


def _owned(db, owner, identifier):
    library._owned(db, owner, identifier)
    row = db.get(ResearchJourney, identifier)
    if row is None or row.owner != owner:
        raise LookupError("Research journey not found")
    return row, json.loads(row.state)


def _response(store, owner, identifier):
    return {
        **status(store, owner, identifier),
        "experiment": library.get_experiment(store, owner, identifier),
    }


def _style(portfolio):
    styles = {
        "intraday" if r["config"].get("trade_horizon") == "intraday" else "positional"
        for r in portfolio["strategies"]
    }
    if len(styles) != 1:
        raise ValueError("Choose one trading style for this study in Settings")
    return styles.pop()


def _guided_draft(draft):
    from research.execution_policy import normalize_limits, normalize_policy
    from services.research_playbook import intent

    result = deepcopy(draft)
    p = result["portfolio"]
    if not p.get("strategies") or any(r.get("type") != "signals" for r in p["strategies"]):
        raise ValueError("Choose saved scanner or CSV signals for automatic research")
    limits = normalize_limits({**p.get("risk_limits", {}), "version": "research-risk-limits-v3"})
    p["risk_limits"] = limits
    for row in p["strategies"]:
        policy = row.get("execution_policy", {})
        row["execution_policy"] = normalize_policy(
            {
                **policy,
                "version": "research-execution-aware-v5",
                "risk_limits": limits,
                "allocation_mode": policy.get("allocation_mode", "original"),
                "capacity_mode": policy.get("capacity_mode", "prior20_broker_reported"),
                "correlation_mode": policy.get("correlation_mode", "prior60_positive_080"),
            }
        )
    p["engine"] = "vectorbt"
    p["automatic_research"] = {
        "version": "automatic-hypotheses-v16",
        "history_policy": "available_history",
        "design_policy": "automatic_design_v1",
        "decision_policy": "automatic_decision_v1",
        "action_policy": "automatic_actions_v3",
    }
    result["portfolio"] = intent(p, style=_style(p))
    result["optimizing"] = True
    # portfolio_payload owns the sampler/objective and recipe-derived full budget.
    result["optimization"] = deepcopy(
        result["portfolio"].get("optimization", result["optimization"])
    )
    return result


def prepare(store, owner, identifier, data):
    library._object(data, {"revision"}, "journey preparation")
    current = library.get_experiment(store, owner, identifier)
    if data.get("revision") != current["revision"]:
        raise library.RevisionConflict(current["revision"])
    with store.sessions() as db:
        prior = db.get(ResearchJourney, identifier)
        if prior and prior.owner != owner:
            raise LookupError("Research journey not found")
        running = bool(prior and prior.phase in ("researching", "finalizing"))
    if running:
        return _response(store, owner, identifier)
    draft = _guided_draft(current["draft"])
    if draft != current["draft"]:
        # Preserve earlier user work before opting a pre-existing study in.
        if current["job_count"]:
            with store.sessions.begin() as db:
                parent = library._owned(db, owner, identifier)
                library._revision(parent, current["revision"])
                library._freeze(
                    db, parent, current["draft"], name="Before reviewed automatic research"
                )
        current = library.save_draft(
            store, owner, identifier, {"revision": current["revision"], "draft": draft}
        )
    draft_id = fingerprint(current["draft"])
    with store.sessions.begin() as db:
        service.write_guard(db)
        parent = library._owned(db, owner, identifier)
        library._revision(parent, current["revision"])
        row = db.get(ResearchJourney, identifier)
        same = False
        history = []
        if row is not None:
            previous = json.loads(row.state)
            history = previous.get("history", [])[-24:]
            if previous.get("draft_id") == draft_id:
                same = row.phase != "needs_action" or bool(previous.get("job_id"))
        else:
            row = ResearchJourney(
                experiment_id=identifier,
                owner=owner,
                phase="preparing",
                state="{}",
                updated_at=time.time(),
            )
            db.add(row)
        if not same:
            _put(
                row,
                {
                    "version": VERSION,
                    "draft_id": draft_id,
                    "revision": parent.revision,
                    "plan": None,
                    "job_id": None,
                    "supervisor_id": None,
                    "result": None,
                    "error": None,
                    "retries": 0,
                    "history": history,
                },
                "preparing",
            )
    if same:
        return _response(store, owner, identifier)
    preparation.start(store, owner, identifier, {"revision": current["revision"]})
    _review_ready(store, owner, identifier)
    return _response(store, owner, identifier)


def edit_plan(store, owner, identifier, data):
    allowed = {
        "revision",
        "style",
        "capital",
        "order_size_pct",
        "hold_sessions",
        "stop_pct",
        "target_pct",
    }
    library._object(data, allowed, "research plan")
    current = library.get_experiment(store, owner, identifier)
    with store.sessions() as db:
        existing = db.get(ResearchJourney, identifier)
        if existing and existing.phase in ("researching", "finalizing"):
            raise ValueError(
                "This run is already using its reviewed settings; edit the next version after it finishes"
            )
    draft = deepcopy(current["draft"])
    p = draft["portfolio"]
    style = data.get("style", _style(p))
    if style not in ("intraday", "positional"):
        raise ValueError("Choose intraday or positional in Settings")
    if "capital" in data:
        p["capital"] = data["capital"]
    for row in p["strategies"]:
        cfg = row["config"]
        for key in ("order_size_pct", "hold_sessions", "stop_pct", "target_pct"):
            if key in data:
                cfg[key] = data[key]
        cfg["initial_capital"] = p["capital"]
        if style == "intraday":
            cfg.update(trade_horizon="intraday", exit_time=cfg.get("exit_time") or "15:15")
        else:
            for key in ("trade_horizon", "entry_time", "exit_time", "hold_minutes"):
                cfg.pop(key, None)
    saved = library.save_draft(
        store, owner, identifier, {"revision": data.get("revision"), "draft": _guided_draft(draft)}
    )
    return prepare(store, owner, identifier, {"revision": saved["revision"]})


def _prepared(store, owner, identifier):
    p = preparation.status(store, owner, identifier)
    if not p["ready"]:
        return p, None, None
    job = service.get_job(store, owner, p["job_id"])
    bundle = service.read_artifact(store, job.result_artifact)
    return p, service.read_artifact(store, bundle["inputs_artifact"]), bundle["inputs_artifact"]


def _review_ready(store, owner, identifier):
    from services.research_memory import read as read_memory
    from services.research_playbook import describe

    with store.sessions() as db:
        row, state = _owned(db, owner, identifier)
        if row.phase != "preparing":
            return False
        current = library._owned(db, owner, identifier)
        if fingerprint(json.loads(current.draft)) != state["draft_id"]:
            return False
        revision = current.revision
    progress, evidence, inputs_id = _prepared(store, owner, identifier)
    if evidence is None:
        if progress["status"] in ("failed", "interrupted", "cancelled"):
            with store.sessions.begin() as db:
                row, current = _owned(db, owner, identifier)
                current["error"] = (
                    progress.get("error")
                    or "Preparation needs attention; saved progress is retained."
                )
                _put(row, current, "needs_action")
            return True
        return False
    p = evidence["portfolio"]
    contract = describe(p)
    coverage = evidence.get("signal_coverage", {})
    excluded = [
        {"symbol": r.get("symbol", ""), "reason": r.get("reason", "Unavailable price window")}
        for r in coverage.get("exclusions", [])[:100]
    ]
    dates = sorted({s["date"] for s in evidence["signals"]})
    cfg = p["strategies"][0]["config"]
    memory = read_memory(store, owner, identifier, inputs_artifact=inputs_id)
    plan = {
        "revision": revision,
        "style": _style(p),
        "contract": contract,
        "objective": {
            "id": "robust_improvement",
            "label": "Robust historical improvement",
            "reason": contract.get(
                "objective_reason",
                "Improve the supported account while retaining all risk, cost and stability checks.",
            ),
        },
        "engine": {
            "id": p["engine"],
            "label": "VectorBT",
            "reason": "Supports this signal, trading-style and full-validation programme; Optuna supplies the parameter search.",
        },
        "data": {
            "stocks": len({s["symbol"] for s in evidence["signals"]}),
            "signals": len(evidence["signals"]),
            "from": dates[0] if dates else None,
            "to": dates[-1] if dates else None,
            "eligible_signals": coverage.get("eligible_signals", len(evidence["signals"])),
            "excluded": excluded,
            "excluded_count": len(coverage.get("exclusions", [])),
        },
        "settings": {
            "capital": p["capital"],
            **{k: cfg[k] for k in ("order_size_pct", "hold_sessions", "stop_pct", "target_pct")},
        },
        "tests": [
            "Full supported parameter search",
            "Native affordability and risk checks",
            "Higher costs, nearby settings and chronological checks",
        ],
        "estimate": {
            "seconds": None,
            "label": "First matching run: a reliable time estimate is not available yet.",
        },
        "memory": {
            **memory,
            "considered": len(memory.get("entries", [])),
            "retest_reason": "New data requires fresh validation; previous findings guide the plan."
            if memory.get("entries")
            else "First research for this study.",
        },
        "inputs_artifact": inputs_id,
        "preparation_job_id": progress["job_id"],
        "draft_id": state["draft_id"],
        "budget": evidence.get("automatic_recipe", {}).get(
            "budget", {"proposals": 408, "max_simulations": 512}
        ),
    }
    plan["id"] = fingerprint(plan)
    with store.sessions.begin() as db:
        service.write_guard(db)
        row, current = _owned(db, owner, identifier)
        parent = library._owned(db, owner, identifier)
        if (
            row.phase != "preparing"
            or current["draft_id"] != state["draft_id"]
            or parent.revision != revision
        ):
            return False
        current.update(plan=plan, error=None)
        _put(row, current, "review")
    return True


def start(store, owner, identifier, data):
    from database import agent_db
    from services.agent import builder
    from services.research_portfolio import (
        resolve_inputs,
        validate_prepared_inputs,
        validate_submission,
    )

    library._object(data, {"revision", "plan_id", "request_id"}, "reviewed research start")
    if not isinstance(data.get("request_id"), str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{8,128}", data["request_id"]
    ):
        raise ValueError("Supply a stable research request identity")
    with store.sessions() as db:
        row, state = _owned(db, owner, identifier)
        parent = library._owned(db, owner, identifier)
        repeated = (
            row.phase in ("researching", "finalizing", "complete", "none")
            and state.get("start_request") == data["request_id"]
        )
        if repeated:
            plan, draft = state["approved_plan"], json.loads(parent.draft)
        else:
            library._revision(parent, data.get("revision"))
            plan = state.get("plan")
            if (
                row.phase != "review"
                or not plan
                or plan["id"] != data.get("plan_id")
                or plan["revision"] != parent.revision
            ):
                raise ValueError("Review the current prepared plan before starting research")
            draft = json.loads(parent.draft)
            if fingerprint(draft) != plan["draft_id"]:
                raise ValueError("Settings changed; review the updated plan")
            pointer = db.get(ResearchPreparation, identifier)
            prepared_job = db.get(ResearchJob, pointer.job_id) if pointer else None
            if not prepared_job or prepared_job.status != "completed":
                raise ValueError("Data preparation must finish before research starts")
            if pointer.job_id != plan["preparation_job_id"]:
                raise ValueError("Prepared data changed; review the current plan")
    if repeated:
        return _response(store, owner, identifier)
    prepared = service.read_artifact(store, prepared_job.result_artifact)
    prepared_job_artifact = prepared_job.result_artifact
    if prepared.get("inputs_artifact") != plan["inputs_artifact"]:
        raise ValueError("Prepared data changed; review the current plan")
    try:
        model = builder.resolve_model()
    finally:
        agent_db.db_session.remove()
    evidence = resolve_inputs(store, owner, library.portfolio_payload(draft))
    spec = validate_submission(
        evidence,
        "portfolio_optimize",
        {"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    )
    approved_inputs = service.read_artifact(store, plan["inputs_artifact"])
    validate_prepared_inputs(store, approved_inputs, spec)
    if approved_inputs["signals"] != evidence["signals"]:
        raise ValueError("Signals changed; review the current data plan")
    # The reviewed snapshot is immutable. Pin it at admission, rather than let
    # a later cache/freshness miss silently acquire a different data set.
    checkpoint_state = {
        "phase": "calculation",
        "inputs_artifact": plan["inputs_artifact"],
        "calculation": None,
    }
    launch = {"revision": data["revision"], "request_id": data["request_id"]}
    token, request_hash = library._request({**launch, "experiment_id": identifier}, "run")

    def guard(db, parent):
        row, current = _owned(db, owner, identifier)
        if row.phase != "review" or current.get("plan", {}).get("id") != plan["id"]:
            raise ValueError("The reviewed plan changed before admission")
        pointer = db.get(ResearchPreparation, identifier)
        if not pointer or pointer.job_id != plan["preparation_job_id"]:
            raise ValueError("Prepared data changed before admission")
        prepared_job = db.get(ResearchJob, pointer.job_id)
        if not prepared_job or prepared_job.result_artifact != prepared_job_artifact:
            raise ValueError("Prepared evidence changed before admission")

    def publish(db, parent, version, job_id):
        row, current = _owned(db, owner, identifier)
        current.update(
            job_id=job_id,
            start_request=data["request_id"],
            approved_plan=deepcopy(plan),
            model_id=model.id,
            model_name=model.litellm_id,
            error=None,
        )
        _put(row, current, "researching")

    library._enqueue(
        store,
        owner,
        identifier,
        launch,
        token,
        request_hash,
        draft,
        evidence,
        admission_guard=guard,
        publication_hook=publish,
        initial_checkpoint_state=checkpoint_state,
    )
    return _response(store, owner, identifier)


def status(store, owner, identifier):
    from services import research_supervisor as supervisor

    with store.sessions() as db:
        parent = library._owned(db, owner, identifier)
        row = db.get(ResearchJourney, identifier)
        if row is None:
            empty = {
                "version": VERSION,
                "phase": "preparing",
                "revision": parent.revision,
                "preparation": None,
                "plan": None,
                "run": None,
                "decisions": [],
                "result": None,
                "can_start": False,
                "error": None,
            }
        elif row.owner != owner:
            raise LookupError("Research journey not found")
        else:
            phase, state, revision = row.phase, json.loads(row.state), parent.revision
    if row is None:
        empty["preparation"] = preparation.status(store, owner, identifier)
        return empty
    run = None
    if state.get("job_id"):
        job = service.get_job(store, owner, state["job_id"])
        receipt = service.job_receipt(store, job, experiment_id=identifier)
        run = {
            "job_id": job.id,
            "status": job.status,
            "progress": job.progress,
            "stage": receipt.get("counts", {}).get("stage", "Researching"),
            "activity": receipt.get("activity"),
        }
    decisions = []
    if state.get("supervisor_id"):
        current = supervisor.status(
            store, owner, state["supervisor_id"], conversation_id="journey:" + identifier
        )
        decisions = [
            {**d, **proposal, **proposal.get("experiment", proposal.get("playbook", {}))}
            for d in current.get("automation", {}).get("decisions", [])
            for proposal in [
                next(
                    (
                        p
                        for p in current["proposals"]
                        if p["remedy"] == d.get("remedy")
                        and (
                            p.get("candidate", {}).get("id") == d.get("candidate_id")
                            if d.get("candidate_id")
                            else not p.get("candidate")
                        )
                    ),
                    {},
                )
            ]
        ]
        pending = next(
            (p for p in current["proposals"] if p.get("job_id") and not p.get("outcome")), None
        )
        if pending:
            job = service.get_job(store, owner, pending["job_id"])
            run = {
                "job_id": job.id,
                "status": job.status,
                "progress": job.progress,
                "stage": "Validating changed settings",
                "activity": None,
            }
    return {
        "version": VERSION,
        "phase": phase,
        "revision": revision,
        "preparation": preparation.status(store, owner, identifier),
        "plan": state.get("plan"),
        "run": run,
        "decisions": decisions,
        "result": state.get("result"),
        "can_start": phase == "review"
        and bool(state.get("plan"))
        and revision == state["plan"]["revision"],
        "error": state.get("error"),
    }


def _finish(store, owner, identifier, result, *, accepted=False):
    actual_inputs = None
    if result.get("job_id"):
        job = service.get_job(store, owner, result["job_id"])
        if job.result_artifact:
            actual_inputs = service.read_artifact(store, job.result_artifact).get("inputs_artifact")
    with store.sessions.begin() as db:
        service.write_guard(db)
        row, state = _owned(db, owner, identifier)
        state.update(
            result={
                **result,
                "accepted": accepted,
                "headline": "Historically validated research result" if accepted else NO_RESULT,
            },
            error=None,
        )
        plan = state.get("approved_plan") or state.get("plan") or {}
        record = {
            "job_id": result.get("job_id"),
            "inputs_artifact": actual_inputs or plan.get("inputs_artifact"),
            "prepared_inputs_artifact": plan.get("inputs_artifact"),
            "contract_id": plan.get("contract", {}).get("id"),
            "outcome": "validated" if accepted else "rejected",
            "reasons": [] if accepted else [NO_RESULT],
            "updated_at": time.time(),
        }
        state["history"] = [
            r for r in state.get("history", []) if r.get("job_id") != record["job_id"]
        ][-23:] + [record]
        _put(row, state, "complete" if accepted else "none")


def _link(store, owner, identifier, job_id):
    with store.sessions.begin() as db:
        service.write_guard(db)
        library._owned(db, owner, identifier)
        job = db.get(ResearchJob, job_id)
        if not job or job.owner != owner:
            raise LookupError("Research result not found")
        if db.get(ResearchLibraryJob, (identifier, job_id)) is None:
            db.add(
                ResearchLibraryJob(
                    experiment_id=identifier,
                    job_id=job_id,
                    role="automatic",
                    created_at=time.time(),
                )
            )


def _advance(store, owner, identifier):
    from services import research_supervisor as supervisor

    with store.sessions() as db:
        row, state = _owned(db, owner, identifier)
        phase = row.phase
        if phase not in ("preparing", "researching", "finalizing"):
            return False
    if phase == "preparing":
        return _review_ready(store, owner, identifier)
    if state.get("supervisor_id"):
        current = supervisor.status(
            store, owner, state["supervisor_id"], conversation_id="journey:" + identifier
        )
        for p in current["proposals"]:
            if p.get("job_id"):
                _link(store, owner, identifier, p["job_id"])
        if current["automatic_phase"] not in ("completed", "stopped", "cancelled"):
            return False
        if current.get("saved_result"):
            saved = current["saved_result"]
            opened = supervisor.reopen_result(
                store, owner, current["id"], conversation_id="journey:" + identifier
            )
            if not (
                opened["verification"]["matched"]
                and opened["verification"]["new_calculations"] == 0
            ):
                raise ValueError("Saved research result did not reopen exactly")
            _finish(
                store,
                owner,
                identifier,
                {
                    "job_id": current["accepted_job_id"],
                    "experiment_id": identifier,
                    "choice_id": saved["choice_id"],
                    "before": state.get("baseline", {}),
                    "after": opened["summary"],
                    "verification": opened["verification"],
                },
                accepted=True,
            )
        elif current.get("automation", {}).get("attention"):
            with store.sessions.begin() as db:
                row, fresh = _owned(db, owner, identifier)
                fresh["error"] = current["automation"]["attention"]
                _put(row, fresh, "needs_action")
        else:
            last = next(
                (p["job_id"] for p in reversed(current["proposals"]) if p.get("job_id")),
                state["job_id"],
            )
            job = service.get_job(store, owner, last)
            report = (
                service.read_artifact(store, job.result_artifact)["result"]
                if job.result_artifact
                else {}
            )
            _finish(
                store,
                owner,
                identifier,
                {
                    "job_id": job.id,
                    "experiment_id": identifier,
                    "before": state.get("baseline", {}),
                    "after": report.get("summary", {}),
                    "verification": _reopen_native(store, owner, job.id),
                    "stopping_reason": current.get("stopping_reason"),
                },
            )
        return True
    job = service.get_job(store, owner, state["job_id"])
    if job.status in service.ACTIVE:
        return False
    if job.status in ("failed", "interrupted") and state.get("retries", 0) < 1:
        try:
            service.resume(store, owner, job.id)
        except (ValueError, LookupError):
            pass
        else:
            with store.sessions.begin() as db:
                row, current = _owned(db, owner, identifier)
                current["retries"] = current.get("retries", 0) + 1
                _put(row, current)
            return True
    if job.status != "completed":
        with store.sessions.begin() as db:
            row, current = _owned(db, owner, identifier)
            current["error"] = (
                job.error or "Research stopped. Saved progress is retained; resume it from the run."
            )
            _put(row, current, "needs_action")
        return True
    bundle = service.read_artifact(store, job.result_artifact)
    report = bundle["result"]
    findings = report.get("automatic_research", {})
    if findings.get("operating_validation", {}).get("historically_validated"):
        return _save_search_result(store, owner, identifier, state, job, bundle)
    plan = state["approved_plan"]
    adaptive = findings.get("recipe", {}).get("validation_mode") == "exploratory_only"
    result = supervisor.start_automatic(
        store,
        owner,
        job.id,
        conversation_id="journey:" + identifier,
        request_id="journey_" + plan["id"][:48],
        max_experiments=supervisor.ADAPTIVE_EXPERIMENT_LIMIT
        if adaptive
        else supervisor.CONCRETE_EXPERIMENT_LIMIT,
        max_model_calls=supervisor.ADAPTIVE_EXPERIMENT_LIMIT if adaptive else 3,
        model_id=state["model_id"],
        research_context={
            "experiment_id": identifier,
            "inputs_artifact": bundle["inputs_artifact"],
            "contract": plan["contract"],
        },
        experiment_policy=supervisor.EXPERIMENT_POLICY,
        continuation_policy=supervisor.ADAPTIVE_POLICY if adaptive else None,
    )
    with store.sessions.begin() as db:
        row, fresh = _owned(db, owner, identifier)
        fresh.update(
            supervisor_id=result["id"], baseline=findings.get("baseline", report.get("summary", {}))
        )
        _put(row, fresh, "researching")
    return True


def _reopen_native(store, owner, job_id):
    """Reopen immutable saved evidence twice; never calculate to manufacture proof."""
    job = service.get_job(store, owner, job_id)
    if job.status != "completed" or not job.result_artifact:
        raise ValueError("A completed saved result is required")
    first = service.read_artifact(store, job.result_artifact)
    again = service.read_artifact(store, job.result_artifact)
    matched = fingerprint(first) == job.result_artifact == fingerprint(again)
    if not matched:
        raise ValueError("Saved research result changed during reopening")
    return {
        "matched": True,
        "new_calculations": 0,
        "result_artifact": job.result_artifact,
        "settings_id": settings_identity(again["result"]["strategies"]),
    }


def _save_search_result(store, owner, identifier, state, job, bundle):
    """Use native Keep/choose gates; never forge a supervisor validation wrapper."""
    from database.research_db import ResearchChosenRequest, ResearchDecisionRequest
    from services import research_chosen_setups as chosen
    from services import research_decisions as decisions
    from services import research_mcp as mcp
    from services.research_playbook import validate_full_search_result

    report = bundle["result"]
    validation = validate_full_search_result(report, state["approved_plan"]["contract"])
    with store.sessions.begin() as db:
        row, fresh = _owned(db, owner, identifier)
        _put(row, fresh, "finalizing")
    expected = {"result_artifact": job.result_artifact, "analysis_artifact": None}
    context = decisions.direct_context(store, owner, identifier, job.id, expected_report=expected)
    revision = (context.get("current") or {}).get("revision", 0)
    token = "journey_save_" + state["approved_plan"]["id"][:40]
    with store.sessions() as db:
        prior = db.get(ResearchDecisionRequest, (owner, token + "_keep"))
        if prior:
            revision = json.loads(prior.payload)["revision"]
    kept = decisions.save_direct_decision(
        store,
        owner,
        identifier,
        job.id,
        {
            "request_id": token + "_keep",
            "revision": revision,
            "state": "keep",
            "reason": "Exact native operating account passed all historical validation; not independent market evidence.",
            "expected_report": expected,
        },
    )
    current = chosen.context(store, owner, identifier)
    revision = current["revision"]
    with store.sessions() as db:
        prior = db.get(ResearchChosenRequest, (owner, token + "_choose"))
        if prior:
            revision = json.loads(prior.payload)["revision"]
    saved = mcp.save_chosen_result(
        store,
        owner,
        identifier,
        decision_id=kept["decision"]["id"],
        event_id=kept["event"]["id"],
        name="Best validated research result",
        revision=revision,
        request_id=token + "_choose",
    )
    opened = mcp.reopen_chosen_result(store, owner, identifier, saved["choice"]["id"])
    if not opened["verification"]["matched"] or opened["verification"]["new_calculations"] != 0:
        raise ValueError("Saved research result did not reopen exactly")
    _finish(
        store,
        owner,
        identifier,
        {
            "job_id": job.id,
            "experiment_id": identifier,
            "choice_id": saved["choice"]["id"],
            "before": report["automatic_research"].get("baseline", {}),
            "after": opened["summary"],
            "verification": opened["verification"],
            "validation": validation,
        },
        accepted=True,
    )
    return True


def run_one(store, worker_token, *, stop_requested=None):
    if stop_requested and stop_requested():
        return False
    with store.sessions() as db:
        lease = db.get(ResearchWorker, 1)
        if lease is None or lease.token != worker_token or time.time() - lease.heartbeat > 120:
            raise ValueError("Research journey requires its active worker lease")
        rows = [
            (r.experiment_id, r.owner)
            for r in db.scalars(
                select(ResearchJourney)
                .where(ResearchJourney.phase.in_(ACTIVE_PHASES))
                .order_by(ResearchJourney.updated_at)
                .limit(16)
            )
        ]
    for identifier, owner in rows:
        try:
            if _advance(store, owner, identifier):
                return True
        except (ValueError, LookupError) as error:
            with store.sessions.begin() as db:
                row, state = _owned(db, owner, identifier)
                state["error"] = str(error)[:500]
                _put(row, state, "needs_action")
            return True
    return False
