"""Explain saved calendar exclusions without changing calculation evidence.

Only the job-detail response uses this view. Exports and exact replay continue
to read the original artifact. No price reads, broker calls or retained state.
"""

from bisect import bisect_left
from datetime import date

CALENDAR_REASON = "Signal date is outside the recorded session calendar"


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _label(day):
    return f"{day.day} {day:%b %Y}"


def _calendar_reason(signal_date, first, last, sessions):
    observed = _day(signal_date)
    if observed is None:
        return CALENDAR_REASON
    prefix = f"Signal date {_label(observed)}"
    if first is None or last is None or first > last:
        return f"{prefix} is not in the recorded session calendar. This saved result has no calendar date range."
    expected = f"Expected a recorded trading session from {_label(first)} to {_label(last)}."
    if observed < first:
        return f"{prefix} is before this run's calendar. {expected}"
    if observed > last:
        return f"{prefix} is after this run's calendar. {expected}"
    explanation = f"{prefix} is not a recorded trading session in this run. {expected}"
    if sessions and signal_date not in sessions:
        position = bisect_left(sessions, signal_date)
        if 0 < position < len(sessions):
            explanation += (
                f" Nearest recorded sessions: {_label(_day(sessions[position - 1]))}"
                f" and {_label(_day(sessions[position]))}."
            )
    return explanation


def present_result(result):
    """Copy only changed report containers; preserve every saved numerical field."""
    view = result
    ledger = result.get("ledger", [])
    if any(row.get("reason") == CALENDAR_REASON for row in ledger):
        coverage = result.get("coverage", {})
        first, last = _day(coverage.get("date_from")), _day(coverage.get("date_to"))
        # A daily curve records every session; a trimmed minute curve may not.
        # Only call its neighbors the nearest calendar sessions when complete.
        days = sorted(
            {point["date"] for point in result.get("equity_curve", []) if point.get("date")}
        )
        complete = (
            first is not None
            and last is not None
            and len(days) == coverage.get("session_count")
            and days
            and days[0] == first.isoformat()
            and days[-1] == last.isoformat()
            and all(_day(day) is not None for day in days)
        )
        view = {
            **result,
            "ledger": [
                {
                    **row,
                    "reason": _calendar_reason(
                        row.get("signal_date"), first, last, days if complete else []
                    ),
                }
                if row.get("reason") == CALENDAR_REASON
                else row
                for row in ledger
            ],
        }
    validation = result.get("validation")
    if validation and isinstance(validation.get("result"), dict):
        later = present_result(validation["result"])
        if later is not validation["result"]:
            view = {**view, "validation": {**validation, "result": later}}
    experiment = result.get("experiment", {})
    if isinstance(experiment.get("reports"), dict):
        reports = {key: present_result(report) for key, report in experiment["reports"].items()}
        if any(reports[key] is not report for key, report in experiment["reports"].items()):
            view = {**view, "experiment": {**experiment, "reports": reports}}
    return view
