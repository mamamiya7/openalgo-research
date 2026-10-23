"""Explicit historical research when supplied signals cannot form later tests.

The native v16 runner owns every sampler, causal gate, account, validation and
checkpoint. This branch schedules its unchanged six fits and describes their
available evidence without inventing a final period or independent validation.
"""

from copy import deepcopy
from math import prod

from research.automatic_protocol import ALLOCATION_VERSION
from research.automatic_research import _entry_dates, _score
from research.connectors.optuna_portfolio import _values
from research.report_contract import settings_identity

VERSION = "research-available-history-findings-v1"
FIT_NAMES = ("fit1", "fit2", "fit3", "search", "search_seed1", "search_seed2")


def run(
    evidence,
    *,
    recipe,
    state,
    optimizer,
    baseline,
    baseline_id,
    none,
    axes,
    entries,
    fit_names,
    fit,
    measured,
    key_for,
    persist,
    control,
    window,
    prepared_portfolio,
    defer_study_analysis,
):
    availability = recipe.get("history_availability", {})
    if (
        recipe.get("version") != ALLOCATION_VERSION
        or recipe.get("validation_mode") != "exploratory_only"
        or availability.get("version") != "research-available-history-v1"
        or availability.get("mode") != "exploratory_only"
        or tuple(fit_names) != FIT_NAMES
        or recipe["budget"]["proposals"] != 408
        or recipe["budget"]["max_simulations"] != 512
        or recipe["budget"]["proposals_per_fit"] != 68
        or state["folds"]
        or "walk_forward" in recipe
        or any(name.startswith("check") or name == "final" for name in recipe["periods"])
    ):
        raise ValueError("The available-history research programme changed")

    # Every declared proposal is replayed or completed. Same-history repeats
    # cannot disappear because another diagnostic lacked independent support.
    for name in FIT_NAMES[:3]:
        fit(name)
    winner, rows = fit("search")
    for name in FIT_NAMES[4:]:
        fit(name)
    if any(len(state["fits"][name]["trials"]) != 68 for name in FIT_NAMES):
        raise ValueError("The available-history proposal history is incomplete")
    expected = {**deepcopy(winner), "is_baseline": winner["config_id"] == baseline_id}
    if state["selection"] is not None and state["selection"] != expected:
        raise ValueError("The frozen available-history selection changed on resume")
    if state["selection"] is None:
        state["selection"] = expected
        persist()
    selected = state["selection"]
    if state["selected_report"] is None:
        measured(
            selected["strategies"],
            selected["hypothesis"],
            "search",
            report_slot="selected_report",
            reconstruct=True,
        )
    report = state["selected_report"]
    row = state["evaluations"][
        key_for(selected["strategies"], selected["hypothesis"], "search", False)
    ]
    if (
        not isinstance(report, dict)
        or settings_identity(report.get("strategies")) != settings_identity(selected["strategies"])
        or report.get("summary") != row["summary"]
        or _entry_dates(report) != row["entry_dates"]
        or report.get("evaluation_basis") != row["evaluation_basis"]
        or report.get("automatic_stock_condition") != row["automatic_stock_condition"]
        or report.get("automatic_recipe") != recipe
    ):
        raise ValueError("The saved available-history report changed its recorded account evidence")
    audit = prepared_portfolio.assess(report, evidence["portfolio_execution_features"])
    if audit != row.get("portfolio_constraint_evidence") or audit != report.get(
        "portfolio_constraint_evidence"
    ):
        raise ValueError("Saved portfolio constraint evidence changed on recovery")
    if state["baseline_final_report"] is not None or state["final_report"] is not None:
        raise ValueError("Available-history research cannot retain an invented final account")

    baseline_summary = measured(baseline, none, "search")["summary"]
    result = deepcopy(report)
    result.pop("validation", None)
    grid = len(entries) * prod(
        len(domain["choices"]) if domain.get("type") == "categorical" else len(_values(domain))
        for domain in axes.values()
    )
    current = state["fits"]["search"]
    headline = "Research completed on available history"
    result["experiment"] = {
        "kind": "portfolio_optimize",
        "experiment_version": recipe["version"],
        "optimizer": deepcopy(optimizer),
        "specification": {**deepcopy(evidence["portfolio"]["optimization"]), "trials": 68},
        "search_space": {
            "axes": {
                "hypothesis": {"type": "categorical", "choices": [item["id"] for item in entries]},
                **deepcopy(axes),
            },
            "grid_size": grid,
            "proposal_budget": 68,
            "objective_definition": optimizer["objective_definition"],
        },
        "counts": {
            "grid": grid,
            "proposed": 68,
            "evaluated_this_pass": len(rows),
            "evaluated_all_passes": len(rows),
            "rejected_allocations": 0,
            "reused_trials": sum(item["reused"] for item in current["trials"]),
            "remaining": max(0, grid - len(rows)),
        },
        "objective_winner_id": rows[0]["config_id"],
        "recommendation_id": selected["config_id"],
        "rows": deepcopy(rows),
        "pass_rows": deepcopy(rows),
        "trials": deepcopy(current["trials"]),
        "analysis_catalog": [],
        "selected_strategies": deepcopy(result["strategies"]),
        "selected_reports": {selected["config_id"]: deepcopy(result)},
        "findings": [
            headline,
            "Every fit describes historical training, not independent validation.",
        ],
        "next_action": "Review and save a historical setup from the available signals",
        "evaluation_basis_id": result["evaluation_basis"]["evidence_id"],
        **{
            key: deepcopy(recipe[key])
            for key in ("trailing_research", "timing_research", "execution_research")
        },
    }
    fits = []
    for name in FIT_NAMES:
        fit_state = state["fits"][name]
        choice = fit_state["selection"]
        period = "search" if name.startswith("search_seed") else name
        seed = (
            optimizer["search_repeat_seeds"][int(name[-1])]
            if name.startswith("search_seed")
            else optimizer["seed"]
        )
        seed = (
            optimizer.get("offline_calibration_seeds", {})
            .get("fit_sampler_seeds", {})
            .get(name, seed)
        )
        fits.append(
            {
                "fit": name,
                "period": period,
                "sampler_seed": seed,
                **deepcopy(recipe["periods"][period]),
                "proposals": len(fit_state["trials"]),
                "unique_accounts": len(fit_state["candidates"]),
                "selected_config_id": choice["config_id"],
                "summary": deepcopy(choice["summary"]),
                "baseline_summary": deepcopy(measured(baseline, none, period)["summary"]),
                "entry_dates": choice["entry_dates"],
                "independently_validated": False,
            }
        )
    result["automatic_research"] = {
        "version": recipe["version"],
        "status": "inconclusive",
        "headline": headline,
        "recipe": deepcopy(recipe),
        "selected_config_id": selected["config_id"],
        "selected_is_baseline": selected["is_baseline"],
        "selected_condition": selected["condition_label"],
        "selection_basis": f"The primary seed-{optimizer['seed']} full-history training choice remains fixed. Prefix fits and same-history seed repeats are diagnostics; they never select a later or better-looking seed.",
        "baseline": deepcopy(baseline_summary),
        "checks": [],
        "final": None,
        "independently_validated": False,
        "evidence_scope": "Historical research on the available scanner signals. Complete holding windows and native constraints remain required; separate later validation is unavailable.",
        "condition_candidates": deepcopy(rows),
        "hypothesis_candidates": deepcopy(rows),
        "counts": {
            "proposals": sum(len(state["fits"][name]["trials"]) for name in FIT_NAMES),
            "simulations": state["simulations"],
            "attempts_reserved": state["attempts"],
            "finalists": 1,
            "condition_candidates": len(rows),
        },
        "available_history": {
            "version": VERSION,
            "mode": "exploratory_only",
            "fits": fits,
            "primary_fit": "search",
            "seed_comparisons": deepcopy(fits[3:]),
            "independently_validated": False,
            "availability": deepcopy(availability),
        },
        "recommendation_checks": {
            "version": "research-recommendation-evidence-v1",
            "status": "insufficient",
            "qualified": False,
            "reasons": [
                "The available signal history cannot supply separate complete later-period checks."
            ],
        },
        "execution_research": {
            "version": "research-execution-findings-v5",
            "risk_limits": deepcopy(recipe["execution_research"]["risk_limits"]),
            "feature_id": recipe["execution_features_id"],
            "portfolio_features_id": recipe["portfolio_execution_features_id"],
            "sizing_controls": [
                deepcopy(state["fits"][name]["sizing_control"]) for name in FIT_NAMES[:4]
            ],
            "policy": "Historical original-sizing comparisons only; every account keeps its declared hard limits and causal execution rules.",
            "allocation_modes": deepcopy(recipe["execution_research"]["allocation_modes"]),
            "selected_allocation_mode": selected["strategies"][0]["execution_policy"][
                "allocation_mode"
            ],
            "allocation_policy": recipe["execution_research"]["allocation_policy"],
            "portfolio_constraints": deepcopy(recipe["policies"]["portfolio_constraints"]),
            "feature_coverage": deepcopy(evidence["portfolio_execution_features"]["counts"]),
        },
        "unsupported_families": [
            "Independent later-period validation",
            "Original scanner reconstruction",
            "Live execution",
            "Statistical confidence claims",
        ],
    }
    result.update(
        portfolio=deepcopy(evidence["portfolio"]),
        config={"initial_capital": evidence["portfolio"]["capital"]},
        source={
            "interval": evidence["snapshot"]["provenance"]["interval"],
            "provider": "OpenAlgo Historify",
            "signal_count": len(window("search")["signals"]),
            "strategy_count": len(baseline),
        },
    )
    if defer_study_analysis:
        from research.study_diagnostics import deferred

        result["experiment"]["diagnostics"] = deferred()
    else:
        from research.study_analysis import build_study_analysis

        result["experiment"]["study_analysis"] = build_study_analysis(
            result["experiment"], progress=lambda *args: control()
        )
    if result.get("analysis"):
        from research.analytics import add_price_charts

        result["analysis"] = add_price_charts(
            result["analysis"], result, window("search")["snapshot"]
        )
    return result
