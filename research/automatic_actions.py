"""Recorded bounded diagnosis routes; no I/O, profit ranking or relaxed limits."""

from math import isfinite

from research.capital_planning import selected_account_descriptor
from research.report_contract import fingerprint

POLICY = "automatic_actions_v1"
VERSION = "research-automatic-actions-v1"


def enabled(portfolio):
    return portfolio.get("automatic_research", {}).get("action_policy") == POLICY


def descriptor():
    value = {
        "version": VERSION,
        "policy": POLICY,
        "max_accounts": 4,
        "capital_policy_id": selected_account_descriptor()["id"],
        "routes": {
            "whole_share_affordability": "Test the frozen fee-adjusted selected-percentage account estimate.",
            "shared_cash_or_percentage_cap": "Retest the frozen smaller order-size variant, preserving every hard limit.",
            "unavailable_record": "Exclude only the unavailable record, preserve its reason and continue supported records; do not invent prices or signals.",
            "position_or_liquidity_limit": "Preserve the hard cap and retain its affected records as explicit constraints; other independently permitted remedies may continue.",
            "unexplained_limit": "Preserve the evidence and stop this unsupported remedy; do not guess a cause.",
        },
        "selection": "Route by native execution causes only, never profit. Changed account/size variants remain unvalidated until the unchanged performance and chronological gates are rerun.",
        "scope": "Bounded historical capacity diagnosis and retests; performance, cost and chronological validation remain unchanged and are required before any altered operating point is promoted.",
        "stopping": "Stop at complete funding, no remaining permitted remedy, native representation bounds or the frozen three/four-account allowance. Never extend the search.",
    }
    return {**value, "id": fingerprint(value)}


def entry_prices(report, strategies, snapshot=None):
    """Extract observed raw entry prices without inventing absent skipped prices.

    Native filled ledger rows have ``entry_raw_price``. Older filled rows may
    only have the slipped ``entry_price``, whose configured slippage is removed.
    Zero-fill skipped rows currently do not carry either price; their absence is
    explicit. The caller can use frozen scheduled entry bars to supplement these
    observations before freezing the plan, never later outcome-derived prices.
    """
    owners = {row["id"]: row for row in strategies}
    result = {key: [] for key in owners}
    if snapshot is not None:
        from research.connectors.vectorbt_portfolio import _schedule

        interval = snapshot.get("provenance", {}).get("interval")
        if interval not in ("D", "1m"):
            raise ValueError("Selected capacity planning needs daily or minute prices")
        minute = interval == "1m"
        timeline = snapshot.get("timeline" if minute else "sessions", [])
        index = {key: position for position, key in enumerate(timeline)}
    for row in report["ledger"]:
        owner = owners.get(row.get("strategy_id"))
        if owner is None or row.get("status") == "excluded":
            continue
        price = row.get("entry_raw_price")
        if price is None and row.get("entry_price") is not None:
            price = row["entry_price"] / (1 + owner["config"].get("slippage_bps", 0) / 10000)
        if price is None and snapshot is not None:
            signal = {
                "date": row["signal_date"],
                "timestamp": row.get("signal_timestamp"),
                "symbol": row["symbol"],
            }
            position, excluded = _schedule(
                signal, snapshot, owner["config"], timeline, index, minute
            )
            if position >= 0 and not excluded:
                bar = snapshot["bars"].get(row["symbol"], {}).get(timeline[position])
                price = bar.get("open") if bar else None
        if price is None:
            continue
        if type(price) not in (int, float) or not isfinite(price) or price <= 0:
            raise ValueError("Observed native entry price is invalid")
        result[owner["id"]].append(float(price))
    # Only the maximum is used by whole-share planning. Bound retained evidence
    # by strategy count instead of copying the full signal-price sequence.
    return {key: [max(prices)] if prices else [] for key, prices in result.items()}
