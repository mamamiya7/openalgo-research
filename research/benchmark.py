"""Reporting-only benchmark context over independently frozen native prices.

No downloads, search scores or execution inputs are changed here. Comparisons use
one contiguous run of matching daily observations, never forward-filled prices.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from math import isfinite

VERSION = "research-benchmark-v1"
METRICS = {
    "portfolio_return_pct": ("Portfolio return (aligned)", "percent"),
    "benchmark_return_pct": ("Benchmark return", "percent"),
    "excess_return_pct": ("Excess return (percentage points)", "percent"),
    "beta": ("Beta (aligned)", "ratio"),
    "alpha_pct": ("Annualized alpha (aligned)", "percent"),
    "correlation": ("Correlation", "ratio"),
    "tracking_error_pct": ("Annualized tracking error", "percent"),
    "information_ratio": ("Annualized information ratio", "ratio"),
}


def normalize_benchmark(value):
    from research.market_series import normalize_descriptor

    descriptor = normalize_descriptor(value)
    if descriptor["role"] != "benchmark" or descriptor["exchange"] != "NSE_INDEX":
        raise ValueError("Choose an NSE index for the reporting benchmark")
    return descriptor


def report_dates(report):
    """Use actual marked dates, not CSV bounds or another period's coverage."""
    from research.analytics import _daily

    _, _, returns = _daily(report)
    if not 1 <= len(returns) <= 3000:
        raise ValueError("A benchmark needs 1–3000 saved daily account observations")
    return returns.index[0].date().isoformat(), returns.index[-1].date().isoformat()


def comparison(report, evidence):
    import numpy as np
    import pandas as pd
    import vectorbt as vbt

    from research.analytics import _daily
    from research.market_series import slice_series, validate_series

    validate_series(evidence)
    normalize_benchmark(evidence["descriptor"])
    first, last = report_dates(report)
    series = slice_series(evidence, first, last, warmup_sessions=1)
    initial, values, returns = _daily(report)
    equity = dict(zip((stamp.date().isoformat() for stamp in returns.index), values, strict=True))
    points = {point["date"]: point for point in report["equity_curve"]}
    sessions = series["required_dates"]
    groups, current = [], []
    minute = report.get("execution", {}).get("interval") == "1m"

    def completed(day):
        if not minute:
            return True
        stamp = points.get(day, {}).get("timestamp")
        close = series["available_at"].get(day)
        if not stamp or not close:
            return False
        marked = datetime.fromisoformat(stamp)
        if marked.tzinfo is None:
            return False
        # Native minute equity is marked at the end of its minute-open candle.
        return marked + timedelta(minutes=1) >= datetime.fromisoformat(close)

    for i, day in enumerate(sessions):
        if not first <= day <= last:
            continue
        previous = sessions[i - 1] if i else None
        before = initial if day == first else equity.get(previous)
        value = equity.get(day)
        bar, prior = series["bars"].get(day), series["bars"].get(previous)
        valid = (
            before is not None
            and value is not None
            and before > 0
            and isfinite(float(before))
            and isfinite(float(value))
            and bar is not None
            and prior is not None
            and completed(day)
            and (day == first or completed(previous))
        )
        if valid:
            row = {
                "date": day,
                "previous_date": previous,
                "portfolio": float(value / before - 1),
                "benchmark": bar["close"] / prior["close"] - 1,
            }
            current.append(row)
        else:
            if current:
                groups.append(current)
                current = []
    if current:
        groups.append(current)
    chosen = max(groups, key=len, default=[])
    observed = len(equity)
    result = {
        "version": VERSION,
        "descriptor": series["descriptor"],
        "evidence_id": series["id"],
        "status": "unavailable",
        "dates": {
            "from": chosen[0]["date"] if chosen else None,
            "to": chosen[-1]["date"] if chosen else None,
        },
        "observations": len(chosen),
        "omitted_sessions": observed - len(chosen),
        "metrics": dict.fromkeys(METRICS),
        "basis": [
            "Provider-native index prices; total-return treatment is not independently verified. This is market context, not a matched trading strategy.",
            "Account marks and index close-to-close returns share the displayed sessions; the first index return needs the preceding close.",
            "Use the longest contiguous common period, earliest on equal length; missing prices and unfinished account sessions are never filled.",
            "252 sessions per year, zero risk-free return, sample standard deviation (ddof=1).",
            "Existing portfolio statistics, saved trades and optimization rankings are unchanged.",
        ],
    }
    if len(chosen) < 2:
        result["reason"] = (
            "Fewer than two consecutive completed sessions have matching account and index prices."
        )
        return result, None
    if vbt.__version__ != "0.28.5":
        raise ValueError("Benchmark analysis requires the pinned VectorBT 0.28.5 runtime")
    index = pd.to_datetime([row["date"] for row in chosen])
    account = pd.Series([row["portfolio"] for row in chosen], index=index)
    market = pd.Series([row["benchmark"] for row in chosen], index=index)
    ret = account.vbt.returns(
        freq="1D", year_freq="252D", benchmark_rets=market, defaults={"risk_free": 0.0, "ddof": 1}
    )
    account_growth = np.cumprod(1 + account.to_numpy())
    market_growth = np.cumprod(1 + market.to_numpy())
    active = account.to_numpy() - market.to_numpy()
    deviation = float(np.std(active, ddof=1))
    varied_market = float(np.std(market, ddof=1)) > 1e-14
    varied_account = float(np.std(account, ddof=1)) > 1e-14
    metrics = {
        "portfolio_return_pct": (account_growth[-1] - 1) * 100,
        "benchmark_return_pct": (market_growth[-1] - 1) * 100,
        "excess_return_pct": (account_growth[-1] - market_growth[-1]) * 100,
        "beta": ret.beta() if varied_market else None,
        "alpha_pct": ret.alpha(risk_free=0.0) * 100 if varied_market else None,
        "correlation": account.corr(market) if varied_market and varied_account else None,
        "tracking_error_pct": deviation * np.sqrt(252) * 100,
        "information_ratio": ret.information_ratio(ddof=1) * np.sqrt(252)
        if deviation > 1e-14
        else None,
    }
    result["metrics"] = {
        key: float(value) if value is not None and isfinite(value) else None
        for key, value in metrics.items()
    }
    result["status"] = "available" if len(chosen) == observed else "partial"
    if result["status"] == "partial":
        result["reason"] = (
            "Comparison uses the displayed common period; the full portfolio report is unchanged."
        )
    dates = [chosen[0]["previous_date"], *[row["date"] for row in chosen]]
    traces = [
        {
            "type": "scatter",
            "mode": "lines",
            "name": name,
            "x": dates,
            "y": [0.0, *((growth - 1) * 100).tolist()],
        }
        for name, growth in (
            ("Portfolio", account_growth),
            (series["descriptor"]["symbol"], market_growth),
        )
    ]
    return result, traces


def add_benchmark(analysis, report, evidence):
    """Produce a reporting overlay; do not reinterpret full-period metric IDs."""
    from research.analytics import _Analysis

    details, traces = comparison(report, evidence)
    extra = _Analysis()
    for key, (label, format_) in METRICS.items():
        extra.add(
            "benchmark_aligned_" + key,
            label,
            details["metrics"][key],
            group="Benchmark · aligned period",
            format=format_,
            source="VectorBT 0.28.5 and recorded aligned daily returns",
            description=label + " on the displayed common observation period.",
            reason=details.get("reason"),
        )
    extra.chart(
        "benchmark-comparison",
        "Portfolio and benchmark",
        traces,
        reason=details.get("reason") if traces is None else None,
        yaxis={"title": "Cumulative return (%)"},
    )
    result = deepcopy(analysis)
    keys = set(extra.value["metrics"])
    result["catalog"] = [
        row for row in result.get("catalog", []) if row["key"] not in keys
    ] + extra.value["catalog"]
    result.setdefault("metrics", {}).update(extra.value["metrics"])
    result["unavailable"] = {
        key: value for key, value in result.get("unavailable", {}).items() if key not in keys
    }
    result["unavailable"].update(extra.value["unavailable"])
    result["charts"] = [
        row for row in result.get("charts", []) if row["id"] != "benchmark-comparison"
    ] + extra.value["charts"]
    result["benchmark"] = details
    result["basis"] = [
        text
        for text in result.get("basis", [])
        if not text.startswith("No aligned independent benchmark is recorded.")
    ]
    note = "Independent index context uses its displayed aligned period; original full-period statistics and rankings are preserved."
    if note not in result["basis"]:
        result["basis"].append(note)
    return result
