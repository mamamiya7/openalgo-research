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

from research.canonical_json import (
    bounded_canonical_bytes,
    bounded_canonical_fragments,
    prepared_canonical_fragments,
)
from research.data import MAX_BARS
from research.engine import validate_config
from research.requirements import select_interval

VERSION = "research-portfolio-v1"
MAX_STRATEGIES = 8
KINDS = {"portfolio_backtest", "portfolio_optimize"}


def fingerprint(value):
    owned = prepared_canonical_fragments(value, max_bytes=64 * 1024**2, allow_native_float64=True)
    if owned is not None:
        digest = hashlib.sha256()
        for part in owned:
            digest.update(part)
        return digest.hexdigest()
    # The medium encoder has the same detached copier and representation.
    # Trying the small bound first repeats its whole prefix on larger receipts.
    encoded = bounded_canonical_bytes(value, allow_native_float64=True)
    if encoded is not None:
        return hashlib.sha256(encoded).hexdigest()
    fragments = bounded_canonical_fragments(
        value, max_bytes=64 * 1024**2, allow_native_float64=True
    )
    if fragments is not None:
        digest = hashlib.sha256()
        for part in fragments:
            digest.update(part)
        return digest.hexdigest()
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
        "automatic_research",
        "entry_conditions",
        "risk_limits",
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
            "entry_condition",
            "execution_policy",
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
        if "execution_policy" in row:
            from research.execution_policy import normalize_policy

            strategies[-1]["execution_policy"] = normalize_policy(row["execution_policy"])
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
    if raw.get("automatic_research") is not None and not isinstance(
        raw["automatic_research"], dict
    ):
        raise ValueError("Choose a supported automatic research recipe")
    execution_aware = (
        (raw.get("automatic_research") or {}).get("version")
        in (
            "automatic-hypotheses-v10",
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
            "automatic-hypotheses-v16",
        )
        or "risk_limits" in raw
        or any("execution_policy" in row for row in strategies)
    )
    if execution_aware:
        allocation_request = (raw.get("automatic_research") or {}).get(
            "version"
        ) == "automatic-hypotheses-v16"
        capacity_request = (raw.get("automatic_research") or {}).get("version") in (
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
            "automatic-hypotheses-v16",
        )
        turnover_request = (raw.get("automatic_research") or {}).get("version") in (
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
            "automatic-hypotheses-v16",
        )
        priority_request = (raw.get("automatic_research") or {}).get("version") in (
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
            "automatic-hypotheses-v16",
        )
        policies = ["execution_policy" in row for row in result["strategies"]]
        if any(policies) and not all(policies):
            raise ValueError(
                "Every strategy must declare its execution policy when any strategy uses one"
            )
        from research.execution_policy import VERSION as EXECUTION_POLICY_VERSION
        from research.execution_policy import normalize_limits, normalize_policy

        if engine != "vectorbt":
            raise ValueError("Execution-aware exits and portfolio limits currently need VectorBT")
        if raw.get("automatic_research") and raw["automatic_research"]["version"] not in (
            "automatic-hypotheses-v10",
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
            "automatic-hypotheses-v16",
        ):
            raise ValueError("Choose execution-aware automatic research to use portfolio limits")
        raw_limits = raw.get("risk_limits")
        if raw_limits is None and capacity_request:
            raw_limits = {"version": "research-risk-limits-v3"}
        if raw_limits is None and turnover_request:
            raw_limits = {"version": "research-risk-limits-v2"}
        limits = normalize_limits(raw_limits)
        result["risk_limits"] = limits
        for row in strategies:
            policy = row.get("execution_policy") or {
                "version": "research-execution-aware-v5"
                if allocation_request
                else "research-execution-aware-v4"
                if capacity_request
                else "research-execution-aware-v3"
                if turnover_request
                else "research-execution-aware-v2"
                if priority_request
                else EXECUTION_POLICY_VERSION,
                "exit_mode": "percent",
                "sizing": "allocation",
                "risk_limits": limits,
                **({"allocation_mode": "original"} if allocation_request else {}),
                **(
                    {
                        "capacity_mode": "prior20_broker_reported",
                        "correlation_mode": "prior60_positive_080",
                    }
                    if capacity_request
                    else {}
                ),
            }
            row["execution_policy"] = normalize_policy(policy)
            if row["execution_policy"]["risk_limits"] != limits:
                raise ValueError("Every strategy must use the same portfolio risk limits")
        account_policies = {
            (
                row["execution_policy"]["version"],
                row["execution_policy"].get("entry_priority"),
                row["execution_policy"].get("capacity_mode"),
                row["execution_policy"].get("correlation_mode"),
                row["execution_policy"].get("allocation_mode"),
            )
            for row in strategies
        }
        if len(account_policies) != 1:
            raise ValueError(
                "Every strategy must share one execution policy version and entry priority"
            )
        if raw.get("automatic_research") and any(
            row["execution_policy"]["version"]
            != (
                "research-execution-aware-v5"
                if allocation_request
                else "research-execution-aware-v4"
                if capacity_request
                else "research-execution-aware-v3"
                if turnover_request
                else "research-execution-aware-v2"
                if priority_request
                else EXECUTION_POLICY_VERSION
            )
            for row in strategies
        ):
            raise ValueError(
                "Choose the execution policy version matching this automatic research scope"
            )
        if raw.get("automatic_research") and any(
            row["execution_policy"]["exit_mode"] != "percent"
            or row["execution_policy"]["sizing"] != "allocation"
            or row["execution_policy"].get("entry_priority", "source") != "source"
            or row["execution_policy"].get("allocation_mode", "original") != "original"
            for row in strategies
        ):
            raise ValueError(
                "Use a fixed backtest or Custom search to preserve these selected execution rules"
            )
    if raw.get("entry_conditions") is not None:
        from research.entry_conditions import normalize as normalize_conditions

        result["entry_conditions"] = normalize_conditions(
            raw["entry_conditions"], strategies, engine
        )
        from research.entry_conditions import markers

        expected = markers(strategies, result["entry_conditions"])
        for original, canonical, marked in zip(rows, strategies, expected, strict=True):
            if "entry_condition" in original:
                if original["entry_condition"] != marked.get("entry_condition"):
                    raise ValueError("The saved strategy entry condition changed")
                canonical["entry_condition"] = deepcopy(original["entry_condition"])
    elif any("entry_condition" in row for row in rows):
        raise ValueError("Retain the complete entry-condition recipe with these settings")
    for key in ("date_from", "date_to"):
        value = raw.get(key)
        if value:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use valid start and end dates")
            result[key] = value
    if result.get("date_from", "") > result.get("date_to", "9999-12-31"):
        raise ValueError("The start date must come before the end date")
    if raw.get("automatic_research") is not None:
        from research.automatic_protocol import automatic_portfolio

        if raw.get("validation") is not None:
            raise ValueError("Automatic research sets its own chronological checks")
        result["automatic_research"] = deepcopy(raw["automatic_research"])
        return automatic_portfolio(result)
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
    if any("execution_policy" in row for row in portfolio["strategies"]):
        from research.connectors.vectorbt_portfolio import (
            EXECUTION_ADAPTER_VERSION,
            EXECUTION_POLICY_VERSION,
        )

        if engine != "vectorbt":
            raise ValueError("Execution-aware portfolio policies currently need VectorBT")
        ADAPTER_VERSION, POLICY_VERSION = EXECUTION_ADAPTER_VERSION, EXECUTION_POLICY_VERSION
        if (
            portfolio["strategies"][0]["execution_policy"]["version"]
            == "research-execution-aware-v2"
        ):
            from research.execution_policy import PRIORITY_ADAPTER_VERSION, PRIORITY_POLICY_VERSION

            ADAPTER_VERSION, POLICY_VERSION = PRIORITY_ADAPTER_VERSION, PRIORITY_POLICY_VERSION
        if (
            portfolio["strategies"][0]["execution_policy"]["version"]
            == "research-execution-aware-v3"
        ):
            from research.execution_policy import TURNOVER_ADAPTER_VERSION, TURNOVER_POLICY_VERSION

            ADAPTER_VERSION, POLICY_VERSION = TURNOVER_ADAPTER_VERSION, TURNOVER_POLICY_VERSION
    if any(
        row.get("execution_policy", {}).get("version")
        in ("research-execution-aware-v4", "research-execution-aware-v5")
        for row in portfolio["strategies"]
    ):
        from research.execution_policy import CAPACITY_ADAPTER_VERSION, CAPACITY_POLICY_VERSION

        ADAPTER_VERSION, POLICY_VERSION = CAPACITY_ADAPTER_VERSION, CAPACITY_POLICY_VERSION
    if any(
        row.get("execution_policy", {}).get("version") == "research-execution-aware-v5"
        for row in portfolio["strategies"]
    ):
        ADAPTER_VERSION, POLICY_VERSION = (
            "vectorbt-portfolio-adapter-v6",
            "vectorbt-joint-causal-bars-v6",
        )
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
    if any(
        row.get("execution_policy", {}).get("version")
        in ("research-execution-aware-v4", "research-execution-aware-v5")
        for row in portfolio["strategies"]
    ):
        versions["portfolio_features_version"] = "research-portfolio-execution-features-v1"
    if any("execution_policy" in row for row in portfolio["strategies"]):
        from research.execution_features import VERSION as EXECUTION_FEATURE_VERSION
        from research.stock_indicators import runtime_version

        versions["execution_features_version"] = EXECUTION_FEATURE_VERSION
        versions["indicator_runtime_version"] = runtime_version()
    if portfolio.get("automatic_research"):
        versions["automatic_research_version"] = portfolio["automatic_research"]["version"]
    if (
        portfolio.get("entry_conditions")
        or portfolio.get("automatic_research", {}).get("version") == "automatic-entry-conditions-v1"
    ):
        versions["entry_conditions_version"] = portfolio.get("entry_conditions", {}).get(
            "version", "research-entry-conditions-v1"
        )
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
    stock = portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v2",
        "automatic-hypotheses-v3",
        "automatic-hypotheses-v4",
        "automatic-hypotheses-v5",
        "automatic-hypotheses-v6",
        "automatic-hypotheses-v7",
        "automatic-hypotheses-v8",
        "automatic-hypotheses-v9",
        "automatic-hypotheses-v10",
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
        "automatic-hypotheses-v14",
        "automatic-hypotheses-v15",
        "automatic-hypotheses-v16",
    ) or portfolio.get("entry_conditions", {}).get("version") in (
        "research-stock-entry-conditions-v1",
        "research-stock-entry-conditions-v2",
        "research-stock-entry-conditions-v3",
        "research-joint-entry-conditions-v1",
        "research-joint-entry-conditions-v2",
    )
    if stock:
        versions["stock_features_version"] = "research-stock-features-v1"
        versions["entry_conditions_version"] = "research-stock-entry-conditions-v1"
    expanded = (
        portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v3"
        or portfolio.get("entry_conditions", {}).get("version")
        == "research-stock-entry-conditions-v2"
    )
    relative = (
        portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v4"
        or portfolio.get("entry_conditions", {}).get("version")
        == "research-stock-entry-conditions-v3"
    )
    joint = portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v5",
        "automatic-hypotheses-v6",
        "automatic-hypotheses-v7",
        "automatic-hypotheses-v8",
        "automatic-hypotheses-v9",
        "automatic-hypotheses-v10",
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
        "automatic-hypotheses-v14",
        "automatic-hypotheses-v15",
        "automatic-hypotheses-v16",
    ) or portfolio.get("entry_conditions", {}).get("version") in (
        "research-joint-entry-conditions-v1",
        "research-joint-entry-conditions-v2",
    )
    if expanded or relative or joint:
        from research.stock_indicators import ENTRY_VERSION, FEATURE_VERSION, runtime_version

        versions.update(
            entry_conditions_version=ENTRY_VERSION,
            stock_indicators_version=FEATURE_VERSION,
            indicator_runtime_version=runtime_version(),
        )
    if relative or joint:
        from research.stock_relative_strength import ENTRY_VERSION, FEATURE_VERSION
        from research.stock_relative_strength import VERSION as CONTEXT_VERSION

        versions.update(
            entry_conditions_version=ENTRY_VERSION,
            stock_features_version=CONTEXT_VERSION,
            relative_strength_version=FEATURE_VERSION,
        )
    if joint:
        from research.joint_stock_market import ENTRY_VERSION, FEATURE_VERSION
        from research.regimes import VERSION as REGIME_VERSION
        from research.stock_market_context import VERSION as CONTEXT_VERSION

        if (
            portfolio.get("automatic_research", {}).get("version")
            in (
                "automatic-hypotheses-v9",
                "automatic-hypotheses-v10",
                "automatic-hypotheses-v11",
                "automatic-hypotheses-v12",
                "automatic-hypotheses-v13",
                "automatic-hypotheses-v14",
                "automatic-hypotheses-v15",
                "automatic-hypotheses-v16",
            )
            or portfolio.get("entry_conditions", {}).get("version")
            == "research-joint-entry-conditions-v2"
        ):
            from research.expanded_stock_market import ENTRY_VERSION, FEATURE_VERSION

        versions.update(
            entry_conditions_version=ENTRY_VERSION,
            stock_features_version=CONTEXT_VERSION,
            joint_stock_market_version=FEATURE_VERSION,
            market_regimes_version=REGIME_VERSION,
        )
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
