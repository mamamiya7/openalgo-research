"""Exact single-result selection pins shared by decisions and validation.

No bookmark or comparison is manufactured. Read-only resolution uses the native
candidate relationship and the same report verification as saved comparisons.
"""

from sqlalchemy import select

from database.research_db import (
    ResearchCandidateReport,
    ResearchJob,
    ResearchLibraryJob,
    ResearchSetupVersion,
)
from research.report_contract import fingerprint
from services import research_comparisons as comparisons
from services import research_shortlist as shortlist
from services import scanner_research_service as service

MAX_SELECTION_BYTES = 65536


def selection_member(store, owner, experiment_id, job_id, config_id=None):
    from services.research_validation import canonical_source

    source, config_id = canonical_source(store, owner, experiment_id, job_id, config_id)
    bundle, result, _, origin = shortlist._evidence(store, source, config_id)
    name = result.get("portfolio", {}).get("name") or "Backtest"
    if origin["origin_kind"] == "study":
        name = f"{name} · Trial {origin['trial_number'] + 1}"
    member = {
        "id": fingerprint(
            [experiment_id, source.id, source.result_artifact, config_id, origin["period"]]
        )[:32],
        "name": name[:120],
        "note": "",
        "source_job_id": source.id,
        "source_result_artifact": source.result_artifact,
        **origin,
    }
    del bundle, result
    with store.sessions() as db:
        if origin["origin_kind"] == "study" and not origin["is_objective_winner"]:
            link = db.get(ResearchCandidateReport, (owner, source.id, config_id, origin["period"]))
            report_job = db.get(ResearchJob, link.report_job_id) if link else None
        else:
            report_job = db.get(ResearchJob, source.id)
        if (
            not report_job
            or report_job.owner != owner
            or report_job.status != "completed"
            or not report_job.result_artifact
        ):
            raise ValueError("Prepare this candidate's exact report before saving a decision")
        shortlist._linked(db, owner, experiment_id, report_job.id)
        member.update(
            report_job_id=report_job.id,
            report_result_artifact=report_job.result_artifact,
            job={
                "id": report_job.id,
                "status": "completed",
                "progress": 100,
                "created_at": report_job.created_at,
                "kind": "portfolio_optimize"
                if origin["origin_kind"] == "study" and source.id == report_job.id
                else "portfolio_backtest",
            },
        )
    member, _ = comparisons.pin_member(
        store, owner, experiment_id, member, source, report_job, include_presentation=False
    )
    validate_selection(member)
    return member


def validate_selection(member):
    comparisons.validate_member(member)
    raw = service.encoded(member).decode()
    if len(raw.encode()) > MAX_SELECTION_BYTES:
        raise ValueError("Direct decision selection exceeds its size limit")
    return raw


def read_selection_report(store, owner, member):
    from services.research_decisions import _read_pin, _selection_pin

    result = _read_pin(store, owner, _selection_pin(member))
    if result["available"] and result["result"].get("summary") != member["summary"]:
        raise ValueError("Pinned decision statistics differ from the saved selection")
    return result


def validate_target_link(
    db, owner, experiment_id, target, source_job_id, config_id, source_result_artifact=None
):
    """Verify a retained target's native metadata lineage without loading reports."""
    from services.research_decisions import _direct_target_owned
    from services.research_validation import MAX_LINEAGE

    _direct_target_owned(db, owner, experiment_id, target)
    if target.get("config_id") is not None and target["config_id"] != config_id:
        raise ValueError("Direct decision target belongs to another configuration")
    current, visited = target["job_id"], set()
    for _ in range(MAX_LINEAGE):
        if current in visited:
            raise ValueError("Direct decision target has an invalid lineage")
        visited.add(current)
        shortlist._linked(db, owner, experiment_id, current)
        if current == source_job_id:
            return
        candidate = db.scalar(
            select(ResearchCandidateReport).where(ResearchCandidateReport.report_job_id == current)
        )
        link = db.get(ResearchLibraryJob, (experiment_id, current))
        version = db.get(ResearchSetupVersion, link.version_id) if link.version_id else None
        if candidate:
            if candidate.owner != owner or candidate.config_id != config_id:
                raise ValueError("Direct decision target has a different candidate")
            parent, artifact = candidate.study_job_id, candidate.parent_result_artifact
        elif link.role in ("replay", "validation") and version:
            if version.experiment_id != experiment_id or version.parent_trial_id not in (
                None,
                config_id,
            ):
                raise ValueError("Direct decision target has a different replay")
            parent, artifact = version.parent_job_id, version.parent_result_artifact
        else:
            raise ValueError("Direct decision target has a different source")
        shortlist._hex(parent, 32, "direct decision source")
        shortlist._hex(artifact, 64, "direct decision source artifact")
        if (
            parent == source_job_id
            and source_result_artifact
            and artifact != source_result_artifact
        ):
            raise ValueError("Direct decision target has different original evidence")
        current = parent
    raise ValueError("Direct decision target lineage exceeds its limit")
