"""Portfolio request and native-price requirements, independent of simulation.

Strategy identities survive across sources, engine orders, trial parameters and
reports. This module plans inputs; external connectors own account calculation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import date, timedelta

from research.data import MAX_BARS
from research.engine import validate_config
from research.requirements import select_interval

VERSION = "research-portfolio-v1"
MAX_STRATEGIES = 8
KINDS = {"portfolio_backtest", "portfolio_optimize"}


def fingerprint(value):
    digest = hashlib.sha256()
    for part in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(
        value
    ):
        digest.update(part.encode())
    return digest.hexdigest()


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - {
        "version",
        "name",
        "capital",
        "engine",
        "strategies",
        "optimization",
        "date_from",
        "date_to",
        "validation",
    }:
        raise ValueError("Invalid portfolio settings")
    if raw.get("version", VERSION) != VERSION:
        raise ValueError("This portfolio format is not supported")
    capital = raw.get("capital", 100000)
    if (
        isinstance(capital, bool)
        or not isinstance(capital, (int, float))
        or not math.isfinite(capital)
        or not 1 <= capital <= 1e9
    ):
        raise ValueError("Starting capital must be between 1 and 1,000,000,000")
    engine = raw.get("engine", "vectorbt")
    if engine not in ("vectorbt", "nautilus"):
        raise ValueError("Choose an installed portfolio engine")
    rows = raw.get("strategies")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_STRATEGIES:
        raise ValueError(f"Add between one and {MAX_STRATEGIES} strategies")
    strategies, identifiers = [], set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - {
            "id",
            "name",
            "source_id",
            "allocation_pct",
            "config",
            "search",
            "type",
        }:
            raise ValueError("Invalid strategy settings")
        identifier = row.get("id")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
            or identifier in identifiers
        ):
            raise ValueError("Each strategy needs a distinct identifier")
        identifiers.add(identifier)
        if row.get("type", "signals") != "signals":
            raise ValueError("This strategy type is not supported")
        source = row.get("source_id")
        if not isinstance(source, str) or not re.fullmatch(r"[a-f0-9]{32}", source):
            raise ValueError("Upload a signal file for each strategy")
        name = row.get("name", f"Strategy {i + 1}")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Strategy names must contain 1–80 characters")
        allocation = row.get("allocation_pct", 100 / len(rows))
        if (
            isinstance(allocation, bool)
            or not isinstance(allocation, (int, float))
            or not math.isfinite(allocation)
            or not 0 <= allocation <= 100
        ):
            raise ValueError("Strategy allocation must be between 0% and 100%")
        if not isinstance(row.get("config", {}), dict):
            raise ValueError("Strategy trade settings must be an object")
        config = validate_config({**row.get("config", {}), "initial_capital": capital})
        if engine == "nautilus":
            from research.connectors.nautilus_portfolio import validate_config as native_config

            native_config(config)
        if (
            config["modes"] != ["Bypass"]
            or config["entry_priority"] != "csv"
            or config["priority_seed"] != 0
            or config["max_exposure_pct"] != 100
            or config["exposure_fill_mode"] != "strict"
        ):
            raise ValueError(
                "This portfolio connector supports signals in file order without scanner-count filters"
            )
        if config["trailing_enabled"] and config["trailing_pct"] <= 0:
            raise ValueError("Set a positive trailing stop distance or turn it off")
        search = row.get("search", {})
        if not isinstance(search, dict):
            raise ValueError("Strategy search settings must be an object")
        strategies.append(
            {
                "id": identifier,
                "name": name.strip(),
                "type": "signals",
                "source_id": source,
                "allocation_pct": float(allocation),
                "config": config,
                "search": deepcopy(search),
            }
        )
    if not 0 < sum(row["allocation_pct"] for row in strategies) <= 100 + 1e-8:
        raise ValueError("Strategy allocations must total more than 0% and at most 100%")
    name = raw.get("name", "My portfolio")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Portfolio name must contain 1–100 characters")
    result = {
        "version": VERSION,
        "name": name.strip(),
        "capital": float(capital),
        "engine": engine,
        "strategies": strategies,
    }
    for key in ("date_from", "date_to"):
        value = raw.get(key)
        if value:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use valid start and end dates")
            result[key] = value
    if result.get("date_from", "") > result.get("date_to", "9999-12-31"):
        raise ValueError("The start date must come before the end date")
    if raw.get("optimization") is not None:
        from research.connectors.optuna_portfolio import validate_specification

        result["optimization"] = validate_specification(raw["optimization"], strategies)
    elif any(row["search"] for row in strategies):
        # A fixed run uses the entered values; ranges stay in the browser draft.
        for row in strategies:
            row["search"] = {}
    if raw.get("validation") is not None:
        validation = raw["validation"]
        if (
            not isinstance(validation, dict)
            or set(validation) - {"train_pct", "mode"}
            or "train_pct" not in validation
            or type(validation["train_pct"]) is not int
            or not 50 <= validation["train_pct"] <= 90
            or validation.get("mode", "evaluate") not in ("reserve", "evaluate")
        ):
            raise ValueError("The earlier period must use 50–90% of the signal dates")
        result["validation"] = dict(validation)
    return result


def requirement_strategies(portfolio, strategies):
    if portfolio.get("optimization"):
        from research.connectors.optuna_portfolio import requirement_strategies as extremes

        return extremes(strategies, portfolio["optimization"])
    return strategies


def interval_for(portfolio, strategies):
    required = requirement_strategies(portfolio, strategies)
    return (
        "1m"
        if any(
            select_interval(row["signals"], {"config": row["config"]}) == "1m" for row in required
        )
        else "D"
    )


def calendar_tail_sessions(portfolio, strategies):
    """Bound session lookahead by every allowed holding rule, including entry."""
    return max(
        1
        if row["config"].get("trade_horizon") == "intraday"
        else row["config"]["hold_sessions"] + 1
        for row in requirement_strategies(portfolio, strategies)
    )


def calendar_completion_check(portfolio, strategies):
    """Resolve every allowed entry/deadline as a native calendar grows.

    The returned check is local to one append-only calendar construction. Prices
    are not needed. Resolved entries are retained so a long holding range does
    not repeatedly scan earlier sessions for every signal.
    """
    from research.intraday import bounds, entry_open

    required = requirement_strategies(portfolio, strategies)
    minute = any(
        select_interval(row["signals"], {"config": row["config"]}) == "1m" for row in required
    )
    pending = [
        {"signal": signal, "config": row["config"], "entry": None}
        for row in required
        for signal in row["signals"]
    ]

    def complete(sessions, session_hours):
        nonlocal pending
        index = {day: i for i, day in enumerate(sessions)}
        calendar = {"sessions": sessions, "session_hours": session_hours}
        final_bar = bounds(calendar, sessions[-1])[1] - timedelta(minutes=1) if minute else None
        unresolved = []
        for item in pending:
            signal, cfg = item["signal"], item["config"]
            if signal["date"] not in index:
                # Construction calls this only after all signal dates have
                # passed. A non-session signal is excluded by the engine.
                continue
            if not minute:
                if index[signal["date"]] + 1 + cfg["hold_sessions"] >= len(sessions):
                    unresolved.append(item)
                continue
            entry = item["entry"]
            if entry is None:
                entry = entry_open(signal, calendar, cfg)
                if entry is None:
                    unresolved.append(item)
                    continue
                item["entry"] = entry
            if cfg.get("exit_time") and entry.strftime("%H:%M") >= cfg["exit_time"]:
                # The engine excludes this entry instead of delaying it.
                continue
            entry_index = index[entry.date().isoformat()]
            if cfg.get("trade_horizon") == "intraday":
                continue
            if len(sessions) - 1 - entry_index >= cfg["hold_sessions"]:
                continue
            if cfg.get("hold_minutes") is not None and final_bar >= entry + timedelta(
                minutes=cfg["hold_minutes"]
            ):
                continue
            if cfg.get("exit_time") and any(
                session_hours[day]["close"] > cfg["exit_time"] for day in sessions[entry_index:]
            ):
                continue
            unresolved.append(item)
        pending = unresolved
        return not pending

    return complete


def price_plan(portfolio, strategies, calendar):
    """Union every strategy/candidate requirement at the account's finest interval."""
    from research.connectors.vectorbt_portfolio import _deadline, _schedule
    from research.intraday import bounds

    interval = interval_for(portfolio, strategies)
    minute = interval == "1m"
    timeline = []
    if minute:
        for day in calendar["sessions"]:
            cursor, end = bounds(calendar, day)
            while cursor < end:
                timeline.append(cursor.isoformat())
                cursor += timedelta(minutes=1)
    else:
        timeline = calendar["sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    required = {}
    for row in requirement_strategies(portfolio, strategies):
        for signal in row["signals"]:
            slots = required.setdefault(signal["symbol"], set())
            entry, _ = _schedule(signal, calendar, row["config"], timeline, index, minute)
            if entry < 0:
                continue
            deadline = _deadline(entry, calendar, row["config"], timeline, minute)
            slots.update(timeline[entry : deadline + 1])
    if sum(map(len, required.values())) > MAX_BARS:
        raise ValueError(
            "This portfolio needs too many prices. Shorten the date range or holding periods."
        )
    result = {
        "version": VERSION,
        "request_key": fingerprint(portfolio),
        "interval": interval,
        "required_dates" if interval == "D" else "required_timestamps": {
            symbol: sorted(slots) for symbol, slots in sorted(required.items())
        },
    }
    if interval == "1m":
        # Calendar padding is useful when resolving entry/holding dates, but it
        # must not turn a one-hour test into months of empty minute simulation.
        windows = [slots for slots in required.values() if slots]
        if windows:
            first = min(min(slots) for slots in windows)
            last = max(max(slots) for slots in windows)
            timeline = timeline[index[first] : index[last] + 1]
        result.update(
            timeline=timeline,
            session_hours=calendar["session_hours"],
            reasons=["portfolio_execution_requirements"],
        )
    return result


def execution_versions(portfolio, recorded=None):
    from research.connectors.registry import package_status
    from research.connectors.vectorbt_portfolio import ADAPTER_VERSION, POLICY_VERSION

    engine = portfolio["engine"]
    if engine == "nautilus":
        from research.connectors.nautilus_portfolio import ADAPTER_VERSION, POLICY_VERSION
        from research.connectors.nautilus_runtime import status as runtime_status

        runtime = runtime_status()
        if not runtime["available"]:
            raise ValueError(runtime["reason"])
    versions = {
        "engine": engine,
        "adapter_version": ADAPTER_VERSION,
        "policy_version": POLICY_VERSION,
        "portfolio_version": VERSION,
    }
    for name in (engine, "optuna") if portfolio.get("optimization") else (engine,):
        if name == "nautilus":
            versions["engine_version"] = runtime["tested_version"]
            continue
        status = package_status(name)
        if not status["available"]:
            raise ValueError(
                f"{name} is unavailable. Install this release's research dependencies before running."
            )
        versions["engine_version" if name == engine else "optimizer_version"] = status[
            "installed_version"
        ]
    if portfolio.get("optimization"):
        from research.connectors.optuna_portfolio import ADAPTER_VERSION as OPTUNA_ADAPTER_VERSION

        versions.update(optimizer="optuna", optimizer_adapter_version=OPTUNA_ADAPTER_VERSION)
    if recorded is not None and versions != recorded:
        raise ValueError(
            "This saved run needs its original engine versions. Start a new run to use the current release."
        )
    return versions


def capabilities():
    from research.connectors.nautilus_runtime import status
    from research.connectors.registry import package_status

    return {
        "max_strategies": MAX_STRATEGIES,
        "engines": [
            {
                "id": "vectorbt",
                "name": "VectorBT",
                **package_status("vectorbt"),
                "intervals": ["D", "1m"],
                "markets": ["NSE"],
                "account": "long_cash",
                "shared_cash": True,
            },
            {
                "id": "nautilus",
                "name": "NautilusTrader",
                **status(),
                "intervals": ["D", "1m"],
                "markets": ["NSE"],
                "account": "long_cash",
                "shared_cash": True,
                "trailing_stops": False,
                "slippage_bps": [0],
            },
        ],
        "optimizers": [{"id": "optuna", **package_status("optuna"), "samplers": ["tpe", "grid"]}],
    }
