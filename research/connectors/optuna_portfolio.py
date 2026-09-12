"""Bounded Optuna search across strategies sharing one portfolio.

The engine callback owns simulation. This module owns no files, threads, database
connections or global result cache. JSON checkpoints contain bounded trial
evidence and one best report, never repeated signals or price snapshots. Serial
replay rebuilds the pinned sampler's state without pickling executable objects.
"""

import copy
import hashlib
import json
import math
import re
from decimal import Decimal
from time import monotonic

from research.engine import validate_config

ADAPTER_VERSION = "openalgo-optuna-portfolio-v1"
TESTED_OPTUNA_VERSION = "5.0.0"
MAX_STRATEGIES = 8
MAX_TRIALS = 1000
MAX_GRID = 50000
MAX_AXIS_VALUES = 10001
MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
AXIS_BOUNDS = {
    "target_pct": (0.01, 500),
    "stop_pct": (0.01, 99),
    "hold_sessions": (1, 252),
    "hold_minutes": (1, 100000),
    "trailing_pct": (0, 99),
    "order_size_pct": (0.01, 100),
    "allocation_pct": (0, 100),
}
OBJECTIVES = {
    "balanced": "Net return (%) minus maximum drawdown (%)",
    "return": "Net return (%)",
    "drawdown": "Negative maximum drawdown (%)",
}


def _fingerprint(value, *, max_bytes=None):
    digest, size = hashlib.sha256(), 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False)
    for part in encoder.iterencode(value):
        encoded = part.encode()
        size += len(encoded)
        if max_bytes is not None and size > max_bytes:
            raise ValueError("Portfolio search checkpoint exceeds its storage limit")
        digest.update(encoded)
    return digest.hexdigest()


def _number(value, low, high, label, *, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
        or not low <= value <= high
        or (integer and int(value) != value)
    ):
        raise ValueError(
            f"{label} must be {'an integer' if integer else 'a number'} from {low} to {high}"
        )
    return int(value) if integer else value


def _axis(parameter, raw):
    if not isinstance(raw, dict) or set(raw) != {"min", "max", "step"}:
        raise ValueError(f"{parameter} search needs min, max and step")
    integer = parameter in ("hold_sessions", "hold_minutes")
    low, high = AXIS_BOUNDS[parameter]
    minimum = _number(raw["min"], low, high, f"{parameter} minimum", integer=integer)
    maximum = _number(raw["max"], minimum, high, f"{parameter} maximum", integer=integer)
    step = _number(
        raw["step"], 1 if integer else 0.000001, high, f"{parameter} step", integer=integer
    )
    # Decimal prevents a 0.1 step quietly dropping the declared final point.
    count = (Decimal(str(maximum)) - Decimal(str(minimum))) / Decimal(str(step))
    if count != count.to_integral_value():
        raise ValueError(f"{parameter} maximum must be reachable from its minimum and step")
    if count + 1 > MAX_AXIS_VALUES:
        raise ValueError(f"{parameter} search exceeds {MAX_AXIS_VALUES} values")
    return {"min": minimum, "max": maximum, "step": step}


def _prepare(strategies):
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= MAX_STRATEGIES:
        raise ValueError(f"Supply 1-{MAX_STRATEGIES} portfolio strategies")
    canonical, axes, ids = [], {}, set()
    for raw in strategies:
        if not isinstance(raw, dict):
            raise ValueError("Each portfolio strategy must be an object")
        strategy_id = raw.get("id")
        if (
            not isinstance(strategy_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", strategy_id)
            or strategy_id in ids
        ):
            raise ValueError(
                "Portfolio strategy IDs must be unique letters, numbers, underscores or hyphens"
            )
        ids.add(strategy_id)
        name = raw.get("name", strategy_id)
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Each strategy needs a name of 1-120 characters")
        allocation = _number(raw.get("allocation_pct"), 0, 100, "Strategy allocation")
        config = validate_config(raw.get("config"))
        search = raw.get("search", {})
        if not isinstance(search, dict) or set(search) - set(AXIS_BOUNDS):
            raise ValueError("Unknown portfolio search parameter")
        checked_search = {}
        for parameter in sorted(search):
            checked = _axis(parameter, search[parameter])
            if parameter == "trailing_pct" and not config["trailing_enabled"]:
                raise ValueError("Enable trailing protection before optimizing its distance")
            if parameter == "hold_sessions" and (
                config.get("hold_minutes") is not None or config.get("trade_horizon") == "intraday"
            ):
                raise ValueError(
                    "Optimize the active minute holding limit instead of unused sessions"
                )
            if parameter == "hold_minutes" and config.get("hold_minutes") is None:
                raise ValueError("Set a minute holding limit before optimizing it")
            if parameter == "trailing_pct" and checked["min"] <= 0:
                raise ValueError("An enabled trailing search must use positive distances")
            checked_search[parameter] = checked
            axes[f"{strategy_id}.{parameter}"] = checked
        canonical.append(
            {
                **raw,
                "id": strategy_id,
                "name": name,
                "allocation_pct": allocation,
                "config": config,
                "search": checked_search,
            }
        )
    minima = [
        strategy["search"].get("allocation_pct", {}).get("min", strategy["allocation_pct"])
        for strategy in canonical
    ]
    maxima = [
        strategy["search"].get("allocation_pct", {}).get("max", strategy["allocation_pct"])
        for strategy in canonical
    ]
    if sum(Decimal(str(value)) for value in minima) > 100:
        raise ValueError("No allowed allocation combination fits within 100% of portfolio capital")
    if not any(value > 0 for value in maxima):
        raise ValueError("At least one strategy must have a positive allowed allocation")
    return canonical, dict(sorted(axes.items()))


def _values(axis):
    start, stop, step = (Decimal(str(axis[key])) for key in ("min", "max", "step"))
    return [float(start + i * step) for i in range(int((stop - start) / step) + 1)]


def validate_specification(specification, strategies):
    """Validate the complete search union without importing numerical packages."""
    _, axes = _prepare(strategies)
    specification = {} if specification is None else specification
    if not isinstance(specification, dict) or set(specification) - {
        "sampler",
        "trials",
        "objective",
        "seed",
    }:
        raise ValueError("Unknown portfolio optimization setting")
    sampler = specification.get("sampler", "tpe")
    objective = specification.get("objective", "balanced")
    if (
        sampler not in ("tpe", "grid")
        or not isinstance(objective, str)
        or objective not in OBJECTIVES
    ):
        raise ValueError("Choose TPE or grid search and a supported portfolio objective")
    trials = _number(specification.get("trials", 50), 1, MAX_TRIALS, "Search trials", integer=True)
    seed = _number(specification.get("seed", 0), 0, 2147483647, "Search seed", integer=True)
    total = math.prod(len(_values(axis)) for axis in axes.values())
    if sampler == "grid" and total > MAX_GRID:
        raise ValueError(
            f"Grid search exceeds {MAX_GRID} combinations; narrow the ranges or use adaptive search"
        )
    return {"sampler": sampler, "trials": trials, "objective": objective, "seed": seed}


def requirement_strategies(strategies, specification=None):
    """Return the full range's maximum requirements, never a sampled candidate.

    This is a data-planning union, not an executable allocation proposal: maximum
    allocations may jointly exceed 100%. Signal objects are shared read-only.
    """
    validate_specification(specification, strategies)
    canonical, _ = _prepare(strategies)
    result = []
    for strategy in canonical:
        config = dict(strategy["config"])
        allocation = strategy["allocation_pct"]
        for parameter, axis in strategy["search"].items():
            if parameter == "allocation_pct":
                allocation = axis["max"]
            else:
                config[parameter] = axis["max"]
        result.append({**strategy, "config": validate_config(config), "allocation_pct": allocation})
    return result


def describe_search(strategies, specification=None):
    spec = validate_specification(specification, strategies)
    canonical, axes = _prepare(strategies)
    total = math.prod(len(_values(axis)) for axis in axes.values())
    return {
        "specification": spec,
        "axes": axes,
        "grid_size": total,
        "proposal_budget": min(spec["trials"], total)
        if spec["sampler"] == "grid" or total == 1
        else spec["trials"],
        "objective_definition": OBJECTIVES[spec["objective"]],
        "allocation_constraint": "0 < sum(strategy allocations) <= 100; no normalization",
        "requirements": [
            {
                "id": strategy["id"],
                "config": strategy["config"],
                "allocation_pct": strategy["allocation_pct"],
            }
            for strategy in requirement_strategies(canonical, spec)
        ],
    }


def _candidate(strategies, params):
    result = []
    for strategy in strategies:
        config = copy.deepcopy(strategy["config"])
        allocation = strategy["allocation_pct"]
        for parameter, axis in strategy["search"].items():
            proposed = Decimal(str(params[f"{strategy['id']}.{parameter}"]))
            start, step = Decimal(str(axis["min"])), Decimal(str(axis["step"]))
            coordinate = round((proposed - start) / step)
            exact = start + coordinate * step
            if (
                exact < start
                or exact > Decimal(str(axis["max"]))
                or abs(exact - proposed) > Decimal("0.00000001")
            ):
                raise ValueError("Optuna proposed a value outside the declared parameter steps")
            # Enqueued Optuna floats can be represented as ints. Canonicalize
            # both that case and binary step rounding before result identity.
            value = int(exact) if parameter in ("hold_sessions", "hold_minutes") else float(exact)
            if parameter == "allocation_pct":
                allocation = value
            else:
                config[parameter] = value
        result.append(
            {
                **strategy,
                "config": validate_config(config),
                "allocation_pct": allocation,
                "search": {},
            }
        )
    return result


def _settings(strategies):
    return [
        {key: copy.deepcopy(strategy[key]) for key in ("id", "name", "allocation_pct", "config")}
        for strategy in strategies
    ]


def _feasible(strategies):
    total = sum(Decimal(str(strategy["allocation_pct"])) for strategy in strategies)
    return 0 < total <= 100


def _first_candidate(strategies, axes):
    params = {name: axis["min"] for name, axis in axes.items()}
    candidate = _candidate(strategies, params)
    if not _feasible(candidate):
        # A zero lower corner has no invested strategy. Choose the first allowed
        # positive allocation, preserving every other axis at its declared min.
        for name, axis in axes.items():
            if name.endswith(".allocation_pct") and axis["max"] > 0:
                params[name] = axis["step"]
                break
    if not _feasible(_candidate(strategies, params)):
        raise ValueError("No feasible initial portfolio allocation")
    return params


def _checked_score(summary, objective):
    if not isinstance(summary, dict):
        raise ValueError("The portfolio engine did not return a summary")
    for key in ("net_return_pct", "max_drawdown_pct", "closed_trades"):
        value = summary.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"The portfolio engine returned a missing or non-finite {key}")
    if (
        summary["max_drawdown_pct"] < 0
        or summary["closed_trades"] < 0
        or int(summary["closed_trades"]) != summary["closed_trades"]
    ):
        raise ValueError("The portfolio engine returned invalid drawdown or completed-trade counts")
    value = (
        summary["net_return_pct"]
        if objective == "return"
        else -summary["max_drawdown_pct"]
        if objective == "drawdown"
        else summary["net_return_pct"] - summary["max_drawdown_pct"]
    )
    if not math.isfinite(value):
        raise ValueError("The portfolio optimization score is non-finite")
    return value


def _rank(row):
    return -row["score"], row["config_id"]


def run_search(
    strategies,
    snapshot,
    capital,
    specification,
    *,
    evaluate,
    execution,
    progress=None,
    checkpoint=None,
    saved=None,
    activity=None,
    record_timing=False,
    observe=None,
):
    """Run joint grid/TPE proposals through one shared-capital engine callback.

    Budget counts proposals, including rejected allocations and TPE repeats.
    Repeats reuse their exact score; the engine is called once per distinct
    feasible configuration. TPE uses a feasible lower-corner first trial, ten
    startup observations, then Optuna's adaptive proposals. Resume replays all
    completed/pruned trial proposals and scores through the same seeded sampler.
    """
    spec = validate_specification(specification, strategies)
    base, axes = _prepare(strategies)
    capital = _number(capital, 1, 1e9, "Portfolio capital")
    # Every strategy sees the same account. This legacy configuration field is
    # recorded for compatibility and cannot create a private starting balance.
    for strategy in base:
        strategy["config"]["initial_capital"] = float(capital)
    plan = describe_search(base, spec)
    budget = plan["proposal_budget"]
    if not isinstance(execution, dict) or not execution:
        raise ValueError("Portfolio optimization needs resolved engine version metadata")
    if progress:
        progress(0, budget)

    import optuna

    if optuna.__version__ != TESTED_OPTUNA_VERSION:
        raise ValueError(f"Portfolio optimization requires tested Optuna {TESTED_OPTUNA_VERSION}")
    optimizer = {
        "id": "optuna",
        "version": optuna.__version__,
        "adapter_version": ADAPTER_VERSION,
        "sampler": "TPESampler" if spec["sampler"] == "tpe" else "GridSampler",
        "seed": spec["seed"],
        "startup_trials": 10 if spec["sampler"] == "tpe" else 0,
        "resume_policy": "serial-seeded-trial-replay-v1",
        "objective_definition": OBJECTIVES[spec["objective"]],
        "first_trial": "feasible lower corner" if spec["sampler"] == "tpe" else "seeded grid order",
    }
    binding = _fingerprint(
        {
            "strategies": base,
            "snapshot": snapshot,
            "capital": capital,
            "specification": spec,
            "execution": execution,
            "optimizer": optimizer,
        }
    )
    sampler = (
        optuna.samplers.TPESampler(seed=spec["seed"], n_startup_trials=10, constant_liar=False)
        if spec["sampler"] == "tpe"
        else optuna.samplers.GridSampler(
            {name: _values(axis) for name, axis in axes.items()}, seed=spec["seed"]
        )
    )
    study = optuna.create_study(
        study_name=f"openalgo-portfolio-{binding[:24]}", direction="maximize", sampler=sampler
    )
    if spec["sampler"] == "tpe":
        study.enqueue_trial(_first_candidate(base, axes))
    rows, trials = {}, []
    analysis_catalog = {}
    winner_report = winner_row = None
    last_notice, last_notice_key = float("-inf"), None

    def notify(stage="optimizing", active_trial=None, failed=0):
        nonlocal last_notice, last_notice_key
        if activity:
            now = monotonic()
            key = (stage, active_trial, len(trials), failed)
            if key == last_notice_key and now - last_notice < 1:
                return
            last_notice, last_notice_key = now, key
            scored = [item for item in trials if item["state"] == "complete"]
            # Bound the live chart. The complete original history stays in the
            # immutable Optuna evidence and is not sampled away there.
            stride = max(1, math.ceil(len(scored) / 99))
            shown = scored[::stride]
            if scored and shown[-1] is not scored[-1]:
                shown.append(scored[-1])
            activity(
                {
                    "stage": stage,
                    "trials": {
                        "total": budget,
                        "completed": len(trials) + failed,
                        "active_trial": active_trial,
                        "evaluated": sum(not item["reused"] for item in scored),
                        "reused": sum(item["reused"] for item in scored),
                        "rejected": sum(item["state"] == "pruned" for item in trials),
                        "failed": failed,
                        "history": [
                            {"trial": item["number"] + 1, "score": item["value"]} for item in shown
                        ],
                    },
                }
            )

    def suggest(trial):
        return {
            name: trial.suggest_int(
                name, int(axis["min"]), int(axis["max"]), step=int(axis["step"])
            )
            if name.rsplit(".", 1)[1] in ("hold_sessions", "hold_minutes")
            else trial.suggest_float(name, axis["min"], axis["max"], step=axis["step"])
            for name, axis in axes.items()
        }

    def execute(replay=None, restored_rows=None):
        nonlocal winner_row, winner_report
        record = None
        observation_failed = False

        def objective(trial):
            nonlocal record, winner_row, winner_report, observation_failed
            params = suggest(trial)
            candidate = _candidate(base, params)
            settings = _settings(candidate)
            config_id = _fingerprint(settings)
            feasible = _feasible(candidate)
            record = {
                "number": trial.number,
                "params": params,
                "config_id": config_id,
                "state": "complete" if feasible else "pruned",
                "value": None,
                "reused": config_id in rows,
            }
            if replay is not None and any(
                record[key] != replay.get(key) for key in record if key != "value"
            ):
                raise ValueError("Portfolio checkpoint does not replay the same Optuna proposals")
            if replay is None and observe:
                # Operational observations are separate from scientific evidence.
                # Never report replayed proposals as a new calculation attempt.
                try:
                    observe(
                        {
                            "kind": "proposal_started",
                            "number": trial.number,
                            "params": copy.deepcopy(params),
                            "config_id": config_id,
                            "reused": record["reused"],
                        }
                    )
                except Exception:
                    observation_failed = True
                    raise
            if not feasible:
                if replay is not None and replay.get("value") is not None:
                    raise ValueError("Rejected portfolio checkpoint trial has a score")
                raise optuna.TrialPruned(
                    "Strategy allocations must total more than 0% and at most 100%"
                )
            if config_id in rows:
                row = rows[config_id]
            elif replay is not None:
                row = restored_rows.get(config_id)
                if (
                    not isinstance(row, dict)
                    or row.get("strategies") != settings
                    or row.get("config_id") != config_id
                    or row.get("score") != _checked_score(row.get("summary"), spec["objective"])
                    or row.get("trial_number") != trial.number
                ):
                    raise ValueError("Portfolio checkpoint configuration evidence is inconsistent")
                rows[config_id] = row
            else:
                notify("initializing", trial.number + 1)

                def beat(done, count):
                    if done > 0:
                        notify("optimizing", trial.number + 1)
                    if progress:
                        progress(len(trials) + min(1, max(0, done / max(1, count))), budget)

                report = evaluate(candidate, snapshot, capital, progress=beat)
                if not isinstance(report, dict):
                    raise ValueError("The portfolio engine did not return a report")
                if report.get("strategies") != settings:
                    raise ValueError(
                        "The portfolio report does not match its requested strategy settings"
                    )
                value = _checked_score(report.get("summary"), spec["objective"])
                row = {
                    "trial_number": trial.number,
                    "config_id": config_id,
                    "strategies": settings,
                    "summary": copy.deepcopy(report["summary"]),
                    "score": value,
                    "stage": spec["sampler"],
                }
                if isinstance(report.get("analysis"), dict):
                    row["analysis"] = {
                        key: copy.deepcopy(report["analysis"][key])
                        for key in ("version", "metrics", "unavailable")
                    }
                    for definition in report["analysis"].get("catalog", []):
                        analysis_catalog[definition["key"]] = definition
                rows[config_id] = row
                if winner_row is None or _rank(row) < _rank(winner_row):
                    winner_report = report
            if winner_row is None or _rank(row) < _rank(winner_row):
                winner_row = row
            record["value"] = row["score"]
            if replay is not None and any(record[key] != replay.get(key) for key in record):
                raise ValueError("Portfolio checkpoint score evidence is inconsistent")
            return row["score"]

        try:
            study.optimize(objective, n_trials=1, n_jobs=1)
        except Exception:
            if (
                replay is None
                and not observation_failed
                and study.trials
                and study.trials[-1].state == optuna.trial.TrialState.FAIL
            ):
                notify(failed=1)
            raise
        if replay is not None:
            # Reporting timestamps never influence proposal/score replay.
            for key in ("datetime_start", "datetime_complete"):
                if key in replay:
                    record[key] = replay[key]
        elif record_timing:
            native = study.trials[-1]
            record["datetime_start"] = native.datetime_start.isoformat()
            record["datetime_complete"] = native.datetime_complete.isoformat()
        if replay is None and observe:
            observe(
                {
                    "kind": "proposal_finished",
                    "number": record["number"],
                    "state": record["state"],
                    "value": record["value"],
                    "reused": record["reused"],
                }
            )
        trials.append(record)

    if saved is not None:
        if not isinstance(saved, dict) or saved.get("binding") != binding:
            raise ValueError("Portfolio checkpoint inputs, settings or engine versions changed")
        old_trials, old_rows = saved.get("trials"), saved.get("rows")
        if (
            not isinstance(old_trials, list)
            or not 1 <= len(old_trials) <= budget
            or not isinstance(old_rows, list)
            or len(old_rows) > len(old_trials)
            or saved.get("optimizer") != optimizer
        ):
            raise ValueError("Invalid bounded portfolio checkpoint")
        payload = {key: value for key, value in saved.items() if key != "checkpoint_id"}
        if saved.get("checkpoint_id") != _fingerprint(payload, max_bytes=MAX_CHECKPOINT_BYTES):
            raise ValueError("Portfolio checkpoint evidence changed")
        restored = {
            row["config_id"]: row
            for row in old_rows
            if isinstance(row, dict) and "config_id" in row
        }
        if len(restored) != len(old_rows):
            raise ValueError("Duplicate or invalid portfolio checkpoint rows")
        for old_trial in old_trials:
            if progress:
                progress(len(trials), budget)
            if not isinstance(old_trial, dict):
                raise ValueError("Invalid portfolio checkpoint trial")
            execute(old_trial, restored)
        if rows != restored:
            raise ValueError("Portfolio checkpoint contains unused configuration evidence")
        winner_report = saved.get("winner_report")
        for definition in saved.get(
            "analysis_catalog", (winner_report or {}).get("analysis", {}).get("catalog", [])
        ):
            analysis_catalog[definition["key"]] = definition
        if winner_row is not None and (
            not isinstance(winner_report, dict)
            or winner_report.get("summary") != winner_row["summary"]
            or saved.get("winner_config_id") != winner_row["config_id"]
        ):
            raise ValueError("Portfolio checkpoint is missing its exact winning report")
        if winner_row is None and (
            winner_report is not None or saved.get("winner_config_id") is not None
        ):
            raise ValueError("Rejected portfolio trials cannot have a winning report")

    if observe:
        observe({"kind": "search_started", "proposal_budget": budget, "replayed": len(trials)})
    notify()

    while len(trials) < budget:
        if progress:
            progress(len(trials), budget)
        notify(active_trial=len(trials) + 1)
        execute()
        if checkpoint:
            payload = {
                "binding": binding,
                "optimizer": optimizer,
                "rows": list(rows.values()),
                "trials": trials,
                "winner_report": winner_report,
                "winner_config_id": winner_row["config_id"] if winner_row else None,
                "analysis_catalog": list(analysis_catalog.values()),
            }
            payload["checkpoint_id"] = _fingerprint(payload, max_bytes=MAX_CHECKPOINT_BYTES)
            # The callback receives a stable snapshot, not lists that future
            # proposals mutate behind an asynchronous persistence boundary.
            checkpoint(
                copy.deepcopy(payload),
                {"stage": "search", "completed": len(trials), "total": budget},
            )
        notify()
        if progress:
            progress(len(trials), budget)
    if winner_row is None or not isinstance(winner_report, dict):
        raise ValueError(
            "No feasible portfolio was evaluated within this search budget; narrow allocation ranges or increase trials"
        )

    ranked = sorted(rows.values(), key=_rank)
    return {
        **winner_report,
        "experiment": {
            "kind": "portfolio_optimize",
            "experiment_version": ADAPTER_VERSION,
            "optimizer": optimizer,
            "specification": spec,
            "search_space": plan,
            "counts": {
                "grid": plan["grid_size"],
                "proposed": len(trials),
                "evaluated_this_pass": len(rows),
                "evaluated_all_passes": len(rows),
                "rejected_allocations": sum(trial["state"] == "pruned" for trial in trials),
                "reused_trials": sum(trial["reused"] for trial in trials),
                "remaining": plan["grid_size"] - len({trial["config_id"] for trial in trials}),
            },
            "recommendation_id": winner_row["config_id"],
            "rows": ranked,
            "analysis_catalog": copy.deepcopy(list(analysis_catalog.values())),
            "pass_rows": list(rows.values()),
            "trials": trials,
            "selected_reports": {winner_row["config_id"]: winner_report},
            "selected_strategies": winner_row["strategies"],
            "findings": [
                "Ranked on the supplied historical sample; no independent validation period has been evaluated."
            ],
            "next_action": "Test the selected portfolio on an untouched later period",
        },
    }
