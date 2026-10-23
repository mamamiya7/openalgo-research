"""Outcome-free research planning for the signal history actually supplied.

This profile never shortens a trading deadline or invents an untouched period.
It records why chronological validation was unavailable, then allocates the same
search programme to expanding historical prefixes and the full supplied span.
"""

from copy import deepcopy

VERSION = "research-available-history-v1"
DESIGN_VERSION = "research-available-history-v2"
DESIGN_POLICY = "automatic_design_v1"
POLICY = "available_history"


def enabled(portfolio):
    automatic = portfolio.get("automatic_research", {})
    return (
        automatic.get("version") == "automatic-hypotheses-v16"
        and automatic.get("history_policy") == POLICY
    )


def entry_bins(dates):
    """Partition actual opportunity entry dates, without reading any outcomes.

    Empty bins are omitted for fewer than three dates. Each recorded date belongs
    to exactly one bin; calendar gaps and the censored holding tail add no dates.
    """
    dates = sorted(set(dates))
    if len(dates) > 3000:
        raise ValueError("Available-history entry calendar limit reached")
    width, remainder = divmod(len(dates), 3)
    result, start = [], 0
    for index, name in enumerate(("early", "middle", "late")):
        stop = start + width + (index < remainder)
        if stop > start:
            result.append(
                {
                    "name": name,
                    "from": dates[start],
                    "to": dates[stop - 1],
                    "sessions": stop - start,
                }
            )
        start = stop
    return result


def opportunity_calendar(evidence):
    """Return common complete-horizon scheduled entry dates, never fill dates.

    The caller may pass one already prepared period. Maximum permitted holding
    and every registered timing branch remain authoritative. Only scheduling,
    frozen admission and calendar boundaries are read, never OHLC or returns.
    """
    from research.automatic_protocol import _complete_deadline
    from research.connectors.vectorbt_portfolio import _deadline, _schedule
    from research.portfolio import requirement_strategies

    snapshot = evidence["snapshot"]
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    if not timeline:
        return []
    index = {key: position for position, key in enumerate(timeline)}
    requirements = {
        row["id"]: row["config"]
        for row in requirement_strategies(evidence["portfolio"], evidence["strategies"])
    }
    union = evidence.get("timing_union")
    cohort = evidence.get("timing_cohort")
    timing = None
    timing_exclusions = set()
    if union is not None and cohort is not None:
        sources = {row["id"]: row["signals"] for row in cohort["strategies"]}
        timing_exclusions = {
            (
                strategy_id,
                tuple(signal.get(field) for field in ("symbol", "date", "timestamp", "row")),
            )
            for strategy_id, signals in sources.items()
            for signal in signals
            if signal.get("research_exclusion")
        }
        timing = {
            (
                item["strategy_id"],
                tuple(
                    sources[item["strategy_id"]][item["signal_index"]].get(field)
                    for field in ("symbol", "date", "timestamp", "row")
                ),
            ): item["windows"]
            for item in union["opportunities"]
        }
    dates = set()
    for row in evidence["strategies"]:
        cfg = requirements[row["id"]]
        for signal in row["signals"]:
            if signal.get("research_exclusion"):
                continue
            if timing is not None:
                identity = tuple(
                    signal.get(field) for field in ("symbol", "date", "timestamp", "row")
                )
                if (row["id"], identity) in timing_exclusions:
                    continue
                branches = [
                    branch
                    for branch in timing[row["id"], identity].values()
                    if branch["state"] == "scheduled"
                ]
                if branches and all(
                    branch["entry"] in index and branch["deadline"] in index for branch in branches
                ):
                    dates.update(branch["entry"][:10] for branch in branches)
                continue
            entry, reason = _schedule(signal, snapshot, cfg, timeline, index, minute)
            if reason or entry < 0:
                continue
            deadline = _deadline(entry, snapshot, cfg, timeline, minute)
            if _complete_deadline(entry, deadline, snapshot, cfg, timeline, minute):
                dates.add(timeline[entry][:10])
    if len(dates) > 3000:
        raise ValueError("Available-history entry calendar limit reached")
    return sorted(dates)


def plan(evidence, attempted, *, fallback_reason=None):
    """Return bounded recorded availability and periods, without reading scores."""
    signals, snapshot = evidence["signals"], evidence["snapshot"]
    first, last = min(row["date"] for row in signals), max(row["date"] for row in signals)
    sessions = [day for day in snapshot["sessions"] if first <= day <= last]
    count = len(sessions)
    if not sessions or count > 3000:
        raise ValueError("Available-history research needs a bounded recorded calendar")
    sufficient = attempted["status"] == "ready" and fallback_reason is None
    holding = deepcopy(attempted["holding"])
    metadata = {
        "version": VERSION,
        "mode": "historical_only" if sufficient else "exploratory_only",
        "session_count": count,
        "holding": holding,
        "independently_validated": False,
        "reason_code": "recorded_history"
        if sufficient
        else attempted.get("reason_code", "short_history"),
        "reason": (
            "Later-period checks use the supplied history. Reusing this dataset does not create independent evidence."
            if sufficient
            else "The supplied history cannot support all separate later-period checks. Search uses expanding historical prefixes and the full available span."
        ),
    }
    designed = (
        evidence["portfolio"].get("automatic_research", {}).get("design_policy") == DESIGN_POLICY
    )
    if designed:
        bounded_snapshot = {
            **snapshot,
            "sessions": sessions,
        }
        if snapshot["provenance"]["interval"] == "1m":
            bounded_snapshot["timeline"] = [
                key for key in snapshot["timeline"] if first <= key[:10] <= last
            ]
        dates = opportunity_calendar({**evidence, "snapshot": bounded_snapshot})
        metadata.update(
            version=DESIGN_VERSION,
            design_policy=DESIGN_POLICY,
            usable_entry_session_count=len(dates),
            usable_entry_dates=dates,
            entry_date_bins=entry_bins(dates),
            temporal_evidence="overlapping_historical_fits",
            temporal_policy=(
                "Bins partition actual common complete-horizon entry opportunities before scores "
                "or candidate filters. Overlapping training fits are historical evidence, not "
                "independent validation."
            ),
        )
    if sufficient:
        return metadata, deepcopy(attempted["periods"])
    metadata["chronology_reason"] = fallback_reason or attempted["reason"]
    if "minimum_session_lower_bound" in attempted:
        metadata["minimum_session_lower_bound"] = attempted["minimum_session_lower_bound"]
    initial = min(
        count,
        max(holding["min_window_sessions"], holding["max_sessions"] + (60 if designed else 21)),
    )
    prefixes = {
        name: initial + (count - initial) * index // 3
        for index, name in enumerate(("fit1", "fit2", "fit3", "search"))
    }
    if designed:
        metadata["prefix_evidence"] = (
            "expanding_prefixes"
            if len(set(prefixes.values())) == 4
            else "limited_overlapping_prefixes"
        )
    metadata["fit_prefixes"] = prefixes
    periods = {
        name: {"from": sessions[0], "to": sessions[length - 1], "sessions": length}
        for name, length in prefixes.items()
    }
    return metadata, periods
