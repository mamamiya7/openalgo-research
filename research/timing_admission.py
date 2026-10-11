"""Presentation-only timing admission over native inputs and recorded sessions.

This does not add searched clocks, broaden instruments, read prices, or change a
saved recipe. A clock-search recipe must first qualify union coverage and a fixed
opportunity cohort. All state here is bounded and local to this receipt.
"""

from bisect import bisect_left
from datetime import date, timedelta

from research.data import NATIVE_CALENDAR_BASIS
from research.intraday import stamp, validate_timing

VERSION = "research-timing-admission-v1"


def _deadlines(row):
    cfg, search = row["config"], row.get("search", {})
    constraints = []
    if cfg.get("hold_minutes") is not None:
        constraints.append(
            {
                "parameter": "hold_minutes",
                "value": cfg["hold_minutes"],
                "maximum": search.get("hold_minutes", {}).get("max", cfg["hold_minutes"]),
            }
        )
    if cfg.get("trade_horizon") == "intraday":
        constraints.append({"parameter": "session_close", "basis": "recorded native session"})
    else:
        constraints.append(
            {
                "parameter": "hold_sessions",
                "value": cfg["hold_sessions"],
                "maximum": search.get("hold_sessions", {}).get("max", cfg["hold_sessions"]),
            }
        )
    if cfg.get("exit_time") is not None:
        constraints.append({"parameter": "exit_time", "value": cfg["exit_time"]})
    axis = (
        "hold_minutes"
        if cfg.get("hold_minutes") is not None
        else "session_close"
        if cfg.get("trade_horizon") == "intraday"
        else "hold_sessions"
    )
    return {
        "searched_deadline": axis if axis in search else None,
        "constraints": constraints,
        "dormant_parameters": ["hold_sessions"] if cfg.get("trade_horizon") == "intraday" else [],
        "combination": "earliest applicable deadline; clocks and account assumptions stay fixed",
    }


def _native_calendar(calendar):
    if calendar is None:
        return None, "Recorded native sessions have not been prepared yet"
    sessions = calendar.get("sessions", [])
    provenance = calendar.get("provenance", {})
    if (
        provenance.get("calendar_basis") != NATIVE_CALENDAR_BASIS
        or provenance.get("exchange") != "NSE"
    ):
        return None, "Native NSE calendar provenance is required to verify timing"
    if not sessions or len(sessions) > 3000 or sessions != sorted(set(sessions)):
        return None, "Native calendar needs bounded ordered unique sessions"
    hours, parsed = calendar.get("session_hours", {}), {}
    try:
        for day in sessions:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
            item = hours[day]
            validate_timing({"entry_time": item["open"], "exit_time": item["close"]})
            parsed[day] = (
                stamp(f"{day}T{item['open']}:00+05:30"),
                stamp(f"{day}T{item['close']}:00+05:30"),
            )
    except (KeyError, TypeError, ValueError):
        return None, "Recorded native opening and closing hours are required for every session"
    return {"sessions": sessions, "hours": parsed}, None


def _minute_at_or_after(instant, native):
    sessions, hours = native["sessions"], native["hours"]
    index = bisect_left(sessions, instant.date().isoformat())
    for ordinal in range(index, len(sessions)):
        day = sessions[ordinal]
        opening, closing = hours[day]
        candidate = max(opening, instant)
        if candidate < closing:
            return candidate
    return None


def _minute_schedule(signal, cfg, native, entry_days):
    hours = native["hours"]
    day = signal["date"]
    observed = stamp(signal["timestamp"]) if signal.get("timestamp") else hours[day][1]
    if observed.date().isoformat() != day:
        return None, "Signal timestamp and date disagree"
    following = observed.replace(second=0, microsecond=0) + timedelta(minutes=1)
    start = bisect_left(entry_days, following.date().isoformat())
    if entry_days and start >= len(entry_days):
        return None, None
    for current in entry_days[start : start + 2]:
        opening, closing = hours[current]
        candidate = max(opening, following)
        if cfg.get("entry_time"):
            candidate = max(candidate, stamp(f"{current}T{cfg['entry_time']}:00+05:30"))
        if candidate < closing:
            if cfg.get("exit_time") and candidate.strftime("%H:%M") >= cfg["exit_time"]:
                return None, "Eligible entry is at or after the configured exit time"
            return candidate, None
    return None, "No eligible entry fits the recorded session hours"


def _minute_deadline(entry, cfg, native, exit_days):
    sessions, hours = native["sessions"], native["hours"]
    day_index = bisect_left(sessions, entry.date().isoformat())
    candidates = []
    if cfg.get("hold_minutes") is not None:
        candidate = _minute_at_or_after(entry + timedelta(minutes=cfg["hold_minutes"]), native)
        if candidate is not None:
            candidates.append(candidate)
    if cfg.get("exit_time"):
        index = bisect_left(exit_days, entry.date().isoformat())
        if index < len(exit_days):
            day = exit_days[index]
            candidates.append(max(hours[day][0], stamp(f"{day}T{cfg['exit_time']}:00+05:30")))
    final_index = day_index + (
        0 if cfg.get("trade_horizon") == "intraday" else cfg["hold_sessions"]
    )
    if final_index < len(sessions):
        candidates.append(hours[sessions[final_index]][1] - timedelta(minutes=1))
    return min(candidates) if candidates else None


def _check_strategy(row, native, minute):
    cfg, sessions, hours = row["config"], native["sessions"], native["hours"]
    index = {day: i for i, day in enumerate(sessions)}
    entry_days = [
        day
        for day in sessions
        if not cfg.get("entry_time") or cfg["entry_time"] < hours[day][1].strftime("%H:%M")
    ]
    exit_days = [
        day
        for day in sessions
        if cfg.get("exit_time") and cfg["exit_time"] < hours[day][1].strftime("%H:%M")
    ]
    counts = {"scheduled": 0, "excluded": 0, "incomplete": 0}
    first_issue = None
    for signal in row["signals"]:
        issue, entry, deadline = signal.get("research_exclusion"), None, None
        signal_day = signal["date"]
        if not issue and signal_day not in index:
            issue = "Signal date is outside the recorded session calendar"
        if not issue:
            if minute:
                entry, issue = _minute_schedule(signal, cfg, native, entry_days)
                if entry is not None:
                    deadline = _minute_deadline(entry, cfg, native, exit_days)
            else:
                ordinal = index[signal_day] + 1
                entry = sessions[ordinal] if ordinal < len(sessions) else None
                last = ordinal + cfg["hold_sessions"]
                deadline = sessions[last] if last < len(sessions) else None
        if issue:
            counts["excluded"] += 1
        elif entry is None or deadline is None:
            counts["incomplete"] += 1
            issue = "Recorded calendar does not yet contain a complete entry and maximum holding deadline"
        else:
            counts["scheduled"] += 1
        if issue and first_issue is None:
            first_issue = {
                "symbol": signal["symbol"],
                "signal_date": signal_day,
                "signal_timestamp": signal.get("timestamp"),
                "reason": issue,
                "calendar_from": sessions[0],
                "calendar_to": sessions[-1],
                "session_hours": {
                    "open": hours[signal_day][0].strftime("%H:%M"),
                    "close": hours[signal_day][1].strftime("%H:%M"),
                }
                if signal_day in hours
                else None,
                "entry_time": cfg.get("entry_time"),
                "exit_time": cfg.get("exit_time"),
            }
    return {**counts, "first_issue": first_issue}


def build_admission(portfolio, strategies, *, interval=None, calendar=None):
    """Explain existing timing and prerequisites without changing any request.

    Call on normalized native long NSE cash-equity strategies. Calendar readiness
    is not price coverage or engine runtime readiness. No clock alternative is
    admitted to an automatic recipe by this presentation receipt.
    """
    from research.portfolio import fingerprint, interval_for, requirement_strategies
    from research.requirements import select_interval

    if not isinstance(strategies, list) or not 1 <= len(strategies) <= 8:
        raise ValueError("Timing admission supports one to eight native strategies")
    if sum(len(row["signals"]) for row in strategies) > 25000:
        raise ValueError("Timing admission supports at most 25000 normalized signals")
    required = requirement_strategies(portfolio, strategies)
    expected = interval_for(portfolio, strategies)
    effective = expected if interval is None else interval
    native, calendar_reason = _native_calendar(calendar)
    rows, requested_intervals = [], []
    for original, maximum in zip(strategies, required, strict=True):
        signals = original["signals"]
        requested = select_interval(signals, {"config": maximum["config"]})
        requested_intervals.append(requested)
        timed = sum(bool(signal.get("timestamp")) for signal in signals)
        cfg = original["config"]
        resolution = []
        if timed:
            resolution.append("timestamped_signals")
        if cfg.get("trade_horizon") == "intraday":
            resolution.append("intraday_horizon")
        if cfg.get("hold_minutes") is not None:
            resolution.append("minute_holding_limit")
        if cfg.get("entry_time") is not None:
            resolution.append("fixed_entry_clock")
        if cfg.get("exit_time") is not None:
            resolution.append("fixed_exit_clock")
        reason = (
            "Timestamped signals require minute prices"
            if timed
            else "Intraday holding requires minute prices"
            if cfg.get("trade_horizon") == "intraday"
            else "Minute holding limit requires minute prices"
            if cfg.get("hold_minutes") is not None
            else "Entry/exit clocks require minute prices"
            if cfg.get("entry_time") is not None or cfg.get("exit_time") is not None
            else "Dated signals use next-session entry and session holding"
        )
        row = {
            "id": original["id"],
            "required_interval": requested,
            "effective_interval": effective,
            "signals": {"total": len(signals), "timed": timed, "date_only": len(signals) - timed},
            "intent": "mixed"
            if 0 < timed < len(signals)
            else "timed"
            if requested == "1m"
            else "daily",
            "resolution_reasons": resolution or ["daily_signals_and_session_holding"],
            "reason": reason,
            "fixed_clocks": {
                "entry_time": original["config"].get("entry_time"),
                "exit_time": original["config"].get("exit_time"),
            },
            **_deadlines(original),
            "calendar": _check_strategy(maximum, native, effective == "1m")
            if native and effective == expected
            else None,
        }
        rows.append(row)
    checks = [row["calendar"] for row in rows if row["calendar"] is not None]
    status = "pending_calendar" if native is None else "ready"
    reason = calendar_reason
    if effective != expected:
        status, reason = (
            "blocked",
            f"These inputs require {expected} candles; selected data is {effective}",
        )
    elif checks and any(row["incomplete"] for row in checks):
        status, reason = (
            "pending_calendar",
            "Extend the recorded calendar through every maximum holding deadline",
        )
    elif checks and not any(row["scheduled"] for row in checks):
        status, reason = (
            "blocked",
            "No signal has eligible execution timing in the recorded calendar",
        )
    payload = {
        "version": VERSION,
        "status": status,
        "reason": reason,
        "asset_scope": "long NSE cash-equity signals",
        "engine": portfolio["engine"],
        "required_interval": expected,
        "effective_interval": effective,
        "intent": "mixed"
        if len(set(requested_intervals)) > 1 or any(row["intent"] == "mixed" for row in rows)
        else "timed"
        if expected == "1m"
        else "daily",
        "signals": {
            "total": sum(row["signals"]["total"] for row in rows),
            "timed": sum(row["signals"]["timed"] for row in rows),
            "date_only": sum(row["signals"]["date_only"] for row in rows),
        },
        "resolution_reasons": list(
            dict.fromkeys(reason for row in rows for reason in row["resolution_reasons"])
        ),
        "strategies": rows,
        "calendar": {
            "basis": NATIVE_CALENDAR_BASIS if native else None,
            "from": native["sessions"][0] if native else None,
            "to": native["sessions"][-1] if native else None,
        },
        "calendar_counts": {
            key: sum(row[key] for row in checks) for key in ("scheduled", "excluded", "incomplete")
        }
        if checks
        else None,
        "price_coverage": "not_checked",
        "timing_search": {
            "status": "not_searched",
            "reason": "Entry/exit clocks remain fixed; no automatic clock alternatives are admitted",
            "prerequisites": [
                "versioned bounded timing choices",
                "union acquisition for every candidate entry/deadline",
                "fixed opportunity cohort across timing branches",
                "causal timing and feature parity",
                "independent later validation",
            ],
        },
        "inputs_id": fingerprint(
            {
                "portfolio": {
                    key: portfolio[key]
                    for key in (
                        "version",
                        "engine",
                        "capital",
                        "optimization",
                        "automatic_research",
                        "validation",
                    )
                    if key in portfolio
                },
                "strategies": [
                    {
                        **{
                            key: row[key]
                            for key in ("id", "source_id", "allocation_pct", "config", "search")
                            if key in row
                        },
                        "signals": [
                            {
                                key: signal[key]
                                for key in (
                                    "symbol",
                                    "date",
                                    "timestamp",
                                    "row",
                                    "research_exclusion",
                                )
                                if key in signal
                            }
                            for signal in row["signals"]
                        ],
                    }
                    for row in strategies
                ],
                "calendar": {
                    key: calendar.get(key) for key in ("sessions", "session_hours", "provenance")
                }
                if calendar is not None
                else None,
                "interval": effective,
            }
        ),
    }
    return {**payload, "id": fingerprint(payload)}
