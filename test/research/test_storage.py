import gzip
import hashlib
import json
import os
import tempfile
import time
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text

from database.research_db import (
    ResearchExperiment,
    ResearchJob,
    ResearchSource,
    ResearchStore,
    ResearchWorker,
)
from services.research_storage import (
    backup_store,
    inspect_storage,
    maintenance,
    prune_orphans,
    restore_store,
)
from services.scanner_research_service import (
    artifact_dependencies,
    encoded,
    read_artifact,
    save_artifact,
)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ResearchStore(self.root / "store")
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    @contextmanager
    def windows_deep_parent(self, minimum_length):
        # Prefix the owned temporary root too, so cleanup can remove deep files
        # even when Windows' global long-path option is disabled.
        with tempfile.TemporaryDirectory(dir="\\\\?\\" + str(self.root)) as temporary:
            parent = Path(temporary)
            while len(str(parent)[4:]) < minimum_length:
                parent /= "maintenance-directory-" + "x" * 20
            parent.mkdir(parents=True)
            yield Path(str(parent)[4:]), parent

    @unittest.skipUnless(os.name == "nt", "Windows extended-length filesystem paths")
    def test_backup_restore_deep_windows_paths_preserve_evidence_and_database(self):
        import sqlite3

        references = self.populate(legacy=True)
        for minimum_length in (185, 290):
            with (
                self.subTest(minimum_length=minimum_length),
                self.windows_deep_parent(minimum_length) as (parent, io_parent),
            ):
                backup = parent / "backup"
                result = backup_store(self.store, backup)
                self.assertEqual(result["destination"], str(backup))
                manifest = json.loads((io_parent / "backup" / "manifest.json").read_bytes())
                self.assertTrue(all("\\\\?\\" not in name for name in manifest["files"]))
                target = parent / "restored"
                receipt = restore_store(backup, target)
                self.assertEqual(receipt["destination"], str(target))
                restored = SimpleNamespace(root=io_parent / "restored")
                for digest in references:
                    self.assertEqual(
                        read_artifact(restored, digest), read_artifact(self.store, digest)
                    )
                with closing(sqlite3.connect(str(restored.root / "research.db"))) as db:
                    self.assertEqual(
                        db.execute("SELECT id FROM research_jobs").fetchall(), [("job",)]
                    )
                    self.assertEqual(
                        db.execute("SELECT token, heartbeat FROM research_worker").fetchall(),
                        [(None, 0)],
                    )
                self.assertFalse(list(io_parent.glob(".research-maintenance-*")))

    @unittest.skipUnless(os.name == "nt", "Windows extended-length filesystem paths")
    def test_deep_windows_copy_failure_cleans_staging_without_publishing(self):
        from services.research_storage import _copy

        self.populate()
        with self.windows_deep_parent(290) as (parent, io_parent):
            (io_parent / "backup").mkdir()

            def fail_after_copy(*args):
                _copy(*args)
                raise OSError("Forced failure after copying an artifact")

            with patch("services.research_storage._copy", side_effect=fail_after_copy):
                with self.assertRaisesRegex(OSError, "Forced failure"):
                    backup_store(self.store, parent / "backup")
            self.assertEqual(list((io_parent / "backup").iterdir()), [])
            self.assertFalse(list(io_parent.glob(".research-maintenance-*")))
            with self.store.sessions() as db:
                self.assertIsNone(db.get(ResearchWorker, 1).token)

            backup_store(self.store, parent / "backup")
            with patch("services.research_storage._copy", side_effect=fail_after_copy):
                with self.assertRaisesRegex(OSError, "Forced failure"):
                    restore_store(parent / "backup", parent / "restored")
            self.assertFalse((io_parent / "restored").exists())
            self.assertFalse(list(io_parent.glob(".research-maintenance-*")))

    @unittest.skipUnless(os.name == "nt", "Windows extended-length filesystem paths")
    def test_extended_windows_alias_cannot_bypass_source_containment(self):
        self.populate()
        nested = Path("\\\\?\\" + str(self.store.root / "nested"))
        with self.assertRaisesRegex(ValueError, "outside the source store"):
            backup_store(self.store, nested)
        self.assertFalse(nested.exists())
        backup = self.root / "backup"
        backup_store(self.store, backup)
        with self.assertRaisesRegex(ValueError, "outside the source store"):
            restore_store(backup, Path("\\\\?\\" + str(backup / "nested")))
        self.assertFalse((backup / "nested").exists())

    def populate(self, legacy=False):
        inputs = save_artifact(self.store, {"signals": [{"symbol": "AAA", "date": "2026-01-05"}]})
        if legacy:
            path = self.store.root / "artifacts" / f"{inputs}.json"
            path.write_bytes(encoded(read_artifact(self.store, inputs)))
            path.with_suffix(".json.gz").unlink()
        result = save_artifact(self.store, {"inputs_artifact": inputs, "result": {"pnl": 12}})
        shared = save_artifact(self.store, {"shared": "checkpoint-only input"})
        checkpoint = save_artifact(self.store, {"nested": [{"inputs_artifact": shared}], "done": 7})
        with self.store.sessions.begin() as db:
            db.add(ResearchSource(id="source", owner="owner", artifact=inputs, created_at=1))
            db.add(
                ResearchJob(
                    id="job",
                    owner="owner",
                    source_id="source",
                    config="{}",
                    status="completed",
                    progress=100,
                    created_at=1,
                    updated_at=1,
                    result_artifact=result,
                )
            )
            db.add(
                ResearchExperiment(
                    job_id="job",
                    kind="optimize",
                    specification="{}",
                    identity="identity",
                    checkpoint=checkpoint,
                    counts="{}",
                )
            )
        return inputs, result, shared, checkpoint

    def test_backup_restore_closure_shared_inputs_checkpoint_legacy_json(self):
        references = self.populate(legacy=True)
        orphan = save_artifact(self.store, {"orphan": True})
        result = backup_store(self.store, self.root / "backup")
        self.assertEqual(result["referenced_artifacts"], 4)
        self.assertFalse((self.root / "backup" / "artifacts" / f"{orphan}.json.gz").exists())
        restored = restore_store(self.root / "backup", self.root / "restored")
        self.assertEqual(restored["referenced_artifacts"], 4)
        target = ResearchStore(self.root / "restored")
        try:
            for digest in references:
                self.assertEqual(read_artifact(target, digest), read_artifact(self.store, digest))
            with target.sessions() as db:
                self.assertIsNone(db.get(ResearchWorker, 1).token)
                self.assertEqual(db.get(ResearchJob, "job").status, "completed")
        finally:
            target.close()

    def test_populated_old_schema_without_new_experiment_tables(self):
        self.populate()
        with self.store.engine.begin() as db:
            for table in ("research_experiments", "research_requests", "research_history"):
                db.exec_driver_sql(f"DROP TABLE {table}")
        backup_store(self.store, self.root / "old-backup")
        restore_store(self.root / "old-backup", self.root / "old-restored")
        target = ResearchStore(self.root / "old-restored")
        try:
            target.initialize()  # Idempotent migration adds new tables after restoration.
            with target.sessions() as db:
                self.assertEqual(db.get(ResearchSource, "source").owner, "owner")
                self.assertIsNotNone(db.get(ResearchJob, "job").result_artifact)
        finally:
            target.close()

    def test_dry_run_and_apply_only_old_orphans(self):
        references = self.populate()
        old = save_artifact(self.store, {"old": True})
        young = save_artifact(self.store, {"young": True})
        path = self.store.root / "artifacts" / f"{old}.json.gz"
        os.utime(path, (time.time() - 7200, time.time() - 7200))
        unknown = self.store.root / "artifacts" / "unmanaged.tmp"
        unknown.write_text("preserve", encoding="utf-8")
        self.assertEqual(inspect_storage(self.store)["unmanaged_bytes"], len(b"preserve"))
        dry = prune_orphans(self.store)
        self.assertEqual((dry["candidate_files"], dry["deleted_files"]), (1, 0))
        self.assertTrue(path.exists())
        applied = prune_orphans(self.store, apply=True)
        self.assertEqual(applied["deleted_files"], 1)
        self.assertFalse(path.exists())
        self.assertTrue(unknown.exists())
        self.assertIsNotNone(read_artifact(self.store, young))
        for digest in references:
            self.assertIsNotNone(read_artifact(self.store, digest))
        with self.assertRaises(ValueError):
            prune_orphans(self.store, apply=True, min_age_seconds=1)

    def test_live_worker_and_all_active_job_statuses_refused(self):
        self.populate()
        with self.store.sessions.begin() as db:
            worker = db.get(ResearchWorker, 1)
            worker.token, worker.heartbeat = "worker", time.time()
        with self.assertRaisesRegex(ValueError, "lease is active"):
            backup_store(self.store, self.root / "blocked")
        with self.store.sessions.begin() as db:
            db.get(ResearchWorker, 1).heartbeat = 0
        for status in ("queued", "running", "cancelling"):
            with self.store.sessions.begin() as db:
                db.get(ResearchJob, "job").status = status
            with self.assertRaisesRegex(ValueError, "jobs"):
                prune_orphans(self.store)

    def test_maintenance_fenced_reentrant_and_exception_release(self):
        with self.assertRaisesRegex(RuntimeError, "test"):
            with maintenance(self.store) as refresh:
                refresh(force=True)
                with self.store.sessions() as db:
                    self.assertTrue(db.get(ResearchWorker, 1).token.startswith("maintenance:"))
                with self.assertRaises(ValueError):
                    with maintenance(self.store):
                        self.fail("Second maintenance must be refused")
                raise RuntimeError("test")
        with self.store.sessions() as db:
            self.assertIsNone(db.get(ResearchWorker, 1).token)

    def test_corrupted_backup_and_canonical_artifact_rejected(self):
        self.populate()
        backup = self.root / "backup"
        backup_store(self.store, backup)
        artifact = next((backup / "artifacts").iterdir())
        artifact.write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "integrity"):
            restore_store(backup, self.root / "restore")
        self.assertFalse((self.root / "restore").exists())
        # Updating unsigned manifest hashes cannot hide incorrect canonical content.
        manifest = json.loads((backup / "manifest.json").read_bytes())
        key = artifact.relative_to(backup).as_posix()
        manifest["files"][key] = {"bytes": 9, "sha256": hashlib.sha256(b"corrupted").hexdigest()}
        (backup / "manifest.json").write_bytes(encoded(manifest))
        with self.assertRaises((ValueError, OSError)):
            restore_store(backup, self.root / "restore")
        self.assertFalse((self.root / "restore").exists())

    def test_path_traversal_and_nonempty_destination_refused(self):
        self.populate()
        backup = self.root / "backup"
        backup_store(self.store, backup)
        destination = self.root / "existing"
        destination.mkdir()
        (destination / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            restore_store(backup, destination)
        manifest = json.loads((backup / "manifest.json").read_bytes())
        manifest["files"]["../escape.json"] = {"bytes": 0, "sha256": "bad"}
        (backup / "manifest.json").write_bytes(encoded(manifest))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            restore_store(backup, self.root / "bad")
        self.assertEqual((destination / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_missing_metadata_references_fail_closed(self):
        self.populate()
        with self.store.engine.begin() as db:
            db.execute(text("UPDATE research_jobs SET source_id='absent'"))
        with self.assertRaisesRegex(ValueError, "missing source"):
            backup_store(self.store, self.root / "broken")
        with self.store.sessions() as db:
            self.assertIsNone(db.get(ResearchWorker, 1).token)
        self.assertFalse((self.root / "broken").exists())

    def test_lost_maintenance_token_cannot_clear_new_owner(self):
        with maintenance(self.store) as refresh:
            with self.store.sessions.begin() as db:
                db.get(ResearchWorker, 1).token = "replacement"
            with self.assertRaisesRegex(ValueError, "lease was lost"):
                refresh(force=True)
        with self.store.sessions() as db:
            self.assertEqual(db.get(ResearchWorker, 1).token, "replacement")

    def test_quota_receipt_and_repeated_resources_released(self):
        self.populate()
        with patch.dict(os.environ, {"RESEARCH_QUOTA_MB": "1"}):
            for _ in range(100):
                receipt = inspect_storage(self.store)
                self.assertEqual(receipt["referenced_artifacts"], 4)
                self.assertEqual(receipt["quota_bytes"], 1024**2)
                self.assertEqual(
                    receipt["remaining_bytes"],
                    1024**2 - receipt["artifact_bytes"] - receipt["database_bytes"],
                )
        self.store.close()
        original = self.store.root / "research.db"
        moved = original.with_suffix(".moved")
        original.rename(moved)
        moved.rename(original)

    def chunked_pair(self):
        common = [
            {"minute": i, "symbol": "AAA", "observation": "stable checked observation " * 12}
            for i in range(48)
        ]
        reference = save_artifact(self.store, {"reference": "independent logical dependency"})
        values = [
            {"bars": {"AAA": common}, "pass": n, "reference_artifact": reference} for n in (1, 2)
        ]
        with patch("services.scanner_research_service.CHUNK_ARTIFACT_BYTES", 512):
            digests = [save_artifact(self.store, value) for value in values]
        dependencies = [artifact_dependencies(self.store, digest) for digest in digests]
        shared = dependencies[0] & dependencies[1]
        self.assertTrue(shared, "Unchanged data should share physical leaf chunks")
        for i, digest in enumerate(digests):
            self.assertEqual(hashlib.sha256(encoded(values[i])).hexdigest(), digest)
            self.assertEqual(read_artifact(self.store, digest), values[i])
        with self.store.sessions.begin() as db:
            for i, digest in enumerate(digests):
                db.add(
                    ResearchSource(id=f"chunk-{i}", owner="owner", artifact=digest, created_at=1)
                )
        return values, digests, dependencies, shared, reference

    def test_chunked_backup_restore_keeps_shared_physical_and_logical_dependencies(self):
        values, roots, dependencies, shared, reference = self.chunked_pair()
        result = backup_store(self.store, self.root / "chunk-backup")
        expected = set(roots) | set().union(*dependencies) | {reference}
        self.assertEqual(result["referenced_artifacts"], len(expected))
        actual = {
            path.name.split(".")[0] for path in (self.root / "chunk-backup" / "artifacts").iterdir()
        }
        self.assertEqual(actual, expected)
        restore_store(self.root / "chunk-backup", self.root / "chunk-restored")
        target = ResearchStore(self.root / "chunk-restored")
        try:
            for value, digest in zip(values, roots, strict=True):
                self.assertEqual(read_artifact(target, digest), value)
            self.assertEqual(
                artifact_dependencies(target, roots[0]) & artifact_dependencies(target, roots[1]),
                shared,
            )
            self.assertEqual(read_artifact(target, reference), read_artifact(self.store, reference))
        finally:
            target.close()

    def test_pruning_superseded_manifest_preserves_shared_live_chunks(self):
        values, roots, _, shared, reference = self.chunked_pair()
        with self.store.sessions.begin() as db:
            db.delete(db.get(ResearchSource, "chunk-0"))
        for path in (self.store.root / "artifacts").iterdir():
            os.utime(path, (time.time() - 7200, time.time() - 7200))
        dry = prune_orphans(self.store)
        self.assertIn(f"{roots[0]}.json.gz", dry["files"])
        self.assertTrue(all(f"{digest}.json.gz" not in dry["files"] for digest in shared))
        applied = prune_orphans(self.store, apply=True)
        self.assertGreaterEqual(applied["deleted_files"], 1)
        self.assertFalse((self.store.root / "artifacts" / f"{roots[0]}.json.gz").exists())
        self.assertEqual(read_artifact(self.store, roots[1]), values[1])
        self.assertIsNotNone(read_artifact(self.store, reference))
        self.assertEqual(inspect_storage(self.store)["orphan_files"], 0)

    def test_corrupt_chunk_rejected_even_after_unsigned_manifest_hash_is_rewritten(self):
        _, _, _, shared, _ = self.chunked_pair()
        backup = self.root / "chunk-corrupt-backup"
        backup_store(self.store, backup)
        digest = next(iter(shared))
        path = backup / "artifacts" / f"{digest}.json.gz"
        damaged = gzip.compress(encoded({"wrong": "logical chunk"}), mtime=0)
        path.write_bytes(damaged)
        manifest = json.loads((backup / "manifest.json").read_bytes())
        manifest["files"][path.relative_to(backup).as_posix()] = {
            "bytes": len(damaged),
            "sha256": hashlib.sha256(damaged).hexdigest(),
        }
        (backup / "manifest.json").write_bytes(encoded(manifest))
        with self.assertRaises((ValueError, OSError)):
            restore_store(backup, self.root / "chunk-corrupt-restored")
        self.assertFalse((self.root / "chunk-corrupt-restored").exists())

    def test_missing_chunk_blocks_backup_and_releases_maintenance(self):
        _, _, _, shared, _ = self.chunked_pair()
        (self.store.root / "artifacts" / f"{next(iter(shared))}.json.gz").unlink()
        with self.assertRaises((ValueError, OSError)):
            backup_store(self.store, self.root / "missing-chunk-backup")
        self.assertFalse((self.root / "missing-chunk-backup").exists())
        with self.store.sessions() as db:
            self.assertIsNone(db.get(ResearchWorker, 1).token)


if __name__ == "__main__":
    unittest.main()
