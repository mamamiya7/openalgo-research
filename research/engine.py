"""Causal funded daily equity simulation, independent of Flask and live RMS.

Historical cash sizing and OHLC ambiguity are deliberately separate from live
RMS quote/order evaluation. Policy follows the reference eod_next_open_marked_v2
timing, with explicit scanner triggers, priorities and bounded cash/exposure policy.
"""

import math
import random
from collections import Counter
from dataclasses import dataclass

from .data import validate_snapshot

POLICY_VERSION = "scanner-eod-next-open-marked-v3"
# Uptick reads yesterday's trough, which itself reads seven earlier counts.
TRIGGER_WARMUP_SESSIONS = 8
MAX_SIGNALS = 25000
MODE_ORDER = ("Bottom Fishing", "Zero Only", "Uptick")


def config_defaults():
    return {
        "initial_capital": 100000,
        "order_size_pct": 10,
        "target_pct": 10,
        "stop_pct": 5,
        "hold_sessions": 5,
        "cost_bps": 10,
        "slippage_bps": 0,
        "trailing_pct": 0,
        "trailing_enabled": False,
        "modes": ["Bypass"],
        "entry_priority": "csv",
        "priority_seed": 0,
        "max_exposure_pct": 100,
        "exposure_fill_mode": "strict",
    }


def validate_config(config=None):
    cfg = config_defaults()
    if config is None:
        config = {}
    if not isinstance(config, dict) or set(config) - set(cfg) - {
        "trade_horizon",
        "hold_minutes",
        "entry_time",
        "exit_time",
    }:
        raise ValueError("Unknown fixed setup parameter")
    cfg.update(config)
    if any(key in config for key in ("trade_horizon", "hold_minutes", "entry_time", "exit_time")):
        from .intraday import validate_timing

        cfg.update(validate_timing(config))
    if "trailing_enabled" not in config:
        cfg["trailing_enabled"] = bool(cfg["trailing_pct"])
    if not isinstance(cfg["trailing_enabled"], bool):
        raise ValueError("trailing_enabled must be a boolean")
    modes = cfg["modes"]
    if (
        not isinstance(modes, list)
        or not modes
        or any(not isinstance(mode, str) or mode not in (*MODE_ORDER, "Bypass") for mode in modes)
    ):
        raise ValueError(
            "Choose Bypass or a nonempty subset of Bottom Fishing, Zero Only and Uptick"
        )
    cfg["modes"] = (
        ["Bypass"] if "Bypass" in modes else [mode for mode in MODE_ORDER if mode in modes]
    )
    if cfg["entry_priority"] not in ("csv", "alphabetical", "reversed", "shuffle"):
        raise ValueError("Unknown entry priority")
    if cfg["exposure_fill_mode"] not in ("strict", "remaining"):
        raise ValueError("Unknown exposure fill mode")
    bounds = {
        "initial_capital": (1, 1e9),
        "order_size_pct": (0.01, 100),
        "target_pct": (0.01, 500),
        "stop_pct": (0.01, 99),
        "hold_sessions": (1, 252),
        "cost_bps": (0, 500),
        "slippage_bps": (0, 500),
        "trailing_pct": (0, 99),
        "priority_seed": (-2147483648, 2147483647),
        "max_exposure_pct": (0, 100),
    }
    for key, (low, high) in bounds.items():
        value = cfg[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not low <= value <= high
        ):
            raise ValueError(f"{key} must be a finite number between {low} and {high}")
    if int(cfg["hold_sessions"]) != cfg["hold_sessions"]:
        raise ValueError("hold_sessions must be an integer")
    if int(cfg["priority_seed"]) != cfg["priority_seed"]:
        raise ValueError("priority_seed must be an integer")
    cfg["priority_seed"] = int(cfg["priority_seed"])
    cfg["hold_sessions"] = int(cfg["hold_sessions"])
    return cfg


def convert_legacy_config(config, *, source_version):
    """Convert a named legacy policy's units; never reinterpret archived results."""
    if source_version != "eod_next_open_marked_v2":
        raise ValueError(
            "Unsupported legacy execution policy; explicit eod_next_open_marked_v2 required"
        )
    if not isinstance(config, dict):
        raise ValueError("Legacy configuration must be an object")
    values = {
        "tp": 0.1,
        "sl": 0.05,
        "max_hold": 5,
        "slippage": 0.0,
        "cost_bps": 0.0,
        "modes": ["Bypass"],
        "trailing_sl_enabled": False,
        "trailing_sl": 0.0,
        "initial_capital": 100000.0,
        "order_size_pct": 0.05,
        "max_exposure_pct": 1.0,
        "exposure_fill_mode": "strict",
        "entry_priority": "source_order",
        "priority_seed": 0,
    }
    if set(config) - set(values):
        raise ValueError("Unknown legacy execution field")
    values.update(config)
    for key in (
        "tp",
        "sl",
        "max_hold",
        "slippage",
        "cost_bps",
        "trailing_sl",
        "initial_capital",
        "order_size_pct",
        "max_exposure_pct",
        "priority_seed",
    ):
        if isinstance(values[key], bool) or not isinstance(values[key], (int, float)):
            raise ValueError(f"Legacy {key} must be numeric")
    priority = {
        "source_order": "csv",
        "symbol": "alphabetical",
        "alphabetical": "alphabetical",
        "reverse": "reversed",
        "shuffle": "shuffle",
    }.get(values["entry_priority"])
    fill = {"strict": "strict", "use_remaining": "remaining"}.get(values["exposure_fill_mode"])
    aliases = {"All Scan Results": "Bypass", "All signals": "Bypass"}
    try:
        converted = {
            "target_pct": values["tp"] * 100,
            "stop_pct": values["sl"] * 100,
            "hold_sessions": values["max_hold"],
            "slippage_bps": values["slippage"] * 10000,
            "cost_bps": values["cost_bps"],
            "initial_capital": values["initial_capital"],
            "order_size_pct": values["order_size_pct"] * 100,
            "max_exposure_pct": values["max_exposure_pct"] * 100,
            "trailing_enabled": values["trailing_sl_enabled"],
            "trailing_pct": values["trailing_sl"] * 100,
            "modes": [aliases.get(mode, mode) for mode in values["modes"]],
            "entry_priority": priority,
            "priority_seed": values["priority_seed"],
            "exposure_fill_mode": fill,
        }
    except (TypeError, AttributeError) as exc:
        raise ValueError("Invalid legacy configuration") from exc
    return validate_config(converted)


@dataclass(frozen=True)
class PreparedEvaluation:
    """Per-job bounded preparation; no global cache, files or external resources.

    Snapshot/signals must remain immutable for the lifetime of this object.
    """

    signals: object
    snapshot: object
    signal_history: object
    coverage: dict
    index: dict
    counts: tuple
    trough: tuple


def prepare_evaluation(signals, snapshot, signal_history=None):
    if snapshot.get("provenance", {}).get("interval") == "1m":
        from .intraday import prepare

        return prepare(signals, snapshot, signal_history)
    if not signals or len(signals) > MAX_SIGNALS:
        raise ValueError(f"Supply 1-{MAX_SIGNALS} normalized signals")
    coverage = validate_snapshot(snapshot, signals)
    sessions = snapshot["sessions"]
    index = {day: i for i, day in enumerate(sessions)}
    history = signals if signal_history is None else signal_history
    if len(history) > MAX_SIGNALS:
        raise ValueError(f"Scanner count history exceeds {MAX_SIGNALS} signals")
    counts_by_day = Counter(signal["date"] for signal in history)
    counts = tuple(counts_by_day[day] for day in sessions)
    trough = tuple(
        i >= 7 and 0 < count < 3 and count < sum(counts[i - 7 : i]) / 14
        for i, count in enumerate(counts)
    )
    return PreparedEvaluation(signals, snapshot, history, coverage, index, counts, trough)


def _evaluate_grid(
    signals,
    snapshot,
    config=None,
    progress=None,
    *,
    prepared=None,
    summary_only=False,
    signal_history=None,
    _temporal=None,
):
    cfg = validate_config(config)
    if prepared is None:
        prepared = prepare_evaluation(signals, snapshot, signal_history=signal_history)
    elif (
        prepared.signals is not signals
        or prepared.snapshot is not snapshot
        or (signal_history is not None and signal_history is not prepared.signal_history)
    ):
        raise ValueError("Prepared evaluation belongs to different immutable inputs")
    coverage = prepared.coverage
    sessions, bars = snapshot["sessions"], snapshot["bars"]
    index, counts, trough = prepared.index, prepared.counts, prepared.trough
    active_by_session = []
    for i, count in enumerate(counts):
        active = []
        if "Bypass" in cfg["modes"]:
            active = ["Bypass"]
        else:
            if "Bottom Fishing" in cfg["modes"] and trough[i]:
                active.append("Bottom Fishing")
            if "Zero Only" in cfg["modes"] and i > 0 and counts[i - 1] == 0 and count > 0:
                active.append("Zero Only")
            if (
                "Uptick" in cfg["modes"]
                and i > 0
                and (trough[i - 1] or counts[i - 1] == 0)
                and count > counts[i - 1]
            ):
                active.append("Uptick")
        active_by_session.append(active)
    fee, slip = cfg["cost_bps"] / 10000, cfg["slippage_bps"] / 10000
    initial = float(cfg["initial_capital"])
    cash, realized, peak = initial, 0.0, initial
    final_equity, max_drawdown = initial, 0.0
    positions, ledger, curve, entries = [], [], [], {}
    mark_gaps = []
    for signal in signals:
        row = {
            "symbol": signal["symbol"],
            "signal_date": signal["date"],
            "source_row": signal.get("row"),
            "status": "excluded",
            "reason": "Signal date is outside the recorded session calendar",
            "entry_date": None,
            "exit_date": None,
            "entry_price": None,
            "exit_price": None,
            "quantity": 0,
            "pnl": None,
            "fees": 0.0,
            "outcome": None,
        }
        ledger.append(row)
        if _temporal is not None:
            _temporal["schedule"](signal, row, entries)
            continue
        i = index.get(signal["date"])
        if i is not None:
            row["trigger_modes"] = active_by_session[i]
            if not active_by_session[i]:
                row.update(reason="Selected scanner-count trigger modes did not qualify")
                continue
            if i + 1 < len(sessions):
                entries.setdefault(sessions[i + 1], []).append(row)
                row.update(
                    status="pending",
                    reason="Awaiting next session entry",
                    entry_date=sessions[i + 1],
                )
            else:
                row.update(status="pending", reason="Next exchange session is outside the snapshot")

    def close_position(p, day, raw, outcome, timing):
        nonlocal cash, realized
        row = p["row"]
        fill = raw * (1 - slip)
        exit_fee = p["quantity"] * fill * fee
        proceeds = p["quantity"] * fill - exit_fee
        pnl = proceeds - p["cost"]
        cash += proceeds
        realized += pnl
        row.update(
            status="closed",
            reason=f"{outcome}; {timing} exit",
            outcome=outcome,
            exit_date=day,
            exit_price=round(fill, 6),
            exit_raw_price=raw,
            exit_timing=timing,
            pnl=round(pnl, 6),
            fees=round(row["fees"] + exit_fee, 6),
        )
        positions.remove(p)

    def active_stop(p):
        return max(p["stop"], p["trail"])

    for i, day in enumerate(sessions):
        if progress:
            progress(i, len(sessions))  # Callback may raise cancellation.
        # Only opening exits are available to finance today's opening orders.
        for p in positions[:]:
            bar = bars.get(p["row"]["symbol"], {}).get(day)
            if not bar:
                p["uncertain"] = True
                p["row"].update(
                    reason=f"Missing intervening candle from {p.get('first_gap', day)}; outcome remains pending"
                )
                p.setdefault("first_gap", day)
            elif not p["uncertain"]:
                op = float(bar["open"])
                if op <= active_stop(p):
                    close_position(
                        p, day, op, "trailing_stop" if p["trail"] > p["stop"] else "stop", "open"
                    )
                elif op >= p["target"]:
                    close_position(p, day, op, "target", "open")
                elif _temporal is not None and _temporal["open_due"](p, i):
                    close_position(p, day, op, "time", "open")
        opening_equity = cash + sum(
            p["quantity"]
            * float(bars.get(p["row"]["symbol"], {}).get(day, {}).get("open", p["mark"]))
            for p in positions
        )
        requested_budget = opening_equity * cfg["order_size_pct"] / 100
        exposure = opening_equity - cash
        entrants = list(entries.get(day, []))
        if cfg["entry_priority"] == "alphabetical":
            entrants.sort(key=lambda row: row["symbol"])
        elif cfg["entry_priority"] == "reversed":
            entrants.reverse()
        elif cfg["entry_priority"] == "shuffle":
            random.Random(f"{cfg['priority_seed']}:{day}").shuffle(entrants)
        for row in entrants:
            available = max(0.0, opening_equity * cfg["max_exposure_pct"] / 100 - exposure)
            budget = (
                min(requested_budget, available, cash)
                if cfg["exposure_fill_mode"] == "remaining"
                else requested_budget
            )
            row.update(
                requested_budget=round(requested_budget, 6),
                available_exposure=round(available, 6),
                funded_budget=round(budget, 6),
            )
            bar = bars.get(row["symbol"], {}).get(day)
            if not bar:
                row.update(reason="Missing next-session opening candle; no position funded")
                continue
            raw = float(bar["open"])
            fill = raw * (1 + slip)
            quantity = math.floor((budget + 1e-9) / (fill * (1 + fee)))
            if budget <= 0 or available <= 0 or budget > cash + 1e-8 or budget > available + 1e-8:
                row.update(
                    status="skipped",
                    reason="Insufficient opening cash or exposure under selected sizing policy",
                )
                continue
            if quantity < 1:
                row.update(
                    status="skipped",
                    reason="Position budget cannot fund one whole share including entry costs",
                )
                continue
            cost = quantity * fill * (1 + fee)
            cash -= cost
            exposure += cost
            row.update(
                status="pending",
                reason="Position open at snapshot end",
                entry_price=round(fill, 6),
                entry_raw_price=raw,
                quantity=quantity,
                fees=round(quantity * fill * fee, 6),
            )
            positions.append(
                {
                    "row": row,
                    "quantity": quantity,
                    "cost": cost,
                    "entry_i": i,
                    "target": round(fill * (1 + cfg["target_pct"] / 100), 2),
                    "stop": round(fill * (1 - cfg["stop_pct"] / 100), 2),
                    "trail": round(fill * (1 - cfg["trailing_pct"] / 100), 2)
                    if cfg["trailing_enabled"]
                    else 0,
                    "high": fill,
                    "mark": raw,
                    "uncertain": False,
                }
            )
        for p in positions[:]:
            bar = bars.get(p["row"]["symbol"], {}).get(day)
            if bar and not p["uncertain"]:
                op, lo, hi, cl = [float(bar[k]) for k in ("open", "low", "high", "close")]
                stop = active_stop(p)
                # Entry-day opening slippage can already put the raw open below
                # the effective-entry stop. Resolve it exactly as an opening gap.
                if op <= stop:
                    close_position(
                        p, day, op, "trailing_stop" if p["trail"] > p["stop"] else "stop", "open"
                    )
                elif op >= p["target"]:
                    close_position(p, day, op, "target", "open")
                elif lo <= stop:
                    close_position(
                        p,
                        day,
                        stop,
                        "trailing_stop" if p["trail"] > p["stop"] else "stop",
                        "intraday",
                    )
                elif hi >= p["target"]:
                    close_position(p, day, p["target"], "target", "intraday")
                elif (
                    _temporal["close_due"](p, i)
                    if _temporal is not None
                    else i - p["entry_i"] >= cfg["hold_sessions"]
                ):
                    close_position(p, day, cl, "hold", "close")
                else:
                    p["high"] = max(p["high"], hi)
                    if cfg["trailing_enabled"]:
                        p["trail"] = max(
                            p["trail"], round(p["high"] * (1 - cfg["trailing_pct"] / 100), 2)
                        )
        for p in positions:
            bar = bars.get(p["row"]["symbol"], {}).get(day)
            if bar:
                p["mark"] = float(bar["close"])
            else:
                if not summary_only:
                    mark_gaps.append({"date": day, "symbol": p["row"]["symbol"]})
        equity = cash + sum(p["quantity"] * p["mark"] for p in positions)
        peak = max(peak, equity)
        final_equity = round(equity, 6)
        daily_drawdown = round((peak - equity) / peak * 100, 6)
        max_drawdown = max(max_drawdown, daily_drawdown)
        if not summary_only:
            curve.append(
                {
                    "date": day,
                    "equity": final_equity,
                    "cash": round(cash, 6),
                    "realized_equity": round(initial + realized, 6),
                    "drawdown_pct": daily_drawdown,
                    "open_positions": len(positions),
                }
            )
    if progress:
        progress(len(sessions), len(sessions))
    closed = [r for r in ledger if r["status"] == "closed"]
    accepted = [r for r in ledger if r["quantity"] > 0]
    wins, losses = sum(max(0, r["pnl"]) for r in closed), -sum(min(0, r["pnl"]) for r in closed)
    summary = {
        "initial_capital": initial,
        "final_equity": final_equity,
        "net_return_pct": (final_equity / initial - 1) * 100,
        "max_drawdown_pct": max_drawdown,
        "realized_equity": round(initial + realized, 6),
        "accepted_trades": len(accepted),
        "closed_trades": len(closed),
        "pending_trades": len(positions),
        "unfunded_pending": sum(r["status"] == "pending" and not r["quantity"] for r in ledger),
        "skipped_trades": sum(r["status"] == "skipped" for r in ledger),
        "excluded_signals": sum(r["status"] == "excluded" for r in ledger),
        "win_rate_pct": sum(r["pnl"] > 0 for r in closed) / len(closed) * 100 if closed else None,
        "profit_factor": wins / losses if losses else None,
        "sample_adequacy": "Insufficient sample"
        if len(closed) < 30
        else "Historical sample available",
    }
    return {
        "policy_version": POLICY_VERSION,
        "metric_basis": "daily_marked",
        "config": cfg,
        "summary": summary,
        "equity_curve": [] if summary_only else curve,
        "ledger": [] if summary_only else ledger,
        "coverage": coverage,
        "missing_marks": mark_gaps,
        "limits": [
            "One fixed setup on the supplied sample does not establish robustness or independent holdout evidence.",
            "Scanner-count triggers, entry priority, exposure cap and cash fill policy follow the recorded configuration; whole shares, no leverage, repeated symbols may form separate lots.",
            "Signals enter next recorded session open. Stops precede targets inside ambiguous daily bars; gaps fill at open. Trailing changes start next bar.",
            "Holding duration counts elapsed exchange sessions after entry; missing intervening candles leave funded positions pending.",
            "Daily marked equity carries the last mark when a close is missing. Pending positions are not forcibly liquidated.",
            "No capacity, corporate action entitlement cashflows, settlement eligibility or historical scanner membership reconstruction.",
            "Costs and slippage apply per side; targets/stops use slipped entry price rounded to two decimals. Fees are explicit cash expenses, excluded from the target/stop anchor; calculations do not round fee-inclusive fills per share as the legacy engine did.",
        ],
    }


def policy_for_snapshot(snapshot):
    if snapshot.get("provenance", {}).get("interval") == "1m":
        from .intraday import POLICY_VERSION as minute_policy

        return minute_policy
    return POLICY_VERSION


def evaluate(
    signals,
    snapshot,
    config=None,
    progress=None,
    *,
    prepared=None,
    summary_only=False,
    signal_history=None,
):
    if snapshot.get("provenance", {}).get("interval") == "1m":
        from .intraday import evaluate_minutes

        return evaluate_minutes(
            signals,
            snapshot,
            config,
            progress,
            prepared=prepared,
            summary_only=summary_only,
            signal_history=signal_history,
        )
    cfg = validate_config(config)
    if (
        any(s.get("timestamp") for s in signals)
        or cfg.get("trade_horizon") == "intraday"
        or any(cfg.get(key) is not None for key in ("hold_minutes", "entry_time", "exit_time"))
    ):
        raise ValueError("Timestamped signals and intraday timing require a minute snapshot")
    return _evaluate_grid(
        signals,
        snapshot,
        cfg,
        progress,
        prepared=prepared,
        summary_only=summary_only,
        signal_history=signal_history,
    )
