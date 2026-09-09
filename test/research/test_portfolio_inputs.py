# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Compact saved-input identity, naming and owner-scoped paging."""

import copy
import hashlib
import io
import json

from sqlalchemy import event
from test_jobs import app, client  # noqa: F401

from database.research_db import ResearchSourceReceipt
from services import research_portfolio as portfolio
from services import scanner_research_service as service

RAW = b"Date,Symbol\n2026-01-05,AAA\n2026-01-06,BBB\n"


def test_uploaded_filename_is_retained_and_paths_controls_are_removed(app, client):
    uploaded = client.post(
        "/scanner-research/api/portfolio/inputs",
        data={"file": (io.BytesIO(RAW), "My breakout.csv")},
    )
    assert uploaded.status_code == 201, uploaded.json
    assert uploaded.json["receipt"]["filename"] == "My breakout.csv"
    store = app.extensions["research_store"]
    saved = service.create_source(
        store,
        "owner",
        RAW,
        "broker",
        defer_preparation=True,
        filename="C:\\private\\folder\\Breakout\u202e\x00.csv",
    )
    assert saved["receipt"]["filename"] == "Breakout.csv"
    assert saved["receipt"]["original_csv_sha256"] == hashlib.sha256(RAW).hexdigest()
    evidence = service.source_for(store, "owner", saved["id"])
    assert evidence["original_csv"] == RAW.decode()
    assert saved["receipt"]["signals_sha256"] == hashlib.sha256(
        service.encoded(evidence["signals"])
    ).hexdigest()
    assert "preparation_job" not in saved
    assert client.get("/scanner-research/api/jobs").json == []


def test_picker_groups_exact_inputs_across_price_receipts_without_loading_artifacts(app, monkeypatch):
    store = app.extensions["research_store"]
    original = service.create_source(store, "owner", RAW, "broker", defer_preparation=True)
    evidence = service.source_for(store, "owner", original["id"])
    original_bytes = service.encoded(evidence)
    prepared = copy.deepcopy(evidence)
    prepared["snapshot"]["provenance"]["provider"] = "prepared-native-history"
    _, derived = service.register_source(store, "owner", prepared)
    # A different file name still refers to exactly the same bytes/observations.
    renamed = service.create_source(
        store, "owner", RAW, "broker", defer_preparation=True, filename="Renamed.csv"
    )
    service.create_source(store, "someone-else", RAW, "broker", defer_preparation=True)
    combined = copy.deepcopy(evidence)
    combined["receipt"]["input_type"] = "portfolio"
    service.register_source(store, "owner", combined)
    assert service.encoded(service.source_for(store, "owner", original["id"])) == original_bytes
    monkeypatch.setattr(
        service,
        "read_artifact",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Picker must not load price evidence")),
    )
    items = portfolio.list_inputs(store, "owner", limit=1)
    assert len(items["items"]) == 1
    assert items["items"][0]["id"] in {original["id"], derived["id"], renamed["id"]}
    assert items["items"][0]["created_at"] > 0
    assert items["next_offset"] is None
    assert portfolio.list_inputs(store, "unknown")["items"] == []


def test_same_dates_counts_or_csv_bytes_do_not_prove_same_normalized_signals(app):
    store = app.extensions["research_store"]
    original = service.create_source(store, "owner", RAW, "broker", defer_preparation=True)
    evidence = service.source_for(store, "owner", original["id"])
    changed = copy.deepcopy(evidence)
    changed["signals"][0]["symbol"] = "CCC"
    _, changed_source = service.register_source(store, "owner", changed)
    assert changed_source["receipt"]["signals_sha256"] != original["receipt"]["signals_sha256"]
    # Raw bytes differ even when normalization is identical.
    different_file = service.create_source(
        store, "owner", RAW + b"\n", "broker", defer_preparation=True
    )
    assert different_file["receipt"]["signals_sha256"] == original["receipt"]["signals_sha256"]
    assert len(portfolio.list_inputs(store, "owner")["items"]) == 3


def test_legacy_missing_identity_is_never_guessed_and_paging_is_after_grouping(app):
    store = app.extensions["research_store"]
    ids = []
    for index in range(3):
        source = service.create_source(
            store, "owner", RAW, "broker", defer_preparation=True, filename=f"Set {index}.csv"
        )
        ids.append(source["id"])
    with store.sessions.begin() as db:
        for identifier in ids[:2]:
            row = db.get(ResearchSourceReceipt, identifier)
            receipt = json.loads(row.receipt)
            receipt["receipt"].pop("signals_sha256")
            receipt["receipt"].pop("filename")
            row.receipt = service.encoded(receipt).decode()
    first = portfolio.list_inputs(store, "owner", limit=2)
    assert len(first["items"]) == 2
    assert first["next_offset"] == 2
    last = portfolio.list_inputs(store, "owner", limit=2, offset=2)
    assert len(last["items"]) == 1
    assert last["next_offset"] is None
    assert {item["id"] for item in first["items"] + last["items"]} == set(ids)


def test_picker_sessions_close_on_repeated_reads_and_decode_failure(app):
    store = app.extensions["research_store"]
    source = service.create_source(store, "owner", RAW, "broker", defer_preparation=True)
    opened, closed = [], []

    def on_connect(*args):
        opened.append(1)

    def on_close(*args):
        closed.append(1)

    event.listen(store.engine, "connect", on_connect)
    event.listen(store.engine, "close", on_close)
    try:
        for _ in range(10):
            assert len(portfolio.list_inputs(store, "owner", limit=1)["items"]) == 1
            assert len(opened) == len(closed)
        with store.sessions.begin() as db:
            db.get(ResearchSourceReceipt, source["id"]).receipt = "null"
        import pytest

        with pytest.raises(TypeError):
            portfolio.list_inputs(store, "owner")
        assert len(opened) == len(closed)
    finally:
        event.remove(store.engine, "connect", on_connect)
        event.remove(store.engine, "close", on_close)
