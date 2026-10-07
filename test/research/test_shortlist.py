"""Exact saved candidates survive account, archive, concurrency and storage journeys."""

# ruff: noqa: F811 -- shared isolated Flask and native calculation fixtures

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event, func, inspect, select
from sqlalchemy.pool import NullPool
from test_candidate_reports import frozen_studies, seed
from test_jobs import app, client
from test_library import OWNER, create
from test_report_period_workflow import run_worker

from database.research_db import (
    ResearchCandidateReport,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchShortlistCandidate,
    ResearchStore,
)
from services import research_candidates as candidates
from services import research_shortlist as shortlist
from services import scanner_research_service as service
from services.research_portfolio import run
from services.research_storage import backup_store, maintenance, restore_store


def path(experiment, identifier=None):
    base = f"/scanner-research/api/library/experiments/{experiment}/shortlist"
    return f"{base}/{identifier}" if identifier else base


def save(client, experiment, job, config=None, proposal=None):
    return client.post(
        path(experiment),
        json={
            "job_id": job,
            **({"config_id": config} if config is not None else {}),
            **({"proposal_number": proposal} if proposal is not None else {}),
        },
    )


def change_result(store, job, change):
    parent = service.get_job(store, OWNER, job)
    bundle = service.read_artifact(store, parent.result_artifact)
    change(bundle["result"])
    artifact = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, job).result_artifact = artifact
    return artifact


@pytest.mark.parametrize("child", [False, True])
def test_optional_displayed_artifact_fence_reuses_exact_bookmark_and_rejects_changed_report(
    app, client, frozen_studies, child
):
    store = app.extensions["research_store"]
    study, row, experiment = seed(app, client, frozen_studies, True)
    target = study
    if child:
        target = candidates.prepare(store, OWNER, study, row["config_id"])["report_job_id"]
        run_worker(store)
    artifact = service.get_job(store, OWNER, target).result_artifact
    data = {"job_id": target, "config_id": row["config_id"], "expected_result_artifact": artifact}
    wrong = client.post(path(experiment), json={**data, "expected_result_artifact": "f" * 64})
    assert wrong.status_code == 400 and "displayed result changed" in wrong.json["message"]
    accepted = client.post(path(experiment), json=data)
    assert accepted.status_code == 201, accepted.json
    again = client.post(path(experiment), json=data)
    assert again.status_code == 200 and again.json["reused"]
    assert again.json["candidate"] == accepted.json["candidate"]
    assert accepted.json["candidate"]["source_job_id"] == study
    change_result(store, target, lambda report: report["summary"].update(net_return_pct=999))
    changed = client.post(path(experiment), json=data)
    assert changed.status_code == 400 and "displayed result changed" in changed.json["message"]
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchShortlistCandidate)) == 1
        saved = db.get(ResearchShortlistCandidate, accepted.json["candidate"]["id"])
        assert saved.source_result_artifact == accepted.json["candidate"]["source_result_artifact"]


@pytest.mark.parametrize("child", [False, True])
def test_displayed_report_replaced_after_read_cannot_pass_final_bookmark_write(
    app, client, frozen_studies, monkeypatch, child
):
    store = app.extensions["research_store"]
    study, row, experiment = seed(app, client, frozen_studies, True)
    target = study
    if child:
        target = candidates.prepare(store, OWNER, study, row["config_id"])["report_job_id"]
        run_worker(store)
    data = {
        "job_id": target,
        "config_id": row["config_id"],
        "expected_result_artifact": service.get_job(store, OWNER, target).result_artifact,
    }
    snapshot = shortlist._snapshot

    def replaced(*args, **kwargs):
        receipt = snapshot(*args, **kwargs)
        change_result(store, target, lambda report: report["summary"].update(net_return_pct=999))
        return receipt

    monkeypatch.setattr(shortlist, "_snapshot", replaced)
    with pytest.raises(ValueError, match="saved result changed"):
        shortlist.save_candidate(store, OWNER, experiment, data)
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchShortlistCandidate)) == 0


@pytest.mark.parametrize("reserved", [True, False])
def test_native_candidate_bookmark_details_and_export_remain_exact(
    app, client, frozen_studies, monkeypatch, reserved
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies, reserved)
    original = client.get(f"/scanner-research/api/jobs/{job}/export").data
    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_sources.resolve_broker_session",
        "research.connectors.optuna_portfolio.run_search",
        "services.research_candidates.prepare",
    ):
        monkeypatch.setattr(
            target, lambda *a, **k: pytest.fail("Bookmark started calculation or acquisition")
        )
    response = save(client, experiment, job, row["config_id"])
    assert response.status_code == 201, response.json
    saved = response.json["candidate"]
    assert saved["period"] == ("selection" if reserved else "full")
    assert saved["snapshot"]["summary"] == row["summary"]
    assert saved["snapshot"]["objective"]["score"] == row["score"]
    assert saved["snapshot"]["engine"] == "vectorbt"
    assert saved["name"].endswith(f" · Trial {row['trial_number'] + 1}")
    assert saved["report"]["status"] == "available"
    assert saved["revision"] == 1 and not saved["is_objective_winner"]
    duplicate = save(client, experiment, job, row["config_id"])
    assert duplicate.status_code == 200 and duplicate.json == {"candidate": saved, "reused": True}
    details = client.get(path(experiment, saved["id"])).json
    assert details["available"] and details["strategies"] == row["strategies"]
    assert details["analysis"]["metrics"] == row["analysis"]["metrics"]
    assert details["analysis_catalog"]
    assert client.get(f"/scanner-research/api/jobs/{job}/export").data == original
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == 1
        assert db.scalar(select(func.count()).select_from(ResearchCandidateReport)) == 0


def test_baseline_uses_its_actual_native_full_period_and_derived_configuration(
    app, client, frozen_studies
):
    store = app.extensions["research_store"]
    evidence = copy.deepcopy(frozen_studies[False][0])
    evidence["portfolio"].pop("optimization")
    for strategy in evidence["portfolio"]["strategies"]:
        strategy["search"] = {}
    from research.portfolio import execution_versions

    evidence["versions"] = execution_versions(evidence["portfolio"])
    result, frozen = run(
        store,
        OWNER,
        evidence,
        {"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
        checkpoint=lambda *_: None,
        progress=lambda *_: None,
        cancelled=lambda: None,
    )
    _, receipt = service.register_source(store, OWNER, frozen)
    job = service.submit(
        store,
        OWNER,
        receipt["id"],
        {"initial_capital": evidence["portfolio"]["capital"]},
        kind="portfolio_backtest",
        specification={"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    )
    artifact = service.save_artifact(
        store,
        {
            "kind": "portfolio_backtest",
            "inputs_artifact": service.save_artifact(store, frozen),
            "result": result,
        },
    )
    experiment = create(client)["id"]
    with store.sessions.begin() as db:
        stored = db.get(ResearchJob, job["id"])
        stored.status, stored.result_artifact = "completed", artifact
        db.add(
            ResearchLibraryJob(
                experiment_id=experiment, job_id=stored.id, role="run", created_at=time.time()
            )
        )
        library = db.get(ResearchLibraryExperiment, experiment)
        draft = json.loads(library.draft)
        draft["portfolio"]["validation"] = {"mode": "reserve", "train_pct": 60}
        library.draft = json.dumps(draft)
    response = save(client, experiment, job["id"])
    assert response.status_code == 201, response.json
    saved = response.json["candidate"]
    assert saved["period"] == "full" and saved["origin_kind"] == "backtest"
    assert saved["trial_number"] is None and saved["proposal_number"] is None
    assert saved["snapshot"]["summary"] == result["summary"]
    assert saved["report"] == {"status": "ready", "report_job_id": job["id"]}
    assert client.get(path(experiment, saved["id"])).json["strategies"] == result["strategies"]
    assert save(client, experiment, job["id"], "0" * 64).status_code == 400
    change_result(
        store, job["id"], lambda item: item.update(replay_origin={"period": "evaluation"})
    )
    assert save(client, experiment, job["id"]).status_code == 400


def test_repeat_preserves_first_inspected_proposal_and_server_fields(app, client, frozen_studies):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)

    def repeat(result):
        trial = copy.deepcopy(
            next(
                item
                for item in result["experiment"]["trials"]
                if item["config_id"] == row["config_id"]
            )
        )
        trial.update(number=99, reused=True)
        result["experiment"]["trials"].append(trial)

    change_result(store, job, repeat)
    saved = save(client, experiment, job, row["config_id"], 99).json["candidate"]
    assert saved["trial_number"] == row["trial_number"] and saved["proposal_number"] == 99
    assert saved["name"].endswith("Trial 100")
    changed = client.patch(
        path(experiment, saved["id"]),
        json={"revision": 1, "name": "Lower drawdown", "note": "Check later"},
    ).json
    duplicate = save(client, experiment, job, row["config_id"], row["trial_number"])
    assert duplicate.json == {"candidate": changed, "reused": True}
    assert save(client, experiment, job, row["config_id"], 100).status_code == 400
    assert save(client, experiment, job, row["config_id"], True).status_code == 400
    assert (
        client.post(
            path(experiment), json={"job_id": job, "config_id": row["config_id"], "summary": {}}
        ).status_code
        == 400
    )


def test_contradicted_canonical_trial_is_rejected_but_old_canonical_evidence_is_readable(
    app, client, frozen_studies
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    change_result(store, job, lambda result: result["experiment"].update(trials=[]))
    assert save(client, experiment, job, row["config_id"]).status_code == 400
    change_result(store, job, lambda result: result["experiment"].pop("trials"))
    assert save(client, experiment, job, row["config_id"]).status_code == 201


def test_native_prepared_report_normalizes_and_reopens_the_same_bookmark(
    app, client, frozen_studies
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    saved = save(client, experiment, job, row["config_id"]).json["candidate"]
    candidate = candidates.prepare(store, OWNER, job, row["config_id"])
    assert save(client, experiment, candidate["report_job_id"]).status_code == 400
    run_worker(store)
    normalized = save(client, experiment, candidate["report_job_id"])
    assert normalized.status_code == 200, normalized.json
    assert normalized.json["candidate"]["id"] == saved["id"]
    assert normalized.json["candidate"]["report"] == {
        "status": "ready",
        "report_job_id": candidate["report_job_id"],
    }
    lookup = client.get(path(experiment), query_string={"job_id": candidate["report_job_id"]}).json
    assert [item["id"] for item in lookup["items"]] == [saved["id"]]
    change_result(
        store,
        candidate["report_job_id"],
        lambda result: result["candidate_report"].update(config_id="0" * 64),
    )
    assert save(client, experiment, candidate["report_job_id"]).status_code == 400


def test_concurrent_saves_and_revision_conflicts_do_not_touch_draft_revision(
    app, client, frozen_studies
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    with store.sessions() as db:
        initial = db.get(ResearchLibraryExperiment, experiment).revision
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(
            pool.map(
                lambda _: shortlist.save_candidate(
                    store, OWNER, experiment, {"job_id": job, "config_id": row["config_id"]}
                ),
                range(4),
            )
        )
    assert len({value["candidate"]["id"] for value in values}) == 1
    assert sum(not value["reused"] for value in values) == 1
    saved = values[0]["candidate"]
    updated = client.patch(
        path(experiment, saved["id"]), json={"revision": 1, "note": "Useful alternative"}
    ).json
    assert updated["revision"] == 2
    stale = client.patch(path(experiment, saved["id"]), json={"revision": 1, "name": "Stale name"})
    assert stale.status_code == 409 and stale.json["current"] == updated
    assert stale.json["code"] == "shortlist_revision_conflict"
    assert client.delete(path(experiment, saved["id"]), json={"revision": 1}).status_code == 409
    with store.sessions() as db:
        assert db.get(ResearchLibraryExperiment, experiment).revision == initial
    assert client.delete(path(experiment, saved["id"]), json={"revision": 2}).json == {
        "removed": True,
        "id": saved["id"],
    }
    assert client.get(path(experiment)).json["total"] == 0
    assert client.get(f"/scanner-research/api/jobs/{job}").status_code == 200


def test_owner_experiment_archive_and_csrf_gates(app, client, frozen_studies):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    saved = save(client, experiment, job, row["config_id"]).json["candidate"]
    other = create(client)["id"]
    assert save(client, other, job, row["config_id"]).status_code == 404
    assert client.get(path(other, saved["id"])).status_code == 404
    assert app.test_client().get(path(experiment)).status_code == 401
    with client.session_transaction() as session:
        session["user"] = "foreign"
    assert client.get(path(experiment)).status_code == 404
    assert client.get(path(experiment, saved["id"])).status_code == 404
    assert save(client, experiment, job, row["config_id"]).status_code == 404
    with client.session_transaction() as session:
        session["user"] = OWNER
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = True
    assert client.get(path(experiment)).json["archived"]
    assert client.get(path(experiment, saved["id"])).json["available"]
    assert save(client, experiment, job, row["config_id"]).status_code == 400
    assert (
        client.patch(path(experiment, saved["id"]), json={"revision": 1, "note": "No"}).status_code
        == 400
    )
    assert client.delete(path(experiment, saved["id"]), json={"revision": 1}).status_code == 400
    # Production applies the same global CSRF guard to these new native routes.
    app.config["WTF_CSRF_ENABLED"] = True
    # A separate app is needed because Flask disallows registering hooks after requests.
    from flask import Flask

    from blueprints.scanner_research import scanner_research_bp

    protected = Flask(__name__)
    protected.config.update(SECRET_KEY="isolated-csrf", RESEARCH_DATA_DIR=str(store.root))
    CSRFProtect(protected)
    protected.register_blueprint(scanner_research_bp)
    browser = protected.test_client()
    with browser.session_transaction() as session:
        session["user"] = OWNER
    try:
        assert save(browser, experiment, job, row["config_id"]).status_code == 400
        assert (
            browser.patch(
                path(experiment, saved["id"]), json={"revision": 1, "note": "No"}
            ).status_code
            == 400
        )
        assert (
            browser.delete(path(experiment, saved["id"]), json={"revision": 1}).status_code == 400
        )
    finally:
        protected.extensions["research_store"].close()


def test_paging_lookup_and_lists_do_not_expand_evidence(app, client, frozen_studies, monkeypatch):
    job, row, experiment = seed(app, client, frozen_studies)
    rows = frozen_studies[True][1]["experiment"]["rows"]
    for item in rows:
        assert save(client, experiment, job, item["config_id"]).status_code == 201
    monkeypatch.setattr(
        service, "read_artifact", lambda *_: pytest.fail("List expanded frozen artifacts")
    )
    first = client.get(path(experiment), query_string={"limit": 2}).json
    assert first["total"] == 3 and first["next_offset"] == 2
    second = client.get(path(experiment), query_string={"limit": 2, "offset": 2}).json
    assert len(second["items"]) == 1 and second["next_offset"] is None
    assert len({item["id"] for item in first["items"] + second["items"]}) == 3
    lookup = client.get(
        path(experiment), query_string={"job_id": job, "config_id": row["config_id"]}
    ).json
    assert lookup["total"] == 1 and lookup["items"][0]["config_id"] == row["config_id"]
    for query in (
        "limit=51",
        "offset=-1",
        "offset=100001",
        "limit=x",
        "job_id=bad",
        "config_id=bad",
        "other=1",
        "limit=2&limit=3",
    ):
        assert client.get(f"{path(experiment)}?{query}").status_code == 400


def test_source_pointer_mismatch_does_not_silently_replace_saved_candidate(
    app, client, frozen_studies
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    saved = save(client, experiment, job, row["config_id"]).json["candidate"]
    change_result(
        store,
        job,
        lambda result: result["experiment"]["rows"][0]["summary"].update(net_return_pct=99),
    )
    listing = client.get(path(experiment)).json["items"][0]
    assert not listing["source_available"] and listing["snapshot"] == saved["snapshot"]
    detail = client.get(path(experiment, saved["id"])).json
    assert not detail["available"] and detail["strategies"] is None
    assert detail["candidate"]["source_result_artifact"] == saved["source_result_artifact"]


def test_populated_upgrade_backup_restore_archive_and_missing_old_table(
    app, client, frozen_studies, tmp_path
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    with store.engine.begin() as db:
        db.exec_driver_sql("DROP TABLE research_shortlist_candidates")
    assert "research_shortlist_candidates" not in inspect(store.engine).get_table_names()
    old_backup = tmp_path.with_name(tmp_path.name + "-before-shortlist")
    backup_store(store, old_backup)
    restored_old = tmp_path.with_name(tmp_path.name + "-old-restored")
    restore_store(old_backup, restored_old)
    store.initialize()
    saved = save(client, experiment, job, row["config_id"]).json["candidate"]
    changed = client.patch(
        path(experiment, saved["id"]),
        json={"revision": 1, "name": "Keep for comparison", "note": "A note"},
    ).json
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = True
    backup = tmp_path.with_name(tmp_path.name + "-with-shortlist")
    backup_store(store, backup)
    destination = tmp_path.with_name(tmp_path.name + "-restored-shortlist")
    restore_store(backup, destination)
    restored = ResearchStore(destination)
    restored.initialize()
    try:
        detail = shortlist.get_candidate(restored, OWNER, experiment, saved["id"])
        assert detail["available"] and detail["archived"]
        assert (
            detail["candidate"]["name"] == changed["name"] and detail["candidate"]["revision"] == 2
        )
        assert detail["strategies"] == row["strategies"]
        with maintenance(restored):
            pass
    finally:
        restored.close()


def test_bounds_quota_and_scoped_resource_lifecycle(app, client, frozen_studies, monkeypatch):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    saved = save(client, experiment, job, row["config_id"]).json["candidate"]
    assert isinstance(store.engine.pool, NullPool)
    connections = {"active": 0, "peak": 0}

    def opened(*_):
        connections["active"] += 1
        connections["peak"] = max(connections["peak"], connections["active"])

    def closed(*_):
        connections["active"] -= 1

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
            assert connections["active"] == 0
    finally:
        event.remove(store.engine, "connect", opened)
        event.remove(store.engine, "close", closed)
    assert connections["peak"] <= 1
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
    monkeypatch.setattr(shortlist, "MAX_PER_EXPERIMENT", 1)
    other = next(
        item
        for item in frozen_studies[True][1]["experiment"]["rows"]
        if item["config_id"] != row["config_id"]
    )
    assert save(client, experiment, job, other["config_id"]).status_code == 400
    monkeypatch.setattr(
        service,
        "ensure_storage_capacity",
        lambda *a: (_ for _ in ()).throw(ValueError("Research storage is full")),
    )
    assert (
        client.patch(
            path(experiment, saved["id"]), json={"revision": 1, "note": "increase"}
        ).status_code
        == 400
    )
    assert client.get(path(experiment, saved["id"])).json["candidate"]["revision"] == 1


@pytest.mark.parametrize("race", ["archive", "result", "requested_report"])
def test_save_rechecks_archive_and_exact_sources_inside_write_fence(
    app, client, frozen_studies, monkeypatch, race
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    requested = job
    if race == "requested_report":
        report = candidates.prepare(store, OWNER, job, row["config_id"])
        run_worker(store)
        requested = report["report_job_id"]
    original = shortlist._snapshot

    def change_after_read(*args):
        snapshot = original(*args)
        with store.sessions.begin() as db:
            if race == "archive":
                db.get(ResearchLibraryExperiment, experiment).archived = True
            else:
                db.get(ResearchJob, requested).status = "failed"
        return snapshot

    monkeypatch.setattr(shortlist, "_snapshot", change_after_read)
    response = save(client, experiment, requested, row["config_id"])
    assert response.status_code == 400, response.json
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchShortlistCandidate)) == 0


@pytest.mark.parametrize("change", ["owner", "period", "trial", "snapshot", "oversized", "capital"])
def test_backup_rejects_invalid_bookmark_identity_and_bounded_metadata(
    app, client, frozen_studies, tmp_path, change
):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    saved = save(client, experiment, job, row["config_id"]).json["candidate"]
    with store.sessions.begin() as db:
        bookmark = db.get(ResearchShortlistCandidate, saved["id"])
        if change == "owner":
            bookmark.owner = "foreign"
        elif change == "period":
            bookmark.period = "evaluation"
        elif change == "trial":
            bookmark.trial_number = -1
        elif change == "oversized":
            bookmark.snapshot = " " * (shortlist.MAX_SNAPSHOT_BYTES + 1)
        else:
            snapshot = json.loads(bookmark.snapshot)
            if change == "snapshot":
                snapshot["summary"] = {"replacement": [1, 2, 3]}
            else:
                snapshot["capital"] = {"unknown": "nested"}
            bookmark.snapshot = json.dumps(snapshot)
    target = tmp_path.with_name(tmp_path.name + "-invalid-backup")
    with pytest.raises(ValueError, match="candidate"):
        backup_store(store, target)


def test_old_malformed_study_rows_and_missing_child_artifact_are_clear(app, client, frozen_studies):
    store = app.extensions["research_store"]
    job, row, experiment = seed(app, client, frozen_studies)
    saved = save(client, experiment, job, row["config_id"]).json["candidate"]
    report = candidates.prepare(store, OWNER, job, row["config_id"])
    run_worker(store)
    with store.sessions.begin() as db:
        db.get(ResearchJob, report["report_job_id"]).result_artifact = None
    receipt = client.get(path(experiment, saved["id"])).json
    assert receipt["available"] and receipt["report"]["status"] == "unavailable"
    change_result(store, job, lambda result: result["experiment"].pop("rows"))
    assert save(client, experiment, job, row["config_id"]).status_code == 400
