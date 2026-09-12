"""Append-only user decisions and explicit openings of retained research evidence."""

import copy
import hashlib
import json
import math
import re
import time
import uuid

from sqlalchemy import func, select

from database.research_db import (
    ResearchComparison,
    ResearchDecision,
    ResearchDecisionEvent,
    ResearchDecisionRequest,
    ResearchEvidenceOpen,
    ResearchExperiment,
    ResearchHistory,
    ResearchJob,
    ResearchLibraryJob,
    ResearchSetupVersion,
)
from research.report_contract import fingerprint, present_report, settings_identity
from services import research_analysis as analysis
from services import research_comparisons as comparisons
from services import research_library as library
from services import research_shortlist as shortlist
from services import scanner_research_service as service

MAX_BODY_BYTES = 8192
MAX_SNAPSHOT_BYTES = 65536
MAX_ROWS = 100000
MAX_PER_EXPERIMENT = 1000
MAX_EVENTS = 1000
STATES = ("keep", "reject", "revisit")
IDENTITY = ("source_job_id", "source_result_artifact", "config_id", "period")
UNAVAILABLE = (
    "This decision's exact report is unavailable. Its identity and decision history are retained."
)


class DecisionConflict(ValueError):
    def __init__(self, current):
        super().__init__("This decision changed in another session. Reload before saving.")
        self.current = current


class DecisionRequestConflict(ValueError):
    pass


class DecisionEvidenceChanged(ValueError):
    pass


def _member(db, owner, experiment_id, comparison_id, member_id):
    shortlist._hex(comparison_id, 32, "comparison")
    shortlist._hex(member_id, 32, "comparison member")
    experiment, comparison = comparisons._owned(db, owner, experiment_id, comparison_id)
    member = next(
        (item for item in json.loads(comparison.snapshot)["members"] if item["id"] == member_id),
        None,
    )
    if member is None:
        raise LookupError("Comparison member not found")
    return experiment, comparison, member


def _head(db, owner, experiment_id, member):
    return db.scalar(
        select(ResearchDecision).where(
            ResearchDecision.owner == owner,
            ResearchDecision.experiment_id == experiment_id,
            *(getattr(ResearchDecision, key) == member[key] for key in IDENTITY),
        )
    )


def _owned(db, owner, experiment_id, identifier):
    shortlist._hex(identifier, 32, "decision")
    experiment = library._owned(db, owner, experiment_id)
    row = db.get(ResearchDecision, identifier)
    if row is None or row.owner != owner or row.experiment_id != experiment_id:
        raise LookupError("Saved decision not found")
    return experiment, row


def _event(db, owner, experiment_id, identifier, event_id):
    shortlist._hex(event_id, 32, "decision history entry")
    experiment, head = _owned(db, owner, experiment_id, identifier)
    row = db.get(ResearchDecisionEvent, event_id)
    if (
        row is None
        or row.decision_id != head.id
        or row.owner != owner
        or row.experiment_id != experiment_id
    ):
        raise LookupError("Decision history entry not found")
    return experiment, head, row


def _request(data, kind, experiment_id):
    token = data.get("request_id")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", token):
        raise ValueError("Supply a valid request identity")
    payload = {
        "kind": kind,
        "experiment_id": experiment_id,
        **{k: v for k, v in data.items() if k != "request_id"},
    }
    raw = service.encoded(payload)
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("Decision request exceeds its size limit")
    return token, raw.decode(), hashlib.sha256(raw).hexdigest()


def _previous(db, owner, token, digest):
    previous = db.get(ResearchDecisionRequest, (owner, token))
    if previous and previous.payload_hash != digest:
        raise DecisionRequestConflict("This request identity was already used for another action")
    return previous


def _bound(db, model, *, where=(), limit=MAX_ROWS):
    if db.scalar(select(func.count()).select_from(model).where(*where)) >= limit:
        raise ValueError("Saved research history reached its storage limit")


def _remember(db, owner, experiment_id, token, raw, digest, kind, result_id):
    _bound(db, ResearchDecisionRequest)
    db.add(
        ResearchDecisionRequest(
            owner=owner,
            token=token,
            experiment_id=experiment_id,
            kind=kind,
            result_id=result_id,
            payload=raw,
            payload_hash=digest,
        )
    )


def _selection_pin(member):
    return {
        "job_id": member["report_job_id"],
        "result_artifact": member["report_result_artifact"],
        "inputs_artifact": member["report_context"]["inputs_artifact"],
        "analysis_job_id": member["analysis_job_id"],
        "analysis_artifact": member["analysis_artifact"],
        "view": "primary",
        "origin": None,
        "report_context": copy.deepcopy(member["report_context"]),
        "job": copy.deepcopy(member["job"]),
    }


def _pin_id(pin):
    return ".".join(
        (pin["job_id"], pin["result_artifact"], pin["analysis_artifact"] or "original", pin["view"])
    )


def _pin_parts(identifier):
    if not isinstance(identifier, str) or len(identifier) > 200:
        raise ValueError("Choose exact saved later evidence")
    pieces = identifier.split(".")
    if len(pieces) != 4 or pieces[3] not in ("primary", "embedded_later"):
        raise ValueError("Choose exact saved later evidence")
    shortlist._hex(pieces[0], 32, "evaluation job")
    shortlist._hex(pieces[1], 64, "evaluation artifact")
    if pieces[2] != "original":
        shortlist._hex(pieces[2], 64, "evaluation analysis")
    return pieces


def _pin_owned(db, owner, pin):
    job = db.get(ResearchJob, pin["job_id"])
    metadata = db.get(ResearchExperiment, pin["job_id"])
    if (
        not job
        or job.owner != owner
        or not metadata
        or metadata.kind not in ("portfolio_backtest", "portfolio_optimize")
    ):
        return False
    if pin["analysis_job_id"]:
        extra = db.get(ResearchJob, pin["analysis_job_id"])
        spec = db.get(ResearchExperiment, pin["analysis_job_id"])
        if (
            not extra
            or extra.owner != owner
            or not spec
            or spec.kind != "portfolio_analysis"
            or spec.parent_job_id != job.id
        ):
            return False
    return True


def _pin_available(store, db, owner, pin):
    return _pin_owned(db, owner, pin) and all(
        shortlist._artifact_present(store, pin[key])
        for key in ("result_artifact", "inputs_artifact", "analysis_artifact")
        if pin.get(key)
    )


def _later_receipt(store, db, owner, pin):
    available = _pin_available(store, db, owner, pin)
    return {
        "id": _pin_id(pin),
        "job_id": pin["job_id"],
        "report_id": pin["report_context"]["report_id"],
        "view": pin["view"],
        "dates": copy.deepcopy(pin["report_context"]["dates"]),
        "available": available,
        **({"error": UNAVAILABLE} if not available else {}),
    }


def _event_receipt(store, db, row):
    head = db.get(ResearchDecision, row.decision_id)
    _, _, member = _member(db, row.owner, row.experiment_id, row.comparison_id, row.member_id)
    pin = json.loads(row.evaluation_pin) if row.evaluation_pin else None
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "decision_id",
                "revision",
                "supersedes_event_id",
                "state",
                "reason",
                "created_at",
                "candidate_name",
                "comparison_name",
                "comparison_id",
                "member_id",
            )
        },
        **{key: getattr(head, key) for key in IDENTITY},
        **{key: member[key] for key in ("trial_number", "proposal_number", "is_objective_winner")},
        "evaluation": _later_receipt(store, db, row.owner, pin) if pin else None,
        "evidence_use": json.loads(row.evidence_use),
    }


def _summary(store, db, experiment, head, *, event=None):
    event = event or db.get(ResearchDecisionEvent, head.current_event_id)
    return {
        "id": head.id,
        "revision": event.revision,
        "current": _event_receipt(store, db, event),
        "archived": experiment.archived,
    }


def _source(store, member):
    bundle = service.read_artifact(store, member["source_result_artifact"])
    if bundle.get("job_id") not in (None, member["source_job_id"]) or not isinstance(
        bundle.get("result"), dict
    ):
        raise ValueError("The candidate's original evidence is unavailable")
    return {
        **{key: bundle[key] for key in ("kind", "inputs_artifact")},
        "result": _context_result(bundle["result"]),
    }


def _context_result(result):
    """Release full financial series before reading another evidence artifact."""
    reduced = {
        key: result[key]
        for key in (
            "strategies",
            "portfolio",
            "coverage",
            "reserved_evaluation",
            "replay_origin",
            "evaluation_basis",
        )
        if key in result
    }
    if result.get("analysis"):
        reduced["analysis"] = {"version": result["analysis"].get("version")}
    if result.get("experiment"):
        study = result["experiment"]
        reduced["experiment"] = {
            "recommendation_id": study.get("recommendation_id"),
            "rows": [
                {key: row[key] for key in ("config_id", "trial_number") if key in row}
                for row in study.get("rows", [])
            ],
        }
    if result.get("validation"):
        reduced["validation"] = {
            key: value for key, value in result["validation"].items() if key != "result"
        }
        reduced["validation"]["result"] = _context_result(result["validation"]["result"])
    return reduced


def _reservation(result):
    recorded = result.get("reserved_evaluation")
    if isinstance(recorded, dict) and isinstance(recorded.get("evaluation"), dict):
        return copy.deepcopy(recorded["evaluation"])
    legacy = result.get("validation")
    if isinstance(legacy, dict) and legacy.get("test_from") and legacy.get("test_to"):
        return {"from": legacy["test_from"], "to": legacy["test_to"]}
    return None


def _later_query(experiment_id, member):
    return (
        select(ResearchJob)
        .join(ResearchLibraryJob, ResearchLibraryJob.job_id == ResearchJob.id)
        .join(ResearchSetupVersion, ResearchSetupVersion.id == ResearchLibraryJob.version_id)
        .where(
            ResearchLibraryJob.experiment_id == experiment_id,
            ResearchLibraryJob.role == "validation",
            ResearchSetupVersion.parent_job_id == member["source_job_id"],
            ResearchSetupVersion.parent_result_artifact == member["source_result_artifact"],
            ResearchJob.status == "completed",
            ResearchJob.result_artifact.is_not(None),
        )
    )


def _later_pin(store, owner, experiment_id, member, job_id, *, embedded=False, source_bundle=None):
    """Admit only an actual canonical-candidate replay of its retained later range."""
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        if not job or job.owner != owner or job.status != "completed" or not job.result_artifact:
            raise ValueError("Choose a completed later evaluation")
        if embedded:
            if (
                job_id != member["source_job_id"]
                or job.result_artifact != member["source_result_artifact"]
            ):
                raise ValueError("Original later evidence changed")
        elif db.scalar(_later_query(experiment_id, member).where(ResearchJob.id == job_id)) is None:
            raise ValueError("This evaluation is not linked to this exact candidate source")
    source_bundle = source_bundle or _source(store, member)
    reservation = _reservation(source_bundle["result"])
    if member["period"] != "selection" or not reservation:
        raise ValueError("This candidate has no recorded later period")
    bundle = source_bundle if embedded else service.read_artifact(store, job.result_artifact)
    original = _context_result(bundle["result"])
    bundle = {
        "kind": bundle["kind"],
        "inputs_artifact": bundle["inputs_artifact"],
        "result": original,
    }
    if embedded:
        selected = original.get("validation", {}).get("result")
        if (
            not isinstance(selected, dict)
            or settings_identity(selected.get("strategies")) != member["config_id"]
        ):
            raise ValueError("Later evidence belongs to a different candidate")
    else:
        selected = original
        origin = selected.get("replay_origin", {})
        if any(
            origin.get(key) != value
            for key, value in {
                "parent_job_id": member["source_job_id"],
                "parent_result_artifact": member["source_result_artifact"],
                "config_id": member["config_id"],
                "period": "evaluation",
            }.items()
        ):
            raise ValueError("Later evidence does not prove this candidate's frozen origin")
        if settings_identity(selected.get("strategies")) != member["config_id"]:
            raise ValueError("Later settings do not match this candidate")
        evidence = service.read_artifact(store, bundle["inputs_artifact"])
        if (
            evidence.get("replay_origin") != origin
            or evidence.get("parent_result_artifact") != member["source_result_artifact"]
        ):
            raise ValueError("Later inputs do not match their recorded origin")
        parent = service.read_artifact(store, source_bundle["inputs_artifact"])
        from research.portfolio_validation import _snapshot

        expected_snapshot = _snapshot(parent["snapshot"], reservation["from"], reservation["to"])
        if any(
            evidence["snapshot"].get(key) != expected_snapshot.get(key)
            for key in ("sessions", "timeline", "bars", "raw_bars", "session_hours")
        ):
            raise ValueError("Later prices or sessions differ from the original reserved period")
        expected_signals = {
            item["id"]: [
                signal
                for signal in item["signals"]
                if reservation["from"] <= signal["date"] <= reservation["to"]
            ]
            for item in parent["strategies"]
        }
        if {item["id"]: item["signals"] for item in evidence["strategies"]} != expected_signals:
            raise ValueError("Later signals differ from the original reserved period")
        del parent, evidence, expected_snapshot
    combined = analysis.saved_overlay(store, job, original)
    view = present_report(
        combined,
        job_id=job.id,
        result_artifact=job.result_artifact,
        inputs_artifact=bundle.get("inputs_artifact"),
    )
    if embedded:
        view = view["validation"]["result"]
    context = view["report_context"]
    dates = context["dates"]
    if (
        context["period"] != "evaluation"
        or context["config_id"] != member["config_id"]
        or any(
            not isinstance(dates.get(key), str)
            or not reservation["from"] <= dates[key] <= reservation["to"]
            for key in ("from", "to")
        )
    ):
        raise ValueError("Later report is outside its reserved candidate period")
    # Embedded report_context receives the actual optional overlay identity explicitly.
    overlay = combined.get("report_context", {}).get("analysis_artifact")
    context = {**context, "analysis_artifact": overlay}
    analysis_job_id = None
    if overlay:
        with store.sessions() as db:
            analysis_job_id = db.scalar(
                select(ResearchJob.id)
                .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
                .where(
                    ResearchJob.owner == owner,
                    ResearchJob.status == "completed",
                    ResearchJob.result_artifact == overlay,
                    ResearchExperiment.kind == "portfolio_analysis",
                    ResearchExperiment.parent_job_id == job.id,
                )
                .limit(1)
            )
        if not analysis_job_id:
            raise ValueError("Later analysis is not retained")
    pin = {
        "job_id": job.id,
        "result_artifact": job.result_artifact,
        "inputs_artifact": bundle["inputs_artifact"],
        "analysis_job_id": analysis_job_id,
        "analysis_artifact": overlay,
        "view": "embedded_later" if embedded else "primary",
        "origin": {
            "source_job_id": member["source_job_id"],
            "source_result_artifact": member["source_result_artifact"],
            "config_id": member["config_id"],
            "reservation": reservation,
        },
        "report_context": copy.deepcopy(context),
        "job": {
            "id": job.id,
            "status": "completed",
            "progress": 100,
            "created_at": job.created_at,
            "kind": bundle["kind"],
        },
    }
    validate_pin(pin)
    return pin


def _resolve_later(store, owner, experiment_id, member, identifier):
    job_id, _, _, view = _pin_parts(identifier)
    pin = _later_pin(store, owner, experiment_id, member, job_id, embedded=view == "embedded_later")
    if _pin_id(pin) != identifier:
        raise DecisionEvidenceChanged(
            "This later report changed. Open its current evidence before saving."
        )
    return pin


def _evidence_use(store, db, owner, experiment_id, member, *, attached=None, source_bundle=None):
    pin = attached or _selection_pin(member)
    opened = db.scalar(
        select(ResearchEvidenceOpen.opened_at).where(
            ResearchEvidenceOpen.owner == owner,
            ResearchEvidenceOpen.experiment_id == experiment_id,
            ResearchEvidenceOpen.evidence_id == fingerprint(pin),
        )
    )
    result = (source_bundle or _source(store, member))["result"]
    reservation = _reservation(result)
    computed = db.get(ResearchHistory, member["source_job_id"])
    recorded = computed is not None and computed.owner == owner
    overlap = "not_checked"
    if reservation:
        # SQL dates show recorded calculation overlap. Signal matching prevents a
        # different scanner/symbol set being represented as this evidence's reuse.
        bundle = source_bundle or _source(store, member)
        inputs = service.read_artifact(store, bundle["inputs_artifact"])
        keys = {
            signal["date"] + "|" + signal["symbol"]
            for signal in inputs["signals"]
            if reservation["from"] <= signal["date"] <= reservation["to"]
        }
        del inputs
        rows = db.scalars(
            select(ResearchHistory)
            .where(
                ResearchHistory.owner == owner,
                ResearchHistory.test_from <= reservation["to"],
                ResearchHistory.test_end >= reservation["from"],
            )
            .order_by(ResearchHistory.job_id)
            .limit(51)
        ).all()
        overlap = "not_checked" if len(rows) > 50 else "not_found"
        for row in rows[:50]:
            if keys.intersection(json.loads(row.signal_keys)):
                overlap = "recorded"
                break
    used = bool(attached) or bool(
        db.scalar(
            select(ResearchDecisionEvent.id)
            .join(ResearchDecision, ResearchDecision.id == ResearchDecisionEvent.decision_id)
            .where(
                ResearchDecision.owner == owner,
                ResearchDecision.experiment_id == experiment_id,
                *(getattr(ResearchDecision, key) == member[key] for key in IDENTITY),
                ResearchDecisionEvent.evaluation_pin.is_not(None),
            )
            .limit(1)
        )
    )
    return {
        "reservation": reservation,
        "reservation_status": "reserved_in_setup" if reservation else "not_reserved",
        "calculation": "recorded" if recorded else "unknown",
        "opened_at": opened,
        "later_used_for_decision": used,
        "coverage": "legacy_unknown",
        "overlap": overlap,
    }


def decision_context(store, owner, experiment_id, comparison_id, member_id, *, limit=20, offset=0):
    limit, offset = library._page(limit, offset)
    with store.sessions() as db:
        experiment, _, member = _member(db, owner, experiment_id, comparison_id, member_id)
        head = _head(db, owner, experiment_id, member)
        current = _summary(store, db, experiment, head) if head else None
        archived = experiment.archived
        jobs = db.scalars(
            _later_query(experiment_id, member)
            .where(ResearchJob.owner == owner)
            .order_by(ResearchJob.created_at.desc(), ResearchJob.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
    source_bundle = None
    try:
        source_bundle = _source(store, member)
        with store.sessions() as db:
            use = _evidence_use(
                store, db, owner, experiment_id, member, source_bundle=source_bundle
            )
    except (ValueError, OSError, KeyError, TypeError):
        use = {
            "reservation": None,
            "reservation_status": "unknown",
            "calculation": "unknown",
            "opened_at": None,
            "later_used_for_decision": False,
            "coverage": "legacy_unknown",
            "overlap": "not_checked",
        }
    pins = []
    if offset == 0 and source_bundle and source_bundle["result"].get("validation"):
        try:
            pins.append(
                _later_pin(
                    store,
                    owner,
                    experiment_id,
                    member,
                    member["source_job_id"],
                    embedded=True,
                    source_bundle=source_bundle,
                )
            )
        except (ValueError, OSError, KeyError, TypeError):
            pass
    for job in jobs[:limit]:
        try:
            pins.append(
                _later_pin(store, owner, experiment_id, member, job.id, source_bundle=source_bundle)
            )
        except (ValueError, OSError, KeyError, TypeError):
            continue
    with store.sessions() as db:
        items = [_later_receipt(store, db, owner, pin) for pin in pins]
    return {
        "target": {"comparison_id": comparison_id, "member_id": member_id},
        "current": current,
        "evidence_use": use,
        "evaluation": {
            "items": items,
            "next_offset": offset + limit if len(jobs) > limit else None,
            "total": None,
        },
        "archived": archived,
    }


def _read_pin(store, owner, pin):
    response = {"available": False, "job": None, "result": None, "error": UNAVAILABLE}
    with store.sessions() as db:
        if not _pin_available(store, db, owner, pin):
            return response
    try:
        bundle = service.read_artifact(store, pin["result_artifact"])
        origin = pin["origin"]
        if origin and pin["view"] == "primary":
            replay = bundle["result"].get("replay_origin", {})
            if any(
                replay.get(key) != value
                for key, value in {
                    "parent_job_id": origin["source_job_id"],
                    "parent_result_artifact": origin["source_result_artifact"],
                    "config_id": origin["config_id"],
                    "period": "evaluation",
                }.items()
            ):
                raise ValueError("Pinned later report has a different origin")
        result = analysis.pinned_overlay(
            store,
            bundle["result"],
            parent_result_artifact=pin["result_artifact"],
            analysis_artifact=pin["analysis_artifact"],
        )
        result = present_report(
            result,
            job_id=pin["job_id"],
            result_artifact=pin["result_artifact"],
            inputs_artifact=bundle["inputs_artifact"],
        )
        if pin["view"] == "embedded_later":
            result = {**result["validation"]["result"], "portfolio": result.get("portfolio")}
            result["report_context"] = {
                **result["report_context"],
                "analysis_artifact": pin["analysis_artifact"],
            }
        if result["report_context"] != pin["report_context"]:
            raise ValueError("Report identity no longer matches its saved pin")
        result = {
            key: value
            for key, value in result.items()
            if key not in ("experiment", "validation", "reserved_evaluation")
        }
        return {"available": True, "job": copy.deepcopy(pin["job"]), "result": result}
    except (ValueError, OSError, KeyError, TypeError):
        return response


def preview_evaluation(store, owner, experiment_id, comparison_id, member_id, evaluation_id):
    with store.sessions() as db:
        _, _, member = _member(db, owner, experiment_id, comparison_id, member_id)
    return _read_pin(
        store, owner, _resolve_later(store, owner, experiment_id, member, evaluation_id)
    )


def save_decision(store, owner, experiment_id, comparison_id, member_id, data):
    library._object(
        data, {"request_id", "revision", "state", "reason", "evaluation_id"}, "research decision"
    )
    if (
        data.get("state") not in STATES
        or type(data.get("revision")) is not int
        or data["revision"] < 0
    ):
        raise ValueError("Choose a decision and its current revision")
    reason = library._text(data.get("reason", ""), 2000, "Decision reason", empty=True)
    if data.get("evaluation_id") is not None:
        _pin_parts(data["evaluation_id"])
    normalized = {
        **data,
        "reason": reason,
        "evaluation_id": data.get("evaluation_id"),
        "comparison_id": comparison_id,
        "member_id": member_id,
    }
    token, raw, digest = _request(normalized, "decision", experiment_id)
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        prior = _previous(db, owner, token, digest)
        if prior:
            old = db.get(ResearchDecisionEvent, prior.result_id)
            return {
                "decision": _summary(
                    store, db, experiment, db.get(ResearchDecision, old.decision_id), event=old
                ),
                "event": _event_receipt(store, db, old),
                "reused": True,
            }
        experiment, comparison, member = _member(db, owner, experiment_id, comparison_id, member_id)
        library._editable(experiment)
        head = _head(db, owner, experiment_id, member)
        if data["revision"] != (head.revision if head else 0):
            raise DecisionConflict(_summary(store, db, experiment, head) if head else None)
        comparison_name = comparison.name
    report = comparisons.get_member_report(store, owner, experiment_id, comparison_id, member_id)
    if not report["available"]:
        raise ValueError("Open an available exact candidate report before saving a decision")
    del report
    pin = (
        _resolve_later(store, owner, experiment_id, member, data["evaluation_id"])
        if data.get("evaluation_id")
        else None
    )
    with store.sessions() as db:
        use = _evidence_use(store, db, owner, experiment_id, member, attached=pin)
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment, comparison, actual = _member(db, owner, experiment_id, comparison_id, member_id)
        prior = _previous(db, owner, token, digest)
        if prior:
            old = db.get(ResearchDecisionEvent, prior.result_id)
            return {
                "decision": _summary(
                    store, db, experiment, db.get(ResearchDecision, old.decision_id), event=old
                ),
                "event": _event_receipt(store, db, old),
                "reused": True,
            }
        library._editable(experiment)
        if actual != member or comparison.name != comparison_name:
            raise DecisionEvidenceChanged("This comparison changed. Open it again before saving.")
        if not comparisons._member_receipt(store, db, owner, member)["available"]:
            raise ValueError("Exact selection evidence is unavailable")
        if pin:
            _check_current_pin(store, db, owner, pin)
        head = _head(db, owner, experiment_id, member)
        if data["revision"] != (head.revision if head else 0):
            raise DecisionConflict(_summary(store, db, experiment, head) if head else None)
        _bound(db, ResearchDecisionEvent)
        service.ensure_storage_capacity(
            store, len(raw.encode()) + len(service.encoded(pin)) + 16384
        )
        now, event_id = time.time(), uuid.uuid4().hex
        if not head:
            _bound(db, ResearchDecision)
            _bound(
                db,
                ResearchDecision,
                where=(ResearchDecision.experiment_id == experiment_id,),
                limit=MAX_PER_EXPERIMENT,
            )
            head = ResearchDecision(
                id=uuid.uuid4().hex,
                owner=owner,
                experiment_id=experiment_id,
                **{key: member[key] for key in IDENTITY},
                revision=0,
                current_event_id=event_id,
                created_at=now,
                updated_at=now,
            )
            db.add(head)
        if head.revision >= MAX_EVENTS:
            raise ValueError("This candidate's decision history reached its limit")
        previous_id = head.current_event_id if head.revision else None
        head.revision += 1
        head.current_event_id, head.updated_at = event_id, now
        row = ResearchDecisionEvent(
            id=event_id,
            owner=owner,
            experiment_id=experiment_id,
            decision_id=head.id,
            revision=head.revision,
            supersedes_event_id=previous_id,
            state=data["state"],
            reason=reason,
            comparison_id=comparison_id,
            member_id=member_id,
            candidate_name=member["name"],
            comparison_name=comparison.name,
            evaluation_pin=service.encoded(pin).decode() if pin else None,
            evidence_use=service.encoded(use).decode(),
            created_at=now,
        )
        db.add(row)
        _remember(db, owner, experiment_id, token, raw, digest, "decision", event_id)
        db.flush()
        return {
            "decision": _summary(store, db, experiment, head),
            "event": _event_receipt(store, db, row),
            "reused": False,
        }


def _check_current_pin(store, db, owner, pin):
    job = db.get(ResearchJob, pin["job_id"])
    if (
        not _pin_available(store, db, owner, pin)
        or job.status != "completed"
        or job.result_artifact != pin["result_artifact"]
    ):
        raise DecisionEvidenceChanged("Later report changed before it was saved")
    if pin["analysis_job_id"]:
        extra = db.get(ResearchJob, pin["analysis_job_id"])
        if extra.status != "completed" or extra.result_artifact != pin["analysis_artifact"]:
            raise DecisionEvidenceChanged("Later analysis changed before it was saved")
    latest = db.execute(
        select(ResearchJob, ResearchExperiment.specification)
        .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
        .where(
            ResearchJob.owner == owner,
            ResearchJob.status == "completed",
            ResearchExperiment.kind == "portfolio_analysis",
            ResearchExperiment.parent_job_id == pin["job_id"],
        )
        .order_by(ResearchJob.created_at.desc(), ResearchJob.id.desc())
        .limit(1)
    ).first()
    if latest:
        specification = json.loads(latest[1])
        if (
            specification.get("parent_result_artifact") == pin["result_artifact"]
            and specification.get("analysis_version") in analysis.READABLE_VERSIONS
            and latest[0].id != pin["analysis_job_id"]
        ):
            raise DecisionEvidenceChanged(
                "New later analysis completed before this decision was saved"
            )


def list_decisions(store, owner, experiment_id, *, state=None, limit=20, offset=0):
    limit, offset = library._page(limit, offset)
    if state is not None and state not in STATES:
        raise ValueError("Choose Keep, Reject or Revisit")
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        query = (
            select(ResearchDecision)
            .join(
                ResearchDecisionEvent, ResearchDecisionEvent.id == ResearchDecision.current_event_id
            )
            .where(ResearchDecision.owner == owner, ResearchDecision.experiment_id == experiment_id)
        )
        if state:
            query = query.where(ResearchDecisionEvent.state == state)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(
            query.order_by(ResearchDecision.updated_at.desc(), ResearchDecision.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "items": [_summary(store, db, experiment, row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
            "total": total,
        }


def decision_history(store, owner, experiment_id, identifier, *, limit=20, offset=0):
    limit, offset = library._page(limit, offset)
    with store.sessions() as db:
        _, head = _owned(db, owner, experiment_id, identifier)
        rows = db.scalars(
            select(ResearchDecisionEvent)
            .where(ResearchDecisionEvent.decision_id == head.id)
            .order_by(ResearchDecisionEvent.revision.desc())
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "items": [_event_receipt(store, db, row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
            "total": head.revision,
        }


def get_event(store, owner, experiment_id, identifier, event_id):
    with store.sessions() as db:
        experiment, _, row = _event(db, owner, experiment_id, identifier, event_id)
        _, _, member = _member(db, owner, experiment_id, row.comparison_id, row.member_id)
        available = comparisons._member_receipt(store, db, owner, member)["available"]
        pin = json.loads(row.evaluation_pin) if row.evaluation_pin else None
        available = available and (not pin or _pin_available(store, db, owner, pin))
        return {
            "event": _event_receipt(store, db, row),
            "archived": experiment.archived,
            "available": available,
            **({"error": UNAVAILABLE} if not available else {}),
        }


def event_report(store, owner, experiment_id, identifier, event_id, *, evidence="selection"):
    if evidence not in ("selection", "evaluation"):
        raise ValueError("Choose selection or later evidence")
    with store.sessions() as db:
        _, _, row = _event(db, owner, experiment_id, identifier, event_id)
        pin = json.loads(row.evaluation_pin) if row.evaluation_pin else None
        comparison_id, member_id = row.comparison_id, row.member_id
    if evidence == "selection":
        result = comparisons.get_member_report(
            store, owner, experiment_id, comparison_id, member_id
        )
        return {key: value for key, value in result.items() if key != "member"}
    if pin is None:
        raise LookupError("This decision does not attach later evidence")
    return _read_pin(store, owner, pin)


def _open_target(store, owner, experiment_id, target):
    if not isinstance(target, dict):
        raise ValueError("Choose exact report evidence to acknowledge")
    if target.get("kind") == "comparison_member":
        library._object(
            target, {"kind", "comparison_id", "member_id", "evaluation_id"}, "opened report"
        )
        with store.sessions() as db:
            _, _, member = _member(
                db, owner, experiment_id, target.get("comparison_id"), target.get("member_id")
            )
        if target.get("evaluation_id"):
            pin = _resolve_later(store, owner, experiment_id, member, target["evaluation_id"])
            result = _read_pin(store, owner, pin)
        else:
            pin = _selection_pin(member)
            result = comparisons.get_member_report(
                store, owner, experiment_id, target["comparison_id"], target["member_id"]
            )
    elif target.get("kind") == "decision_event":
        library._object(
            target, {"kind", "decision_id", "event_id", "evidence"}, "opened decision report"
        )
        if target.get("evidence") not in ("selection", "evaluation"):
            raise ValueError("Choose selection or later evidence")
        with store.sessions() as db:
            _, _, row = _event(
                db, owner, experiment_id, target.get("decision_id"), target.get("event_id")
            )
            _, _, member = _member(db, owner, experiment_id, row.comparison_id, row.member_id)
            pin = (
                json.loads(row.evaluation_pin)
                if target["evidence"] == "evaluation" and row.evaluation_pin
                else (_selection_pin(member) if target["evidence"] == "selection" else None)
            )
        if pin is None:
            raise LookupError("This decision does not attach later evidence")
        result = _read_pin(store, owner, pin)
    else:
        raise ValueError("Choose a saved comparison or decision report")
    if not result["available"]:
        raise ValueError("Only a successfully opened exact report can be acknowledged")
    return pin


def acknowledge_open(store, owner, experiment_id, data):
    library._object(data, {"request_id", "target"}, "report opening")
    token, raw, digest = _request(data, "opened", experiment_id)
    with store.sessions() as db:
        library._owned(db, owner, experiment_id)
        prior = _previous(db, owner, token, digest)
        if prior:
            return {
                "opened_at": db.get(ResearchEvidenceOpen, prior.result_id).opened_at,
                "reused": True,
            }
    pin = _open_target(store, owner, experiment_id, data.get("target"))
    identity = fingerprint(pin)
    with store.sessions.begin() as db:
        service.write_guard(db)
        library._owned(db, owner, experiment_id)
        prior = _previous(db, owner, token, digest)
        if prior:
            return {
                "opened_at": db.get(ResearchEvidenceOpen, prior.result_id).opened_at,
                "reused": True,
            }
        if not _pin_available(store, db, owner, pin):
            raise ValueError("The opened report is unavailable")
        row = db.scalar(
            select(ResearchEvidenceOpen).where(
                ResearchEvidenceOpen.owner == owner,
                ResearchEvidenceOpen.experiment_id == experiment_id,
                ResearchEvidenceOpen.evidence_id == identity,
            )
        )
        reused = row is not None
        service.ensure_storage_capacity(store, len(raw.encode()) + len(service.encoded(pin)) + 8192)
        if row is None:
            _bound(db, ResearchEvidenceOpen)
            row = ResearchEvidenceOpen(
                id=uuid.uuid4().hex,
                owner=owner,
                experiment_id=experiment_id,
                evidence_id=identity,
                target=service.encoded(data["target"]).decode(),
                report_pin=service.encoded(pin).decode(),
                opened_at=time.time(),
            )
            db.add(row)
        _remember(db, owner, experiment_id, token, raw, digest, "opened", row.id)
        return {"opened_at": row.opened_at, "reused": reused}


def validate_pin(pin):
    fields = {
        "job_id",
        "result_artifact",
        "inputs_artifact",
        "analysis_job_id",
        "analysis_artifact",
        "view",
        "report_context",
        "job",
        "origin",
    }
    if (
        not isinstance(pin, dict)
        or set(pin) != fields
        or pin["view"] not in ("primary", "embedded_later")
    ):
        raise ValueError("Invalid retained decision report")
    if len(service.encoded(pin)) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Retained decision report exceeds its size limit")
    for key, size in (("job_id", 32), ("result_artifact", 64), ("inputs_artifact", 64)):
        shortlist._hex(pin[key], size, "decision report identity")
    if (pin["analysis_job_id"] is None) != (pin["analysis_artifact"] is None):
        raise ValueError("Invalid retained analysis identity")
    if pin["analysis_job_id"]:
        shortlist._hex(pin["analysis_job_id"], 32, "decision analysis job")
        shortlist._hex(pin["analysis_artifact"], 64, "decision analysis artifact")
    context = pin["report_context"]
    if (
        not isinstance(context, dict)
        or context.get("version") != "research-report-context-v1"
        or context.get("period") not in ("full", "selection", "evaluation")
    ):
        raise ValueError("Invalid decision report context")
    for key in ("job_id", "result_artifact", "inputs_artifact", "analysis_artifact"):
        if context.get(key) != pin[key]:
            raise ValueError("Decision report context does not match its pin")
    shortlist._hex(context.get("config_id"), 64, "decision configuration")
    origin = pin["origin"]
    if origin is not None:
        if (
            not isinstance(origin, dict)
            or set(origin)
            != {"source_job_id", "source_result_artifact", "config_id", "reservation"}
            or origin["config_id"] != context["config_id"]
            or context["period"] != "evaluation"
        ):
            raise ValueError("Invalid later evidence origin")
        shortlist._hex(origin["source_job_id"], 32, "later evidence parent")
        shortlist._hex(origin["source_result_artifact"], 64, "later evidence parent artifact")
        reservation = origin["reservation"]
        if (
            not isinstance(reservation, dict)
            or set(reservation) != {"from", "to"}
            or any(
                not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
                for value in reservation.values()
            )
            or reservation["from"] > reservation["to"]
        ):
            raise ValueError("Invalid retained later range")
        if pin["view"] == "embedded_later" and (
            pin["job_id"] != origin["source_job_id"]
            or pin["result_artifact"] != origin["source_result_artifact"]
        ):
            raise ValueError("Embedded later result does not match its source")
        if pin["view"] == "primary" and context.get("parent_job_id") != origin["source_job_id"]:
            raise ValueError("Later report has a different parent")
    elif context["period"] == "evaluation":
        raise ValueError("Later evidence needs an exact origin")
    if context.get("report_id") != fingerprint(
        {key: context[key] for key in ("version", "result_artifact", "period", "config_id")}
    ):
        raise ValueError("Invalid decision report fingerprint")
    dates = context.get("dates")
    if (
        not isinstance(dates, dict)
        or set(dates) != {"from", "to"}
        or any(
            value is not None
            and (not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
            for value in dates.values()
        )
    ):
        raise ValueError("Invalid decision report dates")
    job = pin["job"]
    if (
        not isinstance(job, dict)
        or set(job) != {"id", "status", "progress", "created_at", "kind"}
        or job["id"] != pin["job_id"]
        or job["status"] != "completed"
        or job["progress"] != 100
        or job["kind"] not in ("portfolio_optimize", "portfolio_backtest")
        or isinstance(job["created_at"], bool)
        or not isinstance(job["created_at"], (int, float))
        or not math.isfinite(job["created_at"])
        or job["created_at"] <= 0
    ):
        raise ValueError("Invalid retained decision job")


def validate_use(value):
    if not isinstance(value, dict) or set(value) != {
        "reservation",
        "reservation_status",
        "calculation",
        "opened_at",
        "later_used_for_decision",
        "coverage",
        "overlap",
    }:
        raise ValueError("Invalid decision evidence-use history")
    if (
        value["reservation_status"] not in ("reserved_in_setup", "not_reserved", "unknown")
        or value["calculation"] not in ("recorded", "not_recorded", "unknown")
        or value["coverage"] not in ("recorded", "legacy_unknown")
        or value["overlap"] not in ("recorded", "not_found", "not_checked")
        or type(value["later_used_for_decision"]) is not bool
    ):
        raise ValueError("Invalid recorded evidence-use state")
    reservation = value["reservation"]
    if reservation is not None and (
        not isinstance(reservation, dict)
        or set(reservation) != {"from", "to"}
        or any(
            not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item)
            for item in reservation.values()
        )
        or reservation["from"] > reservation["to"]
    ):
        raise ValueError("Invalid reserved evidence dates")
    if (reservation is not None) != (value["reservation_status"] == "reserved_in_setup"):
        raise ValueError("Reserved period state does not match its dates")
    stamp = value["opened_at"]
    if stamp is not None and (
        isinstance(stamp, bool)
        or not isinstance(stamp, (int, float))
        or not math.isfinite(stamp)
        or stamp <= 0
    ):
        raise ValueError("Invalid report opening time")
