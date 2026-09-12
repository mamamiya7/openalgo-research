"""A reserved evaluation follows the accepted candidate across native report journeys."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.pool import NullPool
from test_jobs import app, client
from test_library import OWNER, create, run
from test_portfolio_validation import inputs
from test_report_period_workflow import draft, run_worker
from test_shortlist import change_result

from database.research_db import (
    ResearchEvidenceOpen,
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchLibraryRequest,
    ResearchStore,
)
from research.portfolio_coverage import prepare as prepare_coverage
from services import research_candidates as candidates
from services import research_library as library
from services import research_validation as validation
from services import scanner_research_service as service
from services.research_storage import backup_store, restore_store


def setup(app, client, monkeypatch, *, reserved=True, legacy=False, optimize=True):
    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda _store, _owner, evidence, **_: prepare_coverage(
            {**evidence, "snapshot": copy.deepcopy(inputs()["snapshot"]), "frozen_prices": True}
        ),
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a, **k: pytest.fail("Validation requested a broker"),
    )
    value = draft(client, optimize)
    if optimize:
        value["optimization"].update(sampler="grid", trials=3)
    if not reserved:
        value["portfolio"].pop("validation")
    if legacy:
        value["portfolio"]["validation"]["mode"] = "evaluate"
    experiment = create(client, value)
    source_id = run(client, experiment)["job"]["id"]
    run_worker(store)
    source = service.get_job(store, OWNER, source_id)
    assert source.status == "completed", source.error
    result = service.read_artifact(store, source.result_artifact)["result"]
    row = next(
        (
            row
            for row in result.get("experiment", {}).get("rows", [])
            if row["config_id"] != result["experiment"]["recommendation_id"]
        ),
        None,
    )
    for target in (
        "services.research_portfolio._prepare_prices",
        "research.connectors.optuna_portfolio.run_search",
    ):
        monkeypatch.setattr(
            target, lambda *a, **k: pytest.fail("Validation acquired data or optimized")
        )
    return store, experiment["id"], source, row


def request(store, experiment, source, config=None, token="validate-once"):
    view = validation.context(store, OWNER, experiment, source.id, config)
    return {
        "revision": view["revision"],
        "request_id": token,
        "source_result_artifact": view["candidate"]["source_result_artifact"],
        **({"config_id": config} if config else {}),
    }


def count(store, model):
    with store.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_nonwinner_can_review_and_evaluate_before_its_full_report_exists(app, client, monkeypatch):
    store, experiment, source, row = setup(app, client, monkeypatch)
    original = service.read_artifact(store, source.result_artifact)
    view = validation.context(store, OWNER, experiment, source.id, row["config_id"])
    assert view["candidate"]["report_job_id"] is None
    assert not view["candidate"]["is_objective_winner"]
    assert view["selection"] == {"from": "2026-01-05", "to": "2026-01-11"}
    assert view["reservation"] == {"from": "2026-01-12", "to": "2026-01-15"}
    assert view["action"] == {"kind": "prepare"}
    assert view["evidence_use"]["opened_at"] is None
    assert count(store, ResearchEvidenceOpen) == 0
    data = request(store, experiment, source, row["config_id"])
    queued = validation.prepare(store, OWNER, experiment, source.id, data)
    assert queued["job"]["status"] == "queued" and not queued["reused"]
    same = validation.prepare(store, OWNER, experiment, source.id, data)
    assert same["reused"] and same["job"]["id"] == queued["job"]["id"]
    run_worker(store)
    later = validation.context(store, OWNER, experiment, source.id, row["config_id"])
    assert later["action"]["kind"] == "open"
    assert later["action"]["job_id"] == queued["job"]["id"]
    assert later["evaluations"][0]["dates"] == view["reservation"]
    assert later["evaluations"][0]["identity"] == {
        "result_artifact": service.get_job(store, OWNER, queued["job"]["id"]).result_artifact,
        "analysis_artifact": None,
        "config_id": row["config_id"],
    }
    assert later["evidence_use"]["overlap"] == "recorded"
    assert service.read_artifact(store, source.result_artifact) == original
    assert count(store, ResearchJob) == 2
    assert count(store, ResearchEvidenceOpen) == 0


def test_prepared_alternative_and_later_replay_return_to_original_candidate_not_winner(
    app, client, monkeypatch
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    child = candidates.prepare(store, OWNER, source.id, row["config_id"])["report_job_id"]
    run_worker(store)
    view = validation.context(store, OWNER, experiment, child)
    assert view["candidate"]["source_job_id"] == source.id
    assert view["candidate"]["report_job_id"] == child
    assert view["candidate"]["config_id"] == row["config_id"]
    data = request(store, experiment, source, row["config_id"])
    queued = validation.prepare(store, OWNER, experiment, child, data)
    run_worker(store)
    current = library.get_experiment(store, OWNER, experiment)
    replay = library.replay_experiment(
        store,
        OWNER,
        experiment,
        {
            "job_id": queued["job"]["id"],
            "revision": current["revision"],
            "request_id": "later-replay",
        },
    )
    run_worker(store)
    restored = validation.context(store, OWNER, experiment, replay["job"]["id"])
    assert restored["candidate"] == view["candidate"]
    assert restored["action"]["job_id"] == queued["job"]["id"]
    assert restored["candidate"]["report_job_id"] != source.id


@pytest.mark.parametrize("completed", [False, True])
def test_reuses_an_existing_native_evaluation_even_with_a_different_browser_request(
    app, client, monkeypatch, completed
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    current = library.get_experiment(store, OWNER, experiment)
    previous = library.replay_experiment(
        store,
        OWNER,
        experiment,
        {
            "job_id": source.id,
            "trial_id": row["config_id"],
            "period": "evaluation",
            "revision": current["revision"],
            "request_id": "old-native-evaluation",
        },
    )
    if completed:
        run_worker(store)
    before = count(store, ResearchJob)
    data = request(store, experiment, source, row["config_id"], "another-browser")
    result = validation.prepare(store, OWNER, experiment, source.id, data)
    assert result["reused"] and result["job"]["id"] == previous["job"]["id"]
    assert count(store, ResearchJob) == before


def test_repeated_proposal_retains_one_canonical_candidate_and_evaluation(app, client, monkeypatch):
    store, experiment, source, row = setup(app, client, monkeypatch)

    def repeat(result):
        study = result["experiment"]
        proposal = next(item for item in study["trials"] if item["config_id"] == row["config_id"])
        study["trials"].append({**proposal, "number": 99, "reused": True})

    change_result(store, source.id, repeat)
    source = service.get_job(store, OWNER, source.id)
    view = validation.context(store, OWNER, experiment, source.id, row["config_id"])
    assert view["candidate"]["trial_number"] == row["trial_number"]
    first = validation.prepare(
        store, OWNER, experiment, source.id, request(store, experiment, source, row["config_id"])
    )
    second = validation.prepare(
        store,
        OWNER,
        experiment,
        source.id,
        request(store, experiment, source, row["config_id"], "repeat-proposal"),
    )
    assert second["job"]["id"] == first["job"]["id"]


def test_concurrent_distinct_requests_queue_one_canonical_evaluation(app, client, monkeypatch):
    store, experiment, source, row = setup(app, client, monkeypatch)
    data = request(store, experiment, source, row["config_id"])
    barrier = Barrier(2)
    enqueue = library._enqueue

    def synchronized(*args, **kwargs):
        barrier.wait(timeout=15)
        return enqueue(*args, **kwargs)

    monkeypatch.setattr(library, "_enqueue", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda i: validation.prepare(
                    store, OWNER, experiment, source.id, {**data, "request_id": f"browser-{i}"}
                ),
                range(2),
            )
        )
    assert len({item["job"]["id"] for item in results}) == 1
    assert sum(not item["reused"] for item in results) == 1
    assert count(store, ResearchJob) == 2


@pytest.mark.parametrize("boundary", ["owner", "link", "artifact", "archive", "revision"])
def test_prepare_rejects_foreign_unlinked_replaced_archived_or_stale_source(
    app, client, monkeypatch, boundary
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    data = request(store, experiment, source, row["config_id"])
    before = count(store, ResearchJob)
    owner = OWNER
    if boundary == "owner":
        owner = "another-account"
    elif boundary == "artifact":
        change_result(store, source.id, lambda result: result["summary"].update(net_return_pct=999))
    else:
        with store.sessions.begin() as db:
            if boundary == "link":
                db.delete(db.get(ResearchLibraryJob, (experiment, source.id)))
            elif boundary == "archive":
                db.get(ResearchLibraryExperiment, experiment).archived = True
            else:
                db.get(ResearchLibraryExperiment, experiment).revision += 1
    with pytest.raises((ValueError, LookupError)):
        validation.prepare(store, owner, experiment, source.id, data)
    assert count(store, ResearchJob) == before


@pytest.mark.parametrize("replace", ["artifact", "version"])
def test_atomic_admission_rechecks_original_result_and_version_after_reconstruction(
    app, client, monkeypatch, replace
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    data = request(store, experiment, source, row["config_id"])
    register = service.register_source

    def changed(*args, **kwargs):
        receipt = register(*args, **kwargs)
        if kwargs.get("publish") is False:
            if replace == "artifact":
                change_result(
                    store, source.id, lambda result: result["summary"].update(net_return_pct=999)
                )
            else:
                with store.sessions.begin() as db:
                    db.get(ResearchLibraryJob, (experiment, source.id)).version_id = "f" * 32
        return receipt

    monkeypatch.setattr(service, "register_source", changed)
    with pytest.raises(ValueError, match="original saved result changed"):
        validation.prepare(store, OWNER, experiment, source.id, data)
    assert count(store, ResearchJob) == 1


def test_legacy_embedded_later_is_only_reused_for_its_actual_candidate(app, client, monkeypatch):
    store, experiment, source, row = setup(app, client, monkeypatch, legacy=True)
    winner = validation.context(store, OWNER, experiment, source.id)
    alternative = validation.context(store, OWNER, experiment, source.id, row["config_id"])
    assert winner["action"]["kind"] == "open"
    assert winner["action"]["view"] == "embedded_later"
    assert winner["evaluations"][0]["identity"] == {
        "result_artifact": source.result_artifact,
        "analysis_artifact": None,
        "config_id": winner["candidate"]["config_id"],
    }
    assert alternative["action"]["kind"] == "prepare"
    assert alternative["evaluations"] == []


@pytest.mark.parametrize("optimize", [False, True])
def test_no_reserved_period_does_not_invent_one_or_queue_work(app, client, monkeypatch, optimize):
    store, experiment, source, row = setup(
        app, client, monkeypatch, reserved=False, optimize=optimize
    )
    view = validation.context(store, OWNER, experiment, source.id)
    assert view["reservation"] is None and view["action"]["kind"] == "unavailable"
    with pytest.raises(ValueError, match="no reserved later period"):
        validation.prepare(store, OWNER, experiment, source.id, request(store, experiment, source))
    assert count(store, ResearchJob) == 1


def test_read_only_context_has_bounded_connection_lifetime_and_does_not_acknowledge_opening(
    app, client, monkeypatch
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    assert isinstance(store.engine.pool, NullPool)
    connections = {"open": 0, "high": 0}

    def opened(*_):
        connections["open"] += 1
        connections["high"] = max(connections["high"], connections["open"])

    def closed(*_):
        connections["open"] -= 1

    event.listen(store.engine, "checkout", opened)
    event.listen(store.engine, "checkin", closed)
    try:
        for _ in range(15):
            validation.context(store, OWNER, experiment, source.id, row["config_id"])
            with pytest.raises(LookupError):
                validation.context(store, "foreign", experiment, source.id)
            assert connections["open"] == 0
    finally:
        event.remove(store.engine, "checkout", opened)
        event.remove(store.engine, "checkin", closed)
    assert connections["high"] <= 2
    assert count(store, ResearchEvidenceOpen) == 0
    assert count(store, ResearchJob) == 1


def test_validation_request_receipts_survive_native_backup_restore(
    app, client, monkeypatch, tmp_path
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    data = request(store, experiment, source, row["config_id"])
    accepted = validation.prepare(store, OWNER, experiment, source.id, data)
    run_worker(store)
    backup = tmp_path.with_name(tmp_path.name + "-backup")
    restored_path = tmp_path.with_name(tmp_path.name + "-restored")
    backup_store(store, backup)
    restore_store(backup, restored_path)
    restored = ResearchStore(restored_path)
    restored.initialize()
    try:
        retry = validation.prepare(restored, OWNER, experiment, source.id, data)
        assert retry["reused"] and retry["job"]["id"] == accepted["job"]["id"]
        assert count(restored, ResearchJob) == 2
        assert count(restored, ResearchLibraryRequest) == count(store, ResearchLibraryRequest)
    finally:
        restored.close()


def test_reserved_baseline_evaluates_without_creating_an_optimization(app, client, monkeypatch):
    store, experiment, source, _ = setup(app, client, monkeypatch, optimize=False)
    accepted = validation.prepare(
        store, OWNER, experiment, source.id, request(store, experiment, source)
    )
    run_worker(store)
    later = validation.context(store, OWNER, experiment, accepted["job"]["id"])
    assert later["candidate"]["trial_number"] is None
    assert later["candidate"]["report_job_id"] == source.id
    assert later["candidate"]["source_job_id"] == source.id
    assert later["action"]["kind"] == "open"
    assert count(store, ResearchJob) == 2


@pytest.mark.parametrize("status", ["failed", "interrupted", "cancelled"])
def test_stopped_evaluation_is_reopened_without_automatic_resume(app, client, monkeypatch, status):
    store, experiment, source, row = setup(app, client, monkeypatch)
    data = request(store, experiment, source, row["config_id"])
    accepted = validation.prepare(store, OWNER, experiment, source.id, data)
    with store.sessions.begin() as db:
        job = db.get(ResearchJob, accepted["job"]["id"])
        job.status = status
        db.get(ResearchExperiment, job.id).checkpoint = source.result_artifact
        job.progress = 25
    view = validation.context(store, OWNER, experiment, source.id, row["config_id"])
    assert view["action"] == {"kind": "progress", "job_id": accepted["job"]["id"]}
    assert view["evaluations"][0]["resumable"]
    reused = validation.prepare(store, OWNER, experiment, source.id, data)
    assert reused["reused"] and reused["job"]["status"] == status
    assert reused["job"]["progress"] == 25 and count(store, ResearchJob) == 2


def test_selection_dates_use_verified_timeline_even_with_padded_coverage(app, client, monkeypatch):
    store, experiment, source, row = setup(app, client, monkeypatch)
    change_result(
        store,
        source.id,
        lambda result: result["coverage"].update(date_from="2025-12-01", date_to="2026-01-20"),
    )
    source = service.get_job(store, OWNER, source.id)
    original = service.read_artifact(store, source.result_artifact)["result"]
    dates = {key: original["evaluation_basis"]["period"][key] for key in ("from", "to")}
    assert validation.context(store, OWNER, experiment, source.id)["selection"] == dates
    assert (
        validation.context(store, OWNER, experiment, source.id, row["config_id"])["selection"]
        == dates
    )
    child = candidates.prepare(store, OWNER, source.id, row["config_id"])["report_job_id"]
    run_worker(store)
    assert validation.context(store, OWNER, experiment, child)["selection"] == dates


@pytest.mark.parametrize("replace", ["source", "child_link", "version"])
def test_later_lineage_rejects_changed_original_or_unlinked_replay(
    app, client, monkeypatch, replace
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    accepted = validation.prepare(
        store, OWNER, experiment, source.id, request(store, experiment, source, row["config_id"])
    )
    run_worker(store)
    if replace == "source":
        change_result(store, source.id, lambda result: result["summary"].update(net_return_pct=999))
    else:
        with store.sessions.begin() as db:
            link = db.get(ResearchLibraryJob, (experiment, accepted["job"]["id"]))
            if replace == "child_link":
                db.delete(link)
            else:
                link.version_id = db.get(ResearchLibraryJob, (experiment, source.id)).version_id
    with pytest.raises((ValueError, LookupError)):
        validation.context(store, OWNER, experiment, accepted["job"]["id"])


def test_invalid_saved_later_evidence_is_not_reopened_or_silently_replaced(
    app, client, monkeypatch
):
    store, experiment, source, row = setup(app, client, monkeypatch)
    data = request(store, experiment, source, row["config_id"])
    accepted = validation.prepare(store, OWNER, experiment, source.id, data)
    run_worker(store)
    change_result(
        store,
        accepted["job"]["id"],
        lambda result: result["replay_origin"].update(config_id="f" * 64),
    )
    view = validation.context(store, OWNER, experiment, source.id, row["config_id"])
    assert view["action"]["kind"] == "unavailable" and view["evaluations"] == []
    for token in (data["request_id"], "new-browser-request"):
        with pytest.raises(ValueError, match="could not be verified"):
            validation.prepare(store, OWNER, experiment, source.id, {**data, "request_id": token})
    assert count(store, ResearchJob) == 2


def test_request_identity_conflict_and_receipt_limit_do_not_admit_work(app, client, monkeypatch):
    store, experiment, source, row = setup(app, client, monkeypatch)
    data = request(store, experiment, source, row["config_id"])
    validation.prepare(store, OWNER, experiment, source.id, data)
    winner = request(store, experiment, source)
    with pytest.raises(ValueError, match="already used"):
        validation.prepare(store, OWNER, experiment, source.id, winner)
    monkeypatch.setattr(validation, "MAX_REQUESTS", count(store, ResearchLibraryRequest))
    with pytest.raises(ValueError, match="request limit"):
        validation.prepare(
            store, OWNER, experiment, source.id, {**winner, "request_id": "fresh-winner-request"}
        )
    assert count(store, ResearchJob) == 2
