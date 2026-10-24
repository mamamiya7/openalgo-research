"""Predeclared automatic decisions over native accounts; no model or broker calls.

The optimizer may propose settings, never rewrite its objective or validation.
Challenges describe the frozen winner and cannot rerank using their outcomes.
"""

from copy import deepcopy
from statistics import median

from research.automatic_research import _support
from research.report_contract import fingerprint

POLICY = "automatic_decision_v1"
VERSION = "research-automatic-decision-v1"
MAX_ACCOUNTS = 9  # cost pair, four neighbours, three fixed-choice prefixes


def enabled(portfolio):
    return portfolio.get("automatic_research", {}).get("decision_policy") == POLICY


def descriptor():
    value = {
        "version": VERSION,
        "policy": POLICY,
        "objective": "net_return_pct - max_drawdown_pct",
        "feedback": "Optuna feasibility constraints: ordinary support, design admission and native audit",
        "max_additional_accounts": MAX_ACCOUNTS,
        "minimum_neighbors": 2,
        "minimum_neighbor_pass_fraction": 0.75,
        "minimum_neighbor_median_return_edge_fraction": 0.5,
        "maximum_drawdown_pct": 25,
        "cost_challenge": "max(2 * cost_bps, cost_bps + 5); frozen slippage",
        "goal": "Positive net improvement over the original account and cash, surviving costs and local settings challenges within every frozen hard limit",
        "stopping": "Complete all 408 proposals within 512 attempts; never extend until profitable",
        "selection": "Challenge the frozen primary choice; failures do not admit a different winner",
        "evidence": "Same-history costs, neighbours, prefixes and seeds are diagnostics, never independent validation",
    }
    return {**value, "id": fingerprint(value)}


def feedback(row, reference, design_policy):
    """Same admission rules at learning and selection; fixed named constraints."""
    from research.automatic_design import reasons

    support = support_reasons(row["summary"], reference["summary"], dates=row["entry_dates"])
    design = reasons(row, design_policy)
    audit = row.get("portfolio_constraint_evidence", {})
    return {
        "support": float(len(support)),
        "sample_design": float(len(design)),
        "native_constraints": 0.0 if audit.get("status") == "supported" else 1.0,
    }


def support_reasons(summary, baseline, *, dates):
    failures = _support(summary, baseline, dates=dates)
    if summary["net_return_pct"] <= baseline["net_return_pct"]:
        failures.append("Net return did not improve on unchanged settings")
    return failures


def preflight(evidence, recipe):
    """Outcome-free intent and evidence limits, frozen before the first account."""
    availability = recipe.get("history_availability", {})
    bounds = recipe["periods"]["search"]
    facts = {
        "version": VERSION,
        "policy_id": recipe["automatic_decision"]["id"],
        "stage": "pre_outcome",
        "goal": descriptor()["goal"],
        "objective": descriptor()["objective"],
        "signal_count": len(evidence["signals"]),
        "symbol_count": len({row["symbol"] for row in evidence["signals"]}),
        "search_period": deepcopy(bounds),
        "complete_entry_sessions": availability.get("usable_entry_session_count"),
        "validation_mode": recipe["validation_mode"],
        "independent_later_period_available": recipe["validation_mode"] == "chronological",
        "history_reason": availability.get("reason"),
        "feasibility": "Use native price, whole-share, cost, liquidity and hard-risk checks for every candidate; extra funds do not automatically resolve policy, ticket, liquidity or evidence constraints",
        "budget": {
            "proposals": recipe["budget"]["proposals"],
            "max_simulations": recipe["budget"]["max_simulations"],
        },
        "steps": [
            "freeze_inputs_and_goal",
            "baseline",
            "constrained_search",
            "freeze_choice",
            "challenge_costs_and_neighbors",
            "diagnose_capital",
            "conclude",
        ],
    }
    return {**facts, "id": fingerprint(facts)}


def conclude(
    *,
    policy,
    selected,
    baseline,
    stress,
    stress_baseline,
    neighbors,
    prefixes,
    seed_choices,
    is_baseline,
    independently_validated=False,
    validation_passed=False,
    capital_diagnosis=None,
):
    if policy != descriptor():
        raise ValueError("The automatic decision policy changed")
    reasons = []
    if is_baseline:
        reasons.append("No changed setup passed historical admission; retain original settings")
    ordinary = support_reasons(
        selected["summary"], baseline["summary"], dates=selected["entry_dates"]
    )
    reasons.extend(ordinary)
    checks = []
    if not is_baseline:
        if stress is None or stress_baseline is None:
            raise ValueError("Automatic decisions require the recorded cost challenge pair")
        failures = support_reasons(
            stress["summary"], stress_baseline["summary"], dates=stress["entry_dates"]
        )
        if stress.get("automatic_design", {}).get("qualified") is not True:
            failures.append(
                "Higher-cost account failed the historical sample and distribution checks"
            )
        if stress.get("portfolio_constraint_evidence", {}).get("status") != "supported":
            failures.append("Higher-cost native financial constraints are not supported")
        checks.append(
            {
                "kind": "higher_costs",
                "passed": not failures,
                "summary": deepcopy(stress["summary"]),
                "reasons": failures,
            }
        )
        if failures:
            reasons.append("The chosen rules did not survive higher transaction costs")
        passing = [
            not support_reasons(row["summary"], baseline["summary"], dates=row["entry_dates"])
            and row.get("automatic_design", {}).get("qualified") is True
            and row.get("portfolio_constraint_evidence", {}).get("status") == "supported"
            for row in neighbors
        ]
        edge = selected["summary"]["net_return_pct"] - baseline["summary"]["net_return_pct"]
        median_edge = (
            median(
                [
                    row["summary"]["net_return_pct"] - baseline["summary"]["net_return_pct"]
                    for row in neighbors
                ]
            )
            if neighbors
            else None
        )
        neighbor_pass = (
            len(neighbors) >= policy["minimum_neighbors"]
            and sum(passing) / len(passing) >= policy["minimum_neighbor_pass_fraction"]
            and median_edge >= policy["minimum_neighbor_median_return_edge_fraction"] * edge
        )
        checks.append(
            {
                "kind": "neighboring_settings",
                "passed": neighbor_pass,
                "tested": len(neighbors),
                "passing": sum(passing),
                "median_return_edge_pct": median_edge,
            }
        )
        if not neighbor_pass:
            reasons.append("Nearby settings did not retain adequate support and improvement")
    stable = sum(item["config_id"] == selected["config_id"] for item in seed_choices)
    # Repeatability is recorded separately: shared screening and shared history
    # cannot provide independent confirmations of economic usefulness.
    historical_candidate = not reasons
    if not historical_candidate:
        outcome = "no_useful_improvement"
    elif not independently_validated:
        outcome = "insufficient_evidence"
    elif not validation_passed:
        outcome = "no_useful_improvement"
        reasons.append("The frozen later-period validation did not support this choice")
    else:
        outcome = "supported_candidate"
    if historical_candidate and not independently_validated:
        reasons.append(
            "A historical candidate passed challenges; independent later evidence is unavailable"
        )
    value = {
        "version": VERSION,
        "policy_id": policy["id"],
        "outcome": outcome,
        "historical_candidate": historical_candidate,
        "independently_validated": independently_validated and validation_passed,
        "selected_config_id": selected["config_id"],
        "reasons": reasons,
        "checks": checks,
        "prefix_diagnostics": deepcopy(prefixes),
        "seed_diagnostics": {
            "matching_choices": stable,
            "tested": len(seed_choices),
            "independent_confirmations": 0,
        },
        "capital_diagnosis": deepcopy(capital_diagnosis),
        "allocation": deepcopy(selected.get("automatic_design", {}).get("capital_use", {})),
        "stopping_reason": "The full predeclared search and bounded challenges completed; no reranking from challenge outcomes",
        "next_action": "Collect new signal outcomes before promotion"
        if outcome == "insufficient_evidence"
        else "Retain original rules; review the recorded failure reasons"
        if outcome == "no_useful_improvement"
        else "Review the supported candidate before any trading decision",
    }
    return {**value, "id": fingerprint(value)}
