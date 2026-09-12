"""Bounded operational study observations, separate from immutable calculations.

The worker owns writes under its existing lease. Each call releases its session;
no engine, session, proposal history or user result is retained between requests.
"""

import json
import math
import re
import time
import uuid

from sqlalchemy import func, select, update

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchStudyExecution,
    ResearchStudyProposal,
    ResearchWorker,
)
from services.scanner_research_service import encoded, ensure_storage_capacity

VERSION = "research-study-activity-v1"
MAX_PROPOSALS = 1000
MAX_METADATA_ROWS = 100000
MAX_EXECUTIONS_PER_JOB = 1000
MAX_PARAMS_BYTES = 16 * 1024
MAX_PARAMS = 128
MAX_PAGE = 100
EXECUTION_PAGE = 20
PROPOSAL_STATES = (
    "running",
    "evaluated",
    "reused",
    "allocation_rejected",
    "failed",
    "cancelled",
    "interrupted",
)
EXECUTION_STATES = (
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "observation_failed",
)
REASONS = (
    "pause_requested",
    "calculation_failed",
    "cancellation_requested",
    "worker_shutdown",
    "worker_lost",
    "observation_failed",
)


class ObservationFailed(Exception):
    """Capture failed; do not misrepresent it as a failed engine evaluation."""


class ObservationLeaseLost(Exception):
    """The observer cannot write on behalf of an expired/replaced job claim."""


def _integer(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid study activity {label}")
    return value


def _params(value):
    if not isinstance(value, dict) or len(value) > MAX_PARAMS:
        raise ValueError("Invalid study activity parameters")
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 160
            or isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
        ):
            raise ValueError("Invalid study activity parameter")
    payload = encoded(value).decode()
    if len(payload.encode()) > MAX_PARAMS_BYTES:
        raise ValueError("Study activity parameters exceed their size limit")
    return payload


def _fence(db, token, job_id, *, terminal=False):
    # Acquire SQLite's write reservation before reading the current claim. A
    # stale observer can never update after a fenced successor has acquired it.
    db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
    lease, job = db.get(ResearchWorker, 1), db.get(ResearchJob, job_id)
    states = ("running", "pausing", "cancelling") if terminal else ("running", "pausing")
    if (
        lease is None
        or lease.token != token
        or job is None
        or job.worker != token
        or job.status not in states
    ):
        raise ObservationLeaseLost("Study activity worker claim changed")
    return job


def reconcile(db, now, *, token=None):
    """Close abandoned records inside an already reserved worker transaction.

    No finish time is known after process loss. Even a completed proposal may
    lack checkpoint coverage: its observed outcome must not be rewritten here.
    """
    execution_query = select(ResearchStudyExecution.id).where(
        ResearchStudyExecution.state == "running"
    )
    if token is not None:
        execution_query = execution_query.where(ResearchStudyExecution.worker == token)
    db.execute(
        update(ResearchStudyProposal)
        .where(
            ResearchStudyProposal.execution_id.in_(execution_query),
            ResearchStudyProposal.state == "running",
        )
        .values(state="interrupted", observed_at=now, finished_at=None, reason_code="worker_lost")
    )
    query = update(ResearchStudyExecution).where(ResearchStudyExecution.state == "running")
    if token is not None:
        query = query.where(ResearchStudyExecution.worker == token)
    db.execute(
        query.values(
            state="interrupted", observed_at=now, finished_at=None, reason_code="worker_lost"
        )
    )


class StudyObserver:
    """One tiny observer per claimed optimization; execution starts after replay."""

    def __init__(self, store, token, job_id):
        self.store, self.token, self.job_id = store, token, job_id
        self.execution_id = None

    def __call__(self, event):
        try:
            self._record(event)
        except ObservationLeaseLost:
            raise
        except Exception as error:
            raise ObservationFailed("Study activity could not be recorded") from error

    def _record(self, event):
        if not isinstance(event, dict):
            raise ValueError("Invalid study activity event")
        kind = event.get("kind")
        if kind not in ("search_started", "proposal_started", "proposal_finished"):
            raise ValueError("Invalid study activity event")
        # Bound one pass's possible metadata before search, rather than walking
        # the artifact store on every proposal. Worker checkpoint/progress retains
        # its ordinary quota checks as the actual database grows.
        if kind == "search_started":
            budget = _integer(event.get("proposal_budget"), 1, MAX_PROPOSALS, "budget")
            replayed = _integer(event.get("replayed"), 0, budget, "replay count")
            ensure_storage_capacity(
                self.store, (budget - replayed) * (MAX_PARAMS_BYTES + 4096) + 8192
            )
        now = time.time()
        with self.store.sessions.begin() as db:
            job = _fence(db, self.token, self.job_id, terminal=kind == "proposal_finished")
            experiment = db.get(ResearchExperiment, self.job_id)
            if experiment is None or experiment.kind != "portfolio_optimize":
                raise ValueError("Study activity requires a portfolio optimization")
            if kind == "search_started":
                budget = _integer(event.get("proposal_budget"), 1, MAX_PROPOSALS, "budget")
                replayed = _integer(event.get("replayed"), 0, budget, "replay count")
                if self.execution_id is not None:
                    existing = db.get(ResearchStudyExecution, self.execution_id)
                    if existing is None or (existing.proposal_budget, existing.replayed) != (
                        budget,
                        replayed,
                    ):
                        raise ValueError("Study activity execution changed")
                    return
                count = db.scalar(select(func.count()).select_from(ResearchStudyExecution))
                job_count = db.scalar(
                    select(func.count())
                    .select_from(ResearchStudyExecution)
                    .where(ResearchStudyExecution.job_id == self.job_id)
                )
                if count >= MAX_METADATA_ROWS or job_count >= MAX_EXECUTIONS_PER_JOB:
                    raise ValueError("Study activity execution capacity reached")
                identifier = uuid.uuid4().hex
                db.add(
                    ResearchStudyExecution(
                        id=identifier,
                        job_id=self.job_id,
                        owner=job.owner,
                        worker=self.token,
                        version=VERSION,
                        state="running",
                        proposal_budget=budget,
                        replayed=replayed,
                        started_at=now,
                        observed_at=now,
                    )
                )
                # Only publish the in-process handle after the transaction commits.
            else:
                execution = (
                    db.get(ResearchStudyExecution, self.execution_id) if self.execution_id else None
                )
                if (
                    execution is None
                    or execution.state != "running"
                    or execution.worker != self.token
                ):
                    raise ValueError("Study activity has no active execution")
                number = _integer(
                    event.get("number"),
                    execution.replayed,
                    execution.proposal_budget - 1,
                    "proposal number",
                )
                reused = event.get("reused")
                if not isinstance(reused, bool):
                    raise ValueError("Invalid study activity reuse flag")
                row = db.scalar(
                    select(ResearchStudyProposal).where(
                        ResearchStudyProposal.execution_id == execution.id,
                        ResearchStudyProposal.number == number,
                    )
                )
                if kind == "proposal_started":
                    params = _params(event.get("params"))
                    config_id = event.get("config_id")
                    if not isinstance(config_id, str) or not re.fullmatch(
                        r"[a-f0-9]{64}", config_id
                    ):
                        raise ValueError("Invalid study activity configuration")
                    if row is not None:
                        if (row.config_id, row.params, row.reused, row.state) != (
                            config_id,
                            params,
                            reused,
                            "running",
                        ):
                            raise ValueError("Study activity proposal identity changed")
                        return
                    if (
                        db.scalar(
                            select(ResearchStudyProposal.id)
                            .where(
                                ResearchStudyProposal.execution_id == execution.id,
                                ResearchStudyProposal.state == "running",
                            )
                            .limit(1)
                        )
                        is not None
                    ):
                        raise ValueError("Study activity already has an active proposal")
                    count = db.scalar(select(func.count()).select_from(ResearchStudyProposal))
                    if count >= MAX_METADATA_ROWS:
                        raise ValueError("Study activity proposal capacity reached")
                    db.add(
                        ResearchStudyProposal(
                            execution_id=execution.id,
                            job_id=self.job_id,
                            number=number,
                            config_id=config_id,
                            params=params,
                            state="running",
                            reused=reused,
                            started_at=now,
                            observed_at=now,
                            checkpointed=False,
                        )
                    )
                else:
                    state, value = event.get("state"), event.get("value")
                    if (
                        state not in ("complete", "pruned")
                        or (state == "pruned" and value is not None)
                        or (
                            state == "complete"
                            and (
                                isinstance(value, bool)
                                or not isinstance(value, (int, float))
                                or not math.isfinite(value)
                            )
                        )
                        or row is None
                        or row.reused != reused
                    ):
                        raise ValueError("Invalid study activity outcome")
                    outcome = (
                        "allocation_rejected"
                        if state == "pruned"
                        else ("reused" if reused else "evaluated")
                    )
                    if row.state != "running":
                        if (row.state, row.value) != (outcome, value):
                            raise ValueError("Study activity outcome changed")
                        return
                    row.state, row.value = outcome, value
                    row.finished_at = row.observed_at = now
                execution.observed_at = now
        if kind == "search_started":
            self.execution_id = identifier

    def checkpoint(self, db, state):
        """Mark matching records only in the scientific publication transaction."""
        if not self.execution_id or not isinstance(state, dict):
            return
        calculation = state.get("calculation")
        if not isinstance(calculation, dict):
            return
        if calculation.get("version") == "automatic-trade-management-v1":
            calculation = calculation.get("search_checkpoint") or (
                calculation.get("search_result") or {}
            ).get("experiment")
            if calculation is None:
                return
        _fence(db, self.token, self.job_id)
        trials = calculation.get("trials")
        try:
            if not isinstance(trials, list) or len(trials) > MAX_PROPOSALS:
                raise ValueError("Invalid study checkpoint observation coverage")
            by_number = {item["number"]: item for item in trials}
            rows = db.scalars(
                select(ResearchStudyProposal)
                .where(
                    ResearchStudyProposal.execution_id == self.execution_id,
                    ResearchStudyProposal.state.in_(("evaluated", "reused", "allocation_rejected")),
                    ResearchStudyProposal.checkpointed.is_(False),
                )
                .limit(MAX_PROPOSALS + 1)
            ).all()
            if len(rows) > MAX_PROPOSALS:
                raise ValueError("Study activity exceeds its bounded proposal budget")
            for row in rows:
                actual = by_number.get(row.number)
                expected = {
                    "number": row.number,
                    "config_id": row.config_id,
                    "params": json.loads(row.params),
                    "state": "pruned" if row.state == "allocation_rejected" else "complete",
                    "value": row.value,
                    "reused": row.reused,
                }
                if actual is None or any(
                    actual.get(key) != value for key, value in expected.items()
                ):
                    raise ValueError("Study activity does not match the published checkpoint")
                row.checkpointed = True
        except Exception as error:
            raise ObservationFailed(
                "Study activity checkpoint coverage could not be recorded"
            ) from error

    def finish(self, db, state, reason_code=None, *, known_finish=True):
        """Finalize inside the same fenced transaction as the owning job state."""
        if self.execution_id is None:
            return
        _fence(db, self.token, self.job_id, terminal=True)
        execution = db.get(ResearchStudyExecution, self.execution_id)
        if execution is None or execution.state != "running" or execution.worker != self.token:
            return
        now = time.time()
        execution.state, execution.reason_code = state, reason_code
        execution.observed_at, execution.finished_at = now, now if known_finish else None
        # A later publication failure cannot turn a completed native outcome into
        # a failed trial. Close only the proposal we actually observed open.
        proposal_state = "interrupted" if state == "observation_failed" else state
        if proposal_state in ("completed", "paused"):
            proposal_state = "interrupted"
        db.execute(
            update(ResearchStudyProposal)
            .where(
                ResearchStudyProposal.execution_id == self.execution_id,
                ResearchStudyProposal.state == "running",
            )
            .values(
                state=proposal_state,
                reason_code=reason_code,
                observed_at=now,
                finished_at=now if known_finish and state != "observation_failed" else None,
            )
        )


def _query_int(value, default, maximum, label):
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,18}", str(value)):
        raise ValueError(f"Invalid study activity {label}")
    return _integer(int(value), 1, maximum, label)


def _execution(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "started_at",
            "finished_at",
            "observed_at",
            "state",
            "reason_code",
            "proposal_budget",
            "replayed",
        )
    }


def read(store, owner, job_id, *, limit=None, before=None, execution=None):
    limit = _query_int(limit, 25, MAX_PAGE, "page size")
    before = _query_int(before, None, 2**63 - 1, "cursor")
    if execution is not None and (
        not isinstance(execution, str) or not re.fullmatch(r"[a-f0-9]{32}", execution)
    ):
        raise ValueError("Invalid study activity execution")
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        if job is None or job.owner != owner:
            raise LookupError("Research run not found")
        experiment = db.get(ResearchExperiment, job_id)
        if experiment is None or experiment.kind != "portfolio_optimize":
            raise ValueError("Study activity is available for optimization runs")
        if execution:
            selected = db.get(ResearchStudyExecution, execution)
            if selected is None or selected.job_id != job_id or selected.owner != owner:
                raise LookupError("Study execution not found")
        scope = [ResearchStudyProposal.job_id == job_id]
        if execution:
            scope.append(ResearchStudyProposal.execution_id == execution)
        counts = dict.fromkeys(PROPOSAL_STATES, 0)
        for state, count in db.execute(
            select(ResearchStudyProposal.state, func.count())
            .where(*scope)
            .group_by(ResearchStudyProposal.state)
        ):
            if state in counts:
                counts[state] = count
        counts["recorded"] = sum(counts.values())
        page_scope = scope + ([ResearchStudyProposal.id < before] if before else [])
        rows = db.scalars(
            select(ResearchStudyProposal)
            .where(*page_scope)
            .order_by(ResearchStudyProposal.id.desc())
            .limit(limit + 1)
        ).all()
        more = len(rows) > limit
        rows = rows[:limit]
        execution_scope = [
            ResearchStudyExecution.job_id == job_id,
            ResearchStudyExecution.owner == owner,
        ]
        executions = db.scalars(
            select(ResearchStudyExecution)
            .where(*execution_scope)
            .order_by(ResearchStudyExecution.started_at.desc(), ResearchStudyExecution.id.desc())
            .limit(EXECUTION_PAGE + 1)
        ).all()
        truncated = len(executions) > EXECUTION_PAGE
        executions = executions[:EXECUTION_PAGE]
        missing = {row.execution_id for row in rows} - {row.id for row in executions}
        if execution and execution not in {row.id for row in executions}:
            missing.add(execution)
        if missing:
            executions += list(
                db.scalars(
                    select(ResearchStudyExecution)
                    .where(
                        ResearchStudyExecution.id.in_(missing),
                        ResearchStudyExecution.job_id == job_id,
                        ResearchStudyExecution.owner == owner,
                    )
                    .limit(MAX_PAGE + 1)
                )
            )
        return {
            "version": VERSION,
            "job_id": job_id,
            "job_status": job.status,
            "available": bool(executions),
            "reason": None if executions else "not_recorded",
            "executions": [_execution(row) for row in executions],
            "executions_truncated": truncated,
            "counts": counts,
            "rows": [
                {
                    **{
                        key: getattr(row, key)
                        for key in (
                            "id",
                            "execution_id",
                            "number",
                            "config_id",
                            "state",
                            "value",
                            "reused",
                            "started_at",
                            "finished_at",
                            "observed_at",
                            "reason_code",
                            "checkpointed",
                        )
                    },
                    "params": json.loads(row.params),
                }
                for row in rows
            ],
            "next_before": rows[-1].id if more else None,
        }
