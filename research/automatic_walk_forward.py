"""Versioned expanding-prefix Optuna fits over one native frozen price union.

Each fit has an independent seeded sampler. Only its training account scores are
told to it; its winner is frozen before the next-window account is calculated.
There is no downloaded data, model service or alternative accounting engine here.
"""

import hashlib
from collections import OrderedDict
from contextlib import nullcontext
from copy import deepcopy
from math import prod

from research.automatic_hypotheses import MAX_GATE_CACHE, _gate, _screening, policy_for_version
from research.automatic_protocol import (
    ALLOCATION_VERSION,
    CAPACITY_VERSION,
    EXECUTION_VERSION,
    METHOD_VERSION,
    PRIORITY_VERSION,
    SEED_VERSION,
    SENSITIVITY_VERSION,
    TURNOVER_VERSION,
    compile_recipe,
    slice_period,
)
from research.automatic_protocol import WALK_FORWARD_VERSION as VERSION
from research.automatic_research import _entry_dates, _score, _support
from research.automatic_research import _settings as numerical_settings
from research.automatic_timing import apply_choices as timing_choices
from research.automatic_timing import axes as timing_axes
from research.automatic_timing import probes
from research.automatic_trailing import apply_choices as trailing_choices
from research.automatic_trailing import axes as trailing_axes
from research.canonical_json import bounded_canonical_snapshot
from research.checkpoint_bytes import MAX_OWNED_BYTES, _OwnedCanonicalJSON, _OwnedCheckpointSink
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
from research.execution_features import _PreparedExecutionFeatures
from research.joint_stock_market import PreparedJointContext
from research.portfolio_execution_features import _PreparedPortfolioFeatures
from research.report_contract import fingerprint, settings_identity

FITS = ("fit1", "fit2", "fit3", "search")
SEED_FITS = (*FITS, "search_seed1", "search_seed2")


def fit_names(version):
    return SEED_FITS if version in (SEED_VERSION, ALLOCATION_VERSION) else FITS


def data_period(fit_name):
    return "search" if fit_name in SEED_FITS[4:] else fit_name


PROPOSALS_PER_FIT = 48
MAX_SIMULATIONS = 320
FINDINGS_VERSION = "research-walk-forward-findings-v1"


def _historical_result(result):
    """Label an explicitly historical programme without changing its accounts.

    Selection and every account have already been frozen. An observed advantage
    is retained as a historical fact, never promoted to independent validation.
    """
    findings = result["automatic_research"]
    findings["historical_checks_passed"] = findings["status"] == "supported"
    findings["status"] = "historical"
    findings["headline"] = "Historical optimisation — not independently validated"
    findings["independently_validated"] = False
    available = findings.get("recipe", {}).get("history_availability") is not None
    findings["evidence_scope"] = (
        "Historical research on supplied signals; later-date comparisons use this recorded dataset. "
        "No independent validation is claimed."
        if available
        else "Historical research on recorded scanner signals; dates may have been used before. "
        "New data is required for independent validation."
    )
    final = findings["final"]
    final["historical_checks_passed"] = final["supports"]
    final["supports"] = False
    final["independently_validated"] = False
    final["reasons"] = [*final["reasons"], "Historical comparison, not an independent final test"]
    result["validation"]["label"] = "Historical later-period comparison"
    result["validation"]["independently_validated"] = False
    result["experiment"]["findings"][0] = findings["headline"]
    result["experiment"]["next_action"] = (
        "Review and save the historical setup"
        if available
        else "Save the historical setup; use genuinely new data for independent validation"
    )
    return result


METHOD_PROBES = (
    "momentum_10_gt0__all",
    "momentum_10_gt2__all",
    "momentum_60_gt0__all",
    "momentum_60_gt2__all",
)


def prepare_gate(bundle):
    """Own one validated price projection for this calculation's lifetime."""
    return PreparedJointContext(bundle)


def prepare_method_gate(bundle):
    """The expanded recipe owns a separate versioned native gate capability."""
    from research.expanded_stock_market import PreparedJointContext as ExpandedContext

    return ExpandedContext(bundle)


def _context(version):
    if version == VERSION:
        from research import joint_stock_market as context
    elif version in (
        METHOD_VERSION,
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        from research import expanded_stock_market as context
    else:
        raise ValueError("Choose a supported walk-forward research version")
    return context


def _screening_program(version, entries, axes, timing, execution=None):
    """Preserve v8's sequence; explicitly screen every newly admitted v9 method."""
    if version in (
        METHOD_VERSION,
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        policy = policy_for_version(version)
        if (
            len(entries) != policy["max_hypotheses"]
            or len({row["id"] for row in entries}) != len(entries)
            or len({row["family"] for row in entries}) != policy["max_families"]
        ):
            raise ValueError("The expanded walk-forward catalogue exceeded its fixed bounds")
    # Existing family representatives stay identical even when a later catalogue
    # contains new choices in the same family. Numeric exits use one midpoint.
    screening = _screening(
        _context(VERSION).catalog()
        if version
        in (
            METHOD_VERSION,
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        else entries,
        axes,
        policy=policy_for_version(VERSION),
    )
    for params in screening:
        for name, domain in axes.items():
            choices = domain["choices"] if domain.get("type") == "categorical" else _values(domain)
            params[name] = (
                "original" if name.endswith(".timing_choice") else choices[len(choices) // 2]
            )
    clock_limit = 8
    if version in (
        METHOD_VERSION,
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        registered = {row["id"] for row in entries}
        if not set(METHOD_PROBES) <= registered:
            raise ValueError("The expanded walk-forward catalogue lost a targeted method")
        midpoint = screening[0]
        screening.extend({**midpoint, "hypothesis": identifier} for identifier in METHOD_PROBES)
        clock_limit = 4
    screening.extend(probes(axes, timing)[:clock_limit])
    if version in (
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        from research.execution_search import probes as execution_probes

        for params in screening:
            for name in axes:
                if name.endswith(".execution_choice"):
                    params[name] = "percent"
                elif name.endswith(".portfolio_choice"):
                    params[name] = "original"
        if version in (
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            for params in screening:
                params["portfolio.entry_priority"] = "source"
                if version == ALLOCATION_VERSION:
                    params["portfolio.allocation_mode"] = "original"
        screening.extend(execution_probes(screening[0], execution))
    if len(screening) > (
        50
        if version
        in (
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        else 48
        if version == EXECUTION_VERSION
        else 40
    ):
        raise ValueError("Walk-forward screening exceeded its reserved refinement budget")
    return screening


def _validate_state(saved, *, version=None):
    saved_version = saved.get("version") if isinstance(saved, dict) else None
    max_simulations = (
        512
        if saved_version in (SEED_VERSION, ALLOCATION_VERSION)
        else 384
        if saved_version
        in (
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        else MAX_SIMULATIONS
    )
    proposals_per_fit = (
        68
        if saved_version
        in (
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        else 64
        if saved_version == EXECUTION_VERSION
        else PROPOSALS_PER_FIT
    )
    if (
        not isinstance(saved, dict)
        or saved.get("version")
        not in (
            VERSION,
            METHOD_VERSION,
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        or (version is not None and saved.get("version") != version)
        or saved.get("checkpoint_id")
        != _fingerprint(
            {key: value for key, value in saved.items() if key != "checkpoint_id"},
            max_bytes=MAX_CHECKPOINT_BYTES,
        )
        or type(saved.get("attempts")) is not int
        or type(saved.get("simulations")) is not int
        or not 0 <= saved["simulations"] <= saved["attempts"] <= max_simulations
        or not isinstance(saved.get("evaluations"), dict)
        or len(saved["evaluations"]) > saved["simulations"]
        or not isinstance(saved.get("fits"), dict)
        or set(saved["fits"]) - set(fit_names(saved_version))
        or saved.get("active_fit") not in fit_names(saved_version)
        or not isinstance(saved.get("folds"), list)
        or len(saved["folds"]) > 3
    ):
        raise ValueError("The walk-forward checkpoint changed or exceeded its bounds")
    for name, fit in saved["fits"].items():
        if (
            not isinstance(fit, dict)
            or not isinstance(fit.get("trials"), list)
            or len(fit["trials"]) > proposals_per_fit
            or not isinstance(fit.get("candidates"), dict)
            or len(fit["candidates"]) > proposals_per_fit
        ):
            raise ValueError("The walk-forward fit history changed")
        for config_id, row in fit["candidates"].items():
            if (
                row.get("config_id") != config_id
                or settings_identity(row.get("strategies")) != config_id
                or row.get("score") != _score(row.get("summary", {}))
            ):
                raise ValueError(f"The {name} candidate evidence changed")


def checkpoint_rows(saved):
    """Read only the active prefix's candidates; never call a sampler or engine."""
    _validate_state(saved)
    fit = saved["fits"].get(saved["active_fit"], {"candidates": {}})
    return deepcopy(
        sorted(fit["candidates"].values(), key=lambda row: (-row["score"], row["config_id"]))
    )


def _fold_record(fold, winner, fit, reference, tested, stress_base, stressed):
    reasons = _support(tested["summary"], reference["summary"], dates=tested["entry_dates"])
    reasons.extend(
        "Higher costs: " + why
        for why in _support(
            stressed["summary"], stress_base["summary"], dates=stressed["entry_dates"]
        )
    )
    return {
        "id": fold["id"],
        "fit": deepcopy(fold["fit"]),
        "test": deepcopy(fold["test"]),
        "selected_config_id": winner["config_id"],
        "selected_settings": deepcopy(winner["strategies"]),
        "selected_condition": winner["condition_label"],
        "baseline_summary": reference["summary"],
        "summary": tested["summary"],
        "stress_baseline_summary": stress_base["summary"],
        "stress_summary": stressed["summary"],
        "eligible": not reasons,
        "reasons": reasons,
        "training": {"proposals": len(fit["trials"]), "unique_candidates": len(fit["candidates"])},
    }


def sensitivity_window(evidence, name):
    """Replay one frozen mask using the ordinary complete-horizon period purge."""
    recipe = evidence.get("automatic_recipe", {})
    scenario = next(
        (
            row
            for row in recipe.get("recommendation_sensitivity", {}).get("scenarios", [])
            if row["name"] == name
        ),
        None,
    )
    if (
        recipe.get("version") not in (SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION)
        or scenario is None
        or scenario["status"] != "available"
    ):
        raise ValueError("Choose an available recorded sensitivity challenge")
    current = slice_period(evidence, name)
    selected_symbols = set(scenario["symbols"])
    current["strategies"] = [
        {
            **row,
            "signals": [
                signal for signal in row["signals"] if signal["symbol"] in selected_symbols
            ],
        }
        for row in current["strategies"]
    ]
    current["signals"] = [
        dict(signal, strategy_id=row["id"])
        for row in current["strategies"]
        for signal in row["signals"]
    ]
    return current


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
    defer_study_analysis=False,
    retain_evaluation=None,
    read_evaluation=None,
    calibration_seed_plan=None,
    reserve_attempt=None,
):
    cancelled()
    if boundary:
        boundary()
    version = evidence.get("portfolio", {}).get("automatic_research", {}).get("version")
    context = _context(version)
    prepare = (
        prepare_method_gate
        if version
        in (
            METHOD_VERSION,
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        else prepare_gate
    )
    with (
        prepare(evidence.get("automatic_stock_market_context")) as prepared,
        (
            _PreparedPortfolioFeatures(evidence.get("portfolio_execution_features"))
            if version in (CAPACITY_VERSION, SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION)
            else nullcontext()
        ) as prepared_portfolio,
        (
            _PreparedExecutionFeatures(evidence.get("execution_features"))
            if version
            in (
                EXECUTION_VERSION,
                PRIORITY_VERSION,
                TURNOVER_VERSION,
                CAPACITY_VERSION,
                SENSITIVITY_VERSION,
                SEED_VERSION,
                ALLOCATION_VERSION,
            )
            else nullcontext()
        ) as prepared_execution,
    ):
        return _run(
            evidence,
            prepared_gate=prepared,
            prepared_portfolio=prepared_portfolio,
            prepared_execution=prepared_execution,
            version=version,
            context=context,
            evaluate=evaluate,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
            activity=activity,
            observe=observe,
            boundary=boundary,
            admit_final=admit_final,
            defer_study_analysis=defer_study_analysis,
            retain_evaluation=retain_evaluation,
            read_evaluation=read_evaluation,
            calibration_seed_plan=calibration_seed_plan,
            reserve_attempt=reserve_attempt,
        )


def _run(
    evidence,
    *,
    prepared_gate,
    prepared_portfolio,
    prepared_execution,
    version,
    context,
    evaluate,
    saved,
    checkpoint,
    progress,
    cancelled,
    activity,
    observe,
    boundary,
    admit_final,
    defer_study_analysis,
    retain_evaluation,
    read_evaluation,
    calibration_seed_plan=None,
    reserve_attempt=None,
):
    import optuna

    if optuna.__version__ != TESTED_OPTUNA_VERSION:
        raise ValueError(f"Walk-forward research requires tested Optuna {TESTED_OPTUNA_VERSION}")
    recipe = compile_recipe(evidence)
    if recipe["version"] != version or evidence.get("automatic_recipe") != recipe:
        raise ValueError("The frozen walk-forward recipe changed")
    stock = evidence.get("automatic_stock_market_context")
    if (
        not stock
        or stock["id"] != recipe["stock_evidence_id"]
        or recipe["hypothesis_catalog"] != context.catalog()
    ):
        raise ValueError("Walk-forward research needs its exact saved stock and market context")
    binding_inputs = {"recipe": recipe, "stock": stock}
    if calibration_seed_plan is not None:
        from research.procedure_seed_calibration import verify_seed_plan

        if version not in (SEED_VERSION, ALLOCATION_VERSION):
            raise ValueError(
                "Offline sampler qualification requires a declared supported procedure"
            )
        calibration_seed_plan = verify_seed_plan(calibration_seed_plan)
        declared_version = calibration_seed_plan.get("recipe_version", SEED_VERSION)
        if declared_version != version:
            raise ValueError("Offline sampler programme belongs to a different recipe")
        binding_inputs["offline_calibration_seeds"] = calibration_seed_plan
    binding = fingerprint(binding_inputs)
    policy = policy_for_version(version)
    proposals_per_fit = policy["proposals_per_fit"]
    max_simulations = policy["max_simulations"]
    proposal_budget = policy["proposals"]
    if version in (
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        # Worst case: baseline + every unique proposal + one control per fit,
        # four later-window accounts per fold, one selected-report rebuild,
        # final pair, and 64 lost attempts.
        upper = 4 * (proposals_per_fit + 2) + 3 * 4 + 1 + 2 + 64
        if version in (
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            upper += 6
        if version in (SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            upper += recipe["budget"]["max_sensitivity_accounts"]
        if version in (SEED_VERSION, ALLOCATION_VERSION):
            upper += recipe["budget"]["max_seed_accounts"]
        if upper > max_simulations or proposals_per_fit - 50 < 16:
            raise ValueError("Priority research exceeded its accounting or refinement reserve")
    base, numeric_axes = _prepare(evidence["strategies"])
    axes = {
        **numeric_axes,
        **trailing_axes(recipe["trailing_research"]),
        **timing_axes(recipe["timing_research"]),
    }
    if version in (
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        from research.execution_search import axes as execution_axes

        axes.update(execution_axes(recipe["execution_research"]))
    baseline = _settings(base)
    baseline_id = settings_identity(baseline)
    entries = context.catalog()
    by_id = {row["id"]: row for row in entries}
    none = by_id["none"]
    screening = _screening_program(
        version, entries, axes, recipe["timing_research"], recipe.get("execution_research")
    )
    optimizer = {
        "id": "optuna",
        "version": optuna.__version__,
        "adapter_version": version,
        "sampler": "TPESampler",
        "seed": 0,
        "startup_trials": len(screening),
        "resume_policy": "serial-seeded-ask-tell-replay-v1",
        "objective_definition": "Net return after costs minus maximum drawdown, percentage points",
    }
    if version in (SEED_VERSION, ALLOCATION_VERSION):
        optimizer["search_repeat_seeds"] = deepcopy(recipe["search_seeds"]["seeds"])
    if calibration_seed_plan is not None:
        actual_seeds = calibration_seed_plan["fit_sampler_seeds"]
        optimizer.update(
            seed=actual_seeds["search"],
            search_repeat_seeds=[actual_seeds[name] for name in ("search", *SEED_FITS[4:])],
            offline_calibration_seeds=deepcopy(calibration_seed_plan),
        )
    state = (
        deepcopy(saved)
        if saved
        else {
            "version": version,
            "binding": binding,
            "optimizer": optimizer,
            "attempts": 0,
            "simulations": 0,
            "evaluations": {},
            "fits": {},
            "active_fit": "fit1",
            "folds": [],
            "selection": None,
            "selected_report": None,
            "baseline_final_report": None,
            "final_report": None,
        }
    )
    if saved:
        _validate_state(state, version=version)
        if state["binding"] != binding or state["optimizer"] != optimizer:
            raise ValueError("The walk-forward checkpoint belongs to different frozen inputs")
    windows, gates = OrderedDict(), OrderedDict()
    opportunity_bases = {}
    capital = evidence["portfolio"]["capital"]

    if reserve_attempt is not None and boundary is not None:
        # The native worker requests a flush before acknowledging a pause.
        # Legacy/direct callers retain their original checkpoint cadence.
        boundary.checkpoint_pause = True

    def control():
        cancelled()
        if boundary:
            flush_requested = boundary()
            if reserve_attempt is not None and flush_requested is True:
                persist()

    def persist(*, proposal=False):
        if proposal and reserve_attempt is not None:
            completed_proposals = sum(len(fit["trials"]) for fit in state["fits"].values())
            if completed_proposals % 16:
                return
        state.pop("checkpoint_id", None)
        captured = bounded_canonical_snapshot(
            state, max_bytes=MAX_CHECKPOINT_BYTES, allow_native_float64=True
        )
        if captured is None:
            state["checkpoint_id"] = _fingerprint(state, max_bytes=MAX_CHECKPOINT_BYTES)
            snapshot = deepcopy(state)
        else:
            snapshot, encoded = captured
            del captured
            state["checkpoint_id"] = hashlib.sha256(encoded).hexdigest()
            if type(checkpoint) is _OwnedCheckpointSink and len(encoded) <= MAX_OWNED_BYTES:
                owned = _OwnedCanonicalJSON(snapshot, encoded)
                del encoded
                try:
                    complete = owned.with_checkpoint_id(state["checkpoint_id"])
                    if complete is not None:
                        checkpoint.publish_owned(
                            complete,
                            {
                                "completed": state["simulations"],
                                "total": max_simulations,
                                "stage": "optimization",
                            },
                        )
                        return
                finally:
                    owned.close()
            else:
                del encoded
            # The callback receives the same detached tree that was hashed.
            # Generic callbacks retain their mutable-tree contract and receive
            # no canonical byte capability that could become stale on mutation.
            snapshot["checkpoint_id"] = state["checkpoint_id"]
        checkpoint(
            snapshot,
            {"completed": state["simulations"], "total": max_simulations, "stage": "optimization"},
        )

    def window(period):
        if period not in windows:
            windows[period] = (
                sensitivity_window(evidence, period)
                if version in (SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION)
                and period
                in {row["name"] for row in recipe["recommendation_sensitivity"]["scenarios"]}
                else slice_period(evidence, period)
            )
            if len(windows) > 2:
                evicted, _ = windows.popitem(last=False)
                opportunity_bases.pop(evicted, None)
        windows.move_to_end(period)
        return windows[period]

    def key_for(settings, hypothesis, period, stress):
        return fingerprint(
            {"settings": settings, "hypothesis": hypothesis, "period": period, "stress": stress}
        )

    if saved:
        try:
            for index, receipt in enumerate(state["folds"]):
                fold = recipe["walk_forward"]["folds"][index]
                fit_state = state["fits"][fold["fit_period"]]
                winner = fit_state["selection"]
                period = fold["test_period"]
                references = [
                    state["evaluations"][key_for(settings, hypothesis, period, stress)]
                    for settings, hypothesis, stress in (
                        (baseline, none, False),
                        (winner["strategies"], winner["hypothesis"], False),
                        (baseline, none, True),
                        (winner["strategies"], winner["hypothesis"], True),
                    )
                ]
                if receipt != _fold_record(fold, winner, fit_state, *references):
                    raise ValueError("A completed walk-forward fold changed its recorded evidence")
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError("The completed walk-forward fold history is incomplete") from error

    def simulate(settings, hypothesis, period, stress=False, *, report_analysis=True):
        control()
        reserve = (
            2
            if period != "final"
            and recipe.get("history_availability", {}).get("mode") != "exploratory_only"
            else 0
        )
        if (
            state["attempts"] >= max_simulations - reserve
            or state["attempts"] - state["simulations"] >= 64
        ):
            raise ValueError(
                "Walk-forward research reached its recorded calculation limit; saved progress is retained"
            )
        current = dict(window(period))
        chosen = {row["id"]: row for row in settings}
        current["strategies"] = [
            {**row, **deepcopy(chosen[row["id"]]), "search": {}} for row in current["strategies"]
        ]
        if stress:
            for row in current["strategies"]:
                cost = row["config"]["cost_bps"]
                row["config"]["cost_bps"] = max(2 * cost, cost + 5)
        if version in (
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            current = prepared_execution.attach(current)
        if version in (CAPACITY_VERSION, SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            current = prepared_portfolio.attach(current)
        if hypothesis["id"] != "none":
            current["portfolio"] = {
                **current["portfolio"],
                "entry_conditions": {
                    "version": policy_for_version(version)["condition_version"],
                    "hypothesis": hypothesis,
                },
            }
        current["strategies"], receipt = _gate(
            current,
            stock,
            hypothesis,
            gates,
            period,
            apply_fn=prepared_gate.apply,
            markers_fn=context.markers,
            calculation_version=policy_for_version(version)["calculation_version"],
            trailing_activation=True,
        )
        current["entry_condition_gates"] = [receipt]
        if reserve_attempt is None:
            state["attempts"] += 1
            persist()
        else:
            # Reserve every actual calculation durably before the engine runs.
            # Full evidence checkpoints can then group completed proposals.
            state["attempts"] = reserve_attempt(
                state["attempts"], state["simulations"], reserve, max_simulations
            )
        from research.connectors.vectorbt_portfolio import discard_trial_analysis

        # Development accounts retain their full scientific evidence below.
        # Only unused display charts/native tear sheets are omitted; chosen
        # and final reports still receive the original complete analysis.
        with nullcontext() if report_analysis else discard_trial_analysis():
            report = evaluate(
                current["strategies"],
                current["snapshot"],
                capital,
                progress=lambda done, total: (
                    # Keep the live cancellation/lease read on every engine tick.
                    # Pause acknowledgement belongs to completed-calculation
                    # boundaries, not an extra writer transaction for every bar.
                    cancelled(),
                    progress(state["simulations"] + min(1, done / max(1, total)), max_simulations),
                ),
            )
        returned = report.get("strategies")
        expected = _settings(current["strategies"])
        if (
            not isinstance(returned, list)
            or numerical_settings(returned) != numerical_settings(expected)
            or any(
                "entry_condition" in actual
                and actual["entry_condition"] != wanted.get("entry_condition")
                for actual, wanted in zip(returned, expected, strict=True)
            )
        ):
            raise ValueError("The native calculation returned different walk-forward settings")
        _score(report["summary"])
        report["evaluation_basis"] = build_evaluation_basis(
            current, period="evaluation" if period == "final" else "selection"
        )
        if (
            version
            in (
                TURNOVER_VERSION,
                CAPACITY_VERSION,
                SENSITIVITY_VERSION,
                SEED_VERSION,
                ALLOCATION_VERSION,
            )
            and period != "final"
        ):
            # This describes the unchanged original opportunity set, not the
            # evaluated settings/gates above. Keep it only while its owned
            # frozen period window is resident, and detach every report copy.
            original_window = window(period)
            if period not in opportunity_bases:
                opportunity_bases[period] = build_evaluation_basis(
                    original_window, period="selection"
                )
            report["research_opportunity_basis"] = deepcopy(opportunity_bases[period])
        report["automatic_stock_condition"] = {
            "hypothesis": deepcopy(hypothesis),
            "bundle_id": stock["id"],
            "gate_id": receipt["id"],
            "counts": deepcopy(receipt["counts"]),
        }
        # Discarded native trial reports retain their scientific account fields;
        # the full recipe already belongs to the frozen run and is not retained
        # in development shards or evaluation rows. Public callbacks keep their
        # complete report contract, as do every selected and final report.
        if report_analysis or type(checkpoint) is not _OwnedCheckpointSink:
            report["automatic_recipe"] = deepcopy(recipe)
        if hypothesis["id"] != "none":
            enrich(report, current)
        if version in (CAPACITY_VERSION, SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            report["portfolio_constraint_evidence"] = prepared_portfolio.assess(
                report, current["portfolio_execution_features"]
            )
            if report["portfolio_constraint_evidence"]["status"] == "invalid":
                raise ValueError(
                    "Native portfolio limits did not reconcile with frozen evidence: "
                    + "; ".join(report["portfolio_constraint_evidence"]["reasons"])
                )
        if version in (SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            scenario = next(
                (
                    row
                    for row in recipe["recommendation_sensitivity"]["scenarios"]
                    if row["name"] == period
                ),
                None,
            )
            if scenario is not None:
                facts = {
                    "version": "research-sensitivity-account-evidence-v1",
                    "plan_id": recipe["recommendation_sensitivity"]["id"],
                    "scenario_id": scenario["id"],
                    "period": period,
                    "basis_id": fingerprint(report["evaluation_basis"]),
                    "support": {
                        "closed_trades": report["summary"]["closed_trades"],
                        "entry_dates": _entry_dates(report),
                    },
                    "constraints": deepcopy(report["portfolio_constraint_evidence"]),
                }
                report["sensitivity_evidence"] = {**facts, "id": fingerprint(facts)}
        if version in (SEED_VERSION, ALLOCATION_VERSION) and period == "search":
            from research.recommendation_seeds import account_facts

            report["search_seed_evidence"] = account_facts(report, recipe["search_seeds"])
        state["simulations"] += 1
        return report

    def measured(
        settings,
        hypothesis,
        period,
        stress=False,
        *,
        report_slot=None,
        reconstruct=False,
        defer_checkpoint=False,
    ):
        key = key_for(settings, hypothesis, period, stress)
        if key not in state["evaluations"] or reconstruct:
            report = simulate(
                settings, hypothesis, period, stress, report_analysis=report_slot is not None
            )
            descriptor = None
            if retain_evaluation is not None and period != "final":
                snapshot = window(period)["snapshot"]
                descriptor = retain_evaluation(
                    report,
                    evaluation_id=key,
                    search_identity=binding,
                    settings=_settings(report["strategies"]),
                    hypothesis=hypothesis,
                    period=period,
                    stress=stress,
                    expected_timeline=snapshot[
                        "timeline" if snapshot["provenance"]["interval"] == "1m" else "sessions"
                    ],
                )
            row = {
                "summary": deepcopy(report["summary"]),
                "entry_dates": _entry_dates(report),
                "counts": deepcopy(report["automatic_stock_condition"]["counts"]),
                "automatic_stock_condition": deepcopy(report["automatic_stock_condition"]),
                "evaluation_basis": deepcopy(report["evaluation_basis"]),
                **(
                    {"search_seed_evidence": deepcopy(report["search_seed_evidence"])}
                    if "search_seed_evidence" in report
                    else {}
                ),
                **(
                    {"sensitivity_evidence": deepcopy(report["sensitivity_evidence"])}
                    if "sensitivity_evidence" in report
                    else {}
                ),
                **({"development_evidence": descriptor} if descriptor is not None else {}),
                **(
                    {
                        "portfolio_constraint_evidence": deepcopy(
                            report["portfolio_constraint_evidence"]
                        )
                    }
                    if version
                    in (CAPACITY_VERSION, SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION)
                    else {}
                ),
            }
            if key in state["evaluations"] and state["evaluations"][key] != row:
                raise ValueError(
                    "The selected walk-forward account did not reproduce its recorded evidence"
                )
            state["evaluations"][key] = row
            if report_slot:
                state[report_slot] = report
            if not defer_checkpoint:
                persist()
        return {
            key: value
            for key, value in state["evaluations"][
                key_for(settings, hypothesis, period, stress)
            ].items()
            if key != "development_evidence"
        }

    if observe:
        observe(
            {
                "kind": "search_started",
                "proposal_budget": proposal_budget,
                "replayed": sum(len(fit["trials"]) for fit in state["fits"].values()),
                "restored_evaluations": sum(
                    len(fit["candidates"]) for fit in state["fits"].values()
                ),
                "search_identity": binding,
                "search_identity_version": version,
            }
        )

    def fit(fit_name):
        control()
        period = data_period(fit_name)
        seed = SEED_FITS.index(fit_name) - 3 if fit_name in SEED_FITS[4:] else 0
        if calibration_seed_plan is not None:
            seed = calibration_seed_plan["fit_sampler_seeds"][fit_name]
        state["active_fit"] = fit_name
        current = state["fits"].setdefault(
            fit_name, {"trials": [], "candidates": {}, "selection": None}
        )
        reference = measured(baseline, none, period)
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(
                seed=seed, n_startup_trials=len(screening), constant_liar=False
            ),
        )
        for params in screening:
            study.enqueue_trial(params)
        seen = set()
        for index in range(proposals_per_fit):
            control()
            trial = study.ask()
            hypothesis = by_id[trial.suggest_categorical("hypothesis", list(by_id))]
            params = {"hypothesis": hypothesis["id"]}
            for name, domain in axes.items():
                params[name] = (
                    trial.suggest_categorical(name, domain["choices"])
                    if domain.get("type") == "categorical"
                    else trial.suggest_int(
                        name, int(domain["min"]), int(domain["max"]), step=int(domain["step"])
                    )
                    if name.rsplit(".", 1)[-1] in ("hold_sessions", "hold_minutes")
                    else trial.suggest_float(
                        name, domain["min"], domain["max"], step=domain["step"]
                    )
                )
            proposed = _candidate(base, params)
            proposed = trailing_choices(proposed, params, recipe["trailing_research"])
            proposed = timing_choices(proposed, params, recipe["timing_research"])
            if version in (
                EXECUTION_VERSION,
                PRIORITY_VERSION,
                TURNOVER_VERSION,
                CAPACITY_VERSION,
                SENSITIVITY_VERSION,
                SEED_VERSION,
                ALLOCATION_VERSION,
            ):
                from research.execution_search import apply_choices

                proposed = apply_choices(proposed, params, recipe["execution_research"])
            settings = context.markers(_settings(proposed), hypothesis, stock["id"])
            config_id = settings_identity(settings)
            record = {
                "number": index,
                "params": params,
                "config_id": config_id,
                "state": "complete",
                "reused": config_id in seen,
                "phase": "family_screening" if index < len(screening) else "joint_refinement",
            }
            if index < len(current["trials"]):
                old = current["trials"][index]
                row = current["candidates"].get(config_id)
                if (
                    not row
                    or any(old.get(key) != value for key, value in record.items())
                    or old.get("value") != row["score"]
                    or row["strategies"] != settings
                ):
                    raise ValueError(
                        "The saved walk-forward fit did not replay its seeded proposals"
                    )
                score = old["value"]
            else:
                if activity:
                    completed = sum(len(item["trials"]) for item in state["fits"].values())
                    activity(
                        {
                            "stage": "optimizing",
                            "calculations": {
                                "completed": state["simulations"],
                                "limit": max_simulations,
                            },
                            "trials": {
                                "total": proposal_budget,
                                "completed": completed,
                                "active_trial": completed + 1,
                                "evaluated": sum(
                                    len(item["candidates"]) for item in state["fits"].values()
                                ),
                                "reused": sum(
                                    row["reused"]
                                    for item in state["fits"].values()
                                    for row in item["trials"]
                                ),
                                "rejected": 0,
                                "failed": 0,
                                "history": [],
                            },
                        }
                    )
                if observe:
                    observe(
                        {
                            "kind": "proposal_started",
                            **record,
                            "number": fit_names(version).index(fit_name) * proposals_per_fit
                            + index,
                            "fit_period": fit_name,
                        }
                    )
                tested = measured(settings, hypothesis, period, defer_checkpoint=True)
                had_candidate = config_id in current["candidates"]
                trial_count = len(current["trials"])
                try:
                    score = _score(tested["summary"])
                    current["candidates"].setdefault(
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
                    current["trials"].append({**record, "value": score})
                except BaseException:
                    # Retain the completed account if building its proposal
                    # record fails, just as the earlier account-only checkpoint
                    # did. An incomplete proposal must not appear on recovery.
                    if not had_candidate:
                        current["candidates"].pop(config_id, None)
                    del current["trials"][trial_count:]
                    persist()
                    raise
                # All scientific checks and the durable attempt reservation
                # precede this observation. Native workers group at most sixteen
                # completed proposals; pause/check/selection boundaries flush.
                persist(proposal=True)
                if observe:
                    observe(
                        {
                            "kind": "proposal_finished",
                            "number": fit_names(version).index(fit_name) * proposals_per_fit
                            + index,
                            "state": "complete",
                            "value": score,
                            "reused": record["reused"],
                            "fit_period": fit_name,
                        }
                    )
            study.tell(trial, score)
            seen.add(config_id)
        rows = sorted(
            current["candidates"].values(), key=lambda row: (-row["score"], row["config_id"])
        )
        winner = next(
            (
                row
                for row in rows
                if not _support(row["summary"], reference["summary"], dates=row["entry_dates"])
            ),
            None,
        )
        if winner is None:
            winner = {
                "config_id": baseline_id,
                "strategies": baseline,
                "hypothesis": none,
                "condition_label": none["label"],
                "trial_number": None,
                "score": _score(reference["summary"]),
                **reference,
            }
        if current["selection"] is not None and current["selection"] != winner:
            raise ValueError("The walk-forward training selection changed on resume")
        if current["selection"] is None:
            current["selection"] = deepcopy(winner)
            # A new choice must be durable before requesting the next-window
            # account. On recovery the exact saved choice was verified above;
            # replaying it has produced no new scientific evidence to publish.
            persist()
        if fit_name not in SEED_FITS[4:] and version in (
            EXECUTION_VERSION,
            PRIORITY_VERSION,
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        ):
            from research.execution_search import original_sizing

            control_settings = original_sizing(winner["strategies"], recipe["execution_research"])
            control_account = measured(
                control_settings,
                winner["hypothesis"],
                period,
                defer_checkpoint=reserve_attempt is not None,
            )
            receipt = {
                "version": "research-sizing-control-v1",
                "role": "matched_original_sizing_development",
                "period": period,
                "selected_config_id": winner["config_id"],
                "control_config_id": settings_identity(control_settings),
                "strategies": control_settings,
                "selected_summary": deepcopy(winner["summary"]),
                "summary": deepcopy(control_account["summary"]),
                "score_delta": _score(winner["summary"]) - _score(control_account["summary"]),
                "sizing_changed": control_settings != winner["strategies"],
                "policy": "Same selected filters, exits and hard caps; restore original allocation, order size and allocation sizing. Development diagnostic only; does not use final outcomes or establish sizing alpha.",
            }
            if current.get("sizing_control") is not None and current["sizing_control"] != receipt:
                raise ValueError("The matched original-sizing development control changed")
            if current.get("sizing_control") is None:
                current["sizing_control"] = receipt
                persist()
        return winner, rows

    if recipe.get("history_availability", {}).get("mode") == "exploratory_only":
        from research.available_history_research import run as available_history_run

        return available_history_run(
            evidence,
            recipe=recipe,
            state=state,
            optimizer=optimizer,
            baseline=baseline,
            baseline_id=baseline_id,
            none=none,
            axes=axes,
            entries=entries,
            fit_names=fit_names(version),
            fit=fit,
            measured=measured,
            key_for=key_for,
            persist=persist,
            control=control,
            window=window,
            prepared_portfolio=prepared_portfolio,
            defer_study_analysis=defer_study_analysis,
        )

    for fold in recipe["walk_forward"]["folds"]:
        winner, _ = fit(fold["fit_period"])
        if any(row["id"] == fold["id"] for row in state["folds"]):
            continue
        if activity:
            activity(
                {
                    "stage": "validation",
                    "calculations": {"completed": state["simulations"], "limit": max_simulations},
                }
            )
        period = fold["test_period"]
        reference = measured(baseline, none, period, defer_checkpoint=reserve_attempt is not None)
        tested = measured(
            winner["strategies"],
            winner["hypothesis"],
            period,
            defer_checkpoint=reserve_attempt is not None,
        )
        stress_base = measured(
            baseline, none, period, True, defer_checkpoint=reserve_attempt is not None
        )
        stressed = measured(
            winner["strategies"],
            winner["hypothesis"],
            period,
            True,
            defer_checkpoint=reserve_attempt is not None,
        )
        state["folds"].append(
            _fold_record(
                fold,
                winner,
                state["fits"][fold["fit_period"]],
                reference,
                tested,
                stress_base,
                stressed,
            )
        )
        persist()

    winner, rows = fit("search")
    process_supported = all(row["eligible"] for row in state["folds"])
    folds_supported = process_supported
    if version in (
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        from research.recommendation_evidence import assess
        from research.recommendation_plan import challenges

        diagnostic = {
            "version": "research-recommendation-evidence-v1",
            "status": "not_supported",
            "reasons": ["No non-baseline development choice passed all chronological checks"],
        }
        if process_supported and winner["config_id"] != baseline_id:

            def history(settings, hypothesis, period, stress=False):
                measured(settings, hypothesis, period, stress)
                return (
                    read_evaluation(key_for(settings, hypothesis, period, stress))
                    if read_evaluation is not None
                    else None
                )

            plan = challenges(winner["strategies"], recipe["ranges"])
            fold_histories = []
            for fold in recipe["walk_forward"]["folds"]:
                choice = state["fits"][fold["fit_period"]]["selection"]
                period = fold["test_period"]
                fold_histories.append(
                    {
                        "id": fold["id"],
                        "candidate": history(choice["strategies"], choice["hypothesis"], period),
                        "baseline": history(baseline, none, period),
                        "stress_candidate": history(
                            choice["strategies"], choice["hypothesis"], period, True
                        ),
                        "stress_baseline": history(baseline, none, period, True),
                    }
                )
            diagnostic = assess(
                candidate=history(winner["strategies"], winner["hypothesis"], "search"),
                baseline=history(baseline, none, "search"),
                folds=fold_histories,
                stress_candidate=history(
                    winner["strategies"], winner["hypothesis"], "search", True
                ),
                stress_baseline=history(baseline, none, "search", True),
                sensitivity=[
                    history(row["strategies"], winner["hypothesis"], "search")
                    for row in plan["neighbors"]
                ],
            )
            diagnostic["challenge_plan"] = {
                **{key: value for key, value in plan.items() if key != "neighbors"},
                "neighbors": [
                    {key: value for key, value in row.items() if key != "strategies"}
                    for row in plan["neighbors"]
                ],
            }
        if version in (CAPACITY_VERSION, SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            audits = []
            for settings, hypothesis, period, stress in (
                (baseline, none, "search", False),
                (winner["strategies"], winner["hypothesis"], "search", False),
                (baseline, none, "search", True),
                (winner["strategies"], winner["hypothesis"], "search", True),
            ):
                audits.append(
                    measured(settings, hypothesis, period, stress).get(
                        "portfolio_constraint_evidence"
                    )
                )
            for fold in recipe["walk_forward"]["folds"]:
                choice = state["fits"][fold["fit_period"]]["selection"]
                for settings, hypothesis in (
                    (baseline, none),
                    (choice["strategies"], choice["hypothesis"]),
                ):
                    for stress in (False, True):
                        audits.append(
                            measured(settings, hypothesis, fold["test_period"], stress).get(
                                "portfolio_constraint_evidence"
                            )
                        )
            complete = all(audit and audit.get("status") == "supported" for audit in audits)
            diagnostic["portfolio_constraints"] = {
                "version": "research-portfolio-recommendation-constraints-v1",
                "status": "supported" if complete else "incomplete",
                "development_accounts": len(audits),
                "evidence_ids": [audit.get("id") if audit else None for audit in audits],
                "policy": "Every development reference, chosen account and higher-cost comparison must reconcile its frozen volume and correlation limits. Missing inputs never support a recommendation; final outcomes are excluded.",
            }
            if not complete:
                diagnostic["status"] = "not_supported"
                diagnostic["qualified"] = False
                diagnostic["reasons"] = [
                    *diagnostic.get("reasons", []),
                    "Portfolio constraint evidence is incomplete in a development comparison",
                ]
        if version in (SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            from research.recommendation_sensitivity import assess as assess_sensitivity

            sensitivity_plan = recipe["recommendation_sensitivity"]
            pairs = None
            if (
                diagnostic["status"] == "supported"
                and winner["config_id"] != baseline_id
                and sensitivity_plan["status"] == "available"
            ):
                pairs = []
                for scenario in sensitivity_plan["scenarios"]:
                    control()
                    period = scenario["name"]
                    pair = {"scenario": period}
                    for role, settings, hypothesis in (
                        ("candidate", winner["strategies"], winner["hypothesis"]),
                        ("baseline", baseline, none),
                    ):
                        row = measured(settings, hypothesis, period)
                        pair[role] = (
                            read_evaluation(key_for(settings, hypothesis, period, False))
                            if read_evaluation is not None
                            else None
                        )
                        shard = pair[role]
                        facts = (
                            shard.get("sensitivity_evidence") if isinstance(shard, dict) else None
                        )
                        if facts is not None and (
                            facts != row.get("sensitivity_evidence")
                            or facts["plan_id"] != sensitivity_plan["id"]
                            or facts["scenario_id"] != scenario["id"]
                            or facts["support"]
                            != {
                                "closed_trades": row["summary"]["closed_trades"],
                                "entry_dates": row["entry_dates"],
                            }
                            or facts["constraints"] != row.get("portfolio_constraint_evidence")
                        ):
                            raise ValueError("Saved sensitivity account facts changed on recovery")
                        if facts is None:
                            pair[role] = None
                        pair[f"{role}_support"] = {
                            "closed_trades": row["summary"]["closed_trades"],
                            "entry_dates": row["entry_dates"],
                        }
                        pair[f"{role}_constraints"] = row.get("portfolio_constraint_evidence")
                    pairs.append(pair)
            sensitivity = assess_sensitivity(sensitivity_plan, pairs)
            diagnostic["sensitivity"] = sensitivity
            if sensitivity["status"] != "supported":
                if diagnostic["status"] == "supported":
                    diagnostic["status"] = (
                        "insufficient"
                        if sensitivity["status"] == "insufficient"
                        else "not_supported"
                    )
                    diagnostic["reasons"] = [
                        *diagnostic.get("reasons", []),
                        *sensitivity["reasons"],
                    ]
                diagnostic["qualified"] = False
        if version in (SEED_VERSION, ALLOCATION_VERSION):
            from research.recommendation_seeds import assess as assess_seeds

            seed_runs = None
            if diagnostic["status"] == "supported" and winner["config_id"] != baseline_id:
                seed_runs = []
                for seed, name in enumerate(("search", *SEED_FITS[4:])):
                    choice = winner if seed == 0 else fit(name)[0]
                    row = measured(choice["strategies"], choice["hypothesis"], "search")
                    shard = (
                        read_evaluation(
                            key_for(choice["strategies"], choice["hypothesis"], "search", False)
                        )
                        if read_evaluation
                        else None
                    )
                    facts = row.get("search_seed_evidence")
                    if facts is not None and (
                        facts.get("support")
                        != {
                            "closed_trades": row["summary"]["closed_trades"],
                            "entry_dates": row["entry_dates"],
                        }
                        or facts.get("constraints") != row.get("portfolio_constraint_evidence")
                        or (isinstance(shard, dict) and shard.get("search_seed_evidence") != facts)
                    ):
                        raise ValueError("Saved search-repeat account facts changed on recovery")
                    current = state["fits"][name]
                    seed_runs.append(
                        {
                            "seed": seed,
                            "proposals": len(current["trials"]),
                            "adaptive_proposals": sum(
                                trial["phase"] == "joint_refinement" for trial in current["trials"]
                            ),
                            "unique_accounts": len(current["candidates"]),
                            "search_id": fingerprint(
                                {"binding": binding, "seed": seed, "trials": current["trials"]}
                            ),
                            "account": shard,
                            "facts": facts,
                        }
                    )
            seed_check = assess_seeds(
                recipe["search_seeds"],
                seed_runs,
                read_evaluation(key_for(baseline, none, "search", False))
                if seed_runs and read_evaluation
                else None,
            )
            diagnostic["search_seeds"] = seed_check
            if seed_check["status"] != "supported":
                if diagnostic["status"] == "supported":
                    diagnostic["status"] = (
                        "insufficient"
                        if seed_check["status"] == "insufficient"
                        else "not_supported"
                    )
                    diagnostic["reasons"] = [*diagnostic.get("reasons", []), *seed_check["reasons"]]
                diagnostic["qualified"] = False
        if (
            state.get("recommendation_checks") is not None
            and state["recommendation_checks"] != diagnostic
        ):
            raise ValueError("The frozen development recommendation evidence changed on resume")
        state["recommendation_checks"] = diagnostic
        persist()
        process_supported = process_supported and diagnostic["status"] == "supported"
    if process_supported:
        chosen = winner
    else:
        reference = measured(baseline, none, "search")
        chosen = {
            "config_id": baseline_id,
            "strategies": baseline,
            "hypothesis": none,
            "condition_label": none["label"],
            "trial_number": None,
            "score": _score(reference["summary"]),
            **reference,
        }
    expected_selection = {**deepcopy(chosen), "is_baseline": chosen["config_id"] == baseline_id}
    if state["selection"] is not None and state["selection"] != expected_selection:
        raise ValueError("The frozen walk-forward selection changed on resume")
    if state["selection"] is None:
        state["selection"] = expected_selection
        persist()
    selected = state["selection"]

    def checked_report(slot, settings, hypothesis, period):
        report = state[slot]
        row = state["evaluations"][key_for(settings, hypothesis, period, False)]
        if (
            not isinstance(report, dict)
            or settings_identity(report.get("strategies")) != settings_identity(settings)
            or report.get("summary") != row["summary"]
            or _entry_dates(report) != row["entry_dates"]
            or report.get("evaluation_basis") != row["evaluation_basis"]
            or report.get("automatic_stock_condition") != row["automatic_stock_condition"]
            or report.get("automatic_recipe") != recipe
        ):
            raise ValueError(
                "A saved walk-forward report differs from its recorded account evidence"
            )
        if version in (CAPACITY_VERSION, SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
            actual = prepared_portfolio.assess(report, evidence["portfolio_execution_features"])
            if actual != row.get("portfolio_constraint_evidence") or actual != report.get(
                "portfolio_constraint_evidence"
            ):
                raise ValueError("Saved portfolio constraint evidence changed on recovery")
        return report

    if state["selected_report"] is None:
        measured(
            selected["strategies"],
            selected["hypothesis"],
            "search",
            report_slot="selected_report",
            reconstruct=True,
        )
    checked_report("selected_report", selected["strategies"], selected["hypothesis"], "search")
    if admit_final:
        admit_final(recipe)
    final_base = measured(baseline, none, "final", report_slot="baseline_final_report")
    checked_report("baseline_final_report", baseline, none, "final")
    if state["final_report"] is None:
        if selected["is_baseline"]:
            state["final_report"] = deepcopy(state["baseline_final_report"])
            persist()
        else:
            measured(
                selected["strategies"], selected["hypothesis"], "final", report_slot="final_report"
            )
    final = checked_report("final_report", selected["strategies"], selected["hypothesis"], "final")
    final_reasons = _support(final["summary"], final_base["summary"], dates=_entry_dates(final))
    supported = process_supported and not selected["is_baseline"] and not final_reasons
    status = (
        "supported"
        if supported
        else "inconclusive"
        if any("Fewer than" in reason for reason in final_reasons)
        or (
            version
            in (
                TURNOVER_VERSION,
                CAPACITY_VERSION,
                SENSITIVITY_VERSION,
                SEED_VERSION,
                ALLOCATION_VERSION,
            )
            and state["recommendation_checks"]["status"] == "insufficient"
        )
        else "not_supported"
    )
    headline = (
        "An improvement held up across refits and the final check"
        if supported
        else "More comparable development history is needed"
        if version
        in (
            TURNOVER_VERSION,
            CAPACITY_VERSION,
            SENSITIVITY_VERSION,
            SEED_VERSION,
            ALLOCATION_VERSION,
        )
        and status == "inconclusive"
        else "More completed trades across different dates are needed"
        if status == "inconclusive"
        else "No reliable improvement over unchanged settings"
    )
    result = deepcopy(state["selected_report"])
    final_fit = state["fits"]["search"]
    grid = len(entries) * prod(
        len(domain["choices"]) if domain.get("type") == "categorical" else len(_values(domain))
        for domain in axes.values()
    )
    result["experiment"] = {
        "kind": "portfolio_optimize",
        "experiment_version": version,
        "optimizer": optimizer,
        "specification": {
            **deepcopy(evidence["portfolio"]["optimization"]),
            "trials": proposals_per_fit,
        },
        "search_space": {
            "axes": {"hypothesis": {"type": "categorical", "choices": list(by_id)}, **axes},
            "grid_size": grid,
            "proposal_budget": proposals_per_fit,
            "objective_definition": optimizer["objective_definition"],
        },
        "counts": {
            "grid": grid,
            "proposed": proposals_per_fit,
            "evaluated_this_pass": len(rows),
            "evaluated_all_passes": len(rows),
            "rejected_allocations": 0,
            "reused_trials": sum(row["reused"] for row in final_fit["trials"]),
            "remaining": max(0, grid - len(rows)),
        },
        "objective_winner_id": rows[0]["config_id"],
        "recommendation_id": selected["config_id"],
        "rows": deepcopy(rows),
        "pass_rows": deepcopy(rows),
        "trials": deepcopy(final_fit["trials"]),
        "analysis_catalog": [],
        "selected_strategies": deepcopy(result["strategies"]),
        "selected_reports": {selected["config_id"]: deepcopy(result)},
        "findings": [
            headline,
            "Table and Optuna diagnostics describe the final development fit, not a pooled four-fit study.",
        ],
        "next_action": "Inspect later-window evidence before saving a tested setup",
        "evaluation_basis_id": result["evaluation_basis"]["evidence_id"],
        "trailing_research": deepcopy(recipe["trailing_research"]),
        "timing_research": deepcopy(recipe["timing_research"]),
    }
    if version in (
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        result["experiment"]["execution_research"] = deepcopy(recipe["execution_research"])
    final_info = {
        "summary": final["summary"],
        "baseline_summary": final_base["summary"],
        "score_delta": _score(final["summary"]) - _score(final_base["summary"]),
        "supports": supported,
        "reasons": final_reasons,
        "entry_dates": _entry_dates(final),
    }
    result["automatic_research"] = {
        "version": version,
        "status": status,
        "headline": headline,
        "recipe": deepcopy(recipe),
        "selected_config_id": selected["config_id"],
        "selected_is_baseline": selected["is_baseline"],
        "selected_condition": selected["condition_label"],
        "selection_basis": "Training-only expanding refits; all three next-window and higher-cost checks must pass before settings from the final development fit are eligible. Final outcomes never choose settings.",
        "baseline": measured(baseline, none, "search")["summary"],
        "checks": [],
        "final": final_info,
        "condition_candidates": deepcopy(rows),
        "hypothesis_candidates": deepcopy(rows),
        "counts": {
            "proposals": sum(len(fit["trials"]) for fit in state["fits"].values()),
            "simulations": state["simulations"],
            "attempts_reserved": state["attempts"],
            "finalists": 1,
            "condition_candidates": len(rows),
        },
        "unsupported_families": [
            "Original scanner reconstruction",
            "Learned indicator transforms",
            "Continuous carried-account walk-forward trading",
            "Live execution",
            "Statistical confidence claims",
            "Per-fit neighbouring-parameter stability checks",
        ],
        "evidence_scope": "Expanding-prefix fits on recorded scanner signals; prior viewing outside this application is unknown",
        "walk_forward": {
            "version": FINDINGS_VERSION,
            "status": "supported" if folds_supported else "not_supported",
            "folds": deepcopy(state["folds"]),
            "final_fit": deepcopy(recipe["periods"]["search"]),
            "selection_policy": "Each choice is frozen using only its training prefix. All folds use fresh equal cash and unchanged cost assumptions; returns are not pooled into a carried account.",
        },
    }
    if version in (
        EXECUTION_VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        result["automatic_research"]["execution_research"] = {
            "version": "research-execution-findings-v1",
            "risk_limits": deepcopy(recipe["execution_research"]["risk_limits"]),
            "feature_id": recipe["execution_features_id"],
            "sizing_controls": [
                deepcopy(state["fits"][period]["sizing_control"]) for period in FITS
            ],
            "policy": "All candidates stay within original allocation and order ceilings and fixed hard risk limits. Original-sizing controls describe development outcomes only; no final-period sizing comparison or larger-exposure improvement claim.",
        }
    if version in (
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        from research.execution_search import priority_counts

        result["automatic_research"]["execution_research"].update(
            version="research-execution-findings-v2",
            entry_priorities=deepcopy(recipe["execution_research"]["entry_priorities"]),
            selected_entry_priority=selected["strategies"][0]["execution_policy"]["entry_priority"],
            priority_policy=recipe["execution_research"]["entry_priority_policy"],
            entry_priority_counts=[
                {"period": period, "choices": priority_counts(state["fits"][period]["trials"])}
                for period in FITS
            ],
        )
    if version in (
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        SENSITIVITY_VERSION,
        SEED_VERSION,
        ALLOCATION_VERSION,
    ):
        result["automatic_research"]["recommendation_checks"] = deepcopy(
            state["recommendation_checks"]
        )
        result["automatic_research"]["selection_basis"] = (
            "Training-only expanding refits, all later-window and higher-cost checks, "
            "paired daily development uncertainty and prespecified neighbouring settings "
            "must qualify before freezing a non-baseline choice. Final outcomes never select settings."
        )
        result["automatic_research"]["execution_research"].update(
            version="research-execution-findings-v3",
            turnover_policy=recipe["execution_research"]["turnover_policy"],
        )
    if version in (CAPACITY_VERSION, SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
        result["automatic_research"]["execution_research"].update(
            version="research-execution-findings-v4",
            portfolio_features_id=recipe["portfolio_execution_features_id"],
            portfolio_constraints=deepcopy(recipe["policies"]["portfolio_constraints"]),
            feature_coverage=deepcopy(evidence["portfolio_execution_features"]["counts"]),
        )
    if version == ALLOCATION_VERSION:
        result["automatic_research"]["execution_research"].update(
            version="research-execution-findings-v5",
            allocation_modes=deepcopy(recipe["execution_research"]["allocation_modes"]),
            selected_allocation_mode=selected["strategies"][0]["execution_policy"][
                "allocation_mode"
            ],
            allocation_policy=recipe["execution_research"]["allocation_policy"],
        )
    if version in (SENSITIVITY_VERSION, SEED_VERSION, ALLOCATION_VERSION):
        result["automatic_research"]["selection_basis"] = (
            "Training-only expanding refits, later-window and higher-cost checks, "
            "paired daily ranges, measured neighbours, native constraint audits and "
            "prespecified symbol/date account reruns must qualify before freezing a "
            "changed setup. Final outcomes never select settings."
        )
    if version in (SEED_VERSION, ALLOCATION_VERSION):
        result["automatic_research"]["selection_basis"] += (
            " Three declared search seeds must also support a stable development advantage; seed 0 remains primary."
        )
    if calibration_seed_plan is not None:
        result["automatic_research"]["offline_calibration_seeds"] = deepcopy(calibration_seed_plan)
        result["automatic_research"]["selection_basis"] = (
            "Offline full-procedure sampler qualification only. Logical repeat indices remain "
            "0, 1 and 2; actual fit sampler seeds are recorded in offline_calibration_seeds. "
            "The primary development fit stays fixed; final outcomes cannot select settings."
        )
    from research.automatic_comparison import build as comparison_curves

    result["automatic_research"]["comparison"] = comparison_curves(
        state["baseline_final_report"], state["final_report"]
    )
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
        "selection_basis": result["automatic_research"]["selection_basis"],
        "result": deepcopy(final),
    }
    result.update(portfolio=deepcopy(evidence["portfolio"]), config={"initial_capital": capital})
    result["source"] = {
        "interval": evidence["snapshot"]["provenance"]["interval"],
        "provider": "OpenAlgo Historify",
        "signal_count": len(window("search")["signals"]),
        "strategy_count": len(base),
    }
    if defer_study_analysis:
        from research.study_diagnostics import deferred

        result["experiment"]["diagnostics"] = deferred()
    else:
        from research.study_analysis import build_study_analysis

        result["experiment"]["study_analysis"] = build_study_analysis(
            result["experiment"], progress=lambda *args: control()
        )
    from research.analytics import add_price_charts

    for report, period in ((result, "search"), (result["validation"]["result"], "final")):
        if report.get("analysis"):
            report["analysis"] = add_price_charts(
                report["analysis"], report, window(period)["snapshot"]
            )
    return (
        _historical_result(result) if recipe.get("validation_mode") == "historical_only" else result
    )
