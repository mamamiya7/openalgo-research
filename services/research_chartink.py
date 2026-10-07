"""Authenticated Chartink history intake into native, immutable research inputs.

The browser supplies an authorized export. This service never contacts Chartink,
a broker, Historify or a calculation worker. Native artifact publication precedes
one fenced metadata transaction, matching library run admission.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import select

from database.research_db import (
    ResearchChartinkImport,
    ResearchLibraryExperiment,
    ResearchLibrarySource,
    ResearchSource,
    ResearchSourceReceipt,
)
from research.engine import config_defaults
from research.signals import normalize_csv
from services import research_library as library
from services import scanner_research_service as evidence_service

PROTOCOL_VERSION = 1
MAX_CSV_BYTES = 8 * 1024 * 1024
# A JSON string can use six ASCII bytes for one escaped source byte.
MAX_BODY_BYTES = 6 * MAX_CSV_BYTES + 65536


class ImportConflict(ValueError):
    pass


class ImportTooLarge(ValueError):
    pass


def capabilities():
    return {"protocol_version": PROTOCOL_VERSION, "max_csv_bytes": MAX_CSV_BYTES}


def _text(value, label, limit):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"Supply a valid Chartink {label}")
    return value.strip()


def validate_capture(data):
    if not isinstance(data, dict) or set(data) != {"version", "request_id", "csv_text", "source"}:
        raise ValueError("Supply a versioned Chartink history import")
    if type(data["version"]) is not int or data["version"] != PROTOCOL_VERSION:
        raise ValueError("This Chartink import version is not supported")
    token = data["request_id"]
    try:
        if not isinstance(token, str) or str(uuid.UUID(token)) != token.lower():
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Supply a valid Chartink capture request identity") from None
    token = token.lower()
    text = data["csv_text"]
    if not isinstance(text, str):
        raise ValueError("Supply the original Chartink history CSV text")
    if len(text) > MAX_CSV_BYTES:
        raise ImportTooLarge("CSV upload exceeds 8 MiB")
    try:
        raw = text.encode("utf-8")
    except UnicodeError:
        raise ValueError("Chartink CSV must be valid UTF-8") from None
    if len(raw) > MAX_CSV_BYTES:
        raise ImportTooLarge("CSV upload exceeds 8 MiB")
    source = data["source"]
    fields = {"url", "title", "selected_period", "captured_at", "repaints", "export_kind"}
    if not isinstance(source, dict) or set(source) != fields:
        raise ValueError("Supply the Chartink history source details")
    source = dict(source)
    source["url"] = _text(source["url"], "scanner URL", 512)
    parsed = urlsplit(source["url"])
    if (
        parsed.scheme != "https"
        or parsed.netloc != "chartink.com"
        or not re.fullmatch(r"/screener/[A-Za-z0-9][A-Za-z0-9_-]{0,199}/?", parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTPS scanner URL on chartink.com/screener/")
    source["url"] = source["url"].rstrip("/")
    source["title"] = _text(source["title"], "scanner title", 240)
    source["selected_period"] = _text(source["selected_period"], "selected history period", 80)
    source["captured_at"] = _text(source["captured_at"], "capture time", 40)
    try:
        captured_at = datetime.fromisoformat(source["captured_at"].replace("Z", "+00:00"))
        if captured_at.tzinfo is None:
            raise ValueError
    except ValueError:
        raise ValueError("Supply a capture timestamp with its timezone") from None
    if source["repaints"] is not None and type(source["repaints"]) is not bool:
        raise ValueError("Chartink repaint information must be true, false or unknown")
    if source["export_kind"] != "chartink_history_csv":
        raise ValueError("Import Chartink Backtest History, rather than today's scanner table")
    fingerprint = hashlib.sha256(
        evidence_service.encoded({**data, "request_id": token})
    ).hexdigest()
    identity = hashlib.sha256(
        evidence_service.encoded(
            {
                "version": PROTOCOL_VERSION,
                "csv_sha256": hashlib.sha256(raw).hexdigest(),
                "source": {key: value for key, value in source.items() if key != "captured_at"},
            }
        )
    ).hexdigest()
    return token, raw, source, fingerprint, identity


def _previous(db, owner, token, fingerprint):
    prior = db.get(ResearchChartinkImport, (owner, token))
    if prior and prior.payload_hash != fingerprint:
        raise ImportConflict("This capture identity was already used for different scanner data")
    return prior


def _response(store, owner, record, *, reused):
    with store.sessions() as db:
        experiment = db.get(ResearchLibraryExperiment, record.experiment_id)
        if experiment is None or experiment.owner != owner or record.owner != owner:
            raise LookupError("Imported experiment not found")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "experiment_id": record.experiment_id,
        "source_id": record.source_id,
        "url": f"/scanner-research?experiment={record.experiment_id}&view=setup",
        "receipt": evidence_service.source_receipt(store, owner, record.source_id),
        "reused": reused,
        "parent_experiment_id": record.parent_experiment_id,
    }


def _matching(db, owner, identity=None, scanner_url=None):
    query = (
        select(ResearchChartinkImport)
        .join(
            ResearchLibraryExperiment,
            ResearchLibraryExperiment.id == ResearchChartinkImport.experiment_id,
        )
        .where(
            ResearchChartinkImport.owner == owner,
            ResearchLibraryExperiment.owner == owner,
            ResearchLibraryExperiment.archived.is_(False),
        )
        .order_by(ResearchChartinkImport.created_at.desc(), ResearchChartinkImport.request_id)
        .limit(1)
    )
    if identity is not None:
        query = query.join(
            ResearchLibrarySource,
            (ResearchLibrarySource.experiment_id == ResearchChartinkImport.experiment_id)
            & (ResearchLibrarySource.source_id == ResearchChartinkImport.source_id)
            & (ResearchLibrarySource.version_id == ""),
        ).where(ResearchChartinkImport.import_identity == identity)
    if scanner_url is not None:
        query = query.where(ResearchChartinkImport.scanner_url == scanner_url)
    return db.scalar(query)


def _evidence(raw, source):
    normalized = normalize_csv(raw)
    normalized["receipt"].update(filename=(source["title"][:108] + ".csv"), chartink=source)
    evidence = {
        **normalized,
        "original_csv": raw.decode("utf-8-sig"),
        "original_csv_base64": base64.b64encode(raw).decode("ascii"),
        "original_csv_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot": {
            "sessions": [],
            "bars": {},
            "coverage": {"status": "preparing", "warnings": []},
            "provenance": {
                "provider": "queued-openalgo-history",
                "synthetic": False,
                "acquisition_mode": "stored_then_broker",
            },
        },
    }
    if any(signal.get("timestamp") for signal in normalized["signals"]):
        from research.requirements import VERSION, normalize_request, select_interval

        evidence["data_request"] = normalize_request(None)[0]
        evidence["data_source_kind"] = "broker"
        evidence["snapshot"]["provenance"].update(
            interval=select_interval(normalized["signals"], evidence["data_request"]),
            requirements_version=VERSION,
            native_price_policy="openalgo-native-history-v1",
            temporal_version="minute-open-v1",
        )
    return evidence


def _draft(source, receipt, previous=None):
    draft = library.fresh_draft()
    draft["optimization"]["trials"] = 25
    config, search = config_defaults(), {}
    if previous:
        old, source_id = previous
        draft["portfolio"].update(
            capital=old["portfolio"]["capital"], engine=old["portfolio"]["engine"]
        )
        draft["optimization"] = deepcopy(old["optimization"])
        strategy = next(
            (row for row in old["portfolio"]["strategies"] if row["source_id"] == source_id), None
        )
        if strategy:
            config, search = deepcopy(strategy["config"]), deepcopy(strategy["search"])
    draft["portfolio"].update(
        name=source["title"][:120],
        strategies=[
            {
                "id": uuid.uuid4().hex,
                "name": source["title"][:80],
                "source_id": receipt["id"],
                "allocation_pct": 100,
                "config": config,
                "search": search,
                "type": "signals",
            }
        ],
    )
    draft["sources"] = {receipt["id"]: receipt}
    return draft


def import_capture(store, owner, data):
    token, raw, source, fingerprint, identity = validate_capture(data)
    with store.sessions() as db:
        prior = _previous(db, owner, token, fingerprint)
        if prior:
            return _response(store, owner, prior, reused=True)
        equivalent = _matching(db, owner, identity=identity)
    # Publish immutable native evidence before its metadata, as library launch does.
    # A failed metadata transaction leaves only unreferenced, safely prunable files.
    receipt, artifact = None, None
    if equivalent is None:
        evidence = _evidence(raw, source)
        _, receipt = evidence_service.register_source(store, owner, evidence, publish=False)
        artifact = hashlib.sha256(evidence_service.encoded(evidence)).hexdigest()
    with store.sessions.begin() as db:
        evidence_service.write_guard(db)
        prior = _previous(db, owner, token, fingerprint)
        equivalent = _matching(db, owner, identity=identity)
        if prior:
            record, reused = prior, True
        else:
            parent_id = None
            if equivalent:
                source_id, experiment_id = equivalent.source_id, equivalent.experiment_id
                parent_id, reused = equivalent.parent_experiment_id, True
                evidence_service.ensure_storage_capacity(store, 16384)
            else:
                if receipt is None:
                    # An equivalent experiment was archived/deleted after the first read.
                    raise ImportConflict(
                        "The earlier import changed. Retry this capture to save a new experiment"
                    )
                previous = _matching(db, owner, scanner_url=source["url"])
                previous_draft = None
                if previous:
                    parent_id = previous.experiment_id
                    previous_draft = (
                        json.loads(db.get(ResearchLibraryExperiment, parent_id).draft),
                        previous.source_id,
                    )
                draft = _draft(source, receipt, previous_draft)
                evidence_service.ensure_storage_capacity(
                    store, len(library._bounded_json(draft)) + 32768
                )
                source_id = receipt["id"]
                if db.get(ResearchSource, source_id) is None:
                    db.add(
                        ResearchSource(
                            id=source_id, owner=owner, artifact=artifact, created_at=time.time()
                        )
                    )
                    db.add(
                        ResearchSourceReceipt(
                            source_id=source_id, receipt=evidence_service.encoded(receipt).decode()
                        )
                    )
                metadata = library._metadata(
                    {
                        "name": source["title"][:120],
                        "notes": "Updated scanner import; the earlier experiment and its saved work are preserved."
                        if parent_id
                        else "",
                    },
                    create=True,
                )
                experiment_id = library._new_experiment(db, owner, draft, metadata).id
                reused = False
            record = ResearchChartinkImport(
                owner=owner,
                request_id=token,
                payload_hash=fingerprint,
                import_identity=identity,
                scanner_url=source["url"],
                source_id=source_id,
                experiment_id=experiment_id,
                parent_experiment_id=parent_id,
                created_at=time.time(),
            )
            db.add(record)
    return _response(store, owner, record, reused=reused)
