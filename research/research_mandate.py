"""Pure pre-outcome mandate for the existing frozen automatic programme.

This additive receipt names what the versioned compiler actually admitted. It
does not expand a search, infer economic intent, reserve periods, read results or
claim a global optimum. Durable period-use reservations remain service-owned.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy

from research.automatic_protocol import compile_recipe
from research.connectors.optuna_portfolio import OBJECTIVES
from research.data import MAX_BARS
from research.engine import MAX_SIGNALS
from research.evaluation_basis import IDENTITIES, build_evaluation_basis
from research.portfolio import MAX_STRATEGIES
from research.report_contract import fingerprint, settings_identity

VERSION = "research-mandate-v1"
MAX_METADATA_BYTES = 64 * 1024
EXECUTION_MAX_METADATA_BYTES = 128 * 1024


def _bounded(value):
    size = 0
    source = value.get("source") if isinstance(value, dict) else None
    maximum = (
        EXECUTION_MAX_METADATA_BYTES
        if isinstance(source, dict)
        and source.get("recipe_version")
        in ("automatic-hypotheses-v10", "automatic-hypotheses-v11", "automatic-hypotheses-v12")
        else MAX_METADATA_BYTES
    )
    try:
        for chunk in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value):
            size += len(chunk.encode())
            if size > maximum:
                raise ValueError("Research mandate metadata limit reached")
    except (TypeError, OverflowError) as error:
        raise ValueError("Research mandate metadata is not canonical JSON") from error


def _admit_inputs(evidence, inputs_artifact):
    if not isinstance(inputs_artifact, str) or not re.fullmatch(r"[a-f0-9]{64}", inputs_artifact):
        raise ValueError("Research mandate needs the immutable inputs artifact identity")
    if not isinstance(evidence, dict):
        raise ValueError("Research mandate needs frozen automatic evidence")
    snapshot, portfolio = evidence.get("snapshot"), evidence.get("portfolio")
    strategies, signals = evidence.get("strategies"), evidence.get("signals")
    if not isinstance(snapshot, dict) or not isinstance(portfolio, dict):
        raise ValueError("Research mandate needs frozen account and price inputs")
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= MAX_STRATEGIES:
        raise ValueError("Research mandate strategy limit reached")
    if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_SIGNALS:
        raise ValueError("Research mandate signal limit reached")
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Research mandate price provenance is missing")
    interval = provenance.get("interval")
    timeline = snapshot.get("timeline" if interval == "1m" else "sessions")
    if (
        interval not in ("D", "1m")
        or not isinstance(timeline, list)
        or not 1 <= len(timeline) <= MAX_BARS
    ):
        raise ValueError("Research mandate observation limit reached")
    bars = snapshot.get("bars")
    if (
        not isinstance(bars, dict)
        or sum(len(rows) for rows in bars.values() if isinstance(rows, dict)) > MAX_BARS
    ):
        raise ValueError("Research mandate price limit reached")
    if (
        portfolio.get("engine") not in ("vectorbt", "nautilus")
        or snapshot["provenance"].get("exchange") != "NSE"
    ):
        raise ValueError("Research mandate requires the qualified native portfolio profile")


def build(evidence, *, inputs_artifact):
    """Freeze actual assumptions, admitted methods and period roles before scores.

    The existing compiler is authoritative. A retained recipe cannot override
    changed inputs, and no calculated report or trial is part of this identity.
    Full evaluation identities bind recorded inputs, not final-period outcomes.
    """
    _admit_inputs(evidence, inputs_artifact)
    recipe = compile_recipe(evidence)
    if evidence.get("automatic_recipe") is not None and evidence["automatic_recipe"] != recipe:
        raise ValueError("Research mandate frozen recipe conflicts with source inputs")
    # The existing recipe duplicates its registered catalog in research_plan.
    # Bound the compact receipt below rather than reject a qualified v7 recipe
    # merely because that older layout contains duplicate metadata.
    basis = build_evaluation_basis(evidence, period="full")
    portfolio, snapshot = evidence["portfolio"], evidence["snapshot"]
    interval = snapshot["provenance"]["interval"]
    settings = [
        {
            **{key: deepcopy(row[key]) for key in ("id", "name", "allocation_pct", "config")},
            **(
                {"execution_policy": deepcopy(row["execution_policy"])}
                if "execution_policy" in row
                else {}
            ),
        }
        for row in evidence["strategies"]
    ]
    plan = recipe.get("research_plan", {})
    methods = deepcopy(plan.get("methods", []))
    if not methods:
        methods = [{"id": "exit_management", "status": "admitted", "reason": recipe["scope"]}]
        if recipe.get("condition_catalog"):
            methods.append(
                {
                    "id": "market_entry_conditions",
                    "status": "admitted",
                    "reason": "Only the frozen registered condition catalog is searched.",
                }
            )
    if recipe.get("hypothesis_catalog") and not recipe.get("stock_evidence_id"):
        for method in methods:
            if method["status"] == "admitted" and method["id"] not in (
                "exit_management",
                "trailing_activation",
                "native_timing",
            ):
                method.update(
                    recipe_status="admitted",
                    status="unavailable",
                    reason="Required frozen stock/market context has not been retained.",
                )
    validation_mode = recipe.get("validation_mode", "chronological")
    if validation_mode not in ("chronological", "exploratory_only"):
        raise ValueError("Research mandate validation mode is unsupported")
    periods = []
    for name, bounds in recipe["periods"].items():
        if name not in ("search", "check1", "check2", "check3", "fit1", "fit2", "fit3", "final"):
            raise ValueError("Research mandate period role is unsupported")
        role = (
            "development_selection"
            if name in ("search", "fit1", "fit2", "fit3")
            else "reserved_final_verification"
            if name == "final"
            else "development_validation"
        )
        if validation_mode == "exploratory_only":
            role = "exploratory_selection"  # The compiler overlaps these periods.
        periods.append({"name": name, "role": role, **deepcopy(bounds), "untouched_claim": False})
    objective = recipe["search"].get("objective")
    if objective not in OBJECTIVES:
        raise ValueError("Research mandate objective is unsupported")
    symbols = sorted({row["symbol"] for row in evidence["signals"]})
    subject_rows = []
    for row in evidence["strategies"]:
        subject_rows.append(
            {
                "strategy_id": row["id"],
                "source_id": row.get("source_id"),
                "signal_count": len(row["signals"]),
                "signal_identity": fingerprint(
                    [
                        {
                            key: signal[key]
                            for key in ("symbol", "date", "timestamp", "row")
                            if key in signal
                        }
                        for signal in row["signals"]
                    ]
                ),
            }
        )
    mandate = {
        "version": VERSION,
        "stage": "pre_outcome",
        "source": {
            "inputs_artifact": inputs_artifact,
            "recipe_id": recipe["id"],
            "recipe_version": recipe["version"],
            "source_id": basis["source_id"],
            "evaluation_basis_id": basis["evidence_id"],
            "identities": {key: basis[key] for key in IDENTITIES},
            "account_comparison_id": basis["comparison"]["id"],
            "strategies": subject_rows,
        },
        "subject": {
            "scope": recipe["scope"],
            "strategy_type": "supplied_scanner_signals",
            "asset_profile": "NSE_long_cash_equities",
            "exchange": snapshot["provenance"]["exchange"],
            "symbol_count": len(symbols),
            "symbols_id": fingerprint(symbols),
            "interval": interval,
            "observations": basis["period"],
            "instrument_identity": basis["instruments_id"],
        },
        "economic_assumptions": {
            "capital": basis["comparison"]["capital"],
            "currency": basis["comparison"]["currency"],
            "engine": portfolio["engine"],
            "execution": deepcopy(basis["comparison"]["execution"]),
            "settings": settings,
            "settings_id": settings_identity(settings),
            "costs": deepcopy(basis["comparison"]["costs"]),
            "price_basis": {
                key: deepcopy(snapshot["provenance"][key])
                for key in (
                    "provider",
                    "native_price_policy",
                    "adjustment_basis",
                    "synthetic",
                    "calendar_basis",
                    "temporal_version",
                )
                if key in snapshot["provenance"]
            },
            "risk_policy": recipe["policies"]["risk"],
        },
        "objective": {
            "id": objective,
            "definition": OBJECTIVES[objective],
            "selection_acceptance": deepcopy(recipe["selection"]),
            "threshold_interpretation": "Historical selection acceptance only; not a future drawdown cap or live risk stop.",
        },
        "method_admission": methods,
        "search": {
            "specification": deepcopy(recipe["search"]),
            "ranges": deepcopy(recipe["ranges"]),
            "hypothesis_catalog": deepcopy(
                recipe.get("hypothesis_catalog", recipe.get("condition_catalog", []))
            ),
            "data_requirements": deepcopy(plan.get("data", {})),
            "stock_evidence_id": recipe.get("stock_evidence_id"),
            "market_evidence_id": recipe.get("market_evidence_id"),
        },
        "budget": {
            **deepcopy(recipe["budget"]),
            "max_final_verification_evaluations": 0 if validation_mode == "exploratory_only" else 2,
        },
        "validation": {
            "mode": validation_mode,
            "periods": periods,
            "reservation_owner": "existing_account_period_use_service",
            "reservation_status": "not_certified_by_this_receipt",
            "policies": deepcopy(recipe["policies"]),
        },
        "supported_promise": {
            "selection_scope": "Best supported evaluated settings within this recorded programme.",
            "global_optimum": False,
            "statistical_confidence_claim": False,
            "validated_recommendation_possible": validation_mode == "chronological",
            "final_results_do_not_rerank": True,
            "original_scanner_reconstruction": False,
            "live_execution": False,
        },
    }
    if "walk_forward" in recipe:
        mandate["validation"]["walk_forward"] = deepcopy(recipe["walk_forward"])
    if "execution_research" in recipe:
        mandate["search"]["execution_research"] = deepcopy(recipe["execution_research"])
        mandate["search"]["execution_features_id"] = recipe.get("execution_features_id")
        mandate["economic_assumptions"]["risk_limits"] = deepcopy(portfolio["risk_limits"])
    _bounded(mandate)
    mandate["id"] = fingerprint(mandate)
    _bounded(mandate)
    return mandate


def verify(mandate, *, evidence, inputs_artifact):
    """Reject self-consistent tampering by rebuilding from the frozen inputs."""
    if not isinstance(mandate, dict) or mandate.get("version") != VERSION:
        raise ValueError("Research mandate contract is unavailable")
    _bounded(mandate)
    if mandate.get("id") != fingerprint(
        {key: value for key, value in mandate.items() if key != "id"}
    ):
        raise ValueError("Research mandate identity conflicts")
    if mandate != build(evidence, inputs_artifact=inputs_artifact):
        raise ValueError("Research mandate conflicts with frozen source assumptions")
    return True
