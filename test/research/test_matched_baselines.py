"""Matching preserves original rules while proving the study's actual eligible cohort."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, func, select
from test_jobs import app, client
from test_library import OWNER, create, run
from test_portfolio_validation import inputs
from test_report_period_workflow import draft, run_worker
from test_shortlist import change_result
from test_vectorbt_portfolio import snapshot as minute_snapshot

from database.research_db import (
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchStore,
)
from research.evaluation_basis import comparison_status
from research.portfolio_coverage import prepare as prepare_coverage
from research.report_contract import settings_identity
from services import research_baselines as baselines
from services import research_comparisons as comparisons
from services import research_decisions as decisions
from services import research_library as library
from services import research_shortlist as shortlist
from services import scanner_research_service as service
from services.research_storage import backup_store, restore_store


def setup(app, client, monkeypatch, *, reserved=True, minute=False):
    store = app.extensions["research_store"]

    def snapshot():
        saved = copy.deepcopy(inputs()["snapshot"])
        if minute:
            saved = minute_snapshot(symbols=("AAA",), minute=True, days=saved["sessions"])
            saved["bars"]["AAA"].pop("2026-01-10T09:18:00+05:30")
        elif not reserved:
            saved["bars"]["AAA"].pop("2026-01-10")
        return saved

    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda _store, _owner, evidence, **_: prepare_coverage(
            {**evidence, "snapshot": snapshot(), "frozen_prices": True}
        ),
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a, **k: pytest.fail("Matched baseline requested a broker"),
    )
    value = draft(client, False)
    value["portfolio"]["strategies"][0].update(search={})
    value["portfolio"]["strategies"][0]["config"]["hold_sessions"] = 1
    if minute:
        value["portfolio"]["strategies"][0]["config"].update(
            trade_horizon="intraday", hold_minutes=1
        )
    if not reserved:
        value["portfolio"].pop("validation")
    experiment = create(client, value)
    original = run(client, experiment)["job"]["id"]
    run_worker(store)
    value["optimizing"] = True
    value["optimization"].update(sampler="grid", trials=3)
    value["portfolio"]["strategies"][0].update(
        search={"hold_minutes" if minute else "hold_sessions": {"min": 1, "max": 3, "step": 1}}
    )
    current = library.get_experiment(store, OWNER, experiment["id"])
    library.save_draft(
        store, OWNER, experiment["id"], {"revision": current["revision"], "draft": value}
    )
    current = library.get_experiment(store, OWNER, experiment["id"])
    study = library.run_experiment(
        store,
        OWNER,
        experiment["id"],
        {"revision": current["revision"], "request_id": "study-for-matching"},
    )["job"]["id"]
    run_worker(store)
    for target in (
        "services.research_portfolio._prepare_prices",
        "research.connectors.optuna_portfolio.run_search",
    ):
        monkeypatch.setattr(
            target, lambda *a, **k: pytest.fail("Matching acquired data or optimized")
        )
    for identifier in (original, study):
        job = service.get_job(store, OWNER, identifier)
        assert job.status == "completed", job.error
    return store, experiment["id"], study, original


def request(store, experiment, study, baseline, token="match-baseline-once"):
    view = baselines.context(store, OWNER, experiment, study, baseline)
    return {
        "revision": view["revision"],
        "request_id": token,
        "study_result_artifact": view["study"]["result_artifact"],
        "baseline_result_artifact": view["baseline"]["result_artifact"],
    }


def result(store, job_id):
    return service.read_artifact(store, service.get_job(store, OWNER, job_id).result_artifact)[
        "result"
    ]


def count(store):
    with store.sessions() as db:
        return db.scalar(select(func.count()).select_from(ResearchJob))


@pytest.mark.parametrize("reserved", [False, True])
def test_recalculates_baseline_rules_on_frozen_study_cohort_then_comparison_and_decision(
    app, client, monkeypatch, reserved
):
    store, experiment, study_id, baseline_id = setup(app, client, monkeypatch, reserved=reserved)
    period = "selection" if reserved else "full"
    baseline, study = result(store, baseline_id), result(store, study_id)
    assert comparison_status(baseline["evaluation_basis"], study["evaluation_basis"])[
        "differences"
    ] == ["cohort"]
    before = count(store)
    view = baselines.context(store, OWNER, experiment, study_id, baseline_id)
    assert view["action"]["kind"] == "prepare", view
    assert view["differences"] == ["cohort"] and view["period"] == period
    assert view["recipe"]["config_id"] == settings_identity(baseline["strategies"])
    assert count(store) == before
    data = request(store, experiment, study_id, baseline_id)
    accepted = baselines.prepare(store, OWNER, experiment, study_id, baseline_id, data)
    assert not accepted["reused"] and accepted["job"]["status"] == "queued"
    same = baselines.prepare(store, OWNER, experiment, study_id, baseline_id, data)
    assert same["reused"] and same["job"]["id"] == accepted["job"]["id"]
    run_worker(store)
    matched_id = accepted["job"]["id"]
    job = service.get_job(store, OWNER, matched_id)
    assert job.status == "completed", job.error
    matched = result(store, matched_id)
    assert matched["strategies"] == baseline["strategies"]
    assert comparison_status(matched["evaluation_basis"], study["evaluation_basis"])["compatible"]
    assert "replay_origin" not in matched and "reserved_evaluation" not in matched
    assert matched["matched_baseline_origin"]["baseline_job_id"] == baseline_id
    assert matched["matched_baseline_origin"]["study_job_id"] == study_id
    assert result(store, baseline_id) == baseline and result(store, study_id) == study
    first = shortlist.save_candidate(store, OWNER, experiment, {"job_id": matched_id})["candidate"]
    second = shortlist.save_candidate(
        store,
        OWNER,
        experiment,
        {"job_id": study_id, "config_id": study["experiment"]["recommendation_id"]},
    )["candidate"]
    assert first["origin_kind"] == "backtest" and first["period"] == period
    assert first["name"] == matched["portfolio"]["name"]
    assert "Matched baseline" in first["name"]
    compared = comparisons.create_comparison(
        store,
        OWNER,
        experiment,
        {
            "request_id": "matched-comparison",
            "candidate_ids": [first["id"], second["id"]],
            "reference_candidate_id": first["id"],
        },
    )["comparison"]
    assert compared["compatible"]
    assert decisions.direct_context(store, OWNER, experiment, matched_id)["target"] == {
        "job_id": matched_id,
        "config_id": first["config_id"],
    }
    view = baselines.context(store, OWNER, experiment, study_id, baseline_id)
    assert view["action"] == {"kind": "open", "job_id": matched_id}
    assert count(store) == 3
    shortlist.update_candidate(
        store,
        OWNER,
        experiment,
        first["id"],
        {"revision": first["revision"], "name": "My matched benchmark"},
    )
    repeated = shortlist.save_candidate(store, OWNER, experiment, {"job_id": matched_id})
    assert repeated["reused"] and repeated["candidate"]["name"] == "My matched benchmark"


@pytest.mark.parametrize(
    "boundary", ["owner", "link", "archive", "revision", "study_artifact", "baseline_artifact"]
)
def test_no_admission_for_foreign_stale_or_replaced_sources(app, client, monkeypatch, boundary):
    store, experiment, study, baseline = setup(app, client, monkeypatch)
    data = request(store, experiment, study, baseline)
    owner = OWNER
    if boundary == "owner":
        owner = "someone-else"
    elif boundary.endswith("artifact"):
        change_result(
            store,
            study if boundary == "study_artifact" else baseline,
            lambda report: report["summary"].update(net_return_pct=999),
        )
    else:
        with store.sessions.begin() as db:
            if boundary == "link":
                db.delete(db.get(ResearchLibraryJob, (experiment, baseline)))
            elif boundary == "archive":
                db.get(ResearchLibraryExperiment, experiment).archived = True
            else:
                db.get(ResearchLibraryExperiment, experiment).revision += 1
    with pytest.raises((ValueError, LookupError)):
        baselines.prepare(store, owner, experiment, study, baseline, data)
    assert count(store) == 2


@pytest.mark.parametrize(
    "field", ["capital", "costs", "execution", "entry", "sources", "unverified"]
)
def test_incompatible_baseline_is_explained_without_calculation(app, client, monkeypatch, field):
    store, experiment, study, baseline = setup(app, client, monkeypatch)

    def changed(report):
        if field == "entry":
            report["strategies"][0]["config"]["entry_time"] = "10:00"
        elif field == "sources":
            report["portfolio"]["strategies"][0]["source_id"] = "f" * 32
        elif field == "unverified":
            report.pop("evaluation_basis")
        else:
            report["evaluation_basis"]["comparison"][field] = "changed"

    change_result(store, baseline, changed)
    view = baselines.context(store, OWNER, experiment, study, baseline)
    assert view["action"]["kind"] == "unavailable"
    assert view["action"]["reason"]
    with pytest.raises(ValueError):
        baselines.prepare(
            store, OWNER, experiment, study, baseline, request(store, experiment, study, baseline)
        )
    assert count(store) == 2


def test_atomic_baseline_source_fence_after_inputs_reconstruction(app, client, monkeypatch):
    store, experiment, study, baseline = setup(app, client, monkeypatch)
    data = request(store, experiment, study, baseline)
    register = service.register_source

    def replaced(*args, **kwargs):
        receipt = register(*args, **kwargs)
        if kwargs.get("publish") is False:
            change_result(
                store, baseline, lambda report: report["summary"].update(net_return_pct=999)
            )
        return receipt

    monkeypatch.setattr(service, "register_source", replaced)
    with pytest.raises(ValueError, match="original saved result changed"):
        baselines.prepare(store, OWNER, experiment, study, baseline, data)
    assert count(store) == 2


def test_concurrent_distinct_requests_reuse_one_matched_baseline(app, client, monkeypatch):
    store, experiment, study, baseline = setup(app, client, monkeypatch)
    data = request(store, experiment, study, baseline)
    enqueue, barrier = library._enqueue, Barrier(2)

    def synchronized(*args, **kwargs):
        barrier.wait(timeout=15)
        return enqueue(*args, **kwargs)

    monkeypatch.setattr(library, "_enqueue", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                baselines.prepare,
                store,
                OWNER,
                experiment,
                study,
                baseline,
                {**data, "request_id": f"match-browser-{i}"},
            )
            for i in range(2)
        ]
        values = [future.result(timeout=30) for future in futures]
    assert len({value["job"]["id"] for value in values}) == 1
    assert sum(not value["reused"] for value in values) == 1 and count(store) == 3


def test_verification_rejects_engine_output_with_wrong_rules_or_basis(app, client, monkeypatch):
    store, experiment, study, baseline = setup(app, client, monkeypatch)
    _, evidence, *_ = baselines._inspect(store, OWNER, experiment, study, baseline)
    good = {
        "strategies": copy.deepcopy(evidence["strategies"]),
        "evaluation_basis": copy.deepcopy(evidence["matched_baseline_origin"]["evaluation_basis"]),
    }
    baselines.verify_reconstruction(evidence, good)
    wrong = copy.deepcopy(good)
    wrong["strategies"][0]["config"]["target_pct"] += 1
    with pytest.raises(ValueError, match="verification failed"):
        baselines.verify_reconstruction(evidence, wrong)
    wrong = copy.deepcopy(good)
    wrong["evaluation_basis"]["cohort_id"] = "f" * 64
    with pytest.raises(ValueError, match="verification failed"):
        baselines.verify_reconstruction(evidence, wrong)


def test_native_replay_backup_and_restore_keep_matched_baseline_identity(
    app, client, monkeypatch, tmp_path
):
    store, experiment, study, baseline = setup(app, client, monkeypatch)
    data = request(store, experiment, study, baseline)
    accepted = baselines.prepare(store, OWNER, experiment, study, baseline, data)
    run_worker(store)
    original = result(store, accepted["job"]["id"])
    current = library.get_experiment(store, OWNER, experiment)
    replay = library.replay_experiment(
        store,
        OWNER,
        experiment,
        {
            "job_id": accepted["job"]["id"],
            "revision": current["revision"],
            "request_id": "replay-matched-baseline",
        },
    )
    run_worker(store)
    job = service.get_job(store, OWNER, replay["job"]["id"])
    assert job.status == "completed", job.error
    assert result(store, job.id)["matched_baseline_origin"] == original["matched_baseline_origin"]
    assert comparison_status(
        result(store, job.id)["evaluation_basis"], original["evaluation_basis"]
    )["compatible"]
    backup, restored_path = (
        tmp_path.with_name(tmp_path.name + "-backup"),
        tmp_path.with_name(tmp_path.name + "-restored"),
    )
    backup_store(store, backup)
    restore_store(backup, restored_path)
    restored = ResearchStore(restored_path)
    restored.initialize()
    try:
        reused = baselines.prepare(restored, OWNER, experiment, study, baseline, data)
        assert reused["reused"] and reused["job"]["id"] == accepted["job"]["id"]
    finally:
        restored.close()


def test_context_releases_connections_on_success_and_failure(app, client, monkeypatch):
    store, experiment, study, baseline = setup(app, client, monkeypatch)
    state = {"open": 0}

    def opened(*_):
        state["open"] += 1

    def closed(*_):
        state["open"] -= 1

    event.listen(store.engine, "checkout", opened)
    event.listen(store.engine, "checkin", closed)
    try:
        for _ in range(10):
            assert (
                baselines.context(store, OWNER, experiment, study, baseline)["action"]["kind"]
                == "prepare"
            )
            with pytest.raises(LookupError):
                baselines.context(store, "foreign", experiment, study, baseline)
            assert state["open"] == 0
    finally:
        event.remove(store.engine, "checkout", opened)
        event.remove(store.engine, "checkin", closed)
    assert count(store) == 2


def test_minute_baseline_uses_exact_study_observations_and_preserves_intraday_rules(
    app, client, monkeypatch
):
    store, experiment, study, baseline = setup(
        app, client, monkeypatch, reserved=False, minute=True
    )
    expected = result(store, study)["evaluation_basis"]
    original = result(store, baseline)
    view = baselines.context(store, OWNER, experiment, study, baseline)
    assert view["action"]["kind"] == "prepare", view
    assert "T09:15" in view["dates"]["from"]
    accepted = baselines.prepare(
        store, OWNER, experiment, study, baseline, request(store, experiment, study, baseline)
    )
    run_worker(store)
    job = service.get_job(store, OWNER, accepted["job"]["id"])
    assert job.status == "completed", job.error
    matched = result(store, job.id)
    assert matched["strategies"] == original["strategies"]
    assert matched["evaluation_basis"]["period"]["interval"] == "1m"
    assert comparison_status(matched["evaluation_basis"], expected)["compatible"]


def test_actual_worker_rejects_an_unverified_matched_result_and_preserves_originals(
    app, client, monkeypatch
):
    from research.connectors import vectorbt_portfolio

    store, experiment, study, baseline = setup(app, client, monkeypatch)
    original = result(store, baseline)
    accepted = baselines.prepare(
        store, OWNER, experiment, study, baseline, request(store, experiment, study, baseline)
    )
    evaluate = vectorbt_portfolio.evaluate

    def changed(*args, **kwargs):
        output = evaluate(*args, **kwargs)
        output["strategies"][0]["config"]["target_pct"] += 1
        return output

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", changed)
    run_worker(store)
    job = service.get_job(store, OWNER, accepted["job"]["id"])
    assert job.status == "failed" and "verification failed" in job.error
    assert job.result_artifact is None
    assert result(store, baseline) == original
    assert baselines.context(store, OWNER, experiment, study, baseline)["action"] == {
        "kind": "progress",
        "job_id": job.id,
    }


def test_already_compatible_result_and_request_limits_do_not_create_redundant_work(
    app, client, monkeypatch
):
    store, experiment, study, baseline = setup(app, client, monkeypatch)
    data = request(store, experiment, study, baseline)
    accepted = baselines.prepare(store, OWNER, experiment, study, baseline, data)
    run_worker(store)
    matched = accepted["job"]["id"]
    view = baselines.context(store, OWNER, experiment, study, matched)
    assert view["action"]["kind"] == "unavailable"
    assert "already use the same" in view["action"]["reason"]
    with pytest.raises(ValueError, match="already used"):
        baselines.prepare(
            store, OWNER, experiment, study, matched, request(store, experiment, study, matched)
        )
    monkeypatch.setattr("services.research_validation.MAX_REQUESTS", 1)
    with pytest.raises(ValueError, match="request limit"):
        baselines.prepare(
            store, OWNER, experiment, study, baseline, {**data, "request_id": "limit-new-request"}
        )
    assert count(store) == 3
