# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Date-only HTTP intake selects scoped native daily prices and daily execution."""

import copy
import io
import json
from datetime import datetime, timedelta

import pytest
from test_acquisition import candle, reference
from test_intraday_acquisition import row
from test_jobs import app, client  # noqa: F401
from test_native_price_workflow import prepare_archive

from research.data import cutoff_snapshot, validate_snapshot
from research.engine import POLICY_VERSION
from services import research_native_prices as acquisition
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_historify import native_historify_read, native_historify_write


@pytest.mark.parametrize("missing_daily", [False, True])
@pytest.mark.parametrize("engine", ["scanner", "vectorbt"])
def test_date_only_csv_uses_daily_cache_or_download_and_reopens_exactly(
    app, client, monkeypatch, tmp_path, missing_daily, engine
):
    from research.connectors.registry import package_status
    from research.connectors.vectorbt_adapter import POLICY_VERSION as VECTORBT_POLICY

    if engine == "vectorbt" and not package_status("vectorbt")["available"]:
        pytest.skip("Optional VectorBT connector not installed")
    spec = {"execution": {"engine": "vectorbt"}} if engine == "vectorbt" else {}
    expected_policy = VECTORBT_POLICY if engine == "vectorbt" else POLICY_VERSION
    days = ["2026-01-05", "2026-01-07"] if missing_daily else reference()["sessions"]
    target = prepare_archive(monkeypatch, tmp_path, days)
    monkeypatch.delenv("RESEARCH_PUBLIC_EVIDENCE_DIR")
    monkeypatch.setattr(
        "research.evidence_import.public_snapshot",
        lambda *a, **kw: pytest.fail("Native history cannot require public price files"),
    )
    # Complete minute history must not change the requested EOD execution policy.
    opening = datetime.fromisoformat("2026-01-06T09:15:00+05:30")
    native_historify_write(
        "AAA", [row((opening + timedelta(minutes=n)).isoformat()) for n in range(375)], target
    )
    calls = []

    def history(**request):
        calls.append(request)
        assert request["interval"] == "D"
        return True, {"data": [candle("2026-01-06")]}, 200

    def credentials(_):
        assert missing_daily, "Covered daily data must not require broker authentication"
        return {"broker": "controlled", "auth_token": "TEST_TOKEN_NOT_FOR_PERSISTENCE"}

    def minute_forbidden(*args, **kwargs):
        pytest.fail("A date-only daily backtest must not acquire minute data")

    monkeypatch.setattr(acquisition, "native_history", history)
    monkeypatch.setattr("services.research_sources.resolve_broker_session", credentials)
    monkeypatch.setattr("services.research_intraday.acquire_intraday", minute_forbidden)
    config = {"hold_sessions": 1}
    uploaded = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (
                io.BytesIO(b"Date,Symbol,Marketcapname,Sector\n2026-01-05,AAA,Small,Other\n"),
                "signals.csv",
            ),
            "source": "broker",
            "requirements": json.dumps({"config": config, "specification": spec}),
        },
    )
    assert uploaded.status_code == 201, uploaded.json
    assert uploaded.json["provenance"]["interval"] == "D"
    assert "temporal_version" not in uploaded.json["provenance"]
    store = app.extensions["research_store"]
    worker.acquire(store, "daily-workflow")
    try:
        assert worker.run_one(store, "daily-workflow")
        preparation = client.get(
            f"/scanner-research/api/jobs/{uploaded.json['preparation_job']['id']}"
        ).json
        assert preparation["status"] == "completed", preparation
        source = preparation["result"]["prepared_source"]
        evidence = service.source_for(store, "research-test", source["id"])
        assert evidence["snapshot"]["required_dates"] == {"AAA": ["2026-01-06", "2026-01-07"]}
        assert sorted(evidence["snapshot"]["bars"]["AAA"]) == ["2026-01-06", "2026-01-07"]
        assert (
            evidence["snapshot"]["provenance"]["native_price_policy"]
            == "openalgo-native-history-v1"
        )
        assert evidence["snapshot"]["provenance"]["independent_verification"] is False
        assert evidence["snapshot"]["bars"] == evidence["snapshot"]["raw_bars"]
        assert source["coverage"]["symbols"][0]["missing_sessions"] == []
        assert len(calls) == int(missing_daily)
        if calls:
            assert calls[0]["start_date"] == calls[0]["end_date"] == "2026-01-06"
        stored = native_historify_read("AAA", "2026-01-06", "2026-01-07", target)
        assert len(stored) == 2

        monkeypatch.setattr("services.research_sources.resolve_broker_session", minute_forbidden)
        monkeypatch.setattr(acquisition, "native_history", minute_forbidden)
        check = client.post(
            "/scanner-research/api/preflight",
            json={
                "source_id": source["id"],
                "config": config,
                "kind": "backtest",
                "specification": spec,
            },
        )
        assert check.status_code == 200 and check.json["policy_version"] == expected_policy
        assert "preparation_job" not in check.json
        submitted = client.post(
            "/scanner-research/api/jobs",
            json={
                "source_id": source["id"],
                "config": config,
                "specification": check.json.get("specification", {}),
            },
        )
        assert submitted.status_code == 202, submitted.json
        assert worker.run_one(store, "daily-workflow")
        job_id = submitted.json["id"]
        completed = client.get(f"/scanner-research/api/jobs/{job_id}").json
        assert completed["status"] == "completed", completed
        assert completed["result"]["policy_version"] == expected_policy
        trade = completed["result"]["ledger"][0]
        assert trade["entry_date"] == "2026-01-06" and trade["exit_date"] == "2026-01-07"
        exported = client.get(f"/scanner-research/api/jobs/{job_id}/export")
        assert b"TEST_TOKEN_NOT_FOR_PERSISTENCE" not in exported.data
        assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == exported.data
    finally:
        worker.release(store, "daily-workflow")


def test_daily_coverage_and_causal_cutoff_honor_only_required_dates():
    snapshot = reference()
    snapshot["required_dates"] = {"AAA": ["2026-01-06", "2026-01-07"]}
    for field in ("bars", "raw_bars"):
        snapshot[field]["AAA"].pop("2026-01-05")
    signals = [{"symbol": "AAA", "date": "2026-01-05", "row": 2}]
    assert validate_snapshot(snapshot, signals)["symbols"][0]["missing_sessions"] == []
    cutoff = cutoff_snapshot(snapshot, "2026-01-06")
    assert cutoff["required_dates"] == {"AAA": ["2026-01-06"]}
    assert validate_snapshot(cutoff, signals)["symbols"][0]["missing_sessions"] == []
    bad = copy.deepcopy(snapshot)
    bad["required_dates"]["AAA"].append("2026-01-08")
    with pytest.raises(ValueError, match="Required daily dates"):
        validate_snapshot(bad, signals)


def test_native_missing_day_completes_preparation_and_keeps_trade_pending(
    app, client, monkeypatch, tmp_path
):
    target = prepare_archive(monkeypatch, tmp_path, ["2026-01-06", "2026-01-08"])
    days = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    monkeypatch.setattr(
        "services.research_native_calendar.native_calendar_snapshot",
        lambda *a, **kw: {
            "sessions": days,
            "bars": {},
            "provenance": {"calendar_basis": "openalgo-market-calendar-v1"},
        },
    )
    calls = []

    def missing(**request):
        calls.append((request["interval"], request["start_date"], request["end_date"]))
        return True, {"status": "success", "data": []}, 200

    monkeypatch.setattr(acquisition, "native_history", missing)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: {"broker": "controlled", "auth_token": "NOT_A_REAL_SECRET"},
    )
    config = {"hold_sessions": 2}
    uploaded = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (io.BytesIO(b"Date,Symbol\n2026-01-05,AAA\n"), "signals.csv"),
            "source": "broker",
            "requirements": json.dumps({"config": config}),
        },
    ).json
    store = app.extensions["research_store"]
    worker.acquire(store, "native-partial")
    try:
        worker.run_one(store, "native-partial")
        prepared = client.get(
            f"/scanner-research/api/jobs/{uploaded['preparation_job']['id']}"
        ).json
        assert prepared["status"] == "completed", prepared
        source = prepared["result"]["prepared_source"]
        assert source["coverage"]["symbols"][0]["missing_sessions"] == ["2026-01-07"]
        assert calls == [("D", "2026-01-07", "2026-01-07")]
        job = client.post(
            "/scanner-research/api/jobs", json={"source_id": source["id"], "config": config}
        )
        assert job.status_code == 202, job.json
        worker.run_one(store, "native-partial")
        result = client.get(f"/scanner-research/api/jobs/{job.json['id']}").json
        assert result["status"] == "completed", result
        assert result["result"]["ledger"][0]["status"] == "pending"
        assert len(native_historify_read("AAA", days[0], days[-1], target)) == 2
        assert len(calls) == 1
    finally:
        worker.release(store, "native-partial")
