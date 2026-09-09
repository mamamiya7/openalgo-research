"""Normal broker history, original native timestamps, and truthful sparse coverage."""

import copy
import json
from datetime import datetime, timedelta

import pytest

from services.research_acquisition import acquisition_receipts
from services.research_historify import native_historify_read, native_historify_write
from services.research_native_prices import POLICY, acquire_native_prices, native_history

SIGNALS = [{"symbol": "AAA", "date": "2026-01-05", "row": 2}]
DAYS = ["2026-01-05", "2026-01-06", "2026-01-07"]


def calendar():
    return {
        "sessions": list(DAYS),
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in DAYS},
        "provenance": {"calendar_basis": "openalgo-market-calendar-v1"},
    }


def plan(interval="D"):
    if interval == "D":
        return {"interval": "D", "required_dates": {"AAA": DAYS[1:]}}
    opening = datetime.fromisoformat(DAYS[1] + "T09:15:00+05:30")
    timeline = [(opening + timedelta(minutes=n)).isoformat() for n in range(375)]
    return {
        "interval": "1m",
        "timeline": timeline,
        "required_timestamps": {"AAA": timeline[:3]},
        "session_hours": calendar()["session_hours"],
    }


def candle(day, close=77.7):
    stamp = day + "T00:00:00+00:00" if len(day) == 10 else day
    return {
        "timestamp": int(datetime.fromisoformat(stamp).timestamp()),
        "open": 77.0,
        "high": 80.0,
        "low": 70.0,
        "close": close,
        "volume": 100,
        "oi": 0,
    }


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from database import historify_db

    result = tmp_path / "native.duckdb"
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(result))
    return result


def options(path, interval="D"):
    return {
        "archive_dir": str(path.parent / "receipts"),
        "reader": lambda symbol, first, last: native_historify_read(
            symbol, first, last, path, interval=interval
        ),
        "writer": lambda symbol, rows: native_historify_write(
            symbol, rows, path, interval=interval
        ),
        "credentials": lambda: {"broker": "fyers", "auth_token": "test-credential"},
    }


def forbidden(**_):
    raise AssertionError("No download or login expected")


def test_normal_native_service_receives_no_evidence_override(monkeypatch):
    from services import history_service

    calls = []
    monkeypatch.setattr(
        history_service,
        "get_history",
        lambda **request: calls.append(request) or (True, {"data": []}, 200),
    )
    native_history(symbol="AAA", interval="D", source="api")
    assert calls == [{"symbol": "AAA", "interval": "D", "source": "api"}]


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_complete_native_cache_needs_no_public_reference_or_login(archive, interval):
    own_plan, opts = plan(interval), options(archive, interval)
    slots = own_plan["required_dates" if interval == "D" else "required_timestamps"]["AAA"]
    opts["writer"]("AAA", [candle(slot) for slot in slots])
    opts.update(history=forbidden, credentials=forbidden)
    result = acquire_native_prices(SIGNALS, own_plan, calendar(), **opts)
    assert len(result["bars"]["AAA"]) == len(slots)
    assert result["bars"] == result["raw_bars"]
    assert result["provenance"]["native_price_policy"] == POLICY
    assert result["provenance"]["independent_verification"] is False
    assert result["provenance"]["identity_verified"] is False
    assert result["provenance"]["acquisition_status"] == "complete"
    assert result["provenance"]["download_brokers"] == []
    assert "actions" not in result["provenance"]


@pytest.mark.parametrize("broker", ["fyers", "zerodha", "dhan"])
def test_daily_download_keeps_native_broker_timestamp_and_ohlc(archive, broker):
    calls = []
    opts = options(archive)
    opts["credentials"] = lambda: {"broker": broker, "auth_token": "test-credential"}
    rows = [candle(day) for day in DAYS[1:]]

    def history(**request):
        calls.append(request)
        return True, {"data": rows}, 200

    result = acquire_native_prices(SIGNALS, plan(), calendar(), history=history, **opts)
    stored = opts["reader"]("AAA", DAYS[1], DAYS[-1])
    assert [row["timestamp"] for row in stored] == [row["timestamp"] for row in rows]
    assert [row["close"] for row in stored] == [row["close"] for row in rows]
    assert result["bars"]["AAA"][DAYS[1]]["close"] == 77.7
    assert result["provenance"]["download_brokers"] == [broker]
    assert "evidence_mode" not in calls[0] and "request_control" not in calls[0]
    receipts = acquisition_receipts(result, opts["archive_dir"])
    observations = receipts[0]["receipt"]["observations"]
    assert [row["source_timestamp"] for row in observations] == [row["timestamp"] for row in rows]
    assert "test-credential" not in json.dumps(result) + json.dumps(receipts)


def test_broker_minute_prices_are_not_compared_to_public_daily_range(archive):
    opts, own_plan = options(archive, "1m"), plan("1m")
    rows = [candle(slot) for slot in own_plan["required_timestamps"]["AAA"]]
    result = acquire_native_prices(
        SIGNALS, own_plan, calendar(), history=lambda **_: (True, {"data": rows}, 200), **opts
    )
    assert len(result["bars"]["AAA"]) == 3
    assert result["bars"] == result["raw_bars"]
    assert result["provenance"]["quality_findings"] == []


def test_separate_minute_holes_share_one_broker_date_request(archive):
    own_plan, opts = plan("1m"), options(archive, "1m")
    slots = own_plan["timeline"][:5]
    own_plan["required_timestamps"]["AAA"] = slots
    cached = [candle(slots[index], close=75.0) for index in (0, 2, 4)]
    opts["writer"]("AAA", cached)
    calls = []

    def history(**request):
        calls.append(request)
        # Native history returns the full date, including already stored minutes.
        return True, {"data": [candle(slot, close=77.7) for slot in slots]}, 200

    result = acquire_native_prices(SIGNALS, own_plan, calendar(), history=history, **opts)
    assert len(calls) == 1
    assert calls[0]["start_date"] == calls[0]["end_date"] == DAYS[1]
    assert [result["bars"]["AAA"][slot]["close"] for slot in slots] == [
        75.0,
        77.7,
        75.0,
        77.7,
        75.0,
    ]
    assert result["provenance"]["acquisition_status"] == "complete"


@pytest.mark.parametrize("rows", [[], [candle(DAYS[1])]])
def test_successful_missing_daily_prices_are_publishable_pending_coverage(archive, rows):
    result = acquire_native_prices(
        SIGNALS,
        plan(),
        calendar(),
        history=lambda **_: (True, {"data": rows}, 200),
        **options(archive),
    )
    assert result["provenance"]["acquisition_status"] == "partial"
    assert result["provenance"]["hard_failures"] == []
    assert result["provenance"]["batch_pending"] is False
    assert result["coverage"]["status"] == "warning"
    assert DAYS[-1] in result["coverage"]["symbols"][0]["missing_sessions"]


@pytest.mark.parametrize(
    "status,kind",
    [
        (400, "download_failed"),
        (401, "auth_expired"),
        (429, "rate_limited"),
        (500, "download_failed"),
    ],
)
def test_failed_requests_block_and_resume_only_missing_prices(archive, status, kind):
    opts = options(archive)
    opts["writer"]("AAA", [candle(DAYS[1])])
    first = acquire_native_prices(
        SIGNALS,
        plan(),
        calendar(),
        history=lambda **_: (False, {"message": "secret-test-credential"}, status),
        **opts,
    )
    assert first["provenance"]["hard_failures"][0]["kind"] == kind
    assert first["provenance"]["acquisition_status"] == "failed"
    assert "secret-test-credential" not in json.dumps(first)
    calls = []

    def history(**request):
        calls.append(request)
        return True, {"data": [candle(DAYS[-1])]}, 200

    second = acquire_native_prices(
        SIGNALS, plan(), calendar(), prior=first["acquisition_checkpoint"], history=history, **opts
    )
    assert second["provenance"]["hard_failures"] == []
    assert second["provenance"]["acquisition_status"] == "complete"
    assert [(r["start_date"], r["end_date"]) for r in calls] == [(DAYS[-1], DAYS[-1])]


def test_unavailable_symbol_preserves_gap_and_continues_without_repeating(archive):
    opts = options(archive)
    own_plan = {"interval": "D", "required_dates": {"AAA": DAYS[1:], "BBB": DAYS[1:]}}
    signals = [*SIGNALS, {"symbol": "BBB", "date": DAYS[0], "row": 3}]
    calls = []

    def history(**request):
        calls.append(request["symbol"])
        if request["symbol"] == "AAA":
            return False, {"error_code": "symbol_unavailable", "message": "private-context"}, 400
        return True, {"data": [candle(day) for day in DAYS[1:]]}, 200

    result = acquire_native_prices(signals, own_plan, calendar(), history=history, **opts)
    assert calls == ["AAA", "BBB"]
    assert not result["bars"]["AAA"]
    assert sorted(result["bars"]["BBB"]) == DAYS[1:]
    assert result["provenance"]["acquisition_status"] == "partial"
    assert result["provenance"]["hard_failures"] == []
    assert result["provenance"]["quality_findings"][0]["kind"] == "symbol_unavailable"
    receipts = acquisition_receipts(result, opts["archive_dir"])
    unavailable = next(item["receipt"] for item in receipts if item["receipt"]["symbol"] == "AAA")
    assert unavailable["outcome"] == "symbol_unavailable"
    assert unavailable["http_status"] == 400 and unavailable["accepted_slots"] == []
    assert "private-context" not in json.dumps(result) + json.dumps(receipts)
    resumed = acquire_native_prices(
        signals,
        own_plan,
        calendar(),
        prior=result["acquisition_checkpoint"],
        history=forbidden,
        **{**opts, "credentials": forbidden},
    )
    assert resumed["bars"] == result["bars"]
    assert resumed["provenance"]["acquisition_status"] == "partial"


def test_bounded_continuation_does_not_repeat_successful_empty_request(archive):
    own_plan = {"interval": "D", "required_dates": {"AAA": [DAYS[0], DAYS[2]]}}
    calls = []

    def history(**request):
        calls.append(request)
        return True, {"data": [] if request["start_date"] == DAYS[0] else [candle(DAYS[2])]}, 200

    opts = options(archive)
    first = acquire_native_prices(
        SIGNALS, own_plan, calendar(), max_requests=1, history=history, **opts
    )
    assert first["provenance"]["batch_pending"] is True
    second = acquire_native_prices(
        SIGNALS,
        own_plan,
        calendar(),
        max_requests=1,
        history=history,
        prior=first["acquisition_checkpoint"],
        **opts,
    )
    assert len(calls) == 2
    assert calls[-1]["start_date"] == DAYS[2]
    assert second["provenance"]["batch_pending"] is False
    assert second["provenance"]["hard_failures"] == []
    assert second["provenance"]["acquisition_status"] == "partial"


@pytest.mark.parametrize(
    "rows", [[candle(DAYS[1]), candle(DAYS[1], 75)], [{**candle(DAYS[1]), "high": 1}]]
)
def test_duplicate_or_malformed_native_candles_remain_pending(archive, rows):
    result = acquire_native_prices(
        SIGNALS,
        plan(),
        calendar(),
        history=lambda **_: (True, {"data": rows}, 200),
        **options(archive),
    )
    assert not result["bars"]["AAA"]
    assert result["provenance"]["acquisition_status"] == "partial"
    assert result["provenance"]["quality_findings"]


def test_persistence_mismatch_keeps_source_receipt_and_fails_closed(archive):
    opts = options(archive)
    opts["writer"] = lambda symbol, rows: None
    saved = []
    with pytest.raises(ValueError, match="differ from the downloaded"):
        acquire_native_prices(
            SIGNALS,
            plan(),
            calendar(),
            history=lambda **_: (True, {"data": [candle(DAYS[1])]}, 200),
            checkpoint=lambda state: saved.append(copy.deepcopy(state)),
            **opts,
        )
    assert saved[-1]["receipts"] and not saved[-1]["bars"]["AAA"]


def test_checkpoint_identity_prevents_calendar_or_scope_change(archive):
    opts = options(archive)
    first = acquire_native_prices(
        SIGNALS, plan(), calendar(), history=lambda **_: (True, {"data": []}, 200), **opts
    )
    changed = plan()
    changed["required_dates"]["AAA"] = [DAYS[1]]
    with pytest.raises(ValueError, match="different immutable"):
        acquire_native_prices(
            SIGNALS, changed, calendar(), prior=first["acquisition_checkpoint"], **opts
        )


def test_cancel_and_deadline_save_recovery_without_calling_broker(archive):
    opts = options(archive)
    saved = []
    with pytest.raises(InterruptedError):
        acquire_native_prices(
            SIGNALS,
            plan(),
            calendar(),
            cancelled=lambda: True,
            checkpoint=lambda state: saved.append(copy.deepcopy(state)),
            history=forbidden,
            **opts,
        )
    assert saved and "progress" in saved[-1]
    ticks = iter([0, 2])
    with pytest.raises(TimeoutError):
        acquire_native_prices(
            SIGNALS,
            plan(),
            calendar(),
            max_seconds=1,
            clock=lambda: next(ticks),
            checkpoint=lambda state: saved.append(copy.deepcopy(state)),
            history=forbidden,
            **opts,
        )


def test_receipt_budget_rejects_before_native_ingestion(archive, monkeypatch):
    from services import research_native_prices

    monkeypatch.setattr(research_native_prices, "MAX_RECEIPT_BYTES", 100)
    opts = options(archive)
    with pytest.raises(ValueError, match="bounded storage"):
        acquire_native_prices(
            SIGNALS,
            plan(),
            calendar(),
            history=lambda **_: (True, {"data": [candle(DAYS[1])]}, 200),
            **opts,
        )
    assert not archive.exists()


def test_empty_scope_uses_no_data_or_broker(archive):
    own_plan = {"interval": "D", "required_dates": {"AAA": []}}
    opts = options(archive)
    opts.update(credentials=forbidden, history=forbidden)
    result = acquire_native_prices(SIGNALS, own_plan, calendar(), **opts)
    assert result["provenance"]["acquisition_status"] == "complete"
