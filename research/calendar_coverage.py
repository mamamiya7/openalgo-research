"""Reviewed NSE calendar boundaries; prices and session hours stay native.

This is an admission check, not a second calendar or a price source. Native
exceptions and administrator session hours are retained. Extend this reviewed
baseline when exchange circulars add years or change special sessions.
"""

from datetime import date

VERSION = "nse-calendar-admission-2025-2026-v1"
FIRST_DAY = date(2025, 1, 1)
LAST_DAY = date(2026, 12, 31)
SOURCES = tuple(
    f"https://nsearchives.nseindia.com/content/circulars/CMTR{number}.pdf"
    for number in (65587, 65729, 70319, 71775, 72260, 72349)
)
CLOSED_DAYS = frozenset(
    date.fromisoformat(f"{year}-{value}")
    for year, values in (
        (2025, "02-26 03-14 03-31 04-10 04-14 04-18 05-01 08-15 08-27 10-02 10-22 11-05 12-25"),
        (
            2026,
            "01-15 01-26 03-03 03-26 03-31 04-03 04-14 05-01 05-28 06-26 09-14 10-02 10-20 11-10 11-24 12-25",
        ),
    )
    for value in values.split()
)
SPECIAL_HOURS = {
    date(2025, 2, 1): ("09:15", "15:30"),
    date(2025, 10, 21): ("13:45", "14:45"),
    date(2026, 2, 1): ("09:15", "15:30"),
}
# The annual circular announces this session but does not establish its hours.
UNCONFIRMED_HOURS = frozenset({date(2026, 11, 8)})


def require_covered_day(day):
    if not FIRST_DAY <= day <= LAST_DAY:
        raise ValueError(
            "The NSE calendar supports 2025–2026. "
            "Use signals within that range or update OpenAlgo Research."
        )


def check_native_window(day, window):
    """Reject missing baseline exceptions without overriding native answers."""
    require_covered_day(day)
    if day in UNCONFIRMED_HOURS:
        raise ValueError(
            f"NSE session hours for {day.isoformat()} need a calendar update before this run."
        )
    if (day in CLOSED_DAYS and window is not None) or (day in SPECIAL_HOURS and window is None):
        raise ValueError(
            f"OpenAlgo's NSE calendar needs an update for {day.isoformat()}. "
            "Apply the research calendar upgrade, then resume this run."
        )


def check_saved_calendar(snapshot):
    """Reject incorrect unfinished acquisition evidence without rewriting it.

    Completed exports/replay never call this: they keep their original evidence.
    """
    if snapshot.get("provenance", {}).get("calendar_admission") != VERSION:
        raise ValueError(
            "This unfinished run uses an older NSE calendar. Start a new run with the same signals."
        )
    sessions = snapshot.get("sessions", [])
    if not sessions:
        raise ValueError("The saved calendar is empty. Start a new run.")
    days = {date.fromisoformat(day) for day in sessions}
    first, last = min(days), max(days)
    require_covered_day(first)
    require_covered_day(last)
    if days & CLOSED_DAYS or any(first <= day <= last and day not in days for day in SPECIAL_HOURS):
        raise ValueError(
            "This unfinished run uses an older NSE calendar. Start a new run with the same signals."
        )
    if any(first <= day <= last for day in UNCONFIRMED_HOURS):
        raise ValueError(
            "This unfinished run needs updated NSE session hours. Start a new run after the calendar update."
        )
