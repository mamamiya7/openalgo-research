"""Bounded trade-management research using native simulation and Optuna.

The search sample proposes candidates; two later development windows select one;
the final period can only confirm or reject it. No prices are fetched here. All
durable units are completed simulations or Optuna proposals, never partial PnL.
"""

from copy import deepcopy
from math import isfinite

from research.connectors.optuna_portfolio import MAX_CHECKPOINT_BYTES, _fingerprint, run_search
from research.portfolio import fingerprint
from research.report_contract import settings_identity

VERSION = "automatic-trade-management-v1"
MAX_SIMULATIONS = 70


def _settings(rows):
    return [
        {key: deepcopy(row[key]) for key in ("id", "name", "allocation_pct", "config")}
        for row in rows
    ]


def _score(summary):
    for key in ("net_return_pct", "max_drawdown_pct", "closed_trades"):
        value = summary.get(key)
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError(f"Automatic research needs a finite {key} from the engine")
    if (
        summary["max_drawdown_pct"] < 0
        or summary["closed_trades"] < 0
        or int(summary["closed_trades"]) != summary["closed_trades"]
    ):
        raise ValueError("Automatic research received invalid risk or trade counts")
    return summary["net_return_pct"] - summary["max_drawdown_pct"]


def _support(summary, baseline, *, dates):
    reasons = []
    if summary["closed_trades"] < 5 or dates < 5:
        reasons.append("Fewer than five closed trades or five entry dates")
    if summary["max_drawdown_pct"] > 25:
        reasons.append("Drawdown exceeds the research preset's 25% limit")
    if summary["net_return_pct"] <= 0:
        reasons.append("Return after costs is not positive")
    if _score(summary) <= _score(baseline):
        reasons.append("Return minus drawdown did not improve on unchanged settings")
    return reasons


def _entry_dates(report):
    # Count distinct entry dates, not the number of stocks on one crowded day.
    # Different dates do not establish statistically independent observations.
    return len(
        {
            str(row.get("entry_timestamp") or row.get("entry_date") or "")[:10]
            for row in report.get("ledger", [])
            if row.get("status") == "closed"
            and (row.get("entry_timestamp") or row.get("entry_date"))
        }
    )


def run(
    evidence,
    *,
    evaluate,
    saved=None,
    checkpoint,
    progress,
    cancelled,
    activity=None,
    observe=None,
    boundary=None,
):
    from research.automatic_protocol import compile_recipe, slice_period
    from research.evaluation_basis import build_evaluation_basis

    recipe = compile_recipe(evidence)
    if evidence.get("automatic_recipe") != recipe:
        raise ValueError("The saved automatic research recipe changed")
    binding = fingerprint(
        {
            "recipe": recipe,
            "portfolio": evidence["portfolio"],
            "strategies": evidence["strategies"],
            "snapshot": evidence["snapshot"],
            "versions": evidence["versions"],
        }
    )
    state = (
        deepcopy(saved)
        if saved
        else {
            "version": VERSION,
            "binding": binding,
            "simulations": 0,
            "evaluations": {},
            "search_checkpoint": None,
            "search_result": None,
            "selection": None,
            "selected_report": None,
            "final_report": None,
        }
    )
    if saved:
        if (
            state.get("version") != VERSION
            or state.get("binding") != binding
            or state.get("checkpoint_id")
            != _fingerprint(
                {key: value for key, value in state.items() if key != "checkpoint_id"},
                max_bytes=MAX_CHECKPOINT_BYTES,
            )
        ):
            raise ValueError("Automatic research checkpoint inputs or evidence changed")
        if not 0 <= state.get("simulations", -1) <= MAX_SIMULATIONS:
            raise ValueError("Invalid automatic research simulation count")
    capital = evidence["portfolio"]["capital"]
    baseline = _settings(evidence["strategies"])
    baseline_id = settings_identity(baseline)
    period_cache = {}  # At most four disjoint, run-local slices; no global feature cache.

    def window_for(period):
        if period not in period_cache:
            period_cache[period] = slice_period(evidence, period)
        return period_cache[period]

    def control():
        cancelled()
        if boundary:
            boundary()

    def persist(stage="optimization"):
        state.pop("checkpoint_id", None)
        state["checkpoint_id"] = _fingerprint(state, max_bytes=MAX_CHECKPOINT_BYTES)
        checkpoint(
            deepcopy(state),
            {"completed": state["simulations"], "total": MAX_SIMULATIONS, "stage": stage},
        )

    def beat(done, total):
        cancelled()
        progress(state["simulations"] + min(1, done / max(1, total)), MAX_SIMULATIONS)

    def simulate(settings, period, *, stress=False):
        control()
        if state["simulations"] >= MAX_SIMULATIONS:
            raise ValueError("Automatic research reached its saved simulation limit")
        window = window_for(period)
        chosen = {row["id"]: row for row in settings}
        rows = [
            {**row, **deepcopy(chosen[row["id"]]), "search": {}} for row in window["strategies"]
        ]
        if stress:
            for row in rows:
                cost = row["config"]["cost_bps"]
                row["config"]["cost_bps"] = max(2 * cost, cost + 5)
        report = evaluate(rows, window["snapshot"], capital, progress=beat)
        if report.get("strategies") != _settings(rows):
            raise ValueError("Automatic research received different settings from its engine")
        _score(report["summary"])
        state["simulations"] += 1
        return report

    def measured(settings, period, *, stress=False):
        key = fingerprint({"settings": settings, "period": period, "stress": stress})
        if key not in state["evaluations"]:
            report = simulate(settings, period, stress=stress)
            state["evaluations"][key] = {
                "summary": deepcopy(report["summary"]),
                "entry_dates": _entry_dates(report),
            }
            if period == "final" and state["selection"]["is_baseline"]:
                state["final_report"] = report
            persist()
        return state["evaluations"][key]

    if activity:
        activity({"stage": "backtest"})
    baseline_search = measured(baseline, "search")
    search_evidence = window_for("search")
    if state["search_result"] is None:

        def evaluate_search(rows, snapshot, amount, progress=None):
            # The adapter supplies the exact fixed search window and owns repeated
            # proposal reuse. This wrapper counts actual engine simulations only.
            cancelled()
            if state["simulations"] >= MAX_SIMULATIONS:
                raise ValueError("Automatic research reached its saved simulation limit")
            report = evaluate(rows, snapshot, amount, progress=progress)
            state["simulations"] += 1
            return report

        def save_search(value, counts):
            state["search_checkpoint"] = value
            persist()

        result = run_search(
            search_evidence["strategies"],
            search_evidence["snapshot"],
            capital,
            evidence["portfolio"]["optimization"],
            evaluate=evaluate_search,
            execution=evidence["versions"],
            progress=beat,
            checkpoint=save_search,
            saved=state["search_checkpoint"],
            activity=activity,
            observe=observe,
            record_timing=True,
            boundary=control,
        )
        state["search_result"] = result
        state["search_checkpoint"] = None
        persist()
    search_result = state["search_result"]
    if activity:
        activity({"stage": "validation"})

    if state["selection"] is None:
        references = {period: measured(baseline, period) for period in ("check1", "check2")}
        checks = []
        finalists = [
            row for row in search_result["experiment"]["rows"] if row["config_id"] != baseline_id
        ][:3]
        for row in finalists:
            settings, windows, reasons = row["strategies"], [], []
            for period in ("check1", "check2"):
                tested = measured(settings, period)
                reference = references[period]["summary"]
                failures = _support(tested["summary"], reference, dates=tested["entry_dates"])
                reasons.extend(f"{period}: {reason}" for reason in failures)
                windows.append(
                    {
                        "period": period,
                        "summary": tested["summary"],
                        "baseline_summary": reference,
                        "entry_dates": tested["entry_dates"],
                        "score_delta": _score(tested["summary"]) - _score(reference),
                    }
                )
            stressed = None
            if not reasons:
                stressed_base = measured(baseline, "check2", stress=True)
                stress = measured(settings, "check2", stress=True)
                reasons.extend(
                    f"Higher costs: {reason}"
                    for reason in _support(
                        stress["summary"], stressed_base["summary"], dates=stress["entry_dates"]
                    )
                )
                stressed = {
                    "summary": stress["summary"],
                    "baseline_summary": stressed_base["summary"],
                    "score_delta": _score(stress["summary"]) - _score(stressed_base["summary"]),
                }
            checks.append(
                {
                    "config_id": row["config_id"],
                    "trial_number": row["trial_number"],
                    "eligible": not reasons,
                    "reasons": reasons,
                    "windows": windows,
                    "stress": stressed,
                    "selection_score": min(item["score_delta"] for item in windows),
                }
            )
        retained = sorted(
            (row for row in checks if row["eligible"]),
            key=lambda row: (-row["selection_score"], row["config_id"]),
        )
        chosen_id = retained[0]["config_id"] if retained else baseline_id
        chosen = next(
            (row["strategies"] for row in finalists if row["config_id"] == chosen_id), baseline
        )
        # Committed BEFORE the first final-period simulation. Resume cannot use
        # the final outcome to select a different candidate or extend the search.
        state["selection"] = {
            "config_id": chosen_id,
            "strategies": chosen,
            "is_baseline": not retained,
            "checks": checks,
        }
        persist()
    selection = state["selection"]
    if state["selected_report"] is None:
        if selection["config_id"] == search_result["experiment"]["recommendation_id"]:
            state["selected_report"] = {
                key: value for key, value in search_result.items() if key != "experiment"
            }
        else:
            report = simulate(selection["strategies"], "search")
            expected = (
                baseline_search["summary"]
                if selection["is_baseline"]
                else next(
                    row["summary"]
                    for row in search_result["experiment"]["rows"]
                    if row["config_id"] == selection["config_id"]
                )
            )
            if report["summary"] != expected:
                raise ValueError(
                    "The selected settings did not reproduce their saved search result"
                )
            state["selected_report"] = report
        persist()
    final_baseline = measured(baseline, "final")
    if state["final_report"] is None:
        state["final_report"] = simulate(selection["strategies"], "final")
        persist()
    final_report = state["final_report"]
    final_reasons = _support(
        final_report["summary"], final_baseline["summary"], dates=_entry_dates(final_report)
    )
    supports = not selection["is_baseline"] and not final_reasons
    status = "supported" if supports else "not_supported"
    insufficient = any("Fewer than" in reason for reason in final_reasons) or any(
        "Fewer than" in reason for row in selection["checks"] for reason in row["reasons"]
    )
    if insufficient and not supports:
        status = "inconclusive"
    headline = {
        "supported": "An improvement held up in the later check",
        "not_supported": "No reliable improvement over unchanged settings",
        "inconclusive": "More completed trades across different dates are needed",
    }[status]
    result = deepcopy(state["selected_report"])
    experiment = deepcopy(search_result["experiment"])
    experiment.update(
        objective_winner_id=experiment["recommendation_id"],
        recommendation_id=selection["config_id"],
        selected_strategies=deepcopy(selection["strategies"]),
        selected_reports={selection["config_id"]: deepcopy(result)},
        findings=[
            headline,
            "Search scores use the first period; research selection uses the two later development checks.",
        ],
        next_action="Inspect the saved checks before choosing a setup",
    )
    result["experiment"] = experiment
    result["automatic_research"] = {
        "version": VERSION,
        "status": status,
        "headline": headline,
        "recipe": recipe,
        "selected_config_id": selection["config_id"],
        "selected_is_baseline": selection["is_baseline"],
        "selection_basis": "Best worst-window improvement among candidates passing both development checks and higher costs; final dates never re-rank settings",
        "baseline": baseline_search["summary"],
        "checks": selection["checks"],
        "final": {
            "summary": final_report["summary"],
            "baseline_summary": final_baseline["summary"],
            "score_delta": _score(final_report["summary"]) - _score(final_baseline["summary"]),
            "supports": supports,
            "reasons": final_reasons,
            "entry_dates": _entry_dates(final_report),
        },
        "counts": {
            "proposals": len(experiment["trials"]),
            "simulations": state["simulations"],
            "finalists": len(selection["checks"]),
        },
        "unsupported_families": [
            "Indicator and market-regime discovery",
            "Independent market benchmark",
            "Neighbouring-parameter stability",
        ],
        "evidence_scope": "Exploratory research on supplied scanner observations; prior viewing elsewhere is unknown",
    }
    search_basis = build_evaluation_basis(search_evidence, period="selection")
    final_evidence = window_for("final")
    later = deepcopy(final_report)
    later["evaluation_basis"] = build_evaluation_basis(final_evidence, period="evaluation")
    periods = recipe["periods"]
    result["validation"] = {
        "label": "Final later-period check",
        "train_from": periods["search"]["from"],
        "train_to": periods["search"]["to"],
        "test_from": periods["final"]["from"],
        "test_to": periods["final"]["to"],
        "training_signals": len(search_evidence["signals"]),
        "testing_signals": len(final_evidence["signals"]),
        "selection_basis": result["automatic_research"]["selection_basis"],
        "shared_positions_across_periods": False,
        "result": later,
    }
    result.update(
        evaluation_basis=search_basis,
        portfolio=deepcopy(evidence["portfolio"]),
        config={"initial_capital": capital},
    )
    result["source"] = {
        "interval": evidence["snapshot"]["provenance"]["interval"],
        "provider": "OpenAlgo Historify",
        "signal_count": len(search_evidence["signals"]),
        "strategy_count": len(evidence["strategies"]),
        **{
            key: value
            for key, value in search_evidence.get("signal_coverage", {}).items()
            if key != "exclusions"
        },
    }
    experiment["evaluation_basis_id"] = search_basis["evidence_id"]
    from research.study_analysis import build_study_analysis

    experiment["study_analysis"] = build_study_analysis(experiment, progress=beat)
    from research.analytics import add_price_charts

    for report, window in ((result, search_evidence), (later, final_evidence)):
        if report.get("analysis"):
            report["analysis"] = add_price_charts(report["analysis"], report, window["snapshot"])
    return result
