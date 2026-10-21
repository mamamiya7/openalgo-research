"""Read-only, bounded Optuna visualizations from recorded proposal evidence.

No sampler is run, no objective is evaluated, and no prices are requested here.
The caller is the research calculation worker, never the web request process.
"""

import json
import math
import re
from datetime import datetime

VERSION = "research-analysis-v1"
MAX_TRIALS = 1000
MAX_AXES = 57
EXECUTION_MAX_AXES = 73
PRIORITY_MAX_AXES = 74
MAX_FIGURE_BYTES = 8 * 1024 * 1024
# fANOVA integrates all one-hot dimensions of a categorical parameter jointly.
# Its tree midpoint Cartesian product can grow exponentially with the category
# count. Bound admission before fitting; proposal count alone does not bound it.
MAX_FANOVA_CATEGORIES = 8


def _trailing_axes(experiment):
    """Admit only the frozen, finite atomic states of the v6 recipe."""
    specification = experiment.get("trailing_research")
    if specification is None:
        return {}
    from research.automatic_trailing import VERSION as TRAILING_VERSION
    from research.connectors.optuna_portfolio import _axis, _values

    if (
        experiment.get("experiment_version")
        not in (
            "automatic-hypotheses-v6",
            "automatic-hypotheses-v7",
            "automatic-hypotheses-v8",
            "automatic-hypotheses-v9",
            "automatic-hypotheses-v10",
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
        )
        or not isinstance(specification, dict)
        or specification.get("version") != TRAILING_VERSION
        or not isinstance(specification.get("strategies"), list)
        or not 1 <= len(specification["strategies"]) <= 8
    ):
        raise ValueError("Invalid recorded trailing research contract")
    result = {}
    for row in specification["strategies"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["id"])
        ):
            raise ValueError("Invalid recorded trailing strategy")
        name = f"{row['id']}.trailing_choice"
        domain = _axis("trailing_pct", row.get("domain"))
        choices = row.get("choices")
        if (
            name in result
            or domain["min"] <= 0
            or not isinstance(choices, list)
            or not 2 <= len(choices) <= 8
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in choices)
            or choices != [0.0, *_values(domain)]
        ):
            raise ValueError("Invalid recorded conditional trailing choices")
        result[name] = {"type": "categorical", "choices": choices}
    return result


def _timing_axes(experiment):
    """Read only finite, frozen native clock states; never infer a new clock."""
    specification = experiment.get("timing_research")
    if specification is None:
        return {}
    from research.automatic_timing import VERSION as TIMING_VERSION
    from research.engine import validate_config

    if (
        experiment.get("experiment_version")
        not in (
            "automatic-hypotheses-v7",
            "automatic-hypotheses-v8",
            "automatic-hypotheses-v9",
            "automatic-hypotheses-v10",
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
        )
        or not isinstance(specification, dict)
        or specification.get("version") != TIMING_VERSION
        or specification.get("interval") not in ("D", "1m")
        or not isinstance(specification.get("strategies"), list)
        or not 1 <= len(specification["strategies"]) <= 8
    ):
        raise ValueError("Invalid recorded timing research contract")
    result = {}
    for row in specification["strategies"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["id"])
            or not isinstance(row.get("original"), dict)
            or set(row["original"]) - {"entry_time", "exit_time"}
            or not isinstance(row.get("branches"), list)
            or not 1 <= len(row["branches"]) <= 4
        ):
            raise ValueError("Invalid recorded timing strategy")
        name = f"{row['id']}.timing_choice"
        choices = []
        for branch in row["branches"]:
            if (
                not isinstance(branch, dict)
                or set(branch) != {"id", "label", "changes"}
                or not isinstance(branch["id"], str)
                or not re.fullmatch(r"original|(?:entry|exit)_[0-9]{4}", branch["id"])
                or not isinstance(branch["label"], str)
                or len(branch["label"]) > 80
                or not isinstance(branch["changes"], dict)
                or set(branch["changes"]) - {"entry_time", "exit_time"}
                or (branch["id"] == "original" and branch["changes"])
                or (branch["id"] != "original" and len(branch["changes"]) != 1)
            ):
                raise ValueError("Invalid recorded timing choice")
            if branch["id"] != "original":
                key = "entry_time" if branch["id"].startswith("entry_") else "exit_time"
                clock = branch["id"].split("_", 1)[1]
                if branch["changes"] != {key: f"{clock[:2]}:{clock[2:]}"}:
                    raise ValueError("Recorded timing clock differs from its registered identity")
            validate_config({**row["original"], **branch["changes"]})
            choices.append(branch["id"])
        if (
            name in result
            or choices[0] != "original"
            or len(set(choices)) != len(choices)
            or (specification["interval"] == "D" and choices != ["original"])
        ):
            raise ValueError("Invalid recorded timing choices")
        result[name] = {"type": "categorical", "choices": choices}
    return result


def reconstruct_study(experiment):
    import optuna

    trials = experiment.get("trials", [])
    axes = experiment.get("search_space", {}).get("axes", {})
    if not isinstance(trials, list) or len(trials) > MAX_TRIALS:
        raise ValueError("Study analysis supports at most 1,000 recorded proposals")
    maximum = (
        PRIORITY_MAX_AXES
        if experiment.get("experiment_version")
        in (
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
        )
        else EXECUTION_MAX_AXES
        if experiment.get("experiment_version")
        in (
            "automatic-hypotheses-v10",
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
        )
        else MAX_AXES
    )
    if not isinstance(axes, dict) or len(axes) > maximum:
        raise ValueError("Invalid recorded study parameter space")
    trailing_axes = _trailing_axes(experiment)
    if any(axes.get(name) != axis for name, axis in trailing_axes.items()):
        raise ValueError("Recorded trailing parameters differ from their frozen contract")
    timing_axes = _timing_axes(experiment)
    if any(axes.get(name) != axis for name, axis in timing_axes.items()):
        raise ValueError("Recorded timing parameters differ from their frozen contract")
    if any(name.endswith(".timing_choice") and name not in timing_axes for name in axes):
        raise ValueError("Recorded timing parameters need their frozen contract")
    execution_axes = {}
    expected_execution = {
        "automatic-hypotheses-v10": "research-execution-search-v1",
        "automatic-hypotheses-v11": "research-execution-search-v2",
        "automatic-hypotheses-v12": "research-execution-search-v3",
    }.get(experiment.get("experiment_version"))
    if expected_execution and (
        not isinstance(experiment.get("execution_research"), dict)
        or experiment["execution_research"].get("version") != expected_execution
    ):
        raise ValueError("Recorded execution descriptor differs from its programme version")
    if experiment.get("execution_research") is not None:
        if experiment.get("experiment_version") not in (
            "automatic-hypotheses-v10",
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
        ):
            raise ValueError("Execution research needs its recorded programme")
        from research.execution_search import axes as execution_domains

        execution_axes = execution_domains(experiment["execution_research"])
        if any(axes.get(name) != axis for name, axis in execution_axes.items()):
            raise ValueError("Recorded execution parameters differ from their frozen contract")
    if any(
        (
            name.endswith((".execution_choice", ".portfolio_choice"))
            or name == "portfolio.entry_priority"
        )
        and name not in execution_axes
        for name in axes
    ):
        raise ValueError("Recorded execution parameters need their frozen contract")
    distributions = {}
    for name, axis in axes.items():
        if axis.get("type") == "categorical":
            choices = axis.get("choices")
            limit = 32
            if name == "hypothesis":
                from research.joint_stock_market import catalog

                if experiment.get("experiment_version") in (
                    "automatic-hypotheses-v9",
                    "automatic-hypotheses-v10",
                    "automatic-hypotheses-v11",
                    "automatic-hypotheses-v12",
                    "automatic-hypotheses-v13",
                    "automatic-hypotheses-v14",
                    "automatic-hypotheses-v15",
                ):
                    from research.expanded_stock_market import catalog

                if choices == [row["id"] for row in catalog()]:
                    limit = len(choices)
            if (
                not isinstance(choices, list)
                or not 1 <= len(choices) <= limit
                or (
                    name not in trailing_axes
                    and any(not isinstance(value, str) for value in choices)
                )
                or len(set(choices)) != len(choices)
            ):
                raise ValueError("Invalid bounded categorical study parameter")
            distributions[name] = optuna.distributions.CategoricalDistribution(choices)
        elif name.rsplit(".", 1)[-1] in ("hold_sessions", "hold_minutes"):
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
    large_categories = [
        (name, len(axis["choices"]))
        for name, axis in axes.items()
        if axis.get("type") == "categorical" and len(axis["choices"]) > MAX_FANOVA_CATEGORIES
    ]
    importance_limit = (
        "Parameter importance is unavailable: native fANOVA is limited to "
        f"{MAX_FANOVA_CATEGORIES} choices per categorical parameter to keep report preparation bounded. "
        + "; ".join(f"{name}: {count} choices" for name, count in large_categories)
        + ". Other study charts and saved calculation results remain available."
        if large_categories
        else None
    )
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
        or importance_limit
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
        else "This recorded study does not include original trial start and finish times.",
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
            "Parameter importance, when available, uses seeded fANOVA (32 trees, depth 8); "
            f"categorical parameters above {MAX_FANOVA_CATEGORIES} choices are not estimated. "
            "It describes this search, not future performance.",
            "Timeline uses the original calculation runtime's local clock where recorded.",
        ],
        "parameters": params,
        "charts": charts,
    }
