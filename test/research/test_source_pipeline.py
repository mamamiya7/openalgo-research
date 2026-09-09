# ruff: noqa: F811 -- pytest fixtures imported for injection
"""HTTP CSV → actual native Fyers chain → worker → immutable source/reopen."""

import copy
import io
import threading
import time

import pytest
from test_acquisition import candle, reference
from test_jobs import app, client  # noqa: F401
from test_native_history_evidence import native_chain  # noqa: F401

from database.research_db import ResearchExperiment, ResearchJob, ResearchStore, ResearchWorker
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_storage import backup_store, restore_store


def controlled_source(monkeypatch, store):
    # This regression deliberately restores the previous daily source contract.
    # New HTTP minute planning is covered in test_native_price_workflow.
    original_create = service.create_source
    monkeypatch.setattr(
        service,
        "create_source",
        lambda store, owner, raw, kind, requirements=None, **kwargs: original_create(
            store, owner, raw, kind, **kwargs
        ),
    )
    snapshot = reference()
    for field in ("bars", "raw_bars"):
        snapshot[field]["BBB"] = copy.deepcopy(snapshot[field]["AAA"])
    snapshot["provenance"]["symbol_identities"]["BBB"] = copy.deepcopy(
        snapshot["provenance"]["symbol_identities"]["AAA"]
    )
    monkeypatch.setenv("RESEARCH_PUBLIC_EVIDENCE_DIR", "controlled-generated-evidence")
    monkeypatch.setenv("RESEARCH_HISTORIFY_DATABASE_PATH", str(store.root / "isolated.duckdb"))
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", str(store.root / "isolated.duckdb"))
    monkeypatch.setattr(
        "research.evidence_import.public_snapshot", lambda *a, **kw: copy.deepcopy(snapshot)
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda owner: {
            "auth_token": "NEVER_PERSIST_THIS_TEST_TOKEN",
            "broker": "fyers",
            "feed_token": None,
        },
    )
    written = []
    stored_rows = {}

    def write_archive(symbol, rows, target):
        written.append((symbol, rows))
        stored_rows.setdefault(symbol, {}).update({row["timestamp"]: row for row in rows})

    def read_archive(symbol, first, last, target):
        from services.research_acquisition import trading_date

        return [
            row
            for row in stored_rows.get(symbol, {}).values()
            if first <= trading_date(row["timestamp"]) <= last
        ]

    monkeypatch.setattr(
        "services.research_acquisition.native_historify_ingest",
        write_archive,
    )
    monkeypatch.setattr("services.research_acquisition.native_historify_read", read_archive)
    return written


def upload(client):
    result = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (io.BytesIO(b"Date,Symbol\n2026-01-05,AAA\n2026-01-05,BBB\n"), "dated.csv"),
            "source": "broker",
        },
    )
    assert result.status_code == 201, result.json
    return result.json


def raw_candles():
    return [
        [candle(day)[field] for field in ("timestamp", "open", "high", "low", "close", "volume")]
        for day in reference()["sessions"]
    ]


@pytest.mark.parametrize("recovery", ["resume", "retry"])
def test_csv_native_expiry_missing_windows_checked_source_run_export_and_restore(
    app, client, native_chain, monkeypatch, tmp_path, recovery
):
    store = app.extensions["research_store"]
    written = controlled_source(monkeypatch, store)
    _, _, _, calls, install = native_chain
    install(
        [
            (200, {"s": "ok", "candles": raw_candles()}),
            (200, {"s": "error", "code": -16, "message": "Access token expired"}),
        ]
    )
    pending = upload(client)
    first = pending["preparation_job"]["id"]
    assert pending["preparation_job"]["kind"] == "acquire"
    worker.acquire(store, "pipeline-worker")
    try:
        worker.run_one(store, "pipeline-worker")
        failed = client.get(f"/scanner-research/api/jobs/{first}").json
        assert failed["status"] == "failed" and failed["resumable"]
        assert "expired" in failed["error"]
        assert len(calls) == 2 and len(written) == 1
        # A backup restores only canonical artifacts. Rebuild disposable sidecars
        # from the exact embedded checkpoint rather than relying on live files.
        for path in (store.root / "acquisition-receipts").glob("*.json"):
            path.unlink()
        recovered = client.post(
            f"/scanner-research/api/jobs/{first}/{recovery}",
            json={"request_id": "recover-native-price-attempt"},
        ).json
        target = recovered["id"]
        assert (target == first) == (recovery == "resume")
        install([(200, {"s": "ok", "candles": raw_candles()})])
        worker.run_one(store, "pipeline-worker")
        complete = client.get(f"/scanner-research/api/jobs/{target}").json
        assert complete["status"] == "completed", complete
        assert len(calls) == 3 and "BBB" in calls[-1]["url"]
        prepared = complete["result"]["prepared_source"]
        assert prepared["coverage"]["status"] != "blocked"
        assert upload(client)["preparation_job"]["id"] == target
        job = client.post(
            "/scanner-research/api/jobs", json={"source_id": prepared["id"], "config": {}}
        ).json
        monkeypatch.setattr(
            "services.research_sources.resolve_broker_session",
            lambda _: pytest.fail("saved calculation must not resolve broker credentials"),
        )
        worker.run_one(store, "pipeline-worker")
        exact = client.get(f"/scanner-research/api/jobs/{job['id']}/export")
        assert exact.status_code == 200
        assert b"NEVER_PERSIST_THIS_TEST_TOKEN" not in exact.data
        retained = [item["receipt"] for item in exact.json["inputs"]["acquisition_receipts"]]
        assert sum(item["version"] == "openalgo-history-receipt-v2" for item in retained) == 3
        assert any(item["version"] == "openalgo-historify-receipt-v1" for item in retained)
        assert exact.json["result"]["equity_curve"]
    finally:
        worker.release(store, "pipeline-worker")
    backup_store(store, tmp_path.with_name(tmp_path.name + "-backup"))
    restore_store(
        tmp_path.with_name(tmp_path.name + "-backup"),
        tmp_path.with_name(tmp_path.name + "-restored"),
    )
    restored = ResearchStore(tmp_path.with_name(tmp_path.name + "-restored"))
    try:
        row = service.get_job(restored, "research-test", job["id"])
        bundle = service.read_artifact(restored, row.result_artifact)
        inputs = service.read_artifact(restored, bundle["inputs_artifact"])
        assert inputs["acquisition_receipts"] == exact.json["inputs"]["acquisition_receipts"]
        assert service.read_artifact(restored, inputs["reference_artifact"])["raw_bars"]
    finally:
        restored.close()


def test_acquisition_heartbeat_and_cancel_during_blocking_native_request(
    app, client, native_chain, monkeypatch
):
    store = app.extensions["research_store"]
    controlled_source(monkeypatch, store)
    pending = upload(client)["preparation_job"]["id"]
    entered, release_request = threading.Event(), threading.Event()

    def blocking(**request):
        entered.set()
        assert release_request.wait(5)
        request["request_control"]["check"]()
        return True, {"data": []}, 200

    monkeypatch.setattr("services.research_acquisition.native_history", blocking)
    monkeypatch.setattr(worker, "NETWORK_HEARTBEAT_SECONDS", 0.03)
    worker.acquire(store, "blocking-worker")
    thread = threading.Thread(target=worker.run_one, args=(store, "blocking-worker"))
    try:
        thread.start()
        assert entered.wait(3)
        with store.sessions.begin() as db:
            db.get(ResearchWorker, 1).heartbeat = 1
        deadline = time.monotonic() + 3
        refreshed = False
        while time.monotonic() < deadline:
            with store.sessions() as db:
                refreshed = db.get(ResearchWorker, 1).heartbeat > 1
            if refreshed:
                break
            threading.Event().wait(0.02)
        assert refreshed
        assert client.get("/scanner-research/api/health").json["worker_state"] == "online"
        client.post(f"/scanner-research/api/jobs/{pending}/cancel")
        release_request.set()
        thread.join(5)
        assert not thread.is_alive()
        assert service.get_job(store, "research-test", pending).status == "cancelled"
        assert not any(t.name == "research-network-lease" for t in threading.enumerate())
    finally:
        release_request.set()
        thread.join(5)
        worker.release(store, "blocking-worker")


def test_official_update_endpoint_is_queued_owner_scoped_and_publishes_receipt(
    app, client, monkeypatch
):
    from test_jobs import source

    source_id = source(client)
    calls = []

    def update(end_date, progress=None, cancelled=None, **kwargs):
        calls.append(end_date)
        assert not cancelled()
        progress(1, 1)
        return {"date_from": "2026-01-05", "date_to": end_date, "manifest_sha256": "e" * 64}

    monkeypatch.setattr("services.research_sources.update_evidence", update)
    response = client.post(
        "/scanner-research/api/evidence-updates",
        json={
            "source_id": source_id,
            "end_date": "2026-09-04",
            "request_id": "official-evidence-update",
        },
    )
    assert response.status_code == 202 and calls == []
    store = app.extensions["research_store"]
    worker.acquire(store, "evidence-worker")
    try:
        worker.run_one(store, "evidence-worker")
    finally:
        worker.release(store, "evidence-worker")
    result = client.get(f"/scanner-research/api/jobs/{response.json['id']}").json
    assert (
        result["status"] == "completed"
        and result["result"]["evidence_update"]["date_to"] == calls[0]
    )
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    assert (
        client.post(
            "/scanner-research/api/evidence-updates",
            json={"source_id": source_id, "end_date": "2026-09-04"},
        ).status_code
        == 404
    )


def test_actual_official_extension_worker_then_newer_csv_and_old_snapshot(
    app, client, tmp_path, monkeypatch
):
    from test_data_audit import tiny_bundle
    from test_evidence_import import archive, row

    from research import evidence_import as importer

    store = app.extensions["research_store"]
    base, extension = tmp_path / "reviewed-base", tmp_path / "extensions"
    monkeypatch.setenv("RESEARCH_PUBLIC_EVIDENCE_DIR", str(base))
    monkeypatch.setenv("RESEARCH_PUBLIC_EXTENSION_DIR", str(extension))

    def public_csv(day):
        return client.post(
            "/scanner-research/api/sources",
            data={
                "file": (io.BytesIO(f"Date,Symbol\n{day},AAA\n".encode()), "dates.csv"),
                "source": "public",
            },
        ).json

    with tiny_bundle(base, ["2026-01-05", "2026-01-06"]):
        old = public_csv("2026-01-05")
        worker.acquire(store, "extension-worker")
        try:
            worker.run_one(store, "extension-worker")
            original = client.get(
                f"/scanner-research/api/jobs/{old['preparation_job']['id']}/export"
            ).data
            payloads = {
                importer.archive_url(day): (
                    base / "nse_daily" / (day.replace("-", "") + ".zip")
                ).read_bytes()
                for day in ["2026-01-05", "2026-01-06"]
            }
            record = row()
            record[0] = "2026-01-07"
            payloads[importer.archive_url("2026-01-07")] = archive([record])
            monkeypatch.setattr(importer, "_official_fetch", lambda url: payloads[url])
            update = client.post(
                "/scanner-research/api/evidence-updates",
                json={
                    "source_id": old["id"],
                    "end_date": "2026-01-07",
                    "request_id": "actual-official-update",
                },
            ).json
            worker.run_one(store, "extension-worker")
            assert (
                client.get(f"/scanner-research/api/jobs/{update['id']}").json["status"]
                == "completed"
            )
            assert (
                client.get("/scanner-research/api/source-capabilities").json["public"]["date_to"]
                == "2026-01-07"
            )
            new = public_csv("2026-01-07")
            worker.run_one(store, "extension-worker")
            saved = client.get(f"/scanner-research/api/jobs/{new['preparation_job']['id']}").json
            assert saved["status"] == "completed", saved
            assert saved["result"]["prepared_source"]["id"] != old["id"]
            assert (
                client.get(f"/scanner-research/api/jobs/{old['preparation_job']['id']}/export").data
                == original
            )
        finally:
            worker.release(store, "extension-worker")


def test_cancel_during_prepared_source_publication_hides_new_source(app, client, monkeypatch):
    from research.data import fixture_snapshot

    monkeypatch.setenv("RESEARCH_PUBLIC_EVIDENCE_DIR", "controlled-only")
    monkeypatch.setattr(
        "research.evidence_import.public_snapshot",
        lambda signals, *a, **kw: fixture_snapshot(signals),
    )
    pending = client.post(
        "/scanner-research/api/sources",
        data={"file": (io.BytesIO(b"Date,Symbol\n2026-01-05,AAA\n"), "x.csv"), "source": "public"},
    ).json
    store = app.extensions["research_store"]
    actual = worker.prepare_source

    def cancel_after_prepare(*args, **kwargs):
        result = actual(*args, **kwargs)
        service.cancel(store, "research-test", pending["preparation_job"]["id"])
        return result

    monkeypatch.setattr(worker, "prepare_source", cancel_after_prepare)
    worker.acquire(store, "publication-worker")
    try:
        worker.run_one(store, "publication-worker")
    finally:
        worker.release(store, "publication-worker")
    assert (
        client.get(f"/scanner-research/api/jobs/{pending['preparation_job']['id']}").json["status"]
        == "cancelled"
    )
    assert client.get("/scanner-research/api/sources").json == []
