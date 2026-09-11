"""Versioned analysis of retained account evidence and bounded native statistics.

No simulation, price service, I/O, global cache or retained engine object. Native
objects are optional and are only inspected while their calculation owns them.
"""

from __future__ import annotations

import math
import re
import warnings
from datetime import date, datetime

VERSION = "research-analysis-v2"
ANNUAL_SESSIONS = 252
MAX_CHART_POINTS = 1200
MAX_REPORT_ROWS = 200
REPORT_DEPTH_VERSION = "research-report-depth-v1"
ROLLING_WINDOWS = (21, 63, 126)
RETURN_PERCENTILES = (0, 5, 25, 50, 75, 95, 100)
BENCHMARK_REASON = "No independent aligned benchmark is recorded."
NATIVE_REASON = "The native engine object is not retained; an explicit backtest replay is required."
RETURN_REASON = "Daily return series contains an undefined return because prior account equity is nonpositive or absent; observations were not discarded."


def _key(value):
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _scalar(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    if isinstance(value, str):
        return value if value.lower() not in {"nan", "nat", "inf", "-inf", "infinity"} else None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


class _Analysis:
    def __init__(self):
        self.value = {
            "version": VERSION,
            "basis": [],
            "catalog": [],
            "metrics": {},
            "unavailable": {},
            "charts": [],
        }

    def add(
        self,
        key,
        label,
        value,
        *,
        group="Account",
        format="number",
        source="Saved account evidence",
        description="",
        reason=None,
    ):
        value = _scalar(value)
        self.value["catalog"].append(
            {
                "key": key,
                "label": label,
                "group": group,
                "format": format,
                "source": source,
                "description": description or label,
            }
        )
        self.value["metrics"][key] = value
        if value is None:
            self.value["unavailable"][key] = (
                reason
                or "Undefined for this sample: insufficient observations or a zero denominator."
            )

    def chart(self, identity, title, traces=None, *, reason=None, **layout):
        chart = {"id": identity, "title": title, "status": "unavailable" if reason else "available"}
        if reason:
            chart["reason"] = reason
        else:
            chart["figure"] = {
                "data": traces or [],
                "layout": {
                    "autosize": True,
                    "margin": {"l": 60, "r": 25, "t": 20, "b": 50},
                    "hovermode": "closest",
                    "showlegend": len(traces or []) > 1,
                    **layout,
                },
            }
        self.value["charts"].append(chart)


def _daily(report):
    """Select the actual last marked observation of each recorded session day."""
    import numpy as np
    import pandas as pd

    points = report.get("equity_curve", [])
    daily = {}
    previous = None
    for point in points:
        day = point.get("date")
        if not isinstance(day, str) or len(day) != 10:
            raise ValueError("Analysis needs dated account-equity observations")
        parsed = date.fromisoformat(day)
        if previous is not None and parsed < previous:
            raise ValueError("Analysis account observations are not chronological")
        previous = parsed
        equity = _scalar(point.get("equity"))
        if not isinstance(equity, (int, float)):
            raise ValueError("Analysis needs finite marked account equity")
        daily[day] = equity
    initial = _scalar(report.get("summary", {}).get("initial_capital"))
    values = np.array(list(daily.values()), dtype=float)
    prior = (
        np.r_[initial if initial is not None else np.nan, values[:-1]] if len(values) else values
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = values / prior - 1
    # Percentage account returns require positive beginning equity. Version 1
    # accepted negative denominators, incorrectly turning deeper deficits into
    # positive percentage returns. Keep the observation, but mark it undefined.
    returns[(prior <= 0) | ~np.isfinite(returns)] = np.nan
    series = pd.Series(returns, index=pd.to_datetime(list(daily)), dtype=float)
    return initial, values, series


def _ratio(numerator, denominator):
    return numerator / denominator if denominator is not None and abs(denominator) > 1e-14 else None


def _account(out, report, initial, values, returns):
    import numpy as np

    summary = report.get("summary", {})
    summary_fields = [
        ("initial_capital", "Starting capital", "money"),
        ("final_equity", "Final equity", "money"),
        ("net_pnl", "Net P&L", "money"),
        ("net_return_pct", "Period return", "percent"),
        ("max_drawdown_pct", "Max marked drawdown", "percent"),
        ("realized_equity", "Realized equity", "money"),
        ("accepted_trades", "Entered trades", "number"),
        ("closed_trades", "Closed trades", "number"),
        ("pending_trades", "Open trades", "number"),
        ("unfunded_pending", "Pending entries", "number"),
        ("skipped_trades", "Skipped entries", "number"),
        ("excluded_signals", "Excluded signals", "number"),
        ("win_rate_pct", "Closed-trade win rate", "percent"),
        ("profit_factor", "Closed-trade profit factor", "number"),
        ("sample_adequacy", "Sample assessment", "text"),
    ]
    for key, label, fmt in summary_fields:
        out.add(
            "account_" + key,
            label,
            summary.get(key),
            format=fmt,
            description="Original saved summary; its calculation and ranking semantics are unchanged.",
        )
    daily = returns.to_numpy()
    valid = len(daily) > 0 and np.isfinite(daily).all()
    annual, volatility, sharpe, sortino, calmar, omega, var, shortfall = (None,) * 8
    if valid:
        average = float(np.mean(daily))
        stdev = float(np.std(daily, ddof=1)) if len(daily) > 1 else None
        downside = float(np.sqrt(np.mean(np.minimum(daily, 0) ** 2)))
        if initial and initial > 0 and values[-1] >= 0:
            with np.errstate(over="ignore", invalid="ignore"):
                annual = _scalar(np.power(values[-1] / initial, ANNUAL_SESSIONS / len(daily)) - 1)
        volatility = stdev * math.sqrt(ANNUAL_SESSIONS) if stdev is not None else None
        sharpe = _ratio(average * math.sqrt(ANNUAL_SESSIONS), stdev)
        sortino = _ratio(average * math.sqrt(ANNUAL_SESSIONS), downside)
        max_dd = summary.get("max_drawdown_pct")
        calmar = (
            _ratio(annual, max_dd / 100)
            if annual is not None and isinstance(max_dd, (int, float))
            else None
        )
        omega = _ratio(float(np.maximum(daily, 0).sum()), float(-np.minimum(daily, 0).sum()))
        var = float(np.quantile(daily, 0.05))
        shortfall = float(np.mean(daily[daily <= var]))
    for key, label, value, fmt, description in [
        (
            "annualized_return_pct",
            "Annualized return",
            None if annual is None else annual * 100,
            "percent",
            "Compounded marked-account return, annualized over 252 recorded trading sessions; not calendar-year CAGR.",
        ),
        (
            "annualized_volatility_pct",
            "Annualized volatility",
            None if volatility is None else volatility * 100,
            "percent",
            "Sample standard deviation (ddof 1) of end-of-day returns × sqrt(252).",
        ),
        (
            "sharpe_ratio",
            "Sharpe ratio",
            sharpe,
            "number",
            "Daily marked-account excess-return mean / sample standard deviation × sqrt(252); risk-free return 0.",
        ),
        (
            "sortino_ratio",
            "Sortino ratio",
            sortino,
            "number",
            "Daily return mean / downside root-mean-square × sqrt(252); required return 0.",
        ),
        (
            "calmar_ratio",
            "Calmar ratio",
            calmar,
            "number",
            "252-session annualized return / original maximum marked-account drawdown, including intraday marks when present.",
        ),
        (
            "omega_ratio",
            "Omega ratio",
            omega,
            "number",
            "Sum of positive daily returns / absolute sum of negative daily returns; threshold 0.",
        ),
        (
            "value_at_risk_pct",
            "Daily value at risk (95%)",
            None if var is None else var * 100,
            "percent",
            "Historical 5th percentile of daily returns; signed return, not a guaranteed loss bound.",
        ),
        (
            "expected_shortfall_pct",
            "Daily expected shortfall (95%)",
            None if shortfall is None else shortfall * 100,
            "percent",
            "Mean of daily returns at or below their historical 5th percentile.",
        ),
    ]:
        out.add(
            "account_" + key,
            label,
            value,
            group="Account risk",
            format=fmt,
            description=description,
            reason=RETURN_REASON if len(daily) and not valid else None,
        )
    out.add(
        "account_return_sessions",
        "Return observations",
        len(returns),
        description="One last recorded mark per trading-session date. Weekends and missing dates are not inserted.",
    )
    out.add(
        "account_available_cash",
        "Available cash",
        report.get("equity_curve", [{}])[-1].get("cash") if report.get("equity_curve") else None,
        format="money",
    )
    closed = [r for r in report.get("ledger", []) if r.get("status") == "closed"]
    fees = [_scalar(r.get("fees")) for r in report.get("ledger", [])]
    out.add(
        "account_recorded_fees",
        "Recorded fees",
        sum(v for v in fees if isinstance(v, (int, float)))
        if all(v is not None for v in fees)
        else None,
        format="money",
        description="Sum of recorded ledger fees, including entered open positions; excludes costs absent from the ledger.",
    )
    pnls = [_scalar(r.get("pnl")) for r in closed]
    out.add(
        "account_trade_expectancy",
        "Average closed-trade P&L",
        sum(pnls) / len(pnls) if pnls and all(p is not None for p in pnls) else None,
        format="money",
        description="Arithmetic mean of saved closed-trade P&L, after recorded trade fees.",
    )


def _native_format(key, title):
    if "[%]" in title:
        return "percent"
    if key in {"start", "end", "first_trade_start", "last_trade_end"}:
        return "text"
    if key in {"start_value", "end_value", "total_fees_paid", "open_trade_pnl", "expectancy"}:
        return "money"
    return "number"


def _vbt_stats(
    out,
    obj,
    registry,
    prefix,
    group,
    source,
    *,
    excluded=None,
    overrides=None,
    unit="trading-session bars",
    group_by=None,
):
    excluded, overrides = excluded or {}, overrides or {}
    applicable = [key for key in registry if key not in excluded and key not in overrides]
    stats = {}
    if obj is not None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                data = obj.stats(
                    metrics=applicable,
                    group_by=group_by,
                    settings={"to_timedelta": False, "incl_open": False, "year_freq": "252D"},
                    silence_warnings=True,
                )
                stats = data.to_dict() if data is not None else {}
            except (ValueError, TypeError, IndexError, ZeroDivisionError):
                # Empty subsets can make a native registry metric fail; isolate
                # that metric so unrelated valid native observations survive.
                for key in applicable:
                    try:
                        value = obj.stats(
                            metrics=[key],
                            group_by=group_by,
                            settings={
                                "to_timedelta": False,
                                "incl_open": False,
                                "year_freq": "252D",
                            },
                            silence_warnings=True,
                        )
                        if value is not None:
                            stats.update(value.to_dict())
                    except (ValueError, TypeError, IndexError, ZeroDivisionError):
                        pass
    for key, definition in registry.items():
        title = str(definition["title"])
        value = overrides.get(key, stats.get(title))
        fmt = _native_format(key, title)
        if key == "value_at_risk" and isinstance(value, (int, float)):
            value *= 100
            fmt = "percent"
        label = title.replace(" [%]", "")
        if group == "VectorBT drawdowns" and key in {
            "max_dd",
            "avg_dd",
            "max_dd_duration",
            "avg_dd_duration",
        }:
            label = "Recovered · " + label
        duration = (
            "duration" in key
            or key == "period"
            or (group == "VectorBT trades" and key in {"coverage", "overlap_coverage"})
        )
        if duration:
            label += f" ({unit})"
        reason = excluded.get(key) or (
            NATIVE_REASON if obj is None and key not in overrides else None
        )
        out.add(
            prefix + key,
            label,
            None if reason else value,
            group=group,
            format=fmt,
            source=source,
            description=f"{source}; registry statistic {key}. "
            + (
                f"Duration measured in {unit}, not elapsed calendar time."
                if duration
                else "Native definition; closed trades for win/loss statistics."
            ),
            reason=reason,
        )
    return stats


def _vectorbt(out, report, returns, portfolio):
    import numpy as np
    import pandas as pd
    import vectorbt as vbt

    if vbt.__version__ != "0.28.5":
        raise ValueError("Analysis requires the pinned VectorBT 0.28.5 runtime")
    source = "VectorBT 0.28.5"
    ret = returns.vbt.returns(
        freq="1D", year_freq="252D", defaults={"risk_free": 0.0, "required_return": 0.0, "ddof": 1}
    )
    benchmark = dict.fromkeys(("benchmark_return", "alpha", "beta"), BENCHMARK_REASON)
    daily_values = returns.to_numpy()
    if not np.isfinite(daily_values).all():
        benchmark.update(
            dict.fromkeys(
                (
                    key
                    for key in ret.metrics
                    if key not in {"start", "end", "period"} and key not in benchmark
                ),
                "Daily return series contains an undefined return; observations were not silently discarded.",
            )
        )
    elif len(daily_values) > 1 and np.std(daily_values, ddof=1) <= 1e-14:
        benchmark.update(
            dict.fromkeys(
                ("sharpe_ratio", "skew", "kurtosis"),
                "Daily returns have no measurable variation; this statistic is undefined.",
            )
        )
    ret_stats = _vbt_stats(
        out,
        ret,
        ret.metrics,
        "vectorbt_returns_",
        "VectorBT daily returns",
        source + " ReturnsAccessor on shared marked-account EOD returns",
        excluded=benchmark,
    )
    risk = {
        key: ret_stats.get(title)
        for key, title in [
            ("sharpe_ratio", "Sharpe Ratio"),
            ("sortino_ratio", "Sortino Ratio"),
            ("omega_ratio", "Omega Ratio"),
            ("calmar_ratio", "Calmar Ratio"),
        ]
    }
    interval = report.get("execution", {}).get("interval", "D")
    unit = "recorded minute bars" if interval == "1m" else "trading-session bars"
    _vbt_stats(
        out,
        portfolio,
        vbt.Portfolio.metrics,
        "vectorbt_portfolio_",
        "VectorBT portfolio",
        source + " Portfolio, one shared cash group; annual ratios use EOD returns",
        excluded={"benchmark_return": BENCHMARK_REASON},
        overrides=risk,
        unit=unit,
        group_by=True,
    )
    points = report.get("equity_curve", [])
    index = pd.to_datetime([point.get("timestamp", point["date"]) for point in points])
    values = pd.Series([point["equity"] for point in points], index=index)
    # Pool native trade records into a single account series, retaining all
    # prices/sizes/PnL, ordered by recorded exit then entry. Averaging lot stats
    # would be wrong; each signal lot can otherwise report a one-trade streak.
    trades = None
    records = report.get("engine_records", {}).get("trades")
    if records is not None and len(points):
        from vectorbt.portfolio.enums import trade_dt

        arr = np.array(
            [tuple(row[name] for name in trade_dt.names) for row in records], dtype=trade_dt
        )
        if len(arr):
            arr = arr[np.lexsort((arr["id"], arr["entry_idx"], arr["exit_idx"]))].copy()
            arr["col"] = 0
            arr["id"] = np.arange(len(arr))
        wrapper = vbt.ArrayWrapper.from_obj(values, freq="1min" if interval == "1m" else "1D")
        trades = vbt.Trades(wrapper, arr, close=values)
    _vbt_stats(
        out,
        trades,
        vbt.Trades.metrics,
        "vectorbt_trades_",
        "VectorBT trades",
        source + " pooled native account trade records, exit/entry order",
        unit=unit,
    )
    dd = (
        vbt.Drawdowns.from_ts(values, wrapper_kwargs={"freq": "1min" if interval == "1m" else "1D"})
        if len(values)
        else None
    )
    _vbt_stats(
        out,
        dd,
        vbt.Drawdowns.metrics,
        "vectorbt_drawdowns_",
        "VectorBT drawdowns",
        source
        + " Drawdowns on the shared account marked-equity series (first mark is the native baseline)",
        unit=unit,
    )
    out.value["basis"].append(
        "VectorBT registry durations count recorded bars. Pooled trade streaks use native records ordered by exit then entry; no independently funded lot-statistic averaging."
    )


NAUTILUS_STATISTICS = (
    "Alpha",
    "AvgLoser",
    "AvgWinner",
    "BetaRatio",
    "CAGR",
    "CalmarRatio",
    "DownCaptureRatio",
    "Expectancy",
    "ExpectedShortfall",
    "InformationRatio",
    "LongRatio",
    "MaxDrawdown",
    "MaxLoser",
    "MaxWinner",
    "MinLoser",
    "MinWinner",
    "OmegaRatio",
    "ProfitFactor",
    "ReturnsAverage",
    "ReturnsAverageLoss",
    "ReturnsAverageWin",
    "ReturnsKurtosis",
    "ReturnsSkewness",
    "ReturnsVolatility",
    "RiskReturnRatio",
    "SharpeRatio",
    "SortinoRatio",
    "TailRatio",
    "TrackingError",
    "TreynorRatio",
    "UlcerIndex",
    "UpCaptureRatio",
    "ValueAtRisk",
    "WinRate",
)
NAUTILUS_PNL = {
    "AvgLoser",
    "AvgWinner",
    "Expectancy",
    "MaxLoser",
    "MaxWinner",
    "MinLoser",
    "MinWinner",
    "WinRate",
}
NAUTILUS_BENCHMARK = {
    "Alpha",
    "BetaRatio",
    "DownCaptureRatio",
    "InformationRatio",
    "TrackingError",
    "TreynorRatio",
    "UpCaptureRatio",
}
NAUTILUS_PERCENT = {
    "CAGR",
    "ReturnsAverage",
    "ReturnsAverageLoss",
    "ReturnsAverageWin",
    "ReturnsVolatility",
    "MaxDrawdown",
    "ValueAtRisk",
    "ExpectedShortfall",
    "UlcerIndex",
    "WinRate",
    "LongRatio",
}


def _nautilus(out, report, returns, engine, venue, currency, charts):
    if engine is None:
        for name in NAUTILUS_STATISTICS:
            bases = (
                ("pnl",)
                if name in NAUTILUS_PNL
                else (("positions",) if name == "LongRatio" else ("account", "positions"))
            )
            for basis in bases:
                key = (
                    "nautilus_positions_long_ratio"
                    if name == "LongRatio"
                    else f"nautilus_{basis}_" + _key(name)
                )
                out.add(
                    key,
                    name,
                    None,
                    group="Nautilus native",
                    source="NautilusTrader 1.231.0",
                    format="percent"
                    if name in NAUTILUS_PERCENT
                    else ("money" if name in NAUTILUS_PNL else "number"),
                    reason=BENCHMARK_REASON if name in NAUTILUS_BENCHMARK else NATIVE_REASON,
                )
        for key, label, fmt in (
            ("total_pnl", "PnL (total)", "money"),
            ("total_pnl_pct", "PnL% (total)", "percent"),
        ):
            out.add(
                "nautilus_pnl_" + key,
                label,
                None,
                group="Nautilus P&L",
                format=fmt,
                source="NautilusTrader 1.231.0",
                reason=NATIVE_REASON,
            )
        return
    import inspect

    from nautilus_trader.core.nautilus_pyo3 import analysis as native

    analyzer = engine.portfolio.analyzer
    statistics = []
    for name in NAUTILUS_STATISTICS:
        factory = getattr(native, name)
        parameters = inspect.signature(factory).parameters
        kwargs = {
            key: value
            for key, value in {
                "period": 252,
                "risk_free_rate": 0.0,
                "threshold": 0.0,
                "confidence": 0.95,
            }.items()
            if key in parameters
        }
        statistic = factory(**kwargs)
        analyzer.register_statistic(statistic)
        statistics.append((name, statistic))
    account = engine.cache.account_for_venue(venue)
    positions = engine.cache.positions()
    analyzer.calculate_statistics(account, positions)
    pnl_values = analyzer.get_performance_stats_pnls(currency=currency)
    position_returns = analyzer.position_returns()
    position_dict = {int(k.value): float(v) for k, v in position_returns.items()}
    account_dict = {int(k.value): float(v) for k, v in returns.items() if math.isfinite(v)}
    all_daily_valid = len(account_dict) == len(returns)
    for name, statistic in statistics:
        if name in NAUTILUS_PNL:
            value = pnl_values.get(statistic.name)
            scale = 100 if name == "WinRate" else 1
            out.add(
                "nautilus_pnl_" + _key(name),
                statistic.name,
                value * scale if isinstance(value, (int, float)) else value,
                group="Nautilus P&L",
                format="percent" if scale == 100 else "money",
                source="NautilusTrader 1.231.0 native analyzer, realized position P&L",
            )
        elif name == "LongRatio":
            out.add(
                "nautilus_positions_long_ratio",
                statistic.name,
                _scaled(statistic.calculate_from_positions(positions), 100),
                group="Nautilus positions",
                format="percent",
                source="NautilusTrader 1.231.0 native position registry",
            )
        else:
            for basis, data, valid in (
                ("account", account_dict, all_daily_valid),
                ("positions", position_dict, True),
            ):
                reason = BENCHMARK_REASON if name in NAUTILUS_BENCHMARK else None
                if not valid:
                    reason = "Daily return series contains an undefined return; observations were not silently discarded."
                if (
                    name in {"SharpeRatio", "RiskReturnRatio", "ReturnsSkewness", "ReturnsKurtosis"}
                    and len(data) > 1
                ):
                    import numpy as np

                    if np.std(list(data.values()), ddof=1) <= 1e-14:
                        reason = "Return observations have no measurable variation; this statistic is undefined."
                try:
                    value = statistic.calculate_from_returns(data) if not reason else None
                except (ValueError, ZeroDivisionError, OverflowError):
                    value = None
                percent = name in NAUTILUS_PERCENT
                out.add(
                    f"nautilus_{basis}_" + _key(name),
                    statistic.name,
                    _scaled(value, 100 if percent else 1),
                    group="Nautilus daily account"
                    if basis == "account"
                    else "Nautilus closed-position returns",
                    format="percent" if percent else "number",
                    source=f"NautilusTrader 1.231.0 {type(statistic).__name__}; "
                    + (
                        "marked-account EOD returns"
                        if basis == "account"
                        else "native closed-position return observations, not marked account returns"
                    ),
                    reason=reason,
                )
    for key, name, fmt in (
        ("total_pnl", "PnL (total)", "money"),
        ("total_pnl_pct", "PnL% (total)", "percent"),
    ):
        out.add(
            "nautilus_pnl_" + key,
            name,
            pnl_values.get(name),
            group="Nautilus P&L",
            format=fmt,
            source="NautilusTrader native analyzer account-balance P&L without added unrealized marks; distinct from final marked equity",
        )
    if charts:
        frame = engine.trader.generate_account_report(venue=venue)
        if frame is not None and not frame.empty:
            _table(
                out,
                "nautilus-account-report",
                "Native account events",
                frame.head(MAX_REPORT_ROWS).reset_index().to_dict("records"),
            )
    out.value["basis"].append(
        "Nautilus native closed-position returns are event observations and may aggregate positions with the same timestamp; their 252-observation ratios are not daily marked-account ratios. Native account P&L uses account balances; marked equity is reported separately."
    )


def _scaled(value, factor):
    return value * factor if isinstance(value, (int, float)) else value


def _sample(length):
    if length <= MAX_CHART_POINTS:
        return list(range(length))
    return sorted(
        {
            0,
            length - 1,
            *(round(i * (length - 1) / (MAX_CHART_POINTS - 1)) for i in range(MAX_CHART_POINTS)),
        }
    )


def _line(name, x, y, **kwargs):
    return {
        "type": "scatter",
        "mode": "lines",
        "name": name,
        "x": list(x),
        "y": [_scalar(v) for v in y],
        **kwargs,
    }


def _table(out, identity, title, rows):
    if not rows:
        out.chart(identity, title, reason="No native records were recorded.")
        return
    keys = list(dict.fromkeys(key for row in rows[:MAX_REPORT_ROWS] for key in row))[:18]
    out.chart(
        identity,
        title,
        [
            {
                "type": "table",
                "header": {"values": keys},
                "cells": {
                    "values": [
                        [str(row.get(key, ""))[:200] for row in rows[:MAX_REPORT_ROWS]]
                        for key in keys
                    ]
                },
            }
        ],
    )


def _drawdown_episodes(report, initial):
    """Extract episodes before sampling, retaining only the deepest bounded rows.

    The initial-capital peak has no invented timestamp. Duration counts actual
    underwater observations/session dates; recovery counts session transitions
    from the trough to the first mark at or above the old peak.
    """
    import heapq

    curve = report.get("equity_curve", [])
    result = {
        "status": "available",
        "total": 0,
        "shown": 0,
        "truncated": False,
        "order": "depth_descending",
        "rows": [],
        "basis": "Complete marked account equity, with starting capital as the initial peak.",
        "duration_definition": "Underwater bars and sessions count recorded marks and distinct session dates below the peak; the recovery mark is excluded.",
        "recovery_definition": "Recovery sessions count recorded session transitions from trough to recovery; elapsed days use their actual timestamps. Unrecovered episodes have no recovery duration.",
    }
    if not curve or initial is None or initial <= 0:
        result.update(
            status="unavailable",
            reason="Drawdown episodes require positive starting capital and retained account marks.",
        )
        return result
    peak, peak_at, peak_index = initial, None, None
    session, previous_day = -1, None
    active = None
    retained = []

    def finish(episode, stamp, recovered):
        result["total"] += 1
        row = {
            "id": result["total"],
            "peak_at": episode["peak_at"],
            "start_at": episode["start_at"],
            "trough_at": episode["trough_at"],
            "end_at": stamp,
            "recovered_at": stamp if recovered else None,
            "status": "recovered" if recovered else "ongoing",
            "depth_pct": (episode["peak_equity"] - episode["trough_equity"])
            / episode["peak_equity"]
            * 100,
            "peak_equity": episode["peak_equity"],
            "trough_equity": episode["trough_equity"],
            "underwater_bars": episode["underwater_bars"],
            "underwater_sessions": episode["underwater_sessions"],
            "recovery_sessions": session - episode["trough_session"] if recovered else None,
            "recovery_days": (
                datetime.fromisoformat(stamp) - datetime.fromisoformat(episode["trough_at"])
            ).total_seconds()
            / 86400
            if recovered
            else None,
            "peak_index": episode["peak_index"],
            "trough_index": episode["trough_index"],
            "end_index": index,
        }
        # Earlier episodes win equal-depth ties. Never retain an unbounded
        # episode list even when a long curve oscillates around the same peak.
        item = (row["depth_pct"], -row["id"], row)
        if len(retained) < MAX_REPORT_ROWS:
            heapq.heappush(retained, item)
        elif item[:2] > retained[0][:2]:
            heapq.heapreplace(retained, item)

    for index, point in enumerate(curve):
        day = point["date"]
        if day != previous_day:
            session += 1
            previous_day = day
        stamp = point.get("timestamp", day)
        equity = point["equity"]
        if equity >= peak:
            if active:
                finish(active, stamp, True)
                active = None
            peak, peak_at, peak_index = equity, stamp, index
            continue
        if active is None:
            active = {
                "peak_at": peak_at,
                "peak_index": peak_index,
                "peak_equity": peak,
                "start_at": stamp,
                "trough_at": stamp,
                "trough_index": index,
                "trough_equity": equity,
                "trough_session": session,
                "underwater_bars": 0,
                "underwater_sessions": 0,
                "last_underwater_day": None,
            }
        active["underwater_bars"] += 1
        if active["last_underwater_day"] != day:
            active["underwater_sessions"] += 1
            active["last_underwater_day"] = day
        if equity < active["trough_equity"]:
            active.update(
                trough_at=stamp, trough_equity=equity, trough_index=index, trough_session=session
            )
    if active:
        finish(active, stamp, False)
    result["rows"] = [item[2] for item in sorted(retained, key=lambda item: (-item[0], -item[1]))]
    result["shown"] = len(retained)
    result["truncated"] = result["total"] > len(retained)
    return result


def _extrema_sample(series, *, must=()):
    """Keep endpoints, global extremes and bounded per-bucket extrema."""
    import numpy as np

    length = len(series[0]) if series else 0
    if length <= MAX_CHART_POINTS:
        return list(range(length))
    chosen = {0, length - 1, *(index for index in must if index is not None)}
    for values in series:
        finite = [index for index, value in enumerate(values) if _scalar(value) is not None]
        if finite:
            chosen.add(min(finite, key=lambda index: values[index]))
            chosen.add(max(finite, key=lambda index: values[index]))
    # Callers pass at most three indices for each of 200 retained episodes.
    if len(chosen) > MAX_CHART_POINTS:
        raise ValueError("Too many required account chart observations")
    budget = MAX_CHART_POINTS - len(chosen)
    buckets = max(1, budget // max(2, len(series) * 2))
    boundaries = np.linspace(0, length, buckets + 1, dtype=int)
    for left, right in zip(boundaries[:-1], boundaries[1:], strict=True):
        for values in series:
            finite = [index for index in range(left, right) if _scalar(values[index]) is not None]
            if finite:
                for index in (
                    min(finite, key=lambda index: values[index]),
                    max(finite, key=lambda index: values[index]),
                ):
                    if len(chosen) < MAX_CHART_POINTS:
                        chosen.add(index)
    for index in _sample(length):
        if len(chosen) >= MAX_CHART_POINTS:
            break
        chosen.add(index)
    return sorted(chosen)


def _report_depth(out, report, returns, initial):
    """Add full-evidence risk detail without changing scalar trial statistics."""
    import numpy as np

    daily = returns.to_numpy()
    defined = len(daily) > 0 and np.isfinite(daily).all()
    quantiles = {
        "status": "available" if defined else "unavailable",
        "observations": len(daily),
        "method": "linear",
        "unit": "percent",
        "sampling": "Complete end-of-session account returns, including starting capital to the first close; no missing-session rows inserted.",
        "rows": [
            {
                "percentile": percentile,
                "return_pct": _scalar(np.quantile(daily, percentile / 100) * 100),
            }
            for percentile in RETURN_PERCENTILES
        ]
        if defined
        else [],
    }
    if not defined:
        quantiles["reason"] = (
            "Requires a complete daily return sample with positive prior equity for every observation."
        )
    drawdowns = _drawdown_episodes(report, initial)
    out.value["report_depth"] = {
        "version": REPORT_DEPTH_VERSION,
        "daily_return_quantiles": quantiles,
        "drawdowns": drawdowns,
        "rolling": {
            "windows": list(ROLLING_WINDOWS),
            "annual_sessions": ANNUAL_SESSIONS,
            "sampling": "End-of-session account returns; complete fixed-length windows only.",
            "volatility_ddof": 1,
            "risk_free_return": 0,
            "required_return": 0,
            "sortino_definition": "Mean daily return / root mean square of min(return, 0) over all window observations, annualized by sqrt(252).",
        },
    }
    if defined:
        _table(
            out,
            "daily-return-quantiles",
            "Daily return quantiles",
            [
                {
                    "Percentile": f"{row['percentile']}%",
                    "Daily return (%)": round(row["return_pct"], 4),
                }
                for row in quantiles["rows"]
            ],
        )
    else:
        out.chart("daily-return-quantiles", "Daily return quantiles", reason=quantiles["reason"])
    if drawdowns["rows"]:
        _table(
            out,
            "drawdown-episodes",
            "Worst five marked drawdowns",
            [
                {
                    "Peak": row["peak_at"] or "Starting capital",
                    "Trough": row["trough_at"],
                    "Recovered": row["recovered_at"] or "Ongoing",
                    "Depth (%)": round(row["depth_pct"], 4),
                    "Underwater sessions": row["underwater_sessions"],
                    "Recovery sessions": row["recovery_sessions"]
                    if row["recovery_sessions"] is not None
                    else "—",
                }
                for row in drawdowns["rows"][:5]
            ],
        )
    else:
        out.chart(
            "drawdown-episodes",
            "Worst five marked drawdowns",
            reason=drawdowns.get("reason", "No drawdown episodes are recorded."),
        )
    return drawdowns


def _rolling_charts(out, returns):
    import numpy as np

    for window in ROLLING_WINDOWS:
        stats = {}
        if len(returns) >= window:
            mean = returns.rolling(window, min_periods=window).mean()
            std = returns.rolling(window, min_periods=window).std(ddof=1)
            downside = np.sqrt(
                returns.clip(upper=0).pow(2).rolling(window, min_periods=window).mean()
            )
            stats = {
                "sharpe": mean / std.where(std.abs() > 1e-14) * math.sqrt(ANNUAL_SESSIONS),
                "volatility": std * math.sqrt(ANNUAL_SESSIONS) * 100,
                "sortino": mean
                / downside.where(downside.abs() > 1e-14)
                * math.sqrt(ANNUAL_SESSIONS),
            }
        for metric in ("sharpe", "volatility", "sortino"):
            identity = "rolling-" + metric + (f"-{window}" if window != 21 else "")
            title = f"Rolling {metric.title() if metric != 'volatility' else metric} ({window} sessions)"
            values = stats.get(metric)
            if values is None:
                out.chart(
                    identity,
                    title,
                    reason=f"Requires at least {window} recorded daily return observations.",
                )
                continue
            if not np.isfinite(values.to_numpy()).any():
                out.chart(
                    identity,
                    title,
                    reason=f"No defined {window}-session {metric} window: all observations and a nonzero ratio denominator are required.",
                )
                continue
            observations = values.to_numpy()
            undefined = ~np.isfinite(observations)
            gap_starts = np.flatnonzero(undefined & np.r_[True, ~undefined[:-1]])
            # A sampled line must not bridge an omitted undefined window. For
            # highly fragmented data, bounded markers preserve observed values
            # without implying continuity across thousands of missing windows.
            markers_only = len(gap_starts) > MAX_CHART_POINTS - 4
            samples = _extrema_sample(
                [observations], must=() if markers_only else gap_starts.tolist()
            )
            out.chart(
                identity,
                title,
                [
                    _line(
                        metric.title(),
                        [returns.index[i].isoformat() for i in samples],
                        [values.iloc[i] for i in samples],
                        mode="markers" if markers_only else "lines",
                    )
                ],
                yaxis={"title": "Annualized %" if metric == "volatility" else "Ratio"},
                meta={
                    "window_sessions": window,
                    "sampling": "end-of-session",
                    "annual_sessions": ANNUAL_SESSIONS,
                    "ddof": 1,
                    "risk_free_return": 0,
                    "required_return": 0,
                },
            )


def _charts(out, report, returns, initial):
    import numpy as np

    curve = report.get("equity_curve", [])
    drawdowns = _report_depth(out, report, returns, initial)
    ix = _extrema_sample(
        [[point["equity"] for point in curve], [point.get("drawdown_pct", 0) for point in curve]],
        must=[
            row[key]
            for row in drawdowns["rows"]
            for key in ("peak_index", "trough_index", "end_index")
        ],
    )
    x = [curve[i].get("timestamp", curve[i]["date"]) for i in ix]
    equity = [curve[i]["equity"] for i in ix]
    cash = [curve[i].get("cash") for i in ix]
    out.chart(
        "account-equity",
        "Equity and cash",
        [_line("Equity", x, equity), _line("Cash", x, cash)],
        yaxis={"title": "INR"},
    )
    out.chart(
        "account-underwater",
        "Underwater",
        [_line("Drawdown", x, [-curve[i].get("drawdown_pct", 0) for i in ix], fill="tozeroy")],
        yaxis={"title": "% below peak"},
    )
    exposure = [
        100 * (point["equity"] - point["cash"]) / point["equity"]
        if isinstance(point.get("cash"), (int, float)) and point["equity"]
        else None
        for point in curve
    ]
    out.chart(
        "account-exposure",
        "Account exposure",
        [_line("Invested equity", x, [exposure[i] for i in ix])],
        yaxis={"title": "% of equity"},
    )
    cumulative = [(value / initial - 1) * 100 if initial else None for value in equity]
    out.chart(
        "account-cumulative",
        "Cumulative return",
        [_line("Account return", x, cumulative)],
        yaxis={"title": "%"},
    )
    r = returns.to_numpy()
    finite = r[np.isfinite(r)]
    if len(finite) and len(finite) == len(r):
        counts, edges = np.histogram(
            finite * 100, bins=min(40, max(1, int(math.sqrt(len(finite)))))
        )
        out.chart(
            "daily-return-distribution",
            "Daily return distribution",
            [
                {
                    "type": "bar",
                    "x": ((edges[:-1] + edges[1:]) / 2).tolist(),
                    "y": counts.tolist(),
                    "width": np.diff(edges).tolist(),
                }
            ],
            xaxis={"title": "Daily return (%)"},
            yaxis={"title": "Sessions"},
        )
    else:
        out.chart(
            "daily-return-distribution",
            "Daily return distribution",
            reason=RETURN_REASON if len(r) else "No daily returns are retained.",
        )
    monthly, yearly = {}, {}
    for stamp, value in returns.items():
        for grouped, key in ((monthly, stamp.strftime("%Y-%m")), (yearly, stamp.strftime("%Y"))):
            grouped[key] = grouped.get(key, 1) * (1 + value)
    years = sorted(yearly)
    out.chart(
        "monthly-returns",
        "Monthly returns",
        [
            {
                "type": "heatmap",
                "x": [f"{i:02}" for i in range(1, 13)],
                "y": years,
                "z": [
                    [
                        _scalar((monthly[f"{year}-{month:02}"] - 1) * 100)
                        if f"{year}-{month:02}" in monthly
                        else None
                        for month in range(1, 13)
                    ]
                    for year in years
                ],
                "colorscale": "RdYlGn",
                "zmid": 0,
                "hoverongaps": False,
                "colorbar": {"title": "%"},
            }
        ],
    )
    out.chart(
        "yearly-returns",
        "Yearly returns",
        [{"type": "bar", "x": years, "y": [_scalar((yearly[year] - 1) * 100) for year in years]}],
        yaxis={"title": "% (partial periods included)"},
    )
    _rolling_charts(out, returns)
    closed = [row for row in report.get("ledger", []) if row.get("status") == "closed"]
    if closed:
        chosen = [closed[i] for i in _sample(len(closed))]
        out.chart(
            "trade-pnl",
            "Closed-trade P&L",
            [
                {
                    "type": "scatter",
                    "mode": "markers",
                    "x": [row.get("exit_timestamp", row.get("exit_date")) for row in chosen],
                    "y": [_scalar(row.get("pnl")) for row in chosen],
                    "text": [str(row.get("symbol", "")) for row in chosen],
                }
            ],
            yaxis={"title": "INR"},
        )
        duration, pnls = [], []
        for row in chosen:
            start, end = (
                row.get("entry_timestamp") or row.get("entry_date"),
                row.get("exit_timestamp") or row.get("exit_date"),
            )
            try:
                duration.append(
                    (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
                    / 86400
                )
                pnls.append(row.get("pnl"))
            except (ValueError, TypeError):
                continue
        out.chart(
            "trade-duration",
            "Trade P&L by elapsed holding time",
            [
                {
                    "type": "scatter",
                    "mode": "markers",
                    "x": duration,
                    "y": [_scalar(p) for p in pnls],
                }
            ],
            xaxis={"title": "Elapsed calendar days"},
            yaxis={"title": "INR"},
        )
    else:
        out.chart("trade-pnl", "Closed-trade P&L", reason="No closed trades are recorded.")
        out.chart(
            "trade-duration",
            "Trade P&L by elapsed holding time",
            reason="No closed trades are recorded.",
        )
    for kind, rows in report.get("engine_records", {}).items():
        _table(out, "native-" + kind, "Native " + kind, rows)
    out.chart("benchmark-comparison", "Benchmark comparison", reason=BENCHMARK_REASON)
    out.chart(
        "bars-with-fills",
        "Price bars with fills",
        reason="Open the retained frozen-price evidence to display recorded bars with executions.",
    )
    out.value["basis"].append(
        f"Line/scatter charts display at most {MAX_CHART_POINTS} sampled observations including endpoints; account performance charts also preserve extrema and boundaries of the deepest {MAX_REPORT_ROWS} drawdown episodes. Scalar metrics, quantiles and episode calculations use full evidence. Native record tables show the first {MAX_REPORT_ROWS} rows; drawdown detail keeps the deepest {MAX_REPORT_ROWS}, with the full episode count. The original evidence remains unchanged. Monthly/yearly returns include partial periods."
    )


def build_analysis(
    report, *, portfolio=None, nautilus_engine=None, venue=None, currency=None, charts=True
):
    """Build JSON-only analysis from saved evidence; optional live native objects.

    Call before disposing a native engine. No input is mutated. `charts=False`
    supports compact trial collection; scalar calculations are identical.
    """
    out = _Analysis()
    initial, values, returns = _daily(report)
    out.value["basis"] = [
        "Shared-account marked equity, not averages of independently funded strategies or signal lots.",
        "Return sampling: actual final recorded account mark per session date, including initial capital to the first close. No weekend rows or fabricated missing-day returns.",
        "Annualization: 252 trading sessions; risk-free return 0; required return 0; volatility sample ddof 1. Non-finite or undefined statistics are null, never fabricated zero or infinity.",
        "Analysis v2 percentage returns require positive prior account equity. Observations following zero or negative equity remain undefined; version 1 allowed negative denominators. Original summary values, objective scores and saved evidence are unchanged.",
        "No aligned independent benchmark is recorded. Native provider definitions remain distinct from saved summary values and ranking scores.",
    ]
    _account(out, report, initial, values, returns)
    engine = report.get("execution", {}).get("engine")
    saved = report.get("analysis", {})
    preserved = (
        [
            entry
            for entry in saved.get("catalog", [])
            if entry.get("key", "").startswith(("vectorbt_", "nautilus_"))
        ]
        if saved.get("version") in {"research-analysis-v1", VERSION}
        and portfolio is None
        and nautilus_engine is None
        else []
    )
    if preserved:
        for entry in preserved:
            key = entry["key"]
            out.add(
                key,
                entry["label"],
                saved.get("metrics", {}).get(key),
                group=entry["group"],
                format=entry["format"],
                source=entry["source"],
                description=entry.get("description", ""),
                reason=saved.get("unavailable", {}).get(key),
            )
        out.value["basis"].append(
            "Previously recorded native metrics are preserved; no native engine replay was performed."
        )
        if charts:
            out.value["charts"].extend(
                chart
                for chart in saved.get("charts", [])
                if chart.get("id") == "nautilus-account-report"
            )
    elif engine == "vectorbt" or portfolio is not None:
        _vectorbt(out, report, returns, portfolio)
    elif engine == "nautilus" or nautilus_engine is not None:
        _nautilus(out, report, returns, nautilus_engine, venue, currency, charts)
    if charts:
        _charts(out, report, returns, initial)
    return out.value


def add_price_charts(analysis, report, snapshot, symbol=None):
    """Return a new analysis with one bounded frozen-OHLC / execution figure.

    Consumes retained input evidence only; never requests prices. Sampling keeps
    execution bars and endpoints, then fills the remaining budget uniformly.
    """
    import copy

    result = copy.deepcopy(analysis)
    all_bars = snapshot.get("bars", {})
    curve = report.get("equity_curve", [])
    ledger_symbols = {row.get("symbol") for row in report.get("ledger", []) if row.get("symbol")}

    def in_scope(stamp):
        if not curve:
            return False
        if curve[0]["date"] <= stamp[:10] <= curve[-1]["date"]:
            if "T" in stamp and curve[0].get("timestamp") and curve[-1].get("timestamp"):
                return (
                    datetime.fromisoformat(curve[0]["timestamp"])
                    <= datetime.fromisoformat(stamp)
                    <= datetime.fromisoformat(curve[-1]["timestamp"])
                )
            return True
        return False

    bars = {
        name: {stamp: candle for stamp, candle in prices.items() if in_scope(stamp)}
        for name, prices in all_bars.items()
        if not ledger_symbols or name in ledger_symbols
    }
    symbols = sorted(name for name, rows in bars.items() if rows)
    result["price_symbols"] = symbols
    traded = [
        row.get("symbol")
        for row in report.get("ledger", [])
        if row.get("quantity", 0) > 0 and row.get("symbol") in symbols
    ]
    selected = symbol or next(iter(traded), next(iter(symbols), None))
    if selected is not None and selected not in symbols:
        raise ValueError("Selected symbol is not present in the retained price snapshot")
    result["price_symbol"] = selected
    result["charts"] = [
        chart for chart in result.get("charts", []) if chart.get("id") != "bars-with-fills"
    ]
    if selected is None:
        result["charts"].append(
            {
                "id": "bars-with-fills",
                "title": "Price bars with fills",
                "status": "unavailable",
                "reason": "No frozen OHLC bars are retained.",
            }
        )
        return result
    prices = bars[selected]
    keys = sorted(prices)
    rows = [
        row
        for row in report.get("ledger", [])
        if row.get("symbol") == selected and row.get("quantity", 0) > 0
    ]
    markers = []
    event_times = set()
    minute = snapshot.get("provenance", {}).get("interval") == "1m"
    for kind in ("entry", "exit"):
        events = []
        for row in rows:
            if kind == "exit" and row.get("status") != "closed":
                continue
            stamp = (row.get(kind + "_timestamp") if minute else None) or row.get(kind + "_date")
            price = _scalar(row.get(kind + "_price"))
            if stamp and isinstance(price, (int, float)):
                events.append((stamp, price, str(row.get("strategy_id", ""))))
                event_times.add(stamp)
        sampled = [events[i] for i in _sample(len(events))]
        markers.append(
            {
                "type": "scatter",
                "mode": "markers",
                "name": "Entry" if kind == "entry" else "Exit",
                "x": [item[0] for item in sampled],
                "y": [item[1] for item in sampled],
                "text": [item[2] for item in sampled],
                "marker": {
                    "symbol": "triangle-up" if kind == "entry" else "triangle-down",
                    "size": 10,
                },
            }
        )
    must = {0, len(keys) - 1} | {i for i, key in enumerate(keys) if key in event_times}
    if len(must) >= MAX_CHART_POINTS:
        selected_ix = sorted(must)[:: max(1, math.ceil(len(must) / (MAX_CHART_POINTS - 2)))]
        selected_ix = sorted(set(selected_ix) | {0, len(keys) - 1})
    else:
        remaining = [i for i in _sample(len(keys)) if i not in must]
        budget = MAX_CHART_POINTS - len(must)
        selected_ix = sorted(must | set(remaining[:budget]))
    shown = [keys[i] for i in selected_ix]
    candles = {
        "type": "candlestick",
        "name": selected,
        "x": shown,
        **{
            field: [_scalar(prices[key].get(field)) for key in shown]
            for field in ("open", "high", "low", "close")
        },
    }
    result["charts"].append(
        {
            "id": "bars-with-fills",
            "title": f"{selected} · prices and fills",
            "status": "available",
            "figure": {
                "data": [candles, *markers],
                "layout": {
                    "autosize": True,
                    "margin": {"l": 60, "r": 25, "t": 20, "b": 50},
                    "xaxis": {"rangeslider": {"visible": False}},
                    "yaxis": {"title": "INR"},
                    "showlegend": True,
                },
            },
        }
    )
    result["basis"] = [
        *(
            line
            for line in result.get("basis", [])
            if not line.startswith("Price chart uses retained ")
        ),
        f"Price chart uses retained {snapshot.get('provenance', {}).get('interval', '')} OHLC evidence and recorded execution prices. At most {MAX_CHART_POINTS} bars and markers per side are displayed; omitted bars are not reconstructed. Nautilus intrabar execution timestamps are model-derived when its OHLC execution model is used.",
    ]
    return result
