"""Separate, owner-bound saved analysis jobs for immutable portfolio evidence."""

import copy
import hashlib
import json

from sqlalchemy import select

from database.research_db import ResearchExperiment, ResearchJob
from services import scanner_research_service as service

VERSION = "research-analysis-v2"
READABLE_VERSIONS = {"research-analysis-v1", VERSION}


def validate_request(store, owner, source_id, specification):
    if not isinstance(specification, dict) or set(specification) != {
        "parent_job_id",
        "parent_result_artifact",
        "analysis_version",
        "parameters",
        "symbol",
        "period",
    }:
        raise ValueError("Invalid saved analysis request")
    parent = service.get_job(store, owner, specification["parent_job_id"])
    if parent.status != "completed" or parent.source_id != source_id:
        raise ValueError("Analysis requires a completed portfolio result")
    if (
        specification["parent_result_artifact"] != parent.result_artifact
        or specification["analysis_version"] not in READABLE_VERSIONS
    ):
        raise ValueError("The requested analysis does not match the saved result")
    with store.sessions() as db:
        experiment = db.get(ResearchExperiment, parent.id)
    if not experiment or experiment.kind not in ("portfolio_backtest", "portfolio_optimize"):
        raise ValueError("Analysis requires a saved portfolio backtest or study")
    result = service.read_artifact(store, parent.result_artifact)["result"]
    parameters = specification["parameters"]
    axes = result.get("experiment", {}).get("search_space", {}).get("axes", {})
    if parameters is not None and (
        not isinstance(parameters, list)
        or len(parameters) > 2
        or any(not isinstance(p, str) or p not in axes for p in parameters)
        or len(set(parameters)) != len(parameters)
    ):
        raise ValueError("Choose up to two parameters from this study")
    period = specification["period"]
    if period not in ("selection", "validation"):
        raise ValueError("Choose a recorded analysis period")
    if period == "validation":
        result = result.get("validation", {}).get("result")
        if not result:
            raise ValueError("This result has no later validation period")
    symbol = specification["symbol"]
    if symbol is not None and (
        not isinstance(symbol, str)
        or len(symbol) > 100
        or symbol not in {r.get("symbol") for r in result.get("ledger", [])}
    ):
        raise ValueError("Choose a symbol from this saved portfolio")
    return specification


def latest_job(store, parent, *, completed=False):
    statement = (
        select(ResearchJob)
        .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
        .where(
            ResearchJob.owner == parent.owner,
            ResearchExperiment.kind == "portfolio_analysis",
            ResearchExperiment.parent_job_id == parent.id,
        )
        .order_by(ResearchJob.created_at.desc(), ResearchJob.id.desc())
    )
    if completed:
        statement = statement.where(ResearchJob.status == "completed")
    with store.sessions() as db:
        return db.scalar(statement.limit(1))


def saved_overlay(store, parent, result):
    analysis_job = latest_job(store, parent, completed=True)
    if analysis_job is None:
        return result
    bundle = service.read_artifact(store, analysis_job.result_artifact)
    if bundle.get("parent_result_artifact") != parent.result_artifact:
        return result
    extra = bundle["result"]
    if extra.get("version") not in READABLE_VERSIONS:
        return result
    enriched = {
        **result,
        "analysis": extra["analysis"],
        "report_context": {"analysis_artifact": analysis_job.result_artifact},
    }
    if result.get("experiment"):
        study = {**result["experiment"], **extra.get("experiment", {})}
        row_analysis = study.pop("row_analysis", {})
        study["rows"] = [
            {**row, "analysis": row_analysis[row["config_id"]]}
            if row["config_id"] in row_analysis
            else row
            for row in study.get("rows", [])
        ]
        enriched["experiment"] = study
    if extra.get("validation") and result.get("validation", {}).get("result"):
        enriched["validation"] = {
            **result["validation"],
            "result": {
                **result["validation"]["result"],
                "analysis": extra["validation"],
                "report_context": {"analysis_artifact": analysis_job.result_artifact},
            },
        }
    return enriched


def status(store, owner, parent_id):
    parent = service.get_job(store, owner, parent_id)
    latest = latest_job(store, parent)
    if latest:
        mapped = (
            "complete"
            if latest.status == "completed"
            else (
                "failed"
                if latest.status in ("failed", "cancelled", "interrupted", "cancelling")
                else latest.status
            )
        )
        response = {"status": mapped, "analysis_job_id": latest.id}
        if mapped == "complete":
            response["job"] = service.job_receipt(store, parent, include_result=True)
        elif mapped == "failed":
            response["error"] = latest.error or "Analysis stopped. You can prepare it again."
        return response
    if parent.status == "completed":
        result = service.read_artifact(store, parent.result_artifact)["result"]
        if result.get("analysis", {}).get("version") == VERSION:
            return {
                "status": "complete",
                "job": service.job_receipt(store, parent, include_result=True),
            }
    return {"status": "missing"}


def submit(store, owner, parent_id, parameters=None, symbol=None, period="selection"):
    parent = service.get_job(store, owner, parent_id)
    specification = {
        "parent_job_id": parent.id,
        "parent_result_artifact": parent.result_artifact,
        "analysis_version": VERSION,
        "parameters": parameters,
        "symbol": symbol,
        "period": period,
    }
    validate_request(store, owner, parent.source_id, specification)
    current = status(store, owner, parent_id)
    if current["status"] in ("queued", "running"):
        return current
    if (
        parameters is None
        and symbol is None
        and current["status"] == "complete"
        and current["job"]["result"].get("analysis", {}).get("version") == VERSION
    ):
        return current
    if current["status"] == "complete":
        previous_result = current["job"]["result"]
        if parameters is None:
            specification["parameters"] = (
                previous_result.get("experiment", {}).get("study_analysis", {}).get("parameters")
            )
        if symbol is None:
            previous_period = (
                previous_result
                if period == "selection"
                else previous_result.get("validation", {}).get("result", {})
            )
            specification["symbol"] = previous_period.get("analysis", {}).get("price_symbol")
    # Same inputs share a request identity. A failed/cancelled analysis may be
    # retried deliberately without changing the original result.
    retry = current.get("analysis_job_id") if current["status"] == "failed" else None
    token = (
        "analysis-"
        + hashlib.sha256(
            service.encoded({**specification, "previous_analysis": current.get("analysis_job_id")})
        ).hexdigest()
    )
    service.submit(
        store,
        owner,
        parent.source_id,
        json.loads(parent.config),
        request_id=token,
        kind="portfolio_analysis",
        specification=specification,
        previous_attempt_id=retry,
    )
    return status(store, owner, parent_id)


def run(store, owner, source_id, specification, progress=None):
    from research.analytics import add_price_charts, build_analysis
    from research.study_analysis import build_study_analysis

    validate_request(store, owner, source_id, specification)
    parent_bundle = service.read_artifact(store, specification["parent_result_artifact"])
    original = parent_bundle["result"]
    parent = service.get_job(store, owner, specification["parent_job_id"])
    retained = saved_overlay(store, parent, original)
    if progress:
        progress(0, 15)
    saved_analysis = retained.get("analysis")
    analysis = (
        copy.deepcopy(saved_analysis)
        if saved_analysis and saved_analysis.get("version") == VERSION
        else build_analysis(
            {**original, **({"analysis": saved_analysis} if saved_analysis else {})}
        )
    )
    snapshot = None
    if parent_bundle.get("inputs_artifact"):
        snapshot = service.read_artifact(store, parent_bundle["inputs_artifact"]).get("snapshot")
    if snapshot:
        selected_symbol = (
            specification["symbol"]
            if specification["period"] == "selection"
            else analysis.get("price_symbol")
        )
        analysis = add_price_charts(analysis, original, snapshot, symbol=selected_symbol)
    result = {"version": specification["analysis_version"], "analysis": analysis}
    experiment = original.get("experiment")
    if experiment:
        rows = {
            r["config_id"]: r["analysis"]
            for r in retained.get("experiment", {}).get("rows", [])
            if r.get("analysis")
        }
        for key, report in experiment.get("selected_reports", {}).items():
            if key in rows and rows[key].get("version") == VERSION:
                continue
            full = (
                report["analysis"]
                if report.get("analysis", {}).get("version") == VERSION
                else build_analysis(report)
            )
            rows[key] = {k: copy.deepcopy(full[k]) for k in ("version", "metrics", "unavailable")}
        previous_study = retained.get("experiment", {}).get("study_analysis")
        parameters = specification["parameters"]
        if previous_study and (
            parameters is None or parameters == previous_study.get("parameters")
        ):
            study_analysis = previous_study
        else:
            study_analysis = build_study_analysis(
                experiment,
                parameters=parameters,
                progress=(lambda done, total: progress(2 + done, 15)) if progress else None,
            )
        result["experiment"] = {
            "analysis_catalog": analysis["catalog"],
            "row_analysis": rows,
            "study_analysis": study_analysis,
        }
    later = original.get("validation", {}).get("result")
    if later:
        saved_later = retained.get("validation", {}).get("result", {}).get("analysis")
        result["validation"] = (
            copy.deepcopy(saved_later)
            if saved_later and saved_later.get("version") == VERSION
            else build_analysis({**later, **({"analysis": saved_later} if saved_later else {})})
        )
        if snapshot:
            selected_symbol = (
                specification["symbol"]
                if specification["period"] == "validation"
                else result["validation"].get("price_symbol")
            )
            result["validation"] = add_price_charts(
                result["validation"], later, snapshot, symbol=selected_symbol
            )
    if progress:
        progress(15, 15)
    return result
