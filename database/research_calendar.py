"""Idempotent, NSE-only repair of missing and identifiable upstream seed rows."""

from datetime import date, datetime, timedelta, timezone

from research.calendar_coverage import CLOSED_DAYS, SPECIAL_HOURS

IST = timezone(timedelta(hours=5, minutes=30))


def repair_nse_calendar(*, status=False):
    """Preserve custom entries and other exchanges; report every proposed edit."""
    from sqlalchemy import select

    from database.market_calendar_db import Holiday, HolidayExchange, db_session

    changes = []
    try:
        rows = db_session.execute(
            select(Holiday, HolidayExchange)
            .join(HolidayExchange, HolidayExchange.holiday_id == Holiday.id)
            .where(HolidayExchange.exchange_code == "NSE")
            .where(Holiday.holiday_date.between(date(2025, 1, 1), date(2026, 12, 31)))
        ).all()
        by_day = {}
        for holiday, exchange in rows:
            by_day.setdefault(holiday.holiday_date, []).append((holiday, exchange))

        # These exact shipped defaults are erroneous. Do not remove a customized
        # row or edit the shared parent used by BSE, derivatives or commodities.
        bad_seeds = {
            date(2025, 11, 1): (
                "Diwali Laxmi Pujan (Muhurat Trading)",
                "SPECIAL_SESSION",
                True,
                1730469000000,
                1730473500000,
            ),
            date(2025, 11, 14): ("Guru Nanak Jayanti", "TRADING_HOLIDAY", False, None, None),
        }
        for day, expected in bad_seeds.items():
            for holiday, exchange in by_day.get(day, []):
                actual = (
                    holiday.description,
                    holiday.holiday_type,
                    exchange.is_open,
                    exchange.start_time,
                    exchange.end_time,
                )
                if actual == expected:
                    changes.append({"date": day.isoformat(), "action": "remove incorrect NSE seed"})
                    if not status:
                        db_session.delete(exchange)

        for day in sorted(CLOSED_DAYS | SPECIAL_HOURS.keys()):
            existing = by_day.get(day, [])
            hours = SPECIAL_HOURS.get(day)
            if existing:
                # Repair only the exact wrong 2025 Dussehra seed, never custom hours.
                if day == date(2025, 10, 21) and len(existing) == 1:
                    holiday, exchange = existing[0]
                    if (
                        holiday.description == "Dussehra"
                        and holiday.holiday_type == "TRADING_HOLIDAY"
                        and not exchange.is_open
                        and exchange.start_time is None
                        and exchange.end_time is None
                    ):
                        changes.append(
                            {"date": day.isoformat(), "action": "correct NSE Muhurat hours"}
                        )
                        if not status:
                            exchange.is_open = True
                            exchange.start_time, exchange.end_time = _epoch_hours(day, hours)
                continue
            changes.append(
                {
                    "date": day.isoformat(),
                    "action": "add NSE special session" if hours else "add NSE holiday",
                }
            )
            if status:
                continue
            kind = "SPECIAL_SESSION" if hours else "TRADING_HOLIDAY"
            holiday = (
                db_session.execute(
                    select(Holiday)
                    .where(Holiday.holiday_date == day, Holiday.holiday_type == kind)
                    .order_by(Holiday.id)
                )
                .scalars()
                .first()
            )
            if holiday is None:
                holiday = Holiday(
                    holiday_date=day,
                    year=day.year,
                    description="NSE reviewed special session"
                    if hours
                    else "NSE reviewed trading holiday",
                    holiday_type=kind,
                )
                db_session.add(holiday)
                db_session.flush()
            opening, closing = _epoch_hours(day, hours) if hours else (None, None)
            db_session.add(
                HolidayExchange(
                    holiday_id=holiday.id,
                    exchange_code="NSE",
                    is_open=bool(hours),
                    start_time=opening,
                    end_time=closing,
                )
            )
        if not status:
            db_session.commit()
            from database.market_calendar_db import _holidays_cache, _timings_cache

            _holidays_cache.clear()
            _timings_cache.clear()
        return changes
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.remove()


def _epoch_hours(day, hours):
    return tuple(
        int(datetime.fromisoformat(f"{day}T{value}:00").replace(tzinfo=IST).timestamp() * 1000)
        for value in hours
    )
