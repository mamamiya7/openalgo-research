"""Offline maintenance for local research evidence; never operates trading data."""

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import URL

from database.engine_factory import create_db_engine
from database.research_db import ResearchJob, ResearchWorker
from services.scanner_research_service import (
    ACTIVE,
    artifact_dependencies,
    encoded,
    managed_storage_bytes,
    read_artifact,
)

LEASE_SECONDS = 120
MAX_FILES = 100000
MAX_BACKUP_BYTES = 16 * 1024**3
ARTIFACT_NAME = re.compile(r"([a-f0-9]{64})\.json(?:\.gz)?")


@contextmanager
def maintenance(store):
    """Reserve the worker slot atomically; refuse active jobs or a live lease."""
    _safe_file(store.root / "research.db", store.root)
    token = f"maintenance:{uuid.uuid4().hex}"
    now = time.time()
    with store.sessions.begin() as db:
        db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
        worker = db.get(ResearchWorker, 1)
        if worker is None:
            raise ValueError(
                "Research worker metadata is missing; initialize the research store first"
            )
        if worker.token and worker.heartbeat > now - LEASE_SECONDS:
            raise ValueError(
                "Research worker or maintenance lease is active; stop services and wait for lease expiry"
            )
        active = db.scalar(
            select(func.count()).select_from(ResearchJob).where(ResearchJob.status.in_(ACTIVE))
        )
        if active:
            raise ValueError(
                "Research has queued, running or cancelling jobs; finish or cancel them first"
            )
        worker.token, worker.heartbeat = token, now
    last_refresh = now

    def refresh(force=False):
        nonlocal last_refresh
        now = time.time()
        if force or now - last_refresh >= 30:
            with store.sessions.begin() as db:
                updated = db.execute(
                    update(ResearchWorker)
                    .where(ResearchWorker.id == 1, ResearchWorker.token == token)
                    .values(heartbeat=now)
                )
                if updated.rowcount != 1:
                    raise ValueError("Research maintenance lease was lost")
            last_refresh = now

    try:
        yield refresh
    finally:
        with store.sessions.begin() as db:
            db.execute(
                update(ResearchWorker)
                .where(ResearchWorker.id == 1, ResearchWorker.token == token)
                .values(token=None, heartbeat=0)
            )


def _rows(connection, table, columns):
    # All names are fixed internal literals, never manifest or user SQL.
    existing = (
        connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        .scalars()
        .all()
    )
    if table not in existing:
        return []
    rows = connection.exec_driver_sql(
        f"SELECT {','.join(columns)} FROM {table} LIMIT {MAX_FILES + 1}"
    ).all()
    if len(rows) > MAX_FILES:
        raise ValueError("Metadata exceeds the 100000-row maintenance bound")
    return rows


def _references(engine):
    """Validate cross-table identity while supporting populated milestone schemas."""
    with engine.connect() as db:
        tables = set(
            db.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").scalars()
        )
        if not {"research_sources", "research_jobs", "research_worker"}.issubset(tables):
            raise ValueError("Required research metadata tables are missing")
        if db.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
            raise ValueError("Research metadata integrity check failed")
        sources = _rows(db, "research_sources", ("id", "artifact"))
        jobs = _rows(db, "research_jobs", ("id", "source_id", "result_artifact"))
        experiments = _rows(db, "research_experiments", ("job_id", "checkpoint", "parent_job_id"))
        requests = _rows(db, "research_requests", ("job_id",))
        history = _rows(db, "research_history", ("job_id",))
        attempts = _rows(db, "research_attempts", ("job_id", "previous_job_id", "root_job_id"))
        library_roots = _library_references(db, tables)
    source_ids, job_ids = {row[0] for row in sources}, {row[0] for row in jobs}
    if any(row[1] not in source_ids for row in jobs):
        raise ValueError("Research job references a missing source")
    if any(row[0] not in job_ids or (row[2] and row[2] not in job_ids) for row in experiments):
        raise ValueError("Research experiment references a missing job")
    if any(row[0] not in job_ids for row in requests + history):
        raise ValueError("Research request/history references a missing job")
    if any(identifier not in job_ids for row in attempts for identifier in row):
        raise ValueError("Research attempt references a missing job")
    return (
        {row[1] for row in sources}
        | {row[2] for row in jobs if row[2]}
        | {row[1] for row in experiments if row[1]}
        | library_roots
    )


def _library_references(db, tables):
    """Validate the additive library graph; old stores without it stay supported."""
    expected = {
        "research_library_experiments",
        "research_setup_versions",
        "research_library_jobs",
        "research_library_sources",
        "research_library_requests",
    }
    if not expected.intersection(tables):
        return set()
    if not expected.issubset(tables):
        raise ValueError("Research library metadata tables are incomplete")
    experiments = {
        row[0]: row
        for row in _rows(
            db,
            "research_library_experiments",
            (
                "id",
                "owner",
                "parent_job_id",
                "parent_result_artifact",
                "parent_trial_id",
                "parent_version_id",
            ),
        )
    }
    versions = {
        row[0]: row
        for row in _rows(
            db,
            "research_setup_versions",
            (
                "id",
                "experiment_id",
                "parent_job_id",
                "parent_result_artifact",
                "parent_trial_id",
                "parent_version_id",
            ),
        )
    }
    jobs = {row[0]: row for row in _rows(db, "research_jobs", ("id", "owner", "result_artifact"))}
    sources = {row[0]: row[1] for row in _rows(db, "research_sources", ("id", "owner"))}
    roots = set()

    def container(identifier):
        if identifier not in experiments:
            raise ValueError("Research library reference has a missing experiment")
        return experiments[identifier]

    def job_owner(job_id, owner):
        if job_id not in jobs or jobs[job_id][1] != owner:
            raise ValueError("Research library reference has a missing or foreign job")

    def version_owner(version_id, experiment_id):
        if version_id not in versions or versions[version_id][1] != experiment_id:
            raise ValueError("Research library reference has a missing or foreign setup version")

    def parents(row, experiment_id):
        owner = container(experiment_id)[1]
        parent_job, artifact, trial, version = row[2:]
        if bool(parent_job) != bool(artifact) or (trial and not parent_job):
            raise ValueError("Research library parent evidence is incomplete")
        if parent_job:
            job_owner(parent_job, owner)
            if jobs[parent_job][2] != artifact:
                raise ValueError("Research library parent artifact does not match its result")
            roots.add(artifact)
        if version:
            version_owner(version, experiment_id)

    for identifier, row in experiments.items():
        parents(row, identifier)
    for identifier, row in versions.items():
        container(row[1])
        parents(row, row[1])
        if row[5] == identifier:
            raise ValueError("Research setup version cannot be its own parent")
    links = _rows(db, "research_library_jobs", ("experiment_id", "job_id", "version_id"))
    linked_versions = {(row[0], row[1]): row[2] for row in links}
    for experiment_id, job_id, version_id in links:
        job_owner(job_id, container(experiment_id)[1])
        if version_id:
            version_owner(version_id, experiment_id)
    for experiment_id, source_id, version_id in _rows(
        db, "research_library_sources", ("experiment_id", "source_id", "version_id")
    ):
        if sources.get(source_id) != container(experiment_id)[1]:
            raise ValueError("Research library reference has a missing or foreign source")
        if version_id:
            version_owner(version_id, experiment_id)
    for owner, experiment_id, kind, version_id, job_id in _rows(
        db, "research_library_requests", ("owner", "experiment_id", "kind", "version_id", "job_id")
    ):
        if container(experiment_id)[1] != owner:
            raise ValueError("Research library request belongs to another account")
        if (
            kind not in ("run", "replay", "from_job")
            or not job_id
            or (kind in ("run", "replay") and not version_id)
        ):
            raise ValueError("Research library request has incomplete accepted work")
        job_owner(job_id, owner)
        if version_id:
            version_owner(version_id, experiment_id)
        if (experiment_id, job_id) not in linked_versions or (
            kind in ("run", "replay") and linked_versions[(experiment_id, job_id)] != version_id
        ):
            raise ValueError("Research library request is missing its accepted job link")
    for owner, experiment_id, source_id, parent_id in _rows(
        db,
        "research_chartink_imports",
        ("owner", "experiment_id", "source_id", "parent_experiment_id"),
    ):
        if container(experiment_id)[1] != owner or sources.get(source_id) != owner:
            raise ValueError("Chartink import references missing or foreign research")
        if parent_id and container(parent_id)[1] != owner:
            raise ValueError("Chartink import references a foreign parent experiment")
    return roots


def _safe_file(path, root):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Storage contains an unsafe or missing file")
    return path


def _artifact_path(root, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid artifact reference in metadata or evidence")
    path = root / "artifacts" / f"{digest}.json"
    if not path.exists():
        path = path.with_suffix(".json.gz")
    return _safe_file(path, root)


def _input_references(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ("inputs_artifact", "parent_result_artifact", "reference_artifact"):
                    if not isinstance(child, str) or not re.fullmatch(r"[a-f0-9]{64}", child):
                        raise ValueError("Invalid nested inputs_artifact reference")
                    yield child
                elif isinstance(child, (dict, list)):
                    pending.append(child)
        elif isinstance(item, list):
            pending.extend(child for child in item if isinstance(child, (dict, list)))


def _closure(store, roots, refresh=lambda: None):
    pending, found = set(roots), {}
    while pending:
        refresh()
        digest = pending.pop()
        if digest in found:
            continue
        if len(found) >= MAX_FILES:
            raise ValueError("Artifact closure exceeds the 100000-file bound")
        path = _artifact_path(store.root, digest)
        dependencies = artifact_dependencies(store, digest)
        # Check referenced leaf paths before transparent decoding opens them.
        for reference in dependencies:
            _artifact_path(store.root, reference)
        evidence = read_artifact(store, digest)
        found[digest] = path
        # Transparent decoding hides physical chunk references. Retain them as
        # well as logical cross-artifact inputs for backup, restore and pruning.
        pending.update(ref for ref in dependencies if ref not in found)
        pending.update(ref for ref in _input_references(evidence) if ref not in found)
    return found


def _inventory(store, refresh=lambda: None):
    _safe_file(store.root / "research.db", store.root)
    closure = _closure(store, _references(store.engine), refresh)
    directory = store.root / "artifacts"
    files, unknown = [], []
    if directory.exists():
        if directory.is_symlink():
            raise ValueError("Artifact directory must not be a symlink")
        for path in directory.iterdir():
            if len(files) + len(unknown) >= MAX_FILES:
                raise ValueError("Artifact directory exceeds the maintenance file bound")
            match = ARTIFACT_NAME.fullmatch(path.name)
            if path.is_symlink() or not path.is_file() or not match:
                unknown.append(path.name)
                continue
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "digest": match.group(1),
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "referenced": match.group(1) in closure,
                }
            )
    return closure, files, unknown


def inspect_storage(store):
    """Read-only sampled quota receipt; safe to use while the worker runs."""
    closure, files, unknown = _inventory(store)
    quota = int(os.getenv("RESEARCH_QUOTA_MB", "2048")) * 1024**2
    unmanaged_bytes = sum(
        (store.root / "artifacts" / name).stat().st_size
        for name in unknown
        if (store.root / "artifacts" / name).is_file()
        and not (store.root / "artifacts" / name).is_symlink()
    )
    used = sum(file["bytes"] for file in files) + unmanaged_bytes
    database_bytes = sum(p.stat().st_size for p in store.root.glob("research.db*") if p.is_file())
    total_bytes = managed_storage_bytes(store)
    return {
        "sampled_at": time.time(),
        "consistent_snapshot": False,
        "artifact_files": len(files),
        "referenced_artifacts": len(closure),
        "referenced_bytes": sum(f["bytes"] for f in files if f["referenced"]),
        "orphan_files": sum(not f["referenced"] for f in files),
        "orphan_bytes": sum(f["bytes"] for f in files if not f["referenced"]),
        "artifact_bytes": used,
        "unmanaged_bytes": unmanaged_bytes,
        "database_bytes": database_bytes,
        "acquisition_and_other_bytes": max(0, total_bytes - used - database_bytes),
        "total_bytes": total_bytes,
        "quota_bytes": quota,
        "remaining_bytes": max(0, quota - total_bytes),
        "over_quota": total_bytes > quota,
        "unmanaged_entries": unknown,
        "integrity": "referenced canonical artifact hashes verified",
    }


def prune_orphans(store, *, apply=False, min_age_seconds=3600):
    if min_age_seconds < 3600:
        raise ValueError("Orphan grace period must be at least one hour")
    with maintenance(store) as refresh:
        _, files, unknown = _inventory(store, refresh)
        cutoff = time.time() - min_age_seconds
        candidates = [f for f in files if not f["referenced"] and f["mtime"] <= cutoff]
        if apply:
            for file in candidates:
                refresh()
                path = _safe_file(store.root / "artifacts" / file["name"], store.root)
                # Refuse replacing a selected file between inventory and deletion.
                stat = path.stat()
                if stat.st_mtime != file["mtime"] or stat.st_size != file["bytes"]:
                    raise ValueError("Orphan changed during maintenance; rerun inspection")
                path.unlink()
        return {
            "applied": apply,
            "candidate_files": len(candidates),
            "candidate_bytes": sum(f["bytes"] for f in candidates),
            "files": [f["name"] for f in candidates],
            "unmanaged_entries": unknown,
            "grace_seconds": min_age_seconds,
            "deleted_files": len(candidates) if apply else 0,
        }


def _hash(path, refresh=lambda: None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            refresh()
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source, destination, refresh=lambda: None):
    """Bounded copy with lease renewal even on slow filesystems."""
    with source.open("rb") as reader, destination.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1024**2), b""):
            refresh()
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())


@contextmanager
def _destination(destination, forbidden=None):
    target = _maintenance_path(destination)
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("Destination must be a new or empty directory")
    if forbidden and target.resolve().is_relative_to(_maintenance_path(forbidden).resolve()):
        raise ValueError("Destination must be outside the source store")
    if not target.parent.is_dir():
        raise ValueError("Destination parent must already exist")
    with tempfile.TemporaryDirectory(
        prefix=".research-maintenance-", dir=target.parent
    ) as temporary:
        staging = Path(temporary) / "output"
        staging.mkdir()
        yield staging
        if target.exists():
            target.rmdir()  # Empty-only operation; never deletes populated output.
        staging.rename(target)


def _maintenance_path(path):
    """Use the Windows extended namespace for I/O, retaining symlinks for checks.

    Normalize absolute segments before prefixing: extended Windows paths do not
    interpret dot segments. Use the same namespace for containment comparisons
    and the temporary root so its context manager can clean deep files on error.
    Public receipts and manifest-relative names retain their ordinary spelling.
    """
    if os.name != "nt":
        return Path(path).absolute()
    absolute = Path(os.path.abspath(path))
    name = str(absolute)
    if name.startswith("\\\\?\\"):
        return absolute
    if name.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + name[2:])
    return Path("\\\\?\\" + name)


def _maintenance_engine(path):
    # A structured URL keeps the '?' in a Windows extended path out of the URL
    # query string. The shared factory still provides SQLite's required NullPool.
    return create_db_engine(URL.create("sqlite", database=str(path)))


def _database_copy(source_engine, path, refresh):
    target_engine = _maintenance_engine(path)
    source, target = None, None
    try:
        source = source_engine.raw_connection()
        target = target_engine.raw_connection()
        source.driver_connection.backup(
            target.driver_connection, pages=256, progress=lambda *_: refresh()
        )
        target.close()
        target = None
        with target_engine.begin() as db:
            db.execute(text("UPDATE research_worker SET token=NULL, heartbeat=0 WHERE id=1"))
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
        target_engine.dispose()


def backup_store(store, destination):
    with maintenance(store) as refresh, _destination(destination, forbidden=store.root) as staging:
        _database_copy(store.engine, staging / "research.db", refresh)
        engine = _maintenance_engine(staging / "research.db")
        try:
            roots = _references(engine)
        finally:
            engine.dispose()
        closure = _closure(store, roots, refresh)
        if len(closure) + 1 > MAX_FILES:
            raise ValueError("Backup exceeds the 100000-file maintenance bound")
        if (staging / "research.db").stat().st_size + sum(
            path.stat().st_size for path in closure.values()
        ) > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds the 16 GiB maintenance bound")
        (staging / "artifacts").mkdir()
        for path in closure.values():
            refresh()
            _copy(path, staging / "artifacts" / path.name, refresh)
        _closure(SimpleNamespace(root=staging), roots, refresh)
        paths = [staging / "research.db", *sorted((staging / "artifacts").iterdir())]
        entries = {
            p.relative_to(staging).as_posix(): {
                "bytes": p.stat().st_size,
                "sha256": _hash(p, refresh),
            }
            for p in paths
        }
        if sum(item["bytes"] for item in entries.values()) > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds the 16 GiB maintenance bound")
        manifest = {
            "format": "openalgo-research-backup-v1",
            "created_at": time.time(),
            "integrity": "SHA256 integrity only; not signed or encrypted",
            "files": entries,
            "referenced_artifacts": len(closure),
        }
        (staging / "manifest.json").write_bytes(encoded(manifest))
        refresh(force=True)
    return {
        "destination": str(Path(destination).absolute()),
        "files": len(entries),
        "bytes": sum(item["bytes"] for item in entries.values()),
        "referenced_artifacts": len(closure),
    }


def restore_store(backup_directory, destination):
    source = _maintenance_path(backup_directory).resolve()
    manifest_path = _safe_file(source / "manifest.json", source)
    if manifest_path.stat().st_size > 32 * 1024**2:
        raise ValueError("Backup manifest exceeds 32 MiB")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("format") != "openalgo-research-backup-v1":
        raise ValueError("Unsupported backup manifest")
    entries = manifest.get("files", {})
    if (
        not isinstance(entries, dict)
        or not 1 <= len(entries) <= MAX_FILES
        or "research.db" not in entries
    ):
        raise ValueError("Invalid backup file manifest")
    total = 0
    for name, receipt in entries.items():
        if name != "research.db" and not re.fullmatch(
            r"artifacts/[a-f0-9]{64}\.json(?:\.gz)?", name
        ):
            raise ValueError("Unsafe backup manifest path")
        path = _safe_file(source / name, source)
        size = path.stat().st_size
        total += size
        if total > MAX_BACKUP_BYTES:
            raise ValueError("Backup exceeds 16 GiB")
        if (
            not isinstance(receipt, dict)
            or size != receipt.get("bytes")
            or _hash(path) != receipt.get("sha256")
        ):
            raise ValueError("Backup file integrity check failed")
    # Never restore unlisted files, symlinks, arbitrary directories or SQLite sidecars.
    with _destination(destination, forbidden=source) as staging:
        for name in entries:
            path = staging / name
            path.parent.mkdir(exist_ok=True)
            _copy(source / name, path)
            if _hash(path) != entries[name]["sha256"]:
                raise ValueError("Backup changed while restoring")
        engine = _maintenance_engine(staging / "research.db")
        try:
            roots = _references(engine)
            closure = _closure(SimpleNamespace(root=staging), roots)
            listed = {
                name.split("/")[1].split(".")[0]
                for name in entries
                if name.startswith("artifacts/")
            }
            if listed != set(closure):
                raise ValueError("Backup artifact list does not match the referenced closure")
            with engine.begin() as db:
                if db.exec_driver_sql(
                    "SELECT COUNT(*) FROM research_jobs WHERE status IN ('queued','running','cancelling')"
                ).scalar():
                    raise ValueError("Backup contains unfinished jobs")
                db.execute(text("UPDATE research_worker SET token=NULL, heartbeat=0 WHERE id=1"))
        finally:
            engine.dispose()
    return {
        "destination": str(Path(destination).absolute()),
        "files": len(entries),
        "bytes": total,
        "referenced_artifacts": len(closure),
        "integrity": "hashes and metadata/artifact references verified; signature not available",
    }
