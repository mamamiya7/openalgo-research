"""Typed joint-account policy for VectorBT's compiled flexible simulator.

VectorBT remains the fill and account engine.  This module only translates the
existing policy into numeric callbacks and materialises its receipts afterwards.
No fastmath, parallel execution, global account state or policy approximations.
"""

import math
from collections import namedtuple
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from functools import partial

import numpy as np
from numba import njit, objmode
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import Direction, NoOrder, OrderStatus

_PROGRESS = ContextVar("compiled_portfolio_progress", default=None)


def _check_progress(i, total):
    callback = _PROGRESS.get()
    if callback is not None:
        callback(i, total)


@contextmanager
def progress_checks(callback):
    token = _PROGRESS.set(callback)
    try:
        yield
    finally:
        _PROGRESS.reset(token)


Inputs = namedtuple(
    "Inputs",
    "bars owners symbols cfg entry open_due close_due flags limits features "
    "correlations clusters allocation_weights correlation_symbols cluster_symbols capacity_key capacity_source feature_errors day",
)
State = namedtuple(
    "State",
    "filled price high stops targets rejected native budget raw_in raw_out "
    "outcome timing details capacities ranks scores actions action_type "
    "action_price action_outcome action_timing meta turnover participation active",
)
CAP_NAMES = (
    "requested_cash_budget",
    "shared_cash",
    "strategy_allocation",
    "gross_exposure",
    "symbol_exposure",
    "trade_stop_risk",
    "total_stop_risk",
    "open_positions",
    "daily_buy_turnover",
    "daily_volume_participation",
    "correlated_exposure",
    "portfolio_symbol_allocation",
    "portfolio_cluster_allocation",
    "risk_sizing_target",
)
LIMIT_NAMES = (
    "max_gross_exposure_pct",
    "max_symbol_exposure_pct",
    "max_trade_risk_pct",
    "max_total_stop_risk_pct",
    "max_open_positions",
    "max_daily_buy_turnover_pct",
    "max_daily_volume_participation_pct",
    "max_correlated_exposure_pct",
)
# Config columns: allocation, order, stop, target, trailing flag/percent, fee,
# slip, ATR stop/target multiple, risk sizing flag, risk budget fraction.
# Feature columns: ATR, mean daily shares, allocation weight, cluster count.
# Flags: execution, priority (0:none,1:source,2:reverse,3:lower-risk), turnover,
# capacity, correlation, allocation, minute, progress.
# Rejections 1..7 are native/static policy failures; feature errors start at100.
ERRORS = {
    1: "Strategy allocation is already in use",
    2: "Insufficient opening cash for the selected position budget",
    3: "Position budget cannot fund one whole share including costs",
    4: "VectorBT did not fill the opening order",
    5: "Capacity observations disagree for the same symbol and entry session",
    6: "correlation_unavailable: a held symbol lacks aligned prior returns",
    7: "allocation_unavailable: a new or held symbol lacks aligned prior returns",
    8: "Execution protection requires a positive stop below the fill and target above it",
    9: "Execution loss to stop must be positive and finite",
    10: "limits",
}


@njit(cache=True)
def _protection(p, col, fill):
    cfg = p.cfg[col]
    if not np.isnan(p.features[col, 0]):
        stop = fill - p.features[col, 0] * cfg[8]
        target = fill + p.features[col, 0] * cfg[9]
    else:
        stop = fill * (1 - cfg[2] / 100)
        target = fill * (1 + cfg[3] / 100)
    return stop, target


@njit(cache=True)
def _risk(c, p, s, col, opening):
    cfg = p.cfg[col]
    op, hi, lo, close = p.bars[c.i, p.symbols[col]]
    anchor, stop_pct, reason = s.price[col], cfg[2] / 100, 1
    target_pct = cfg[3] / 100
    if p.flags[0]:
        stop_pct = (s.price[col] - s.stops[col]) / s.price[col]
        target_pct = (s.targets[col] - s.price[col]) / s.price[col]
    if cfg[4] and s.high[col] * (1 - cfg[5] / 100) > anchor * (1 - stop_pct):
        anchor, stop_pct, reason = s.high[col], cfg[5] / 100, 2
    if opening:
        lo = op
        hi = op
    stop = nb.get_stop_price_nb(c.last_position[col], anchor, stop_pct, op, lo, hi, True)
    target = nb.get_stop_price_nb(c.last_position[col], s.price[col], target_pct, op, lo, hi, False)
    timing = 1 if opening else 2
    if not np.isnan(stop):
        return stop, reason, timing
    if not np.isnan(target):
        return target, 3, timing
    if opening and c.i >= p.open_due[col, 0]:
        return op, p.open_due[col, 1], 1
    if not opening and c.i >= p.close_due[col]:
        return close, 4, 3
    return np.nan, 0, 0


@njit(cache=True)
def before_bar(c, p, s):
    if p.flags[7]:
        with objmode():
            _check_progress(c.i, p.bars.shape[0])
    n, action_count = len(p.entry), 0
    held = np.zeros(n, dtype=np.bool_)
    entering = np.zeros(n, dtype=np.bool_)
    for col in range(n):
        op = p.bars[c.i, p.symbols[col], 0]
        if not np.isnan(op):
            c.last_val_price[col] = op
        if c.last_position[col] > 0:
            if np.isnan(op):
                raise ValueError("An open portfolio position has no current price")
            held[col] = True
            raw, outcome, timing = _risk(c, p, s, col, True)
            if outcome:
                s.actions[action_count] = col
                s.action_type[action_count] = 0
                s.action_price[action_count] = raw
                s.action_outcome[action_count] = outcome
                s.action_timing[action_count] = timing
                action_count += 1
        entering[col] = p.entry[col] == c.i
    equity = nb.get_group_value_nb(0, n, c.last_cash[c.group], c.last_position, c.last_val_price)
    s.meta[0] = action_count
    s.meta[1] = equity
    day = p.day[c.i]
    if p.flags[2] and np.isnan(s.turnover[day, 0]):
        if not np.isfinite(equity):
            raise ValueError("Daily turnover requires a finite opening account equity")
        budget = max(0.0, equity) * (p.limits[5] / 100)
        if not np.isfinite(budget):
            raise ValueError("Daily turnover requires a finite opening buy budget")
        s.turnover[day, 0] = equity
        s.turnover[day, 1] = budget
        s.turnover[day, 2] = 0
        s.turnover[day, 3] = 0
        s.turnover[day, 4] = c.i
    entry_cols = np.flatnonzero(entering)
    if p.flags[1]:
        for col in entry_cols:
            cfg = p.cfg[col]
            raw = p.bars[c.i, p.symbols[col], 0]
            fill = raw * (1 + cfg[7])
            cash_cost = fill * (1 + cfg[6])
            stop, target = _protection(p, col, fill)
            score = (cash_cost - stop * (1 - cfg[7]) * (1 - cfg[6])) / cash_cost
            error = p.feature_errors[col, 0]
            if not error and (
                not np.isfinite(stop) or not np.isfinite(target) or not 0 < stop < fill < target
            ):
                error = 8
            if not error and (not np.isfinite(score) or score <= 0):
                error = 9
            if error:
                s.rejected[col] = error
                s.scores[col] = np.nan
            else:
                s.scores[col] = score
        if p.flags[1] == 2:
            entry_cols = entry_cols[::-1]
        elif p.flags[1] == 3:
            # Stable insertion order: ties always retain source column order.
            for k in range(1, len(entry_cols)):
                value = entry_cols[k]
                score = s.scores[value] if np.isfinite(s.scores[value]) else np.inf
                j = k - 1
                while j >= 0:
                    previous = s.scores[entry_cols[j]]
                    if np.isnan(previous):
                        previous = np.inf
                    if previous <= score:
                        break
                    entry_cols[j + 1] = entry_cols[j]
                    j -= 1
                entry_cols[j + 1] = value
        for k in range(len(entry_cols)):
            s.ranks[entry_cols[k], 0] = k + 1
            s.ranks[entry_cols[k], 1] = len(entry_cols)
    for col in entry_cols:
        s.actions[action_count] = col
        s.action_type[action_count] = 1
        action_count += 1
    for col in range(n):
        if held[col] or entering[col]:
            s.actions[action_count] = col
            s.action_type[action_count] = 2
            action_count += 1
    s.meta[0] = action_count
    return ()


@njit(cache=True)
def _capacity(equity, fraction, existing, raw, friction):
    return max(0.0, (equity * fraction - existing) / (raw + fraction * friction))


@njit(cache=True)
def _feature_value(values, symbols, col, symbol, sparse, missing):
    if not sparse:
        return values[col, symbol]
    for k in range(symbols.shape[1]):
        if symbols[col, k] == symbol:
            return values[col, k]
    return missing


@njit(cache=True)
def constrained_entry(c, p, s, col, budget):
    cfg = p.cfg[col]
    raw = p.bars[c.i, p.symbols[col], 0]
    fee, slip = cfg[6], cfg[7]
    fill, cash_per_share = raw * (1 + slip), raw * (1 + slip) * (1 + fee)
    requested = math.floor((budget + 1e-9) / cash_per_share)
    if p.flags[9] and requested < 1:
        s.rejected[col] = 3
        return 0
    stop, target = _protection(p, col, fill)
    error = p.feature_errors[col, 0]
    if not error and (
        not np.isfinite(stop) or not np.isfinite(target) or not 0 < stop < fill < target
    ):
        error = 8
    if error:
        s.rejected[col] = error
        return 0
    loss = cash_per_share - stop * (1 - slip) * (1 - fee)
    if not np.isfinite(loss) or loss <= 0:
        s.rejected[col] = 9
        return 0
    gross, symbol, deployed, stop_risk, correlated, cluster_before = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    held_count = 0
    missing_corr, missing_allocation = False, False
    for j in range(len(p.entry)):
        if c.last_position[j] <= 0:
            continue
        held_count += 1
        mark = p.bars[c.i, p.symbols[j], 0]
        value = c.last_position[j] * mark
        gross += value
        if p.symbols[j] == p.symbols[col]:
            symbol += value
        if p.owners[j] == p.owners[col]:
            deployed += value
        held_stop = s.stops[j]
        if p.cfg[j, 4]:
            held_stop = max(held_stop, s.high[j] * (1 - p.cfg[j, 5] / 100))
        proceeds = held_stop * (1 - p.cfg[j, 7]) * (1 - p.cfg[j, 6])
        stop_risk += c.last_position[j] * max(0.0, mark - proceeds)
        if p.flags[4]:
            relation = _feature_value(
                p.correlations, p.correlation_symbols, col, p.symbols[j], p.flags[8], np.nan
            )
            missing_corr = missing_corr or np.isnan(relation)
            if p.symbols[j] == p.symbols[col] or relation >= 0.8:
                correlated += value
        if p.flags[5]:
            group = _feature_value(
                p.clusters, p.cluster_symbols, col, p.symbols[j], p.flags[8], -1.0
            )
            missing_allocation = missing_allocation or group < 0
            current_group = (
                _feature_value(p.clusters, p.cluster_symbols, col, p.symbols[col], p.flags[8], -1.0)
                if p.flags[9]
                else 1
            )
            if group == current_group:
                cluster_before += value
    equity = c.last_cash[c.group] + gross
    friction = cash_per_share - raw
    capacities = s.capacities[col]
    capacities[0] = requested
    capacities[1] = max(0.0, c.last_cash[c.group] / cash_per_share)
    capacities[2] = _capacity(equity, cfg[0] / 100, deployed, raw, friction)
    capacities[3] = _capacity(equity, p.limits[0] / 100, gross, raw, friction)
    capacities[4] = _capacity(equity, p.limits[1] / 100, symbol, raw, friction)
    capacities[5] = max(0.0, equity * p.limits[2] / 100 / (loss + p.limits[2] / 100 * friction))
    capacities[6] = max(
        0.0, (equity * p.limits[3] / 100 - stop_risk) / (loss + p.limits[3] / 100 * friction)
    )
    capacities[7] = requested if held_count < p.limits[4] else 0
    day = p.day[c.i]
    if p.flags[2]:
        capacities[8] = max(0.0, s.turnover[day, 1] - s.turnover[day, 2]) / fill
    key = p.capacity_key[col]
    if p.flags[3]:
        error = p.feature_errors[col, 1]
        if (
            not error
            and s.participation[key, 2] > 0
            and (
                s.participation[key, 3] != p.features[col, 1]
                or s.participation[key, 4] != p.capacity_source[col]
            )
        ):
            error = 5
        if error:
            s.rejected[col] = error
            return 0
        capacities[9] = max(0.0, p.features[col, 1] * p.limits[6] / 100 - s.participation[key, 0])
    if p.flags[4]:
        error = p.feature_errors[col, 2]
        if not error and missing_corr:
            error = p.feature_errors[col, 4] or 6
        if error:
            s.rejected[col] = error
            return 0
        capacities[10] = _capacity(equity, p.limits[7] / 100, correlated, raw, friction)
    if p.flags[5]:
        error = p.feature_errors[col, 3]
        if not error and missing_allocation:
            error = p.feature_errors[col, 5] or 7
        if error:
            s.rejected[col] = error
            return 0
        weight, cluster_count = p.features[col, 2], p.features[col, 3]
        if p.flags[9]:
            own_group = _feature_value(
                p.clusters, p.cluster_symbols, col, p.symbols[col], p.flags[8], -1.0
            )
            for j in range(len(p.entry)):
                active = c.last_position[j] > 0
                if p.entry[j] == c.i:
                    cost = p.bars[c.i, p.symbols[j], 0] * (1 + p.cfg[j, 7]) * (1 + p.cfg[j, 6])
                    budget_j = s.meta[1] * p.cfg[j, 0] / 100 * p.cfg[j, 1] / 100
                    active = active or (
                        not p.feature_errors[j, 3] and np.isfinite(cost) and cost <= budget_j + 1e-9
                    )
                if active:
                    if not p.flags[8]:
                        s.active[col, p.symbols[j]] = True
                    else:
                        for k in range(p.clusters.shape[1]):
                            if p.cluster_symbols[col, k] == p.symbols[j]:
                                s.active[col, k] = True
            groups = np.zeros(201, dtype=np.bool_)
            total, own_weight = 0.0, 0.0
            for k in range(p.clusters.shape[1]):
                if s.active[col, k]:
                    group = p.clusters[col, k]
                    if group < 1:
                        s.rejected[col] = 7
                        return 0
                    groups[group] = True
                    if group == own_group:
                        total += p.allocation_weights[col, k]
                    symbol_k = p.cluster_symbols[col, k] if p.flags[8] else k
                    if symbol_k == p.symbols[col]:
                        own_weight = p.allocation_weights[col, k]
            cluster_count = np.sum(groups)
            weight = own_weight / total / cluster_count
        s.details[col, 21], s.details[col, 22], s.details[col, 23] = (
            weight,
            cluster_count,
            s.meta[1],
        )
        capacities[11] = _capacity(equity, p.limits[0] / 100 * weight, symbol, raw, friction)
        capacities[12] = _capacity(
            equity, p.limits[0] / 100 / cluster_count, cluster_before, raw, friction
        )
    risk_target = np.nan
    if cfg[10]:
        risk_target = p.limits[2] / 100 * cfg[11]
        capacities[13] = max(0.0, equity * risk_target / (loss + risk_target * friction))
    minimum = capacities[0]
    for capacity in capacities:
        if not np.isnan(capacity):
            minimum = min(minimum, capacity)
    quantity = max(0, math.floor(minimum + 1e-9)) if equity > 0 else 0
    d = s.details[col]
    d[0] = stop
    d[1] = target
    d[2] = loss
    d[3] = quantity * loss
    d[4] = requested
    d[5] = 0
    d[6] = quantity < requested
    d[7] = equity
    d[8] = gross
    d[9] = symbol
    d[10] = held_count
    d[11] = stop_risk
    d[12] = quantity * cash_per_share
    d[13] = risk_target
    if p.flags[2]:
        d[14], d[15], d[16] = s.turnover[day, 0], s.turnover[day, 1], s.turnover[day, 2]
    if p.flags[3]:
        d[17] = p.features[col, 1] * p.limits[6] / 100
        d[18] = s.participation[key, 0]
    d[19] = correlated
    d[20] = cluster_before
    if quantity < 1:
        s.rejected[col] = 10
    return quantity


@njit(cache=True)
def order(c, p, s):
    if c.call_idx >= int(s.meta[0]):
        return -1, NoOrder
    col = s.actions[c.call_idx]
    action = s.action_type[c.call_idx]
    cfg = p.cfg[col]
    fee, slip = cfg[6], cfg[7]
    if action == 1:
        if p.flags[1] and s.rejected[col]:
            return col, NoOrder
        cap = s.meta[1] * cfg[0] / 100
        budget = cap * cfg[1] / 100
        s.budget[col] = budget
        raw = p.bars[c.i, p.symbols[col], 0]
        if p.flags[0]:
            quantity = constrained_entry(c, p, s, col, budget)
        else:
            deployed = 0.0
            for j in range(len(p.entry)):
                if p.owners[j] == p.owners[col] and c.last_position[j] > 0:
                    deployed += c.last_position[j] * p.bars[c.i, p.symbols[j], 0]
            quantity = math.floor((budget + 1e-9) / (raw * (1 + slip) * (1 + fee)))
            if deployed + budget > cap + 1e-8:
                s.rejected[col] = 1
            elif budget > c.last_cash[c.group] + 1e-8:
                s.rejected[col] = 2
            elif quantity < 1:
                s.rejected[col] = 3
        if s.rejected[col]:
            return col, NoOrder
        s.raw_in[col] = raw
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
    if action == 2:
        raw, outcome, timing = _risk(c, p, s, col, False)
    else:
        raw = s.action_price[c.call_idx]
        outcome, timing = s.action_outcome[c.call_idx], s.action_timing[c.call_idx]
    if not outcome:
        return col, NoOrder
    s.raw_out[col], s.outcome[col], s.timing[col] = raw, outcome, timing
    return col, nb.close_position_nb(
        price=raw, fees=fee, slippage=slip, allow_partial=False, log=True
    )


@njit(cache=True)
def after_order(c, p, s):
    col = c.col
    if c.order_result.status != OrderStatus.Filled:
        if not np.isnan(s.raw_in[col]) and s.filled[col] < 0:
            s.native[col] = int(c.order_result.status_info)
            s.rejected[col] = 4
        return
    if c.position_before == 0:
        if p.flags[3]:
            key = p.capacity_key[col]
            s.participation[key, 0] += int(c.order_result.size)
            s.participation[key, 1] += 1
            s.participation[key, 2] = col + 1
            s.participation[key, 3] = p.features[col, 1]
            s.participation[key, 4] = p.capacity_source[col]
        if p.flags[2]:
            day = p.day[c.i]
            s.turnover[day, 2] += c.order_result.size * c.order_result.price
            s.turnover[day, 3] += 1
        s.filled[col], s.price[col], s.high[col] = c.i, c.order_result.price, c.order_result.price
        if p.flags[0]:
            stop, target = _protection(p, col, c.order_result.price)
            s.stops[col], s.targets[col] = stop, target
            s.details[col, 0], s.details[col, 1] = stop, target
            s.details[col, 5] = int(c.order_result.size)


@njit(cache=True)
def after_bar(c, p, s):
    for col in range(len(p.entry)):
        if c.last_position[col] > 0:
            s.high[col] = max(s.high[col], p.bars[c.i, p.symbols[col], 1])


def native_report_arrays(portfolio, capital):
    """Use the same native analysis kernels once, without repeated pandas wrapping.

    Kept to discarded development accounts: final presentation still uses the
    public Portfolio methods. This is their exact native call graph for this
    adapter's single shared-cash, long-only group, with fillna_close=False.
    """
    shape = portfolio.wrapper.shape_2d
    orders = portfolio.orders
    column_map = orders.col_mapper.col_map
    group_lens = np.array([shape[1]], dtype=np.int64)
    asset_flow = nb.asset_flow_nb(shape, orders.values, column_map, Direction.Both)
    assets = nb.assets_nb(asset_flow)
    cashflows = nb.cash_flow_nb(shape, orders.values, column_map, False)
    grouped_flow = nb.cash_flow_grouped_nb(cashflows, group_lens)
    cash = nb.cash_grouped_nb(shape, grouped_flow, group_lens, np.array([float(capital)]))
    close = portfolio.close.to_numpy().copy()
    close[assets == 0] = 0.0
    asset_values = nb.asset_value_nb(close, assets)
    grouped_assets = nb.asset_value_grouped_nb(asset_values, group_lens)
    values = nb.value_nb(cash, grouped_assets)
    return values.reshape(-1), cash.reshape(-1), assets, asset_values, cashflows


def _correlation_row(feature, symbols, symbol_index, sparse):
    related = feature["correlations"]
    if sparse:
        names = [symbol for symbol in related if symbol in symbol_index]
        values = np.asarray([related[symbol] for symbol in names], dtype=np.float64)
        indices = np.asarray([symbol_index[symbol] for symbol in names], dtype=np.int64)
    else:
        values = np.asarray([related.get(symbol, np.nan) for symbol in symbols], dtype=np.float64)
        indices = np.empty(0, dtype=np.int64)
    return values, indices, tuple(symbol for symbol in symbols if related.get(symbol) is None)


def _cluster_row(feature, symbol, symbols, symbol_index, sparse, active=False):
    cluster = next(group for group in feature["clusters"] if symbol in group)
    group, weights = set(cluster), feature["weights"]
    labels = {name: i + 1 for i, cluster in enumerate(feature["clusters"]) for name in cluster}
    if sparse:
        names = [name for name in weights if name in symbol_index]
        values = np.asarray(
            [(labels[name] if active else int(name in group)) for name in names], dtype=np.int64
        )
        indices = np.asarray([symbol_index[name] for name in names], dtype=np.int64)
    else:
        values = np.asarray(
            [
                (labels[name] if active else int(name in group)) if name in weights else -1
                for name in symbols
            ],
            dtype=np.int64,
        )
        indices = np.empty(0, dtype=np.int64)
    return values, indices, tuple(name for name in symbols if name not in weights)


def prepare(strategies, lots, scheduled, snapshot, progress):
    """Validate causal features once per entry and encode without relaxing checks."""
    from research.intraday import bounds, stamp
    from research.prepared_calculation import prepared_market

    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    market = prepared_market(snapshot)
    lot_symbols = {signal["symbol"] for _, signal in lots}
    symbols = (
        tuple(market.symbols) + tuple(sorted(lot_symbols - set(market.symbols)))
        if market is not None
        else sorted(lot_symbols)
    )
    symbol_index = {symbol: i for i, symbol in enumerate(symbols)}
    days = sorted({key[:10] for key in timeline})
    day_index = {day: i for i, day in enumerate(days)}
    if market is not None:
        bars = market.ohlc
        if len(symbols) > len(market.symbols):
            bars = np.concatenate(
                (bars, np.full((len(timeline), len(symbols) - len(market.symbols), 4), np.nan)),
                axis=1,
            )
            bars.setflags(write=False)
    else:
        bars = np.full((len(timeline), len(symbols), 4), np.nan)
        grid = {key: i for i, key in enumerate(timeline)}
        for symbol in symbols:
            for key, bar in snapshot["bars"].get(symbol, {}).items():
                bars[grid[key], symbol_index[symbol]] = [
                    float(bar[k]) for k in ("open", "high", "low", "close")
                ]
        bars.setflags(write=False)
    n = len(lots)
    features = np.full((n, 4), np.nan)
    feature_errors = np.zeros((n, 6), dtype=np.int64)
    # Each admitted causal feature contains at most200 symbols. Sparse rows
    # retain that bound when a request contains a larger symbol union.
    width = min(len(symbols), 200)
    correlations = np.full((n, width), np.nan)
    clusters = np.full((n, width), -1, dtype=np.int64)
    allocation_weights = np.full((n, width), np.nan)
    sparse = len(symbols) > 200
    correlation_symbols = np.full((n, width) if sparse else (1, 1), -1, dtype=np.int64)
    cluster_symbols = np.full((n, width) if sparse else (1, 1), -1, dtype=np.int64)
    open_due = np.full((n, 2), len(timeline) + 1, dtype=np.int64)
    close_due = np.full(n, len(timeline) + 1, dtype=np.int64)
    errors, feature_refs, keys, sources = {}, [], {}, {}
    owners = np.array([owner for owner, _ in lots], dtype=np.int64)
    symbol_cols = np.array([symbol_index[signal["symbol"]] for _, signal in lots], dtype=np.int64)
    entry = np.array([value[0] for value in scheduled], dtype=np.int64)
    capacity_keys = np.zeros(n, dtype=np.int64)
    capacity_sources = np.zeros(n, dtype=np.int64)
    policy = strategies[0].get("execution_policy", {})
    risk_limits = policy.get("risk_limits", {})
    flags = np.array(
        [
            bool(policy),
            {
                None: 0,
                "source": 1,
                "source_order": 1,
                "reversed_source": 2,
                "lower_stop_risk": 3,
            }.get(policy.get("entry_priority"), 1),
            "max_daily_buy_turnover_pct" in risk_limits,
            policy.get("capacity_mode") == "prior20_broker_reported",
            policy.get("correlation_mode") == "prior60_positive_080",
            policy.get("allocation_mode")
            in ("prior60_cluster_inverse_vol", "active_prior60_cluster_inverse_vol"),
            minute,
            bool(progress),
            sparse,
            policy.get("allocation_mode") == "active_prior60_cluster_inverse_vol",
        ],
        dtype=np.int64,
    )
    limits = np.array([risk_limits.get(name, 0) for name in LIMIT_NAMES], dtype=np.float64)

    def check(col, kind, callback):
        try:
            return callback()
        except ValueError as error:
            code = 100 + len(errors)
            errors[code] = str(error)
            feature_errors[col, kind] = code
            return None

    instants = [stamp(key) for key in timeline] if minute else []

    def numeric_entry(feature, kind, factory):
        if market is None:
            return factory()
        return market.memoize_entry_feature(feature, kind, factory)

    symbol_key = tuple(symbols)
    session_index = {day: i for i, day in enumerate(snapshot["sessions"])}
    daily_decisions = {}
    config_rows = []
    for strategy in strategies:
        config, pol = strategy["config"], strategy.get("execution_policy", {})
        config_rows.append(
            [
                strategy["allocation_pct"],
                config["order_size_pct"],
                config["stop_pct"],
                config["target_pct"],
                config["trailing_enabled"],
                config["trailing_pct"],
                config["cost_bps"] / 10000,
                config["slippage_bps"] / 10000,
                pol.get("atr_stop_multiple", 0),
                pol.get("atr_target_multiple", 0),
                pol.get("sizing") == "stop_risk",
                pol.get("risk_budget_fraction", 0),
            ]
        )
    cfg = np.asarray(config_rows, dtype=np.float64)[owners]
    for col, (owner, signal) in enumerate(lots):
        strategy = strategies[owner]
        config, pol = strategy["config"], strategy.get("execution_policy", {})
        feature_refs.append({})
        if entry[col] < 0:
            continue
        e = int(entry[col])
        day = timeline[e][:10]
        if not minute and day not in daily_decisions:
            daily_decisions[day] = bounds(snapshot, day)[0].isoformat()
        decision = timeline[e] if minute else daily_decisions[day]
        key = (day, signal["symbol"])
        capacity_keys[col] = keys.setdefault(key, len(keys))
        if not minute:
            close_due[col] = e + config["hold_sessions"]
        else:
            for i in range(e, len(timeline)):
                now = instants[i]
                if open_due[col, 0] > len(timeline):
                    if config.get("hold_minutes") is not None and now >= instants[e] + timedelta(
                        minutes=config["hold_minutes"]
                    ):
                        open_due[col] = i, 4
                    elif config.get("exit_time") and now.strftime("%H:%M") >= config["exit_time"]:
                        open_due[col] = i, 5
                if (
                    close_due[col] > len(timeline)
                    and now + timedelta(minutes=1) == bounds(snapshot, timeline[i][:10])[1]
                    and (
                        config.get("trade_horizon") == "intraday"
                        or session_index[timeline[i][:10]] - session_index[day]
                        >= config["hold_sessions"]
                    )
                ):
                    close_due[col] = i
        if pol.get("exit_mode") == "atr":
            from research.execution_features import validate_entry_feature

            feature = check(
                col,
                0,
                partial(
                    validate_entry_feature,
                    signal.get("execution_atr"),
                    symbol=signal["symbol"],
                    entry_session=day,
                    decision_at=decision,
                ),
            )
            if feature:
                features[col, 0] = feature["value"]
                feature_refs[col]["atr"] = feature
        if flags[3]:
            from research.portfolio_execution_features import validate_capacity_entry

            feature = check(
                col,
                1,
                partial(
                    validate_capacity_entry,
                    signal.get("entry_capacity"),
                    symbol=signal["symbol"],
                    entry_session=day,
                    decision_at=decision,
                ),
            )
            if feature:
                features[col, 1] = feature["mean_daily_shares"]
                capacity_sources[col] = sources.setdefault(feature["source_id"], len(sources))
                feature_refs[col]["capacity"] = feature
        if flags[4]:
            from research.portfolio_execution_features import validate_correlation_entry

            feature = check(
                col,
                2,
                partial(
                    validate_correlation_entry,
                    signal.get("entry_correlation"),
                    symbol=signal["symbol"],
                    entry_session=day,
                    decision_at=decision,
                    required_symbols=(),
                ),
            )
            if feature:
                values, indices, absent = numeric_entry(
                    feature,
                    ("correlation", symbol_key),
                    partial(_correlation_row, feature, symbols, symbol_index, sparse),
                )
                correlations[col, : len(values)] = values
                if sparse:
                    correlation_symbols[col, : len(indices)] = indices
                feature_refs[col]["correlation"] = feature
                missing = sorted(lot_symbols.intersection(absent)) if absent else ()
                if missing:
                    check(
                        col,
                        4,
                        partial(
                            validate_correlation_entry,
                            signal.get("entry_correlation"),
                            symbol=signal["symbol"],
                            entry_session=day,
                            decision_at=decision,
                            required_symbols=missing[:1],
                        ),
                    )
        if flags[5]:
            from research.portfolio_allocation import validate

            feature = check(
                col,
                3,
                partial(
                    validate,
                    signal.get("entry_allocation"),
                    symbol=signal["symbol"],
                    entry_session=day,
                    decision_at=decision,
                    required_symbols=(),
                ),
            )
            if feature:
                features[col, 2] = feature["weights"][signal["symbol"]]
                features[col, 3] = len(feature["clusters"])
                values, indices, absent = numeric_entry(
                    feature,
                    ("allocation", signal["symbol"], symbol_key, bool(flags[9])),
                    partial(
                        _cluster_row,
                        feature,
                        signal["symbol"],
                        symbols,
                        symbol_index,
                        sparse,
                        bool(flags[9]),
                    ),
                )
                clusters[col, : len(values)] = values
                if flags[9]:
                    names = [symbols[int(index)] for index in indices] if sparse else symbols
                    allocation_weights[col, : len(values)] = [
                        feature["weights"].get(name, np.nan) for name in names
                    ]
                if sparse:
                    cluster_symbols[col, : len(indices)] = indices
                feature_refs[col]["allocation"] = feature
                missing = sorted(lot_symbols.intersection(absent)) if absent else ()
                if missing:
                    check(
                        col,
                        5,
                        partial(
                            validate,
                            signal.get("entry_allocation"),
                            symbol=signal["symbol"],
                            entry_session=day,
                            decision_at=decision,
                            required_symbols=missing[:1],
                        ),
                    )
    p = Inputs(
        bars,
        owners,
        symbol_cols,
        cfg,
        entry,
        open_due,
        close_due,
        flags,
        limits,
        features,
        correlations,
        clusters,
        allocation_weights,
        correlation_symbols,
        cluster_symbols,
        capacity_keys,
        capacity_sources,
        feature_errors,
        np.array([day_index[key[:10]] for key in timeline], dtype=np.int64),
    )

    def f():
        return np.full(n, np.nan)

    def ints():
        return np.zeros(n, dtype=np.int64)

    s = State(
        np.full(n, -1, dtype=np.int64),
        f(),
        f(),
        f(),
        f(),
        ints(),
        np.full(n, -1, dtype=np.int64),
        f(),
        f(),
        f(),
        ints(),
        ints(),
        np.full((n, 24), np.nan),
        np.full((n, 14), np.nan),
        np.zeros((n, 2), dtype=np.int64),
        f(),
        np.zeros(n * 3, dtype=np.int64),
        np.zeros(n * 3, dtype=np.int64),
        np.zeros(n * 3),
        np.zeros(n * 3, dtype=np.int64),
        np.zeros(n * 3, dtype=np.int64),
        np.zeros(2),
        np.full((len(days), 5), np.nan),
        np.zeros((max(1, len(keys)), 5)),
        np.zeros((n, width), dtype=np.bool_),
    )
    return p, s, (errors, feature_refs, list(keys), days)


def receipts(p, s, metadata, strategies, lots, timeline):
    errors, features, keys, days = metadata
    details = [None] * len(lots) if p.flags[0] else None
    rejected = [None] * len(lots)
    constraints_by_col = [[] for _ in lots]
    capacity_mask = (~np.isnan(s.capacities)) & (
        np.floor(s.capacities + 1e-9) < s.details[:, 4, None]
    )
    constrained_cols, constrained_caps = np.nonzero(capacity_mask)
    for col, index in zip(constrained_cols, constrained_caps, strict=True):
        constraints_by_col[col].append(CAP_NAMES[index])
    for col, (owner, signal) in enumerate(lots):
        policy = strategies[owner].get("execution_policy", {})
        d, caps = s.details[col], s.capacities[col]
        constraints = constraints_by_col[col]
        code = int(s.rejected[col])
        if code:
            rejected[col] = (
                (
                    "Execution limits cannot admit one whole share: "
                    + ", ".join(constraints or ["requested_cash_budget"])
                )
                if code == 10
                else errors.get(code, ERRORS.get(code))
            )
        if not p.flags[0]:
            continue
        detail = None
        if p.flags[1] and s.ranks[col, 0]:
            detail = {
                "entry_priority": policy["entry_priority"],
                "entry_rank": int(s.ranks[col, 0]),
                "entry_batch_size": int(s.ranks[col, 1]),
                "stop_risk_fraction": None if np.isnan(s.scores[col]) else float(s.scores[col]),
            }
        if not np.isnan(d[4]):
            detail = {
                **(detail or {}),
                "exit_mode": policy["exit_mode"],
                "sizing": policy["sizing"],
                "risk_budget_fraction": policy.get("risk_budget_fraction"),
                "target_stop_risk_pct": None if np.isnan(d[13]) else float(d[13] * 100),
                "atr": dict(features[col]["atr"]) if "atr" in features[col] else None,
                "initial_stop": float(d[0]),
                "initial_target": float(d[1]),
                "initial_risk_per_share": float(d[2]),
                "initial_stop_risk": float(d[3]),
                "requested_quantity": int(d[4]),
                "filled_quantity": int(d[5]),
                "clipped": bool(d[6]),
                "constraints": constraints,
                "opening_equity": float(d[7]),
                "gross_before": float(d[8]) if d[10] else 0,
                "symbol_before": float(d[9]) if d[9] else 0,
                "open_positions_before": int(d[10]),
                "total_stop_risk_before": float(d[11]) if d[10] else 0,
                "accepted_cash_budget": float(d[12]),
            }
            if p.flags[2]:
                detail.update(
                    daily_opening_equity=float(d[14]),
                    daily_buy_budget=float(d[15]),
                    daily_buy_notional_before=float(d[16]),
                    daily_buy_capacity_shares=float(caps[8]),
                )
            if p.flags[3]:
                feature = features[col]["capacity"]
                detail.update(
                    capacity_feature_id=feature["id"],
                    capacity_mean_daily_shares=feature["mean_daily_shares"],
                    daily_participation_share_budget=float(d[17]),
                    daily_bought_shares_before=int(d[18]),
                    remaining_participation_shares=float(caps[9]),
                )
            if p.flags[4]:
                detail.update(
                    correlation_feature_id=features[col]["correlation"]["id"],
                    correlated_exposure_before=float(d[19]) if d[19] else 0,
                    correlated_capacity_shares=float(caps[10]),
                )
            if p.flags[5]:
                feature = features[col]["allocation"]
                cluster = next(group for group in feature["clusters"] if signal["symbol"] in group)
                if p.flags[9]:
                    active = sorted(
                        {
                            lots[j][1]["symbol"]
                            for j in range(len(lots))
                            for k in np.flatnonzero(s.active[col])
                            if p.symbols[j] == (p.cluster_symbols[col, k] if p.flags[8] else k)
                        }
                    )
                    cluster = [name for name in cluster if name in active]
                    detail.update(
                        allocation_active_symbols=active,
                        allocation_cluster_count=int(d[22]),
                        allocation_opening_equity=float(d[23]),
                    )
                detail.update(
                    allocation_feature_id=feature["id"],
                    allocation_weight=float(d[21])
                    if p.flags[9]
                    else feature["weights"][signal["symbol"]],
                    allocation_cluster=list(cluster),
                    allocation_cluster_before=float(d[20]) if d[20] else 0,
                    allocation_symbol_capacity_shares=float(caps[11]),
                    allocation_cluster_capacity_shares=float(caps[12]),
                )
        details[col] = detail
    turnover = None
    if p.flags[2]:
        turnover = [
            {
                "date": day,
                "opening_at": timeline[int(row[4])],
                "opening_equity": float(row[0]),
                "buy_budget": float(row[1]),
                "buy_notional": float(row[2]),
                "buy_fills": int(row[3]),
            }
            for day, row in zip(days, s.turnover, strict=True)
            if not np.isnan(row[0])
        ]
    participation = None
    if p.flags[3]:
        participation = {}
        for key, row in zip(keys, s.participation, strict=False):
            if row[2] > 0:
                feature = features[int(row[2]) - 1]["capacity"]
                participation[key] = {
                    "date": key[0],
                    "symbol": key[1],
                    "mean_daily_shares": feature["mean_daily_shares"],
                    "source_id": feature["source_id"],
                    "share_budget": feature["mean_daily_shares"] * p.limits[6] / 100,
                    "bought_shares": int(row[0]),
                    "buy_fills": int(row[1]),
                }

    def optional(array):
        return [None if np.isnan(value) else float(value) for value in array]

    return (
        optional(s.budget),
        rejected,
        [None if value < 0 else int(value) for value in s.native],
        optional(s.raw_in),
        optional(s.raw_out),
        [
            {0: None, 1: "stop", 2: "trailing_stop", 3: "target", 4: "hold", 5: "time"}[int(value)]
            for value in s.outcome
        ],
        [{0: None, 1: "open", 2: "intraday", 3: "close"}[int(value)] for value in s.timing],
        details,
        turnover,
        participation,
    )
