"""Owner-bound, offline condition replay using the existing native job pipeline."""

import re
from copy import deepcopy

from sqlalchemy import select

from database.research_db import ResearchExperiment, ResearchJob
from research import condition_replay as contract
from research.portfolio import fingerprint
from services import scanner_research_service as service


def submit(store, owner, job_id, request, *, request_id=None):
    from services.research_portfolio import replay_inputs
    from services.research_regimes import retained_conditions

    if (
        not isinstance(request, dict)
        or set(request) - {"strategy_id", "dimension", "regime", "analysis_artifact", "period"}
        or not {"strategy_id", "dimension", "regime", "analysis_artifact"} <= set(request)
    ):
        raise ValueError("Choose a saved strategy, market condition and analysis")
    if request_id is not None and (
        not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
    ):
        raise ValueError("Use a request identity of 1–128 characters")
    artifact, period = request["analysis_artifact"], request.get("period", "selection")
    if not isinstance(artifact, str) or not re.fullmatch(r"[a-f0-9]{64}", artifact):
        raise ValueError("Choose a saved market-condition analysis")
    if period not in ("selection", "validation"):
        raise ValueError("Choose a published selection or later period")
    parent = service.get_job(store, owner, job_id)
    if parent.status != "completed":
        raise ValueError("Choose a completed portfolio run")
    original = service.read_artifact(store, parent.result_artifact)["result"]
    if original.get("condition_replay"):
        raise ValueError("Start another condition test from the original unfiltered report")
    baseline = original
    if period == "validation":
        baseline = original.get("validation", {}).get("result")
        if not baseline:
            raise ValueError("This result has no published later period")
    with store.sessions() as db:
        analysis = db.scalar(
            select(ResearchJob)
            .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
            .where(
                ResearchJob.owner == owner,
                ResearchJob.status == "completed",
                ResearchJob.result_artifact == artifact,
                ResearchExperiment.kind == "portfolio_analysis",
                ResearchExperiment.parent_job_id == parent.id,
            )
            .limit(1)
        )
    if analysis is None:
        raise ValueError("Choose completed market conditions saved for this report")
    retained_conditions(store, artifact, parent.result_artifact)
    _, base = replay_inputs(
        store, owner, job_id, period="evaluation" if period == "validation" else "selection"
    )
    condition = {key: request[key] for key in ("strategy_id", "dimension", "regime")}
    contract.normalize(condition, base["strategies"])
    if base["portfolio"]["engine"] != "vectorbt":
        raise ValueError("Condition replay currently requires a saved VectorBT portfolio")
    base_artifact = service.save_artifact(store, base)
    manifest = {
        "version": contract.VERSION,
        "parent_job_id": parent.id,
        "parent_result_artifact": parent.result_artifact,
        "reference_artifact": artifact,
        "inputs_artifact": base_artifact,
        "period": period,
        "condition": condition,
        "baseline": {
            "summary": deepcopy(baseline["summary"]),
            "evaluation_basis": deepcopy(baseline.get("evaluation_basis")),
        },
    }
    manifest["id"] = fingerprint(manifest)
    evidence = {**base, "condition_replay": manifest}
    from services.research_library import enqueue_condition_replay

    return enqueue_condition_replay(store, owner, parent, evidence, request_id)


def verify(store, evidence):
    """Cheap immutable checks for admission; numerical verification stays in worker."""
    from services.research_regimes import retained_conditions

    saved = evidence.get("condition_replay")
    if saved is None:
        return
    if saved.get("version") != contract.VERSION or saved.get("id") != fingerprint(
        {key: value for key, value in saved.items() if key != "id"}
    ):
        raise ValueError("Saved entry-condition evidence failed its identity check")
    base = service.read_artifact(store, saved["inputs_artifact"])
    for key in ("portfolio", "signals", "snapshot", "versions"):
        if evidence[key] != base[key]:
            raise ValueError("Saved entry-condition execution inputs changed")
    retained_conditions(store, saved["reference_artifact"], saved["parent_result_artifact"])
    gate = evidence.get("condition_gate")
    if gate is None:
        if evidence["strategies"] != base["strategies"]:
            raise ValueError("Saved entry-condition membership changed")
    elif (
        gate.get("id") != fingerprint({key: value for key, value in gate.items() if key != "id"})
        or gate.get("conditioned_strategies_id") != fingerprint(evidence["strategies"])
        or gate.get("condition") != contract.normalize(saved["condition"], base["strategies"])
    ):
        raise ValueError("Saved entry-condition membership changed")


def prepare(store, evidence):
    """Compute/recheck the causal gate inside the bounded calculation worker."""
    from services.research_regimes import retained_conditions

    verify(store, evidence)
    saved = evidence["condition_replay"]
    base = service.read_artifact(store, saved["inputs_artifact"])
    market = retained_conditions(
        store, saved["reference_artifact"], saved["parent_result_artifact"]
    )
    strategies, gate = contract.apply(base, market, saved["condition"])
    if evidence.get("condition_gate") is not None and evidence["condition_gate"] != gate:
        raise ValueError("Saved entry-condition membership changed")
    return {**evidence, "strategies": strategies, "condition_gate": gate}


def enrich(result, evidence):
    saved = evidence.get("condition_replay")
    if saved is None:
        return
    gate = evidence["condition_gate"]
    basis = result["evaluation_basis"]
    comparison = basis["comparison"]
    comparison["execution"]["entry_condition"] = {
        "version": contract.VERSION,
        "gate_id": gate["id"],
    }
    comparison["id"] = fingerprint(
        {
            "version": basis["version"],
            **{key: value for key, value in comparison.items() if key != "id"},
        }
    )
    result["condition_replay"] = {
        "version": contract.VERSION,
        "id": saved["id"],
        **{
            key: saved[key]
            for key in ("parent_job_id", "parent_result_artifact", "period", "baseline")
        },
        "analysis_artifact": saved["reference_artifact"],
        "condition": deepcopy(gate["condition"]),
        "counts": deepcopy(gate["counts"]),
        "delta": {
            key: result["summary"][key] - saved["baseline"]["summary"][key]
            for key in ("net_return_pct", "max_drawdown_pct", "closed_trades")
        },
        "basis": [
            "Replayed the complete VectorBT cash account on identical saved prices and trade settings.",
            "Applied one fixed condition before entry; unavailable conditions exclude the signal.",
            "This is an exploratory comparison on an already viewed period, not untouched validation.",
        ],
    }
