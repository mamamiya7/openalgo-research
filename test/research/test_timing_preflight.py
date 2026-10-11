"""Timing disclosure uses native CSV intake without changing saved inputs or doing IO."""

# ruff: noqa: F811 -- shared isolated native Flask fixtures
from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO

import pytest
from test_jobs import app, client  # noqa: F401

from research.portfolio import fingerprint
from services import research_portfolio
from services import scanner_research_service as service

OWNER = "research-test"


def csv_bytes(kind):
    days, cursor = [], date(2026, 1, 5)
    while len(days) < 65:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    header = "Timestamp" if kind == "timed" else "Date"
    lines = [f"{header},Symbol"]
    for i, day in enumerate(days):
        observed = (
            day + "T10:05:30+05:30" if kind == "timed" or kind == "mixed_csv" and i % 2 else day
        )
        lines.append(f"{observed},AAA")
    return ("\n".join(lines) + "\n").encode()


def upload(client, kind):
    raw = csv_bytes(kind)
    response = client.post(
        "/scanner-research/api/portfolio/inputs",
        data={"file": (BytesIO(raw), f"{kind}.csv")},
    )
    assert response.status_code == 201, response.json
    return response.json["id"]


def request(client, mode):
    kinds = (
        ["daily", "timed"]
        if mode == "mixed_strategies"
        else [
            "timed" if mode == "timed_multiday" else "mixed_csv" if mode == "mixed_csv" else "daily"
        ]
    )
    rows = []
    for i, kind in enumerate(kinds):
        config = {"hold_sessions": 3, "target_pct": 4, "stop_pct": 2}
        if mode == "fixed_clocks":
            config.update(entry_time="10:00", exit_time="14:45")
        if mode == "intraday":
            config.update(trade_horizon="intraday", hold_minutes=30)
        rows.append(
            {
                "id": f"scanner{i}",
                "name": f"Scanner{i}",
                "source_id": upload(client, kind),
                "allocation_pct": 100 / len(kinds),
                "config": config,
            }
        )
    return {
        "name": "Timing disclosure",
        "capital": 100000,
        "engine": "vectorbt",
        "strategies": rows,
    }


def forbid_market_io(monkeypatch):
    from database import historify_db, market_calendar_db
    from research.connectors import vectorbt_portfolio
    from services import history_service, research_sources

    def fail(*args, **kwargs):
        pytest.fail(
            "Preflight timing disclosure must not read prices, fetch a calendar, or run an engine"
        )

    monkeypatch.setattr(research_portfolio, "_prepare_prices", fail)
    monkeypatch.setattr(research_sources, "resolve_broker_session", fail)
    monkeypatch.setattr(history_service, "get_history", fail)
    monkeypatch.setattr(historify_db, "get_connection", fail)
    monkeypatch.setattr(market_calendar_db, "get_market_timings_for_date", fail)
    monkeypatch.setattr(market_calendar_db, "get_holidays_by_year", fail)
    monkeypatch.setattr(vectorbt_portfolio, "evaluate", fail)


def artifacts(store):
    return {p.name: p.read_bytes() for p in (store.root / "artifacts").rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "mode,interval,intent,timed,date_only",
    [
        ("daily", "D", "daily", 0, 65),
        ("timed_multiday", "1m", "timed", 65, 0),
        ("fixed_clocks", "1m", "timed", 0, 65),
        ("intraday", "1m", "timed", 0, 65),
        ("mixed_strategies", "1m", "mixed", 65, 65),
        ("mixed_csv", "1m", "mixed", 32, 33),
    ],
)
def test_native_csv_preflight_reports_timing_without_acquisition_or_evidence_mutation(
    app, client, monkeypatch, mode, interval, intent, timed, date_only
):
    raw = request(client, mode)
    original = deepcopy(raw)
    store = app.extensions["research_store"]
    source_before = {
        row["source_id"]: service.encoded(service.source_for(store, OWNER, row["source_id"]))
        for row in raw["strategies"]
    }
    evidence_before = service.encoded(research_portfolio.resolve_inputs(store, OWNER, raw))
    files_before = artifacts(store)
    forbid_market_io(monkeypatch)
    response = client.post("/scanner-research/api/portfolio/preflight", json=raw)
    assert response.status_code == 200, response.json
    preview = response.json
    timing = preview["timing_admission"]
    assert preview["interval"] == interval
    assert timing["version"] == "research-timing-admission-v1"
    assert timing["required_interval"] == timing["effective_interval"] == interval
    assert timing["intent"] == intent
    assert timing["signals"] == {"total": timed + date_only, "timed": timed, "date_only": date_only}
    assert timing["status"] == "pending_calendar"
    assert timing["calendar"] == {"basis": None, "from": None, "to": None}
    assert timing["price_coverage"] == "not_checked"
    assert timing["timing_search"]["status"] == "not_searched"
    assert (
        "fixed opportunity cohort across timing branches"
        in timing["timing_search"]["prerequisites"]
    )
    assert all(row["calendar"] is None for row in timing["strategies"])
    assert timing["id"] == fingerprint({key: value for key, value in timing.items() if key != "id"})
    if mode == "mixed_strategies":
        assert [row["required_interval"] for row in timing["strategies"]] == ["D", "1m"]
        assert all(row["effective_interval"] == "1m" for row in timing["strategies"])
    if mode == "fixed_clocks":
        assert timing["strategies"][0]["fixed_clocks"] == {
            "entry_time": "10:00",
            "exit_time": "14:45",
        }
    if mode == "timed_multiday":
        assert (
            preview["portfolio"]["strategies"][0]["config"].get("trade_horizon", "multiday")
            == "multiday"
        )
        assert timing["strategies"][0]["constraints"][0]["parameter"] == "hold_sessions"
    if mode == "intraday":
        deadline = timing["strategies"][0]
        assert deadline["dormant_parameters"] == ["hold_sessions"]
        assert {row["parameter"] for row in deadline["constraints"]} == {
            "hold_minutes",
            "session_close",
        }
    assert raw == original
    assert service.encoded(research_portfolio.resolve_inputs(store, OWNER, raw)) == evidence_before
    assert source_before == {
        row["source_id"]: service.encoded(service.source_for(store, OWNER, row["source_id"]))
        for row in raw["strategies"]
    }
    assert artifacts(store) == files_before
    assert client.get("/scanner-research/api/jobs").json == []
    repeat = research_portfolio.preview(store, OWNER, raw)
    assert repeat["timing_admission"] == timing


def test_automatic_v6_timing_disclosure_does_not_change_execution_recipe_or_resolved_inputs(
    app, client, monkeypatch
):
    raw = request(client, "timed_multiday")
    raw["automatic_research"] = {"version": "automatic-hypotheses-v6"}
    original = deepcopy(raw)
    store = app.extensions["research_store"]
    before = research_portfolio.resolve_inputs(store, OWNER, raw)
    bytes_before = service.encoded(before)
    files_before = artifacts(store)
    forbid_market_io(monkeypatch)
    preview = research_portfolio.preview(store, OWNER, raw)
    timing = preview["timing_admission"]
    assert preview["automatic_plan"]["execution_recipe_version"] == "automatic-hypotheses-v6"
    assert timing["timing_search"]["status"] == "not_searched"
    assert timing["strategies"][0]["searched_deadline"] == "hold_sessions"
    assert "timing_admission" not in preview["portfolio"]
    assert "timing_admission" not in research_portfolio.resolve_inputs(store, OWNER, raw)
    assert preview["portfolio"] == before["portfolio"]
    assert service.encoded(research_portfolio.resolve_inputs(store, OWNER, raw)) == bytes_before
    assert raw == original
    assert artifacts(store) == files_before
