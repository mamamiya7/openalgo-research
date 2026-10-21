"""Bounded fill activity from actual native orders; no capacity inference or I/O."""

import math

VERSION = "research-execution-activity-v1"
MAX_ORDERS = 50_000


def build(orders, timeline, capital):
    """Two-sided traded notional / starting capital, not annualised turnover."""
    if (
        not isinstance(orders, list)
        or len(orders) > MAX_ORDERS
        or not timeline
        or isinstance(capital, bool)
        or not isinstance(capital, (int, float))
        or not math.isfinite(capital)
        or capital <= 0
    ):
        raise ValueError("Native activity needs bounded orders and positive starting capital")
    daily, seen = {}, set()
    for order in orders:
        if (
            not isinstance(order, dict)
            or type(order.get("id")) is not int
            or order["id"] in seen
            or type(order.get("idx")) is not int
            or not 0 <= order["idx"] < len(timeline)
            or type(order.get("side")) is not int
            or order["side"] not in (0, 1)
            or any(
                isinstance(order.get(key), bool)
                or not isinstance(order.get(key), (int, float))
                or not math.isfinite(order[key])
                or order[key] <= 0
                for key in ("size", "price")
            )
            or isinstance(order.get("fees"), bool)
            or not isinstance(order.get("fees"), (int, float))
            or not math.isfinite(order["fees"])
            or order["fees"] < 0
            or not math.isfinite(order["size"] * order["price"])
        ):
            raise ValueError("Native activity found an invalid or duplicate filled order")
        seen.add(order["id"])
        day = str(timeline[order["idx"]])[:10]
        row = daily.setdefault(
            day,
            {
                "date": day,
                "buy_notional": 0.0,
                "sell_notional": 0.0,
                "fees": 0.0,
                "buy_fills": 0,
                "sell_fills": 0,
            },
        )
        side = "buy" if order["side"] == 0 else "sell"
        row[f"{side}_notional"] += order["size"] * order["price"]
        row[f"{side}_fills"] += 1
        row["fees"] += order["fees"]
    rows = []
    for day in sorted(daily):
        row = daily[day]
        rows.append(
            {
                **row,
                "total_traded_notional": row["buy_notional"] + row["sell_notional"],
                "total_fills": row["buy_fills"] + row["sell_fills"],
            }
        )
    result = {
        "version": VERSION,
        **{
            key: sum(row[key] for row in rows)
            for key in (
                "buy_notional",
                "sell_notional",
                "total_traded_notional",
                "fees",
                "buy_fills",
                "sell_fills",
                "total_fills",
            )
        },
        "daily": rows,
        "basis": "Actual VectorBT filled size times slipped fill price, excluding fees from notional; both buy and sell sides counted once. Daily rows use the recorded bar's trading date. Ratio uses original account starting capital, is not annualised, and does not establish liquidity or capacity.",
    }
    result["traded_notional_to_starting_capital_pct"] = (
        result["total_traded_notional"] / capital * 100
    )
    return result
