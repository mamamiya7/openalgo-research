"""Bounded causal market-condition screening after an unchanged Optuna search.

The exit search uses the first period only. Four fixed market presets screen the
baseline and up to three exit candidates; later checks select at most one before
any final-period calculation. This is a staged screen, not exhaustive joint
indicator optimization. All caches/checkpoints are run-local and bounded.
"""

from copy import deepcopy

from research.automatic_protocol import CONDITION_VERSION as VERSION
from research.automatic_protocol import compile_recipe, slice_period
from research.automatic_research import _entry_dates, _score, _settings, _support
from research.connectors.optuna_portfolio import MAX_CHECKPOINT_BYTES, _fingerprint, run_search
from research.entry_conditions import VERSION as RULE_VERSION
from research.entry_conditions import apply, enrich, markers
from research.evaluation_basis import build_evaluation_basis
from research.portfolio import fingerprint
from research.regimes import recipe as market_recipe
from research.report_contract import settings_identity

MAX_SIMULATIONS = 100


def catalog(strategies):
    entries = [{"label": "No extra condition", "entry_conditions": None}]
    for dimension, regime, label in (
        ("trend", "up", "Rising Nifty trend"),
        ("volatility", "normal", "Normal Nifty volatility"),
        ("volatility", "high", "High Nifty volatility"),
    ):
        entries.append(
            {
                "label": label,
                "entry_conditions": {
                    "version": RULE_VERSION,
                    "recipe": market_recipe(),
                    "rules": [
                        {"strategy_id": row["id"], "dimension": dimension, "regime": regime}
                        for row in strategies
                    ],
                },
            }
        )
    return entries


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
    admit_final=None,
):
    recipe = compile_recipe(evidence)
    if evidence.get("automatic_recipe") != recipe or recipe["version"] != VERSION:
        raise ValueError("The saved automatic entry-condition recipe changed")
    market = evidence.get("entry_condition_market")
    if not market or recipe["market_evidence_id"] != market["evidence"]["id"]:
        raise ValueError("Automatic conditions need exact saved market history")
    binding = fingerprint({"recipe": recipe, "market": market})
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
            "candidates": None,
            "selection": None,
            "selected_report": None,
            "final_report": None,
            "baseline_final_report": None,
        }
    )
    if saved and (
        state.get("version") != VERSION
        or state.get("binding") != binding
        or state.get("checkpoint_id")
        != _fingerprint(
            {k: v for k, v in state.items() if k != "checkpoint_id"},
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        or type(state.get("simulations")) is not int
        or not 0 <= state["simulations"] <= MAX_SIMULATIONS
    ):
        raise ValueError("Automatic condition checkpoint inputs or evidence changed")
    baseline = _settings(evidence["strategies"])
    baseline_id = settings_identity(baseline)
    capital = evidence["portfolio"]["capital"]
    windows = {}

    def control():
        cancelled()
        if boundary:
            boundary()

    def persist(stage="optimization"):
        state.pop("checkpoint_id", None)
        state["checkpoint_id"] = _fingerprint(state, max_bytes=MAX_CHECKPOINT_BYTES)
        checkpoint(
            deepcopy(state),
            {
                "completed": state["simulations"],
                "total": MAX_SIMULATIONS,
                "stage": stage,
            },
        )

    def beat(done, total):
        cancelled()
        progress(state["simulations"] + min(1, done / max(1, total)), MAX_SIMULATIONS)

    def window(period):
        if period not in windows:
            windows[period] = slice_period(evidence, period)
        return windows[period]

    def prepared(settings, condition, period, stress=False):
        current = deepcopy(window(period))
        by_id = {row["id"]: row for row in settings}
        current["strategies"] = [
            {**row, **deepcopy(by_id[row["id"]]), "search": {}} for row in current["strategies"]
        ]
        if stress:
            for row in current["strategies"]:
                cost = row["config"]["cost_bps"]
                row["config"]["cost_bps"] = max(2 * cost, cost + 5)
        if condition:
            current["portfolio"] = {**current["portfolio"], "entry_conditions": condition}
            current = apply(current, market)
        return current

    def simulate(settings, condition, period, stress=False):
        control()
        if state["simulations"] >= MAX_SIMULATIONS:
            raise ValueError("Automatic research reached its recorded calculation limit")
        current = prepared(settings, condition, period, stress)
        report = evaluate(current["strategies"], current["snapshot"], capital, progress=beat)
        if _settings(report["strategies"]) != _settings(current["strategies"]):
            raise ValueError("The calculation returned different strategy settings")
        _score(report["summary"])
        report["evaluation_basis"] = build_evaluation_basis(
            current, period=("evaluation" if period == "final" else "selection")
        )
        enrich(report, current)
        state["simulations"] += 1
        return report

    def measured(settings, condition, period, stress=False):
        key = fingerprint(
            {"settings": settings, "condition": condition, "period": period, "stress": stress}
        )
        if key not in state["evaluations"]:
            report = simulate(settings, condition, period, stress)
            state["evaluations"][key] = {
                "summary": deepcopy(report["summary"]),
                "entry_dates": _entry_dates(report),
                "counts": report.get("entry_condition_counts", []),
            }
            if period == "final" and condition is None and settings == baseline:
                state["baseline_final_report"] = report
            persist()
        return state["evaluations"][key]

    baseline_search = measured(baseline, None, "search")
    search_window = window("search")
    if state["search_result"] is None:

        def evaluate_search(rows, snapshot, amount, progress=None):
            control()
            if state["simulations"] >= MAX_SIMULATIONS:
                raise ValueError("Automatic research reached its calculation limit")
            report = evaluate(rows, snapshot, amount, progress=progress)
            state["simulations"] += 1
            return report

        def save_search(value, counts):
            state["search_checkpoint"] = value
            persist()

        state["search_result"] = run_search(
            search_window["strategies"],
            search_window["snapshot"],
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
        state["search_checkpoint"] = None
        persist()
    search = state["search_result"]
    if state["candidates"] is None:
        exits = [{"strategies": baseline, "trial_number": None}]
        exits.extend(
            [row for row in search["experiment"]["rows"] if row["config_id"] != baseline_id][:3]
        )
        candidates = []
        for entry in recipe["condition_catalog"]:
            for exit_choice in exits:
                settings, condition = exit_choice["strategies"], entry["entry_conditions"]
                tested = measured(settings, condition, "search")
                candidate_settings = markers(settings, condition)
                candidates.append(
                    {
                        "config_id": settings_identity(candidate_settings),
                        "strategies": _settings(settings),
                        "entry_conditions": condition,
                        "condition_label": entry["label"],
                        "trial_number": exit_choice["trial_number"],
                        **deepcopy(tested),
                    }
                )
        state["candidates"] = sorted(
            candidates,
            key=lambda row: (
                -_score(row["summary"]),
                row["config_id"],
            ),
        )
        persist()
    if activity:
        activity({"stage": "validation"})
    if state["selection"] is None:
        references = {period: measured(baseline, None, period) for period in ("check1", "check2")}
        finalists = [row for row in state["candidates"] if row["config_id"] != baseline_id][:3]
        checks = []
        for row in finalists:
            settings, condition, reasons, checks_windows = (
                row["strategies"],
                row["entry_conditions"],
                [],
                [],
            )
            for period in ("check1", "check2"):
                tested = measured(settings, condition, period)
                reference = references[period]["summary"]
                reasons.extend(
                    f"{period}: {why}"
                    for why in _support(
                        tested["summary"],
                        reference,
                        dates=tested["entry_dates"],
                    )
                )
                checks_windows.append(
                    {
                        "period": period,
                        **tested,
                        "baseline_summary": reference,
                        "score_delta": _score(tested["summary"]) - _score(reference),
                    }
                )
            stress = None
            if not reasons:
                stressed_base = measured(baseline, None, "check2", True)
                tested = measured(settings, condition, "check2", True)
                reasons.extend(
                    f"Higher costs: {why}"
                    for why in _support(
                        tested["summary"],
                        stressed_base["summary"],
                        dates=tested["entry_dates"],
                    )
                )
                stress = {
                    **tested,
                    "baseline_summary": stressed_base["summary"],
                    "score_delta": _score(tested["summary"]) - _score(stressed_base["summary"]),
                }
            checks.append(
                {
                    "config_id": row["config_id"],
                    "trial_number": row["trial_number"],
                    "condition_label": row["condition_label"],
                    "entry_conditions": condition,
                    "eligible": not reasons,
                    "reasons": reasons,
                    "windows": checks_windows,
                    "stress": stress,
                    "selection_score": min(item["score_delta"] for item in checks_windows),
                }
            )
        retained = sorted(
            [row for row in checks if row["eligible"]],
            key=lambda row: (
                -row["selection_score"],
                row["config_id"],
            ),
        )
        chosen = next(
            (row for row in finalists if retained and row["config_id"] == retained[0]["config_id"]),
            {
                "config_id": baseline_id,
                "strategies": baseline,
                "entry_conditions": None,
                "condition_label": "No extra condition",
                "trial_number": None,
            },
        )
        state["selection"] = {**deepcopy(chosen), "is_baseline": not retained, "checks": checks}
        persist()
    selected = state["selection"]
    if state["selected_report"] is None:
        state["selected_report"] = simulate(
            selected["strategies"], selected["entry_conditions"], "search"
        )
        expected = baseline_search if selected["is_baseline"] else selected
        if state["selected_report"]["summary"] != expected["summary"]:
            raise ValueError("The selected entry rules did not reproduce their search result")
        persist()
    # Account-owned admission is required in the service; numerical tests may
    # supply an isolated callback. A failed claim performs no final simulation.
    if admit_final:
        admit_final(recipe)
    final_baseline = measured(baseline, None, "final")
    if state["final_report"] is None:
        state["final_report"] = (
            deepcopy(state["baseline_final_report"])
            if selected["is_baseline"]
            else simulate(selected["strategies"], selected["entry_conditions"], "final")
        )
        persist()
    final = state["final_report"]
    reasons = _support(final["summary"], final_baseline["summary"], dates=_entry_dates(final))
    supports = not selected["is_baseline"] and not reasons
    status = "supported" if supports else "not_supported"
    if not supports and (
        any("Fewer than" in why for why in reasons)
        or any("Fewer than" in why for check in selected["checks"] for why in check["reasons"])
    ):
        status = "inconclusive"
    headline = {
        "supported": "An improvement held up in the later check",
        "not_supported": "No reliable improvement over unchanged settings",
        "inconclusive": "More completed trades across different dates are needed",
    }[status]
    result = deepcopy(state["selected_report"])
    experiment = deepcopy(search["experiment"])
    experiment.update(
        objective_winner_id=experiment["recommendation_id"],
        recommendation_id=selected["config_id"],
        selected_strategies=result["strategies"],
        selected_reports={selected["config_id"]: deepcopy(result)},
        findings=[
            headline,
            "Exit proposals precede a fixed four-condition screen; final outcomes never rerank.",
        ],
        next_action="Inspect the saved checks before choosing a setup",
    )
    result["experiment"] = experiment
    result["automatic_research"] = {
        "version": VERSION,
        "status": status,
        "headline": headline,
        "recipe": recipe,
        "selected_config_id": selected["config_id"],
        "selected_is_baseline": selected["is_baseline"],
        "selected_condition": selected["condition_label"],
        "selection_basis": "Best weakest-window improvement after both development and higher-cost checks; fixed before final evaluation",
        "baseline": baseline_search["summary"],
        "checks": selected["checks"],
        "condition_candidates": [
            {
                k: row[k]
                for k in (
                    "config_id",
                    "condition_label",
                    "summary",
                    "entry_dates",
                    "counts",
                )
            }
            for row in state["candidates"]
        ],
        "final": {
            "summary": final["summary"],
            "baseline_summary": final_baseline["summary"],
            "score_delta": _score(final["summary"]) - _score(final_baseline["summary"]),
            "supports": supports,
            "reasons": reasons,
            "entry_dates": _entry_dates(final),
        },
        "counts": {
            "proposals": len(experiment["trials"]),
            "simulations": state["simulations"],
            "finalists": len(selected["checks"]),
            "condition_candidates": len(state["candidates"]),
        },
        "unsupported_families": [
            "Stock indicator discovery",
            "Neighbouring-parameter stability",
            "Live execution",
        ],
        "evidence_scope": "Exploratory scanner research; prior viewing outside this application is unknown",
    }
    periods = recipe["periods"]
    result["validation"] = {
        "label": "Final later-period check",
        "train_from": periods["search"]["from"],
        "train_to": periods["search"]["to"],
        "test_from": periods["final"]["from"],
        "test_to": periods["final"]["to"],
        "training_signals": len(search_window["signals"]),
        "testing_signals": len(window("final")["signals"]),
        "shared_positions_across_periods": False,
        "selection_basis": result["automatic_research"]["selection_basis"],
        "result": deepcopy(final),
    }
    result.update(portfolio=deepcopy(evidence["portfolio"]), config={"initial_capital": capital})
    result["source"] = {
        "interval": evidence["snapshot"]["provenance"]["interval"],
        "provider": "OpenAlgo Historify",
        "signal_count": len(search_window["signals"]),
        "strategy_count": len(evidence["strategies"]),
    }
    experiment["evaluation_basis_id"] = build_evaluation_basis(search_window, period="selection")[
        "evidence_id"
    ]
    from research.study_analysis import build_study_analysis

    experiment["study_analysis"] = build_study_analysis(experiment, progress=beat)
    from research.analytics import add_price_charts

    for report, context in (
        (result, search_window),
        (result["validation"]["result"], window("final")),
    ):
        if report.get("analysis"):
            report["analysis"] = add_price_charts(report["analysis"], report, context["snapshot"])
    return result
