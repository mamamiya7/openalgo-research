"""Bounded scanner strategy translation to VectorBT's native portfolio engine.

The callbacks decide orders; VectorBT owns fills, shared cash, positions, fees,
and trade accounting. There is deliberately no broker or storage client here.
"""

import math

from research.data import validate_snapshot
from research.engine import validate_config as validate_scanner_config

ADAPTER_VERSION = "vectorbt-scanner-adapter-v1"
POLICY_VERSION = "vectorbt-daily-next-open-v1"
MAX_MATRIX_CELLS = 500_000
MAX_SIGNALS = 25_000


def validate_config(config=None, signals=None):
    """Check request capabilities without importing optional engine packages."""
    cfg = validate_scanner_config(config)
    if (
        cfg.get("trade_horizon") == "intraday"
        or any(cfg.get(k) is not None for k in ("hold_minutes", "entry_time", "exit_time"))
        or any(s.get("timestamp") for s in signals or [])
    ):
        raise ValueError("VectorBT connector currently supports date-only daily signals")
    if cfg["modes"] != ["Bypass"]:
        raise ValueError("VectorBT connector currently supports the Bypass scanner mode")
    if cfg["entry_priority"] != "csv" or cfg["priority_seed"] != 0:
        raise ValueError("VectorBT connector currently supports CSV entry priority")
    if cfg["max_exposure_pct"] != 100 or cfg["exposure_fill_mode"] != "strict":
        raise ValueError("VectorBT connector requires 100% cash exposure and strict fills")
    if cfg["trailing_enabled"] and cfg["trailing_pct"] <= 0:
        raise ValueError("An enabled trailing stop requires a positive trailing percentage")
    return cfg


def validate(signals, snapshot, config=None):
    cfg = validate_config(config, signals)
    if not signals or len(signals) > MAX_SIGNALS:
        raise ValueError(f"Supply 1-{MAX_SIGNALS} normalized signals")
    if snapshot.get("provenance", {}).get("interval") != "D":
        raise ValueError("VectorBT connector currently requires daily price data")
    sessions = snapshot.get("sessions", [])
    # One column per signal preserves simultaneous/repeated-symbol lot identity.
    # Bound this expansion before importing NumPy or allocating any arrays.
    if len(sessions) * len(signals) > MAX_MATRIX_CELLS:
        raise ValueError("VectorBT connector matrix limit reached; use a smaller signal sample")
    validate_snapshot(snapshot, signals)
    index = {day: i for i, day in enumerate(sessions)}
    for signal in signals:
        signal_i = index.get(signal["date"])
        if signal_i is None:
            continue
        series = snapshot["bars"].get(signal["symbol"], {})
        required = sessions[signal_i + 1 : signal_i + 2 + cfg["hold_sessions"]]
        if any(day not in series for day in required):
            raise ValueError(
                "VectorBT requires complete daily prices through each potential holding window; "
                f"missing data for {signal['symbol']} after {signal['date']}"
            )
    return cfg


def evaluate(signals, snapshot, config=None, progress=None):
    cfg = validate(signals, snapshot, config)
    sessions, bars = snapshot["sessions"], snapshot["bars"]
    if progress:
        progress(0, len(sessions))
    # Optional packages are loaded only inside the calculation worker.
    import numpy as np
    import pandas as pd
    import vectorbt as vbt
    from vectorbt.portfolio import nb
    from vectorbt.portfolio.enums import Direction, NoOrder, OrderStatus, SizeType, TradeStatus

    index = {day: i for i, day in enumerate(sessions)}
    count = len(signals)
    close = np.full((len(sessions), count), np.nan)
    entry_at = np.full(count, -1, dtype=np.int64)
    filled_at = np.full(count, -1, dtype=np.int64)
    fill_price = np.full(count, np.nan)
    high_water = np.full(count, np.nan)
    outcomes = [None] * count
    timings = [None] * count
    rejected = [None] * count
    native_status = [None] * count
    requested_budgets = [None] * count
    order_raw = [None] * count
    exit_raw = [None] * count
    fee, slip = cfg["cost_bps"] / 10000, cfg["slippage_bps"] / 10000
    for col, signal in enumerate(signals):
        signal_i = index.get(signal["date"])
        if signal_i is not None and signal_i + 1 < len(sessions):
            entry_at[col] = signal_i + 1
        for day, bar in bars.get(signal["symbol"], {}).items():
            close[index[day], col] = float(bar["close"])

    def exit_for(col, i, position):
        """Translate this scanner's protective orders using native stop pricing."""
        bar = bars[signals[col]["symbol"]][sessions[i]]
        op, lo, hi = (float(bar[k]) for k in ("open", "low", "high"))
        stop_anchor, stop_pct, outcome = fill_price[col], cfg["stop_pct"] / 100, "stop"
        if cfg["trailing_enabled"]:
            trail_pct = cfg["trailing_pct"] / 100
            if high_water[col] * (1 - trail_pct) > stop_anchor * (1 - stop_pct):
                stop_anchor, stop_pct, outcome = high_water[col], trail_pct, "trailing_stop"
        # Opening gaps precede all within-bar events, including the other stop.
        opening_stop = nb.get_stop_price_nb(position, stop_anchor, stop_pct, op, op, op, True)
        opening_target = nb.get_stop_price_nb(
            position, fill_price[col], cfg["target_pct"] / 100, op, op, op, False
        )
        if not np.isnan(opening_stop):
            return float(opening_stop), outcome, "open"
        if not np.isnan(opening_target):
            return float(opening_target), "target", "open"
        stop = nb.get_stop_price_nb(position, stop_anchor, stop_pct, op, lo, hi, True)
        target = nb.get_stop_price_nb(
            position, fill_price[col], cfg["target_pct"] / 100, op, lo, hi, False
        )
        if not np.isnan(stop):
            return float(stop), outcome, "intraday"
        if not np.isnan(target):
            return float(target), "target", "intraday"
        if i - filled_at[col] >= cfg["hold_sessions"]:
            return float(bar["close"]), "hold", "close"
        return None

    def before_session(c):
        if progress:
            progress(c.i, len(sessions))
        exits = {}
        for col in range(count):
            bar = bars.get(signals[col]["symbol"], {}).get(sessions[c.i])
            if bar:
                c.last_val_price[col] = float(bar["open"])
            if c.last_position[col] > 0:
                exits[col] = exit_for(col, c.i, c.last_position[col])
        # No within-bar/closing proceeds may fund today's opening entries.
        order = sorted(
            range(count),
            key=lambda col: (
                0
                if exits.get(col) and exits[col][2] == "open"
                else 1
                if entry_at[col] == c.i
                else 2,
                col,
            ),
        )
        c.call_seq_now[:] = order
        return exits, [None]

    def order(c, exits, session_budget):
        proposed = exits.get(c.col)
        if c.position_now > 0 and proposed:
            raw, outcome, timing = proposed
            outcomes[c.col], timings[c.col], exit_raw[c.col] = outcome, timing, raw
            return nb.order_nb(
                size=0,
                size_type=SizeType.TargetAmount,
                price=raw,
                direction=Direction.LongOnly,
                fees=fee,
                slippage=slip,
                allow_partial=False,
                log=True,
            )
        if c.i != entry_at[c.col] or c.position_now != 0:
            return NoOrder
        if session_budget[0] is None:
            # VectorBT recalculates group value using opening marks and completed
            # opening exits; freeze this equity budget for all opening entrants.
            session_budget[0] = c.value_now * cfg["order_size_pct"] / 100
        budget = session_budget[0]
        requested_budgets[c.col] = float(budget)
        raw = float(bars[signals[c.col]["symbol"]][sessions[c.i]]["open"])
        quantity = math.floor((budget + 1e-9) / (raw * (1 + slip) * (1 + fee)))
        if budget > c.cash_now + 1e-8:
            rejected[c.col] = "Insufficient opening cash for the selected position budget"
            return NoOrder
        if quantity < 1:
            rejected[c.col] = "Position budget cannot fund one whole share including costs"
            return NoOrder
        order_raw[c.col] = raw
        return nb.order_nb(
            size=quantity,
            price=raw,
            direction=Direction.LongOnly,
            fees=fee,
            slippage=slip,
            size_granularity=1,
            allow_partial=False,
            log=True,
        )

    def after_order(c, exits, session_budget):
        if c.i != entry_at[c.col] or c.position_before != 0:
            return
        native_status[c.col] = int(c.order_result.status_info)
        if c.order_result.status == OrderStatus.Filled:
            filled_at[c.col] = c.i
            fill_price[c.col] = c.order_result.price
            high_water[c.col] = c.order_result.price
        elif not rejected[c.col]:
            rejected[c.col] = "VectorBT did not fill the opening order"

    def after_session(c):
        for col in range(count):
            if c.last_position[col] > 0:
                high_water[col] = max(
                    high_water[col], float(bars[signals[col]["symbol"]][sessions[c.i]]["high"])
                )

    portfolio = vbt.Portfolio.from_order_func(
        pd.DataFrame(close, index=pd.to_datetime(sessions), columns=list(range(count))),
        order,
        init_cash=float(cfg["initial_capital"]),
        cash_sharing=True,
        group_by=True,
        row_wise=True,
        use_numba=False,
        update_value=True,
        pre_segment_func_nb=before_session,
        post_order_func_nb=after_order,
        post_segment_func_nb=after_session,
        max_orders=count * 2,
        max_logs=count * 2,
        freq="1D",
        fillna_close=False,
    )
    if progress:
        progress(len(sessions), len(sessions))
    return _report(
        portfolio,
        signals,
        snapshot,
        cfg,
        outcomes,
        timings,
        rejected,
        native_status,
        requested_budgets,
        order_raw,
        exit_raw,
        vbt.__version__,
        TradeStatus,
    )


def _report(
    pf,
    signals,
    snapshot,
    cfg,
    outcomes,
    timings,
    rejected,
    native_status,
    budgets,
    entry_raw,
    exit_raw,
    engine_version,
    trade_status,
):
    """Normalize native records; never recompute fills or the account ledger."""
    sessions = snapshot["sessions"]
    by_col = {int(row["col"]): row for row in pf.trades.records_arr}
    ledger = []
    realized_by_day = {}
    for col, signal in enumerate(signals):
        row = {
            "symbol": signal["symbol"],
            "signal_date": signal["date"],
            "source_row": signal.get("row"),
            "trigger_modes": ["Bypass"],
            "status": "pending",
            "reason": "Next exchange session is outside the snapshot",
            "entry_date": None,
            "exit_date": None,
            "entry_price": None,
            "exit_price": None,
            "quantity": 0,
            "pnl": None,
            "fees": 0.0,
            "outcome": None,
            "engine_column": col,
        }
        native = by_col.get(col)
        if signal["date"] not in sessions:
            row.update(
                status="excluded", reason="Signal date is outside the recorded session calendar"
            )
        elif native is None and rejected[col]:
            row.update(
                status="skipped",
                reason=rejected[col],
                requested_budget=budgets[col],
                native_status_info=native_status[col],
            )
        elif native is not None:
            row.update(
                entry_date=sessions[int(native["entry_idx"])],
                entry_price=float(native["entry_price"]),
                entry_raw_price=entry_raw[col],
                quantity=int(native["size"]),
                fees=float(native["entry_fees"]),
                requested_budget=budgets[col],
                reason="Position open at snapshot end",
            )
            if native["status"] == trade_status.Closed:
                exit_i = int(native["exit_idx"])
                row.update(
                    status="closed",
                    reason=f"{outcomes[col]}; {timings[col]} exit",
                    exit_date=sessions[exit_i],
                    exit_price=float(native["exit_price"]),
                    exit_raw_price=exit_raw[col],
                    exit_timing=timings[col],
                    pnl=float(native["pnl"]),
                    outcome=outcomes[col],
                    fees=float(native["entry_fees"] + native["exit_fees"]),
                )
                realized_by_day[exit_i] = realized_by_day.get(exit_i, 0.0) + row["pnl"]
        ledger.append(row)
    # Account values and cash are read directly from the external engine.
    values = pf.value().to_numpy().reshape(-1)
    cash = pf.cash().to_numpy().reshape(-1)
    position_counts = (pf.assets().to_numpy() > 0).sum(axis=1)
    initial = float(cfg["initial_capital"])
    peak, realized, curve = initial, initial, []
    for i, day in enumerate(sessions):
        value = float(values[i])
        if not math.isfinite(value) or not math.isfinite(float(cash[i])):
            raise ValueError(
                "VectorBT returned an unvalued account; complete price coverage is required"
            )
        peak = max(peak, value)
        realized += realized_by_day.get(i, 0.0)
        curve.append(
            {
                "date": day,
                "equity": round(value, 6),
                "cash": round(float(cash[i]), 6),
                "realized_equity": round(realized, 6),
                "drawdown_pct": round((peak - value) / peak * 100, 6),
                "open_positions": int(position_counts[i]),
            }
        )
    closed = [r for r in ledger if r["status"] == "closed"]
    wins = sum(max(0, r["pnl"]) for r in closed)
    losses = -sum(min(0, r["pnl"]) for r in closed)
    raw_orders = [
        {key: row[key].item() for key in row.dtype.names} for row in pf.orders.records_arr
    ]
    raw_trades = [
        {key: row[key].item() for key in row.dtype.names} for row in pf.trades.records_arr
    ]
    return {
        "policy_version": POLICY_VERSION,
        "metric_basis": "daily_marked",
        "config": cfg,
        "summary": {
            "initial_capital": initial,
            "final_equity": curve[-1]["equity"],
            "net_return_pct": (float(values[-1]) / initial - 1) * 100,
            "max_drawdown_pct": max(point["drawdown_pct"] for point in curve),
            "realized_equity": round(realized, 6),
            "accepted_trades": sum(r["quantity"] > 0 for r in ledger),
            "closed_trades": len(closed),
            "pending_trades": sum(r["status"] == "pending" and r["quantity"] > 0 for r in ledger),
            "unfunded_pending": sum(
                r["status"] == "pending" and r["quantity"] == 0 for r in ledger
            ),
            "skipped_trades": sum(r["status"] == "skipped" for r in ledger),
            "excluded_signals": sum(r["status"] == "excluded" for r in ledger),
            "win_rate_pct": sum(r["pnl"] > 0 for r in closed) / len(closed) * 100
            if closed
            else None,
            "profit_factor": wins / losses if losses else None,
            "sample_adequacy": "Insufficient sample"
            if len(closed) < 30
            else "Historical sample available",
        },
        "equity_curve": curve,
        "ledger": ledger,
        "coverage": validate_snapshot(snapshot, signals),
        "missing_marks": [],
        "execution": {
            "engine": "vectorbt",
            "engine_version": engine_version,
            "adapter_version": ADAPTER_VERSION,
            "simulation_api": "Portfolio.from_order_func",
            "shared_cash": True,
            "signal_columns": len(signals),
            "matrix_cells": len(signals) * len(sessions),
        },
        "engine_records": {"orders": raw_orders, "trades": raw_trades},
        "limits": [
            "VectorBT owns fills, fees, shared cash, positions and trade accounting; the connector translates the scanner's orders.",
            "One order per signal lot per daily bar: protective exits start on the session after entry; entry-day stop/target touches do not close the new position.",
            "Opening gap exits precede opening entries; within-bar and closing proceeds cannot fund earlier entries. CSV priority and whole-share equity budgets apply.",
            "Separate columns retain repeated-symbol lots. Stops precede targets inside ambiguous daily bars; trailing changes use completed bars.",
            "Stops and targets use slipped entry prices without rounding thresholds to two decimals; fees and slippage apply per side.",
            "Complete prices are required through every potential holding window. Unrelated flat periods need no invented execution prices; open positions are not forcibly liquidated.",
            "No margin, short selling, derivatives, corporate-action cashflows or capacity model. A fixed sample does not establish robustness.",
        ],
    }
