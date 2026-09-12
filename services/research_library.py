"""Account-owned research library around immutable portfolio jobs and evidence.

Drafts accept incomplete financial settings; the existing portfolio contract owns
run admission. All metadata writes use the store's fenced, context-managed
transaction. Launch inserts its source, job, frozen version and retry key together.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from copy import deepcopy

from sqlalchemy import delete, func, or_, select, update

from database.research_db import (
    ResearchChartinkImport,
    ResearchComparison,
    ResearchExperiment,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchLibraryRequest,
    ResearchLibrarySource,
    ResearchRequest,
    ResearchSetupVersion,
    ResearchShortlistCandidate,
    ResearchSource,
    ResearchSourceReceipt,
)
from services import scanner_research_service as evidence_service

MAX_DRAFT_BYTES = 256 * 1024
MAX_EXPERIMENTS = 5000
MAX_VERSIONS = 1000
PAGE_SIZE = 50
PARENTS = ("parent_job_id", "parent_result_artifact", "parent_trial_id", "parent_version_id")


class RevisionConflict(ValueError):
    def __init__(self, revision):
        super().__init__("This research was changed in another session. Reload or save a copy.")
        self.revision = revision


def fresh_draft():
    return {
        "portfolio": {
            "version": "research-portfolio-v1",
            "name": "My portfolio",
            "capital": 100000,
            "engine": "vectorbt",
            "strategies": [],
        },
        "optimizing": False,
        "equalWeights": True,
        "sources": {},
        "optimization": {"sampler": "tpe", "trials": 50, "objective": "balanced", "seed": 0},
    }


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"Invalid {label}")
    return value


def _text(value, limit, label, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"{label} must contain {'0' if empty else '1'}–{limit} characters")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError(f"Invalid {label}")
    return value.strip()


def _bounded_json(value):
    # Bound traversal before JSON encoding; do not allow enormous nesting or NaN.
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 10000 or depth > 16:
            raise ValueError("Research draft is too complex")
        if isinstance(item, dict):
            if len(item) > 1000 or any(not isinstance(key, str) or len(key) > 128 for key in item):
                raise ValueError("Invalid research draft fields")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > 1000:
                raise ValueError("Research draft list is too large")
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Research draft numbers must be finite")
        elif not isinstance(item, (str, int, float, bool, type(None))):
            raise ValueError("Invalid research draft value")
    raw = evidence_service.encoded(value)
    if len(raw) > MAX_DRAFT_BYTES:
        raise ValueError("Research draft exceeds 256 KiB")
    return raw.decode()


def _source_ids(draft):
    return {row["source_id"] for row in draft["portfolio"]["strategies"] if row.get("source_id")}


def _number_shape(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")


def _optimization_shape(value):
    _object(value, {"sampler", "trials", "objective", "seed"}, "draft optimization settings")
    if value.get("sampler") not in ("tpe", "grid") or value.get("objective") not in (
        "balanced",
        "return",
        "drawdown",
    ):
        raise ValueError("Choose supported draft optimization controls")
    for field in ("trials", "seed"):
        _number_shape(value.get(field), f"Draft {field}")


def normalize_draft(store, owner, raw):
    _bounded_json(raw)
    _object(raw, {"portfolio", "optimizing", "equalWeights", "sources", "optimization"}, "draft")
    result = deepcopy(raw)
    portfolio = _object(
        result.get("portfolio"),
        {
            "version",
            "name",
            "capital",
            "engine",
            "strategies",
            "optimization",
            "validation",
            "automatic_research",
            "date_from",
            "date_to",
        },
        "portfolio draft",
    )
    if portfolio.get("version") != "research-portfolio-v1":
        raise ValueError("This portfolio draft format is not supported")
    _text(portfolio.get("name"), 120, "Portfolio name", empty=True)
    if isinstance(portfolio.get("capital"), bool) or not isinstance(
        portfolio.get("capital"), (int, float)
    ):
        raise ValueError("Draft capital must be a number")
    if portfolio.get("engine") not in ("vectorbt", "nautilus"):
        raise ValueError("Choose a supported portfolio engine")
    if type(result.get("optimizing")) is not bool or type(result.get("equalWeights")) is not bool:
        raise ValueError("Draft mode and allocation choice must be booleans")
    if not isinstance(result.get("optimization"), dict):
        raise ValueError("Draft optimization settings must be an object")
    _optimization_shape(result["optimization"])
    if "optimization" in portfolio:
        _optimization_shape(portfolio["optimization"])
    for field in ("date_from", "date_to"):
        if field in portfolio:
            _text(portfolio[field], 10, "Draft date", empty=True)
    if portfolio.get("validation") is not None:
        _object(portfolio["validation"], {"train_pct", "mode"}, "draft validation")
        _number_shape(portfolio["validation"].get("train_pct"), "Draft training share")
        if portfolio["validation"].get("mode", "evaluate") not in ("reserve", "evaluate"):
            raise ValueError("Choose reserve or evaluate for the later period")
    if portfolio.get("automatic_research") is not None:
        from research.automatic_protocol import VERSION as AUTOMATIC_VERSION

        if portfolio["automatic_research"] != {"version": AUTOMATIC_VERSION}:
            raise ValueError("Choose a supported automatic research preset")
    rows = portfolio.get("strategies")
    if not isinstance(rows, list) or len(rows) > 8:
        raise ValueError("A draft can contain at most eight strategies")
    identifiers = set()
    for row in rows:
        _object(
            row,
            {"id", "name", "source_id", "allocation_pct", "config", "search", "type"},
            "draft strategy",
        )
        identifier = row.get("id")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
            or identifier in identifiers
        ):
            raise ValueError("Each draft strategy needs a distinct identifier")
        identifiers.add(identifier)
        _text(row.get("name"), 80, "Strategy name", empty=True)
        if (
            row.get("type", "signals") != "signals"
            or not isinstance(row.get("config"), dict)
            or not isinstance(row.get("search"), dict)
        ):
            raise ValueError("Invalid draft strategy structure")
        for key, value in row["config"].items():
            if key == "modes":
                if (
                    not isinstance(value, list)
                    or len(value) > 8
                    or not all(isinstance(mode, str) and len(mode) <= 40 for mode in value)
                ):
                    raise ValueError("Invalid draft strategy modes")
            elif isinstance(value, (dict, list)):
                raise ValueError("Draft strategy settings must contain simple values")
        for axis in row["search"].values():
            _object(axis, {"min", "max", "step"}, "draft parameter range")
            for field in ("min", "max", "step"):
                _number_shape(axis.get(field), f"Draft range {field}")
        if isinstance(row.get("allocation_pct"), bool) or not isinstance(
            row.get("allocation_pct"), (int, float)
        ):
            raise ValueError("Draft allocation must be a number")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or (
            source_id and not re.fullmatch(r"[a-f0-9]{32}", source_id)
        ):
            raise ValueError("Invalid draft source identity")
    supplied = result.get("sources")
    if not isinstance(supplied, dict) or len(supplied) > 8:
        raise ValueError("Invalid draft source list")
    for source_id in set(supplied) | _source_ids(result):
        if not re.fullmatch(r"[a-f0-9]{32}", source_id):
            raise ValueError("Invalid draft source identity")
        # Read only stored source evidence. No broker session or price preparation.
        receipt = evidence_service.source_receipt(store, owner, source_id)
        if source_id in _source_ids(result):
            filename = (
                (supplied.get(source_id) or {}).get("filename")
                if isinstance(supplied.get(source_id), dict)
                else None
            )
            if filename is not None:
                receipt["filename"] = _text(filename, 120, "Source name", empty=True)
            result.setdefault("_sources", {})[source_id] = receipt
    result["sources"] = result.pop("_sources", {})
    _bounded_json(result)
    return result


def portfolio_payload(draft):
    """Match the native builder's mode and inactive-axis filtering on the server."""
    portfolio = deepcopy(draft["portfolio"])
    portfolio.pop("optimization", None)
    validation = portfolio.pop("validation", None)
    automatic = portfolio.pop("automatic_research", None)
    for strategy in portfolio["strategies"]:
        strategy["config"]["initial_capital"] = portfolio["capital"]
        search = strategy["search"] if draft["optimizing"] else {}
        cfg = strategy["config"]
        if cfg.get("hold_minutes") is not None or cfg.get("trade_horizon") == "intraday":
            search.pop("hold_sessions", None)
        if cfg.get("hold_minutes") is None:
            search.pop("hold_minutes", None)
        if not cfg.get("trailing_enabled"):
            search.pop("trailing_pct", None)
        strategy["search"] = search
    if draft["optimizing"] and automatic:
        portfolio["automatic_research"] = automatic
        for strategy in portfolio["strategies"]:
            strategy["search"] = {}
        return portfolio
    if draft["optimizing"]:
        portfolio["optimization"] = deepcopy(draft["optimization"])
    if validation is not None and (draft["optimizing"] or validation.get("mode") == "reserve"):
        portfolio["validation"] = validation
    return portfolio


def _owned(db, owner, identifier):
    row = db.get(ResearchLibraryExperiment, identifier)
    if row is None or row.owner != owner:
        raise LookupError("Research experiment not found")
    return row


def _revision(row, revision):
    if type(revision) is not int or revision < 1:
        raise ValueError("Supply the saved research revision")
    if row.revision != revision:
        raise RevisionConflict(row.revision)


def _editable(row):
    if row.archived:
        raise ValueError("Restore this experiment before changing or running its setup")


def _touch(row):
    row.revision += 1
    row.updated_at = time.time()


def _check_sources(db, owner, draft):
    for identifier in _source_ids(draft):
        source = db.get(ResearchSource, identifier)
        if source is None or source.owner != owner:
            raise LookupError("Source not found")


def _link_sources(db, experiment_id, draft, version_id=""):
    if not version_id:
        db.execute(
            delete(ResearchLibrarySource).where(
                ResearchLibrarySource.experiment_id == experiment_id,
                ResearchLibrarySource.version_id == "",
            )
        )
    for source_id in _source_ids(draft):
        db.add(
            ResearchLibrarySource(
                experiment_id=experiment_id, source_id=source_id, version_id=version_id
            )
        )


def _metadata(data, *, create=False):
    values = {}
    for key, limit in (("name", 120), ("notes", 10000)):
        if key in data:
            values[key] = _text(data[key], limit, key.title(), empty=key == "notes")
    if "tags" in data:
        tags = data["tags"]
        if not isinstance(tags, list) or len(tags) > 12:
            raise ValueError("Use at most 12 research tags")
        cleaned = [_text(tag, 40, "Tag") for tag in tags]
        values["tags"] = evidence_service.encoded(list(dict.fromkeys(cleaned))).decode()
    for key in ("pinned", "archived"):
        if key in data:
            if type(data[key]) is not bool:
                raise ValueError(f"{key.title()} must be a boolean")
            values[key] = data[key]
    if create:
        values = {
            "name": "New experiment",
            "notes": "",
            "tags": "[]",
            "pinned": False,
            "archived": False,
            **values,
        }
    return values


def _new_experiment(db, owner, draft, metadata):
    if (
        db.scalar(
            select(func.count())
            .select_from(ResearchLibraryExperiment)
            .where(ResearchLibraryExperiment.owner == owner)
        )
        >= MAX_EXPERIMENTS
    ):
        raise ValueError("Research library contains the maximum 5,000 experiments")
    now = time.time()
    row = ResearchLibraryExperiment(
        id=uuid.uuid4().hex,
        owner=owner,
        draft=_bounded_json(draft),
        revision=1,
        created_at=now,
        updated_at=now,
        **metadata,
    )
    db.add(row)
    _link_sources(db, row.id, draft)
    return row


def _summary(db, row):
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "name",
            "notes",
            "pinned",
            "archived",
            "revision",
            "created_at",
            "updated_at",
            *PARENTS,
        )
    }
    result["tags"] = json.loads(row.tags)
    result["job_count"] = db.scalar(
        select(func.count())
        .select_from(ResearchLibraryJob)
        .where(ResearchLibraryJob.experiment_id == row.id)
    )
    result["version_count"] = db.scalar(
        select(func.count())
        .select_from(ResearchSetupVersion)
        .where(ResearchSetupVersion.experiment_id == row.id)
    )
    result["active_job_count"] = db.scalar(
        select(func.count())
        .select_from(ResearchLibraryJob)
        .join(ResearchJob, ResearchJob.id == ResearchLibraryJob.job_id)
        .where(
            ResearchLibraryJob.experiment_id == row.id,
            ResearchJob.status.in_(evidence_service.ACTIVE),
        )
    )
    return result


def _version_receipt(row, *, full=True):
    value = {
        key: getattr(row, key)
        for key in ("id", "experiment_id", "name", "number", "created_at", *PARENTS)
    }
    if full:
        value.update(
            draft=json.loads(row.draft),
            portfolio=json.loads(row.portfolio) if row.portfolio else None,
        )
    return value


def _page(limit=20, offset=0):
    if (
        type(limit) is not int
        or not 1 <= limit <= PAGE_SIZE
        or type(offset) is not int
        or not 0 <= offset <= 100000
    ):
        raise ValueError("Use a page size of 1–50 and a valid offset")
    return limit, offset


def _filter(query, owner, search, archived):
    if type(archived) is not bool:
        raise ValueError("Archived filter must be a boolean")
    search = _text(search, 120, "Search", empty=True)
    query = query.where(
        ResearchLibraryExperiment.owner == owner, ResearchLibraryExperiment.archived == archived
    )
    if search:
        query = query.where(
            or_(
                ResearchLibraryExperiment.name.contains(search, autoescape=True),
                ResearchLibraryExperiment.notes.contains(search, autoescape=True),
                ResearchLibraryExperiment.tags.contains(search, autoescape=True),
            )
        )
    return query


def list_experiments(store, owner, *, search="", archived=False, limit=20, offset=0):
    from services.research_chosen_setups import current_summaries

    limit, offset = _page(limit, offset)
    with store.sessions() as db:
        rows = db.scalars(
            _filter(select(ResearchLibraryExperiment), owner, search, archived)
            .order_by(
                ResearchLibraryExperiment.pinned.desc(),
                ResearchLibraryExperiment.updated_at.desc(),
                ResearchLibraryExperiment.id,
            )
            .offset(offset)
            .limit(limit + 1)
        ).all()
        choices = current_summaries(db, owner, rows[:limit])
        return {
            "items": [
                {**_summary(db, row), "chosen_setup": choices.get(row.id)} for row in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
        }


def get_experiment(store, owner, identifier, *, jobs_offset=0, versions_offset=0):
    from services.research_chosen_setups import current_summaries

    _page(PAGE_SIZE, jobs_offset)
    _page(PAGE_SIZE, versions_offset)
    with store.sessions() as db:
        row = _owned(db, owner, identifier)
        result = _summary(db, row)
        result["chosen_setup"] = current_summaries(db, owner, [row]).get(identifier)
        draft = json.loads(row.draft)
        linked = db.execute(
            select(ResearchLibraryJob, ResearchJob)
            .join(ResearchJob, ResearchJob.id == ResearchLibraryJob.job_id)
            .where(ResearchLibraryJob.experiment_id == identifier, ResearchJob.owner == owner)
            .order_by(ResearchLibraryJob.created_at.desc(), ResearchLibraryJob.job_id)
            .offset(jobs_offset)
            .limit(PAGE_SIZE + 1)
        ).all()
        versions = db.scalars(
            select(ResearchSetupVersion)
            .where(ResearchSetupVersion.experiment_id == identifier)
            .order_by(ResearchSetupVersion.number.desc())
            .offset(versions_offset)
            .limit(PAGE_SIZE + 1)
        ).all()
    result["draft"] = normalize_draft(store, owner, draft)
    result["jobs"] = [
        {
            **evidence_service.job_receipt(store, job, experiment_id=identifier),
            "version_id": link.version_id,
            "role": link.role,
            "experiment_id": identifier,
        }
        for link, job in linked[:PAGE_SIZE]
    ]
    result["versions"] = [_version_receipt(version) for version in versions[:PAGE_SIZE]]
    result["jobs_next_offset"] = jobs_offset + PAGE_SIZE if len(linked) > PAGE_SIZE else None
    result["versions_next_offset"] = (
        versions_offset + PAGE_SIZE if len(versions) > PAGE_SIZE else None
    )
    return result


def create_experiment(store, owner, data):
    _object(data, {"name", "notes", "tags", "draft"}, "new experiment")
    metadata = _metadata(data, create=True)
    raw_draft = data.get("draft", fresh_draft())
    if "draft" not in data and "name" in data:
        raw_draft["portfolio"]["name"] = metadata["name"]
    draft = normalize_draft(store, owner, raw_draft)
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        evidence_service.ensure_storage_capacity(store, len(_bounded_json(draft)) + 16384)
        _check_sources(db, owner, draft)
        identifier = _new_experiment(db, owner, draft, metadata).id
    return get_experiment(store, owner, identifier)


def update_experiment(store, owner, identifier, data):
    _object(data, {"revision", "name", "notes", "tags", "pinned", "archived"}, "experiment changes")
    metadata = _metadata(data)
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        row = _owned(db, owner, identifier)
        _revision(row, data.get("revision"))
        if metadata.get("archived") and db.scalar(
            select(func.count())
            .select_from(ResearchLibraryJob)
            .join(ResearchJob, ResearchJob.id == ResearchLibraryJob.job_id)
            .where(
                ResearchLibraryJob.experiment_id == identifier,
                ResearchJob.status.in_(evidence_service.ACTIVE),
            )
        ):
            raise ValueError("Stop or finish active runs before archiving this experiment")
        for key, value in metadata.items():
            setattr(row, key, value)
        _touch(row)
    return get_experiment(store, owner, identifier)


def save_draft(store, owner, identifier, data):
    _object(data, {"revision", "draft"}, "draft save")
    # Ownership before touching a caller-supplied source identity.
    with store.sessions() as db:
        _owned(db, owner, identifier)
    draft = normalize_draft(store, owner, data.get("draft"))
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        row = _owned(db, owner, identifier)
        _revision(row, data.get("revision"))
        _editable(row)
        _check_sources(db, owner, draft)
        evidence_service.ensure_storage_capacity(store, len(_bounded_json(draft)) + 4096)
        row.draft = _bounded_json(draft)
        _link_sources(db, identifier, draft)
        _touch(row)
    return get_experiment(store, owner, identifier)


def _freeze(db, row, draft, name=None, portfolio=None, parents=None):
    latest = (
        db.scalar(
            select(func.max(ResearchSetupVersion.number)).where(
                ResearchSetupVersion.experiment_id == row.id
            )
        )
        or 0
    )
    if latest >= MAX_VERSIONS:
        raise ValueError("This experiment has reached 1,000 setup versions")
    version = ResearchSetupVersion(
        id=uuid.uuid4().hex,
        experiment_id=row.id,
        name=_text(f"Setup {latest + 1}" if name is None else name, 120, "Setup name"),
        number=latest + 1,
        draft=_bounded_json(draft),
        portfolio=evidence_service.encoded(portfolio).decode() if portfolio else None,
        created_at=time.time(),
        **(parents if parents is not None else {key: getattr(row, key) for key in PARENTS}),
    )
    db.add(version)
    _link_sources(db, row.id, draft, version.id)
    return version


def save_version(store, owner, identifier, data):
    from research.portfolio import normalize

    _object(data, {"revision", "name"}, "setup save")
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        row = _owned(db, owner, identifier)
        _revision(row, data.get("revision"))
        _editable(row)
        draft = json.loads(row.draft)
        _check_sources(db, owner, draft)
        try:
            portfolio = normalize(portfolio_payload(draft))
        except ValueError:
            portfolio = None
        evidence_service.ensure_storage_capacity(store, len(row.draft) * 2 + 8192)
        version = _version_receipt(_freeze(db, row, draft, data.get("name"), portfolio))
        _touch(row)
    return {"experiment": get_experiment(store, owner, identifier), "version": version}


def restore_version(store, owner, identifier, version_id, data):
    _object(data, {"revision"}, "setup restore")
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        row = _owned(db, owner, identifier)
        _revision(row, data.get("revision"))
        _editable(row)
        version = db.get(ResearchSetupVersion, version_id)
        if version is None or version.experiment_id != identifier:
            raise LookupError("Saved setup not found")
        draft = json.loads(version.draft)
        _check_sources(db, owner, draft)
        _preserve_draft(db, row, draft, "Draft before restoring setup")
        row.draft = version.draft
        for key in PARENTS:
            setattr(row, key, getattr(version, key))
        row.parent_version_id = version.id
        _link_sources(db, identifier, draft)
        _touch(row)
    return get_experiment(store, owner, identifier)


def get_version(store, owner, identifier, version_id):
    with store.sessions() as db:
        _owned(db, owner, identifier)
        version = db.get(ResearchSetupVersion, version_id)
        if version is None or version.experiment_id != identifier:
            raise LookupError("Saved setup not found")
        return _version_receipt(version)


def _preserve_draft(db, row, replacement, name):
    prior = json.loads(row.draft)
    if prior != fresh_draft() and prior != replacement:
        _freeze(db, row, prior, name)


def _request(data, kind):
    token = data.get("request_id")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", token):
        raise ValueError("request_id must contain 8–128 letters, numbers, underscores or hyphens")
    return token, hashlib.sha256(evidence_service.encoded({"kind": kind, **data})).hexdigest()


def _job_selection(data):
    if not isinstance(data.get("job_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", data["job_id"]):
        raise ValueError("Choose a saved job identity")
    trial = data.get("trial_id")
    if trial is not None and (
        not isinstance(trial, str) or not re.fullmatch(r"[a-f0-9]{64}", trial)
    ):
        raise ValueError("Choose a saved trial identity")
    target = data.get("experiment_id")
    if target is not None and (
        not isinstance(target, str) or not re.fullmatch(r"[a-f0-9]{32}", target)
    ):
        raise ValueError("Choose a saved experiment identity")


def _previous(db, owner, token, fingerprint):
    prior = db.get(ResearchLibraryRequest, (owner, token))
    if prior and prior.payload_hash != fingerprint:
        raise ValueError("This request identity was already used for different research")
    return prior


def _run_response(store, owner, identifier, version_id, job_id):
    with store.sessions() as db:
        _owned(db, owner, identifier)
        version = _version_receipt(db.get(ResearchSetupVersion, version_id))
    return {
        "experiment": get_experiment(store, owner, identifier),
        "version": version,
        "job": evidence_service.job_receipt(
            store, evidence_service.get_job(store, owner, job_id), experiment_id=identifier
        ),
    }


def run_experiment(store, owner, identifier, data):
    from services.research_portfolio import resolve_inputs

    _object(data, {"revision", "request_id"}, "research launch")
    token, fingerprint = _request({**data, "experiment_id": identifier}, "run")
    with store.sessions() as db:
        row = _owned(db, owner, identifier)
        prior = _previous(db, owner, token, fingerprint)
        if prior:
            return _run_response(store, owner, identifier, prior.version_id, prior.job_id)
        _revision(row, data.get("revision"))
        _editable(row)
        draft = json.loads(row.draft)
    evidence = resolve_inputs(store, owner, portfolio_payload(draft))
    return _enqueue(store, owner, identifier, data, token, fingerprint, draft, evidence)


def _enqueue(
    store,
    owner,
    identifier,
    data,
    token,
    fingerprint,
    draft,
    evidence,
    *,
    role="run",
    parents=None,
    with_reuse=False,
    source_fences=(),
    admission_guard=None,
    publication_hook=None,
):
    from research.connectors.registry import policy_for_request
    from research.engine import validate_config
    from services.research_portfolio import validate_submission

    portfolio = evidence["portfolio"]
    kind = "portfolio_optimize" if portfolio.get("optimization") else "portfolio_backtest"
    specification = validate_submission(
        evidence, kind, {"portfolio": portfolio, "versions": evidence["versions"]}
    )
    config = validate_config({"initial_capital": portfolio["capital"]})
    # Canonical artifact creation follows the existing atomic artifact publisher.
    # Uncommitted files are harmless pruning candidates; no orphan metadata is made.
    _, receipt = evidence_service.register_source(store, owner, evidence, publish=False)
    artifact = hashlib.sha256(evidence_service.encoded(evidence)).hexdigest()
    source_id = receipt["id"]
    identity = hashlib.sha256(
        evidence_service.encoded(
            {
                "source_id": source_id,
                "config": config,
                "kind": kind,
                "specification": specification,
                "policy_version": policy_for_request(evidence["snapshot"], specification),
            }
        )
    ).hexdigest()
    # Core retry key has a separate namespace from callers of the older job API.
    core_token = "library_" + hashlib.sha256(f"{owner}:{token}".encode()).hexdigest()
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        row = _owned(db, owner, identifier)
        prior = _previous(db, owner, token, fingerprint)
        if prior:
            version_id, job_id = prior.version_id, prior.job_id
        else:
            _revision(row, data.get("revision"))
            _editable(row)
            for source_parent in ([parents] if parents else []) + list(source_fences):
                parent = db.get(ResearchJob, source_parent.get("parent_job_id"))
                parent_link = db.get(
                    ResearchLibraryJob, (identifier, source_parent.get("parent_job_id"))
                )
                if (
                    not parent
                    or parent.owner != owner
                    or parent.status != "completed"
                    or parent.result_artifact != source_parent.get("parent_result_artifact")
                    or not parent_link
                    or parent_link.version_id != source_parent.get("parent_version_id")
                ):
                    raise ValueError(
                        "The original saved result changed; reopen it before replaying"
                    )
            if admission_guard is not None:
                admission_guard(db, row)
            _check_sources(db, owner, draft)
            if (
                db.scalar(
                    select(func.count())
                    .select_from(ResearchJob)
                    .where(ResearchJob.status.in_(evidence_service.ACTIVE))
                )
                >= 4
            ):
                raise ValueError("Research queue is full (four active or pending jobs)")
            evidence_service.ensure_storage_capacity(
                store, len(row.draft) * 2 + len(evidence_service.encoded(specification)) + 32768
            )
            version = _freeze(
                db,
                row,
                draft,
                name="Exact replay" if role == "replay" else None,
                portfolio=portfolio,
                parents=parents,
            )
            version_id, job_id, now = version.id, uuid.uuid4().hex, time.time()
            if db.get(ResearchSource, source_id) is None:
                db.add(ResearchSource(id=source_id, owner=owner, artifact=artifact, created_at=now))
                db.add(
                    ResearchSourceReceipt(
                        source_id=source_id, receipt=evidence_service.encoded(receipt).decode()
                    )
                )
            db.add(
                ResearchJob(
                    id=job_id,
                    owner=owner,
                    source_id=source_id,
                    config=evidence_service.encoded(config).decode(),
                    status="queued",
                    progress=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                ResearchExperiment(
                    job_id=job_id,
                    kind=kind,
                    specification=evidence_service.encoded(specification).decode(),
                    identity=identity,
                    counts="{}",
                )
            )
            db.add(
                ResearchRequest(owner=owner, token=core_token, payload_hash=identity, job_id=job_id)
            )
            db.add(
                ResearchLibraryJob(
                    experiment_id=identifier,
                    job_id=job_id,
                    version_id=version_id,
                    role=role,
                    created_at=now,
                )
            )
            db.add(
                ResearchLibraryRequest(
                    owner=owner,
                    token=token,
                    payload_hash=fingerprint,
                    experiment_id=identifier,
                    kind=role,
                    version_id=version_id,
                    job_id=job_id,
                )
            )
            _touch(row)
            if publication_hook is not None:
                publication_hook(db, row, version, job_id)
    response = _run_response(store, owner, identifier, version_id, job_id)
    return {**response, "reused": bool(prior)} if with_reuse else response


def replay_experiment(store, owner, identifier, data):
    from services.research_portfolio import replay_inputs

    _object(
        data, {"revision", "request_id", "job_id", "trial_id", "period"}, "exact research replay"
    )
    _job_selection(data)
    token, fingerprint = _request({**data, "experiment_id": identifier}, "replay")
    with store.sessions() as db:
        row = _owned(db, owner, identifier)
        prior = _previous(db, owner, token, fingerprint)
        if prior:
            return _run_response(store, owner, identifier, prior.version_id, prior.job_id)
        _revision(row, data.get("revision"))
        _editable(row)
        link = db.get(ResearchLibraryJob, (identifier, data.get("job_id")))
        if link is None:
            raise LookupError("Saved result is not part of this experiment")
        parent_version_id = link.version_id
    job, evidence = replay_inputs(
        store,
        owner,
        data["job_id"],
        trial_id=data.get("trial_id"),
        period=data.get("period", "selection"),
    )
    portfolio = evidence["portfolio"]
    draft = fresh_draft()
    draft.update(portfolio=portfolio, equalWeights=False)
    draft = normalize_draft(store, owner, draft)
    parents = {
        "parent_job_id": job.id,
        "parent_result_artifact": job.result_artifact,
        "parent_trial_id": data.get("trial_id"),
        "parent_version_id": parent_version_id,
    }
    return _enqueue(
        store,
        owner,
        identifier,
        data,
        token,
        fingerprint,
        draft,
        evidence,
        role="validation" if data.get("period") == "evaluation" else "replay",
        parents=parents,
    )


def from_job(store, owner, data):
    _object(
        data,
        {"job_id", "trial_id", "mode", "name", "request_id", "experiment_id", "revision"},
        "saved result copy",
    )
    if data.get("mode") not in ("backtest", "optimize"):
        raise ValueError("Choose backtest or optimize for the copied setup")
    _job_selection(data)
    token, fingerprint = _request(data, "from_job")
    with store.sessions() as db:
        prior = _previous(db, owner, token, fingerprint)
        if prior:
            return get_experiment(store, owner, prior.experiment_id)
        if data.get("experiment_id"):
            target = _owned(db, owner, data["experiment_id"])
            _revision(target, data.get("revision"))
            _editable(target)
    job = evidence_service.get_job(store, owner, data.get("job_id"))
    if job.status != "completed" or not job.result_artifact:
        raise ValueError("Choose a completed portfolio result")
    bundle = evidence_service.read_artifact(store, job.result_artifact)
    if bundle.get("kind") not in ("portfolio_backtest", "portfolio_optimize"):
        raise ValueError("Choose a completed portfolio result")
    report = bundle["result"]
    inputs = evidence_service.read_artifact(store, bundle["inputs_artifact"])
    portfolio = deepcopy(report.get("portfolio") or inputs["portfolio"])
    chosen = report["strategies"]
    trial_id = data.get("trial_id")
    if trial_id is not None:
        matches = [
            item
            for item in report.get("experiment", {}).get("rows", [])
            if item["config_id"] == trial_id
        ]
        if len(matches) != 1:
            raise ValueError("Choose an actual saved trial from this result")
        chosen = matches[0]["strategies"]
    definitions = {item["id"]: item for item in chosen}
    if set(definitions) != {item["id"] for item in portfolio["strategies"]}:
        raise ValueError("Saved strategy identities do not match")
    for strategy in portfolio["strategies"]:
        selected = definitions[strategy["id"]]
        strategy.update(
            config=deepcopy(selected["config"]), allocation_pct=selected["allocation_pct"]
        )
        strategy.setdefault("search", {})
    draft = fresh_draft()
    draft.update(
        portfolio=portfolio,
        optimizing=data["mode"] == "optimize",
        equalWeights=False,
        optimization=deepcopy(portfolio.pop("optimization", draft["optimization"])),
    )
    draft = normalize_draft(store, owner, draft)
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        prior = _previous(db, owner, token, fingerprint)
        if prior:
            identifier = prior.experiment_id
        else:
            _check_sources(db, owner, draft)
            evidence_service.ensure_storage_capacity(store, len(_bounded_json(draft)) + 16384)
            if data.get("experiment_id"):
                row = _owned(db, owner, data["experiment_id"])
                _revision(row, data.get("revision"))
                _editable(row)
                _preserve_draft(db, row, draft, "Draft before using saved result")
                row.draft = _bounded_json(draft)
                if "name" in data:
                    row.name = _text(data["name"], 120, "Name")
                _link_sources(db, row.id, draft)
                _touch(row)
            else:
                row = _new_experiment(
                    db,
                    owner,
                    draft,
                    _metadata({"name": data.get("name") or portfolio["name"]}, create=True),
                )
            identifier = row.id
            row.parent_job_id, row.parent_result_artifact, row.parent_trial_id = (
                job.id,
                job.result_artifact,
                trial_id,
            )
            row.parent_version_id = None
            link = db.get(ResearchLibraryJob, (identifier, job.id))
            if link is None:
                db.add(
                    ResearchLibraryJob(
                        experiment_id=identifier,
                        job_id=job.id,
                        role="baseline",
                        created_at=time.time(),
                    )
                )
            db.add(
                ResearchLibraryRequest(
                    owner=owner,
                    token=token,
                    payload_hash=fingerprint,
                    experiment_id=identifier,
                    kind="from_job",
                    job_id=job.id,
                )
            )
    return get_experiment(store, owner, identifier)


def list_versions(store, owner, *, search="", archived=False, limit=20, offset=0):
    limit, offset = _page(limit, offset)
    with store.sessions() as db:
        query = select(ResearchSetupVersion, ResearchLibraryExperiment).join(
            ResearchLibraryExperiment,
            ResearchLibraryExperiment.id == ResearchSetupVersion.experiment_id,
        )
        rows = db.execute(
            _filter(query, owner, search, archived)
            .order_by(ResearchSetupVersion.created_at.desc(), ResearchSetupVersion.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "items": [
                {**_version_receipt(version, full=False), "experiment_name": row.name}
                for version, row in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
        }


def list_studies(store, owner, *, search="", archived=False, limit=20, offset=0):
    limit, offset = _page(limit, offset)
    with store.sessions() as db:
        query = (
            select(ResearchJob, ResearchLibraryJob, ResearchLibraryExperiment)
            .join(ResearchLibraryJob, ResearchLibraryJob.job_id == ResearchJob.id)
            .join(
                ResearchLibraryExperiment,
                ResearchLibraryExperiment.id == ResearchLibraryJob.experiment_id,
            )
            .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
            .where(ResearchJob.owner == owner, ResearchExperiment.kind == "portfolio_optimize")
        )
        rows = db.execute(
            _filter(query, owner, search, archived)
            .order_by(ResearchJob.created_at.desc(), ResearchJob.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
    return {
        "items": [
            {
                **evidence_service.job_receipt(store, job, experiment_id=row.id),
                "experiment_id": row.id,
                "experiment_name": row.name,
                "version_id": link.version_id,
            }
            for job, link, row in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


def delete_experiment(store, owner, identifier, data):
    _object(data, {"revision"}, "experiment deletion")
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        row = _owned(db, owner, identifier)
        _revision(row, data.get("revision"))
        has_jobs = db.scalar(
            select(func.count())
            .select_from(ResearchLibraryJob)
            .where(ResearchLibraryJob.experiment_id == identifier)
        )
        has_versions = db.scalar(
            select(func.count())
            .select_from(ResearchSetupVersion)
            .where(ResearchSetupVersion.experiment_id == identifier)
        )
        has_candidates = db.scalar(
            select(func.count())
            .select_from(ResearchShortlistCandidate)
            .where(ResearchShortlistCandidate.experiment_id == identifier)
        )
        has_comparisons = db.scalar(
            select(func.count())
            .select_from(ResearchComparison)
            .where(ResearchComparison.experiment_id == identifier)
        )
        if (
            has_jobs
            or has_versions
            or has_candidates
            or has_comparisons
            or any(getattr(row, key) for key in PARENTS)
        ):
            raise ValueError(
                "This experiment retains saved evidence or setup versions. Archive it instead."
            )
        db.execute(
            delete(ResearchLibrarySource).where(ResearchLibrarySource.experiment_id == identifier)
        )
        db.execute(
            delete(ResearchLibraryRequest).where(ResearchLibraryRequest.experiment_id == identifier)
        )
        db.execute(
            delete(ResearchChartinkImport).where(ResearchChartinkImport.experiment_id == identifier)
        )
        db.execute(
            update(ResearchChartinkImport)
            .where(ResearchChartinkImport.parent_experiment_id == identifier)
            .values(parent_experiment_id=None)
        )
        db.delete(row)
    return {"deleted": True, "id": identifier}
