"""Bounded deferred shard writes preserve exact validated development evidence."""

# ruff: noqa: F811 -- imported isolated store fixture
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy

import pytest
from test_chunked_artifacts import store  # noqa: F401
from test_development_evidence import inputs, report_and_kwargs

from database.research_db import ResearchStore
from research.report_contract import fingerprint
from services import research_development_evidence as module
from services import scanner_research_service as artifacts


def configured(store, *, staged=True, maximum=32):
    evidence = inputs()
    source = artifacts.save_artifact(store, evidence)
    binding = {"inputs_artifact": source, "search_identity": "staging-test"}
    writer = module.DevelopmentEvidenceWriter(
        store, evidence, binding=binding, max_evaluations=maximum, stage_publication=staged
    )
    report, context = report_and_kwargs()
    context.pop("binding")
    return writer, evidence, binding, report, context


def retain(writer, report, context, index=0):
    return writer.retain(
        report,
        search_identity=writer.binding["search_identity"],
        **{**context, "evaluation_id": fingerprint({"trial": index})},
    )


def test_staged_manifest_and_shards_match_default_without_writes_before_flush(store, tmp_path):
    staged, evidence, binding, report, context = configured(store)
    other = ResearchStore(tmp_path / "immediate")
    other.initialize()
    try:
        immediate, _, _, _, _ = configured(other, staged=False)
        before = set((store.root / "artifacts").iterdir())
        for index in range(3):
            assert retain(staged, report, context, index) == retain(
                immediate, report, context, index
            )
        assert staged.snapshot() == immediate.snapshot()
        assert set((store.root / "artifacts").iterdir()) == before
        with pytest.raises(ValueError, match="active artifact scope"):
            staged.publish_pending()
        with artifacts.artifact_publication(store):
            staged.publish_pending()
        assert staged._pending == {} and staged._pending_bytes == 0
        for key in staged.entries:
            assert staged.read(key) == immediate.read(key)
        restored = module.DevelopmentEvidenceWriter(
            store, evidence, binding=binding, max_evaluations=32,
            stage_publication=True, saved=staged.snapshot()
        )
        assert restored.snapshot() == staged.snapshot()
        assert restored._pending == {}
    finally:
        other.close()


def test_pending_read_is_verified_detached_and_never_publishes(store):
    writer, _, _, report, context = configured(store)
    descriptor = retain(writer, report, context)
    key = descriptor["evaluation_id"]
    expected = writer.read(key)
    before = set((store.root / "artifacts").iterdir())
    report["equity_curve"][0]["equity"] = 0
    descriptor["bytes"] = -1
    loaded = writer.read(key)
    loaded["curve"][0]["equity"] = 0
    assert writer.read(key) == expected
    assert set((store.root / "artifacts").iterdir()) == before
    writer._pending[key] += b" "
    with pytest.raises(ValueError, match="identity changed"):
        writer.read(key)
    with artifacts.artifact_publication(store):
        with pytest.raises(ValueError, match="identity changed"):
            writer.publish_pending()


def test_pending_count_bound_never_silently_publishes_or_changes_manifest(store):
    writer, _, _, report, context = configured(store)
    before = set((store.root / "artifacts").iterdir())
    for index in range(16):
        retain(writer, report, context, index)
    saved = writer.snapshot()
    pending = deepcopy(writer._pending)
    with pytest.raises(ValueError, match="must be published"):
        retain(writer, report, context, 16)
    assert writer.snapshot() == saved and writer._pending == pending
    assert writer._pending_bytes == sum(map(len, pending.values()))
    assert set((store.root / "artifacts").iterdir()) == before


def test_pending_byte_bound_keeps_previous_state_and_repeated_key_is_not_double_counted(
    store, monkeypatch
):
    writer, _, _, report, context = configured(store)
    first = retain(writer, report, context)
    size = writer._pending_bytes
    monkeypatch.setattr(module, "MAX_PENDING_BYTES", size * 2 - 1)
    assert retain(writer, report, context) == first
    assert len(writer._pending) == 1 and writer._pending_bytes == size
    saved = writer.snapshot()
    with pytest.raises(ValueError, match="must be published"):
        retain(writer, report, context, 1)
    assert writer.snapshot() == saved
    assert len(writer._pending) == 1 and writer._pending_bytes == size


@pytest.mark.parametrize("failure", ["write", "identity"])
def test_failed_flush_retains_all_pending_shards_for_exact_retry(store, monkeypatch, failure):
    writer, _, _, report, context = configured(store)
    retain(writer, report, context)
    retain(writer, report, context, 1)
    pending = deepcopy(writer._pending)
    manifest = writer.snapshot()
    original = module.save_artifact
    calls = []

    def fail(selected, value):
        calls.append(True)
        if len(calls) == 2:
            if failure == "write":
                raise ValueError("controlled quota failure")
            return "f" * 64
        return original(selected, value)

    monkeypatch.setattr(module, "save_artifact", fail)
    with pytest.raises(ValueError, match="quota failure|identity changed"):
        with artifacts.artifact_publication(store):
            writer.publish_pending()
    assert writer._pending == pending and writer.snapshot() == manifest
    assert writer._pending_bytes == sum(map(len, pending.values()))
    assert artifacts._artifact_publication.get() is None
    monkeypatch.setattr(module, "save_artifact", original)
    with artifacts.artifact_publication(store):
        writer.publish_pending()
    assert writer._pending == {} and writer._pending_bytes == 0
    assert writer.snapshot() == manifest
    for key in writer.entries:
        assert writer.read(key)["evaluation_id"] == key


def test_restore_does_not_trust_a_manifest_with_unpublished_pending_shards(store):
    writer, evidence, binding, report, context = configured(store)
    retain(writer, report, context)
    with pytest.raises(ValueError, match="missing or damaged"):
        module.DevelopmentEvidenceWriter(
            store, evidence, binding=binding, max_evaluations=32,
            stage_publication=True, saved=writer.snapshot()
        )


def test_flush_requires_same_store_live_owner_scope(store, tmp_path):
    writer, _, _, report, context = configured(store)
    retain(writer, report, context)
    other = ResearchStore(tmp_path / "other")
    other.initialize()
    try:
        with artifacts.artifact_publication(other):
            with pytest.raises(ValueError, match="different stores"):
                writer.publish_pending()
        with artifacts.artifact_publication(store):
            copied = copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                with pytest.raises(ValueError, match="owning context"):
                    executor.submit(copied.run, writer.publish_pending).result(10)
        assert len(writer._pending) == 1
    finally:
        other.close()


def test_changed_existing_evaluation_cannot_replace_pending_evidence(store):
    writer, _, _, report, context = configured(store)
    retain(writer, report, context)
    pending, manifest = deepcopy(writer._pending), writer.snapshot()
    changed = deepcopy(report)
    changed["equity_curve"][-1]["equity"] += 1
    changed["equity_curve"][-1]["cash"] += 1
    with pytest.raises(ValueError, match="evaluation changed"):
        retain(writer, changed, context)
    assert writer._pending == pending and writer.snapshot() == manifest


def test_complete_manifest_cannot_reference_pending_shards(store):
    writer, _, _, report, context = configured(store)
    retain(writer, report, context)
    with pytest.raises(ValueError, match="must be published"):
        writer.snapshot(complete=True)
    with artifacts.artifact_publication(store):
        writer.publish_pending()
    assert writer.snapshot(complete=True)["status"] == "complete"
