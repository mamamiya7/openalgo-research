"""Chronological holdout inputs: split before search, never share positions."""

import math

from research.portfolio import requirement_strategies
from research.portfolio_coverage import prepare


def split_date(evidence):
    days = sorted({signal["date"] for signal in evidence["signals"]})
    if len(days) < 5:
        raise ValueError("A later-period check needs signals on at least five different dates")
    count = math.floor(len(days) * evidence["portfolio"]["validation"]["train_pct"] / 100)
    return days[count - 1], days[count]


def _snapshot(snapshot, first, last):
    minute = snapshot["provenance"]["interval"] == "1m"
    result = {
        **snapshot,
        "sessions": [day for day in snapshot["sessions"] if first <= day <= last],
        "bars": {
            symbol: {key: bar for key, bar in series.items() if first <= key[:10] <= last}
            for symbol, series in snapshot["bars"].items()
        },
        "provenance": {
            **snapshot["provenance"],
            "available_through": last,
            "research_period_slice": {"from": first, "to": last},
        },
    }
    if "raw_bars" in snapshot:
        result["raw_bars"] = {
            symbol: {key: bar for key, bar in series.items() if first <= key[:10] <= last}
            for symbol, series in snapshot["raw_bars"].items()
        }
    for field in ("required_dates", "required_timestamps"):
        if field in snapshot:
            result[field] = {
                symbol: [key for key in slots if first <= key[:10] <= last]
                for symbol, slots in snapshot[field].items()
            }
    if minute:
        result["timeline"] = [key for key in snapshot["timeline"] if first <= key[:10] <= last]
    if "session_hours" in snapshot:
        result["session_hours"] = {
            day: hours for day, hours in snapshot["session_hours"].items() if first <= day <= last
        }
    return result


def partition(evidence):
    from research.connectors.vectorbt_portfolio import _deadline, _schedule

    train_end, test_start = split_date(evidence)
    snapshot = evidence["snapshot"]
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    requirements = {
        row["id"]: row
        for row in requirement_strategies(evidence["portfolio"], evidence["strategies"])
    }
    periods = []
    for training in (True, False):
        first = snapshot["sessions"][0] if training else test_start
        last = train_end if training else snapshot["sessions"][-1]
        strategies = []
        for row in evidence["strategies"]:
            signals = []
            cfg = requirements[row["id"]]["config"]
            for original in row["signals"]:
                if not first <= original["date"] <= last:
                    continue
                signal = dict(original)
                if training:
                    entry, reason = _schedule(signal, snapshot, cfg, timeline, index, minute)
                    if (
                        entry >= 0
                        and timeline[_deadline(entry, snapshot, cfg, timeline, minute)][:10] > last
                    ):
                        signal["research_exclusion"] = (
                            "Holding window crosses the later-period boundary"
                        )
                signals.append(signal)
            strategies.append({**row, "signals": signals})
        selected = {
            **evidence,
            "strategies": strategies,
            "signals": [
                dict(signal, strategy_id=row["id"])
                for row in strategies
                for signal in row["signals"]
            ],
            "snapshot": _snapshot(snapshot, first, last),
        }
        periods.append(prepare(selected))
    return (
        periods[0],
        periods[1],
        {
            "label": "Later period",
            "train_from": min(s["date"] for s in periods[0]["signals"]),
            "train_to": train_end,
            "test_from": test_start,
            "test_to": snapshot["sessions"][-1],
            "training_signals": len(periods[0]["signals"]),
            "testing_signals": len(periods[1]["signals"]),
            "selection_basis": "settings chosen on earlier period only",
            "shared_positions_across_periods": False,
        },
    )
