"""Pure, versioned identities for reports; calculation evidence stays immutable."""

import hashlib
import json

VERSION = "research-report-context-v1"


def fingerprint(value):
    # Match the existing Optuna settings identity, including its UTF-8 encoding.
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def settings_identity(strategies):
    if not isinstance(strategies, list) or not strategies:
        return None
    keys = ("id", "name", "allocation_pct", "config")
    if any(not isinstance(row, dict) or any(key not in row for key in keys) for row in strategies):
        return None
    return fingerprint([{key: row[key] for key in keys} for row in strategies])


def report_context(result, *, job_id, result_artifact, inputs_artifact, period=None):
    """Describe this exact saved report, never a similarly named trial or CSV range."""
    from research.evaluation_basis import VERSION as BASIS_VERSION

    origin = result.get("replay_origin", {})
    experiment = result.get("experiment", {})
    config_id = settings_identity(result.get("strategies"))
    period = (
        period
        or origin.get("period")
        or result.get("matched_baseline_origin", {}).get("period")
        or (
            "selection" if result.get("validation") or result.get("reserved_evaluation") else "full"
        )
    )
    coverage = result.get("coverage", {})
    context = {
        "version": VERSION,
        "job_id": job_id,
        "result_artifact": result_artifact,
        "inputs_artifact": inputs_artifact,
        "config_id": config_id,
        "period": period,
        "period_label": {
            "selection": "Selection period",
            "evaluation": "Later period",
            "full": "Full period",
        }[period],
        "dates": {"from": coverage.get("date_from"), "to": coverage.get("date_to")},
        "analysis_version": result.get("analysis", {}).get("version"),
        "analysis_artifact": result.get("report_context", {}).get("analysis_artifact"),
        "evaluation_basis": result.get("evaluation_basis")
        or {"version": BASIS_VERSION, "status": "unverified"},
    }
    context["report_id"] = fingerprint(
        {key: context[key] for key in ("version", "result_artifact", "period", "config_id")}
    )
    # Only bind to actual recorded rows. Names, rounded scores and graph coordinates
    # cannot identify a candidate. A zero-based trial number remains zero-based here.
    match = next(
        (row for row in experiment.get("rows", []) if row.get("config_id") == config_id), None
    )
    if match is not None:
        context["candidate"] = {
            "study_job_id": job_id,
            "trial_number": match["trial_number"],
            "config_id": config_id,
            "is_objective_winner": experiment.get("recommendation_id") == config_id,
        }
    elif origin.get("config_id") == config_id and origin.get("study_job_id"):
        context["candidate"] = {
            key: origin[key]
            for key in ("study_job_id", "trial_number", "config_id", "is_objective_winner")
            if key in origin
        }
    if origin.get("parent_job_id"):
        context["parent_job_id"] = origin["parent_job_id"]
    return context


def present_report(result, *, job_id, result_artifact, inputs_artifact):
    context = report_context(
        result, job_id=job_id, result_artifact=result_artifact, inputs_artifact=inputs_artifact
    )
    view = {**result, "report_context": context}
    later = result.get("validation", {}).get("result")
    if isinstance(later, dict):
        later_context = report_context(
            later,
            job_id=job_id,
            result_artifact=result_artifact,
            inputs_artifact=inputs_artifact,
            period="evaluation",
        )
        if context.get("candidate") and later_context["config_id"] == context["config_id"]:
            later_context["candidate"] = dict(context["candidate"])
        view["validation"] = {
            **result["validation"],
            "result": {**later, "report_context": later_context},
        }
    return view
