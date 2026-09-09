"""Joint scanner strategies translated into one native VectorBT cash account.

Strategy callbacks schedule orders, never account for fills. All state is scoped
to one bounded worker evaluation; no clients, threads, files or global caches.
"""

import math
from datetime import timedelta

from research.data import validate_snapshot
from research.engine import validate_config as validate_scanner_config
from research.intraday import bounds, entry_open, stamp

ADAPTER_VERSION = "vectorbt-portfolio-adapter-v1"
POLICY_VERSION = "vectorbt-joint-causal-bars-v1"
MAX_MATRIX_CELLS = 500_000
MAX_SIGNALS = 25_000
MAX_STRATEGIES = 20


def validate_config(config=None):
    """Capabilities available to the request layer without importing VectorBT."""
    cfg = validate_scanner_config(config)
    if cfg["modes"] != ["Bypass"]:
        raise ValueError("Portfolio VectorBT currently supports the Bypass scanner mode")
    if cfg["entry_priority"] != "csv" or cfg["priority_seed"] != 0:
        raise ValueError("Portfolio VectorBT requires strategy order then CSV order")
    if cfg["max_exposure_pct"] != 100 or cfg["exposure_fill_mode"] != "strict":
        raise ValueError("Portfolio VectorBT requires cash exposure and strict fills")
    if cfg["trailing_enabled"] and cfg["trailing_pct"] <= 0:
        raise ValueError("An enabled trailing stop requires a positive trailing percentage")
    return cfg


def _schedule(signal, snapshot, cfg, timeline, index, minute):
    if signal.get("research_exclusion"):
        return -1, signal["research_exclusion"]
    if signal["date"] not in snapshot["sessions"]:
        return -1, "Signal date is outside the recorded session calendar"
    if minute:
        intended = entry_open(signal, snapshot, cfg)
        if intended is None:
            return -1, None
        if cfg.get("exit_time") and intended.strftime("%H:%M") >= cfg["exit_time"]:
            return -1, "Eligible entry is at or after the configured exit time"
        return index.get(intended.isoformat(), -1), None
    signal_i = index[signal["date"]]
    return (signal_i + 1 if signal_i + 1 < len(timeline) else -1), None


def _deadline(entry, snapshot, cfg, timeline, minute):
    """Last potentially held bar, independent of future OHLC values."""
    if not minute:
        return min(entry + cfg["hold_sessions"], len(timeline) - 1)
    entered = stamp(timeline[entry])
    day_index = {day: i for i, day in enumerate(snapshot["sessions"])}
    for i in range(entry, len(timeline)):
        now = stamp(timeline[i])
        if cfg.get("hold_minutes") is not None and now >= entered + timedelta(
            minutes=cfg["hold_minutes"]
        ):
            return i
        if cfg.get("exit_time") and now.strftime("%H:%M") >= cfg["exit_time"]:
            return i
        if now + timedelta(minutes=1) == bounds(snapshot, timeline[i][:10])[1] and (
            cfg.get("trade_horizon") == "intraday"
            or day_index[timeline[i][:10]] - day_index[timeline[entry][:10]] >= cfg["hold_sessions"]
        ):
            return i
    return len(timeline) - 1


def validate(strategies, snapshot, capital):
    """Validate before optional imports or matrix allocation; retain lot identity."""
    if (
        isinstance(capital, bool)
        or not isinstance(capital, (int, float))
        or not math.isfinite(capital)
        or not 1 <= capital <= 1e9
    ):
        raise ValueError("Portfolio capital must be between 1 and 1000000000")
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= MAX_STRATEGIES:
        raise ValueError(f"Supply 1-{MAX_STRATEGIES} strategies")
    normalized, ids, lots = [], set(), []
    for strategy in strategies:
        identity = strategy.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 100 or identity in ids:
            raise ValueError("Each strategy needs a unique bounded id")
        ids.add(identity)
        name = strategy.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError("Each strategy needs a name of 1-160 characters")
        allocation = strategy.get("allocation_pct")
        if (
            isinstance(allocation, bool)
            or not isinstance(allocation, (int, float))
            or not math.isfinite(allocation)
            or not 0 <= allocation <= 100
        ):
            raise ValueError("Strategy allocation must be between 0 and 100 percent")
        signals = strategy.get("signals")
        if not isinstance(signals, list):
            raise ValueError("Each strategy needs normalized signals")
        if len(lots) + len(signals) > MAX_SIGNALS:
            raise ValueError(f"Portfolio exceeds {MAX_SIGNALS} signals")
        cfg = validate_config(strategy.get("config"))
        # Account capital lives only at portfolio level, never in private accounts.
        cfg["initial_capital"] = float(capital)
        normalized.append({**strategy, "config": cfg})
        for signal in signals:
            if not isinstance(signal, dict) or not signal.get("symbol") or not signal.get("date"):
                raise ValueError("Each signal needs a symbol and date")
            if (
                signal.get("timestamp")
                and stamp(signal["timestamp"]).date().isoformat() != signal["date"]
            ):
                raise ValueError("Signal timestamp and date disagree")
            lots.append((len(normalized) - 1, signal))
    if not lots:
        raise ValueError("This period has no strategy signals")
    if len(lots) > MAX_SIGNALS:
        raise ValueError(f"Portfolio exceeds {MAX_SIGNALS} signals")
    interval = snapshot.get("provenance", {}).get("interval")
    minute = interval == "1m"
    if interval not in ("D", "1m"):
        raise ValueError("Portfolio VectorBT requires daily or one-minute data")
    timeline = snapshot.get("timeline" if minute else "sessions", [])
    if len(timeline) * len(lots) > MAX_MATRIX_CELLS:
        raise ValueError("Portfolio VectorBT matrix limit reached; use a smaller period or sample")
    if not minute and any(
        s["config"].get("trade_horizon") == "intraday"
        or any(s["config"].get(k) is not None for k in ("hold_minutes", "entry_time", "exit_time"))
        or any(signal.get("timestamp") for signal in s["signals"])
        for s in normalized
    ):
        raise ValueError("Timestamped or intraday strategies require one-minute prices")
    coverage = validate_snapshot(snapshot, [signal for _, signal in lots])
    index = {key: i for i, key in enumerate(timeline)}
    scheduled = []
    for strategy_i, signal in lots:
        cfg = normalized[strategy_i]["config"]
        entry, excluded = _schedule(signal, snapshot, cfg, timeline, index, minute)
        scheduled.append((entry, excluded))
        if entry < 0:
            continue
        deadline = _deadline(entry, snapshot, cfg, timeline, minute)
        series = snapshot["bars"].get(signal["symbol"], {})
        missing = next((key for key in timeline[entry : deadline + 1] if key not in series), None)
        if missing:
            raise ValueError(
                "Portfolio VectorBT requires complete prices through each potential holding window; "
                f"missing {signal['symbol']} at {missing}"
            )
    return normalized, lots, scheduled, coverage


def evaluate(strategies, snapshot, capital, *, progress=None):
    strategies, lots, scheduled, coverage = validate(strategies, snapshot, capital)
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    if progress:
        progress(0, len(timeline))
    import numpy as np
    import pandas as pd
    import vectorbt as vbt
    from vectorbt.portfolio import nb
    from vectorbt.portfolio.enums import Direction, NoOrder, OrderStatus, TradeStatus

    count = len(lots)
    close = np.full((len(timeline), count), np.nan)
    grid_index = {key: i for i, key in enumerate(timeline)}
    day_index = {day: i for i, day in enumerate(snapshot["sessions"])}
    instants = [stamp(key) for key in timeline] if minute else None
    closing_times = (
        {day: bounds(snapshot, day)[1] for day in snapshot["sessions"]} if minute else {}
    )
    filled_at = np.full(count, -1, dtype=np.int64)
    entry_price = np.full(count, np.nan)
    high_water = np.full(count, np.nan)
    rejected, budgets, entry_raw, exit_raw, outcomes, timings = ([None] * count for _ in range(6))
    native_status = [None] * count
    entries = {}
    for col, (_, signal) in enumerate(lots):
        if scheduled[col][0] >= 0:
            entries.setdefault(scheduled[col][0], []).append(col)
        for key, bar in snapshot["bars"].get(signal["symbol"], {}).items():
            close[grid_index[key], col] = float(bar["close"])

    def bar_at(col, i):
        return snapshot["bars"].get(lots[col][1]["symbol"], {}).get(timeline[i])

    def risk(col, i, position, opening_only=False):
        cfg = strategies[lots[col][0]]["config"]
        bar = bar_at(col, i)
        op, lo, hi = (float(bar[k]) for k in ("open", "low", "high"))
        anchor, stop_pct, reason = entry_price[col], cfg["stop_pct"] / 100, "stop"
        if cfg["trailing_enabled"] and high_water[col] * (
            1 - cfg["trailing_pct"] / 100
        ) > anchor * (1 - stop_pct):
            anchor, stop_pct, reason = high_water[col], cfg["trailing_pct"] / 100, "trailing_stop"
        if opening_only:
            lo = hi = op
        stop = nb.get_stop_price_nb(position, anchor, stop_pct, op, lo, hi, True)
        target = nb.get_stop_price_nb(
            position, entry_price[col], cfg["target_pct"] / 100, op, lo, hi, False
        )
        timing = "open" if opening_only else "intraday"
        if not np.isnan(stop):
            return float(stop), reason, timing
        if not np.isnan(target):
            return float(target), "target", timing
        if minute and opening_only:
            now, entered = instants[i], instants[filled_at[col]]
            if cfg.get("hold_minutes") is not None and now >= entered + timedelta(
                minutes=cfg["hold_minutes"]
            ):
                return op, "hold", "open"
            if cfg.get("exit_time") and now.strftime("%H:%M") >= cfg["exit_time"]:
                return op, "time", "open"
        if not opening_only:
            if not minute:
                due = i - filled_at[col] >= cfg["hold_sessions"]
            else:
                due = instants[i] + timedelta(minutes=1) == closing_times[timeline[i][:10]] and (
                    cfg.get("trade_horizon") == "intraday"
                    or day_index[timeline[i][:10]] - day_index[timeline[filled_at[col]][:10]]
                    >= cfg["hold_sessions"]
                )
            if due:
                return float(bar["close"]), "hold", "close"
        return None

    def before_bar(c):
        if progress:
            progress(c.i, len(timeline))
        held = []
        opening_exits = []
        for col in range(count):
            bar = bar_at(col, c.i)
            if bar:
                c.last_val_price[col] = float(bar["open"])
            if c.last_position[col] > 0:
                if bar is None:
                    raise ValueError("An open portfolio position has no current price")
                held.append(col)
                proposal = risk(col, c.i, c.last_position[col], opening_only=True)
                if proposal:
                    opening_exits.append((col, proposal))
        # Freeze before any fills: allocation is based on opening marked equity.
        equity = float(
            nb.get_group_value_nb(0, count, c.last_cash[c.group], c.last_position, c.last_val_price)
        )
        entry_cols = entries.get(c.i, [])
        actions = [(col, proposal) for col, proposal in opening_exits]
        actions += [(col, "entry") for col in entry_cols]
        actions += [(col, "later") for col in sorted(set(held + entry_cols))]
        return ({"actions": actions, "equity": equity},)

    def order(c, state):
        if c.call_idx >= len(state["actions"]):
            return -1, NoOrder
        col, action = state["actions"][c.call_idx]
        strategy_i, _ = lots[col]
        strategy = strategies[strategy_i]
        cfg = strategy["config"]
        fee, slip = cfg["cost_bps"] / 10000, cfg["slippage_bps"] / 10000
        if action == "entry":
            cap = state["equity"] * strategy["allocation_pct"] / 100
            budget = cap * cfg["order_size_pct"] / 100
            budgets[col] = float(budget)
            deployed = sum(
                c.last_position[j] * float(bar_at(j, c.i)["open"])
                for j in range(count)
                if lots[j][0] == strategy_i and c.last_position[j] > 0
            )
            raw = float(bar_at(col, c.i)["open"])
            quantity = math.floor((budget + 1e-9) / (raw * (1 + slip) * (1 + fee)))
            if deployed + budget > cap + 1e-8:
                rejected[col] = "Strategy allocation is already in use"
            elif budget > c.last_cash[c.group] + 1e-8:
                rejected[col] = "Insufficient opening cash for the selected position budget"
            elif quantity < 1:
                rejected[col] = "Position budget cannot fund one whole share including costs"
            if rejected[col]:
                return col, NoOrder
            entry_raw[col] = raw
            return col, nb.order_nb(
                size=quantity,
                price=raw,
                direction=Direction.LongOnly,
                fees=fee,
                slippage=slip,
                size_granularity=1,
                allow_partial=False,
                log=True,
            )
        if c.last_position[col] <= 0:
            return col, NoOrder
        proposed = risk(col, c.i, c.last_position[col]) if action == "later" else action
        if not proposed:
            return col, NoOrder
        raw, outcome, timing = proposed
        exit_raw[col], outcomes[col], timings[col] = raw, outcome, timing
        return col, nb.close_position_nb(
            price=raw, fees=fee, slippage=slip, allow_partial=False, log=True
        )

    def after_order(c, state):
        if c.order_result.status != OrderStatus.Filled:
            if entry_raw[c.col] is not None and filled_at[c.col] < 0:
                native_status[c.col] = int(c.order_result.status_info)
                rejected[c.col] = "VectorBT did not fill the opening order"
            return
        if c.position_before == 0:
            filled_at[c.col], entry_price[c.col], high_water[c.col] = (
                c.i,
                c.order_result.price,
                c.order_result.price,
            )

    def after_bar(c):
        for col in range(count):
            if c.last_position[col] > 0:
                high_water[col] = max(high_water[col], float(bar_at(col, c.i)["high"]))

    portfolio = vbt.Portfolio.from_order_func(
        pd.DataFrame(close, index=pd.to_datetime(timeline), columns=list(range(count))),
        order,
        init_cash=float(capital),
        cash_sharing=True,
        group_by=True,
        flexible=True,
        row_wise=True,
        use_numba=False,
        update_value=True,
        pre_segment_func_nb=before_bar,
        post_order_func_nb=after_order,
        post_segment_func_nb=after_bar,
        max_orders=count * 2,
        max_logs=count * 2,
        freq="1min" if minute else "1D",
        fillna_close=False,
    )
    if progress:
        progress(len(timeline), len(timeline))
    return _report(
        portfolio,
        strategies,
        lots,
        scheduled,
        snapshot,
        capital,
        coverage,
        budgets,
        rejected,
        native_status,
        entry_raw,
        exit_raw,
        outcomes,
        timings,
        vbt.__version__,
        TradeStatus,
    )


def _summary(ledger, initial, values, curve):
    closed = [row for row in ledger if row["status"] == "closed"]
    wins = sum(max(0, row["pnl"]) for row in closed)
    losses = -sum(min(0, row["pnl"]) for row in closed)
    return {
        "initial_capital": initial,
        "final_equity": float(values[-1]),
        "net_pnl": float(values[-1]) - initial,
        "net_return_pct": (float(values[-1]) / initial - 1) * 100 if initial else None,
        "max_drawdown_pct": max(point["drawdown_pct"] for point in curve),
        "realized_equity": initial + sum(row["pnl"] for row in closed),
        "accepted_trades": sum(row["quantity"] > 0 for row in ledger),
        "closed_trades": len(closed),
        "pending_trades": sum(row["status"] == "pending" and row["quantity"] > 0 for row in ledger),
        "unfunded_pending": sum(
            row["status"] == "pending" and row["quantity"] == 0 for row in ledger
        ),
        "skipped_trades": sum(row["status"] == "skipped" for row in ledger),
        "excluded_signals": sum(row["status"] == "excluded" for row in ledger),
        "win_rate_pct": sum(row["pnl"] > 0 for row in closed) / len(closed) * 100
        if closed
        else None,
        "profit_factor": wins / losses if losses else None,
        "sample_adequacy": "Insufficient sample"
        if len(closed) < 30
        else "Historical sample available",
    }


def _curve(timeline, values, cash, counts, initial, minute):
    peak, curve = initial, []
    for i, key in enumerate(timeline):
        value = float(values[i])
        if not math.isfinite(value) or not math.isfinite(float(cash[i])):
            raise ValueError(
                "VectorBT returned an unvalued account; complete price coverage is required"
            )
        peak = max(peak, value)
        point = {
            "date": key[:10],
            "equity": value,
            "cash": float(cash[i]),
            "drawdown_pct": (peak - value) / peak * 100 if peak else 0,
            "open_positions": int(counts[i]),
        }
        if minute:
            point["timestamp"] = key
        curve.append(point)
    return curve


def _report(
    pf,
    strategies,
    lots,
    scheduled,
    snapshot,
    capital,
    coverage,
    budgets,
    rejected,
    native_status,
    entry_raw,
    exit_raw,
    outcomes,
    timings,
    engine_version,
    trade_status,
):
    import numpy as np

    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    by_col = {int(row["col"]): row for row in pf.trades.records_arr}
    ledger = []
    for col, (strategy_i, signal) in enumerate(lots):
        strategy = strategies[strategy_i]
        row = {
            "strategy_id": strategy["id"],
            "strategy_name": strategy["name"],
            "symbol": signal["symbol"],
            "signal_date": signal["date"],
            "signal_timestamp": signal.get("timestamp"),
            "source_row": signal.get("row"),
            "trigger_modes": ["Bypass"],
            "status": "pending",
            "reason": "Next eligible opening is outside the snapshot",
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
        if scheduled[col][1]:
            row.update(status="excluded", reason=scheduled[col][1])
        elif native is None and rejected[col]:
            row.update(
                status="skipped",
                reason=rejected[col],
                requested_budget=budgets[col],
                native_status_info=native_status[col],
            )
        elif native is not None:
            entry_key = timeline[int(native["entry_idx"])]
            row.update(
                entry_date=entry_key[:10],
                entry_price=float(native["entry_price"]),
                entry_raw_price=entry_raw[col],
                quantity=int(native["size"]),
                fees=float(native["entry_fees"]),
                requested_budget=budgets[col],
                reason="Position open at snapshot end",
                unrealized_pnl=float(native["pnl"]),
            )
            if minute:
                row["entry_timestamp"] = entry_key
            if native["status"] == trade_status.Closed:
                exit_key = timeline[int(native["exit_idx"])]
                row.update(
                    status="closed",
                    reason=f"{outcomes[col]}; {timings[col]} exit",
                    exit_date=exit_key[:10],
                    exit_price=float(native["exit_price"]),
                    exit_raw_price=exit_raw[col],
                    exit_timing=timings[col],
                    pnl=float(native["pnl"]),
                    unrealized_pnl=0.0,
                    outcome=outcomes[col],
                    fees=float(native["entry_fees"] + native["exit_fees"]),
                )
                if minute:
                    row["exit_timestamp"] = (
                        (stamp(exit_key) + timedelta(minutes=1)).isoformat()
                        if timings[col] == "close"
                        else exit_key
                    )
                    row["exit_timestamp_basis"] = (
                        "bar_open; crossing time within minute unknown"
                        if timings[col] == "intraday"
                        else timings[col]
                    )
        ledger.append(row)
    values = pf.value().to_numpy().reshape(-1)
    cash = pf.cash().to_numpy().reshape(-1)
    assets = pf.assets().to_numpy()
    native_asset_values = pf.asset_value(group_by=False).to_numpy()
    native_cashflows = pf.cash_flow(group_by=False).to_numpy()
    # Native flows + native marked assets give additive PnL per lot. Never split
    # shared initial cash through VectorBT's hypothetical ungrouped cash accounts.
    lot_pnl = np.cumsum(native_cashflows, axis=0) + native_asset_values
    if not np.allclose(lot_pnl.sum(axis=1), values - capital, rtol=1e-10, atol=1e-6):
        raise ValueError("Strategy attribution does not reconcile to the native portfolio")
    curve = _curve(timeline, values, cash, (assets > 0).sum(axis=1), capital, minute)
    per_strategy = []
    for strategy_i, strategy in enumerate(strategies):
        cols = [col for col, (owner, _) in enumerate(lots) if owner == strategy_i]
        pnl = lot_pnl[:, cols].sum(axis=1)
        initial = capital * strategy["allocation_pct"] / 100
        attributed_values = initial + pnl
        attributed_cash = initial + np.cumsum(native_cashflows[:, cols].sum(axis=1))
        strategy_curve = _curve(
            timeline,
            attributed_values,
            attributed_cash,
            (assets[:, cols] > 0).sum(axis=1),
            initial,
            minute,
        )
        for i, point in enumerate(strategy_curve):
            point["net_pnl"] = float(pnl[i])
            point["contribution_pct"] = float(pnl[i]) / capital * 100
        own_ledger = [row for row in ledger if row["strategy_id"] == strategy["id"]]
        summary = _summary(own_ledger, initial, attributed_values, strategy_curve)
        per_strategy.append(
            {
                "id": strategy["id"],
                "name": strategy["name"],
                "allocation_pct": strategy["allocation_pct"],
                "summary": summary,
                "equity_curve": strategy_curve,
                "net_pnl": float(pnl[-1]),
                "contribution_pct": float(pnl[-1]) / capital * 100,
            }
        )

    def records(rows):
        return [{key: row[key].item() for key in row.dtype.names} for row in rows]

    return {
        "policy_version": POLICY_VERSION,
        "metric_basis": "minute_marked" if minute else "daily_marked",
        "strategies": [
            {key: strategy[key] for key in ("id", "name", "allocation_pct", "config")}
            for strategy in strategies
        ],
        "config": {"initial_capital": float(capital)},
        "summary": _summary(ledger, capital, values, curve),
        "equity_curve": curve,
        "ledger": ledger,
        "per_strategy": per_strategy,
        "coverage": coverage,
        "missing_marks": [],
        "execution": {
            "engine": "vectorbt",
            "engine_version": engine_version,
            "adapter_version": ADAPTER_VERSION,
            "simulation_api": "Portfolio.from_order_func",
            "shared_cash": True,
            "flexible_orders": True,
            "strategy_count": len(strategies),
            "signal_columns": len(lots),
            "matrix_cells": len(lots) * len(timeline),
            "interval": snapshot["provenance"]["interval"],
            "allocation_basis": "opening_account_equity_marked_cap",
            "attribution_basis": "native_lot_cashflows_plus_marked_assets",
            "contribution_basis": "percentage_points_of_initial_portfolio_capital",
        },
        "engine_records": {
            "orders": records(pf.orders.records_arr),
            "trades": records(pf.trades.records_arr),
        },
        "limits": [
            "All strategies share one native VectorBT cash account; allocations are deployed-capital limits, not separate cash accounts.",
            "Opening exits precede entries in strategy then CSV order; within-bar and closing proceeds cannot retroactively fund opening entries.",
            "Protection applies on the entry bar. Stop-first ambiguity and completed-bar trailing apply at the selected account interval, so minute and daily runs can differ.",
            "All lots use the finest interval required by the account; date-only signals remain next-session entries on minute data.",
            "Strategy equity curves are attribution against their initial allocation, not independently simulated or reserved cash accounts.",
            "Complete potential holding-window prices are required. Snapshot tails retain open positions; no invented bars or forced liquidation.",
            "Long cash equities only; no margin, shorts, derivatives or corporate-action cashflows.",
        ],
    }
