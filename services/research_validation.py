"""Explicit later-period review and native frozen-input admission for one candidate."""

import hashlib

from sqlalchemy import func, or_, select

from database.research_db import (
    ResearchCandidateReport,
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryJob,
    ResearchLibraryRequest,
    ResearchSetupVersion,
    ResearchSource,
)
from research.report_contract import fingerprint, report_context, settings_identity
from services import research_candidates as candidates
from services import research_decisions as decisions
from services import research_library as library
from services import research_shortlist as shortlist
from services import scanner_research_service as service

VERSION = "research-validation-v1"
MAX_LINEAGE = 16
MAX_EVALUATIONS = 30
MAX_REQUESTS = 10000


def canonical_source(store, owner, experiment_id, job_id, config_id=None):
    """Resolve retained native lineage, never a child report's stripped reservation.

    Returns a detached original completed ResearchJob and its accepted configuration
    identity. Every edge requires owned library membership and an unchanged parent
    artifact. A copied setup/new calculation is a new source, not a lineage edge.
    """
    shortlist._hex(experiment_id, 32, "experiment")
    shortlist._hex(job_id, 32, "result")
    if config_id is not None:
        shortlist._hex(config_id, 64, "candidate")
    visited = set()
    study_claim = None
    for _ in range(MAX_LINEAGE):
        if job_id in visited:
            raise ValueError("The saved result has an invalid replay lineage")
        visited.add(job_id)
        with store.sessions() as db:
            _, job = shortlist._linked(db, owner, experiment_id, job_id)
            if job.status != "completed" or not job.result_artifact:
                raise ValueError("Choose a completed saved result")
            candidate = db.scalar(
                select(ResearchCandidateReport).where(
                    ResearchCandidateReport.report_job_id == job_id
                )
            )
            native = db.get(ResearchExperiment, job_id)
            link = db.get(ResearchLibraryJob, (experiment_id, job_id))
            version = db.get(ResearchSetupVersion, link.version_id) if link.version_id else None
            replay_edge = (
                (
                    link.role,
                    version.experiment_id,
                    version.parent_job_id,
                    version.parent_result_artifact,
                    version.parent_trial_id,
                )
                if version
                else None
            )
            candidate_edge = (
                (
                    candidate.owner,
                    candidate.study_job_id,
                    candidate.parent_result_artifact,
                    candidate.config_id,
                    native.parent_job_id if native else None,
                )
                if candidate
                else None
            )
        bundle = service.read_artifact(store, job.result_artifact)
        result = bundle.get("result")
        if (
            bundle.get("job_id") not in (None, job.id)
            or bundle.get("kind") not in ("portfolio_backtest", "portfolio_optimize")
            or not isinstance(result, dict)
        ):
            raise ValueError("The saved result does not retain a native portfolio")
        origin = result.get("replay_origin")
        if not origin and not candidate_edge:
            if bundle["kind"] == "portfolio_optimize":
                study = result.get("experiment", {})
                config_id = config_id or study.get("recommendation_id")
                selected = candidates._selected(study, config_id)
                if settings_identity(selected.get("strategies")) != config_id:
                    raise ValueError("The saved candidate settings changed")
            else:
                actual = settings_identity(result.get("strategies"))
                if actual is None or (config_id is not None and config_id != actual):
                    raise ValueError("The saved result does not match this candidate")
                config_id = actual
            if study_claim is not None and study_claim != job.id:
                raise ValueError("The saved replay does not match its original study")
            return job, config_id
        if not isinstance(origin, dict):
            raise ValueError("The saved result does not retain its exact replay origin")
        parent_id = shortlist._hex(origin.get("parent_job_id"), 32, "original result")
        parent_artifact = shortlist._hex(
            origin.get("parent_result_artifact"), 64, "original evidence"
        )
        actual = settings_identity(result.get("strategies"))
        if (
            actual is None
            or origin.get("config_id") != actual
            or (config_id is not None and config_id != actual)
            or origin.get("period") not in ("full", "selection", "evaluation")
        ):
            raise ValueError("The saved replay does not match this candidate")
        config_id = actual
        if origin.get("study_job_id") is not None:
            claimed = shortlist._hex(origin["study_job_id"], 32, "original study")
            if study_claim is not None and study_claim != claimed:
                raise ValueError("The saved replay has conflicting study identities")
            study_claim = claimed
        if candidate_edge:
            if candidate_edge != (owner, parent_id, parent_artifact, config_id, parent_id):
                raise ValueError("The candidate report does not match its original study")
            proof = result.get("candidate_report", {})
            if (
                proof.get("study_job_id") != parent_id
                or proof.get("parent_result_artifact") != parent_artifact
                or proof.get("config_id") != config_id
                or proof.get("verification", {}).get("summary") != "matched"
            ):
                raise ValueError("The candidate report does not retain its verification")
        elif (
            not replay_edge
            or replay_edge[0] not in ("replay", "validation")
            or replay_edge[1:4] != (experiment_id, parent_id, parent_artifact)
            or replay_edge[4] not in (None, config_id)
        ):
            raise ValueError("The saved replay does not match its retained setup lineage")
        inputs = service.read_artifact(store, bundle["inputs_artifact"])
        if (
            inputs.get("replay_origin") != origin
            or inputs.get("parent_result_artifact") != parent_artifact
            or not inputs.get("frozen_prices")
        ):
            raise ValueError("The saved replay inputs do not match their exact source")
        del inputs, bundle, result
        with store.sessions() as db:
            _, parent = shortlist._linked(db, owner, experiment_id, parent_id)
            if parent.status != "completed" or parent.result_artifact != parent_artifact:
                raise ValueError("The original saved result changed; reopen it before continuing")
        job_id = parent_id
    raise ValueError("The saved replay lineage is too long to verify")


def _source_fence(db, owner, experiment_id, source, version_id=None):
    experiment, current = shortlist._linked(db, owner, experiment_id, source.id)
    link = db.get(ResearchLibraryJob, (experiment_id, source.id))
    if (
        current.status != "completed"
        or current.result_artifact != source.result_artifact
        or (version_id is not None and link.version_id != version_id)
    ):
        raise ValueError("The original saved result changed; reopen it before continuing")
    return experiment, link


def _later_jobs(store, owner, experiment_id, member):
    with store.sessions() as db:
        return db.scalars(
            select(ResearchJob)
            .join(ResearchLibraryJob, ResearchLibraryJob.job_id == ResearchJob.id)
            .join(ResearchSetupVersion, ResearchSetupVersion.id == ResearchLibraryJob.version_id)
            .where(
                ResearchJob.owner == owner,
                ResearchLibraryJob.experiment_id == experiment_id,
                ResearchLibraryJob.role == "validation",
                ResearchSetupVersion.parent_job_id == member["source_job_id"],
                ResearchSetupVersion.parent_result_artifact == member["source_result_artifact"],
                or_(
                    ResearchSetupVersion.parent_trial_id == member["config_id"],
                    ResearchSetupVersion.parent_trial_id.is_(None),
                ),
            )
            .order_by(ResearchJob.created_at.desc(), ResearchJob.id)
            .limit(MAX_EVALUATIONS + 1)
        ).all()


def _pending_matches(store, owner, job, member):
    with store.sessions() as db:
        native = db.get(ResearchExperiment, job.id)
        source = db.get(ResearchSource, job.source_id)
        if not native or native.kind != "portfolio_backtest" or not source or source.owner != owner:
            return False
        artifact = source.artifact
    inputs = service.read_artifact(store, artifact)
    origin = inputs.get("replay_origin", {})
    return (
        inputs.get("frozen_prices") is True
        and inputs.get("parent_result_artifact") == member["source_result_artifact"]
        and all(
            origin.get(key) == value
            for key, value in {
                "parent_job_id": member["source_job_id"],
                "parent_result_artifact": member["source_result_artifact"],
                "config_id": member["config_id"],
                "period": "evaluation",
            }.items()
        )
        and settings_identity(inputs.get("portfolio", {}).get("strategies")) == member["config_id"]
    )


def context(store, owner, experiment_id, job_id, config_id=None):
    """Read saved candidate, reserved range and known exposure; never acquire or open evidence."""
    from services.research_decision_targets import selection_member

    source, config_id = canonical_source(store, owner, experiment_id, job_id, config_id)
    bundle, result, _, origin = shortlist._evidence(store, source, config_id)
    member = {
        "source_job_id": source.id,
        "source_result_artifact": source.result_artifact,
        **origin,
    }
    recorded = report_context(
        result,
        job_id=source.id,
        result_artifact=source.result_artifact,
        inputs_artifact=bundle["inputs_artifact"],
    )
    # The verified study basis describes the common input timeline for every
    # accepted configuration. A report's trade dates/CSV padding are not that range.
    basis = recorded["evaluation_basis"]
    shared_period = basis.get("period", {}) if basis.get("status") == "verified" else {}
    selection = {key: shared_period.get(key) for key in ("from", "to")}
    reservation = decisions._reservation(result)
    source_bundle = {
        "kind": bundle["kind"],
        "inputs_artifact": bundle["inputs_artifact"],
        "result": decisions._context_result(result),
    }
    del bundle, result
    available_selection = False
    try:
        member = selection_member(store, owner, experiment_id, source.id, config_id)
        available_selection = True
        recorded = member["report_context"]
        basis = recorded["evaluation_basis"]
        period = basis.get("period", {}) if basis.get("status") == "verified" else {}
        selection = {key: period.get(key) or recorded["dates"].get(key) for key in ("from", "to")}
    except (ValueError, OSError, KeyError, TypeError):
        # A not-yet-prepared alternative still has accepted settings and a reserved
        # range. Its source winner must never stand in for an opened selection report.
        pass
    with store.sessions() as db:
        experiment, _ = _source_fence(db, owner, experiment_id, source)
        revision, archived = experiment.revision, experiment.archived
        current_head = decisions._head(db, owner, experiment_id, member)
        current = decisions._summary(store, db, experiment, current_head) if current_head else None
        use = decisions._evidence_use(
            store,
            db,
            owner,
            experiment_id,
            member,
            source_bundle=source_bundle,
            selection_available=available_selection,
        )
    items = []
    invalid_evidence = False
    jobs = _later_jobs(store, owner, experiment_id, member) if reservation else []
    if reservation and source_bundle["result"].get("validation"):
        try:
            pin = decisions._later_pin(
                store,
                owner,
                experiment_id,
                member,
                source.id,
                embedded=True,
                source_bundle=source_bundle,
            )
            with store.sessions() as db:
                receipt = decisions._later_receipt(store, db, owner, pin)
            if receipt["available"]:
                items.append(
                    {
                        "job_id": source.id,
                        "status": "completed",
                        "progress": 100,
                        "evidence_id": receipt["id"],
                        "dates": receipt["dates"],
                        "view": receipt["view"],
                        "identity": {
                            "result_artifact": pin["result_artifact"],
                            "analysis_artifact": pin["analysis_artifact"],
                            "config_id": pin["report_context"]["config_id"],
                        },
                    }
                )
        except (ValueError, OSError, KeyError, TypeError):
            pass
    for job in jobs[:MAX_EVALUATIONS]:
        try:
            if job.status == "completed":
                pin = decisions._later_pin(
                    store, owner, experiment_id, member, job.id, source_bundle=source_bundle
                )
                with store.sessions() as db:
                    receipt = decisions._later_receipt(store, db, owner, pin)
                if not receipt["available"]:
                    raise ValueError("Saved later evidence is unavailable")
                items.append(
                    {
                        "job_id": job.id,
                        "status": "completed",
                        "progress": 100,
                        "evidence_id": receipt["id"],
                        "dates": receipt["dates"],
                        "view": receipt["view"],
                        "identity": {
                            "result_artifact": pin["result_artifact"],
                            "analysis_artifact": pin["analysis_artifact"],
                            "config_id": pin["report_context"]["config_id"],
                        },
                    }
                )
            elif _pending_matches(store, owner, job, member):
                receipt = service.job_receipt(store, job, experiment_id=experiment_id)
                items.append(
                    {
                        "job_id": job.id,
                        "status": job.status,
                        "progress": job.progress,
                        "resumable": receipt.get("resumable", False),
                    }
                )
        except (ValueError, OSError, KeyError, TypeError):
            # Native setup metadata names this exact source/configuration. If its
            # saved inputs still match, silently starting another would hide a
            # damaged retained evaluation and defeat the canonical retry receipt.
            try:
                invalid_evidence = invalid_evidence or _pending_matches(store, owner, job, member)
            except (ValueError, OSError, KeyError, TypeError):
                invalid_evidence = True
            continue
    complete = next((item for item in items if item["status"] == "completed"), None)
    waiting = next((item for item in items if item["status"] in service.ACTIVE), None)
    stopped = next((item for item in items if item["status"] != "completed"), None)
    if complete:
        action = {
            "kind": "open",
            **{key: complete[key] for key in ("job_id", "evidence_id", "view")},
        }
    elif waiting or stopped:
        action = {"kind": "progress", "job_id": (waiting or stopped)["job_id"]}
    elif not reservation:
        action = {"kind": "unavailable", "reason": "This result has no reserved later period."}
    elif archived:
        action = {
            "kind": "unavailable",
            "reason": "Restore this experiment to evaluate its later period.",
        }
    elif invalid_evidence:
        action = {
            "kind": "unavailable",
            "reason": "Saved later evidence could not be verified; review its run before continuing.",
        }
    elif len(jobs) > MAX_EVALUATIONS:
        action = {
            "kind": "unavailable",
            "reason": "Review this candidate's saved evaluations before starting another.",
        }
    else:
        action = {"kind": "prepare"}
    with store.sessions() as db:
        _source_fence(db, owner, experiment_id, source)
    return {
        "version": VERSION,
        "experiment_id": experiment_id,
        "revision": revision,
        "archived": archived,
        "current": current,
        "candidate": {
            **{
                key: member[key]
                for key in (
                    "source_job_id",
                    "source_result_artifact",
                    "config_id",
                    "trial_number",
                    "is_objective_winner",
                )
            },
            "report_job_id": member.get("report_job_id") if available_selection else None,
        },
        "selection": selection,
        "reservation": reservation,
        "evidence_use": use,
        "evaluations": items,
        "action": action,
    }


def _client_request(data, experiment_id, candidate):
    token, _ = library._request(data, "validation")
    request = "validation_request_" + hashlib.sha256(token.encode()).hexdigest()
    digest = fingerprint(
        {"experiment_id": experiment_id, "candidate": candidate, "revision": data.get("revision")}
    )
    return request, digest


def _remember(store, owner, experiment_id, source, token, digest, job_id):
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment, _ = _source_fence(db, owner, experiment_id, source)
        library._editable(experiment)
        prior = library._previous(db, owner, token, digest)
        if prior:
            if prior.job_id != job_id:
                raise ValueError("This validation request already opened a different result")
            return
        link = db.get(ResearchLibraryJob, (experiment_id, job_id))
        if link is None:
            raise LookupError("This saved evaluation is not part of the experiment")
        if (
            db.scalar(
                select(func.count())
                .select_from(ResearchLibraryRequest)
                .where(
                    ResearchLibraryRequest.owner == owner,
                    ResearchLibraryRequest.experiment_id == experiment_id,
                )
            )
            >= MAX_REQUESTS
        ):
            raise ValueError("This experiment has reached its saved request limit")
        db.add(
            ResearchLibraryRequest(
                owner=owner,
                token=token,
                payload_hash=digest,
                experiment_id=experiment_id,
                kind="validation",
                version_id=link.version_id,
                job_id=job_id,
            )
        )


def prepare(store, owner, experiment_id, job_id, data):
    """Explicitly queue once, or reopen exact retained evaluation/progress."""
    from services.research_portfolio import replay_inputs

    library._object(
        data, {"revision", "request_id", "config_id", "source_result_artifact"}, "later evaluation"
    )
    expected = shortlist._hex(data.get("source_result_artifact"), 64, "original result evidence")
    if type(data.get("revision")) is not int or data["revision"] < 1:
        raise ValueError("Supply the saved research revision")
    source, config_id = canonical_source(store, owner, experiment_id, job_id, data.get("config_id"))
    if source.result_artifact != expected:
        raise ValueError("The original saved result changed; reopen it before continuing")
    identity = {
        "source_job_id": source.id,
        "source_result_artifact": expected,
        "config_id": config_id,
    }
    token, digest = _client_request(data, experiment_id, identity)
    with store.sessions() as db:
        experiment, link = _source_fence(db, owner, experiment_id, source)
        library._editable(experiment)
        parent_version_id = link.version_id
        prior = library._previous(db, owner, token, digest)
        previous_job_id = prior.job_id if prior else None
        if (
            not prior
            and db.scalar(
                select(func.count())
                .select_from(ResearchLibraryRequest)
                .where(
                    ResearchLibraryRequest.owner == owner,
                    ResearchLibraryRequest.experiment_id == experiment_id,
                )
            )
            >= MAX_REQUESTS
        ):
            raise ValueError("This experiment has reached its saved request limit")
    inspected = context(store, owner, experiment_id, source.id, config_id)
    if previous_job_id:
        job_id = previous_job_id
        reused = True
    elif inspected["action"]["kind"] in ("open", "progress"):
        job_id = inspected["action"]["job_id"]
        reused = True
    else:
        if inspected["action"]["kind"] != "prepare":
            raise ValueError(
                inspected["action"].get("reason", "This later period cannot be evaluated")
            )
        with store.sessions() as db:
            experiment, _ = _source_fence(db, owner, experiment_id, source, parent_version_id)
            library._revision(experiment, data.get("revision"))
            library._editable(experiment)
        source, evidence = replay_inputs(
            store,
            owner,
            source.id,
            trial_id=config_id if inspected["candidate"]["trial_number"] is not None else None,
            period="evaluation",
        )
        if source.result_artifact != expected:
            raise ValueError("The original saved result changed; reopen it before continuing")
        # Stable canonical identity, independent of a browser's new request UUID or
        # current draft revision. Native _enqueue serializes concurrent admissions.
        canonical = fingerprint(
            {
                "version": VERSION,
                "experiment_id": experiment_id,
                **identity,
                "reservation": inspected["reservation"],
            }
        )
        canonical_token = "validation_run_" + canonical
        draft = library.fresh_draft()
        draft.update(portfolio=evidence["portfolio"], equalWeights=False)
        draft = library.normalize_draft(store, owner, draft)
        parents = {
            "parent_job_id": source.id,
            "parent_result_artifact": expected,
            "parent_trial_id": config_id
            if inspected["candidate"]["trial_number"] is not None
            else None,
            "parent_version_id": parent_version_id,
        }
        response = library._enqueue(
            store,
            owner,
            experiment_id,
            data,
            canonical_token,
            canonical,
            draft,
            evidence,
            role="validation",
            parents=parents,
            with_reuse=True,
        )
        job_id = response["job"]["id"]
        reused = response["reused"]
    inspected = context(store, owner, experiment_id, source.id, config_id)
    if not any(item["job_id"] == job_id for item in inspected["evaluations"]):
        raise ValueError("The saved later evaluation could not be verified; reopen its run")
    _remember(store, owner, experiment_id, source, token, digest, job_id)
    return {
        "job": service.job_receipt(
            store,
            service.get_job(store, owner, job_id),
            include_result=True,
            experiment_id=experiment_id,
        ),
        "reused": reused,
        "context": inspected,
    }
