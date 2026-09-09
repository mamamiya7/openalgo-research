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
