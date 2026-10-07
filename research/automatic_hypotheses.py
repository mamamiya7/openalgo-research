"""Bounded joint stock-condition and exit research over retained native evidence.

Optuna proposes complete portfolios, never post-processes independent trades.
Completed summaries and serial ask/tell history are durable; an interrupted
attempt consumes a conservative budget reservation before it can be retried.
No prices, files, sockets, database engines or background workers are owned here.
"""

from collections import OrderedDict
from copy import deepcopy
from math import prod

from research.automatic_protocol import compile_recipe, slice_period
from research.automatic_research import _entry_dates, _score, _support
from research.automatic_research import _settings as _numerical_settings
from research.automatic_stability import neighbors
from research.connectors.optuna_portfolio import (
    MAX_CHECKPOINT_BYTES,
    TESTED_OPTUNA_VERSION,
    _candidate,
    _fingerprint,
    _prepare,
    _settings,
    _values,
)
from research.entry_conditions import enrich
from research.evaluation_basis import build_evaluation_basis
from research.portfolio import fingerprint
from research.report_contract import settings_identity
from research.stock_features import apply, catalog, markers

VERSION = "automatic-hypotheses-v2"
EXPANDED_VERSION = "automatic-hypotheses-v3"
BENCHMARK_VERSION = "automatic-hypotheses-v4"
JOINT_VERSION = "automatic-hypotheses-v5"
CONDITION_VERSION = "research-stock-entry-conditions-v1"
MAX_SIMULATIONS = 180
PROPOSALS = 60
FAMILY_SCREEN = 10
MAX_FINALISTS = 3
MAX_NEIGHBORS = 4
MAX_GATE_CACHE = 8


def _display_history(trials):
    """Bound animation points; retain every original proposal in the checkpoint."""
    indexes = (
        range(len(trials))
        if len(trials) <= 100
        else [i * (len(trials) - 1) // 99 for i in range(100)]
    )
    return [{"trial": trials[i]["number"] + 1, "score": trials[i]["value"]} for i in indexes]


def policy_for_version(version):
    """Return a fresh bounded policy; a newer recipe never changes old replay."""
    if version == VERSION:
        return {
            "version": VERSION,
            "condition_version": CONDITION_VERSION,
            "calculation_version": "research-stock-features-v1",
            "proposals": PROPOSALS,
            "max_simulations": MAX_SIMULATIONS,
            "family_screen": FAMILY_SCREEN,
            "max_hypotheses": 9,
            "max_families": 5,
        }
    if version == EXPANDED_VERSION:
        return {
            "version": EXPANDED_VERSION,
            "condition_version": "research-stock-entry-conditions-v2",
            "calculation_version": "research-stock-indicators-v1",
            "proposals": 84,
            "max_simulations": 220,
            "family_screen": 8,
            "max_hypotheses": 18,
            "max_families": 7,
        }
    if version == BENCHMARK_VERSION:
        return {
            "version": BENCHMARK_VERSION,
            "condition_version": "research-stock-entry-conditions-v3",
            "calculation_version": "research-stock-relative-strength-v1",
            "proposals": 96,
            "max_simulations": 240,
            "family_screen": 8,
            "max_hypotheses": 23,
            "max_families": 8,
        }
    if version == JOINT_VERSION:
        return {
            "version": JOINT_VERSION,
            "condition_version": "research-joint-entry-conditions-v1",
            "calculation_version": "research-joint-stock-market-features-v1",
            "proposals": 128,
            "max_simulations": 320,
            "family_screen": 3,
            "max_hypotheses": 92,
            "max_families": 32,
        }
    raise ValueError("Choose a supported automatic hypothesis version")


def _gate(
    current,
    stock,
    hypothesis,
    cache,
    period,
    *,
    apply_fn=None,
    markers_fn=None,
    calculation_version=None,
):
    """Reuse fixed causal membership, then bind its receipt to the actual exits.

    Only declared execution-management axes and transaction cost are ignored in
    the cache key. Entry timing, source rows and their exclusions remain bound.
    The caller owns one frozen snapshot per named period and this run-local cache.
    """
    exit_only = {
        "target_pct",
        "stop_pct",
        "hold_sessions",
        "hold_minutes",
        "trailing_pct",
        "cost_bps",
    }
    apply_fn, markers_fn = apply_fn or apply, markers_fn or markers
    key = fingerprint(
        {
            **({"calculation_version": calculation_version} if calculation_version else {}),
            "period": period,
            "hypothesis": hypothesis,
            "bundle_id": stock["id"],
            "schedule": [
                {
                    "id": row["id"],
                    "signals": row["signals"],
                    "config": {k: v for k, v in row["config"].items() if k not in exit_only},
                }
                for row in current["strategies"]
            ],
        }
    )
    if key not in cache:
        rows, receipt = apply_fn(current, stock, hypothesis)
        if (
            calculation_version
            and receipt.get("calculation_version", receipt.get("version")) != calculation_version
        ):
            raise ValueError("The stock gate returned a different calculation version")
        if len(cache) >= MAX_GATE_CACHE:
            cache.popitem(last=False)
        cache[key] = (rows, receipt)
        return rows, receipt
    cache.move_to_end(key)
    previous_rows, previous_receipt = cache[key]
    by_id = {row["id"]: row for row in previous_rows}
    rows = markers_fn(
        [{**row, "signals": by_id[row["id"]]["signals"]} for row in current["strategies"]],
        hypothesis,
        stock["id"],
    )
    receipt = {
        **{k: v for k, v in previous_receipt.items() if k != "id"},
        "execution_inputs_id": fingerprint(
            {k: current[k] for k in ("portfolio", "strategies", "signals", "snapshot", "versions")}
        ),
        "conditioned_strategies_id": fingerprint(rows),
    }
    return rows, {**receipt, "id": fingerprint(receipt)}


def checkpoint_rows(saved):
    """Expose only completed bounded candidates from an intact checkpoint."""
    if not isinstance(saved, dict) or saved.get("version") not in (
        VERSION,
        EXPANDED_VERSION,
        BENCHMARK_VERSION,
        JOINT_VERSION,
    ):
        raise ValueError("Automatic hypothesis checkpoint inputs or evidence changed")
    policy = policy_for_version(saved["version"])
    if (
        saved.get("checkpoint_id")
        != _fingerprint(
            {key: value for key, value in saved.items() if key != "checkpoint_id"},
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        or type(saved.get("simulations")) is not int
        or type(saved.get("attempts")) is not int
        or not 0 <= saved["simulations"] <= saved["attempts"] <= policy["max_simulations"]
        or not isinstance(saved.get("trials"), list)
        or len(saved["trials"]) > policy["proposals"]
        or not isinstance(saved.get("candidates"), dict)
        or len(saved["candidates"]) > policy["proposals"]
        or not isinstance(saved.get("evaluations"), dict)
        or len(saved["evaluations"]) > saved["simulations"]
    ):
        raise ValueError("Automatic hypothesis checkpoint inputs or evidence changed")
    rows = list(saved["candidates"].values())
    if any(
        row.get("config_id") != settings_identity(row.get("strategies"))
        or row.get("score") != _score(row["summary"])
        for row in rows
    ):
        raise ValueError("Automatic hypothesis checkpoint candidate evidence changed")
    return deepcopy(sorted(rows, key=lambda row: (-row["score"], row["config_id"])))


def _screening(entries, axes, *, policy=None):
    policy = policy or policy_for_version(VERSION)
    families = list(dict.fromkeys(row["family"] for row in entries))
    if (
        not 1 <= len(entries) <= policy["max_hypotheses"]
        or not 1 <= len(families) <= policy["max_families"]
    ):
        raise ValueError("Automatic research needs its bounded stock hypothesis catalog")
    by_family = {family: [row for row in entries if row["family"] == family] for family in families}
    queued = []
    for attempt in range(policy["family_screen"]):
        for family in families:
            hypotheses = by_family[family]
            params = {"hypothesis": hypotheses[attempt % len(hypotheses)]["id"]}
            # Every family sees low, high and midpoint exits before refinement.
            # Remaining screening trials draw their exits from seeded Optuna.
            if attempt < 3:
                for name, domain in axes.items():
                    values = _values(domain)
                    index = (0, len(values) - 1, len(values) // 2)[attempt]
                    params[name] = values[index]
            queued.append(params)
    return queued


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
    """Search jointly, freeze a selection, then assess it without reranking."""
    import optuna

    if optuna.__version__ != TESTED_OPTUNA_VERSION:
        raise ValueError(f"Automatic research requires tested Optuna {TESTED_OPTUNA_VERSION}")
    recipe = compile_recipe(evidence)
    version = recipe["version"]
    policy = policy_for_version(version)
    proposals, max_simulations = policy["proposals"], policy["max_simulations"]
    stock = (
        evidence.get("automatic_stock_market_context")
        if version == JOINT_VERSION
        else evidence.get("automatic_stock_features")
    )
    if evidence.get("automatic_recipe") != recipe:
        raise ValueError("The saved automatic hypothesis recipe changed")
    if evidence["portfolio"].get("engine", "vectorbt") != "vectorbt":
        raise ValueError("Automatic stock hypotheses currently need VectorBT")
    gate_apply, gate_markers = apply, markers
    if version == JOINT_VERSION:
        from research.joint_stock_market import apply as gate_apply
        from research.joint_stock_market import catalog as joint_catalog
        from research.joint_stock_market import markers as gate_markers

        entries = joint_catalog()
    elif version == BENCHMARK_VERSION:
        from research.stock_relative_strength import apply as gate_apply
        from research.stock_relative_strength import catalog as relative_catalog
        from research.stock_relative_strength import markers as gate_markers

        entries = relative_catalog()
    elif version == EXPANDED_VERSION:
        from research.stock_indicators import apply as gate_apply
        from research.stock_indicators import catalog as indicator_catalog
        from research.stock_indicators import markers as gate_markers

        entries = indicator_catalog()
    else:
        entries = catalog()
    if (
        not stock
        or recipe.get("stock_evidence_id") != stock.get("id")
        or recipe.get("hypothesis_catalog") != entries
        or recipe["budget"]["max_simulations"] != max_simulations
        or recipe["budget"]["proposals"] != proposals
    ):
        raise ValueError("Automatic hypotheses need their exact saved stock evidence and catalog")
    binding = fingerprint({"recipe": recipe, "stock": stock})
    base, axes = _prepare(evidence["strategies"])
    baseline = _settings(base)
    baseline_id = settings_identity(baseline)
    none = next(row for row in entries if row["id"] == "none")
    by_hypothesis = {row["id"]: row for row in entries}
    screening = _screening(entries, axes, policy=policy)
    optimizer = {
        "id": "optuna",
        "version": optuna.__version__,
        "adapter_version": version,
        "sampler": "TPESampler",
        "seed": 0,
        "startup_trials": len(screening),
        "family_screening_trials": policy["family_screen"],
        "resume_policy": "serial-seeded-ask-tell-replay-v1",
        "objective_definition": "Net return after costs minus maximum drawdown, percentage points",
    }
    state = (
        deepcopy(saved)
        if saved
        else {
            "version": version,
            "binding": binding,
            "optimizer": optimizer,
            "simulations": 0,
            "attempts": 0,
            "evaluations": {},
            "trials": [],
            "candidates": {},
            "selection": None,
            "selected_report": None,
            "baseline_final_report": None,
            "final_report": None,
        }
    )
    if saved:
        checkpoint_rows(state)
        if state["binding"] != binding or state.get("optimizer") != optimizer:
            raise ValueError("Automatic hypothesis checkpoint inputs or evidence changed")
    capital = evidence["portfolio"]["capital"]
    exploratory = recipe.get("validation_mode") == "exploratory_only"
    windows = {}
    gate_cache = OrderedDict()

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
                "total": max_simulations,
                "stage": stage,
            },
        )

    def beat(done, total):
        cancelled()
        progress(state["simulations"] + min(1, done / max(1, total)), max_simulations)

    def window(period):
        if period not in windows:
            windows[period] = slice_period(evidence, period)
        return windows[period]

    def prepared(settings, hypothesis, period, stress):
        # Frozen prices/features are read-only. Copy mutable settings and let the
        # gate copy its signals instead of duplicating the whole feature bundle.
        current = dict(window(period))
        chosen = {row["id"]: row for row in settings}
        current["strategies"] = [
            {**row, **deepcopy(chosen[row["id"]]), "search": {}} for row in current["strategies"]
        ]
        if stress:
            for row in current["strategies"]:
                cost = row["config"]["cost_bps"]
                row["config"]["cost_bps"] = max(2 * cost, cost + 5)
        if hypothesis["id"] != "none":
            current["portfolio"] = {
                **current["portfolio"],
                "entry_conditions": {
                    "version": policy["condition_version"],
                    "hypothesis": hypothesis,
                },
            }
        current["strategies"], receipt = _gate(
            current,
            stock,
            hypothesis,
            gate_cache,
            period,
            apply_fn=gate_apply,
            markers_fn=gate_markers,
            calculation_version=policy["calculation_version"]
            if version in (EXPANDED_VERSION, BENCHMARK_VERSION, JOINT_VERSION)
            else None,
        )
        current["entry_condition_gates"] = [receipt]
        return current, receipt

    def simulate(settings, hypothesis, period, stress=False):
        control()
        if state["attempts"] >= max_simulations:
            raise ValueError("Automatic research reached its recorded calculation limit")
        current, receipt = prepared(settings, hypothesis, period, stress)
        # A lost response cannot silently grant another uncounted simulation.
        # Reservations may exceed completed calls after abrupt interruption.
        state["attempts"] += 1
        persist()
        report = evaluate(current["strategies"], current["snapshot"], capital, progress=beat)
        returned = report.get("strategies")
        expected = _settings(current["strategies"])
        if (
            not isinstance(returned, list)
            or _numerical_settings(returned) != _numerical_settings(expected)
            or any(
                "entry_condition" in actual
                and actual["entry_condition"] != wanted.get("entry_condition")
                for actual, wanted in zip(returned, expected, strict=True)
            )
        ):
            raise ValueError("The calculation returned different hypothesis or exit settings")
        _score(report["summary"])
        report["evaluation_basis"] = build_evaluation_basis(
            current, period="evaluation" if period == "final" else "selection"
        )
        report["automatic_stock_condition"] = {
            "hypothesis": deepcopy(hypothesis),
            "bundle_id": stock["id"],
            "gate_id": receipt["id"],
            "counts": deepcopy(receipt["counts"]),
        }
        report["automatic_recipe"] = deepcopy(recipe)
        if hypothesis["id"] != "none":
            enrich(report, current)
        state["simulations"] += 1
        return report

    def key_for(settings, hypothesis, period, stress=False):
        return fingerprint(
            {
                "settings": settings,
                "hypothesis": hypothesis,
                "period": period,
                "stress": stress,
            }
        )

    def measured(settings, hypothesis, period, stress=False, *, report_slot=None):
        key = key_for(settings, hypothesis, period, stress)
        if key not in state["evaluations"]:
            report = simulate(settings, hypothesis, period, stress)
            state["evaluations"][key] = {
                "summary": deepcopy(report["summary"]),
                "entry_dates": _entry_dates(report),
                "counts": deepcopy(report["automatic_stock_condition"]["counts"]),
                "automatic_stock_condition": deepcopy(report["automatic_stock_condition"]),
                "evaluation_basis": deepcopy(report["evaluation_basis"]),
            }
            if report_slot:
                state[report_slot] = report
            persist()
        return state["evaluations"][key]

    baseline_search = measured(baseline, none, "search")
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(
            seed=0, n_startup_trials=len(screening), constant_liar=False
        ),
    )
    for params in screening:
        study.enqueue_trial(params)
    if observe:
        observe(
            {
                "kind": "search_started",
                "proposal_budget": proposals,
                "replayed": len(state["trials"]),
                "restored_evaluations": len(state["candidates"]),
                "search_identity": binding,
                "search_identity_version": version,
            }
        )
    seen = set()
    # Asking and telling every old proposal restores the sampler RNG without
    # calling the calculation engine or making replay look like fresh activity.
    for index in range(proposals):
        control()
        trial = study.ask()
        hypothesis = by_hypothesis[trial.suggest_categorical("hypothesis", list(by_hypothesis))]
        params = {"hypothesis": hypothesis["id"]}
        for name, domain in axes.items():
            params[name] = (
                trial.suggest_int(
                    name, int(domain["min"]), int(domain["max"]), step=int(domain["step"])
                )
                if name.rsplit(".", 1)[-1] in ("hold_sessions", "hold_minutes")
                else trial.suggest_float(name, domain["min"], domain["max"], step=domain["step"])
            )
        settings = gate_markers(_settings(_candidate(base, params)), hypothesis, stock["id"])
        config_id = settings_identity(settings)
        record = {
            "number": index,
            "params": params,
            "config_id": config_id,
            "state": "complete",
            "reused": config_id in seen,
            "phase": "family_screening" if index < len(screening) else "joint_refinement",
        }
        if index < len(state["trials"]):
            old = state["trials"][index]
            row = state["candidates"].get(config_id)
            if (
                any(old.get(key) != value for key, value in record.items())
                or not row
                or row["strategies"] != settings
                or old.get("value") != row["score"]
                or row["score"] != _score(row["summary"])
            ):
                raise ValueError(
                    "Automatic hypothesis checkpoint does not replay the same proposals"
                )
            score = old["value"]
        else:
            if observe:
                observe({"kind": "proposal_started", **deepcopy(record)})
            if activity:
                activity(
                    {
                        "stage": "optimizing",
                        "trials": {
                            "total": proposals,
                            "completed": index,
                            "active_trial": index + 1,
                            "evaluated": len(state["candidates"]),
                            "reused": sum(t["reused"] for t in state["trials"]),
                            "rejected": 0,
                            "failed": 0,
                            "history": _display_history(state["trials"]),
                        },
                    }
                )
            tested = measured(settings, hypothesis, "search")
            score = _score(tested["summary"])
            state["candidates"].setdefault(
                config_id,
                {
                    "config_id": config_id,
                    "strategies": settings,
                    "hypothesis": hypothesis,
                    "condition_label": hypothesis["label"],
                    "trial_number": index,
                    "score": score,
                    "stage": record["phase"],
                    **deepcopy(tested),
                },
            )
            if observe:
                observe(
                    {
                        "kind": "proposal_finished",
                        "number": index,
                        "state": "complete",
                        "value": score,
                        "reused": record["reused"],
                    }
                )
            state["trials"].append({**record, "value": score})
            persist()
        study.tell(trial, score)
        seen.add(config_id)

    candidates = sorted(
        state["candidates"].values(), key=lambda row: (-row["score"], row["config_id"])
    )
    if activity:
        activity(
            {
                "stage": "validation",
                "trials": {
                    "total": proposals,
                    "completed": len(state["trials"]),
                    "active_trial": None,
                    "evaluated": len(state["candidates"]),
                    "reused": sum(t["reused"] for t in state["trials"]),
                    "rejected": 0,
                    "failed": 0,
                    "history": _display_history(state["trials"]),
                },
            }
        )
    if state["selection"] is None:
        checks = []
        finalists = [row for row in candidates if row["config_id"] != baseline_id][:MAX_FINALISTS]
        if exploratory:
            eligible = [
                row
                for row in candidates
                if not _support(
                    row["summary"], baseline_search["summary"], dates=row["entry_dates"]
                )
            ]
        else:
            references = {
                period: measured(baseline, none, period) for period in ("check1", "check2")
            }
            for row in finalists:
                settings, hypothesis = row["strategies"], row["hypothesis"]
                reasons, checked_windows = [], []
                for period in ("check1", "check2"):
                    tested = measured(settings, hypothesis, period)
                    reference = references[period]["summary"]
                    reasons.extend(
                        f"{period}: {reason}"
                        for reason in _support(
                            tested["summary"], reference, dates=tested["entry_dates"]
                        )
                    )
                    checked_windows.append(
                        {
                            "period": period,
                            **tested,
                            "baseline_summary": reference,
                            "score_delta": _score(tested["summary"]) - _score(reference),
                        }
                    )
                stress = stability = None
                if not reasons:
                    stressed_base = measured(baseline, none, "check2", True)
                    stress = measured(settings, hypothesis, "check2", True)
                    reasons.extend(
                        f"Higher costs: {reason}"
                        for reason in _support(
                            stress["summary"], stressed_base["summary"], dates=stress["entry_dates"]
                        )
                    )
                    stress = {
                        **stress,
                        "baseline_summary": stressed_base["summary"],
                        "score_delta": _score(stress["summary"]) - _score(stressed_base["summary"]),
                    }
                if not reasons:
                    neighborhood = neighbors(settings, recipe["ranges"])
                    tested_neighbors = []
                    for neighbor in neighborhood["neighbors"][:MAX_NEIGHBORS]:
                        neighbor_windows, failures = [], []
                        for period in ("check1", "check2"):
                            tested = measured(neighbor["strategies"], hypothesis, period)
                            reference = references[period]["summary"]
                            failures.extend(
                                f"{period}: {why}"
                                for why in _support(
                                    tested["summary"], reference, dates=tested["entry_dates"]
                                )
                            )
                            neighbor_windows.append(
                                {
                                    "period": period,
                                    **tested,
                                    "baseline_summary": reference,
                                    "score_delta": _score(tested["summary"]) - _score(reference),
                                }
                            )
                        tested_neighbors.append(
                            {
                                **{k: v for k, v in neighbor.items() if k != "strategies"},
                                "eligible": not failures,
                                "reasons": failures,
                                "windows": neighbor_windows,
                            }
                        )
                    count = len(tested_neighbors)
                    passed = sum(item["eligible"] for item in tested_neighbors)
                    fraction = passed / count if count else None
                    stable = bool(count and fraction >= 0.75)
                    stability = {
                        "status": "stable" if stable else "fragile" if count else "unavailable",
                        "tested": count,
                        "passed": passed,
                        "pass_fraction": fraction,
                        "available": neighborhood["available"],
                        "truncated": neighborhood["available"] > count,
                        "neighbors": tested_neighbors,
                    }
                    if not stable:
                        reasons.append(
                            "Neighbouring settings did not pass both development checks often enough"
                            if count
                            else "No adjacent settings are available to verify stability"
                        )
                checks.append(
                    {
                        "config_id": row["config_id"],
                        "trial_number": row["trial_number"],
                        "hypothesis": hypothesis,
                        "condition_label": row["condition_label"],
                        "eligible": not reasons,
                        "reasons": reasons,
                        "windows": checked_windows,
                        "stress": stress,
                        "stability": stability,
                        "selection_score": min(item["score_delta"] for item in checked_windows),
                    }
                )
            retained = sorted(
                (row for row in checks if row["eligible"]),
                key=lambda row: (-row["selection_score"], row["config_id"]),
            )
            eligible = (
                [next(row for row in finalists if row["config_id"] == retained[0]["config_id"])]
                if retained
                else []
            )
        chosen = (
            eligible[0]
            if eligible
            else {
                "config_id": baseline_id,
                "strategies": baseline,
                "hypothesis": none,
                "condition_label": none["label"],
                "trial_number": None,
                **deepcopy(baseline_search),
            }
        )
        state["selection"] = {**deepcopy(chosen), "is_baseline": not eligible, "checks": checks}
        persist("validation")
    selected = state["selection"]
    if state["selected_report"] is None:
        report = simulate(selected["strategies"], selected["hypothesis"], "search")
        if report["summary"] != selected["summary"]:
            raise ValueError(
                "The selected stock and exit settings did not reproduce their search result"
            )
        state["selected_report"] = report
        persist("validation")
    final_info = None
    status = "inconclusive"
    headline = "Best settings found; later validation needs more signal history"
    if not exploratory:
        if admit_final:
            admit_final(recipe)
        final_base = measured(baseline, none, "final", report_slot="baseline_final_report")
        if state["final_report"] is None:
            state["final_report"] = (
                deepcopy(state["baseline_final_report"])
                if selected["is_baseline"]
                else simulate(selected["strategies"], selected["hypothesis"], "final")
            )
            persist("validation")
        final = state["final_report"]
        reasons = _support(final["summary"], final_base["summary"], dates=_entry_dates(final))
        supports = not selected["is_baseline"] and not reasons
        status = "supported" if supports else "not_supported"
        if not supports and (
            any("Fewer than" in why for why in reasons)
            or any("Fewer than" in why for row in selected["checks"] for why in row["reasons"])
        ):
            status = "inconclusive"
        headline = {
            "supported": "An improvement held up in the later check",
            "not_supported": "No reliable improvement over unchanged settings",
            "inconclusive": "More completed trades across different dates are needed",
        }[status]
        final_info = {
            "summary": final["summary"],
            "baseline_summary": final_base["summary"],
            "score_delta": _score(final["summary"]) - _score(final_base["summary"]),
            "supports": supports,
            "reasons": reasons,
            "entry_dates": _entry_dates(final),
        }

    result = deepcopy(state["selected_report"])
    grid = len(entries) * prod(len(_values(domain)) for domain in axes.values())
    selection_basis = (
        "Best supported search-period score; no later period is available"
        if exploratory
        else "Best weakest-window improvement after two later checks, higher costs and neighboring exits; fixed before the final period"
    )
    result["experiment"] = {
        "kind": "portfolio_optimize",
        "experiment_version": version,
        "optimizer": optimizer,
        "specification": deepcopy(evidence["portfolio"]["optimization"]),
        "search_space": {
            "axes": {"hypothesis": {"type": "categorical", "choices": list(by_hypothesis)}, **axes},
            "grid_size": grid,
            "proposal_budget": proposals,
            "objective_definition": optimizer["objective_definition"],
        },
        "counts": {
            "grid": grid,
            "proposed": len(state["trials"]),
            "evaluated_this_pass": len(candidates),
            "evaluated_all_passes": len(candidates),
            "rejected_allocations": 0,
            "reused_trials": sum(row["reused"] for row in state["trials"]),
            "remaining": max(0, grid - len(candidates)),
        },
        "objective_winner_id": candidates[0]["config_id"],
        "recommendation_id": selected["config_id"],
        "rows": deepcopy(candidates),
        "pass_rows": deepcopy(candidates),
        "analysis_catalog": [],
        "trials": deepcopy(state["trials"]),
        "selected_strategies": deepcopy(result["strategies"]),
        "selected_reports": {selected["config_id"]: deepcopy(result)},
        "findings": [
            headline,
            "Every hypothesis family received exit proposals before joint refinement.",
        ],
        "next_action": "Inspect the saved checks before choosing a setup",
        "evaluation_basis_id": result["evaluation_basis"]["evidence_id"],
    }
    result["automatic_research"] = {
        "version": version,
        "status": status,
        "headline": headline,
        "recipe": recipe,
        "selected_config_id": selected["config_id"],
        "selected_is_baseline": selected["is_baseline"],
        "selected_condition": selected["condition_label"],
        "selection_basis": selection_basis,
        "baseline": baseline_search["summary"],
        "checks": deepcopy(selected["checks"]),
        "condition_candidates": deepcopy(candidates),
        "hypothesis_candidates": deepcopy(candidates),
        "final": final_info,
        "counts": {
            "proposals": len(state["trials"]),
            "simulations": state["simulations"],
            "attempts_reserved": state["attempts"],
            "finalists": len(selected["checks"]),
            "condition_candidates": len(candidates),
        },
        "coverage": {
            "catalog_size": len(entries),
            "joint_grid_size": grid,
            "unique_candidates": len(candidates),
            "exhaustive": len(candidates) == grid,
            "family_screening_proposals": len(screening),
            "families": [
                {
                    "family": family,
                    "screened": sum(
                        t["phase"] == "family_screening"
                        and by_hypothesis[t["params"]["hypothesis"]]["family"] == family
                        for t in state["trials"]
                    ),
                }
                for family in dict.fromkeys(row["family"] for row in entries)
            ],
            "neighbor_limit": MAX_NEIGHBORS,
            "search": "Bounded joint Optuna proposals; not exhaustive indicator discovery",
        },
        "unsupported_families": [
            "Arbitrary indicator or original-scanner discovery",
            "Independent market benchmark",
            "Live execution",
        ],
        "evidence_scope": "Exploratory only; no untouched later check"
        if exploratory
        else "Exploratory scanner research; prior viewing outside this application is unknown",
    }
    if not exploratory:
        periods = recipe["periods"]
        result["validation"] = {
            "label": "Final later-period check",
            "train_from": periods["search"]["from"],
            "train_to": periods["search"]["to"],
            "test_from": periods["final"]["from"],
            "test_to": periods["final"]["to"],
            "training_signals": len(window("search")["signals"]),
            "testing_signals": len(window("final")["signals"]),
            "shared_positions_across_periods": False,
            "selection_basis": selection_basis,
            "result": deepcopy(state["final_report"]),
        }
    result.update(portfolio=deepcopy(evidence["portfolio"]), config={"initial_capital": capital})
    result["source"] = {
        "interval": evidence["snapshot"]["provenance"]["interval"],
        "provider": "OpenAlgo Historify",
        "signal_count": len(window("search")["signals"]),
        "strategy_count": len(base),
    }
    from research.study_analysis import build_study_analysis

    result["experiment"]["study_analysis"] = build_study_analysis(
        result["experiment"], progress=beat
    )
    from research.analytics import add_price_charts

    for report, period in [
        (result, "search"),
        *([] if exploratory else [(result["validation"]["result"], "final")]),
    ]:
        if report.get("analysis"):
            report["analysis"] = add_price_charts(
                report["analysis"], report, window(period)["snapshot"]
            )
    return result
