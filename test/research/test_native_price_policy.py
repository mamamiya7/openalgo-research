"""Native calendars and broker OHLC need no external price-file qualification."""

import copy
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import literal_column

from research.data import NATIVE_CALENDAR_BASIS, NATIVE_PRICE_POLICY, validate_snapshot
from research.engine import evaluate
from research.requirements import build_plan
from services.research_native_calendar import CALENDAR_SOURCE, IST, native_calendar_snapshot


def session(day, opening="09:15", closing="15:30"):
    return {
        "start_ms": int(datetime.fromisoformat(f"{day}T{opening}:00+05:30").timestamp() * 1000),
        "end_ms": int(datetime.fromisoformat(f"{day}T{closing}:00+05:30").timestamp() * 1000),
    }


def native_window(day, exchange):
    assert exchange == "NSE"
    return session(day) if day.weekday() < 5 else None


def source():
    sessions = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    return {
        "sessions": sessions,
        "required_dates": {"AAA": sessions[1:]},
        "bars": {
            "AAA": {
                day: {"open": 100, "high": 105, "low": 95, "close": 102}
                for day in sessions[1:]
            }
        },
        "provenance": {
            "provider": "fyers",
            "exchange": "NSE",
            "interval": "D",
            "native_price_policy": NATIVE_PRICE_POLICY,
            "adjustment_basis": "provider-native",
            "calendar_basis": NATIVE_CALENDAR_BASIS,
            "independent_verification": False,
        },
    }


def test_native_prices_are_admitted_without_public_identity_or_adjustment_claims():
    snapshot = source()
    before = copy.deepcopy(snapshot)
    coverage = validate_snapshot(snapshot, [{"date": "2026-01-05", "symbol": "AAA"}])
    assert coverage["status"] == "ready"
    assert snapshot == before
    assert "identity_verified" not in snapshot["provenance"]
    assert "calendar_verified" not in snapshot["provenance"]


@pytest.mark.parametrize(
    "changes",
    [
        {"adjustment_basis": "raw"},
        {"calendar_basis": "unverified"},
        {"identity_verified": True},
        {"independent_verification": True},
        {"independent_verification": None},
        {"synthetic": True},
        {"native_price_policy": "unknown-policy"},
    ],
)
def test_native_policy_rejects_false_or_unknown_provenance(changes):
    snapshot = source()
    snapshot["provenance"].update(changes)
    with pytest.raises(ValueError, match="Native history|native historical price policy"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize(
    "bar",
    [
        {"open": 100, "high": 105, "low": 95, "close": float("nan")},
        {"open": 100, "high": 105, "low": 95, "close": 0},
        {"open": 100, "high": 98, "low": 95, "close": 102},
        {"open": 100, "high": 105, "low": 95},
    ],
)
def test_native_policy_retains_ohlc_structural_checks(bar):
    snapshot = source()
    snapshot["bars"]["AAA"]["2026-01-06"] = bar
    with pytest.raises(ValueError, match="OHLC"):
        validate_snapshot(snapshot)


def test_legacy_price_qualification_is_unchanged():
    snapshot = source()
    snapshot["provenance"].pop("native_price_policy")
    with pytest.raises(ValueError, match="verified instrument identity"):
        validate_snapshot(snapshot)
    snapshot["provenance"].update(
        adjustment_basis="raw", identity_verified=True, calendar_verified=True
    )
    assert validate_snapshot(snapshot)["status"] == "ready"


def test_absent_native_day_stays_pending_instead_of_filled_or_discarded():
    snapshot = source()
    snapshot["bars"]["AAA"].pop("2026-01-07")
    snapshot["bars"]["AAA"]["2026-01-08"]["high"] = 130
    signals = [{"date": "2026-01-05", "symbol": "AAA"}]
    coverage = validate_snapshot(snapshot, signals)
    assert coverage["symbols"][0]["missing_sessions"] == ["2026-01-07"]
    result = evaluate(signals, snapshot, {"stop_pct": 20, "target_pct": 20, "hold_sessions": 2})
    assert result["summary"]["accepted_trades"] == 1
    assert result["summary"]["pending_trades"] == 1
    assert result["ledger"][0]["status"] == "pending"
    assert result["ledger"][0]["exit_date"] is None
    assert "2026-01-07" not in snapshot["bars"]["AAA"]


def test_absent_native_minute_retains_scheduled_gap_and_pending_outcome():
    from test_intraday_engine import config, example

    signals, snapshot = example(1)
    snapshot["provenance"].update(source()["provenance"], interval="1m", synthetic=False)
    missing = snapshot["timeline"][2]
    snapshot["bars"]["AAA"].pop(missing)
    snapshot["bars"]["AAA"][snapshot["timeline"][3]]["high"] = 120
    result = evaluate(signals, snapshot, config())
    assert result["summary"]["pending_trades"] == 1
    assert result["ledger"][0]["exit_date"] is None
    assert result["coverage"]["symbols"][0]["missing_sessions"] == [missing]


def test_native_calendar_uses_holidays_weekend_sessions_and_admin_hours(monkeypatch):
    monkeypatch.setitem(sys.modules, "research.evidence_import", None)

    def reader(day, exchange):
        if day == date(2026, 1, 6):
            return None
        if day == date(2026, 1, 10):
            return session(day, "18:00", "19:15")
        if day == date(2026, 1, 7):
            return session(day, "10:00", "15:00")
        return native_window(day, exchange)

    snapshot = native_calendar_snapshot(
        [{"date": "2026-01-05", "symbol": "AAA"}],
        today=date(2026, 1, 11),
        window_reader=reader,
    )
    assert "2026-01-06" not in snapshot["sessions"]
    assert "2026-01-11" not in snapshot["sessions"]
    assert snapshot["session_hours"]["2026-01-10"] == {"open": "18:00", "close": "19:15"}
    assert snapshot["session_hours"]["2026-01-07"] == {"open": "10:00", "close": "15:00"}
    assert len([day for day in snapshot["sessions"] if day < "2026-01-05"]) == 8
    assert snapshot["provenance"]["calendar_basis"] == NATIVE_CALENDAR_BASIS
    assert snapshot["provenance"]["available_through"] == "2026-01-10"
    assert snapshot["bars"] == {}


def test_native_calendar_excludes_unfinished_current_session():
    snapshot = native_calendar_snapshot(
        [{"date": "2026-01-05", "symbol": "AAA"}],
        today=datetime(2026, 1, 9, 10, 0, tzinfo=IST),
        window_reader=native_window,
    )
    assert snapshot["sessions"][-1] == "2026-01-08"


def test_native_special_session_builds_minutes_without_public_muhurat_gate():
    def reader(day, exchange):
        if day == date(2026, 11, 8):
            return session(day, "18:00", "19:15")
        return native_window(day, exchange)

    signals = [{"date": "2026-11-08", "symbol": "AAA", "timestamp": "2026-11-08T18:00:00+05:30"}]
    snapshot = native_calendar_snapshot(signals, today=date(2026, 11, 9), window_reader=reader)
    plan = build_plan(signals, {"config": {"trade_horizon": "intraday"}}, snapshot)
    assert plan["interval"] == "1m"
    assert plan["session_hours_source"] == CALENDAR_SOURCE
    assert len(plan["required_timestamps"]["AAA"]) == 75
    assert plan["required_timestamps"]["AAA"][0] == "2026-11-08T18:00:00+05:30"
    assert plan["required_timestamps"]["AAA"][-1] == "2026-11-08T19:14:00+05:30"


def test_native_calendar_does_not_mix_public_special_hours():
    signals = [{"date": "2025-10-20", "symbol": "AAA"}]
    snapshot = native_calendar_snapshot(signals, today=date(2025, 10, 22), window_reader=native_window)
    plan = build_plan(signals, {"config": {"trade_horizon": "intraday"}}, snapshot)
    assert plan["session_hours"]["2025-10-21"] == {"open": "09:15", "close": "15:30"}


def test_calendar_rejects_inconsistent_native_session_timestamp():
    def reader(day, exchange):
        if day == date(2026, 1, 6):
            return session(day - timedelta(days=1))
        return native_window(day, exchange)

    with pytest.raises(ValueError, match="invalid NSE session times for 2026-01-06"):
        native_calendar_snapshot(
            [{"date": "2026-01-05", "symbol": "AAA"}],
            today=date(2026, 1, 9),
            window_reader=reader,
        )


@pytest.mark.parametrize("failure", [None, "schema", "window"])
def test_native_database_reader_releases_session_on_every_exit(monkeypatch, failure):
    monkeypatch.setattr("database.research_calendar.repair_nse_calendar", lambda **_: [])
    class Session:
        removed = 0
        reads = 0

        def execute(self, statement):
            self.reads += 1
            if failure == "schema":
                raise RuntimeError("calendar database unavailable")

        def remove(self):
            self.removed += 1

    db = Session()

    def reader(day, exchange):
        if failure == "window":
            raise RuntimeError("calendar read interrupted")
        from research.calendar_coverage import CLOSED_DAYS

        if day in CLOSED_DAYS:
            return None
        return native_window(day, exchange)

    model = SimpleNamespace(id=literal_column("id"))
    monkeypatch.setitem(
        sys.modules,
        "database.market_calendar_db",
        SimpleNamespace(
            Holiday=model,
            HolidayExchange=model,
            MarketTiming=model,
            db_session=db,
            get_effective_session_window=reader,
        ),
    )
    if failure:
        with pytest.raises(RuntimeError):
            native_calendar_snapshot(
                [{"date": "2026-01-05", "symbol": "AAA"}], today=date(2026, 1, 9)
            )
    else:
        native_calendar_snapshot(
            [{"date": "2026-01-05", "symbol": "AAA"}], today=date(2026, 1, 9)
        )
    assert db.removed == 1
    assert db.reads == (1 if failure == "schema" else 3)


def test_real_native_calendar_models_are_read_in_place_with_closed_sessions(tmp_path):
    # A separate process prevents this genuine native module import from sharing
    # the web integration harness's intentionally stubbed operational databases.
    script = r'''
from datetime import date, datetime
from sqlalchemy import event
from database import market_calendar_db as calendar
from services.research_native_calendar import native_calendar_snapshot

calendar.Base.metadata.create_all(calendar.engine)
calendar.seed_holidays_2025()
calendar.seed_holidays_2026()
from database.research_calendar import repair_nse_calendar
repair_nse_calendar()
with calendar.db_session() as db:
    holiday = calendar.Holiday(holiday_date=date(2026, 1, 6), year=2026,
        description="Fixture closure", holiday_type="TRADING_HOLIDAY")
    special = calendar.Holiday(holiday_date=date(2026, 1, 10), year=2026,
        description="Fixture weekend", holiday_type="SPECIAL_SESSION")
    db.add_all([holiday, special])
    db.flush()
    db.add(calendar.HolidayExchange(holiday_id=holiday.id, exchange_code="NSE", is_open=False))
    db.add(calendar.HolidayExchange(holiday_id=special.id, exchange_code="NSE", is_open=True,
        start_time=int(datetime.fromisoformat("2026-01-10T18:00:00+05:30").timestamp()*1000),
        end_time=int(datetime.fromisoformat("2026-01-10T19:15:00+05:30").timestamp()*1000)))
    db.add(calendar.MarketTiming(exchange_code="NSE", start_time="10:00", end_time="15:00",
        start_offset=36000000, end_offset=54000000))
    db.commit()
calendar.db_session.remove()
counts = {"out": 0, "in": 0}
event.listen(calendar.engine, "checkout", lambda *a: counts.update(out=counts["out"] + 1))
event.listen(calendar.engine, "checkin", lambda *a: counts.update(**{"in": counts["in"] + 1}))
for _ in range(10):
    snapshot = native_calendar_snapshot([{"date": "2026-01-05", "symbol": "AAA"}],
        today=date(2026, 1, 11))
    assert "2026-01-06" not in snapshot["sessions"]
    assert snapshot["session_hours"]["2026-01-07"] == {"open": "10:00", "close": "15:00"}
    assert snapshot["session_hours"]["2026-01-10"] == {"open": "18:00", "close": "19:15"}
    assert not calendar.db_session.registry.has()
assert counts["out"] == counts["in"] == 20, counts
calendar.engine.dispose()
print("native calendar and connection lifetimes verified")
'''
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + (tmp_path / "calendar.db").as_posix()}
    completed = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 0, completed.stderr
    assert "native calendar and connection lifetimes verified" in completed.stdout
