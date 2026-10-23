"""Native owned bytes preserve canonical roots, admission and generic mutation."""

# ruff: noqa: F811 -- shared isolated fixtures
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy

import pytest
from test_chunked_artifacts import store  # noqa: F401
from test_jobs import app, client  # noqa: F401

from database.research_db import ResearchExperiment, ResearchJob
from research import checkpoint_bytes as transfer
from research.canonical_json import bounded_canonical_snapshot
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker


def owned(value):
    captured = bounded_canonical_snapshot(value, allow_native_float64=True)
    assert captured is not None
    return transfer._OwnedCanonicalJSON(*captured)


@pytest.mark.parametrize(
    "fields",
    [{}, {"a": 1}, {"z": 2}, {"a": 1, "z": [2, 3]}, {"\u2603": "\U0001f680", "a": '\\"\n\x00'}],
)
@pytest.mark.parametrize("key", ["0", "middle", "zz"])
def test_member_composition_is_byte_exact_for_all_key_positions(fields, key):
    value = {"nested": [1, 2, {"price": -0.0}]}
    payload = owned(value)
    wrapped = payload.wrap(key, fields)
    assert wrapped is not None
    with pytest.raises(ValueError, match="synchronous owner"):
        _ = payload.value
    actual, raw = wrapped.take()
    assert actual == {**fields, key: value}
    assert raw == service.encoded(actual)
    with pytest.raises(ValueError):
        wrapped.take()


def test_checkpoint_id_and_both_native_envelopes_match_original_encoding():
    value = {
        "active_fit": "fit1",
        "attempts": 17,
        "binding": "b" * 64,
        "evaluations": {"entry": {"return": 1.25}},
        "version": "test",
    }
    original = deepcopy(value)
    identity = hashlib.sha256(service.encoded(value)).hexdigest()
    payload = owned(value).with_checkpoint_id(identity)
    state = {**value, "checkpoint_id": identity}
    payload = payload.wrap(
        "calculation",
        {
            "phase": "calculation",
            "inputs_artifact": "i" * 64,
            "development_evidence": {"entries": {}},
        },
    )
    payload = payload.wrap("state", {"identity": "r" * 64, "policy_version": "test"})
    actual, raw = payload.take()
    assert actual["state"]["calculation"] == state
    assert value == original
    assert raw == service.encoded(actual)


def test_owned_limit_and_unusual_siblings_fall_back_without_consuming(monkeypatch):
    payload = owned({"a": "b"})
    monkeypatch.setattr(transfer, "MAX_OWNED_BYTES", 20)
    assert payload.with_checkpoint_id("a" * 64) is None
    assert payload.wrap("large", {"padding": "x" * 100}) is None
    assert payload.value == {"a": "b"}
    monkeypatch.setattr(transfer, "MAX_OWNED_BYTES", 4 * 1024**2)
    assert payload.wrap("a", {"tuple": (1, 2)}) is None
    assert payload.wrap("a", {"a": 2}) is None
    assert payload.with_checkpoint_id("invalid") is None
    payload.close()


def test_large_middle_prefix_uses_original_fallback():
    payload = owned({"baseline_final_report": "x" * 70000, "version": "test"})
    assert payload.with_checkpoint_id("a" * 64) is None
    payload.close()


def test_owned_ceiling_accepts_prior_four_mib_boundary_and_exact_wrap_limit(monkeypatch):
    # Real admission above the former ceiling, without a 32MiB test allocation.
    value = {"padding": "x" * (4 * 1024**2 + 1)}
    captured = bounded_canonical_snapshot(value, max_bytes=transfer.MAX_OWNED_BYTES)
    assert captured is not None
    payload = transfer._OwnedCanonicalJSON(*captured)
    wrapped = payload.wrap("state", {})
    assert wrapped is not None
    actual, raw = wrapped.take()
    assert raw == service.encoded(actual)
    assert transfer.MAX_OWNED_BYTES == 32 * 1024**2
    assert service.MAX_COMPACT_CHECKPOINT_BYTES == 4 * 1024**2
    small = {"a": 1}
    payload = owned(small)
    exact = len(service.encoded({"state": small}))
    monkeypatch.setattr(transfer, "MAX_OWNED_BYTES", exact - 1)
    assert payload.wrap("state", {}) is None
    monkeypatch.setattr(transfer, "MAX_OWNED_BYTES", exact)
    wrapped = payload.wrap("state", {})
    assert wrapped is not None
    assert len(wrapped.take()[1]) == exact


def test_expired_and_foreign_thread_payloads_cannot_publish():
    payload = owned({"a": 1})
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValueError, match="synchronous owner"):
            pool.submit(payload.take).result()
    assert payload.value == {"a": 1}
    payload.close()
    with pytest.raises(ValueError):
        payload.wrap("state", {})


def test_sink_is_explicit_and_releases_payload_on_error():
    generic = []

    def callback(value, counts):
        value["mutated"] = True
        generic.append(value)

    def native(payload, counts, publish):
        assert payload.value == {"a": 1}
        raise RuntimeError("native cancellation")

    sink = transfer._OwnedCheckpointSink(callback, native)
    mutable = {"a": 1}
    sink(mutable, {})
    assert generic == [{"a": 1, "mutated": True}]
    payload = owned({"a": 1})
    with pytest.raises(RuntimeError, match="native cancellation"):
        sink.publish_owned(payload, {})
    with pytest.raises(ValueError):
        _ = payload.value


@pytest.mark.parametrize("compact", [False, True])
def test_owned_artifact_reuses_only_root_encoding_with_full_shadow_checks(
    store, monkeypatch, compact
):
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    value = {"state": [{"account": i, "values": list(range(i, i + 20))} for i in range(25)]}
    payload = owned(value)
    original = service.encoded
    calls = []

    def encoded(item):
        calls.append(original(item))
        return original(item)

    monkeypatch.setattr(service, "encoded", encoded)
    with service.compact_checkpoint_publication() if compact else nullcontext():
        identity = service._save_owned_artifact(store, payload)
    assert identity == hashlib.sha256(original(value)).hexdigest()
    assert original(value) not in calls
    assert calls, "all shadow child/manifest encoding must remain"
    assert service.read_artifact(store, identity) == value
    dependencies = service.artifact_dependencies(store, identity)
    assert (dependencies == set()) if compact else bool(dependencies)
    with pytest.raises(ValueError):
        payload.take()


def test_owned_noncompact_manifest_and_children_match_original_writer(store, tmp_path, monkeypatch):
    from database.research_db import ResearchStore

    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    other = ResearchStore(tmp_path / "ordinary")
    other.initialize()
    value = {"state": [{"account": i, "values": list(range(20))} for i in range(25)]}
    try:
        original = service.save_artifact(other, value)
        actual = service._save_owned_artifact(store, owned(value))
        assert actual == original
        assert service.artifact_dependencies(store, actual) == service.artifact_dependencies(
            other, original
        )
        actual_files = {p.name: p.read_bytes() for p in (store.root / "artifacts").iterdir()}
        expected_files = {p.name: p.read_bytes() for p in (other.root / "artifacts").iterdir()}
        assert actual_files == expected_files
        changed = deepcopy(value)
        changed["state"][0]["values"][0] = 99
        changed_id = service._save_owned_artifact(store, owned(changed))
        assert changed_id != actual
        assert service.read_artifact(store, changed_id) == changed
        assert service.read_artifact(store, actual) == value
    finally:
        other.close()


@pytest.mark.parametrize("guard", ["nodes", "bytes", "quota"])
def test_owned_artifact_keeps_existing_admission_guards(store, monkeypatch, guard):
    value = {"state": [{"account": i, "values": list(range(20))} for i in range(25)]}
    payload = owned(value)
    monkeypatch.setattr(service, "CHUNK_ARTIFACT_BYTES", 128)
    if guard == "nodes":
        monkeypatch.setattr(service, "MAX_ARTIFACT_NODES", 1)
    elif guard == "bytes":
        monkeypatch.setattr(service, "MAX_ARTIFACT_BYTES", 1)
    else:
        monkeypatch.setenv("RESEARCH_QUOTA_MB", "0")
    with service.compact_checkpoint_publication(), pytest.raises(ValueError):
        service._save_owned_artifact(store, payload)
    assert service._artifact_publication.get() is None
    with pytest.raises(ValueError):
        payload.take()


@pytest.mark.parametrize("generic_mutation", [False, True])
def test_native_worker_transfer_and_generic_save_mutation(
    app, client, monkeypatch, generic_mutation
):
    from test_checkpoint_publication_fences import run, submit

    store, job_id = submit(app, client)
    snapshots, writes = [], []
    original_owned = worker._save_owned_artifact
    original_save = worker.save_artifact

    def save_owned(selected, payload):
        writes.append("owned")
        return original_owned(selected, payload)

    def save_generic(selected, value):
        writes.append("generic")
        value["state"]["phase"] = "mutated by generic callback"
        return original_save(selected, value)

    def calculate(_store, _owner, _evidence, _spec, **callbacks):
        sink = callbacks["checkpoint"]
        assert type(sink) is transfer._OwnedCheckpointSink
        payload = owned({"phase": "calculation", "calculation": {"attempts": 1}})
        snapshots.append(payload)
        sink.publish_owned(payload, {"completed": 37, "total": 100})
        raise worker.Interrupted("fixture complete")

    monkeypatch.setattr(worker, "_save_owned_artifact", save_owned)
    if generic_mutation:
        monkeypatch.setattr(worker, "save_artifact", save_generic)
    monkeypatch.setattr(research_portfolio, "run", calculate)
    run(store)
    assert writes == ["generic" if generic_mutation else "owned"]
    with store.sessions() as db:
        artifact = db.get(ResearchExperiment, job_id).checkpoint
        assert db.get(ResearchJob, job_id).progress == 37
    restored = service.read_artifact(store, artifact)
    assert restored["state"]["phase"] == (
        "mutated by generic callback" if generic_mutation else "calculation"
    )
    with pytest.raises(ValueError):
        _ = snapshots[0].value
    assert service._artifact_publication.get() is None
    assert service._compact_checkpoint_publication.get() is None


@pytest.mark.parametrize("native", [False, True])
def test_controller_first_snapshot_keeps_exact_scientific_identity(monkeypatch, native):
    from test_expanded_walk_forward import synthetic_prepare_method_gate
    from test_turnover_integration import prepared

    from research import automatic_walk_forward as controller

    value = prepared()
    received = []

    class Finished(Exception):
        pass

    def callback(snapshot, counts):
        received.append((snapshot, service.encoded(snapshot)))
        snapshot["generic_mutation"] = True
        raise Finished

    def native_callback(payload, counts, publish):
        received.append(payload.take())
        raise Finished

    sink = transfer._OwnedCheckpointSink(callback, native_callback) if native else callback
    monkeypatch.setattr(controller, "prepare_method_gate", synthetic_prepare_method_gate)
    with pytest.raises(Finished):
        controller.run(
            value,
            evaluate=lambda *args: pytest.fail("unexpected engine call"),
            checkpoint=sink,
            progress=lambda *args: None,
            cancelled=lambda: None,
        )
    snapshot, raw = received[0]
    snapshot.pop("generic_mutation", None)
    assert raw == service.encoded(snapshot)
    identity = snapshot.pop("checkpoint_id")
    assert identity == hashlib.sha256(service.encoded(snapshot)).hexdigest()
