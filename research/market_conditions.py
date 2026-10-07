"""Descriptive entry-condition cohorts over saved native fills, never a new backtest."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import mean

from research.benchmark import report_dates
from research.market_series import fingerprint

VERSION = "research-market-conditions-v1"
IST = timezone(timedelta(hours=5, minutes=30))
LABELS = {
    "trend": {
        "up": "Rising trend",
        "down": "Falling trend",
        "range": "Range-bound",
        "unknown": "Unclassified",
    },
    "volatility": {
        "low": "Low volatility",
        "normal": "Normal volatility",
        "high": "High volatility",
        "unknown": "Unclassified",
    },
}


def _entry_day(trade, calendar, first, last, *, minute=False):
    day = trade.get("entry_date")
    if not isinstance(day, str) or not first <= day <= last:
        return None
    hours = calendar.get("session_hours", {}).get(day)
    if not hours:
        return None
    stamp = trade.get("entry_timestamp")
    if minute and stamp is None:
        return None
    if stamp is not None:
        try:
            entered = datetime.fromisoformat(stamp)
            opening = datetime.fromisoformat(f"{day}T{hours['open']}:00+05:30")
            closing = datetime.fromisoformat(f"{day}T{hours['close']}:00+05:30")
            if entered.tzinfo is None or not opening <= entered <= closing:
                return None
        except (TypeError, ValueError):
            return None
    return day


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value)


def _cohorts(groups):
    rows, candidates = [], []
    for (strategy, name, dimension, regime), trades in sorted(groups.items()):
        dates = {row["entry_date"] for row in trades if row["entry_date"]}
        returns = [row["net_return_pct"] for row in trades if row["net_return_pct"] is not None]
        valid_dates = {
            row["entry_date"]
            for row in trades
            if row["entry_date"] and row["net_return_pct"] is not None
        }
        enough = regime != "unknown" and len(returns) >= 30 and len(valid_dates) >= 20
        label = LABELS[dimension][regime]
        row = {
            "strategy_id": strategy,
            "strategy_name": name,
            "dimension": dimension,
            "regime": regime,
            "label": label,
            "closed_trades": len(trades),
            "entry_sessions": len(dates),
            "average_net_return_pct": mean(returns) if returns else None,
            "win_rate_pct": 100 * sum(t["pnl"] > 0 for t in trades) / len(trades),
            "net_pnl": sum(t["pnl"] for t in trades),
            "evidence": "descriptive" if enough else "limited",
        }
        rows.append(row)
        if enough:
            daily = defaultdict(list)
            for trade in trades:
                if trade["net_return_pct"] is not None:
                    daily[trade["entry_date"]].append(trade["net_return_pct"])
            values = [mean(daily[day]) for day in sorted(daily)]
            half = len(values) // 2
            # Entry dates, not correlated stocks on a single scanner date, are
            # the units in this descriptive stability screen. No p-value claim.
            if len(values) >= 20 and min(mean(values[:half]), mean(values[half:])) > 0:
                candidates.append((mean(values), row))
    supported = [
        (score, row)
        for score, row in candidates
        if any(
            other["strategy_id"] == row["strategy_id"]
            and other["dimension"] == row["dimension"]
            and other["regime"] != row["regime"]
            and other["evidence"] == "descriptive"
            and other["average_net_return_pct"] < row["average_net_return_pct"]
            for other in rows
        )
    ]
    if supported:
        _, row = max(
            supported,
            key=lambda item: (
                item[0],
                item[1]["strategy_id"],
                item[1]["dimension"],
                item[1]["regime"],
            ),
        )
        finding = {
            "status": "observed",
            "text": f"{row['strategy_name']} showed stronger average trade returns in {row['label'].lower()} on this saved sample.",
            "next_step": "Treat this as a research lead. A separate filtered backtest and untouched later-period check are needed before changing the strategy.",
        }
    else:
        finding = {
            "status": "insufficient",
            "text": "Not enough evidence to recommend a market-condition filter.",
            "next_step": "Keep the existing strategy as the comparison. More distinct entry dates and a separate later-period test are needed.",
        }
    return rows, finding


def build_conditions(report, evidence, calendar):
    from research.regimes import classify

    first, last = report_dates(report)
    dates = [day for day in calendar["sessions"] if first <= day <= last]
    if not dates:
        raise ValueError("This report has no recorded market sessions")
    decisions = [f"{day}T{calendar['session_hours'][day]['open']}:00+05:30" for day in dates]
    classified = classify(evidence, decisions)
    timeline = [
        {"date": day, **row} for day, row in zip(dates, classified["timeline"], strict=True)
    ]
    by_day = {row["date"]: row for row in timeline}
    groups, retained = defaultdict(list), []
    ledger = report.get("ledger", [])
    if len(ledger) > 25000:
        raise ValueError("Market-condition review is limited to 25000 saved trades")
    closed = [
        (index, trade)
        for index, trade in enumerate(ledger)
        if trade.get("status") == "closed" and _number(trade.get("pnl"))
    ]
    classified_count = 0
    for index, trade in closed:
        day = _entry_day(
            trade, calendar, first, last, minute=report.get("execution", {}).get("interval") == "1m"
        )
        context = by_day.get(day, {})
        available = context.get("status") == "available"
        both_known = available and all(
            context.get(dimension, "unknown") != "unknown" for dimension in LABELS
        )
        classified_count += int(both_known)
        price, quantity = trade.get("entry_price"), trade.get("quantity")
        invested = price * quantity if _number(price) and _number(quantity) else 0
        value = {
            "entry_date": day,
            "pnl": trade["pnl"],
            "net_return_pct": trade["pnl"] / invested * 100
            if invested > 0 and isfinite(invested)
            else None,
        }
        strategy = trade.get("strategy_id") or "portfolio"
        name = trade.get("strategy_name") or "Portfolio"
        if len({key[0] for key in groups} | {strategy}) > 8:
            raise ValueError("Market-condition review supports up to eight saved strategies")
        for dimension in LABELS:
            regime = context.get(dimension, "unknown") if available else "unknown"
            groups[strategy, name, dimension, regime].append(value)
        retained.append(
            {
                "ledger_index": index,
                "trade_id": fingerprint(trade),
                "entry_date": day,
                "condition_input_id": context.get("input_id"),
                "classified": both_known,
            }
        )
    cohorts, finding = _cohorts(groups)
    count = sum(
        row["status"] == "available" and all(row[dimension] != "unknown" for dimension in LABELS)
        for row in timeline
    )
    usable = any(
        row["status"] == "available" and any(row[dimension] != "unknown" for dimension in LABELS)
        for row in timeline
    )
    payload = {
        "version": VERSION,
        "status": "unavailable"
        if not usable
        else "available"
        if count == len(timeline) and classified_count == len(closed)
        else "partial",
        "descriptor": evidence["descriptor"],
        "dates": {"from": first, "to": last},
        "coverage": {
            "total_sessions": len(timeline),
            "classified_sessions": count,
            "closed_trades": len(closed),
            "classified_trades": classified_count,
            "unclassified_trades": len(closed) - classified_count,
        },
        "recipe": classified["recipe"],
        "timeline": timeline,
        "cohorts": cohorts,
        "finding": finding,
        "trade_assignments": retained,
        "basis": [
            "Historical Nifty 50 price conditions, not a live regime or a prediction.",
            "Classified totals require both trend and volatility to be known. A known dimension is still shown when the other is uncertain.",
            "Each trade uses only completed index closes strictly before its recorded session opening. Intraday entries must be within the recorded session.",
            "Trend and volatility are separate; range-bound does not establish mean reversion, and elevated stress is not a crisis forecast.",
            "Trade P&L includes the saved engine costs. Average net trade return is P&L divided by entry price times quantity; it is not a filtered portfolio return.",
            "Cohorts describe existing fills. Removing signals changes shared cash, capacity and reinvestment and requires a new native backtest.",
            "Descriptive comparison requires 30 trades and 20 distinct entry dates per compared cohort. Positive halves are a screening rule, not proof or statistical confidence.",
            "No strategy ranking, position size, orders or kill switch is changed.",
            *classified["basis"],
            *classified["limitations"],
        ],
    }
    return {**payload, "id": fingerprint(payload)}
