"""Freeze required exchange metadata from OpenAlgo's existing master contract."""

from decimal import Decimal


def native_instruments(symbols, *, allow_missing=False):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from database.symbol import SymToken, engine

    result = {}
    with Session(engine) as session:
        for symbol in sorted(set(symbols)):
            rows = session.scalars(
                select(SymToken)
                .where(SymToken.exchange == "NSE", SymToken.symbol == symbol)
                .limit(2)
            ).all()
            if not rows and allow_missing:
                continue
            if len(rows) != 1 or rows[0].lotsize != 1 or not rows[0].tick_size:
                raise ValueError(f"OpenAlgo needs a current NSE master contract for {symbol}")
            tick = Decimal(str(rows[0].tick_size)).normalize()
            if not tick.is_finite() or tick <= 0:
                raise ValueError(f"OpenAlgo has no valid tick size for {symbol}")
            result[symbol] = {
                "currency": "INR",
                "lot_size": 1,
                "tick_size": str(tick),
                "price_precision": max(0, -tick.as_tuple().exponent),
                "source": "OpenAlgo NSE master contract",
            }
    return result


def prepare_nautilus_instruments(evidence):
    """Freeze available native specifications before trial selection, with exact gaps."""
    from research.connectors.nautilus_portfolio import (
        INSTRUMENT_ELIGIBILITY_VERSION,
        incompatible_price_reason,
        validate_instrument,
    )
    from research.connectors.vectorbt_portfolio import _deadline, _schedule
    from research.portfolio import requirement_strategies
    from research.portfolio_coverage import prepare

    snapshot = evidence["snapshot"]
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    configs = {
        row["id"]: row["config"]
        for row in requirement_strategies(evidence["portfolio"], evidence["strategies"])
    }
    scheduled = {
        (row["id"], i): entry
        for row in evidence["strategies"]
        for i, signal in enumerate(row["signals"])
        if (
            entry := _schedule(signal, snapshot, configs[row["id"]], timeline, index, minute)[0]
        )
        >= 0
    }
    symbols = {
        signal["symbol"]
        for row in evidence["strategies"]
        for i, signal in enumerate(row["signals"])
        if (row["id"], i) in scheduled
    }
    instruments = native_instruments(symbols, allow_missing=True)
    for symbol, item in instruments.items():
        validate_instrument(symbol, item)
    missing = symbols - set(instruments)
    if scheduled and not instruments:
        raise ValueError(
            "OpenAlgo has no trading specifications for these symbols. Refresh master contracts before using NautilusTrader."
        )
    strategies, exclusions = [], []
    for row in evidence["strategies"]:
        selected = []
        for i, original in enumerate(row["signals"]):
            signal = dict(original)
            entry = scheduled.get((row["id"], i))
            reason = None
            if entry is not None:
                symbol = signal["symbol"]
                if symbol in missing:
                    reason = "Trading specifications unavailable in OpenAlgo's NSE master contract"
                else:
                    last = _deadline(entry, snapshot, configs[row["id"]], timeline, minute)
                    for key in timeline[entry : last + 1]:
                        bar = snapshot["bars"].get(symbol, {}).get(key)
                        if bar is not None:
                            reason = incompatible_price_reason(symbol, key, bar, instruments[symbol])
                            if reason:
                                break
                if reason:
                    signal["research_exclusion"] = reason
                    exclusions.append(
                        {
                            "strategy_id": row["id"],
                            "symbol": symbol,
                            "date": signal["date"],
                            "source_row": signal.get("row"),
                            "reason": reason,
                        }
                    )
            selected.append(signal)
        strategies.append({**row, "signals": selected})
    if scheduled and len(exclusions) == len(scheduled):
        raise ValueError(
            "No signal windows match the supplied trading specifications. "
            "Use VectorBT for these prices or supply compatible instrument metadata."
        )
    return prepare(
        {
            **evidence,
            "strategies": strategies,
            "snapshot": {
                **snapshot,
                "instruments": instruments,
                "instrument_gaps": sorted(missing),
                "instrument_eligibility": {
                    "policy_version": INSTRUMENT_ELIGIBILITY_VERSION,
                    "metadata_basis": "OpenAlgo current NSE master contract",
                    "window_basis": "maximum_requested_holding_period",
                    "excluded_signals": len(exclusions),
                    "exclusions": exclusions,
                },
            },
        }
    )
