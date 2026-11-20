"""Durable, owner/conversation-bound authority for saved-data research only.

The confirmed Agent opening grants a finite plan. Continuations cannot widen
that authority, reset its budget, submit arbitrary settings or reach a broker.
Native worker jobs perform calculations; request threads never backtest.
"""

import json
import re
import time
import uuid
from collections import Counter, defaultdict
from copy import deepcopy

from sqlalchemy import func, select

from database.research_db import ResearchDecisionJob, ResearchSupervisor
from research.report_contract import fingerprint, settings_identity
from services import scanner_research_service as service

VERSION = "research-supervisor-v1"
REMEDIES = ("funding_and_size", "lower_turnover", "reduce_concentration", "temporal_stability")
MAX_LEDGER_BYTES = 131072
DECISION_CATALOG_VERSION = "executable-development-remedies-v5"
EXPERIMENT_POLICY = "evidence-candidates-v1"
CONCRETE_EXPERIMENT_LIMIT = 4  # one native feasibility repair + three bounded model choices
ADAPTIVE_POLICY = "adaptive-feedback-v1"
# Matches the bounded candidate/memory ledger. Numerical work is independently
# limited to the original512 accounts and requires the entire validation reserve.
ADAPTIVE_EXPERIMENT_LIMIT = 24


def experiment_limit(policy):
    return ADAPTIVE_EXPERIMENT_LIMIT if policy == ADAPTIVE_POLICY else CONCRETE_EXPERIMENT_LIMIT


def validation_reserve(remedy, mode="exploratory_only"):
    return (17 if remedy == "funding_and_size" else 14) if mode == "exploratory_only" else 24


def _conversation(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not 1 <= len(str(value)) <= 128
    ):
        raise ValueError("Research authorization needs its trusted conversation")
    return str(value)


def _owned(db, owner, identifier, conversation_id):
    row = db.get(ResearchSupervisor, identifier)
    if row is None or row.owner != owner or row.conversation_id != _conversation(conversation_id):
        raise LookupError("This conversation has no authorized research plan")
    return row, json.loads(row.state)


def _put(row, state):
    raw = service.encoded(state).decode()
    if len(raw.encode()) > MAX_LEDGER_BYTES:
        raise ValueError("Research supervisor history reached its bounded limit")
    row.state, row.updated_at = raw, time.time()


def _parent(store, owner, identifier):
    job = service.get_job(store, owner, identifier)
    if job.status != "completed" or not job.result_artifact:
        raise ValueError("Choose a completed saved automatic research study")
    bundle = service.read_artifact(store, job.result_artifact)
    report = bundle["result"]
    recipe = report.get("automatic_research", {}).get("recipe", report.get("automatic_recipe", {}))
    if recipe.get("version") != "automatic-hypotheses-v16":
        raise ValueError("This supervisor needs a saved v16 study and its frozen recipe")
    return job, bundle, report, recipe


def _remaining(state):
    committed = sum(
        p["spent"] if p.get("outcome") else p["reserved_accounts"] for p in state["proposals"]
    )
    return max(0, 512 - state["original_attempts"] - committed)


def _lost(state):
    return (
        state["original_attempts"]
        - state["original_simulations"]
        + sum(p["spent"] - p.get("completed", 0) for p in state["proposals"])
    )


def _planning_source(state, root_job, root_artifact):
    recovery = state.get("blocked_recovery")
    return (
        (recovery["job_id"], recovery["artifact"])
        if recovery
        else (
            state.get("latest_seed_job_id", root_job),
            state.get("latest_source_result_artifact", root_artifact),
        )
    )


def feasibility_evidence(store, owner, seed, report):
    """Verify feasibility without treating a blocked account as a performance seed.

    Old saved reports retain their immutable identity. Their native checkpoint
    supplies the already frozen plan, reproduced by the unchanged pure planner.
    """
    from database.research_db import ResearchExperiment
    from research.connectors.registry import policy_for_request
    from research.operating_validation import build_plan

    value = report.get("supervisor_validation", {})
    with store.sessions() as db:
        row = db.get(ResearchSupervisor, value.get("supervisor_id"))
        if row is None or row.owner != owner or seed.owner != owner or seed.status != "completed":
            raise ValueError("Blocked feasibility ownership changed")
        saved = json.loads(row.state)
        proposal = next((p for p in saved["proposals"] if p.get("job_id") == seed.id), None)
        experiment = db.get(ResearchExperiment, seed.id)
        root_id, supervisor_id = row.root_job_id, row.id
        checkpoint = experiment.checkpoint if experiment else None
        identity = experiment.identity if experiment else None
        specification = json.loads(experiment.specification) if experiment else None
    if proposal is None or value.get("proposal_id") != proposal["id"]:
        raise ValueError("Blocked feasibility proposal changed")
    checked = _verified_conclusion(report, supervisor_id, proposal["id"], root_id, store, owner)
    if checked["outcome"] != "blocked":
        raise ValueError("Feasibility recovery needs a blocked native result")
    evidence = value.get("feasibility")
    if evidence is None:
        if not checkpoint:
            raise ValueError("Blocked feasibility checkpoint is unavailable")
        envelope = service.read_artifact(store, checkpoint)
        source = service.source_for(store, owner, seed.source_id)
        if envelope.get("identity") != identity or envelope.get(
            "policy_version"
        ) != policy_for_request(source["snapshot"], specification):
            raise ValueError("Blocked feasibility checkpoint identity changed")
        calculation = envelope.get("state", {}).get("calculation") or {}
        plan = calculation.get("validation_plan")
        if (
            calculation.get("proposal_id") != proposal["id"]
            or not isinstance(plan, dict)
            or plan != build_plan(**plan["inputs"])
            or plan["id"] != checked["operating_validation"].get("plan_id")
            or plan["selected_config_id"] != checked["selected_config_id"]
            or plan["capital"] != checked["capital"]
            or fingerprint(calculation.get("selected_report")) != checked["native_report_id"]
        ):
            raise ValueError("Blocked native feasibility plan changed")
        evidence = {
            "version": "research-feasibility-v1",
            "source_settings_id": plan["selected_config_id"],
            "counts": deepcopy(plan["inputs"]["capacity_counts"]),
            "unresolved_constraints": deepcopy(plan["unresolved_constraints"]),
            "scope": "feasibility_only",
        }
    if (
        evidence.get("version") != "research-feasibility-v1"
        or evidence.get("scope") != "feasibility_only"
        or evidence.get("source_settings_id") != settings_identity(report["strategies"])
        or (
            evidence.get("counts") is not None
            and (
                not isinstance(evidence["counts"], dict)
                or any(type(v) is not int or v < 0 for v in evidence["counts"].values())
            )
        )
        or not isinstance(evidence.get("unresolved_constraints"), dict)
    ):
        raise ValueError("Blocked feasibility evidence is invalid or unbound")
    return deepcopy(evidence)


def planning_report(store, owner, seed, report):
    if report.get("supervisor_validation", {}).get("outcome") != "blocked":
        return report
    result = deepcopy(report)
    result["supervisor_validation"]["feasibility"] = feasibility_evidence(
        store, owner, seed, report
    )
    return result


def _recovery_record(store, owner, job, report, proposal):
    evidence = feasibility_evidence(store, owner, job, report)
    return {
        "job_id": job.id,
        "artifact": job.result_artifact,
        "proposal_id": proposal["id"],
        "source_settings_id": evidence["source_settings_id"],
        "counts": evidence["counts"],
        "unresolved_constraints": evidence["unresolved_constraints"],
        "scope": "feasibility_only",
    }


def reserve_account(store, owner, supervisor_id, proposal_id):
    """Worker-only debit before computation; a lost attempt never gets refunded."""
    with store.sessions.begin() as db:
        service.write_guard(db)
        row = db.get(ResearchSupervisor, supervisor_id)
        if row is None or row.owner != owner:
            raise LookupError("Research supervisor not found")
        state = json.loads(row.state)
        item = next(p for p in state["proposals"] if p["id"] == proposal_id)
        if _lost(state) >= 64:
            raise ValueError("The original 64 interrupted-account ceiling is exhausted")
        if item["spent"] >= item["reserved_accounts"] or item.get("outcome"):
            raise ValueError("The reserved supervisor validation allowance is exhausted")
        item["spent"] += 1
        _put(row, state)


def _exclusion_request(row):
    return fingerprint(
        {
            "strategy_id": row.get("strategy_id"),
            "symbol": row.get("symbol"),
            "date": row.get("signal_date", row.get("date")),
            "row": row.get("source_row", row.get("row")),
        }
    )


def _frozen_exclusions(store, bundle):
    """Planning-only proof from exact frozen preparation, never account evidence."""
    identifier = bundle.get("inputs_artifact")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        return None
    try:
        inputs = service.read_artifact(store, identifier)
    except (ValueError, LookupError, KeyError):
        return None
    coverage, signals = inputs.get("signal_coverage", {}), inputs.get("signals", [])
    excluded = coverage.get("exclusions")
    if (
        coverage.get("policy_version") != "portfolio-fixed-price-cohort-v1"
        or not isinstance(signals, list)
        or not isinstance(excluded, list)
        or len(excluded) > 512
        or type(coverage.get("excluded_signals")) is not int
        or coverage["excluded_signals"] != len(excluded)
        or type(coverage.get("eligible_signals")) is not int
        or type(coverage.get("pending_signals")) is not int
        or min(coverage["eligible_signals"], coverage["pending_signals"]) < 0
        or coverage["eligible_signals"] + coverage["pending_signals"] + len(excluded)
        != len(signals)
    ):
        return None
    groups = defaultdict(list)
    for signal in signals:
        if not isinstance(signal, dict):
            return None
        groups[(signal.get("strategy_id"), signal.get("symbol"), signal.get("date"))].append(signal)
    requested = Counter()
    reasons = {}
    for row in excluded:
        if not isinstance(row, dict) or not isinstance(row.get("reason"), str) or not row["reason"]:
            return None
        key = (row.get("strategy_id"), row.get("symbol"), row.get("date"))
        requested[key] += 1
        if key in reasons and reasons[key] != row["reason"]:
            return None
        reasons[key] = row["reason"]
    entries = []
    for key, count in requested.items():
        source = groups.get(key, [])
        if len(source) != count or any(type(row.get("row")) is not int for row in source):
            return None
        entries.extend(
            {"request_id": _exclusion_request(row), "reason_id": fingerprint(reasons[key])}
            for row in source
        )
    if len({r["request_id"] for r in entries}) != len(entries):
        return None
    body = {
        "version": "frozen-preparation-exclusions-v1",
        "inputs_artifact": identifier,
        "source_signal_count": len(signals),
        "excluded_signals": len(excluded),
        "entries": sorted(entries, key=lambda row: row["request_id"]),
    }
    return {**body, "id": fingerprint(body)}


def required_evidence(report, counts, *, exclusions=None):
    """Do not confuse frozen source exclusions with missing execution evidence."""
    keys = (
        "unavailable_sizing_intent",
        "unexplained_execution_limit",
        "evidence_gap_requests",
        "unavailable_evidence",
    )
    required = {key: counts.get(key, 0) for key in keys}
    if any(type(value) is not int or value < 0 for value in required.values()):
        return {
            "missing_required": True,
            "proven_preexcluded_requests": 0,
            "required_counts": required,
            "unknown_excluded_requests": 0,
        }
    proven = {}
    if (
        isinstance(exclusions, dict)
        and exclusions.get("version") == "frozen-preparation-exclusions-v1"
        and exclusions.get("id") == fingerprint({k: v for k, v in exclusions.items() if k != "id"})
        and isinstance(exclusions.get("entries"), list)
        and len(exclusions["entries"]) == exclusions.get("excluded_signals")
        and type(exclusions.get("excluded_signals")) is int
        and 0 <= exclusions["excluded_signals"] <= 512
        and type(exclusions.get("source_signal_count")) is int
        and exclusions["source_signal_count"] >= exclusions["excluded_signals"]
        and isinstance(exclusions.get("inputs_artifact"), str)
        and re.fullmatch(r"[a-f0-9]{64}", exclusions["inputs_artifact"])
        and all(
            isinstance(row, dict)
            and set(row) == {"request_id", "reason_id"}
            and all(
                isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
                for value in row.values()
            )
            for row in exclusions["entries"]
        )
        and len({row["request_id"] for row in exclusions["entries"]}) == len(exclusions["entries"])
    ):
        proven = {r["request_id"]: r["reason_id"] for r in exclusions["entries"]}
    matched, unknown = set(), 0
    for row in report.get("ledger", []):
        if row.get("status") != "excluded":
            continue
        reason = str(row.get("reason") or "")
        if reason.lower().startswith(
            (
                "joint entry condition not met: ",
                "stock condition not met: ",
                "entry condition not met: ",
                "maximum holding window crosses the ",
            )
        ):
            continue
        identity = _exclusion_request(row)
        if (
            identity in proven
            and proven[identity] == fingerprint(reason)
            and identity not in matched
        ):
            matched.add(identity)
        else:
            unknown += 1
    for key in ("evidence_gap_requests", "unavailable_evidence"):
        required[key] = max(0, required[key] - len(matched))
    return {
        "missing_required": bool(unknown or any(required.values())),
        "proven_preexcluded_requests": len(matched),
        "required_counts": required,
        "unknown_excluded_requests": unknown,
    }


def _diagnosis(report, *, exclusions=None):
    findings = report.get("automatic_research", {})
    validation = report.get("supervisor_validation", {}).get(
        "operating_validation", findings.get("operating_validation", {})
    )
    decision = validation.get("decision", findings.get("decision", {}))
    checks = decision.get("checks", [])
    planning = findings.get("capital_planning", {})
    counts = decision.get("capital_diagnosis") or planning.get("diagnosis", {}).get(
        "categories", {}
    )
    # Native decision reports carry a diagnosis envelope; older exact reports
    # carry the category counts directly. Read both without losing blockers.
    if isinstance(counts, dict) and isinstance(counts.get("categories"), dict):
        counts = counts["categories"]
    blocked = report.get("supervisor_validation", {}).get("outcome") == "blocked"
    if blocked:
        feasibility = report.get("supervisor_validation", {}).get("feasibility", {})
        counts = feasibility.get("counts") or {"unavailable_evidence": 1}
    evidence = required_evidence(report, counts, exclusions=exclusions)
    missing = evidence["missing_required"]
    routes = []
    if any(
        counts.get(k, 0)
        for k in ("cash_shortfall", "whole_share_affordability", "percentage_policy")
    ):
        routes.append("funding_and_size")
    if blocked:
        return {
            "permitted_remedies": [] if missing else routes,
            "missing_evidence": missing,
            "evidence_status": evidence,
            "fixed_limits": {k: counts.get(k, 0) for k in ("position_limit", "liquidity_limit")},
            "feasibility_only": True,
        }
    if any(c.get("kind") == "higher_costs" and not c.get("passed") for c in checks):
        routes.append("lower_turnover")
    design = report.get("automatic_design") or findings.get("automatic_design", {}).get(
        "selected", {}
    )
    fraction = design.get("largest_symbol_positive_profit_fraction")
    if isinstance(fraction, (int, float)) and fraction > 0.5:
        routes.append("reduce_concentration")
    prefixes = list(decision.get("prefix_diagnostics", []))
    prefixes += validation.get("diagnostics", [])
    if any(p.get("passed") is False for p in prefixes):
        routes.append("temporal_stability")
    return {
        "permitted_remedies": [] if missing else routes,
        "missing_evidence": missing,
        "evidence_status": evidence,
        "fixed_limits": {k: counts.get(k, 0) for k in ("position_limit", "liquidity_limit")},
    }


def _available_diagnosis(report, recipe, *, exclusions=None):
    """Advertise only executable frozen remedies, without observing new outcomes."""
    diagnosis = _diagnosis(report, exclusions=exclusions)
    # A robust-improvement goal is broader than repairing a failed gate. A
    # recorded native cost pair can justify one development-only cost-reduction
    # hypothesis even when both accounts remain profitable. Passing checks stay
    # passing; acceptance still requires strict improvement and every challenge.
    findings = report.get("automatic_research", {})
    costs = [
        c
        for c in findings.get("decision", {}).get("checks", [])
        if c.get("kind") == "higher_costs" and c.get("passed") is True
    ]
    ordinary_return = report.get("summary", {}).get("net_return_pct")
    has_cost = any(
        row.get("config", {}).get("cost_bps", 0) > 0 for row in report.get("strategies", [])
    )
    if (
        not diagnosis["missing_evidence"]
        and recipe["validation_mode"] == "exploratory_only"
        and has_cost
        and type(ordinary_return) in (int, float)
        and any(
            type(c.get("summary", {}).get("net_return_pct")) in (int, float)
            and c["summary"]["net_return_pct"] < ordinary_return
            for c in costs
        )
    ):
        if "lower_turnover" not in diagnosis["permitted_remedies"]:
            diagnosis["permitted_remedies"].append("lower_turnover")
        diagnosis["development_opportunity"] = (
            "Actual higher-cost development account records cost drag; test a legal holding-period alternative, not a failed-check repair"
        )
    available, unavailable = [], {}
    for remedy in diagnosis["permitted_remedies"]:
        if recipe["validation_mode"] != "exploratory_only" and remedy != "funding_and_size":
            unavailable[remedy] = "Later outcomes cannot select another performance candidate"
            continue
        try:
            _settings_for(report, recipe, remedy)
        except ValueError as error:
            unavailable[remedy] = str(error)
        else:
            available.append(remedy)
    return {**diagnosis, "permitted_remedies": available, "unavailable_remedies": unavailable}


def _research_context(store, owner, job, bundle, report, supplied):
    """Bind the optional reviewed journey to its owned, exact saved inputs."""
    if supplied is None:
        return None, None
    from database.research_db import ResearchLibraryExperiment, ResearchLibraryJob
    from services import research_memory, research_playbook

    if not isinstance(supplied, dict) or set(supplied) not in (
        {
            "experiment_id",
            "inputs_artifact",
            "contract",
        },
        {"experiment_id", "inputs_artifact", "contract", "flow", "baseline_settings"},
    ):
        raise ValueError("Research continuation needs the exact reviewed study context")
    with store.sessions() as db:
        experiment = db.get(ResearchLibraryExperiment, supplied["experiment_id"])
        link = db.get(ResearchLibraryJob, (supplied["experiment_id"], job.id))
        if experiment is None or experiment.owner != owner or experiment.archived or link is None:
            raise LookupError("Reviewed research study not found")
    if supplied["inputs_artifact"] != bundle.get("inputs_artifact"):
        raise ValueError("Reviewed research observations changed")
    inputs = service.read_artifact(store, bundle["inputs_artifact"])
    if "flow" in supplied:
        from database.research_db import ResearchJourney
        from research.user_flow import normalize, permitted

        with store.sessions() as db:
            reviewed = db.get(ResearchJourney, supplied["experiment_id"])
            approved = json.loads(reviewed.state) if reviewed and reviewed.owner == owner else {}
        if (
            approved.get("job_id") != job.id
            or approved.get("approved_plan", {}).get("flow") != supplied["flow"]
            or approved.get("approved_plan", {}).get("baseline_settings")
            != supplied["baseline_settings"]
        ):
            raise ValueError("Research permissions do not match the approved plan")
        if (
            normalize(supplied["flow"]) != supplied["flow"]
            or supplied["baseline_settings"] != inputs["portfolio"]["strategies"]
        ):
            raise ValueError("Reviewed research permissions or original settings changed")
        if not permitted(report["strategies"], supplied["baseline_settings"], supplied["flow"]):
            raise ValueError("The research result changed a fixed user setting")
    if supplied["contract"] != research_playbook.describe(inputs["portfolio"]):
        raise ValueError("Reviewed research objective or constraints changed")
    research_playbook.validate_settings(supplied["contract"], report["strategies"])
    context = deepcopy(supplied)
    memory = research_memory.read(
        store, owner, supplied["experiment_id"], supplied["inputs_artifact"]
    )
    return context, memory


def _reviewed_diagnosis(report, recipe, context, memory, *, exclusions=None):
    diagnosis = _available_diagnosis(report, recipe, exclusions=exclusions)
    if not context:
        return diagnosis
    from services import research_memory, research_playbook

    permitted = []
    for remedy in diagnosis["permitted_remedies"]:
        settings = _settings_for(report, recipe, remedy)
        try:
            research_playbook.validate_settings(context["contract"], settings)
        except ValueError as error:
            diagnosis["unavailable_remedies"][remedy] = str(error)
            continue
        previous = research_memory.matching(memory, remedy, settings, context["contract"]["id"])
        if previous:
            diagnosis["unavailable_remedies"][remedy] = (
                "This exact setting already failed or had no effect on the same observations; "
                "new data or a different supported setting is required"
            )
        else:
            permitted.append(remedy)
    return {**diagnosis, "permitted_remedies": permitted}


def open_supervisor(
    store,
    owner,
    job_id,
    *,
    conversation_id,
    objective="robust_improvement",
    max_experiments=3,
    request_id,
    research_context=None,
    experiment_policy=None,
    continuation_policy=None,
):
    conversation_id = _conversation(conversation_id)
    if (
        objective != "robust_improvement"
        or type(max_experiments) is not int
        or not 1
        <= max_experiments
        <= (experiment_limit(continuation_policy) if experiment_policy == EXPERIMENT_POLICY else 3)
    ):
        raise ValueError(
            "Choose robust_improvement and the bounded experiment allowance for this policy"
        )
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
        raise ValueError("Supply a stable research request identity")
    job, bundle, report, recipe = _parent(store, owner, job_id)
    exclusions = _frozen_exclusions(store, bundle)
    context, memory = _research_context(store, owner, job, bundle, report, research_context)
    if continuation_policy not in (None, ADAPTIVE_POLICY) or (
        continuation_policy
        and (
            experiment_policy != EXPERIMENT_POLICY
            or recipe["validation_mode"] != "exploratory_only"
        )
    ):
        raise ValueError("Adaptive continuation requires a reviewed exploratory experiment plan")
    if experiment_policy not in (None, EXPERIMENT_POLICY) or (experiment_policy and not context):
        raise ValueError("Concrete experiments require the reviewed research contract")
    attempts = report["automatic_research"].get("counts", {}).get("attempts_reserved")
    simulations = report["automatic_research"].get("counts", {}).get("simulations")
    if (
        type(attempts) is not int
        or not 0 <= attempts <= 512
        or type(simulations) is not int
        or not 0 <= simulations <= attempts
    ):
        raise ValueError("The saved study lacks verified attempt accounting")
    with store.sessions.begin() as db:
        service.write_guard(db)
        previous = db.scalar(
            select(ResearchSupervisor).where(
                ResearchSupervisor.owner == owner,
                (ResearchSupervisor.root_artifact == job.result_artifact)
                | (ResearchSupervisor.request_id == request_id),
            )
        )
        if previous:
            _, state = _owned(db, owner, previous.id, conversation_id)
            if (
                previous.root_artifact != job.result_artifact
                or state["objective"] != objective
                or state["max_experiments"] != max_experiments
                or state.get("research_context") != context
                or state.get("experiment_policy") != experiment_policy
                or state.get("continuation_policy") != continuation_policy
            ):
                raise ValueError(
                    "This saved study already has a different frozen research authorization"
                )
            identifier = previous.id
        else:
            if db.scalar(select(func.count()).select_from(ResearchSupervisor)) >= 100000:
                raise ValueError("Research supervisor storage is full")
            identifier = uuid.uuid4().hex
            state = {
                "version": VERSION,
                "objective": objective,
                "max_experiments": max_experiments,
                "original_attempts": attempts,
                "original_simulations": simulations,
                "proposals": [],
                "status": "planning",
                "validation_mode": recipe["validation_mode"],
                "diagnosis": _reviewed_diagnosis(
                    report, recipe, context, memory, exclusions=exclusions
                ),
                "capital_policy_version": "research-capital-planning-v8",
                "decision_catalog_version": DECISION_CATALOG_VERSION,
                "experiment_policy": experiment_policy,
                "failure_diagnosis_version": "research-failure-diagnosis-v1"
                if experiment_policy == EXPERIMENT_POLICY
                else None,
                "continuation_policy": continuation_policy,
                "accepted_job_id": None,
                "saved_result": None,
                "goal": "Find a feasible historical improvement over original settings that survives unchanged native validation; otherwise retain or stop.",
                "authority": "Saved data, registered remedies and exact historical save/reopen only",
            }
            if context:
                state.update(research_context=context, study_memory=memory)
            if exclusions is not None:
                state["frozen_exclusions"] = exclusions
            if experiment_policy == EXPERIMENT_POLICY:
                catalog = _experiment_catalog(
                    report, recipe, {**state, "source_result_artifact": job.result_artifact}
                )
                state["diagnosis"]["permitted_remedies"] = list(
                    dict.fromkeys(o["remedy"] for o in catalog["options"])
                )
            if not state["diagnosis"]["permitted_remedies"] or attempts - simulations >= 64:
                state["status"] = "stopped"
                state["stopping_reason"] = (
                    catalog.get("stop_reason") if experiment_policy == EXPERIMENT_POLICY else None
                ) or (
                    "Missing evidence, fixed constraints or the interrupted-account ceiling prevent another supported experiment; retained original result"
                )
            row = ResearchSupervisor(
                id=identifier,
                owner=owner,
                root_job_id=job.id,
                root_artifact=job.result_artifact,
                conversation_id=conversation_id,
                request_id=request_id,
                state="{}",
                updated_at=time.time(),
            )
            _put(row, state)
            db.add(row)
    return status(store, owner, identifier, conversation_id=conversation_id)


def status(store, owner, supervisor_id, *, conversation_id):
    with store.sessions() as db:
        row, state = _owned(db, owner, supervisor_id, conversation_id)
        job_id, artifact = row.root_job_id, row.root_artifact
        automatic_job = db.get(ResearchDecisionJob, supervisor_id)
        automatic_phase = automatic_job.phase if automatic_job is not None else None
    job, root_bundle, root_report, recipe = _parent(store, owner, job_id)
    if job.result_artifact != artifact:
        raise ValueError("The authorized saved study changed")
    planning_seed, planning_artifact = _planning_source(state, job_id, artifact)
    result = {
        "id": supervisor_id,
        "supervisor_id": supervisor_id,
        **state,
        "remaining_accounts": _remaining(state),
        "lost_accounts": _lost(state),
        "seed_job_id": planning_seed,
        "source_result_artifact": planning_artifact,
        "root_job_id": job_id,
        "historical_only": True,
        "automatic_phase": automatic_phase,
        "next_action": (
            "The plan stopped; retain the original result and report the stopping reason"
            if state["status"] == "stopped"
            else "Reopen the saved accepted result"
            if state["status"] == "saved"
            else "The authorized worker continues automatically; no new chat message is required"
            if automatic_phase in ("queued", "deciding", "applying", "waiting", "finalizing")
            else "Another Agent turn may choose from the new verified development diagnosis"
            if state["status"] == "awaiting_plan"
            else "Queue evidence-supported remedies, then advance once; the worker validates and saves the first accepted result"
        ),
    }
    exclusions = state.get("frozen_exclusions") or _frozen_exclusions(store, root_bundle)
    if exclusions is not None:
        result["frozen_exclusions"] = exclusions
    if state.get("experiment_policy") == EXPERIMENT_POLICY:
        seed_id = result["seed_job_id"]
        report = root_report
        if seed_id != job_id:
            seed = service.get_job(store, owner, seed_id)
            if (
                seed.status != "completed"
                or seed.result_artifact != result["source_result_artifact"]
            ):
                raise ValueError("The experiment planning seed changed")
            report = service.read_artifact(store, seed.result_artifact)["result"]
            if state.get("blocked_recovery"):
                report = planning_report(store, owner, seed, report)
                result["feasibility_only"] = True
                result["diagnosis"] = _diagnosis(report, exclusions=exclusions)
        result["diagnosis"] = _reviewed_diagnosis(
            report,
            recipe,
            result.get("research_context"),
            result.get("study_memory"),
            exclusions=exclusions,
        )
        frontier = (
            state.get("continuation_policy") == ADAPTIVE_POLICY
            and state["validation_mode"] == "exploratory_only"
        )
        catalog = (
            _frontier_catalog(store, owner, supervisor_id, result, job, root_report, recipe)
            if frontier
            else _experiment_catalog(report, recipe, result)
        )
        if frontier:
            result["experiment_frontier"] = {
                key: catalog[key]
                for key in ("version", "id", "branches", "unavailable", "feedback", "stop_reason")
            }
            result["diagnosis"] = {
                **result["diagnosis"],
                "scope": "verified_development_frontier",
                "active_branch_missing_evidence": result["diagnosis"]["missing_evidence"],
                "missing_evidence": not bool(catalog["options"])
                and all(
                    branch.get("missing_required_evidence", False) for branch in catalog["branches"]
                ),
            }
        if state.get("continuation_policy") == ADAPTIVE_POLICY:
            supported = catalog["options"]
            catalog["options"] = [
                o
                for o in supported
                if validation_reserve(o["remedy"], state["validation_mode"])
                <= result["remaining_accounts"]
            ]
            if not catalog["options"]:
                result["continuation_stop_reason"] = catalog.get("stop_reason") or (
                    f"The remaining {result['remaining_accounts']} of512 accounts cannot fund the complete "
                    f"{min(validation_reserve(o['remedy'], state['validation_mode']) for o in supported)}-account "
                    "validation of any supported next change; validation was not reduced"
                    if supported
                    else (
                        "Blocked attempt: "
                        + ", ".join(
                            f"{k}={v}"
                            for k, v in state["blocked_recovery"]["unresolved_constraints"].items()
                        )
                        + "; no untried supported funding/sizing repair is available for this complete configuration; check earlier fully checked branches"
                        if state.get("blocked_recovery")
                        else "No untested effect-relevant setting remains within the frozen methods, saved data and risk limits"
                    )
                )
        result["experiment_options"] = catalog["options"]
        result["experiment_feedback"] = catalog["feedback"]
        result["diagnosis"] = {
            **result["diagnosis"],
            "permitted_remedies": list(dict.fromkeys(o["remedy"] for o in catalog["options"])),
            "unavailable_experiments": catalog.get("unavailable", []),
        }
    return result


def _structural_sample_stop(recipe, context):
    """A fixed signal row can create at most one native portfolio entry.

    Use only sealed, pre-outcome facts under the fixed-signal VectorBT contract.
    Do not infer impossibility from the observed fills of a particular setup.
    """
    from research.automatic_design import descriptor

    contract = (context or {}).get("contract", {})
    preflight = recipe.get("decision_preflight", {})
    intent = recipe.get("execution_intent", {})
    policy = descriptor()
    count = preflight.get("signal_count")
    if (
        contract.get("version") != "research-execution-playbook-v1"
        or contract.get("id") != fingerprint({k: v for k, v in contract.items() if k != "id"})
        or contract.get("signal_policy") != "fixed_signals"
        or contract.get("engine") != "vectorbt"
        or recipe.get("version") != "automatic-hypotheses-v16"
        or intent.get("version") != "fixed-signals-v1"
        or intent.get("engine") != "vectorbt"
        or intent.get("hypothesis_ids") != ["none"]
        or recipe.get("automatic_design") != policy
        or recipe.get("id") != fingerprint({k: v for k, v in recipe.items() if k != "id"})
        or preflight.get("id") != fingerprint({k: v for k, v in preflight.items() if k != "id"})
        or type(count) is not int
        or count < 0
        or count >= policy["minimum_closed_trades"]
    ):
        return None
    return (
        f"Only {count} fixed signal opportunities are available, so at most {count} "
        f"closed trades are possible; unchanged validation requires at least "
        f"{policy['minimum_closed_trades']}. Capital, trade size and holding/exit "
        "changes cannot create additional signals. More signal data is required; "
        "no setup was validated."
    )


def _experiment_catalog(report, recipe, current):
    from services.research_experiments import build
    from services.research_playbook import validate_settings

    structural_stop = _structural_sample_stop(recipe, current.get("research_context"))
    if structural_stop:
        return {
            "options": [],
            "feedback": {"structural_stop_reason": structural_stop},
            "unavailable": [{"method": "fixed_signal_sample", "reason": structural_stop}],
            "stop_reason": structural_stop,
        }
    catalog = build(
        report,
        recipe,
        current["proposals"],
        source_artifact=current["source_result_artifact"],
        memory=current.get("study_memory"),
        research_context=current.get("research_context"),
        evidence_status=_diagnosis(report, exclusions=current.get("frozen_exclusions"))[
            "evidence_status"
        ],
    )
    evidence = _diagnosis(report, exclusions=current.get("frozen_exclusions"))["evidence_status"]
    catalog["feedback"]["evidence_status"] = evidence
    if evidence["missing_required"]:
        catalog["options"] = []
        counts = ", ".join(
            f"{key}={value}" for key, value in evidence["required_counts"].items() if value
        )
        catalog["stop_reason"] = (
            "Required execution or sizing evidence is unavailable"
            + (f" ({counts})" if counts else " for unmatched excluded requests")
            + "; no new setup was validated. Frozen source exclusions remain excluded."
        )
        return catalog
    elif recipe.get("validation_mode") != "exploratory_only":
        before = len(catalog["options"])
        catalog["options"] = [o for o in catalog["options"] if o["remedy"] == "funding_and_size"]
        if before and not catalog["options"]:
            catalog["stop_reason"] = (
                "The frozen validation programme permits funding repairs only; later outcomes cannot select another performance candidate."
            )
    contract = current.get("research_context", {}).get("contract")
    if contract:
        for option in catalog["options"]:
            validate_settings(contract, option["settings"])
    authority = current.get("research_context", {})
    if authority.get("flow"):
        from research.user_flow import permitted

        before = len(catalog["options"])
        catalog["options"] = [
            o
            for o in catalog["options"]
            if permitted(o["settings"], authority["baseline_settings"], authority["flow"])
        ]
        filtered = before - len(catalog["options"])
        filtered += sum(
            row.get("reason_code") == "fixed_settings" for row in catalog.get("unavailable", [])
        )
        if filtered and not catalog["options"]:
            catalog["stop_reason"] = (
                "No remaining supported experiment respects your fixed settings; no new setup was validated."
            )
    return catalog


def _checked_branch(
    store,
    owner,
    supervisor_id,
    current,
    root,
    root_report,
    job_id,
    artifact=None,
    *,
    allow_no_effect=False,
):
    """Read one immutable same-supervisor branch; blocked results expose feasibility only."""
    if job_id == root.id:
        if root.owner != owner or (artifact is not None and artifact != root.result_artifact):
            raise ValueError("The original branch artifact changed")
        return root, root_report, None, root_report
    prior = next((p for p in current["proposals"] if p.get("job_id") == job_id), None)
    if prior is None or prior.get("outcome") not in ("rejected", "blocked"):
        raise ValueError("Only completed checked branches may supply a frontier experiment")
    job = service.get_job(store, owner, job_id)
    if (
        job.status != "completed"
        or job.owner != owner
        or not job.result_artifact
        or (artifact is not None and job.result_artifact != artifact)
    ):
        raise ValueError("The checked branch artifact changed")
    report = service.read_artifact(store, job.result_artifact)["result"]
    conclusion = _verified_conclusion(report, supervisor_id, prior["id"], root.id, store, owner)
    if conclusion["outcome"] != prior["outcome"]:
        raise ValueError("The checked branch outcome changed")
    if not allow_no_effect and (
        conclusion.get("no_effect", {}).get("status") == "unchanged"
        or prior.get("no_effect", {}).get("status") == "unchanged"
    ):
        raise ValueError("A no-effect probe is not a fully checked planning source")
    planning = (
        planning_report(store, owner, job, report) if conclusion["outcome"] == "blocked" else report
    )
    return job, report, conclusion, planning


def _frontier_catalog(store, owner, supervisor_id, current, root, root_report, recipe):
    from services.research_frontier import build
    from services.research_validation_scorecard import build as scorecard

    prior = [
        p
        for p in current["proposals"]
        if p.get("outcome") in ("rejected", "blocked") and p.get("job_id")
    ]
    if len(prior) > ADAPTIVE_EXPERIMENT_LIMIT:
        raise ValueError("The checked branch history exceeds its frozen bound")
    branches, incomplete = [], []
    for job_id in dict.fromkeys([root.id, *(p["job_id"] for p in prior)]):
        previous = next((p for p in prior if p.get("job_id") == job_id), None)
        if previous is not None and previous["outcome"] == "blocked":
            attempt = service.get_job(store, owner, job_id)
            if attempt.owner != owner:
                raise ValueError("The incomplete branch owner changed")
            if attempt.status in ("failed", "cancelled"):
                incomplete.append(
                    {
                        "job_id": job_id,
                        "result_artifact": attempt.result_artifact,
                        "settings_id": None,
                        "source_eligibility": "incomplete_native_attempt",
                        "native_status": attempt.status,
                        "available_options": 0,
                        "scorecard": {"eligible": False, "scope": "incomplete_native_attempt"},
                        "reason_code": "incomplete_native_attempt",
                        "stop_reason": "The native attempt did not complete a verified report; it cannot supply a checked configuration or new experiment.",
                    }
                )
                continue
        job, report, conclusion, planning = _checked_branch(
            store, owner, supervisor_id, current, root, root_report, job_id, allow_no_effect=True
        )
        branch_state = {
            **current,
            "seed_job_id": job.id,
            "source_result_artifact": job.result_artifact,
            "diagnosis": _reviewed_diagnosis(
                planning,
                recipe,
                current.get("research_context"),
                current.get("study_memory"),
                exclusions=current.get("frozen_exclusions"),
            ),
        }
        previous = next((p for p in current["proposals"] if p.get("job_id") == job.id), {})
        probe_only = (
            conclusion and conclusion.get("no_effect", {}).get("status") == "unchanged"
        ) or previous.get("no_effect", {}).get("status") == "unchanged"
        catalog = (
            {
                "options": [],
                "unavailable": [
                    {
                        "reason_code": "no_effect_probe_only",
                        "reason": "This one-account no-effect probe did not complete native validation and cannot become a checked planning source",
                    }
                ],
                "feedback": {},
                "stop_reason": "No-effect probe only; continue from its fully checked origin.",
            }
            if probe_only
            else _experiment_catalog(planning, recipe, branch_state)
        )
        card = scorecard(report)
        if probe_only:
            card = {**card, "source_eligibility": "no_effect_probe_only"}
        elif conclusion and conclusion["outcome"] == "blocked":
            card = {**card, "source_eligibility": "feasibility_only"}
            feasibility = planning.get("supervisor_validation", {}).get("feasibility")
            if (
                isinstance(feasibility, dict)
                and feasibility.get("version") == "research-feasibility-v1"
                and feasibility.get("scope") == "feasibility_only"
                and feasibility.get("source_settings_id") == settings_identity(report["strategies"])
                and isinstance(feasibility.get("counts"), dict)
                and len(feasibility["counts"]) <= 24
                and all(
                    type(count) is int and count >= 0 for count in feasibility["counts"].values()
                )
                and isinstance(feasibility.get("unresolved_constraints"), dict)
                and len(feasibility["unresolved_constraints"]) <= 24
                and all(
                    feasibility["counts"].get(key) == count and type(count) is int and count >= 0
                    for key, count in feasibility["unresolved_constraints"].items()
                )
            ):
                card["feasibility"] = {
                    key: deepcopy(feasibility[key])
                    for key in (
                        "version",
                        "scope",
                        "source_settings_id",
                        "counts",
                        "unresolved_constraints",
                    )
                }
            else:
                card["feasibility_unavailable"] = True
        branches.append(
            {
                "job_id": job.id,
                "result_artifact": job.result_artifact,
                "report": {
                    "strategies": report["strategies"],
                    "portfolio": {
                        "engine": report.get("portfolio", {}).get("engine", recipe.get("engine"))
                    },
                },
                "catalog": catalog,
                "scorecard": card,
                "missing_required_evidence": branch_state["diagnosis"]["missing_evidence"],
            }
        )
    frontier = build(
        branches,
        remaining_accounts=current["remaining_accounts"],
        validation_mode=current["validation_mode"],
    )
    if incomplete:
        frontier["branches"].extend(incomplete)
        frontier["unavailable"].extend(
            {
                "origin_job_id": item["job_id"],
                "reason_code": item["reason_code"],
                "reason": item["stop_reason"],
                "native_status": item["native_status"],
            }
            for item in incomplete
        )
        frontier["feedback"]["incomplete_native_attempts"] = len(incomplete)
        frontier["feedback"]["coverage"] = (
            "All completed checked branches and supported catalogues; failed/cancelled attempts remain explicitly unavailable history, not tested configurations."
        )
        frontier["id"] = fingerprint({key: value for key, value in frontier.items() if key != "id"})
    return frontier


def _concrete_option(current, remedy, candidate_id):
    selected = next(
        (
            o
            for o in current.get("experiment_options", [])
            if o["id"] == candidate_id and o["remedy"] == remedy
        ),
        None,
    )
    if not selected:
        raise ValueError("Choose an effect-relevant experiment from the current frozen catalogue")
    return selected


def resume_frontier(store, owner, supervisor_id, *, conversation_id):
    """Return to an unexhausted checked branch, without a new grant or account.

    Rejected developmental seeds are context, never accepted results. All
    completed settings remain excluded globally, including across branches.
    """
    current = status(store, owner, supervisor_id, conversation_id=conversation_id)
    if current.get("experiment_frontier"):
        # The complete checked frontier is already offered to the model. A
        # background read cannot select a branch or revoke its stop decision.
        return current
    if (
        current.get("continuation_policy") != ADAPTIVE_POLICY
        or current["validation_mode"] != "exploratory_only"
        or current["status"] not in ("awaiting_plan", "stopped")
        or current.get("experiment_options")
        or (current["diagnosis"]["missing_evidence"] and not current.get("blocked_recovery"))
        or current["lost_accounts"] >= 64
        or current.get("automation", {}).get("attention")
        or current.get("stop_decision")
        or current.get("experiment_feedback", {}).get("structural_stop_reason")
        or any(not p.get("outcome") for p in current["proposals"])
        or len(current["proposals"]) >= current["max_experiments"]
        or current["automation"]["calls_spent"] >= current["automation"]["max_model_calls"]
    ):
        return current
    if len(current.get("frontier_transitions", [])) >= ADAPTIVE_EXPERIMENT_LIMIT:
        return {
            **current,
            "continuation_stop_reason": "The bounded checked-branch history is exhausted; untested alternatives remain unvalidated",
        }
    root, _, root_report, recipe = _parent(store, owner, current["root_job_id"])
    candidates = [
        p
        for p in reversed(current["proposals"])
        if p.get("outcome") == "rejected"
        and p.get("job_id")
        and p.get("no_effect", {}).get("status") != "unchanged"
    ]
    # Do not go back to an unfunded account after an actual funding repair.
    if not any(
        p["remedy"] == "funding_and_size" and p.get("outcome") for p in current["proposals"]
    ):
        candidates.append({"job_id": root.id})
    required = []
    selected = None
    for prior in candidates:
        if prior["job_id"] == current["seed_job_id"]:
            continue
        seed = service.get_job(store, owner, prior["job_id"])
        if seed.status != "completed" or not seed.result_artifact:
            raise ValueError("A checked research branch changed its saved result")
        report = (
            root_report
            if seed.id == root.id
            else service.read_artifact(store, seed.result_artifact)["result"]
        )
        conclusion = (
            None
            if seed.id == root.id
            else _verified_conclusion(report, supervisor_id, prior["id"], root.id, store, owner)
        )
        diagnosis = _reviewed_diagnosis(
            report,
            recipe,
            current.get("research_context"),
            current.get("study_memory"),
            exclusions=current.get("frozen_exclusions"),
        )
        planning = {
            **current,
            "source_result_artifact": seed.result_artifact,
            "diagnosis": diagnosis,
        }
        catalog = _experiment_catalog(report, recipe, planning)
        for option in catalog["options"]:
            cost = validation_reserve(option["remedy"])
            required.append(cost)
            if cost <= current["remaining_accounts"]:
                candidate = seed, diagnosis, conclusion, report
                if selected is None:
                    selected = candidate
                else:
                    from services.research_validation_scorecard import dominates

                    # All reports above were verified against this supervisor.
                    # Prefer a strict development-gate improvement, never raw
                    # profit or recency alone; incomparable branches keep order.
                    if dominates(report, selected[3]):
                        selected = candidate
                break
    if selected is None:
        reason = (
            f"The remaining {current['remaining_accounts']} of512 accounts cannot fund the complete {min(required)}-account validation of supported changes on any checked branch; validation was not reduced"
            if required
            else current.get(
                "continuation_stop_reason",
                "No untested effect-relevant setting remains on any checked branch within the frozen methods, saved data and risk limits",
            )
        )
        if current.get("blocked_recovery"):
            constraints = current["blocked_recovery"].get("unresolved_constraints") or {}
            blocked = (
                ", ".join(f"{k}={v}" for k, v in constraints.items())
                or "native feasibility evidence is unavailable"
            )
            reason = f"Blocked attempt: {blocked}. " + (
                f"No supported repair or checked alternative fits the remaining {current['remaining_accounts']} accounts with complete validation ({min(required)} required)."
                if required
                else "No untried supported funding/sizing repair or effect-relevant alternative remains on any fully checked branch; the blocked setup was not validated."
            )
        return {**current, "continuation_stop_reason": reason}
    seed, diagnosis, conclusion, selected_report = selected
    from database.research_db import ResearchJourney
    from services.research_validation_scorecard import build as validation_scorecard

    with store.sessions.begin() as db:
        service.write_guard(db)
        row, saved = _owned(db, owner, supervisor_id, conversation_id)
        if (
            saved["proposals"] != current["proposals"]
            or _planning_source(saved, row.root_job_id, row.root_artifact)[1]
            != current["source_result_artifact"]
        ):
            raise ValueError("The checked research frontier changed during continuation")
        operation = db.get(ResearchDecisionJob, supervisor_id)
        if not operation or operation.phase not in ("queued", "waiting", "applying", "stopped"):
            raise ValueError("Wait for the active model operation before changing branch")
        saved.setdefault("frontier_transitions", []).append(
            {
                "from": current["source_result_artifact"],
                "to": seed.result_artifact,
                "after_proposals": len(saved["proposals"]),
                "attempts_already_spent": 512 - current["remaining_accounts"],
                "reason": "Resume an affordable fully checked branch; comparable development-gate improvements take priority, while incomparable branches retain their recorded order. This is planning evidence, not validation acceptance.",
                "validation_scorecard": validation_scorecard(selected_report),
            }
        )
        saved.update(
            latest_seed_job_id=seed.id,
            latest_source_result_artifact=seed.result_artifact,
            diagnosis=diagnosis,
            latest_conclusion=conclusion,
            status="awaiting_plan",
        )
        saved.pop("blocked_recovery", None)
        saved.pop("stopping_reason", None)
        saved.pop("stopping", None)
        if operation.phase == "stopped":
            operation.phase, operation.claim_token, operation.claimed_at = "queued", None, None
        _put(row, saved)
        context = saved.get("research_context", {})
        journey = (
            db.get(ResearchJourney, context.get("experiment_id"))
            if context.get("experiment_id")
            else None
        )
        if journey and journey.owner == owner:
            from services import research_journey

            journey_state = json.loads(journey.state)
            if journey_state.get("supervisor_id") != supervisor_id:
                raise ValueError("The study's active research plan changed")
            if journey.phase == "none":
                journey_state.update(result=None, error=None)
                research_journey._put(journey, journey_state, "researching")
    return status(store, owner, supervisor_id, conversation_id=conversation_id)


def resume_frontier_review(store, owner, supervisor_id, *, conversation_id, request_id):
    """Explicitly resume this same stopped grant, preserving every spent allowance."""
    from database.research_db import ResearchJourney

    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
        raise ValueError("Frontier review needs a stable explicitly confirmed request identity")
    current = status(store, owner, supervisor_id, conversation_id=conversation_id)
    if any(review["request_id"] == request_id for review in current.get("frontier_reviews", [])):
        return current
    automation = current.get("automation", {})
    if (
        current["status"] != "stopped"
        or current.get("experiment_policy") != EXPERIMENT_POLICY
        or current.get("continuation_policy") != ADAPTIVE_POLICY
        or current["validation_mode"] != "exploratory_only"
        or not current.get("research_context")
        or not current.get("experiment_frontier", {}).get("feedback", {}).get("complete")
        or not current.get("experiment_options")
        or any(not proposal.get("outcome") for proposal in current["proposals"])
        or len(current["proposals"]) >= current["max_experiments"]
        or current["lost_accounts"] >= 64
        or type(automation.get("calls_spent")) is not int
        or type(automation.get("max_model_calls")) is not int
        or not 0
        <= automation["calls_spent"]
        < automation["max_model_calls"]
        <= ADAPTIVE_EXPERIMENT_LIMIT
    ):
        raise ValueError(
            "This stopped grant has no affordable checked-frontier experiment within its remaining authority"
        )
    with store.sessions.begin() as db:
        service.write_guard(db)
        row, saved = _owned(db, owner, supervisor_id, conversation_id)
        if any(review["request_id"] == request_id for review in saved.get("frontier_reviews", [])):
            return status(store, owner, supervisor_id, conversation_id=conversation_id)
        if (
            saved["status"] != "stopped"
            or saved["proposals"] != current["proposals"]
            or saved.get("automation") != current.get("automation")
        ):
            raise ValueError("Research changed before the explicit frontier review")
        reviews = saved.setdefault("frontier_reviews", [])
        if len(reviews) >= ADAPTIVE_EXPERIMENT_LIMIT:
            raise ValueError("The retained explicit frontier-review history is full")
        operation = db.get(ResearchDecisionJob, supervisor_id)
        if (
            operation is None
            or operation.owner != owner
            or operation.phase not in ("stopped", "completed")
        ):
            raise ValueError("Wait for the current decision operation before explicitly resuming")
        reviews.append(
            {
                "request_id": request_id,
                "catalog_version": DECISION_CATALOG_VERSION,
                "frontier_id": current["experiment_frontier"]["id"],
                "prior_stop_decision": deepcopy(saved.get("stop_decision")),
                "prior_stopping_reason": saved.get("stopping_reason"),
                "prior_stopping": deepcopy(saved.get("stopping")),
                "prior_decision_phase": operation.phase,
                "original_attempts": saved["original_attempts"],
                "attempts_already_spent": 512 - _remaining(saved),
                "remaining_accounts": _remaining(saved),
                "proposal_count": len(saved["proposals"]),
                "model_calls_already_spent": saved["automation"]["calls_spent"],
                "maximum_model_calls": saved["automation"]["max_model_calls"],
                "created_at": time.time(),
            }
        )
        prior_stop = saved.get("stop_decision")
        if prior_stop:
            for index in range(len(saved["automation"]["decisions"]) - 1, -1, -1):
                decision = saved["automation"]["decisions"][index]
                if (
                    decision.get("remedy") == "stop"
                    and not decision.get("superseded_by_review")
                    and all(
                        decision.get(key) == prior_stop.get(key)
                        for key in ("selection", "reason", "seed_artifact")
                    )
                ):
                    reviews[-1]["prior_model_stop"] = deepcopy(decision)
                    reviews[-1]["superseded_stop_index"] = index
                    decision["superseded_by_review"] = request_id
                    break
        saved.update(
            status="awaiting_plan",
            decision_catalog_version=DECISION_CATALOG_VERSION,
            failure_diagnosis_version="research-failure-diagnosis-v1",
            diagnosis=current["diagnosis"],
        )
        saved.pop("stop_decision", None)
        saved.pop("stopping_reason", None)
        saved.pop("stopping", None)
        saved.pop("continuation_stop_reason", None)
        saved["automation"].pop("attention", None)
        operation.phase, operation.claim_token, operation.claimed_at = "queued", None, None
        operation.updated_at = time.time()
        _put(row, saved)
        context = saved["research_context"]
        journey = db.get(ResearchJourney, context.get("experiment_id"))
        if journey is None or journey.owner != owner:
            raise ValueError("The owned saved-study journey is unavailable")
        if journey is not None:
            from services import research_journey

            journey_state = json.loads(journey.state)
            if journey_state.get("supervisor_id") != supervisor_id:
                raise ValueError("The study's active research grant changed")
            reviews[-1]["prior_journey_result"] = deepcopy(journey_state.get("result"))
            journey_state.update(result=None, error=None)
            research_journey._put(journey, journey_state, "researching")
            _put(row, saved)
    return status(store, owner, supervisor_id, conversation_id=conversation_id)


def replan(store, owner, supervisor_id, *, conversation_id, request_id, continuation_policy=None):
    """Explicitly confirmed extra experiment; never reset previous authority/debits.

    Existing frozen proposals keep their semantics. Explicit adaptive authority
    admits feedback-driven continuation within the unchanged512-account ceiling.
    Background continuations cannot call this authorization entry point.
    """
    from database.research_db import ResearchJourney
    from services import research_memory

    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
        raise ValueError("Replanning needs a stable confirmed request identity")
    current = status(store, owner, supervisor_id, conversation_id=conversation_id)
    if any(r["request_id"] == request_id for r in current.get("replans", [])):
        return current
    context = current.get("research_context")
    if continuation_policy not in (None, ADAPTIVE_POLICY):
        raise ValueError("Unsupported continuation policy")
    adaptive = continuation_policy == ADAPTIVE_POLICY
    if (
        not context
        or current["diagnosis"]["missing_evidence"]
        or current["status"] not in ("stopped", "awaiting_plan")
        or current["validation_mode"] != "exploratory_only"
        or any(not p.get("outcome") for p in current["proposals"])
        or len(current["proposals"]) >= experiment_limit(continuation_policy)
        or current["lost_accounts"] >= 64
        or current["remaining_accounts"] < (14 if adaptive else 17)
        or current.get("automation", {}).get("calls_spent", 3)
        >= (
            ADAPTIVE_EXPERIMENT_LIMIT
            if adaptive
            else current.get("automation", {}).get("max_model_calls", 3)
        )
    ):
        raise ValueError("The saved plan cannot admit another complete supported experiment")
    _, _, report, recipe = _parent(store, owner, current["root_job_id"])
    if current["seed_job_id"] != current["root_job_id"]:
        seed = service.get_job(store, owner, current["seed_job_id"])
        if seed.status != "completed" or seed.result_artifact != current["source_result_artifact"]:
            raise ValueError("The checked development seed changed")
        report = service.read_artifact(store, seed.result_artifact)["result"]
        prior = next(p for p in current["proposals"] if p.get("job_id") == seed.id)
        _verified_conclusion(
            report, supervisor_id, prior["id"], current["root_job_id"], store, owner
        )
    checked_report = report
    checked_source = current["source_result_artifact"]
    recovery = current.get("blocked_recovery")
    if recovery:
        checked_seed = service.get_job(
            store, owner, current.get("latest_seed_job_id", current["root_job_id"])
        )
        checked_source = checked_seed.result_artifact
        checked_report = service.read_artifact(store, checked_source)["result"]
    if (
        adaptive
        and not recovery
        and current["proposals"]
        and current["proposals"][-1].get("outcome") == "blocked"
    ):
        previous = current["proposals"][-1]
        blocked_job = service.get_job(store, owner, previous["job_id"])
        blocked_report = service.read_artifact(store, blocked_job.result_artifact)["result"]
        recovery = _recovery_record(store, owner, blocked_job, blocked_report, previous)
        report = planning_report(store, owner, blocked_job, blocked_report)
    elif recovery:
        report = planning_report(store, owner, seed, report)
    memory = research_memory.read(
        store, owner, context["experiment_id"], context["inputs_artifact"]
    )
    planning = {**current, "study_memory": memory, "experiment_policy": EXPERIMENT_POLICY}
    if recovery:
        planning.update(
            source_result_artifact=recovery["artifact"],
            blocked_recovery=recovery,
            diagnosis=_diagnosis(report, exclusions=current.get("frozen_exclusions")),
        )
    catalog = _experiment_catalog(report, recipe, planning)
    if adaptive:
        catalog["options"] = [
            o
            for o in catalog["options"]
            if validation_reserve(o["remedy"]) <= current["remaining_accounts"]
        ]
    if recovery and not catalog["options"]:
        fallback = {
            **current,
            "source_result_artifact": checked_source,
            "study_memory": memory,
            "diagnosis": _reviewed_diagnosis(
                checked_report, recipe, context, memory, exclusions=current.get("frozen_exclusions")
            ),
        }
        catalog = _experiment_catalog(checked_report, recipe, fallback)
        catalog["options"] = [
            o
            for o in catalog["options"]
            if validation_reserve(o["remedy"]) <= current["remaining_accounts"]
        ]
        recovery = None
    if not catalog["options"]:
        raise ValueError("No supported effect-relevant experiment remains on this saved data")
    with store.sessions.begin() as db:
        service.write_guard(db)
        row, saved = _owned(db, owner, supervisor_id, conversation_id)
        if any(r["request_id"] == request_id for r in saved.get("replans", [])):
            pass
        else:
            if (
                saved["status"] != current["status"]
                or _planning_source(saved, row.root_job_id, row.root_artifact)[1]
                != current["source_result_artifact"]
                or saved["proposals"] != current["proposals"]
            ):
                raise ValueError("Research changed before the confirmed replan")
            if len(saved.get("replans", [])) >= 3:
                raise ValueError("Research replanning reached its retained authorization bound")
            decision_job = db.get(ResearchDecisionJob, supervisor_id)
            if not decision_job or decision_job.phase not in ("stopped", "completed"):
                raise ValueError("Wait for the active decision operation before replanning")
            old_maximum = saved["max_experiments"]
            saved["max_experiments"] = max(old_maximum, len(saved["proposals"]) + 1)
            if adaptive:
                saved["continuation_policy"] = ADAPTIVE_POLICY
                saved["max_experiments"] = ADAPTIVE_EXPERIMENT_LIMIT
                saved["automation"]["max_model_calls"] = ADAPTIVE_EXPERIMENT_LIMIT
            saved["experiment_policy"] = EXPERIMENT_POLICY
            saved["failure_diagnosis_version"] = "research-failure-diagnosis-v1"
            if recovery:
                saved["blocked_recovery"] = recovery
            else:
                saved.pop("blocked_recovery", None)
            saved["study_memory"] = memory
            saved["diagnosis"]["permitted_remedies"] = list(
                dict.fromkeys(o["remedy"] for o in catalog["options"])
            )
            saved.update(status="awaiting_plan")
            prior_stopping = saved.pop("stopping", None)
            saved.pop("stopping_reason", None)
            saved.pop("stop_decision", None)
            saved["automation"].pop("attention", None)
            saved.setdefault("replans", []).append(
                {
                    "request_id": request_id,
                    "policy": EXPERIMENT_POLICY,
                    "continuation_policy": continuation_policy,
                    "source_result_artifact": current["source_result_artifact"],
                    "original_maximum": old_maximum,
                    "maximum": saved["max_experiments"],
                    "attempts_already_spent": 512 - current["remaining_accounts"],
                    "model_calls_already_spent": saved["automation"]["calls_spent"],
                    "prior_stopping": prior_stopping,
                    "reason": "Explicitly confirmed automatic feedback-driven continuation on the same frozen evidence"
                    if adaptive
                    else "Explicitly confirmed feedback-driven research on the same frozen evidence",
                    "created_at": time.time(),
                }
            )
            (
                decision_job.phase,
                decision_job.claim_token,
                decision_job.claimed_at,
                decision_job.updated_at,
            ) = "queued", None, None, time.time()
            _put(row, saved)
            journey = db.get(ResearchJourney, context["experiment_id"])
            if journey and journey.owner == owner:
                from services import research_journey

                journey_state = json.loads(journey.state)
                if journey_state.get("supervisor_id") != supervisor_id:
                    raise ValueError("The study's active research plan changed")
                journey_state.update(result=None, error=None)
                research_journey._put(journey, journey_state, "researching")
    return status(store, owner, supervisor_id, conversation_id=conversation_id)


def _settings_for(report, recipe, remedy):
    settings = deepcopy(report["strategies"])
    point = report.get("automatic_research", {}).get("capital_planning", {}).get("operating_point")
    if point and remedy != "funding_and_size":
        settings = deepcopy(point["settings"])
    if remedy in ("lower_turnover", "temporal_stability"):
        from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

        domains = {r["id"]: r["search"] for r in recipe["ranges"]}
        changed = False
        for row in settings:
            axis = (
                "hold_minutes" if row["config"].get("hold_minutes") is not None else "hold_sessions"
            )
            domain = domains.get(row["id"], {}).get(axis)
            if not domain:
                continue
            low, high, step = (Decimal(str(domain[k])) for k in ("min", "max", "step"))
            value = Decimal(str(row["config"][axis]))
            if not all(v.is_finite() for v in (low, high, step, value)) or step <= 0 or low > high:
                continue
            # A saved baseline can sit between the frozen grid's points. Pick
            # its nearest strictly directional legal point, without shifting
            # that grid or treating the baseline as an already searched value.
            offset = (value - low) / step
            last = ((high - low) / step).to_integral_value(rounding=ROUND_FLOOR)
            if remedy == "lower_turnover":
                index = max(Decimal(0), offset.to_integral_value(rounding=ROUND_FLOOR) + 1)
            else:
                index = min(last, offset.to_integral_value(rounding=ROUND_CEILING) - 1)
            adjacent = low + index * step
            if 0 <= index <= last and adjacent == adjacent.to_integral_value():
                row["config"][axis] = int(adjacent)
                changed = True
                break
        if not changed:
            raise ValueError("No registered holding-period remedy remains on the frozen grid")
    if remedy == "reduce_concentration":
        changed = False
        for row in settings:
            policy = row["execution_policy"]
            if policy["allocation_mode"] != "active_prior60_cluster_inverse_vol":
                policy["allocation_mode"] = "active_prior60_cluster_inverse_vol"
                changed = True
        if not changed:
            raise ValueError("The causal diversification remedy is already active")
    return settings


def propose(
    store,
    owner,
    supervisor_id,
    remedy,
    rationale,
    *,
    conversation_id,
    candidate_id=None,
    diagnosis=None,
):
    if (
        remedy not in REMEDIES
        or not isinstance(rationale, str)
        or not 1 <= len(rationale.strip()) <= 1000
    ):
        raise ValueError("Choose a registered research remedy and a short evidence-based reason")
    current = status(store, owner, supervisor_id, conversation_id=conversation_id)
    if diagnosis is not None:
        from services import research_failure_diagnosis

        diagnosis = research_failure_diagnosis.validate(
            {key: diagnosis[key] for key in research_failure_diagnosis.RAW_FIELDS},
            current,
            candidate_id,
        )
    root, _, root_report, recipe = _parent(store, owner, current["root_job_id"])
    option = None
    if current.get("experiment_policy") == EXPERIMENT_POLICY:
        option = _concrete_option(current, remedy, candidate_id)
    origin_job_id = (
        option.get("origin_job_id", current["seed_job_id"]) if option else current["seed_job_id"]
    )
    origin_artifact = (
        option.get("origin_result_artifact", current["source_result_artifact"])
        if option
        else current["source_result_artifact"]
    )
    seed, native_report, origin_conclusion, report = _checked_branch(
        store, owner, supervisor_id, current, root, root_report, origin_job_id, origin_artifact
    )
    origin_diagnosis = _reviewed_diagnosis(
        report,
        recipe,
        current.get("research_context"),
        current.get("study_memory"),
        exclusions=current.get("frozen_exclusions"),
    )
    if recipe["validation_mode"] != "exploratory_only" and remedy != "funding_and_size":
        raise ValueError(
            "Later results have already been exposed; do not use them to select another performance candidate"
        )
    if remedy not in current["diagnosis"]["permitted_remedies"]:
        raise ValueError("Saved native evidence does not support that remedy")
    if option:
        rebound = _experiment_catalog(
            report,
            recipe,
            {**current, "source_result_artifact": origin_artifact, "diagnosis": origin_diagnosis},
        )
        verified_option = _concrete_option(
            {"experiment_options": rebound["options"]}, remedy, candidate_id
        )
        if verified_option["settings"] != option["settings"]:
            raise ValueError("The selected origin's complete experiment settings changed")
        settings = deepcopy(option["settings"])
    else:
        if candidate_id is not None:
            raise ValueError("This saved plan does not authorize concrete candidate selection")
        settings = _settings_for(report, recipe, remedy)
    origin_blocked = origin_conclusion and origin_conclusion["outcome"] == "blocked"
    if origin_blocked and (
        remedy != "funding_and_size" or recipe["validation_mode"] != "exploratory_only"
    ):
        raise ValueError(
            "A blocked account permits only native feasibility repair, not performance selection"
        )
    origin_recovery = (
        _recovery_record(
            store,
            owner,
            seed,
            native_report,
            next(p for p in current["proposals"] if p.get("job_id") == origin_job_id),
        )
        if origin_blocked
        else None
    )
    if current.get("research_context"):
        from services import research_memory, research_playbook

        contract = current["research_context"]["contract"]
        research_playbook.validate_settings(contract, settings)
        if current["research_context"].get("flow"):
            from research.user_flow import permitted

            authority = current["research_context"]
            if not permitted(settings, authority["baseline_settings"], authority["flow"]):
                raise ValueError("This experiment changes a fixed user setting")
        if research_memory.matching(current["study_memory"], remedy, settings, contract["id"]):
            raise ValueError("The same observations and settings already failed this experiment")
    maximum = validation_reserve(remedy, recipe["validation_mode"])
    value = {
        "remedy": remedy,
        "settings": settings,
        "source_result_artifact": origin_artifact,
        **(
            {"capital_policy_version": current["capital_policy_version"]}
            if current.get("capital_policy_version")
            else {}
        ),
    }
    proposal_id = fingerprint({**value, "root_job_id": current["root_job_id"]})
    with store.sessions.begin() as db:
        service.write_guard(db)
        row, state = _owned(db, owner, supervisor_id, conversation_id)
        previous = next((p for p in state["proposals"] if p["id"] == proposal_id), None)
        if previous is not None:
            return {"proposal": previous, "reused": True}
        if (
            _planning_source(state, current["root_job_id"], current["source_result_artifact"])[0]
            != current["seed_job_id"]
            or state["proposals"] != current["proposals"]
        ):
            raise ValueError(
                "Development evidence changed while the proposal was being prepared; read status again"
            )
        if (
            state["status"] in ("accepted", "saved", "stopped")
            or len(state["proposals"]) >= state["max_experiments"]
        ):
            raise ValueError("This frozen research plan has reached its stopping condition")
        if _remaining(state) < maximum:
            raise ValueError(
                "The unchanged 512-account ceiling cannot fund every required validation check"
            )
        if state["proposals"] and state["validation_mode"] != "exploratory_only":
            raise ValueError("Only one historical later-period remedy may be declared")
        item = {
            "id": proposal_id,
            "remedy": remedy,
            "rationale": rationale.strip(),
            "settings": settings,
            "reserved_accounts": maximum,
            "spent": 0,
            "completed": 0,
            "seed_job_id": origin_job_id,
            "source_job_id": origin_job_id,
            "source_result_artifact": origin_artifact,
            "job_id": None,
            "outcome": None,
            **(
                {"capital_policy_version": current["capital_policy_version"]}
                if current.get("capital_policy_version")
                else {}
            ),
        }
        if state.get("research_context"):
            from services.research_playbook import experiment

            item["experiment"] = experiment(remedy, origin_diagnosis, rationale.strip())
        if diagnosis is not None:
            item["decision_diagnosis"] = deepcopy(diagnosis)
        if option:
            item["candidate"] = {k: v for k, v in option.items() if k != "settings"}
            item["experiment_policy"] = EXPERIMENT_POLICY
            item["experiment"].update(
                change=option["change"], expected_evidence=option["expected_effect"]
            )
        if option and option.get("origin_job_id"):
            if origin_artifact != current["source_result_artifact"]:
                state.setdefault("frontier_transitions", []).append(
                    {
                        "from": current["source_result_artifact"],
                        "to": origin_artifact,
                        "origin_job_id": origin_job_id,
                        "candidate_id": candidate_id,
                        "after_proposals": len(state["proposals"]),
                        "attempts_already_spent": 512 - _remaining(state),
                        "reason": "The model selected this immutable checked-branch option from the complete frontier; no outcome was accepted by changing branches.",
                    }
                )
            state.update(
                latest_seed_job_id=origin_job_id,
                latest_source_result_artifact=origin_artifact,
                diagnosis=origin_diagnosis,
                latest_conclusion=origin_conclusion,
            )
            if origin_blocked:
                state["blocked_recovery"] = origin_recovery
            else:
                state.pop("blocked_recovery", None)
        state["proposals"].append(item)
        _put(row, state)
    return {"proposal": item, "reused": False}


def _verified_conclusion(report, supervisor_id, proposal_id, root_job_id, store, owner):
    _, _, _, _ = _parent(store, owner, root_job_id)
    root = service.get_job(store, owner, root_job_id)
    value = report.get("supervisor_validation", {})
    native = {
        k: v
        for k, v in report.items()
        if k not in ("supervisor_validation", "automatic_research", "experiment")
    }
    operating = value.get("operating_validation", {})
    if (
        value.get("version") != "research-supervisor-validation-v1"
        or value.get("id") != fingerprint({k: v for k, v in value.items() if k != "id"})
        or value.get("supervisor_id") != supervisor_id
        or value.get("proposal_id") != proposal_id
        or value.get("source_result_artifact") != root.result_artifact
        or value.get("native_report_id") != fingerprint(native)
        or value.get("selected_config_id") != settings_identity(native.get("strategies"))
        or value.get("capital") != native.get("summary", {}).get("initial_capital")
        or value.get("independently_validated") is not False
        or value.get("outcome") not in ("validated", "rejected", "blocked")
        or operating.get("id") != fingerprint({k: v for k, v in operating.items() if k != "id"})
        or operating.get("selected_report_id") != value["native_report_id"]
        or operating.get("selected_config_id") != value["selected_config_id"]
        or operating.get("capital") != value["capital"]
    ):
        raise ValueError("The supervisor native result binding changed")
    if value["outcome"] == "validated" and (
        value.get("reasons") != []
        or value.get("historically_validated") is not True
        or operating.get("outcome") != "validated"
        or operating.get("historically_validated") is not True
        or operating.get("reasons") != []
        or len(operating.get("diagnostics", [])) < 3
        or any(d.get("passed") is not True for d in operating["diagnostics"])
        or native.get("portfolio_constraint_evidence", {}).get("status") != "supported"
    ):
        raise ValueError("The supervisor cannot accept failed or incomplete validation")
    return value


def advance(store, owner, supervisor_id, *, conversation_id):
    current = status(store, owner, supervisor_id, conversation_id=conversation_id)
    pending = next((p for p in current["proposals"] if not p.get("outcome")), None)
    if pending is None or current["status"] in ("accepted", "saved", "stopped"):
        return current
    if pending["job_id"]:
        job = service.get_job(store, owner, pending["job_id"])
        if job.status in service.ACTIVE or job.status == "paused":
            return {**current, "pending_job": service.job_receipt(store, job)}
        completed_report = (
            service.read_artifact(store, job.result_artifact)["result"]
            if job.status == "completed"
            else None
        )
        conclusion = (
            _verified_conclusion(
                completed_report, supervisor_id, pending["id"], current["root_job_id"], store, owner
            )
            if completed_report
            else {}
        )
        blocked_recovery = (
            _recovery_record(store, owner, job, completed_report, pending)
            if completed_report is not None
            and conclusion.get("outcome") == "blocked"
            and current.get("continuation_policy") == ADAPTIVE_POLICY
            and current["validation_mode"] == "exploratory_only"
            else None
        )
        if completed_report and current.get("research_context"):
            from services.research_playbook import validate_settings

            validate_settings(
                current["research_context"]["contract"], completed_report["strategies"]
            )
            if current["research_context"].get("flow"):
                from research.user_flow import permitted

                authority = current["research_context"]
                if not permitted(
                    completed_report["strategies"],
                    authority["baseline_settings"],
                    authority["flow"],
                ):
                    raise ValueError("The completed experiment changed a fixed user setting")
        retain_stronger_seed = False
        if (
            completed_report is not None
            and conclusion.get("outcome") == "rejected"
            and not conclusion.get("no_effect")
            and current.get("continuation_policy") == ADAPTIVE_POLICY
            and current.get("experiment_policy") == EXPERIMENT_POLICY
            and current["validation_mode"] == "exploratory_only"
            and not current.get("feasibility_only")
        ):
            from services.research_validation_scorecard import dominates

            checked_seed = (
                _parent(store, owner, current["root_job_id"])[2]
                if current["seed_job_id"] == current["root_job_id"]
                else service.read_artifact(store, current["source_result_artifact"])["result"]
            )
            retain_stronger_seed = dominates(checked_seed, completed_report)
        with store.sessions.begin() as db:
            service.write_guard(db)
            row, state = _owned(db, owner, supervisor_id, conversation_id)
            if current.get("frozen_exclusions") is not None:
                state["frozen_exclusions"] = deepcopy(current["frozen_exclusions"])
            item = next(p for p in state["proposals"] if p["id"] == pending["id"])
            if item.get("outcome") or state["status"] in ("accepted", "saved", "stopped"):
                return {"id": supervisor_id, "supervisor_id": supervisor_id, **state}
            item["outcome"] = conclusion.get("outcome", "blocked")
            item["reasons"] = conclusion.get(
                "reasons",
                [
                    getattr(job, "error", None)
                    or f"The native experiment {job.status}; retained the last checked result"
                ],
            )
            if conclusion.get("no_effect"):
                item["no_effect"] = deepcopy(conclusion["no_effect"])
            if retain_stronger_seed:
                item["planning_branch"] = {
                    "retained_job_id": current["seed_job_id"],
                    "reason": "The existing checked development branch has strictly fewer failed gates without a gate regression; keep it as the planning source. The new attempt remains rejected and fully recorded.",
                }
            if item.get("experiment"):
                from services.research_experiments import (
                    CONFIGURATION_VERSION,
                    configuration_identity,
                )

                item["experiment"]["feedback"] = {
                    "outcome": item["outcome"],
                    "reasons": deepcopy(item["reasons"]),
                    "no_effect": bool(item.get("no_effect")),
                    "validated_settings_id": settings_identity(completed_report["strategies"])
                    if completed_report
                    else None,
                    "configuration_version": CONFIGURATION_VERSION,
                    "configuration_id": configuration_identity(
                        completed_report["strategies"],
                        engine=completed_report.get("portfolio", {}).get("engine"),
                    )
                    if completed_report
                    else None,
                }
            if item["outcome"] == "validated":
                state.pop("blocked_recovery", None)
                state.update(status="accepted", accepted_job_id=job.id)
            else:
                if blocked_recovery:
                    state["blocked_recovery"] = blocked_recovery
                if (
                    completed_report is not None
                    and item["outcome"] == "rejected"
                    and state["validation_mode"] == "exploratory_only"
                    and conclusion.get("no_effect", {}).get("status") != "unchanged"
                    and not retain_stronger_seed
                ):
                    state["diagnosis"] = (
                        _reviewed_diagnosis(
                            completed_report,
                            _parent(store, owner, current["root_job_id"])[3],
                            state.get("research_context"),
                            state.get("study_memory"),
                            exclusions=state.get("frozen_exclusions"),
                        )
                        if state.get("capital_policy_version")
                        else _diagnosis(completed_report, exclusions=state.get("frozen_exclusions"))
                    )
                    state["latest_conclusion"] = conclusion
                    state["latest_seed_job_id"] = job.id
                    state["latest_source_result_artifact"] = job.result_artifact
                    state.pop("blocked_recovery", None)
                frontier_review = (
                    state.get("continuation_policy") == ADAPTIVE_POLICY
                    and state.get("experiment_policy") == EXPERIMENT_POLICY
                    and state["validation_mode"] == "exploratory_only"
                )
                if (state["diagnosis"]["missing_evidence"] and not frontier_review) or _lost(
                    state
                ) >= 64:
                    state["status"] = "stopped"
                    state["stopping_reason"] = (
                        "Missing saved evidence or the original interrupted-account ceiling prevents continuation"
                    )
                elif not any(not p.get("outcome") for p in state["proposals"]):
                    state["status"] = (
                        "awaiting_plan"
                        if len(state["proposals"]) < state["max_experiments"]
                        and state["validation_mode"] == "exploratory_only"
                        and (
                            state["diagnosis"]["permitted_remedies"]
                            or state.get("experiment_policy") == EXPERIMENT_POLICY
                        )
                        and _lost(state) < 64
                        and (
                            item["outcome"] == "rejected"
                            or blocked_recovery is not None
                            or frontier_review
                            and item["outcome"] == "blocked"
                        )
                        else "stopped"
                    )
                    if state["status"] == "stopped":
                        state["stopping_reason"] = (
                            f"The {ADAPTIVE_EXPERIMENT_LIMIT}-experiment saved-history safety limit is exhausted; {_remaining(state)} of512 accounts remain. Untested settings remain unvalidated"
                            if state.get("continuation_policy") == ADAPTIVE_POLICY
                            and len(state["proposals"]) >= state["max_experiments"]
                            else "The declared experiment allowance is exhausted; untested alternatives remain unvalidated"
                            if state.get("experiment_policy") == EXPERIMENT_POLICY
                            and len(state["proposals"]) >= state["max_experiments"]
                            else "Native attempt blocked: "
                            + "; ".join(str(r) for r in item["reasons"])
                            + ". No unvalidated result was promoted."
                            if item["outcome"] == "blocked"
                            else "No supported unspent development remedy remains; retained original result"
                        )
            _put(row, state)
        return status(store, owner, supervisor_id, conversation_id=conversation_id)
    from services.research_portfolio import replay_inputs

    _, evidence = replay_inputs(store, owner, current["root_job_id"])
    evidence = {
        **evidence,
        "supervisor_program": {
            "version": VERSION,
            "supervisor_id": supervisor_id,
            "proposal_id": pending["id"],
            "root_job_id": current["root_job_id"],
        },
    }
    _, source = service.register_source(store, owner, evidence)
    portfolio = evidence["portfolio"]
    job = service.submit(
        store,
        owner,
        source["id"],
        {"initial_capital": portfolio["capital"]},
        request_id="supervisor_" + pending["id"],
        kind="portfolio_backtest",
        specification={"portfolio": portfolio, "versions": evidence["versions"]},
    )
    with store.sessions.begin() as db:
        service.write_guard(db)
        row, state = _owned(db, owner, supervisor_id, conversation_id)
        item = next(p for p in state["proposals"] if p["id"] == pending["id"])
        if item["job_id"] not in (None, job["id"]):
            raise ValueError("Research proposal job identity changed")
        item["job_id"] = job["id"]
        if not item.get("outcome") and state["status"] not in ("accepted", "saved", "stopped"):
            state["status"] = "running"
        _put(row, state)
    return status(store, owner, supervisor_id, conversation_id=conversation_id)


def save_result(store, owner, supervisor_id, name, *, conversation_id):
    from services.research_mcp import save_accepted_result

    current = status(store, owner, supervisor_id, conversation_id=conversation_id)
    if current["saved_result"]:
        return current["saved_result"]
    if not current["accepted_job_id"]:
        raise ValueError("No candidate passed the complete research validation")
    result = save_accepted_result(
        store,
        owner,
        current["accepted_job_id"],
        name=name,
        request_id="supervisor_save_" + supervisor_id,
    )
    with store.sessions.begin() as db:
        service.write_guard(db)
        row, state = _owned(db, owner, supervisor_id, conversation_id)
        state.update(saved_result=result, status="saved")
        _put(row, state)
    return result


def reopen_result(store, owner, supervisor_id, *, conversation_id):
    from services.research_mcp import reopen_chosen_result

    current = status(store, owner, supervisor_id, conversation_id=conversation_id)
    saved = current.get("saved_result")
    if not saved:
        raise ValueError("This research plan has no saved accepted result")
    return reopen_chosen_result(store, owner, saved["experiment_id"], saved["choice_id"])


def job_finished(store, owner, job_id):
    """Complete predeclared native steps even after the initiating chat returns."""
    with store.sessions() as db:
        rows = [
            (r.id, r.conversation_id)
            for r in db.scalars(
                select(ResearchSupervisor)
                .where(ResearchSupervisor.owner == owner, ResearchSupervisor.state.contains(job_id))
                .limit(4)
            )
        ]
    for identifier, conversation in rows:
        try:
            current = advance(store, owner, identifier, conversation_id=conversation)
            if current.get("accepted_job_id"):
                save_result(
                    store,
                    owner,
                    identifier,
                    "Historically checked research result",
                    conversation_id=conversation,
                )
            elif any(not p.get("outcome") and not p.get("job_id") for p in current["proposals"]):
                advance(store, owner, identifier, conversation_id=conversation)
        except (ValueError, LookupError) as error:
            with store.sessions.begin() as db:
                row, state = _owned(db, owner, identifier, conversation)
                state["attention"] = str(error)[:500]
                _put(row, state)


def start_automatic(
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
    """Confirmed command: enqueue durable work; never call a provider on the hub."""
    from services.research_automation import start

    return start(
        store,
        owner,
        job_id,
        conversation_id=conversation_id,
        request_id=request_id,
        max_experiments=max_experiments,
        model_id=model_id,
        max_model_calls=max_model_calls,
        research_context=research_context,
        experiment_policy=experiment_policy,
        continuation_policy=continuation_policy,
    )
