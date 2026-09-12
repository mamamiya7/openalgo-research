"""Explicit extra work on a frozen study; every continuation is a new saved result.

The existing library transaction owns admission, retries and source fences. This
module never acquires prices or calculates a trial in the web process.
"""

import copy
import json

from database.research_db import ResearchExperiment, ResearchLibraryJob
from research.connectors.optuna_portfolio import MAX_TRIALS, validate_checkpoint
from research.portfolio import execution_versions, normalize
from services import research_library as library
from services import research_shortlist as shortlist
from services import scanner_research_service as service

VERSION = "research-study-continuation-v1"


def _parent(store, owner, experiment_id, job_id):
    shortlist._hex(experiment_id, 32, "experiment")
    shortlist._hex(job_id, 32, "study")
    with store.sessions() as db:
        experiment, job = shortlist._linked(db, owner, experiment_id, job_id)
        native = db.get(ResearchExperiment, job_id)
        link = db.get(ResearchLibraryJob, (experiment_id, job_id))
        if job.status != "completed" or not native or native.kind != "portfolio_optimize":
            raise ValueError("Choose a completed optimization study")
        return job, native, link.version_id, experiment.revision, experiment.archived


def _recipe(store, parent, native):
    if not native.checkpoint:
        raise ValueError("This older study has no saved search checkpoint. Start a new search.")
    bundle = service.read_artifact(store, parent.result_artifact)
    envelope = service.read_artifact(store, native.checkpoint)
    state = envelope.get("state", {})
    if (
        bundle.get("kind") != "portfolio_optimize"
        or envelope.get("identity") != native.identity
        or state.get("phase") != "calculation"
        or state.get("inputs_artifact") != bundle.get("inputs_artifact")
    ):
        raise ValueError("The saved search checkpoint does not match this result")
    calculation = state.get("calculation")
    info = validate_checkpoint(calculation)
    result = bundle["result"]
    study = result["experiment"]
    if (
        calculation["trials"] != study.get("trials")
        or calculation.get("winner_config_id") != study.get("recommendation_id")
        or {row["config_id"]: row for row in calculation["rows"]}
        != {row["config_id"]: row for row in study.get("rows", [])}
    ):
        raise ValueError("The saved trials do not match this study's result")
    evidence = service.read_artifact(store, bundle["inputs_artifact"])
    portfolio = evidence["portfolio"]
    if portfolio != result.get("portfolio") or bundle.get("specification") != json.loads(
        native.specification
    ):
        raise ValueError("The saved study settings do not match its inputs")
    # Validate the locally installed engine versions without broker access.
    execution_versions(portfolio, evidence["versions"])
    validation = portfolio.get("validation")
    if validation and validation.get("mode") != "reserve":
        raise ValueError(
            "This study already runs its later test during search. Refine a new search instead."
        )
    specification = study["specification"]
    if (
        specification != portfolio.get("optimization")
        or calculation.get("optimizer") != study.get("optimizer")
        or (info.get("specification") is not None and info["specification"] != specification)
    ):
        raise ValueError("The saved search protocol does not match this study")
    proposed = len(calculation["trials"])
    maximum = (
        min(MAX_TRIALS, study["search_space"]["grid_size"])
        if specification["sampler"] == "grid" or study["search_space"]["grid_size"] == 1
        else MAX_TRIALS
    )
    origin = {
        "version": VERSION,
        "parent_job_id": parent.id,
        "parent_result_artifact": parent.result_artifact,
        # Existing storage closure follows reference_artifact, including backup
        # and pruning, even if the parent's mutable checkpoint pointer changes.
        "reference_artifact": native.checkpoint,
        "previous_specification": copy.deepcopy(specification),
        "replayed_proposals": proposed,
        "retained_portfolios": len(calculation["rows"]),
        "search_identity": info.get("search_identity"),
    }
    return evidence, origin, proposed, maximum


def context(store, owner, experiment_id, job_id):
    parent, native, _, revision, archived = _parent(store, owner, experiment_id, job_id)
    value = {
        "version": VERSION,
        "revision": revision,
        "parent_result_artifact": parent.result_artifact,
        "current_proposed": 0,
        "max_total": MAX_TRIALS,
        "available": False,
        "reason": None,
    }
    try:
        _, _, proposed, maximum = _recipe(store, parent, native)
        value.update(current_proposed=proposed, max_total=maximum)
        if archived:
            value["reason"] = "Restore this experiment before adding trials."
        elif proposed >= maximum:
            value["reason"] = "This study has reached its search limit."
        else:
            value["available"] = True
    except (ValueError, OSError, KeyError, TypeError) as error:
        value["reason"] = (
            str(error)
            if isinstance(error, ValueError)
            else ("The saved checkpoint needed to continue this study is unavailable.")
        )
    return value


def extend(store, owner, experiment_id, job_id, data):
    library._object(
        data,
        {"revision", "request_id", "parent_result_artifact", "additional_trials"},
        "study continuation",
    )
    token, digest = library._request(
        {**data, "experiment_id": experiment_id, "job_id": job_id}, "study_continuation"
    )
    with store.sessions() as db:
        row = library._owned(db, owner, experiment_id)
        prior = library._previous(db, owner, token, digest)
        if prior:
            return library._run_response(
                store, owner, experiment_id, prior.version_id, prior.job_id
            )
        library._revision(row, data.get("revision"))
        library._editable(row)
    additional = data.get("additional_trials")
    if type(additional) is not int or not 1 <= additional <= MAX_TRIALS:
        raise ValueError("Choose a whole number of additional trials between 1 and 1,000")
    parent, native, version_id, _, _ = _parent(store, owner, experiment_id, job_id)
    if data.get("parent_result_artifact") != parent.result_artifact:
        raise ValueError("This study changed. Reopen it before adding trials.")
    evidence, origin, proposed, maximum = _recipe(store, parent, native)
    total = proposed + additional
    if total > maximum:
        raise ValueError(f"This study can add at most {max(0, maximum - proposed):,} trials")
    portfolio = copy.deepcopy(evidence["portfolio"])
    portfolio["optimization"]["trials"] = total
    portfolio = normalize(portfolio)
    origin.update(total_proposals=total, additional_proposals=additional)
    evidence.update(portfolio=portfolio, frozen_prices=True, study_continuation=origin)
    # No draft replacement: the new immutable setup version contains the exact
    # continued rules while the trader's current edits remain their own.
    draft = library.fresh_draft()
    draft_portfolio = copy.deepcopy(portfolio)
    draft.update(
        portfolio=draft_portfolio,
        equalWeights=False,
        optimizing=True,
        optimization=draft_portfolio.pop("optimization"),
    )
    draft = library.normalize_draft(store, owner, draft)
    return library._enqueue(
        store,
        owner,
        experiment_id,
        data,
        token,
        digest,
        draft,
        evidence,
        parents={
            "parent_job_id": parent.id,
            "parent_result_artifact": parent.result_artifact,
            "parent_trial_id": None,
            "parent_version_id": version_id,
        },
    )


def seed(store, origin):
    """Read the exact pinned parent checkpoint; called only in the leased worker."""
    if origin.get("version") != VERSION:
        raise ValueError("Unsupported saved study continuation")
    envelope = service.read_artifact(store, origin["reference_artifact"])
    calculation = envelope["state"]["calculation"]
    validate_checkpoint(calculation)
    if len(calculation["trials"]) != origin["replayed_proposals"]:
        raise ValueError("Saved continuation trial count changed")
    return calculation, origin["previous_specification"]
