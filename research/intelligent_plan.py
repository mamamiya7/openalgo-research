"""Pure finite research admission and chronology over native input capabilities."""

from copy import deepcopy
from math import ceil

VERSION = "research-intelligence-plan-v1"


def chronology(portfolio, sessions, interval, snapshot=None):
    """No outcomes: preserve longest horizon and require four complete cohorts."""
    from collections import Counter

    minute_counts = Counter(stamp[:10] for stamp in (snapshot or {}).get("timeline", []))
    observed_minutes = min(minute_counts.values()) if minute_counts else 1
    # Minute windows need distinct dates too. Intraday deadlines stay inside one
    # session; multi-day holding still needs its declared maximum session limit.
    longest = max(
        row["search"].get("hold_sessions", {}).get("max", row["config"]["hold_sessions"])
        if interval == "D"
        or (
            row["config"].get("hold_minutes") is None
            and row["config"].get("trade_horizon") != "intraday"
        )
        else ceil(
            row["search"].get("hold_minutes", {}).get("max", row["config"].get("hold_minutes") or 1)
            / observed_minutes
        )
        for row in portfolio["strategies"]
    )
    floor = max(15, int(longest) + 2)
    if sessions < 4 * floor:
        return (
            0,
            max(1, sessions * 60 // 100),
            max(2, sessions * 70 // 100),
            max(3, sessions * 80 // 100),
            sessions,
        ), "exploratory_only"
    check1 = max(floor, sessions * 10 // 100)
    check2 = max(floor, sessions * 10 // 100)
    final = max(floor, sessions * 20 // 100)
    search = sessions - check1 - check2 - final
    if search < floor:
        search = check1 = check2 = floor
        final = sessions - 3 * floor
    return (0, search, search + check1, search + check1 + check2, sessions), "chronological"


def build_plan(portfolio, *, interval):
    from research.portfolio import fingerprint
    from research.stock_features import VERSION as FEATURE_VERSION
    from research.stock_features import catalog

    if portfolio["engine"] != "vectorbt" or interval not in ("D", "1m"):
        raise ValueError("Smart research needs VectorBT with daily or minute NSE equity prices")
    trailing = portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v6",
        "automatic-hypotheses-v7",
        "automatic-hypotheses-v8",
        "automatic-hypotheses-v9",
        "automatic-hypotheses-v10",
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
    )
    joint = (
        trailing
        or portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v5"
    )
    relative = (
        joint or portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v4"
    )
    expanded = joint or portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v3",
        "automatic-hypotheses-v4",
    )
    if expanded:
        from research.stock_indicators import catalog
    if relative:
        from research.stock_relative_strength import catalog
    hypotheses = catalog()
    methods = [
        {
            "id": "exit_management",
            "status": "admitted",
            "reason": "Native targets, stops, active holding deadlines and enabled trailing distance.",
        },
        {
            "id": "stock_trend",
            "status": "admitted",
            "reason": "Completed NSE daily closes; finite seeded EMA10/20 with continuous warmup.",
        },
        {
            "id": "stock_momentum",
            "status": "admitted",
            "reason": "Completed NSE daily closes; 5/20-session change and 0/2% thresholds.",
        },
        {
            "id": "liquidity",
            "status": "unsupported",
            "reason": "The frozen native auxiliary series does not record observed volume.",
        },
        {
            "id": "original_scanner",
            "status": "unsupported",
            "reason": "A positive-only CSV does not contain original scanner rules and non-triggering universe.",
        },
        {
            "id": "other_indicators",
            "status": "deferred",
            "reason": "Need causal numerical and execution reference tests before admission.",
        },
        {
            "id": "portfolio_risk",
            "status": "fixed",
            "reason": "Retain capital, sizing, allocations and exposure for a fair original comparison.",
        },
        {
            "id": "market_conditions",
            "status": "deferred",
            "reason": "Use the existing dedicated market-condition recipe; stock joint search does not silently claim it.",
        },
    ]
    if expanded:
        methods = [row for row in methods if row["id"] != "other_indicators"]
        methods += [
            {"id": name, "status": "admitted", "reason": reason}
            for name, reason in [
                ("stock_rsi", "Pinned OpenAlgo RSI on 100 prior completed daily closes."),
                (
                    "bollinger",
                    "Pinned OpenAlgo20-session population-deviation bands; breakout and pullback separate.",
                ),
                (
                    "atr",
                    "Pinned OpenAlgo Wilder ATR14 divided by last close; prior observed OHLC only.",
                ),
                (
                    "filter_stacks",
                    "Two registered two-condition AND pairs; unknown inputs exclude entry.",
                ),
            ]
        ]
        methods.append(
            {
                "id": "other_indicators",
                "status": "deferred",
                "reason": "Relative strength, volume and arbitrary combinations need separate data and reference qualification.",
            }
        )
    payload = {
        "version": VERSION,
        "engine": "vectorbt",
        "feature_version": FEATURE_VERSION,
        "interval": interval,
        "hypotheses": deepcopy(hypotheses),
        "methods": methods,
        "data": {
            "provider": "OpenAlgo Historify/history",
            "feature_interval": "D",
            "warmup_sessions": 100,
            "max_symbols": 200,
            "max_feature_bars": 200000,
        },
        "budget": {
            "proposals": 60,
            "screening_proposals": 30,
            "refinement_proposals": 30,
            "max_simulations": 180,
            "max_finalists": 3,
            "max_neighbors": 4,
        },
        "objective": "Balanced net return minus drawdown; risk assumptions fixed",
        "strategy_subject": "Supplied scanner opportunities; additional conditions and exits",
        "chronology": "Recorded sessions and maximum hold determine feasible four-window checks; otherwise explicit exploratory-only research",
        "stopping": "Finite work budget; never extend until profitable",
    }
    if expanded:
        from research.stock_indicators import FEATURE_VERSION, recipe

        payload["feature_version"] = FEATURE_VERSION
        payload["indicator_recipe"] = recipe()
        payload["budget"].update(
            proposals=84, screening_proposals=56, refinement_proposals=28, max_simulations=220
        )
    if relative:
        from research.stock_relative_strength import FEATURE_VERSION, recipe

        payload["feature_version"] = FEATURE_VERSION
        payload["indicator_recipe"] = recipe()
        payload["data"]["benchmark"] = {"symbol": "NIFTY", "exchange": "NSE_INDEX", "interval": "D"}
        payload["data"]["benchmark_warmup_sessions"] = 100
        payload["budget"].update(
            proposals=96, screening_proposals=64, refinement_proposals=32, max_simulations=240
        )
        payload["methods"].append(
            {
                "id": "relative_strength",
                "status": "admitted",
                "reason": "Aligned stock/Nifty price-return ratios over5/20/60 prior sessions, with no missing or future closes.",
            }
        )
        for method in payload["methods"]:
            if method["id"] == "other_indicators":
                method["reason"] = (
                    "Volume and arbitrary combinations still need separate data and reference qualification."
                )
    if joint:
        from research.joint_stock_market import FEATURE_VERSION, catalog, recipe

        payload["feature_version"] = FEATURE_VERSION
        payload["indicator_recipe"] = recipe()
        payload["hypotheses"] = catalog()
        payload["data"]["benchmark_warmup_sessions"] = 160
        payload["data"]["stock_warmup_sessions"] = 100
        payload["budget"].update(
            proposals=128, screening_proposals=96, refinement_proposals=32, max_simulations=320
        )
        for method in payload["methods"]:
            if method["id"] == "market_conditions":
                method.update(
                    status="admitted",
                    reason="One causal Nifty trend or volatility gate AND one registered stock hypothesis, on separate recorded history roles.",
                )
    if trailing:
        from research.automatic_trailing import recipe as trailing_recipe

        payload["trailing_research"] = trailing_recipe(portfolio["strategies"])
        payload["methods"].append(
            {
                "id": "trailing_activation",
                "status": "admitted",
                "reason": "One disabled state or a positive trailing distance per strategy; compare on the same native account with unchanged risk and costs.",
            }
        )
        for method in payload["methods"]:
            if method["id"] == "exit_management":
                method["reason"] = (
                    "Native targets, stops, active holding deadlines and conditional trailing protection."
                )
    if portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v7",
        "automatic-hypotheses-v8",
        "automatic-hypotheses-v9",
        "automatic-hypotheses-v10",
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
    ):
        payload["methods"].append(
            {
                "id": "native_timing",
                "status": "conditional" if interval == "1m" else "not_applicable",
                "reason": "Bounded recorded native entry/exit clocks only when minute inputs require them; daily inputs retain original execution and daily candles.",
            }
        )
        payload["budget"].update(timing_probe_limit=24, min_joint_refinement_proposals=8)
    if portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v8",
        "automatic-hypotheses-v9",
        "automatic-hypotheses-v10",
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
    ):
        payload["budget"].update(
            proposals=192,
            proposals_per_fit=48,
            fits=4,
            screening_proposals=128,
            refinement_proposals=64,
            max_simulations=320,
            max_finalists=1,
            max_neighbors=0,
            timing_probe_limit=32,
            timing_probe_limit_per_fit=8,
            min_joint_refinement_proposals=32,
            min_joint_refinement_proposals_per_fit=8,
        )
        payload["chronology"] = (
            "Three expanding-prefix fits checked only on their next windows, then a final development fit and reserved final check; fresh capital per account."
        )
        payload["methods"].append(
            {
                "id": "walk_forward",
                "status": "admitted",
                "reason": "Independent seeded Optuna fits use only each earlier prefix; later scores never update its sampler. Requires sufficient recorded dates and holding support.",
            }
        )
    if portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v9",
        "automatic-hypotheses-v10",
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
    ):
        from research.expanded_stock_market import FEATURE_VERSION, catalog, recipe

        payload["feature_version"] = FEATURE_VERSION
        payload["indicator_recipe"] = recipe()
        payload["hypotheses"] = catalog()
        payload["budget"].update(
            screening_proposals=144,
            refinement_proposals=48,
            momentum_probe_limit=16,
            momentum_probe_limit_per_fit=4,
            timing_probe_limit=16,
            timing_probe_limit_per_fit=4,
        )
        for method in payload["methods"]:
            if method["id"] == "stock_momentum":
                method["reason"] = (
                    "Completed NSE daily closes; 5/10/20/60-session change and 0/2% thresholds. Every new10/60-session condition receives a fixed original-clock probe per fit."
                )
    if portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v10",
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
    ):
        payload["budget"].update(
            proposals=256,
            proposals_per_fit=64,
            max_simulations=384,
            screening_proposals=192,
            refinement_proposals=64,
            execution_probe_limit=16,
            execution_probe_limit_per_fit=4,
            portfolio_probe_limit=16,
            portfolio_probe_limit_per_fit=4,
            min_joint_refinement_proposals=64,
            min_joint_refinement_proposals_per_fit=16,
            max_sizing_control_evaluations=4,
        )
        for method in payload["methods"]:
            if method["id"] == "portfolio_risk":
                method.update(
                    status="admitted",
                    reason="Original or reduced allocation and order ceilings; allocation or stop-risk sizing targeting half the fixed trade-risk ceiling under all user hard limits. Each fit retains a matched original-sizing development control.",
                )
        payload["methods"].append(
            {
                "id": "atr_exits",
                "status": "admitted",
                "reason": "Four registered ATR14 stop/target pairs use prior observed daily OHLC at actual native entries; missing ATR is excluded across all candidates and baseline.",
            }
        )
        payload["objective"] = (
            "Balanced net return minus drawdown under frozen hard risk limits; never increase declared allocation or order ceilings"
        )
    if portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
    ):
        payload["budget"].update(
            proposals=272,
            proposals_per_fit=68,
            screening_proposals=200,
            refinement_proposals=72,
            entry_priority_probe_limit=8,
            entry_priority_probe_limit_per_fit=2,
            min_joint_refinement_proposals=72,
            min_joint_refinement_proposals_per_fit=18,
        )
        payload["methods"].append(
            {
                "id": "entry_priority",
                "status": "admitted",
                "reason": "One frozen account-wide ordering: source, reversed source, or lower modeled stop-loss fraction of entry cash cost first. Two explicit priority probes per fit; no future prices or per-strategy priority advantage.",
            }
        )
    if portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v13":
        capacity = portfolio["strategies"][0]["execution_policy"]["capacity_mode"] != "disabled"
        correlation = (
            portfolio["strategies"][0]["execution_policy"]["correlation_mode"] != "disabled"
        )
        for method in payload["methods"]:
            if method["id"] == "liquidity":
                method.update(
                    status="conditional" if capacity else "not_applicable",
                    reason="Fixed participation limit uses 20 prior completed broker-volume sessions under a reviewed historical-coordinate model; unavailable provenance cannot pass. This does not prove executable liquidity.",
                )
        payload["methods"].append(
            {
                "id": "correlated_exposure",
                "status": "conditional" if correlation else "not_applicable",
                "reason": "Fixed entry cap counts held symbols with positive Pearson correlation of at least 0.8 over 60 aligned prior daily returns; unknown held pairs skip entry.",
            }
        )
        payload["data"].update(
            volume_prior_sessions=20 if capacity else 0,
            correlation_prior_closes=61 if correlation else 0,
        )
    return {**payload, "id": fingerprint(payload)}
