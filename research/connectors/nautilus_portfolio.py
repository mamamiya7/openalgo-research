"""Bounded native NautilusTrader portfolio execution in its separate runtime.

The adapter translates checked OHLC into explicitly modelled market events and
scanner rules into native orders. Nautilus owns matching, cash, positions and PnL.
Optional numerical/runtime imports occur only during worker evaluation.
"""

import math
import sys
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from research.connectors.vectorbt_portfolio import (
    _curve,
    _deadline,
    _summary,
)
from research.connectors.vectorbt_portfolio import (
    validate as validate_portfolio,
)
from research.connectors.vectorbt_portfolio import (
    validate_config as validate_portfolio_config,
)
from research.intraday import bounds, stamp

ADAPTER_VERSION = "nautilus-portfolio-adapter-v2"
POLICY_VERSION = "nautilus-ohlc-low-first-v1"
INSTRUMENT_ELIGIBILITY_VERSION = "nautilus-supplied-grid-windows-v1"
TESTED_VERSION = "1.231.0"
MAX_EVENTS = 200_000
MAX_BAR_LOT_CELLS = 100_000
MAX_SIGNAL_LOTS = 2_500
MODEL_QUOTE_SIZE = 10_000_000_000


def validate_config(config=None):
    cfg = validate_portfolio_config(config)
    if cfg["slippage_bps"]:
        raise ValueError("Nautilus OHLC connector currently requires zero slippage")
    if cfg["trailing_enabled"]:
        raise ValueError("Nautilus OHLC connector does not yet support trailing stops")
    return cfg


def validate_instrument(symbol, item):
    """Validate supplied specifications without guessing a historical tick grid."""
    try:
        tick = Decimal(str(item["tick_size"]))
        precision = item["price_precision"]
        valid = (
            item["currency"] == "INR"
            and item["lot_size"] == 1
            and not isinstance(item["lot_size"], bool)
            and not isinstance(precision, bool)
            and isinstance(precision, int)
            and 0 <= precision <= 8
            and tick.is_finite()
            and tick > 0
            and tick == tick.quantize(Decimal(1).scaleb(-precision))
        )
    except (KeyError, ValueError, TypeError, ArithmeticError):
        valid = False
    if not valid:
        raise ValueError(
            f"Nautilus requires {symbol} instrument metadata: positive tick_size, "
            "price_precision, lot_size 1 and currency INR"
        )
    return tick, precision


def incompatible_price_reason(symbol, key, bar, item):
    """A real price difference is unsupported, never a license to round OHLC."""
    tick, precision = validate_instrument(symbol, item)
    quantum = Decimal(1).scaleb(-precision)
    for field in ("open", "high", "low", "close"):
        value = Decimal(str(bar[field]))
        if value != value.quantize(quantum):
            return (
                f"Historical price precision is incompatible with supplied trading specifications: "
                f"{symbol} at {key} ({field})"
            )
        if value % tick:
            return (
                f"Historical price is incompatible with the supplied tick size: "
                f"{symbol} at {key} ({field})"
            )
    return None


def _market_windows(strategies, lots, scheduled, snapshot):
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    windows = {}
    for col, (owner, signal) in enumerate(lots):
        entry = scheduled[col][0]
        if entry < 0:
            continue
        last = _deadline(entry, snapshot, strategies[owner]["config"], timeline, minute)
        windows.setdefault(signal["symbol"], set()).update(timeline[entry : last + 1])
    return windows


def validate(strategies, snapshot, capital):
    normalized, lots, scheduled, coverage = validate_portfolio(strategies, snapshot, capital)
    if Decimal(str(capital)) != Decimal(str(capital)).quantize(Decimal("0.01")):
        raise ValueError("Nautilus INR capital must use at most two decimal places")
    for strategy in normalized:
        validate_config(strategy["config"])
    timeline = snapshot["timeline" if snapshot["provenance"]["interval"] == "1m" else "sessions"]
    if len(lots) > MAX_SIGNAL_LOTS or len(lots) * len(timeline) > MAX_BAR_LOT_CELLS:
        raise ValueError("Nautilus portfolio size limit reached; shorten the period or sample")
    windows = _market_windows(normalized, lots, scheduled, snapshot)
    events = 8 * len(timeline) + 4 * sum(len(keys) for keys in windows.values())
    if events > MAX_EVENTS:
        raise ValueError("Nautilus market-event limit reached; shorten the period or sample")
    metadata = snapshot.get("instruments", {})
    for symbol in sorted(windows):
        item = metadata.get(symbol, {})
        validate_instrument(symbol, item)
        for key in sorted(windows[symbol]):
            reason = incompatible_price_reason(symbol, key, snapshot["bars"][symbol][key], item)
            if reason:
                raise ValueError(reason)
    return normalized, lots, scheduled, coverage


def evaluate(strategies, snapshot, capital, *, progress=None):
    strategies, lots, scheduled, coverage = validate(strategies, snapshot, capital)
    if sys.platform != "linux":
        raise ValueError("The tested Nautilus connector requires its separate Linux runtime")
    if progress:
        progress(0, 1)
    import nautilus_trader

    if nautilus_trader.__version__ != TESTED_VERSION:
        raise ValueError(f"Nautilus runtime requires tested version {TESTED_VERSION}")
    import pandas as pd
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models.fee import FeeModel
    from nautilus_trader.config import (
        BacktestEngineConfig,
        LoggingConfig,
        RiskEngineConfig,
        StrategyConfig,
    )
    from nautilus_trader.core.data import Data
    from nautilus_trader.model.data import CustomData, DataType, QuoteTick
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
    from nautilus_trader.model.identifiers import ClientId, InstrumentId, PositionId, Symbol, Venue
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    market_windows = _market_windows(strategies, lots, scheduled, snapshot)
    instants = [stamp(key) for key in timeline] if minute else None
    day_index = {day: i for i, day in enumerate(snapshot["sessions"])}
    currency, venue, client = Currency.from_str("INR"), Venue("NSE"), ClientId("RESEARCH")
    metadata, instruments = snapshot.get("instruments", {}), {}
    for symbol in sorted(
        {signal["symbol"] for col, (_, signal) in enumerate(lots) if scheduled[col][0] >= 0}
    ):
        item = metadata[symbol]
        instruments[symbol] = Equity(
            instrument_id=InstrumentId(Symbol(symbol), venue),
            raw_symbol=Symbol(symbol),
            currency=currency,
            price_precision=item["price_precision"],
            price_increment=Price.from_str(str(item["tick_size"])),
            lot_size=Quantity.from_int(1),
            isin=item.get("isin"),
            ts_event=0,
            ts_init=0,
        )
    state = {"i": 0, "phase": "open", "opening_equity": float(capital)}
    lot_state = [
        {
            "position_id": PositionId(f"research-lot-{i}"),
            "orders": [],
            "entry_i": None,
            "entry_order": None,
            "exit_order": None,
            "budget": None,
            "reason": None,
            "outcome": None,
            "timing": None,
        }
        for i in range(len(lots))
    ]
    entries = {}
    for col, (entry, _) in enumerate(scheduled):
        if entry >= 0:
            entries.setdefault(entry, []).append(col)
    values, cash, counts, strategy_pnls, strategy_counts = [], [], [], [], []
    fee_rates = {}
    native_fills = []

    class StrategyFees(FeeModel):
        def get_commission(self, order, fill_qty, fill_px, instrument):
            return Money(
                instrument.notional_value(fill_qty, fill_px).as_decimal()
                * fee_rates[str(order.strategy_id)],
                currency,
            )

    class Phase(Data):
        def __init__(self, i, phase, after, timestamp):
            self.i, self.phase, self.after, self._timestamp = i, phase, after, timestamp

        @property
        def ts_event(self):
            return self._timestamp

        @property
        def ts_init(self):
            return self._timestamp

    def mark(col, field):
        symbol = lots[col][1]["symbol"]
        if timeline[state["i"]] not in market_windows.get(symbol, set()):
            raise ValueError(f"Nautilus position crossed its checked price window: {symbol}")
        bar = snapshot["bars"].get(symbol, {}).get(timeline[state["i"]])
        if bar is None:
            raise ValueError(
                f"Open Nautilus position has no price: {symbol} {timeline[state['i']]}"
            )
        return instruments[symbol].make_price(Decimal(str(bar[field])))

    def position(col):
        return engine.cache.position(lot_state[col]["position_id"])

    def open_position(col):
        found = position(col)
        return found if found is not None and found.is_open else None

    def due(col, closing):
        cfg = strategies[lots[col][0]]["config"]
        entered, i = lot_state[col]["entry_i"], state["i"]
        if not minute:
            return closing and i - entered >= cfg["hold_sessions"]
        now = instants[i]
        if not closing:
            return (
                cfg.get("hold_minutes") is not None
                and now >= instants[entered] + timedelta(minutes=cfg["hold_minutes"])
            ) or bool(cfg.get("exit_time") and now.strftime("%H:%M") >= cfg["exit_time"])
        return now + timedelta(minutes=1) == bounds(snapshot, timeline[i][:10])[1] and (
            cfg.get("trade_horizon") == "intraday"
            or day_index[timeline[i][:10]] - day_index[timeline[entered][:10]]
            >= cfg["hold_sessions"]
        )

    class ScannerStrategy(Strategy):
        def __init__(self, owner):
            self.owner = owner
            super().__init__(
                StrategyConfig(
                    strategy_id=f"Research{owner}", order_id_tag=f"{owner:03}", oms_type="HEDGING"
                )
            )
            self.own_cols = [col for col, (strategy_i, _) in enumerate(lots) if strategy_i == owner]
            self.orders_by_id = {}

        def remember(self, col, order, outcome):
            self.orders_by_id[str(order.client_order_id)] = (col, outcome)
            lot_state[col]["orders"].append(order)

        def enter(self, col):
            cfg, allocation = (
                strategies[self.owner]["config"],
                strategies[self.owner]["allocation_pct"],
            )
            cap = state["opening_equity"] * allocation / 100
            budget = cap * cfg["order_size_pct"] / 100
            lot_state[col]["budget"] = budget
            deployed = sum(
                float(open_position(j).notional_value(mark(j, "open")))
                for j in self.own_cols
                if open_position(j)
            )
            account_cash = float(self.portfolio.account(venue).balance_total(currency))
            raw = float(mark(col, "open"))
            quantity = math.floor((budget + 1e-9) / (raw * (1 + cfg["cost_bps"] / 10000)))
            if quantity > MODEL_QUOTE_SIZE:
                raise ValueError("Requested Nautilus order exceeds the bounded model liquidity")
            if deployed + budget > cap + 1e-8:
                lot_state[col]["reason"] = "Strategy allocation is already in use"
            elif budget > account_cash + 1e-8:
                lot_state[col]["reason"] = (
                    "Insufficient opening cash for the selected position budget"
                )
            elif quantity < 1:
                lot_state[col]["reason"] = (
                    "Position budget cannot fund one whole share including costs"
                )
            if lot_state[col]["reason"]:
                return
            instrument = instruments[lots[col][1]["symbol"]]
            order = self.order_factory.market(
                instrument.id, OrderSide.BUY, Quantity.from_int(quantity)
            )
            self.remember(col, order, "entry")
            lot_state[col]["entry_order"] = order
            self.submit_order(order, position_id=lot_state[col]["position_id"])
            found = open_position(col)
            if not found:
                lot_state[col]["reason"] = (
                    f"Nautilus did not fill the opening order ({order.status_string()})"
                )
                return
            if float(found.quantity) != quantity:
                raise ValueError("Nautilus partially filled an order under the strict-fill policy")
            lot_state[col]["entry_i"] = state["i"]
            step = Decimal(str(metadata[lots[col][1]["symbol"]]["tick_size"]))
            base = Decimal(str(found.avg_px_open))
            stop = (base * (1 - Decimal(str(cfg["stop_pct"])) / 100) / step).to_integral_value(
                rounding=ROUND_FLOOR
            ) * step
            target = (base * (1 + Decimal(str(cfg["target_pct"])) / 100) / step).to_integral_value(
                rounding=ROUND_CEILING
            ) * step
            if stop <= 0:
                raise ValueError(
                    "Configured stop falls below the instrument's minimum price increment"
                )
            stop_order = self.order_factory.stop_market(
                instrument.id,
                OrderSide.SELL,
                found.quantity,
                instrument.make_price(stop),
                reduce_only=True,
            )
            target_order = self.order_factory.limit(
                instrument.id,
                OrderSide.SELL,
                found.quantity,
                instrument.make_price(target),
                reduce_only=True,
            )
            self.remember(col, stop_order, "stop")
            self.remember(col, target_order, "target")
            self.submit_order(stop_order, position_id=lot_state[col]["position_id"])
            self.submit_order(target_order, position_id=lot_state[col]["position_id"])
            if not stop_order.is_open or not target_order.is_open:
                raise ValueError("Nautilus did not accept the protective orders")

        def close_due(self, col, closing):
            found = open_position(col)
            if not found or not due(col, closing):
                return
            for order in lot_state[col]["orders"]:
                if order.is_open:
                    self.cancel_order(order)
            order = self.order_factory.market(
                found.instrument_id, OrderSide.SELL, found.quantity, reduce_only=True
            )
            self.remember(col, order, "hold")
            self.submit_order(order, position_id=found.id)

        def on_order_filled(self, event):
            col, outcome = self.orders_by_id[str(event.client_order_id)]
            native_fills.append(
                {
                    "strategy_id": strategies[self.owner]["id"],
                    "engine_column": col,
                    "native_strategy_id": str(event.strategy_id),
                    "position_id": str(event.position_id),
                    "order_id": str(event.client_order_id),
                    "side": event.order_side.name,
                    "price": float(event.last_px),
                    "quantity": float(event.last_qty),
                    "commission": float(event.commission),
                    "timestamp_ns": event.ts_event,
                }
            )
            if outcome != "entry":
                lot_state[col]["exit_order"] = self.cache.order(event.client_order_id)
                lot_state[col]["exit_i"] = state["i"]
                lot_state[col]["outcome"] = outcome
                lot_state[col]["timing"] = (
                    state["phase"] if state["phase"] in ("open", "close") else "intraday"
                )
                for order in lot_state[col]["orders"]:
                    if str(order.client_order_id) != str(event.client_order_id) and order.is_open:
                        self.cancel_order(order)

    class Coordinator(Strategy):
        def on_start(self):
            self.subscribe_data(DataType(Phase), client_id=client)

        def on_data(self, event):
            state["i"], state["phase"] = event.i, event.phase
            if not event.after:
                if event.phase == "open":
                    if progress:
                        progress(event.i, len(timeline))
                    state["opening_equity"] = float(
                        self.portfolio.account(venue).balance_total(currency)
                    ) + sum(
                        float(open_position(col).notional_value(mark(col, "open")))
                        for col in range(len(lots))
                        if open_position(col)
                    )
                return
            if event.phase == "open":
                for owner in owners:
                    for col in owner.own_cols:
                        owner.close_due(col, False)
                for col in entries.get(event.i, []):
                    owners[lots[col][0]].enter(col)
            elif event.phase == "close":
                for owner in owners:
                    for col in owner.own_cols:
                        owner.close_due(col, True)
                balance = float(self.portfolio.account(venue).balance_total(currency))
                marked, pnl, open_counts = 0.0, [0.0] * len(strategies), [0] * len(strategies)
                for col, (strategy_i, _) in enumerate(lots):
                    found = position(col)
                    if found:
                        if found.is_open:
                            price = mark(col, "close")
                            marked += float(found.notional_value(price))
                            pnl[strategy_i] += float(found.total_pnl(price))
                            open_counts[strategy_i] += 1
                        else:
                            pnl[strategy_i] += float(found.realized_pnl)
                value = balance + marked
                if not math.isclose(sum(pnl), value - capital, abs_tol=0.011, rel_tol=1e-10):
                    raise ValueError(
                        "Nautilus native strategy PnL does not reconcile to account equity"
                    )
                values.append(value)
                cash.append(balance)
                counts.append(sum(open_counts))
                strategy_pnls.append(pnl)
                strategy_counts.append(open_counts)

    engine = None
    try:
        engine = BacktestEngine(
            BacktestEngineConfig(
                logging=LoggingConfig(bypass_logging=True),
                run_analysis=False,
                risk_engine=RiskEngineConfig(
                    max_order_submit_rate="100000/00:00:01", max_order_modify_rate="100000/00:00:01"
                ),
            )
        )
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.CASH,
            base_currency=currency,
            starting_balances=[Money(capital, currency)],
            use_message_queue=False,
            fee_model=StrategyFees(),
            bar_execution=False,
        )
        for instrument in instruments.values():
            engine.add_instrument(instrument)
        owners = [ScannerStrategy(i) for i in range(len(strategies))]
        for owner in owners:
            fee_rates[str(owner.id)] = (
                Decimal(str(strategies[owner.owner]["config"]["cost_bps"])) / 10000
            )
            engine.add_strategy(owner)
        engine.add_strategy(
            Coordinator(StrategyConfig(strategy_id="Coordinator", order_id_tag="999"))
        )
        data = []
        for i, key in enumerate(timeline):
            base = pd.Timestamp(key if minute else f"{key}T09:15:00+05:30").value
            # Nanosecond offsets order modelled events, never observed tick times.
            for phase_i, phase in enumerate(("open", "low", "high", "close")):
                timestamp = base + phase_i * 10000
                data.append(CustomData(DataType(Phase), Phase(i, phase, False, timestamp)))
                for offset, (symbol, instrument) in enumerate(instruments.items(), 1):
                    bar = snapshot["bars"].get(symbol, {}).get(key)
                    if bar and key in market_windows[symbol]:
                        price = instrument.make_price(Decimal(str(bar[phase])))
                        size = Quantity.from_int(MODEL_QUOTE_SIZE)
                        data.append(
                            QuoteTick(
                                instrument.id,
                                price,
                                price,
                                size,
                                size,
                                timestamp + offset,
                                timestamp + offset,
                            )
                        )
                data.append(CustomData(DataType(Phase), Phase(i, phase, True, timestamp + 9999)))
        engine.add_data(data, client_id=client)
        engine.run()
        if progress:
            progress(len(timeline), len(timeline))
        ledger = []
        for col, (strategy_i, signal) in enumerate(lots):
            recorded, found = lot_state[col], position(col)
            row = {
                "strategy_id": strategies[strategy_i]["id"],
                "strategy_name": strategies[strategy_i]["name"],
                "symbol": signal["symbol"],
                "signal_date": signal["date"],
                "signal_timestamp": signal.get("timestamp"),
                "source_row": signal.get("row"),
                "trigger_modes": ["Bypass"],
                "engine_column": col,
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
            }
            if scheduled[col][1]:
                row.update(status="excluded", reason=scheduled[col][1])
            elif found is None and recorded["reason"]:
                row.update(
                    status="skipped", reason=recorded["reason"], requested_budget=recorded["budget"]
                )
            elif found:
                row.update(
                    entry_date=timeline[recorded["entry_i"]][:10],
                    entry_price=found.avg_px_open,
                    quantity=int(float(found.peak_qty)),
                    fees=sum(float(fee) for fee in found.commissions()),
                    requested_budget=recorded["budget"],
                    reason="Position open at snapshot end",
                )
                if minute:
                    row["entry_timestamp"] = timeline[recorded["entry_i"]]
                if found.is_closed:
                    row.update(
                        status="closed",
                        reason=f"{recorded['outcome']}; {recorded['timing']} exit",
                        exit_date=timeline[recorded["exit_i"]][:10],
                        exit_price=found.avg_px_close,
                        pnl=float(found.realized_pnl),
                        outcome=recorded["outcome"],
                        exit_timing=recorded["timing"],
                    )
                    if minute:
                        exit_time = stamp(timeline[recorded["exit_i"]])
                        row["exit_timestamp"] = (
                            exit_time + timedelta(minutes=1)
                            if recorded["timing"] == "close"
                            else exit_time
                        ).isoformat()
            ledger.append(row)
        curve = _curve(timeline, values, cash, counts, capital, minute)
        per_strategy = []
        for i, strategy in enumerate(strategies):
            initial = capital * strategy["allocation_pct"] / 100
            pnls = [point[i] for point in strategy_pnls]
            attributed = [initial + value for value in pnls]
            own_counts = [point[i] for point in strategy_counts]
            own_curve = _curve(
                timeline, attributed, [0] * len(timeline), own_counts, initial, minute
            )
            for j, point in enumerate(own_curve):
                point.pop("cash")  # Shared account does not allocate private cash balances.
                point.update(net_pnl=pnls[j], contribution_pct=pnls[j] / capital * 100)
            per_strategy.append(
                {
                    "id": strategy["id"],
                    "name": strategy["name"],
                    "allocation_pct": strategy["allocation_pct"],
                    "summary": _summary(
                        [r for r in ledger if r["strategy_id"] == strategy["id"]],
                        initial,
                        attributed,
                        own_curve,
                    ),
                    "equity_curve": own_curve,
                    "net_pnl": pnls[-1],
                    "contribution_pct": pnls[-1] / capital * 100,
                }
            )
        report = {
            "policy_version": POLICY_VERSION,
            "metric_basis": "minute_marked" if minute else "daily_marked",
            "config": {"initial_capital": float(capital)},
            "strategies": [
                {key: s[key] for key in ("id", "name", "allocation_pct", "config")}
                for s in strategies
            ],
            "summary": _summary(ledger, capital, values, curve),
            "equity_curve": curve,
            "ledger": ledger,
            "per_strategy": per_strategy,
            "coverage": coverage,
            "missing_marks": [],
            "execution": {
                "engine": "nautilus",
                "engine_version": TESTED_VERSION,
                "adapter_version": ADAPTER_VERSION,
                "simulation_api": "BacktestEngine",
                "shared_cash": True,
                "strategy_count": len(strategies),
                "interval": snapshot["provenance"]["interval"],
                "market_event_basis": "modelled_ohlc_open_low_high_close",
                "market_event_scope": "eligible_potential_holding_windows",
                "instrument_eligibility": snapshot.get("instrument_eligibility"),
                "attribution_basis": "native_position_pnl",
                "allocation_basis": "opening_account_equity_marked_cap",
            },
            "engine_records": {
                "orders": [order.to_dict() for order in engine.cache.orders()],
                "trades": [position.to_dict() for position in engine.cache.positions()],
                "fills": native_fills,
            },
            "limits": [
                "OHLC-derived zero-spread quotes model Open→Low→High→Close; they are not observed ticks.",
                "Nautilus owns stop/limit matching, positions, fees and one shared INR cash account; native fills can differ from VectorBT.",
                "Protective levels round outward to supplied instrument ticks. Native commission amounts round to INR currency precision.",
                "Complete potential holding windows are required; tails retain open positions.",
                "Long cash equities with supplied instrument metadata and zero configured slippage only.",
            ],
        }
        from research.analytics import build_analysis

        report["analysis"] = build_analysis(
            report, nautilus_engine=engine, venue=venue, currency=currency
        )
        return report
    finally:
        if engine is not None:
            engine.dispose()
