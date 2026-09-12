"""Saved comparisons pin native report evidence independently of later UI work."""

# ruff: noqa: F811 -- isolated shared Flask/native calculation fixtures

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, func, inspect, select
from test_candidate_reports import frozen_studies, seed
from test_jobs import app, client
from test_library import OWNER
from test_report_period_workflow import run_worker
from test_shortlist import change_result, save
from test_shortlist import path as shortlist_path

from database.research_db import (
    ResearchComparison,
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchShortlistCandidate,
    ResearchStore,
)
from services import research_analysis as analysis_service
from services import research_candidates as candidates
from services import research_comparisons as comparisons
from services import scanner_research_service as service
from services.research_storage import backup_store, restore_store


def path(experiment, identifier=None, member=None):
    base = f"/scanner-research/api/library/experiments/{experiment}/comparisons"
    if identifier:
        base += f"/{identifier}"
    return f"{base}/members/{member}" if member else base


def ready(app, client, frozen_studies, reserved=True):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies, reserved)
    study = service.read_artifact(store, service.get_job(store, OWNER, job).result_artifact)[
        "result"
    ]["experiment"]
    winner = save(client, experiment, job, study["recommendation_id"]).json["candidate"]
    alternative = save(client, experiment, job, row["config_id"]).json["candidate"]
    child = candidates.prepare(store, OWNER, job, row["config_id"])
    run_worker(store)
    return experiment, [winner, alternative], job, child["report_job_id"]


def body(members, token="compare-1", reference=None):
    return {
        "request_id": token,
        "candidate_ids": [item["id"] for item in members],
        "reference_candidate_id": reference or members[0]["id"],
    }


def create(client, experiment, members, token="compare-1", reference=None):
    response = client.post(path(experiment), json=body(members, token, reference))
    assert response.status_code in (200, 201), response.json
    return response.json["comparison"]


@pytest.mark.parametrize("reserved", [True, False])
def test_native_reports_compare_and_open_exact_primary_period_without_calculating(
    app, client, frozen_studies, monkeypatch, reserved
):
    experiment, members, job, child = ready(app, client, frozen_studies, reserved)
    original = client.get(f"/scanner-research/api/jobs/{job}/export").data
    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_sources.resolve_broker_session",
        "research.connectors.optuna_portfolio.run_search",
        "services.research_candidates.prepare",
        "services.research_analysis.submit",
    ):
        monkeypatch.setattr(
            target, lambda *a, **k: pytest.fail("Comparison started calculation or acquisition")
        )
    result = create(client, experiment, members)
    assert (
        result["name"].endswith(" · Comparison 1")
        and result["reference_member_id"] == members[0]["id"]
    )
    assert result["compatible"] and result["currency"] == "INR", result["differences"]
    values = next(item for item in result["metrics"] if item["key"] == "net_return_pct")
    assert values["deltas"][members[0]["id"]] == 0
    assert values["deltas"][members[1]["id"]] == pytest.approx(
        members[1]["snapshot"]["summary"]["net_return_pct"]
        - members[0]["snapshot"]["summary"]["net_return_pct"]
    )
    assert result["cumulative"]["status"] == "available"
    monkeypatch.setattr(
        analysis_service,
        "latest_job",
        lambda *a, **k: pytest.fail("Pinned report looked for latest analysis"),
    )
    for member in result["members"]:
        report = client.get(path(experiment, result["id"], member["id"])).json
        assert report["available"], report
        assert report["result"]["summary"] == member["summary"]
        assert report["result"]["report_context"] == member["report_context"]
        assert (
            "experiment" not in report["result"]
            and "validation" not in report["result"]
            and "reserved_evaluation" not in report["result"]
        )
        assert report["result"]["report_context"]["period"] == ("selection" if reserved else "full")
    # Export is the original scientific artifact; overlays are never written into it.
    assert client.get(f"/scanner-research/api/jobs/{job}/export").data == original


def test_bookmark_removal_rename_pointer_updates_and_accepted_retry_preserve_comparison(
    app, client, frozen_studies, monkeypatch
):
    store = app.extensions["research_store"]
    experiment, members, job, child = ready(app, client, frozen_studies)
    result = create(client, experiment, members)
    first_report = client.get(path(experiment, result["id"], members[0]["id"])).json
    for member in members:
        client.patch(
            shortlist_path(experiment, member["id"]),
            json={"revision": 1, "name": "Renamed bookmark"},
        )
        assert (
            client.delete(
                shortlist_path(experiment, member["id"]), json={"revision": 2}
            ).status_code
            == 200
        )
    for identifier in (job, child):
        change_result(store, identifier, lambda item: item["summary"].update(net_return_pct=999))
    monkeypatch.setattr(
        comparisons,
        "_selected_member",
        lambda *a: pytest.fail("Accepted retry read removed bookmarks"),
    )
    response = client.post(path(experiment), json=body(members))
    assert response.status_code == 200 and response.json["reused"]
    assert response.json["comparison"] == result
    assert client.get(path(experiment, result["id"], members[0]["id"])).json == first_report
    changed = body(members, reference=members[1]["id"])
    assert client.post(path(experiment), json=changed).status_code == 409


def test_analysis_pin_and_null_pin_do_not_follow_subsequent_overlays(app, client, frozen_studies):
    store = app.extensions["research_store"]
    experiment, members, job, child = ready(app, client, frozen_studies)
    original = create(client, experiment, members, "original")
    assert all(member["analysis_artifact"] is None for member in original["members"])
    queued = analysis_service.submit(store, OWNER, child, symbol="AAA")
    assert queued["status"] == "queued"
    run_worker(store)
    pinned = create(client, experiment, members, "pinned-analysis")
    member = next(item for item in pinned["members"] if item["report_job_id"] == child)
    assert member["analysis_artifact"] and member["analysis_job_id"] == queued["analysis_job_id"]
    before = client.get(path(experiment, pinned["id"], member["id"])).json
    null_before = client.get(path(experiment, original["id"], member["id"])).json
    artifact = service.read_artifact(store, member["analysis_artifact"])
    artifact["result"]["analysis"]["metrics"]["account_net_return_pct"] = 999
    new_artifact = service.save_artifact(store, artifact)
    with store.sessions.begin() as db:
        db.get(ResearchJob, queued["analysis_job_id"]).result_artifact = new_artifact
    assert client.get(path(experiment, pinned["id"], member["id"])).json == before
    assert client.get(path(experiment, original["id"], member["id"])).json == null_before
    assert client.get(path(experiment, pinned["id"])).json == pinned


def test_incomplete_reports_and_invalid_selections_never_start_work(app, client, frozen_studies):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    original = frozen_studies[True][1]["experiment"]
    members = [
        save(client, experiment, job, item["config_id"]).json["candidate"]
        for item in original["rows"][:2]
    ]
    assert client.post(path(experiment), json=body(members)).status_code == 400
    for invalid in (
        {"request_id": "x", "candidate_ids": [], "reference_candidate_id": "x"},
        body([members[0], members[0]]),
        {**body(members), "reference_candidate_id": "0" * 32},
        {**body(members), "metrics": {}},
        {**body(members), "request_id": "bad token"},
    ):
        assert client.post(path(experiment), json=invalid).status_code == 400
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == 1
        assert db.scalar(select(func.count()).select_from(ResearchComparison)) == 0


def test_concurrent_idempotency_revision_and_archive(app, client, frozen_studies):
    store = app.extensions["research_store"]
    experiment, members, *_ = ready(app, client, frozen_studies)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(
            pool.map(
                lambda _: comparisons.create_comparison(store, OWNER, experiment, body(members)),
                range(4),
            )
        )
    assert len({value["comparison"]["id"] for value in values}) == 1
    assert sum(not value["reused"] for value in values) == 1
    result = values[0]["comparison"]
    with store.sessions() as db:
        revision = db.get(ResearchLibraryExperiment, experiment).revision
    changed = client.patch(
        path(experiment, result["id"]),
        json={"revision": 1, "name": "Lower drawdown options", "note": "Review later"},
    ).json
    assert changed["revision"] == 2 and changed["members"] == result["members"]
    stale = client.patch(path(experiment, result["id"]), json={"revision": 1, "note": "old"})
    assert stale.status_code == 409 and stale.json["current"] == changed
    with store.sessions.begin() as db:
        saved = db.get(ResearchLibraryExperiment, experiment)
        assert saved.revision == revision
        saved.archived = True
    assert client.get(path(experiment, result["id"])).json["archived"]
    assert client.post(path(experiment), json=body(members)).status_code == 200
    assert client.post(path(experiment), json=body(members, "new")).status_code == 400
    assert (
        client.patch(path(experiment, result["id"]), json={"revision": 2, "note": "No"}).status_code
        == 400
    )
    assert client.delete(path(experiment, result["id"])).status_code == 405


def test_owner_member_query_and_bounded_list_resource_reads(
    app, client, frozen_studies, monkeypatch
):
    store = app.extensions["research_store"]
    experiment, members, *_ = ready(app, client, frozen_studies)
    saved = create(client, experiment, members)
    create(client, experiment, members, "second")
    monkeypatch.setattr(
        service, "read_artifact", lambda *a: pytest.fail("List loaded report evidence")
    )
    first = client.get(path(experiment), query_string={"limit": 1}).json
    assert first["total"] == 2 and first["next_offset"] == 1
    assert (
        client.get(path(experiment), query_string={"limit": 1, "offset": 1}).json["next_offset"]
        is None
    )
    assert "members" not in first["items"][0]
    for query in (
        "limit=51",
        "offset=-1",
        "offset=100001",
        "limit=x",
        "limit=1&limit=2",
        "other=1",
    ):
        assert client.get(path(experiment) + "?" + query).status_code == 400
    assert client.get(path(experiment, saved["id"], "0" * 32)).status_code == 404
    assert app.test_client().get(path(experiment)).status_code == 401
    count = {"active": 0}

    def opened(*_):
        count["active"] += 1

    def closed(*_):
        count["active"] -= 1

    event.listen(store.engine, "connect", opened)
    event.listen(store.engine, "close", closed)
    try:
        for _ in range(100):
            assert client.get(path(experiment)).status_code == 200
            assert (
                client.patch(
                    path(experiment, saved["id"]), json={"revision": 99, "note": "stale"}
                ).status_code
                == 409
            )
            assert count["active"] == 0
    finally:
        event.remove(store.engine, "connect", opened)
        event.remove(store.engine, "close", closed)
    with client.session_transaction() as session:
        session["user"] = "foreign"
    assert client.get(path(experiment)).status_code == 404
    assert client.get(path(experiment, saved["id"], members[0]["id"])).status_code == 404


def test_old_schema_backup_and_pinned_restore_after_bookmark_removal(
    app, client, frozen_studies, tmp_path
):
    store = app.extensions["research_store"]
    experiment, members, job, child = ready(app, client, frozen_studies)
    with store.engine.begin() as db:
        db.exec_driver_sql("DROP TABLE research_comparisons")
    backup_store(store, tmp_path.with_name(tmp_path.name + "-old"))
    store.initialize()
    assert "research_comparisons" in inspect(store.engine).get_table_names()
    analysis_service.submit(store, OWNER, child, symbol="AAA")
    run_worker(store)
    saved = create(client, experiment, members)
    for member in members:
        client.delete(shortlist_path(experiment, member["id"]), json={"revision": 1})
    before = client.get(path(experiment, saved["id"], members[1]["id"])).json
    backup = tmp_path.with_name(tmp_path.name + "-backup")
    backup_store(store, backup)
    restored_path = tmp_path.with_name(tmp_path.name + "-restored")
    restore_store(backup, restored_path)
    restored = ResearchStore(restored_path)
    restored.initialize()
    try:
        assert comparisons.get_comparison(restored, OWNER, experiment, saved["id"]) == saved
        assert (
            comparisons.get_member_report(
                restored, OWNER, experiment, saved["id"], members[1]["id"]
            )
            == before
        )
    finally:
        restored.close()


@pytest.mark.parametrize("variant", ["full", "unknown", "currency"])
def test_real_period_mismatch_and_recorded_currency_gate(app, client, frozen_studies, variant):
    store = app.extensions["research_store"]
    experiment, members, *_ = ready(app, client, frozen_studies)
    other_job, _, other_experiment = seed(app, client, frozen_studies, variant != "full")
    with store.sessions.begin() as db:
        db.add(
            ResearchLibraryJob(
                experiment_id=experiment, job_id=other_job, role="run", created_at=time.time()
            )
        )
    if variant == "unknown":
        change_result(store, other_job, lambda result: result.pop("evaluation_basis"))
    elif variant == "currency":
        change_result(
            store,
            other_job,
            lambda result: result["evaluation_basis"]["comparison"].update(currency="USD"),
        )
    study = service.read_artifact(store, service.get_job(store, OWNER, other_job).result_artifact)[
        "result"
    ]["experiment"]
    other = save(client, experiment, other_job, study["recommendation_id"]).json["candidate"]
    response = client.post(path(experiment), json=body([members[0], other]))
    if variant == "currency":
        assert response.status_code == 400 and "currency" in response.json["message"]
        return
    assert response.status_code == 201, response.json
    compared = response.json["comparison"]
    assert not compared["compatible"] and compared["cumulative"]["status"] == "unavailable"
    assert all(metric["deltas"] is None for metric in compared["metrics"])
    if variant == "unknown":
        assert compared["currency"] is None
        assert all(
            value is None
            for metric in compared["metrics"]
            if metric["format"] == "money"
            for value in metric["values"].values()
        )
    else:
        assert "period" in {
            code for difference in compared["differences"] for code in difference["codes"]
        }


@pytest.mark.parametrize("race", ["archive", "bookmark", "report"])
def test_comparison_admission_rechecks_atomic_source_and_archive_state(
    app, client, frozen_studies, monkeypatch, race
):
    store = app.extensions["research_store"]
    experiment, members, job, child = ready(app, client, frozen_studies)
    from research.comparison import build_comparison

    def changed(*args):
        output = build_comparison(*args)
        with store.sessions.begin() as db:
            if race == "archive":
                db.get(ResearchLibraryExperiment, experiment).archived = True
            elif race == "bookmark":
                db.get(ResearchShortlistCandidate, members[0]["id"]).revision += 1
            else:
                db.get(ResearchJob, child).status = "failed"
        return output

    monkeypatch.setattr("research.comparison.build_comparison", changed)
    response = client.post(path(experiment), json=body(members))
    assert response.status_code == 400, response.json
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchComparison)) == 0


@pytest.mark.parametrize("field", ["candidate_report", "evaluation_basis", "analysis"])
def test_unproven_child_report_is_rejected(app, client, frozen_studies, field):
    store = app.extensions["research_store"]
    experiment, members, job, child = ready(app, client, frozen_studies)

    def corrupt(result):
        if field == "candidate_report":
            result[field]["study_job_id"] = "0" * 32
        elif field == "evaluation_basis":
            result[field]["prices_id"] = "0" * 64
        else:
            result[field]["metrics"]["account_net_return_pct"] = 999

    change_result(store, child, corrupt)
    assert client.post(path(experiment), json=body(members)).status_code == 400


@pytest.mark.parametrize(
    "field", ["reference", "request", "oversized", "deltas", "curve", "job", "metric", "proposal"]
)
def test_backup_rejects_corrupt_comparison_snapshot_without_recalculating(
    app, client, frozen_studies, tmp_path, monkeypatch, field
):
    store = app.extensions["research_store"]
    experiment, members, *_ = ready(app, client, frozen_studies)
    saved = create(client, experiment, members)
    with store.sessions.begin() as db:
        row = db.get(ResearchComparison, saved["id"])
        snapshot = json.loads(row.snapshot)
        if field == "reference":
            row.reference_member_id = members[1]["id"]
        elif field == "request":
            row.request_hash = "0" * 64
        elif field == "oversized":
            row.snapshot = " " * (comparisons.MAX_SNAPSHOT_BYTES + 1)
        elif field == "deltas":
            snapshot["presentation"]["metrics"][0]["deltas"][members[0]["id"]] = {"bad": 1}
        elif field == "curve":
            snapshot["presentation"]["cumulative"]["figure"]["data"][0]["x"] *= 2000
        elif field == "job":
            snapshot["members"][0]["job"]["id"] = "0" * 32
        elif field == "metric":
            snapshot["presentation"]["metrics"][0]["format"] = "invented"
        else:
            snapshot["members"][0]["proposal_number"] = -1
        if field not in ("reference", "request", "oversized"):
            row.snapshot = json.dumps(snapshot)
    monkeypatch.setattr(
        "research.comparison.build_comparison",
        lambda *a: pytest.fail("Restore recalculated a frozen comparison"),
    )
    with pytest.raises(ValueError, match="comparison"):
        backup_store(store, tmp_path.with_name(tmp_path.name + "-bad"))


def test_body_quota_and_optional_overlay_validation(app, client, frozen_studies, monkeypatch):
    experiment, members, job, child = ready(app, client, frozen_studies)
    saved = create(client, experiment, members)
    assert (
        client.post(
            path(experiment), data='{"x":"' + "x" * 9000 + '"}', content_type="application/json"
        ).status_code
        == 400
    )
    assert (
        client.patch(
            path(experiment, saved["id"]), json={"revision": 1, "note": "x" * 2001}
        ).status_code
        == 400
    )
    monkeypatch.setattr(comparisons, "MAX_PER_EXPERIMENT", 1)
    assert client.post(path(experiment), json=body(members, "new")).status_code == 400
    monkeypatch.setattr(
        service,
        "ensure_storage_capacity",
        lambda *a: (_ for _ in ()).throw(ValueError("Research storage is full")),
    )
    assert (
        client.patch(
            path(experiment, saved["id"]), json={"revision": 1, "note": "A larger note"}
        ).status_code
        == 400
    )
    assert client.get(path(experiment, saved["id"])).json["revision"] == 1


def test_comparison_mutations_use_native_csrf(app, client, frozen_studies):
    from flask import Flask
    from flask_wtf.csrf import CSRFProtect

    from blueprints.scanner_research import scanner_research_bp

    experiment, members, *_ = ready(app, client, frozen_studies)
    saved = create(client, experiment, members)
    protected = Flask(__name__)
    protected.config.update(
        SECRET_KEY="isolated-comparison-csrf",
        RESEARCH_DATA_DIR=str(app.extensions["research_store"].root),
    )
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    browser = protected.test_client()
    with browser.session_transaction() as session:
        session["user"] = OWNER
    try:
        assert browser.post(path(experiment), json=body(members, "csrf-test")).status_code == 400
        assert (
            browser.patch(
                path(experiment, saved["id"]), json={"revision": 1, "note": "No"}
            ).status_code
            == 400
        )
        assert browser.get(path(experiment, saved["id"])).status_code == 200
    finally:
        protected.extensions["research_store"].close()
