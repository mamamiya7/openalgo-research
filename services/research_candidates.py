"""Exact requested study reports, retained independently of study ranking.

Admission uses the native bounded worker and one atomic metadata transaction.
No market-data service or optimizer is invoked when preparing a candidate.
"""

import copy
import hashlib
import re
import time
import uuid

from sqlalchemy import func, select

from database.research_db import (
    ResearchCandidateReport,
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchSource,
    ResearchSourceReceipt,
)
from research.report_contract import settings_identity
from services import scanner_research_service as service

VERSION = "research-candidate-reports-v1"
MISMATCH = "Candidate report does not reproduce its saved study"


def _study(store, owner, job_id):
    parent = service.get_job(store, owner, job_id)
    if parent.status != "completed" or not parent.result_artifact:
        raise ValueError("Choose a completed optimization study")
    bundle = service.read_artifact(store, parent.result_artifact)
    result = bundle.get("result", {})
    study = result.get("experiment", {})
    if bundle.get("kind") != "portfolio_optimize" or not isinstance(study.get("rows"), list):
        raise ValueError("Choose a completed optimization study")
    period = (
        "selection" if result.get("validation") or result.get("reserved_evaluation") else "full"
    )
    return parent, bundle, study, period


def _library_links(db, owner, job_id):
    rows = db.execute(
        select(ResearchLibraryJob, ResearchLibraryExperiment)
        .join(
            ResearchLibraryExperiment,
            ResearchLibraryExperiment.id == ResearchLibraryJob.experiment_id,
        )
        .where(ResearchLibraryJob.job_id == job_id)
    ).all()
    if any(experiment.owner != owner for _, experiment in rows):
        raise ValueError("Saved study has an invalid library link")
    return rows


def guard_resume(db, owner, job_id):
    """Candidate resume obeys the same archive gate as initial preparation."""
    candidate = db.scalar(
        select(ResearchCandidateReport).where(ResearchCandidateReport.report_job_id == job_id)
    )
    if candidate is None:
        return
    if candidate.owner != owner:
        raise LookupError("Candidate report not found")
    links = _library_links(db, owner, candidate.study_job_id) + _library_links(db, owner, job_id)
    if any(experiment.archived for _, experiment in links):
        raise ValueError("Restore this experiment from the archive before resuming its report.")


def _availability_error(store, bundle):
    try:
        identifier = bundle.get("inputs_artifact")
        if not identifier:
            raise ValueError("missing")
        # Polling only checks retained archive structure/presence. Reconstructing
        # a candidate performs the full canonical read once; do not expand every
        # frozen minute candle just to display the trial table's report buttons.
        for digest in service.artifact_dependencies(store, identifier):
            path = store.root / "artifacts" / f"{digest}.json"
            if not path.exists():
                path = path.with_suffix(".json.gz")
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(store.root)
            ):
                raise ValueError("missing")
    except (OSError, ValueError, KeyError, TypeError):
        return "The study's frozen price inputs are unavailable. Its saved trial statistics remain readable."
    result = bundle.get("result", {})
    if result.get("portfolio"):
        from research.portfolio import execution_versions

        portfolio = {
            key: value for key, value in result["portfolio"].items() if key != "optimization"
        }
        try:
            current = execution_versions(portfolio)
        except ValueError as error:
            return str(error)
        recorded = bundle.get("specification", {}).get("versions") or result.get("execution", {})
        if any(key in recorded and recorded[key] != value for key, value in current.items()):
            return "This saved study needs its recorded engine version to prepare exact reports."
    return None


def _receipt(row, study, parent, link, child, unavailable):
    value = {
        "config_id": row["config_id"],
        "trial_number": row["trial_number"],
        "is_objective_winner": study.get("recommendation_id") == row["config_id"],
    }
    if value["is_objective_winner"]:
        return {**value, "status": "ready", "report_job_id": parent.id}
    if link:
        if (
            child is None
            or child.owner != parent.owner
            or link.parent_result_artifact != parent.result_artifact
        ):
            return {
                **value,
                "status": "unavailable",
                "error": "The saved candidate report link is incomplete.",
            }
        if child.status == "completed":
            return {**value, "status": "ready", "report_job_id": child.id}
        status = (
            child.status
            if child.status in ("queued", "running")
            else "running"
            if child.status == "cancelling"
            else "failed"
        )
        if (child.error or "").startswith(MISMATCH):
            status = "mismatch"
        return {
            **value,
            "status": status,
            "report_job_id": child.id,
            **(
                {"error": child.error or "Report preparation stopped. Open the run to resume."}
                if status in ("failed", "mismatch")
                else {}
            ),
        }
    if unavailable:
        return {**value, "status": "unavailable", "error": unavailable}
    return {**value, "status": "available"}


def availability(store, owner, study_job_id):
    parent, bundle, study, period = _study(store, owner, study_job_id)
    with store.sessions() as db:
        links = db.scalars(
            select(ResearchCandidateReport).where(
                ResearchCandidateReport.owner == owner,
                ResearchCandidateReport.study_job_id == parent.id,
                ResearchCandidateReport.period == period,
            )
        ).all()
        linked = {row.config_id: row for row in links}
        children = {
            job.id: job
            for job in db.scalars(
                select(ResearchJob).where(ResearchJob.id.in_([row.report_job_id for row in links]))
            )
        }
        archived = any(
            experiment.archived for _, experiment in _library_links(db, owner, parent.id)
        )
    unavailable = (
        "Restore this experiment from the archive before preparing a report."
        if archived
        else _availability_error(store, bundle)
    )
    return {
        "version": VERSION,
        "study_job_id": parent.id,
        "period": period,
        "candidates": [
            _receipt(
                row,
                study,
                parent,
                linked.get(row["config_id"]),
                children.get(linked[row["config_id"]].report_job_id)
                if row["config_id"] in linked
                else None,
                unavailable,
            )
            for row in study["rows"]
        ],
    }


def _selected(study, config_id):
    if not isinstance(config_id, str) or not re.fullmatch(r"[a-f0-9]{64}", config_id):
        raise ValueError("Choose a saved candidate identity")
    matches = [row for row in study["rows"] if row.get("config_id") == config_id]
    if len(matches) != 1 or settings_identity(matches[0].get("strategies")) != config_id:
        raise ValueError("Choose a saved candidate from this study")
    return matches[0]


def prepare(store, owner, study_job_id, config_id):
    from research.connectors.registry import policy_for_request
    from research.engine import validate_config
    from services.research_portfolio import replay_inputs, validate_submission

    parent, bundle, study, period = _study(store, owner, study_job_id)
    selected = _selected(study, config_id)
    current = next(
        row
        for row in availability(store, owner, parent.id)["candidates"]
        if row["config_id"] == config_id
    )
    if current["status"] != "available":
        return current
    # Retain exact expected scalars separately from the new result. They can only
    # validate reconstruction, never replace an engine output or a study row.
    try:
        _, evidence = replay_inputs(store, owner, parent.id, trial_id=config_id)
    except (ValueError, OSError) as error:
        return {**current, "status": "unavailable", "error": str(error)}
    evidence["candidate_report"] = {
        "version": VERSION,
        "study_job_id": parent.id,
        "parent_result_artifact": parent.result_artifact,
        "config_id": config_id,
        "period": period,
        "summary": copy.deepcopy(selected["summary"]),
        "analysis": copy.deepcopy(selected.get("analysis")),
        "evaluation_basis": copy.deepcopy(bundle["result"].get("evaluation_basis")),
    }
    portfolio = evidence["portfolio"]
    specification = validate_submission(
        evidence, "portfolio_backtest", {"portfolio": portfolio, "versions": evidence["versions"]}
    )
    config = validate_config({"initial_capital": portfolio["capital"]})
    # Atomic publisher protects artifact writes during maintenance. A transaction
    # loser can leave only the same content-addressed artifact, never another job.
    _, source = service.register_source(store, owner, evidence, publish=False)
    artifact = hashlib.sha256(service.encoded(evidence)).hexdigest()
    identity = hashlib.sha256(
        service.encoded(
            {
                "source_id": source["id"],
                "config": config,
                "kind": "portfolio_backtest",
                "specification": specification,
                "policy_version": policy_for_request(evidence["snapshot"], specification),
            }
        )
    ).hexdigest()
    with store.sessions.begin() as db:
        service.write_guard(db)
        key = (owner, parent.id, config_id, period)
        prior = db.get(ResearchCandidateReport, key)
        if prior is None:
            libraries = _library_links(db, owner, parent.id)
            if any(experiment.archived for _, experiment in libraries):
                raise ValueError(
                    "Restore this experiment from the archive before preparing a report."
                )
            if (
                db.scalar(
                    select(func.count())
                    .select_from(ResearchJob)
                    .where(ResearchJob.status.in_(service.ACTIVE))
                )
                >= 4
            ):
                raise ValueError("Research queue is full (four active or pending jobs)")
            service.ensure_storage_capacity(store, len(service.encoded(specification)) + 32768)
            now, job_id = time.time(), uuid.uuid4().hex
            if db.get(ResearchSource, source["id"]) is None:
                db.add(
                    ResearchSource(id=source["id"], owner=owner, artifact=artifact, created_at=now)
                )
                db.add(
                    ResearchSourceReceipt(
                        source_id=source["id"], receipt=service.encoded(source).decode()
                    )
                )
            db.add(
                ResearchJob(
                    id=job_id,
                    owner=owner,
                    source_id=source["id"],
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
                    kind="portfolio_backtest",
                    specification=service.encoded(specification).decode(),
                    identity=identity,
                    counts="{}",
                    parent_job_id=parent.id,
                )
            )
            db.add(
                ResearchCandidateReport(
                    owner=owner,
                    study_job_id=parent.id,
                    config_id=config_id,
                    period=period,
                    parent_result_artifact=parent.result_artifact,
                    report_job_id=job_id,
                    created_at=now,
                )
            )
            for _, experiment in libraries:
                # A candidate has its own exact settings in immutable evidence;
                # attaching the study's optimizing setup version would mislabel it.
                db.add(
                    ResearchLibraryJob(
                        experiment_id=experiment.id, job_id=job_id, role="candidate", created_at=now
                    )
                )
                experiment.updated_at = now
    return next(
        row
        for row in availability(store, owner, parent.id)["candidates"]
        if row["config_id"] == config_id
    )


def verify_reconstruction(evidence, result):
    expected = evidence.get("candidate_report")
    if not expected:
        return
    differences = []
    if expected.get("version") != VERSION or settings_identity(
        result.get("strategies")
    ) != expected.get("config_id"):
        differences.append("settings")
    if result.get("summary") != expected.get("summary"):
        differences.append("summary values")
    original_analysis = expected.get("analysis")
    current_analysis = result.get("analysis", {})
    if original_analysis and original_analysis.get("version") == current_analysis.get("version"):
        if any(
            current_analysis.get(key) != original_analysis.get(key)
            for key in ("metrics", "unavailable")
        ):
            differences.append("saved statistics")
    original_basis = expected.get("evaluation_basis")
    if original_basis and original_basis.get("status") == "verified":
        from research.evaluation_basis import comparison_status

        if not comparison_status(original_basis, result.get("evaluation_basis"))["compatible"]:
            differences.append("evaluation basis")
    if differences:
        raise ValueError(
            f"{MISMATCH}: {', '.join(differences)} differ. The original study and trial values are unchanged."
        )
    result["candidate_report"] = {
        key: copy.deepcopy(expected[key])
        for key in ("version", "study_job_id", "parent_result_artifact", "config_id", "period")
    }
    result["candidate_report"]["verification"] = {
        "summary": "matched",
        "basis": "matched"
        if original_basis and original_basis.get("status") == "verified"
        else "original-unverified",
    }
