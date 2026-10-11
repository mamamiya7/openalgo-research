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


def _same_wire_value(actual, expected):
    """JSON has one number type; Python evidence keeps its canonical representation."""
    from math import isfinite

    if type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected and isfinite(actual) and isfinite(expected)
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same_wire_value(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_wire_value(a, b) for a, b in zip(actual, expected, strict=True)
        )
    return actual == expected


def _canonical_condition_request(request):
    """Admit an exact registered rule after a browser's numeric JSON round trip.

    Restoring 60 to the registered 60.0 is a transport conversion, not a different
    hypothesis. Global fingerprint and saved-recipe validation remain strict.
    Unknown rules, booleans, extra fields and changed values are never repaired.
    """
    if not isinstance(request, dict):
        return request
    condition = request.get("entry_conditions")
    from research.entry_conditions import STOCK_VERSIONS, stock_module

    if (
        not isinstance(condition, dict)
        or set(condition) != {"version", "hypothesis"}
        or condition.get("version") not in STOCK_VERSIONS
    ):
        return request
    for expected in stock_module(condition["version"]).catalog():
        if _same_wire_value(condition["hypothesis"], expected):
            canonical = dict(request)
            canonical["entry_conditions"] = {**condition, "hypothesis": expected}
            rows = request.get("strategies")
            if isinstance(rows, list):
                canonical["strategies"] = []
                for row in rows:
                    if isinstance(row, dict) and "entry_condition" in row:
                        if not _same_wire_value(
                            row["entry_condition"], canonical["entry_conditions"]
                        ):
                            raise ValueError("The saved strategy entry condition changed")
                        row = {**row, "entry_condition": deepcopy(canonical["entry_conditions"])}
                    canonical["strategies"].append(row)
            return canonical
    return request


def resolve_inputs(store, owner, request):
    portfolio = contract.normalize(_canonical_condition_request(request))
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
    if portfolio.get("automatic_research"):
        from research.automatic_protocol import check_signal_span

        check_signal_span(portfolio, receipt, interval=interval)
        if (
            portfolio["automatic_research"]["version"]
            in (
                "automatic-hypotheses-v2",
                "automatic-hypotheses-v3",
                "automatic-hypotheses-v4",
                "automatic-hypotheses-v5",
                "automatic-hypotheses-v6",
                "automatic-hypotheses-v7",
            )
            and receipt["symbol_count"] > 200
        ):
            raise ValueError(
                "Smart research supports at most 200 NSE stocks; shorten the source universe"
            )
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
    from research.timing_admission import build_admission

    timing = build_admission(
        evidence["portfolio"],
        evidence["strategies"],
        interval=evidence["snapshot"]["provenance"]["interval"],
    )
    automatic_plan = {}
    if evidence["portfolio"].get("automatic_research"):
        from services.research_automatic_catalog import build_plan

        automatic_plan["automatic_plan"] = build_plan(
            evidence["portfolio"], interval=evidence["snapshot"]["provenance"]["interval"]
        )
    return {
        "portfolio": evidence["portfolio"],
        "receipt": evidence["receipt"],
        "interval": evidence["snapshot"]["provenance"]["interval"],
        "versions": evidence["versions"],
        "timing_admission": timing,
        **({"period_plan": evidence["period_plan"]} if evidence.get("period_plan") else {}),
        **automatic_plan,
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
    if evidence.get("condition_replay"):
        from services.research_condition_replay import verify

        verify(store, evidence)
        if trial_id is not None or period != "selection":
            raise ValueError("Replay this saved condition test on its recorded period")
    if (
        period == "evaluation"
        and evidence.get("automatic_recipe", {}).get("validation_mode") == "exploratory_only"
    ):
        raise ValueError("This exploratory study has no untouched later period")
    has_split = bool(
        evidence.get("portfolio", {}).get("validation")
        or evidence.get("portfolio", {}).get("automatic_research")
    )
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
    portfolio.pop("automatic_research", None)
    selected_conditions = (
        report.get("entry_conditions")
        if (trial_id is None or not report.get("automatic_research"))
        else None
    )
    if report.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v2",
        "automatic-hypotheses-v3",
        "automatic-hypotheses-v4",
        "automatic-hypotheses-v5",
        "automatic-hypotheses-v6",
        "automatic-hypotheses-v7",
    ):
        marker = selected[0].get("entry_condition")
        selected_conditions = deepcopy(marker) if marker else None
    if selected_conditions:
        portfolio["entry_conditions"] = deepcopy(selected_conditions)
    else:
        portfolio.pop("entry_conditions", None)
    for item in portfolio["strategies"]:
        chosen = definitions[item["id"]]
        item.update(config=chosen["config"], allocation_pct=chosen["allocation_pct"], search={})
        item.pop("entry_condition", None)
        if chosen.get("entry_condition"):
            item["entry_condition"] = deepcopy(chosen["entry_condition"])
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
            else report.get("replay_origin", {}).get("period")
            or report.get("matched_baseline_origin", {}).get("period", "full"),
            "config_id": config_id,
            **(
                {
                    "study_job_id": job.id,
                    "trial_number": selected_row["trial_number"],
                    "is_objective_winner": study.get(
                        "objective_winner_id", study.get("recommendation_id")
                    )
                    == config_id,
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
    store, owner, evidence, *, saved, checkpoint, progress, cancelled, activity=None, calendar=None
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
    calendar = calendar or (
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
    if evidence["portfolio"].get("automatic_research"):
        from research.automatic_protocol import compile_recipe

        # Reject insufficient chronology before spending broker requests. The
        # final recipe is frozen again with the actual admitted prices below.
        compile_recipe(
            {
                **evidence,
                "snapshot": {
                    **calendar,
                    "provenance": {**calendar.get("provenance", {}), "interval": interval},
                },
            }
        )
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
        reader_many=archive.read_many,
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


def _prepare_automatic_timing(
    store, owner, evidence, *, saved, checkpoint, progress, cancelled, activity=None
):
    """Resolve actual exchange hours once, then acquire the v7 clock union once."""
    from research.automatic_protocol import compile_recipe
    from research.timing_search import build_union, propose
    from services.research_native_calendar import native_calendar_snapshot
    from services.research_timing_prices import PHASE, READY_PHASE
    from services.research_timing_prices import prepare as prepare_union

    if contract.interval_for(evidence["portfolio"], evidence["strategies"]) == "D":
        return _prepare_prices(
            store,
            owner,
            evidence,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
            activity=activity,
        )

    def complete(sessions, hours):
        native = {
            "sessions": sessions,
            "session_hours": hours,
            "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
        }
        return propose(evidence["portfolio"], evidence["strategies"], native)["status"] in (
            "ready",
            "not_applicable",
        )

    reference_id = (saved or {}).get("reference_artifact")
    calendar = (
        service.read_artifact(store, reference_id)
        if reference_id
        else native_calendar_snapshot(
            evidence["signals"],
            warmup_sessions=0,
            tail_sessions=contract.calendar_tail_sessions(
                evidence["portfolio"], evidence["strategies"]
            ),
            completion_check=complete,
        )
    )
    if reference_id:
        from research.calendar_coverage import check_saved_calendar

        check_saved_calendar(calendar)
    descriptor = propose(evidence["portfolio"], evidence["strategies"], calendar)
    if descriptor["status"] == "not_applicable":
        if saved and saved.get("phase") in (PHASE, READY_PHASE):
            raise ValueError("Saved timing union no longer matches the native clock choices")
        prepared = _prepare_prices(
            store,
            owner,
            evidence,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
            activity=activity,
            calendar=calendar,
        )
        return {
            **prepared,
            "timing_descriptor": propose(
                prepared["portfolio"], prepared["strategies"], prepared["snapshot"]
            ),
        }
    if descriptor["status"] != "ready":
        raise ValueError(descriptor.get("reason") or "Native timing history is incomplete")
    plan = build_union(evidence["portfolio"], evidence["strategies"], calendar, descriptor)
    # Admission is price independent; fail before broker work for invalid history.
    compile_recipe(
        {
            **evidence,
            "timing_descriptor": descriptor,
            "timing_union": plan,
            "snapshot": {**calendar, "provenance": {**calendar["provenance"], "interval": "1m"}},
        }
    )
    return prepare_union(
        store,
        owner,
        evidence,
        calendar,
        descriptor=descriptor,
        saved=saved,
        checkpoint=lambda state, counts: checkpoint(
            state,
            {
                **counts,
                "completed": 0.6 * counts["completed"],
                "stage": "prices",
            },
        ),
        progress=progress,
        cancelled=cancelled,
        activity=activity,
    )


def run(
    store,
    owner,
    evidence,
    spec,
    *,
    saved=None,
    checkpoint,
    progress,
    cancelled,
    activity=None,
    observe=None,
    boundary=None,
    job_id=None,
    defer_study_analysis=False,
):
    validate_submission(
        evidence,
        "portfolio_optimize" if spec["portfolio"].get("optimization") else "portfolio_backtest",
        spec,
    )
    portfolio = evidence["portfolio"]
    if activity:
        activity({"stage": "planning"})
    if saved and saved.get("phase") in (
        "calculation",
        "condition_prices",
        "stock_feature_prices",
        "stock_context_prices",
        "stock_market_context_prices",
    ):
        evidence = service.read_artifact(store, saved["inputs_artifact"])
        if evidence["portfolio"] != portfolio or evidence["versions"] != spec["versions"]:
            raise ValueError("Saved calculation inputs do not match this portfolio")
        if not evidence.get("frozen_prices"):
            from research.calendar_coverage import check_saved_calendar

            check_saved_calendar(evidence["snapshot"])
        if evidence.get("condition_replay"):
            from services.research_condition_replay import prepare as prepare_condition

            cancelled()
            evidence = prepare_condition(store, evidence)
            cancelled()
        inputs_id = saved["inputs_artifact"]
    elif evidence.get("frozen_prices"):
        if evidence.get("condition_replay"):
            from services.research_condition_replay import prepare as prepare_condition

            cancelled()
            evidence = prepare_condition(store, evidence)
            cancelled()
        if portfolio.get("automatic_research"):
            from research.automatic_protocol import compile_recipe

            evidence = {**evidence, "automatic_recipe": compile_recipe(evidence)}
        inputs_id = service.save_artifact(store, evidence)
        checkpoint(
            {"phase": "calculation", "inputs_artifact": inputs_id, "calculation": None},
            {"completed": 60, "total": 100, "stage": "backtest"},
        )
    else:
        prepare_prices = (
            _prepare_automatic_timing
            if portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v7"
            else _prepare_prices
        )
        evidence = prepare_prices(
            store,
            owner,
            evidence,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
            **({"activity": activity} if activity else {}),
        )
        if portfolio.get("automatic_research"):
            from research.automatic_protocol import compile_recipe

            evidence = {**evidence, "automatic_recipe": compile_recipe(evidence)}
        inputs_id = service.save_artifact(store, evidence)
        checkpoint(
            {"phase": "calculation", "inputs_artifact": inputs_id, "calculation": None},
            {"completed": 60, "total": 100, "stage": "backtest"},
        )
    from research.automatic_protocol import (
        CONDITION_VERSION,
        HYPOTHESIS_VERSIONS,
        STABILITY_VERSION,
    )

    stock_condition = portfolio.get("entry_conditions", {}).get("version") in (
        "research-stock-entry-conditions-v1",
        "research-stock-entry-conditions-v2",
        "research-stock-entry-conditions-v3",
        "research-joint-entry-conditions-v1",
    )
    needs_stock = (
        stock_condition
        or portfolio.get("automatic_research", {}).get("version") in HYPOTHESIS_VERSIONS
    )
    if needs_stock:
        from services.research_stock_features import prepare_context as prepare_stock

        stock_saved = saved
        if (
            portfolio.get("automatic_research", {}).get("version")
            in ("automatic-hypotheses-v5", "automatic-hypotheses-v6", "automatic-hypotheses-v7")
            or portfolio.get("entry_conditions", {}).get("version")
            == "research-joint-entry-conditions-v1"
        ):
            from services.research_stock_market_context import prepare_context as prepare_stock

            if stock_saved and stock_saved.get("phase") not in (
                "stock_market_context_prices",
                "calculation",
            ):
                stock_saved = None
        elif (
            portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v4"
            or portfolio.get("entry_conditions", {}).get("version")
            == "research-stock-entry-conditions-v3"
        ):
            from services.research_stock_context import prepare_context as prepare_stock

            # Execution acquisition has finished. Only this context's own durable
            # phase or a calculation checkpoint belongs to the context wrapper.
            if stock_saved and stock_saved.get("phase") not in (
                "stock_context_prices",
                "calculation",
            ):
                stock_saved = None

        evidence = prepare_stock(
            store,
            owner,
            evidence,
            saved=stock_saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
            activity=activity,
        )
        if portfolio.get("automatic_research"):
            from research.automatic_protocol import compile_recipe

            evidence["automatic_recipe"] = compile_recipe(evidence)
        inputs_id = service.save_artifact(store, evidence)
        checkpoint(
            {
                "phase": "calculation",
                "inputs_artifact": inputs_id,
                "calculation": (saved or {}).get("calculation"),
                **{
                    key: saved[key]
                    for key in ("development_evidence", "research_mandate")
                    if saved and key in saved
                },
            },
            {"completed": 60, "total": 100, "stage": "optimization"},
        )
    needs_conditions = (
        bool(portfolio.get("entry_conditions"))
        and not stock_condition
        or (portfolio.get("automatic_research", {}).get("version") == CONDITION_VERSION)
    )
    if needs_conditions:
        from services.research_entry_conditions import prepare_context

        evidence = prepare_context(
            store,
            owner,
            evidence,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
        )
        if portfolio.get("automatic_research"):
            from research.automatic_protocol import compile_recipe

            evidence["automatic_recipe"] = compile_recipe(evidence)
        inputs_id = service.save_artifact(store, evidence)
        checkpoint(
            {
                "phase": "calculation",
                "inputs_artifact": inputs_id,
                "calculation": (saved or {}).get("calculation"),
                **{
                    key: saved[key]
                    for key in ("development_evidence", "research_mandate")
                    if saved and key in saved
                },
            },
            {"completed": 60, "total": 100, "stage": "backtest"},
        )
    if boundary:
        boundary()
    if evidence.get("condition_replay"):
        from services.research_condition_replay import verify

        verify(store, evidence)
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

    if portfolio.get("automatic_research"):
        development_writer = None
        research_mandate = None
        if portfolio["automatic_research"]["version"] in HYPOTHESIS_VERSIONS:
            from research.automatic_hypotheses import (
                JOINT_VERSION,
                TIMING_VERSION,
                TRAILING_VERSION,
                policy_for_version,
            )
            from research.automatic_hypotheses import (
                run as automatic_run,
            )
            from research.report_contract import fingerprint
            from research.research_mandate import build as build_mandate
            from research.research_mandate import verify as verify_mandate
            from services.research_automatic_periods import claim
            from services.research_development_evidence import DevelopmentEvidenceWriter

            if not job_id:
                raise ValueError("Smart research needs its durable job identity")
            # Legacy checkpoints did not retain every candidate's history.
            # Do not rerun those candidates or retroactively call them complete.
            saved_calculation = (saved or {}).get("calculation")
            saved_development = (saved or {}).get("development_evidence")
            if not saved_calculation or saved_development is not None:
                research_mandate = (saved or {}).get("research_mandate")
                if saved_calculation:
                    verify_mandate(research_mandate, evidence=evidence, inputs_artifact=inputs_id)
                else:
                    research_mandate = build_mandate(evidence, inputs_artifact=inputs_id)
                version = evidence["automatic_recipe"]["version"]
                stock = evidence.get(
                    "automatic_stock_market_context"
                    if version in (JOINT_VERSION, TRAILING_VERSION, TIMING_VERSION)
                    else "automatic_stock_features"
                )
                development_writer = DevelopmentEvidenceWriter(
                    store,
                    evidence,
                    binding={
                        "inputs_artifact": inputs_id,
                        "search_identity": fingerprint(
                            {"recipe": evidence["automatic_recipe"], "stock": stock}
                        ),
                    },
                    max_evaluations=policy_for_version(version)["max_simulations"],
                    saved=saved_development,
                )
                if saved_calculation:
                    development_writer.reconcile(saved_calculation["evaluations"])
            if evidence["automatic_recipe"]["validation_mode"] != "exploratory_only":
                claim(store, owner, inputs_id, evidence, job_id)
        elif portfolio["automatic_research"]["version"] == CONDITION_VERSION:
            from research.automatic_conditions import run as automatic_run
            from services.research_automatic_periods import claim

            if not job_id:
                raise ValueError("Automatic condition research needs its durable job identity")
            claim(store, owner, inputs_id, evidence, job_id)
        else:
            from research.automatic_research import run as automatic_run

            if portfolio["automatic_research"]["version"] == STABILITY_VERSION:
                from services.research_automatic_periods import claim

                if not job_id:
                    raise ValueError("Automatic stability research needs its durable job identity")
                claim(store, owner, inputs_id, evidence, job_id)

        def persist_automatic(state, counts):
            retained = {}
            if development_writer is not None:
                development_writer.reconcile(state["evaluations"])
                retained["development_evidence"] = development_writer.snapshot()
                retained["research_mandate"] = research_mandate
            checkpoint(
                {
                    "phase": "calculation",
                    "inputs_artifact": inputs_id,
                    "calculation": state,
                    **retained,
                },
                {
                    **counts,
                    "completed": 60 + 40 * counts["completed"] / max(1, counts["total"]),
                    "total": 100,
                    "stage": "optimization",
                },
            )

        result = automatic_run(
            evidence,
            evaluate=evaluate,
            saved=(saved or {}).get("calculation"),
            checkpoint=persist_automatic,
            progress=lambda done, total: progress(60 + 40 * done / max(1, total), 100),
            cancelled=cancelled,
            activity=activity,
            observe=observe,
            boundary=boundary,
            defer_study_analysis=defer_study_analysis,
            **(
                {"retain_evaluation": development_writer.retain}
                if development_writer is not None
                else {}
            ),
        )
        if development_writer is not None:
            result["development_evidence"] = development_writer.snapshot(complete=True)
            result["research_mandate"] = research_mandate
        return result, evidence

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

    if portfolio.get("entry_conditions"):
        from research.entry_conditions import apply

        context = (
            evidence["automatic_stock_market_context"]
            if portfolio["entry_conditions"]["version"] == "research-joint-entry-conditions-v1"
            else evidence["automatic_stock_features"]
            if stock_condition
            else evidence["entry_condition_market"]
        )
        calculation_evidence = apply(calculation_evidence, context)
        if validation_evidence is not None:
            validation_evidence = apply(validation_evidence, context)
        native_evaluate = evaluate

        def evaluate(rows, snapshot, amount, progress=None):
            from research.entry_conditions import markers

            calculated = native_evaluate(rows, snapshot, amount, progress=progress)
            calculated["strategies"] = markers(
                calculated["strategies"], portfolio["entry_conditions"]
            )
            return calculated

    from research.evaluation_basis import build_evaluation_basis

    calculation_basis = build_evaluation_basis(
        calculation_evidence,
        period="selection"
        if portfolio.get("validation")
        else evidence.get("replay_origin", {}).get("period")
        or evidence.get("matched_baseline_origin", {}).get("period", "full"),
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

        calculation_saved = (saved or {}).get("calculation")
        continuation_from = None
        if evidence.get("study_continuation") and not calculation_saved:
            from services.research_study_continuation import seed

            calculation_saved, continuation_from = seed(store, evidence["study_continuation"])

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
            saved=calculation_saved,
            **({"continuation_from": continuation_from} if continuation_from else {}),
            record_timing=True,
            **({"activity": activity} if activity else {}),
            **({"observe": observe} if observe else {}),
            **({"boundary": boundary} if boundary else {}),
        )
        if boundary:
            boundary()
        if defer_study_analysis:
            from research.study_diagnostics import deferred

            result["experiment"]["diagnostics"] = deferred()
        else:
            from research.study_analysis import build_study_analysis

            result["experiment"]["study_analysis"] = build_study_analysis(
                result["experiment"], progress=check_control
            )
        # Every candidate uses the same prepared cohort and price observations.
        # Compact rows reference the study-level basis instead of copying it.
        result["experiment"]["evaluation_basis_id"] = calculation_basis["evidence_id"]
        if evidence.get("study_continuation"):
            result["study_continuation"] = deepcopy(evidence["study_continuation"])
    else:
        result = evaluate(
            calculation_evidence["strategies"],
            calculation_evidence["snapshot"],
            portfolio["capital"],
            progress=calculation_progress,
        )
    if validation_evidence is not None:
        if boundary:
            boundary()
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
    if portfolio.get("entry_conditions"):
        from research.entry_conditions import enrich

        enrich(result, calculation_evidence)
        if validation_evidence is not None:
            enrich(result["validation"]["result"], validation_evidence)
    if evidence.get("candidate_report"):
        from services.research_candidates import verify_reconstruction

        verify_reconstruction(evidence, result)
    if evidence.get("matched_baseline_origin"):
        from services.research_baselines import verify_reconstruction as verify_baseline

        verify_baseline(evidence, result)
    if evidence.get("condition_replay"):
        from services.research_condition_replay import enrich

        enrich(result, evidence)
    return result, evidence
