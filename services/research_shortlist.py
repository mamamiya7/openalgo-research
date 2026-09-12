"""Server-saved candidate bookmarks; no search, price acquisition or calculation.

Descriptive snapshots keep list reads bounded. Settings and statistics are read
on demand from the original content-addressed result, never a current draft.
"""

import copy
import json
import math
import re
import time
import uuid

from sqlalchemy import func, select

from database.research_db import (
    ResearchCandidateReport,
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryJob,
    ResearchShortlistCandidate,
)
from research.report_contract import report_context, settings_identity
from services import research_candidates as candidates
from services import research_library as library
from services import scanner_research_service as service

VERSION = "research-shortlist-v1"
MAX_BODY_BYTES = 8192
MAX_SNAPSHOT_BYTES = 32768
MAX_PER_EXPERIMENT = 1000
MAX_ROWS = 100000
FIELDS = (
    "id",
    "experiment_id",
    "source_job_id",
    "source_result_artifact",
    "config_id",
    "period",
    "origin_kind",
    "trial_number",
    "proposal_number",
    "is_objective_winner",
    "name",
    "note",
    "revision",
    "created_at",
    "updated_at",
)
SOURCE_ERROR = "The original saved result is unavailable. This bookmark keeps its saved identity."


class ShortlistConflict(ValueError):
    def __init__(self, current):
        super().__init__("This saved candidate changed in another session. Reload before editing.")
        self.current = current


def _hex(value, length, label):
    if not isinstance(value, str) or not re.fullmatch(rf"[a-f0-9]{{{length}}}", value):
        raise ValueError(f"Choose a saved {label}")
    return value


def _linked(db, owner, experiment_id, job_id):
    experiment = library._owned(db, owner, experiment_id)
    job = db.get(ResearchJob, job_id)
    if (
        job is None
        or job.owner != owner
        or db.get(ResearchLibraryJob, (experiment_id, job_id)) is None
    ):
        raise LookupError("Saved result not found in this experiment")
    return experiment, job


def _owned(db, owner, experiment_id, identifier):
    experiment = library._owned(db, owner, experiment_id)
    row = db.get(ResearchShortlistCandidate, identifier)
    if row is None or row.owner != owner or row.experiment_id != experiment_id:
        raise LookupError("Saved candidate not found")
    return experiment, row


def _artifact_present(store, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        return False
    try:
        path = store.root / "artifacts" / f"{identifier}.json"
        if not path.exists():
            path = path.with_suffix(".json.gz")
        return (
            path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(store.root)
        )
    except OSError:
        return False


def _normal_source(db, owner, experiment_id, job_id, config_id=None):
    """Normalize only the native exact-report relationship, never a similar name."""
    experiment, job = _linked(db, owner, experiment_id, job_id)
    link = db.scalar(
        select(ResearchCandidateReport).where(ResearchCandidateReport.report_job_id == job_id)
    )
    if link is None:
        return experiment, job, config_id
    _, parent = _linked(db, owner, experiment_id, link.study_job_id)
    native = db.get(ResearchExperiment, job_id)
    if (
        link.owner != owner
        or link.parent_result_artifact != parent.result_artifact
        or native is None
        or native.kind != "portfolio_backtest"
        or native.parent_job_id != parent.id
        or (config_id is not None and config_id != link.config_id)
    ):
        raise ValueError("The candidate report does not match its original study")
    return experiment, parent, link.config_id


def _evidence(store, job, config_id=None, proposal_number=None):
    if job.status != "completed" or not job.result_artifact:
        raise ValueError("Choose a completed backtest or optimization study")
    bundle = service.read_artifact(store, job.result_artifact)
    result = bundle.get("result", {})
    kind = bundle.get("kind")
    if kind not in ("portfolio_optimize", "portfolio_backtest") or not isinstance(result, dict):
        raise ValueError("Choose a completed backtest or optimization study")
    if kind == "portfolio_optimize":
        study = result.get("experiment", {})
        if (
            not isinstance(study, dict)
            or not isinstance(study.get("rows"), list)
            or any(not isinstance(row, dict) for row in study["rows"])
        ):
            raise ValueError("This study does not retain candidate rows")
        selected = candidates._selected(study, config_id)
        canonical = selected.get("trial_number")
        if type(canonical) is not int or canonical < 0:
            raise ValueError("This study does not retain a valid candidate trial")
        number = canonical if proposal_number is None else proposal_number
        if type(number) is not int or number < 0:
            raise ValueError("Choose a recorded completed trial")
        trials = study.get("trials")
        if trials is not None and not isinstance(trials, list):
            raise ValueError("This study does not retain valid trial records")
        if (isinstance(trials, list) or number != canonical) and not any(
            isinstance(item, dict)
            and item.get("number") == number
            and item.get("config_id") == config_id
            and item.get("state") == "complete"
            for item in (trials or [])
        ):
            raise ValueError("Choose a recorded completed trial for this candidate")
        period = (
            "selection" if result.get("validation") or result.get("reserved_evaluation") else "full"
        )
        origin = {
            "origin_kind": "study",
            "trial_number": canonical,
            "proposal_number": number,
            "is_objective_winner": study.get("recommendation_id") == config_id,
        }
    else:
        actual = settings_identity(result.get("strategies"))
        if (
            actual is None
            or (config_id is not None and actual != config_id)
            or proposal_number is not None
        ):
            raise ValueError("Choose this backtest's saved configuration")
        config_id = actual
        selected = result
        period = report_context(
            result,
            job_id=job.id,
            result_artifact=job.result_artifact,
            inputs_artifact=bundle.get("inputs_artifact"),
        )["period"]
        if period not in ("full", "selection"):
            raise ValueError("Save the original selection candidate to your shortlist")
        origin = {
            "origin_kind": "backtest",
            "trial_number": None,
            "proposal_number": None,
            "is_objective_winner": False,
        }
    return bundle, result, selected, {**origin, "config_id": config_id, "period": period}


def validate_snapshot(snapshot):
    """A small descriptor shared by admission and offline metadata validation."""
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "summary",
        "dates",
        "capital",
        "currency",
        "engine",
        "objective",
    }:
        raise ValueError("Invalid saved candidate summary")
    summary = snapshot["summary"]
    if (
        not isinstance(summary, dict)
        or len(summary) > 128
        or any(
            not isinstance(key, str)
            or len(key) > 128
            or not isinstance(value, (str, int, float, type(None)))
            or isinstance(value, bool)
            or (isinstance(value, float) and not math.isfinite(value))
            or (isinstance(value, str) and len(value) > 1024)
            for key, value in summary.items()
        )
    ):
        raise ValueError("This result has invalid saved summary statistics")
    dates = snapshot["dates"]
    if (
        not isinstance(dates, dict)
        or set(dates) != {"from", "to"}
        or any(
            value is not None
            and (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
            for value in dates.values()
        )
    ):
        raise ValueError("Invalid saved candidate dates")
    capital = snapshot["capital"]
    if capital is not None and (
        isinstance(capital, bool)
        or not isinstance(capital, (int, float))
        or not math.isfinite(capital)
    ):
        raise ValueError("Invalid saved candidate capital")
    for key, limit in (("currency", 16), ("engine", 40)):
        value = snapshot[key]
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ValueError(f"Invalid saved candidate {key}")
    objective = snapshot["objective"]
    if objective is not None and (
        not isinstance(objective, dict)
        or set(objective) != {"key", "score"}
        or not isinstance(objective["key"], str)
        or len(objective["key"]) > 40
        or isinstance(objective["score"], bool)
        or not isinstance(objective["score"], (int, float))
        or not math.isfinite(objective["score"])
    ):
        raise ValueError("Invalid saved candidate objective")
    raw = service.encoded(snapshot).decode()
    if len(raw.encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Saved candidate summary exceeds its size limit")
    return raw


def _snapshot(result, selected, origin):
    summary = selected.get("summary")
    basis = result.get("evaluation_basis", {})
    comparison = basis.get("comparison", {})
    portfolio = result.get("portfolio", {})
    coverage = result.get("coverage", {})
    score = selected.get("score")
    objective = result.get("experiment", {}).get("specification", {}).get("objective")
    snapshot = {
        "summary": copy.deepcopy(summary),
        "dates": {"from": coverage.get("date_from"), "to": coverage.get("date_to")},
        "capital": comparison.get("capital", result.get("config", {}).get("initial_capital")),
        "currency": comparison.get("currency"),
        "engine": portfolio.get("engine"),
        "objective": {"key": objective, "score": score}
        if origin["origin_kind"] == "study"
        and isinstance(objective, str)
        and isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
        else None,
    }
    return validate_snapshot(snapshot)


def _receipt(store, db, experiment, row):
    source = db.get(ResearchJob, row.source_job_id)
    native = db.get(ResearchExperiment, row.source_job_id)
    available = bool(
        source
        and source.owner == row.owner
        and source.status == "completed"
        and source.result_artifact == row.source_result_artifact
        and native
        and native.kind
        == ("portfolio_optimize" if row.origin_kind == "study" else "portfolio_backtest")
        and db.get(ResearchLibraryJob, (row.experiment_id, row.source_job_id))
        and _artifact_present(store, row.source_result_artifact)
    )
    report = {"status": "unavailable", "error": SOURCE_ERROR}
    if available:
        if row.origin_kind == "backtest" or row.is_objective_winner:
            report = {"status": "ready", "report_job_id": row.source_job_id}
        else:
            link = db.get(
                ResearchCandidateReport, (row.owner, row.source_job_id, row.config_id, row.period)
            )
            child = db.get(ResearchJob, link.report_job_id) if link else None
            if link and (
                link.parent_result_artifact != row.source_result_artifact
                or not child
                or child.owner != row.owner
            ):
                report = {
                    "status": "unavailable",
                    "error": "The saved candidate report link is incomplete.",
                }
            elif child:
                status = (
                    "ready"
                    if child.status == "completed"
                    else (
                        child.status
                        if child.status in ("queued", "running")
                        else "running"
                        if child.status == "cancelling"
                        else "failed"
                    )
                )
                if (child.error or "").startswith(candidates.MISMATCH):
                    status = "mismatch"
                report = {"status": status, "report_job_id": child.id}
                if status == "ready" and not _artifact_present(store, child.result_artifact):
                    report = {
                        "status": "unavailable",
                        "error": "The saved candidate report is unavailable.",
                    }
                if status in ("failed", "mismatch"):
                    report["error"] = (
                        child.error or "Report preparation stopped. Open the run to resume."
                    )
            elif experiment.archived:
                report = {
                    "status": "unavailable",
                    "error": "Restore this experiment before preparing a report.",
                }
            else:
                report = {"status": "available"}
    return {
        **{field: getattr(row, field) for field in FIELDS},
        "snapshot": json.loads(row.snapshot),
        "archived": experiment.archived,
        "source_available": available,
        **({"source_error": SOURCE_ERROR} if not available else {}),
        "report": report,
    }


def list_candidates(
    store, owner, experiment_id, *, limit=20, offset=0, job_id=None, config_id=None
):
    limit, offset = library._page(limit, offset)
    if job_id is not None:
        _hex(job_id, 32, "result")
    if config_id is not None:
        _hex(config_id, 64, "configuration")
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        if job_id:
            _, normalized, config_id = _normal_source(db, owner, experiment_id, job_id, config_id)
            job_id = normalized.id
        query = select(ResearchShortlistCandidate).where(
            ResearchShortlistCandidate.owner == owner,
            ResearchShortlistCandidate.experiment_id == experiment_id,
        )
        if job_id:
            query = query.where(ResearchShortlistCandidate.source_job_id == job_id)
        if config_id:
            query = query.where(ResearchShortlistCandidate.config_id == config_id)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(
            query.order_by(
                ResearchShortlistCandidate.created_at.desc(), ResearchShortlistCandidate.id
            )
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "version": VERSION,
            "experiment_id": experiment_id,
            "archived": experiment.archived,
            "items": [_receipt(store, db, experiment, row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
            "total": total,
        }


def save_candidate(store, owner, experiment_id, data):
    library._object(
        data,
        {"job_id", "config_id", "proposal_number", "expected_result_artifact"},
        "candidate bookmark",
    )
    job_id = _hex(data.get("job_id"), 32, "result")
    expected_result = (
        _hex(data["expected_result_artifact"], 64, "displayed result evidence")
        if "expected_result_artifact" in data
        else None
    )
    config_id = data.get("config_id")
    if config_id is not None:
        _hex(config_id, 64, "configuration")
    with store.sessions() as db:
        experiment, job, config_id = _normal_source(db, owner, experiment_id, job_id, config_id)
        library._editable(experiment)
        # A linked report submitted by the browser must itself have completed.
        requested = db.get(ResearchJob, job_id)
        if requested.status != "completed":
            raise ValueError("Choose a completed backtest or optimization study")
        if expected_result is not None and requested.result_artifact != expected_result:
            raise ValueError("The displayed result changed; reopen it before saving a candidate")
    _, result, selected, origin = _evidence(store, job, config_id, data.get("proposal_number"))
    if requested.id != job.id:
        _, child_result, _, child_origin = _evidence(store, requested, config_id)
        expected = child_result.get("candidate_report", {})
        if (
            child_origin["period"] != origin["period"]
            or expected.get("version") != candidates.VERSION
            or expected.get("study_job_id") != job.id
            or expected.get("parent_result_artifact") != job.result_artifact
            or expected.get("config_id") != config_id
            or expected.get("period") != origin["period"]
            or expected.get("verification", {}).get("summary") != "matched"
            or child_result.get("summary") != selected.get("summary")
        ):
            raise ValueError("The candidate report does not match its original study")
    snapshot = _snapshot(result, selected, origin)
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment, current, _ = _normal_source(db, owner, experiment_id, job_id, config_id)
        library._editable(experiment)
        current_requested = db.get(ResearchJob, requested.id)
        native = db.get(ResearchExperiment, current.id)
        if (
            current.id != job.id
            or current.status != "completed"
            or current.result_artifact != job.result_artifact
            or current_requested.status != "completed"
            or current_requested.result_artifact != requested.result_artifact
            or native is None
            or native.kind
            != ("portfolio_optimize" if origin["origin_kind"] == "study" else "portfolio_backtest")
        ):
            raise ValueError("The saved result changed; reopen it before saving a candidate")
        row = db.scalar(
            select(ResearchShortlistCandidate).where(
                ResearchShortlistCandidate.experiment_id == experiment_id,
                ResearchShortlistCandidate.source_job_id == job.id,
                ResearchShortlistCandidate.source_result_artifact == job.result_artifact,
                ResearchShortlistCandidate.config_id == origin["config_id"],
                ResearchShortlistCandidate.period == origin["period"],
            )
        )
        reused = row is not None
        if row is None:
            count = db.scalar(
                select(func.count())
                .select_from(ResearchShortlistCandidate)
                .where(ResearchShortlistCandidate.experiment_id == experiment_id)
            )
            if (
                count >= MAX_PER_EXPERIMENT
                or db.scalar(select(func.count()).select_from(ResearchShortlistCandidate))
                >= MAX_ROWS
            ):
                raise ValueError("This shortlist is full. Remove a bookmark before saving another.")
            service.ensure_storage_capacity(store, len(snapshot.encode()) + 8192)
            now = time.time()
            suffix = (
                f" · Trial {origin['proposal_number'] + 1}"
                if origin["origin_kind"] == "study"
                else " · Backtest"
            )
            name = experiment.name[: 120 - len(suffix)] + suffix
            matched = result.get("matched_baseline_origin", {})
            if (
                origin["origin_kind"] == "backtest"
                and matched.get("version") == "research-matched-baseline-v1"
                and matched.get("config_id") == origin["config_id"]
                and matched.get("period") == origin["period"]
                and matched.get("verification") == {"settings": "matched", "basis": "matched"}
            ):
                frozen_name = result.get("portfolio", {}).get("name")
                name = (
                    frozen_name.strip()
                    if isinstance(frozen_name, str) and frozen_name.strip()
                    else experiment.name[:101] + " · Matched baseline"
                )[:120]
            row = ResearchShortlistCandidate(
                id=uuid.uuid4().hex,
                owner=owner,
                experiment_id=experiment_id,
                source_job_id=job.id,
                source_result_artifact=job.result_artifact,
                **origin,
                snapshot=snapshot,
                name=name,
                note="",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            experiment.updated_at = now
            db.flush()
        return {"candidate": _receipt(store, db, experiment, row), "reused": reused}


def get_candidate(store, owner, experiment_id, identifier):
    with store.sessions() as db:
        experiment, row = _owned(db, owner, experiment_id, identifier)
        receipt = _receipt(store, db, experiment, row)
        source = db.get(ResearchJob, row.source_job_id)
    response = {
        "candidate": receipt,
        "archived": receipt["archived"],
        "available": False,
        "strategies": None,
        "analysis": None,
        "analysis_catalog": None,
        "report": receipt["report"],
    }
    if receipt["source_available"]:
        try:
            bundle, result, selected, origin = _evidence(
                store, source, row.config_id, row.proposal_number
            )
            if any(origin[key] != getattr(row, key) for key in origin):
                raise ValueError(
                    "The original candidate identity does not match its saved bookmark"
                )
            if row.origin_kind == "study" and receipt["report"]["status"] == "available":
                reason = candidates._availability_error(store, bundle)
                if reason:
                    response["report"] = receipt["report"] = {
                        "status": "unavailable",
                        "error": reason,
                    }
            analysis = selected.get("analysis")
            response.update(
                available=True,
                strategies=selected["strategies"],
                analysis={key: analysis.get(key) for key in ("version", "metrics", "unavailable")}
                if isinstance(analysis, dict)
                else None,
                analysis_catalog=result.get("experiment", {}).get(
                    "analysis_catalog", result.get("analysis", {}).get("catalog")
                ),
            )
            return response
        except (ValueError, OSError, KeyError, TypeError):
            receipt["source_available"] = False
            receipt["source_error"] = SOURCE_ERROR
            response["report"] = receipt["report"] = {
                "status": "unavailable",
                "error": SOURCE_ERROR,
            }
    response["error"] = SOURCE_ERROR
    return response


def _revision(store, db, experiment, row, revision):
    if type(revision) is not int or revision < 1:
        raise ValueError("Supply the saved candidate revision")
    if row.revision != revision:
        raise ShortlistConflict(_receipt(store, db, experiment, row))


def update_candidate(store, owner, experiment_id, identifier, data):
    library._object(data, {"revision", "name", "note"}, "saved candidate changes")
    changes = {}
    if "name" in data:
        changes["name"] = library._text(data["name"], 120, "Candidate name")
    if "note" in data:
        changes["note"] = library._text(data["note"], 2000, "Candidate note", empty=True)
    if not changes:
        raise ValueError("Choose a candidate name or note to change")
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment, row = _owned(db, owner, experiment_id, identifier)
        library._editable(experiment)
        _revision(store, db, experiment, row, data.get("revision"))
        if any(getattr(row, key) != value for key, value in changes.items()):
            growth = sum(
                max(0, len(value.encode()) - len(getattr(row, key).encode()))
                for key, value in changes.items()
            )
            if growth:
                service.ensure_storage_capacity(store, growth + 4096)
            for key, value in changes.items():
                setattr(row, key, value)
            row.revision += 1
            row.updated_at = experiment.updated_at = time.time()
        return _receipt(store, db, experiment, row)


def remove_candidate(store, owner, experiment_id, identifier, data):
    library._object(data, {"revision"}, "candidate bookmark removal")
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment, row = _owned(db, owner, experiment_id, identifier)
        library._editable(experiment)
        _revision(store, db, experiment, row, data.get("revision"))
        db.delete(row)
        experiment.updated_at = time.time()
    return {"removed": True, "id": identifier}
