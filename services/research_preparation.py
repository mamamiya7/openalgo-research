"""Early broker acquisition, bounded by the ordinary research queue and evidence store.

The pointer is operational metadata, never a tested setup or financial history.
No account calculation is started here. A completed preparation can only seed a
new run after exact input binding and fresh native-source guards succeed.
"""

from __future__ import annotations

import hashlib
import json
import stat
import time
import uuid

from sqlalchemy import func, select

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryJob,
    ResearchPreparation,
    ResearchSource,
    ResearchSourceReceipt,
)
from research import portfolio as contract
from services import research_library as library
from services import scanner_research_service as service

KIND = "portfolio_prepare"


def _binding(evidence, spec):
    return hashlib.sha256(
        service.encoded({"evidence": evidence, "specification": spec})
    ).hexdigest()


def _receipt(db, pointer):
    job = db.get(ResearchJob, pointer.job_id) if pointer else None
    experiment = db.get(ResearchExperiment, pointer.job_id) if job else None
    if not job or job.owner != pointer.owner or not experiment or experiment.kind != KIND:
        return {
            "job_id": None,
            "status": "idle",
            "progress": 0,
            "activity": None,
            "ready": False,
            "error": None,
            "reuse_mode": None,
        }
    counts = json.loads(experiment.counts)
    return {
        "job_id": job.id,
        "status": job.status,
        "progress": job.progress,
        "activity": counts.get("activity"),
        "ready": job.status == "completed" and bool(job.result_artifact),
        "error": job.error,
        "reuse_mode": pointer.reuse_mode if job.status == "completed" else None,
    }


def status(store, owner, identifier):
    with store.sessions() as db:
        library._owned(db, owner, identifier)
        pointer = db.get(ResearchPreparation, identifier)
        if pointer and pointer.owner != owner:
            raise LookupError("Research preparation not found")
        return _receipt(db, pointer)


def start(store, owner, identifier, data):
    from research.connectors.registry import policy_for_request
    from research.engine import validate_config
    from services.research_portfolio import resolve_inputs, validate_submission

    library._object(data, {"revision"}, "research preparation")
    with store.sessions() as db:
        row = library._owned(db, owner, identifier)
        library._revision(row, data.get("revision"))
        library._editable(row)
        draft = json.loads(row.draft)
    evidence = resolve_inputs(store, owner, library.portfolio_payload(draft))
    portfolio = evidence["portfolio"]
    spec = validate_submission(
        evidence,
        "portfolio_optimize" if portfolio.get("optimization") else "portfolio_backtest",
        {"portfolio": portfolio, "versions": evidence["versions"]},
    )
    binding = _binding(evidence, spec)
    # Repeated page effects/polls do not republish an unchanged source. Keep the
    # same write reservation and ownership/revision/source fences as enqueue.
    with store.sessions.begin() as db:
        service.write_guard(db)
        row = library._owned(db, owner, identifier)
        library._revision(row, data.get("revision"))
        library._editable(row)
        library._check_sources(db, owner, draft)
        pointer = db.get(ResearchPreparation, identifier)
        previous = db.get(ResearchJob, pointer.job_id) if pointer else None
        if pointer and pointer.owner != owner:
            raise LookupError("Research preparation not found")
        if previous and previous.owner != owner:
            raise LookupError("Research preparation not found")
        if (
            pointer
            and previous
            and pointer.binding == binding
            and previous.status in service.ACTIVE
        ):
            return _receipt(db, pointer)
    current_completed = None
    completed_job = None
    with store.sessions() as db:
        pointer = db.get(ResearchPreparation, identifier)
        previous = db.get(ResearchJob, pointer.job_id) if pointer else None
        if (
            pointer
            and pointer.owner == owner
            and pointer.binding == binding
            and previous
            and previous.owner == owner
            and previous.status == "completed"
            and previous.result_artifact
        ):
            completed_job = previous
    if completed_job:
        if _eligible(evidence, spec):
            fresh = _checked_inputs(store, completed_job, evidence, spec, binding)
        else:
            fresh = _checked_cache(store, completed_job, evidence, spec, binding)
        if fresh:
            current_completed = completed_job.id
    config = validate_config({"initial_capital": portfolio["capital"]})
    # Atomic artifact publication still performs its usual quota/path checks.
    _, receipt = service.register_source(store, owner, evidence, publish=False)
    artifact = hashlib.sha256(service.encoded(evidence)).hexdigest()
    source_id = receipt["id"]
    identity = hashlib.sha256(
        service.encoded(
            {
                "source_id": source_id,
                "config": config,
                "kind": KIND,
                "specification": spec,
                "policy_version": policy_for_request(evidence["snapshot"], spec),
            }
        )
    ).hexdigest()
    with store.sessions.begin() as db:
        service.write_guard(db)
        row = library._owned(db, owner, identifier)
        library._revision(row, data.get("revision"))
        library._editable(row)
        library._check_sources(db, owner, draft)
        pointer = db.get(ResearchPreparation, identifier)
        if pointer and pointer.owner != owner:
            raise LookupError("Research preparation not found")
        previous = db.get(ResearchJob, pointer.job_id) if pointer else None
        if previous and previous.owner != owner:
            raise LookupError("Research preparation not found")
        if (
            previous
            and pointer.binding == binding
            and (previous.status in service.ACTIVE or previous.id == current_completed)
        ):
            return _receipt(db, pointer)
        if previous and previous.status in service.ACTIVE:
            previous.status = "cancelled" if previous.status == "queued" else "cancelling"
            previous.updated_at = time.time()
        db.flush()
        if (
            db.scalar(
                select(func.count())
                .select_from(ResearchJob)
                .where(ResearchJob.status.in_(service.ACTIVE))
            )
            >= 4
        ):
            raise ValueError("Research queue is full (four active or pending jobs)")
        service.ensure_storage_capacity(
            store, len(row.draft) * 2 + len(service.encoded(spec)) + 32768
        )
        job_id, now = uuid.uuid4().hex, time.time()
        if db.get(ResearchSource, source_id) is None:
            db.add(ResearchSource(id=source_id, owner=owner, artifact=artifact, created_at=now))
            db.add(
                ResearchSourceReceipt(
                    source_id=source_id, receipt=service.encoded(receipt).decode()
                )
            )
        db.add(
            ResearchJob(
                id=job_id,
                owner=owner,
                source_id=source_id,
                config=service.encoded(config).decode(),
                status="queued",
                progress=0,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            ResearchExperiment(
                job_id=job_id,
                kind=KIND,
                specification=service.encoded(spec).decode(),
                identity=identity,
                counts="{}",
            )
        )
        if pointer is None:
            pointer = ResearchPreparation(experiment_id=identifier, owner=owner)
            db.add(pointer)
        pointer.job_id, pointer.binding, pointer.revision = job_id, binding, row.revision
        pointer.reuse_mode = "prepared" if _eligible(evidence, spec) else "cache"
        db.flush()
        return _receipt(db, pointer)


def _eligible(evidence, spec):
    portfolio = spec.get("portfolio", {})
    return (
        not portfolio.get("automatic_research")
        and not portfolio.get("optimization")
        and not any(
            evidence.get(key)
            for key in (
                "frozen_prices",
                "replay_origin",
                "condition_replay",
                "parent_result_artifact",
                "automatic_recipe",
            )
        )
        and contract.interval_for(portfolio, evidence["strategies"]) == "D"
    )


def _file_stamp(path):
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(observed.st_mode):
        raise ValueError("Native preparation freshness requires ordinary files")
    return [
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    ]


def _footprint(store, evidence):
    from pathlib import Path

    from services.research_sources import acquisition_paths
    from services.research_volume_provenance import NativeVolumeProvenance

    target, _ = acquisition_paths(store)
    result = {"archive": _file_stamp(target), "wal": _file_stamp(Path(str(target) + ".wal"))}
    # Fingerprint only relevant bounded sidecars, rather than scan an unbounded directory.
    root = store.root / "volume-provenance"
    if root.is_symlink():
        raise ValueError("Volume provenance cannot follow symbolic links")
    archive_id = contract.fingerprint(str(target.resolve()))
    cache = NativeVolumeProvenance(root, archive_id)
    volume_required = any(
        row.get("execution_policy", {}).get("capacity_mode") == "prior20_broker_reported"
        for row in evidence["strategies"]
    )
    symbols = sorted({row["symbol"] for row in evidence["signals"]}) if volume_required else []
    if len(symbols) > 200:
        raise ValueError("Early preparation freshness supports at most 200 stocks")
    result["volume"] = {symbol: _file_stamp(cache._path(symbol, "D", "NSE")) for symbol in symbols}
    return result


def _calendar_observation(initial):
    from services.research_native_calendar import native_calendar_snapshot

    return native_calendar_snapshot(
        initial["signals"],
        warmup_sessions=0,
        tail_sessions=contract.calendar_tail_sessions(initial["portfolio"], initial["strategies"]),
        completion_check=contract.calendar_completion_check(
            initial["portfolio"], initial["strategies"]
        ),
    )


def _fresh_calendars(store, initial, prepared):
    calendar = _calendar_observation(initial)
    if calendar != service.read_artifact(store, prepared["reference_artifact"]):
        return False
    sessions = prepared["snapshot"]["sessions"]
    if sessions != calendar["sessions"]:
        return False
    return _fresh_auxiliary_calendars(prepared)


def _fresh_auxiliary_calendars(prepared):
    from services.research_native_calendar import native_calendar_window

    sessions = prepared["snapshot"]["sessions"]
    records = []
    if prepared.get("entry_condition_market"):
        records.append(prepared["entry_condition_market"]["calendar"])
    for key in ("automatic_stock_features", "automatic_stock_market_context"):
        bundle = prepared.get(key)
        if bundle:
            records.extend(row["calendar"] for row in bundle["series"].values())
            for field in ("market", "benchmark"):
                if bundle.get(field):
                    records.append(bundle[field]["calendar"])
    # All stock records must agree; fetch each distinct native window only once.
    windows = {}
    for recorded in records:
        warmup = recorded["provenance"]["warmup_sessions"]
        key = (sessions[0], sessions[-1], warmup)
        if key not in windows:
            if len(windows) >= 4:
                return False
            windows[key] = native_calendar_window(key[0], key[1], warmup_sessions=warmup)
        if windows[key] != recorded:
            return False
    return True


def completed_receipt(store, evidence, prepared, spec):
    """Capture mutable native-source identities after all preparation has finished."""
    return {
        "binding": _binding(evidence, spec),
        "freshness": _footprint(store, evidence),
        "native_calendar_id": hashlib.sha256(
            service.encoded(_calendar_observation(evidence))
        ).hexdigest(),
    }


def _checked_cache(store, job, evidence, spec, binding):
    try:
        bundle = service.read_artifact(store, job.result_artifact)
        if (
            not isinstance(bundle, dict)
            or bundle.get("schema_version") != 2
            or bundle.get("kind") != KIND
            or bundle.get("job_id") != job.id
            or bundle.get("source_id") != job.source_id
            or bundle.get("specification") != spec
            or not isinstance(bundle.get("preparation"), dict)
        ):
            return False
        receipt = bundle["preparation"]
        before = _footprint(store, evidence)
        if (
            receipt.get("binding") != binding
            or before.get("archive") is None
            or receipt.get("freshness") != before
        ):
            return False
        calendar_id = hashlib.sha256(service.encoded(_calendar_observation(evidence))).hexdigest()
        if receipt.get("native_calendar_id") != calendar_id:
            return False
        prepared = service.read_artifact(store, bundle["inputs_artifact"])
        return (
            isinstance(prepared, dict)
            and _fresh_auxiliary_calendars(prepared)
            and _footprint(store, evidence) == before
        )
    except (ValueError, LookupError, KeyError, TypeError, OSError):
        return False


def _checked_inputs(store, job, evidence, spec, binding):
    if not _eligible(evidence, spec):
        return None
    try:
        bundle = service.read_artifact(store, job.result_artifact)
        if (
            not isinstance(bundle, dict)
            or bundle.get("schema_version") != 2
            or bundle.get("kind") != KIND
            or bundle.get("job_id") != job.id
            or bundle.get("source_id") != job.source_id
            or bundle.get("specification") != spec
            or not isinstance(bundle.get("preparation"), dict)
            or bundle.get("preparation", {}).get("binding") != binding
        ):
            return None
        receipt = bundle["preparation"]
        before = _footprint(store, evidence)
        if before.get("archive") is None or receipt.get("freshness") != before:
            return None
        prepared = service.read_artifact(store, bundle["inputs_artifact"])
        if not isinstance(prepared, dict) or not _fresh_calendars(store, evidence, prepared):
            return None
        from services.research_portfolio import validate_prepared_inputs

        validate_prepared_inputs(store, prepared, spec)
        if _footprint(store, evidence) != before:
            return None
        return bundle["inputs_artifact"]
    except (ValueError, LookupError, KeyError, TypeError, OSError):
        return None


def reuse_for_run(store, owner, job_id, evidence, spec):
    """Return a fully checked checkpoint or conservatively do ordinary preparation."""
    if not _eligible(evidence, spec):
        return None
    with store.sessions() as db:
        main = db.get(ResearchJob, job_id)
        link = db.scalar(select(ResearchLibraryJob).where(ResearchLibraryJob.job_id == job_id))
        if not main or main.owner != owner or not link:
            return None
        container = library._owned(db, owner, link.experiment_id)
        pointer = db.get(ResearchPreparation, container.id)
        if not pointer or pointer.owner != owner or pointer.binding != _binding(evidence, spec):
            return None
        job = db.get(ResearchJob, pointer.job_id)
        experiment = db.get(ResearchExperiment, pointer.job_id)
        if (
            not job
            or job.owner != owner
            or job.source_id != main.source_id
            or job.status != "completed"
            or not job.result_artifact
            or not experiment
            or experiment.kind != KIND
            or json.loads(experiment.specification) != spec
        ):
            return None
        result_id = job.result_artifact
        binding = pointer.binding
    inputs_id = _checked_inputs(store, job, evidence, spec, binding)
    if not inputs_id:
        return None
    # Recheck the replaceable pointer and result after potentially slow readers.
    with store.sessions() as db:
        pointer = db.get(ResearchPreparation, link.experiment_id)
        job = db.get(ResearchJob, job.id)
        if (
            not pointer
            or pointer.owner != owner
            or pointer.binding != binding
            or not job
            or pointer.job_id != job.id
            or job.status != "completed"
            or job.result_artifact != result_id
        ):
            return None
    return {"phase": "calculation", "inputs_artifact": inputs_id, "calculation": None}
