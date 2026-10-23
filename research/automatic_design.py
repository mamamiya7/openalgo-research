"""Recorded historical admission checks; never an independent validation claim.

Cash use is a diagnostic, not an objective: idle cash can be required by the
fixed risk limits or the absence of eligible entries. Calendar bins are frozen
from the common opportunity set before gates or candidate outcomes are read.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from math import isfinite

from research.report_contract import fingerprint

POLICY = "automatic_design_v1"
VERSION = "research-automatic-design-v1"


def enabled(portfolio):
    automatic = portfolio.get("automatic_research", {})
    return (
        automatic.get("version") == "automatic-hypotheses-v16"
        and automatic.get("history_policy") == "available_history"
        and automatic.get("design_policy") == POLICY
    )


def descriptor():
    value = {
        "version": VERSION,
        "policy": POLICY,
        "minimum_closed_trades": 30,
        "minimum_entry_dates": 10,
        "calendar_bins": 3,
        "minimum_active_bins": 2,
        "minimum_dates_per_active_bin": 3,
        "maximum_bin_trade_fraction": 0.7,
        "maximum_symbol_positive_profit_fraction": 0.5,
        "scope": "Historical training admission only; overlapping trades and dates are not independent samples. Separate later-period checks retain their recorded validation rules.",
        "capital": "Original order size is a reference. Automatic sizing may use more within unchanged strategy allocation and account risk limits; full investment is never required.",
    }
    return {**value, "id": fingerprint(value)}


def _number(value, name):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"Automatic design needs finite {name}")
    return value


def assess(report, bins, policy, *, opportunity_dates):
    """Read the actual native ledger once; retain a compact, replayable receipt."""
    if policy != descriptor():
        raise ValueError("The recorded automatic design checks changed")
    from research.available_history_protocol import entry_bins

    if not isinstance(opportunity_dates, list) or len(opportunity_dates) > 3000:
        raise ValueError("Automatic design needs a bounded complete-entry calendar")
    try:
        if (
            any(
                not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                for day in opportunity_dates
            )
            or opportunity_dates != sorted(set(opportunity_dates))
            or bins != entry_bins(opportunity_dates)
        ):
            raise ValueError("The automatic design entry calendar or bins changed")
    except (TypeError, ValueError) as error:
        raise ValueError("The automatic design entry calendar or bins changed") from error
    allowed = set(opportunity_dates)
    closed_count = _number(report["summary"]["closed_trades"], "closed-trade count")
    if closed_count < 0 or int(closed_count) != closed_count:
        raise ValueError("Automatic design needs a nonnegative integer closed-trade count")
    closed = [row for row in report["ledger"] if row.get("status") == "closed"]
    dates, profits, counts = set(), defaultdict(float), [Counter() for _ in bins]
    for row in closed:
        day = str(row.get("entry_timestamp") or row.get("entry_date") or "")[:10]
        symbol = row.get("symbol")
        if len(day) != 10 or not isinstance(symbol, str) or not symbol:
            raise ValueError("Automatic design needs recorded native entry dates and symbols")
        matches = [i for i, item in enumerate(bins) if item["from"] <= day <= item["to"]]
        if day not in allowed or len(matches) != 1:
            raise ValueError("A native entry lies outside the frozen complete-entry calendar")
        counts[matches[0]][day] += 1
        dates.add(day)
        profits[symbol] += _number(row["pnl"], "closed-trade profit")
    if closed_count != len(closed):
        raise ValueError("Automatic design trade counts disagree with the native ledger")
    positive = {name: pnl for name, pnl in profits.items() if pnl > 0}
    gross_positive = sum(positive.values())
    largest = max(positive.values(), default=0) / gross_positive if gross_positive else None
    bin_facts = [
        {**deepcopy(item), "closed_trades": sum(count.values()), "entry_dates": len(count)}
        for item, count in zip(bins, counts, strict=True)
    ]
    active = sum(len(count) >= policy["minimum_dates_per_active_bin"] for count in counts)
    largest_bin = (
        max((sum(count.values()) for count in counts), default=0) / len(closed) if closed else 0
    )
    exposures = []
    for point in report.get("equity_curve", []):
        equity, cash = _number(point["equity"], "equity"), _number(point["cash"], "cash")
        if equity > 0:
            exposures.append(100 * (equity - cash) / equity)
    reasons = []
    if len(closed) < policy["minimum_closed_trades"]:
        reasons.append("Fewer than 30 closed trades: native sample adequacy is insufficient")
    if len(dates) < policy["minimum_entry_dates"]:
        reasons.append("Fewer than 10 distinct entry dates")
    if len(bins) != 3 or active < policy["minimum_active_bins"]:
        reasons.append(
            "Trades do not cover at least two complete-entry calendar thirds with three entry dates each"
        )
    if largest_bin > policy["maximum_bin_trade_fraction"]:
        reasons.append("More than 70% of trades fall in one complete-entry calendar third")
    if largest is None or largest > policy["maximum_symbol_positive_profit_fraction"]:
        reasons.append("Positive profit is absent or more than 50% comes from one stock")
    facts = {
        "version": VERSION,
        "policy_id": policy["id"],
        "calendar_id": fingerprint({"opportunity_dates": opportunity_dates, "bins": bins}),
        "support_ledger_id": fingerprint(
            [
                {
                    key: row[key]
                    for key in (
                        "strategy_id",
                        "symbol",
                        "entry_date",
                        "entry_timestamp",
                        "exit_date",
                        "exit_timestamp",
                        "quantity",
                        "pnl",
                    )
                    if key in row
                }
                for row in closed
            ]
        ),
        "qualified": not reasons,
        "reasons": reasons,
        "closed_trades": len(closed),
        "entry_dates": len(dates),
        "calendar_bins": bin_facts,
        "active_bins": active,
        "largest_bin_trade_fraction": largest_bin,
        "largest_symbol_positive_profit_fraction": largest,
        "capital_use": {
            "mean_invested_pct": sum(exposures) / len(exposures) if exposures else None,
            "maximum_invested_pct": max(exposures) if exposures else None,
            "idle_cash_is_failure": False,
        },
        "independently_validated": False,
    }
    return {**facts, "id": fingerprint(facts)}


def reasons(row, policy):
    """Fail closed if a candidate lacks the receipt frozen into its recipe."""
    receipt = row.get("automatic_design")
    if (
        policy != descriptor()
        or not isinstance(receipt, dict)
        or receipt.get("version") != VERSION
        or receipt.get("policy_id") != policy["id"]
        or not isinstance(receipt.get("calendar_id"), str)
        or len(receipt["calendar_id"]) != 64
        or not isinstance(receipt.get("support_ledger_id"), str)
        or len(receipt["support_ledger_id"]) != 64
        or receipt.get("id")
        != fingerprint({key: value for key, value in receipt.items() if key != "id"})
        or type(receipt.get("qualified")) is not bool
        or not isinstance(receipt.get("reasons"), list)
        or any(not isinstance(reason, str) or not reason for reason in receipt["reasons"])
        or receipt["qualified"] != (not receipt["reasons"])
        or receipt.get("closed_trades") != row["summary"]["closed_trades"]
        or receipt.get("entry_dates") != row["entry_dates"]
    ):
        raise ValueError("The recorded automatic design evidence changed")
    return list(receipt["reasons"])
