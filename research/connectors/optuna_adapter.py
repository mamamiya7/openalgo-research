"""Optuna finite-grid search over a supplied engine and one immutable snapshot.

Only this worker-side module imports Optuna, and only when a search runs. The
existing job service owns persistence and cancellation. Each checkpoint contains
completed trials and one winning report; it never copies the price snapshot.
"""

import hashlib
import json
import math

from research.engine import validate_config
from research.experiments import (
    EXPERIMENT_VERSION,
    grid_config,
    grid_dimensions,
    identity,
    score,
    validate_search,
)

ADAPTER_VERSION = "openalgo-optuna-grid-v1"
SEED = 0


def validate_specification(spec, config):
    """Validate the bounded candidate union before requesting broker prices."""
    canonical = validate_search(spec, validate_config(config))
    if canonical["exclude_indices"] or "parent_job_id" in canonical:
        raise ValueError(
            "Optuna currently requires a fresh search; use Resume for an interrupted run"
        )
    return canonical


def _fingerprint(value):
    # Stream JSON into the digest instead of materializing a second price file.
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False)
    for part in encoder.iterencode(value):
        digest.update(part.encode())
    return digest.hexdigest()


def _search_space(spec):
    dimensions = grid_dimensions(spec)
    hold_axis = spec.get("hold_axis", "hold_sessions")
    # Categorical strings preserve the two composite states without inventing
    # independent trailing/trigger combinations outside the accepted grid.
    space = {
        "target_pct": dimensions[0],
        "stop_pct": dimensions[1],
        hold_axis: dimensions[2],
        "trailing": [json.dumps(value, separators=(",", ":")) for value in dimensions[3]],
        "modes": [json.dumps(value, separators=(",", ":")) for value in dimensions[4]],
    }
    return dimensions, space


def _grid_index(params, space, spec):
    keys = ("target_pct", "stop_pct", spec.get("hold_axis", "hold_sessions"), "trailing", "modes")
    if not isinstance(params, dict) or set(params) != set(keys):
        raise ValueError("Optuna trial parameters do not match the search space")
    index = 0
    for key in keys:
        try:
            coordinate = space[key].index(params[key])
        except ValueError as error:
            raise ValueError("Optuna trial contains an out-of-grid parameter") from error
        index = index * len(space[key]) + coordinate
    return index


def _checked_score(summary, rank_by):
    if not isinstance(summary, dict):
        raise ValueError("The engine did not return a research summary")
    for key in ("net_return_pct", "max_drawdown_pct", "closed_trades"):
        value = summary.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"The engine returned a missing or non-finite {key}")
    if summary["closed_trades"] < 0 or int(summary["closed_trades"]) != summary["closed_trades"]:
        raise ValueError("The engine returned an invalid completed-trade count")
    value = score(summary, rank_by)
    if not math.isfinite(value):
        raise ValueError("The optimization score is not finite")
    return value


def _rank(row):
    return -row["score"], row["config_id"]


def run_search(
    signals,
    snapshot,
    config,
    spec,
    *,
    evaluate,
    execution,
    progress=None,
    checkpoint=None,
    saved=None,
):
    """Run actual Optuna GridSampler trials with serial, exactly resumable work.

    Quick/full/auto retain their existing budgets, but all use seeded grid
    sampling. They are not advertised as adaptive TPE or the legacy broad/focus
    search. Exhaustive mode must cover the entire bounded grid.
    """
    base = validate_config(config)
    spec = validate_specification(spec, base)
    if not isinstance(execution, dict) or not execution:
        raise ValueError("Optuna requires the resolved engine and optimizer version metadata")
    if progress:
        progress(0, spec["budget"])

    import optuna

    dimensions, space = _search_space(spec)
    total = math.prod(len(dimension) for dimension in dimensions)
    optimizer = {
        "id": "optuna",
        "version": optuna.__version__,
        "adapter_version": ADAPTER_VERSION,
        "sampler": "GridSampler",
        "seed": SEED,
        "requested_mode": spec["mode"],
    }
    binding = _fingerprint(
        {
            "signals": signals,
            "snapshot": snapshot,
            "config": base,
            "specification": spec,
            "execution": execution,
            "optimizer": optimizer,
        }
    )
    sampler = optuna.samplers.GridSampler(space, seed=SEED)
    study = optuna.create_study(
        study_name=f"openalgo-{binding[:24]}", direction="maximize", sampler=sampler
    )
    distributions = {
        name: optuna.distributions.CategoricalDistribution(values) for name, values in space.items()
    }
    rows = []
    trials = []
    winner_report = None
    winner_report_id = None
    winner_row = None
    seen = set()

    if saved is not None:
        if not isinstance(saved, dict) or saved.get("binding") != binding:
            raise ValueError("Optuna checkpoint inputs, settings or engine versions changed")
        saved_rows, saved_trials = saved.get("rows"), saved.get("trials")
        if (
            not isinstance(saved_rows, list)
            or not isinstance(saved_trials, list)
            or len(saved_rows) != len(saved_trials)
            or not 1 <= len(saved_rows) <= spec["budget"]
            or saved.get("optimizer") != optimizer
        ):
            raise ValueError("Invalid bounded Optuna checkpoint")
        for number, (row, trial) in enumerate(zip(saved_rows, saved_trials, strict=True)):
            if not isinstance(row, dict) or not isinstance(trial, dict):
                raise ValueError("Invalid Optuna trial evidence")
            params = trial.get("params")
            index = _grid_index(params, space, spec)
            expected = grid_config(index, spec, base)
            value = _checked_score(row.get("summary"), spec["rank_by"])
            if (
                index in seen
                or row.get("grid_index") != index
                or row.get("config") != expected
                or row.get("config_id") != identity(expected)
                or row.get("score") != value
                or row.get("stage") != "grid"
                or trial.get("number") != number
                or trial.get("value") != value
                or trial.get("grid_id") != number
            ):
                raise ValueError("Optuna checkpoint configuration evidence is inconsistent")
            study.add_trial(
                optuna.trial.create_trial(
                    state=optuna.trial.TrialState.COMPLETE,
                    value=value,
                    params=params,
                    distributions=distributions,
                    system_attrs={"grid_id": trial["grid_id"], "search_space": space},
                )
            )
            seen.add(index)
            rows.append(row)
            trials.append(trial)
        winner_row = min(rows, key=_rank)
        winner_report = saved.get("winner_report")
        if (
            not isinstance(winner_report, dict)
            or winner_report.get("config") != winner_row["config"]
            or winner_report.get("summary") != winner_row["summary"]
            or saved.get("winner_report_id") != _fingerprint(winner_report)
        ):
            raise ValueError("Optuna checkpoint is missing its exact winning report")
        winner_report_id = saved["winner_report_id"]

    def objective(trial):
        nonlocal winner_report, winner_report_id, winner_row
        params = {name: trial.suggest_categorical(name, choices) for name, choices in space.items()}
        index = _grid_index(params, space, spec)
        if index in seen:
            raise ValueError("Optuna proposed an already completed configuration")
        candidate = grid_config(index, spec, base)

        def beat(done, count):
            if progress:
                progress(len(rows) + done / max(1, count), spec["budget"])

        if progress:
            progress(len(rows), spec["budget"])
        report = evaluate(signals, snapshot, candidate, progress=beat)
        if not isinstance(report, dict) or report.get("config") != candidate:
            raise ValueError("The engine report does not match the requested configuration")
        value = _checked_score(report.get("summary"), spec["rank_by"])
        row = {
            "grid_index": index,
            "config_id": identity(candidate),
            "config": candidate,
            "summary": report["summary"],
            "score": value,
            "stage": "grid",
        }
        rows.append(row)
        seen.add(index)
        if winner_row is None or _rank(row) < _rank(winner_row):
            winner_row, winner_report = row, report
            winner_report_id = _fingerprint(report)
        return value

    def completed(_study, trial):
        # Optuna invokes callbacks only after it marks the trial COMPLETE.
        # Exceptions from cancellation or persistence propagate to the worker.
        if trial.state != optuna.trial.TrialState.COMPLETE:
            raise ValueError("Optuna trial did not complete")
        trials.append(
            {
                "number": trial.number,
                "params": trial.params,
                "value": trial.value,
                # The immutable specification already contains the whole grid.
                # Persist its ID, rather than repeat that grid in every trial.
                "grid_id": trial.system_attrs["grid_id"],
            }
        )
        if checkpoint:
            checkpoint(
                {
                    "binding": binding,
                    "optimizer": optimizer,
                    "rows": rows,
                    "trials": trials,
                    "winner_report": winner_report,
                    "winner_report_id": winner_report_id,
                },
                {"stage": "search", "completed": len(rows), "total": spec["budget"]},
            )
        if progress:
            progress(len(rows), spec["budget"])

    if len(rows) < spec["budget"]:
        study.optimize(
            objective, n_trials=spec["budget"] - len(rows), callbacks=[completed], n_jobs=1
        )
    if len(rows) != spec["budget"] or winner_row is None:
        raise ValueError("Optuna stopped before completing the requested search budget")

    ranked = sorted(rows, key=_rank)
    enough = len(signals) >= 30 and winner_row["summary"]["closed_trades"] >= 30
    verdict = (
        "final_insufficient_evidence"
        if not enough
        else "final_no_reliable_edge"
        if winner_row["summary"]["net_return_pct"] <= 0
        else "final_useful_result"
    )
    return {
        **winner_report,
        "experiment": {
            "kind": "optimize",
            "experiment_version": EXPERIMENT_VERSION,
            "optimizer": optimizer,
            "state": verdict,
            "specification": spec,
            "counts": {
                "grid": total,
                "evaluated_this_pass": len(rows),
                "evaluated_all_passes": len(rows),
                "inherited_rows": 0,
                "stored_rows": len(rows),
                "stored_pass_rows": len(rows),
                "remaining": total - len(rows),
            },
            "recommendation_id": winner_row["config_id"],
            "alternatives": [],
            "rows": ranked,
            "pass_rows": rows,
            "pass_recommendation_id": winner_row["config_id"],
            "selected_reports": {winner_row["config_id"]: winner_report},
            "findings": [
                "Optuna sampled distinct settings from the declared finite grid; ranking uses this historical sample, not an independent holdout.",
                "Fewer than 30 completed trades/signals is insufficient sample."
                if not enough
                else "Test the selected setting on an untouched later period.",
            ],
            "next_action": "Test the selected setting on an untouched later period"
            if enough
            else "Collect more independent dated signals",
            "follow_up": None,
        },
    }
