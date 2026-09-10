"""Research library persistence, ownership, atomic launch and source continuity."""

# ruff: noqa: F811 -- shared isolated fixtures

import copy
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.pool import NullPool
from test_acquisition import reference
from test_jobs import app, client  # noqa: F401
from test_native_price_workflow import prepare_archive
from test_portfolio_workflow import portfolio as request_for

from database.research_db import (
    Base,
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchLibraryRequest,
    ResearchLibrarySource,
    ResearchRequest,
    ResearchSetupVersion,
    ResearchSource,
)
from research.portfolio import normalize
from services import research_library as library
from services import scanner_research_service as service
from services import scanner_research_worker as worker

BASE = "/scanner-research/api/library"
OWNER = "research-test"


def draft_for(client, *, optimize=False):
    portfolio = normalize(request_for(client, optimize=optimize))
    draft = library.fresh_draft()
    draft.update(portfolio=portfolio, optimizing=optimize, equalWeights=False)
    if optimize:
        draft["optimization"] = portfolio.pop("optimization")
    return draft


def create(client, draft=None, name="Research idea"):
    response = client.post(
        f"{BASE}/experiments", json={"name": name, **({"draft": draft} if draft else {})}
    )
    assert response.status_code == 201, response.json
    return response.json


def run(client, experiment, token="library-run-first"):
    response = client.post(
        f"{BASE}/experiments/{experiment['id']}/run",
        json={"revision": experiment["revision"], "request_id": token},
    )
    assert response.status_code == 202, response.json
    return response.json


def count(store, model):
    with store.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_empty_and_invalid_financial_draft_survives_new_authenticated_session(app, client):
    experiment = create(client)
    assert experiment["revision"] == 1 and experiment["draft"]["portfolio"]["strategies"] == []
    draft = experiment["draft"]
    draft["portfolio"].update(name="", capital=0)
    saved = client.put(
        f"{BASE}/experiments/{experiment['id']}/draft", json={"revision": 1, "draft": draft}
    )
    assert saved.status_code == 200
    second = app.test_client()
    with second.session_transaction() as session:
        session["user"] = OWNER
    reopened = second.get(f"{BASE}/experiments/{experiment['id']}").json
    assert reopened["draft"]["portfolio"]["name"] == ""
    assert reopened["draft"]["portfolio"]["capital"] == 0
    assert reopened["revision"] == 2
    rejected = second.post(
        f"{BASE}/experiments/{experiment['id']}/run",
        json={"revision": 2, "request_id": "invalid-empty-run"},
    )
    assert rejected.status_code == 400
    store = app.extensions["research_store"]
    assert count(store, ResearchJob) == count(store, ResearchSetupVersion) == 0


def test_owner_sources_are_authoritative_and_foreign_sources_are_rejected(app, client):
    draft = draft_for(client)
    source_id = draft["portfolio"]["strategies"][0]["source_id"]
    draft["sources"] = {
        source_id: {
            "id": "forged",
            "receipt": {"signal_count": 99999},
            "filename": "Chosen label.csv",
        }
    }
    experiment = create(client, draft)
    stored = experiment["draft"]["sources"][source_id]
    assert stored["id"] == source_id
    assert stored["receipt"]["signal_count"] == 1
    assert stored["filename"] == "Chosen label.csv"
    with client.session_transaction() as session:
        session["user"] = "other-account"
    assert client.post(f"{BASE}/experiments", json={"draft": draft}).status_code == 404
    for url in (f"/experiments/{experiment['id']}", "/studies", "/versions"):
        response = client.get(BASE + url)
        if "/experiments/" in url:
            assert response.status_code == 404
        else:
            assert response.json["items"] == []
    assert client.get(f"{BASE}/experiments").json["items"] == []
    assert (
        client.put(
            f"{BASE}/experiments/{experiment['id']}/draft", json={"revision": 1, "draft": draft}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"{BASE}/experiments/{experiment['id']}", json={"revision": 1}).status_code
        == 404
    )
    assert app.test_client().get(f"{BASE}/experiments").status_code == 401


def test_metadata_and_revision_conflict_do_not_lose_saved_work(client):
    experiment = create(client)
    first = client.patch(
        f"{BASE}/experiments/{experiment['id']}",
        json={
            "revision": 1,
            "name": "Idea changed",
            "notes": "Review later",
            "tags": ["daily", "daily"],
            "pinned": True,
        },
    )
    assert first.status_code == 200
    assert first.json["tags"] == ["daily"] and first.json["pinned"] is True
    stale = client.patch(
        f"{BASE}/experiments/{experiment['id']}", json={"revision": 1, "name": "stale overwrite"}
    )
    assert stale.status_code == 409
    assert stale.json["code"] == "revision_conflict" and stale.json["revision"] == 2
    assert client.get(f"{BASE}/experiments/{experiment['id']}").json["name"] == "Idea changed"


def test_concurrent_revision_writers_have_one_winner(app, client):
    experiment = create(client)
    store, gate = app.extensions["research_store"], Barrier(2)

    def save(name):
        gate.wait()
        try:
            return library.update_experiment(
                store, OWNER, experiment["id"], {"revision": 1, "name": name}
            )["name"]
        except library.RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(save, ("First", "Second")))
    assert result.count("conflict") == 1
    assert library.get_experiment(store, OWNER, experiment["id"])["revision"] == 2


def test_bounded_pagination_search_archive_and_draft_deletion(app, client):
    first = create(client, name="literal%match")
    second = create(client, name="literalXmatch")
    assert len(client.get(f"{BASE}/experiments?search=%25").json["items"]) == 1
    page = client.get(f"{BASE}/experiments?limit=1").json
    assert len(page["items"]) == 1 and page["next_offset"] == 1
    assert len(client.get(f"{BASE}/experiments?limit=1&offset=1").json["items"]) == 1
    archived = client.patch(
        f"{BASE}/experiments/{first['id']}", json={"revision": 1, "archived": True}
    ).json
    assert client.get(f"{BASE}/experiments").json["items"][0]["id"] == second["id"]
    assert client.get(f"{BASE}/experiments?archived=true").json["items"][0]["id"] == first["id"]
    assert (
        client.put(
            f"{BASE}/experiments/{first['id']}/draft",
            json={"revision": archived["revision"], "draft": archived["draft"]},
        ).status_code
        == 400
    )
    restored = client.patch(
        f"{BASE}/experiments/{first['id']}",
        json={"revision": archived["revision"], "archived": False},
    ).json
    assert client.delete(
        f"{BASE}/experiments/{first['id']}", json={"revision": restored["revision"]}
    ).json["deleted"]
    for query in ("limit=51", "offset=-1", "archived=maybe", "limit=word"):
        assert client.get(f"{BASE}/experiments?{query}").status_code == 400


def test_library_search_finds_research_notes_without_crossing_accounts(client):
    experiment = create(client, name="Breakout research")
    response = client.patch(
        f"{BASE}/experiments/{experiment['id']}",
        json={"revision": 1, "notes": "Review earnings gaps with 10% stops"},
    )
    assert response.status_code == 200
    matches = client.get(f"{BASE}/experiments?search=earnings").json["items"]
    assert [row["id"] for row in matches] == [experiment["id"]]
    assert len(client.get(f"{BASE}/experiments?search=%25").json["items"]) == 1
    with client.session_transaction() as session:
        session["user"] = "another-account"
    assert client.get(f"{BASE}/experiments?search=earnings").json["items"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(extra="unknown"),
        lambda d: d["portfolio"].update(strategies=[{}] * 9),
        lambda d: d["portfolio"].update(capital=float("nan")),
        lambda d: d["portfolio"].update(name="x" * 121),
        lambda d: d["optimization"].update(extra="x" * (256 * 1024)),
    ],
)
def test_structural_and_size_limits_reject_before_save(client, mutation):
    draft = library.fresh_draft()
    mutation(draft)
    assert client.post(f"{BASE}/experiments", json={"draft": draft}).status_code == 400
    assert client.get(f"{BASE}/experiments").json["items"] == []


def test_launch_is_atomic_idempotent_and_uses_existing_worker_contract(app, client):
    experiment = create(client, draft_for(client))
    result = run(client, experiment)
    repeated = run(client, experiment)
    assert repeated["job"]["id"] == result["job"]["id"]
    assert repeated["version"]["id"] == result["version"]["id"]
    assert result["experiment"]["revision"] == 2
    assert result["experiment"]["jobs"][0]["version_id"] == result["version"]["id"]
    store = app.extensions["research_store"]
    assert (
        count(store, ResearchJob)
        == count(store, ResearchSetupVersion)
        == count(store, ResearchLibraryRequest)
        == 1
    )
    job = service.get_job(store, OWNER, result["job"]["id"])
    # The original submit API accepts the same identity, confirming admission parity.
    with store.sessions() as db:
        request = db.scalars(select(ResearchRequest)).one()
        execution = db.get(ResearchExperiment, job.id)
    duplicate = service.submit(
        store,
        OWNER,
        job.source_id,
        json.loads(job.config),
        request_id=request.token,
        kind=execution.kind,
        specification=json.loads(execution.specification),
    )
    assert duplicate["id"] == job.id
    changed = client.post(
        f"{BASE}/experiments/{experiment['id']}/run",
        json={"revision": 2, "request_id": "library-run-first"},
    )
    assert changed.status_code == 400
    stale = client.post(
        f"{BASE}/experiments/{experiment['id']}/run",
        json={"revision": 1, "request_id": "different-new-run"},
    )
    assert stale.status_code == 409
    draft = result["experiment"]["draft"]
    draft["portfolio"]["capital"] = 75000
    saved = client.put(
        f"{BASE}/experiments/{experiment['id']}/draft", json={"revision": 2, "draft": draft}
    ).json
    frozen = client.get(
        f"{BASE}/experiments/{experiment['id']}/versions/{result['version']['id']}"
    ).json
    assert frozen["portfolio"]["capital"] == 20000
    assert saved["draft"]["portfolio"]["capital"] == 75000
    assert (
        client.delete(f"{BASE}/experiments/{experiment['id']}", json={"revision": 3}).status_code
        == 400
    )


def test_launch_failure_rolls_back_source_job_version_and_links(app, client):
    experiment = create(client, draft_for(client))
    store = app.extensions["research_store"]
    models = (
        ResearchJob,
        ResearchExperiment,
        ResearchSource,
        ResearchSetupVersion,
        ResearchLibraryJob,
        ResearchLibraryRequest,
        ResearchLibrarySource,
    )
    before = [count(store, model) for model in models]

    def reject_publication(session, *_):
        if any(isinstance(item, ResearchLibraryJob) for item in session.new):
            raise RuntimeError("injected before commit")

    event.listen(store.sessions.class_, "before_flush", reject_publication)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            run(client, experiment)
    finally:
        event.remove(store.sessions.class_, "before_flush", reject_publication)
    assert [count(store, model) for model in models] == before
    assert library.get_experiment(store, OWNER, experiment["id"])["revision"] == 1
    assert run(client, experiment)["job"]["status"] == "queued"


def test_concurrent_same_launch_key_creates_one_version_and_job(app, client):
    experiment = create(client, draft_for(client))
    store, gate = app.extensions["research_store"], Barrier(2)

    def launch(_):
        gate.wait()
        return library.run_experiment(
            store,
            OWNER,
            experiment["id"],
            {"revision": 1, "request_id": "concurrent-library-launch"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, range(2)))
    assert results[0]["job"]["id"] == results[1]["job"]["id"]
    assert count(store, ResearchJob) == count(store, ResearchSetupVersion) == 1


def test_queue_full_creates_no_orphan_version_or_source(app, client):
    for index in range(4):
        run(client, create(client, draft_for(client)), f"queue-library-{index}")
    experiment = create(client, draft_for(client), name="Cannot start yet")
    store = app.extensions["research_store"]
    before = count(store, ResearchSource)
    response = client.post(
        f"{BASE}/experiments/{experiment['id']}/run",
        json={"revision": 1, "request_id": "queue-fifth-library"},
    )
    assert response.status_code == 400 and "queue is full" in response.json["message"]
    assert count(store, ResearchJob) == count(store, ResearchSetupVersion) == 4
    assert count(store, ResearchSource) == before
    assert library.get_experiment(store, OWNER, experiment["id"])["version_count"] == 0


def test_saved_version_restore_preserves_displaced_draft_and_references(app, client):
    experiment = create(client, draft_for(client))
    path = f"{BASE}/experiments/{experiment['id']}"
    original = client.post(path + "/versions", json={"revision": 1, "name": "Original"}).json
    draft = original["experiment"]["draft"]
    draft["portfolio"]["capital"] = 54321
    edited = client.put(path + "/draft", json={"revision": 2, "draft": draft}).json
    restored = client.post(
        path + f"/versions/{original['version']['id']}/restore",
        json={"revision": edited["revision"]},
    )
    assert restored.status_code == 200
    assert restored.json["draft"]["portfolio"]["capital"] == 20000
    assert restored.json["parent_version_id"] == original["version"]["id"]
    assert restored.json["versions"][0]["draft"]["portfolio"]["capital"] == 54321
    assert restored.json["version_count"] == 2
    assert client.delete(path, json={"revision": restored.json["revision"]}).status_code == 400
    listed = client.get(f"{BASE}/versions?limit=1").json
    assert listed["next_offset"] == 1 and "draft" not in listed["items"][0]
    assert listed["items"][0]["experiment_name"] == experiment["name"]
    assert client.get(path + f"/versions/{original['version']['id']}").json["name"] == "Original"


@pytest.mark.timeout(120)
def test_real_search_to_selected_draft_keeps_parent_and_original_evidence(
    app, client, monkeypatch, tmp_path, tmp_path_factory
):
    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("Stored coverage should not contact broker"),
    )
    experiment = create(client, draft_for(client, optimize=True))
    launched = run(client, experiment)
    store = app.extensions["research_store"]
    worker.acquire(store, "library-real-search")
    try:
        assert worker.run_one(store, "library-real-search")
    finally:
        worker.release(store, "library-real-search")
    job_id = launched["job"]["id"]
    job = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert job["status"] == "completed", job
    export_url = f"/scanner-research/api/jobs/{job_id}/export"
    original_export = hashlib.sha256(client.get(export_url).data).hexdigest()
    trial = job["result"]["experiment"]["rows"][-1]
    changed = copy.deepcopy(launched["experiment"]["draft"])
    changed["portfolio"]["capital"] = 45678
    edited = client.put(
        f"{BASE}/experiments/{experiment['id']}/draft", json={"revision": 2, "draft": changed}
    ).json
    request = {
        "job_id": job_id,
        "trial_id": trial["config_id"],
        "mode": "backtest",
        "experiment_id": experiment["id"],
        "revision": edited["revision"],
        "request_id": "copy-selected-library-trial",
    }
    response = client.post(f"{BASE}/experiments/from-job", json=request)
    assert response.status_code == 200, response.json
    copied = response.json
    assert copied["parent_job_id"] == job_id and copied["parent_trial_id"] == trial["config_id"]
    assert copied["draft"]["optimizing"] is False
    assert copied["versions"][0]["draft"]["portfolio"]["capital"] == 45678
    by_id = {row["id"]: row for row in trial["strategies"]}
    for row in copied["draft"]["portfolio"]["strategies"]:
        assert row["config"] == by_id[row["id"]]["config"]
        assert row["source_id"] in copied["draft"]["sources"]
    assert (
        client.post(f"{BASE}/experiments/from-job", json=request).json["revision"]
        == copied["revision"]
    )
    assert hashlib.sha256(client.get(export_url).data).hexdigest() == original_export
    studies = client.get(f"{BASE}/studies").json["items"]
    assert studies[0]["id"] == job_id and studies[0]["experiment_id"] == experiment["id"]
    assert "result" not in studies[0]
    candidate_version = client.post(
        f"{BASE}/experiments/{experiment['id']}/versions",
        json={"revision": copied["revision"], "name": "Selected candidate"},
    ).json
    assert candidate_version["version"]["parent_trial_id"] == trial["config_id"]
    original_version_id = launched["version"]["id"]
    restored = client.post(
        f"{BASE}/experiments/{experiment['id']}/versions/{original_version_id}/restore",
        json={"revision": candidate_version["experiment"]["revision"]},
    ).json
    assert restored["parent_job_id"] is None and restored["parent_trial_id"] is None
    assert restored["versions"][0]["parent_job_id"] == job_id
    before_replay = copy.deepcopy(restored["draft"])
    replay_request = {
        "revision": restored["revision"],
        "job_id": job_id,
        "trial_id": trial["config_id"],
        "request_id": "library-exact-replay",
    }
    replay_url = f"{BASE}/experiments/{experiment['id']}/replay"
    replay = client.post(replay_url, json=replay_request)
    assert replay.status_code == 202, replay.json
    assert replay.json["experiment"]["draft"] == before_replay
    assert replay.json["experiment"]["parent_job_id"] is None
    assert replay.json["version"]["parent_trial_id"] == trial["config_id"]
    assert replay.json["experiment"]["jobs"][0]["role"] == "replay"
    assert (
        client.post(replay_url, json=replay_request).json["job"]["id"] == replay.json["job"]["id"]
    )
    assert (
        client.patch(
            f"{BASE}/experiments/{experiment['id']}",
            json={"revision": replay.json["experiment"]["revision"], "archived": True},
        ).status_code
        == 400
    )
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda *a, **k: pytest.fail("Exact replay must not prepare prices"),
    )
    worker.acquire(store, "library-exact-replay")
    try:
        assert worker.run_one(store, "library-exact-replay")
    finally:
        worker.release(store, "library-exact-replay")
    replay_result = client.get(f"/scanner-research/api/jobs/{replay.json['job']['id']}").json
    assert replay_result["status"] == "completed", replay_result
    assert replay_result["result"]["summary"] == trial["summary"]
    assert hashlib.sha256(client.get(export_url).data).hexdigest() == original_export
    from database.research_db import ResearchStore
    from services.research_storage import backup_store, prune_orphans, restore_store

    orphan = service.save_artifact(store, {"unused_library_test_artifact": True})
    orphan_path = store.root / "artifacts" / f"{orphan}.json.gz"
    os.utime(orphan_path, (time.time() - 7200, time.time() - 7200))
    assert prune_orphans(store, apply=True)["deleted_files"] >= 1
    assert not orphan_path.exists()
    before_backup = library.get_experiment(store, OWNER, experiment["id"])
    backup_root = tmp_path_factory.mktemp("library-parent-backup")
    backup_store(store, backup_root / "backup")
    restore_store(backup_root / "backup", backup_root / "restored")
    restored_store = ResearchStore(backup_root / "restored")
    restored_store.initialize()
    try:
        after_backup = library.get_experiment(restored_store, OWNER, experiment["id"])
        assert after_backup == before_backup
        original_job = service.get_job(restored_store, OWNER, job_id)
        assert (
            service.read_artifact(restored_store, original_job.result_artifact)["result"]
            == job["result"]
        )
    finally:
        restored_store.close()
    with client.session_transaction() as session:
        session["user"] = "other-account"
    assert (
        client.post(
            f"{BASE}/experiments/from-job",
            json={**request, "experiment_id": None, "request_id": "foreign-trial-copy"},
        ).status_code
        == 404
    )


def test_additive_populated_migration_preserves_legacy_report(app, client):
    from test_jobs import source, submit

    job_id = submit(client, source(client))
    store = app.extensions["research_store"]
    worker.acquire(store, "library-old-report")
    try:
        assert worker.run_one(store, "library-old-report")
    finally:
        worker.release(store, "library-old-report")
    url = f"/scanner-research/api/jobs/{job_id}/export"
    before = client.get(url).data
    with store.engine.begin() as db:
        for table in reversed(Base.metadata.sorted_tables):
            if (
                table.name.startswith("research_library_")
                or table.name == "research_setup_versions"
            ):
                table.drop(db)
    store.initialize()
    store.initialize()
    assert client.get(url).data == before
    assert create(client)["revision"] == 1


def test_draft_delete_retains_reusable_source_and_preview_cannot_run(app, client):
    experiment = create(client, draft_for(client))
    store = app.extensions["research_store"]
    before = count(store, ResearchSource)
    app.config["RESEARCH_PREVIEW"] = True
    assert (
        client.post(
            f"{BASE}/experiments/{experiment['id']}/run",
            json={"revision": 1, "request_id": "preview-must-not-run"},
        ).status_code
        == 400
    )
    assert (
        client.delete(f"{BASE}/experiments/{experiment['id']}", json={"revision": 1}).status_code
        == 200
    )
    assert count(store, ResearchSource) == before
    assert count(store, ResearchLibrarySource) == 0


def test_repeated_library_success_and_error_paths_release_database_handles(app, client):
    import psutil

    experiment = create(client)
    store = app.extensions["research_store"]
    assert isinstance(store.engine.pool, NullPool)
    process = psutil.Process()
    measure = process.num_handles if sys.platform == "win32" else process.num_fds
    for _ in range(5):
        library.get_experiment(store, OWNER, experiment["id"])
    before = measure()
    for _ in range(100):
        library.get_experiment(store, OWNER, experiment["id"])
        library.list_experiments(store, OWNER)
        with pytest.raises(LookupError):
            library.get_experiment(store, "other-account", experiment["id"])
        with pytest.raises(library.RevisionConflict):
            library.update_experiment(
                store, OWNER, experiment["id"], {"revision": 999, "name": "stale"}
            )
    assert measure() <= before + 3


def test_library_backup_restore_retains_draft_versions_and_raw_sources(
    app, client, tmp_path_factory
):
    from database.research_db import ResearchStore
    from services.research_storage import backup_store, restore_store

    experiment = create(client, draft_for(client))
    saved = client.post(
        f"{BASE}/experiments/{experiment['id']}/versions",
        json={"revision": 1, "name": "Reusable setup"},
    ).json
    store = app.extensions["research_store"]
    root = tmp_path_factory.mktemp("library-backup")
    backup_store(store, root / "backup")
    restore_store(root / "backup", root / "restored")
    restored = ResearchStore(root / "restored")
    restored.initialize()
    try:
        reopened = library.get_experiment(restored, OWNER, experiment["id"])
        assert reopened["draft"] == saved["experiment"]["draft"]
        assert reopened["versions"] == saved["experiment"]["versions"]
        source_id = reopened["draft"]["portfolio"]["strategies"][0]["source_id"]
        assert service.source_for(restored, OWNER, source_id)["original_csv_base64"]
    finally:
        restored.close()


def test_library_json_body_is_bounded_without_content_length(client):
    response = client.post(
        f"{BASE}/experiments",
        data=b'{"name":"' + b"x" * (library.MAX_DRAFT_BYTES + 65536) + b'"}',
        content_type="application/json",
        environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True},
    )
    assert response.status_code == 400 and "size limit" in response.json["message"]
    assert (
        client.post(
            f"{BASE}/experiments", data=b'{"name":', content_type="application/json"
        ).status_code
        == 400
    )
    assert client.post(f"{BASE}/experiments", data="not json").status_code == 400
    assert client.get(f"{BASE}/experiments").json["items"] == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE research_library_sources SET source_id='absent'",
        "UPDATE research_setup_versions SET experiment_id='absent'",
        "UPDATE research_library_jobs SET job_id='absent'",
        "UPDATE research_library_requests SET owner='foreign'",
        "UPDATE research_library_requests SET job_id='absent'",
        "UPDATE research_setup_versions SET parent_version_id='absent'",
        "UPDATE research_library_experiments SET parent_job_id='absent', parent_result_artifact='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
        "DELETE FROM research_library_jobs",
        "DROP TABLE research_library_sources",
    ],
)
def test_library_maintenance_rejects_dangling_or_foreign_references(
    app, client, statement, tmp_path_factory
):
    from services.research_storage import backup_store

    experiment = create(client, draft_for(client))
    launched = run(client, experiment)
    store = app.extensions["research_store"]
    service.cancel(store, OWNER, launched["job"]["id"])
    with store.engine.begin() as db:
        db.exec_driver_sql(statement)
    root = tmp_path_factory.mktemp("invalid-library-backup")
    with pytest.raises(ValueError, match="library"):
        backup_store(store, root / "backup")
    assert not (root / "backup").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"job_id": []},
        {"job_id": "a" * 32, "trial_id": {}},
        {"job_id": "a" * 32, "experiment_id": []},
    ],
)
def test_library_copy_rejects_malformed_identity_shapes(client, changes):
    response = client.post(
        f"{BASE}/experiments/from-job",
        json={"mode": "backtest", "request_id": "malformed-copy-id", **changes},
    )
    assert response.status_code == 400


def test_restore_preserves_unfinished_settings_before_any_csv_is_added(client):
    experiment = create(client)
    path = f"{BASE}/experiments/{experiment['id']}"
    original = client.post(path + "/versions", json={"revision": 1}).json
    draft = original["experiment"]["draft"]
    draft["portfolio"].update(name="Unfinished research", capital=34567)
    changed = client.put(path + "/draft", json={"revision": 2, "draft": draft}).json
    restored = client.post(
        path + f"/versions/{original['version']['id']}/restore",
        json={"revision": changed["revision"]},
    ).json
    assert restored["versions"][0]["draft"]["portfolio"]["capital"] == 34567
    assert restored["versions"][0]["draft"]["portfolio"]["name"] == "Unfinished research"
    assert restored["draft"] == library.fresh_draft()
