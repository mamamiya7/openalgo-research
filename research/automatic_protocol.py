"""Frozen, bounded execution-management research over native price evidence.

This module plans and slices evidence. It does not download, simulate, propose
parameters, or retain resources. Optuna and the selected native engine do that.
"""

from copy import deepcopy
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

VERSION = "automatic-trade-management-v1"
PERIODS = ("search", "check1", "check2", "final")
MIN_SESSIONS = 100


def normalize_auto(raw, strategies=None, engine="vectorbt"):
    """Accept only the named recipe; budgets and evidence gates are versioned."""
    if not isinstance(raw, dict) or set(raw) != {"version"} or raw["version"] != VERSION:
        raise ValueError("Choose the supported automatic research recipe")
    if engine not in ("vectorbt", "nautilus"):
        raise ValueError("Automatic research needs a supported portfolio engine")
    return {"version": VERSION}


def _range(value, ceiling, *, integer=False):
    """At most seven deterministic points around the unchanged baseline value."""
    scale = 1 if integer else 100
    units = max(1, int((Decimal(str(value)) * scale).to_integral_value(rounding=ROUND_HALF_UP)))
    low, high = max(1, units // 2), min(ceiling * scale, units * 2)
    step = max(1, (high - low + 5) // 6)
    high = low + ((high - low) // step) * step
    return {
        "min": low if integer else low / scale,
        "max": high if integer else high / scale,
        "step": step if integer else step / scale,
    }


def automatic_portfolio(portfolio):
    """Return canonical automatic ranges without changing baseline or account risk.

    Run after ordinary portfolio/config normalization. Repeated normalization is
    identical; manual search ranges never influence this recipe's hypothesis set.
    """
    result = deepcopy(portfolio)
    if "automatic_research" not in result:
        return result
    result["automatic_research"] = normalize_auto(
        result["automatic_research"], result.get("strategies"), result.get("engine", "vectorbt")
    )
    if result.get("validation"):
        raise ValueError("Automatic research sets its own chronological checks")
    for row in result["strategies"]:
        cfg = row["config"]
        # The shared account is authoritative, as in both native engines. Keep
        # exact settings hashes consistent with their float-valued report field.
        cfg["initial_capital"] = float(result["capital"])
        if cfg["cost_bps"] > 250:
            raise ValueError(
                "Automatic research supports base transaction costs up to 250 bps so its "
                "higher-cost check stays within the engine's 500 bps limit."
            )
        search = {
            "target_pct": _range(cfg["target_pct"], 500),
            "stop_pct": _range(cfg["stop_pct"], 99),
        }
        if cfg.get("hold_minutes") is not None:
            search["hold_minutes"] = _range(cfg["hold_minutes"], 100000, integer=True)
        elif cfg.get("trade_horizon") != "intraday":
            search["hold_sessions"] = _range(cfg["hold_sessions"], 252, integer=True)
        if cfg["trailing_enabled"]:
            search["trailing_pct"] = _range(cfg["trailing_pct"], 99)
        row["search"] = search
    result["optimization"] = {
        "sampler": "tpe",
        "trials": 50,
        "objective": "balanced",
        "seed": 0,
    }
    return result


def compile_recipe(evidence):
    """Freeze session boundaries and input identity before the first score exists."""
    from research.portfolio import fingerprint, requirement_strategies

    portfolio = evidence["portfolio"]
    if "automatic_research" not in portfolio or automatic_portfolio(portfolio) != portfolio:
        raise ValueError("Automatic research settings must be normalized before preparing prices")
    signals, snapshot = evidence["signals"], evidence["snapshot"]
    if not signals:
        raise ValueError("Automatic research needs dated scanner signals")
    first, last = min(row["date"] for row in signals), max(row["date"] for row in signals)
    sessions = [day for day in snapshot["sessions"] if first <= day <= last]
    if sessions != sorted(set(sessions)) or len(sessions) < MIN_SESSIONS:
        raise ValueError(
            f"Automatic research needs at least {MIN_SESSIONS} trading sessions between the first "
            f"and last signal; this file covers {len(sessions)}. Use a longer signal history."
        )
    count = len(sessions)
    cuts = (0, count * 60 // 100, count * 70 // 100, count * 80 // 100, count)
    periods = {
        name: {
            "from": sessions[cuts[i]],
            "to": sessions[cuts[i + 1] - 1],
            "sessions": cuts[i + 1] - cuts[i],
        }
        for i, name in enumerate(PERIODS)
    }
    requirements = requirement_strategies(portfolio, evidence["strategies"])
    if snapshot["provenance"]["interval"] == "D":
        longest = max(row["config"]["hold_sessions"] for row in requirements)
        shortest = min(periods[name]["sessions"] for name in ("check1", "check2"))
        if shortest <= longest + 1:
            raise ValueError(
                f"Automatic research checks have {shortest} trading sessions, but the search "
                f"allows a {longest}-session hold plus next-session entry. Use a longer signal "
                "history or a shorter baseline holding period."
            )
    payload = {
        "version": VERSION,
        "scope": "execution management of supplied scanner signals",
        "periods": periods,
        "basis": {
            "split": "recorded exchange sessions between first and last signal",
            "session_count": count,
            "signal_dates": len({row["date"] for row in signals}),
            "inputs_sha256": fingerprint(
                {
                    "portfolio": portfolio,
                    "strategies": evidence["strategies"],
                    "signals": signals,
                    "snapshot": snapshot,
                    "versions": evidence.get("versions", {}),
                }
            ),
        },
        "search": deepcopy(portfolio["optimization"]),
        "ranges": [
            {"id": row["id"], "search": deepcopy(row["search"])} for row in portfolio["strategies"]
        ],
        "baseline": [
            {key: deepcopy(row[key]) for key in ("id", "name", "allocation_pct", "config")}
            for row in portfolio["strategies"]
        ],
        "selection": {
            "max_finalists": 3,
            "min_closed_trades": 5,
            "min_signal_dates": 5,
            "max_drawdown_pct": 25,
        },
        "budget": {"proposals": 50, "additional_simulations": 20, "max_simulations": 70},
        "policies": {
            "positions": "fresh capital in every period; no carried positions",
            "purge": "maximum allowed holding deadline must fit wholly inside each period",
            "coverage": "fixed maximum-horizon cohort; preserve original exclusions",
            "selection": "both development checks precede the frozen final choice",
            "final": "evaluate the fixed selection once; never rerank using final results",
            "cost_stress": "check2 at max(2 * cost_bps, cost_bps + 5); slippage unchanged",
            "risk": "capital, allocations, order sizing and exposure limits remain fixed",
            "unsupported": "no indicator, regime, benchmark or original-scanner discovery",
        },
    }
    return {**payload, "id": fingerprint(payload)}


def _complete_deadline(entry, deadline, snapshot, cfg, timeline, minute):
    """Do not mistake the engine's clipped last bar for a complete holding limit."""
    if not minute:
        return entry + cfg["hold_sessions"] < len(timeline)
    from research.intraday import bounds, stamp

    entered, ended = stamp(timeline[entry]), stamp(timeline[deadline])
    if cfg.get("hold_minutes") is not None and ended >= entered + timedelta(
        minutes=cfg["hold_minutes"]
    ):
        return True
    if cfg.get("exit_time") and ended.strftime("%H:%M") >= cfg["exit_time"]:
        return True
    days = {day: i for i, day in enumerate(snapshot["sessions"])}
    end_of_session = ended + timedelta(minutes=1) == bounds(snapshot, timeline[deadline][:10])[1]
    return end_of_session and (
        cfg.get("trade_horizon") == "intraday"
        or days[timeline[deadline][:10]] - days[timeline[entry][:10]] >= cfg["hold_sessions"]
    )


def slice_period(evidence, name):
    """Prepare one fresh-capital cohort, purged against the full frozen timeline."""
    from research.connectors.vectorbt_portfolio import _deadline, _schedule
    from research.portfolio import requirement_strategies
    from research.portfolio_coverage import prepare
    from research.portfolio_validation import _snapshot

    if name not in PERIODS:
        raise ValueError("Choose a recorded automatic research period")
    recipe = compile_recipe(evidence)
    if evidence.get("automatic_recipe") is not None and evidence["automatic_recipe"] != recipe:
        raise ValueError("Saved automatic research recipe or frozen inputs changed")
    period = recipe["periods"][name]
    first, last = period["from"], period["to"]
    snapshot = evidence["snapshot"]
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    requirements = {
        row["id"]: row
        for row in requirement_strategies(evidence["portfolio"], evidence["strategies"])
    }
    strategies, purged = [], 0
    for row in evidence["strategies"]:
        chosen = []
        cfg = requirements[row["id"]]["config"]
        for original in row["signals"]:
            if not first <= original["date"] <= last:
                continue
            signal = dict(original)
            if not signal.get("research_exclusion"):
                entry, reason = _schedule(signal, snapshot, cfg, timeline, index, minute)
                if reason:
                    signal["research_exclusion"] = reason
                else:
                    deadline = (
                        _deadline(entry, snapshot, cfg, timeline, minute) if entry >= 0 else -1
                    )
                    if (
                        entry < 0
                        or timeline[entry][:10] > last
                        or timeline[deadline][:10] > last
                        or not _complete_deadline(entry, deadline, snapshot, cfg, timeline, minute)
                    ):
                        signal["research_exclusion"] = (
                            f"Maximum holding window crosses the {name} period end ({last})"
                        )
                        purged += 1
            chosen.append(signal)
        strategies.append({**row, "signals": chosen})
    selected = {
        **evidence,
        "strategies": strategies,
        "signals": [
            dict(signal, strategy_id=row["id"]) for row in strategies for signal in row["signals"]
        ],
        "snapshot": _snapshot(snapshot, first, last),
        "automatic_recipe": recipe,
    }
    if not selected["signals"]:
        raise ValueError(f"The {name} research period has no scanner signals")
    try:
        prepared = prepare(selected)
    except ValueError as error:
        if str(error).startswith("No signals have complete broker prices"):
            raise ValueError(
                f"The {name} research period has no complete signal outcomes within its "
                "dates. Use a longer signal history or a shorter holding period."
            ) from error
        raise
    eligible_dates = {
        signal["date"]
        for row in prepared["strategies"]
        for signal in row["signals"]
        if not signal.get("research_exclusion")
    }
    prepared["automatic_period"] = {
        "name": name,
        **period,
        "eligible_signal_dates": len(eligible_dates),
        "purged_signals": purged,
    }
    return prepared
