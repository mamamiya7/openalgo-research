"""Immutable comparisons of exact saved reports, with bounded editable metadata."""

import copy
import hashlib
import json
import math
import re
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import defer

from database.research_db import ResearchComparison, ResearchExperiment, ResearchJob
from research.report_contract import present_report
from services import research_analysis as analysis_service
from services import research_candidates as candidate_service
from services import research_library as library
from services import research_shortlist as shortlist
from services import scanner_research_service as service

VERSION = "research-comparison-v1"
MAX_BODY_BYTES = 8192
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_PER_EXPERIMENT = 1000
MAX_ROWS = 100000
MEMBER_FIELDS = (
    "id",
    "name",
    "note",
    "origin_kind",
    "source_job_id",
    "source_result_artifact",
    "config_id",
    "period",
    "trial_number",
    "proposal_number",
    "is_objective_winner",
)
UNAVAILABLE = (
    "This comparison's pinned report is unavailable. Its saved values and identity are retained."
)


class ComparisonConflict(ValueError):
    def __init__(self, current):
        super().__init__("This comparison changed in another session. Reload before editing.")
        self.current = current


class ComparisonRequestConflict(ValueError):
    pass


def _owned(db, owner, experiment_id, identifier):
    experiment = library._owned(db, owner, experiment_id)
    row = db.get(ResearchComparison, identifier)
    if row is None or row.owner != owner or row.experiment_id != experiment_id:
        raise LookupError("Saved comparison not found")
    return experiment, row


def _summary(experiment, row):
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "experiment_id",
                "number",
                "name",
                "note",
                "revision",
                "created_at",
                "updated_at",
                "member_count",
                "reference_member_id",
                "compatible",
                "currency",
            )
        },
        "member_names": json.loads(row.member_names),
        "archived": experiment.archived,
    }


def _member_owned(db, owner, member):
    expected = (
        (
            "source_job_id",
            "portfolio_optimize" if member["origin_kind"] == "study" else "portfolio_backtest",
        ),
        (
            "report_job_id",
            "portfolio_optimize"
            if member["report_job_id"] == member["source_job_id"]
            and member["origin_kind"] == "study"
            else "portfolio_backtest",
        ),
    )
    for key, kind in expected:
        job = db.get(ResearchJob, member[key])
        metadata = db.get(ResearchExperiment, member[key])
        if not job or job.owner != owner or not metadata or metadata.kind != kind:
            return False
    if member["analysis_job_id"]:
        job = db.get(ResearchJob, member["analysis_job_id"])
        metadata = db.get(ResearchExperiment, member["analysis_job_id"])
        if (
            not job
            or job.owner != owner
            or not metadata
            or metadata.kind != "portfolio_analysis"
            or metadata.parent_job_id != member["report_job_id"]
        ):
            return False
    return True


def _member_receipt(store, db, owner, member):
    available = _member_owned(db, owner, member) and all(
        shortlist._artifact_present(store, member[key])
        for key in ("source_result_artifact", "report_result_artifact", "analysis_artifact")
        if member.get(key) is not None
    )
    return {**member, "available": available, **({"error": UNAVAILABLE} if not available else {})}


def _receipt(store, db, experiment, row):
    snapshot = json.loads(row.snapshot)
    return {
        **_summary(experiment, row),
        "version": VERSION,
        "members": [_member_receipt(store, db, row.owner, item) for item in snapshot["members"]],
        **{
            key: copy.deepcopy(snapshot["presentation"][key])
            for key in ("differences", "metrics", "cumulative")
        },
    }


def list_comparisons(store, owner, experiment_id, *, limit=20, offset=0):
    limit, offset = library._page(limit, offset)
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        criteria = (
            ResearchComparison.owner == owner,
            ResearchComparison.experiment_id == experiment_id,
        )
        total = db.scalar(select(func.count()).select_from(ResearchComparison).where(*criteria))
        rows = db.scalars(
            select(ResearchComparison)
            .options(defer(ResearchComparison.snapshot))
            .where(*criteria)
            .order_by(ResearchComparison.created_at.desc(), ResearchComparison.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "items": [_summary(experiment, row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
            "total": total,
        }


def _selected_member(store, owner, experiment_id, identifier):
    with store.sessions() as db:
        experiment, candidate = shortlist._owned(db, owner, experiment_id, identifier)
        library._editable(experiment)
        receipt = shortlist._receipt(store, db, experiment, candidate)
        source = db.get(ResearchJob, candidate.source_job_id)
        if not receipt["source_available"] or receipt["report"]["status"] != "ready":
            raise ValueError("Prepare every selected candidate's report before comparing")
        report_job = db.get(ResearchJob, receipt["report"]["report_job_id"])
        if (
            not report_job
            or report_job.owner != owner
            or report_job.status != "completed"
            or not report_job.result_artifact
        ):
            raise ValueError("Choose complete saved reports for comparison")
        member = {key: getattr(candidate, key) for key in MEMBER_FIELDS}
        member.update(
            report_job_id=report_job.id, report_result_artifact=report_job.result_artifact
        )
        member["job"] = {
            "id": report_job.id,
            "status": "completed",
            "progress": 100,
            "created_at": report_job.created_at,
            "kind": "portfolio_optimize"
            if candidate.origin_kind == "study" and source.id == report_job.id
            else "portfolio_backtest",
        }
        revision = candidate.revision
    member, inputs = pin_member(store, owner, experiment_id, member, source, report_job)
    return member, inputs, revision


def pin_member(
    store, owner, experiment_id, member, source, report_job, *, include_presentation=True
):
    """Pin one canonical selection report for comparisons or direct decisions."""
    # Validate the exact candidate behind the bookmark; reduce its source before
    # reading another potentially large full report.
    bundle, source_result, selected, origin = shortlist._evidence(
        store, source, member["config_id"], member["proposal_number"]
    )
    if any(origin[key] != member[key] for key in origin):
        raise ValueError("The saved candidate does not match its original source")
    summary = copy.deepcopy(selected["summary"])
    expected_basis = copy.deepcopy(source_result.get("evaluation_basis"))
    expected_analysis = copy.deepcopy(selected.get("analysis"))
    if source.id == report_job.id:
        original_bundle = bundle
    else:
        del bundle, selected, source_result
        original_bundle = service.read_artifact(store, report_job.result_artifact)
    original = original_bundle.get("result")
    if not isinstance(original, dict) or original.get("summary") != summary:
        raise ValueError("The candidate report does not match its saved statistics")
    if source.id != report_job.id:
        with store.sessions() as db:
            _, parent, config = shortlist._normal_source(
                db, owner, experiment_id, report_job.id, member["config_id"]
            )
            if (
                parent.id != source.id
                or parent.result_artifact != member["source_result_artifact"]
                or config != member["config_id"]
            ):
                raise ValueError("The report is not the retained exact candidate")
        proof = original.get("candidate_report", {})
        expected = {
            "version": candidate_service.VERSION,
            "study_job_id": source.id,
            "parent_result_artifact": member["source_result_artifact"],
            "config_id": member["config_id"],
            "period": member["period"],
        }
        if (
            any(proof.get(key) != value for key, value in expected.items())
            or proof.get("verification", {}).get("summary") != "matched"
        ):
            raise ValueError("The report is not the retained exact candidate")
        from research.evaluation_basis import comparison_status

        if (
            expected_basis
            and expected_basis.get("status") == "verified"
            and not comparison_status(expected_basis, original.get("evaluation_basis"))[
                "compatible"
            ]
        ):
            raise ValueError("The candidate report does not match its recorded evaluation data")
        actual_analysis = original.get("analysis", {})
        if (
            expected_analysis
            and expected_analysis.get("version") == actual_analysis.get("version")
            and any(
                expected_analysis.get(key) != actual_analysis.get(key)
                for key in ("metrics", "unavailable")
            )
        ):
            raise ValueError("The candidate report does not match its recorded analysis")
    overlay = analysis_service.saved_overlay(store, report_job, original)
    result = present_report(
        overlay,
        job_id=report_job.id,
        result_artifact=report_job.result_artifact,
        inputs_artifact=original_bundle.get("inputs_artifact"),
    )
    context = result["report_context"]
    if context["config_id"] != member["config_id"] or context["period"] != member["period"]:
        raise ValueError("The candidate report does not match its configuration or period")
    analysis_artifact = context["analysis_artifact"]
    analysis_job_id = None
    if analysis_artifact is not None:
        with store.sessions() as db:
            pinned = db.scalar(
                select(ResearchJob)
                .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
                .where(
                    ResearchJob.owner == owner,
                    ResearchJob.status == "completed",
                    ResearchJob.result_artifact == analysis_artifact,
                    ResearchExperiment.kind == "portfolio_analysis",
                    ResearchExperiment.parent_job_id == report_job.id,
                )
                .order_by(ResearchJob.created_at.desc(), ResearchJob.id.desc())
                .limit(1)
            )
            if pinned is None:
                raise ValueError(
                    "The displayed analysis changed; reopen the report before comparing"
                )
            analysis_job_id = pinned.id
        # This stricter read also confirms kind and exact parent artifact.
        analysis_service.pinned_overlay(
            store,
            original,
            parent_result_artifact=report_job.result_artifact,
            analysis_artifact=analysis_artifact,
        )
    member.update(
        analysis_job_id=analysis_job_id,
        analysis_artifact=analysis_artifact,
        report_context=copy.deepcopy(context),
        summary=summary,
    )
    if not include_presentation:
        return member, None
    analysis = result.get("analysis")
    inputs = {
        "id": member["id"],
        "name": member["name"],
        "report_context": member["report_context"],
        "summary": summary,
        "analysis": {
            key: copy.deepcopy(analysis.get(key))
            for key in ("version", "metrics", "unavailable", "catalog")
        }
        if isinstance(analysis, dict)
        else None,
        "cumulative": copy.deepcopy(
            next(
                (
                    chart
                    for chart in (analysis or {}).get("charts", [])
                    if chart.get("id") == "account-cumulative"
                ),
                None,
            )
        ),
    }
    return member, inputs


def _request(data):
    library._object(
        data, {"request_id", "candidate_ids", "reference_candidate_id"}, "comparison request"
    )
    token = data.get("request_id")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token):
        raise ValueError("Supply a comparison retry identity")
    identifiers = data.get("candidate_ids")
    if not isinstance(identifiers, list) or not 2 <= len(identifiers) <= 4:
        raise ValueError("Choose two to four saved candidates")
    for identifier in identifiers:
        shortlist._hex(identifier, 32, "candidate")
    if (
        len(set(identifiers)) != len(identifiers)
        or data.get("reference_candidate_id") not in identifiers
    ):
        raise ValueError("Choose distinct candidates and a reference from this selection")
    return token


def _prior(db, owner, experiment_id, token, digest):
    row = db.scalar(
        select(ResearchComparison).where(
            ResearchComparison.owner == owner, ResearchComparison.request_id == token
        )
    )
    if row and (row.experiment_id != experiment_id or row.request_hash != digest):
        raise ComparisonRequestConflict(
            "This comparison retry identity was used for another selection"
        )
    return row


def create_comparison(store, owner, experiment_id, data):
    from research.comparison import build_comparison

    token = _request(data)
    digest = hashlib.sha256(
        service.encoded(
            {
                "experiment_id": experiment_id,
                "candidate_ids": data["candidate_ids"],
                "reference_candidate_id": data["reference_candidate_id"],
            }
        )
    ).hexdigest()
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, experiment_id, token, digest)
        if prior:
            return {"comparison": _receipt(store, db, experiment, prior), "reused": True}
        library._editable(experiment)
    members, presentation_inputs, revisions = [], [], {}
    for identifier in data["candidate_ids"]:
        member, inputs, revision = _selected_member(store, owner, experiment_id, identifier)
        members.append(member)
        presentation_inputs.append(inputs)
        revisions[identifier] = revision
    presentation = build_comparison(presentation_inputs, data["reference_candidate_id"])
    snapshot = {
        "version": VERSION,
        "members": members,
        "presentation": presentation,
        "reference_member_id": data["reference_candidate_id"],
    }
    raw = validate_snapshot(snapshot)
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, experiment_id, token, digest)
        if prior:
            return {"comparison": _receipt(store, db, experiment, prior), "reused": True}
        library._editable(experiment)
        for member in members:
            _, current = shortlist._owned(db, owner, experiment_id, member["id"])
            if current.revision != revisions[member["id"]] or any(
                getattr(current, key) != member[key] for key in MEMBER_FIELDS
            ):
                raise ValueError(
                    "A selected candidate changed; reopen the selection before comparing"
                )
            if not _member_owned(db, owner, member):
                raise ValueError("A selected report changed; reopen it before comparing")
            if member["source_job_id"] != member["report_job_id"]:
                _, parent, config = shortlist._normal_source(
                    db, owner, experiment_id, member["report_job_id"], member["config_id"]
                )
                if (
                    parent.id != member["source_job_id"]
                    or parent.result_artifact != member["source_result_artifact"]
                    or config != member["config_id"]
                ):
                    raise ValueError(
                        "A retained candidate report link changed; reopen before comparing"
                    )
            for job_key, artifact_key in (
                ("source_job_id", "source_result_artifact"),
                ("report_job_id", "report_result_artifact"),
                ("analysis_job_id", "analysis_artifact"),
            ):
                if member[job_key]:
                    job = db.get(ResearchJob, member[job_key])
                    if job.status != "completed" or job.result_artifact != member[artifact_key]:
                        raise ValueError("A selected report changed; reopen it before comparing")
        if (
            db.scalar(
                select(func.count())
                .select_from(ResearchComparison)
                .where(ResearchComparison.experiment_id == experiment_id)
            )
            >= MAX_PER_EXPERIMENT
            or db.scalar(select(func.count()).select_from(ResearchComparison)) >= MAX_ROWS
        ):
            raise ValueError("This experiment has reached its saved comparison limit")
        service.ensure_storage_capacity(store, len(raw.encode()) + 16384)
        number = (
            db.scalar(
                select(func.max(ResearchComparison.number)).where(
                    ResearchComparison.experiment_id == experiment_id
                )
            )
            or 0
        ) + 1
        suffix = f" · Comparison {number}"
        now = time.time()
        row = ResearchComparison(
            id=uuid.uuid4().hex,
            owner=owner,
            experiment_id=experiment_id,
            number=number,
            name=experiment.name[: 120 - len(suffix)] + suffix,
            note="",
            revision=1,
            request_id=token,
            request_hash=digest,
            member_count=len(members),
            member_names=service.encoded([item["name"] for item in members]).decode(),
            reference_member_id=data["reference_candidate_id"],
            compatible=presentation["compatible"],
            currency=presentation["currency"],
            snapshot=raw,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        experiment.updated_at = now
        return {"comparison": _receipt(store, db, experiment, row), "reused": False}


def get_comparison(store, owner, experiment_id, identifier):
    with store.sessions() as db:
        experiment, row = _owned(db, owner, experiment_id, identifier)
        return _receipt(store, db, experiment, row)


def update_comparison(store, owner, experiment_id, identifier, data):
    library._object(data, {"revision", "name", "note"}, "comparison changes")
    changes = {
        key: library._text(data[key], limit, f"Comparison {key}", empty=key == "note")
        for key, limit in (("name", 120), ("note", 2000))
        if key in data
    }
    if not changes or type(data.get("revision")) is not int or data["revision"] < 1:
        raise ValueError("Supply the saved comparison revision and a name or note")
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment, row = _owned(db, owner, experiment_id, identifier)
        library._editable(experiment)
        if row.revision != data["revision"]:
            raise ComparisonConflict(_receipt(store, db, experiment, row))
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


def get_member_report(store, owner, experiment_id, identifier, member_id):
    with store.sessions() as db:
        _, row = _owned(db, owner, experiment_id, identifier)
        member = next(
            (item for item in json.loads(row.snapshot)["members"] if item["id"] == member_id), None
        )
        if member is None:
            raise LookupError("Comparison member not found")
        receipt = _member_receipt(store, db, owner, member)
    response = {"member": receipt, "available": False, "job": None, "result": None}
    if receipt["available"]:
        try:
            bundle = service.read_artifact(store, member["report_result_artifact"])
            result = analysis_service.pinned_overlay(
                store,
                bundle["result"],
                parent_result_artifact=member["report_result_artifact"],
                analysis_artifact=member["analysis_artifact"],
            )
            result = present_report(
                result,
                job_id=member["report_job_id"],
                result_artifact=member["report_result_artifact"],
                inputs_artifact=bundle.get("inputs_artifact"),
            )
            if (
                result["report_context"] != member["report_context"]
                or result.get("summary") != member["summary"]
            ):
                raise ValueError("Pinned comparison evidence does not match its saved identity")
            result = {
                key: value
                for key, value in result.items()
                if key not in ("experiment", "validation", "reserved_evaluation")
            }
            response.update(available=True, job=member["job"], result=result)
            return response
        except (ValueError, OSError, KeyError, TypeError):
            pass
    response["member"] = {**receipt, "available": False, "error": UNAVAILABLE}
    response["error"] = UNAVAILABLE
    return response


def validate_member(member):
    """Bound a single pinned selection without requiring a comparison."""

    def finite(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False

    if not isinstance(member, dict) or set(member) != set(MEMBER_FIELDS) | {
        "report_job_id",
        "report_result_artifact",
        "analysis_job_id",
        "analysis_artifact",
        "report_context",
        "summary",
        "job",
    }:
        raise ValueError("Invalid comparison member")
    for key in ("id", "source_job_id", "report_job_id"):
        shortlist._hex(member.get(key), 32, "comparison member")
    for key in ("source_result_artifact", "report_result_artifact", "config_id"):
        shortlist._hex(member.get(key), 64, "comparison evidence")
    if bool(member.get("analysis_job_id")) != bool(member.get("analysis_artifact")):
        raise ValueError("Incomplete pinned comparison analysis")
    if member.get("analysis_artifact"):
        shortlist._hex(member["analysis_job_id"], 32, "analysis job")
        shortlist._hex(member["analysis_artifact"], 64, "analysis artifact")
    if member.get("origin_kind") not in ("study", "backtest") or member.get("period") not in (
        "selection",
        "full",
    ):
        raise ValueError("Invalid comparison member identity")
    if (
        type(member.get("is_objective_winner")) is not bool
        or (
            member["origin_kind"] == "study"
            and any(
                type(member.get(key)) is not int or not 0 <= member[key] < 1000000
                for key in ("trial_number", "proposal_number")
            )
        )
        or (
            member["origin_kind"] == "backtest"
            and (
                member.get("trial_number") is not None
                or member.get("proposal_number") is not None
                or member["is_objective_winner"]
            )
        )
    ):
        raise ValueError("Invalid comparison proposal identity")
    library._text(member.get("name"), 120, "Comparison member name")
    library._text(member.get("note"), 2000, "Comparison member note", empty=True)
    context = member.get("report_context")
    if not isinstance(context, dict) or any(
        context.get(key) != member[field]
        for key, field in (
            ("job_id", "report_job_id"),
            ("result_artifact", "report_result_artifact"),
            ("analysis_artifact", "analysis_artifact"),
            ("config_id", "config_id"),
            ("period", "period"),
        )
    ):
        raise ValueError("Invalid pinned comparison report context")
    from research.report_contract import fingerprint

    if (
        context.get("version") != "research-report-context-v1"
        or context.get("report_id")
        != fingerprint(
            {key: context[key] for key in ("version", "result_artifact", "period", "config_id")}
        )
        or not isinstance(context.get("evaluation_basis"), dict)
        or context.get("analysis_version") is not None
        and (
            not isinstance(context["analysis_version"], str)
            or len(context["analysis_version"]) > 64
        )
    ):
        raise ValueError("Invalid pinned comparison report descriptor")
    if context.get("inputs_artifact") is not None:
        shortlist._hex(context["inputs_artifact"], 64, "report inputs")
    shortlist.validate_snapshot(
        {
            "summary": member.get("summary"),
            "dates": context.get("dates"),
            "capital": None,
            "currency": None,
            "engine": None,
            "objective": None,
        }
    )
    job = member.get("job")
    kind = (
        "portfolio_optimize"
        if member["origin_kind"] == "study" and member["report_job_id"] == member["source_job_id"]
        else "portfolio_backtest"
    )
    if (
        not isinstance(job, dict)
        or set(job) != {"id", "status", "progress", "created_at", "kind"}
        or job["id"] != member["report_job_id"]
        or job["status"] != "completed"
        or job["progress"] != 100
        or not finite(job["created_at"])
        or job["created_at"] <= 0
        or job["kind"] != kind
    ):
        raise ValueError("Invalid frozen comparison job")


def validate_snapshot(snapshot):
    """Bound nested immutable metadata before writing or restoring it."""
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != {"version", "members", "presentation", "reference_member_id"}
        or snapshot["version"] != VERSION
    ):
        raise ValueError("Invalid saved comparison snapshot")
    members = snapshot["members"]
    if not isinstance(members, list) or not 2 <= len(members) <= 4:
        raise ValueError("Invalid comparison member count")
    ids = set()

    def finite(value):
        try:
            return (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        except OverflowError:
            return False

    def scalar(value, text=False):
        return value is None or (
            isinstance(value, str) and len(value) <= 4000 if text else finite(value)
        )

    for member in members:
        validate_member(member)
        if member["id"] in ids:
            raise ValueError("Duplicate comparison member identity")
        ids.add(member["id"])
    if snapshot["reference_member_id"] not in ids:
        raise ValueError("Invalid frozen comparison reference")
    presentation = snapshot["presentation"]
    if (
        not isinstance(presentation, dict)
        or set(presentation)
        != {"version", "currency", "compatible", "differences", "metrics", "cumulative"}
        or presentation.get("version") != "research-comparison-presentation-v1"
        or type(presentation.get("compatible")) is not bool
    ):
        raise ValueError("Invalid comparison presentation")
    currency = presentation["currency"]
    if (
        currency is not None
        and (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency))
        or presentation["compatible"]
        and currency is None
    ):
        raise ValueError("Invalid comparison currency")
    differences = presentation["differences"]
    codes = {
        "source",
        "cohort",
        "observations",
        "prices",
        "calendar",
        "instruments",
        "period",
        "currency",
        "capital",
        "execution",
        "costs",
        "unverified",
        "evidence",
        "comparison_context",
    }
    if (
        not isinstance(differences, list)
        or len(differences) > 4
        or any(
            not isinstance(item, dict)
            or set(item) != {"member_id", "codes"}
            or item["member_id"] not in ids
            or not isinstance(item["codes"], list)
            or not item["codes"]
            or len(item["codes"]) > len(codes)
            or any(not isinstance(code, str) or code not in codes for code in item["codes"])
            for item in differences
        )
        or len({item["member_id"] for item in differences}) != len(differences)
        or presentation["compatible"] != (not differences)
    ):
        raise ValueError("Invalid comparison differences")
    metrics = presentation.get("metrics")
    if not isinstance(metrics, list) or len(metrics) > 512:
        raise ValueError("Comparison metrics exceed their size limit")
    for metric in metrics:
        if (
            not isinstance(metric, dict)
            or not isinstance(metric.get("values"), dict)
            or set(metric["values"]) != ids
        ):
            raise ValueError("Invalid comparison metric members")
        if (
            set(metric)
            != {
                "key",
                "label",
                "format",
                "source",
                "description",
                "values",
                "deltas",
                "unavailable",
            }
            or metric.get("format") not in ("percent", "money", "number", "text")
            or metric.get("source") not in ("summary", "analysis")
            or any(
                not isinstance(metric.get(key), str) or not 0 < len(metric[key]) <= limit
                for key, limit in (("key", 256), ("label", 256))
            )
            or not isinstance(metric.get("description"), str)
            or len(metric["description"]) > 4200
            or any(
                not scalar(value, metric["format"] == "text") for value in metric["values"].values()
            )
            or not isinstance(metric.get("unavailable"), dict)
            or not set(metric["unavailable"]).issubset(ids)
            or any(
                not isinstance(value, str) or len(value) > 4000
                for value in metric["unavailable"].values()
            )
            or currency is None
            and metric["format"] == "money"
            and any(value is not None for value in metric["values"].values())
        ):
            raise ValueError("Invalid saved comparison metric")
        deltas = metric["deltas"]
        if deltas is not None and (
            not presentation["compatible"]
            or metric["format"] == "text"
            or not isinstance(deltas, dict)
            or set(deltas) != ids
            or any(not scalar(value) for value in deltas.values())
        ):
            raise ValueError("Invalid saved comparison deltas")
    chart = presentation.get("cumulative")
    if not isinstance(chart, dict) or chart.get("status") not in ("available", "unavailable"):
        raise ValueError("Invalid comparison chart")
    if (
        chart["status"] == "available"
        and not presentation["compatible"]
        or chart["status"] == "unavailable"
        and "figure" in chart
    ):
        raise ValueError("An incompatible comparison cannot overlay curves")
    traces = chart.get("figure", {}).get("data", [])
    if (
        not isinstance(traces, list)
        or len(traces) > 4
        or any(
            not isinstance(trace, dict)
            or not isinstance(trace.get("x"), list)
            or not isinstance(trace.get("y"), list)
            or len(trace["x"]) != len(trace["y"])
            or len(trace["x"]) > 1200
            or any(not isinstance(value, str) or len(value) > 64 for value in trace["x"])
            or any(not scalar(value) for value in trace["y"])
            for trace in traces
        )
    ):
        raise ValueError("Comparison chart exceeds its size limit")
    # Bound depth and node count before encoding; frozen presentation is small,
    # not an arbitrary report or code/strategy payload.
    pending, count = [(snapshot, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > 60000 or depth > 20:
            raise ValueError("Saved comparison metadata is too complex")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    raw = service.encoded(snapshot).decode()
    if len(raw.encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Saved comparison exceeds its size limit")
    return raw
