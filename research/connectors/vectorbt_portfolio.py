"""Joint scanner strategies translated into one native VectorBT cash account.

Strategy callbacks schedule orders, never account for fills. All state is scoped
to one bounded worker evaluation; no clients, threads, files or global caches.
"""

import math
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from research.data import validate_snapshot
from research.engine import validate_config as validate_scanner_config
from research.intraday import bounds, entry_open, stamp

ADAPTER_VERSION = "vectorbt-portfolio-adapter-v1"
POLICY_VERSION = "vectorbt-joint-causal-bars-v1"
EXECUTION_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v2"
EXECUTION_POLICY_VERSION = "vectorbt-joint-causal-bars-v2"
PRIORITY_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v3"
PRIORITY_POLICY_VERSION = "vectorbt-joint-causal-bars-v3"
TURNOVER_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v4"
TURNOVER_POLICY_VERSION = "vectorbt-joint-causal-bars-v4"
CAPACITY_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v5"
CAPACITY_POLICY_VERSION = "vectorbt-joint-causal-bars-v5"
MAX_MATRIX_CELLS = 500_000
MAX_SIGNALS = 25_000
MAX_STRATEGIES = 20

_DISCARD_TRIAL_ANALYSIS = ContextVar("discard_vectorbt_trial_analysis", default=False)


@contextmanager
def discard_trial_analysis():
    """Omit presentation objects only for reports the search will discard.

    Scientific account evidence and all account checks are still constructed.
    Context-local state prevents one worker's trial affecting another request;
    selected/final reports must be calculated outside this scope.
    """
    token = _DISCARD_TRIAL_ANALYSIS.set(True)
    try:
        yield
    finally:
        _DISCARD_TRIAL_ANALYSIS.reset(token)


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
    execution_aware = any("execution_policy" in row for row in strategies)
    shared_limits, shared_priority = None, None
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
        current = {**strategy, "config": cfg}
        if execution_aware:
            from research.execution_policy import normalize_policy

            if "execution_policy" not in strategy:
                raise ValueError(
                    "Execution-aware accounts require an explicit policy on every strategy"
                )
            current["execution_policy"] = normalize_policy(strategy["execution_policy"])
            limits = current["execution_policy"]["risk_limits"]
            if shared_limits is not None and shared_limits != limits:
                raise ValueError("All execution-aware strategies must share the same risk limits")
            shared_limits = limits
            priority = (
                current["execution_policy"]["version"],
                current["execution_policy"].get("entry_priority"),
                current["execution_policy"].get("capacity_mode"),
                current["execution_policy"].get("correlation_mode"),
                current["execution_policy"].get("allocation_mode"),
            )
            if shared_priority is not None and shared_priority != priority:
                raise ValueError(
                    "All strategies must share the same execution policy version and entry priority"
                )
            shared_priority = priority
        normalized.append(current)
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
    execution_aware = "execution_policy" in strategies[0]
    priority_aware = execution_aware and "entry_priority" in strategies[0]["execution_policy"]
    priority = strategies[0]["execution_policy"]["entry_priority"] if priority_aware else None
    limits = strategies[0]["execution_policy"]["risk_limits"] if execution_aware else None
    turnover_aware = execution_aware and "max_daily_buy_turnover_pct" in limits
    account_policy = strategies[0].get("execution_policy", {})
    capacity_aware = account_policy.get("capacity_mode") == "prior20_broker_reported"
    correlation_aware = account_policy.get("correlation_mode") == "prior60_positive_080"
    allocation_aware = account_policy.get("allocation_mode") == "prior60_cluster_inverse_vol"
    # At most one entry per signal: local fill accounting is bounded by MAX_SIGNALS.
    participation = {} if capacity_aware else None
    # Evaluation-local and bounded by snapshot dates; no retained account state.
    turnover_days = [] if turnover_aware else None
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
    initial_stops = np.full(count, np.nan) if execution_aware else None
    initial_targets = np.full(count, np.nan) if execution_aware else None
    execution_details = [None] * count if execution_aware else None
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
        target_pct = cfg["target_pct"] / 100
        if execution_aware:
            stop_pct = (entry_price[col] - initial_stops[col]) / entry_price[col]
            target_pct = (initial_targets[col] - entry_price[col]) / entry_price[col]
        if cfg["trailing_enabled"] and high_water[col] * (
            1 - cfg["trailing_pct"] / 100
        ) > anchor * (1 - stop_pct):
            anchor, stop_pct, reason = high_water[col], cfg["trailing_pct"] / 100, "trailing_stop"
        if opening_only:
            lo = hi = op
        stop = nb.get_stop_price_nb(position, anchor, stop_pct, op, lo, hi, True)
        target = nb.get_stop_price_nb(position, entry_price[col], target_pct, op, lo, hi, False)
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

    def protection(col, fill):
        strategy_i, signal = lots[col]
        strategy = strategies[strategy_i]
        cfg, policy = strategy["config"], strategy["execution_policy"]
        if policy["exit_mode"] == "atr":
            from research.execution_features import validate_entry_feature

            entry = scheduled[col][0]
            day = timeline[entry][:10]
            decision_at = timeline[entry] if minute else bounds(snapshot, day)[0].isoformat()
            feature = validate_entry_feature(
                signal.get("execution_atr"),
                symbol=signal["symbol"],
                entry_session=day,
                decision_at=decision_at,
            )
            stop = fill - feature["value"] * policy["atr_stop_multiple"]
            target = fill + feature["value"] * policy["atr_target_multiple"]
        else:
            feature = None
            stop, target = fill * (1 - cfg["stop_pct"] / 100), fill * (1 + cfg["target_pct"] / 100)
        if (
            not all(math.isfinite(value) for value in (stop, target))
            or not 0 < stop < fill < target
        ):
            raise ValueError(
                "Execution protection requires a positive stop below the fill and target above it"
            )
        return float(stop), float(target), feature

    def remaining_stop_risk(c, col, i):
        cfg = strategies[lots[col][0]]["config"]
        mark = float(bar_at(col, i)["open"])
        stop = initial_stops[col]
        if cfg["trailing_enabled"]:
            stop = max(stop, high_water[col] * (1 - cfg["trailing_pct"] / 100))
        proceeds = stop * (1 - cfg["slippage_bps"] / 10000) * (1 - cfg["cost_bps"] / 10000)
        return c.last_position[col] * max(0.0, mark - proceeds)

    def constrained_entry(c, state, col, strategy_i, cfg, raw, budget, fee, slip):
        strategy, signal = strategies[strategy_i], lots[col][1]
        fill, cash_per_share = raw * (1 + slip), raw * (1 + slip) * (1 + fee)
        requested = math.floor((budget + 1e-9) / cash_per_share)
        stop, target, feature = protection(col, fill)
        loss = cash_per_share - stop * (1 - slip) * (1 - fee)
        if not math.isfinite(loss) or loss <= 0:
            raise ValueError("Execution loss to stop must be positive and finite")
        held = [j for j in range(count) if c.last_position[j] > 0]
        gross = sum(c.last_position[j] * float(bar_at(j, c.i)["open"]) for j in held)
        # An earlier same-opening fill can replace its native valuation with
        # the slipped fill price. Mark every existing lot at the same observed
        # opening as gross/symbol risk: friction cannot become fictitious equity
        # available to the next order.
        equity = float(c.last_cash[c.group] + gross)
        symbol = sum(
            c.last_position[j] * float(bar_at(j, c.i)["open"])
            for j in held
            if lots[j][1]["symbol"] == signal["symbol"]
        )
        deployed = sum(
            c.last_position[j] * float(bar_at(j, c.i)["open"])
            for j in held
            if lots[j][0] == strategy_i
        )
        stop_risk = sum(remaining_stop_risk(c, j, c.i) for j in held)
        friction = cash_per_share - raw

        # Include entry friction in the prospective equity denominator. Limits
        # apply after the contemplated fill, not to a pre-fee inflated account.
        def exposure_capacity(fraction, existing):
            return max(0.0, (equity * fraction - existing) / (raw + fraction * friction))

        capacities = {
            "requested_cash_budget": requested,
            "shared_cash": max(0.0, c.last_cash[c.group] / cash_per_share),
            "strategy_allocation": exposure_capacity(strategy["allocation_pct"] / 100, deployed),
            "gross_exposure": exposure_capacity(limits["max_gross_exposure_pct"] / 100, gross),
            "symbol_exposure": exposure_capacity(limits["max_symbol_exposure_pct"] / 100, symbol),
            "trade_stop_risk": max(
                0.0,
                equity
                * limits["max_trade_risk_pct"]
                / 100
                / (loss + limits["max_trade_risk_pct"] / 100 * friction),
            ),
            "total_stop_risk": max(
                0.0,
                (equity * limits["max_total_stop_risk_pct"] / 100 - stop_risk)
                / (loss + limits["max_total_stop_risk_pct"] / 100 * friction),
            ),
            "open_positions": requested if len(held) < limits["max_open_positions"] else 0,
        }
        if turnover_aware:
            daily = turnover_days[-1]
            capacities["daily_buy_turnover"] = (
                max(0.0, daily["buy_budget"] - daily["buy_notional"]) / fill
            )
        if capacity_aware or correlation_aware or allocation_aware:
            entry_day = timeline[c.i][:10]
            decision_at = timeline[c.i] if minute else bounds(snapshot, entry_day)[0].isoformat()
        capacity_feature, correlation_feature, capacity_receipt, correlated = None, None, None, None
        if capacity_aware:
            from research.portfolio_execution_features import validate_capacity_entry

            capacity_feature = validate_capacity_entry(
                signal.get("entry_capacity"),
                symbol=signal["symbol"],
                entry_session=entry_day,
                decision_at=decision_at,
            )
            key = (entry_day, signal["symbol"])
            daily_shares = (
                capacity_feature["mean_daily_shares"]
                * limits["max_daily_volume_participation_pct"]
                / 100
            )
            capacity_receipt = participation.get(key)
            if capacity_receipt is not None and (
                capacity_receipt["mean_daily_shares"] != capacity_feature["mean_daily_shares"]
                or capacity_receipt["source_id"] != capacity_feature["source_id"]
            ):
                raise ValueError(
                    "Capacity observations disagree for the same symbol and entry session"
                )
            bought = capacity_receipt["bought_shares"] if capacity_receipt else 0
            capacities["daily_volume_participation"] = max(0.0, daily_shares - bought)
        if correlation_aware:
            from research.portfolio_execution_features import validate_correlation_entry

            correlation_feature = validate_correlation_entry(
                signal.get("entry_correlation"),
                symbol=signal["symbol"],
                entry_session=entry_day,
                decision_at=decision_at,
                required_symbols=sorted({lots[j][1]["symbol"] for j in held}),
            )
            related = correlation_feature["correlations"]
            correlated = sum(
                c.last_position[j] * float(bar_at(j, c.i)["open"])
                for j in held
                if lots[j][1]["symbol"] == signal["symbol"] or related[lots[j][1]["symbol"]] >= 0.8
            )
            capacities["correlated_exposure"] = exposure_capacity(
                limits["max_correlated_exposure_pct"] / 100,
                correlated,
            )
        allocation_feature = None
        if allocation_aware:
            from research.portfolio_allocation import validate as validate_allocation

            allocation_feature = validate_allocation(
                signal.get("entry_allocation"),
                symbol=signal["symbol"],
                entry_session=entry_day,
                decision_at=decision_at,
                required_symbols=sorted({lots[j][1]["symbol"] for j in held}),
            )
            cluster = next(
                group for group in allocation_feature["clusters"] if signal["symbol"] in group
            )
            cluster_before = sum(
                c.last_position[j] * float(bar_at(j, c.i)["open"])
                for j in held
                if lots[j][1]["symbol"] in cluster
            )
            gross_fraction = limits["max_gross_exposure_pct"] / 100
            weight = allocation_feature["weights"][signal["symbol"]]
            cluster_fraction = gross_fraction / len(allocation_feature["clusters"])
            capacities["portfolio_symbol_allocation"] = exposure_capacity(
                gross_fraction * weight, symbol
            )
            capacities["portfolio_cluster_allocation"] = exposure_capacity(
                cluster_fraction, cluster_before
            )
        policy = strategy["execution_policy"]
        risk_target = None
        if policy["sizing"] == "stop_risk":
            risk_target = limits["max_trade_risk_pct"] / 100 * policy["risk_budget_fraction"]
            capacities["risk_sizing_target"] = max(
                0.0, equity * risk_target / (loss + risk_target * friction)
            )
        quantity = max(0, math.floor(min(capacities.values()) + 1e-9)) if equity > 0 else 0
        constraints = [
            name for name, capacity in capacities.items() if math.floor(capacity + 1e-9) < requested
        ]
        execution_details[col] = {
            **(execution_details[col] or {}),
            "exit_mode": strategy["execution_policy"]["exit_mode"],
            "sizing": strategy["execution_policy"]["sizing"],
            "risk_budget_fraction": policy.get("risk_budget_fraction"),
            "target_stop_risk_pct": risk_target * 100 if risk_target is not None else None,
            "atr": dict(feature) if feature else None,
            "initial_stop": stop,
            "initial_target": target,
            "initial_risk_per_share": loss,
            "initial_stop_risk": quantity * loss,
            "requested_quantity": requested,
            "filled_quantity": 0,
            "clipped": quantity < requested,
            "constraints": constraints,
            "opening_equity": equity,
            "gross_before": gross,
            "symbol_before": symbol,
            "open_positions_before": len(held),
            "total_stop_risk_before": stop_risk,
            "accepted_cash_budget": quantity * cash_per_share,
        }
        if turnover_aware:
            execution_details[col].update(
                daily_opening_equity=daily["opening_equity"],
                daily_buy_budget=daily["buy_budget"],
                daily_buy_notional_before=daily["buy_notional"],
                daily_buy_capacity_shares=capacities["daily_buy_turnover"],
            )
        if capacity_aware:
            execution_details[col].update(
                capacity_feature_id=capacity_feature["id"],
                capacity_mean_daily_shares=capacity_feature["mean_daily_shares"],
                daily_participation_share_budget=daily_shares,
                daily_bought_shares_before=bought,
                remaining_participation_shares=capacities["daily_volume_participation"],
            )
        if correlation_aware:
            execution_details[col].update(
                correlation_feature_id=correlation_feature["id"],
                correlated_exposure_before=correlated,
                correlated_capacity_shares=capacities["correlated_exposure"],
            )
        if allocation_aware:
            execution_details[col].update(
                allocation_feature_id=allocation_feature["id"],
                allocation_weight=weight,
                allocation_cluster=cluster,
                allocation_cluster_before=cluster_before,
                allocation_symbol_capacity_shares=capacities["portfolio_symbol_allocation"],
                allocation_cluster_capacity_shares=capacities["portfolio_cluster_allocation"],
            )
        if quantity < 1:
            rejected[col] = "Execution limits cannot admit one whole share: " + ", ".join(
                constraints or ["requested_cash_budget"]
            )
        return quantity

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
        if turnover_aware and (
            not turnover_days or turnover_days[-1]["date"] != timeline[c.i][:10]
        ):
            # Freeze at the first recorded opening of the date, before any
            # opening exits or buys. Later minute marks/proceeds never reset it.
            if not math.isfinite(equity):
                raise ValueError("Daily turnover requires a finite opening account equity")
            buy_budget = max(0.0, equity) * (limits["max_daily_buy_turnover_pct"] / 100)
            if not math.isfinite(buy_budget):
                raise ValueError("Daily turnover requires a finite opening buy budget")
            turnover_days.append(
                {
                    "date": timeline[c.i][:10],
                    "opening_at": timeline[c.i],
                    "opening_equity": equity,
                    "buy_budget": buy_budget,
                    "buy_notional": 0.0,
                    "buy_fills": 0,
                }
            )
        entry_cols = entries.get(c.i, [])
        if priority_aware:
            scores = {}
            for col in entry_cols:
                cfg = strategies[lots[col][0]]["config"]
                raw = float(bar_at(col, c.i)["open"])
                fee, slip = cfg["cost_bps"] / 10000, cfg["slippage_bps"] / 10000
                cash_cost = raw * (1 + slip) * (1 + fee)
                try:
                    stop, _, _ = protection(col, raw * (1 + slip))
                    loss = cash_cost - stop * (1 - slip) * (1 - fee)
                    score = loss / cash_cost
                    if not math.isfinite(score) or score <= 0:
                        raise ValueError("Execution loss to stop must be positive and finite")
                    scores[col] = score
                except ValueError as error:
                    scores[col] = None
                    rejected[col] = str(error)
            if priority == "reversed_source":
                entry_cols = list(reversed(entry_cols))
            elif priority == "lower_stop_risk":
                entry_cols = sorted(
                    entry_cols,
                    key=lambda col: (scores[col] if scores[col] is not None else math.inf, col),
                )
            for rank, col in enumerate(entry_cols, 1):
                execution_details[col] = {
                    "entry_priority": priority,
                    "entry_rank": rank,
                    "entry_batch_size": len(entry_cols),
                    "stop_risk_fraction": scores[col],
                }
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
            if priority_aware and rejected[col]:
                return col, NoOrder
            cap = state["equity"] * strategy["allocation_pct"] / 100
            budget = cap * cfg["order_size_pct"] / 100
            budgets[col] = float(budget)
            deployed = sum(
                c.last_position[j] * float(bar_at(j, c.i)["open"])
                for j in range(count)
                if lots[j][0] == strategy_i and c.last_position[j] > 0
            )
            raw = float(bar_at(col, c.i)["open"])
            if execution_aware:
                try:
                    quantity = constrained_entry(
                        c, state, col, strategy_i, cfg, raw, budget, fee, slip
                    )
                except ValueError as error:
                    rejected[col] = str(error)
                    return col, NoOrder
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
            if capacity_aware:
                # Debit confirmed native buys only; exits never replenish capacity.
                signal = lots[c.col][1]
                key = (timeline[c.i][:10], signal["symbol"])
                feature = signal["entry_capacity"]
                receipt = participation.setdefault(
                    key,
                    {
                        "date": key[0],
                        "symbol": key[1],
                        "mean_daily_shares": feature["mean_daily_shares"],
                        "source_id": feature["source_id"],
                        "share_budget": feature["mean_daily_shares"]
                        * limits["max_daily_volume_participation_pct"]
                        / 100,
                        "bought_shares": 0,
                        "buy_fills": 0,
                    },
                )
                receipt["bought_shares"] += int(c.order_result.size)
                receipt["buy_fills"] += 1
            if turnover_aware:
                # Debit only confirmed native fills, using slipped fill price
                # and excluding fees. Rejections and sales consume no budget.
                turnover_days[-1]["buy_notional"] += float(c.order_result.size) * float(
                    c.order_result.price
                )
                turnover_days[-1]["buy_fills"] += 1
            filled_at[c.col], entry_price[c.col], high_water[c.col] = (
                c.i,
                c.order_result.price,
                c.order_result.price,
            )
            if execution_aware:
                stop, target, _ = protection(c.col, c.order_result.price)
                initial_stops[c.col], initial_targets[c.col] = stop, target
                execution_details[c.col]["initial_stop"] = stop
                execution_details[c.col]["initial_target"] = target
                execution_details[c.col]["filled_quantity"] = int(c.order_result.size)

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
        execution_details=execution_details,
        turnover_days=turnover_days,
        participation=participation,
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
    *,
    execution_details=None,
    turnover_days=None,
    participation=None,
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
        if execution_details is not None:
            row["execution_details"] = execution_details[col]
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

    report = {
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
    if _DISCARD_TRIAL_ANALYSIS.get():
        from research.analytics import validate_analysis_evidence

        validate_analysis_evidence(report)
    else:
        from research.analytics import build_analysis

        report["analysis"] = build_analysis(report, portfolio=pf)
    if execution_details is not None:
        from copy import deepcopy

        report["policy_version"] = EXECUTION_POLICY_VERSION
        for row, strategy in zip(report["strategies"], strategies, strict=True):
            row["execution_policy"] = deepcopy(strategy["execution_policy"])
        report["execution"].update(
            adapter_version=EXECUTION_ADAPTER_VERSION,
            risk_limits=deepcopy(strategies[0]["execution_policy"]["risk_limits"]),
            risk_basis="Entry-time marked account after entry friction; current protective-stop loss includes projected exit fees/slippage; new-trade loss includes both sides",
            sizing_policy="Allocation starts with the original cash request; stop-risk targets its explicit fraction of the user trade-risk cap. Both clip to original cash budget and every hard cap",
        )
        report["limits"].extend(
            [
                "Risk controls clip new whole-share entries; appreciation can move existing exposure above a cap without forcing sales.",
                "Stop-risk is planned loss at the protective stop including costs; price gaps can exceed it and no outcome is guaranteed.",
                "ATR exits use100 completed prior daily OHLC observations, even when the account executes on minute bars.",
            ]
        )
        if "entry_priority" in strategies[0]["execution_policy"]:
            from research.execution_activity import build as build_activity

            report["policy_version"] = PRIORITY_POLICY_VERSION
            report["execution"].update(
                adapter_version=PRIORITY_ADAPTER_VERSION,
                entry_priority=strategies[0]["execution_policy"]["entry_priority"],
                activity=build_activity(report["engine_records"]["orders"], timeline, capital),
            )
            report["limits"][1] = (
                "Opening exits precede entries under the recorded same-opening priority; ties use original source order. Within-bar and closing proceeds cannot retroactively fund opening entries."
            )
        if turnover_days is not None:
            report["policy_version"] = TURNOVER_POLICY_VERSION
            report["execution"].update(
                adapter_version=TURNOVER_ADAPTER_VERSION,
                turnover={
                    "version": "research-daily-buy-turnover-v1",
                    "max_daily_buy_turnover_pct": strategies[0]["execution_policy"]["risk_limits"][
                        "max_daily_buy_turnover_pct"
                    ],
                    "basis": "Actual filled BUY size times slipped price, excluding fees, divided by equity frozen at the first recorded opening of each trading date. Carried positions use that opening price. Sales never consume or replenish the budget; protective exits remain unrestricted. This is a trading activity bound, not liquidity or market-impact capacity.",
                    "daily": [
                        {
                            **day,
                            "remaining_buy_budget": max(
                                0.0, day["buy_budget"] - day["buy_notional"]
                            ),
                            "buy_turnover_pct": day["buy_notional"] / day["opening_equity"] * 100
                            if day["opening_equity"] > 0
                            else None,
                        }
                        for day in turnover_days
                    ],
                },
            )
            report["limits"].append(
                "Daily buy-turnover clips new whole-share entries against a frozen first-opening equity budget; actual sales do not replenish it and protective exits are never blocked."
            )
        if strategies[0]["execution_policy"].get("version") in (
            "research-execution-aware-v4",
            "research-execution-aware-v5",
        ):
            policy = strategies[0]["execution_policy"]
            report["policy_version"] = CAPACITY_POLICY_VERSION
            report["execution"].update(
                adapter_version=CAPACITY_ADAPTER_VERSION,
                capacity_mode=policy["capacity_mode"],
                correlation_mode=policy["correlation_mode"],
            )
            if participation is not None:
                report["execution"]["capacity"] = {
                    "version": "research-daily-share-capacity-v1",
                    "max_daily_volume_participation_pct": policy["risk_limits"][
                        "max_daily_volume_participation_pct"
                    ],
                    "basis": "Prior20 completed broker-reported daily quantities in historical coordinates; actual filled buys consume one symbol/session budget across strategies. Sales never replenish it; exits are unrestricted. This modeled cap does not establish corporate-action comparable quantities, executable liquidity or market impact.",
                    "daily_symbols": [
                        {
                            **row,
                            "remaining_shares": max(
                                0.0, row["share_budget"] - row["bought_shares"]
                            ),
                        }
                        for _, row in sorted(participation.items())
                    ],
                }
                report["limits"].append(
                    "Daily participation clips new whole-share buys using observed prior20 broker-reported daily quantities. Historical quantity units lack a corporate-action comparability attestation; this is an explicit historical-coordinate model, not qualified present-share liquidity or an execution guarantee. Exits remain unrestricted."
                )
            if policy["correlation_mode"] != "disabled":
                report["execution"]["correlation"] = {
                    "version": "research-positive-correlation-cap-v1",
                    "observations": 60,
                    "threshold": 0.8,
                    "max_correlated_exposure_pct": policy["risk_limits"][
                        "max_correlated_exposure_pct"
                    ],
                    "basis": "Prior60 completed aligned daily returns, fixed positive Pearson correlation >=0.8; existing same-symbol and directly related positions marked at the observed entry opening, including entry friction in prospective equity. No transitive grouping or negative-correlation credit.",
                }
                report["limits"].append(
                    "Correlation controls clip new entries against directly correlated existing exposure. They do not rebalance held positions or perform covariance optimization; relationships can change after entry."
                )
    if strategies[0].get("execution_policy", {}).get("version") == "research-execution-aware-v5":
        report["policy_version"] = "vectorbt-joint-causal-bars-v6"
        report["execution"].update(
            adapter_version="vectorbt-portfolio-adapter-v6",
            allocation_mode=strategies[0]["execution_policy"]["allocation_mode"],
            allocation={
                "version": "research-cluster-allocation-v1",
                "basis": "Prior60 aligned completed returns; 10% diagonal covariance shrinkage; positive regularized rho>=0.8 connected clusters; equal cluster budgets and inverse-volatility symbol weights. Fixed source universe, unavailable series excluded explicitly. Entry caps only; no forced rebalancing or negative-correlation credit.",
            },
        )
    return report
