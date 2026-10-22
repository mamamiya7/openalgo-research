"""Small read-only views of committed development trials, never new research."""

import json
from math import isfinite
from statistics import median

from sqlalchemy import select

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchStudyExecution,
    ResearchStudyProposal,
)
from research.automatic_hypotheses import policy_for_version
from research.automatic_research import _score
from research.automatic_walk_forward import (
    MAX_CHECKPOINT_BYTES,
    _validate_state,
    data_period,
    fit_names,
)
from research.report_contract import settings_identity
from services import scanner_research_service as service

VERSION = "research-live-progress-v1"
SUPPORTED = {f"automatic-hypotheses-v{number}" for number in range(8, 17)}
MAX_RESPONSE_BYTES = 16 * 1024
LABELS = {
    "fit1": "Training window 1",
    "fit2": "Training window 2",
    "fit3": "Training window 3",
    "search": "Development search",
    "search_seed1": "Search stability check 1",
    "search_seed2": "Search stability check 2",
}


def _metrics(row, *, trial=False):
    summary = row["summary"]
    score = _score(summary)
    result = {
        "score": score,
        "return_pct": summary["net_return_pct"],
        "drawdown_pct": summary["max_drawdown_pct"],
        "closed_trades": summary["closed_trades"],
    }
    if trial:
        result["trial_number"] = row["trial_number"]
    return result


def _baseline(calculation, mandate, period):
    settings = mandate.get("economic_assumptions", {}).get("settings")
    identity = settings_identity(settings)
    if identity is None:
        return None
    for row in calculation["evaluations"].values():
        recorded = row.get("development_evidence", {})
        if (
            recorded.get("config_id") == identity
            and recorded.get("period") == period
            and recorded.get("stress") is False
            and row.get("automatic_stock_condition", {}).get("hypothesis", {}).get("id") == "none"
        ):
            return _metrics(row)
    return None


def _pace(db, owner, job_id, calculation, trials, budget):
    start = fit_names(calculation["version"]).index(calculation["active_fit"]) * budget
    committed = {start + row["number"]: row["config_id"] for row in trials}
    samples = db.execute(
        select(
            ResearchStudyProposal.number,
            ResearchStudyProposal.config_id,
            ResearchStudyProposal.started_at,
            ResearchStudyProposal.finished_at,
        )
        .join(
            ResearchStudyExecution, ResearchStudyExecution.id == ResearchStudyProposal.execution_id
        )
        .where(
            ResearchStudyExecution.owner == owner,
            ResearchStudyExecution.job_id == job_id,
            ResearchStudyProposal.job_id == job_id,
            ResearchStudyProposal.number >= start,
            ResearchStudyProposal.number < start + len(trials),
            ResearchStudyProposal.state == "evaluated",
            ResearchStudyProposal.reused.is_(False),
            ResearchStudyProposal.finished_at.is_not(None),
        )
        .order_by(ResearchStudyProposal.finished_at.desc(), ResearchStudyProposal.id.desc())
        .limit(20)
    ).all()
    durations = [
        finished - started
        for number, identity, started, finished in samples
        if committed.get(number) == identity
        and type(started) in (int, float)
        and type(finished) in (int, float)
        and isfinite(started)
        and isfinite(finished)
        and 0 < finished - started <= 86400
    ]
    return median(durations) if len(durations) >= 3 else None


def read(store, owner, job_id):
    # Fence ownership before reading any checkpoint or operational timing rows.
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        if job is None or job.owner != owner:
            raise LookupError("Research run not found")
        native = db.get(ResearchExperiment, job_id)
        digest = native.checkpoint if native else None
        identity = native.identity if native else None
        specification = native.specification if native else "{}"
        status = job.status
        kind = native.kind if native else None
    response = {
        "version": VERSION,
        "job_id": job_id,
        "available": False,
        "job_status": status,
        "provisional": True,
        "checkpoint_artifact": digest,
        "reason": "No completed development trial has been committed yet.",
        "stage": "preparing",
        "fit": None,
        "latest": None,
        "best": None,
        "baseline": None,
        "events": [],
        "pace": {"seconds_per_trial": None, "remaining_search_seconds_upper": None},
    }
    if kind != "portfolio_optimize" or not digest:
        return response
    try:
        envelope = service.read_artifact(store, digest, max_bytes=MAX_CHECKPOINT_BYTES + 1024**2)
        if not isinstance(envelope, dict) or envelope.get("identity") != identity:
            raise ValueError("Saved study identity changed")
        state = envelope["state"]
        calculation = state.get("calculation")
        version = calculation.get("version") if isinstance(calculation, dict) else None
        if state.get("phase") != "calculation" or version not in SUPPORTED:
            return response
        requested = json.loads(specification).get("portfolio", {}).get("automatic_research", {})
        if requested.get("version") != version:
            raise ValueError("Saved recipe differs from this study")
        _validate_state(calculation, version=version)
        key = calculation["active_fit"]
        current = calculation["fits"].get(key, {"trials": [], "candidates": {}})
        trials, candidates = current["trials"], current["candidates"]
        seen = set()
        for number, trial in enumerate(trials):
            row = candidates.get(trial.get("config_id"))
            if (
                trial.get("number") != number
                or trial.get("state") != "complete"
                or not row
                or trial.get("value") != row["score"]
                or type(trial.get("reused")) is not bool
                or trial["reused"] != (trial["config_id"] in seen)
                or trial.get("phase") not in ("family_screening", "joint_refinement")
                or type(row.get("trial_number")) is not int
                or not 0 <= row["trial_number"] <= number
                or not trial["reused"]
                and row["trial_number"] != number
            ):
                raise ValueError("Committed trial correspondence changed")
            seen.add(trial["config_id"])
        if seen != set(candidates):
            raise ValueError("Candidate has no committed trial")
        budget = policy_for_version(version)["proposals_per_fit"]
        fit = {"key": key, "label": LABELS[key], "completed": len(trials), "budget": budget}
        mandate = state.get("research_mandate", {})
        period = data_period(key)
        dates = next(
            (
                row
                for row in mandate.get("validation", {}).get("periods", [])
                if row.get("name") == period
            ),
            None,
        )
        if dates and all(
            isinstance(dates.get(field), str) and len(dates[field]) == 10
            for field in ("from", "to")
        ):
            fit["dates"] = {field: dates[field] for field in ("from", "to")}
        response.update(available=True, reason=None, fit=fit)
        if trials:
            latest_trial = trials[-1]
            response["latest"] = {
                **_metrics(candidates[latest_trial["config_id"]], trial=True),
                "trial_number": latest_trial["number"],
            }
            response["best"] = _metrics(
                min(candidates.values(), key=lambda row: (-row["score"], row["config_id"])),
                trial=True,
            )
            response["stage"] = latest_trial.get("phase", "joint_refinement")
            response["events"].append(
                {"kind": "committed", "text": f"Trial {len(trials)} saved in this window."}
            )
            if latest_trial["reused"]:
                response["events"].append(
                    {
                        "kind": "reused",
                        "text": "These settings were already evaluated in this window; the saved result was reused.",
                    }
                )
            response["events"].append(
                {
                    "kind": "objective",
                    "text": "Best so far compares return minus drawdown within this development window only.",
                }
            )
        folds = calculation["folds"]
        if folds and isinstance(folds[-1], dict) and type(folds[-1].get("eligible")) is bool:
            last_fold = folds[-1]
            reasons = last_fold.get("reasons", [])
            outcome = (
                "met the recorded checks"
                if last_fold["eligible"]
                else reasons[0][:500]
                if isinstance(reasons, list) and reasons and isinstance(reasons[0], str)
                else "did not meet the recorded checks"
            )
            response["events"].append(
                {
                    "kind": "later_check",
                    "text": f"Completed later-window check {len(folds)}: {outcome}.",
                }
            )
        if status == "completed":
            response["stage"] = "completed"
            response["events"].append(
                {
                    "kind": "completed",
                    "text": "Checks did not establish a reliable improvement; original settings retained."
                    if (calculation.get("selection") or {}).get("is_baseline") is True
                    else "Research completed. Open the final findings for the recorded outcome.",
                }
            )
        elif current.get("selection") is not None:
            response["stage"] = "checking"
            response["events"].append(
                {
                    "kind": "frozen",
                    "text": "This window's choice is frozen. Separate later-window and robustness checks still determine the outcome.",
                }
            )
        response["baseline"] = _baseline(calculation, mandate, period)
        with store.sessions() as db:
            current_job = db.get(ResearchJob, job_id)
            native = db.get(ResearchExperiment, job_id)
            if current_job is None or current_job.owner != owner:
                raise LookupError("Research run not found")
            if not native or native.checkpoint != digest or current_job.status != status:
                raise ValueError("A newer checkpoint is being published")
            response["pace"]["seconds_per_trial"] = _pace(
                db, owner, job_id, calculation, trials, budget
            )
        if len(service.encoded(response)) > MAX_RESPONSE_BYTES:
            raise ValueError("Live progress response exceeds its bound")
    except (KeyError, TypeError, ValueError, AttributeError, OSError, ArithmeticError):
        response.update(
            available=False,
            reason="Committed progress could not be verified. The run and its saved results are unchanged.",
            fit=None,
            latest=None,
            best=None,
            baseline=None,
            events=[],
        )
        response["pace"] = {"seconds_per_trial": None, "remaining_search_seconds_upper": None}
    return response
