"""Read-only automatic research planning over the native portfolio contract.

This is a preflight explanation, not a new executor or saved recipe. Existing
normalization owns every executable range. No prices, scores, files, runtimes,
broker clients, or account resources are read while constructing a plan.
"""

from copy import deepcopy
from decimal import Decimal
from math import prod

from research.automatic_protocol import CONDITION_VERSION, MIN_SESSIONS, STABILITY_VERSION, VERSION
from research.portfolio import fingerprint, normalize
from research.regimes import recipe as regime_recipe

CATALOG_VERSION = "automatic-search-plan-v1"
NEXT_RECIPE_VERSION = "automatic-hypotheses-v6"

_PARAMETERS = {
    "target_pct": ("Profit target", "%"),
    "stop_pct": ("Stop loss", "%"),
    "hold_sessions": ("Maximum holding sessions", "sessions"),
    "hold_minutes": ("Maximum holding minutes", "minutes"),
    "trailing_pct": ("Trailing stop distance", "%"),
    "trailing_enabled": ("Trailing stop enabled", "boolean"),
    "initial_capital": ("Starting capital", "INR"),
    "order_size_pct": ("Order size within strategy allocation", "%"),
    "cost_bps": ("Transaction costs", "bps"),
    "slippage_bps": ("Slippage", "bps"),
    "modes": ("Signal admission", "policy"),
    "entry_priority": ("Entry priority", "policy"),
    "priority_seed": ("Entry-priority seed", "seed"),
    "max_exposure_pct": ("Maximum exposure", "%"),
    "exposure_fill_mode": ("Cash fill policy", "policy"),
    "trade_horizon": ("Trade horizon", "policy"),
    "entry_time": ("Entry clock", "exchange time"),
    "exit_time": ("Exit clock", "exchange time"),
}


def _domain(axis, *, integer):
    """Describe existing decimal steps without floating-point endpoint drift."""
    low, high, step = (Decimal(str(axis[key])) for key in ("min", "max", "step"))
    count = int((high - low) / step) + 1
    if not 1 <= count <= 7 or low + (count - 1) * step != high:
        raise ValueError("The automatic preset must expose at most seven exact axis values")
    cast = int if integer else float
    return {
        **deepcopy(axis),
        "values": [cast(low + i * step) for i in range(count)],
        "count": count,
    }


def _parameters(strategy, engine):
    cfg, search = strategy["config"], strategy["search"]
    parameters = []
    for key, (label, unit) in _PARAMETERS.items():
        if key not in cfg:
            continue
        status, reason = "fixed", "Retain the original execution assumptions."
        item = {"id": key, "label": label, "unit": unit, "current": deepcopy(cfg[key])}
        if key in search:
            status, reason = "search", "Bounded values around the original setting."
            item["domain"] = _domain(search[key], integer=key.startswith("hold_"))
        elif key == "hold_sessions" and (
            cfg.get("hold_minutes") is not None or cfg.get("trade_horizon") == "intraday"
        ):
            status, reason = "unavailable", "The active minute or intraday deadline takes priority."
        elif key == "hold_minutes" and cfg[key] is None:
            status, reason = "unavailable", "No minute holding limit is configured."
        elif key in ("trailing_pct", "trailing_enabled") and engine == "nautilus":
            status, reason = (
                "unavailable",
                "The Nautilus OHLC adapter does not support trailing stops.",
            )
        elif key == "trailing_pct" and not cfg["trailing_enabled"]:
            status, reason = "unavailable", "Trailing stops are disabled in the original setup."
        elif key == "trailing_enabled":
            reason = (
                "This recipe changes the distance only when trailing stops are already enabled."
            )
        elif key in ("initial_capital", "order_size_pct", "max_exposure_pct"):
            reason = "Keep account risk comparable to the unchanged strategy."
        elif key in ("cost_bps", "slippage_bps"):
            reason = "Execution assumptions are not tuned to improve the result."
        elif key in ("entry_time", "exit_time", "trade_horizon"):
            reason = "Keep the original timing and required candle interval."
        parameters.append({**item, "status": status, "reason": reason})
    parameters.append(
        {
            "id": "allocation_pct",
            "label": "Strategy allocation",
            "unit": "%",
            "current": strategy["allocation_pct"],
            "status": "fixed",
            "reason": "Keep allocation and shared-account cash constraints unchanged.",
        }
    )
    return parameters


def _family(identifier, label, status, reason, requirements=(), blockers=()):
    return {
        "id": identifier,
        "label": label,
        "status": status,
        "reason": reason,
        "requirements": list(requirements),
        "blockers": list(blockers),
    }


def _decisions(portfolio, interval, strategies, *, proposals, max_simulations):
    """Explain automatic choices without claiming a completed admission check.

    This is new preflight presentation evidence only. It never changes a saved
    execution recipe, the request's version, or any numerical search domain.
    """
    intelligent = portfolio["automatic_research"]["version"].startswith("automatic-hypotheses-")
    return {
        "version": "automatic-decision-summary-v1",
        "automatic": [
            {
                "id": "interval",
                "label": "Price interval",
                "decision": "Daily candles" if interval == "D" else "One-minute candles",
            },
            {
                "id": "history",
                "label": "Required history",
                "decision": "Reuse Historify prices; fetch only missing execution and admitted feature windows through the connected broker.",
            },
            {
                "id": "search",
                "label": "Search budget",
                "decision": f"Up to {proposals} proposals and {max_simulations} calculations, including comparisons and checks.",
            },
            {
                "id": "ranges",
                "label": "Trading settings",
                "decision": "Bounded target, stop and active holding ranges; enabled trailing distance. Original settings remain a separate reference.",
            },
            {
                "id": "checks",
                "label": "Validation",
                "decision": "Use recorded sessions and holding limits to test later periods when feasible; insufficient history produces an exploratory result."
                if intelligent
                else "Use this recipe's recorded chronological checks; insufficient history fails admission.",
            },
        ],
        "retained": [
            {
                "id": "risk",
                "label": "Account risk",
                "decision": "Keep capital, allocations, order size and exposure unchanged for a fair comparison.",
            },
            {
                "id": "costs",
                "label": "Execution costs",
                "decision": "Keep declared costs and slippage; challenge eligible settings with higher costs.",
            },
            {
                "id": "timing",
                "label": "Trading horizon and clocks",
                "decision": "Keep the original intraday or multiday horizon and entry/exit clock rules.",
            },
        ],
        "not_searched": [
            {
                "id": "trailing_activation",
                "label": "Turning trailing stops on or off",
                "reason": "Search trailing distance only for strategies where it is already enabled."
                if portfolio["engine"] == "vectorbt"
                else "The Nautilus OHLC adapter does not support trailing stops.",
            },
            {
                "id": "timing_search",
                "label": "Alternative entry and exit clocks",
                "reason": "This recipe retains original execution timing.",
            },
            {
                "id": "portfolio_risk_search",
                "label": "Larger bets or different allocations",
                "reason": "Changing account risk would invalidate comparison with the original setup.",
            },
            {
                "id": "original_scanner",
                "label": "Rebuilding the original scanner",
                "reason": "A signals CSV cannot establish rules or the non-triggering universe.",
            },
            *(
                [
                    {
                        "id": "liquidity",
                        "label": "Volume and liquidity filters",
                        "reason": "The frozen auxiliary feature input does not record qualified observed volume.",
                    }
                ]
                if intelligent
                else []
            ),
        ],
        "prerequisites": [
            {
                "id": identifier,
                "label": label,
                "status": "pending",
                "reason": reason,
            }
            for identifier, label, reason in (
                (
                    "coverage",
                    "Price coverage",
                    "Verify native execution and feature prices before scoring; missing observations never pass a filter.",
                ),
                (
                    "calendar",
                    "Trading sessions",
                    "Verify the recorded exchange calendar and required session hours.",
                ),
                (
                    "runtime",
                    "Calculation runtime",
                    "Check the selected engine and pinned numerical versions before execution.",
                ),
                (
                    "validation",
                    "Evidence for a recommendation",
                    "Only completed checks can support an improvement; retaining original settings is a valid result.",
                ),
            )
        ],
        "scope": {
            "exhaustive": False,
            "recommendation_basis": "Strongest supported scenario among the tested settings; no global optimum or guaranteed profit.",
            "searched_parameters": sum(
                item["status"] == "search" for row in strategies for item in row["parameters"]
            ),
        },
    }


def _families(engine, version):
    conditions = version == CONDITION_VERSION
    stability = version == STABILITY_VERSION
    market_block = (
        "Choose the explicit market-condition recipe."
        if engine == "vectorbt"
        else "Conditional entry execution is currently implemented only for VectorBT."
    )
    families = [
        _family(
            "trade_management",
            "Targets, stops and holding limits",
            "search",
            "Use Optuna TPE on the declared numeric domains, with original settings as a reference.",
            ("Native OHLC coverage for the largest permitted holding window.",),
        ),
        _family(
            "market_conditions",
            "Market trend and volatility",
            "search" if conditions else "unavailable",
            "Screen four fixed presets after the exit search; do not fit regime thresholds."
            if conditions
            else "This execution recipe does not search market conditions.",
            (
                "Separate daily NIFTY/NSE_INDEX evidence and 160 prior exchange sessions.",
                "Completed observations strictly before entry; missing context rejects that entry.",
            ),
            () if conditions else (market_block,),
        ),
        _family(
            "account_risk",
            "Capital, allocation and sizing",
            "fixed",
            "Preserve the original risk envelope; larger bets are not a strategy improvement.",
        ),
        _family(
            "execution_assumptions",
            "Costs, fills and timing",
            "fixed",
            "Keep the original broker-cost assumptions, clock rules and native engine fill policy.",
        ),
        _family(
            "cost_stress",
            "Higher transaction costs",
            "validation",
            "Compare eligible candidates with the unchanged strategy at max(2 × costs, costs + 5 bps).",
            ("Use the second development check; leave slippage unchanged.",),
        ),
        _family(
            "chronological_checks",
            "Later-period checks",
            "validation",
            "Two development checks precede one fixed final-period evaluation.",
            (
                "Recorded sessions, complete holding deadlines and fresh capital in each period.",
                "Preserve saved date-use reservations; a new upload does not make seen dates unseen.",
            ),
        ),
        _family(
            "parameter_stability",
            "Nearby parameter settings",
            "validation" if stability else "unavailable",
            "Check bounded neighboring exit settings in both development windows."
            if stability
            else "This recipe does not test neighboring parameter stability.",
            ("Use declared exit domains and development outcomes only.",),
            () if stability else ("Requires the explicit stability-checked exit recipe.",),
        ),
    ]
    future = (
        (
            "stock_trend",
            "Stock EMA and trend",
            "Stock closes before entry; declared EMA initialization and warmup.",
        ),
        (
            "stock_momentum",
            "Stock momentum",
            "Contiguous stock closes before entry and declared lookback lengths.",
        ),
        (
            "stock_rsi",
            "Stock RSI",
            "Contiguous stock closes; pinned smoothing, zero-denominator and warmup rules.",
        ),
        (
            "relative_strength",
            "Relative strength",
            "Aligned stock and named index returns over the same completed sessions.",
        ),
        (
            "bollinger",
            "Bollinger Bands",
            "Contiguous closes; declared window, deviation estimator and band multiplier.",
        ),
        (
            "atr",
            "ATR and volatility-scaled exits",
            "Contiguous observed OHLC and previous close; declared ATR smoothing and units.",
        ),
        (
            "liquidity",
            "Volume and liquidity",
            "Proven observed volume and its units; missing volume must never become zero.",
        ),
        (
            "filter_stacks",
            "Combinations of entry conditions",
            "Versioned AND/OR semantics, missing-feature policy and bounded complexity.",
        ),
        (
            "trailing_activation",
            "Enable or disable trailing stops",
            "Conditional categorical proposals and engine support for both branches.",
        ),
        (
            "timing_search",
            "Entry clocks and intraday timing",
            "Causal minute observations and actual session hours, including special sessions.",
        ),
        (
            "walk_forward",
            "Walk-forward validation",
            "Sufficient distinct decision dates and untouched windows after horizon purging.",
        ),
        (
            "portfolio_risk_search",
            "Allocation and exposure alternatives",
            "An explicit risk envelope and comparable exposure/concentration constraints.",
        ),
        (
            "benchmark_selection",
            "Benchmark-aware ranking",
            "Named instrument and price/total-return basis fixed before observing scores.",
        ),
    )
    families.extend(
        _family(
            identifier,
            label,
            "planned",
            "Not searched by this execution recipe.",
            (requirement,),
            ("Requires a new versioned hypothesis evaluator and validated data admission.",),
        )
        for identifier, label, requirement in future
    )
    return families


def build_plan(portfolio, *, interval):
    """Explain one automatic request without changing it or preparing prices.

    ``interval`` must come from native resolved-input planning, which can inspect
    CSV timestamps. This function cannot infer those timestamps from settings.
    Search eligibility here means supported by the recipe, not proven data
    coverage or runtime availability. The worker remains the admission authority.
    """
    if interval not in ("D", "1m"):
        raise ValueError("Automatic research needs the resolved daily or minute interval")
    canonical = normalize(portfolio)
    auto = canonical.get("automatic_research")
    if not auto or auto["version"] not in (
        VERSION,
        CONDITION_VERSION,
        STABILITY_VERSION,
        "automatic-hypotheses-v2",
        "automatic-hypotheses-v3",
        "automatic-hypotheses-v4",
        "automatic-hypotheses-v5",
    ):
        raise ValueError("Choose a supported automatic research recipe before planning")
    version, engine = auto["version"], canonical["engine"]
    if interval == "D" and any(
        row["config"].get("trade_horizon") == "intraday"
        or any(
            row["config"].get(key) is not None
            for key in ("hold_minutes", "entry_time", "exit_time")
        )
        for row in canonical["strategies"]
    ):
        raise ValueError("Timed execution requires the resolved one-minute interval")
    strategies = [
        {"id": row["id"], "name": row["name"], "parameters": _parameters(row, engine)}
        for row in canonical["strategies"]
    ]
    axes = [item for row in strategies for item in row["parameters"] if item["status"] == "search"]
    if version in (
        "automatic-hypotheses-v2",
        "automatic-hypotheses-v3",
        "automatic-hypotheses-v4",
        "automatic-hypotheses-v5",
    ):
        from research.intelligent_plan import build_plan as intelligence_plan

        plan = intelligence_plan(canonical, interval=interval)
        payload = {
            "version": "automatic-search-plan-v1",
            "phase": "preflight",
            "execution_recipe_version": version,
            "engine": engine,
            "interval": interval,
            "strategies": strategies,
            "summary": {
                "searched_parameters": len(axes),
                "condition_presets": len(plan["hypotheses"]),
                "max_simulations": plan["budget"]["max_simulations"],
            },
            "search": {
                "proposals": plan["budget"]["proposals"],
                "stages": ["family_screening", "joint_refinement", "feasible_later_checks"],
                "budget": plan["budget"],
            },
            "research_plan": plan,
            "families": plan["methods"],
            "data": plan["data"],
            "fixed_assumptions": "Cash, allocation, sizing, exposure and costs stay fixed.",
            "decisions": _decisions(
                canonical,
                interval,
                strategies,
                proposals=plan["budget"]["proposals"],
                max_simulations=plan["budget"]["max_simulations"],
            ),
        }
        return {**payload, "plan_id": fingerprint(payload)}
    conditions, stability = version == CONDITION_VERSION, version == STABILITY_VERSION
    max_simulations = 100 if conditions else 118 if stability else 70
    search = {
        **deepcopy(canonical["optimization"]),
        "proposals": canonical["optimization"]["trials"],
        "numeric_space_size": str(prod(item["domain"]["count"] for item in axes)),
        "space_size_basis": "Exit-domain combinations only; not a claim of work performed.",
        "baseline": "Evaluate the unchanged settings separately, even when not on a search grid.",
        "exhaustive": False,
        "budget": {
            "max_simulations": max_simulations,
            "scope": "All native simulations, including references, checks and report reconstruction.",
            "deduplicate": "Reuse completed identical settings on identical evidence within this run.",
        },
        "stages": ["unchanged_baseline", "exit_search"],
    }
    if conditions:
        search["budget"].update(max_exit_candidates=4, max_condition_candidates=16)
        search["condition_presets"] = [
            {"label": "No extra condition", "dimension": None, "regime": None},
            {"label": "Rising Nifty trend", "dimension": "trend", "regime": "up"},
            {"label": "Normal Nifty volatility", "dimension": "volatility", "regime": "normal"},
            {"label": "High Nifty volatility", "dimension": "volatility", "regime": "high"},
        ]
        search["stages"].append("fixed_condition_screen")
    search["stages"].extend(["development_checks", "higher_cost_check"])
    if stability:
        search["budget"].update(max_neighbors_per_finalist=8, max_neighbor_simulations=48)
        search["stages"].append("parameter_stability_check")
        search["stability"] = {
            "neighbors": "Adjacent declared grid values; change one axis of one strategy at a time.",
            "selection": "Balance strategy, axis and direction counts deterministically; freeze before scoring.",
            "coverage": "Report total available neighbors and any truncation at eight per finalist.",
            "min_neighbors": 1,
            "required_pass_fraction": 0.75,
            "pass_rule": "Each passing neighbor must meet the same support gates in both development checks.",
            "final_data_used": False,
        }
    search["stages"].extend(["freeze_choice", "final_check"])
    max_hold = max(
        row["search"].get("hold_sessions", {}).get("max", row["config"]["hold_sessions"])
        for row in canonical["strategies"]
    )
    auxiliary = []
    if conditions:
        auxiliary.append(
            {
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "interval": "D",
                "role": "market_conditions",
                "warmup_sessions": 160,
                "feature_recipe": regime_recipe(),
                "availability": "Recorded session close strictly before each decision.",
                "scored": False,
            }
        )
    from research.connectors.vectorbt_portfolio import MAX_MATRIX_CELLS

    limits = {"max_bar_signal_cells": MAX_MATRIX_CELLS, "max_signals": 25000}
    if engine == "nautilus":
        from research.connectors.nautilus_portfolio import (
            MAX_BAR_LOT_CELLS,
            MAX_EVENTS,
            MAX_SIGNAL_LOTS,
        )

        limits = {
            "max_bar_signal_cells": MAX_BAR_LOT_CELLS,
            "max_signals": MAX_SIGNAL_LOTS,
            "max_events": MAX_EVENTS,
            "instrument_metadata": "Supplied historical tick grid; do not guess instrument specifications.",
        }
    payload = {
        "version": CATALOG_VERSION,
        "phase": "preflight",
        "execution_recipe_version": version,
        "request_id": fingerprint(canonical),
        "engine": engine,
        "interval": interval,
        "summary": {
            "searched_parameters": len(axes),
            "condition_presets": 4 if conditions else 0,
            "max_simulations": max_simulations,
        },
        "strategies": strategies,
        "families": _families(engine, version),
        "search": search,
        "data": {
            "coverage_verified": False,
            "source": "Historify first; missing coverage through OpenAlgo history and the connected broker.",
            "execution": {
                "exchange": "NSE",
                "asset_class": "cash_equity",
                "interval": interval,
                "fields": ["open", "high", "low", "close"],
            },
            "planning": "Union required windows at maximum active search horizons before scoring.",
            "auxiliary": auxiliary,
            "missing": "Preserve missing observations and coverage exclusions; never fill from future data.",
            "calendar": "Recorded exchange sessions and actual hours; calendar days are not trading sessions.",
            "engine_limits": limits,
            "pending_checks": [
                "Connected-broker availability and interval support",
                "Recorded session coverage",
                "Runtime versions and engine resource bounds",
                "Frozen native price coverage",
            ],
        },
        "validation": {
            "status": "requires_frozen_evidence",
            "min_sessions": MIN_SESSIONS,
            "period_percentages": {"search": 60, "check1": 10, "check2": 10, "final": 20},
            "min_daily_check_sessions": int(max_hold) + 2 if interval == "D" else None,
            "purge": "Every permitted holding deadline must end within its own period.",
            "capital": "Fresh capital, no carried positions, same fixed risk envelope in each period.",
            "max_finalists": 3,
            "min_closed_trades_per_check": 5,
            "min_distinct_entry_dates_per_check": 5,
            "max_drawdown_pct": 25,
            "positive_net_return_required": True,
            "objective": "net_return_pct - max_drawdown_pct",
            "selection": "Require improvement over unchanged settings in both development checks and higher costs.",
            "final": "Freeze the choice before the final check; final results never select a different candidate.",
            "cost_stress": [
                {
                    "strategy_id": row["id"],
                    "base_bps": row["config"]["cost_bps"],
                    "stressed_bps": max(
                        2 * row["config"]["cost_bps"], row["config"]["cost_bps"] + 5
                    ),
                    "slippage_bps": row["config"]["slippage_bps"],
                }
                for row in canonical["strategies"]
            ],
            "fallback": "Retain the unchanged strategy when improvement is unsupported; incomplete is not a pass.",
        },
        "limitations": [
            "This describes supported tests, not completed work or verified data coverage.",
            "A bounded search does not try every combination or establish a global optimum.",
            "Several stocks on one date are not independent market observations.",
            "More price history cannot recreate scanner signals absent from the supplied CSV.",
            "Minimum sample gates are engineering safeguards, not statistical confidence guarantees.",
        ],
        "next_recipe": {
            "version": NEXT_RECIPE_VERSION,
            "status": "planned",
            "admission": "Declare causal features, conditional hypotheses and a total budget before any score.",
            "search": "Broad bounded hypothesis screening, then refinement of supported parameter neighborhoods.",
            "compatibility": "A new recipe must not reinterpret or extend existing saved v1/v2 runs.",
        },
        "decisions": _decisions(
            canonical,
            interval,
            strategies,
            proposals=search["proposals"],
            max_simulations=max_simulations,
        ),
    }
    if conditions:
        payload["limitations"].append(
            "Exit search precedes fixed-condition screening; this can miss settings that only work with a condition."
        )
    return {**payload, "plan_id": fingerprint(payload)}
