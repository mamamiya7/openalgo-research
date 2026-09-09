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

from sqlalchemy import select, update

from database.research_db import (
    ResearchAttempt,
    ResearchExperiment,
    ResearchHistory,
    ResearchJob,
    ResearchSource,
    ResearchSourceReceipt,
    ResearchStore,
    ResearchWorker,
)
from services.research_sources import AcquisitionBatchPending
from services.scanner_research_service import (
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
LEASE_SECONDS = 120
NETWORK_HEARTBEAT_SECONDS = 1


class Cancelled(Exception):
    pass


class Interrupted(Exception):
    pass


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
        db.execute(
            update(ResearchJob)
            .where(ResearchJob.status.in_(("running", "cancelling")))
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

    def check():
        if stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        with store.sessions.begin() as db:
            lease, job = db.get(ResearchWorker, 1), db.get(ResearchJob, job_id)
            if lease.token != token or job.status != "running":
                raise Cancelled("Cancellation requested or worker lease lost")
            lease.heartbeat = job.updated_at = time.time()

    def monitor():
        while not finished.wait(NETWORK_HEARTBEAT_SECONDS):
            try:
                check()
            except Exception as error:
                reasons.append(error)
                return

    def cancelled():
        if reasons:
            raise reasons[0]
        check()
        return False

    helper = threading.Thread(target=monitor, name="research-network-lease", daemon=True)
    helper.start()
    try:
        yield cancelled
        cancelled()
    finally:
        finished.set()
        helper.join()


def run_one(store, token, stop_requested=None):
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
        job = db.scalar(
            select(ResearchJob)
            .where(ResearchJob.status == "queued")
            .order_by(ResearchJob.created_at)
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

    def progress(completed, total, force=False):
        nonlocal last_update
        # Session-level cancellation remains bounded without a SQLite transaction
        # for every session of every grid configuration.
        now = time.monotonic()
        if now - last_update < 0.25 and completed < total and not force:
            return
        if stop_requested and stop_requested():
            raise Interrupted("Worker shutdown requested")
        last_update = now
        if kind in ("acquire", "evidence_update", "portfolio_backtest", "portfolio_optimize"):
            ensure_storage_capacity(store)
        with store.sessions.begin() as db:
            lease = db.get(ResearchWorker, 1)
            current = db.get(ResearchJob, job.id)
            if lease.token != token or current.status != "running":
                raise Cancelled("Cancellation requested or worker lease lost")
            lease.heartbeat = time.time()
            current.progress = min(99, int(100 * completed / max(1, total)))
            current.updated_at = time.time()

    def checkpoint(state, counts):
        progress(counts["completed"], counts["total"], force=True)
        artifact = save_artifact(
            store,
            {"identity": experiment.identity, "policy_version": policy, "state": state},
        )
        with store.sessions.begin() as db:
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            current = db.get(ResearchJob, job.id)
            lease = db.get(ResearchWorker, 1)
            if current.status != "running" or lease.token != token:
                raise Cancelled("Cancellation requested before checkpoint publication")
            receipt = db.get(ResearchExperiment, job.id)
            receipt.checkpoint, receipt.counts = artifact, encoded(counts).decode()
            current.progress = min(99, int(100 * counts["completed"] / max(1, counts["total"])))

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
        elif kind == "acquire" and attempt:
            with store.sessions() as db:
                prior = db.get(ResearchExperiment, attempt.previous_job_id)
                prior_job = db.get(ResearchJob, attempt.previous_job_id)
            if (
                prior
                and prior.checkpoint
                and prior_job.source_id == job.source_id
                and prior_job.owner == job.owner
            ):
                receipt = read_artifact(store, prior.checkpoint)
                if receipt["identity"] == prior.identity and receipt["policy_version"] == policy:
                    saved = receipt["state"]
        if kind in ("portfolio_backtest", "portfolio_optimize"):
            from services.research_portfolio import run as run_portfolio

            with network_lease(store, token, job.id, stop_requested) as cancelled:
                result, evidence = run_portfolio(
                    store,
                    job.owner,
                    evidence,
                    spec,
                    saved=saved,
                    checkpoint=checkpoint,
                    progress=progress,
                    cancelled=cancelled,
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
        if kind not in ("prepare", "acquire", "evidence_update") and history_record is None:
            history_record = ResearchHistory(
                job_id=job.id,
                owner=job.owner,
                test_from=min(s["date"] for s in evidence["signals"]),
                test_end=evidence["snapshot"]["sessions"][-1],
                signal_keys=encoded(
                    sorted({s["date"] + "|" + s["symbol"] for s in evidence["signals"]})
                ).decode(),
            )
        progress(1, 1)
        if kind in ("prepare", "acquire", "portfolio_backtest", "portfolio_optimize"):
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
            **(
                {"parent_result_artifact": parent_result_artifact} if parent_result_artifact else {}
            ),
        }
        digest = save_artifact(store, bundle)
        with store.sessions.begin() as db:
            # The write reservation orders cancellation and publication atomically.
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            lease = db.get(ResearchWorker, 1)
            if lease.token != token:
                raise Cancelled("Worker lease lost")
            changed = db.execute(
                update(ResearchJob)
                .where(
                    ResearchJob.id == job.id,
                    ResearchJob.status == "running",
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
                current.updated_at = time.time()
    except Interrupted:
        with store.sessions.begin() as db:
            db.execute(
                update(ResearchJob)
                .where(
                    ResearchJob.id == job.id,
                    ResearchJob.worker == token,
                    ResearchJob.status.in_(("running", "cancelling")),
                )
                .values(
                    status="interrupted",
                    error="Worker stopped; resume the last verified checkpoint when available",
                    updated_at=time.time(),
                )
            )
    except Cancelled:
        with store.sessions.begin() as db:
            db.execute(
                update(ResearchJob)
                .where(
                    ResearchJob.id == job.id,
                    ResearchJob.worker == token,
                    ResearchJob.status.in_(("running", "cancelling")),
                )
                .values(status="cancelled", updated_at=time.time())
            )
    except Exception as error:
        logger.exception("Scanner Research calculation failed")
        with store.sessions.begin() as db:
            db.execute(
                update(ResearchJob)
                .where(
                    ResearchJob.id == job.id,
                    ResearchJob.worker == token,
                    ResearchJob.status.in_(("running", "cancelling")),
                )
                .values(
                    status="failed",
                    error=str(error)
                    if isinstance(error, ValueError)
                    else "Calculation failed; see the local error log.",
                    updated_at=time.time(),
                )
            )
    return True


def release(store, token):
    with store.sessions.begin() as db:
        db.execute(
            update(ResearchJob)
            .where(ResearchJob.worker == token, ResearchJob.status.in_(("running", "cancelling")))
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
    args = parser.parse_args()
    store = ResearchStore()
    token = uuid.uuid4().hex
    stopping = False
    previous_handlers = {}

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
            release(store, token)
    finally:
        try:
            store.close()
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
