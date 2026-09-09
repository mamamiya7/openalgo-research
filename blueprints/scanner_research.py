"""App-authenticated research routes, independent of broker-token validity."""

from flask import Blueprint, Response, current_app, jsonify, request, session
from sqlalchemy import func, select

from database.research_db import ResearchJob, ResearchStore, ResearchWorker
from services import scanner_research_service as service
from utils.logging import get_logger

logger = get_logger(__name__)
scanner_research_bp = Blueprint("scanner_research", __name__, url_prefix="/scanner-research/api")


@scanner_research_bp.record_once
def configure(state):
    store = ResearchStore(state.app.config.get("RESEARCH_DATA_DIR"))
    store.initialize()
    state.app.extensions["research_store"] = store


@scanner_research_bp.before_request
def authenticated():
    # Matches OpenAlgo app-account APIs; no broker check or token refresh.
    if not session.get("user"):
        return jsonify(message="Sign in to OpenAlgo to access research"), 401


@scanner_research_bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(message=str(error)), 400


@scanner_research_bp.errorhandler(LookupError)
def missing(error):
    return jsonify(message=str(error)), 404


def store():
    return current_app.extensions["research_store"]


@scanner_research_bp.get("/portfolio/capabilities")
def portfolio_capabilities():
    from research.portfolio import capabilities

    return jsonify(capabilities())


@scanner_research_bp.post("/portfolio/inputs")
def portfolio_input():
    if request.content_length and request.content_length > 8 * 1024 * 1024 + 65536:
        return jsonify(message="CSV upload exceeds 8 MiB"), 413
    upload = request.files.get("file")
    if upload is None:
        raise ValueError("Upload a strategy's dated signal CSV")
    raw = upload.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        return jsonify(message="CSV upload exceeds 8 MiB"), 413
    return jsonify(
        service.create_source(
            store(),
            session["user"],
            raw,
            "broker",
            defer_preparation=True,
            filename=upload.filename,
        )
    ), 201


@scanner_research_bp.post("/portfolio/preflight")
def portfolio_preflight():
    from services.research_portfolio import preview

    return jsonify(preview(store(), session["user"], request.get_json()))


@scanner_research_bp.post("/portfolio/jobs")
def portfolio_submit():
    from services.research_portfolio import submit

    data = request.get_json()
    if not isinstance(data, dict) or set(data) - {"portfolio", "request_id"}:
        raise ValueError("Supply portfolio settings and a request identity")
    if current_app.config.get("RESEARCH_PREVIEW"):
        raise ValueError(
            "Open your native OpenAlgo installation to run a portfolio with broker prices."
        )
    return jsonify(
        submit(store(), session["user"], data.get("portfolio"), data.get("request_id"))
    ), 202


@scanner_research_bp.post("/portfolio/jobs/<job_id>/rerun")
def portfolio_rerun(job_id):
    from services.research_portfolio import rerun

    data = request.get_json()
    if not isinstance(data, dict) or set(data) - {"trial_id", "request_id"}:
        raise ValueError("Supply a saved trial and request identity")
    return jsonify(
        rerun(
            store(),
            session["user"],
            job_id,
            trial_id=data.get("trial_id"),
            request_id=data.get("request_id"),
        )
    ), 202


@scanner_research_bp.route("/sources", methods=["GET", "POST"])
def sources():
    if request.method == "GET":
        if request.args.get("portfolio_inputs") == "1":
            from services.research_portfolio import list_inputs

            return jsonify(
                list_inputs(
                    store(),
                    session["user"],
                    limit=int(request.args.get("limit", "20")),
                    offset=int(request.args.get("offset", "0")),
                )
            )
        return jsonify(service.list_sources(store(), session["user"]))
    if request.content_length and request.content_length > 8 * 1024 * 1024 + 65536:
        return jsonify(message="CSV upload exceeds 8 MiB"), 413
    upload = request.files.get("file")
    if upload is None:
        raise ValueError("Upload a dated scanner CSV")
    kind = request.form.get("source", "")
    if kind not in ("fixture", "public", "broker", "historify"):
        raise ValueError(
            "Choose a price source: OpenAlgo history, official evidence, "
            "a synthetic demo, or a configured Historify archive"
        )
    raw = upload.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        return jsonify(message="CSV upload exceeds 8 MiB"), 413
    import json

    try:
        requirements = (
            json.loads(request.form.get("requirements", "{}"))
            if kind in {"broker", "historify"}
            else None
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid backtest settings") from exc
    return jsonify(
        service.create_source(
            store(), session["user"], raw, kind, requirements, filename=upload.filename
        )
    ), 201


@scanner_research_bp.get("/sources/<source_id>")
def source(source_id):
    return jsonify(service.source_receipt(store(), session["user"], source_id))


@scanner_research_bp.get("/source-capabilities")
def source_capabilities():
    from research.connectors.registry import catalog
    from services.research_sources import source_capabilities

    preview = bool(current_app.config.get("RESEARCH_PREVIEW", False))
    broker_session = bool(not preview and session.get("broker") and session.get("logged_in"))
    if broker_session:
        from utils.session import is_session_valid

        broker_session = is_session_valid()
    return jsonify(
        {
            **source_capabilities(
                store=store(),
                owner=session["user"],
                broker_session=broker_session,
                broker_name=session.get("broker"),
                preview=preview,
            ),
            "connectors": catalog(),
        }
    )


@scanner_research_bp.get("/connectors")
def connectors():
    from research.connectors.registry import catalog

    return jsonify(catalog())


@scanner_research_bp.post("/evidence-updates")
def evidence_update():
    data = request.get_json()
    if not isinstance(data, dict) or not isinstance(data.get("source_id"), str):
        raise ValueError("Choose an owner-visible saved CSV source for the evidence update")
    return jsonify(
        service.submit(
            store(),
            session["user"],
            data["source_id"],
            {},
            request_id=data.get("request_id"),
            kind="evidence_update",
            specification={"end_date": data.get("end_date")},
        )
    ), 202


@scanner_research_bp.route("/jobs", methods=["GET", "POST"])
def jobs():
    if request.method == "POST":
        data = request.get_json()
        if not isinstance(data, dict) or not isinstance(data.get("source_id"), str):
            raise ValueError("A saved source_id and configuration are required")
        return jsonify(
            service.submit(
                store(),
                session["user"],
                data["source_id"],
                data.get("config", {}),
                request_id=data.get("request_id"),
                kind=data.get("kind", "backtest"),
                specification=data.get("specification", {}),
            )
        ), 202
    if "page_size" in request.args:
        return jsonify(
            service.list_jobs(
                store(),
                session["user"],
                page_size=request.args["page_size"],
                cursor=request.args.get("cursor"),
                query=request.args.get("q", ""),
                kind=request.args.get("kind"),
                status=request.args.get("status"),
            )
        )
    with store().sessions() as db:
        rows = db.scalars(
            select(ResearchJob)
            .where(ResearchJob.owner == session["user"])
            .order_by(ResearchJob.created_at.desc())
            .limit(100)
        ).all()
        return jsonify([service.job_receipt(store(), job) for job in rows])


@scanner_research_bp.get("/jobs/<job_id>")
def job(job_id):
    return jsonify(
        service.job_receipt(store(), service.get_job(store(), session["user"], job_id), True)
    )


@scanner_research_bp.post("/jobs/<job_id>/cancel")
def cancel(job_id):
    return jsonify(service.cancel(store(), session["user"], job_id))


@scanner_research_bp.get("/jobs/<job_id>/export")
def export(job_id):
    job = service.get_job(store(), session["user"], job_id)
    if job.status != "completed":
        raise ValueError("Only completed evidence can be exported")
    bundle = service.read_artifact(store(), job.result_artifact)
    if "inputs_artifact" in bundle:
        bundle = {**bundle, "inputs": service.read_artifact(store(), bundle["inputs_artifact"])}
    raw = service.encoded(bundle)
    import hashlib

    return Response(
        raw,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="research-{job.id}.json"',
            "X-Evidence-SHA256": hashlib.sha256(raw).hexdigest(),
            "X-Stored-Artifact-SHA256": job.result_artifact,
        },
    )


@scanner_research_bp.post("/jobs/<job_id>/resume")
def resume(job_id):
    return jsonify(service.resume(store(), session["user"], job_id))


@scanner_research_bp.post("/jobs/<job_id>/retry")
def retry(job_id):
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError("A request identity is required")
    return jsonify(
        service.retry_attempt(store(), session["user"], job_id, data.get("request_id"))
    ), 202


@scanner_research_bp.post("/preflight")
def preflight():
    data = request.get_json()
    if not isinstance(data, dict) or not isinstance(data.get("source_id"), str):
        raise ValueError("A saved source is required")
    if current_app.config.get("RESEARCH_PREVIEW"):
        evidence = service.source_for(store(), session["user"], data["source_id"])
        if service.data_preparation_needed(
            store(),
            evidence,
            data.get("config", {}),
            data.get("kind", "backtest"),
            data.get("specification", {}),
        ):
            raise ValueError("Open the native OpenAlgo app to prepare intraday broker prices.")
    return jsonify(
        service.preflight(
            store(),
            session["user"],
            data["source_id"],
            data.get("config", {}),
            data.get("kind", "backtest"),
            data.get("specification", {}),
        )
    )


@scanner_research_bp.get("/health")
def health():
    import time

    with store().sessions() as db:
        lease = db.get(ResearchWorker, 1)
        active = db.scalar(
            select(func.count())
            .select_from(ResearchJob)
            .where(ResearchJob.status.in_(service.ACTIVE))
        )
        fresh = bool(lease and lease.token and time.time() - lease.heartbeat < 120)
        maintenance = bool(lease and (lease.token or "").startswith("maintenance:"))
        state = (
            "maintenance"
            if fresh and maintenance
            else "online"
            if fresh
            else "stale"
            if lease and lease.token
            else "offline"
        )
        return jsonify(
            status="ok",
            active_jobs=active,
            worker_online=fresh and not maintenance,
            worker_state=state,
            maintenance=fresh and maintenance,
            last_heartbeat=lease.heartbeat if lease else None,
        )
