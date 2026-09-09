"""Content-addressed evidence and bounded, persisted calculation admission."""

import base64
import gzip
import hashlib
import json
import os
import re
import tempfile
import time
import uuid

from sqlalchemy import func, or_, select, update

from database.research_db import (
    ResearchAttempt,
    ResearchExperiment,
    ResearchJob,
    ResearchRequest,
    ResearchSource,
    ResearchSourceReceipt,
    ResearchWorker,
)

MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
CHUNK_ARTIFACT_BYTES = 256 * 1024
MAX_ARTIFACT_NODES = 100000
CHUNK_FORMAT = "research-chunks-v1"
ACTIVE = ("queued", "running", "cancelling")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_guard(db):
    db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
    lease = db.get(ResearchWorker, 1)
    if lease and (lease.token or "").startswith("maintenance:"):
        raise ValueError(
            "Research storage is in maintenance; retry after backup or cleanup finishes"
        )


def managed_storage_bytes(store):
    # DirEntry reuses directory metadata on Windows. Repeated Path.stat calls
    # made checkpoint admission increasingly expensive as saved work accumulated.
    pending, used, count = [store.root], 0, 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if count > 100000:
                    raise ValueError("Research storage exceeds the 100000-entry management bound")
                if entry.is_symlink():
                    raise ValueError("Research storage must not contain symlinks")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    used += entry.stat(follow_symlinks=False).st_size
    return used


def ensure_storage_capacity(store, additional=0):
    quota = int(os.getenv("RESEARCH_QUOTA_MB", "2048")) * 1024 * 1024
    used = managed_storage_bytes(store)
    if used + additional > quota:
        raise ValueError("Research storage is full. Your saved progress is safe.")


def _write_artifact_bytes(store, digest, raw):
    """Publish one immutable physical file; children precede their manifest."""
    directory = store.root / "artifacts"
    directory.mkdir(exist_ok=True)
    target = directory / f"{digest}.json.gz"
    if not target.exists() and not (directory / f"{digest}.json").exists():
        compressed = gzip.compress(raw, compresslevel=3, mtime=0)
        if len(compressed) > MAX_COMPRESSED_BYTES:
            raise ValueError("Evidence exceeds the 32 MiB compressed research limit")
        with store.sessions.begin() as db:
            write_guard(db)
            ensure_storage_capacity(store, len(compressed))
            fd, name = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(compressed)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
    return digest


def save_artifact(store, value):
    raw = encoded(value)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("Evidence exceeds the decoded research size limit")
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) <= CHUNK_ARTIFACT_BYTES or not isinstance(value, (dict, list)):
        return _write_artifact_bytes(store, digest, raw)
    directory = store.root / "artifacts"
    if any((directory / f"{digest}{suffix}").exists() for suffix in (".json", ".json.gz")):
        return digest
    decoded_bytes = len(raw)
    del raw
    nodes = 0

    def node(item, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > MAX_ARTIFACT_NODES:
            raise ValueError("Research evidence exceeds the chunk structure limit")
        part = encoded(item)
        if len(part) <= min(4096, max(1, CHUNK_ARTIFACT_BYTES // 4)):
            return {"value": item}
        if len(part) <= CHUNK_ARTIFACT_BYTES or not isinstance(item, (dict, list)):
            child = hashlib.sha256(part).hexdigest()
            _write_artifact_bytes(store, child, part)
            return {"artifact": child}
        average = max(1, len(part) // max(1, len(item)))
        target_count = min(512, max(1, CHUNK_ARTIFACT_BYTES // average), len(item) - 1)
        width = 1 << max(0, target_count.bit_length() - 1)
        del part
        # Fixed groups preserve earlier chunks as later observations arrive.
        # Small dictionaries split at semantic keys (e.g. bars per symbol).
        if isinstance(item, dict):
            keys = sorted(item)
            if width > 1:
                return {
                    "dicts": [
                        node({k: item[k] for k in keys[i : i + width]}, depth + 1)
                        for i in range(0, len(keys), width)
                    ]
                }
            return {"object": {key: node(item[key], depth + 1) for key in keys}}
        if width > 1:
            return {
                "lists": [node(item[i : i + width], depth + 1) for i in range(0, len(item), width)]
            }
        return {"array": [node(child, depth + 1) for child in item]}

    manifest = encoded(
        {
            "format": CHUNK_FORMAT,
            "logical_sha256": digest,
            "decoded_bytes": decoded_bytes,
            "tree": node(value, 1),
        }
    )
    return _write_artifact_bytes(store, digest, manifest)


def _artifact_content(store, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid evidence identity")
    path = store.root / "artifacts" / f"{digest}.json"
    if not path.exists():
        path = path.with_suffix(".json.gz")
    if path.is_symlink() or not path.resolve().is_relative_to(store.root.resolve()):
        raise ValueError("Unsafe research artifact path")
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as stream:
            raw = stream.read(MAX_ARTIFACT_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise ValueError("Saved evidence is missing or damaged") from exc
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("Saved evidence integrity check failed")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Saved evidence integrity check failed") from exc
    if hashlib.sha256(raw).hexdigest() == digest:
        return value, None, len(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "logical_sha256", "decoded_bytes", "tree"}
        or value["format"] != CHUNK_FORMAT
        or value["logical_sha256"] != digest
        or type(value["decoded_bytes"]) is not int
        or not 1 <= value["decoded_bytes"] <= MAX_ARTIFACT_BYTES
    ):
        raise ValueError("Saved evidence integrity check failed")
    return None, value, len(raw)


def artifact_dependencies(store, digest):
    """Physical dependencies, also required by backup and orphan collection."""
    _, manifest, _ = _artifact_content(store, digest)
    if manifest is None:
        return set()
    pending, found, nodes = [(manifest["tree"], 0)], set(), 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > MAX_ARTIFACT_NODES or not isinstance(item, dict) or len(item) != 1:
            raise ValueError("Invalid research chunk structure")
        key, child = next(iter(item.items()))
        if key == "artifact":
            if not isinstance(child, str) or not re.fullmatch(r"[a-f0-9]{64}", child):
                raise ValueError("Invalid research chunk reference")
            found.add(child)
        elif key == "object" and isinstance(child, dict):
            pending.extend((v, depth + 1) for v in child.values())
        elif key in ("array", "lists", "dicts") and isinstance(child, list):
            pending.extend((v, depth + 1) for v in child)
        elif key != "value":
            raise ValueError("Invalid research chunk structure")
    return found


def read_artifact(store, digest):
    remaining = MAX_ARTIFACT_BYTES + 4 * MAX_ARTIFACT_NODES
    nodes = 0
    active = set()

    def read(identifier, depth=0):
        nonlocal remaining, nodes
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError("Invalid research chunk reference")
        if identifier in active or depth > 33:
            raise ValueError("Cyclic or overly nested research chunks")
        active.add(identifier)
        try:
            value, manifest, size = _artifact_content(store, identifier)
            if manifest is None:
                remaining -= size
                if remaining < 0:
                    raise ValueError("Research chunks exceed the decoded size limit")
                return value
            if depth == 0:
                remaining = min(remaining, manifest["decoded_bytes"] + 4 * MAX_ARTIFACT_NODES)

            def expand(item, level):
                nonlocal nodes, remaining
                nodes += 1
                if (
                    level > 32
                    or nodes > MAX_ARTIFACT_NODES
                    or not isinstance(item, dict)
                    or len(item) != 1
                ):
                    raise ValueError("Invalid research chunk structure")
                key, child = next(iter(item.items()))
                if key == "value":
                    remaining -= len(encoded(child))
                    if remaining < 0:
                        raise ValueError("Research chunks exceed the decoded size limit")
                    return child
                if key == "artifact":
                    return read(child, level + 1)
                if key == "object" and isinstance(child, dict):
                    return {k: expand(v, level + 1) for k, v in child.items()}
                if key in ("array", "lists", "dicts") and isinstance(child, list):
                    result = {} if key == "dicts" else []
                    for part in child:
                        decoded = expand(part, level + 1)
                        if key == "array":
                            result.append(decoded)
                        elif key == "lists" and isinstance(decoded, list):
                            result.extend(decoded)
                        elif (
                            key == "dicts"
                            and isinstance(decoded, dict)
                            and not result.keys() & decoded.keys()
                        ):
                            result.update(decoded)
                        else:
                            raise ValueError("Invalid research chunk collection")
                    return result
                raise ValueError("Invalid research chunk structure")

            value = expand(manifest["tree"], depth + 1)
            raw = encoded(value)
            if (
                len(raw) != manifest["decoded_bytes"]
                or hashlib.sha256(raw).hexdigest() != identifier
            ):
                raise ValueError("Saved evidence integrity check failed")
            return value
        finally:
            active.remove(identifier)

    return read(digest)


def source_for(store, owner, source_id):
    with store.sessions() as db:
        source = db.get(ResearchSource, source_id)
        if source is None or source.owner != owner:
            raise LookupError("Source not found")
        return read_artifact(store, source.artifact)


def compact_source_receipt(receipt):
    """Keep intake/drafts small; exact per-bar lineage stays in hashed inputs."""
    provenance = dict(receipt["provenance"])
    for key in ("source_receipts", "symbol_identities", "missing_minutes"):
        if key in provenance:
            provenance[key + "_count"] = len(provenance.pop(key))
    provenance["lineage_detail"] = (
        "Complete per-bar identity and archive receipts are retained in the exact evidence export"
    )
    coverage = {key: value for key, value in receipt["coverage"].items() if key != "provenance"}
    if isinstance(coverage.get("symbols"), list):
        summaries = []
        for symbol in coverage["symbols"]:
            row = dict(symbol)
            for key, value in symbol.items():
                if isinstance(value, list):
                    row.setdefault(key + "_count", len(value))
                    row[key] = value[:10]
                    if len(value) > 10:
                        row[key + "_detail"] = "First 10 shown; full list retained in exact export"
            summaries.append(row)
        coverage["symbols"] = summaries
    return {**receipt, "provenance": provenance, "coverage": coverage}


def create_source(
    store, owner, raw, kind, requirements=None, *, defer_preparation=False, filename=None
):
    from research.data import fixture_snapshot
    from research.signals import normalize_csv

    normalized = normalize_csv(raw)
    # New source metadata only: existing immutable sources remain untouched.
    # Both identities are required before a picker can group equivalent inputs.
    normalized["receipt"]["original_csv_sha256"] = hashlib.sha256(raw).hexdigest()
    normalized["receipt"]["signals_sha256"] = hashlib.sha256(
        encoded(normalized["signals"])
    ).hexdigest()
    if isinstance(filename, str):
        import unicodedata

        basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        basename = "".join(c for c in basename if not unicodedata.category(c).startswith("C"))
        basename = basename.strip(" .")[:120].rstrip()
        if basename:
            normalized["receipt"]["filename"] = basename
    if requirements is not None:
        from research.connectors.registry import validate_capabilities
        from research.requirements import normalize_request

        requirements = normalize_request(requirements)[0]
        requirements["specification"] = validate_capabilities(
            requirements["kind"],
            requirements["specification"],
            requirements["config"],
            signals=normalized["signals"],
        )
    if kind == "fixture":
        snapshot = fixture_snapshot(normalized["signals"])
    elif kind in ("public", "broker", "historify"):
        snapshot = {
            "sessions": [],
            "bars": {},
            "provenance": {
                "provider": "public-evidence-import"
                if kind == "public"
                else "queued-openalgo-history",
                "synthetic": False,
                **(
                    {
                        "acquisition_mode": "stored_only"
                        if kind == "historify"
                        else "stored_then_broker"
                    }
                    if kind != "public"
                    else {}
                ),
            },
            "coverage": {
                "status": "preparing",
                "warnings": [],
            },
        }
    else:
        raise ValueError(
            "Choose OpenAlgo history, public evidence, a synthetic demo or stored prices only"
        )
    evidence = {
        **normalized,
        "snapshot": snapshot,
        "original_csv": raw.decode("utf-8-sig"),
        "original_csv_base64": base64.b64encode(raw).decode("ascii"),
        "original_csv_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if requirements is not None or any(signal.get("timestamp") for signal in normalized["signals"]):
        from research.requirements import VERSION, normalize_request, select_interval

        evidence["data_request"] = normalize_request(requirements)[0]
        evidence["data_source_kind"] = kind
        if kind in {"broker", "historify"}:
            interval = select_interval(normalized["signals"], evidence["data_request"])
            snapshot["provenance"].update(
                interval=interval,
                requirements_version=VERSION,
                native_price_policy="openalgo-native-history-v1",
            )
            if interval == "1m":
                snapshot["provenance"]["temporal_version"] = "minute-open-v1"
    digest = save_artifact(store, evidence)
    source_id = hashlib.sha256((owner + ":" + digest).encode()).hexdigest()[:32]
    receipt = compact_source_receipt(
        {
            "id": source_id,
            "receipt": normalized["receipt"],
            "coverage": snapshot["coverage"],
            "provenance": snapshot["provenance"],
        }
    )
    with store.sessions.begin() as db:
        write_guard(db)
        if db.get(ResearchSource, source_id) is None:
            db.add(
                ResearchSource(id=source_id, owner=owner, artifact=digest, created_at=time.time())
            )
            db.add(ResearchSourceReceipt(source_id=source_id, receipt=encoded(receipt).decode()))
    if kind in ("public", "broker", "historify") and not defer_preparation:
        receipt["preparation_job"] = submit(
            store,
            owner,
            source_id,
            {},
            request_id="prepare-" + source_id,
            kind="prepare" if kind == "public" else "acquire",
            specification={"provider": "public" if kind == "public" else "openalgo"},
        )
        with store.sessions() as db:
            latest = db.scalar(
                select(ResearchAttempt)
                .where(ResearchAttempt.root_job_id == receipt["preparation_job"]["id"])
                .order_by(ResearchAttempt.created_at.desc(), ResearchAttempt.job_id.desc())
                .limit(1)
            )
            if latest:
                receipt["preparation_job"] = job_receipt(store, db.get(ResearchJob, latest.job_id))
    return receipt


def submit(
    store,
    owner,
    source_id,
    config,
    request_id=None,
    kind="backtest",
    specification=None,
    previous_attempt_id=None,
):
    from research.connectors.registry import (
        policy_for_request,
        validate_capabilities,
        validate_specification,
    )
    from research.engine import policy_for_snapshot, validate_config, validate_snapshot

    evidence = source_for(store, owner, source_id)
    config = validate_config(config)
    if kind in ("portfolio_backtest", "portfolio_optimize"):
        from services.research_portfolio import validate_submission

        specification = validate_submission(evidence, kind, specification)
    elif kind in ("prepare", "acquire"):
        if (
            specification
            not in (
                [{"provider": "public"}]
                if kind == "prepare"
                else [{"provider": "fyers"}, {"provider": "openalgo"}]
            )
            or evidence["snapshot"].get("coverage", {}).get("status") != "preparing"
        ):
            raise ValueError("Invalid data preparation request")
    elif kind == "evidence_update":
        from datetime import date

        if not isinstance(specification, dict) or set(specification) != {"end_date"}:
            raise ValueError("An official evidence update requires only end_date")
        try:
            requested = date.fromisoformat(specification["end_date"])
        except (ValueError, TypeError):
            raise ValueError("Use a valid end date for official evidence") from None
        if requested > date.today():
            raise ValueError("Official evidence cannot be requested for a future date")
    else:
        specification = validate_capabilities(
            kind, specification or {}, config, signals=evidence["signals"]
        )
        if data_preparation_needed(store, evidence, config, kind, specification or {}):
            raise ValueError(
                "The settings need additional OpenAlgo prices. Review this setup to prepare them automatically."
            )
        validate_snapshot(evidence["snapshot"])
        specification = validate_specification(kind, specification or {}, config, evidence)
        validate_follow_up(store, owner, source_id, config, kind, specification)
    request_id = request_id or uuid.uuid4().hex
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", request_id):
        raise ValueError("request_id must contain 8–128 letters, numbers, underscores or hyphens")
    identity = hashlib.sha256(
        encoded(
            {
                "source_id": source_id,
                "config": config,
                "kind": kind,
                "specification": specification,
                "policy_version": policy_for_request(evidence["snapshot"], specification),
                **({"previous_attempt_id": previous_attempt_id} if previous_attempt_id else {}),
            }
        )
    ).hexdigest()
    now = time.time()
    with store.sessions.begin() as db:
        # Acquire the SQLite write reservation before counting admission slots.
        write_guard(db)
        previous = db.get(ResearchRequest, (owner, request_id))
        if previous:
            if previous.payload_hash != identity:
                raise ValueError(
                    "This request_id was already used with different inputs; create a new request"
                )
            return job_receipt(store, db.get(ResearchJob, previous.job_id))
        prior_attempt = None
        if previous_attempt_id:
            prior = db.get(ResearchJob, previous_attempt_id)
            if not prior or prior.owner != owner or prior.status in ACTIVE:
                raise ValueError("A new attempt requires an owner-visible terminal run")
            prior_attempt = db.get(ResearchAttempt, previous_attempt_id)
        count = db.scalar(
            select(func.count()).select_from(ResearchJob).where(ResearchJob.status.in_(ACTIVE))
        )
        if count >= 4:
            raise ValueError("Research queue is full (four active or pending jobs)")
        job = ResearchJob(
            id=uuid.uuid4().hex,
            owner=owner,
            source_id=source_id,
            config=encoded(config).decode(),
            status="queued",
            progress=0,
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        if previous_attempt_id:
            db.add(
                ResearchAttempt(
                    job_id=job.id,
                    previous_job_id=previous_attempt_id,
                    root_job_id=prior_attempt.root_job_id if prior_attempt else previous_attempt_id,
                    created_at=now,
                )
            )
        db.add(ResearchRequest(owner=owner, token=request_id, payload_hash=identity, job_id=job.id))
        db.add(
            ResearchExperiment(
                job_id=job.id,
                kind=kind,
                specification=encoded(specification).decode(),
                identity=identity,
                counts="{}",
                parent_job_id=specification.get("parent_job_id") if kind == "optimize" else None,
            )
        )
    return job_receipt(store, job)


def retry_attempt(store, owner, job_id, request_id):
    if not request_id:
        raise ValueError("A new attempt requires a request identity for safe retries")
    prior = get_job(store, owner, job_id)
    with store.sessions() as db:
        experiment = db.get(ResearchExperiment, job_id)
        kind = experiment.kind if experiment else "backtest"
        specification = json.loads(experiment.specification) if experiment else {}
    if kind in ("prepare", "acquire") and prior.status == "completed":
        raise ValueError("This source is already prepared; reuse the saved source")
    if kind == "optimize" and not specification.get("parent_job_id"):
        specification.pop("experiment_version", None)
    return submit(
        store,
        owner,
        prior.source_id,
        json.loads(prior.config),
        request_id=request_id,
        kind=kind,
        specification=specification,
        previous_attempt_id=job_id,
    )


def list_sources(store, owner):
    with store.sessions() as db:
        sources = db.scalars(
            select(ResearchSource)
            .where(ResearchSource.owner == owner)
            .order_by(ResearchSource.created_at.desc())
            .limit(100)
        ).all()
    receipts = []
    for source in sources:
        with store.sessions() as db:
            cached = db.get(ResearchSourceReceipt, source.id)
        if cached:
            receipt = compact_source_receipt(json.loads(cached.receipt))
            if receipt["coverage"].get("status") != "preparing":
                receipts.append(receipt)
            continue
        evidence = read_artifact(store, source.artifact)
        if evidence["snapshot"].get("coverage", {}).get("status") == "preparing":
            continue
        receipts.append(
            {
                "id": source.id,
                "receipt": evidence["receipt"],
                "coverage": evidence["snapshot"]["coverage"],
                "provenance": evidence["snapshot"]["provenance"],
            }
        )
    return [compact_source_receipt(receipt) for receipt in receipts]


def source_receipt(store, owner, source_id):
    with store.sessions() as db:
        source = db.get(ResearchSource, source_id)
        if source is None or source.owner != owner:
            raise LookupError("Source not found")
        cached = db.get(ResearchSourceReceipt, source_id)
        if cached:
            return compact_source_receipt(json.loads(cached.receipt))
        evidence = read_artifact(store, source.artifact)
    return compact_source_receipt(
        {
            "id": source_id,
            "receipt": evidence["receipt"],
            "coverage": evidence["snapshot"]["coverage"],
            "provenance": evidence["snapshot"]["provenance"],
        }
    )


def prepare_source(store, owner, evidence, progress=None, *, publish=True):
    from research.evidence_import import public_snapshot

    directory = os.getenv("RESEARCH_PUBLIC_EVIDENCE_DIR")
    if not directory:
        raise ValueError(
            "No public evidence bundle configured; set RESEARCH_PUBLIC_EVIDENCE_DIR to the reviewed public seeds directory"
        )
    snapshot = public_snapshot(
        evidence["signals"],
        directory,
        progress=progress,
        extension_dir=os.getenv("RESEARCH_PUBLIC_EXTENSION_DIR"),
    )
    prepared = {**evidence, "snapshot": snapshot}
    return register_source(store, owner, prepared, publish=publish)


def register_source(store, owner, prepared, *, publish=True):
    snapshot = prepared["snapshot"]
    digest = save_artifact(store, prepared)
    source_id = hashlib.sha256((owner + ":" + digest).encode()).hexdigest()[:32]
    details = dict(prepared["receipt"])
    csv_hash = prepared.get("original_csv_sha256")
    if isinstance(csv_hash, str) and re.fullmatch(r"[a-f0-9]{64}", csv_hash):
        details["original_csv_sha256"] = csv_hash
        details["signals_sha256"] = hashlib.sha256(encoded(prepared["signals"])).hexdigest()
    else:
        details.pop("original_csv_sha256", None)
        details.pop("signals_sha256", None)
    receipt = compact_source_receipt(
        {
            "id": source_id,
            "receipt": details,
            "coverage": snapshot["coverage"],
            "provenance": snapshot["provenance"],
        }
    )
    if not publish:
        return prepared, receipt
    with store.sessions.begin() as db:
        write_guard(db)
        if db.get(ResearchSource, source_id) is None:
            db.add(
                ResearchSource(id=source_id, owner=owner, artifact=digest, created_at=time.time())
            )
            db.add(ResearchSourceReceipt(source_id=source_id, receipt=encoded(receipt).decode()))
    return prepared, receipt


def validate_follow_up(store, owner, source_id, config, kind, spec):
    """Previously tested counts must come from immutable, owner-visible evidence."""
    from research.engine import policy_for_snapshot

    if kind != "optimize":
        if kind == "research" and (
            spec.get("search", {}).get("exclude_indices")
            or spec.get("search", {}).get("parent_job_id")
        ):
            raise ValueError(
                "Earlier-only selection must start a fresh search for each training window"
            )
        return
    parent_id = spec.get("parent_job_id")
    if not parent_id:
        if spec.get("exclude_indices"):
            raise ValueError("Previously tested settings require a completed parent search")
        return
    with store.sessions() as db:
        parent = db.get(ResearchJob, parent_id)
        experiment = db.get(ResearchExperiment, parent_id)
        if (
            not parent
            or parent.owner != owner
            or parent.status != "completed"
            or not experiment
            or experiment.kind != "optimize"
        ):
            raise ValueError("Completed parent search not found")
        if parent.source_id != source_id or json.loads(parent.config) != config:
            raise ValueError("Follow-up must preserve the parent source and base setup")
        parent_spec = json.loads(experiment.specification)
        bundle = read_artifact(store, parent.result_artifact)
    parent_inputs = (
        read_artifact(store, bundle["inputs_artifact"])
        if "inputs_artifact" in bundle
        else bundle["inputs"]
    )
    if bundle["result"]["policy_version"] != policy_for_snapshot(parent_inputs["snapshot"]):
        raise ValueError("Parent execution policy changed; start a fresh search")
    for key in (
        "axes",
        "mode_strategies",
        "include_trailing_off",
        "rank_by",
        "trailing_choices",
        "experiment_version",
    ):
        if spec.get(key) != parent_spec.get(key):
            raise ValueError("Follow-up must preserve the parent search boundaries and ranking")
    indices = set(parent_spec.get("exclude_indices", []))
    indices.update(row["grid_index"] for row in bundle["result"]["experiment"]["rows"])
    if spec["exclude_indices"] != sorted(indices):
        raise ValueError("Previously tested settings do not match the saved parent evidence")


def data_preparation_needed(store, evidence, config, kind, specification):
    from research.requirements import build_plan, covers_plan, select_interval

    snapshot = evidence["snapshot"]
    # Explicit daily demo/public evidence and exact legacy reports remain reusable.
    native = bool(evidence.get("data_request")) and bool(evidence.get("reference_artifact"))
    timed = (
        select_interval(
            evidence["signals"], {"config": config, "kind": kind, "specification": specification}
        )
        == "1m"
    )
    if not native and not timed:
        return False
    if snapshot.get("provenance", {}).get("synthetic"):
        if timed:
            raise ValueError("Timed setups require OpenAlgo price history")
        return False
    reference_id = evidence.get("reference_artifact")
    reference = read_artifact(store, reference_id) if reference_id else snapshot
    plan = build_plan(
        evidence["signals"],
        {"config": config, "kind": kind, "specification": specification},
        reference,
    )
    return not covers_plan(snapshot, plan)


def preflight(store, owner, source_id, config, kind, specification):
    from research.connectors.registry import (
        policy_for_request,
        split_specification,
        validate_capabilities,
        validate_specification,
    )
    from research.engine import policy_for_snapshot, validate_config, validate_snapshot
    from research.experiments import research_windows, search_preflight

    evidence = source_for(store, owner, source_id)
    config = validate_config(config)
    specification = validate_capabilities(kind, specification, config, signals=evidence["signals"])
    if data_preparation_needed(store, evidence, config, kind, specification):
        raw = base64.b64decode(evidence["original_csv_base64"])
        prepared = create_source(
            store,
            owner,
            raw,
            "historify" if evidence.get("data_source_kind") == "historify" else "broker",
            {"config": config, "kind": kind, "specification": specification},
        )
        return {"status": "preparing", "preparation_job": prepared["preparation_job"]}
    validate_snapshot(evidence["snapshot"])
    spec = validate_specification(kind, specification, config, evidence)
    validate_follow_up(store, owner, source_id, config, kind, spec)
    if kind == "optimize":
        execution, search_spec = split_specification(spec)
        return {
            **search_preflight(search_spec, config),
            "config": config,
            "policy_version": policy_for_request(evidence["snapshot"], spec),
            **({"specification": spec, "execution": execution} if execution else {}),
        }
    if kind == "research":
        return {
            "config": config,
            "policy_version": policy_for_snapshot(evidence["snapshot"]),
            "specification": spec,
            "windows": research_windows(evidence["signals"], evidence["snapshot"], spec),
            "planned_evaluations": spec["folds"]
            * (spec.get("search", {}).get("budget", 0) + 1 + len(spec["variants"])),
            "interpretation": "Possible entries are upper bounds before trigger, coverage and cash checks; folds use fresh capital",
        }
    return {
        "config": config,
        "specification": spec,
        "policy_version": policy_for_request(evidence["snapshot"], spec),
        "planned_evaluations": 1 + len(spec.get("variants", [])),
    }


def list_jobs(store, owner, *, page_size=30, cursor=None, query="", kind=None, status=None):
    try:
        page_size = int(page_size)
    except (ValueError, TypeError):
        raise ValueError("Page size must be an integer") from None
    if not 1 <= page_size <= 100:
        raise ValueError("Page size must be between 1 and 100")
    if not isinstance(query, str) or len(query) > 120:
        raise ValueError("Search must be at most 120 characters")
    statement = (
        select(ResearchJob)
        .outerjoin(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
        .outerjoin(ResearchSourceReceipt, ResearchSourceReceipt.source_id == ResearchJob.source_id)
        .where(ResearchJob.owner == owner)
    )
    if kind:
        statement = statement.where(ResearchExperiment.kind == kind)
    if status:
        statement = statement.where(ResearchJob.status == status)
    if query:
        term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        statement = statement.where(
            or_(
                ResearchJob.id.like(term, escape="\\"),
                ResearchJob.config.like(term, escape="\\"),
                ResearchSourceReceipt.receipt.like(term, escape="\\"),
            )
        )
    if cursor:
        try:
            created, identity = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if not isinstance(created, (int, float)) or not re.fullmatch(r"[a-f0-9]{32}", identity):
                raise ValueError()
        except Exception:
            raise ValueError("Invalid saved-run cursor") from None
        statement = statement.where(
            or_(
                ResearchJob.created_at < created,
                (ResearchJob.created_at == created) & (ResearchJob.id < identity),
            )
        )
    with store.sessions() as db:
        rows = db.scalars(
            statement.order_by(ResearchJob.created_at.desc(), ResearchJob.id.desc()).limit(
                page_size + 1
            )
        ).all()
    selected = rows[:page_size]
    return {
        "items": [job_receipt(store, row) for row in selected],
        "next_cursor": base64.urlsafe_b64encode(
            encoded([selected[-1].created_at, selected[-1].id])
        ).decode()
        if len(rows) > page_size
        else None,
    }


def job_receipt(store, job, include_result=False):
    value = {
        key: getattr(job, key)
        for key in ("id", "source_id", "status", "progress", "created_at", "updated_at", "error")
    }
    value["config"] = json.loads(job.config)
    value["evidence_id"] = job.result_artifact
    with store.sessions() as db:
        experiment = db.get(ResearchExperiment, job.id)
        attempt = db.get(ResearchAttempt, job.id)
        cached_source = db.get(ResearchSourceReceipt, job.source_id)
        source_summary = (
            json.loads(cached_source.receipt).get("receipt", {}) if cached_source else {}
        )
        value["source_summary"] = source_summary
        value["previous_attempt_id"] = attempt.previous_job_id if attempt else None
        value.update(
            kind=experiment.kind if experiment else "backtest",
            specification=json.loads(experiment.specification) if experiment else {},
            counts=json.loads(experiment.counts) if experiment else {},
            resumable=bool(
                experiment
                and experiment.checkpoint
                and job.status in ("interrupted", "failed", "cancelled")
            ),
        )
        value["queue_position"] = (
            db.scalar(
                select(func.count())
                .select_from(ResearchJob)
                .where(ResearchJob.status == "queued", ResearchJob.created_at <= job.created_at)
            )
            if job.status == "queued"
            else None
        )
    value["title"] = " · ".join(
        str(part)
        for part in (
            {
                "backtest": "Fixed backtest",
                "optimize": "Parameter search",
                "research": "Later-period research",
                "sensitivity": "Sensitivity check",
                "prepare": "Public price preparation",
                "acquire": "Broker price download",
                "evidence_update": "Official evidence update",
                "portfolio_backtest": "Portfolio backtest",
                "portfolio_optimize": "Portfolio optimization",
            }.get(value["kind"], value["kind"]),
            source_summary.get("date_from"),
            source_summary.get("date_to"),
            value["config"].get("mode"),
        )
        if part
    )
    if include_result and job.status == "completed":
        value["result"] = read_artifact(store, job.result_artifact)["result"]
    return value


def resume(store, owner, job_id):
    from research.connectors.registry import policy_for_request, validate_capabilities

    job = get_job(store, owner, job_id)
    evidence = source_for(store, owner, job.source_id)
    with store.sessions() as db:
        saved_experiment = db.get(ResearchExperiment, job_id)
    saved_spec = json.loads(saved_experiment.specification) if saved_experiment else {}
    if saved_experiment and saved_experiment.kind in ("portfolio_backtest", "portfolio_optimize"):
        from services.research_portfolio import validate_submission

        validate_submission(evidence, saved_experiment.kind, saved_spec)
    elif saved_spec.get("execution"):
        validate_capabilities(
            saved_experiment.kind, saved_spec, json.loads(job.config), evidence["signals"]
        )
    policy = policy_for_request(evidence["snapshot"], saved_spec)
    with store.sessions.begin() as db:
        write_guard(db)
        current = db.get(ResearchJob, job_id)
        experiment = db.get(ResearchExperiment, job_id)
        if current.status in ACTIVE:
            return job_receipt(store, current)
        if (
            current.status not in ("interrupted", "failed", "cancelled")
            or not experiment
            or not experiment.checkpoint
        ):
            raise ValueError("This run has no resumable checkpoint")
        checkpoint = read_artifact(store, experiment.checkpoint)
        if (
            checkpoint["identity"] != experiment.identity
            or checkpoint.get("policy_version") != policy
        ):
            raise ValueError("Checkpoint identity does not match this experiment")
        count = db.scalar(
            select(func.count()).select_from(ResearchJob).where(ResearchJob.status.in_(ACTIVE))
        )
        if count >= 4:
            raise ValueError("Research queue is full")
        current.status, current.error, current.worker, current.updated_at = (
            "queued",
            None,
            None,
            time.time(),
        )
    return job_receipt(store, get_job(store, owner, job_id))


def get_job(store, owner, job_id):
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        if job is None or job.owner != owner:
            raise LookupError("Run not found")
        return job


def cancel(store, owner, job_id):
    get_job(store, owner, job_id)
    with store.sessions.begin() as db:
        db.execute(
            update(ResearchJob)
            .where(ResearchJob.id == job_id, ResearchJob.status == "queued")
            .values(status="cancelled", updated_at=time.time())
        )
        db.execute(
            update(ResearchJob)
            .where(ResearchJob.id == job_id, ResearchJob.status == "running")
            .values(status="cancelling", updated_at=time.time())
        )
    return job_receipt(store, get_job(store, owner, job_id))
