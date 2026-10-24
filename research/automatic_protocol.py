"""Frozen, bounded execution-management research over native price evidence.

This module plans and slices evidence. It does not download, simulate, propose
parameters, or retain resources. Optuna and the selected native engine do that.
"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

VERSION = "automatic-trade-management-v1"
CONDITION_VERSION = "automatic-entry-conditions-v1"
STABILITY_VERSION = "automatic-trade-management-v2"
HYPOTHESIS_VERSION = "automatic-hypotheses-v2"
EXPANDED_VERSION = "automatic-hypotheses-v3"
BENCHMARK_VERSION = "automatic-hypotheses-v4"
JOINT_VERSION = "automatic-hypotheses-v5"
TRAILING_VERSION = "automatic-hypotheses-v6"
TIMING_VERSION = "automatic-hypotheses-v7"
WALK_FORWARD_VERSION = "automatic-hypotheses-v8"
METHOD_VERSION = "automatic-hypotheses-v9"
EXECUTION_VERSION = "automatic-hypotheses-v10"
PRIORITY_VERSION = "automatic-hypotheses-v11"
TURNOVER_VERSION = "automatic-hypotheses-v12"
CAPACITY_VERSION = "automatic-hypotheses-v13"
SENSITIVITY_VERSION = "automatic-hypotheses-v14"
SEED_VERSION = "automatic-hypotheses-v15"
ALLOCATION_VERSION = "automatic-hypotheses-v16"
WALK_FORWARD_VERSIONS = (
    WALK_FORWARD_VERSION,
    METHOD_VERSION,
    EXECUTION_VERSION,
    PRIORITY_VERSION,
    TURNOVER_VERSION,
    CAPACITY_VERSION,
    SENSITIVITY_VERSION,
    SEED_VERSION,
    ALLOCATION_VERSION,
)
HYPOTHESIS_VERSIONS = (
    HYPOTHESIS_VERSION,
    EXPANDED_VERSION,
    BENCHMARK_VERSION,
    JOINT_VERSION,
    TRAILING_VERSION,
    TIMING_VERSION,
    WALK_FORWARD_VERSION,
    METHOD_VERSION,
    EXECUTION_VERSION,
    PRIORITY_VERSION,
    TURNOVER_VERSION,
    CAPACITY_VERSION,
    SENSITIVITY_VERSION,
    SEED_VERSION,
    ALLOCATION_VERSION,
)
PERIODS = ("search", "check1", "check2", "final")
MIN_SESSIONS = 100


def check_signal_span(portfolio, receipt, *, interval):
    """Reject provably impossible v2 histories before any price acquisition.

    Calendar days are only an upper bound on sessions, including special sessions.
    This never invents an exchange calendar or certifies sufficient coverage.
    """
    version = portfolio.get("automatic_research", {}).get("version")
    from research.available_history_protocol import enabled

    if version not in (STABILITY_VERSION, *HYPOTHESIS_VERSIONS):
        return
    span = (
        date.fromisoformat(receipt["date_to"]) - date.fromisoformat(receipt["date_from"])
    ).days + 1
    if enabled(portfolio):
        # Available-history research makes no claim that separate test windows
        # exist. Keep source-date admission; the compiler checks its recorded
        # calendar and unchanged holding deadlines after price validation.
        if span < 1:
            raise ValueError("Signal history dates must be ordered")
        return
    minimum = (
        150
        if version
        in (
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        else 20
        if version in HYPOTHESIS_VERSIONS
        else MIN_SESSIONS
    )
    if span < minimum:
        raise ValueError(
            f"Automatic research needs {minimum} trading sessions; these signals span only "
            f"{span} calendar days. Use a longer signal history."
        )
    if interval == "D" and version == STABILITY_VERSION:
        longest = max(
            row["search"].get("hold_sessions", {}).get("max", row["config"]["hold_sessions"])
            for row in portfolio["strategies"]
        )
        upper_check = (span + 9) // 10
        if upper_check <= longest + 1:
            raise ValueError(
                f"This signal history is too short for the allowed {longest:g}-session hold "
                "and separate later checks. Shorten the holding period or use longer signal history."
            )


def normalize_auto(raw, strategies=None, engine="vectorbt"):
    """Accept only the named recipe; budgets and evidence gates are versioned."""
    if (
        not isinstance(raw, dict)
        or "version" not in raw
        or set(raw)
        - {"version", "validation_mode", "history_policy", "design_policy", "decision_policy"}
        or raw["version"]
        not in (VERSION, CONDITION_VERSION, STABILITY_VERSION, *HYPOTHESIS_VERSIONS)
    ):
        raise ValueError("Choose the supported automatic research recipe")
    if engine not in ("vectorbt", "nautilus"):
        raise ValueError("Automatic research needs a supported portfolio engine")
    if raw["version"] in (CONDITION_VERSION, *HYPOTHESIS_VERSIONS) and engine != "vectorbt":
        raise ValueError("Automatic entry-condition research currently needs VectorBT")
    result = {"version": raw["version"]}
    if "history_policy" in raw:
        if raw["version"] != ALLOCATION_VERSION or raw["history_policy"] != "available_history":
            raise ValueError("Choose the current available-history research policy")
        result["history_policy"] = "available_history"
    if "design_policy" in raw:
        if (
            raw["version"] != ALLOCATION_VERSION
            or raw.get("history_policy") != "available_history"
            or raw["design_policy"] != "automatic_design_v1"
        ):
            raise ValueError("Choose the current automatic design policy")
        result["design_policy"] = "automatic_design_v1"
    if "decision_policy" in raw:
        if (
            raw["version"] != ALLOCATION_VERSION
            or raw.get("history_policy") != "available_history"
            or raw.get("design_policy") != "automatic_design_v1"
            or raw["decision_policy"] != "automatic_decision_v1"
        ):
            raise ValueError("Choose the current automatic decision policy with design admission")
        result["decision_policy"] = raw["decision_policy"]
    if "validation_mode" in raw:
        if raw["version"] != ALLOCATION_VERSION or raw["validation_mode"] != "historical_only":
            raise ValueError(
                "Historical optimisation is available only for the current research recipe"
            )
        result["validation_mode"] = "historical_only"
    return result


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
    if result.get("entry_conditions"):
        raise ValueError(
            "Use a fixed backtest or custom search for an already chosen entry condition"
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
        if cfg["trailing_enabled"] and result["automatic_research"]["version"] not in (
            TRAILING_VERSION,
            TIMING_VERSION,
            WALK_FORWARD_VERSION,
            METHOD_VERSION,
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            search["trailing_pct"] = _range(cfg["trailing_pct"], 99)
        row["search"] = search
    result["optimization"] = {
        "sampler": "tpe",
        "trials": 128
        if result["automatic_research"]["version"]
        in (JOINT_VERSION, TRAILING_VERSION, TIMING_VERSION, *WALK_FORWARD_VERSIONS)
        else 96
        if result["automatic_research"]["version"] == BENCHMARK_VERSION
        else 84
        if result["automatic_research"]["version"] == EXPANDED_VERSION
        else 60
        if result["automatic_research"]["version"] == HYPOTHESIS_VERSION
        else 50,
        "objective": "balanced",
        "seed": 0,
    }
    if result["automatic_research"]["version"] in WALK_FORWARD_VERSIONS:
        # Each expanding prefix gets its own seeded fit. No later-window score
        # is told to that fit's sampler. This is a distinct versioned programme.
        result["optimization"]["trials"] = 192
    if result["automatic_research"]["version"] == EXECUTION_VERSION:
        result["optimization"]["trials"] = 256
    if result["automatic_research"]["version"] in (
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        result["optimization"]["trials"] = 272
    if result["automatic_research"]["version"] in (SEED_VERSION, ALLOCATION_VERSION):
        result["optimization"]["trials"] = 408
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
    intelligent = portfolio["automatic_research"]["version"] in HYPOTHESIS_VERSIONS
    from research.available_history_protocol import enabled
    from research.available_history_protocol import plan as available_history_plan

    available_history = enabled(portfolio)
    minimum = 1 if available_history else 20 if intelligent else MIN_SESSIONS
    if sessions != sorted(set(sessions)) or len(sessions) < minimum:
        raise ValueError(
            f"Automatic research needs at least {minimum} trading sessions between the first "
            f"and last signal; this file covers {len(sessions)}. Use a longer signal history."
        )
    count = len(sessions)
    validation_mode = "chronological"
    cuts = (0, count * 60 // 100, count * 70 // 100, count * 80 // 100, count)
    if intelligent and not available_history:
        from research.intelligent_plan import chronology

        cuts, validation_mode = chronology(
            portfolio, count, snapshot["provenance"]["interval"], snapshot
        )
    periods = (
        {}
        if available_history
        else {
            name: {
                "from": sessions[cuts[i]],
                "to": sessions[cuts[i + 1] - 1],
                "sessions": cuts[i + 1] - cuts[i],
            }
            for i, name in enumerate(PERIODS)
        }
    )
    walk_forward = None
    history_availability = None
    if portfolio["automatic_research"]["version"] in WALK_FORWARD_VERSIONS:
        from research.walk_forward_protocol import build

        walk_forward = build(evidence)
        if walk_forward["status"] != "ready" and not available_history:
            raise ValueError(
                "Walk-forward research needs more signal history for separate training, "
                "later checks and final verification: " + walk_forward["reason"]
            )
        periods = deepcopy(walk_forward["periods"])
        validation_mode = "chronological"
        recommendation_reason = None
        if (
            portfolio["automatic_research"]["version"]
            in (
                TURNOVER_VERSION,
                CAPACITY_VERSION,
                SENSITIVITY_VERSION,
                SEED_VERSION,
                ALLOCATION_VERSION,
            )
            and walk_forward["status"] == "ready"
            and (
                periods["search"]["sessions"] < 60
                or sum(fold["test"]["sessions"] for fold in walk_forward["folds"]) < 60
                or any(fold["test"]["sessions"] < 10 for fold in walk_forward["folds"])
            )
        ):
            recommendation_reason = "The supplied history does not contain 60 later-window sessions across three separate checks."
            if not available_history:
                raise ValueError(
                    "Automatic recommendation needs 60 later-window trading sessions across "
                    "three checks. Use a longer signal history, or run a backtest."
                )
        if available_history:
            history_availability, periods = available_history_plan(
                evidence, walk_forward, fallback_reason=recommendation_reason
            )
            validation_mode = history_availability["mode"]
            if validation_mode == "exploratory_only":
                walk_forward = None
    if (
        portfolio["automatic_research"].get("validation_mode") == "historical_only"
        and validation_mode != "exploratory_only"
    ):
        # Keep the same chronological fits, comparison periods and search.
        # Explicit historical research makes no unused-period certification.
        validation_mode = "historical_only"
    if intelligent and validation_mode == "exploratory_only" and history_availability is None:
        periods = {
            name: {"from": sessions[0], "to": sessions[-1], "sessions": count} for name in PERIODS
        }
    requirements = requirement_strategies(portfolio, evidence["strategies"])
    if snapshot["provenance"]["interval"] == "D" and not intelligent:
        longest = max(row["config"]["hold_sessions"] for row in requirements)
        shortest = min(periods[name]["sessions"] for name in ("check1", "check2"))
        if shortest <= longest + 1:
            raise ValueError(
                f"Automatic research checks have {shortest} trading sessions, but the search "
                f"allows a {longest}-session hold plus next-session entry. Use a longer signal "
                "history or a shorter baseline holding period."
            )
    baseline_keys = ("id", "name", "allocation_pct", "config")
    if portfolio["automatic_research"]["version"] in (
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        baseline_keys += ("execution_policy",)
    payload = {
        "version": portfolio["automatic_research"]["version"],
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
            {key: deepcopy(row[key]) for key in baseline_keys} for row in portfolio["strategies"]
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
    if payload["version"] == CONDITION_VERSION:
        from research.automatic_conditions import catalog

        payload["scope"] = (
            "bounded market entry conditions and trade management of supplied signals"
        )
        payload["condition_catalog"] = catalog(evidence["strategies"])
        payload["market_evidence_id"] = (
            evidence.get("entry_condition_market", {}).get("evidence", {}).get("id")
        )
        payload["budget"] = {"proposals": 50, "max_simulations": 100, "max_exit_candidates": 4}
        payload["policies"]["unsupported"] = (
            "no arbitrary indicators, original-scanner reconstruction or live execution"
        )
    if payload["version"] == STABILITY_VERSION:
        payload["budget"] = {"proposals": 50, "additional_simulations": 68, "max_simulations": 118}
        payload["stability"] = {
            "version": "adjacent-grid-v1",
            "max_neighbors": 8,
            "max_finalists": 3,
            "periods": ["check1", "check2"],
            "min_pass_fraction": 0.75,
            "policy": "one active axis of one strategy, one declared step, within frozen ranges",
            "sampling": "balanced strategy, axis and direction counts; bounded deterministic subset",
            "acceptance": "at least one neighbor; 75% pass ordinary support gates in both development windows",
        }
    if intelligent:
        from research.intelligent_plan import build_plan
        from research.stock_features import catalog, validate_bundle

        relative = portfolio["automatic_research"]["version"] == BENCHMARK_VERSION
        expanded = portfolio["automatic_research"]["version"] in (
            EXPANDED_VERSION,
            BENCHMARK_VERSION,
        )
        if expanded:
            from research.stock_indicators import catalog

        if relative:
            from research.stock_relative_strength import catalog, validate_bundle
        joint = portfolio["automatic_research"]["version"] in (
            JOINT_VERSION,
            TRAILING_VERSION,
            TIMING_VERSION,
            WALK_FORWARD_VERSION,
            METHOD_VERSION,
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        if joint:
            from research.joint_stock_market import catalog
            from research.stock_market_context import validate_bundle
        if portfolio["automatic_research"]["version"] in (
            METHOD_VERSION,
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            from research.expanded_stock_market import catalog
        stock = (
            evidence.get("automatic_stock_market_context")
            if joint
            else evidence.get("automatic_stock_features")
        )
        if stock is not None:
            validate_bundle(stock)
        payload["scope"] = (
            "joint stock trend, momentum and exit research on supplied scanner opportunities"
        )
        if expanded:
            payload["scope"] = (
                "joint stock trend, momentum, RSI, Bollinger, ATR and bounded conjunctions with exit research"
            )
        payload["validation_mode"] = validation_mode
        payload["hypothesis_catalog"] = catalog()
        payload["stock_evidence_id"] = stock["id"] if stock else None
        payload["research_plan"] = build_plan(
            portfolio, interval=snapshot["provenance"]["interval"]
        )
        payload["budget"] = {
            "proposals": 96 if relative else 84 if expanded else 60,
            "max_simulations": 240 if relative else 220 if expanded else 180,
            "max_finalists": 3,
            "max_neighbors": 4,
        }
        payload["policies"]["unsupported"] = (
            "original-scanner reconstruction, other indicators, live execution or statistical confidence claims"
        )
        if expanded:
            from research.stock_indicators import recipe as indicator_recipe

            payload["indicator_recipe"] = indicator_recipe()
            payload["policies"]["unsupported"] = (
                "original-scanner reconstruction, benchmark/regime stacks, volume, unregistered indicators, live execution or statistical confidence claims"
            )
        if relative:
            from research.stock_relative_strength import recipe as relative_recipe

            payload["indicator_recipe"] = relative_recipe()
            payload["scope"] = (
                "joint stock indicators, relative strength against Nifty 50 and exit research"
            )
            payload["policies"]["unsupported"] = (
                "original-scanner reconstruction, market-regime stacks, volume, unregistered indicators, live execution or statistical confidence claims"
            )
        if joint:
            from research.joint_stock_market import recipe as joint_recipe

            if payload["version"] in (
                METHOD_VERSION,
                EXECUTION_VERSION,
                PRIORITY_VERSION,
                TURNOVER_VERSION,
                CAPACITY_VERSION,
                SENSITIVITY_VERSION,
                SEED_VERSION,
                ALLOCATION_VERSION,
            ):
                from research.expanded_stock_market import recipe as joint_recipe

            payload["scope"] = (
                "joint stock indicators, relative strength, causal Nifty trend or volatility and exit research"
            )
            payload["indicator_recipe"] = joint_recipe()
            payload["budget"].update(proposals=128, max_simulations=320)
            payload["policies"]["unsupported"] = (
                "original-scanner reconstruction, arbitrary Boolean stacks, volume, unregistered indicators, live execution or statistical confidence claims"
            )
        if payload["version"] in (TRAILING_VERSION, TIMING_VERSION, *WALK_FORWARD_VERSIONS):
            from research.automatic_trailing import descriptor

            payload["trailing_research"] = descriptor(evidence["strategies"])
            payload["policies"]["trailing"] = (
                "off is one canonical candidate; positive distances enable trailing; "
                "unchanged original settings remain a separate baseline"
            )
        if payload["version"] in (TIMING_VERSION, *WALK_FORWARD_VERSIONS):
            from research.automatic_timing import descriptor as timing_descriptor

            payload["timing_research"] = timing_descriptor(evidence)
            payload["policies"]["coverage"] = payload["timing_research"]["policy"]
        if walk_forward is not None:
            payload["walk_forward"] = walk_forward
            payload["budget"].update(
                proposals=192,
                proposals_per_fit=48,
                fits=4,
                max_simulations=320,
                max_final_verification_evaluations=2,
                max_finalists=1,
                max_neighbors=0,
            )
            payload["selection"]["max_finalists"] = 1
            payload["policies"].update(
                selection="Refit only on each earlier expanding prefix; later scores never feed its sampler. All three next-window and cost checks precede the final development refit and frozen choice.",
                cost_stress="Each next-window account at max(2 * cost_bps, cost_bps + 5); slippage unchanged",
            )
        if payload["version"] in (
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            from research.execution_search import descriptor as execution_descriptor

            payload["execution_research"] = execution_descriptor(evidence)
            payload["execution_features_id"] = evidence.get("execution_features", {}).get("id")
            payload["budget"].update(
                proposals=256,
                proposals_per_fit=64,
                max_simulations=384,
                max_sizing_control_evaluations=4,
            )
            payload["scope"] = (
                "Bounded native stock, market, timing, ATR exits and risk-constrained sizing research on supplied scanner opportunities"
            )
            payload["policies"]["risk"] = payload["execution_research"]["ceilings"]
            payload["policies"]["sizing_control"] = payload["execution_research"]["sizing_control"]
            if payload["version"] in (
                PRIORITY_VERSION,
                TURNOVER_VERSION,
                CAPACITY_VERSION,
                SENSITIVITY_VERSION,
                SEED_VERSION,
                ALLOCATION_VERSION,
            ):
                payload["budget"].update(proposals=272, proposals_per_fit=68)
                payload["policies"]["entry_priority"] = payload["execution_research"][
                    "entry_priority_policy"
                ]
        if payload["version"] in (
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            from research.recommendation_plan import policy as recommendation_policy

            payload["recommendation_checks"] = recommendation_policy()
            payload["budget"].update(max_neighbors=4, max_recommendation_accounts=6)
            payload["policies"]["turnover"] = payload["execution_research"]["turnover_policy"]
        if payload["version"] in (
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            payload["portfolio_execution_features_id"] = evidence.get(
                "portfolio_execution_features", {}
            ).get("id")
            payload["policies"]["portfolio_constraints"] = {
                "volume": "Fixed prior20 broker-reported daily-share participation MODEL; no entry-day volume, actual filled buys share one symbol/session budget; corporate-action and executable-liquidity parity are not established",
                "correlation": "Fixed positive Pearson>=0.8 on exactly60 prior completed aligned daily returns; new-entry clipping only, no negative-correlation credit or held-position rebalancing",
                "reference": "Original trade settings under the same newly declared portfolio constraints and common data mask; older v12 accounts are separate evidence",
            }
        if payload["version"] in (SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            from research.recommendation_sensitivity import build_plan

            sensitivity = build_plan(evidence, periods["search"])
            payload["recommendation_sensitivity"] = sensitivity
            payload["budget"]["max_sensitivity_accounts"] = sensitivity["maximum_accounts"]
            for scenario in sensitivity["scenarios"]:
                if scenario["status"] == "available":
                    payload["periods"][scenario["name"]] = {
                        key: scenario[key] for key in ("from", "to", "sessions")
                    }
            payload["policies"]["sensitivity"] = sensitivity["policy"]
        if payload["version"] in (SEED_VERSION, ALLOCATION_VERSION):
            from research.automatic_timing import axes as timing_axes
            from research.automatic_trailing import axes as trailing_axes
            from research.automatic_walk_forward import _screening_program
            from research.connectors.optuna_portfolio import _prepare
            from research.execution_search import axes as execution_axes
            from research.recommendation_seeds import policy as seed_policy

            _, numeric_axes = _prepare(evidence["strategies"])
            axes = {
                **numeric_axes,
                **trailing_axes(payload["trailing_research"]),
                **timing_axes(payload["timing_research"]),
                **execution_axes(payload["execution_research"]),
            }
            screening_count = len(
                _screening_program(
                    payload["version"],
                    payload["hypothesis_catalog"],
                    axes,
                    payload["timing_research"],
                    payload["execution_research"],
                )
            )
            payload["search_seeds"] = seed_policy(screening_proposals=screening_count)
            payload["budget"].update(
                proposals=408,
                max_simulations=512,
                fits=6,
                max_seed_proposals=136,
                max_seed_accounts=136,
            )
            payload["policies"]["search_seeds"] = payload["search_seeds"]["policy"]
        if payload["version"] == ALLOCATION_VERSION:
            payload["policies"]["allocation"] = payload["execution_research"]["allocation_policy"]
            payload["policies"]["portfolio_constraints"]["allocation"] = payload[
                "execution_research"
            ]["allocation_policy"]
            payload["policies"]["portfolio_constraints"]["reference"] = (
                "Original allocation under the same capital, fixed hard risk limits, costs, capacity/correlation rules and common source data; new entry-only allocation policy is a separate v16 numerical contract"
            )
        if validation_mode == "exploratory_only":
            payload["policies"]["final"] = (
                "No untouched later period; exploratory ranking only, never a validated recommendation"
            )
    if history_availability is not None:
        payload["history_availability"] = history_availability
        if validation_mode == "exploratory_only":
            payload["selection"]["max_finalists"] = 1
            payload["budget"].update(
                max_finalists=1, max_neighbors=0, max_final_verification_evaluations=0
            )
            payload["policies"].update(
                selection="Freeze the full-history primary search choice; expanding prefixes and seed repeats are historical diagnostics, not independent validation.",
                final="No separate final period is available. Historical research only; no independent validation or validated recommendation is claimed.",
            )
            for method in payload.get("research_plan", {}).get("methods", []):
                if method.get("id") == "walk_forward":
                    method.update(status="unavailable", reason=history_availability["reason"])
            plan = payload.get("research_plan")
            if plan:
                plan["id"] = fingerprint({key: value for key, value in plan.items() if key != "id"})
    if validation_mode == "historical_only":
        payload["policies"]["final"] = (
            "Historical later-period comparison only; dates may have been used before. "
            "No independent final test or validated recommendation is claimed. "
            "Later-period results never rerank the development selection."
        )
    from research.automatic_design import descriptor
    from research.automatic_design import enabled as design_enabled

    if design_enabled(portfolio):
        payload["automatic_design"] = descriptor()
        from research.capital_planning import descriptor as capital_descriptor

        payload["capital_planning"] = capital_descriptor()
        payload["budget"]["max_capital_planning_accounts"] = (
            4 if validation_mode == "exploratory_only" else 3
        )
        payload["policies"]["selection"] += (
            " Automatic design admission additionally requires adequate trade/date support, "
            "participation across the predeclared complete-entry calendar and bounded "
            "symbol-profit concentration. Failure retains the unchanged baseline."
        )
    from research.automatic_decision import descriptor as decision_descriptor
    from research.automatic_decision import enabled as decision_enabled

    if decision_enabled(portfolio):
        from research.capital_planning import DECISION_VERSION

        payload["automatic_decision"] = decision_descriptor()
        payload["capital_planning"] = capital_descriptor(DECISION_VERSION)
        payload["budget"]["max_decision_accounts"] = (
            decision_descriptor()["max_additional_accounts"]
            if validation_mode == "exploratory_only"
            else 0
        )
        payload["policies"]["decision"] = decision_descriptor()["selection"]
        plan = payload.get("research_plan")
        if plan:
            plan["decision"] = deepcopy(payload["automatic_decision"])
            if validation_mode == "exploratory_only":
                plan["chronology"] = (
                    "Expanding historical prefixes and same-history seed repeats; no separate later-period validation"
                )
                for method in plan.get("methods", []):
                    if method.get("id") == "symbol_date_sensitivity":
                        method.update(
                            status="unavailable",
                            reason="This available-history programme records fixed-choice prefixes and stock-profit concentration; it does not run separate symbol-group or chronological-half accounts",
                        )
                    if method.get("id") in (
                        "recommendation_evidence",
                        "recommendation_sensitivity",
                        "search_seed_stability",
                    ):
                        method.update(
                            status="diagnostic",
                            reason="Same-history diagnostics cannot establish independent validation",
                        )
            plan["id"] = fingerprint({key: value for key, value in plan.items() if key != "id"})
        from research.automatic_decision import preflight

        payload["decision_preflight"] = preflight(evidence, payload)
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

    recipe = compile_recipe(evidence)
    if name not in recipe["periods"]:
        raise ValueError("Choose a recorded automatic research period")
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
    timing_union = (
        recipe["version"] in (TIMING_VERSION, *WALK_FORWARD_VERSIONS)
        and evidence.get("timing_cohort") is not None
    )
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
    if timing_union:
        from research.automatic_timing import period_strategies

        strategies, purged = period_strategies(evidence, first, last, name)
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
        prepared = selected if timing_union else prepare(selected)
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
