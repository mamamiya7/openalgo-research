"""Freeze OpenAlgo's exchange calendar without public price-file dependencies.

The native calendar owns holidays and session hours. This adapter only records
its existing answers for a bounded research window; it does not seed or edit it.
"""

from contextlib import contextmanager, nullcontext
from datetime import date, datetime, time, timedelta, timezone

from research.calendar_coverage import VERSION as COVERAGE_VERSION
from research.calendar_coverage import check_native_window, require_covered_day
from research.data import NATIVE_CALENDAR_BASIS, NATIVE_PRICE_POLICY
from research.engine import TRIGGER_WARMUP_SESSIONS

IST = timezone(timedelta(hours=5, minutes=30))
CALENDAR_SOURCE = "database.market_calendar_db.get_effective_session_window"
MAX_CALENDAR_DAYS = 4200


@contextmanager
def _native_reader():
    from sqlalchemy import select

    from database.market_calendar_db import (
        Holiday,
        HolidayExchange,
        MarketTiming,
        db_session,
        get_effective_session_window,
    )

    try:
        # Native helpers catch database failures. Check their tables first so a
        # missing calendar cannot silently turn an entire period into holidays.
        for model in (Holiday, HolidayExchange, MarketTiming):
            db_session.execute(select(model.id).limit(1))
        from database.research_calendar import repair_nse_calendar

        if repair_nse_calendar(status=True):
            raise ValueError(
                "OpenAlgo's NSE calendar needs an update. "
                "Apply the research calendar upgrade, then resume this run."
            )

        def checked_reader(day, exchange):
            window = get_effective_session_window(day, exchange)
            check_native_window(day, window)
            return window

        yield checked_reader
    finally:
        db_session.remove()


def _session(day, reader, as_of):
    window = reader(day, "NSE")
    if window is None:
        return None
    try:
        start_ms, end_ms = window["start_ms"], window["end_ms"]
        if any(isinstance(value, bool) for value in (start_ms, end_ms)):
            raise ValueError
        opening = datetime.fromtimestamp(start_ms / 1000, IST)
        closing = datetime.fromtimestamp(end_ms / 1000, IST)
        if (
            opening.date() != day
            or closing.date() != day
            or opening >= closing
            or opening.second
            or opening.microsecond
            or closing.second
            or closing.microsecond
        ):
            raise ValueError
    except (TypeError, KeyError, ValueError, OverflowError, OSError) as exc:
        raise ValueError(f"OpenAlgo has invalid NSE session times for {day.isoformat()}") from exc
    if closing > as_of:
        return None
    return {"open": opening.strftime("%H:%M"), "close": closing.strftime("%H:%M")}


def native_calendar_snapshot(
    signals,
    request=None,
    *,
    today=None,
    window_reader=None,
    warmup_sessions=TRIGGER_WARMUP_SESSIONS,
    tail_sessions=None,
    completion_check=None,
):
    """Record only completed native sessions required by the caller.

    ``today`` accepts an aware datetime, or an explicit date meaning that day's
    end, for deterministic checks. Production uses the current IST instant and
    excludes an unfinished current session. The requested candle interval and
    holding-window union are selected separately by ``requirements.build_plan``.
    ``window_reader(date, 'NSE')`` may inject the same native response contract.
    Portfolios use no scanner-count warm-up and bound the tail by all allowed
    holding settings. Legacy scanner requests retain their eight-session warm-up.
    """
    if not signals or len(signals) > 25000:
        raise ValueError("Supply 1–25000 normalized signals for the native calendar")
    if type(warmup_sessions) is not int or not 0 <= warmup_sessions <= TRIGGER_WARMUP_SESSIONS:
        raise ValueError("Invalid calendar warm-up")
    if tail_sessions is not None and (
        type(tail_sessions) is not int or not 1 <= tail_sessions <= 253
    ):
        raise ValueError("Invalid calendar holding window")
    if completion_check is not None and not callable(completion_check):
        raise ValueError("Invalid calendar completion check")
    if request is not None:
        from research.requirements import normalize_request

        normalize_request(request)
    if today is None:
        as_of = datetime.now(IST)
    elif isinstance(today, datetime):
        if today.tzinfo is None:
            raise ValueError("Native calendar cutoff must include its timezone")
        as_of = today.astimezone(IST)
    elif isinstance(today, date):
        as_of = datetime.combine(today, time.max, IST)
    else:
        raise ValueError("Native calendar cutoff must be a date or aware datetime")
    first = date.fromisoformat(min(signal["date"] for signal in signals))
    last = date.fromisoformat(max(signal["date"] for signal in signals))
    if not 0 <= (as_of.date() - first).days <= MAX_CALENDAR_DAYS - 60:
        raise ValueError("Native calendar requires a bounded historical signal range")
    require_covered_day(first)
    require_covered_day(last)
    if tail_sessions is None and completion_check is None:
        require_covered_day(as_of.date())
    context = nullcontext(window_reader) if window_reader is not None else _native_reader()
    with context as reader:
        warmup = []
        cursor = first - timedelta(days=1)
        for _ in range(60 if warmup_sessions else 0):
            try:
                require_covered_day(cursor)
            except ValueError as exc:
                raise ValueError(
                    "These scanner signals need eight earlier verified trading sessions. Use later signal dates."
                ) from exc
            if _session(cursor, reader, as_of):
                warmup.append(cursor)
                if len(warmup) == warmup_sessions:
                    break
            cursor -= timedelta(days=1)
        if len(warmup) < warmup_sessions:
            raise ValueError("OpenAlgo calendar does not contain enough earlier NSE sessions")
        sessions, hours = [], {}
        cursor = min(warmup) if warmup else first
        tail_count = 0
        while cursor <= as_of.date():
            require_covered_day(cursor)
            timing = _session(cursor, reader, as_of)
            if timing:
                day = cursor.isoformat()
                sessions.append(day)
                hours[day] = timing
                if len(sessions) > 3000:
                    raise ValueError("Native research calendar exceeds 3000 exchange sessions")
                if cursor > last:
                    tail_count += 1
                if completion_check is not None:
                    if cursor >= last and completion_check(sessions, hours):
                        break
                elif tail_sessions is not None and tail_count >= tail_sessions:
                    break
            cursor += timedelta(days=1)
    if not sessions:
        raise ValueError("There are no completed NSE sessions for these signals yet.")
    return {
        "sessions": sessions,
        "session_hours": hours,
        "session_hours_source": CALENDAR_SOURCE,
        "bars": {},
        "provenance": {
            "provider": "openalgo-market-calendar",
            "exchange": "NSE",
            "interval": "D",
            "native_price_policy": NATIVE_PRICE_POLICY,
            "adjustment_basis": "provider-native",
            "calendar_basis": NATIVE_CALENDAR_BASIS,
            "calendar_source": CALENDAR_SOURCE,
            "independent_verification": False,
            "synthetic": False,
            "available_through": sessions[-1],
            "warmup_sessions": len(warmup),
            "calendar_admission": COVERAGE_VERSION
            if window_reader is None
            else "injected-window-reader",
        },
    }
