"""Deterministic, bounded adjacent-grid challenges; no resources or evaluation.

The proposal is fixed before scores are observed. This tests local fragility,
not every configuration or statistical confidence in future performance.
"""

from copy import deepcopy
from decimal import Decimal

from research.report_contract import settings_identity

AXES = ("hold_sessions", "hold_minutes", "stop_pct", "target_pct", "trailing_pct")
MAX_NEIGHBORS = 8


def neighbors(settings, ranges):
    """Challenge one active axis of one strategy within its frozen search domain.

    Balance strategy, axis and direction coverage before repeating a dimension.
    A subset is explicit; the eight-neighbor cap does not imply full coverage.
    """
    domains = {row["id"]: row["search"] for row in ranges}
    proposed, seen = [], {settings_identity(settings)}
    for direction in (-1, 1):
        for axis in AXES:
            for index, row in enumerate(settings):
                domain = domains.get(row["id"], {}).get(axis)
                if not domain:
                    continue
                value = Decimal(str(row["config"][axis]))
                low, high, step = (Decimal(str(domain[key])) for key in ("min", "max", "step"))
                if (
                    not all(item.is_finite() for item in (value, low, high, step))
                    or step <= 0
                    or low > high
                ):
                    raise ValueError("Stability needs finite ordered search domains")
                if value < low or value > high or (value - low) % step:
                    raise ValueError("Stability settings must lie on their frozen search grid")
                adjacent = value + direction * step
                if not low <= adjacent <= high:
                    continue
                changed = deepcopy(settings)
                changed[index]["config"][axis] = (
                    int(adjacent) if axis.startswith("hold_") else float(adjacent)
                )
                identity = settings_identity(changed)
                if identity in seen:
                    continue
                seen.add(identity)
                proposed.append(
                    {
                        "config_id": identity,
                        "strategy_id": row["id"],
                        "axis": axis,
                        "from": row["config"][axis],
                        "to": changed[index]["config"][axis],
                        "strategies": changed,
                    }
                )
    chosen, strategies_seen, axes_seen, directions_seen = [], {}, {}, {}
    pending = list(enumerate(proposed))
    while pending and len(chosen) < MAX_NEIGHBORS:

        def priority(item):
            ordinal, row = item
            direction = -1 if row["to"] < row["from"] else 1
            return (
                strategies_seen.get(row["strategy_id"], 0),
                axes_seen.get(row["axis"], 0),
                directions_seen.get(direction, 0),
                ordinal,
            )

        selected = min(pending, key=priority)
        pending.remove(selected)
        row = selected[1]
        chosen.append(row)
        direction = -1 if row["to"] < row["from"] else 1
        strategies_seen[row["strategy_id"]] = strategies_seen.get(row["strategy_id"], 0) + 1
        axes_seen[row["axis"]] = axes_seen.get(row["axis"], 0) + 1
        directions_seen[direction] = directions_seen.get(direction, 0) + 1
    return {
        "neighbors": chosen,
        "available": len(proposed),
        "truncated": len(proposed) > MAX_NEIGHBORS,
    }
