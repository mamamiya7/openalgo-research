"""Read-only, bounded Optuna visualizations from recorded proposal evidence.

No sampler is run, no objective is evaluated, and no prices are requested here.
The caller is the research calculation worker, never the web request process.
"""

import json
import math
from datetime import datetime

VERSION = "research-analysis-v1"
MAX_TRIALS = 1000
MAX_AXES = 56
MAX_FIGURE_BYTES = 8 * 1024 * 1024


def reconstruct_study(experiment):
    import optuna

    trials = experiment.get("trials", [])
    axes = experiment.get("search_space", {}).get("axes", {})
    if not isinstance(trials, list) or len(trials) > MAX_TRIALS:
        raise ValueError("Study analysis supports at most 1,000 recorded proposals")
    if not isinstance(axes, dict) or len(axes) > MAX_AXES:
        raise ValueError("Invalid recorded study parameter space")
    distributions = {}
    for name, axis in axes.items():
        if name.rsplit(".", 1)[-1] in ("hold_sessions", "hold_minutes"):
            distributions[name] = optuna.distributions.IntDistribution(
                int(axis["min"]), int(axis["max"]), step=int(axis["step"])
            )
        else:
            distributions[name] = optuna.distributions.FloatDistribution(
                axis["min"], axis["max"], step=axis["step"]
            )
    study = optuna.create_study(direction="maximize")
    for index, item in enumerate(trials):
        if item.get("number") != index or item.get("state") not in ("complete", "pruned"):
            raise ValueError("Recorded proposal numbers or states are inconsistent")
        params = item.get("params", {})
        if set(params) != set(distributions):
            raise ValueError("Recorded study parameters do not match their distributions")
        value = item.get("value")
        if item["state"] == "complete" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("Recorded study objective must be finite")
        # create_trial supplies current timestamps. FrozenTrial keeps genuine
        # saved timestamps separate from absent historical observations.
        start = (
            datetime.fromisoformat(item["datetime_start"]) if item.get("datetime_start") else None
        )
        end = (
            datetime.fromisoformat(item["datetime_complete"])
            if item.get("datetime_complete")
            else None
        )
        # Optuna validates finished trials have timestamps. A constant internal
        # placeholder is needed to load old records; timeline is explicitly
        # disabled for those records and these dates never leave this function.
        frozen = optuna.trial.FrozenTrial(
            number=index,
            state=optuna.trial.TrialState.COMPLETE
            if item["state"] == "complete"
            else optuna.trial.TrialState.PRUNED,
            value=value,
            datetime_start=start or datetime(1970, 1, 1),
            datetime_complete=end or datetime(1970, 1, 1),
            params=params,
            distributions=distributions,
            user_attrs={"configuration": item.get("config_id"), "reused": bool(item.get("reused"))},
            system_attrs={},
            intermediate_values={},
            trial_id=index,
        )
        study.add_trial(frozen)
    return study, axes


def build_study_analysis(experiment, *, parameters=None, progress=None):
    import optuna

    study, axes = reconstruct_study(experiment)
    params = list(parameters) if parameters is not None else list(axes)[:2]
    if (
        len(params) > 2
        or len(set(params)) != len(params)
        or any(name not in axes for name in params)
    ):
        raise ValueError("Choose up to two parameters from this study")
    completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    varied = [name for name in axes if len({t.params[name] for t in completed}) > 1]
    target_name = experiment.get("search_space", {}).get("objective_definition", "Objective score")
    visual = optuna.visualization
    charts = []

    def add(identifier, title, factory=None, reason=None):
        if progress:
            progress(len(charts), 12)
        chart = {"id": identifier, "title": title, "status": "unavailable"}
        if reason:
            chart["reason"] = reason
        else:
            try:
                figure = factory()
                figure.update_layout(template=None, margin={"l": 55, "r": 25, "t": 45, "b": 60})
                raw = figure.to_json(remove_uids=True)
                if len(raw.encode()) > MAX_FIGURE_BYTES:
                    raise ValueError(
                        "Choose fewer parameters to keep this chart within its size limit"
                    )
                chart.update(status="available", figure=json.loads(raw))
            except (ValueError, ImportError, RuntimeError, ZeroDivisionError) as exc:
                # A sparse or constant study can be valid while a native
                # visualization has no estimable quantity.
                chart["reason"] = (
                    f"This analysis is unavailable for the recorded study: {str(exc)[:240]}"
                )
        charts.append(chart)

    empty = None if completed else "This study needs a completed trial."
    need_params = empty or (None if params else "This study has no searched parameters.")
    need_pair = empty or (
        None
        if len(params) == 2 and all(p in varied for p in params)
        else "Choose two parameters that varied across completed trials."
    )
    add(
        "history",
        "Optimization history",
        lambda: visual.plot_optimization_history(study, target_name=target_name),
        empty,
    )
    add(
        "importance",
        "Parameter importance",
        lambda: visual.plot_param_importances(
            study,
            evaluator=optuna.importance.FanovaImportanceEvaluator(n_trees=32, max_depth=8, seed=0),
            target_name=target_name,
        ),
        empty
        or (
            None
            if len(completed) >= 3 and varied and len({t.value for t in completed}) > 1
            else "Importance needs varying parameters and scores across at least three completed trials."
        ),
    )
    add(
        "slice",
        "Parameter slices",
        lambda: visual.plot_slice(study, params=params, target_name=target_name),
        need_params,
    )
    add(
        "contour",
        "Parameter contours",
        lambda: visual.plot_contour(study, params=params, target_name=target_name),
        need_pair,
    )
    add(
        "parallel",
        "Parallel coordinates",
        lambda: visual.plot_parallel_coordinate(
            study,
            params=(params if parameters is not None else list(axes)[:12]),
            target_name=target_name,
        ),
        need_params,
    )
    add(
        "rank",
        "Parameter rank",
        lambda: visual.plot_rank(study, params=params, target_name=target_name),
        need_params,
    )
    add(
        "edf",
        "Objective distribution",
        lambda: visual.plot_edf(study, target_name=target_name),
        empty,
    )
    timed = bool(study.trials) and all(
        t.get("datetime_start") and t.get("datetime_complete") for t in experiment.get("trials", [])
    )
    add(
        "timeline",
        "Trial timeline",
        lambda: visual.plot_timeline(study),
        None
        if timed
        else "This older study did not record trial start and finish times. New studies include them.",
    )
    add(
        "intermediate",
        "Intermediate values",
        reason="Each trial currently evaluates one complete portfolio; it does not report intermediate objective steps.",
    )
    add(
        "pareto",
        "Pareto front",
        reason="This study optimizes one objective. Native Pareto analysis requires a study with multiple objectives.",
    )
    add(
        "hypervolume",
        "Hypervolume history",
        reason="Hypervolume requires multiple objectives and a recorded reference point.",
    )
    add(
        "terminator",
        "Terminator improvement",
        reason="This study does not record the cross-validation error estimates required by Optuna's terminator analysis.",
    )
    if progress:
        progress(12, 12)
    return {
        "version": VERSION,
        "basis": [
            f"Optuna {optuna.__version__} native visualizations; recorded proposals and objective scores.",
            "Repeated proposals and rejected allocations remain in the study. No trial was rerun.",
            "Parameter importance uses seeded fANOVA (32 trees, depth 8); it describes this search, not future performance.",
            "Timeline uses the original calculation runtime's local clock where recorded.",
        ],
        "parameters": params,
        "charts": charts,
    }
