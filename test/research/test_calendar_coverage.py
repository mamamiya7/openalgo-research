"""Calendar admission and upgrades must prevent invented trading sessions."""

import ast
import copy
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.calendar_coverage import VERSION, check_native_window, check_saved_calendar
from services.research_native_calendar import native_calendar_snapshot


@pytest.mark.parametrize(
    "first,cutoff", [("2024-12-20", date(2026, 1, 8)), ("2026-01-05", date(2027, 1, 8))]
)
def test_unknown_year_fails_before_reading_calendar(first, cutoff):
    def forbidden(*args):
        pytest.fail("Unsupported range must not read the calendar")

    with pytest.raises(ValueError, match="supports 2025–2026"):
        native_calendar_snapshot(
            [{"date": first, "symbol": "AAA"}], today=cutoff, window_reader=forbidden
        )


def test_warmup_may_not_cross_coverage_boundary():
    from test_native_price_policy import native_window

    with pytest.raises(ValueError, match="eight earlier"):
        native_calendar_snapshot(
            [{"date": "2025-01-06", "symbol": "AAA"}],
            today=date(2025, 1, 10),
            window_reader=native_window,
        )


@pytest.mark.parametrize("day,window", [(date(2026, 1, 15), {}), (date(2026, 2, 1), None)])
def test_missing_native_exception_requires_upgrade(day, window):
    with pytest.raises(ValueError, match="calendar needs an update"):
        check_native_window(day, window)


def test_unannounced_special_hours_are_not_guessed():
    with pytest.raises(ValueError, match="session hours"):
        check_native_window(date(2026, 11, 8), {"start_ms": 1, "end_ms": 2})


def test_native_administrator_hours_are_retained():
    check_native_window(date(2026, 2, 1), {"start_ms": 1, "end_ms": 2})
    check_native_window(date(2026, 1, 6), None)


def test_portfolio_needs_no_legacy_warmup_or_unrelated_future_sessions():
    from test_native_price_policy import native_window

    seen = []

    def reader(day, exchange):
        seen.append(day)
        return native_window(day, exchange)

    result = native_calendar_snapshot(
        [{"date": "2025-01-01", "symbol": "AAA"}],
        today=date(2027, 1, 20),
        window_reader=reader,
        warmup_sessions=0,
        tail_sessions=2,
    )
    assert result["sessions"] == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert min(seen) == date(2025, 1, 1)
    assert max(seen) == date(2025, 1, 3)
    assert result["provenance"]["warmup_sessions"] == 0


def test_full_optimizer_holding_range_bounds_the_calendar():
    from research.portfolio import calendar_tail_sessions, normalize

    spec = normalize(
        {
            "strategies": [
                {
                    "id": "one",
                    "source_id": "a" * 32,
                    "config": {"hold_sessions": 2},
                    "search": {"hold_sessions": {"min": 2, "max": 12, "step": 2}},
                }
            ],
            "optimization": {"sampler": "tpe", "trials": 3},
        }
    )
    assert calendar_tail_sessions(spec, spec["strategies"]) == 13
    spec["strategies"][0]["config"].update(trade_horizon="intraday")
    # Unsupported optimization combinations are validated separately. Holding
    # sessions never extend a strategy explicitly restricted to one session.
    spec.pop("optimization")
    assert calendar_tail_sessions(spec, spec["strategies"]) == 1


def _october_window(day, exchange):
    assert exchange == "NSE"
    if day.weekday() >= 5 or day == date(2025, 10, 22):
        return None
    hours = ("13:45", "14:45") if day == date(2025, 10, 21) else ("09:15", "15:30")
    offset = timezone(timedelta(hours=5, minutes=30))
    start, end = (
        int(datetime.fromisoformat(f"{day}T{value}:00").replace(tzinfo=offset).timestamp() * 1000)
        for value in hours
    )
    return {"start_ms": start, "end_ms": end}


@pytest.mark.parametrize(
    "config,observed,last",
    [
        (
            {"trade_horizon": "intraday", "entry_time": "15:00", "hold_minutes": 30},
            None,
            "2025-10-23",
        ),
        ({"hold_sessions": 2, "entry_time": "15:00"}, None, "2025-10-27"),
        # A minute deadline at the closing boundary falls on the next opening.
        ({"hold_sessions": 5, "entry_time": "15:00", "hold_minutes": 30}, None, "2025-10-24"),
        ({"hold_sessions": 5, "exit_time": "15:00"}, "2025-10-21T13:50:00+05:30", "2025-10-23"),
        ({"hold_sessions": 2}, None, "2025-10-24"),
    ],
)
def test_bounded_calendar_covers_actual_entries_and_matches_full_price_windows(
    config, observed, last
):
    from research.portfolio import (
        calendar_completion_check,
        calendar_tail_sessions,
        normalize,
        price_plan,
    )

    signal = {"date": observed[:10] if observed else "2025-10-20", "symbol": "AAA"}
    if observed:
        signal["timestamp"] = observed
    portfolio = normalize({"strategies": [{"id": "one", "source_id": "a" * 32, "config": config}]})
    strategies = [{**portfolio["strategies"][0], "signals": [signal]}]
    full = native_calendar_snapshot(
        [signal], today=date(2025, 11, 20), window_reader=_october_window, warmup_sessions=0
    )
    bounded = native_calendar_snapshot(
        [signal],
        today=date(2025, 11, 20),
        window_reader=_october_window,
        warmup_sessions=0,
        tail_sessions=calendar_tail_sessions(portfolio, strategies),
        completion_check=calendar_completion_check(portfolio, strategies),
    )
    assert bounded["sessions"][-1] == last
    expected, actual = (
        price_plan(portfolio, strategies, full),
        price_plan(portfolio, strategies, bounded),
    )
    field = "required_dates" if actual["interval"] == "D" else "required_timestamps"
    assert actual[field] == expected[field]
    if actual["interval"] == "1m":
        assert actual["timeline"] == expected["timeline"]


def test_calendar_completion_uses_full_trial_range_and_finest_mixed_interval():
    from research.portfolio import calendar_completion_check, normalize, price_plan

    portfolio = normalize(
        {
            "strategies": [
                {
                    "id": "daily",
                    "source_id": "a" * 32,
                    "allocation_pct": 50,
                    "config": {"entry_time": "15:00", "hold_sessions": 1},
                    "search": {"hold_sessions": {"min": 1, "max": 5, "step": 2}},
                },
                {
                    "id": "timed",
                    "source_id": "b" * 32,
                    "allocation_pct": 50,
                    "config": {"trade_horizon": "intraday", "hold_minutes": 10},
                },
            ],
            "optimization": {"sampler": "tpe", "trials": 2},
        }
    )
    strategies = [
        {**portfolio["strategies"][0], "signals": [{"date": "2025-10-20", "symbol": "AAA"}]},
        {
            **portfolio["strategies"][1],
            "signals": [
                {"date": "2025-10-21", "symbol": "BBB", "timestamp": "2025-10-21T13:50:00+05:30"}
            ],
        },
    ]
    signals = [signal for row in strategies for signal in row["signals"]]
    full = native_calendar_snapshot(
        signals, today=date(2025, 11, 20), window_reader=_october_window, warmup_sessions=0
    )
    bounded = native_calendar_snapshot(
        signals,
        today=date(2025, 11, 20),
        window_reader=_october_window,
        warmup_sessions=0,
        completion_check=calendar_completion_check(portfolio, strategies),
    )
    assert bounded["sessions"][-1] == "2025-10-30"
    expected, actual = (
        price_plan(portfolio, strategies, full),
        price_plan(portfolio, strategies, bounded),
    )
    assert actual["interval"] == "1m"
    assert actual["required_timestamps"] == expected["required_timestamps"]
    assert actual["timeline"] == expected["timeline"]


def test_unreachable_entry_clock_stops_at_completed_cutoff():
    from research.portfolio import calendar_completion_check, normalize

    signal = {"date": "2025-10-20", "symbol": "AAA"}
    portfolio = normalize(
        {"strategies": [{"id": "one", "source_id": "a" * 32, "config": {"entry_time": "23:00"}}]}
    )
    strategies = [{**portfolio["strategies"][0], "signals": [signal]}]
    bounded = native_calendar_snapshot(
        [signal],
        today=date(2025, 10, 24),
        window_reader=_october_window,
        warmup_sessions=0,
        completion_check=calendar_completion_check(portfolio, strategies),
    )
    assert bounded["sessions"][-1] == "2025-10-24"


def test_older_unfinished_calendar_is_rejected_without_altering_it():
    snapshot = {"sessions": ["2025-11-13", "2025-11-17"], "provenance": {}}
    before = copy.deepcopy(snapshot)
    with pytest.raises(ValueError, match="Start a new run"):
        check_saved_calendar(snapshot)
    assert snapshot == before
    # Current admitted native custom closures stay authoritative.
    snapshot["provenance"]["calendar_admission"] = VERSION
    check_saved_calendar(snapshot)


def test_automatic_upgrade_requires_repair_and_never_resets_calendar():
    tree = ast.parse((Path(__file__).resolve().parents[2] / "upgrade/migrate_all.py").read_text())
    assignments = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    migrations = ast.literal_eval(assignments["MIGRATIONS"])
    required = ast.literal_eval(assignments["REQUIRED_MIGRATIONS"].args[0])
    assert "migrate_research_nse_calendar.py" in {name for name, _ in migrations}
    assert "migrate_research_nse_calendar.py" in required
    assert "migrate_market_holidays.py" not in {name for name, _ in migrations}


def test_calendar_upgrade_status_does_not_create_a_missing_database(tmp_path):
    database = tmp_path / "not-created.db"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + database.as_posix()}
    script = Path(__file__).resolve().parents[2] / "upgrade/migrate_research_nse_calendar.py"
    with (tmp_path / "status.log").open("w+", encoding="utf-8") as log:
        result = subprocess.run(
            [sys.executable, str(script), "--status"], env=env, stdout=log, stderr=log, timeout=30
        )
        log.seek(0)
        assert result.returncode == 0, log.read()
    assert not database.exists()


def test_real_seed_upgrade_is_idempotent_preserves_custom_entries_and_other_exchanges(tmp_path):
    script = r"""
from datetime import date
from sqlalchemy import select, event
from database import market_calendar_db as c
from database.research_calendar import repair_nse_calendar
from services.research_native_calendar import native_calendar_snapshot

c.Base.metadata.create_all(c.engine)
c.seed_holidays_2025()
c.seed_holidays_2026()
def exchange_rows(code):
    return [(h.id,h.holiday_date,h.description,h.holiday_type,e.id,e.is_open,e.start_time,e.end_time)
        for h,e in c.db_session.execute(select(c.Holiday,c.HolidayExchange).join(c.HolidayExchange,c.HolidayExchange.holiday_id==c.Holiday.id).where(c.HolidayExchange.exchange_code==code).order_by(c.Holiday.id,c.HolidayExchange.id)).all()]
other = exchange_rows("BSE")
before = exchange_rows("NSE")
c.db_session.remove()
# Populate both positive and negative native caches before repair.
assert c.get_effective_session_window(date(2026,2,1),"NSE") is None
assert c.get_effective_session_window(date(2025,11,1),"NSE") is not None
c.db_session.remove()
planned = repair_nse_calendar(status=True)
assert planned
assert exchange_rows("NSE") == before
c.db_session.remove()
applied = repair_nse_calendar()
assert applied == planned
assert repair_nse_calendar() == []
assert exchange_rows("BSE") == other
c.db_session.remove()
assert c.get_effective_session_window(date(2026,2,1),"NSE") is not None
assert c.is_market_holiday(date(2026,2,1),"NSE") is False
assert c.get_effective_session_window(date(2026,2,1),"BSE") is None
assert c.is_market_holiday(date(2026,2,1),"BSE") is True
assert c.get_effective_session_window(date(2025,11,1),"NSE") is None
assert c.get_effective_session_window(date(2025,11,14),"NSE") is not None
assert c.get_effective_session_window(date(2025,11,5),"NSE") is None
c.db_session.remove()
snapshot=native_calendar_snapshot([{"date":"2025-02-03","symbol":"AAA"}],today=date(2026,9,9))
assert "2025-02-01" in snapshot["sessions"]
assert "2026-02-01" in snapshot["sessions"]
assert "2026-01-15" not in snapshot["sessions"]
assert snapshot["session_hours"]["2025-10-21"] == {"open":"13:45","close":"14:45"}
assert snapshot["provenance"]["calendar_admission"] == "nse-calendar-admission-2025-2026-v1"
# A changed native entry is retained, never silently reset by the upgrade.
with c.db_session() as db:
    row=db.execute(select(c.HolidayExchange).join(c.Holiday,c.Holiday.id==c.HolidayExchange.holiday_id).where(c.Holiday.holiday_date==date(2025,10,21),c.HolidayExchange.exchange_code=="NSE")).scalar_one()
    row.start_time += 60000
    custom=row.start_time
    db.commit()
c.db_session.remove()
assert repair_nse_calendar()==[]
assert c.get_effective_session_window(date(2025,10,21),"NSE")["start_ms"]==custom
c.db_session.remove()
assert not c.db_session.registry.has()
c.engine.dispose()
print("calendar upgrade and native schedule verified")
"""
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + (tmp_path / "calendar.db").as_posix()}
    with (tmp_path / "check.log").open("w+", encoding="utf-8") as log:
        completed = subprocess.run(
            [sys.executable, "-c", script], env=env, stdout=log, stderr=log, timeout=45
        )
        log.seek(0)
        output = log.read()
    assert completed.returncode == 0, output

    assert "calendar upgrade and native schedule verified" in output
