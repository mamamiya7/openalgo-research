"""Bind AI refinement to the approved journey's pinned model and worker lease."""

import json

from sqlalchemy import select

from database.research_db import ResearchJourney, ResearchLibraryJob


def planner_for(store, owner, job_id):
    with store.sessions() as db:
        rows = db.scalars(
            select(ResearchJourney)
            .join(
                ResearchLibraryJob,
                ResearchLibraryJob.experiment_id == ResearchJourney.experiment_id,
            )
            .where(ResearchJourney.owner == owner, ResearchLibraryJob.job_id == job_id)
            .limit(2)
        ).all()
        matches = [
            (r.experiment_id, json.loads(r.state))
            for r in rows
            if json.loads(r.state).get("job_id") == job_id
        ]
    if len(matches) != 1:
        raise ValueError("Hands-off planning needs its exact approved journey and pinned model")
    journey_id, state = matches[0]
    model_id, model_name = state.get("model_id"), state.get("model_name")
    if type(model_id) is not int or not model_name:
        raise ValueError("Hands-off planning has no approved configured model")

    approved_flow = state.get("approved_plan", {}).get("flow")
    update = state.get("controller_update")
    if (
        update is None
        and state.get("approved_plan", {}).get("controller_revision") == "research-controller-v2"
    ):
        from research.report_contract import fingerprint

        body = {
            "version": "research-controller-v2",
            "job_id": job_id,
            "plan_id": state["approved_plan"]["id"],
        }
        update = {**body, "id": fingerprint(body)}
    if update is not None:
        from research.report_contract import fingerprint

        if (
            update.get("version") != "research-controller-v2"
            or update.get("job_id") != job_id
            or update.get("plan_id") != state.get("approved_plan", {}).get("id")
            or update.get("id") != fingerprint({k: v for k, v in update.items() if k != "id"})
        ):
            raise ValueError("The reviewed controller update changed")

    def choose(evidence):
        from database import agent_db
        from services.agent import builder, research_planning

        try:
            if builder.resolve_model(model_id).litellm_id != model_name:
                raise ValueError("The approved research model changed; no fallback is allowed")
            plan = research_planning.select_plan(evidence, model_id)
            from services import research_journey as journey

            compact = {
                k: plan[k]
                for k in (
                    "version",
                    "action",
                    "method",
                    "hypothesis",
                    "reason",
                    "expected_evidence",
                    "stop_rule",
                    "domains",
                    "id",
                )
            }
            with store.sessions.begin() as db:
                row, current = journey._owned(db, owner, journey_id)
                if current.get("job_id") != job_id or current.get("model_id") != model_id:
                    raise ValueError("The approved hands-off journey changed during planning")
                decisions = current.setdefault("planning_decisions", [])
                if not any(d.get("planning", {}).get("id") == plan["id"] for d in decisions):
                    decisions.append(
                        {
                            "planning": compact,
                            "reason": plan["reason"],
                            "remedy": plan["action"]
                            if plan["action"] != "search"
                            else plan["method"],
                            "selection": plan.get("selection", "model"),
                            "provider_called": plan.get("provider_called", True),
                            "settings_change": {"status": "proposed", "rows": []},
                        }
                    )
                journey._put(row, current)
            return plan
        finally:
            agent_db.db_session.remove()

    if update is not None:
        choose.controller_update = update

        def feedback_reference():
            from database.research_db import ResearchExperiment

            with store.sessions() as db:
                experiment = db.get(ResearchExperiment, job_id)
                if experiment is None or not experiment.checkpoint:
                    raise ValueError("Planning feedback requires its published checkpoint")
                return {"checkpoint": experiment.checkpoint, "job_id": job_id}

        choose.feedback_reference = feedback_reference

        def report_controller_event(event_id, reason):
            from services import research_journey as journey

            with store.sessions.begin() as db:
                row, current = journey._owned(db, owner, journey_id)
                if current.get("job_id") != job_id:
                    raise ValueError("The controller event no longer belongs to this run")
                events = current.setdefault("controller_events", [])
                if event_id not in events:
                    if len(events) >= 36:
                        raise ValueError("Controller event history reached its bound")
                    events.append(event_id)
                    current.setdefault("planning_decisions", []).append(
                        {
                            "remedy": "approved_search_continuation",
                            "reason": reason,
                            "selection": "native",
                            "provider_called": False,
                            "settings_change": {"status": "proposed", "rows": []},
                        }
                    )
                    journey._put(row, current)

        choose.report_controller_event = report_controller_event
    if approved_flow is not None:
        from research.user_flow import normalize

        choose.research_flow = normalize(approved_flow)
        if choose.research_flow["involvement"] == "guided":

            def gate(record):
                from services import research_journey as journey

                plan = record["plan"]
                with store.sessions.begin() as db:
                    row, current = journey._owned(db, owner, journey_id)
                    if (
                        current.get("job_id") != job_id
                        or current.get("approved_plan", {}).get("flow") != approved_flow
                    ):
                        raise ValueError("The guided research authority changed")
                    decision = current.get("approval_decisions", {}).get(plan["id"])
                    if decision:
                        return decision
                    current["pending_approval"] = {
                        "id": plan["id"],
                        "job_id": job_id,
                        "kind": plan["action"],
                        "reason": plan["reason"],
                        "hypothesis": plan["hypothesis"],
                        "domains": plan["domains"],
                    }
                    journey._put(row, current, "awaiting_approval")
                from services import scanner_research_service as service

                service.pause(store, owner, job_id)
                return None

            choose.approval_gate = gate
    return choose


def admit_completed_rejection(store, owner, identifier, *, job_id, checkpoint_id, request_id):
    """Operator recovery for the exact legacy compiler rejection identified in logs.

    This is not an inference about an arbitrary interrupted provider request.
    The original immutable checkpoint, calls and financial accounts survive.
    """
    from database.research_db import ResearchExperiment, ResearchJob
    from research.report_contract import fingerprint
    from services import research_journey as journey
    from services import scanner_research_service as service

    with store.sessions() as db:
        _, current = journey._owned(db, owner, identifier)
        job = db.get(ResearchJob, job_id)
        experiment = db.get(ResearchExperiment, job_id)
        if (
            current.get("job_id") != job_id
            or not job
            or job.owner != owner
            or job.status != "failed"
            or not experiment
            or experiment.checkpoint != checkpoint_id
        ):
            raise ValueError("Recovery requires the exact failed job and checkpoint")
    saved = service.read_artifact(store, checkpoint_id)["state"]["calculation"]
    book = saved["hands_off_planning"]
    key = book.get("active")
    if (
        not key
        or book["records"][key].get("request_id") != request_id
        or book["records"][key].get("phase") != "requesting"
    ):
        raise ValueError("The legacy rejected request changed")
    body = {
        "version": "research-controller-v2",
        "job_id": job_id,
        "plan_id": current["approved_plan"]["id"],
        "known_rejection": {
            "boundary_key": key,
            "checkpoint_id": checkpoint_id,
            "request_id": request_id,
            "error": "AI proposed a value outside the frozen data/search domain",
        },
    }
    update = {**body, "id": fingerprint(body)}
    with store.sessions.begin() as db:
        row, latest = journey._owned(db, owner, identifier)
        if (
            latest.get("job_id") != job_id
            or latest.get("approved_plan", {}).get("id") != body["plan_id"]
        ):
            raise ValueError("The approved run changed during recovery admission")
        if latest.get("controller_update") not in (None, update):
            raise ValueError("A different controller update is already recorded")
        latest["controller_update"] = update
        journey._put(row, latest)
    return update


def resume_updated_controller(store, owner, identifier):
    from services import research_journey as journey
    from services import scanner_research_service as service

    with store.sessions() as db:
        _, current = journey._owned(db, owner, identifier)
    if not current.get("controller_update") and current.get("approved_plan", {}).get("controller_revision") != "research-controller-v2":
        raise ValueError("The failed run has no authorized controller update")
    job = service.get_job(store, owner, current["job_id"])
    if job.status in ("failed", "interrupted", "paused"):
        service.resume(store, owner, job.id)
    elif job.status not in ("queued", "running", "completed"):
        raise ValueError("The preserved run is not resumable")
    with store.sessions.begin() as db:
        row, current = journey._owned(db, owner, identifier)
        current["error"] = None
        journey._put(row, current, "researching")
    return journey._response(store, owner, identifier)


def resolve_approval(store, owner, identifier, data):
    """Resume a compiled plan once, without another model call or budget grant."""
    from database.research_db import ResearchDecisionJob
    from services import research_journey as journey
    from services import scanner_research_service as service

    with store.sessions() as db:
        row, state = journey._owned(db, owner, identifier)
        pending = state.get("pending_approval")
    prior = state.get("approval_decisions", {}).get(data["approval_id"])
    if pending is None and prior == data["decision"]:
        return journey._response(store, owner, identifier)
    if (
        not pending
        or pending["id"] != data["approval_id"]
        or pending["job_id"] != state.get("job_id")
    ):
        raise ValueError("This approval is stale; review the current proposal")
    if pending["kind"] != "continuation":
        job = service.get_job(store, owner, pending["job_id"])
        if job.status != "paused" and not (
            prior == data["decision"] and job.status in ("queued", "running", "completed")
        ):
            raise ValueError("Research is finishing its safe pause; try again shortly")
    with store.sessions.begin() as db:
        row, current = journey._owned(db, owner, identifier)
        if current.get("pending_approval") != pending:
            raise ValueError("The pending research proposal changed")
        decisions = current.setdefault("approval_decisions", {})
        if len(decisions) >= 36 and pending["id"] not in decisions:
            raise ValueError("The bounded approval history is full")
        previous = decisions.get(pending["id"])
        if previous is not None and previous != data["decision"]:
            raise ValueError("This proposal already has a different approval decision")
        decisions[pending["id"]] = data["decision"]
        if pending["kind"] == "continuation":
            decision_job = db.get(ResearchDecisionJob, current["supervisor_id"])
            if decision_job is None or decision_job.phase != "approval":
                raise ValueError("The guided continuation no longer awaits approval")
            decision_job.phase = "queued"
            current["pending_approval"] = None
            journey._put(row, current, "researching")
        else:
            journey._put(row, current)
    if pending["kind"] != "continuation":
        job = service.get_job(store, owner, pending["job_id"])
        if job.status == "paused":
            service.resume(store, owner, job.id)
        elif job.status not in ("queued", "running", "completed"):
            raise ValueError("Research is finishing its safe pause; try again shortly")
        with store.sessions.begin() as db:
            row, current = journey._owned(db, owner, identifier)
            if current.get("pending_approval") not in (None, pending):
                raise ValueError("The pending research proposal changed")
            current["pending_approval"] = None
            journey._put(row, current, "researching")
    return journey._response(store, owner, identifier)


def continuation_gate(store, owner, supervisor_id, current, decision):
    """Persist a later experiment and wait; never ask the model again on approval."""
    authority = current.get("research_context") or {}
    flow = authority.get("flow") or {}
    if flow.get("involvement") != "guided" or decision.get("remedy") == "stop":
        return "approve"
    from database.research_db import ResearchDecisionJob
    from research.report_contract import fingerprint
    from services import research_journey as journey

    key = fingerprint({"supervisor": supervisor_id, "decision": decision})
    with store.sessions.begin() as db:
        row, state = journey._owned(db, owner, authority["experiment_id"])
        if (
            state.get("supervisor_id") != supervisor_id
            or state.get("approved_plan", {}).get("flow") != flow
        ):
            raise ValueError("Guided continuation no longer has its reviewed authority")
        response = state.get("approval_decisions", {}).get(key)
        if response:
            return response
        option = next(
            (
                o
                for o in current.get("experiment_options", [])
                if o.get("id") == decision.get("candidate_id")
            ),
            {},
        )
        state["pending_approval"] = {
            "id": key,
            "job_id": state["job_id"],
            "kind": "continuation",
            "reason": decision["reason"],
            "hypothesis": option.get("hypothesis", decision["remedy"]),
            "domains": {},
            "settings": option.get("settings", []),
        }
        journey._put(row, state, "awaiting_approval")
        task = db.get(ResearchDecisionJob, supervisor_id)
        task.phase = "approval"
    return None
