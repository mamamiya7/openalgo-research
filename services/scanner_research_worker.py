"""Run with uv run python -m services.scanner_research_worker.

One external process performs calculations; Flask only submits metadata.
A fenced lease prevents duplicate workers and makes interruption explicit.
"""

import argparse
import hashlib
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import case, select, update
from sqlalchemy.orm import aliased

from database.research_db import (
    ResearchAttempt,
    ResearchExperiment,
    ResearchHistory,
    ResearchJob,
    ResearchLibraryJob,
    ResearchPreparation,
    ResearchSource,
    ResearchSourceReceipt,
    ResearchStore,
    ResearchWorker,
)
from research.checkpoint_bytes import _OwnedCheckpointSink
from services import research_study_activity as study_activity
from services.research_sources import AcquisitionBatchPending
from services.scanner_research_service import (
    _save_owned_artifact,
    artifact_publication,
    compact_checkpoint_publication,
    encoded,
    ensure_storage_capacity,
    get_job,
    prepare_source,
    read_artifact,
    save_artifact,
    source_for,
)
from utils.logging import get_logger

logger = get_logger(__name__)
_OWNED_SAVE_ARTIFACT = save_artifact
LEASE_SECONDS = 120
NETWORK_HEARTBEAT_SECONDS = 1
STORAGE_CHECK_INTERVAL_SECONDS = 10
WORKING = ("running", "pausing")


class Cancelled(Exception):
    pass


class Interrupted(Exception):
    pass


class Paused(Exception):
    """The job and its last complete calculation checkpoint were saved together."""


def acquire(store, token):
    now = time.time()
    with store.sessions.begin() as db:
        changed = db.execute(
            update(ResearchWorker)
            .where(
                ResearchWorker.id == 1,
                (ResearchWorker.token.is_(None)) | (ResearchWorker.heartbeat < now - LEASE_SECONDS),
            )
            .values(token=token, heartbeat=now)
        ).rowcount
        if not changed:
            raise RuntimeError("A research worker already holds the active lease")
        study_activity.reconcile(db, now)
        db.execute(
            update(ResearchJob)
            .where(ResearchJob.status.in_((*WORKING, "cancelling")))
            .values(
                status="interrupted",
                error="Worker stopped before publication; resume a verified checkpoint when available.",
                updated_at=now,
            )
        )


def heartbeat(store, token):
    with store.sessions.begin() as db:
        changed = db.execute(
            update(ResearchWorker)
            .where(ResearchWorker.id == 1, ResearchWorker.token == token)
            .values(heartbeat=time.time())
        ).rowcount
        if not changed:
            raise Cancelled("Worker lease lost")


@contextmanager
def network_lease(store, token, job_id, stop_requested=None):
    """One joined helper in the external worker keeps blocking I/O fenced."""
    finished = threading.Event()
    reasons = []
    owner_thread = threading.get_ident()
    read_connection = None
    read_fence = None
    live_fence = (
        select(ResearchWorker.token, ResearchJob.worker, ResearchJob.status)
        .join(ResearchJob, ResearchJob.id == job_id)
        .where(ResearchWorker.id == 1)
    )

    def check(*, refresh_heartbeat=False):
        if stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        if not refresh_heartbeat:
            # Keep every callback live, reading both fences in one statement.
            # ORM loading two separate rows adds work and can mix observations
            # from opposite sides of a concurrent cancellation/worker takeover.
            if threading.get_ident() == owner_thread:
                fence = read_fence()
            else:
                # Native acquisition helpers may call this callback too. Their
                # connections stay independent of the owner's cursor/thread.
                with store.sessions() as db:
                    fence = db.execute(live_fence).one_or_none()
            if fence is None or fence[0] != token or fence[1] != token or fence[2] not in WORKING:
                raise Cancelled("Cancellation requested or worker lease lost")
            return
        with store.sessions.begin() as db:
            lease, job = db.get(ResearchWorker, 1), db.get(ResearchJob, job_id)
            if lease.token != token or job.status not in WORKING or job.worker != token:
                raise Cancelled("Cancellation requested or worker lease lost")
            # Engine callbacks can check cancellation several times per bar.
            # They must keep reading the live fence, but committing a heartbeat
            # for every check turns a calculation into thousands of disk flushes
            # on Windows. The joined monitor owns the periodic heartbeat.
            if refresh_heartbeat:
                lease.heartbeat = job.updated_at = time.time()

    def monitor():
        while not finished.wait(NETWORK_HEARTBEAT_SECONDS):
            try:
                check(refresh_heartbeat=True)
            except Exception as error:
                reasons.append(error)
                return

    def cancelled():
        if reasons:
            raise reasons[0]
        check()
        return False

    helper = threading.Thread(target=monitor, name="research-network-lease", daemon=True)
    try:
        # Keep one read-only callback connection for this owner thread. SQLite
        # autocommit and promptly consumed/closed results leave no read snapshot
        # between callbacks, so external commits are visible on the next check.
        read_connection = store.engine.connect()
        read_connection.execution_options(isolation_level="AUTOCOMMIT")
        from services.research_worker_sessions import fence_reader

        read_fence = fence_reader(read_connection, live_fence)
        helper.start()
        yield cancelled
        cancelled()
    finally:
        finished.set()
        try:
            if helper.ident is not None:
                helper.join()
        finally:
            if read_connection is not None:
                read_connection.close()


def run_one(store, token, stop_requested=None):
    from services.research_worker_sessions import scoped_metadata_sessions

    with scoped_metadata_sessions(store):
        return _run_one(store, token, stop_requested)


def _run_one(store, token, stop_requested=None):
    from research.connectors.registry import policy_for_request
    from research.connectors.registry import run as run_connector
    from research.engine import POLICY_VERSION, evaluate, policy_for_snapshot
    from research.experiments import research_windows, run_research, run_search, run_sensitivity

    heartbeat(store, token)
    with store.sessions.begin() as db:
        claimed = db.execute(
            update(ResearchWorker)
            .where(ResearchWorker.id == 1, ResearchWorker.token == token)
            .values(heartbeat=time.time())
        ).rowcount
        if not claimed:
            raise Cancelled("Worker lease lost before claim")
        preparation_job = aliased(ResearchJob)
        preparation_experiment = aliased(ResearchExperiment)
        waiting_for_preparation = (
            select(ResearchPreparation.experiment_id)
            .join(
                ResearchLibraryJob,
                ResearchLibraryJob.experiment_id == ResearchPreparation.experiment_id,
            )
            .join(preparation_job, preparation_job.id == ResearchPreparation.job_id)
            .join(preparation_experiment, preparation_experiment.job_id == preparation_job.id)
            .where(
                ResearchLibraryJob.job_id == ResearchJob.id,
                ResearchPreparation.owner == ResearchJob.owner,
                preparation_job.owner == ResearchJob.owner,
                preparation_job.source_id == ResearchJob.source_id,
                preparation_job.status.in_(("queued", "running")),
                preparation_experiment.kind == "portfolio_prepare",
                preparation_experiment.specification == ResearchExperiment.specification,
            )
            .correlate(ResearchJob, ResearchExperiment)
            .exists()
        )
        job = db.scalar(
            select(ResearchJob)
            .outerjoin(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
            .where(ResearchJob.status == "queued", ~waiting_for_preparation)
            .order_by(
                case((ResearchExperiment.kind == "portfolio_analysis", 1), else_=0),
                ResearchJob.updated_at,
                ResearchJob.created_at,
                ResearchJob.id,
            )
            .limit(1)
        )
        if job is None:
            return False
        job.status, job.worker, job.updated_at = "running", token, time.time()
        experiment = db.get(ResearchExperiment, job.id)
    kind = experiment.kind if experiment else "backtest"
    spec = json.loads(experiment.specification) if experiment else {}
    saved = None
    policy = POLICY_VERSION
    last_update = 0.0
    last_storage_check = float("-inf")
    activity_state = json.loads(experiment.counts).get("activity") if experiment else None
    last_activity_update = float("-inf")
    study_observer = (
        study_activity.StudyObserver(store, token, job.id) if kind == "portfolio_optimize" else None
    )

    def observe(event):
        # Preserve a native outcome already observed before a simultaneous stop.
        # The following progress/checkpoint boundary honors cancellation before
        # more work, and the observer still rejects a replaced worker token.
        if event.get("kind") != "proposal_finished" and stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        try:
            study_observer(event)
        except study_activity.ObservationLeaseLost as error:
            raise Cancelled("Cancellation requested or worker lease lost") from error

    def finish_job(state, reason_code, error=None, *, observation_failed=False, failure=None):
        # The same reservation fences both operational observations and job state.
        # A stale worker does nothing; the successor owns interruption reconciliation.
        with store.sessions.begin() as db:
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            lease, current = db.get(ResearchWorker, 1), db.get(ResearchJob, job.id)
            if (
                lease.token != token
                or current.worker != token
                or current.status not in (*WORKING, "cancelling")
            ):
                return
            if study_observer:
                study_observer.finish(
                    db,
                    "observation_failed" if observation_failed else state,
                    reason_code,
                    known_finish=not observation_failed,
                )
            current.status, current.updated_at = state, time.time()
            if error is not None:
                current.error = error
            if failure is not None:
                receipt = db.get(ResearchExperiment, job.id)
                if receipt:
                    counts = json.loads(receipt.counts)
                    counts["failure"] = failure
                    receipt.counts = encoded(counts).decode()

    def activity(event, force=False):
        nonlocal activity_state, last_activity_update
        from services.research_activity import merge_activity

        previous = activity_state or {}
        activity_state = merge_activity(previous, event, time.time())
        changed = (
            previous.get("stage") != activity_state.get("stage")
            or previous.get("trials", {}).get("completed")
            != activity_state.get("trials", {}).get("completed")
            or previous.get("trials", {}).get("active_trial")
            != activity_state.get("trials", {}).get("active_trial")
            or previous.get("prices", {}).get("current_symbol")
            != activity_state.get("prices", {}).get("current_symbol")
            or previous.get("prices", {}).get("cache_complete")
            != activity_state.get("prices", {}).get("cache_complete")
        )
        now = time.monotonic()
        if not force and not changed and now - last_activity_update < 1:
            return
        if stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        with store.sessions.begin() as db:
            lease = db.get(ResearchWorker, 1)
            current = db.get(ResearchJob, job.id)
            if lease.token != token or current.status not in WORKING or current.worker != token:
                raise Cancelled("Cancellation requested or worker lease lost")
            receipt = db.get(ResearchExperiment, job.id)
            counts = json.loads(receipt.counts)
            counts["activity"] = activity_state
            receipt.counts = encoded(counts).decode()
            lease.heartbeat = current.updated_at = time.time()
        last_activity_update = now

    def progress(completed, total, force=False, *, check_storage=True):
        nonlocal last_update, last_storage_check
        # Session-level cancellation remains bounded without a SQLite transaction
        # for every session of every grid configuration.
        now = time.monotonic()
        if now - last_update < 0.25 and completed < total and not force:
            return
        if stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        last_update = now
        if (
            check_storage
            and kind
            in (
                "acquire",
                "evidence_update",
                "portfolio_backtest",
                "portfolio_optimize",
                "portfolio_prepare",
            )
            and (
                kind not in ("portfolio_backtest", "portfolio_optimize", "portfolio_prepare")
                or now - last_storage_check >= STORAGE_CHECK_INTERVAL_SECONDS
            )
        ):
            # An account tick writes no price/evidence files. Rewalking the
            # complete research archive four times a second can dominate the
            # simulation on Windows. Keep periodic admission here; checkpoints
            # and result/artifact publication still enforce capacity on every
            # write, independently of this job-local progress interval.
            ensure_storage_capacity(store)
            last_storage_check = now
        with store.sessions.begin() as db:
            lease = db.get(ResearchWorker, 1)
            current = db.get(ResearchJob, job.id)
            if lease.token != token or current.status not in WORKING or current.worker != token:
                raise Cancelled("Cancellation requested or worker lease lost")
            lease.heartbeat = time.time()
            current.progress = min(99, int(100 * completed / max(1, total)))
            current.updated_at = time.time()

    def acknowledge_pause(db, current):
        """Called only at a completed-calculation boundary under the claim fence."""
        if current.status != "pausing":
            return False
        if study_observer:
            study_observer.finish(db, "paused", "pause_requested")
        current.status, current.worker, current.error = "paused", None, None
        current.updated_at = time.time()
        return True

    def calculation_boundary():
        # No engine call has started at this boundary. Its existing checkpoint
        # already contains all admitted work, including an empty frozen seed.
        if stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        # Replaying admitted proposals changes no state. Read both live fences
        # together, without reserving SQLite's sole writer on every proposal.
        with store.sessions() as db:
            fence = db.execute(
                select(ResearchWorker.token, ResearchJob.worker, ResearchJob.status)
                .join(ResearchJob, ResearchJob.id == job.id)
                .where(ResearchWorker.id == 1)
            ).one_or_none()
            if fence is None or fence[0] != token or fence[1] != token or fence[2] not in WORKING:
                raise Cancelled("Cancellation requested or worker lease lost")
            if fence[2] == "running":
                return
            if getattr(calculation_boundary, "checkpoint_pause", False):
                # The controller first flushes all validated staged accounts;
                # checkpoint publication then acknowledges pause atomically.
                return True
        # Close the read transaction first. A pause still reserves the writer
        # and rereads the fences, so cancellation or a successor always wins
        # before this worker can acknowledge the pause.
        with store.sessions.begin() as db:
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            if stop_requested and stop_requested():
                raise Interrupted("Worker shutdown requested")
            lease, current = db.get(ResearchWorker, 1), db.get(ResearchJob, job.id)
            if lease.token != token or current.worker != token or current.status not in WORKING:
                raise Cancelled("Cancellation requested or worker lease lost")
            paused = acknowledge_pause(db, current)
        if paused:
            raise Paused()

    def checkpoint(state, counts, *, _publish=None, _owned=None):
        nonlocal last_update
        # Check the same live fences before artifact I/O, then publish progress
        # with the checkpoint below. A separate progress-only commit repeated
        # the same job/lease write before every checkpoint.
        if stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        with store.sessions() as db:
            fence = db.execute(
                select(ResearchWorker.token, ResearchJob.worker, ResearchJob.status)
                .join(ResearchJob, ResearchJob.id == job.id)
                .where(ResearchWorker.id == 1)
            ).one_or_none()
            if fence is None or fence[0] != token or fence[1] != token or fence[2] not in WORKING:
                raise Cancelled("Cancellation requested before checkpoint artifacts")
        # Small/medium checkpoints keep the same logical digest and complete
        # chunk admission, but can use one bounded physical file.
        with artifact_publication(store):
            if _publish is not None:
                _publish()
            with compact_checkpoint_publication():
                wrapped = (
                    _owned.wrap(
                        "state", {"identity": experiment.identity, "policy_version": policy}
                    )
                    if _owned is not None and save_artifact is _OWNED_SAVE_ARTIFACT
                    else None
                )
                if wrapped is not None:
                    try:
                        artifact = _save_owned_artifact(store, wrapped)
                    finally:
                        wrapped.close()
                else:
                    if _owned is not None:
                        _owned.close()
                    artifact = save_artifact(
                        store,
                        {"identity": experiment.identity, "policy_version": policy, "state": state},
                    )
        with store.sessions.begin() as db:
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            current = db.get(ResearchJob, job.id)
            lease = db.get(ResearchWorker, 1)
            if current.status not in WORKING or lease.token != token or current.worker != token:
                raise Cancelled("Cancellation requested before checkpoint publication")
            if study_observer:
                study_observer.checkpoint(db, state)
            receipt = db.get(ResearchExperiment, job.id)
            display_counts = {**counts, **({"activity": activity_state} if activity_state else {})}
            from services.research_attempt_budget import merge_counts

            display_counts = merge_counts(
                receipt.counts,
                display_counts,
                checkpoint_state=state.get("calculation")
                if isinstance(state.get("calculation"), dict)
                else None,
            )
            receipt.checkpoint, receipt.counts = artifact, encoded(display_counts).decode()
            current.progress = min(99, int(100 * counts["completed"] / max(1, counts["total"])))
            lease.heartbeat = current.updated_at = time.time()
            last_update = time.monotonic()
            paused = (
                acknowledge_pause(db, current)
                if kind == "portfolio_optimize" and state.get("phase") == "calculation"
                else False
            )
        # Unwind only after the checkpoint, coverage and terminal execution commit.
        if paused:
            raise Paused()

    def publish_with_artifacts(state, counts, publish):
        return checkpoint(state, counts, _publish=publish)

    checkpoint.publish_with_artifacts = publish_with_artifacts
    attempt_reservation = None

    def reserve_attempt(expected, simulations, reserved_final, limit):
        nonlocal attempt_reservation
        from services.research_attempt_budget import AttemptLeaseLost, attempt_reserver

        if attempt_reservation is None:
            attempt_reservation = attempt_reserver(
                store,
                job.owner,
                job.id,
                token,
                experiment.identity,
                policy,
                checkpoint_attempts=expected,
                checkpoint_simulations=simulations,
                limit=limit,
            )
        try:
            return attempt_reservation(expected, simulations, reserved_final, limit)
        except AttemptLeaseLost as error:
            raise Cancelled(str(error)) from error

    checkpoint.reserve_attempt = reserve_attempt

    def checkpoint_owned(owned, counts, publish):
        return checkpoint(owned.value, counts, _publish=publish, _owned=owned)

    checkpoint = _OwnedCheckpointSink(checkpoint, checkpoint_owned)

    history_record = None
    parent_result_artifact = None

    try:
        evidence = source_for(store, job.owner, job.source_id)
        policy = policy_for_request(evidence["snapshot"], spec)
        config = json.loads(job.config)
        with store.sessions() as db:
            attempt = db.get(ResearchAttempt, job.id)
        if experiment:
            expected_identity = hashlib.sha256(
                encoded(
                    {
                        "source_id": job.source_id,
                        "config": config,
                        "kind": kind,
                        "specification": spec,
                        "policy_version": policy,
                        **({"previous_attempt_id": attempt.previous_job_id} if attempt else {}),
                    }
                )
            ).hexdigest()
            if expected_identity != experiment.identity:
                raise ValueError(
                    "The execution policy changed after this job was queued. Start a new attempt to use the corrected policy; older completed evidence remains unchanged."
                )
        if experiment and experiment.checkpoint:
            receipt = read_artifact(store, experiment.checkpoint)
            if receipt["identity"] != experiment.identity or receipt["policy_version"] != policy:
                raise ValueError(
                    "Checkpoint inputs or execution policy changed; start a new experiment"
                )
            saved = receipt["state"]
        elif kind in ("acquire", "portfolio_analysis") and attempt:
            with store.sessions() as db:
                prior = db.get(ResearchExperiment, attempt.previous_job_id)
                prior_job = db.get(ResearchJob, attempt.previous_job_id)
            prior_spec = json.loads(prior.specification) if prior else {}
            same_benchmark = (
                kind == "portfolio_analysis"
                and prior is not None
                and prior.kind == kind
                and (spec.get("benchmark") is not None or spec.get("market_conditions") is True)
                and prior_spec.get("benchmark") == spec.get("benchmark")
                and prior_spec.get("market_conditions") == spec.get("market_conditions")
                and prior_spec.get("market_context") == spec.get("market_context")
                and prior_spec.get("parent_result_artifact") == spec["parent_result_artifact"]
            )
            if (
                prior
                and prior.checkpoint
                and prior_job.source_id == job.source_id
                and prior_job.owner == job.owner
                and (kind == "acquire" or same_benchmark)
            ):
                receipt = read_artifact(store, prior.checkpoint)
                if receipt["identity"] == prior.identity and receipt["policy_version"] == policy:
                    saved = receipt["state"]
        if kind == "portfolio_analysis":
            from services.research_analysis import run as run_analysis

            parent_result_artifact = spec["parent_result_artifact"]
            with network_lease(store, token, job.id, stop_requested) as cancelled:
                cancelled()
                result = run_analysis(
                    store,
                    job.owner,
                    job.source_id,
                    spec,
                    progress=progress,
                    saved=saved,
                    checkpoint=checkpoint,
                    cancelled=cancelled,
                )
                cancelled()
        elif kind in ("portfolio_backtest", "portfolio_optimize", "portfolio_prepare"):
            from services.research_activity import initial_activity
            from services.research_portfolio import run as run_portfolio

            if not activity_state:
                activity_state = initial_activity(
                    evidence["receipt"], spec["portfolio"], time.time()
                )
            activity({}, force=True)
            with network_lease(store, token, job.id, stop_requested) as cancelled:
                if kind == "portfolio_prepare":
                    from services.research_portfolio import prepare_inputs
                    from services.research_preparation import completed_receipt

                    initial_evidence = evidence
                    evidence, _ = prepare_inputs(
                        store,
                        job.owner,
                        evidence,
                        spec,
                        saved=saved,
                        checkpoint=checkpoint,
                        progress=progress,
                        cancelled=cancelled,
                        activity=activity,
                    )
                    preparation_receipt = completed_receipt(store, initial_evidence, evidence, spec)
                    cancelled()
                    result = {"prepared": True}
                else:
                    if saved is None:
                        from services.research_preparation import reuse_for_run

                        saved = reuse_for_run(store, job.owner, job.id, evidence, spec)
                    result, evidence = run_portfolio(
                        store,
                        job.owner,
                        evidence,
                        spec,
                        defer_study_analysis=True,
                        job_id=job.id,
                        saved=saved,
                        checkpoint=checkpoint,
                        progress=progress,
                        cancelled=cancelled,
                        activity=activity,
                        **(
                            {"observe": observe, "boundary": calculation_boundary}
                            if study_observer
                            else {}
                        ),
                    )
        elif kind in ("prepare", "acquire"):
            if kind == "acquire":
                from services.research_sources import acquire_source

                with network_lease(store, token, job.id, stop_requested) as cancelled:
                    evidence, prepared_source = acquire_source(
                        store,
                        job.owner,
                        evidence,
                        saved=saved,
                        checkpoint=checkpoint,
                        progress=progress,
                        cancelled=cancelled,
                    )
            else:
                evidence, prepared_source = prepare_source(
                    store, job.owner, evidence, progress, publish=False
                )
            job.source_id = prepared_source["id"]
            result = {
                "prepared_source": prepared_source,
                "config": config,
                "policy_version": policy,
                "summary": {"signal_count": len(evidence["signals"])},
                "limits": [],
                "equity_curve": [],
                "ledger": [],
            }
        elif kind == "evidence_update":
            from services.research_sources import update_evidence

            with network_lease(store, token, job.id, stop_requested) as cancelled:
                updated = update_evidence(
                    spec["end_date"], progress=progress, cancelled=cancelled, store=store
                )
            result = {
                "evidence_update": updated,
                "config": config,
                "policy_version": policy,
                "summary": {},
                "limits": [],
                "equity_curve": [],
                "ledger": [],
            }
        elif spec.get("execution"):
            # Optional imports and native compilation can outlast the lease.
            # Reuse the joined lease helper; publication remains fenced even if
            # cancellation arrives inside an external package's blocking call.
            with network_lease(store, token, job.id, stop_requested) as cancelled:
                last_connector_check = 0.0

                def connector_progress(done, total):
                    nonlocal last_connector_check
                    now = time.monotonic()
                    if now - last_connector_check >= 0.25 or done >= total:
                        cancelled()
                        last_connector_check = now
                    progress(done, total)

                result = run_connector(
                    evidence["signals"],
                    evidence["snapshot"],
                    config,
                    kind,
                    spec,
                    progress=connector_progress,
                    checkpoint=checkpoint,
                    saved=saved,
                )
        elif kind == "optimize":
            parent_result = None
            if spec.get("parent_job_id"):
                parent = get_job(store, job.owner, spec["parent_job_id"])
                from services.scanner_research_service import validate_follow_up

                validate_follow_up(store, job.owner, job.source_id, config, kind, spec)
                parent_result_artifact = parent.result_artifact
                parent_result = read_artifact(store, parent_result_artifact)["result"]["experiment"]
            result = run_search(
                evidence["signals"],
                evidence["snapshot"],
                config,
                spec,
                progress=progress,
                checkpoint=checkpoint,
                saved=saved,
                parent_rows=parent_result["rows"] if parent_result else None,
                parent_reports=parent_result["selected_reports"] if parent_result else None,
            )
            result["experiment"]["root_job_id"] = (
                parent_result.get("root_job_id", spec["parent_job_id"]) if parent_result else job.id
            )
            if result["experiment"].get("follow_up"):
                result["experiment"]["follow_up"]["parent_job_id"] = job.id
        elif kind == "research":
            windows = research_windows(evidence["signals"], evidence["snapshot"], spec)
            keys = {s["date"] + "|" + s["symbol"] for s in evidence["signals"]}
            overlaps = []
            with store.sessions() as db:
                previous = db.scalars(
                    select(ResearchHistory).where(
                        ResearchHistory.owner == job.owner,
                        ResearchHistory.test_from <= windows[-1]["test_end"],
                        ResearchHistory.test_end >= windows[0]["test_from"],
                    )
                ).yield_per(20)
                for record in previous:
                    if keys.intersection(json.loads(record.signal_keys)):
                        overlaps.append(record.job_id)
            result = run_research(
                evidence["signals"],
                evidence["snapshot"],
                config,
                spec,
                progress=progress,
                checkpoint=checkpoint,
                saved=saved,
            )
            result["experiment"]["exploration"] = {
                "prior_explored": spec["prior_explored"],
                "known_overlap_jobs": overlaps,
                "interpretation": "Chronological replay of explored evidence"
                if spec["prior_explored"] or overlaps
                else "First recorded holdout declared unexplored; this does not prove the data was never seen",
            }
            history_record = ResearchHistory(
                job_id=job.id,
                owner=job.owner,
                test_from=windows[0]["test_from"],
                test_end=windows[-1]["test_end"],
                signal_keys=encoded(sorted(keys)).decode(),
            )
        elif kind == "sensitivity":
            result = run_sensitivity(
                evidence["signals"], evidence["snapshot"], config, spec, progress=progress
            )
        else:
            result = evaluate(evidence["signals"], evidence["snapshot"], config, progress=progress)
        if (
            kind
            not in (
                "prepare",
                "acquire",
                "evidence_update",
                "portfolio_analysis",
                "portfolio_prepare",
            )
            and history_record is None
        ):
            used_signals = evidence["signals"]
            used_through = evidence["snapshot"]["sessions"][-1]
            if kind in ("portfolio_backtest", "portfolio_optimize") and result.get(
                "reserved_evaluation"
            ):
                used_through = result["reserved_evaluation"]["selection"]["to"]
                used_signals = [s for s in used_signals if s["date"] <= used_through]
            history_record = ResearchHistory(
                job_id=job.id,
                owner=job.owner,
                test_from=min(s["date"] for s in used_signals),
                test_end=used_through,
                signal_keys=encoded(
                    sorted({s["date"] + "|" + s["symbol"] for s in used_signals})
                ).decode(),
            )
        if activity_state:
            activity({"stage": "saving"}, force=True)
        progress(1, 1)
        if kind in (
            "prepare",
            "acquire",
            "portfolio_backtest",
            "portfolio_optimize",
            "portfolio_prepare",
        ):
            inputs_artifact = save_artifact(store, evidence)
        else:
            with store.sessions() as db:
                source = db.get(ResearchSource, job.source_id)
                inputs_artifact = source.artifact
        bundle = {
            "schema_version": 2,
            "job_id": job.id,
            "source_id": job.source_id,
            "created_at": job.created_at,
            "inputs_artifact": inputs_artifact,
            "specification": spec,
            "kind": kind,
            "result": result,
            **({"preparation": preparation_receipt} if kind == "portfolio_prepare" else {}),
            **(
                {"parent_result_artifact": parent_result_artifact} if parent_result_artifact else {}
            ),
        }
        digest = save_artifact(store, bundle)
        result_display = None
        if kind in ("portfolio_backtest", "portfolio_optimize"):
            from research.result_descriptor import recorded_result

            result_display = recorded_result(
                result, job_id=job.id, result_artifact=digest, inputs_artifact=inputs_artifact
            )
        with store.sessions.begin() as db:
            # The write reservation orders cancellation and publication atomically.
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            lease = db.get(ResearchWorker, 1)
            if lease.token != token:
                raise Cancelled("Worker lease lost")
            if study_observer:
                try:
                    study_observer.finish(db, "completed")
                except study_activity.ObservationLeaseLost as error:
                    raise Cancelled("Cancellation requested before result publication") from error
            changed = db.execute(
                update(ResearchJob)
                .where(
                    ResearchJob.id == job.id,
                    ResearchJob.status.in_(WORKING),
                    ResearchJob.worker == token,
                )
                .values(
                    status="completed",
                    progress=100,
                    result_artifact=digest,
                    updated_at=time.time(),
                    source_id=job.source_id,
                )
            ).rowcount
            if not changed:
                raise Cancelled("Cancellation requested")
            if activity_state or result_display:
                receipt = db.get(ResearchExperiment, job.id)
                counts = json.loads(receipt.counts)
                if result_display:
                    counts["result_display"] = result_display
            if activity_state:
                from services.research_activity import merge_activity

                activity_state = merge_activity(activity_state, {"stage": "complete"}, time.time())
                counts["activity"] = activity_state
            if activity_state or result_display:
                receipt.counts = encoded(counts).decode()
            if kind in ("prepare", "acquire") and db.get(ResearchSource, job.source_id) is None:
                db.add(
                    ResearchSource(
                        id=job.source_id,
                        owner=job.owner,
                        artifact=inputs_artifact,
                        created_at=time.time(),
                    )
                )
                db.add(
                    ResearchSourceReceipt(
                        source_id=job.source_id, receipt=encoded(prepared_source).decode()
                    )
                )
            if history_record is not None:
                db.add(history_record)
        # Findings are committed before optional plots. Admission cannot change
        # the completed study's immutable evidence or completion state.
        if kind == "portfolio_optimize" and result.get("experiment", {}).get("diagnostics"):
            try:
                from services.research_analysis import submit as submit_analysis

                submit_analysis(store, job.owner, job.id)
            except Exception:
                logger.exception("Optional saved-study diagnostics were not queued")
    except AcquisitionBatchPending:
        # The checkpoint is already durable. Release this pass without creating
        # another user attempt; the worker picks up the remaining windows itself.
        with store.sessions.begin() as db:
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            lease = db.get(ResearchWorker, 1)
            current = db.get(ResearchJob, job.id)
            if lease.token == token and current.worker == token:
                if current.status == "cancelling":
                    current.status = "cancelled"
                elif current.status == "running":
                    current.status, current.worker, current.error = "queued", None, None
                    if activity_state:
                        activity_state["batch_count"] = activity_state.get("batch_count", 0) + 1
                        activity_state["updated_at"] = time.time()
                        receipt = db.get(ResearchExperiment, job.id)
                        counts = json.loads(receipt.counts)
                        counts["activity"] = activity_state
                        receipt.counts = encoded(counts).decode()
                current.updated_at = time.time()
    except Paused:
        pass
    except Interrupted:
        finish_job(
            "interrupted",
            "worker_shutdown",
            "Worker stopped; resume the last verified checkpoint when available",
        )
    except Cancelled:
        finish_job("cancelled", "cancellation_requested")
    except study_activity.ObservationFailed:
        logger.exception("Research study activity recording failed")
        finish_job(
            "interrupted",
            "observation_failed",
            "Study activity could not be recorded; resume the last verified checkpoint when available.",
            observation_failed=True,
        )
    except Exception as error:
        logger.exception("Scanner Research calculation failed")
        from research.failures import failure_receipt

        failure = failure_receipt(error, stage=(activity_state or {}).get("stage", "calculation"))
        finish_job(
            "failed",
            "calculation_failed",
            str(error)
            if isinstance(error, ValueError)
            else "Calculation failed; see the local error log.",
            failure=failure,
        )
    return True


def release(store, token):
    with store.sessions.begin() as db:
        claimed = db.execute(
            update(ResearchWorker)
            .where(ResearchWorker.id == 1, ResearchWorker.token == token)
            .values(heartbeat=time.time())
        ).rowcount
        if not claimed:
            return
        study_activity.reconcile(db, time.time(), token=token)
        db.execute(
            update(ResearchJob)
            .where(ResearchJob.worker == token, ResearchJob.status.in_((*WORKING, "cancelling")))
            .values(
                status="interrupted",
                error="Worker stopped before completion",
                updated_at=time.time(),
            )
        )
        db.execute(
            update(ResearchWorker)
            .where(ResearchWorker.id == 1, ResearchWorker.token == token)
            .values(token=None, heartbeat=0)
        )


def main():
    import signal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    parser.add_argument(
        "--stop-file", type=Path, help="Exit gracefully when a local supervisor creates this file"
    )
    parser.add_argument(
        "--ready-file",
        type=Path,
        help="Create a new local supervisor marker after acquiring the worker lease",
    )
    args = parser.parse_args()
    store = ResearchStore()
    token = uuid.uuid4().hex
    stopping = False
    previous_handlers = {}
    ready_created = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    def stop_requested():
        return stopping or bool(args.stop_file and args.stop_file.exists())

    try:
        # Only this dedicated worker process owns these handlers. A terminal
        # interrupt takes the same cooperative checkpoint path as the supervisor.
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        store.initialize()
        acquire(store, token)
        try:
            if args.ready_file:
                # The desktop launcher supplies a unique private runtime path.
                # Refuse to overwrite an existing file supplied by another caller.
                with args.ready_file.open("x", encoding="utf-8") as marker:
                    ready_created = True
                    marker.write("ready\n")
            while not stop_requested():
                worked = run_one(
                    store,
                    token,
                    stop_requested=stop_requested,
                )
                if args.once:
                    break
                if not worked and not stop_requested():
                    time.sleep(1)
        finally:
            try:
                if ready_created:
                    args.ready_file.unlink(missing_ok=True)
            finally:
                release(store, token)
    finally:
        try:
            store.close()
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
