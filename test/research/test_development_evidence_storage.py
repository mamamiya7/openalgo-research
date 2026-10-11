"""Immutable account histories survive checkpoints and native artifact maintenance."""

import gc
import tracemalloc
from copy import deepcopy

import psutil
import pytest
from test_development_evidence import inputs, report_and_kwargs

from database.research_db import ResearchStore
from research.report_contract import fingerprint
from services import scanner_research_service as artifacts
from services.research_development_evidence import DevelopmentEvidenceWriter
from services.research_storage import _closure, _input_references


@pytest.fixture
def store(tmp_path):
    instance = ResearchStore(tmp_path / "research")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


def setup(store):
    evidence = inputs()
    source = artifacts.save_artifact(store, evidence)
    binding = {"inputs_artifact": source, "search_identity": "search-test"}
    writer = DevelopmentEvidenceWriter(store, evidence, binding=binding, max_evaluations=2)
    report, context = report_and_kwargs()
    context.pop("binding")
    return evidence, binding, writer, report, context


def test_immutable_shard_restores_and_remains_in_backup_prune_closure(store):
    evidence, binding, writer, report, context = setup(store)
    original = deepcopy(report)
    descriptor = writer.retain(report, search_identity=binding["search_identity"], **context)
    manifest = writer.snapshot(complete=True)
    checkpoint = artifacts.save_artifact(store, {"development_evidence": manifest})
    assert manifest["status"] == "complete"
    assert "curve" not in str(manifest)
    assert report == original
    assert descriptor["inputs_artifact"] in set(_input_references(manifest))
    assert {checkpoint, descriptor["inputs_artifact"], binding["inputs_artifact"]} <= set(
        _closure(store, [checkpoint])
    )
    restored = DevelopmentEvidenceWriter(
        store, evidence, binding=binding, max_evaluations=2, saved=manifest
    )
    assert restored.snapshot(complete=True) == manifest
    assert (
        restored.retain(report, search_identity=binding["search_identity"], **context) == descriptor
    )
    assert restored.snapshot(complete=True) == manifest
    assert (
        artifacts.read_artifact(store, descriptor["inputs_artifact"])["curve"]
        == report["equity_curve"]
    )


@pytest.mark.parametrize("damage", ["source", "totals", "descriptor", "missing_shard"])
def test_rehashed_resume_corruption_is_rejected(store, damage):
    evidence, binding, writer, report, context = setup(store)
    descriptor = writer.retain(report, search_identity=binding["search_identity"], **context)
    manifest = writer.snapshot()
    if damage == "source":
        manifest["binding"]["inputs_artifact"] = "b" * 64
    elif damage == "totals":
        manifest["marks"] += 1
    elif damage == "descriptor":
        manifest["entries"][context["evaluation_id"]]["config_id"] = "c" * 64
    else:
        directory = store.root / "artifacts"
        path = directory / (descriptor["inputs_artifact"] + ".json.gz")
        if not path.exists():
            path = path.with_suffix("")
        path.unlink()
    manifest["id"] = fingerprint({key: value for key, value in manifest.items() if key != "id"})
    with pytest.raises((ValueError, FileNotFoundError)):
        DevelopmentEvidenceWriter(
            store, evidence, binding=binding, max_evaluations=2, saved=manifest
        )


def test_unavailable_curve_is_explicit_and_does_not_become_complete(store):
    evidence, binding, writer, report, context = setup(store)
    report.pop("equity_curve")
    descriptor = writer.retain(report, search_identity=binding["search_identity"], **context)
    assert descriptor["status"] == "unavailable"
    assert writer.snapshot(complete=True)["status"] == "partial"
    restored = DevelopmentEvidenceWriter(
        store, evidence, binding=binding, max_evaluations=2, saved=writer.snapshot()
    )
    restored.reconcile(
        {
            context["evaluation_id"]: {
                "development_evidence": descriptor,
                "evaluation_basis": report["evaluation_basis"],
            }
        }
    )
    with pytest.raises(ValueError, match="disagree"):
        restored.reconcile({context["evaluation_id"]: {}})


def test_write_failure_does_not_publish_checkpoint_or_swallow_quota_error(store, monkeypatch):
    _, binding, writer, report, context = setup(store)

    def fail(*args):
        raise ValueError("storage quota reached")

    monkeypatch.setattr("services.research_development_evidence.save_artifact", fail)
    with pytest.raises(ValueError, match="quota"):
        writer.retain(report, search_identity=binding["search_identity"], **context)
    assert writer.snapshot()["entries"] == {}


@pytest.mark.parametrize("damage", ["index_count", "aggregate_bytes", "aggregate_marks"])
def test_resume_admits_index_before_any_shard_read(store, monkeypatch, damage):
    evidence, binding, writer, report, context = setup(store)
    descriptor = writer.retain(report, search_identity=binding["search_identity"], **context)
    manifest = writer.snapshot()
    if damage == "index_count":
        for index in range(2):
            key = f"extra-{index}"
            manifest["entries"][key] = {**descriptor, "evaluation_id": key}
    else:
        writer = DevelopmentEvidenceWriter(store, evidence, binding=binding, max_evaluations=320)
        manifest = writer.snapshot()
        count = 6 if damage == "aggregate_bytes" else 41
        for index in range(count):
            key = f"evaluation-{index}"
            manifest["entries"][key] = {
                **descriptor,
                "evaluation_id": key,
                **(
                    {"bytes": 24 * 1024 * 1024}
                    if damage == "aggregate_bytes"
                    else {"marks": 100_000}
                ),
            }
        manifest["bytes"] = sum(row["bytes"] for row in manifest["entries"].values())
        manifest["marks"] = sum(row["marks"] for row in manifest["entries"].values())
    manifest["id"] = fingerprint({key: value for key, value in manifest.items() if key != "id"})
    monkeypatch.setattr(
        "services.research_development_evidence.read_artifact",
        lambda *a, **kw: pytest.fail("Unadmitted index read a shard"),
    )
    with pytest.raises(ValueError, match="bound|admitted"):
        DevelopmentEvidenceWriter(
            store,
            evidence,
            binding=binding,
            max_evaluations=2 if damage == "index_count" else 320,
            saved=manifest,
        )


@pytest.mark.parametrize("damage", ["basis", "hypothesis", "gate"])
def test_scored_row_and_retained_history_context_must_agree(store, damage):
    _, binding, writer, report, context = setup(store)
    condition = {
        "hypothesis": context["hypothesis"],
        "bundle_id": "a" * 64,
        "gate_id": "b" * 64,
        "counts": {"allowed": 1, "filtered": 0, "unknown": 0, "original_excluded": 0, "pending": 0},
    }
    report["automatic_stock_condition"] = condition
    descriptor = writer.retain(report, search_identity=binding["search_identity"], **context)
    assert descriptor["status"] == "retained"
    row = {
        "development_evidence": descriptor,
        "evaluation_basis": deepcopy(report["evaluation_basis"]),
        "automatic_stock_condition": deepcopy(condition),
    }
    if damage == "basis":
        row["evaluation_basis"]["comparison"]["capital"] += 1
    elif damage == "hypothesis":
        row["automatic_stock_condition"]["hypothesis"] = {"id": "different"}
    else:
        row["automatic_stock_condition"]["gate_id"] = "different"
    with pytest.raises(ValueError, match="disagree"):
        writer.reconcile({context["evaluation_id"]: row})


def test_reserved_final_and_changed_search_are_rejected_before_write(store):
    _, binding, writer, report, context = setup(store)
    with pytest.raises(ValueError, match="different search"):
        writer.retain(report, search_identity="other", **context)
    with pytest.raises(ValueError, match="reserved final"):
        writer.retain(
            report, search_identity=binding["search_identity"], **{**context, "period": "final"}
        )
    assert writer.snapshot()["entries"] == {}


def test_repeated_resume_success_and_failed_retention_release_resources(store, record_property):
    evidence, binding, writer, report, context = setup(store)
    writer.retain(report, search_identity=binding["search_identity"], **context)
    saved = writer.snapshot()
    malformed = {key: value for key, value in report.items() if key != "equity_curve"}
    failing = DevelopmentEvidenceWriter(store, evidence, binding=binding, max_evaluations=2)
    failing.retain(malformed, search_identity=binding["search_identity"], **context)
    failed = failing.snapshot()

    def iteration():
        good = DevelopmentEvidenceWriter(
            store, evidence, binding=binding, max_evaluations=2, saved=saved
        )
        good.retain(report, search_identity=binding["search_identity"], **context)
        bad = DevelopmentEvidenceWriter(
            store, evidence, binding=binding, max_evaluations=2, saved=failed
        )
        bad.retain(malformed, search_identity=binding["search_identity"], **context)

    for _ in range(20):
        iteration()
    gc.collect()
    process = psutil.Process()
    descriptors = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    before, rss_before = descriptors(), process.memory_info().rss
    tracemalloc.start()
    try:
        for _ in range(120):
            iteration()
        gc.collect()
        retained, peak = tracemalloc.get_traced_memory()
        after, rss_after = descriptors(), process.memory_info().rss
    finally:
        tracemalloc.stop()
    record_property("iterations", 120)
    record_property("handles_before", before)
    record_property("handles_after", after)
    record_property("retained_bytes", retained)
    record_property("peak_bytes", peak)
    record_property("rss_growth", rss_after - rss_before)
    assert after <= before + 2
    assert retained < 512 * 1024
    assert peak < 2 * 1024 * 1024
    assert rss_after - rss_before < 8 * 1024 * 1024
