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
    required = {
        "parent_job_id",
        "parent_result_artifact",
        "analysis_version",
        "parameters",
        "symbol",
        "period",
    }
    if (
        not isinstance(specification, dict)
        or not required <= set(specification)
        or set(specification)
        - required
        - {"benchmark", "reference_artifact", "market_conditions", "market_context"}
    ):
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
    if specification.get("benchmark") is not None:
        from research.benchmark import normalize_benchmark
        from services.research_benchmarks import retained_benchmark

        descriptor = normalize_benchmark(specification["benchmark"])
        if descriptor != specification["benchmark"]:
            raise ValueError("Benchmark request must use its normalized instrument")
        if specification.get("reference_artifact"):
            retained_benchmark(
                store, specification["reference_artifact"], parent.result_artifact, descriptor
            )
    elif specification.get("reference_artifact"):
        raise ValueError("A saved benchmark needs its instrument identity")
    if "market_conditions" in specification:
        from services.research_regimes import request_context, retained_conditions

        context = specification.get("market_context")
        if specification["market_conditions"] is not True or not isinstance(context, dict):
            raise ValueError("Choose the supported historical market-condition analysis")
        if {
            key: value for key, value in context.items() if key != "reference_artifact"
        } != request_context():
            raise ValueError("The market-condition recipe changed; prepare a new analysis")
        if context.get("reference_artifact"):
            retained_conditions(store, context["reference_artifact"], parent.result_artifact)
    elif "market_context" in specification:
        raise ValueError("Saved market conditions need an explicit recipe")
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
    return _merge_overlay(result, extra, analysis_job.result_artifact)


def pinned_overlay(store, result, *, parent_result_artifact, analysis_artifact):
    """Use one explicitly pinned analysis; null always means the original report."""
    if analysis_artifact is None:
        return result
    bundle = service.read_artifact(store, analysis_artifact)
    extra = bundle.get("result")
    if (
        bundle.get("kind") != "portfolio_analysis"
        or bundle.get("parent_result_artifact") != parent_result_artifact
        or not isinstance(extra, dict)
        or extra.get("version") not in READABLE_VERSIONS
        or not isinstance(extra.get("analysis"), dict)
    ):
        raise ValueError("The pinned analysis does not match this saved report")
    return _merge_overlay(result, extra, analysis_artifact)


def _merge_overlay(result, extra, analysis_artifact):
    enriched = {
        **result,
        "analysis": extra["analysis"],
        "report_context": {"analysis_artifact": analysis_artifact},
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
                "report_context": {"analysis_artifact": analysis_artifact},
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
        with store.sessions() as db:
            latest_spec = db.get(ResearchExperiment, latest.id)
            saved_spec = json.loads(latest_spec.specification) if latest_spec else {}
            descriptor = saved_spec.get("benchmark")
        if descriptor:
            response["benchmark"] = descriptor
        if saved_spec.get("market_conditions"):
            response["market_conditions"] = True
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


def submit(
    store,
    owner,
    parent_id,
    parameters=None,
    symbol=None,
    period="selection",
    benchmark=None,
    market_conditions=None,
):
    parent = service.get_job(store, owner, parent_id)
    specification = {
        "parent_job_id": parent.id,
        "parent_result_artifact": parent.result_artifact,
        "analysis_version": VERSION,
        "parameters": parameters,
        "symbol": symbol,
        "period": period,
    }
    if benchmark is not None:
        from research.benchmark import normalize_benchmark

        specification["benchmark"] = normalize_benchmark(benchmark)
    if market_conditions is not None:
        from services.research_regimes import request_context

        if market_conditions is not True:
            raise ValueError("Choose the supported historical market-condition analysis")
        specification.update(market_conditions=True, market_context=request_context())
    validate_request(store, owner, parent.source_id, specification)
    current = status(store, owner, parent_id)
    if current["status"] in ("queued", "running"):
        return current
    if (
        current["status"] == "failed"
        and benchmark is None
        and parameters is None
        and symbol is None
        and current.get("benchmark")
    ):
        specification["benchmark"] = current["benchmark"]
    if (
        current["status"] == "failed"
        and market_conditions is None
        and benchmark is None
        and parameters is None
        and symbol is None
        and current.get("market_conditions")
    ):
        from services.research_regimes import request_context

        specification.update(market_conditions=True, market_context=request_context())
    if (
        parameters is None
        and symbol is None
        and benchmark is None
        and market_conditions is None
        and current["status"] == "complete"
        and current["job"]["result"].get("analysis", {}).get("version") == VERSION
    ):
        return current
    if (
        current["status"] == "complete"
        and parameters is None
        and symbol is None
        and benchmark is not None
        and market_conditions is None
        and specification.get("benchmark")
        == current["job"]["result"].get("analysis", {}).get("benchmark", {}).get("descriptor")
    ):
        return current
    if (
        current["status"] == "complete"
        and market_conditions is True
        and benchmark is None
        and parameters is None
        and symbol is None
        and current["job"]["result"].get("analysis", {}).get("market_conditions", {}).get("version")
        == specification["market_context"]["version"]
        and current["job"]["result"].get("analysis", {}).get("market_conditions", {}).get("recipe")
        == specification["market_context"]["recipe"]
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
    # Pin an existing independent series when changing charts/parameters. Opening
    # or enriching a saved report must not replace its benchmark with newer bars.
    previous = latest_job(store, parent, completed=True)
    if previous:
        previous_bundle = service.read_artifact(store, previous.result_artifact)
        prior_series = previous_bundle.get("result", {}).get("benchmark_evidence")
        if prior_series and (
            specification.get("benchmark") is None
            or specification.get("benchmark") == prior_series["descriptor"]
        ):
            specification["benchmark"] = prior_series["descriptor"]
            specification["reference_artifact"] = previous.result_artifact
        prior_conditions = previous_bundle.get("result", {}).get("market_conditions_evidence")
        if prior_conditions:
            from services.research_regimes import request_context

            if prior_conditions.get("context") == request_context():
                specification["market_conditions"] = True
                specification["market_context"] = {
                    **request_context(),
                    "reference_artifact": previous.result_artifact,
                }
    validate_request(store, owner, parent.source_id, specification)
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


def run(
    store,
    owner,
    source_id,
    specification,
    progress=None,
    *,
    saved=None,
    checkpoint=None,
    cancelled=None,
):
    from research.analytics import add_price_charts, build_analysis
    from research.study_analysis import build_study_analysis

    validate_request(store, owner, source_id, specification)
    outer_progress = progress
    if (specification.get("benchmark") or specification.get("market_conditions")) and progress:

        def progress(done, total):
            outer_progress(80 * done / max(1, total), 100)

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
    # Independent contexts retain separate resumable states in one canonical
    # artifact closure. Existing benchmark-only checkpoints remain readable.
    context_states = (
        copy.deepcopy(saved.get("contexts", {}))
        if saved and saved.get("phase") == "analysis_context"
        else {}
    )
    if saved and saved.get("phase") == "benchmark":
        context_states["benchmark"] = saved

    def persist_context(name):
        def persist(state, counts):
            context_states[name] = state
            if checkpoint:
                checkpoint(
                    state
                    if name == "benchmark" and not specification.get("market_conditions")
                    else {"phase": "analysis_context", "contexts": context_states},
                    counts,
                )

        return persist

    benchmark_share = 10 if specification.get("market_conditions") else 20
    conditions_start = 90 if specification.get("benchmark") else 80

    if specification.get("benchmark"):
        from research.benchmark import add_benchmark
        from services.research_benchmarks import prepare, retained_benchmark

        descriptor = specification["benchmark"]
        market = (
            retained_benchmark(
                store, specification["reference_artifact"], parent.result_artifact, descriptor
            )
            if specification.get("reference_artifact")
            else prepare(
                store,
                owner,
                original,
                descriptor,
                saved=context_states.get("benchmark"),
                checkpoint=persist_context("benchmark"),
                progress=(
                    lambda done, total: outer_progress(
                        80 + benchmark_share * done / max(1, total), 100
                    )
                )
                if outer_progress
                else None,
                cancelled=cancelled,
            )
        )
        result["benchmark_evidence"] = market["evidence"]
        result["benchmark_receipts"] = market["acquisition_receipts"]
        result["analysis"] = add_benchmark(result["analysis"], original, market["evidence"])
        if later:
            result["validation"] = add_benchmark(result["validation"], later, market["evidence"])
    if specification.get("market_conditions"):
        from services.research_regimes import enrich, retained_conditions, run_context

        context = specification["market_context"]
        market = (
            retained_conditions(store, context["reference_artifact"], parent.result_artifact)
            if context.get("reference_artifact")
            else run_context(
                store,
                owner,
                original,
                saved=context_states.get("market_conditions"),
                checkpoint=persist_context("market_conditions"),
                progress=(
                    lambda done, total: outer_progress(
                        conditions_start + (100 - conditions_start) * done / max(1, total), 100
                    )
                )
                if outer_progress
                else None,
                cancelled=cancelled,
            )
        )
        if cancelled:
            cancelled()
        enrich(result, original, market)
    if outer_progress:
        outer_progress(100, 100)
    return result
