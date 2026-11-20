"""Narrow offline SDK adapter; the Rust engine owns every fill and cash account.

Repeated symbols are admitted only with disjoint potential holding windows. No
synthetic instrument aliases or private per-signal accounts hide native netting.
Imports of the optional SDK happen only inside its isolated calculation process.
"""

import math
from datetime import date, timedelta
from decimal import Decimal

from research.connectors.nautilus_portfolio import incompatible_price_reason, validate_instrument
from research.connectors.vectorbt_portfolio import _curve, _summary
from research.connectors.vectorbt_portfolio import validate as validate_portfolio
from research.connectors.vectorbt_portfolio import validate_config as base_config

TESTED_VERSION = "2.2.0"
ADAPTER_VERSION = "openalgo-portfolio-adapter-v1"
POLICY_VERSION = "openalgo-daily-opening-delivery-v1"
INSTRUMENT_ELIGIBILITY_VERSION = "openalgo-instrument-window-eligibility-v1"


def _window(strategies, lots, scheduled, sessions):
    spans = [
        (entry, min(entry + strategies[owner]["config"]["hold_sessions"], len(sessions) - 1))
        for (owner, _), (entry, excluded) in zip(lots, scheduled, strict=True)
        if entry >= 0 and not excluded
    ]
    if not spans:
        raise ValueError("No executable OpenAlgo signal windows remain")
    return min(a for a, _ in spans), max(b for _, b in spans)


def validate_config(config=None):
    cfg = base_config(config)
    if cfg["trailing_enabled"]:
        raise ValueError("OpenAlgo preview does not support this connector's trailing stops")
    if cfg["cost_bps"] or cfg["slippage_bps"]:
        raise ValueError(
            "OpenAlgo preview uses native Zerodha delivery charges and zero slippage; "
            "custom flat costs and slippage must be zero"
        )
    if cfg.get("trade_horizon") == "intraday" or any(
        cfg.get(k) is not None for k in ("hold_minutes", "entry_time", "exit_time")
    ):
        raise ValueError("OpenAlgo preview supports daily untimed NSE cash signals only")
    return cfg


def validate_signal_windows(strategies, sessions):
    """Reject unsupported overlap using only the frozen calendar, before downloads."""
    index, windows = {day: i for i, day in enumerate(sessions)}, {}
    for strategy in strategies:
        cfg = validate_config(strategy["config"])
        for signal in strategy["signals"]:
            if signal.get("timestamp"):
                raise ValueError("OpenAlgo preview supports daily untimed signals only")
            if signal.get("research_exclusion") or signal["date"] not in index:
                continue
            entry = index[signal["date"]] + 1
            if entry < len(sessions):
                windows.setdefault(signal["symbol"], []).append(
                    (entry, entry + cfg["hold_sessions"])
                )
    for symbol, spans in windows.items():
        previous_end = -1
        for first, last in sorted(spans):
            if first <= previous_end:
                raise ValueError(
                    f"OpenAlgo preview cannot preserve overlapping repeated signals for {symbol}; "
                    "use VectorBT to keep independent trades. No signals were dropped or merged."
                )
            previous_end = last


def validate(strategies, snapshot, capital):
    normalized, lots, scheduled, coverage = validate_portfolio(strategies, snapshot, capital)
    validate_signal_windows(normalized, snapshot["sessions"])
    if snapshot["provenance"]["interval"] != "D":
        raise ValueError("OpenAlgo preview supports daily NSE cash signals only")
    if snapshot["provenance"].get("exchange", "NSE") != "NSE":
        raise ValueError("OpenAlgo preview supports NSE cash signals only")
    if Decimal(str(capital)) != Decimal(str(capital)).quantize(Decimal("0.01")):
        raise ValueError("OpenAlgo INR capital must use at most two decimal places")
    for row in normalized:
        validate_config(row["config"])
        if "execution_policy" in row or "entry_condition" in row:
            raise ValueError("Execution policies and entry conditions currently need VectorBT")
        if any(s.get("timestamp") for s in row["signals"]):
            raise ValueError("OpenAlgo preview supports daily untimed signals only")
    sessions, windows = snapshot["sessions"], {}
    for col, (owner, signal) in enumerate(lots):
        entry, excluded = scheduled[col]
        if entry < 0 or excluded:
            continue
        # Do not clamp an intended deadline to the snapshot tail: tails stay open.
        last = entry + normalized[owner]["config"]["hold_sessions"]
        symbol = signal["symbol"]
        windows.setdefault(symbol, []).append((entry, last))
        item = snapshot.get("instruments", {}).get(symbol, {})
        validate_instrument(symbol, item)
        for key in sessions[entry : min(last + 1, len(sessions))]:
            reason = incompatible_price_reason(symbol, key, snapshot["bars"][symbol][key], item)
            if reason:
                raise ValueError(reason)
    first, last = _window(normalized, lots, scheduled, sessions)
    for symbol in windows:
        for key in sessions[first : last + 1]:
            if key not in snapshot["bars"].get(symbol, {}):
                raise ValueError(
                    f"OpenAlgo preview needs shared frozen daily coverage for every traded symbol; "
                    f"missing {symbol} at {key}. Use VectorBT for sparse per-signal coverage."
                )
            reason = incompatible_price_reason(
                symbol, key, snapshot["bars"][symbol][key], snapshot["instruments"][symbol]
            )
            if reason:
                raise ValueError(reason)
    return normalized, lots, scheduled, coverage


def _records(frame):
    # pandas' JSON conversion normalizes NaN/NaT and serializes native timestamps.
    import json

    return json.loads(frame.to_json(orient="records", date_format="iso", double_precision=15))


def evaluate(strategies, snapshot, capital, *, progress=None):
    """Called exclusively by the isolated SDK worker; no broker client is created."""
    strategies, lots, scheduled, coverage = validate(strategies, snapshot, capital)
    from importlib import metadata

    import openalgo.backtest as bt
    import pandas as pd
    from openalgo.backtest.reference import Instrument

    if metadata.version("openalgo") != TESTED_VERSION:
        raise ValueError(f"OpenAlgo backtest runtime requires SDK {TESTED_VERSION}")
    sessions = snapshot["sessions"]
    first, last = _window(strategies, lots, scheduled, sessions)
    run_sessions = sessions[first : last + 1]
    # Freeze both trading and non-trading days; never let the SDK add weekdays.
    calendar = bt.calendar.Calendar(builtin=False)
    day, end = date.fromisoformat(run_sessions[0]), date.fromisoformat(run_sessions[-1])
    included = set(run_sessions)
    while day <= end:
        key = day.isoformat()
        if key in included:
            hours = snapshot.get("session_hours", {}).get(key, {})
            calendar.add_session(
                "NSE", key, hours.get("open", "09:15"), hours.get("close", "15:30")
            )
        else:
            calendar.add_closure("NSE", key)
        day += timedelta(days=1)
    symbols = tuple(
        sorted(
            {
                signal["symbol"]
                for (_, signal), (entry, excluded) in zip(lots, scheduled, strict=True)
                if entry >= 0 and not excluded
            }
        )
    )
    data = {
        symbol: pd.DataFrame.from_dict(
            {key: snapshot["bars"][symbol][key] for key in run_sessions}, orient="index"
        ).rename_axis("date")
        for symbol in symbols
    }
    for frame in data.values():
        frame.index = pd.to_datetime(frame.index)
    instruments = {}
    for symbol in symbols:
        item = snapshot.get("instruments", {}).get(symbol)
        if item is None:
            # Excluded-only symbols have no executable orders and need no invented instrument.
            continue
        instruments[symbol] = Instrument(
            symbol=symbol,
            exchange="NSE",
            kind="equity",
            lot_size=1,
            tick_size=float(item["tick_size"]),
            settlement="delivery",
        )
    symbols = tuple(s for s in symbols if s in instruments)
    if not symbols:
        raise ValueError("No executable OpenAlgo signal windows have instrument metadata")
    data = {s: data[s] for s in symbols}
    attempts = {}
    signal_at = {}
    for col, (_, _signal) in enumerate(lots):
        entry, excluded = scheduled[col]
        if entry >= 0 and not excluded:
            signal_at.setdefault(sessions[entry], []).append(col)

    class ResearchSignals(bt.Strategy):
        exchange, interval = "NSE", "D"

        def init(self):
            # Runs at every supplied day's first opening, including special sessions.
            self.at("00:00", self.opening)

        def opening(self):
            key = self.now.date().isoformat()
            i = sessions.index(key)
            if progress:
                progress(i, len(sessions))
            held = self.positions
            # Engine equity at this timer is the prior close. Only today's opens
            # mark its native positions for the allocation cap; no future OHLC.
            equity = self.equity
            for symbol, position in held.items():
                if position.qty and key != run_sessions[0] and symbol in snapshot["bars"]:
                    today = snapshot["bars"][symbol].get(key)
                    prior = snapshot["bars"][symbol].get(sessions[i - 1])
                    if today is None or prior is None:
                        raise ValueError("Complete opening marks are required for held positions")
                    equity += position.qty * (today["open"] - prior["close"])
            for col, (owner, signal) in enumerate(lots):
                entry = scheduled[col][0]
                if entry >= 0 and i == entry + strategies[owner]["config"]["hold_sessions"]:
                    if signal["symbol"] in held and held[signal["symbol"]].qty:
                        self.exit(symbol=signal["symbol"])
            # Queued exits are not credited before the SDK actually fills them.
            available = self.cash
            deployed = [0.0] * len(strategies)
            for col, (owner, signal) in enumerate(lots):
                entry = scheduled[col][0]
                if entry < 0 or not (
                    entry <= i <= entry + strategies[owner]["config"]["hold_sessions"]
                ):
                    continue
                p = held.get(signal["symbol"])
                if p is not None and p.qty:
                    deployed[owner] += p.qty * snapshot["bars"][signal["symbol"]][key]["open"]
            for col in signal_at.get(key, []):
                owner, signal = lots[col]
                row, symbol = strategies[owner], signal["symbol"]
                cfg = row["config"]
                cap = equity * row["allocation_pct"] / 100
                budget = max(
                    0.0, min(cap * cfg["order_size_pct"] / 100, cap - deployed[owner], available)
                )
                price = snapshot["bars"][symbol][key]["open"]
                qty = math.floor(budget / price)
                # Native dated charges are included in affordability; binary search
                # avoids both arbitrary buffers and unbounded decrement loops.
                low, high = 0, qty
                while low < high:
                    mid = (low + high + 1) // 2
                    charge = bt.costs.trade_cost(
                        "BUY", price, mid, key, exchange="NSE", product="CNC"
                    ).total
                    if mid * price + charge <= budget:
                        low = mid
                    else:
                        high = mid - 1
                qty = low
                attempts[col] = {"requested_budget": budget, "quantity": qty}
                if qty:
                    order = self.buy(
                        qty=qty,
                        symbol=symbol,
                        tag=f"signal-{col}",
                        stop=bt.pct(cfg["stop_pct"]),
                        target=bt.pct(cfg["target_pct"]),
                    )
                    attempts[col]["order_id"] = order.number
                    spent = (
                        qty * price
                        + bt.costs.trade_cost(
                            "BUY", price, qty, key, exchange="NSE", product="CNC"
                        ).total
                    )
                    available -= spent
                    deployed[owner] += spent

    ResearchSignals.symbols = symbols
    native = bt.backtest(
        ResearchSignals,
        client=None,
        data=data,
        instrument=instruments,
        calendar=calendar,
        capital=capital,
        start=run_sessions[0],
        end=run_sessions[-1],
        base="D",
        tz="Asia/Kolkata",
        product="CNC",
        quantity_mode="venue",
        costs="zerodha",
        slippage=0,
    )
    if progress:
        progress(len(sessions), len(sessions))
    return _report(native, strategies, lots, scheduled, snapshot, capital, coverage, attempts)


def _report(native, strategies, lots, scheduled, snapshot, capital, coverage, attempts):
    sessions = snapshot["sessions"]
    fills, orders, trades = _records(native.fills), _records(native.orders), _records(native.trades)
    costs, notes = _records(native.costs), _records(native.contract_notes)
    if len(costs) != len(fills):
        raise ValueError("OpenAlgo charge and fill evidence do not align")
    # The native ledger settles each contract note once per day. Attribute its
    # exact posted paise across that day's fills in proportion to accrued fees;
    # largest remainders use stable native fill order. DP charges stay on their fill.
    settled = [int(round(c["dp_charge"] * 100)) for c in costs]
    for note in notes:
        indices = [i for i, c in enumerate(costs) if c["date"][:10] == note["date"][:10]]
        weights = [costs[i]["cost"] for i in indices]
        total = sum(weights)
        charged = int(round(note["charged_total"] * 100))
        shares = [charged * w / total if total else 0 for w in weights]
        pennies = [math.floor(v) for v in shares]
        remainder = charged - sum(pennies)
        for j in sorted(range(len(indices)), key=lambda j: (-(shares[j] - pennies[j]), j))[
            :remainder
        ]:
            pennies[j] += 1
        for i, amount in zip(indices, pennies, strict=True):
            settled[i] += amount
    for fill, amount in zip(fills, settled, strict=True):
        fill["accrued_costs_rupees"] = fill["costs_rupees"]
        fill["settled_costs_rupees"] = amount / 100
    ledger, attributed = [], [[0.0] * len(sessions) for _ in strategies]
    for col, (owner, signal) in enumerate(lots):
        row = {
            "strategy_id": strategies[owner]["id"],
            "strategy_name": strategies[owner]["name"],
            "symbol": signal["symbol"],
            "signal_date": signal["date"],
            "source_row": signal.get("row"),
            "signal_timestamp": None,
            "trigger_modes": ["Bypass"],
            "status": "pending",
            "reason": "Next eligible opening is outside the snapshot",
            "quantity": 0,
            "entry_date": None,
            "exit_date": None,
            "entry_price": None,
            "exit_price": None,
            "pnl": None,
            "fees": 0.0,
            "outcome": None,
            "engine_column": col,
        }
        entry, excluded = scheduled[col]
        if excluded:
            row.update(status="excluded", reason=excluded)
        attempt = attempts.get(col)
        if attempt:
            row["requested_budget"] = attempt["requested_budget"]
            order = next((o for o in orders if o["order_id"] == attempt.get("order_id")), None)
            order_id = attempt.get("order_id")
            own = [
                f
                for f in fills
                if order_id is not None and f["order_id"] == order_id and f["side"] == "BUY"
            ]
            if not own:
                row.update(
                    status="skipped",
                    reason=(order or {}).get("reject_code")
                    or "Budget cannot fund one whole share including native charges",
                )
            else:
                first = own[0]
                entry_date = sessions[entry]
                # Windows are disjoint by admission, so symbol+entry time uniquely identifies a trade.
                trade = next(
                    (
                        t
                        for t in trades
                        if t["symbol"] == signal["symbol"] and t["entry_time"] == first["time"]
                    ),
                    None,
                )
                row.update(
                    quantity=int(first["quantity_units"]),
                    entry_date=entry_date,
                    entry_price=first["price_rupees"],
                    fees=first["settled_costs_rupees"],
                    reason="Position open at snapshot end",
                )
                exit_date = None
                if trade:
                    import pandas as pd

                    exit_date = (
                        pd.Timestamp(trade["exit_time"])
                        .tz_convert("Asia/Kolkata")
                        .date()
                        .isoformat()
                    )
                    exit_fills = [
                        f
                        for f in fills
                        if f["symbol"] == signal["symbol"]
                        and f["side"] == "SELL"
                        and pd.Timestamp(first["time"])
                        <= pd.Timestamp(f["time"])
                        <= pd.Timestamp(trade["exit_time"])
                    ]
                    fees = first["settled_costs_rupees"] + sum(
                        f["settled_costs_rupees"] for f in exit_fills
                    )
                    row.update(
                        status="closed",
                        exit_date=exit_date,
                        exit_price=trade["exit_price_rupees"],
                        pnl=trade["gross_pnl_rupees"] - fees,
                        fees=fees,
                        outcome=trade["exit_reason"],
                        reason=f"Native {trade['exit_reason']} exit",
                        exit_timing="open" if trade["exit_reason"] == "signal" else "intraday",
                    )
                own_pnl = 0.0
                for i, key in enumerate(sessions):
                    if key < entry_date:
                        continue
                    if exit_date and key >= exit_date:
                        pnl = row["pnl"]
                    else:
                        mark = snapshot["bars"][signal["symbol"]].get(key)
                        if mark is None:
                            raise ValueError("OpenAlgo position lacks a frozen closing mark")
                        pnl = (
                            row["quantity"] * (mark["close"] - row["entry_price"])
                            - first["settled_costs_rupees"]
                        )
                    attributed[owner][i] += pnl
                    own_pnl = pnl
                if not trade:
                    row["unrealized_pnl"] = own_pnl
        ledger.append(row)
    native_values = native.equity["equity_rupees"].tolist()
    native_days = [stamp.date().isoformat() for stamp in native.equity.index]
    first, last = _window(strategies, lots, scheduled, sessions)
    if native_days != sessions[first : last + 1]:
        raise ValueError("OpenAlgo returned a calendar different from the frozen session evidence")
    values = [capital] * first + native_values + [native_values[-1]] * (len(sessions) - last - 1)
    for i, value in enumerate(values):
        if not math.isclose(capital + sum(a[i] for a in attributed), value, abs_tol=0.02):
            raise ValueError(
                f"OpenAlgo fills and attributed account equity do not reconcile at {sessions[i]}: native={value}, attributed={capital + sum(a[i] for a in attributed)}"
            )
    counts = [
        sum(
            r["quantity"] > 0
            and r["entry_date"] <= d
            and (not r["exit_date"] or r["exit_date"] > d)
            for r in ledger
        )
        for d in sessions
    ]
    native_cash = (native.equity["equity_rupees"] - native.equity["exposure_rupees"]).tolist()
    cash = [capital] * first + native_cash + [native_cash[-1]] * (len(sessions) - last - 1)
    curve = _curve(sessions, values, cash, counts, capital, False)
    per_strategy = []
    for owner, strategy in enumerate(strategies):
        initial = capital * strategy["allocation_pct"] / 100
        own_values = [initial + p for p in attributed[owner]]
        own_ledger = [r for r in ledger if r["strategy_id"] == strategy["id"]]
        own_counts, own_cash = [], []
        for i, day in enumerate(sessions):
            held = [
                r
                for r in own_ledger
                if r["quantity"] > 0
                and r["entry_date"] <= day
                and (not r["exit_date"] or r["exit_date"] > day)
            ]
            own_counts.append(len(held))
            own_cash.append(
                own_values[i]
                - sum(r["quantity"] * snapshot["bars"][r["symbol"]][day]["close"] for r in held)
            )
        own_curve = _curve(sessions, own_values, own_cash, own_counts, initial, False)
        for point, pnl in zip(own_curve, attributed[owner], strict=True):
            point["net_pnl"] = pnl
        per_strategy.append(
            {
                "id": strategy["id"],
                "name": strategy["name"],
                "allocation_pct": strategy["allocation_pct"],
                "summary": _summary(own_ledger, initial, own_values, own_curve),
                "equity_curve": own_curve,
                "net_pnl": attributed[owner][-1],
                "contribution_pct": attributed[owner][-1] / capital * 100,
            }
        )
    report = {
        "policy_version": POLICY_VERSION,
        "metric_basis": "daily_marked",
        "strategies": [
            {k: r[k] for k in ("id", "name", "allocation_pct", "config")} for r in strategies
        ],
        "config": {"initial_capital": capital},
        "summary": _summary(ledger, capital, values, curve),
        "equity_curve": curve,
        "ledger": ledger,
        "per_strategy": per_strategy,
        "coverage": coverage,
        "missing_marks": [],
        "execution": {
            "engine": "openalgo",
            "engine_version": TESTED_VERSION,
            "adapter_version": ADAPTER_VERSION,
            "simulation_api": "openalgo.backtest.backtest",
            "shared_cash": True,
            "interval": "D",
            "strategy_count": len(strategies),
            "cost_model": "zerodha_dated_CNC",
            "holding_exit": "opening_of_entry_session_plus_hold_sessions",
            "independent_overlaps": False,
            "warnings": native.warnings,
            "native_manifest": native.manifest,
        },
        "engine_records": {
            "orders": orders,
            "trades": trades,
            "fills": fills,
            "costs": costs,
            "contract_notes": notes,
        },
        "limits": [
            "Preview: disjoint daily NSE cash signal windows only; overlapping repeated signals are rejected, never merged or dropped.",
            "Holding exits occur at the opening of entry session plus hold_sessions, unlike VectorBT's deadline close; native stop/target matching can also differ.",
            "Native dated Zerodha delivery charges apply. Flat-cost overrides, slippage, trailing stops and automatic research are not supported by this connector.",
            "Native CNC settlement determines available buying cash. Queued sales are not precredited to simultaneous entries.",
            "Trade attribution apportions native settled daily contract-note charges in paise; raw accrued native trade costs remain in engine records. Chart cash is settled cash, not the broker's free buying cash.",
            "Supplied master-contract ticks are checked against frozen prices; they are not proof of historical tick rules. Snapshot tails stay open.",
        ],
    }
    from research.analytics import build_analysis

    report["analysis"] = build_analysis(report)
    return report
