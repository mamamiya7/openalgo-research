"""Bounded account-scoped research actions shared by native MCP transports.

Transport authentication supplies the owner. Arguments can never choose an
owner, broker credential, filesystem path or simulation implementation.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy

from sqlalchemy import select

from database.research_db import ResearchSource
from services import scanner_research_service as research

READ_SCOPE = "read:research"
WRITE_SCOPE = "write:research"
READ_TOOLS = frozenset(
    {
        "research_capabilities",
        "research_list_sources",
        "research_list_runs",
        "research_preview_portfolio",
        "research_get_run",
        "research_get_trades",
        "research_export_strategy",
    }
)
WRITE_TOOLS = frozenset(
    {
        "research_upload_csv",
        "research_run_portfolio",
        "research_cancel_run",
        "research_resume_run",
        "research_rerun_trial",
    }
)
TOOLS = READ_TOOLS | WRITE_TOOLS
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CSV_BYTES = 8 * 1024 * 1024


def owner_from_claims(claims):
    """Resolve a verified JWT's subject to its exact native OpenAlgo account."""
    from sqlalchemy.orm import Session

    from database.user_db import User, engine

    subject = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(subject, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", subject):
        raise PermissionError("Invalid research account")
    with Session(engine) as db:
        owner = db.scalar(select(User.username).where(User.id == int(subject)))
    if not isinstance(owner, str) or not owner:
        raise PermissionError("Invalid research account")
    return owner


def owner_from_api_key(api_key):
    """Use the existing native API key check, independent of broker-token validity."""
    from sqlalchemy.orm import Session

    from database.auth_db import db_session, verify_api_key
    from database.user_db import User, engine

    if not isinstance(api_key, str) or not api_key or len(api_key) > 512:
        raise PermissionError("Invalid OpenAlgo API key")
    try:
        owner = verify_api_key(api_key)
    finally:
        db_session.remove()
    if not owner:
        raise PermissionError("Invalid OpenAlgo API key")
    with Session(engine) as db:
        found = db.scalar(select(User.username).where(User.username == owner))
    if not found:
        raise PermissionError("Invalid research account")
    return found


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError(f"Supply a saved {label} id")
    return value


def _limit(value, maximum, label="limit"):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be an integer from 1 to {maximum}")
    return value


def _request_id(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise ValueError("Supply a stable request_id of 8-128 characters for this submission")
    return value


def _job_receipt(value):
    keys = (
        "id",
        "source_id",
        "status",
        "progress",
        "created_at",
        "updated_at",
        "title",
        "kind",
        "counts",
        "resumable",
        "pausable",
        "queue_position",
        "evidence_id",
    )
    result = {key: value[key] for key in keys if key in value}
    if value.get("error"):
        result["message"] = "This run needs attention. Open its saved page in OpenAlgo for details."
    result["path"] = f"/scanner-research?job={value['id']}"
    return result


def _completed(store, owner, job_id):
    job = research.get_job(store, owner, _identifier(job_id, "run"))
    if job.status != "completed":
        raise ValueError("Choose a completed research run")
    bundle = research.read_artifact(store, job.result_artifact)
    return job, bundle


def capabilities(store, owner):
    from research.portfolio import capabilities as portfolio_capabilities

    return {
        **portfolio_capabilities(),
        "portfolio": {
            "max_strategies": 8,
            "intervals": ["D", "1m"],
            "data_selection": "Automatic from saved signals and all selected settings",
            "data_source": "OpenAlgo Historify, with missing prices fetched through the connected broker",
            "requires_saved_source_ids": True,
        },
        "actions": sorted(TOOLS),
        "execution": "Historical research only",
    }


def list_sources(store, owner, limit=25, offset=0):
    _limit(limit, 100)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
        raise ValueError("offset must be an integer from 0 to 10000")
    with store.sessions() as db:
        rows = db.scalars(
            select(ResearchSource)
            .where(ResearchSource.owner == owner)
            .order_by(ResearchSource.created_at.desc(), ResearchSource.id.desc())
            .offset(offset)
            .limit(limit + 1)
        ).all()
    items = []
    for source in rows[:limit]:
        receipt = research.source_receipt(store, owner, source.id)
        info = receipt.get("receipt", {})
        items.append(
            {
                "id": source.id,
                "created_at": source.created_at,
                **{
                    key: info[key]
                    for key in (
                        "name",
                        "input_type",
                        "signal_count",
                        "symbol_count",
                        "date_from",
                        "date_to",
                        "strategy_count",
                    )
                    if key in info
                },
                "price_status": receipt.get("coverage", {}).get("status"),
                "interval": receipt.get("provenance", {}).get("interval"),
            }
        )
    return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}


def list_runs(store, owner, limit=25, cursor=None, query="", status=None):
    _limit(limit, 100)
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 256):
        raise ValueError("Invalid saved-run cursor")
    if status is not None and status not in {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }:
        raise ValueError("Invalid run status")
    result = research.list_jobs(
        store, owner, page_size=limit, cursor=cursor, query=query, status=status
    )
    return {
        "items": [_job_receipt(item) for item in result["items"]],
        "next_cursor": result["next_cursor"],
    }


def preview_portfolio(store, owner, portfolio):
    from services.research_portfolio import preview

    return preview(store, owner, portfolio)


def run_portfolio(store, owner, portfolio, request_id):
    from services.research_portfolio import submit

    return _job_receipt(submit(store, owner, portfolio, _request_id(request_id)))


def upload_csv(store, owner, csv_text):
    if not isinstance(csv_text, str) or not csv_text:
        raise ValueError("Supply the user's dated signal CSV text")
    if len(csv_text) > MAX_CSV_BYTES:
        raise ValueError("CSV upload exceeds 8 MiB")
    raw = csv_text.encode("utf-8")
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError("CSV upload exceeds 8 MiB")
    saved = research.create_source(store, owner, raw, "broker", defer_preparation=True)
    return {
        "id": saved["id"],
        "receipt": saved["receipt"],
        "prices": "Prepared automatically when the portfolio runs",
    }


def get_run(store, owner, job_id, curve_points=100):
    _limit(curve_points, 300, "curve_points")
    job = research.get_job(store, owner, _identifier(job_id, "run"))
    response = _job_receipt(research.job_receipt(store, job))
    if job.status != "completed":
        return response
    report = research.read_artifact(store, job.result_artifact)["result"]
    curve = report.get("equity_curve", [])
    if len(curve) > curve_points:
        indexes = (
            sorted({round(i * (len(curve) - 1) / (curve_points - 1)) for i in range(curve_points)})
            if curve_points > 1
            else [len(curve) - 1]
        )
        curve = [curve[i] for i in indexes]
    response.update(
        research_evidence=_decision_evidence(job, report),
        summary=report.get("summary", {}),
        equity_curve=curve,
        per_strategy=[
            {
                key: row[key]
                for key in (
                    "id",
                    "name",
                    "allocation_pct",
                    "summary",
                    "net_pnl",
                    "contribution_pct",
                )
                if key in row
            }
            for row in report.get("per_strategy", [])
        ],
        execution=report.get("execution", {}),
        policy_version=report.get("policy_version"),
        validation=(
            {
                **{key: value for key, value in report["validation"].items() if key != "result"},
                "summary": report["validation"].get("result", {}).get("summary", {}),
            }
            if report.get("validation")
            else None
        ),
        trials=[
            {key: row[key] for key in ("config_id", "rank", "summary") if key in row}
            for row in report.get("experiment", {}).get("rows", [])[:25]
        ],
    )
    return response


# Only native, bounded facts reach the supervisor. This is deliberately not a
# checkpoint/recipe dump: private source inputs and financial ledgers stay native.
_EVIDENCE_FIELDS = frozenset(
    """
version id policy_id plan_id outcome status headline goal objective stage reasons
checks kind passed tested passing median_return_edge_pct historical_candidate
independently_validated historically_validated selected_config_id selected_is_baseline
selection_basis stopping_reason next_action feasibility validation_mode history_reason
signal_count symbol_count complete_entry_sessions independent_later_period_available
steps search_period from to budget proposals max_simulations counts simulations
attempts_reserved finalists condition_candidates accounts attempts max_accounts
recommended_capital verified_capital feasible_capital original_capital estimated_capital
performance_validated representation_limited scope diagnosis action stop_increases
categories constraint_counts reason cash_shortfall_requests noncash_blocker_requests
operating_point capital settings option_index remaining_exclusions attempts tested_range
admitted_requests eligible_requests fulfilled_risk_targets constrained_requests
evidence_gap_requests whole_share_affordability cash_shortfall percentage_policy
position_limit liquidity missing_price missing_stop_price unknown_sizing excluded_requests
unavailable_prices max_open_positions liquidity_limit inactive_strategy rule_or_window_exclusion unavailable_evidence
unavailable_sizing_intent unexplained_execution_limit gross_exposure strategy_allocation
symbol_exposure trade_stop_risk total_stop_risk correlated_exposure daily_buy_turnover
portfolio_symbol_allocation portfolio_cluster_allocation shared_cash open_positions
daily_volume_participation risk_sizing_target requested_cash_budget verified_sufficient
ledger_id settings_id option_id portfolio_allocation allocation_pct per_trade_pct
max_position_pct initial_capital qualified capital_use idle_cash_pct invested_pct
summary net_return_pct net_pnl max_drawdown_pct closed_trades win_rate_pct profit_factor
final_balance final_equity total_trades exposure_pct return_pct sharpe_ratio
validation_passed supported supports period stress diagnostics chronological decision
selected_report_id reused_selected_report_id selection_changed primary_selected_config_id
primary_capital primary_summary primary_decision chosen_config_id funding_receipt_id
matched_capital baseline_summary account_capital evidence_id policy_version
supervisor_id proposal_id source_result_artifact native_report_id operating_validation
entry_dates_count complete_sessions signal_sessions minimum_closed_trades minimum_entry_dates
minimum_bins minimum_active_bins active_bins closed_entry_sessions bins reason_counts
fraction min max median percentile value count required actual label name enabled
execution_policy entry_condition risk_limits type mode stop_loss_pct take_profit_pct
trailing_stop_pct holding_period_days position_size_pct size_mode risk_per_trade_pct
cost_bps slippage_bps entry_delay_days exit_rule trailing_activation_pct trailing_distance_pct
selected policy candidates_passing_design candidates_checked is_baseline_reference
folds final_fit selection_policy training validation config_id baseline config strategies
order_size_pct target_pct stop_pct hold_sessions trailing_pct trailing_enabled modes
entry_priority priority_seed max_exposure_pct exposure_fill_mode trade_horizon hold_minutes
entry_time exit_time exit_mode sizing atr_period atr_stop_multiple atr_target_multiple
risk_budget_fraction capacity_mode correlation_mode allocation_mode
max_gross_exposure_pct max_symbol_exposure_pct max_trade_risk_pct max_total_stop_risk_pct
max_daily_buy_turnover_pct max_daily_volume_participation_pct max_correlated_exposure_pct
start end key relation params family length multiplier fast slow signal operator left right
allocation priority liquidity_mode capital_use_pct rejected constraints unavailable
engine adapter engine_version adapter_version implementation policy calendar_id support_ledger_id
calendar_bins largest_bin_trade_fraction largest_symbol_positive_profit_fraction
maximum_bin_trade_fraction maximum_symbol_positive_profit_fraction
mean_invested_pct maximum_invested_pct idle_cash_is_failure entry_dates
""".split()
)


def _compact_evidence(value, *, depth=0):
    """Allowlisted bounded projection; never truncate a failure into a pass."""
    import math

    if depth > 10:
        return {"omitted": True}
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, list):
        rows = [_compact_evidence(row, depth=depth + 1) for row in value[:32]]
        if len(value) > 32:
            rows.append({"omitted_items": len(value) - 32})
        return rows
    if isinstance(value, dict):
        return {
            key: _compact_evidence(item, depth=depth + 1)
            for key, item in value.items()
            if key in _EVIDENCE_FIELDS
        }
    return None


def _report_binding(job, report):
    from research.report_contract import fingerprint, settings_identity

    facts = {
        "job_id": job.id,
        "result_artifact": job.result_artifact,
        "config_id": settings_identity(report["strategies"]) if report.get("strategies") else None,
        "capital": report.get("summary", {}).get("initial_capital"),
        "summary": report.get("summary", {}),
        "evaluation_basis": report.get("evaluation_basis"),
        "execution": report.get("execution", {}),
    }
    return {**facts, "financial_report_id": fingerprint(facts)}


def _decision_evidence(job, report):
    findings = report.get("automatic_research") or {}
    recipe = findings.get("recipe") or {}
    budget = recipe.get("budget") or {}
    counts = findings.get("counts") or {}
    maximum, attempted = budget.get("max_simulations"), counts.get("attempts_reserved")
    remaining = (
        max(0, maximum - attempted)
        if type(maximum) is int and type(attempted) is int and maximum >= 0 and attempted >= 0
        else None
    )
    study = report.get("experiment") or {}
    selected = report.get("strategies", [])
    compact_settings = [
        {
            **{key: deepcopy(row[key]) for key in ("id", "name", "allocation_pct") if key in row},
            "config": _compact_evidence(row.get("config", {})),
            **{
                key: _compact_evidence(row[key])
                for key in ("execution_policy", "entry_condition")
                if key in row
            },
        }
        for row in selected[:8]
    ]
    sections = {
        key: _compact_evidence(findings[key])
        for key in (
            "decision",
            "decision_preflight",
            "capital_planning",
            "operating_validation",
            "automatic_design",
            "recommendation_checks",
            "walk_forward",
            "final",
            "counts",
            "primary_decision",
        )
        if key in findings
    }
    if report.get("supervisor_validation"):
        sections["supervisor_validation"] = _compact_evidence(report["supervisor_validation"])
    decision = findings.get("decision") or {}
    # Prefix diagnostics overlap the development observations. A recorded pass
    # must never become independent evidence or an enforced temporal gate.
    prefixes = decision.get("prefix_diagnostics", [])
    binding = _report_binding(job, report)
    return {
        "version": "research-agent-evidence-v1",
        "available": bool(findings or report.get("supervisor_validation")),
        "status": findings.get("status") or report.get("supervisor_validation", {}).get("outcome"),
        "headline": findings.get("headline"),
        "selected_config_id": study.get("recommendation_id") or binding["config_id"],
        "selected_settings": compact_settings,
        "objective": _compact_evidence(
            findings.get("decision_preflight", {}).get("objective")
            or recipe.get("automatic_decision", {}).get("objective")
            or study.get("optimizer", {}).get("objective_definition")
        ),
        "budget": {
            "limit": maximum,
            "attempts_reserved": attempted,
            "remaining_accounts": remaining,
            "proposals": budget.get("proposals"),
        },
        "validation_scope": {
            "mode": recipe.get("validation_mode"),
            "evidence_scope": findings.get("evidence_scope"),
            "selection_basis": findings.get("selection_basis"),
            "prefix_diagnostics": _compact_evidence(prefixes),
            "prefixes_are_independent": False,
            "prefixes_are_acceptance_gates": False,
            "independently_validated": decision.get("independently_validated") is True,
        },
        "provenance": {
            "job_id": job.id,
            "result_artifact": job.result_artifact,
            "financial_report_id": binding["financial_report_id"],
            "evaluation_basis_id": (report.get("evaluation_basis") or {}).get("evidence_id"),
            "policy_version": report.get("policy_version"),
            "execution": _compact_evidence(report.get("execution", {})),
            "historical_research_only": True,
        },
        "preflight": sections.pop("decision_preflight", None),
        **sections,
    }


def decision_evidence(store, owner, job_id):
    """Internal owner-bound evidence for the built-in research supervisor."""
    job, bundle = _completed(store, owner, job_id)
    return _decision_evidence(job, bundle["result"])


def materialize_candidate(store, owner, job_id, config_id=None):
    """Request one exact native report; canonical admission is idempotent."""
    from services import research_candidates as candidates

    job, bundle = _completed(store, owner, job_id)
    config_id = config_id or bundle["result"].get("experiment", {}).get("recommendation_id")
    if not isinstance(config_id, str) or not re.fullmatch(r"[a-f0-9]{64}", config_id):
        raise ValueError("Choose an exact saved candidate identity")
    return candidates.prepare(store, owner, job.id, config_id)


def save_chosen_result(
    store, owner, experiment_id, *, decision_id, event_id, name, revision, request_id
):
    """Save existing exact Keep evidence; never invent or promote a decision."""
    from services import research_chosen_setups as chosen

    result = chosen.choose(
        store,
        owner,
        _identifier(experiment_id, "experiment"),
        {
            "decision_id": _identifier(decision_id, "decision"),
            "event_id": _identifier(event_id, "decision event"),
            "name": name,
            "revision": revision,
            "request_id": _request_id(request_id),
        },
    )
    reopened = reopen_chosen_result(store, owner, experiment_id, result["choice"]["id"])
    if not result["choice"].get("usable"):
        raise ValueError("The chosen setup is no longer usable; review its current decision")
    return {**result, "verification": reopened["verification"]}


def save_accepted_result(store, owner, job_id, *, name, request_id):
    """Publish a supervisor-validated account through native Keep/choice gates.

    Request tokens and the original revisions are retained by the existing
    native services. A retry cannot manufacture another Keep event or choice.
    """
    import hashlib

    from database.research_db import (
        ResearchChosenRequest,
        ResearchDecisionRequest,
        ResearchLibraryExperiment,
        ResearchLibraryJob,
    )
    from research.report_contract import fingerprint, settings_identity
    from services import research_chosen_setups as chosen
    from services import research_decisions as decisions
    from services import research_library as library

    token = _request_id(request_id)
    name = library._text(name, 120, "Chosen setup name")
    job, bundle = _completed(store, owner, job_id)
    report = bundle["result"]
    validation = report.get("supervisor_validation")
    native = {
        k: v
        for k, v in report.items()
        if k not in ("supervisor_validation", "automatic_research", "experiment")
    }
    native_id = fingerprint(native)
    operating = validation.get("operating_validation") if isinstance(validation, dict) else None
    if (
        not isinstance(validation, dict)
        or validation.get("version") != "research-supervisor-validation-v1"
        or validation.get("id") != fingerprint({k: v for k, v in validation.items() if k != "id"})
        or validation.get("outcome") != "validated"
        or validation.get("reasons") != []
        or validation.get("historically_validated") is not True
        or validation.get("independently_validated") is not False
        or validation.get("native_report_id") != native_id
        or validation.get("selected_config_id") != settings_identity(native.get("strategies"))
        or validation.get("capital") != native.get("summary", {}).get("initial_capital")
        or not isinstance(operating, dict)
        or operating.get("version") != "research-operating-validation-v1"
        or operating.get("id") != fingerprint({k: v for k, v in operating.items() if k != "id"})
        or operating.get("outcome") != "validated"
        or operating.get("reasons") != []
        or any(row.get("passed") is not True for row in operating.get("diagnostics", []))
        or operating.get("historically_validated") is not True
        or operating.get("independently_validated") is not False
        or operating.get("selected_report_id") != native_id
        or operating.get("selected_config_id") != validation["selected_config_id"]
        or operating.get("capital") != validation["capital"]
    ):
        raise ValueError("Automatic saving requires exact native supervisor validation")

    def step(label):
        return "research_supervisor_" + hashlib.sha256(f"{token}:{label}".encode()).hexdigest()

    with store.sessions() as db:
        links = db.scalars(
            select(ResearchLibraryExperiment)
            .join(
                ResearchLibraryJob, ResearchLibraryJob.experiment_id == ResearchLibraryExperiment.id
            )
            .where(ResearchLibraryJob.job_id == job.id, ResearchLibraryExperiment.owner == owner)
        ).all()
    if len(links) > 1:
        raise ValueError("Choose the exact experiment before saving this shared result")
    if links:
        if links[0].archived:
            raise ValueError("Restore this experiment before saving its result")
        experiment_id = links[0].id
    else:
        experiment_id = library.from_job(
            store,
            owner,
            {"job_id": job.id, "mode": "backtest", "name": name, "request_id": step("container")},
        )["id"]
    expected = {"result_artifact": job.result_artifact, "analysis_artifact": None}
    context = decisions.direct_context(
        store, owner, experiment_id, job.id, expected_report=expected
    )
    revision = (context.get("current") or {}).get("revision", 0)
    with store.sessions() as db:
        previous = db.get(ResearchDecisionRequest, (owner, step("keep")))
        if previous:
            revision = json.loads(previous.payload)["revision"]
    kept = decisions.save_direct_decision(
        store,
        owner,
        experiment_id,
        job.id,
        {
            "request_id": step("keep"),
            "revision": revision,
            "state": "keep",
            "reason": "Automatically checked historical account; independent evidence is still required. "
            + validation["id"],
            "expected_report": expected,
        },
    )
    context = chosen.context(store, owner, experiment_id)
    revision = context["revision"]
    with store.sessions() as db:
        previous = db.get(ResearchChosenRequest, (owner, step("choose")))
        if previous:
            revision = json.loads(previous.payload)["revision"]
    result = save_chosen_result(
        store,
        owner,
        experiment_id,
        decision_id=kept["decision"]["id"],
        event_id=kept["event"]["id"],
        name=name,
        revision=revision,
        request_id=step("choose"),
    )
    if (
        result["verification"]["financial_report_id"]
        != _report_binding(job, native)["financial_report_id"]
    ):
        raise ValueError("The chosen result differs from the supervisor's native account")
    return {
        **result,
        "experiment_id": experiment_id,
        "supervisor_validation_id": validation["id"],
        "choice_id": result["choice"]["id"],
    }


def reopen_chosen_result(store, owner, experiment_id, choice_id=None):
    """Reopen saved report evidence without admitting replay or optimization."""
    from database.research_db import ResearchSetupVersion
    from research.report_contract import settings_identity
    from services import research_chosen_setups as chosen
    from services import research_decisions as decisions

    experiment_id = _identifier(experiment_id, "experiment")
    if choice_id is None:
        current = chosen.context(store, owner, experiment_id).get("current")
        if not current:
            raise LookupError("No chosen setup is saved")
        choice_id = current["id"]
    choice_id = _identifier(choice_id, "chosen setup")
    with store.sessions() as db:
        experiment, choice = chosen._owned(db, owner, experiment_id, choice_id)
        version = db.get(ResearchSetupVersion, choice.version_id)
        snapshot = deepcopy(chosen.validate_version(choice, version))
        summary = chosen._summary(db, experiment, choice)
        decision_id, event_id = choice.decision_id, choice.event_id
    opened = decisions.event_report(
        store, owner, experiment_id, decision_id, event_id, evidence="selection"
    )
    if not opened.get("available"):
        raise ValueError("The exact chosen report is unavailable")
    job, bundle = _completed(store, owner, snapshot["report_job_id"])
    if job.result_artifact != snapshot["report_result_artifact"]:
        raise ValueError("The chosen native report changed")
    report = opened.get("result") or {}
    binding = _report_binding(job, report)
    if (
        settings_identity(report.get("strategies")) != snapshot["config_id"]
        or binding["capital"] != snapshot["capital"]
        or report.get("summary") != bundle.get("result", {}).get("summary")
    ):
        raise ValueError("The chosen capital, rules or financial report changed")
    return {
        "choice": summary,
        "summary": deepcopy(report["summary"]),
        "research_evidence": _decision_evidence(job, report),
        "verification": {
            "matched": True,
            "choice_id": choice_id,
            "config_id": snapshot["config_id"],
            "capital": snapshot["capital"],
            "report_job_id": job.id,
            "result_artifact": job.result_artifact,
            "financial_report_id": binding["financial_report_id"],
            "new_calculations": 0,
        },
    }


def get_trades(store, owner, job_id, limit=50, offset=0, strategy_id=None, status=None):
    _limit(limit, 200)
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 25000:
        raise ValueError("offset must be an integer from 0 to 25000")
    if strategy_id is not None and (not isinstance(strategy_id, str) or len(strategy_id) > 100):
        raise ValueError("Invalid strategy id")
    if status is not None and status not in {"closed", "pending", "skipped", "excluded"}:
        raise ValueError("Invalid trade status")
    _, bundle = _completed(store, owner, job_id)
    rows = [
        row
        for row in bundle["result"].get("ledger", [])
        if (strategy_id is None or row.get("strategy_id") == strategy_id)
        and (status is None or row.get("status") == status)
    ]
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "next_offset": offset + limit if offset + limit < len(rows) else None,
    }


def cancel_run(store, owner, job_id):
    return _job_receipt(research.cancel(store, owner, _identifier(job_id, "run")))


def resume_run(store, owner, job_id):
    return _job_receipt(research.resume(store, owner, _identifier(job_id, "run")))


def rerun_trial(store, owner, job_id, request_id, trial_id=None):
    from services.research_portfolio import rerun

    if trial_id is not None and (not isinstance(trial_id, str) or len(trial_id) > 128):
        raise ValueError("Invalid saved trial id")
    return _job_receipt(
        rerun(
            store,
            owner,
            _identifier(job_id, "run"),
            trial_id=trial_id,
            request_id=_request_id(request_id),
        )
    )


def export_strategy(store, owner, job_id, strategy_id, trial_id=None):
    if not isinstance(strategy_id, str) or not 1 <= len(strategy_id) <= 100:
        raise ValueError("Invalid strategy id")
    if trial_id is not None and (not isinstance(trial_id, str) or len(trial_id) > 128):
        raise ValueError("Invalid saved trial id")
    job, bundle = _completed(store, owner, job_id)
    report = bundle["result"]
    selected = report.get("strategies", [])
    if trial_id is not None:
        trials = [
            row
            for row in report.get("experiment", {}).get("rows", [])
            if row.get("config_id") == trial_id
        ]
        if len(trials) != 1:
            raise ValueError("Choose a saved trial from this run")
        selected = trials[0]["strategies"]
    matches = [row for row in selected if row.get("id") == strategy_id]
    if len(matches) != 1:
        raise ValueError("Choose a strategy from this completed portfolio")
    evidence = research.read_artifact(store, bundle["inputs_artifact"])
    original = next((row for row in evidence.get("strategies", []) if row["id"] == strategy_id), {})
    definition = {
        key: deepcopy(matches[0][key]) for key in ("id", "name", "allocation_pct", "config")
    }
    return {
        "schema": "openalgo-research-strategy-definition-v1",
        "strategy": definition,
        "input": {
            "kind": "dated_signals",
            "source_id": original.get("source_id"),
            "csv_sha256": original.get("original_csv_sha256"),
        },
        "evidence": {
            "job_id": job.id,
            "trial_id": trial_id,
            "result_artifact": job.result_artifact,
            "engine": report.get("execution", {}),
            "policy_version": report.get("policy_version"),
        },
        "execution_enabled": False,
    }


_HANDLERS = {
    f"research_{function.__name__}": function
    for function in (
        capabilities,
        list_sources,
        list_runs,
        preview_portfolio,
        run_portfolio,
        upload_csv,
        get_run,
        get_trades,
        cancel_run,
        resume_run,
        rerun_trial,
        export_strategy,
    )
}


def dispatch(store, owner, name, arguments):
    if not isinstance(owner, str) or not owner or len(owner) > 80:
        raise PermissionError("Invalid research account")
    if name not in _HANDLERS:
        raise ValueError("Unknown research action")
    if not isinstance(arguments, dict) or any(
        key in arguments for key in ("owner", "store", "api_key", "apikey", "credentials")
    ):
        raise ValueError("Invalid research arguments")
    result = _HANDLERS[name](store, owner, **arguments)
    if (
        len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        > MAX_RESPONSE_BYTES
    ):
        raise ValueError("Research response is too large; request a smaller page")
    return result
