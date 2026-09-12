"""Native portfolio orchestration over the existing research store and worker.

One queued job prepares the native price union, freezes it, then runs a shared
external-engine account. Original source rows and saved results are immutable.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from time import monotonic

from research import portfolio as contract
from services import scanner_research_service as service


def resolve_inputs(store, owner, request):
    portfolio = contract.normalize(request)
    versions = contract.execution_versions(portfolio)
    strategies, signals, input_rows = [], [], 0
    first, last = portfolio.get("date_from", ""), portfolio.get("date_to", "9999-12-31")
    for definition in portfolio["strategies"]:
        evidence = service.source_for(store, owner, definition["source_id"])
        selected = [
            dict(signal) for signal in evidence["signals"] if first <= signal["date"] <= last
        ]
        if not selected:
            raise ValueError(f"{definition['name']} has no signals in this date range")
        strategies.append(
            {
                **definition,
                "signals": selected,
                "source_receipt": evidence["receipt"],
                "original_csv_base64": evidence.get("original_csv_base64"),
                "original_csv_sha256": evidence.get("original_csv_sha256"),
            }
        )
        signals.extend(dict(signal, strategy_id=definition["id"]) for signal in selected)
        input_rows += evidence["receipt"].get("input_rows", len(selected))
    if len(signals) > 25000:
        raise ValueError("The combined portfolio exceeds 25,000 signals. Shorten the date range.")
    interval = contract.interval_for(portfolio, strategies)
    receipt = {
        "input_rows": input_rows,
        "signal_count": len(signals),
        "symbol_count": len({s["symbol"] for s in signals}),
        "date_from": min(s["date"] for s in signals),
        "date_to": max(s["date"] for s in signals),
        "strategy_count": len(strategies),
        "name": portfolio["name"],
        "input_type": "portfolio",
        "warnings": [],
    }
    result = {
        "portfolio": portfolio,
        "strategies": strategies,
        "signals": signals,
        "receipt": receipt,
        "versions": versions,
        "snapshot": {
            "sessions": [],
            "bars": {},
            "provenance": {
                "provider": "queued-openalgo-history",
                "interval": interval,
                "synthetic": False,
            },
            "coverage": {"status": "preparing", "warnings": []},
        },
    }
    if portfolio.get("validation"):
        from research.portfolio_validation import period_plan

        result["period_plan"] = period_plan(result)
    return result


def preview(store, owner, request):
    evidence = resolve_inputs(store, owner, request)
    return {
        "portfolio": evidence["portfolio"],
        "receipt": evidence["receipt"],
        "interval": evidence["snapshot"]["provenance"]["interval"],
        "versions": evidence["versions"],
        **({"period_plan": evidence["period_plan"]} if evidence.get("period_plan") else {}),
    }


def list_inputs(store, owner, *, limit=20, offset=0):
    """Page saved source receipts, including CSVs whose prices are not prepared."""
    import json

    from sqlalchemy import and_, case, func, select

    from database.research_db import ResearchSource, ResearchSourceReceipt

    if (
        type(limit) is not int
        or type(offset) is not int
        or not 1 <= limit <= 50
        or not 0 <= offset <= 100000
    ):
        raise ValueError("Use a page size of 1–50 and a valid offset")
    csv_hash = func.json_extract(ResearchSourceReceipt.receipt, "$.receipt.original_csv_sha256")
    signals_hash = func.json_extract(ResearchSourceReceipt.receipt, "$.receipt.signals_sha256")
    has_identity = and_(
        func.length(csv_hash) == 64,
        func.length(signals_hash) == 64,
        csv_hash.op("NOT GLOB")("*[^a-f0-9]*"),
        signals_hash.op("NOT GLOB")("*[^a-f0-9]*"),
    )
    # Group only proven original bytes AND normalized observations. Old receipts
    # without both identities remain separate; matching dates/counts prove nothing.
    ranked = (
        select(
            ResearchSourceReceipt.receipt,
            ResearchSource.created_at,
            ResearchSource.id,
            func.row_number()
            .over(
                partition_by=[
                    case((has_identity, csv_hash), else_=ResearchSource.id),
                    case((has_identity, signals_hash), else_=ResearchSource.id),
                ],
                order_by=[ResearchSource.created_at.desc(), ResearchSource.id],
            )
            .label("input_rank"),
        )
        .join(ResearchSource, ResearchSource.id == ResearchSourceReceipt.source_id)
        .where(
            ResearchSource.owner == owner,
            ResearchSourceReceipt.receipt.notlike('%"input_type":"portfolio"%'),
        )
        .subquery()
    )
    with store.sessions() as db:
        receipts = db.execute(
            select(ranked.c.receipt, ranked.c.created_at)
            .where(ranked.c.input_rank == 1)
            .order_by(ranked.c.created_at.desc(), ranked.c.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
    return {
        "items": [
            {**json.loads(value), "created_at": created_at}
            for value, created_at in receipts[:limit]
        ],
        "next_offset": offset + limit if len(receipts) > limit else None,
    }


def submit(store, owner, request, request_id=None):
    evidence = resolve_inputs(store, owner, request)
    _, source = service.register_source(store, owner, evidence)
    portfolio = evidence["portfolio"]
    return service.submit(
        store,
        owner,
        source["id"],
        {"initial_capital": portfolio["capital"]},
        request_id=request_id,
        kind="portfolio_optimize" if portfolio.get("optimization") else "portfolio_backtest",
        specification={"portfolio": portfolio, "versions": evidence["versions"]},
    )


def replay_inputs(store, owner, job_id, *, trial_id=None, period="selection"):
    """One exact candidate/period contract for direct and library follow-up jobs."""
    from research.report_contract import settings_identity

    if period not in ("selection", "evaluation"):
        raise ValueError("Choose the selection or later period")
    job = service.get_job(store, owner, job_id)
    if job.status != "completed":
        raise ValueError("Choose a completed portfolio run")
    bundle = service.read_artifact(store, job.result_artifact)
    if bundle.get("kind") not in contract.KINDS:
        raise ValueError("Choose a completed portfolio run")
    evidence = service.read_artifact(store, bundle["inputs_artifact"])
    report = bundle["result"]
    has_split = bool(evidence.get("portfolio", {}).get("validation"))
    if period == "evaluation" and not has_split:
        raise ValueError(
            "This run has no reserved later period. Choose a run with saved evaluation dates."
        )
    if has_split:
        from research.portfolio_validation import partition

        earlier, later, _ = partition(evidence, prepare_period=period)
        evidence = later if period == "evaluation" else earlier
    selected = report["strategies"]
    selected_row = None
    if trial_id is not None:
        rows = report.get("experiment", {}).get("rows", [])
        matches = [row for row in rows if row["config_id"] == trial_id]
        if len(matches) != 1:
            raise ValueError("Choose a saved trial from this run")
        selected_row = matches[0]
        selected = selected_row["strategies"]
    config_id = settings_identity(selected)
    study = report.get("experiment", {})
    inherited_candidate = report.get("replay_origin", {})
    if selected_row is None:
        selected_row = next(
            (row for row in study.get("rows", []) if row["config_id"] == config_id), None
        )
    definitions = {item["id"]: item for item in selected}
    portfolio = deepcopy(evidence["portfolio"])
    if set(definitions) != {item["id"] for item in portfolio["strategies"]}:
        raise ValueError("Saved strategy identities do not match")
    portfolio.pop("optimization", None)
    portfolio.pop("validation", None)
    for item in portfolio["strategies"]:
        chosen = definitions[item["id"]]
        item.update(config=chosen["config"], allocation_pct=chosen["allocation_pct"], search={})
    portfolio = contract.normalize(portfolio)
    versions = contract.execution_versions(portfolio)
    if any(evidence["versions"].get(key) != value for key, value in versions.items()):
        raise ValueError("This saved run needs its recorded engine version to reproduce exactly")
    evidence = {
        **evidence,
        "portfolio": portfolio,
        "versions": versions,
        "frozen_prices": True,
        "parent_result_artifact": job.result_artifact,
        "replay_origin": {
            "parent_job_id": job.id,
            "parent_result_artifact": job.result_artifact,
            "period": period
            if has_split
            else report.get("replay_origin", {}).get("period", "full"),
            "config_id": config_id,
            **(
                {
                    "study_job_id": job.id,
                    "trial_number": selected_row["trial_number"],
                    "is_objective_winner": study.get("recommendation_id") == config_id,
                }
                if selected_row is not None
                else {
                    key: inherited_candidate[key]
                    for key in ("study_job_id", "trial_number", "is_objective_winner")
                    if inherited_candidate.get("config_id") == config_id
                    and key in inherited_candidate
                }
            ),
        },
        "strategies": [
            {**item, **definitions[item["id"]], "search": {}} for item in evidence["strategies"]
        ],
    }
    return job, evidence


def rerun(store, owner, job_id, *, trial_id=None, request_id=None, period="selection"):
    """Replay or evaluate one retained candidate without acquiring market data."""
    _, evidence = replay_inputs(store, owner, job_id, trial_id=trial_id, period=period)
    portfolio, versions = evidence["portfolio"], evidence["versions"]
    _, source = service.register_source(store, owner, evidence)
    return service.submit(
        store,
        owner,
        source["id"],
        {"initial_capital": portfolio["capital"]},
        request_id=request_id,
        kind="portfolio_backtest",
        specification={"portfolio": portfolio, "versions": versions},
    )


def validate_submission(evidence, kind, spec):
    if (
        kind not in contract.KINDS
        or not isinstance(spec, dict)
        or set(spec) != {"portfolio", "versions"}
    ):
        raise ValueError("Invalid portfolio run")
    portfolio = contract.normalize(spec["portfolio"])
    contract.execution_versions(portfolio, spec["versions"])
    if portfolio != evidence.get("portfolio") or spec["versions"] != evidence.get("versions"):
        raise ValueError("Portfolio settings do not match its saved inputs")
    expected = "portfolio_optimize" if portfolio.get("optimization") else "portfolio_backtest"
    if kind != expected:
        raise ValueError("Portfolio run type does not match its settings")
    return {"portfolio": portfolio, "versions": spec["versions"]}


def _prepare_prices(
    store, owner, evidence, *, saved, checkpoint, progress, cancelled, activity=None
):
    from services.research_acquisition import _save_receipt, acquisition_receipts
    from services.research_checkpoint import MinuteCheckpointWriter, unpack_checkpoint
    from services.research_historify import NativeHistorifyArchive
    from services.research_native_calendar import native_calendar_snapshot
    from services.research_native_prices import acquire_native_prices
    from services.research_sources import (
        AcquisitionBatchPending,
        acquisition_paths,
        resolve_broker_session,
    )

    target, receipts_dir = acquisition_paths(store)
    writer = MinuteCheckpointWriter(store, receipts_dir, saved)
    restored = unpack_checkpoint(store, saved) if saved else None
    reference_id = restored.get("reference_artifact") if restored else None
    calendar = (
        service.read_artifact(store, reference_id)
        if reference_id
        else native_calendar_snapshot(
            evidence["signals"],
            warmup_sessions=0,
            tail_sessions=contract.calendar_tail_sessions(
                evidence["portfolio"], evidence["strategies"]
            ),
            completion_check=contract.calendar_completion_check(
                evidence["portfolio"], evidence["strategies"]
            ),
        )
    )
    if reference_id:
        from research.calendar_coverage import check_saved_calendar

        check_saved_calendar(calendar)
    reference_id = reference_id or service.save_artifact(store, calendar)
    if restored:
        receipts_dir.mkdir(parents=True, exist_ok=True)
        for item in restored.get("acquisition_receipts", []):
            if hashlib.sha256(service.encoded(item["receipt"])).hexdigest() != item["sha256"]:
                raise ValueError("Saved price receipt failed its integrity check")
            _save_receipt(receipts_dir, item["receipt"])
    plan = contract.price_plan(evidence["portfolio"], evidence["strategies"], calendar)
    interval = plan["interval"]
    from research.connectors.vectorbt_portfolio import MAX_MATRIX_CELLS

    cells = len(plan.get("timeline", calendar["sessions"])) * len(evidence["signals"])
    too_large = cells > MAX_MATRIX_CELLS
    if evidence["portfolio"]["engine"] == "nautilus":
        from research.connectors.nautilus_portfolio import (
            MAX_BAR_LOT_CELLS,
            MAX_EVENTS,
            MAX_SIGNAL_LOTS,
        )

        clock_count = len(plan.get("timeline", calendar["sessions"]))
        slots = plan.get("required_timestamps", plan.get("required_dates", {}))
        too_large = (
            cells > MAX_BAR_LOT_CELLS
            or len(evidence["signals"]) > MAX_SIGNAL_LOTS
            or 8 * clock_count + 4 * sum(map(len, slots.values())) > MAX_EVENTS
        )
    if too_large:
        raise ValueError(
            "This portfolio is too large for one run. Shorten the dates or use fewer signals."
        )

    def persist(state):
        # Publish changed symbol/receipt files under one accounted quota scan.
        # Release that transaction before checkpoint() updates the worker lease.
        with service.artifact_publication(store):
            manifest = writer.pack(state)
        checkpoint(
            {
                "phase": "prices",
                "reference_artifact": reference_id,
                "acquisition_manifest": manifest,
            },
            {
                "completed": 0.6 * state["progress"]["completed"],
                "total": state["progress"]["total"],
                "stage": "prices",
            },
        )

    archive = NativeHistorifyArchive(target, interval=interval)
    snapshot = acquire_native_prices(
        evidence["signals"],
        plan,
        calendar,
        reader=archive.read,
        writer=archive.write,
        credentials=lambda: resolve_broker_session(owner),
        archive_dir=receipts_dir,
        prior=restored.get("acquisition") if restored else None,
        checkpoint=persist,
        progress=lambda done, total: progress(0.6 * done, total),
        cancelled=cancelled,
        **({"activity": activity} if activity else {}),
    )
    snapshot.pop("acquisition_checkpoint")
    snapshot["data_requirements"] = plan
    provenance = snapshot["provenance"]
    if provenance.get("batch_pending"):
        raise AcquisitionBatchPending()
    if provenance.get("hard_failures") or provenance.get("acquisition_status") == "failed":
        kinds = {item["kind"] for item in provenance.get("hard_failures", [])}
        if "auth_expired" in kinds:
            raise ValueError("Reconnect your broker in OpenAlgo, then resume this run.")
        raise ValueError("Some required prices could not be downloaded. Resume to retry.")
    from research.portfolio_coverage import prepare

    prepared = prepare(
        {
            **evidence,
            "snapshot": snapshot,
            "reference_artifact": reference_id,
            "acquisition_receipts": acquisition_receipts(snapshot, receipts_dir),
        }
    )

    if evidence["portfolio"]["engine"] == "nautilus":
        from services.research_instruments import prepare_nautilus_instruments

        prepared = prepare_nautilus_instruments(prepared)
    return prepared


def run(
    store, owner, evidence, spec, *, saved=None, checkpoint, progress, cancelled, activity=None
):
    validate_submission(
        evidence,
        "portfolio_optimize" if spec["portfolio"].get("optimization") else "portfolio_backtest",
        spec,
    )
    portfolio = evidence["portfolio"]
    if activity:
        activity({"stage": "planning"})
    if saved and saved.get("phase") == "calculation":
        evidence = service.read_artifact(store, saved["inputs_artifact"])
        if evidence["portfolio"] != portfolio or evidence["versions"] != spec["versions"]:
            raise ValueError("Saved calculation inputs do not match this portfolio")
        if not evidence.get("frozen_prices"):
            from research.calendar_coverage import check_saved_calendar

            check_saved_calendar(evidence["snapshot"])
        inputs_id = saved["inputs_artifact"]
    elif evidence.get("frozen_prices"):
        inputs_id = service.save_artifact(store, evidence)
        checkpoint(
            {"phase": "calculation", "inputs_artifact": inputs_id, "calculation": None},
            {"completed": 60, "total": 100, "stage": "backtest"},
        )
    else:
        evidence = _prepare_prices(
            store,
            owner,
            evidence,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
            **({"activity": activity} if activity else {}),
        )
        inputs_id = service.save_artifact(store, evidence)
        checkpoint(
            {"phase": "calculation", "inputs_artifact": inputs_id, "calculation": None},
            {"completed": 60, "total": 100, "stage": "backtest"},
        )
    if activity:
        # Frozen replays need no archive check or broker download. Only report
        # quantities actually present in the retained input evidence.
        activity(
            {
                "stage": "initializing",
                "prices": {"interval": evidence["snapshot"]["provenance"]["interval"]},
            }
        )
    if portfolio["engine"] == "nautilus":
        from research.connectors.nautilus_runtime import evaluate
    else:
        from research.connectors.vectorbt_portfolio import evaluate

    calculation_evidence = evidence
    validation_evidence = None
    reserved_evaluation = None
    if portfolio.get("validation"):
        from research.portfolio_validation import partition

        calculation_evidence, validation_evidence, validation_info = partition(
            evidence,
            prepare_period="selection"
            if portfolio["validation"].get("mode") == "reserve"
            else "both",
        )
        if portfolio["validation"].get("mode") == "reserve":
            reserved_evaluation = {
                "version": "research-period-plan-v1",
                "selection": {
                    "from": validation_info["train_from"],
                    "to": validation_info["train_to"],
                },
                "evaluation": {
                    "from": validation_info["test_from"],
                    "to": validation_info["test_to"],
                },
                "status": "reserved",
            }
            validation_evidence = None

    from research.evaluation_basis import build_evaluation_basis

    calculation_basis = build_evaluation_basis(
        calculation_evidence,
        period="selection"
        if portfolio.get("validation")
        else evidence.get("replay_origin", {}).get("period", "full"),
    )
    validation_basis = (
        build_evaluation_basis(validation_evidence, period="evaluation")
        if validation_evidence is not None
        else None
    )

    last_control_check = float("-inf")

    def check_control(done, total):
        nonlocal last_control_check
        now = monotonic()
        if done >= total or now - last_control_check >= 0.25:
            cancelled()
            last_control_check = now

    def calculation_progress(done, total):
        check_control(done, total)
        if activity and not portfolio.get("optimization") and done > 0:
            activity({"stage": "backtest"})
        progress(60 + (35 if validation_evidence else 40) * done / max(1, total), 100)

    if portfolio.get("optimization"):
        from research.connectors.optuna_portfolio import run_search

        def persist_calculation(state, counts):
            checkpoint(
                {"phase": "calculation", "inputs_artifact": inputs_id, "calculation": state},
                {
                    **counts,
                    "completed": 60
                    + (35 if validation_evidence else 40)
                    * counts["completed"]
                    / max(1, counts["total"]),
                    "total": 100,
                    "stage": "optimization",
                },
            )

        result = run_search(
            calculation_evidence["strategies"],
            calculation_evidence["snapshot"],
            portfolio["capital"],
            portfolio["optimization"],
            evaluate=evaluate,
            execution=spec["versions"],
            progress=calculation_progress,
            checkpoint=persist_calculation,
            saved=(saved or {}).get("calculation"),
            record_timing=True,
            **({"activity": activity} if activity else {}),
        )
        from research.study_analysis import build_study_analysis

        result["experiment"]["study_analysis"] = build_study_analysis(
            result["experiment"], progress=check_control
        )
        # Every candidate uses the same prepared cohort and price observations.
        # Compact rows reference the study-level basis instead of copying it.
        result["experiment"]["evaluation_basis_id"] = calculation_basis["evidence_id"]
    else:
        result = evaluate(
            calculation_evidence["strategies"],
            calculation_evidence["snapshot"],
            portfolio["capital"],
            progress=calculation_progress,
        )
    if validation_evidence is not None:
        if activity:
            activity({"stage": "validation"})
        selected = {row["id"]: row for row in result["strategies"]}
        testing = [
            {**row, **selected[row["id"]], "search": {}}
            for row in validation_evidence["strategies"]
        ]

        def validation_progress(done, total):
            check_control(done, total)
            progress(95 + 5 * done / max(1, total), 100)

        later = evaluate(
            testing,
            validation_evidence["snapshot"],
            portfolio["capital"],
            progress=validation_progress,
        )
        later["evaluation_basis"] = validation_basis
        later["source"] = {
            "provider": "OpenAlgo Historify",
            "interval": evidence["snapshot"]["provenance"]["interval"],
            "signal_count": len(validation_evidence["signals"]),
            "strategy_count": len(testing),
            **{
                k: v for k, v in validation_evidence["signal_coverage"].items() if k != "exclusions"
            },
        }
        result["validation"] = {**validation_info, "result": later}
    result["evaluation_basis"] = calculation_basis
    result["portfolio"] = deepcopy(portfolio)
    if reserved_evaluation:
        result["reserved_evaluation"] = reserved_evaluation
    if evidence.get("replay_origin"):
        result["replay_origin"] = deepcopy(evidence["replay_origin"])
    result["source"] = {
        "interval": evidence["snapshot"]["provenance"]["interval"],
        "provider": "OpenAlgo Historify",
        "signal_count": len(calculation_evidence["signals"]),
        "strategy_count": len(evidence["strategies"]),
        **{
            key: value
            for key, value in calculation_evidence.get("signal_coverage", {}).items()
            if key != "exclusions"
        },
    }
    result.setdefault("config", {"initial_capital": portfolio["capital"]})
    if result.get("analysis"):
        from research.analytics import add_price_charts

        result["analysis"] = add_price_charts(
            result["analysis"], result, calculation_evidence["snapshot"]
        )
        if validation_evidence and result.get("validation", {}).get("result", {}).get("analysis"):
            later = result["validation"]["result"]
            later["analysis"] = add_price_charts(
                later["analysis"], later, validation_evidence["snapshot"]
            )
    if evidence.get("candidate_report"):
        from services.research_candidates import verify_reconstruction

        verify_reconstruction(evidence, result)
    return result, evidence
