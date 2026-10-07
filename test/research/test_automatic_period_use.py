"""Durable account-wide period admission, migration and exact-resume fencing."""

from copy import deepcopy

import pytest
from sqlalchemy import inspect, select
from test_automatic_conditions import prepared

from database.research_db import ResearchAutomaticPeriodUse, ResearchStore
from services.research_automatic_periods import claim


@pytest.fixture
def store(tmp_path):
    value = ResearchStore(tmp_path)
    value.initialize()
    try:
        yield value
    finally:
        value.close()


def test_same_inputs_resume_but_new_source_or_recipe_cannot_recycle_final_dates(store):
    evidence = prepared()
    claim(store, "owner", "a" * 64, evidence, "job-a")
    claim(store, "owner", "a" * 64, evidence, "job-a")
    with pytest.raises(ValueError, match="already used"):
        claim(store, "owner", "a" * 64, evidence, "job-b")
    with store.sessions() as db:
        rows = db.scalars(select(ResearchAutomaticPeriodUse)).all()
        assert len(rows) == 160
        assert sum(row.role == "final" for row in rows) == 32
    with pytest.raises(ValueError, match="already used"):
        claim(store, "owner", "b" * 64, evidence, "job-b")
    claim(store, "other-owner", "b" * 64, evidence, "job-b")


def test_new_final_cannot_overlap_previous_development_dates(store):
    evidence = prepared()
    claim(store, "owner", "a" * 64, evidence, "job-a")
    changed = deepcopy(evidence)
    changed["automatic_recipe"]["periods"]["final"] = changed["automatic_recipe"]["periods"][
        "check1"
    ]
    with pytest.raises(ValueError, match="already used"):
        claim(store, "owner", "b" * 64, changed, "job-b")


def test_claim_schema_is_additive_idempotent_and_survives_store_restart(store):
    evidence = prepared()
    claim(store, "owner", "a" * 64, evidence, "job-a")
    store.initialize()
    assert "research_automatic_period_use" in inspect(store.engine).get_table_names()
    root = store.root
    store.close()
    replacement = ResearchStore(root)
    try:
        replacement.initialize()
        claim(replacement, "owner", "a" * 64, evidence, "job-a")
        with pytest.raises(ValueError, match="already used"):
            claim(replacement, "owner", "b" * 64, evidence, "job-b")
    finally:
        replacement.close()


def test_maintenance_fence_prevents_claims(store):
    from database.research_db import ResearchWorker

    with store.sessions.begin() as db:
        db.get(ResearchWorker, 1).token = "maintenance:test"
    with pytest.raises(ValueError, match="maintenance"):
        claim(store, "owner", "a" * 64, prepared(), "job-a")
    with store.sessions() as db:
        assert db.scalars(select(ResearchAutomaticPeriodUse)).all() == []


@pytest.mark.parametrize("known_dates", [True, False])
def test_pre_claim_published_reports_cannot_be_relabelled_untouched(store, known_dates):
    from database.research_db import ResearchExperiment, ResearchJob
    from services import scanner_research_service as service

    evidence = prepared()
    final = evidence["automatic_recipe"]["periods"]["final"]
    report = {"equity_curve": [{"date": final["from"]}]} if known_dates else {}
    artifact = service.save_artifact(store, {"result": report, "inputs_artifact": "a" * 64})
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id="earlier",
                owner="owner",
                source_id="source",
                config="{}",
                status="completed",
                created_at=1,
                updated_at=1,
                result_artifact=artifact,
            )
        )
        db.add(
            ResearchExperiment(
                job_id="earlier", kind="portfolio_backtest", specification="{}", identity="original"
            )
        )
    with pytest.raises(ValueError, match="published research|unknown dates"):
        claim(store, "owner", "a" * 64, evidence, "new-job")
    with store.sessions() as db:
        assert db.scalars(select(ResearchAutomaticPeriodUse)).all() == []
