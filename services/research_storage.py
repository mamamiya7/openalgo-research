"""Offline maintenance for local research evidence; never operates trading data."""

import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import URL

from database.engine_factory import create_db_engine
from database.research_db import ResearchJob, ResearchWorker
from services.scanner_research_service import (
    ACTIVE,
    artifact_dependencies,
    encoded,
    managed_storage_bytes,
    read_artifact,
)

LEASE_SECONDS = 120
MAX_FILES = 100000
MAX_BACKUP_BYTES = 16 * 1024**3
ARTIFACT_NAME = re.compile(r"([a-f0-9]{64})\.json(?:\.gz)?")


@contextmanager
def maintenance(store):
    """Reserve the worker slot atomically; refuse active jobs or a live lease."""
    _safe_file(store.root / "research.db", store.root)
    token = f"maintenance:{uuid.uuid4().hex}"
    now = time.time()
    with store.sessions.begin() as db:
        db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
        worker = db.get(ResearchWorker, 1)
        if worker is None:
            raise ValueError(
                "Research worker metadata is missing; initialize the research store first"
            )
        if worker.token and worker.heartbeat > now - LEASE_SECONDS:
            raise ValueError(
                "Research worker or maintenance lease is active; stop services and wait for lease expiry"
            )
        active = db.scalar(
            select(func.count()).select_from(ResearchJob).where(ResearchJob.status.in_(ACTIVE))
        )
        if active:
            raise ValueError("Research has active jobs; finish, pause or cancel them first")
        worker.token, worker.heartbeat = token, now
    last_refresh = now

    def refresh(force=False):
        nonlocal last_refresh
        now = time.time()
        if force or now - last_refresh >= 30:
            with store.sessions.begin() as db:
                updated = db.execute(
                    update(ResearchWorker)
                    .where(ResearchWorker.id == 1, ResearchWorker.token == token)
                    .values(heartbeat=now)
                )
                if updated.rowcount != 1:
                    raise ValueError("Research maintenance lease was lost")
            last_refresh = now

    try:
        yield refresh
    finally:
        with store.sessions.begin() as db:
            db.execute(
                update(ResearchWorker)
                .where(ResearchWorker.id == 1, ResearchWorker.token == token)
                .values(token=None, heartbeat=0)
            )


def _rows(connection, table, columns):
    # All names are fixed internal literals, never manifest or user SQL.
    existing = (
        connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        .scalars()
        .all()
    )
    if table not in existing:
        return []
    rows = connection.exec_driver_sql(
        f"SELECT {','.join(columns)} FROM {table} LIMIT {MAX_FILES + 1}"
    ).all()
    if len(rows) > MAX_FILES:
        raise ValueError("Metadata exceeds the 100000-row maintenance bound")
    return rows


def _references(engine, *, store=None):
    """Validate cross-table identity while supporting populated milestone schemas."""
    with engine.connect() as db:
        tables = set(
            db.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").scalars()
        )
        if not {"research_sources", "research_jobs", "research_worker"}.issubset(tables):
            raise ValueError("Required research metadata tables are missing")
        if db.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
            raise ValueError("Research metadata integrity check failed")
        sources = _rows(db, "research_sources", ("id", "artifact"))
        jobs = _rows(db, "research_jobs", ("id", "source_id", "result_artifact"))
        experiments = _rows(db, "research_experiments", ("job_id", "checkpoint", "parent_job_id"))
        requests = _rows(db, "research_requests", ("job_id",))
        history = _rows(db, "research_history", ("job_id",))
        attempts = _rows(db, "research_attempts", ("job_id", "previous_job_id", "root_job_id"))
        library_roots = _library_references(db, tables)
        candidate_roots = _candidate_references(db)
        shortlist_roots = _shortlist_references(db, tables)
        comparison_roots = _comparison_references(db, tables)
        decision_roots = _decision_references(db, tables)
        from services.research_chosen_setups import references as chosen_references

        chosen_roots = chosen_references(db, tables)
        automatic_roots = _automatic_period_references(db, tables, store=store)
        supervisor_roots = _supervisor_references(db, tables, store=store)
        journey_roots = _journey_references(db, tables)
        _study_activity_references(db, tables)
    source_ids, job_ids = {row[0] for row in sources}, {row[0] for row in jobs}
    if any(row[1] not in source_ids for row in jobs):
        raise ValueError("Research job references a missing source")
    if any(row[0] not in job_ids or (row[2] and row[2] not in job_ids) for row in experiments):
        raise ValueError("Research experiment references a missing job")
    if any(row[0] not in job_ids for row in requests + history):
        raise ValueError("Research request/history references a missing job")
    if any(identifier not in job_ids for row in attempts for identifier in row):
        raise ValueError("Research attempt references a missing job")
    return (
        {row[1] for row in sources}
        | {row[2] for row in jobs if row[2]}
        | {row[1] for row in experiments if row[1]}
        | library_roots
        | candidate_roots
        | shortlist_roots
        | comparison_roots
        | decision_roots
        | chosen_roots
        | automatic_roots
        | supervisor_roots
        | journey_roots
    )


def _journey_references(connection, tables):
    """Reviewed input snapshots and compact memory remain owned retained roots."""
    if "research_journeys" not in tables:
        return set()
    from services.research_journey import MAX_STATE_BYTES, VERSION

    if connection.exec_driver_sql(
        "SELECT 1 FROM research_journeys WHERE length(CAST(state AS BLOB)) > ? LIMIT 1",
        (MAX_STATE_BYTES,),
    ).first():
        raise ValueError("Research journey state exceeds its maintenance bound")
    parents = dict(_rows(connection, "research_library_experiments", ("id", "owner")))
    jobs = dict(_rows(connection, "research_jobs", ("id", "owner")))
    roots = set()
    for identifier, owner, phase, raw in _rows(
        connection, "research_journeys", ("experiment_id", "owner", "phase", "state")
    ):
        state = json.loads(raw)
        if (
            parents.get(identifier) != owner
            or phase
            not in (
                "preparing",
                "review",
                "researching",
                "finalizing",
                "complete",
                "none",
                "needs_action",
            )
            or state.get("version") != VERSION
        ):
            raise ValueError("Research journey metadata no longer matches its study")
        if state.get("job_id") and jobs.get(state["job_id"]) != owner:
            raise ValueError("Research journey references a missing owned job")
        history = state.get("history", [])
        if not isinstance(history, list) or len(history) > 24:
            raise ValueError("Research journey memory exceeds its supported bound")
        entries = [state.get("plan") or {}, state.get("approved_plan") or {}, *history]
        for entry in entries:
            for field in ("inputs_artifact", "prepared_inputs_artifact"):
                value = entry.get(field)
                if value is not None:
                    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                        raise ValueError("Research journey references invalid input evidence")
                    roots.add(value)
    return roots


def _supervisor_references(connection, tables, *, store=None):
    """Retain bounded research grants and verify their exact historical chain."""
    table = "research_supervisors"
    if table not in tables:
        return set()
    from research.report_contract import fingerprint, settings_identity
    from services.research_supervisor import (
        ADAPTIVE_EXPERIMENT_LIMIT,
        ADAPTIVE_POLICY,
        CONCRETE_EXPERIMENT_LIMIT,
        EXPERIMENT_POLICY,
        MAX_LEDGER_BYTES,
        REMEDIES,
        VERSION,
    )

    if connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() > MAX_FILES:
        raise ValueError("Research supervisor metadata exceeds its maintenance bound")
    if connection.exec_driver_sql(
        f"SELECT 1 FROM {table} WHERE length(CAST(state AS BLOB)) > ? LIMIT 1",
        (MAX_LEDGER_BYTES,),
    ).first():
        raise ValueError("Research supervisor state exceeds its size bound")
    jobs = {
        row[0]: row[1:]
        for row in _rows(connection, "research_jobs", ("id", "owner", "result_artifact", "status"))
    }
    requests = {
        (row[0], row[1]): row[2:]
        for row in _rows(
            connection, "research_requests", ("owner", "token", "job_id", "payload_hash")
        )
    }
    choices = {
        row[0]: row[1:]
        for row in _rows(
            connection, "research_chosen_setups", ("id", "owner", "experiment_id", "snapshot")
        )
    }
    library = dict(_rows(connection, "research_library_experiments", ("id", "owner")))

    def hex_id(value, length=64):
        return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value)

    def integer(value, maximum):
        return type(value) is int and 0 <= value <= maximum

    def fail():
        raise ValueError("Research supervisor references invalid or changed historical evidence")

    roots = set()
    cursor = connection.exec_driver_sql(
        "SELECT id,owner,root_job_id,root_artifact,conversation_id,request_id,state,updated_at "
        "FROM research_supervisors"
    )
    try:
        for identifier, owner, root, artifact, conversation, request, raw, updated in cursor:
            if (
                not hex_id(identifier, 32)
                or not isinstance(owner, str)
                or not 1 <= len(owner) <= 255
                or not owner.strip()
                or jobs.get(root) != (owner, artifact, "completed")
                or not hex_id(artifact)
                or not isinstance(conversation, str)
                or not 1 <= len(conversation.strip()) <= 128
                or not isinstance(request, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request)
                or type(updated) not in (int, float)
                or not math.isfinite(updated)
                or updated < 0
                or not isinstance(raw, str)
            ):
                fail()
            try:
                state = json.loads(
                    raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
                )
            except (ValueError, TypeError, RecursionError):
                fail()
            if not isinstance(state, dict):
                fail()
            capital_policy = state.get("capital_policy_version")
            if capital_policy not in (
                None,
                "research-capital-planning-v6",
                "research-capital-planning-v7",
                "research-capital-planning-v8",
            ):
                fail()
            proposals = state.get("proposals")
            maximum = state.get("max_experiments")
            mode = state.get("validation_mode")
            attempts, simulations = (
                state.get("original_attempts"),
                state.get("original_simulations"),
            )
            if (
                state.get("version") != VERSION
                or state.get("objective") != "robust_improvement"
                or type(maximum) is not int
                or state.get("continuation_policy") not in (None, ADAPTIVE_POLICY)
                or (
                    state.get("continuation_policy") == ADAPTIVE_POLICY
                    and (
                        state.get("experiment_policy") != EXPERIMENT_POLICY
                        or mode != "exploratory_only"
                    )
                )
                or not 1
                <= maximum
                <= (
                    (
                        ADAPTIVE_EXPERIMENT_LIMIT
                        if state.get("continuation_policy") == ADAPTIVE_POLICY
                        else CONCRETE_EXPERIMENT_LIMIT
                    )
                    if state.get("experiment_policy") == EXPERIMENT_POLICY
                    else 3
                )
                or mode not in ("exploratory_only", "chronological", "historical_only")
                or state.get("status")
                not in ("planning", "running", "awaiting_plan", "accepted", "saved", "stopped")
                or not integer(attempts, 512)
                or not integer(simulations, attempts)
                or not isinstance(proposals, list)
                or len(proposals) > maximum
                or (mode != "exploratory_only" and len(proposals) > 1)
            ):
                fail()
            if store is not None:
                report = read_artifact(store, artifact).get("result", {})
                saved = report.get("automatic_research", {})
                counts = saved.get("counts", {})
                recipe = saved.get("recipe", report.get("automatic_recipe", {}))
                if (
                    counts.get("attempts_reserved") != attempts
                    or counts.get("simulations") != simulations
                    or recipe.get("version") != "automatic-hypotheses-v16"
                    or recipe.get("validation_mode") != mode
                ):
                    fail()
                del report, saved, counts, recipe
            roots.add(artifact)
            seen, children = set(), set()
            seeds = {root: artifact}
            blocked_seeds = set()
            committed = attempts
            lost = attempts - simulations
            accepted = []
            for proposal in proposals:
                if not isinstance(proposal, dict):
                    fail()
                pid, remedy = proposal.get("id"), proposal.get("remedy")
                seed, source = proposal.get("seed_job_id"), proposal.get("source_result_artifact")
                reservation, spent = proposal.get("reserved_accounts"), proposal.get("spent")
                completed, outcome, child = (
                    proposal.get("completed", 0),
                    proposal.get("outcome"),
                    proposal.get("job_id"),
                )
                expected = (
                    (17 if remedy == "funding_and_size" else 14)
                    if mode == "exploratory_only"
                    else 24
                )
                rationale = proposal.get("rationale")
                if (
                    not hex_id(pid)
                    or proposal.get("capital_policy_version") != capital_policy
                    or pid in seen
                    or remedy not in REMEDIES
                    or not isinstance(seed, str)
                    or seed not in seeds
                    or source != seeds.get(seed)
                    or not hex_id(source)
                    or jobs.get(seed) != (owner, source, "completed")
                    or (
                        seed in blocked_seeds
                        and (remedy != "funding_and_size" or mode != "exploratory_only")
                    )
                    or not isinstance(rationale, str)
                    or not 1 <= len(rationale.strip()) <= 1000
                    or settings_identity(proposal.get("settings")) is None
                    or len(proposal["settings"]) > 8
                    or type(reservation) is not int
                    or reservation != expected
                    or not integer(spent, reservation)
                    or not integer(completed, spent)
                    or outcome not in (None, "validated", "rejected", "blocked")
                    or pid
                    != fingerprint(
                        {
                            "remedy": remedy,
                            "settings": proposal["settings"],
                            "source_result_artifact": source,
                            "root_job_id": root,
                            **(
                                {"capital_policy_version": proposal["capital_policy_version"]}
                                if proposal.get("capital_policy_version")
                                else {}
                            ),
                        }
                    )
                    or (child is None and (spent or completed or outcome is not None))
                    or (
                        mode != "exploratory_only"
                        and (remedy != "funding_and_size" or seed != root)
                    )
                ):
                    fail()
                seen.add(pid)
                committed += spent if outcome is not None else reservation
                lost += spent - completed
                if committed > 512 or lost > 64:
                    fail()
                if child is not None:
                    receipt = requests.get((owner, "supervisor_" + pid))
                    if (
                        not isinstance(child, str)
                        or child in children
                        or child == root
                        or child not in jobs
                        or jobs[child][0] != owner
                        or receipt is None
                        or receipt[0] != child
                        or not hex_id(receipt[1])
                        or (outcome == "validated" and jobs[child][2] != "completed")
                    ):
                        fail()
                    children.add(child)
                    if jobs[child][1]:
                        if not hex_id(jobs[child][1]):
                            fail()
                        roots.add(jobs[child][1])
                    if outcome in ("rejected", "blocked") and jobs[child][2] == "completed":
                        seeds[child] = jobs[child][1]
                        if outcome == "blocked":
                            blocked_seeds.add(child)
                    if outcome == "validated":
                        accepted.append(child)
                roots.add(source)
            chosen = state.get("accepted_job_id")
            if (chosen is None and accepted) or (chosen is not None and accepted != [chosen]):
                fail()
            if state["status"] in ("accepted", "saved") and chosen is None:
                fail()
            if chosen is not None and state["status"] not in ("accepted", "saved"):
                fail()
            latest = state.get("latest_source_result_artifact")
            recovery = state.get("blocked_recovery")
            if recovery is not None and (
                not isinstance(recovery, dict)
                or recovery.get("scope") != "feasibility_only"
                or recovery.get("job_id") not in blocked_seeds
                or seeds.get(recovery.get("job_id")) != recovery.get("artifact")
                or not hex_id(recovery.get("source_settings_id"))
                or not isinstance(recovery.get("unresolved_constraints"), dict)
            ):
                fail()
            if latest is not None and latest not in seeds.values():
                fail()
            latest_seed = state.get("latest_seed_job_id")
            if latest_seed is not None and (
                not isinstance(latest_seed, str) or seeds.get(latest_seed) != latest
            ):
                fail()
            saved_result = state.get("saved_result")
            if saved_result is not None:
                if (
                    not isinstance(saved_result, dict)
                    or chosen is None
                    or state["status"] != "saved"
                ):
                    fail()
                experiment, choice_id = (
                    saved_result.get("experiment_id"),
                    saved_result.get("choice_id"),
                )
                choice = choices.get(choice_id) if isinstance(choice_id, str) else None
                if (
                    not hex_id(experiment, 32)
                    or not hex_id(choice_id, 32)
                    or library.get(experiment) != owner
                    or choice is None
                    or choice[:2] != (owner, experiment)
                    or not hex_id(saved_result.get("supervisor_validation_id"))
                ):
                    fail()
                try:
                    snapshot = json.loads(choice[2])
                except (TypeError, ValueError):
                    fail()
                if (
                    not isinstance(snapshot, dict)
                    or snapshot.get("report_job_id") != chosen
                    or snapshot.get("report_result_artifact") != jobs[chosen][1]
                ):
                    fail()
            elif state["status"] == "saved":
                fail()
            automatic = state.get("automation")
            if automatic is not None:
                from services.research_automation import VERSION as automatic_version

                if (
                    not isinstance(automatic, dict)
                    or automatic.get("version") != automatic_version
                    or not integer(
                        automatic.get("max_model_calls"),
                        ADAPTIVE_EXPERIMENT_LIMIT
                        if state.get("continuation_policy") == ADAPTIVE_POLICY
                        else 3,
                    )
                    or not integer(automatic.get("calls_spent"), automatic["max_model_calls"])
                    or not isinstance(automatic.get("decisions"), list)
                    or len(automatic["decisions"]) > maximum + 1
                    or (
                        automatic.get("model_id") is not None
                        and (type(automatic["model_id"]) is not int or automatic["model_id"] < 1)
                    )
                ):
                    fail()
                for decision in automatic["decisions"]:
                    if (
                        not isinstance(decision, dict)
                        or decision.get("remedy") not in (*REMEDIES, "stop")
                        or not isinstance(decision.get("reason"), str)
                        or not 1 <= len(decision["reason"]) <= 1000
                        or decision.get("seed_artifact") not in seeds.values()
                    ):
                        fail()
                if "research_decision_jobs" not in tables:
                    fail()
                queued = connection.exec_driver_sql(
                    "SELECT owner,phase,claim_token FROM research_decision_jobs WHERE supervisor_id=?",
                    (identifier,),
                ).fetchone()
                if (
                    not queued
                    or queued[0] != owner
                    or queued[1]
                    not in (
                        "queued",
                        "deciding",
                        "waiting",
                        "applying",
                        "finalizing",
                        "completed",
                        "stopped",
                        "cancelled",
                    )
                ):
                    fail()
    finally:
        cursor.close()
    if "research_decision_jobs" in tables:
        orphan = connection.exec_driver_sql(
            "SELECT 1 FROM research_decision_jobs d LEFT JOIN research_supervisors s "
            "ON s.id=d.supervisor_id WHERE s.id IS NULL OR s.owner!=d.owner LIMIT 1"
        ).first()
        if orphan:
            fail()
    return roots


def _automatic_period_references(connection, tables, *, store=None):
    """Retain consumed-period bindings, including cancelled/orphaned launches.

    Old metadata without this additive table remains valid. Maintenance callers
    supply the artifact store to also verify every claim's original chronology;
    only one input artifact is retained in memory at a time.
    """
    from services.research_automatic_periods import MAX_ROWS

    table = "research_automatic_period_use"
    if table not in tables:
        return set()
    rows = _rows(
        connection, table, ("owner", "day", "inputs_artifact", "job_id", "role", "created_at")
    )
    if len(rows) > MAX_ROWS:
        raise ValueError("Saved automatic period history exceeds its maintenance bound")
    roots, identity = set(), set()
    retained_digest, periods, sessions = None, None, None
    jobs = dict(_rows(connection, "research_jobs", ("id", "owner")))
    for owner, day, digest, job_id, role, created in sorted(rows, key=lambda row: str(row[2])):
        try:
            valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid_day = False
        if (
            not isinstance(owner, str)
            or not owner.strip()
            or len(owner) > 255
            or any(ord(char) < 32 for char in owner)
            or not valid_day
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or not isinstance(job_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", job_id)
            or job_id in jobs
            and jobs[job_id] != owner
            or role not in ("development", "final")
            or type(created) not in (int, float)
            or not math.isfinite(created)
            or created < 0
            or (owner, day) in identity
        ):
            raise ValueError("Saved automatic period history has invalid metadata")
        identity.add((owner, day))
        roots.add(digest)
        if store is None:
            continue
        if digest != retained_digest:
            from research.automatic_protocol import CONDITION_VERSION
            from research.portfolio import fingerprint

            _artifact_path(store.root, digest)
            evidence = read_artifact(store, digest)
            recipe = evidence.get("automatic_recipe")
            if (
                not isinstance(recipe, dict)
                or recipe.get("version") != CONDITION_VERSION
                or recipe.get("id")
                != fingerprint({key: value for key, value in recipe.items() if key != "id"})
                or evidence.get("portfolio", {}).get("automatic_research", {}).get("version")
                != CONDITION_VERSION
            ):
                raise ValueError("Saved automatic period history lost its original recipe")
            periods = recipe.get("periods", {})
            try:
                for name in ("search", "check1", "check2", "final"):
                    for edge in ("from", "to"):
                        value = periods[name][edge]
                        if date.fromisoformat(value).isoformat() != value:
                            raise ValueError
                    if periods[name]["from"] > periods[name]["to"]:
                        raise ValueError
                sessions = set(evidence["snapshot"]["sessions"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "Saved automatic period history has invalid original dates"
                ) from None
            retained_digest = digest
            del evidence
        expected_role = (
            "final" if periods["final"]["from"] <= day <= periods["final"]["to"] else "development"
        )
        if (
            day not in sessions
            or not periods["search"]["from"] <= day <= periods["final"]["to"]
            or role != expected_role
        ):
            raise ValueError("Saved automatic period history differs from its original chronology")
    return roots


def _study_activity_references(db, tables):
    """Validate additive observation metadata without requiring it in old stores."""
    from services.research_study_activity import (
        EXECUTION_STATES,
        MAX_EXECUTIONS_PER_JOB,
        MAX_PARAMS_BYTES,
        MAX_PROPOSALS,
        PROPOSAL_STATES,
        REASONS,
        VERSION,
        _params,
    )

    expected = {"research_study_executions", "research_study_proposals"}
    if not expected.intersection(tables):
        return
    if not expected.issubset(tables):
        raise ValueError("Research study activity metadata tables are incomplete")
    executions = _rows(
        db,
        "research_study_executions",
        (
            "id",
            "job_id",
            "owner",
            "worker",
            "version",
            "state",
            "proposal_budget",
            "replayed",
            "started_at",
            "finished_at",
            "observed_at",
            "reason_code",
        ),
    )
    import math
    from collections import Counter

    def timestamp(value, *, optional=False):
        return (optional and value is None) or (
            isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
        )

    jobs = {row[0]: row[1] for row in _rows(db, "research_jobs", ("id", "owner"))}
    kinds = dict(_rows(db, "research_experiments", ("job_id", "kind")))
    per_job = Counter(row[1] for row in executions)
    if any(count > MAX_EXECUTIONS_PER_JOB for count in per_job.values()):
        raise ValueError("Research study activity execution bound exceeded")
    execution_map = {}
    for row in executions:
        (
            identifier,
            job,
            owner,
            worker,
            version,
            state,
            budget,
            replayed,
            started,
            finished,
            observed,
            reason,
        ) = row
        if (
            not re.fullmatch(r"[a-f0-9]{32}", identifier or "")
            or jobs.get(job) != owner
            or kinds.get(job) != "portfolio_optimize"
            or not isinstance(worker, str)
            or not 1 <= len(worker) <= 64
            or version != VERSION
            or state not in EXECUTION_STATES
            or not isinstance(budget, int)
            or not 1 <= budget <= MAX_PROPOSALS
            or not isinstance(replayed, int)
            or not 0 <= replayed <= budget
            or not timestamp(started)
            or not timestamp(observed)
            or not timestamp(finished, optional=True)
            or reason not in (*REASONS, None)
            or (state == "running" and (finished is not None or reason is not None))
            or (state == "completed" and (finished is None or reason is not None))
        ):
            raise ValueError("Research study execution references invalid or foreign metadata")
        execution_map[identifier] = row
    counts = Counter()
    open_counts = Counter()

    def validate_proposal(row):
        (
            identifier,
            execution_id,
            job,
            number,
            config,
            params,
            state,
            value,
            reused,
            started,
            finished,
            observed,
            reason,
            checkpointed,
        ) = row
        execution = execution_map.get(execution_id)
        try:
            valid_params = isinstance(params, str) and _params(json.loads(params)) == params
        except (ValueError, TypeError, OverflowError):
            valid_params = False
        outcome = state in ("evaluated", "reused", "allocation_rejected")
        if (
            not isinstance(identifier, int)
            or identifier < 1
            or execution is None
            or job != execution[1]
            or not isinstance(number, int)
            or not execution[7] <= number < execution[6]
            or not re.fullmatch(r"[a-f0-9]{64}", config or "")
            or not valid_params
            or state not in PROPOSAL_STATES
            or reused not in (0, 1)
            or checkpointed not in (0, 1)
            or not timestamp(started)
            or not timestamp(observed)
            or not timestamp(finished, optional=True)
            or reason not in (*REASONS, None)
            or (checkpointed and not outcome)
            or (
                state == "running"
                and (execution[5] != "running" or finished is not None or reason is not None)
            )
            or (outcome and (finished is None or reason is not None))
            or (
                state in ("evaluated", "reused")
                and (not isinstance(value, (int, float)) or not math.isfinite(value))
            )
            or (state not in ("evaluated", "reused") and value is not None)
            or (state == "evaluated" and reused)
            or (state == "reused" and not reused)
        ):
            raise ValueError("Research study proposal references invalid or foreign metadata")
        counts[execution_id] += 1
        open_counts[execution_id] += state == "running"

    # Stream bounded batches so a large retained observation history does not
    # materialize every parameter document at once. Reject oversized imported
    # cells in SQLite before copying their contents into Python memory.
    with db.exec_driver_sql(
        "SELECT id,execution_id,job_id,number,config_id,"
        "CASE WHEN length(CAST(params AS BLOB)) <= ? THEN params ELSE NULL END,"
        "state,value,reused,started_at,finished_at,observed_at,reason_code,checkpointed "
        "FROM research_study_proposals LIMIT ?",
        (MAX_PARAMS_BYTES, MAX_FILES + 1),
    ) as proposals:
        for index, row in enumerate(proposals.yield_per(100)):
            if index >= MAX_FILES:
                raise ValueError("Metadata exceeds the 100000-row maintenance bound")
            validate_proposal(row)
    if any(count > MAX_PROPOSALS for count in counts.values()) or any(
        count > 1 for count in open_counts.values()
    ):
        raise ValueError("Research study proposal bound exceeded")


def _comparison_references(db, tables):
    """Pinned comparison roots survive source bookmark removal and later overlays."""
    from services import research_library as library
    from services.research_comparisons import (
        MAX_PER_EXPERIMENT,
        MAX_ROWS,
        MAX_SNAPSHOT_BYTES,
        validate_snapshot,
    )

    table = "research_comparisons"
    if table not in tables:
        return set()
    if db.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() > MAX_ROWS:
        raise ValueError("Saved comparisons exceed the maintenance row bound")
    if db.exec_driver_sql(
        f"SELECT 1 FROM {table} GROUP BY experiment_id HAVING count(*) > ? LIMIT 1",
        (MAX_PER_EXPERIMENT,),
    ).first():
        raise ValueError("Saved comparisons exceed the experiment bound")
    if db.exec_driver_sql(
        f"SELECT 1 FROM {table} WHERE length(CAST(snapshot AS BLOB)) > ? OR length(member_names) > 2048 OR length(name) > 120 OR length(note) > 2000 LIMIT 1",
        (MAX_SNAPSHOT_BYTES,),
    ).first():
        raise ValueError("Saved comparison metadata exceeds its size limit")
    experiments = {
        row[0]: row[1] for row in _rows(db, "research_library_experiments", ("id", "owner"))
    }
    jobs = {row[0]: row[1] for row in _rows(db, "research_jobs", ("id", "owner"))}
    kinds = {
        row[0]: row[1:]
        for row in _rows(db, "research_experiments", ("job_id", "kind", "parent_job_id"))
    }
    roots, requests, numbers = set(), set(), set()
    with db.exec_driver_sql(
        f"SELECT id,owner,experiment_id,number,name,note,revision,request_id,request_hash,member_count,member_names,reference_member_id,compatible,currency,snapshot,created_at,updated_at FROM {table}"
    ) as cursor:
        while rows := cursor.fetchmany(8):
            for (
                identifier,
                owner,
                experiment,
                number,
                name,
                note,
                revision,
                request_id,
                digest,
                member_count,
                member_names,
                reference,
                compatible,
                currency,
                raw,
                created,
                updated,
            ) in rows:
                if (
                    experiments.get(experiment) != owner
                    or not isinstance(identifier, str)
                    or not re.fullmatch(r"[a-f0-9]{32}", identifier)
                    or type(number) is not int
                    or number < 1
                    or type(revision) is not int
                    or revision < 1
                    or not isinstance(request_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or (owner, request_id) in requests
                    or (experiment, number) in numbers
                    or any(
                        not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value <= 0
                        for value in (created, updated)
                    )
                    or updated < created
                ):
                    raise ValueError("Saved comparison has invalid or foreign metadata")
                library._text(name, 120, "Comparison name")
                library._text(note, 2000, "Comparison note", empty=True)
                try:
                    snapshot = json.loads(raw)
                    validate_snapshot(snapshot)
                    members = snapshot["members"]
                    if (
                        member_count != len(members)
                        or json.loads(member_names) != [item["name"] for item in members]
                        or reference not in {item["id"] for item in members}
                        or reference != snapshot["reference_member_id"]
                        or compatible != snapshot["presentation"]["compatible"]
                        or currency != snapshot["presentation"]["currency"]
                    ):
                        raise ValueError()
                    if (
                        hashlib.sha256(
                            encoded(
                                {
                                    "experiment_id": experiment,
                                    "candidate_ids": [item["id"] for item in members],
                                    "reference_candidate_id": reference,
                                }
                            )
                        ).hexdigest()
                        != digest
                    ):
                        raise ValueError()
                except (ValueError, TypeError, KeyError, RecursionError):
                    raise ValueError("Saved comparison has invalid snapshot metadata") from None
                for member in members:
                    source_kind = (
                        "portfolio_optimize"
                        if member["origin_kind"] == "study"
                        else "portfolio_backtest"
                    )
                    report_kind = (
                        source_kind
                        if member["source_job_id"] == member["report_job_id"]
                        else "portfolio_backtest"
                    )
                    if (
                        any(
                            jobs.get(member[key]) != owner
                            for key in ("source_job_id", "report_job_id")
                        )
                        or kinds.get(member["source_job_id"], (None,))[0] != source_kind
                        or kinds.get(member["report_job_id"], (None,))[0] != report_kind
                    ):
                        raise ValueError("Saved comparison references foreign or invalid reports")
                    if member["analysis_job_id"] and (
                        jobs.get(member["analysis_job_id"]) != owner
                        or kinds.get(member["analysis_job_id"])
                        != ("portfolio_analysis", member["report_job_id"])
                    ):
                        raise ValueError("Saved comparison references foreign or invalid analysis")
                    roots.update(
                        member[key]
                        for key in (
                            "source_result_artifact",
                            "report_result_artifact",
                            "analysis_artifact",
                        )
                        if member.get(key)
                    )
                requests.add((owner, request_id))
                numbers.add((experiment, number))
    return roots


def _decision_references(connection, tables):
    """Stream bounded decision history; preserve exact report pins without recalculation."""
    from sqlalchemy.orm import Session

    from database.research_db import (
        ResearchDecision,
        ResearchDecisionEvent,
        ResearchDecisionRequest,
        ResearchDecisionTarget,
        ResearchEvidenceOpen,
        ResearchLibraryExperiment,
    )
    from services import research_decisions as decisions
    from services import research_library as library

    required = {
        "research_decisions",
        "research_decision_events",
        "research_decision_requests",
        "research_evidence_opens",
    }
    if not required.intersection(tables):
        if (
            "research_decision_targets" in tables
            and connection.exec_driver_sql(
                "SELECT 1 FROM research_decision_targets LIMIT 1"
            ).first()
        ):
            raise ValueError("Direct decision selection is orphaned")
        return set()
    if not required.issubset(tables):
        raise ValueError("Decision metadata tables are incomplete")
    has_direct = "research_decision_targets" in tables
    if has_direct:
        if (
            connection.exec_driver_sql("SELECT count(*) FROM research_decision_targets").scalar()
            > decisions.MAX_ROWS
        ):
            raise ValueError("Direct decision selections exceed their limit")
        if connection.exec_driver_sql(
            "SELECT 1 FROM research_decision_targets t LEFT JOIN research_decision_events e ON e.id=t.event_id "
            "WHERE e.id IS NULL OR e.comparison_id != '' OR length(CAST(t.selection AS BLOB)) > ? OR length(t.target) > 512 LIMIT 1",
            (decisions.MAX_SNAPSHOT_BYTES,),
        ).first():
            raise ValueError("Direct decision selection is orphaned or exceeds its limit")
    for table in sorted(required):
        if (
            connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar()
            > decisions.MAX_ROWS
        ):
            raise ValueError("Decision history exceeds the maintenance row bound")
    for table, clause, bound in (
        (
            "research_decision_events",
            "length(CAST(evaluation_pin AS BLOB)) > ? OR length(evidence_use) > 4096 OR length(reason) > 2000 OR length(candidate_name) > 120 OR length(comparison_name) > 120",
            decisions.MAX_SNAPSHOT_BYTES,
        ),
        (
            "research_evidence_opens",
            "length(CAST(report_pin AS BLOB)) > ? OR length(target) > 2048",
            decisions.MAX_SNAPSHOT_BYTES,
        ),
        (
            "research_decision_requests",
            "length(CAST(payload AS BLOB)) > ?",
            decisions.MAX_BODY_BYTES,
        ),
    ):
        if connection.exec_driver_sql(
            f"SELECT 1 FROM {table} WHERE {clause} LIMIT 1", (bound,)
        ).first():
            raise ValueError("Decision metadata exceeds its size limit")
    if connection.exec_driver_sql(
        "SELECT 1 FROM research_decisions GROUP BY experiment_id HAVING count(*) > ? LIMIT 1",
        (decisions.MAX_PER_EXPERIMENT,),
    ).first():
        raise ValueError("Experiment decisions exceed their limit")
    roots = set()

    def stamp(value):
        return type(value) in (int, float) and math.isfinite(value) and value > 0

    def identity(value, size=32):
        return isinstance(value, str) and re.fullmatch(f"[a-f0-9]{{{size}}}", value)

    def pin_roots(db, owner, pin, member=None, *, later=False):
        decisions.validate_pin(pin)
        if not decisions._pin_owned(db, owner, pin):
            raise ValueError("Decision report references foreign jobs or analysis")
        if later:
            origin = pin["origin"]
            if (
                not origin
                or not member
                or any(
                    origin.get(key) != member[key]
                    for key in ("source_job_id", "source_result_artifact", "config_id")
                )
                or member["period"] != "selection"
            ):
                raise ValueError("Decision later evidence belongs to another candidate")
        elif member and pin != decisions._selection_pin(member):
            raise ValueError("Decision selection pin differs from its saved comparison")
        roots.update(
            pin[key]
            for key in ("result_artifact", "inputs_artifact", "analysis_artifact")
            if pin.get(key)
        )
        if pin["origin"]:
            roots.add(pin["origin"]["source_result_artifact"])

    with Session(bind=connection) as db:
        for head in db.scalars(select(ResearchDecision).order_by(ResearchDecision.id)).yield_per(
            20
        ):
            experiment = db.get(ResearchLibraryExperiment, head.experiment_id)
            current = db.get(ResearchDecisionEvent, head.current_event_id)
            count, maximum = db.execute(
                select(func.count(), func.max(ResearchDecisionEvent.revision)).where(
                    ResearchDecisionEvent.decision_id == head.id
                )
            ).one()
            if (
                not experiment
                or experiment.owner != head.owner
                or not identity(head.id)
                or not identity(head.source_job_id)
                or not identity(head.source_result_artifact, 64)
                or not identity(head.config_id, 64)
                or head.period not in ("full", "selection")
                or type(head.revision) is not int
                or not 1 <= head.revision <= decisions.MAX_EVENTS
                or count != head.revision
                or maximum != head.revision
                or not current
                or current.decision_id != head.id
                or current.revision != head.revision
                or not stamp(head.created_at)
                or not stamp(head.updated_at)
                or head.updated_at < head.created_at
            ):
                raise ValueError("Decision head has invalid or foreign history")
        for item in db.scalars(
            select(ResearchDecisionEvent).order_by(
                ResearchDecisionEvent.decision_id, ResearchDecisionEvent.revision
            )
        ).yield_per(8):
            head = db.get(ResearchDecision, item.decision_id)
            if (
                not head
                or item.owner != head.owner
                or item.experiment_id != head.experiment_id
                or not identity(item.id)
                or type(item.revision) is not int
                or not 1 <= item.revision <= head.revision
                or item.state not in decisions.STATES
                or not stamp(item.created_at)
                or item.created_at < head.created_at
            ):
                raise ValueError("Decision event has invalid or foreign metadata")
            previous = (
                db.get(ResearchDecisionEvent, item.supersedes_event_id)
                if item.supersedes_event_id
                else None
            )
            if (
                item.revision == 1
                and previous is not None
                or item.revision > 1
                and (
                    not previous
                    or previous.decision_id != head.id
                    or previous.revision != item.revision - 1
                    or previous.created_at > item.created_at
                )
                or item.revision == 1
                and item.supersedes_event_id is not None
            ):
                raise ValueError("Decision supersession chain is invalid")
            if not item.comparison_id and not has_direct:
                raise ValueError("Direct decision selection table is missing")
            member = decisions._event_member(db, item)
            if not item.comparison_id:
                from services.research_decision_targets import validate_target_link

                direct = db.get(ResearchDecisionTarget, item.id)
                retained_target = json.loads(direct.target)
                validate_target_link(
                    db,
                    item.owner,
                    item.experiment_id,
                    retained_target,
                    member["source_job_id"],
                    member["config_id"],
                    member["source_result_artifact"],
                )
                pin_roots(db, item.owner, decisions._selection_pin(member), member)
                roots.add(member["source_result_artifact"])
                if "expected_report" in retained_target:
                    displayed = retained_target["expected_report"]
                    roots.update(identity for identity in displayed.values() if identity)
                    if retained_target["job_id"] == member["report_job_id"] and displayed != {
                        "result_artifact": member["report_result_artifact"],
                        "analysis_artifact": member["analysis_artifact"],
                    }:
                        raise ValueError("Displayed selection differs from its retained decision")
            if any(getattr(head, key) != member[key] for key in decisions.IDENTITY):
                raise ValueError("Decision was retargeted to a different candidate")
            library._text(item.reason, 2000, "Decision reason", empty=True)
            library._text(item.candidate_name, 120, "Decision candidate")
            library._text(
                item.comparison_name, 120, "Decision comparison", empty=not item.comparison_id
            )
            use = json.loads(item.evidence_use)
            decisions.validate_use(use)
            if item.evaluation_pin:
                pin = json.loads(item.evaluation_pin)
                pin_roots(db, item.owner, pin, member, later=True)
                if (
                    not use["later_used_for_decision"]
                    or use["reservation"] != pin["origin"]["reservation"]
                ):
                    raise ValueError("Decision evidence use differs from attached later period")
            if (
                db.scalar(
                    select(func.count())
                    .select_from(ResearchDecisionRequest)
                    .where(
                        ResearchDecisionRequest.kind == "decision",
                        ResearchDecisionRequest.result_id == item.id,
                    )
                )
                != 1
            ):
                raise ValueError("Decision event has no unique accepted request")
        for opened in db.scalars(select(ResearchEvidenceOpen)).yield_per(8):
            experiment = db.get(ResearchLibraryExperiment, opened.experiment_id)
            pin, target = json.loads(opened.report_pin), json.loads(opened.target)
            if (
                not experiment
                or experiment.owner != opened.owner
                or not identity(opened.id)
                or not stamp(opened.opened_at)
                or decisions.fingerprint(pin) != opened.evidence_id
            ):
                raise ValueError("Report opening has invalid or foreign metadata")
            member = _decision_open_member(
                db, decisions, opened.owner, opened.experiment_id, target, pin
            )
            pin_roots(
                db, opened.owner, pin, member, later=pin["report_context"]["period"] == "evaluation"
            )
            if not db.scalar(
                select(ResearchDecisionRequest.token)
                .where(
                    ResearchDecisionRequest.kind == "opened",
                    ResearchDecisionRequest.result_id == opened.id,
                )
                .limit(1)
            ):
                raise ValueError("Report opening has no accepted request")
        for request in db.scalars(select(ResearchDecisionRequest)).yield_per(20):
            payload = json.loads(request.payload)
            if (
                request.kind not in ("decision", "opened")
                or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request.token)
                or not isinstance(payload, dict)
                or payload.get("kind") != request.kind
                or payload.get("experiment_id") != request.experiment_id
                or hashlib.sha256(encoded(payload)).hexdigest() != request.payload_hash
            ):
                raise ValueError("Decision retry receipt has invalid binding")
            if request.kind == "decision":
                item = db.get(ResearchDecisionEvent, request.result_id)
                pin = json.loads(item.evaluation_pin) if item and item.evaluation_pin else None
                expected = {
                    "kind": "decision",
                    "experiment_id": request.experiment_id,
                    "comparison_id": item.comparison_id if item else None,
                    "member_id": item.member_id if item else None,
                    "revision": item.revision - 1 if item else None,
                    "state": item.state if item else None,
                    "reason": item.reason if item else None,
                    "evaluation_id": decisions._pin_id(pin) if pin else None,
                }
                if item is not None and not item.comparison_id:
                    direct = db.get(ResearchDecisionTarget, item.id)
                    expected.pop("comparison_id")
                    expected.pop("member_id")
                    retained_target = json.loads(direct.target)
                    expected["target"] = {
                        key: retained_target[key] for key in ("job_id", "config_id")
                    }
                    if "expected_report" in retained_target:
                        expected["expected_report"] = retained_target["expected_report"]
                if (
                    not item
                    or item.owner != request.owner
                    or item.experiment_id != request.experiment_id
                    or payload != expected
                ):
                    raise ValueError("Decision retry receipt points to different evidence")
            else:
                item = db.get(ResearchEvidenceOpen, request.result_id)
                if (
                    not item
                    or item.owner != request.owner
                    or item.experiment_id != request.experiment_id
                    or set(payload) != {"kind", "experiment_id", "target"}
                ):
                    raise ValueError("Opening retry receipt has invalid ownership")
                _decision_open_member(
                    db,
                    decisions,
                    request.owner,
                    request.experiment_id,
                    payload["target"],
                    json.loads(item.report_pin),
                )
    return roots


def _decision_open_member(db, decisions, owner, experiment_id, target, pin):
    """Verify an opening's exact server link without reading result artifacts."""
    if not isinstance(target, dict):
        raise ValueError("Invalid report opening target")
    if target.get("kind") == "comparison_member":
        if set(target) - {"kind", "comparison_id", "member_id", "evaluation_id"}:
            raise ValueError("Invalid comparison opening target")
        _, _, member = decisions._member(
            db, owner, experiment_id, target.get("comparison_id"), target.get("member_id")
        )
        if target.get("evaluation_id"):
            if (
                decisions._pin_id(pin) != target["evaluation_id"]
                or pin["report_context"]["period"] != "evaluation"
            ):
                raise ValueError("Opened later report does not match its target")
        elif pin != decisions._selection_pin(member):
            raise ValueError("Opened comparison report differs from its pin")
    elif target.get("kind") == "decision_event":
        if set(target) != {"kind", "decision_id", "event_id", "evidence"} or target[
            "evidence"
        ] not in ("selection", "evaluation"):
            raise ValueError("Invalid decision opening target")
        _, _, item = decisions._event(
            db, owner, experiment_id, target["decision_id"], target["event_id"]
        )
        member = decisions._event_member(db, item)
        expected = (
            json.loads(item.evaluation_pin)
            if target["evidence"] == "evaluation" and item.evaluation_pin
            else (decisions._selection_pin(member) if target["evidence"] == "selection" else None)
        )
        if pin != expected:
            raise ValueError("Opened decision report differs from its saved evidence")
    elif target.get("kind") == "direct_report":
        from services.research_decision_targets import validate_target_link

        if set(target) - {"kind", "job_id", "config_id", "evaluation_id"}:
            raise ValueError("Invalid direct report opening target")
        decisions._direct_target_owned(
            db,
            owner,
            experiment_id,
            {"job_id": target.get("job_id"), "config_id": target.get("config_id")},
        )
        context = pin["report_context"]
        origin = pin.get("origin")
        source_id = (
            origin["source_job_id"]
            if origin
            else context.get("candidate", {}).get("study_job_id", pin["job_id"])
        )
        validate_target_link(
            db,
            owner,
            experiment_id,
            {"job_id": target["job_id"], "config_id": target.get("config_id")},
            source_id,
            context["config_id"],
            origin["source_result_artifact"] if origin else None,
        )
        if target.get("evaluation_id"):
            if (
                decisions._pin_id(pin) != target["evaluation_id"]
                or context["period"] != "evaluation"
                or not origin
            ):
                raise ValueError("Opened direct later evidence differs from its target")
            member = {
                "source_job_id": origin["source_job_id"],
                "source_result_artifact": origin["source_result_artifact"],
                "config_id": context["config_id"],
                "period": "selection",
            }
        else:
            if context["period"] not in ("selection", "full"):
                raise ValueError("Direct selection opening is not a selection report")
            member = None
    else:
        raise ValueError("Invalid saved opening target")
    return member


def _shortlist_references(db, tables):
    """Retain exact bookmark roots; older backups may omit this additive table."""
    from services import research_library as library
    from services.research_shortlist import (
        MAX_PER_EXPERIMENT,
        MAX_ROWS,
        MAX_SNAPSHOT_BYTES,
        validate_snapshot,
    )

    table = "research_shortlist_candidates"
    if table not in tables:
        return set()
    if db.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() > MAX_ROWS:
        raise ValueError("Saved candidates exceed the maintenance row bound")
    if db.exec_driver_sql(
        f"SELECT 1 FROM {table} GROUP BY experiment_id HAVING count(*) > ? LIMIT 1",
        (MAX_PER_EXPERIMENT,),
    ).first():
        raise ValueError("Saved candidates exceed the experiment bound")
    # Reject oversized SQLite cells before materializing even one snapshot.
    if db.exec_driver_sql(
        f"SELECT 1 FROM {table} WHERE length(CAST(snapshot AS BLOB)) > ? "
        "OR length(name) > 120 OR length(note) > 2000 LIMIT 1",
        (MAX_SNAPSHOT_BYTES,),
    ).first():
        raise ValueError("Saved candidate metadata exceeds its size limit")
    experiments = {
        row[0]: row[1] for row in _rows(db, "research_library_experiments", ("id", "owner"))
    }
    jobs = {
        row[0]: row[1:]
        for row in _rows(db, "research_jobs", ("id", "owner", "result_artifact", "status"))
    }
    kinds = {row[0]: row[1] for row in _rows(db, "research_experiments", ("job_id", "kind"))}
    links = {tuple(row) for row in _rows(db, "research_library_jobs", ("experiment_id", "job_id"))}
    roots, identities = set(), set()
    with db.exec_driver_sql(
        f"SELECT id,owner,experiment_id,source_job_id,source_result_artifact,config_id,period,"
        f"origin_kind,trial_number,proposal_number,is_objective_winner,snapshot,name,note,revision,created_at,updated_at FROM {table}"
    ) as cursor:
        while rows := cursor.fetchmany(64):
            for row in rows:
                (
                    identifier,
                    owner,
                    experiment,
                    source,
                    artifact,
                    config,
                    period,
                    origin,
                    trial,
                    proposal,
                    winner,
                    raw,
                    name,
                    note,
                    revision,
                    created,
                    updated,
                ) = row
                identity = (experiment, source, artifact, config, period)
                if (
                    not isinstance(identifier, str)
                    or not re.fullmatch(r"[a-f0-9]{32}", identifier)
                    or experiments.get(experiment) != owner
                    or jobs.get(source) != (owner, artifact, "completed")
                    or (experiment, source) not in links
                    or not isinstance(config, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", config)
                    or not isinstance(artifact, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", artifact)
                    or period not in ("full", "selection")
                    or origin not in ("study", "backtest")
                    or kinds.get(source)
                    != ("portfolio_optimize" if origin == "study" else "portfolio_backtest")
                    or winner not in (0, 1)
                    or type(revision) is not int
                    or revision < 1
                    or any(
                        not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value <= 0
                        for value in (created, updated)
                    )
                    or updated < created
                    or identity in identities
                    or (
                        origin == "study"
                        and any(
                            type(value) is not int or not 0 <= value < 1000000
                            for value in (trial, proposal)
                        )
                    )
                    or (
                        origin == "backtest"
                        and (trial is not None or proposal is not None or winner)
                    )
                ):
                    raise ValueError("Saved candidate references invalid or foreign evidence")
                library._text(name, 120, "Candidate name")
                library._text(note, 2000, "Candidate note", empty=True)
                try:
                    validate_snapshot(json.loads(raw))
                except (ValueError, TypeError, RecursionError):
                    raise ValueError("Saved candidate has invalid summary metadata") from None
                roots.add(artifact)
                identities.add(identity)
    return roots


def _candidate_references(db):
    """An absent additive table is valid for older backups."""
    rows = _rows(
        db,
        "research_candidate_reports",
        ("owner", "study_job_id", "config_id", "period", "parent_result_artifact", "report_job_id"),
    )
    if not rows:
        return set()
    jobs = {
        row[0]: row[1:] for row in _rows(db, "research_jobs", ("id", "owner", "result_artifact"))
    }
    experiments = {
        row[0]: row[1:]
        for row in _rows(db, "research_experiments", ("job_id", "kind", "parent_job_id"))
    }
    for owner, study, config, period, artifact, child in rows:
        if (
            study not in jobs
            or child not in jobs
            or jobs[study] != (owner, artifact)
            or jobs[child][0] != owner
            or period not in ("full", "selection")
            or not re.fullmatch(r"[a-f0-9]{64}", config)
            or experiments.get(study, (None,))[0] != "portfolio_optimize"
            or experiments.get(child) != ("portfolio_backtest", study)
        ):
            raise ValueError("Candidate report references missing or incompatible study evidence")
    return {row[4] for row in rows}


def _library_references(db, tables):
    """Validate the additive library graph; old stores without it stay supported."""
    expected = {
        "research_library_experiments",
        "research_setup_versions",
        "research_library_jobs",
        "research_library_sources",
        "research_library_requests",
    }
    if not expected.intersection(tables):
        return set()
    if not expected.issubset(tables):
        raise ValueError("Research library metadata tables are incomplete")
    experiments = {
        row[0]: row
        for row in _rows(
            db,
            "research_library_experiments",
            (
                "id",
                "owner",
                "parent_job_id",
                "parent_result_artifact",
                "parent_trial_id",
                "parent_version_id",
            ),
        )
    }
    versions = {
        row[0]: row
        for row in _rows(
            db,
            "research_setup_versions",
            (
                "id",
                "experiment_id",
                "parent_job_id",
                "parent_result_artifact",
                "parent_trial_id",
                "parent_version_id",
            ),
        )
    }
    jobs = {row[0]: row for row in _rows(db, "research_jobs", ("id", "owner", "result_artifact"))}
    sources = {row[0]: row[1] for row in _rows(db, "research_sources", ("id", "owner"))}
    roots = set()

    def container(identifier):
        if identifier not in experiments:
            raise ValueError("Research library reference has a missing experiment")
        return experiments[identifier]

    def job_owner(job_id, owner):
        if job_id not in jobs or jobs[job_id][1] != owner:
            raise ValueError("Research library reference has a missing or foreign job")

    def version_owner(version_id, experiment_id):
        if version_id not in versions or versions[version_id][1] != experiment_id:
            raise ValueError("Research library reference has a missing or foreign setup version")

    def parents(row, experiment_id):
        owner = container(experiment_id)[1]
        parent_job, artifact, trial, version = row[2:]
        if bool(parent_job) != bool(artifact) or (trial and not parent_job):
            raise ValueError("Research library parent evidence is incomplete")
        if parent_job:
            job_owner(parent_job, owner)
            if jobs[parent_job][2] != artifact:
                raise ValueError("Research library parent artifact does not match its result")
            roots.add(artifact)
        if version:
            version_owner(version, experiment_id)

    for identifier, row in experiments.items():
        parents(row, identifier)
    for identifier, row in versions.items():
        container(row[1])
        parents(row, row[1])
        if row[5] == identifier:
            raise ValueError("Research setup version cannot be its own parent")
    links = _rows(db, "research_library_jobs", ("experiment_id", "job_id", "version_id"))
    linked_versions = {(row[0], row[1]): row[2] for row in links}
    for experiment_id, job_id, version_id in links:
        job_owner(job_id, container(experiment_id)[1])
        if version_id:
            version_owner(version_id, experiment_id)
    for experiment_id, source_id, version_id in _rows(
        db, "research_library_sources", ("experiment_id", "source_id", "version_id")
    ):
        if sources.get(source_id) != container(experiment_id)[1]:
            raise ValueError("Research library reference has a missing or foreign source")
        if version_id:
            version_owner(version_id, experiment_id)
    for owner, experiment_id, kind, version_id, job_id in _rows(
        db, "research_library_requests", ("owner", "experiment_id", "kind", "version_id", "job_id")
    ):
        if container(experiment_id)[1] != owner:
            raise ValueError("Research library request belongs to another account")
        if (
            kind not in ("run", "replay", "validation", "from_job")
            or not job_id
            or (kind in ("run", "replay", "validation") and not version_id)
        ):
            raise ValueError("Research library request has incomplete accepted work")
        job_owner(job_id, owner)
        if version_id:
            version_owner(version_id, experiment_id)
        if (experiment_id, job_id) not in linked_versions or (
            kind in ("run", "replay", "validation")
            and linked_versions[(experiment_id, job_id)] != version_id
        ):
            raise ValueError("Research library request is missing its accepted job link")
    for owner, experiment_id, source_id, parent_id in _rows(
        db,
        "research_chartink_imports",
        ("owner", "experiment_id", "source_id", "parent_experiment_id"),
    ):
        if container(experiment_id)[1] != owner or sources.get(source_id) != owner:
            raise ValueError("Chartink import references missing or foreign research")
        if parent_id and container(parent_id)[1] != owner:
            raise ValueError("Chartink import references a foreign parent experiment")
    return roots


def _safe_file(path, root):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Storage contains an unsafe or missing file")
    return path


def _artifact_path(root, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid artifact reference in metadata or evidence")
    path = root / "artifacts" / f"{digest}.json"
    if not path.exists():
        path = path.with_suffix(".json.gz")
    return _safe_file(path, root)


def _input_references(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ("inputs_artifact", "parent_result_artifact", "reference_artifact"):
                    if not isinstance(child, str) or not re.fullmatch(r"[a-f0-9]{64}", child):
                        raise ValueError("Invalid nested inputs_artifact reference")
                    yield child
                elif isinstance(child, (dict, list)):
                    pending.append(child)
        elif isinstance(item, list):
            pending.extend(child for child in item if isinstance(child, (dict, list)))


def _closure(store, roots, refresh=lambda: None):
    pending, found = set(roots), {}
    while pending:
        refresh()
        digest = pending.pop()
        if digest in found:
            continue
        if len(found) >= MAX_FILES:
            raise ValueError("Artifact closure exceeds the 100000-file bound")
        path = _artifact_path(store.root, digest)
        dependencies = artifact_dependencies(store, digest)
        # Check referenced leaf paths before transparent decoding opens them.
        for reference in dependencies:
            _artifact_path(store.root, reference)
        evidence = read_artifact(store, digest)
        found[digest] = path
        # Transparent decoding hides physical chunk references. Retain them as
        # well as logical cross-artifact inputs for backup, restore and pruning.
        pending.update(ref for ref in dependencies if ref not in found)
        pending.update(ref for ref in _input_references(evidence) if ref not in found)
    return found


def _inventory(store, refresh=lambda: None):
    _safe_file(store.root / "research.db", store.root)
    closure = _closure(store, _references(store.engine, store=store), refresh)
    directory = store.root / "artifacts"
    files, unknown = [], []
    if directory.exists():
        if directory.is_symlink():
            raise ValueError("Artifact directory must not be a symlink")
        for path in directory.iterdir():
            if len(files) + len(unknown) >= MAX_FILES:
                raise ValueError("Artifact directory exceeds the maintenance file bound")
            match = ARTIFACT_NAME.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or not match:
                unknown.append(path.name)
                continue
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "digest": match.group(1),
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "referenced": match.group(1) in closure,
                }
            )
    return closure, files, unknown


def inspect_storage(store):
    """Read-only sampled quota receipt; safe to use while the worker runs."""
    closure, files, unknown = _inventory(store)
    quota = int(os.getenv("RESEARCH_QUOTA_MB", "2048")) * 1024**2
    unmanaged_bytes = sum(
        (store.root / "artifacts" / name).stat().st_size
        for name in unknown
        if (store.root / "artifacts" / name).is_file()
        and not (store.root / "artifacts" / name).is_symlink()
    )
    used = sum(file["bytes"] for file in files) + unmanaged_bytes
    database_bytes = sum(p.stat().st_size for p in store.root.glob("research.db*") if p.is_file())
    total_bytes = managed_storage_bytes(store)
    return {
        "sampled_at": time.time(),
        "consistent_snapshot": False,
        "artifact_files": len(files),
        "referenced_artifacts": len(closure),
        "referenced_bytes": sum(f["bytes"] for f in files if f["referenced"]),
        "orphan_files": sum(not f["referenced"] for f in files),
        "orphan_bytes": sum(f["bytes"] for f in files if not f["referenced"]),
        "artifact_bytes": used,
        "unmanaged_bytes": unmanaged_bytes,
        "database_bytes": database_bytes,
        "acquisition_and_other_bytes": max(0, total_bytes - used - database_bytes),
        "total_bytes": total_bytes,
        "quota_bytes": quota,
        "remaining_bytes": max(0, quota - total_bytes),
        "over_quota": total_bytes > quota,
        "unmanaged_entries": unknown,
        "integrity": "referenced canonical artifact hashes verified",
    }


def prune_orphans(store, *, apply=False, min_age_seconds=3600):
    if min_age_seconds < 3600:
        raise ValueError("Orphan grace period must be at least one hour")
    with maintenance(store) as refresh:
        _, files, unknown = _inventory(store, refresh)
        cutoff = time.time() - min_age_seconds
        candidates = [f for f in files if not f["referenced"] and f["mtime"] <= cutoff]
        if apply:
            for file in candidates:
                refresh()
                path = _safe_file(store.root / "artifacts" / file["name"], store.root)
                # Refuse replacing a selected file between inventory and deletion.
                stat = path.stat()
                if stat.st_mtime != file["mtime"] or stat.st_size != file["bytes"]:
                    raise ValueError("Orphan changed during maintenance; rerun inspection")
                path.unlink()
        return {
            "applied": apply,
            "candidate_files": len(candidates),
            "candidate_bytes": sum(f["bytes"] for f in candidates),
            "files": [f["name"] for f in candidates],
            "unmanaged_entries": unknown,
            "grace_seconds": min_age_seconds,
            "deleted_files": len(candidates) if apply else 0,
        }


def _hash(path, refresh=lambda: None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            refresh()
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source, destination, refresh=lambda: None):
    """Bounded copy with lease renewal even on slow filesystems."""
    with source.open("rb") as reader, destination.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1024**2), b""):
            refresh()
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())


@contextmanager
def _destination(destination, forbidden=None):
    target = _maintenance_path(destination)
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("Destination must be a new or empty directory")
    if forbidden and target.resolve().is_relative_to(_maintenance_path(forbidden).resolve()):
        raise ValueError("Destination must be outside the source store")
    if not target.parent.is_dir():
        raise ValueError("Destination parent must already exist")
    with tempfile.TemporaryDirectory(
        prefix=".research-maintenance-", dir=target.parent
    ) as temporary:
        staging = Path(temporary) / "output"
        staging.mkdir()
        yield staging
        if target.exists():
            target.rmdir()  # Empty-only operation; never deletes populated output.
        staging.rename(target)


def _maintenance_path(path):
    """Use the Windows extended namespace for I/O, retaining symlinks for checks.

    Normalize absolute segments before prefixing: extended Windows paths do not
    interpret dot segments. Use the same namespace for containment comparisons
    and the temporary root so its context manager can clean deep files on error.
    Public receipts and manifest-relative names retain their ordinary spelling.
    """
    if os.name != "nt":
        return Path(path).absolute()
    absolute = Path(os.path.abspath(path))
    name = str(absolute)
    if name.startswith("\\\\?\\"):
        return absolute
    if name.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + name[2:])
    return Path("\\\\?\\" + name)


def _maintenance_engine(path):
    # A structured URL keeps the '?' in a Windows extended path out of the URL
    # query string. The shared factory still provides SQLite's required NullPool.
    return create_db_engine(URL.create("sqlite", database=str(path)))


def _database_copy(source_engine, path, refresh):
    target_engine = _maintenance_engine(path)
    source, target = None, None
    try:
        source = source_engine.raw_connection()
        target = target_engine.raw_connection()
        source.driver_connection.backup(
            target.driver_connection, pages=256, progress=lambda *_: refresh()
        )
        target.close()
        target = None
        with target_engine.begin() as db:
            db.execute(text("UPDATE research_worker SET token=NULL, heartbeat=0 WHERE id=1"))
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
        target_engine.dispose()


def backup_store(store, destination):
    with maintenance(store) as refresh, _destination(destination, forbidden=store.root) as staging:
        _database_copy(store.engine, staging / "research.db", refresh)
        engine = _maintenance_engine(staging / "research.db")
        try:
            roots = _references(engine, store=store)
        finally:
            engine.dispose()
        closure = _closure(store, roots, refresh)
        if len(closure) + 1 > MAX_FILES:
            raise ValueError("Backup exceeds the 100000-file maintenance bound")
        if (staging / "research.db").stat().st_size + sum(
            path.stat().st_size for path in closure.values()
        ) > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds the 16 GiB maintenance bound")
        (staging / "artifacts").mkdir()
        for path in closure.values():
            refresh()
            _copy(path, staging / "artifacts" / path.name, refresh)
        _closure(SimpleNamespace(root=staging), roots, refresh)
        paths = [staging / "research.db", *sorted((staging / "artifacts").iterdir())]
        entries = {
            p.relative_to(staging).as_posix(): {
                "bytes": p.stat().st_size,
                "sha256": _hash(p, refresh),
            }
            for p in paths
        }
        if sum(item["bytes"] for item in entries.values()) > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds the 16 GiB maintenance bound")
        manifest = {
            "format": "openalgo-research-backup-v1",
            "created_at": time.time(),
            "integrity": "SHA256 integrity only; not signed or encrypted",
            "files": entries,
            "referenced_artifacts": len(closure),
        }
        (staging / "manifest.json").write_bytes(encoded(manifest))
        refresh(force=True)
    return {
        "destination": str(Path(destination).absolute()),
        "files": len(entries),
        "bytes": sum(item["bytes"] for item in entries.values()),
        "referenced_artifacts": len(closure),
    }


def restore_store(backup_directory, destination):
    source = _maintenance_path(backup_directory).resolve()
    manifest_path = _safe_file(source / "manifest.json", source)
    if manifest_path.stat().st_size > 32 * 1024**2:
        raise ValueError("Backup manifest exceeds 32 MiB")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("format") != "openalgo-research-backup-v1":
        raise ValueError("Unsupported backup manifest")
    entries = manifest.get("files", {})
    if (
        not isinstance(entries, dict)
        or not 1 <= len(entries) <= MAX_FILES
        or "research.db" not in entries
    ):
        raise ValueError("Invalid backup file manifest")
    total = 0
    for name, receipt in entries.items():
        if name != "research.db" and not re.fullmatch(
            r"artifacts/[a-f0-9]{64}\.json(?:\.gz)?", name
        ):
            raise ValueError("Unsafe backup manifest path")
        path = _safe_file(source / name, source)
        size = path.stat().st_size
        total += size
        if total > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds 16 GiB")
        if (
            not isinstance(receipt, dict)
            or size != receipt.get("bytes")
            or _hash(path) != receipt.get("sha256")
        ):
            raise ValueError("Backup file integrity check failed")
    # Never restore unlisted files, symlinks, arbitrary directories or SQLite sidecars.
    with _destination(destination, forbidden=source) as staging:
        for name in entries:
            path = staging / name
            path.parent.mkdir(exist_ok=True)
            _copy(source / name, path)
            if _hash(path) != entries[name]["sha256"]:
                raise ValueError("Backup changed while restoring")
        engine = _maintenance_engine(staging / "research.db")
        try:
            roots = _references(engine, store=SimpleNamespace(root=staging))
            closure = _closure(SimpleNamespace(root=staging), roots)
            listed = {
                name.split("/")[1].split(".")[0]
                for name in entries
                if name.startswith("artifacts/")
            }
            if listed != set(closure):
                raise ValueError("Backup artifact list does not match the referenced closure")
            with engine.begin() as db:
                if db.exec_driver_sql(
                    "SELECT COUNT(*) FROM research_jobs WHERE status IN ('queued','running','pausing','cancelling')"
                ).scalar():
                    raise ValueError("Backup contains unfinished jobs")
                db.execute(text("UPDATE research_worker SET token=NULL, heartbeat=0 WHERE id=1"))
        finally:
            engine.dispose()
    return {
        "destination": str(Path(destination).absolute()),
        "files": len(entries),
        "bytes": total,
        "referenced_artifacts": len(closure),
        "integrity": "hashes and metadata/artifact references verified; signature not available",
    }
