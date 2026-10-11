"""Operational recovery admission over immutable checkpoint references.

No cached frames, resources or global registries. Artifact reads use the existing
bounded reader; callers own short-lived SQLAlchemy context managers.
"""

import json

from research.failures import RECOVERY_VERSION, ResearchFailure, failure_receipt

STOPPED = {"interrupted", "failed", "cancelled", "paused"}
LEGACY_HOURS_ERROR = "Saved stock-market context has different execution session hours"


def _legacy_hours(store, state):
    from research.failures import check_execution_hours
    from research.stock_market_context import KEY
    from services import research_stock_market_context as context
    from services.scanner_research_service import read_artifact

    inputs = read_artifact(store, state["inputs_artifact"])
    if KEY in inputs:
        context._check_execution(
            inputs[KEY], inputs, context.research_stock_features._symbols(inputs), store=store
        )
    else:
        # Assembly failed before committing the completed market bundle. Its
        # stock context has already frozen the same execution-calendar window.
        stocks = inputs.get("automatic_stock_features", {}).get("series", {})
        if not stocks:
            raise ValueError("Saved context has no recorded execution hours")
        for stock in stocks.values():
            check_execution_hours(
                stock["calendar"]["session_hours"],
                context.execution_hours(store, inputs),
                inputs["snapshot"]["sessions"],
            )


def assessment(store, job, experiment):
    """One capability contract for list/open/resume; never mutate old evidence."""
    counts = json.loads(experiment.counts) if experiment else {}
    failure = counts.get("failure") if job.status == "failed" else None
    value = {
        "version": RECOVERY_VERSION,
        "status": "not_needed",
        "actions": [],
        "message": "This run does not need recovery.",
    }
    if job.status not in STOPPED:
        return failure, value
    if not experiment or not experiment.checkpoint:
        value.update(
            status="unavailable",
            actions=["new_run"],
            message="This run has no resumable checkpoint. Start a new run.",
        )
        return failure, value
    try:
        from services.scanner_research_service import read_artifact

        checkpoint = read_artifact(store, experiment.checkpoint)
        if not isinstance(checkpoint, dict) or checkpoint.get("identity") != experiment.identity:
            raise ValueError("Checkpoint identity does not match this experiment")
        if not isinstance(checkpoint.get("state"), dict) or not checkpoint.get("policy_version"):
            raise ValueError("The saved checkpoint is incomplete")
        state = checkpoint["state"]
        value["checkpoint_phase"] = state.get("phase", "calculation")
        if experiment.kind in ("portfolio_backtest", "portfolio_optimize"):
            from research.connectors.registry import policy_for_request

            if checkpoint["policy_version"] != policy_for_request(
                {}, json.loads(experiment.specification)
            ):
                raise ValueError("Checkpoint execution policy changed")
        if job.status == "failed" and failure is None and job.error:
            if job.error == LEGACY_HOURS_ERROR:
                _legacy_hours(store, state)
                failure = {
                    "version": "research-failure-v1",
                    "code": "daily_calendar_hours_omitted",
                    "stage": "prepare_prices",
                    "retryable": True,
                    "actions": ["resume"],
                    "message": "The daily-price snapshot omitted its saved session hours. "
                    "Resume uses the original recorded calendar and completed prices.",
                }
            else:
                failure = failure_receipt(
                    ValueError(job.error), stage=counts.get("stage", "calculation")
                )
        if failure and not failure.get("retryable"):
            value.update(status="blocked", actions=failure["actions"], message=failure["message"])
        else:
            value.update(
                status="available",
                actions=failure["actions"] if failure else ["resume"],
                message="Continue from the saved checkpoint; completed work is retained.",
            )
    except (ValueError, LookupError, KeyError, TypeError, OSError) as error:
        failure = (
            failure_receipt(error, stage="recovery")
            if isinstance(error, ResearchFailure)
            else ResearchFailure(
                "checkpoint_incompatible",
                "The saved checkpoint cannot be resumed. "
                "Start a new run with these signals; the saved run is unchanged.",
                stage="recovery",
            ).receipt
        )
        value.update(status="blocked", actions=failure["actions"], message=failure["message"])
    return failure, value
