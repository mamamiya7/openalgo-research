"""Deliberate reusable choices from exact Keep decisions, with native setup versions."""

import copy
import hashlib
import json
import math
import re
import time
import uuid
from datetime import date

from sqlalchemy import func, select

from database.research_db import (
    ResearchChosenRequest,
    ResearchChosenSetup,
    ResearchDecision,
    ResearchDecisionEvent,
    ResearchJob,
    ResearchLibraryJob,
    ResearchSetupVersion,
)
from research.report_contract import fingerprint, settings_identity
from services import research_decisions as decisions
from services import research_library as library
from services import research_shortlist as shortlist
from services import scanner_research_service as service

VERSION = "research-chosen-setup-v1"
MAX_ROWS = 100000
MAX_CHOICES = 1000
MAX_REQUESTS = 10000
MAX_SNAPSHOT_BYTES = 16384
MAX_RESPONSE_BYTES = 32768


def _current(db, owner, experiment_id):
    return db.scalar(
        select(ResearchChosenSetup)
        .where(
            ResearchChosenSetup.owner == owner, ResearchChosenSetup.experiment_id == experiment_id
        )
        .order_by(ResearchChosenSetup.sequence.desc())
        .limit(1)
    )


def _owned(db, owner, experiment_id, choice_id):
    shortlist._hex(choice_id, 32, "chosen setup")
    experiment = library._owned(db, owner, experiment_id)
    choice = db.get(ResearchChosenSetup, choice_id)
    if not choice or choice.owner != owner or choice.experiment_id != experiment_id:
        raise LookupError("Chosen setup not found")
    return experiment, choice


def _decision(db, owner, experiment_id, decision_id, event_id, *, current=True):
    experiment, head, event = decisions._event(db, owner, experiment_id, decision_id, event_id)
    if event.state != "keep":
        raise ValueError("Choose a setup from a Keep decision")
    if current and head.current_event_id != event.id:
        raise ValueError(
            "This decision changed. Review its current decision before choosing a setup."
        )
    member = decisions._event_member(db, event)
    if any(getattr(head, key) != member[key] for key in decisions.IDENTITY):
        raise ValueError("The decision no longer matches its original candidate")
    return experiment, head, event, member


def _source_fence(db, owner, experiment_id, snapshot):
    _, source = shortlist._linked(db, owner, experiment_id, snapshot["source_job_id"])
    if source.status != "completed" or source.result_artifact != snapshot["source_result_artifact"]:
        raise ValueError(
            "The original result changed. Reopen its saved evidence before continuing."
        )
    return source


def _snapshot(member, portfolio):
    context = member["report_context"]
    basis = context["evaluation_basis"]
    period = basis.get("period", {}) if basis.get("status") == "verified" else {}
    comparison = basis.get("comparison", {}) if basis.get("status") == "verified" else {}
    return {
        **{key: member[key] for key in decisions.IDENTITY},
        **{
            key: member[key]
            for key in (
                "report_job_id",
                "report_result_artifact",
                "analysis_job_id",
                "analysis_artifact",
                "trial_number",
            )
        },
        "inputs_artifact": context["inputs_artifact"],
        "candidate_name": member["name"],
        "capital": portfolio["capital"],
        "currency": comparison.get("currency"),
        "dates": {key: period.get(key) or context["dates"].get(key) for key in ("from", "to")},
        "sources": {row["id"]: row["source_id"] for row in portfolio["strategies"]},
    }


def _prepared_rules(store, owner, member):
    from research.portfolio import normalize
    from services.research_decision_targets import read_selection_report

    report = read_selection_report(store, owner, member)
    if not report["available"]:
        raise ValueError("Restore this decision's exact report before choosing its setup")
    result = report["result"]
    # Candidate report preparation partitions its prices and strips validation.
    # Reusable input/date/reserve intent belongs to the pinned original request;
    # only the selected candidate's actual rules/weights come from this report.
    original = service.read_artifact(store, member["source_result_artifact"])
    inputs = service.read_artifact(store, original["inputs_artifact"])
    portfolio = copy.deepcopy(inputs["portfolio"])
    del inputs, original
    selected = {item["id"]: item for item in result["strategies"]}
    if set(selected) != {item["id"] for item in portfolio["strategies"]}:
        raise ValueError("The saved report has inconsistent strategy identities")
    portfolio.pop("optimization", None)
    for row in portfolio["strategies"]:
        chosen = selected[row["id"]]
        row.update(
            name=chosen["name"],
            config=copy.deepcopy(chosen["config"]),
            allocation_pct=chosen["allocation_pct"],
            search={},
        )
    draft = library.fresh_draft()
    draft.update(portfolio=portfolio, optimizing=False, equalWeights=False)
    draft = library.normalize_draft(store, owner, draft)
    normalized = normalize(library.portfolio_payload(draft))
    if settings_identity(normalized["strategies"]) != member["config_id"]:
        raise ValueError("The current setup format cannot preserve these recorded rules exactly")
    draft["portfolio"] = normalized
    snapshot = _snapshot(member, normalized)
    validate_snapshot(snapshot)
    return draft, normalized, snapshot


def _summary(db, experiment, choice, *, current_id=None):
    version = db.get(ResearchSetupVersion, choice.version_id)
    if not version or version.experiment_id != experiment.id:
        raise ValueError("Chosen setup version is missing")
    snapshot = json.loads(choice.snapshot)
    validate_snapshot(snapshot)
    portfolio = json.loads(version.portfolio)
    head = db.get(ResearchDecision, choice.decision_id)
    current_event = db.get(ResearchDecisionEvent, head.current_event_id) if head else None
    if (
        choice.owner != experiment.owner
        or not head
        or head.owner != experiment.owner
        or head.experiment_id != experiment.id
        or any(getattr(head, key) != snapshot[key] for key in decisions.IDENTITY)
        or not current_event
        or current_event.decision_id != head.id
        or current_event.owner != experiment.owner
        or current_event.experiment_id != experiment.id
    ):
        raise ValueError("Chosen setup decision identity is missing or changed")
    state = current_event.state if current_event else None
    reason = None
    if experiment.archived:
        reason = "Restore this experiment before using its chosen setup."
    elif state != "keep":
        reason = "The current decision is no longer Keep. Review it before using this setup."
    elif current_id is not None and choice.id != current_id:
        reason = "This is an earlier choice. Use the current chosen setup."
    return {
        "id": choice.id,
        "sequence": choice.sequence,
        "name": choice.name,
        "version": {"id": version.id, "number": version.number, "name": version.name},
        "decision_id": choice.decision_id,
        "event_id": choice.event_id,
        **{key: snapshot[key] for key in decisions.IDENTITY},
        "created_at": choice.created_at,
        "current_decision_state": state,
        "usable": reason is None,
        "unavailable_reason": reason,
        "strategies": portfolio["strategies"],
        "capital": snapshot["capital"],
        "currency": snapshot["currency"],
        "dates": snapshot["dates"],
        "candidate_name": snapshot["candidate_name"],
        "trial_number": snapshot["trial_number"],
    }


def current_summaries(db, owner, experiments):
    """Bounded metadata projection for library rows; never reads report artifacts."""
    if len(experiments) > library.PAGE_SIZE:
        raise ValueError("Too many chosen setups requested")
    if not experiments:
        return {}
    ids = [row.id for row in experiments]
    latest = (
        select(
            ResearchChosenSetup.experiment_id,
            func.max(ResearchChosenSetup.sequence).label("sequence"),
        )
        .where(ResearchChosenSetup.owner == owner, ResearchChosenSetup.experiment_id.in_(ids))
        .group_by(ResearchChosenSetup.experiment_id)
        .subquery()
    )
    choices = db.scalars(
        select(ResearchChosenSetup)
        .join(
            latest,
            (ResearchChosenSetup.experiment_id == latest.c.experiment_id)
            & (ResearchChosenSetup.sequence == latest.c.sequence),
        )
        .where(ResearchChosenSetup.owner == owner)
    ).all()
    containers = {row.id: row for row in experiments}
    return {
        choice.experiment_id: _summary(
            db, containers[choice.experiment_id], choice, current_id=choice.id
        )
        for choice in choices
    }


def context(store, owner, experiment_id, *, decision_id=None, event_id=None):
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        choice = _current(db, owner, experiment_id)
        result = {
            "version": VERSION,
            "revision": experiment.revision,
            "archived": experiment.archived,
            "current": _summary(db, experiment, choice, current_id=choice.id) if choice else None,
        }
    if decision_id is None and event_id is None:
        return result
    if not decision_id or not event_id:
        raise ValueError("Choose the exact decision history entry")
    with store.sessions() as db:
        experiment, head, event = decisions._event(db, owner, experiment_id, decision_id, event_id)
        member = decisions._event_member(db, event)
        eligibility = {
            "available": False,
            "candidate_name": event.candidate_name,
            "decision_id": decision_id,
            "event_id": event_id,
            "strategies": [],
            "capital": None,
            "currency": None,
            "period": member["period"],
            "dates": member["report_context"]["dates"],
        }
        reason = (
            "Restore this experiment before choosing a setup."
            if experiment.archived
            else "Choose a setup from the current Keep decision."
            if event.state != "keep" or head.current_event_id != event.id
            else None
        )
    if reason is None:
        try:
            _, portfolio, snapshot = _prepared_rules(store, owner, member)
            with store.sessions() as db:
                experiment, _, _, _ = _decision(db, owner, experiment_id, decision_id, event_id)
                library._revision(experiment, result["revision"])
                library._editable(experiment)
                _source_fence(db, owner, experiment_id, snapshot)
            eligibility.update(
                available=True,
                strategies=portfolio["strategies"],
                capital=snapshot["capital"],
                currency=snapshot["currency"],
                dates=snapshot["dates"],
            )
        except (ValueError, OSError) as error:
            reason = str(error)
    if reason:
        eligibility["reason"] = reason
    result["eligibility"] = eligibility
    return result


def history(store, owner, experiment_id, *, limit=20, offset=0):
    limit, offset = library._page(limit, offset)
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        latest = _current(db, owner, experiment_id)
        query = select(ResearchChosenSetup).where(
            ResearchChosenSetup.owner == owner, ResearchChosenSetup.experiment_id == experiment_id
        )
        rows = db.scalars(
            query.order_by(ResearchChosenSetup.sequence.desc()).offset(offset).limit(limit + 1)
        ).all()
        return {
            "items": [
                _summary(db, experiment, row, current_id=latest.id if latest else None)
                for row in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
            "total": latest.sequence if latest else 0,
        }


def _request(data, experiment_id, kind):
    token, _ = library._request(data, kind)
    payload = {
        "kind": kind,
        "experiment_id": experiment_id,
        **{key: value for key, value in data.items() if key != "request_id"},
    }
    raw = service.encoded(payload)
    if len(raw) > decisions.MAX_BODY_BYTES:
        raise ValueError("Chosen setup request exceeds its limit")
    return token, raw.decode(), hashlib.sha256(raw).hexdigest()


def _prior(db, owner, token, digest):
    previous = db.get(ResearchChosenRequest, (owner, token))
    if previous and previous.payload_hash != digest:
        raise decisions.DecisionRequestConflict(
            "This request identity was already used for another chosen setup action"
        )
    return previous


def _remember(db, owner, experiment_id, token, raw, digest, kind, choice, response):
    decisions._bound(db, ResearchChosenRequest, limit=MAX_ROWS)
    decisions._bound(
        db,
        ResearchChosenRequest,
        where=(ResearchChosenRequest.experiment_id == experiment_id,),
        limit=MAX_REQUESTS,
    )
    encoded = service.encoded(response).decode()
    if len(encoded.encode()) > MAX_RESPONSE_BYTES:
        raise ValueError("Chosen setup receipt exceeds its limit")
    db.add(
        ResearchChosenRequest(
            owner=owner,
            token=token,
            experiment_id=experiment_id,
            kind=kind,
            payload=raw,
            payload_hash=digest,
            choice_id=choice.id,
            response=encoded,
            created_at=time.time(),
        )
    )


def _response(db, experiment, choice, response, *, reused):
    current = _current(db, experiment.owner, experiment.id)
    return {
        **response,
        "choice": _summary(db, experiment, choice, current_id=current.id if current else None),
        "reused": reused,
    }


def choose(store, owner, experiment_id, data):
    library._object(
        data, {"revision", "request_id", "decision_id", "event_id", "name"}, "chosen setup"
    )
    name = library._text(data.get("name"), 120, "Chosen setup name")
    normalized = {**data, "name": name}
    token, raw, digest = _request(normalized, experiment_id, "choose")
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, token, digest)
        if prior:
            return _response(
                db,
                experiment,
                db.get(ResearchChosenSetup, prior.choice_id),
                json.loads(prior.response),
                reused=True,
            )
        library._revision(experiment, data.get("revision"))
        library._editable(experiment)
        _, _, _, member = _decision(
            db, owner, experiment_id, data.get("decision_id"), data.get("event_id")
        )
    draft, portfolio, snapshot = _prepared_rules(store, owner, member)
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, token, digest)
        if prior:
            return _response(
                db,
                experiment,
                db.get(ResearchChosenSetup, prior.choice_id),
                json.loads(prior.response),
                reused=True,
            )
        library._revision(experiment, data.get("revision"))
        library._editable(experiment)
        _, _, _, actual = _decision(db, owner, experiment_id, data["decision_id"], data["event_id"])
        if actual != member or not decisions._pin_available(
            store, db, owner, decisions._selection_pin(member)
        ):
            raise ValueError("The exact decision evidence changed before choosing its setup")
        _source_fence(db, owner, experiment_id, snapshot)
        library._check_sources(db, owner, draft)
        previous = _current(db, owner, experiment_id)
        sequence = previous.sequence + 1 if previous else 1
        if sequence > MAX_CHOICES:
            raise ValueError("This experiment has reached its chosen setup history limit")
        decisions._bound(db, ResearchChosenSetup, limit=MAX_ROWS)
        service.ensure_storage_capacity(
            store, len(library._bounded_json(draft).encode()) * 2 + len(raw.encode()) + 32768
        )
        link = db.get(ResearchLibraryJob, (experiment_id, snapshot["source_job_id"]))
        version = library._freeze(
            db,
            experiment,
            draft,
            name,
            portfolio,
            parents={
                "parent_job_id": snapshot["source_job_id"],
                "parent_result_artifact": snapshot["source_result_artifact"],
                "parent_trial_id": snapshot["config_id"]
                if member["origin_kind"] == "study"
                else None,
                "parent_version_id": link.version_id,
            },
        )
        choice = ResearchChosenSetup(
            id=uuid.uuid4().hex,
            owner=owner,
            experiment_id=experiment_id,
            sequence=sequence,
            name=name,
            decision_id=data["decision_id"],
            event_id=data["event_id"],
            version_id=version.id,
            snapshot=service.encoded(snapshot).decode(),
            created_at=time.time(),
        )
        db.add(choice)
        library._touch(experiment)
        response = {"revision": experiment.revision}
        _remember(db, owner, experiment_id, token, raw, digest, "choose", choice, response)
        db.flush()
        return _response(db, experiment, choice, response, reused=False)


def _usable_choice(db, owner, experiment_id, choice_id):
    experiment, choice = _owned(db, owner, experiment_id, choice_id)
    latest = _current(db, owner, experiment_id)
    summary = _summary(db, experiment, choice, current_id=latest.id if latest else None)
    if not summary["usable"]:
        raise ValueError(summary["unavailable_reason"])
    version = db.get(ResearchSetupVersion, choice.version_id)
    validate_version(choice, version)
    _decision(db, owner, experiment_id, choice.decision_id, choice.event_id, current=False)
    _source_fence(db, owner, experiment_id, json.loads(choice.snapshot))
    return experiment, choice, version


def _prepare_use(store, owner, experiment_id, data):
    from services.research_setup_reuse import reuse_draft

    library._object(data, {"choice_id", "sources", "mode"}, "chosen setup preview")
    with store.sessions() as db:
        experiment, choice, version = _usable_choice(
            db, owner, experiment_id, data.get("choice_id")
        )
        revision, archived = experiment.revision, experiment.archived
        summary = _summary(db, experiment, choice, current_id=choice.id)
        draft = json.loads(version.draft)
        marker = _version_identity(choice, version)
    prepared = reuse_draft(
        store, owner, draft, replacements=data.get("sources"), mode=data.get("mode", "backtest")
    )
    with store.sessions() as db:
        experiment, choice, version = _usable_choice(db, owner, experiment_id, data["choice_id"])
        library._revision(experiment, revision)
        if _version_identity(choice, version) != marker:
            raise ValueError("The chosen setup changed. Reopen it before continuing.")
        library._check_sources(db, owner, prepared["draft"])
    return {"revision": revision, "archived": archived, "choice": summary, **prepared}, marker


def _version_identity(choice, version):
    return fingerprint(
        {"snapshot": choice.snapshot, "draft": version.draft, "portfolio": version.portfolio}
    )


def preview_use(store, owner, experiment_id, data):
    return _prepare_use(store, owner, experiment_id, data)[0]


def use_choice(store, owner, experiment_id, data):
    library._object(
        data, {"revision", "request_id", "choice_id", "sources", "mode"}, "use chosen setup"
    )
    token, raw, digest = _request(data, experiment_id, "use")
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, token, digest)
        if prior:
            response = _response(
                db,
                experiment,
                db.get(ResearchChosenSetup, prior.choice_id),
                json.loads(prior.response),
                reused=True,
            )
        else:
            library._revision(experiment, data.get("revision"))
            response = None
    if response:
        return {**response, "experiment": library.get_experiment(store, owner, experiment_id)}
    prepared, marker = _prepare_use(
        store,
        owner,
        experiment_id,
        {key: data[key] for key in ("choice_id", "sources", "mode") if key in data},
    )
    replacement = prepared["draft"]
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, token, digest)
        if prior:
            response = _response(
                db,
                experiment,
                db.get(ResearchChosenSetup, prior.choice_id),
                json.loads(prior.response),
                reused=True,
            )
        else:
            library._revision(experiment, data.get("revision"))
            experiment, choice, version = _usable_choice(
                db, owner, experiment_id, data["choice_id"]
            )
            if _version_identity(choice, version) != marker:
                raise ValueError("The chosen setup changed. Reopen it before continuing.")
            library._check_sources(db, owner, replacement)
            service.ensure_storage_capacity(
                store,
                len(experiment.draft.encode()) * 2
                + len(library._bounded_json(replacement).encode()) * 2
                + 65536,
            )
            previous = json.loads(experiment.draft)
            preserved = (
                library._freeze(db, experiment, previous, "Draft before using chosen setup")
                if previous != library.fresh_draft() and previous != replacement
                else None
            )
            applied = version
            if json.loads(version.draft) != replacement:
                applied = library._freeze(
                    db,
                    experiment,
                    replacement,
                    ("Refine " if data.get("mode") == "optimize" else "Use ") + choice.name[:110],
                    parents={
                        **{key: getattr(version, key) for key in library.PARENTS},
                        "parent_version_id": version.id,
                    },
                )
            experiment.draft = library._bounded_json(replacement)
            for key in library.PARENTS:
                setattr(experiment, key, getattr(version, key))
            experiment.parent_version_id = applied.id
            library._link_sources(db, experiment_id, replacement)
            library._touch(experiment)
            receipt = {
                "revision": experiment.revision,
                "applied_version_id": applied.id,
                "preserved_version_id": preserved.id if preserved else None,
                "changes": prepared["changes"],
            }
            _remember(db, owner, experiment_id, token, raw, digest, "use", choice, receipt)
            db.flush()
            response = _response(db, experiment, choice, receipt, reused=False)
    return {**response, "experiment": library.get_experiment(store, owner, experiment_id)}


def replay_choice(store, owner, experiment_id, data):
    """Admit an exact frozen-price native replay, with the choice receipt atomically."""
    from services.research_portfolio import replay_inputs

    library._object(data, {"revision", "request_id", "choice_id"}, "replay chosen setup")
    token, raw, digest = _request(data, experiment_id, "replay")
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, token, digest)
        if prior:
            response = json.loads(prior.response)
            accepted = _response(
                db, experiment, db.get(ResearchChosenSetup, prior.choice_id), response, reused=True
            )
        else:
            accepted = None
            library._revision(experiment, data.get("revision"))
            experiment, choice, version = _usable_choice(
                db, owner, experiment_id, data.get("choice_id")
            )
            snapshot = json.loads(choice.snapshot)
            marker = _version_identity(choice, version)
            trial_id = version.parent_trial_id
            source_link = db.get(ResearchLibraryJob, (experiment_id, snapshot["source_job_id"]))
            parents = {
                "parent_job_id": snapshot["source_job_id"],
                "parent_result_artifact": snapshot["source_result_artifact"],
                "parent_trial_id": trial_id,
                "parent_version_id": source_link.version_id,
            }
    if accepted:
        return {
            **accepted,
            **library._run_response(
                store, owner, experiment_id, response["version_id"], response["job_id"]
            ),
        }
    source, evidence = replay_inputs(store, owner, snapshot["source_job_id"], trial_id=trial_id)
    if (
        source.result_artifact != snapshot["source_result_artifact"]
        or settings_identity(evidence["portfolio"]["strategies"]) != snapshot["config_id"]
    ):
        raise ValueError("The original result changed. Reopen its saved evidence before replaying.")
    draft = library.fresh_draft()
    draft.update(portfolio=evidence["portfolio"], equalWeights=False)
    draft = library.normalize_draft(store, owner, draft)

    def guard(db, row):
        _, current, frozen = _usable_choice(db, owner, experiment_id, data["choice_id"])
        if _version_identity(current, frozen) != marker:
            raise ValueError("The chosen setup changed. Reopen it before replaying.")
        _prior(db, owner, token, digest)

    def publish(db, row, frozen, job_id):
        choice = db.get(ResearchChosenSetup, data["choice_id"])
        _remember(
            db,
            owner,
            experiment_id,
            token,
            raw,
            digest,
            "replay",
            choice,
            {
                "revision": row.revision,
                "version_id": frozen.id,
                "job_id": job_id,
            },
        )

    # The native retry key is private; the public receipt is written in the same transaction.
    native_token = "chosen_replay_" + hashlib.sha256(f"{owner}:{token}".encode()).hexdigest()
    response = library._enqueue(
        store,
        owner,
        experiment_id,
        data,
        native_token,
        digest,
        draft,
        evidence,
        role="replay",
        parents=parents,
        with_reuse=True,
        admission_guard=guard,
        publication_hook=publish,
    )
    with store.sessions() as db:
        experiment = library._owned(db, owner, experiment_id)
        prior = _prior(db, owner, token, digest)
        accepted = _response(
            db,
            experiment,
            db.get(ResearchChosenSetup, prior.choice_id),
            json.loads(prior.response),
            reused=response["reused"],
        )
    return {**accepted, **response}


def validate_snapshot(value):
    fields = {
        *decisions.IDENTITY,
        "report_job_id",
        "report_result_artifact",
        "analysis_job_id",
        "analysis_artifact",
        "inputs_artifact",
        "candidate_name",
        "trial_number",
        "capital",
        "currency",
        "dates",
        "sources",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or len(service.encoded(value)) > MAX_SNAPSHOT_BYTES
    ):
        raise ValueError("Invalid chosen setup identity")
    for key in ("source_job_id", "report_job_id"):
        shortlist._hex(value[key], 32, "chosen result")
    for key in ("source_result_artifact", "report_result_artifact", "config_id", "inputs_artifact"):
        shortlist._hex(value[key], 64, "chosen evidence")
    if (value["analysis_job_id"] is None) != (value["analysis_artifact"] is None):
        raise ValueError("Invalid chosen analysis")
    if value["analysis_job_id"] is not None:
        shortlist._hex(value["analysis_job_id"], 32, "chosen analysis job")
        shortlist._hex(value["analysis_artifact"], 64, "chosen analysis")
    if value["period"] not in ("full", "selection"):
        raise ValueError("Choose a selection-period setup")
    library._text(value["candidate_name"], 120, "Chosen candidate")
    if (
        type(value["capital"]) not in (int, float)
        or not math.isfinite(value["capital"])
        or value["capital"] <= 0
    ):
        raise ValueError("Invalid chosen capital")
    if value["currency"] is not None:
        library._text(value["currency"], 16, "Chosen currency")
    if not isinstance(value["dates"], dict) or set(value["dates"]) != {"from", "to"}:
        raise ValueError("Invalid chosen dates")
    for item in value["dates"].values():
        if item is not None and (
            not isinstance(item, str) or date.fromisoformat(item).isoformat() != item
        ):
            raise ValueError("Invalid chosen dates")
    if (
        value["dates"]["from"]
        and value["dates"]["to"]
        and value["dates"]["from"] > value["dates"]["to"]
    ):
        raise ValueError("Invalid chosen date order")
    if value["trial_number"] is not None and (
        type(value["trial_number"]) is not int or value["trial_number"] < 0
    ):
        raise ValueError("Invalid chosen trial")
    if not isinstance(value["sources"], dict) or not 1 <= len(value["sources"]) <= 8:
        raise ValueError("Invalid chosen sources")
    for strategy, source in value["sources"].items():
        if not isinstance(strategy, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", strategy):
            raise ValueError("Invalid chosen strategy identity")
        shortlist._hex(source, 32, "chosen source")


def validate_version(choice, version):
    if not version or version.experiment_id != choice.experiment_id or not version.portfolio:
        raise ValueError("Chosen setup has no retained native version")
    snapshot = json.loads(choice.snapshot)
    validate_snapshot(snapshot)
    draft, portfolio = json.loads(version.draft), json.loads(version.portfolio)
    library._bounded_json(draft)
    if (
        draft.get("optimizing") is not False
        or draft.get("equalWeights") is not False
        or settings_identity(portfolio.get("strategies")) != snapshot["config_id"]
        or settings_identity(draft.get("portfolio", {}).get("strategies")) != snapshot["config_id"]
        or portfolio.get("capital") != snapshot["capital"]
        or draft.get("portfolio") != portfolio
        or any(row.get("search") for row in portfolio["strategies"])
        or {row["id"]: row["source_id"] for row in portfolio["strategies"]} != snapshot["sources"]
        or version.parent_job_id != snapshot["source_job_id"]
        or version.parent_result_artifact != snapshot["source_result_artifact"]
    ):
        raise ValueError("Chosen setup rules differ from its frozen decision")
    return snapshot


def references(connection, tables):
    """Validate and retain bounded immutable choices in populated backups/restores."""
    from sqlalchemy.orm import Session

    from database.research_db import ResearchLibraryRequest

    expected = {"research_chosen_setups", "research_chosen_requests"}
    if not expected.intersection(tables):
        return set()
    if not expected.issubset(tables):
        raise ValueError("Chosen setup metadata tables are incomplete")
    if (
        "research_setup_versions" in tables
        and connection.exec_driver_sql(
            "SELECT 1 FROM research_setup_versions WHERE length(CAST(draft AS BLOB)) > ? OR length(CAST(portfolio AS BLOB)) > ? LIMIT 1",
            (library.MAX_DRAFT_BYTES, library.MAX_DRAFT_BYTES),
        ).first()
    ):
        raise ValueError("Chosen setup version exceeds its native size limit")
    for table in sorted(expected):
        if connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() > MAX_ROWS:
            raise ValueError("Chosen setup metadata exceeds its maintenance limit")
    for table, size_clause, bounds in (
        (
            "research_chosen_setups",
            "length(CAST(snapshot AS BLOB)) > ? OR length(name) > 120",
            (MAX_SNAPSHOT_BYTES,),
        ),
        (
            "research_chosen_requests",
            "length(CAST(payload AS BLOB)) > ? OR length(CAST(response AS BLOB)) > ?",
            (decisions.MAX_BODY_BYTES, MAX_RESPONSE_BYTES),
        ),
    ):
        if connection.exec_driver_sql(
            f"SELECT 1 FROM {table} WHERE {size_clause} LIMIT 1", bounds
        ).first():
            raise ValueError("Chosen setup metadata exceeds its size limit")
    if (
        connection.exec_driver_sql(
            "SELECT 1 FROM research_chosen_setups GROUP BY experiment_id HAVING count(*) > ? OR min(sequence) != 1 OR max(sequence) != count(*) LIMIT 1",
            (MAX_CHOICES,),
        ).first()
        or connection.exec_driver_sql(
            "SELECT 1 FROM research_chosen_requests GROUP BY experiment_id HAVING count(*) > ? LIMIT 1",
            (MAX_REQUESTS,),
        ).first()
    ):
        raise ValueError("Chosen setup history exceeds its bound or sequence")
    roots = set()
    with Session(bind=connection) as db:
        for choice in db.scalars(select(ResearchChosenSetup)).yield_per(8):
            shortlist._hex(choice.id, 32, "chosen setup")
            library._text(choice.name, 120, "Chosen setup name")
            experiment, _, event, member = _decision(
                db,
                choice.owner,
                choice.experiment_id,
                choice.decision_id,
                choice.event_id,
                current=False,
            )
            version = db.get(ResearchSetupVersion, choice.version_id)
            snapshot = validate_version(choice, version)
            portfolio = json.loads(version.portfolio)
            link = db.get(ResearchLibraryJob, (choice.experiment_id, snapshot["source_job_id"]))
            if (
                snapshot != _snapshot(member, portfolio)
                or type(choice.sequence) is not int
                or choice.sequence < 1
                or not math.isfinite(choice.created_at)
                or choice.created_at < event.created_at
                or version.name != choice.name
                or version.parent_trial_id
                != (snapshot["config_id"] if member["origin_kind"] == "study" else None)
                or not link
                or link.version_id != version.parent_version_id
                or db.scalar(
                    select(func.count())
                    .select_from(ResearchChosenRequest)
                    .where(
                        ResearchChosenRequest.choice_id == choice.id,
                        ResearchChosenRequest.kind == "choose",
                    )
                )
                != 1
            ):
                raise ValueError("Chosen setup differs from its original Keep evidence")
            library._check_sources(db, choice.owner, json.loads(version.draft))
            if not decisions._pin_owned(db, choice.owner, decisions._selection_pin(member)):
                raise ValueError("Chosen setup report belongs to another account")
            _source_fence(db, choice.owner, choice.experiment_id, snapshot)
            roots.update(
                snapshot[key]
                for key in (
                    "source_result_artifact",
                    "report_result_artifact",
                    "analysis_artifact",
                    "inputs_artifact",
                )
                if snapshot[key]
            )
        for request in db.scalars(select(ResearchChosenRequest)).yield_per(8):
            payload, response = json.loads(request.payload), json.loads(request.response)
            experiment, choice = _owned(db, request.owner, request.experiment_id, request.choice_id)
            version = db.get(ResearchSetupVersion, choice.version_id)
            snapshot = validate_version(choice, version)
            base = {"kind", "experiment_id", "revision"}
            fields = {
                "choose": {"decision_id", "event_id", "name"},
                "use": {"choice_id", "sources", "mode"},
                "replay": {"choice_id"},
            }
            if (
                request.kind not in fields
                or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request.token)
                or not isinstance(payload, dict)
                or set(payload) - (base | fields.get(request.kind, set()))
                or not isinstance(response, dict)
                or payload.get("kind") != request.kind
                or payload.get("experiment_id") != request.experiment_id
                or hashlib.sha256(service.encoded(payload)).hexdigest() != request.payload_hash
                or type(payload.get("revision")) is not int
                or payload["revision"] < 1
                or response.get("revision") != payload["revision"] + 1
                or response["revision"] > experiment.revision
                or not math.isfinite(request.created_at)
                or request.created_at < choice.created_at
            ):
                raise ValueError("Chosen setup retry receipt has invalid binding")
            if request.kind == "choose":
                if set(response) != {"revision"} or payload != {
                    "kind": "choose",
                    "experiment_id": choice.experiment_id,
                    "revision": payload["revision"],
                    "decision_id": choice.decision_id,
                    "event_id": choice.event_id,
                    "name": choice.name,
                }:
                    raise ValueError("Chosen setup receipt points to different Keep evidence")
                continue
            if payload.get("choice_id") != choice.id:
                raise ValueError("Chosen setup action points to a different choice")
            if request.kind == "use":
                _validate_use_receipt(db, request, choice, version, snapshot, payload, response)
            else:
                if set(response) != {"revision", "version_id", "job_id"}:
                    raise ValueError("Chosen replay has invalid admission metadata")
                native_token = (
                    "chosen_replay_"
                    + hashlib.sha256(f"{request.owner}:{request.token}".encode()).hexdigest()
                )
                native = db.get(ResearchLibraryRequest, (request.owner, native_token))
                replay_version = db.get(ResearchSetupVersion, response["version_id"])
                job = db.get(ResearchJob, response["job_id"])
                if (
                    not native
                    or native.kind != "replay"
                    or native.payload_hash != request.payload_hash
                    or native.experiment_id != choice.experiment_id
                    or native.job_id != response["job_id"]
                    or native.version_id != response["version_id"]
                    or not job
                    or job.owner != request.owner
                    or not replay_version
                    or replay_version.experiment_id != choice.experiment_id
                    or replay_version.parent_job_id != snapshot["source_job_id"]
                    or replay_version.parent_result_artifact != snapshot["source_result_artifact"]
                    or replay_version.parent_trial_id != version.parent_trial_id
                    or settings_identity(json.loads(replay_version.portfolio)["strategies"])
                    != snapshot["config_id"]
                ):
                    raise ValueError("Chosen replay differs from its native admitted work")
    return roots


def _validate_use_receipt(db, request, choice, version, snapshot, payload, response):
    if set(response) != {"revision", "applied_version_id", "preserved_version_id", "changes"}:
        raise ValueError("Chosen setup use has invalid admission metadata")
    applied = db.get(ResearchSetupVersion, response["applied_version_id"])
    if not applied or applied.experiment_id != choice.experiment_id:
        raise ValueError("Chosen setup use references another experiment's version")
    if response["preserved_version_id"]:
        preserved = db.get(ResearchSetupVersion, response["preserved_version_id"])
        if (
            not preserved
            or preserved.experiment_id != choice.experiment_id
            or preserved.number >= applied.number
            and applied.id != version.id
        ):
            raise ValueError("Chosen setup use lost the displaced draft")
    draft = json.loads(applied.draft)
    original = json.loads(version.draft)
    mode = payload.get("mode", "backtest")
    sources = payload.get("sources") or {}
    if (
        mode not in ("backtest", "optimize")
        or not isinstance(sources, dict)
        or set(sources) - snapshot["sources"].keys()
    ):
        raise ValueError("Chosen setup use has invalid replacement input")
    mapping = {key: sources.get(key, value) for key, value in snapshot["sources"].items()}
    changed = mapping != snapshot["sources"]
    expected_portfolio = copy.deepcopy(original["portfolio"])
    for row in expected_portfolio["strategies"]:
        row["source_id"] = mapping[row["id"]]
    if changed:
        expected_portfolio.pop("date_from", None)
        expected_portfolio.pop("date_to", None)
    if (
        draft["portfolio"] != expected_portfolio
        or draft["optimizing"] != (mode == "optimize")
        or draft["equalWeights"] is not False
        or applied.id != version.id
        and (
            applied.parent_version_id != version.id
            or any(
                getattr(applied, key) != getattr(version, key)
                for key in library.PARENTS
                if key != "parent_version_id"
            )
        )
    ):
        raise ValueError("Chosen setup use changed the frozen trade rules")
    library._check_sources(db, request.owner, draft)
    changes = response["changes"]
    if (
        not isinstance(changes, dict)
        or set(changes) != {"sources", "fields", "rules_unchanged"}
        or changes["rules_unchanged"] is not True
        or not isinstance(changes["sources"], list)
        or len(changes["sources"]) > 8
        or not isinstance(changes["fields"], list)
        or len(changes["fields"]) > 4
    ):
        raise ValueError("Chosen setup source changes have invalid metadata")
    expected_ids = {key for key in mapping if mapping[key] != snapshot["sources"][key]}
    actual_ids = set()
    from services.research_setup_reuse import _source_summary

    for item in changes["sources"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"strategy_id", "name", "before", "after"}
            or item["strategy_id"] not in expected_ids
            or item["strategy_id"] in actual_ids
        ):
            raise ValueError("Chosen setup source changes do not match applied inputs")
        strategy = item["strategy_id"]
        row = next(row for row in original["portfolio"]["strategies"] if row["id"] == strategy)
        if (
            item["name"] != row["name"]
            or item["before"] != _source_summary(original["sources"][snapshot["sources"][strategy]])
            or item["after"] != _source_summary(draft["sources"][mapping[strategy]])
        ):
            raise ValueError("Chosen setup source receipt differs from its applied version")
        actual_ids.add(strategy)
    expected_fields = []
    if changed:
        expected_fields.extend(
            {"key": key, "before": original["portfolio"][key], "after": None}
            for key in ("date_from", "date_to")
            if key in original["portfolio"]
        )
    if mode == "optimize":
        expected_fields.append({"key": "mode", "before": "backtest", "after": "optimize"})
    if actual_ids != expected_ids or changes["fields"] != expected_fields:
        raise ValueError("Chosen setup changes differ from its frozen request")
