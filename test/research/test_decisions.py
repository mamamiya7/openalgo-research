"""Native candidate decisions retain exact evidence and an append-only history."""

# ruff: noqa: F811 -- shared isolated native fixtures

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from test_candidate_reports import frozen_studies
from test_comparisons import create, ready
from test_jobs import app, client
from test_library import OWNER
from test_library import create as create_library
from test_library import run as run_library
from test_portfolio_validation import inputs
from test_report_period_workflow import draft, run_worker
from test_shortlist import change_result
from test_shortlist import save as save_candidate

from database.research_db import (
    ResearchDecision,
    ResearchDecisionEvent,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchStore,
)
from services import research_candidates as candidates
from services import research_decisions as decisions
from services import scanner_research_service as service
from services.research_storage import backup_store, restore_store


def setup(app, client, frozen_studies, reserved=True):
    experiment, members, job, child = ready(app, client, frozen_studies, reserved)
    comparison = create(client, experiment, members)
    base = f"/scanner-research/api/library/experiments/{experiment}"
    return experiment, comparison, members, job, child, base


def native_setup(app, client, monkeypatch, *, legacy=False):
    from research.portfolio_coverage import prepare

    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda _store, _owner, evidence, **_: prepare(
            {**evidence, "snapshot": copy.deepcopy(inputs()["snapshot"]), "frozen_prices": True}
        ),
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a: pytest.fail("Native fixture requested broker"),
    )
    configuration = draft(client, True)
    if legacy:
        configuration["portfolio"]["validation"]["mode"] = "evaluate"
    experiment = create_library(client, configuration)
    job = run_library(client, experiment)["job"]["id"]
    run_worker(store)
    result = service.read_artifact(store, service.get_job(store, OWNER, job).result_artifact)[
        "result"
    ]
    study = result["experiment"]
    alternative = next(
        row for row in study["rows"] if row["config_id"] != study["recommendation_id"]
    )
    members = [
        save_candidate(client, experiment["id"], job, config).json["candidate"]
        for config in (study["recommendation_id"], alternative["config_id"])
    ]
    child = candidates.prepare(store, OWNER, job, alternative["config_id"])["report_job_id"]
    run_worker(store)
    comparison = create(client, experiment["id"], members)
    return (
        experiment["id"],
        comparison,
        members,
        job,
        child,
        f"/scanner-research/api/library/experiments/{experiment['id']}",
    )


def member_path(base, comparison, member):
    return f"{base}/comparisons/{comparison['id']}/members/{member['id']}"


def body(token="candidate-choice", revision=0, **values):
    return {
        "request_id": token,
        "revision": revision,
        "state": "keep",
        "reason": "Promising candidate",
        **values,
    }


def save(client, path, **kwargs):
    response = client.post(path + "/decisions", json=body(**kwargs))
    assert response.status_code in (200, 201), response.json
    return response.json


def evaluation(app, client, experiment, job, config):
    store = app.extensions["research_store"]
    with store.sessions() as db:
        revision = db.get(ResearchLibraryExperiment, experiment).revision
    response = client.post(
        f"/scanner-research/api/library/experiments/{experiment}/replay",
        json={
            "revision": revision,
            "job_id": job,
            "trial_id": config,
            "period": "evaluation",
            "request_id": "later-" + config[:12],
        },
    )
    assert response.status_code == 202, response.json
    run_worker(store)
    later = response.json["job"]["id"]
    assert service.get_job(store, OWNER, later).status == "completed"
    return later


@pytest.mark.parametrize("reserved", [False, True])
def test_choice_supersedes_without_changing_candidate_or_objective_winner(
    app, client, frozen_studies, reserved
):
    experiment, comparison, members, job, child, base = setup(app, client, frozen_studies, reserved)
    path = member_path(base, comparison, members[1])
    original = client.get(f"/scanner-research/api/jobs/{job}/export").data
    first = save(client, path)
    second = save(
        client,
        path,
        token="revisit-candidate",
        revision=1,
        state="revisit",
        reason="Need more evidence",
    )
    assert first["event"]["id"] != second["event"]["id"]
    assert second["event"]["supersedes_event_id"] == first["event"]["id"]
    assert second["decision"]["revision"] == 2
    assert not second["event"]["is_objective_winner"]
    assert second["event"]["period"] == ("selection" if reserved else "full")
    history = client.get(f"{base}/decisions/{first['decision']['id']}/history").json
    assert [item["state"] for item in history["items"]] == ["revisit", "keep"]
    assert client.get(base + "/decisions?state=keep").json["items"] == []
    frozen = client.get(
        f"{base}/decisions/{first['decision']['id']}/events/{first['event']['id']}/report"
    ).json
    assert frozen["available"] and frozen["job"]["id"] == child
    assert client.get(f"/scanner-research/api/jobs/{job}/export").data == original


def test_accepted_retry_survives_supersession_archive_bookmark_removal_and_pointer_changes(
    app, client, frozen_studies
):
    experiment, comparison, members, job, child, base = setup(app, client, frozen_studies)
    store = app.extensions["research_store"]
    path = member_path(base, comparison, members[1])
    first = save(client, path)
    before = client.get(
        f"{base}/decisions/{first['decision']['id']}/events/{first['event']['id']}/report"
    ).json
    save(client, path, token="reject-after-review", revision=1, state="reject")
    for item in members:
        assert (
            client.delete(f"{base}/shortlist/{item['id']}", json={"revision": 1}).status_code == 200
        )
    change_result(store, child, lambda result: result["summary"].update(net_return_pct=999))
    change_result(store, job, lambda result: result["summary"].update(net_return_pct=999))
    with store.sessions.begin() as db:
        db.get(ResearchLibraryExperiment, experiment).archived = True
    retried = client.post(path + "/decisions", json=body()).json
    assert retried["reused"] and retried["event"] == first["event"]
    assert retried["decision"]["revision"] == 1
    assert (
        client.get(
            f"{base}/decisions/{first['decision']['id']}/events/{first['event']['id']}/report"
        ).json
        == before
    )


def test_concurrent_retries_and_same_candidate_across_comparisons_share_head(
    app, client, frozen_studies
):
    experiment, comparison, members, *_, base = setup(app, client, frozen_studies)
    store = app.extensions["research_store"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: decisions.save_decision(
                    store, OWNER, experiment, comparison["id"], members[1]["id"], body()
                ),
                range(4),
            )
        )
    assert sum(not item["reused"] for item in results) == 1
    second = create(client, experiment, members, token="second-comparison")
    result = save(
        client, member_path(base, second, members[1]), token="different-comparison", revision=1
    )
    assert result["decision"]["id"] == results[0]["decision"]["id"]
    assert result["event"]["comparison_id"] == second["id"]
    with store.sessions() as db:
        assert len(db.scalars(select(ResearchDecision)).all()) == 1
        assert len(db.scalars(select(ResearchDecisionEvent)).all()) == 2


def test_exact_nonwinner_later_evidence_is_discovered_attached_and_frozen(
    app, client, frozen_studies, monkeypatch
):
    experiment, comparison, members, job, child, base = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    later = evaluation(app, client, experiment, job, members[1]["config_id"])
    path = member_path(base, comparison, members[1])
    context = client.get(path + "/decision").json
    assert len(context["evaluation"]["items"]) == 1, context
    receipt = context["evaluation"]["items"][0]
    assert receipt["job_id"] == later
    assert (
        client.get(member_path(base, comparison, members[0]) + "/decision").json["evaluation"][
            "items"
        ]
        == []
    )
    opened = client.post(
        base + "/evidence/opened",
        json={
            "request_id": "open-later-evidence",
            "target": {
                "kind": "comparison_member",
                "comparison_id": comparison["id"],
                "member_id": members[1]["id"],
                "evaluation_id": receipt["id"],
            },
        },
    )
    assert opened.status_code == 200, opened.json
    result = save(client, path, evaluation_id=receipt["id"])
    assert result["event"]["evidence_use"]["later_used_for_decision"]
    assert result["event"]["evidence_use"]["opened_at"] == opened.json["opened_at"]
    report_path = f"{base}/decisions/{result['decision']['id']}/events/{result['event']['id']}/report?evidence=evaluation"
    before = client.get(report_path).json
    assert before["available"] and before["result"]["report_context"]["period"] == "evaluation"
    assert before["result"]["report_context"]["config_id"] == members[1]["config_id"]
    change_result(store, later, lambda report: report["summary"].update(net_return_pct=999))
    monkeypatch.setattr(
        "services.research_analysis.latest_job",
        lambda *a, **k: pytest.fail("Decision followed latest analysis"),
    )
    assert client.get(report_path).json == before


def test_opened_is_explicit_deduplicated_and_request_bound(app, client, frozen_studies):
    experiment, comparison, members, *_, base = setup(app, client, frozen_studies)
    target = {
        "kind": "comparison_member",
        "comparison_id": comparison["id"],
        "member_id": members[1]["id"],
    }
    request = {"request_id": "open-selection-first", "target": target}
    first = client.post(base + "/evidence/opened", json=request)
    assert first.status_code == 200 and not first.json["reused"], first.json
    other_token = {**request, "request_id": "open-selection-again"}
    second = client.post(base + "/evidence/opened", json=other_token)
    assert second.json["reused"] and second.json["opened_at"] == first.json["opened_at"]
    assert (
        client.post(
            base + "/evidence/opened",
            json={**other_token, "target": {**target, "member_id": members[0]["id"]}},
        ).status_code
        == 409
    )
    context = client.get(member_path(base, comparison, members[1]) + "/decision").json
    assert context["evidence_use"]["opened_at"] == first.json["opened_at"]
    assert context["evidence_use"]["coverage"] == "legacy_unknown"


def test_recorded_full_period_calculation_is_overlap_not_untouched(app, client, monkeypatch):
    experiment, comparison, members, job, _, base = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    path = member_path(base, comparison, members[1])
    before = client.get(path + "/decision").json["evidence_use"]
    assert before["calculation"] == "recorded" and before["overlap"] == "not_found"
    configuration = draft(client, False)
    configuration["portfolio"].pop("validation")
    full_experiment = create_library(client, configuration)
    run_library(client, full_experiment, token="full-period-baseline")
    run_worker(store)
    after = client.get(path + "/decision").json["evidence_use"]
    assert after["overlap"] == "recorded" and after["opened_at"] is None
    assert after["coverage"] == "legacy_unknown"
    assert "untouched" not in json.dumps(after).lower()


def test_legacy_embedded_later_belongs_only_to_actual_winner(app, client, monkeypatch):
    experiment, comparison, members, job, _, base = native_setup(
        app, client, monkeypatch, legacy=True
    )
    path = member_path(base, comparison, members[0])
    context = client.get(path + "/decision").json
    assert len(context["evaluation"]["items"]) == 1, context
    later = context["evaluation"]["items"][0]
    assert later["view"] == "embedded_later" and later["job_id"] == job
    assert context["evidence_use"]["overlap"] == "recorded"
    assert context["evidence_use"]["opened_at"] is None
    assert (
        client.get(member_path(base, comparison, members[1]) + "/decision").json["evaluation"][
            "items"
        ]
        == []
    )
    selected = save(client, path, evaluation_id=later["id"])
    report = client.get(
        f"{base}/decisions/{selected['decision']['id']}/events/{selected['event']['id']}/report?evidence=evaluation"
    ).json
    assert report["available"] and report["result"]["report_context"]["period"] == "evaluation"
    assert report["result"]["report_context"]["candidate"]["config_id"] == members[0]["config_id"]


@pytest.mark.parametrize("mutation", ["origin", "prices"])
def test_same_settings_later_report_with_wrong_origin_or_prices_is_not_admitted(
    app, client, monkeypatch, mutation
):
    experiment, comparison, members, job, _, base = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    later = evaluation(app, client, experiment, job, members[1]["config_id"])
    path = member_path(base, comparison, members[1])
    receipt = client.get(path + "/decision").json["evaluation"]["items"][0]
    saved = service.get_job(store, OWNER, later)
    bundle = service.read_artifact(store, saved.result_artifact)
    if mutation == "origin":
        bundle["result"]["replay_origin"]["parent_result_artifact"] = "0" * 64
    else:
        evidence = service.read_artifact(store, bundle["inputs_artifact"])
        bar = next(iter(evidence["snapshot"]["bars"]["AAA"].values()))
        bar["close"] += 1
        bundle["inputs_artifact"] = service.save_artifact(store, evidence)
    artifact = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, later).result_artifact = artifact
    assert client.get(path + "/decision").json["evaluation"]["items"] == []
    response = client.post(path + "/decisions", json=body(evaluation_id=receipt["id"]))
    assert response.status_code in (400, 409), response.json


def test_analysis_pin_and_original_null_survive_later_upgrade(app, client, monkeypatch):
    from services import research_analysis

    experiment, comparison, members, job, _, base = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    later = evaluation(app, client, experiment, job, members[1]["config_id"])
    path = member_path(base, comparison, members[1])
    original = client.get(path + "/decision").json["evaluation"]["items"][0]
    first = save(client, path, evaluation_id=original["id"])
    first_path = f"{base}/decisions/{first['decision']['id']}/events/{first['event']['id']}/report?evidence=evaluation"
    before = client.get(first_path).json
    upgraded = research_analysis.submit(store, OWNER, later, symbol="AAA")
    run_worker(store)
    refreshed = client.get(path + "/decision").json["evaluation"]["items"][0]
    assert refreshed["id"] != original["id"]
    assert (
        client.post(
            path + "/decisions",
            json=body(token="stale-analysis-evidence", revision=1, evaluation_id=original["id"]),
        ).status_code
        == 409
    )
    second = save(
        client, path, token="updated-analysis-choice", revision=1, evaluation_id=refreshed["id"]
    )
    second_path = f"{base}/decisions/{second['decision']['id']}/events/{second['event']['id']}/report?evidence=evaluation"
    pinned = client.get(second_path).json
    assert pinned["result"]["report_context"]["analysis_artifact"]
    with store.sessions.begin() as db:
        db.get(ResearchJob, upgraded["analysis_job_id"]).result_artifact = None
    assert client.get(first_path).json == before
    assert client.get(second_path).json == pinned


def test_populated_backup_restores_decisions_openings_and_pinned_later_evidence(
    app, client, monkeypatch, tmp_path
):
    experiment, comparison, members, job, _, base = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    later = evaluation(app, client, experiment, job, members[1]["config_id"])
    path = member_path(base, comparison, members[1])
    evidence = client.get(path + "/decision").json["evaluation"]["items"][0]
    first = save(client, path, evaluation_id=evidence["id"])
    event, identifier = first["event"]["id"], first["decision"]["id"]
    request = {
        "request_id": "open-saved-decision",
        "target": {
            "kind": "decision_event",
            "decision_id": identifier,
            "event_id": event,
            "evidence": "evaluation",
        },
    }
    assert client.post(base + "/evidence/opened", json=request).status_code == 200
    assert client.post(
        base + "/evidence/opened", json={**request, "request_id": "second-open-decision"}
    ).json["reused"]
    save(client, path, token="later-revisit-decision", revision=1, state="revisit")
    before = decisions.event_report(
        store, OWNER, experiment, identifier, event, evidence="evaluation"
    )
    for member in members:
        client.delete(f"{base}/shortlist/{member['id']}", json={"revision": 1})
    change_result(store, later, lambda result: result["summary"].update(net_return_pct=999))
    backup = tmp_path.with_name(tmp_path.name + "-decisions-backup")
    backup_store(store, backup)
    restored_path = tmp_path.with_name(tmp_path.name + "-decisions-restored")
    restore_store(backup, restored_path)
    restored = ResearchStore(restored_path)
    restored.initialize()
    try:
        assert (
            decisions.event_report(
                restored, OWNER, experiment, identifier, event, evidence="evaluation"
            )
            == before
        )
        assert decisions.decision_history(restored, OWNER, experiment, identifier)["total"] == 2
        assert decisions.acknowledge_open(restored, OWNER, experiment, request)["reused"]
        assert (
            decisions.save_decision(
                restored,
                OWNER,
                experiment,
                comparison["id"],
                members[1]["id"],
                body(evaluation_id=evidence["id"]),
            )["event"]
            == first["event"]
        )
    finally:
        restored.close()


@pytest.mark.parametrize("mutation", ["head", "supersedes", "request", "pin", "size"])
def test_backup_rejects_retargeted_or_oversized_history(
    app, client, frozen_studies, tmp_path, mutation
):
    from database.research_db import ResearchDecisionRequest

    experiment, comparison, members, _, _, base = setup(app, client, frozen_studies)
    store = app.extensions["research_store"]
    saved = save(client, member_path(base, comparison, members[1]))
    with store.sessions.begin() as db:
        head = db.get(ResearchDecision, saved["decision"]["id"])
        event = db.get(ResearchDecisionEvent, saved["event"]["id"])
        if mutation == "head":
            head.config_id = members[0]["config_id"]
        elif mutation == "supersedes":
            event.supersedes_event_id = event.id
        elif mutation == "request":
            db.get(ResearchDecisionRequest, (OWNER, "candidate-choice")).payload_hash = "0" * 64
        elif mutation == "pin":
            event.member_id = members[0]["id"]
        else:
            event.evaluation_pin = " " * (decisions.MAX_SNAPSHOT_BYTES + 1)
    with pytest.raises(ValueError):
        backup_store(store, tmp_path.with_name(tmp_path.name + "-bad-decision"))


def test_old_store_without_decision_tables_remains_restorable(
    app, client, frozen_studies, tmp_path
):
    setup(app, client, frozen_studies)
    store = app.extensions["research_store"]
    with store.engine.begin() as connection:
        for name in (
            "research_decision_requests",
            "research_evidence_opens",
            "research_decision_events",
            "research_decisions",
        ):
            connection.exec_driver_sql(f"DROP TABLE {name}")
    backup = tmp_path.with_name(tmp_path.name + "-old-decisions")
    backup_store(store, backup)
    restored_path = tmp_path.with_name(tmp_path.name + "-old-restored")
    restore_store(backup, restored_path)
    restored = ResearchStore(restored_path)
    restored.initialize()
    try:
        with restored.sessions() as db:
            assert db.scalars(select(ResearchDecision)).all() == []
    finally:
        restored.close()
