"""App-authenticated research routes, independent of broker-token validity."""

from flask import Blueprint, Response, current_app, jsonify, request, session
from sqlalchemy import func, select

from database.research_db import ResearchJob, ResearchStore, ResearchWorker
from services import research_candidates as candidates
from services import research_chartink as chartink
from services import research_comparisons as comparisons
from services import research_decisions as decisions
from services import research_library as library
from services import research_preferences as preferences
from services import research_shortlist as shortlist
from services import research_study_activity as study_activity
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


@scanner_research_bp.errorhandler(library.RevisionConflict)
def library_conflict(error):
    return jsonify(message=str(error), code="revision_conflict", revision=error.revision), 409


@scanner_research_bp.errorhandler(shortlist.ShortlistConflict)
def shortlist_conflict(error):
    return jsonify(
        message=str(error), code="shortlist_revision_conflict", current=error.current
    ), 409


@scanner_research_bp.errorhandler(comparisons.ComparisonConflict)
def comparison_conflict(error):
    return jsonify(
        message=str(error), code="comparison_revision_conflict", current=error.current
    ), 409


@scanner_research_bp.errorhandler(comparisons.ComparisonRequestConflict)
def comparison_request_conflict(error):
    return jsonify(message=str(error), code="comparison_request_conflict"), 409


@scanner_research_bp.errorhandler(preferences.PreferencesConflict)
def report_preferences_conflict(error):
    return jsonify(
        message=str(error), code="report_preferences_conflict", current=error.current
    ), 409


@scanner_research_bp.errorhandler(decisions.DecisionConflict)
def decision_revision_conflict(error):
    return jsonify(
        message=str(error), code="decision_revision_conflict", current=error.current
    ), 409


@scanner_research_bp.errorhandler(decisions.DecisionRequestConflict)
def decision_request_conflict(error):
    return jsonify(message=str(error), code="decision_request_conflict"), 409


@scanner_research_bp.errorhandler(decisions.DecisionEvidenceChanged)
def decision_evidence_changed(error):
    return jsonify(message=str(error), code="decision_evidence_changed"), 409


@scanner_research_bp.errorhandler(chartink.ImportConflict)
def chartink_conflict(error):
    return jsonify(message=str(error), code="chartink_import_conflict"), 409


@scanner_research_bp.errorhandler(chartink.ImportTooLarge)
def chartink_too_large(error):
    return jsonify(message=str(error)), 413


def store():
    return current_app.extensions["research_store"]


@scanner_research_bp.get("/portfolio/jobs/<job_id>/activity")
def portfolio_study_activity(job_id):
    if set(request.args) - {"limit", "before", "execution"} or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise ValueError("Invalid study activity query")
    return jsonify(
        study_activity.read(
            store(),
            session["user"],
            job_id,
            limit=request.args.get("limit"),
            before=request.args.get("before"),
            execution=request.args.get("execution"),
        )
    )


@scanner_research_bp.get("/portfolio/jobs/<job_id>/candidates")
def portfolio_candidate_reports(job_id):
    return jsonify(candidates.availability(store(), session["user"], job_id))


@scanner_research_bp.post("/portfolio/jobs/<job_id>/candidates/<config_id>/report")
def prepare_candidate_report(job_id, config_id):
    if (request.content_length or 0) > 1024:
        raise ValueError("Candidate report request exceeds its size limit")
    if not request.is_json:
        raise ValueError("Supply an empty JSON object to prepare this exact candidate")
    raw = request.stream.read(1025)
    if len(raw) > 1024:
        raise ValueError("Candidate report request exceeds its size limit")
    try:
        data = current_app.json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Supply an empty JSON object to prepare this exact candidate") from None
    if data != {}:
        raise ValueError("Candidate reports use only their saved settings")
    result = candidates.prepare(store(), session["user"], job_id, config_id)
    return jsonify(result), 202 if result["status"] in ("queued", "running") else 200


@scanner_research_bp.get("/imports/chartink/capabilities")
def chartink_capabilities():
    return jsonify(chartink.capabilities())


@scanner_research_bp.post("/imports/chartink")
def chartink_import():
    if (request.content_length or 0) > chartink.MAX_BODY_BYTES:
        raise chartink.ImportTooLarge("Chartink import exceeds its transport size limit")
    if not request.is_json:
        raise ValueError("Supply a JSON Chartink history import")
    raw = request.stream.read(chartink.MAX_BODY_BYTES + 1)
    if len(raw) > chartink.MAX_BODY_BYTES:
        raise chartink.ImportTooLarge("Chartink import exceeds its transport size limit")
    try:
        data = current_app.json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError("Supply a valid JSON Chartink history import") from None
    result = chartink.import_capture(store(), session["user"], data)
    return jsonify(result), 200 if result["reused"] else 201


def library_body():
    limit = library.MAX_DRAFT_BYTES + 65536
    if (request.content_length or 0) > limit:
        raise ValueError("Research request exceeds its size limit")
    if not request.is_json:
        raise ValueError("Supply a JSON research request")
    raw = request.stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Research request exceeds its size limit")
    try:
        data = current_app.json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Supply valid JSON research settings") from None
    if not isinstance(data, dict):
        raise ValueError("Supply a research request object")
    return data


def library_page():
    archived = request.args.get("archived", "false")
    if archived not in ("true", "false"):
        raise ValueError("Archived filter must be true or false")
    return {
        "search": request.args.get("search", ""),
        "archived": archived == "true",
        "limit": int(request.args.get("limit", "20")),
        "offset": int(request.args.get("offset", "0")),
    }


def shortlist_body():
    if (request.content_length or 0) > shortlist.MAX_BODY_BYTES:
        raise ValueError("Candidate bookmark request exceeds its size limit")
    if not request.is_json:
        raise ValueError("Supply a JSON candidate bookmark request")
    raw = request.stream.read(shortlist.MAX_BODY_BYTES + 1)
    if len(raw) > shortlist.MAX_BODY_BYTES:
        raise ValueError("Candidate bookmark request exceeds its size limit")
    try:
        return current_app.json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError("Supply a valid JSON candidate bookmark request") from None


@scanner_research_bp.route(
    "/library/experiments/<experiment_id>/shortlist", methods=["GET", "POST"]
)
def library_shortlist(experiment_id):
    if request.method == "POST":
        result = shortlist.save_candidate(store(), session["user"], experiment_id, shortlist_body())
        return jsonify(result), 200 if result["reused"] else 201
    if set(request.args) - {"limit", "offset", "job_id", "config_id"} or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise ValueError("Invalid shortlist query")
    return jsonify(
        shortlist.list_candidates(
            store(),
            session["user"],
            experiment_id,
            limit=int(request.args.get("limit", "20")),
            offset=int(request.args.get("offset", "0")),
            job_id=request.args.get("job_id"),
            config_id=request.args.get("config_id"),
        )
    )


@scanner_research_bp.route(
    "/library/experiments/<experiment_id>/shortlist/<candidate_id>",
    methods=["GET", "PATCH", "DELETE"],
)
def library_shortlist_candidate(experiment_id, candidate_id):
    if request.method == "GET":
        if request.args:
            raise ValueError("Invalid saved candidate query")
        return jsonify(
            shortlist.get_candidate(store(), session["user"], experiment_id, candidate_id)
        )
    operation = (
        shortlist.update_candidate if request.method == "PATCH" else shortlist.remove_candidate
    )
    return jsonify(
        operation(store(), session["user"], experiment_id, candidate_id, shortlist_body())
    )


@scanner_research_bp.route(
    "/library/experiments/<experiment_id>/comparisons", methods=["GET", "POST"]
)
def library_comparisons(experiment_id):
    if request.method == "POST":
        result = comparisons.create_comparison(
            store(), session["user"], experiment_id, shortlist_body()
        )
        return jsonify(result), 200 if result["reused"] else 201
    if set(request.args) - {"limit", "offset"} or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise ValueError("Invalid saved comparison query")
    return jsonify(
        comparisons.list_comparisons(
            store(),
            session["user"],
            experiment_id,
            limit=int(request.args.get("limit", "20")),
            offset=int(request.args.get("offset", "0")),
        )
    )


@scanner_research_bp.route(
    "/library/experiments/<experiment_id>/comparisons/<comparison_id>", methods=["GET", "PATCH"]
)
def library_comparison(experiment_id, comparison_id):
    if request.args:
        raise ValueError("Invalid saved comparison query")
    if request.method == "GET":
        return jsonify(
            comparisons.get_comparison(store(), session["user"], experiment_id, comparison_id)
        )
    return jsonify(
        comparisons.update_comparison(
            store(), session["user"], experiment_id, comparison_id, shortlist_body()
        )
    )


@scanner_research_bp.get(
    "/library/experiments/<experiment_id>/comparisons/<comparison_id>/members/<member_id>"
)
def library_comparison_member(experiment_id, comparison_id, member_id):
    if request.args:
        raise ValueError("Invalid saved comparison member query")
    return jsonify(
        comparisons.get_member_report(
            store(), session["user"], experiment_id, comparison_id, member_id
        )
    )


@scanner_research_bp.get(
    "/library/experiments/<experiment_id>/comparisons/<comparison_id>/members/<member_id>/decision"
)
def library_decision_context(experiment_id, comparison_id, member_id):
    if set(request.args) - {"limit", "offset"}:
        raise ValueError("Invalid decision context query")
    return jsonify(
        decisions.decision_context(
            store(), session["user"], experiment_id, comparison_id, member_id, **decision_page()
        )
    )


def decision_page():
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        raise ValueError("Supply each decision query setting once")
    return {
        "limit": int(request.args.get("limit", "20")),
        "offset": int(request.args.get("offset", "0")),
    }


@scanner_research_bp.post(
    "/library/experiments/<experiment_id>/comparisons/<comparison_id>/members/<member_id>/decisions"
)
def library_save_decision(experiment_id, comparison_id, member_id):
    if request.args:
        raise ValueError("Invalid decision query")
    result = decisions.save_decision(
        store(), session["user"], experiment_id, comparison_id, member_id, shortlist_body()
    )
    return jsonify(result), 200 if result["reused"] else 201


@scanner_research_bp.get(
    "/library/experiments/<experiment_id>/comparisons/<comparison_id>/members/<member_id>/decision/evaluations/<evaluation_id>"
)
def library_decision_evaluation(experiment_id, comparison_id, member_id, evaluation_id):
    if request.args:
        raise ValueError("Invalid later evidence query")
    return jsonify(
        decisions.preview_evaluation(
            store(), session["user"], experiment_id, comparison_id, member_id, evaluation_id
        )
    )


@scanner_research_bp.get("/library/experiments/<experiment_id>/decisions")
def library_decisions(experiment_id):
    if set(request.args) - {"state", "limit", "offset"}:
        raise ValueError("Invalid decisions query")
    return jsonify(
        decisions.list_decisions(
            store(),
            session["user"],
            experiment_id,
            state=request.args.get("state"),
            **decision_page(),
        )
    )


@scanner_research_bp.get("/library/experiments/<experiment_id>/decisions/<decision_id>/history")
def library_decision_history(experiment_id, decision_id):
    if set(request.args) - {"limit", "offset"}:
        raise ValueError("Invalid decision history query")
    return jsonify(
        decisions.decision_history(
            store(), session["user"], experiment_id, decision_id, **decision_page()
        )
    )


@scanner_research_bp.get(
    "/library/experiments/<experiment_id>/decisions/<decision_id>/events/<event_id>"
)
def library_decision_event(experiment_id, decision_id, event_id):
    if request.args:
        raise ValueError("Invalid decision entry query")
    return jsonify(
        decisions.get_event(store(), session["user"], experiment_id, decision_id, event_id)
    )


@scanner_research_bp.get(
    "/library/experiments/<experiment_id>/decisions/<decision_id>/events/<event_id>/report"
)
def library_decision_report(experiment_id, decision_id, event_id):
    if set(request.args) - {"evidence"} or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise ValueError("Invalid decision report query")
    return jsonify(
        decisions.event_report(
            store(),
            session["user"],
            experiment_id,
            decision_id,
            event_id,
            evidence=request.args.get("evidence", "selection"),
        )
    )


@scanner_research_bp.post("/library/experiments/<experiment_id>/evidence/opened")
def library_evidence_opened(experiment_id):
    if request.args:
        raise ValueError("Invalid report opening query")
    return jsonify(
        decisions.acknowledge_open(store(), session["user"], experiment_id, shortlist_body())
    )


@scanner_research_bp.route("/library/report-preferences", methods=["GET", "PATCH"])
def report_preferences():
    if request.method == "GET":
        return jsonify(preferences.get_preferences(store(), session["user"]))
    if (request.content_length or 0) > preferences.MAX_BODY_BYTES:
        raise ValueError("Report preferences exceed their size limit")
    if not request.is_json:
        raise ValueError("Supply JSON report preferences")
    raw = request.stream.read(preferences.MAX_BODY_BYTES + 1)
    if len(raw) > preferences.MAX_BODY_BYTES:
        raise ValueError("Report preferences exceed their size limit")
    try:
        data = current_app.json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValueError("Supply valid JSON report preferences") from None
    return jsonify(preferences.update_preferences(store(), session["user"], data))


@scanner_research_bp.route("/library/experiments", methods=["GET", "POST"])
def library_experiments():
    if request.method == "GET":
        return jsonify(library.list_experiments(store(), session["user"], **library_page()))
    return jsonify(library.create_experiment(store(), session["user"], library_body())), 201


@scanner_research_bp.route(
    "/library/experiments/<experiment_id>", methods=["GET", "PATCH", "DELETE"]
)
def library_experiment(experiment_id):
    if request.method == "GET":
        return jsonify(
            library.get_experiment(
                store(),
                session["user"],
                experiment_id,
                jobs_offset=int(request.args.get("jobs_offset", "0")),
                versions_offset=int(request.args.get("versions_offset", "0")),
            )
        )
    if request.method == "DELETE":
        return jsonify(
            library.delete_experiment(store(), session["user"], experiment_id, library_body())
        )
    return jsonify(
        library.update_experiment(store(), session["user"], experiment_id, library_body())
    )


@scanner_research_bp.put("/library/experiments/<experiment_id>/draft")
def library_draft(experiment_id):
    return jsonify(library.save_draft(store(), session["user"], experiment_id, library_body()))


@scanner_research_bp.post("/library/experiments/<experiment_id>/versions")
def library_save_version(experiment_id):
    return jsonify(
        library.save_version(store(), session["user"], experiment_id, library_body())
    ), 201


@scanner_research_bp.get("/library/experiments/<experiment_id>/versions/<version_id>")
def library_version(experiment_id, version_id):
    return jsonify(library.get_version(store(), session["user"], experiment_id, version_id))


@scanner_research_bp.post("/library/experiments/<experiment_id>/versions/<version_id>/restore")
def library_restore_version(experiment_id, version_id):
    return jsonify(
        library.restore_version(store(), session["user"], experiment_id, version_id, library_body())
    )


@scanner_research_bp.post("/library/experiments/<experiment_id>/run")
def library_run(experiment_id):
    if current_app.config.get("RESEARCH_PREVIEW"):
        raise ValueError(
            "Open your native OpenAlgo installation to run a portfolio with broker prices."
        )
    return jsonify(
        library.run_experiment(store(), session["user"], experiment_id, library_body())
    ), 202


@scanner_research_bp.post("/library/experiments/<experiment_id>/replay")
def library_replay(experiment_id):
    return jsonify(
        library.replay_experiment(store(), session["user"], experiment_id, library_body())
    ), 202


@scanner_research_bp.post("/library/experiments/from-job")
def library_from_job():
    data = library_body()
    return jsonify(library.from_job(store(), session["user"], data)), 200 if data.get(
        "experiment_id"
    ) else 201


@scanner_research_bp.get("/library/studies")
def library_studies():
    return jsonify(library.list_studies(store(), session["user"], **library_page()))


@scanner_research_bp.get("/library/versions")
def library_versions():
    return jsonify(library.list_versions(store(), session["user"], **library_page()))


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
    if not isinstance(data, dict) or set(data) - {"trial_id", "request_id", "period"}:
        raise ValueError("Supply a saved trial and request identity")
    return jsonify(
        rerun(
            store(),
            session["user"],
            job_id,
            trial_id=data.get("trial_id"),
            period=data.get("period", "selection"),
            request_id=data.get("request_id"),
        )
    ), 202


@scanner_research_bp.route("/portfolio/jobs/<job_id>/analysis", methods=["GET", "POST"])
def portfolio_analysis(job_id):
    from services import research_analysis

    if request.method == "GET":
        return jsonify(research_analysis.status(store(), session["user"], job_id))
    data = request.get_json()
    if not isinstance(data, dict) or set(data) - {"parameters", "symbol", "period"}:
        raise ValueError("Supply analysis parameters or an empty object")
    result = research_analysis.submit(
        store(),
        session["user"],
        job_id,
        data.get("parameters"),
        data.get("symbol"),
        data.get("period", "selection"),
    )
    return jsonify(result), 200 if result["status"] == "complete" else 202


@scanner_research_bp.get("/portfolio/jobs/<job_id>/analysis/export")
def portfolio_analysis_export(job_id):
    from services.research_analysis import VERSION, latest_job

    parent = service.get_job(store(), session["user"], job_id)
    analysis_job = latest_job(store(), parent, completed=True)
    if analysis_job:
        artifact = analysis_job.result_artifact
        result = service.read_artifact(store(), artifact)
    else:
        if parent.status != "completed":
            raise ValueError("Prepare analysis before exporting it")
        artifact = parent.result_artifact
        original = service.read_artifact(store(), artifact)["result"]
        if not original.get("analysis"):
            raise ValueError("Prepare analysis before exporting it")
        result = {
            "version": VERSION,
            "parent_result_artifact": artifact,
            "analysis": original["analysis"],
            "study_analysis": original.get("experiment", {}).get("study_analysis"),
            "analysis_catalog": original.get("experiment", {}).get("analysis_catalog", []),
            "trial_analysis": [
                {key: row[key] for key in ("trial_number", "config_id", "analysis") if key in row}
                for row in original.get("experiment", {}).get("rows", [])
            ],
            "validation": original.get("validation", {}).get("result", {}).get("analysis"),
        }
    import hashlib

    raw = service.encoded(result)
    return Response(
        raw,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="research-analysis-{parent.id}.json"',
            "X-Stored-Artifact-SHA256": artifact,
            "X-Parent-Artifact-SHA256": parent.result_artifact,
            "X-Evidence-SHA256": hashlib.sha256(raw).hexdigest(),
        },
    )


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
