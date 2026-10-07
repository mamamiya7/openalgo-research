"""Recalculate original baseline rules on one study's exact frozen selection cohort."""

import copy
import hashlib

from sqlalchemy import func, select

from database.research_db import (
    ResearchExperiment,
    ResearchLibraryJob,
    ResearchLibraryRequest,
    ResearchSource,
)
from research.evaluation_basis import build_evaluation_basis, comparison_status
from research.portfolio import execution_versions, normalize
from research.portfolio_coverage import prepare as prepare_coverage
from research.report_contract import fingerprint, settings_identity
from services import research_library as library
from services import research_shortlist as shortlist
from services import research_validation as validation
from services import scanner_research_service as service

VERSION = "research-matched-baseline-v1"
ENTRY_KEYS = (
    "trade_horizon",
    "entry_time",
    "exit_time",
    "modes",
    "entry_priority",
    "priority_seed",
)
PROVENANCE_KEYS = (
    "exchange",
    "interval",
    "native_price_policy",
    "adjustment_basis",
    "synthetic",
    "calendar_basis",
    "calendar_admission",
    "temporal_version",
)
IDENTITY_KEYS = (
    "study_job_id",
    "study_result_artifact",
    "baseline_job_id",
    "baseline_result_artifact",
)


def _sources(store, owner, experiment_id, study_id, baseline_id):
    shortlist._hex(experiment_id, 32, "experiment")
    shortlist._hex(study_id, 32, "study")
    shortlist._hex(baseline_id, 32, "baseline")
    with store.sessions() as db:
        experiment, study = shortlist._linked(db, owner, experiment_id, study_id)
        _, baseline = shortlist._linked(db, owner, experiment_id, baseline_id)
        for job in (study, baseline):
            if job.status != "completed" or not job.result_artifact:
                raise ValueError("Choose completed study and baseline results")
        versions = {
            job.id: db.get(ResearchLibraryJob, (experiment_id, job.id)).version_id
            for job in (study, baseline)
        }
        return study, baseline, versions, experiment.revision, experiment.archived


def _fence(db, owner, experiment_id, study, baseline, versions):
    experiment, _ = validation._source_fence(db, owner, experiment_id, study, versions[study.id])
    validation._source_fence(db, owner, experiment_id, baseline, versions[baseline.id])
    return experiment


def _identity(study, baseline):
    return {
        "version": VERSION,
        "study_job_id": study.id,
        "study_result_artifact": study.result_artifact,
        "baseline_job_id": baseline.id,
        "baseline_result_artifact": baseline.result_artifact,
    }


def _recipe(store, owner, study, baseline):
    """Pure admission over retained native inputs; no acquisition or evaluator call."""
    from services.research_portfolio import replay_inputs

    study_bundle = service.read_artifact(store, study.result_artifact)
    if study_bundle.get("kind") != "portfolio_optimize":
        raise ValueError("Choose an original optimization study")
    study_result = study_bundle["result"]
    expected = copy.deepcopy(study_result.get("evaluation_basis"))
    if not comparison_status(expected, expected)["compatible"]:
        raise ValueError("This study does not retain a verified evaluation timeline")
    period = expected["comparison"]["period"]
    if period not in ("selection", "full"):
        raise ValueError("Choose the study's selection period")
    study_portfolio = copy.deepcopy(study_result["portfolio"])
    del study_result, study_bundle

    baseline_bundle = service.read_artifact(store, baseline.result_artifact)
    result = baseline_bundle.get("result", {})
    if baseline_bundle.get("kind") != "portfolio_backtest" or result.get("replay_origin", {}).get(
        "study_job_id"
    ):
        raise ValueError("Choose an original baseline backtest")
    if result.get("replay_origin", {}).get("period") == "evaluation":
        raise ValueError("A later-period result cannot be used as the baseline")
    original_basis = copy.deepcopy(result.get("evaluation_basis"))
    if not comparison_status(original_basis, original_basis)["compatible"]:
        raise ValueError("This baseline does not retain a verified evaluation timeline")
    chosen = copy.deepcopy(result["strategies"])
    baseline_portfolio = copy.deepcopy(result["portfolio"])
    baseline_inputs_id = baseline_bundle["inputs_artifact"]
    del result, baseline_bundle

    # Source ids and their strategy order are immutable identities, not display names.
    def source_keys(portfolio):
        return [(row["id"], row["source_id"]) for row in portfolio["strategies"]]

    if source_keys(study_portfolio) != source_keys(baseline_portfolio):
        raise ValueError("Study and baseline must use the same signal files and strategy order")
    if {row["id"] for row in chosen} != {row["id"] for row in study_portfolio["strategies"]}:
        raise ValueError("The baseline's saved strategy identities do not match")
    differences = comparison_status(original_basis, expected)["differences"]
    incompatible = [
        key for key in differences if key in ("currency", "capital", "execution", "costs")
    ]
    if incompatible:
        raise ValueError("Study and baseline must use the same " + ", ".join(incompatible))
    definitions = {row["id"]: row for row in chosen}
    for row in study_portfolio["strategies"]:
        before = definitions[row["id"]]["config"]
        if any(before.get(key) != row["config"].get(key) for key in ENTRY_KEYS):
            raise ValueError("Study and baseline must use the same entry timing and order")
    baseline_inputs = service.read_artifact(store, baseline_inputs_id)
    baseline_provenance = {
        key: baseline_inputs["snapshot"]["provenance"].get(key) for key in PROVENANCE_KEYS
    }
    baseline_versions = baseline_inputs["versions"]
    del baseline_inputs
    fresh_study, evidence = replay_inputs(store, owner, study.id, period="selection")
    if fresh_study.result_artifact != study.result_artifact:
        raise ValueError("The saved study changed; reopen it before continuing")
    provenance = {key: evidence["snapshot"]["provenance"].get(key) for key in PROVENANCE_KEYS}
    if provenance != baseline_provenance:
        raise ValueError(
            "Study and baseline must use the same recorded price and calendar policies"
        )
    portfolio = copy.deepcopy(evidence["portfolio"])
    portfolio["name"] = (baseline_portfolio["name"][:95] + " · Matched baseline")[:120]
    for row in portfolio["strategies"]:
        original = definitions[row["id"]]
        row.update(
            name=original["name"],
            config=copy.deepcopy(original["config"]),
            allocation_pct=original["allocation_pct"],
            search={},
        )
    portfolio = normalize(portfolio)
    versions = execution_versions(portfolio)
    if any(baseline_versions.get(key) != value for key, value in versions.items()):
        raise ValueError("This baseline needs its recorded engine version to reproduce its rules")
    evidence.update(
        portfolio=portfolio,
        versions=versions,
        strategies=[
            {**row, **definitions[row["id"]], "search": {}} for row in evidence["strategies"]
        ],
    )
    evidence.pop("replay_origin", None)
    evidence.pop("candidate_report", None)
    evidence.pop("matched_baseline_origin", None)
    # Keep study exclusions, then prove baseline rules do not require new gaps or
    # entries. This never reinstates rows excluded by the study's maximum window.
    evidence = prepare_coverage(evidence)
    actual = build_evaluation_basis(evidence, period=period)
    compared = comparison_status(expected, actual)
    if not compared["compatible"]:
        raise ValueError(
            "These baseline rules cannot use the study's exact "
            + ", ".join(compared["differences"])
        )
    origin = {
        **_identity(study, baseline),
        "period": period,
        "config_id": settings_identity(chosen),
        "evaluation_basis": expected,
    }
    origin["recipe_id"] = fingerprint(origin)
    evidence["matched_baseline_origin"] = origin
    return evidence, differences


def verify_reconstruction(evidence, result):
    """Verify actual engine output; never replace its metrics with expected values."""
    expected = evidence["matched_baseline_origin"]
    recipe = {key: value for key, value in expected.items() if key != "recipe_id"}
    if (
        expected.get("version") != VERSION
        or expected.get("recipe_id") != fingerprint(recipe)
        or expected.get("period") not in ("selection", "full")
        or settings_identity(result.get("strategies")) != expected.get("config_id")
        or not comparison_status(expected.get("evaluation_basis"), result.get("evaluation_basis"))[
            "compatible"
        ]
    ):
        raise ValueError("Matched baseline verification failed; the original results are unchanged")
    result["matched_baseline_origin"] = copy.deepcopy(expected)
    result["matched_baseline_origin"]["verification"] = {"settings": "matched", "basis": "matched"}


def _saved(store, owner, experiment_id, identity, evidence):
    token = "matched_baseline_" + fingerprint(identity)
    with store.sessions() as db:
        prior = db.get(ResearchLibraryRequest, (owner, token))
        if prior is None:
            return None
        if prior.experiment_id != experiment_id:
            raise ValueError("Saved baseline belongs to a different experiment")
        _, job = shortlist._linked(db, owner, experiment_id, prior.job_id)
        native = db.get(ResearchExperiment, job.id)
        source = db.get(ResearchSource, job.source_id)
        link = db.get(ResearchLibraryJob, (experiment_id, job.id))
        if (
            not native
            or native.kind != "portfolio_backtest"
            or not source
            or source.owner != owner
            or link.version_id != prior.version_id
        ):
            raise ValueError("The saved matched baseline link could not be verified")
        inputs_id = source.artifact
    retained = service.read_artifact(store, inputs_id)
    expected = evidence["matched_baseline_origin"]
    if (
        retained.get("matched_baseline_origin") != expected
        or retained.get("frozen_prices") is not True
    ):
        raise ValueError("The saved matched baseline inputs could not be verified")
    del retained
    if job.status == "completed":
        bundle = service.read_artifact(store, job.result_artifact)
        original = bundle["result"].get("matched_baseline_origin", {})
        if any(original.get(key) != value for key, value in expected.items()) or original.get(
            "verification"
        ) != {"settings": "matched", "basis": "matched"}:
            raise ValueError("The saved matched baseline could not be verified")
        verify_reconstruction(
            evidence, {key: bundle["result"].get(key) for key in ("strategies", "evaluation_basis")}
        )
    return job


def _inspect(store, owner, experiment_id, study_id, baseline_id):
    study, baseline, versions, revision, archived = _sources(
        store, owner, experiment_id, study_id, baseline_id
    )
    view = {
        "version": VERSION,
        "revision": revision,
        "archived": archived,
        "study": {"job_id": study.id, "result_artifact": study.result_artifact},
        "baseline": {"job_id": baseline.id, "result_artifact": baseline.result_artifact},
        "period": None,
        "dates": {"from": None, "to": None},
        "recipe": None,
        "differences": [],
    }
    evidence = None
    try:
        evidence, differences = _recipe(store, owner, study, baseline)
        origin = evidence["matched_baseline_origin"]
        basis = origin["evaluation_basis"]
        view.update(
            period=origin["period"],
            dates={key: basis["period"][key] for key in ("from", "to")},
            differences=differences,
            recipe={
                "config_id": origin["config_id"],
                "evaluation_basis_id": basis["evidence_id"],
                "eligible_signals": basis["admission"]["eligible"],
                "excluded_signals": basis["admission"]["excluded"],
            },
        )
        identity = {"experiment_id": experiment_id, **_identity(study, baseline)}
        existing = _saved(store, owner, experiment_id, identity, evidence)
        if existing:
            action = {
                "kind": "open" if existing.status == "completed" else "progress",
                "job_id": existing.id,
            }
        elif archived:
            action = {
                "kind": "unavailable",
                "reason": "Restore this experiment before creating a matching baseline.",
            }
        elif not differences:
            action = {
                "kind": "unavailable",
                "reason": "These results already use the same evaluation data and account.",
            }
        else:
            action = {"kind": "prepare"}
    except (ValueError, OSError, KeyError, TypeError) as error:
        action = {
            "kind": "unavailable",
            "reason": str(error)
            if isinstance(error, ValueError)
            else "The saved inputs needed for this match are unavailable.",
        }
    view["action"] = action
    with store.sessions() as db:
        _fence(db, owner, experiment_id, study, baseline, versions)
    return view, evidence, study, baseline, versions


def context(store, owner, experiment_id, study_job_id, baseline_job_id):
    """Review one explicit recipe without modifying evidence or requesting prices."""
    return _inspect(store, owner, experiment_id, study_job_id, baseline_job_id)[0]


def prepare(store, owner, experiment_id, study_job_id, baseline_job_id, data):
    library._object(
        data,
        {"revision", "request_id", "study_result_artifact", "baseline_result_artifact"},
        "matched baseline",
    )
    token, _ = library._request(data, "matched_baseline")
    client_token = "matched_request_" + hashlib.sha256(token.encode()).hexdigest()
    if type(data.get("revision")) is not int or data["revision"] < 1:
        raise ValueError("Supply the saved research revision")
    view, evidence, study, baseline, versions = _inspect(
        store, owner, experiment_id, study_job_id, baseline_job_id
    )
    for label, job in (("study", study), ("baseline", baseline)):
        artifact = shortlist._hex(data.get(f"{label}_result_artifact"), 64, label + " evidence")
        if artifact != job.result_artifact:
            raise ValueError("The saved study or baseline changed; reopen the matching review")
    identity = {"experiment_id": experiment_id, **_identity(study, baseline)}
    digest = fingerprint({**identity, "revision": data["revision"]})
    with store.sessions() as db:
        experiment = _fence(db, owner, experiment_id, study, baseline, versions)
        library._editable(experiment)
        prior = library._previous(db, owner, client_token, digest)
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
            >= validation.MAX_REQUESTS
        ):
            raise ValueError("This experiment has reached its saved request limit")
    action = view["action"]
    if action["kind"] == "unavailable":
        raise ValueError(action["reason"])
    if previous_job_id and action.get("job_id") != previous_job_id:
        raise ValueError(
            "The saved matched baseline request no longer resolves to its exact result"
        )
    if action["kind"] in ("open", "progress"):
        job_id, reused = action["job_id"], True
    else:
        draft = library.fresh_draft()
        draft.update(portfolio=evidence["portfolio"], equalWeights=False)
        draft = library.normalize_draft(store, owner, draft)

        def parent(job):
            return {
                "parent_job_id": job.id,
                "parent_result_artifact": job.result_artifact,
                "parent_trial_id": None,
                "parent_version_id": versions[job.id],
            }

        response = library._enqueue(
            store,
            owner,
            experiment_id,
            data,
            "matched_baseline_" + fingerprint(identity),
            fingerprint(identity),
            draft,
            evidence,
            parents=parent(study),
            source_fences=(parent(baseline),),
            with_reuse=True,
        )
        job_id, reused = response["job"]["id"], response["reused"]
    with store.sessions.begin() as db:
        service.write_guard(db)
        experiment = _fence(db, owner, experiment_id, study, baseline, versions)
        library._editable(experiment)
        prior = library._previous(db, owner, client_token, digest)
        if prior is None:
            if (
                db.scalar(
                    select(func.count())
                    .select_from(ResearchLibraryRequest)
                    .where(
                        ResearchLibraryRequest.owner == owner,
                        ResearchLibraryRequest.experiment_id == experiment_id,
                    )
                )
                >= validation.MAX_REQUESTS
            ):
                raise ValueError("This experiment has reached its saved request limit")
            _, job = shortlist._linked(db, owner, experiment_id, job_id)
            link = db.get(ResearchLibraryJob, (experiment_id, job.id))
            db.add(
                ResearchLibraryRequest(
                    owner=owner,
                    token=client_token,
                    payload_hash=digest,
                    experiment_id=experiment_id,
                    kind="run",
                    job_id=job.id,
                    version_id=link.version_id,
                )
            )
        elif prior.job_id != job_id:
            raise ValueError("This matched baseline request already selected a different result")
    return {
        "job": service.job_receipt(
            store,
            service.get_job(store, owner, job_id),
            include_result=True,
            experiment_id=experiment_id,
        ),
        "reused": reused,
        "context": context(store, owner, experiment_id, study.id, baseline.id),
    }
