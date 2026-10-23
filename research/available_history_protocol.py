"""Outcome-free research planning for the signal history actually supplied.

This profile never shortens a trading deadline or invents an untouched period.
It records why chronological validation was unavailable, then allocates the same
search programme to expanding historical prefixes and the full supplied span.
"""

from copy import deepcopy

VERSION = "research-available-history-v1"
POLICY = "available_history"


def enabled(portfolio):
    automatic = portfolio.get("automatic_research", {})
    return (
        automatic.get("version") == "automatic-hypotheses-v16"
        and automatic.get("history_policy") == POLICY
    )


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
    if sufficient:
        return metadata, deepcopy(attempted["periods"])
    metadata["chronology_reason"] = fallback_reason or attempted["reason"]
    if "minimum_session_lower_bound" in attempted:
        metadata["minimum_session_lower_bound"] = attempted["minimum_session_lower_bound"]
    initial = min(count, max(holding["min_window_sessions"], holding["max_sessions"] + 21))
    prefixes = {
        name: initial + (count - initial) * index // 3
        for index, name in enumerate(("fit1", "fit2", "fit3", "search"))
    }
    metadata["fit_prefixes"] = prefixes
    periods = {
        name: {"from": sessions[0], "to": sessions[length - 1], "sessions": length}
        for name, length in prefixes.items()
    }
    return metadata, periods
