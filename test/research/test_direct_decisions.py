"""Single-result decisions reuse canonical evidence without comparison scaffolding."""
# ruff: noqa: F811 -- shared isolated native fixtures

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, func, inspect, select
from test_candidate_reports import frozen_studies, seed
from test_comparisons import create as create_comparison
from test_comparisons import ready
from test_decisions import body, evaluation, native_setup
from test_jobs import app, client
from test_library import OWNER, create
from test_report_period_workflow import run_worker

from database.research_db import (
    ResearchComparison,
    ResearchDecision,
    ResearchDecisionEvent,
    ResearchDecisionTarget,
    ResearchEvidenceOpen,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchShortlistCandidate,
    ResearchStore,
)
from research.portfolio import execution_versions
from services import research_decisions as decisions
from services import scanner_research_service as service
from services.research_portfolio import run
from services.research_storage import backup_store, restore_store


def counts(store):
    with store.sessions() as db:
        return [
            db.scalar(select(func.count()).select_from(model))
            for model in (
                ResearchJob,
                ResearchComparison,
                ResearchShortlistCandidate,
                ResearchDecision,
                ResearchDecisionEvent,
                ResearchDecisionTarget,
                ResearchEvidenceOpen,
            )
        ]


def baseline(app, client, frozen_studies):
    store = app.extensions["research_store"]
    evidence = copy.deepcopy(frozen_studies[False][0])
    evidence["portfolio"].pop("optimization")
    for strategy in evidence["portfolio"]["strategies"]:
        strategy["search"] = {}
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
    submitted = service.submit(
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
            "result": result,
            "inputs_artifact": service.save_artifact(store, frozen),
        },
    )
    experiment = create(client)["id"]
    with store.sessions.begin() as db:
        job = db.get(ResearchJob, submitted["id"])
        job.status, job.result_artifact = "completed", artifact
        db.add(
            ResearchLibraryJob(
                experiment_id=experiment, job_id=job.id, role="run", created_at=time.time()
            )
        )
    return experiment, submitted["id"]


@pytest.mark.parametrize("state", ["keep", "reject", "revisit"])
def test_single_baseline_decision_is_real_and_pinned_without_comparison(
    app, client, frozen_studies, monkeypatch, state
):
    experiment, job = baseline(app, client, frozen_studies)
    store = app.extensions["research_store"]
    before = counts(store)
    export = client.get(f"/scanner-research/api/jobs/{job}/export").data
    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_candidates.prepare",
        "services.research_analysis.submit",
    ):
        monkeypatch.setattr(target, lambda *a, **k: pytest.fail("A decision started computation"))
    context = decisions.direct_context(store, OWNER, experiment, job)
    assert context["current"] is None and context["evidence_use"]["reservation"] is None
    assert counts(store) == before
    result = decisions.save_direct_decision(store, OWNER, experiment, job, body(state=state))
    saved = result["event"]
    assert saved["state"] == state and saved["comparison_id"] is None
    assert saved["target"] == context["target"]
    assert counts(store) == before[:3] + [1, 1, 1, 0]
    reopened = decisions.event_report(store, OWNER, experiment, saved["decision_id"], saved["id"])
    assert (
        reopened["available"]
        and reopened["result"]["report_context"]["config_id"] == saved["config_id"]
    )
    assert client.get(f"/scanner-research/api/jobs/{job}/export").data == export


def test_nonwinner_direct_and_comparison_decisions_share_head_and_exact_old_events(
    app, client, frozen_studies
):
    experiment, members, job, child = ready(app, client, frozen_studies)
    store = app.extensions["research_store"]
    before = counts(store)
    context = decisions.direct_context(store, OWNER, experiment, child)
    config = members[1]["config_id"]
    assert context["target"] == {"job_id": job, "config_id": config}
    direct = decisions.save_direct_decision(store, OWNER, experiment, child, body())
    comparison = create_comparison(client, experiment, members)
    old = decisions.save_decision(
        store,
        OWNER,
        experiment,
        comparison["id"],
        members[1]["id"],
        body(token="comparison-after-direct", revision=1, state="revisit"),
    )
    assert direct["decision"]["id"] == old["decision"]["id"]
    third = decisions.save_direct_decision(
        store,
        OWNER,
        experiment,
        job,
        body(token="direct-after-comparison", revision=2, state="reject"),
        config_id=config,
    )
    assert third["event"]["supersedes_event_id"] == old["event"]["id"]
    assert third["event"]["trial_number"] == members[1]["trial_number"]
    assert counts(store)[:3] == [before[0], before[1] + 1, before[2]]
    for entry in (direct, old, third):
        shown = decisions.event_report(
            store, OWNER, experiment, entry["decision"]["id"], entry["event"]["id"]
        )
        assert shown["available"] and shown["job"]["id"] == child
        assert shown["result"]["summary"] == members[1]["snapshot"]["summary"]


def test_missing_alternative_report_is_not_silently_prepared(app, client, frozen_studies):
    job, row, experiment = seed(app, client, frozen_studies)
    store = app.extensions["research_store"]
    before = counts(store)
    with pytest.raises(ValueError, match="Prepare this candidate"):
        decisions.save_direct_decision(
            store, OWNER, experiment, job, body(), config_id=row["config_id"]
        )
    assert counts(store) == before


def test_direct_retry_conflicts_concurrency_archive_and_atomic_failure(app, client, frozen_studies):
    experiment, job = baseline(app, client, frozen_studies)
    store = app.extensions["research_store"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: decisions.save_direct_decision(store, OWNER, experiment, job, body()),
                range(2),
            )
        )
    assert results[0]["event"]["id"] == results[1]["event"]["id"]
    with pytest.raises(decisions.DecisionRequestConflict):
        decisions.save_direct_decision(store, OWNER, experiment, job, body(state="reject"))
    with pytest.raises(decisions.DecisionConflict):
        decisions.save_direct_decision(store, OWNER, experiment, job, body(token="stale-decision"))
    before = counts(store)

    def reject_publish(session, *_):
        if any(isinstance(row, ResearchDecisionTarget) for row in session.new):
            raise RuntimeError("injected direct target publication failure")

    event.listen(store.sessions.class_, "before_flush", reject_publish)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            decisions.save_direct_decision(
                store, OWNER, experiment, job, body(token="failing-direct", revision=1)
            )
    finally:
        event.remove(store.sessions.class_, "before_flush", reject_publish)
    assert counts(store) == before
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = True
    assert decisions.save_direct_decision(store, OWNER, experiment, job, body())["reused"]
    with pytest.raises(ValueError, match="Restore"):
        decisions.save_direct_decision(
            store, OWNER, experiment, job, body(token="new-archived", revision=1)
        )
    with pytest.raises(LookupError):
        decisions.direct_context(store, "foreign-owner", experiment, job)


def test_direct_later_evidence_opening_and_populated_backup_restore(
    app, client, monkeypatch, tmp_path
):
    store = app.extensions["research_store"]
    experiment, _, members, job, child, _ = native_setup(app, client, monkeypatch)
    later = evaluation(app, client, experiment, job, members[1]["config_id"])
    context = decisions.direct_context(store, OWNER, experiment, child)
    item = next(item for item in context["evaluation"]["items"] if item["job_id"] == later)
    pin = decisions.preview_direct_evaluation(store, OWNER, experiment, child, item["id"])
    assert pin["available"] and pin["result"]["report_context"]["period"] == "evaluation"
    for suffix, evaluation_id in (("selection", None), ("later", item["id"])):
        target = {
            "kind": "direct_report",
            "job_id": later,
            **({"evaluation_id": evaluation_id} if evaluation_id else {}),
        }
        decisions.acknowledge_open(
            store, OWNER, experiment, {"request_id": "direct-open-" + suffix, "target": target}
        )
    use = decisions.direct_context(store, OWNER, experiment, child)["evidence_use"]
    assert use["opened_at"] is not None and use["later_opened_at"] is not None
    winner_use = decisions.direct_context(
        store, OWNER, experiment, job, config_id=members[0]["config_id"]
    )["evidence_use"]
    assert winner_use["later_opened_at"] is None
    saved = decisions.save_direct_decision(
        store, OWNER, experiment, later, body(evaluation_id=item["id"])
    )
    assert saved["event"]["evidence_use"]["later_used_for_decision"]
    root = tmp_path.with_name(tmp_path.name + "-direct-backup")
    backup_store(store, root)
    restored_path = tmp_path.with_name(tmp_path.name + "-restored-direct")
    restore_store(root, restored_path)
    restored = ResearchStore(restored_path)
    restored.initialize()
    try:
        result = decisions.event_report(
            restored, OWNER, experiment, saved["decision"]["id"], saved["event"]["id"]
        )
        assert result["available"] and result["job"]["id"] == child
        later_result = decisions.event_report(
            restored,
            OWNER,
            experiment,
            saved["decision"]["id"],
            saved["event"]["id"],
            evidence="evaluation",
        )
        assert later_result["available"] and later_result["job"]["id"] == later
        assert decisions.save_direct_decision(
            restored, OWNER, experiment, later, body(evaluation_id=item["id"])
        )["reused"]
    finally:
        restored.close()


def test_additive_target_table_preserves_populated_comparison_history(app, client, frozen_studies):
    experiment, members, _, _ = ready(app, client, frozen_studies)
    comparison = create_comparison(client, experiment, members)
    store = app.extensions["research_store"]
    original = decisions.save_decision(
        store, OWNER, experiment, comparison["id"], members[0]["id"], body()
    )
    original["event"]["evidence_use"].pop("later_opened_at", None)
    with store.sessions.begin() as db:
        db.get(ResearchDecisionEvent, original["event"]["id"]).evidence_use = service.encoded(
            original["event"]["evidence_use"]
        ).decode()
    with store.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE research_decision_targets")
    store.initialize()
    assert "research_decision_targets" in inspect(store.engine).get_table_names()
    assert (
        decisions.get_event(
            store, OWNER, experiment, original["decision"]["id"], original["event"]["id"]
        )["event"]
        == original["event"]
    )


def test_direct_history_retry_and_unavailable_report_release_resources(
    app, client, frozen_studies, monkeypatch
):
    import gc

    import psutil

    experiment, job = baseline(app, client, frozen_studies)
    store = app.extensions["research_store"]
    expected = {
        "result_artifact": service.get_job(store, OWNER, job).result_artifact,
        "analysis_artifact": None,
    }
    request = {**body(), "expected_report": expected}
    saved = decisions.save_direct_decision(store, OWNER, experiment, job, request)
    identifier, entry = saved["decision"]["id"], saved["event"]["id"]
    active = [0]

    def connected(*_):
        active[0] += 1

    def closed(*_):
        active[0] -= 1

    def unavailable(*_):
        raise OSError("injected artifact read failure")

    monkeypatch.setattr(service, "read_artifact", unavailable)
    process = psutil.Process()
    measure = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    decisions.event_report(store, OWNER, experiment, identifier, entry)
    gc.collect()
    start_handles, start_rss = measure(), process.memory_info().rss
    event.listen(store.engine, "connect", connected)
    event.listen(store.engine, "close", closed)
    try:
        for _ in range(100):
            assert decisions.list_decisions(store, OWNER, experiment)["total"] == 1
            assert decisions.decision_history(store, OWNER, experiment, identifier)["total"] == 1
            assert decisions.save_direct_decision(store, OWNER, experiment, job, request)["reused"]
            assert not decisions.event_report(store, OWNER, experiment, identifier, entry)[
                "available"
            ]
            with pytest.raises(OSError, match="injected"):
                decisions.direct_context(store, OWNER, experiment, job, expected_report=expected)
            assert active[0] == 0
    finally:
        event.remove(store.engine, "connect", connected)
        event.remove(store.engine, "close", closed)
    gc.collect()
    assert measure() <= start_handles + 3
    assert process.memory_info().rss <= start_rss + 16 * 1024 * 1024


@pytest.mark.parametrize("mutation", ["orphan", "selection", "target", "size"])
def test_backup_rejects_tampered_direct_selection(app, client, frozen_studies, tmp_path, mutation):
    experiment, job = baseline(app, client, frozen_studies)
    store = app.extensions["research_store"]
    saved = decisions.save_direct_decision(store, OWNER, experiment, job, body())
    with store.sessions.begin() as db:
        target = db.get(ResearchDecisionTarget, saved["event"]["id"])
        if mutation == "orphan":
            target.event_id = "0" * 32
        elif mutation == "selection":
            selection = json.loads(target.selection)
            selection["source_job_id"] = "0" * 32
            target.selection = service.encoded(selection).decode()
        elif mutation == "target":
            target.target = service.encoded({"job_id": job, "config_id": "0" * 64}).decode()
        else:
            target.selection = " " * (decisions.MAX_SNAPSHOT_BYTES + 1)
    with pytest.raises(ValueError):
        backup_store(store, tmp_path.with_name(tmp_path.name + "-bad-direct"))


def test_direct_later_target_changed_during_review_does_not_publish(app, client, monkeypatch):
    experiment, _, members, job, _, _ = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    later = evaluation(app, client, experiment, job, members[1]["config_id"])
    before = counts(store)
    original = decisions._evidence_use

    def changed_target(*args, **kwargs):
        result = original(*args, **kwargs)
        with store.sessions.begin() as db:
            db.get(ResearchJob, later).result_artifact = "0" * 64
        return result

    monkeypatch.setattr(decisions, "_evidence_use", changed_target)
    with pytest.raises(decisions.DecisionEvidenceChanged, match="opened result changed"):
        decisions.save_direct_decision(store, OWNER, experiment, later, body())
    assert counts(store) == before


@pytest.mark.parametrize("later_target", [False, True])
def test_displayed_analysis_is_fenced_and_accepted_retry_survives_upgrade_and_backup(
    app, client, frozen_studies, monkeypatch, tmp_path, later_target
):
    from services import research_analysis

    store = app.extensions["research_store"]
    if later_target:
        experiment, _, members, source, _, _ = native_setup(app, client, monkeypatch)
        job = evaluation(app, client, experiment, source, members[1]["config_id"])
    else:
        experiment, job = baseline(app, client, frozen_studies)
    parent = service.get_job(store, OWNER, job)
    expected = {"result_artifact": parent.result_artifact, "analysis_artifact": None}
    request = {**body(token="displayed-original"), "expected_report": expected}
    assert (
        decisions.direct_context(store, OWNER, experiment, job, expected_report=expected)["current"]
        is None
    )
    first = decisions.save_direct_decision(store, OWNER, experiment, job, request)
    before = decisions.event_report(
        store, OWNER, experiment, first["decision"]["id"], first["event"]["id"]
    )
    analysis = research_analysis.submit(store, OWNER, job, symbol="AAA")
    run_worker(store)
    analysis_job = service.get_job(store, OWNER, analysis["analysis_job_id"])
    assert analysis_job.status == "completed"
    current = {**expected, "analysis_artifact": analysis_job.result_artifact}
    unchanged = counts(store)
    with pytest.raises(decisions.DecisionEvidenceChanged, match="Reopen the report"):
        decisions.direct_context(
            store,
            OWNER,
            experiment,
            job,
            expected_report={**current, "result_artifact": "0" * 64},
        )
    with pytest.raises(decisions.DecisionEvidenceChanged, match="Reopen the report"):
        decisions.direct_context(store, OWNER, experiment, job, expected_report=expected)
    with pytest.raises(decisions.DecisionEvidenceChanged, match="Reopen the report"):
        decisions.save_direct_decision(
            store,
            OWNER,
            experiment,
            job,
            {**body(token="stale-displayed", revision=1), "expected_report": expected},
        )
    with pytest.raises(decisions.DecisionRequestConflict):
        decisions.save_direct_decision(
            store, OWNER, experiment, job, {**request, "expected_report": current}
        )
    assert counts(store) == unchanged
    assert (
        decisions.save_direct_decision(store, OWNER, experiment, job, request)["event"]
        == first["event"]
    )
    accepted = decisions.save_direct_decision(
        store,
        OWNER,
        experiment,
        job,
        {**body(token="displayed-upgraded", revision=1), "expected_report": current},
    )
    assert accepted["event"]["revision"] == 2
    with store.sessions.begin() as db:
        db.get(ResearchJob, analysis_job.id).result_artifact = None
    backup = tmp_path.with_name(tmp_path.name + "-display-fence-backup")
    backup_store(store, backup)
    restored_path = tmp_path.with_name(tmp_path.name + "-display-fence-restored")
    restore_store(backup, restored_path)
    restored = ResearchStore(restored_path)
    restored.initialize()
    try:
        assert (
            decisions.save_direct_decision(restored, OWNER, experiment, job, request)["event"]
            == first["event"]
        )
        assert (
            decisions.event_report(
                restored, OWNER, experiment, first["decision"]["id"], first["event"]["id"]
            )
            == before
        )
        assert shortlist_artifact_exists(restored, analysis_job.result_artifact)
    finally:
        restored.close()


def shortlist_artifact_exists(store, artifact):
    from services.research_shortlist import _artifact_present

    return _artifact_present(store, artifact)


def test_later_displayed_analysis_changing_during_save_is_fenced(app, client, monkeypatch):
    from services import research_analysis

    experiment, _, members, source, _, _ = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    later = evaluation(app, client, experiment, source, members[1]["config_id"])
    upgraded = research_analysis.submit(store, OWNER, later, symbol="AAA")
    run_worker(store)
    analysis_job = service.get_job(store, OWNER, upgraded["analysis_job_id"])
    expected = {
        "result_artifact": service.get_job(store, OWNER, later).result_artifact,
        "analysis_artifact": analysis_job.result_artifact,
    }
    original = decisions._evidence_use
    before = counts(store)

    def changed_analysis(*args, **kwargs):
        result = original(*args, **kwargs)
        with store.sessions.begin() as db:
            db.get(ResearchJob, analysis_job.id).result_artifact = None
        return result

    monkeypatch.setattr(decisions, "_evidence_use", changed_analysis)
    with pytest.raises(decisions.DecisionEvidenceChanged, match="Reopen the report"):
        decisions.save_direct_decision(
            store, OWNER, experiment, later, {**body(), "expected_report": expected}
        )
    assert counts(store) == before
