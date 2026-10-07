"""Display identities preserve effective periods, exact candidates and cheap lists."""

# ruff: noqa: F811 -- isolated shared Flask fixtures

import copy
import json
import time

import pytest
from test_acquisition import reference
from test_candidate_reports import frozen_studies, path, seed  # noqa: F401
from test_jobs import app, client  # noqa: F401
from test_library import BASE, OWNER, create, draft_for, run
from test_native_price_workflow import prepare_archive
from test_report_period_workflow import run_worker

from database.research_db import ResearchExperiment, ResearchJob, ResearchLibraryJob
from research.report_contract import settings_identity
from research.result_descriptor import VERSION, display_result, recorded_result
from services import scanner_research_service as service


def report():
    strategies = [{"id": "one", "name": "Scanner", "allocation_pct": 100, "config": {}}]
    config_id = settings_identity(strategies)
    return {
        "strategies": strategies,
        "coverage": {"date_from": "2026-01-01", "date_to": "2026-09-11"},
        "source": {"interval": "D"},
        "portfolio": {"capital": 100000},
        "reserved_evaluation": {
            "version": "research-period-plan-v1",
            "selection": {"from": "2026-01-05", "to": "2026-06-30"},
            "evaluation": {"from": "2026-07-01", "to": "2026-09-11"},
            "status": "reserved",
        },
        "evaluation_basis": {
            "version": "research-evaluation-basis-v1",
            "status": "verified",
            "evidence_id": "e" * 64,
            "cohort_id": "c" * 64,
            "period": {
                "kind": "selection",
                "from": "2026-01-05T09:15:00+05:30",
                "to": "2026-06-30T15:29:00+05:30",
                "interval": "1m",
            },
            "comparison": {"capital": 100000, "currency": "INR"},
        },
        "experiment": {
            "rows": [{"config_id": config_id, "trial_number": 0}],
            "recommendation_id": config_id,
        },
    }


def descriptor(**changes):
    return display_result(
        **{
            "kind": "portfolio_backtest",
            "evidence_id": "a" * 64,
            "calculation_id": "j" * 64,
            "specification": {"portfolio": {"capital": 100000}},
            "source_receipt": {"receipt": {"date_from": "2026-01-01", "date_to": "2026-09-11"}},
            **changes,
        }
    )


def test_matching_baseline_keeps_its_identity_without_hiding_a_subsequent_replay():
    original = report()
    original.pop("experiment")
    original["matched_baseline_origin"] = {
        "verification": {"settings": "matched", "basis": "matched"}
    }
    projection = recorded_result(
        original, job_id="j" * 32, result_artifact="a" * 64, inputs_artifact="i" * 64
    )
    assert descriptor(recorded=projection, link_role="run")["role"] == "matched_baseline"
    assert descriptor(recorded=projection, link_role="replay")["role"] == "replay"


def test_explicit_new_idea_name_prefills_only_omitted_draft_and_survives_rename(client):
    named = create(client, name="  Momentum research  ")
    assert named["name"] == named["draft"]["portfolio"]["name"] == "Momentum research"
    renamed = client.patch(
        f"{BASE}/experiments/{named['id']}", json={"revision": 1, "name": "Changed idea label"}
    ).json
    assert renamed["draft"]["portfolio"]["name"] == "Momentum research"
    explicit = draft_for(client)
    explicit["portfolio"]["name"] = "Separately named account"
    assert (
        create(client, explicit, name="Named idea")["draft"]["portfolio"]["name"]
        == "Separately named account"
    )
    unnamed = client.post(f"{BASE}/experiments", json={}).json
    assert unnamed["draft"]["portfolio"]["name"] == "My portfolio"


def test_projection_uses_canonical_trial_and_effective_timestamps_without_mutation():
    original = report()
    before = copy.deepcopy(original)
    saved = recorded_result(original, job_id="study", result_artifact="a" * 64, inputs_artifact="i")
    shown = descriptor(kind="portfolio_optimize", recorded=saved)
    assert original == before
    assert shown["role"] == "optimization" and shown["period"] == "selection"
    assert shown["candidate"] == {
        "study_job_id": "study",
        "trial_number": 0,
        "config_id": settings_identity(original["strategies"]),
        "is_objective_winner": True,
    }
    assert shown["dates"] == {
        "from": "2026-01-05T09:15:00+05:30",
        "to": "2026-06-30T15:29:00+05:30",
        "status": "recorded",
    }
    assert shown["input_dates"] == {"from": "2026-01-01", "to": "2026-09-11"}
    assert shown["interval"] == "1m" and shown["account"] == {"capital": 100000, "currency": "INR"}
    assert shown["reservation"] == original["reserved_evaluation"]
    assert shown["evaluation_basis_id"] == "e" * 64 and shown["cohort_id"] == "c" * 64
    shown["reservation"]["status"] = "changed"
    assert original == before and saved["reservation"]["status"] == "reserved"


@pytest.mark.parametrize(
    "changes,role,period",
    [
        ({}, "backtest", None),
        ({"link_role": "run"}, "baseline", None),
        ({"kind": "portfolio_optimize"}, "optimization", None),
        (
            {
                "link_role": "candidate",
                "candidate": {"study_job_id": "study", "config_id": "cfg", "period": "selection"},
            },
            "candidate",
            "selection",
        ),
        ({"link_role": "validation"}, "evaluation", "evaluation"),
        ({"link_role": "replay", "parent_job_id": "original"}, "replay", None),
        # A changed setup derived from a result is a new backtest, not an exact replay.
        ({"link_role": "run", "parent_job_id": "original"}, "backtest", None),
    ],
)
def test_metadata_roles_do_not_guess_effective_dates(changes, role, period):
    shown = descriptor(**changes)
    assert shown["role"] == role and shown["period"] == period
    assert shown["dates"] == {"from": None, "to": None, "status": "unknown"}
    assert shown["account"]["currency"] is None
    assert shown["interval"] is None and shown["reservation"] is None


@pytest.mark.parametrize("change", [{"evidence_id": "different"}, {"version": "future"}])
def test_stale_projection_cannot_relabel_current_artifact(change):
    saved = recorded_result(report(), job_id="study", result_artifact="a" * 64, inputs_artifact="i")
    shown = descriptor(recorded={**saved, **change})
    assert shown["dates"]["status"] == "unknown"
    assert shown["candidate"] is None and shown["report_id"] is None


def test_new_library_run_publishes_descriptor_without_changing_export(
    app, client, monkeypatch, tmp_path
):
    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    experiment = create(client, draft_for(client))
    response = run(client, experiment)
    identifier = response["job"]["id"]
    assert response["job"]["display"]["dates"]["status"] == "unknown"
    assert response["job"]["display"]["setup"]["id"] == response["version"]["id"]
    store = app.extensions["research_store"]
    run_worker(store)
    opened = client.get(f"/scanner-research/api/jobs/{identifier}").json
    assert opened["status"] == "completed", opened
    exported = client.get(f"/scanner-research/api/jobs/{identifier}/export").data
    with store.sessions() as db:
        record = db.get(ResearchExperiment, identifier)
        stored = json.loads(record.counts)["result_display"]
    assert stored["version"] == VERSION and len(service.encoded(stored)) < 4096
    assert opened["display"]["dates"]["status"] == "recorded"
    assert opened["display"]["account"] == {"capital": 20000, "currency": "INR"}
    assert opened["display"]["role"] == "baseline"
    with monkeypatch.context() as patch:
        patch.setattr(service, "read_artifact", lambda *_: pytest.fail("List read an artifact"))
        listed = client.get(f"{BASE}/experiments/{experiment['id']}").json["jobs"][0]
        assert listed["display"] == opened["display"]
        assert "result" not in listed
        assert client.get("/scanner-research/api/jobs?format=page").status_code == 200
    assert client.get(f"/scanner-research/api/jobs/{identifier}/export").data == exported


def test_legacy_study_and_new_candidate_share_exact_identity_without_list_backfill(
    app, client, frozen_studies, monkeypatch
):
    store = app.extensions["research_store"]
    parent, row, experiment = seed(app, client, frozen_studies)
    with store.sessions() as db:
        old_counts = db.get(ResearchExperiment, parent).counts
    original_export = client.get(f"/scanner-research/api/jobs/{parent}/export").data
    with monkeypatch.context() as patch:
        patch.setattr(
            service, "read_artifact", lambda *_: pytest.fail("Legacy list read an artifact")
        )
        legacy = client.get(f"{BASE}/experiments/{experiment}").json["jobs"][0]["display"]
        assert legacy["role"] == "optimization" and legacy["dates"]["status"] == "unknown"
        assert legacy["input_dates"] == {"from": "2026-01-05", "to": "2026-01-14"}
        assert client.get(f"{BASE}/studies").json["items"][0]["display"] == legacy
    opened = client.get(f"/scanner-research/api/jobs/{parent}").json["display"]
    assert opened["dates"]["status"] == "recorded"
    assert opened["dates"]["to"] < legacy["input_dates"]["to"]
    assert opened["reservation"]["status"] == "reserved"
    with store.sessions() as db:
        assert db.get(ResearchExperiment, parent).counts == old_counts
    prepared = client.post(path(parent, row["config_id"]), json={}).json
    child = prepared["report_job_id"]
    queued = client.get(f"/scanner-research/api/jobs/{child}").json["display"]
    assert queued["role"] == "candidate" and queued["period"] == "selection"
    assert queued["candidate"]["config_id"] == row["config_id"]
    assert queued["setup"] is None  # Never attach the study's optimizing version.
    run_worker(store)
    completed = client.get(f"/scanner-research/api/jobs/{child}").json
    assert completed["status"] == "completed", completed
    shown = completed["display"]
    assert shown["role"] == "candidate" and shown["config_id"] == row["config_id"]
    assert shown["candidate"]["trial_number"] == row["trial_number"]
    assert shown["candidate"]["is_objective_winner"] is False
    assert shown["dates"] == opened["dates"]
    assert shown["cohort_id"] == opened["cohort_id"]
    assert shown["reservation"] is None  # Child cannot invent its original reservation.
    with monkeypatch.context() as patch:
        patch.setattr(service, "read_artifact", lambda *_: pytest.fail("List read an artifact"))
        listed = client.get(f"{BASE}/experiments/{experiment}").json["jobs"]
        assert next(item for item in listed if item["id"] == child)["display"] == shown
    assert client.get(f"/scanner-research/api/jobs/{parent}/export").data == original_export


def test_foreign_or_ambiguous_library_version_is_never_chosen(app, client):
    original = run(client, create(client, draft_for(client)))
    store = app.extensions["research_store"]
    job = service.get_job(store, OWNER, original["job"]["id"])
    other = create(client)
    with store.sessions.begin() as db:
        db.add(
            ResearchLibraryJob(
                experiment_id=other["id"],
                job_id=job.id,
                version_id=original["version"]["id"],
                role="run",
                created_at=time.time(),
            )
        )
    assert service.job_receipt(store, job)["display"]["setup"] is None
    assert (
        service.job_receipt(store, job, experiment_id=original["experiment"]["id"])["display"][
            "setup"
        ]["id"]
        == original["version"]["id"]
    )
    assert service.job_receipt(store, job, experiment_id=other["id"])["display"]["setup"] is None
    with client.session_transaction() as session:
        session["user"] = "someone-else"
    assert client.get(f"/scanner-research/api/jobs/{job.id}").status_code == 404
    assert client.get(f"{BASE}/studies").json["items"] == []


@pytest.mark.parametrize("period,role", [("selection", "replay"), ("evaluation", "evaluation")])
def test_exact_replay_and_later_report_preserve_original_candidate_and_period(
    app, client, frozen_studies, monkeypatch, period, role
):
    store = app.extensions["research_store"]
    parent, row, _ = seed(app, client, frozen_studies)
    original = client.get(f"/scanner-research/api/jobs/{parent}").json
    exported = client.get(f"/scanner-research/api/jobs/{parent}/export").data
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda *a, **k: pytest.fail("Exact follow-up acquired prices"),
    )
    response = client.post(
        f"/scanner-research/api/portfolio/jobs/{parent}/rerun",
        json={"trial_id": row["config_id"], "period": period},
    )
    assert response.status_code == 202, response.json
    run_worker(store)
    opened = client.get(f"/scanner-research/api/jobs/{response.json['id']}").json
    assert opened["status"] == "completed", opened
    shown = opened["display"]
    assert shown["role"] == role and shown["period"] == period
    assert shown["candidate"] == {
        "study_job_id": parent,
        "config_id": row["config_id"],
        "trial_number": row["trial_number"],
        "is_objective_winner": False,
    }
    assert shown["parent_job_id"] == parent
    if period == "selection":
        assert shown["dates"] == original["display"]["dates"]
        assert shown["cohort_id"] == original["display"]["cohort_id"]
    else:
        assert shown["dates"]["from"] > original["display"]["dates"]["to"]
        assert shown["dates"]["from"] == original["display"]["reservation"]["evaluation"]["from"]
        assert shown["cohort_id"] != original["display"]["cohort_id"]
    with monkeypatch.context() as patch:
        patch.setattr(
            service, "read_artifact", lambda *_: pytest.fail("Follow-up list read prices")
        )
        job = service.get_job(store, OWNER, response.json["id"])
        assert service.job_receipt(store, job)["display"] == shown
    assert client.get(f"/scanner-research/api/jobs/{parent}/export").data == exported
