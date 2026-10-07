"""Freeze a consistent, price-complete signal cohort before any trial is scored.

Never fabricate a missing candle or condition eligibility on a trial's outcome.
The original CSV and every normalized row remain in evidence and the ledger.
"""

from research.portfolio import requirement_strategies

POLICY_VERSION = "portfolio-fixed-price-cohort-v1"


def prepare(evidence):
    from research.connectors.vectorbt_portfolio import _deadline, _schedule
    from research.data import validate_snapshot

    snapshot = dict(evidence["snapshot"])
    snapshot["coverage"] = validate_snapshot(snapshot, evidence["signals"])
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    requirements = {
        row["id"]: row
        for row in requirement_strategies(evidence["portfolio"], evidence["strategies"])
    }
    strategies, excluded, eligible = [], [], 0
    for row in evidence["strategies"]:
        signals = []
        cfg = requirements[row["id"]]["config"]
        for signal in row["signals"]:
            signal = dict(signal)
            entry, reason = _schedule(signal, snapshot, cfg, timeline, index, minute)
            if entry >= 0:
                deadline = _deadline(entry, snapshot, cfg, timeline, minute)
                series = snapshot["bars"].get(signal["symbol"], {})
                missing = next(
                    (key for key in timeline[entry : deadline + 1] if key not in series), None
                )
                if missing:
                    reason = f"Broker price unavailable: {signal['symbol']} at {missing}"
                else:
                    eligible += 1
            if reason:
                signal["research_exclusion"] = reason
                excluded.append(
                    {
                        "strategy_id": row["id"],
                        "symbol": signal["symbol"],
                        "date": signal["date"],
                        "reason": reason,
                    }
                )
            signals.append(signal)
        strategies.append({**row, "signals": signals})
    if not eligible:
        raise ValueError(
            "No signals have complete broker prices for this holding period. Try an earlier date range or shorter holding period."
        )
    return {
        **evidence,
        "snapshot": snapshot,
        "strategies": strategies,
        "signal_coverage": {
            "policy_version": POLICY_VERSION,
            "eligible_signals": eligible,
            "excluded_signals": len(excluded),
            "pending_signals": len(evidence["signals"]) - eligible - len(excluded),
            "exclusions": excluded,
        },
    }
