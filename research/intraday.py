"""Minute-open causal scheduling over the shared cash, fill and risk evaluator.

Pure per-evaluation state: no descriptors, external clients or retained global caches.
"""

import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

POLICY_VERSION = "scanner-minute-causal-v1"
IST = timezone(timedelta(hours=5, minutes=30))


def validate_timing(config):
    result = {
        "trade_horizon": config.get("trade_horizon", "multiday"),
        "hold_minutes": config.get("hold_minutes"),
        "entry_time": config.get("entry_time"),
        "exit_time": config.get("exit_time"),
    }
    if result["trade_horizon"] not in ("intraday", "multiday"):
        raise ValueError("trade_horizon must be intraday or multiday")
    value = result["hold_minutes"]
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or int(value) != value
        or not 1 <= value <= 100000
    ):
        raise ValueError("hold_minutes must be an integer from 1 to 100000 or null")
    if value is not None:
        result["hold_minutes"] = int(value)
    for key in ("entry_time", "exit_time"):
        value = result[key]
        if value is not None and (
            not isinstance(value, str)
            or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value)
        ):
            raise ValueError(f"{key} must be HH:MM or null")
    if result["entry_time"] and result["exit_time"] and result["entry_time"] >= result["exit_time"]:
        raise ValueError("entry_time must precede exit_time")
    return result


def stamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() != timedelta(hours=5, minutes=30) or result.isoformat() != value:
            raise ValueError
        return result
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Minute timestamps must be canonical aware ISO timestamps in +05:30"
        ) from exc


def bounds(snapshot, day):
    hours = snapshot.get("session_hours", {}).get(day, {})
    opening = hours.get("open", "09:15")
    closing = hours.get("close", "15:30")
    validate_timing({"entry_time": opening, "exit_time": closing})
    return stamp(f"{day}T{opening}:00+05:30"), stamp(f"{day}T{closing}:00+05:30")


def validate_timeline(snapshot):
    timeline = snapshot.get("timeline", [])
    if not timeline or len(timeline) > 1125000 or timeline != sorted(set(timeline)):
        raise ValueError("Minute snapshot needs a bounded sorted unique timeline")
    if snapshot["provenance"].get("temporal_version") != "minute-open-v1":
        raise ValueError("Minute snapshot requires temporal_version minute-open-v1")
    days = set(snapshot["sessions"])
    previous = None
    session_bounds = {day: bounds(snapshot, day) for day in days}
    for value in timeline:
        instant = stamp(value)
        if instant.second or instant.microsecond or value[:10] not in days:
            raise ValueError("Timeline must contain exchange-session minute opens")
        first, end = session_bounds[value[:10]]
        if not first <= instant < end:
            raise ValueError("Minute lies outside recorded exchange session hours")
        if (
            previous
            and previous.date() == instant.date()
            and instant - previous != timedelta(minutes=1)
        ):
            raise ValueError(
                "Expected minute timeline cannot omit interior slots; retain missing bars explicitly"
            )
        previous = instant
    expected = []
    first_seen, last_seen = stamp(timeline[0]), stamp(timeline[-1])
    for day in snapshot["sessions"]:
        opening, closing = session_bounds[day]
        cursor = max(opening, first_seen)
        while cursor < closing and cursor <= last_seen:
            expected.append(cursor.isoformat())
            cursor += timedelta(minutes=1)
    if expected != timeline:
        raise ValueError(
            "Expected timeline omits scheduled minutes between its first and last observation"
        )


@dataclass(frozen=True)
class PreparedMinutes:
    signals: object
    snapshot: object
    signal_history: object
    coverage: dict
    instants: tuple
    observations: tuple


def prepare(signals, snapshot, signal_history=None):
    from .data import validate_snapshot

    if not signals or len(signals) > 25000:
        raise ValueError("Supply 1-25000 normalized signals")
    history = signals if signal_history is None else signal_history
    if len(history) > 25000:
        raise ValueError("Scanner history exceeds 25000 signals")
    observations = []
    for signal in history:
        if signal["date"] not in snapshot["sessions"]:
            continue
        observed = (
            stamp(signal["timestamp"])
            if signal.get("timestamp")
            else bounds(snapshot, signal["date"])[1]
        )
        if observed.date().isoformat() != signal["date"]:
            raise ValueError("Signal timestamp and date disagree")
        observations.append((observed, signal["date"]))
    return PreparedMinutes(
        signals,
        snapshot,
        history,
        validate_snapshot(snapshot, signals),
        tuple(stamp(t) for t in snapshot["timeline"]),
        tuple(sorted(observations)),
    )


def entry_open(signal, snapshot, cfg):
    """First eligible scheduled opening, shared by evaluator and data planner."""
    observed = (
        stamp(signal["timestamp"])
        if signal.get("timestamp")
        else bounds(snapshot, signal["date"])[1]
    )
    intended = None
    for day in snapshot["sessions"]:
        if day < signal["date"]:
            continue
        opening, closing = bounds(snapshot, day)
        candidate = max(opening, observed.replace(second=0, microsecond=0) + timedelta(minutes=1))
        if cfg.get("entry_time"):
            candidate = max(candidate, stamp(f"{day}T{cfg['entry_time']}:00+05:30"))
        if candidate < closing:
            intended = candidate
            break
    return intended


def evaluate_minutes(
    signals,
    snapshot,
    config=None,
    progress=None,
    *,
    prepared=None,
    summary_only=False,
    signal_history=None,
):
    from .engine import PreparedEvaluation, _evaluate_grid, validate_config

    cfg = validate_config(config)
    if prepared is None:
        prepared = prepare(signals, snapshot, signal_history)
    if (
        prepared.signals is not signals
        or prepared.snapshot is not snapshot
        or (signal_history is not None and prepared.signal_history is not signal_history)
    ):
        raise ValueError("Prepared evaluation belongs to different immutable inputs")
    timeline, instants = snapshot["timeline"], prepared.instants
    day_index = {day: i for i, day in enumerate(snapshot["sessions"])}
    closing_times = {day: bounds(snapshot, day)[1] for day in snapshot["sessions"]}
    observations = prepared.observations
    daily_counts = Counter(day for _, day in observations)
    times_by_day = {}
    for instant, day in observations:
        times_by_day.setdefault(day, []).append(instant)

    def qualifying(signal, observed):
        if "Bypass" in cfg["modes"]:
            return ["Bypass"]
        # Include simultaneous observations, never later signals on this session.
        own_day = signal["date"]
        counts = {day: daily_counts[day] for day in snapshot["sessions"] if day < own_day}
        counts[own_day] = bisect_right(times_by_day.get(own_day, []), observed)
        index = day_index[signal["date"]]
        days = snapshot["sessions"]
        current = counts[signal["date"]]
        previous = counts.get(days[index - 1], 0) if index else None

        def trough(i):
            return (
                i >= 7
                and 0 < counts.get(days[i], 0) < 3
                and counts.get(days[i], 0) < sum(counts.get(d, 0) for d in days[i - 7 : i]) / 14
            )

        return [
            mode
            for mode in cfg["modes"]
            if (mode == "Bottom Fishing" and trough(index))
            or (mode == "Zero Only" and previous == 0 and current > 0)
            or (
                mode == "Uptick"
                and index > 0
                and (trough(index - 1) or previous == 0)
                and current > previous
            )
        ]

    def schedule(signal, row, entries):
        if signal["date"] not in day_index:
            return
        observed = (
            stamp(signal["timestamp"])
            if signal.get("timestamp")
            else bounds(snapshot, signal["date"])[1]
        )
        if observed.date().isoformat() != signal["date"]:
            raise ValueError("Signal timestamp and date disagree")
        row["signal_timestamp"] = observed.isoformat()
        modes = qualifying(signal, observed)
        row["trigger_modes"] = modes
        if not modes:
            row["reason"] = "Selected causal scanner-count trigger modes did not qualify"
            return
        intended = entry_open(signal, snapshot, cfg)
        row.update(status="pending", reason="Next eligible minute opening is outside the snapshot")
        if intended is None:
            return
        i = bisect_left(instants, intended)
        row["entry_date"] = intended.isoformat()
        if i >= len(timeline) or instants[i] != intended:
            return
        # An intraday signal arriving after its configured exit cannot create an
        # overnight position. Date-only signals still schedule next-session entry.
        if cfg.get("exit_time") and instants[i].strftime("%H:%M") >= cfg["exit_time"]:
            row.update(
                status="excluded", reason="Eligible entry is at or after the configured exit time"
            )
            return
        row.update(entry_date=timeline[i], reason="Awaiting strictly later minute open")
        entries.setdefault(timeline[i], []).append(row)

    def open_due(position, i):
        entered, now = instants[position["entry_i"]], instants[i]
        if cfg.get("hold_minutes") is not None and now >= entered + timedelta(
            minutes=cfg["hold_minutes"]
        ):
            return True
        return bool(cfg.get("exit_time") and now.strftime("%H:%M") >= cfg["exit_time"])

    def close_due(position, i):
        now = instants[i]
        session_close = closing_times[timeline[i][:10]]
        if now + timedelta(minutes=1) != session_close:
            return False
        if cfg.get("trade_horizon", "multiday") == "intraday":
            return True
        return (
            day_index[timeline[i][:10]] - day_index[timeline[position["entry_i"]][:10]]
            >= cfg["hold_sessions"]
        )

    # Reuse the daily engine's numerical cash, exposure, fill, slippage, fees,
    # stop-first ambiguity and delayed trailing loop. Only scheduling/clocks differ.
    grid = {**snapshot, "sessions": timeline}
    grid_prepared = PreparedEvaluation(
        signals,
        grid,
        prepared.signal_history,
        prepared.coverage,
        {t: i for i, t in enumerate(timeline)},
        (0,) * len(timeline),
        (False,) * len(timeline),
    )
    result = _evaluate_grid(
        signals,
        grid,
        cfg,
        progress,
        prepared=grid_prepared,
        summary_only=summary_only,
        _temporal={"schedule": schedule, "open_due": open_due, "close_due": close_due},
    )
    result["policy_version"] = POLICY_VERSION
    result["metric_basis"] = "minute_marked"
    result["limits"] = [
        "Timestamped signals enter strictly after observation at a recorded minute open; date-only signals enter the next exchange session.",
        "Stops precede targets only within an ambiguous minute; opening gaps fill at open. Trailing changes become active next minute.",
        "Missing intervening minute bars leave positions pending. Snapshot tails are not assumed to be session closes.",
        "Intraday positions exit at the recorded session's final minute close unless earlier risk/time exits apply; multiday holding counts elapsed exchange sessions.",
        "Scanner-count qualification includes only observations available by the signal timestamp; date-only membership becomes observable at session close.",
        "Whole-share cash/exposure funding, explicit per-side fees and slippage follow the shared daily numerical policy. No unobserved intraminute ordering is inferred.",
    ]
    for row in result["equity_curve"]:
        row["timestamp"] = row["date"]
        row["date"] = row["date"][:10]
    for row in result["ledger"]:
        row["exit_timestamp_basis"] = (
            "bar_open; risk crossing time within minute is unknown"
            if row.get("exit_timing") == "intraday"
            else row.get("exit_timing")
        )
        for field in ("entry", "exit"):
            row[f"{field}_timestamp"] = row[f"{field}_date"]
            if row[f"{field}_date"]:
                row[f"{field}_date"] = row[f"{field}_date"][:10]
        if row.get("exit_timestamp") and row.get("exit_timing") == "close":
            row["exit_timestamp"] = (
                stamp(row["exit_timestamp"]) + timedelta(minutes=1)
            ).isoformat()
    return result
