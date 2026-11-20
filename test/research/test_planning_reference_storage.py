"""Exact AI-feedback checkpoints survive isolated prune/backup without engines."""

import json
import os
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchJourney,
    ResearchLibraryExperiment,
    ResearchSource,
    ResearchStore,
)
from research import planning_recovery
from research.report_contract import fingerprint
from services import research_storage as storage
from services import scanner_research_service as artifacts


def sealed(body):
    return {**body, "id": fingerprint(body)}


@pytest.fixture
def saved(tmp_path):
    store = ResearchStore(tmp_path / "planning-store")
    store.initialize()
    source = artifacts.save_artifact(store, {"fixture": "source"})
    feedback = artifacts.save_artifact(store, {"inputs_artifact": source, "fixture": "AI boundary"})
    legacy = artifacts.save_artifact(
        store, {"inputs_artifact": source, "fixture": "legacy failed checkpoint"}
    )
    authority_checkpoint = artifacts.save_artifact(
        store, {"inputs_artifact": source, "fixture": "operator admission checkpoint"}
    )
    job_id = "j" * 32
    book = {
        "calls_spent": 1,
        "records": {"fit1:11": {"phase": "requesting", "request_id": "a" * 64}},
    }
    annex = planning_recovery.authorize(
        book,
        checkpoint_id=legacy,
        request_id="a" * 64,
        authority_id="authorized-recovery",
        job_id=job_id,
    )
    update = sealed(
        {
            "version": "research-controller-v2",
            "job_id": job_id,
            "plan_id": "b" * 64,
            "known_rejection": {
                "boundary_key": "fit1:11",
                "checkpoint_id": authority_checkpoint,
                "request_id": "a" * 64,
                "error": "Known invalid completed domain",
            },
        }
    )
    calculation = {
        "planning_feedback_refs": {"fit1:11": {"checkpoint": feedback, "job_id": job_id}},
        "planning_recovery": annex,
        "controller_v2": {"authority": update},
    }
    checkpoint = artifacts.save_artifact(store, {"state": {"calculation": calculation}})
    report = artifacts.save_artifact(
        store, {"hands_off_planning": {"version": "hands-off-fixed-signals-v1", "recovery": annex}}
    )
    orphan = artifacts.save_artifact(store, {"fixture": "disposable orphan"})
    with store.sessions.begin() as db:
        db.add(ResearchSource(id="source", owner="owner", artifact=source, created_at=1))
        db.add(
            ResearchJob(
                id=job_id,
                owner="owner",
                source_id="source",
                config="{}",
                status="completed",
                progress=100,
                created_at=1,
                updated_at=1,
                result_artifact=report,
            )
        )
        db.add(
            ResearchExperiment(
                job_id=job_id,
                kind="portfolio_optimize",
                specification="{}",
                identity="identity",
                checkpoint=checkpoint,
                counts="{}",
            )
        )
        db.add(
            ResearchLibraryExperiment(
                id="study",
                owner="owner",
                name="Study",
                notes="",
                tags="[]",
                revision=1,
                draft="{}",
                created_at=1,
                updated_at=1,
            )
        )
        db.add(
            ResearchJourney(
                experiment_id="study",
                owner="owner",
                phase="awaiting_approval",
                state=json.dumps(
                    {
                        "version": "reviewed-research-journey-v1",
                        "job_id": job_id,
                        "history": [],
                        "controller_update": update,
                    }
                ),
                updated_at=1,
            )
        )
    for path in (store.root / "artifacts").iterdir():
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    try:
        yield SimpleNamespace(
            store=store,
            calculation=calculation,
            update=update,
            annex=annex,
            retained={source, feedback, legacy, authority_checkpoint, checkpoint, report},
            feedback=feedback,
            legacy=legacy,
            authority_checkpoint=authority_checkpoint,
            orphan=orphan,
        )
    finally:
        store.close()


def test_prune_and_backup_retain_exact_feedback_legacy_and_authority_checkpoints(saved, tmp_path):
    roots = storage._references(saved.store.engine, store=saved.store)
    assert (
        saved.authority_checkpoint in roots
    )  # Journey root survives independently of its current job artifact.
    closure = storage._closure(saved.store, roots)
    assert saved.retained <= closure.keys()
    assert storage.prune_orphans(saved.store)["files"] == [f"{saved.orphan}.json.gz"]
    pruned = storage.prune_orphans(saved.store, apply=True)
    assert pruned["deleted_files"] == 1
    for digest in saved.retained:
        artifacts.read_artifact(saved.store, digest)
    with pytest.raises(ValueError, match="missing or damaged"):
        artifacts.read_artifact(saved.store, saved.orphan)
    backup = tmp_path / "backup"
    storage.backup_store(saved.store, backup)
    for digest in saved.retained:
        assert artifacts.read_artifact(
            SimpleNamespace(root=backup), digest
        ) == artifacts.read_artifact(saved.store, digest)


def test_report_recovery_alias_retains_annex_checkpoint(saved):
    value = {
        "hands_off_planning": {"version": "hands-off-fixed-signals-v1", "recovery": saved.annex}
    }
    assert saved.legacy in set(storage._input_references(value))


@pytest.mark.parametrize(
    "change",
    [
        "feedback_hash",
        "feedback_extra",
        "too_many",
        "annex_seal",
        "controller_seal",
        "unknown_annex",
    ],
)
def test_malformed_typed_references_fail_closed_instead_of_marking_evidence_orphan(saved, change):
    value = deepcopy(saved.calculation)
    if change == "feedback_hash":
        value["planning_feedback_refs"]["fit1:11"]["checkpoint"] = "not-a-digest"
    elif change == "feedback_extra":
        value["planning_feedback_refs"]["fit1:11"]["extra"] = "ambiguous"
    elif change == "too_many":
        value["planning_feedback_refs"] = {
            f"fit1:{n}": {"checkpoint": saved.feedback, "job_id": "job"} for n in range(13)
        }
    elif change == "annex_seal":
        value["planning_recovery"]["authorization"]["checkpoint_id"] = "f" * 64
    elif change == "controller_seal":
        value["controller_v2"]["authority"]["known_rejection"]["checkpoint_id"] = "f" * 64
    else:
        value["planning_recovery"]["version"] = "future-unknown-version"
    with pytest.raises(ValueError):
        set(storage._input_references(value))


def test_arbitrary_checkpoint_id_is_not_guessed_as_artifact_ownership(saved):
    assert set(storage._input_references({"diagnostic": {"checkpoint_id": saved.orphan}})) == set()


def test_referenced_feedback_artifact_hash_is_still_verified_before_cleanup(saved):
    path = saved.store.root / "artifacts" / f"{saved.feedback}.json.gz"
    path.write_bytes(b"not a canonical artifact")
    with pytest.raises((ValueError, OSError, EOFError)):
        storage.prune_orphans(saved.store, apply=True)
    assert (saved.store.root / "artifacts" / f"{saved.orphan}.json.gz").exists()
