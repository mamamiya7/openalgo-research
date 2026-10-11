"""Bounded, opt-in native timing union and shared evidence cohort.

This qualifies fixed clock branches, not an automatic optimiser recipe. Existing
v2-v6 requests and saved artefacts never call this module. No resources are owned.
"""

from collections import Counter
from copy import deepcopy
from datetime import timedelta

from research.connectors.optuna_portfolio import MAX_CHECKPOINT_BYTES, _fingerprint
from research.engine import validate_config
from research.intraday import stamp
from research.portfolio import fingerprint, interval_for, requirement_strategies
from research.timing_admission import _minute_deadline, _minute_schedule, _native_calendar

VERSION = "research-timing-choices-v1"
UNION_VERSION = "research-timing-union-v1"
COHORT_VERSION = "research-timing-cohort-v1"
MAX_BRANCHES = 4
MAX_WINDOW_VISITS = 2_000_000
MAX_MATRIX_CELLS = 500_000


def _identity(portfolio, strategies):
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= 8:
        raise ValueError("Timing choices support one to eight strategies")
    if sum(len(row["signals"]) for row in strategies) > 25000:
        raise ValueError("Timing choices support at most 25000 observations")
    if portfolio.get("version") != "research-portfolio-v1" or len(
        {row["id"] for row in strategies}
    ) != len(strategies):
        raise ValueError("Timing choices require normalized native portfolio identities")
    definitions = portfolio.get("strategies", strategies)
    if [row["id"] for row in definitions] != [row["id"] for row in strategies] or any(
        any(
            original.get(key) != resolved.get(key)
            for key in ("source_id", "allocation_pct", "config", "search")
        )
        or (
            "entry_condition" in original
            and original["entry_condition"] != resolved.get("entry_condition")
        )
        for original, resolved in zip(definitions, strategies, strict=True)
    ):
        raise ValueError("Timing source strategies differ from the frozen portfolio settings")
    for row in strategies:
        for value in (row, *row["signals"]):
            if value.get("exchange", "NSE") != "NSE" or value.get("asset_class", "equity") not in (
                "equity",
                "cash_equity",
                "stock",
                "EQ",
            ):
                raise ValueError("Timing choices retain the existing long NSE cash-equity scope")
    return fingerprint(
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
                    "date_from",
                    "date_to",
                    "entry_conditions",
                )
                if key in portfolio
            },
            "strategies": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "id",
                            "source_id",
                            "allocation_pct",
                            "config",
                            "search",
                            "entry_condition",
                            "original_csv_sha256",
                            "original_signals_sha256",
                        )
                        if key in row
                    },
                    "signals": [
                        {
                            key: signal[key]
                            for key in ("symbol", "date", "timestamp", "row", "research_exclusion")
                            if key in signal
                        }
                        for signal in row["signals"]
                    ],
                }
                for row in strategies
            ],
        }
    )


def _calendar_identity(calendar):
    projection = _calendar_projection(calendar)
    return fingerprint(
        {
            **projection,
            "exchange": calendar["provenance"].get("exchange"),
            "basis": calendar["provenance"].get("calendar_basis"),
        }
    )


def _calendar_projection(calendar):
    native, reason = _native_calendar(calendar)
    if native is None:
        raise ValueError(reason)
    return {
        "sessions": list(native["sessions"]),
        "session_hours": {
            day: {"open": opening.strftime("%H:%M"), "close": closing.strftime("%H:%M")}
            for day, (opening, closing) in native["hours"].items()
        },
    }


def _seal(payload):
    return {**payload, "id": _fingerprint(payload, max_bytes=MAX_CHECKPOINT_BYTES)}


def _intact(payload, version):
    if (
        not isinstance(payload, dict)
        or payload.get("version") != version
        or payload.get("id")
        != _fingerprint(
            {key: value for key, value in payload.items() if key != "id"},
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
    ):
        raise ValueError("Saved timing evidence changed")


def _clock_domains(cfg, native):
    days, hours = native["sessions"], native["hours"]
    return (
        [
            day
            for day in days
            if not cfg.get("entry_time") or cfg["entry_time"] < hours[day][1].strftime("%H:%M")
        ],
        [
            day
            for day in days
            if cfg.get("exit_time") and cfg["exit_time"] < hours[day][1].strftime("%H:%M")
        ],
    )


def _window(signal, cfg, native, domains):
    if signal.get("research_exclusion"):
        return {"state": "original_excluded", "reason": signal["research_exclusion"]}
    if signal["date"] not in native["hours"]:
        return {
            "state": "calendar_unavailable",
            "reason": f"Signal date {signal['date']} is outside recorded sessions {native['sessions'][0]}–{native['sessions'][-1]}",
        }
    entry, reason = _minute_schedule(signal, cfg, native, domains[0])
    if reason == "Eligible entry is at or after the configured exit time":
        return {"state": "rule_filtered", "reason": reason}
    if entry is None:
        return {
            "state": "calendar_unavailable",
            "reason": reason or "Recorded calendar has no later eligible entry",
        }
    deadline = _minute_deadline(entry, cfg, native, domains[1])
    if deadline is None:
        return {
            "state": "calendar_unavailable",
            "reason": f"Recorded calendar ends {native['sessions'][-1]} before the complete holding deadline",
        }
    return {"state": "scheduled", "entry": entry.isoformat(), "deadline": deadline.isoformat()}


def propose(portfolio, strategies, calendar=None, *, interval=None):
    """Freeze at most four meaningful clock branches without changing intent."""
    from research.requirements import select_interval

    inputs_id = _identity(portfolio, strategies)
    expected = interval_for(portfolio, strategies)
    effective = expected if interval is None else interval
    payload = {
        "version": VERSION,
        "inputs_id": inputs_id,
        "interval": effective,
        "engine": portfolio["engine"],
        "asset_scope": "long NSE cash-equity signals",
        "status": "pending_calendar",
        "reason": "Recorded native hours are needed before proposing clock choices",
        "calendar_id": None,
        "strategies": [],
        "policy": "fixed account, horizon and holding ranges; original reference plus bounded native clock alternatives; no automatic v7 admission",
    }
    if effective != expected:
        payload.update(
            status="blocked",
            reason=f"Inputs require {expected} data; selected interval is {effective}",
        )
    elif expected == "D":
        payload.update(
            status="not_applicable",
            reason="Daily-only inputs retain daily execution; no minute conversion is inferred",
        )
    elif portfolio["engine"] != "vectorbt":
        payload.update(
            status="not_applicable",
            reason="Timing-choice qualification currently requires VectorBT",
        )
    native, calendar_reason = _native_calendar(calendar)
    if native is None and payload["status"] == "pending_calendar":
        payload["reason"] = calendar_reason
    if native is not None:
        payload["calendar_id"] = _calendar_identity(calendar)
    maxima = {row["id"]: row for row in requirement_strategies(portfolio, strategies)}
    incomplete_reason = None
    for row in strategies:
        branches = [{"id": "original", "label": "Original timing", "changes": {}}]
        removed = []
        if (
            native
            and payload["status"] == "pending_calendar"
            and select_interval(row["signals"], {"config": row["config"]}) == "1m"
        ):
            modal = Counter(
                (start.strftime("%H:%M"), end.strftime("%H:%M"))
                for start, end in native["hours"].values()
            )
            opening, closing = sorted(modal, key=lambda value: (-modal[value], value))[0]
            anchor = native["sessions"][0]
            candidates = []
            for minutes in (15, 60):
                clock = (
                    stamp(f"{anchor}T{opening}:00+05:30") + timedelta(minutes=minutes)
                ).strftime("%H:%M")
                if opening <= clock < closing:
                    candidates.append(
                        {
                            "id": f"entry_{clock.replace(':', '')}",
                            "label": f"Entry from {clock}",
                            "changes": {"entry_time": clock},
                        }
                    )
            if row["config"].get("trade_horizon") == "intraday":
                clock = (stamp(f"{anchor}T{closing}:00+05:30") - timedelta(minutes=15)).strftime(
                    "%H:%M"
                )
                if opening < clock < closing:
                    candidates.append(
                        {
                            "id": f"exit_{clock.replace(':', '')}",
                            "label": f"Exit by {clock}",
                            "changes": {"exit_time": clock},
                        }
                    )
            baseline_cfg = maxima[row["id"]]["config"]
            baseline_domains = _clock_domains(baseline_cfg, native)
            baseline_windows = [
                _window(signal, baseline_cfg, native, baseline_domains) for signal in row["signals"]
            ]
            for signal, window in zip(row["signals"], baseline_windows, strict=True):
                if window["state"] == "calendar_unavailable" and incomplete_reason is None:
                    incomplete_reason = (
                        f"{signal['symbol']} · {signal['date']} · original: {window['reason']}"
                    )
            seen = {fingerprint(baseline_windows)}
            for branch in candidates:
                try:
                    cfg = validate_config({**baseline_cfg, **branch["changes"]})
                except ValueError:
                    removed.append(
                        {
                            "id": branch["id"],
                            "reason": "Clock conflicts with retained entry/exit rules",
                        }
                    )
                    continue
                domains = _clock_domains(cfg, native)
                windows = [_window(signal, cfg, native, domains) for signal in row["signals"]]
                identity = fingerprint(windows)
                incomplete = any(window["state"] == "calendar_unavailable" for window in windows)
                for signal, window in zip(row["signals"], windows, strict=True):
                    if window["state"] == "calendar_unavailable" and incomplete_reason is None:
                        incomplete_reason = f"{signal['symbol']} · {signal['date']} · {branch['id']}: {window['reason']}"
                if identity in seen and not incomplete:
                    removed.append(
                        {
                            "id": branch["id"],
                            "reason": "No distinct entry/deadline on these observations",
                        }
                    )
                    continue
                if not incomplete and not any(
                    window["state"] in ("scheduled", "rule_filtered") for window in windows
                ):
                    removed.append(
                        {
                            "id": branch["id"],
                            "reason": "No feasible native timing on recorded observations",
                        }
                    )
                    continue
                seen.add(identity)
                branches.append(branch)
        if len(branches) > MAX_BRANCHES:
            raise ValueError("Timing choices exceed the bounded branch count")
        payload["strategies"].append({"id": row["id"], "branches": branches, "removed": removed})
    if native and payload["status"] == "pending_calendar":
        payload.update(
            status="pending_calendar" if incomplete_reason else "ready", reason=incomplete_reason
        )
        if not incomplete_reason and not any(
            len(row["branches"]) > 1 for row in payload["strategies"]
        ):
            payload.update(
                status="not_applicable",
                reason="No distinct bounded clock alternative changes execution on these observations",
            )
    return _seal(payload)


def build_union(portfolio, strategies, calendar, descriptor=None):
    """Plan all registered branch windows before one native acquisition pass."""
    expected = propose(portfolio, strategies, calendar)
    descriptor = expected if descriptor is None else descriptor
    _intact(descriptor, VERSION)
    if descriptor != expected:
        raise ValueError("Timing choices do not match the frozen inputs and native calendar")
    payload = {
        "version": UNION_VERSION,
        "descriptor_id": descriptor["id"],
        "inputs_id": descriptor["inputs_id"],
        "calendar_id": descriptor["calendar_id"],
        "status": descriptor["status"],
        "reason": descriptor["reason"],
        "interval": descriptor["interval"],
        "required_timestamps": {},
        "opportunities": [],
        "request_key": fingerprint(
            {"portfolio": descriptor["inputs_id"], "choices": descriptor["id"]}
        ),
    }
    if descriptor["status"] != "ready":
        return _seal(payload)
    native, _ = _native_calendar(calendar)
    days, hours = native["sessions"], native["hours"]
    starts, total = {}, 0
    for day in days:
        starts[day] = total
        total += int((hours[day][1] - hours[day][0]).total_seconds() // 60)
    if total > 1_125_000:
        raise ValueError("Timing union exceeds the native minute timeline limit")

    def rank(value):
        return starts[value[:10]] + int((stamp(value) - hours[value[:10]][0]).total_seconds() // 60)

    definitions = {row["id"]: row["branches"] for row in descriptor["strategies"]}
    maxima = {row["id"]: row for row in requirement_strategies(portfolio, strategies)}
    ranges, visits, first, last = {}, 0, total, -1
    for row in strategies:
        configs = [
            (branch, validate_config({**maxima[row["id"]]["config"], **branch["changes"]}))
            for branch in definitions[row["id"]]
        ]
        configs = [(branch, cfg, _clock_domains(cfg, native)) for branch, cfg in configs]
        for index, signal in enumerate(row["signals"]):
            windows = {}
            for branch, cfg, domains in configs:
                window = _window(signal, cfg, native, domains)
                windows[branch["id"]] = window
                if window["state"] == "calendar_unavailable":
                    payload.update(
                        status="pending_calendar",
                        reason=f"{signal['symbol']} · {signal['date']} · {branch['id']}: {window['reason']}",
                    )
                if window["state"] == "scheduled":
                    left, right = rank(window["entry"]), rank(window["deadline"])
                    visits += right - left + 1
                    first, last = min(first, left), max(last, right)
                    if visits > MAX_WINDOW_VISITS:
                        raise ValueError(
                            "Timing union exceeds its bounded coverage work; shorten the period or holding range"
                        )
                    ranges.setdefault(signal["symbol"], []).append((left, right))
            payload["opportunities"].append(
                {
                    "strategy_id": row["id"],
                    "signal_index": index,
                    "symbol": signal["symbol"],
                    "signal_date": signal["date"],
                    "windows": windows,
                }
            )
    count = sum(len(row["signals"]) for row in strategies)
    if last >= first and (last - first + 1) * count > MAX_MATRIX_CELLS:
        raise ValueError(
            "Timing union exceeds the native shared-account matrix bound; shorten the period or reduce observations"
        )
    if payload["status"] != "ready":
        return _seal(payload)
    if last < first:
        payload.update(
            status="not_applicable",
            reason="No scheduled opportunity remains in the bounded timing choices",
        )
        return _seal(payload)
    timeline = []
    for day in days:
        left = max(0, first - starts[day])
        right = min(
            int((hours[day][1] - hours[day][0]).total_seconds() // 60) - 1, last - starts[day]
        )
        timeline.extend(
            (hours[day][0] + timedelta(minutes=minute)).isoformat()
            for minute in range(left, right + 1)
        )
    for symbol, windows in sorted(ranges.items()):
        merged = []
        for left, right in sorted(windows):
            if merged and left <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], right))
            else:
                merged.append((left, right))
        payload["required_timestamps"][symbol] = [
            timeline[ordinal - first]
            for left, right in merged
            for ordinal in range(left, right + 1)
        ]
    payload.update(
        timeline=timeline,
        session_hours=_calendar_projection(calendar)["session_hours"],
        sessions=list(days),
        counts={
            "source_signals": count,
            "window_visits": visits,
            "required_candles": sum(
                len(slots) for slots in payload["required_timestamps"].values()
            ),
            "matrix_cells": len(timeline) * count,
        },
    )
    return _seal(payload)


def validate_union(portfolio, strategies, calendar, descriptor, plan):
    _intact(plan, UNION_VERSION)
    if plan != build_union(portfolio, strategies, calendar, descriptor):
        raise ValueError("Saved timing union changed")
    return plan


def freeze_cohort(evidence, descriptor, plan):
    """Freeze only unavailable evidence as common exclusions, never policy filters."""
    from research.data import validate_snapshot

    _intact(descriptor, VERSION)
    _intact(plan, UNION_VERSION)
    if (
        plan["status"] != "ready"
        or plan["descriptor_id"] != descriptor["id"]
        or descriptor["inputs_id"] != _identity(evidence["portfolio"], evidence["strategies"])
    ):
        raise ValueError("Timing cohort needs its exact ready union and source inputs")
    snapshot = evidence["snapshot"]
    if (
        snapshot["provenance"]["interval"] != "1m"
        or _calendar_identity(snapshot) != plan["calendar_id"]
        or snapshot["timeline"] != plan["timeline"]
    ):
        raise ValueError("Timing cohort needs the exact shared native minute calendar and timeline")
    validate_union(evidence["portfolio"], evidence["strategies"], snapshot, descriptor, plan)
    validate_snapshot(snapshot, evidence["signals"])
    index = {slot: ordinal for ordinal, slot in enumerate(snapshot["timeline"])}
    copied = {
        row["id"]: [dict(signal) for signal in row["signals"]] for row in evidence["strategies"]
    }
    counts = {"eligible": 0, "common_excluded": 0, "original_excluded": 0, "all_rule_filtered": 0}
    exclusions, branch_counts = [], {}
    for item in plan["opportunities"]:
        signal = copied[item["strategy_id"]][item["signal_index"]]
        if signal.get("research_exclusion"):
            counts["original_excluded"] += 1
            continue
        reason, branch_id = None, None
        any_scheduled = False
        for branch, window in item["windows"].items():
            counter = branch_counts.setdefault(item["strategy_id"], {}).setdefault(
                branch, {"scheduled": 0, "rule_filtered": 0}
            )
            if window["state"] == "rule_filtered":
                counter["rule_filtered"] += 1
            elif window["state"] == "scheduled":
                counter["scheduled"] += 1
                any_scheduled = True
                left, right = index[window["entry"]], index[window["deadline"]]
                missing = next(
                    (
                        slot
                        for slot in snapshot["timeline"][left : right + 1]
                        if slot not in snapshot["bars"].get(signal["symbol"], {})
                    ),
                    None,
                )
                if missing is not None and reason is None:
                    reason, branch_id = (
                        f"Timing evidence unavailable: {signal['symbol']} at {missing} ({branch})",
                        branch,
                    )
        if reason:
            signal["research_exclusion"] = reason
            counts["common_excluded"] += 1
            exclusions.append(
                {
                    "strategy_id": item["strategy_id"],
                    "symbol": signal["symbol"],
                    "date": signal["date"],
                    "branch_id": branch_id,
                    "reason": reason,
                }
            )
        elif any_scheduled:
            counts["eligible"] += 1
        else:
            counts["all_rule_filtered"] += 1
    payload = {
        "version": COHORT_VERSION,
        "descriptor_id": descriptor["id"],
        "plan_id": plan["id"],
        "inputs_id": descriptor["inputs_id"],
        "snapshot_id": fingerprint(snapshot),
        "strategies": [
            {"id": row["id"], "signals": copied[row["id"]]} for row in evidence["strategies"]
        ],
        "counts": counts,
        "exclusions": exclusions,
        "branch_counts": branch_counts,
        "policy": "same source opportunities; unavailable scheduled evidence excluded across branches; intentional clock filters remain branch-specific",
    }
    return _seal(payload)


def materialize(evidence, cohort, selection):
    """Produce one staged fixed portfolio on shared frozen evidence, no optimiser."""
    descriptor, plan = evidence["timing_descriptor"], evidence["timing_union"]
    _intact(cohort, COHORT_VERSION)
    expected = freeze_cohort(evidence, descriptor, plan)
    if cohort != expected:
        raise ValueError("Saved timing cohort changed")
    if not cohort["counts"]["eligible"]:
        raise ValueError(
            "No shared price-complete timing opportunities remain; inspect the retained exclusions"
        )
    if not isinstance(selection, dict) or set(selection) != {
        row["id"] for row in evidence["strategies"]
    }:
        raise ValueError("Choose one registered timing branch for every strategy")
    definitions = {
        row["id"]: {branch["id"]: branch for branch in row["branches"]}
        for row in descriptor["strategies"]
    }
    signals = {row["id"]: row["signals"] for row in cohort["strategies"]}
    rows = []
    for original in evidence["strategies"]:
        branch = definitions[original["id"]].get(selection[original["id"]])
        if branch is None:
            raise ValueError("Choose a registered timing branch")
        cfg = (
            validate_config({**original["config"], **branch["changes"]})
            if branch["changes"]
            else deepcopy(original["config"])
        )
        rows.append(
            {**original, "config": cfg, "signals": deepcopy(signals[original["id"]]), "search": {}}
        )
    return {
        "strategies": rows,
        "snapshot": evidence["snapshot"],
        "capital": evidence["portfolio"]["capital"],
        "diagnostic": {
            "descriptor_id": descriptor["id"],
            "union_id": plan["id"],
            "cohort_id": cohort["id"],
            "selection": dict(selection),
            "automatic_recipe": None,
        },
    }
