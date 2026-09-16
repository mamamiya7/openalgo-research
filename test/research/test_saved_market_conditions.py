"""Actual native history/worker lifecycle for saved causal market-condition analysis."""

# ruff: noqa: F811 -- shared isolated Flask/native fixtures
import copy
import json
import os
import time

import pytest
from test_historify_scope import native  # noqa: F401
from test_jobs import app, client  # noqa: F401
from test_saved_analysis import completed_parent
from test_saved_benchmarks import (
    BENCHMARK,
    completed,
    days,
    native_candle,
    replace_report,
    restored_store,
    run_worker,
    setup_native,
    submit,
    url,
)

from database.research_db import ResearchExperiment
from services import scanner_research_service as service
from services.research_historify import NativeHistorifyArchive
from services.research_storage import prune_orphans


@pytest.mark.timeout(180)
def test_conditions_are_explicit_native_saved_and_restore_without_download(
    app, client, native, monkeypatch, tmp_path_factory
):
    path, calls, credentials, _ = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)
    store = app.extensions["research_store"]
    before = service.encoded(service.read_artifact(store, artifact))
    submit(client, parent, {})
    run_worker(app)
    assert calls == []
    child = submit(client, parent, {"market_conditions": True})
    assert client.get(url(parent)).json["market_conditions"] is True
    assert (
        client.post(url(parent), json={"market_conditions": True}).json["analysis_job_id"] == child
    )
    run_worker(app)
    shown = completed(client, parent)
    review = shown["analysis"]["market_conditions"]
    assert review["coverage"] == {
        "total_sessions": 10,
        "classified_sessions": 10,
        "closed_trades": 1,
        "classified_trades": 1,
        "unclassified_trades": 0,
    }
    assert review["status"] == "available"
    assert review["finding"]["status"] == "insufficient"
    assert all(row["observed_through"] < row["date"] for row in review["timeline"])
    assert review["cohorts"][0]["average_net_return_pct"] == 10
    assert len(calls) == 1 and len(credentials) == 1
    assert (calls[0]["symbol"], calls[0]["exchange"], calls[0]["interval"]) == (
        "NIFTY",
        "NSE_INDEX",
        "D",
    )
    assert calls[0]["start_date"] < "2026-01-01"  # Recorded warmup, never minute history.
    exported = client.get(url(parent) + "/export")
    saved = exported.json["result"]["market_conditions_evidence"]
    assert len(saved["evidence"]["required_dates"]) == 170
    assert len(saved["calendar"]["sessions"]) == 170
    assert saved["acquisition_receipts"]
    assert service.encoded(service.read_artifact(store, artifact)) == before
    assert client.post(url(parent), json={"market_conditions": True}).status_code == 200
    NativeHistorifyArchive(path, exchange="NSE_INDEX").write(
        "NIFTY", [native_candle("2026-01-05", 9000)]
    )
    monkeypatch.setattr(
        "services.research_market_series.acquire_series",
        lambda *a, **k: pytest.fail("Reopened conditions requested prices"),
    )
    with restored_store(app, tmp_path_factory) as restored:
        assert completed(client, parent)["analysis"]["market_conditions"] == review
        orphan = service.save_artifact(restored, {"unused_market_context": True})
        for path in (restored.root / "artifacts").iterdir():
            os.utime(path, (time.time() - 7200, time.time() - 7200))
        pruned = prune_orphans(restored, apply=True)
        assert any(name.startswith(orphan) for name in pruned["files"])
        followup = submit(client, parent, {"parameters": ["a.target_pct"]})
        with restored.sessions() as db:
            spec = json.loads(db.get(ResearchExperiment, followup).specification)
        assert (
            spec["market_context"]["reference_artifact"]
            == exported.headers["X-Stored-Artifact-SHA256"]
        )
        run_worker(app)
        assert completed(client, parent)["analysis"]["market_conditions"] == review
        assert (
            client.get(url(parent) + "/export").json["result"]["market_conditions_evidence"]
            == saved
        )
    assert len(calls) == 1


@pytest.mark.timeout(180)
def test_benchmark_and_conditions_pin_separately_and_generic_retry_retains_intent(
    app, client, native, monkeypatch
):
    _, calls, _, history = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)
    submit(client, parent, {"benchmark": BENCHMARK})
    run_worker(app)
    benchmark = copy.deepcopy(completed(client, parent)["analysis"]["benchmark"])

    def fail(**request):
        return False, {"message": "Temporary history failure"}, 503

    monkeypatch.setattr("services.research_native_prices.native_history", fail)
    submit(client, parent, {"market_conditions": True})
    run_worker(app)
    failed = client.get(url(parent)).json
    assert failed["status"] == "failed" and failed["market_conditions"] is True
    assert (
        client.get(f"/scanner-research/api/jobs/{parent}").json["result"]["analysis"]["benchmark"]
        == benchmark
    )
    monkeypatch.setattr("services.research_native_prices.native_history", history)
    child = submit(client, parent, {})
    assert client.get(url(parent)).json["market_conditions"] is True
    run_worker(app)
    shown = completed(client, parent)
    assert shown["analysis"]["benchmark"] == benchmark
    assert shown["analysis"]["market_conditions"]["status"] == "available"
    assert all(call["interval"] == "D" for call in calls)
    store = app.extensions["research_store"]
    with store.sessions() as db:
        spec = json.loads(db.get(ResearchExperiment, child).specification)
    assert spec["parent_result_artifact"] == artifact
    assert spec["benchmark"] and spec["market_conditions"]


@pytest.mark.parametrize("value", [False, 1, "true", {}, []])
def test_only_explicit_supported_condition_request_is_accepted(app, client, value):
    parent, _ = completed_parent(app, client)
    assert client.post(url(parent), json={"market_conditions": value}).status_code == 400


def test_market_conditions_owner_guard(app, client):
    parent, _ = completed_parent(app, client)
    with client.session_transaction() as session:
        session["user"] = "another-owner"
    assert client.post(url(parent), json={"market_conditions": True}).status_code == 404


@pytest.mark.timeout(180)
def test_partial_condition_download_restores_exact_prices_and_receipts(
    app, client, native, monkeypatch, tmp_path_factory
):
    path, calls, _, history = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)

    def longer(report):
        report["equity_curve"] = [
            {"date": day, "equity": 10000 + i, "cash": 9000, "drawdown_pct": 0, "open_positions": 1}
            for i, day in enumerate(days("2026-01-05", "2026-07-20"), 1)
        ]

    replace_report(app, parent, artifact, longer)

    def fail_second(**request):
        if calls:
            calls.append(request)
            return False, {"message": "private rate limit"}, 429
        return history(**request)

    monkeypatch.setattr("services.research_native_prices.native_history", fail_second)
    failed = submit(client, parent, {"market_conditions": True})
    run_worker(app)
    assert client.get(url(parent)).json["status"] == "failed"
    store = app.extensions["research_store"]
    with store.sessions() as db:
        state = service.read_artifact(store, db.get(ResearchExperiment, failed).checkpoint)["state"]
    retained = state["contexts"]["market_conditions"]
    committed = retained["acquisition"]["bars"]["NIFTY"]
    assert len(committed) == 300
    first = min(committed)
    NativeHistorifyArchive(path, exchange="NSE_INDEX").write("NIFTY", [native_candle(first, 9999)])
    monkeypatch.setattr("services.research_native_prices.native_history", history)
    with restored_store(app, tmp_path_factory) as restored:
        monkeypatch.setattr(
            "services.research_native_calendar._native_reader",
            lambda: pytest.fail("Resume changed the calendar"),
        )
        submit(client, parent, {"parameters": ["a.target_pct"], "market_conditions": True})
        run_worker(app)
        recovered = completed(client, parent)["analysis"]["market_conditions"]
        assert recovered["status"] == "partial"  # Complete prices can still have uncertain trend.
        assert (
            recovered["coverage"]["classified_sessions"] < recovered["coverage"]["total_sessions"]
        )
        market = client.get(url(parent) + "/export").json["result"]["market_conditions_evidence"]
        assert market["evidence"]["bars"][first] == committed[first]
        assert calls[-1]["start_date"] > max(committed)
        for receipt in retained["acquisition_receipts"]:
            assert receipt in market["acquisition_receipts"]
            assert (
                restored.root / "acquisition-receipts" / (receipt["sha256"] + ".json")
            ).read_bytes() == service.encoded(receipt["receipt"])


@pytest.mark.timeout(180)
def test_later_period_is_separate_and_unpublished_final_period_is_not_acquired(
    app, client, native, monkeypatch
):
    _, calls, _, _ = setup_native(native, monkeypatch)
    parent, artifact = completed_parent(app, client)

    def later(report):
        separate = copy.deepcopy(
            {key: value for key, value in report.items() if key != "experiment"}
        )
        for point in separate["equity_curve"]:
            point["date"] = point["date"].replace("-01-", "-03-")
        for trade in separate["ledger"]:
            trade["entry_date"] = "2026-03-05"
            trade["exit_date"] = "2026-03-10"
            trade["pnl"] = -200
        report["validation"] = {"result": separate}
        report["reserved_evaluation"] = {
            "status": "reserved",
            "evaluation": {"from": "2026-08-01", "to": "2026-09-01"},
        }

    replace_report(app, parent, artifact, later)
    submit(client, parent, {"market_conditions": True})
    run_worker(app)
    shown = completed(client, parent)
    selection = shown["analysis"]["market_conditions"]
    validation = shown["validation"]["result"]["analysis"]["market_conditions"]
    assert selection["dates"] == {"from": "2026-01-05", "to": "2026-01-14"}
    assert validation["dates"] == {"from": "2026-03-05", "to": "2026-03-14"}
    assert selection["cohorts"][0]["average_net_return_pct"] == 10
    assert validation["cohorts"][0]["average_net_return_pct"] == -20
    assert max(call["end_date"] for call in calls) == "2026-03-14"
