"""Saved report overlays use real isolated Historify and preserve original evidence."""

# ruff: noqa: F811 -- shared isolated Flask/native fixtures
import copy
import json
import os
import time
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta

import pytest
from test_historify_scope import native  # noqa: F401
from test_jobs import app, client  # noqa: F401
from test_saved_analysis import completed_parent

from database.research_db import ResearchExperiment, ResearchJob, ResearchStore
from research.market_series import normalize_descriptor
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_historify import NativeHistorifyArchive
from services.research_storage import backup_store, prune_orphans, restore_store

BENCHMARK = {"symbol": "NIFTY", "exchange": "NSE_INDEX", "interval": "D", "role": "benchmark"}


def native_candle(day, close=None):
    # Different month slopes make separate selection/evaluation alignment visible.
    parsed = date.fromisoformat(day)
    value = close if close is not None else 100 + parsed.month * 10 + parsed.day * parsed.month / 10
    return {
        "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
        "open": value,
        "high": value + 1,
        "low": value - 1,
        "close": value,
        "volume": 0,
        "oi": 0,
    }


def days(first, last):
    cursor, end = date.fromisoformat(first), date.fromisoformat(last)
    while cursor <= end:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def setup_native(native, monkeypatch):
    _, path, stats = native
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", str(path))
    monkeypatch.setenv("RESEARCH_HISTORIFY_DATABASE_PATH", str(path))

    @contextmanager
    def reader():
        # The saved-analysis fixture marks every calendar day. This explicit
        # injected schedule keeps its deterministic clock, not a live-calendar claim.
        def window(day, exchange):
            assert exchange == "NSE"
            return {
                name + "_ms": int(
                    datetime.fromisoformat(day.isoformat() + stamp).timestamp() * 1000
                )
                for name, stamp in (("start", "T09:15:00+05:30"), ("end", "T15:30:00+05:30"))
            }

        yield window

    monkeypatch.setattr("services.research_native_calendar._native_reader", reader)
    credentials = []

    def login(owner):
        credentials.append(owner)
        return {"broker": "controlled", "auth_token": "saved-benchmark-test-only"}

    monkeypatch.setattr("services.research_sources.resolve_broker_session", login)
    calls = []

    def history(**request):
        assert stats["active"] == 0
        calls.append(request)
        return (
            True,
            {
                "data": [
                    native_candle(day) for day in days(request["start_date"], request["end_date"])
                ]
            },
            200,
        )

    monkeypatch.setattr("services.research_native_prices.native_history", history)
    monkeypatch.setattr(
        "services.research_portfolio.run",
        lambda *a, **k: pytest.fail("Report enrichment reran execution prices or strategy"),
    )
    return path, calls, credentials, history


def run_worker(app):
    store = app.extensions["research_store"]
    worker.acquire(store, "saved-benchmark-test")
    try:
        assert worker.run_one(store, "saved-benchmark-test")
    finally:
        worker.release(store, "saved-benchmark-test")


def url(parent):
    return f"/scanner-research/api/portfolio/jobs/{parent}/analysis"


def submit(client, parent, body):
    response = client.post(url(parent), json=body)
    assert response.status_code == 202, response.json
    return response.json["analysis_job_id"]


def completed(client, parent):
    response = client.get(url(parent))
    assert response.status_code == 200 and response.json["status"] == "complete", response.json
    return response.json["job"]["result"]


def replace_report(app, parent, artifact, update):
    store = app.extensions["research_store"]
    bundle = service.read_artifact(store, artifact)
    update(bundle["result"])
    report = bundle["result"]
    report["experiment"]["selected_reports"]["winner"] = copy.deepcopy(
        {k: v for k, v in report.items() if k != "experiment"}
    )
    report["experiment"]["rows"][0]["summary"] = copy.deepcopy(report["summary"])
    artifact = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, parent).result_artifact = artifact
    return artifact


@contextmanager
def restored_store(app, tmp_path_factory):
    original = app.extensions["research_store"]
    outside = tmp_path_factory.mktemp("saved-benchmark-recovery")
    backup_store(original, outside / "backup")
    restore_store(outside / "backup", outside / "restored")
    restored = ResearchStore(outside / "restored")
    try:
        restored.initialize()
        # The real backup copies metadata and its artifact closure, never these
        # operational sidecars. The restored worker must recover from evidence.
        assert not (restored.root / "acquisition-receipts").exists()
        app.extensions["research_store"] = restored
        yield restored
    finally:
        app.extensions["research_store"] = original
        restored.close()


@pytest.mark.timeout(180)
def test_explicit_benchmark_overlay_reopens_exports_and_enriches_without_mutating_original(
    app, client, native, monkeypatch
):
    path, calls, logins, _ = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)
    store = app.extensions["research_store"]
    before = service.encoded(service.read_artifact(store, artifact))
    raw_export = client.get(f"/scanner-research/api/jobs/{parent}/export").data
    submit(client, parent, {})
    run_worker(app)
    assert completed(client, parent)["analysis"]["catalog"]
    assert calls == logins == []  # Reporting remains offline until explicitly requested.

    child = submit(client, parent, {"benchmark": BENCHMARK})
    assert client.post(url(parent), json={"benchmark": BENCHMARK}).json["analysis_job_id"] == child
    run_worker(app)
    shown = completed(client, parent)
    details = shown["analysis"]["benchmark"]
    assert details["status"] == "available" and details["observations"] == 10
    assert details["descriptor"] == normalize_descriptor(BENCHMARK)
    assert len(calls) == 1 and logins == ["research-test"]
    assert (calls[0]["symbol"], calls[0]["exchange"], calls[0]["interval"], calls[0]["source"]) == (
        "NIFTY",
        "NSE_INDEX",
        "D",
        "api",
    )
    assert (calls[0]["start_date"], calls[0]["end_date"]) == ("2026-01-04", "2026-01-14")
    stored_export = client.get(url(parent) + "/export")
    saved = stored_export.json["result"]
    evidence = saved["benchmark_evidence"]
    assert evidence["id"] and saved["benchmark_receipts"]
    assert saved["benchmark_receipts"][-1]["receipt"]["exchange"] == "NSE_INDEX"
    original = service.read_artifact(store, artifact)["result"]
    assert shown["summary"] == original["summary"]
    assert shown["experiment"]["trials"] == original["experiment"]["trials"]
    assert service.encoded(service.read_artifact(store, artifact)) == before
    assert client.get(f"/scanner-research/api/jobs/{parent}/export").data == raw_export

    NativeHistorifyArchive(path, exchange="NSE_INDEX").write(
        "NIFTY", [native_candle("2026-01-14", 9000)]
    )
    monkeypatch.setattr(
        "services.research_market_series.acquire_series",
        lambda *a, **k: pytest.fail("Saved benchmark requested acquisition"),
    )
    assert client.get(f"/scanner-research/api/jobs/{parent}").json["result"] == shown
    assert client.get(url(parent) + "/export").data == stored_export.data
    assert client.post(url(parent), json={"benchmark": BENCHMARK}).status_code == 200
    enriched = submit(client, parent, {"parameters": ["a.target_pct"]})
    with store.sessions() as db:
        spec = json.loads(db.get(ResearchExperiment, enriched).specification)
    assert spec["reference_artifact"] == stored_export.headers["X-Stored-Artifact-SHA256"]
    run_worker(app)
    later = completed(client, parent)
    assert later["analysis"]["benchmark"] == details
    assert client.get(url(parent) + "/export").json["result"]["benchmark_evidence"] == evidence
    assert len(calls) == 1 and logins == ["research-test"]
    assert service.encoded(service.read_artifact(store, artifact)) == before


@pytest.mark.timeout(180)
@pytest.mark.parametrize("recover_from_backup", [False, True])
def test_failed_benchmark_keeps_report_and_retry_restores_committed_prices(
    app, client, native, monkeypatch, tmp_path_factory, recover_from_backup
):
    path, calls, _, history = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)

    def longer(report):
        report["equity_curve"] = [
            {"date": day, "equity": 10000 + i, "cash": 9000, "drawdown_pct": 0, "open_positions": 1}
            for i, day in enumerate(days("2025-01-05", "2025-12-20"), 1)
        ]
        report["summary"].update(final_equity=10350, net_return_pct=3.5, net_pnl=350)
        report["ledger"][0].update(entry_date="2025-01-05", exit_date="2025-12-20")

    artifact = replace_report(app, parent, artifact, longer)
    store = app.extensions["research_store"]
    before = service.encoded(service.read_artifact(store, artifact))
    submit(client, parent, {})
    run_worker(app)
    ordinary = completed(client, parent)
    assert calls == []

    def fail_second(**request):
        if calls:
            calls.append(request)
            return False, {"message": "private-test-message"}, 429
        return history(**request)

    monkeypatch.setattr("services.research_native_prices.native_history", fail_second)
    failed = submit(client, parent, {"benchmark": BENCHMARK})
    run_worker(app)
    state = client.get(url(parent)).json
    assert state["status"] == "failed", state
    assert state["benchmark"] == normalize_descriptor(BENCHMARK)
    assert client.get(f"/scanner-research/api/jobs/{parent}").json["result"] == ordinary
    assert "private-test-message" not in str(state)
    with store.sessions() as db:
        failed_spec = db.get(ResearchExperiment, failed)
        saved = service.read_artifact(store, failed_spec.checkpoint)["state"]
    committed = saved["acquisition"]["bars"]["NIFTY"]
    assert len(committed) == 300
    calendar = service.read_artifact(store, saved["reference_artifact"])
    retained_receipts = saved["acquisition_receipts"]
    assert retained_receipts and retained_receipts[0]["receipt"]["observations"]
    recovery = restored_store(app, tmp_path_factory) if recover_from_backup else nullcontext(store)
    with recovery as current:
        assert service.read_artifact(current, saved["reference_artifact"]) == calendar
        monkeypatch.setattr(
            "services.research_native_calendar._native_reader",
            lambda: pytest.fail("Recovery recomputed the recorded market calendar"),
        )
        first = min(committed)
        NativeHistorifyArchive(path, exchange="NSE_INDEX").write(
            "NIFTY", [native_candle(first, 9999)]
        )
        prior_calls = len(calls)
        monkeypatch.setattr("services.research_native_prices.native_history", history)
        retried = submit(client, parent, {})  # Generic retry retains the failed descriptor.
        assert retried != failed
        run_worker(app)
        result = completed(client, parent)
        exported = client.get(url(parent) + "/export").json["result"]
        assert exported["benchmark_evidence"]["bars"][first] == committed[first]
        assert len(calls) == prior_calls + 1
        assert calls[-1]["start_date"] > max(committed)
        for receipt in retained_receipts:
            assert receipt in exported["benchmark_receipts"]
            sidecar = current.root / "acquisition-receipts" / (receipt["sha256"] + ".json")
            assert sidecar.read_bytes() == service.encoded(receipt["receipt"])
        assert result["summary"] == ordinary["summary"]
        assert service.encoded(service.read_artifact(current, artifact)) == before


@pytest.mark.timeout(180)
def test_completed_benchmark_checkpoint_survives_prune_backup_and_offline_reuse(
    app, client, native, monkeypatch, tmp_path_factory
):
    _, calls, _, _ = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)
    store = app.extensions["research_store"]
    child = submit(client, parent, {"benchmark": BENCHMARK})
    run_worker(app)
    expected = completed(client, parent)
    exported = client.get(url(parent) + "/export")
    with store.sessions() as db:
        checkpoint_id = db.get(ResearchExperiment, child).checkpoint
    state = service.read_artifact(store, checkpoint_id)["state"]
    calendar = service.read_artifact(store, state["reference_artifact"])
    record = service.read_artifact(store, state["inputs_artifact"])
    assert record["evidence"] == exported.json["result"]["benchmark_evidence"]
    assert record["acquisition_receipts"] == exported.json["result"]["benchmark_receipts"]

    orphan = service.save_artifact(store, {"unused_benchmark_test_artifact": True})
    for path in (store.root / "artifacts").iterdir():
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    pruned = prune_orphans(store, apply=True)
    assert any(item.startswith(orphan) for item in pruned["files"])
    assert service.read_artifact(store, state["reference_artifact"]) == calendar
    assert service.read_artifact(store, state["inputs_artifact"]) == record

    with restored_store(app, tmp_path_factory) as restored:
        assert service.read_artifact(restored, state["reference_artifact"]) == calendar
        assert service.read_artifact(restored, state["inputs_artifact"]) == record
        assert client.get(url(parent) + "/export").data == exported.data
        assert completed(client, parent) == expected
        monkeypatch.setattr(
            "services.research_market_series.acquire_series",
            lambda *a, **k: pytest.fail("Restored saved benchmark requested acquisition"),
        )
        monkeypatch.setattr(
            "services.research_native_calendar._native_reader",
            lambda: pytest.fail("Restored saved benchmark recomputed its calendar"),
        )
        from services.research_benchmarks import prepare

        assert (
            prepare(
                restored,
                "research-test",
                service.read_artifact(restored, artifact)["result"],
                BENCHMARK,
                saved=state,
            )
            == record
        )
        enriched = submit(client, parent, {"parameters": ["a.target_pct"]})
        with restored.sessions() as db:
            specification = json.loads(db.get(ResearchExperiment, enriched).specification)
        assert specification["reference_artifact"] == exported.headers["X-Stored-Artifact-SHA256"]
        run_worker(app)
        assert (
            completed(client, parent)["analysis"]["benchmark"] == expected["analysis"]["benchmark"]
        )
        assert len(calls) == 1


@pytest.mark.timeout(180)
def test_benchmark_batches_resume_same_job_and_keep_original_evidence(
    app, client, native, monkeypatch
):
    _, calls, _, _ = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)
    store = app.extensions["research_store"]
    from services import research_market_series

    actual = research_market_series.acquire_series

    def limit_one(*args, **kwargs):
        return actual(*args, **kwargs, max_requests=1)

    # Two disjoint missing windows are too far apart for the daily coalescing bound.
    scope = NativeHistorifyArchive(native[1], exchange="NSE_INDEX")
    scope.write("NIFTY", [native_candle(day) for day in days("2026-01-05", "2026-01-13")])
    monkeypatch.setattr(research_market_series, "acquire_series", limit_one)

    # Adjacent end gaps still coalesce over <=20 sessions; use a long report so the
    # first and last gaps require genuinely separate native download requests.
    def longer(report):
        report["equity_curve"] = [
            {"date": day, "equity": 10000 + i, "cash": 9000, "drawdown_pct": 0, "open_positions": 1}
            for i, day in enumerate(days("2026-01-05", "2026-02-14"), 1)
        ]
        report["summary"].update(final_equity=10041, net_return_pct=0.41, net_pnl=41)

    artifact = replace_report(app, parent, artifact, longer)
    scope.write("NIFTY", [native_candle(day) for day in days("2026-01-14", "2026-02-13")])
    before = service.encoded(service.read_artifact(store, artifact))
    child = submit(client, parent, {"benchmark": BENCHMARK})
    run_worker(app)
    response = client.get(url(parent)).json
    assert response["status"] == "queued" and response["analysis_job_id"] == child, response
    assert len(calls) == 1
    run_worker(app)
    assert client.get(url(parent)).json["analysis_job_id"] == child
    assert completed(client, parent)["analysis"]["benchmark"]["status"] == "available"
    assert len(calls) == 2 and calls[0]["start_date"] != calls[1]["start_date"]
    assert service.encoded(service.read_artifact(store, artifact)) == before


@pytest.mark.timeout(180)
def test_benchmark_later_period_is_separate_and_owner_bound(app, client, native, monkeypatch):
    _, calls, _, _ = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)

    def add_later(report):
        later = copy.deepcopy({k: v for k, v in report.items() if k != "experiment"})
        later["equity_curve"] = [
            {**p, "date": p["date"].replace("2026-01", "2026-02"), "equity": 10000 - i * 12}
            for i, p in enumerate(later["equity_curve"], 1)
        ]
        later["summary"].update(final_equity=9880, net_return_pct=-1.2, net_pnl=-120)
        later["ledger"][0].update(entry_date="2026-02-05", exit_date="2026-02-10")
        report["validation"] = {"result": later}

    artifact = replace_report(app, parent, artifact, add_later)
    child = submit(client, parent, {"benchmark": BENCHMARK})
    run_worker(app)
    shown = completed(client, parent)
    selection, later = (
        shown["analysis"]["benchmark"],
        shown["validation"]["result"]["analysis"]["benchmark"],
    )
    assert selection["dates"] == {"from": "2026-01-05", "to": "2026-01-14"}
    assert later["dates"] == {"from": "2026-02-05", "to": "2026-02-14"}
    for details, month in ((selection, "01"), (later, "02")):
        expected = (
            native_candle(f"2026-{month}-14")["close"] / native_candle(f"2026-{month}-04")["close"]
            - 1
        ) * 100
        assert details["metrics"]["benchmark_return_pct"] == pytest.approx(expected)
    assert selection["evidence_id"] != later["evidence_id"]
    assert (
        selection["metrics"]["portfolio_return_pct"] > 0 > later["metrics"]["portfolio_return_pct"]
    )
    calls_before = len(calls)
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    assert client.get(url(parent)).status_code == 404
    assert client.post(url(parent), json={"benchmark": BENCHMARK}).status_code == 404
    assert client.get(url(parent) + "/export").status_code == 404
    assert client.get(f"/scanner-research/api/jobs/{child}").status_code == 404
    assert client.get(f"/scanner-research/api/jobs/{child}/export").status_code == 404
    assert app.test_client().post(url(parent), json={"benchmark": BENCHMARK}).status_code == 401
    assert len(calls) == calls_before
