"""Compact presentation of recorded portfolio identities; never plans or acquires data.

The worker stores the report projection beside its existing progress metadata.
Old rows may have no projection: input dates are then kept separate from unknown
effective dates. Reading a report can describe its already-loaded evidence without
backfilling metadata or changing that evidence.
"""

from copy import deepcopy

from research.report_contract import report_context

VERSION = "research-result-descriptor-v1"


def recorded_result(result, *, job_id, result_artifact, inputs_artifact):
    """Project the existing canonical resolver, including its candidate identity."""
    context = report_context(
        result, job_id=job_id, result_artifact=result_artifact, inputs_artifact=inputs_artifact
    )
    basis = context["evaluation_basis"]
    period = basis.get("period", {}) if basis.get("status") == "verified" else {}
    dates = {key: period.get(key) or context["dates"].get(key) for key in ("from", "to")}
    comparison = basis.get("comparison", {})
    return {
        "version": VERSION,
        "evidence_id": result_artifact,
        "report_id": context["report_id"],
        "config_id": context["config_id"],
        "period": context["period"],
        "dates": {**dates, "status": "recorded" if all(dates.values()) else "unknown"},
        "candidate": deepcopy(context.get("candidate")),
        "parent_job_id": context.get("parent_job_id"),
        "account": {
            "capital": comparison.get("capital", result.get("portfolio", {}).get("capital")),
            "currency": comparison.get("currency"),
        },
        "interval": period.get("interval") or result.get("source", {}).get("interval"),
        "evaluation_basis_id": basis.get("evidence_id"),
        "cohort_id": basis.get("cohort_id"),
        "reservation": deepcopy(result.get("reserved_evaluation")),
        "matched_baseline": result.get("matched_baseline_origin", {}).get("verification")
        == {"settings": "matched", "basis": "matched"},
    }


def display_result(
    *,
    kind,
    evidence_id,
    calculation_id,
    specification,
    source_receipt,
    recorded=None,
    link_role=None,
    setup=None,
    candidate=None,
    parent_job_id=None,
):
    """Combine exact saved projection and owner-checked links without any I/O.

    A changed/missing artifact cannot inherit a stale projection. The fallback
    deliberately does not calculate period dates from an 80% split or a CSV range.
    """
    portfolio = specification.get("portfolio", {})
    source = source_receipt.get("receipt", {})
    if (
        not isinstance(recorded, dict)
        or recorded.get("version") != VERSION
        or not evidence_id
        or recorded.get("evidence_id") != evidence_id
    ):
        recorded = {}
    value = {
        "version": VERSION,
        "evidence_id": evidence_id,
        "report_id": None,
        "config_id": None,
        "period": "selection" if portfolio.get("validation") else None,
        "dates": {"from": None, "to": None, "status": "unknown"},
        "candidate": deepcopy(candidate),
        "parent_job_id": parent_job_id,
        "account": {"capital": portfolio.get("capital"), "currency": None},
        "interval": source_receipt.get("provenance", {}).get("interval"),
        "evaluation_basis_id": None,
        "cohort_id": None,
        "reservation": None,
        **deepcopy(recorded),
        "calculation_id": calculation_id,
        "input_dates": {"from": source.get("date_from"), "to": source.get("date_to")},
        "setup": deepcopy(setup),
    }
    if candidate and not value["candidate"]:
        value["candidate"] = deepcopy(candidate)
    if value["candidate"] and not value["config_id"]:
        value["config_id"] = value["candidate"].get("config_id")
    if not value["parent_job_id"]:
        value["parent_job_id"] = parent_job_id
    if value["period"] is None and candidate:
        value["period"] = candidate.get("period")
    if link_role == "validation" or value["period"] == "evaluation":
        value.update(role="evaluation", period="evaluation")
    elif kind == "portfolio_optimize":
        value["role"] = "optimization"
    elif link_role == "candidate" or candidate:
        value["role"] = "candidate"
    elif link_role == "replay" or recorded.get("parent_job_id"):
        value["role"] = "replay"
    elif recorded.get("matched_baseline"):
        value["role"] = "matched_baseline"
    elif link_role == "run" and parent_job_id:
        value["role"] = "backtest"
    elif recorded or link_role == "run":
        value["role"] = "baseline"
    else:
        value["role"] = "backtest"
    return value
