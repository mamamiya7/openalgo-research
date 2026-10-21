"""Frozen local challenges; no prices, scores, resources or final-period access."""

from copy import deepcopy
from decimal import Decimal

from research.automatic_stability import neighbors
from research.report_contract import settings_identity

VERSION = "research-recommendation-checks-v1"
MAX_NEIGHBORS = 4


def policy():
    return {
        "version": VERSION,
        "max_neighbors": MAX_NEIGHBORS,
        "max_additional_accounts": 6,
        "resamples": 512,
        "block_sessions": [5, 10],
        "seed": 0,
        "minimum_development_sessions": 60,
        "minimum_sessions_per_later_window": 10,
        "minimum_later_window_sessions_total": 60,
        "minimum_neighbors": 2,
        "minimum_neighbor_pass_fraction": 0.75,
        "minimum_neighbor_median_return_edge_fraction": 0.5,
        "maximum_drawdown_pct": 25,
        "paired_percentiles": [2.5, 97.5],
        "require_both_lower_ranges_positive": True,
        "maximum_daily_sessions": 4096,
        "maximum_raw_marks": 100000,
        "selection": "Development-only paired daily evidence, all later-window and higher-cost checks, and a prespecified local challenge; otherwise retain original settings",
        "scope": "Conditional resampling describes this selected historical account, not search-adjusted confidence, future probability or a universal optimum",
    }


def challenges(settings, ranges):
    """Up to four deterministic active-axis neighbours, fixed before evaluation."""
    domains = deepcopy(ranges)
    by_id = {row["id"]: row for row in settings}
    for row in domains:
        chosen = by_id[row["id"]]
        cfg, execution = chosen["config"], chosen.get("execution_policy", {})
        for key, domain in list(row["search"].items()):
            if key in ("target_pct", "stop_pct") and execution.get("exit_mode") == "atr":
                del row["search"][key]
            elif key == "trailing_pct" and not cfg.get("trailing_enabled"):
                del row["search"][key]
            elif key in ("hold_sessions", "hold_minutes", "stop_pct", "target_pct", "trailing_pct"):
                value = Decimal(str(cfg[key]))
                low, step = Decimal(str(domain["min"])), Decimal(str(domain["step"]))
                if (value - low) % step:
                    del row["search"][key]
    planned = neighbors(settings, domains)
    rows = list(planned["neighbors"])
    original_count = len(rows)
    # ATR multiples are active protective settings, not the ignored percentage axes.
    for index, row in enumerate(settings):
        execution = row.get("execution_policy", {})
        if execution.get("exit_mode") != "atr":
            continue
        for key, low, high in (("atr_stop_multiple", 1, 3), ("atr_target_multiple", 1, 6)):
            for direction in (-1, 1):
                value = execution[key] + direction
                if low <= value <= high:
                    changed = deepcopy(settings)
                    changed[index]["execution_policy"][key] = value
                    rows.append(
                        {
                            "config_id": settings_identity(changed),
                            "strategy_id": row["id"],
                            "axis": key,
                            "from": execution[key],
                            "to": value,
                            "strategies": changed,
                        }
                    )
    unique = {row["config_id"]: row for row in rows}
    # Balance active protective and holding/trailing axes before applying the cap.
    pending, selected, counts = list(unique.values()), [], {}
    while pending and len(selected) < MAX_NEIGHBORS:
        choice = min(
            enumerate(pending),
            key=lambda item: (
                counts.get(item[1]["axis"], 0),
                counts.get(item[1]["strategy_id"], 0),
                item[0],
            ),
        )[1]
        pending.remove(choice)
        selected.append(choice)
        for key in (choice["axis"], choice["strategy_id"]):
            counts[key] = counts.get(key, 0) + 1
    return {
        "neighbors": selected,
        "available": planned["available"] + len(rows) - original_count,
        "truncated": planned["truncated"] or len(unique) > MAX_NEIGHBORS,
        "policy_version": VERSION,
    }
