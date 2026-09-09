"""Bounded search and chronological research on the one execution evaluator.

This module owns findings and exact candidate identity. Persistence supplies
verified checkpoints through callbacks; no files, brokers or web state live here.
"""

import hashlib
import itertools
import json
import math
from decimal import Decimal
from statistics import median
from uuid import UUID

from research.engine import (
    POLICY_VERSION,
    TRIGGER_WARMUP_SESSIONS,
    evaluate,
    policy_for_snapshot,
    prepare_evaluation,
    validate_config,
)

AXES = ("target_pct", "stop_pct", "hold_sessions", "trailing_pct")
FILTERS = ("Bottom Fishing", "Zero Only", "Uptick")
STRATEGIES = [["Bypass"]] + [
    list(group) for n in range(1, 4) for group in itertools.combinations(FILTERS, n)
]
MAX_CONFIGURATIONS = 50000
MAX_BUDGET = 5000
EXPERIMENT_VERSION = "scanner-experiments-v2"


def identity(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def axis_values(axis):
    if not isinstance(axis, dict) or set(axis) != {"min", "max", "step"}:
        raise ValueError("Each axis requires min, max and step")
    try:
        low, high, step = (Decimal(str(axis[key])) for key in ("min", "max", "step"))
        if not all(v.is_finite() for v in (low, high, step)) or step <= 0 or low > high:
            raise ValueError("Axes need finite ordered bounds and a positive step")
        count = int((high - low) // step) + 1
        if count > 1000:
            raise ValueError("An axis may contain at most 1000 points")
        return [float(low + i * step) for i in range(count)]
    except (ArithmeticError, TypeError) as error:
        raise ValueError("Invalid numeric axis") from error


def validate_search(spec, config, *, max_budget=MAX_BUDGET):
    if not isinstance(spec, dict):
        raise ValueError("Search specification must be an object")
    allowed = {
        "mode",
        "axes",
        "mode_strategies",
        "budget",
        "rank_by",
        "include_trailing_off",
        "exclude_indices",
        "parent_job_id",
        "trailing_choices",
        "experiment_version",
        "hold_axis",
    }
    if set(spec) - allowed:
        raise ValueError("Unknown search setting")
    if spec.get("experiment_version", EXPERIMENT_VERSION) != EXPERIMENT_VERSION:
        raise ValueError("Search experiment version changed; start a new investigation")
    mode = spec.get("mode", "quick")
    if mode not in ("quick", "full", "exhaustive", "auto"):
        raise ValueError("Choose quick, full, exhaustive or auto search")
    axes = spec.get("axes", {})
    hold_axis = spec.get("hold_axis", "hold_minutes" if "hold_minutes" in axes else "hold_sessions")
    if hold_axis not in ("hold_sessions", "hold_minutes"):
        raise ValueError("Unknown holding axis")
    own_axes = tuple(hold_axis if key == "hold_sessions" else key for key in AXES)
    if not isinstance(axes, dict) or set(axes) - set(own_axes):
        raise ValueError("Search axes are target, stop, hold and trailing")
    canonical = {}
    for key in own_axes:
        default = config.get(key)
        if key not in axes and default is None:
            raise ValueError("Minute holding search needs an explicit bounded range")
        axis = axes.get(key, {"min": default, "max": default, "step": 1})
        values = axis_values(axis)
        for number in values:
            validate_config({**config, key: number})
        canonical[key] = {part: float(axis[part]) for part in ("min", "max", "step")}
    strategies = spec.get("mode_strategies", [config["modes"]])
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= 8:
        raise ValueError("Choose one to eight meaningful trigger strategies")
    strategies = [validate_config({**config, "modes": group})["modes"] for group in strategies]
    if len({tuple(group) for group in strategies}) != len(strategies):
        raise ValueError(
            "Duplicate trigger strategies, including Bypass combinations, are not allowed"
        )
    strategies.sort(key=tuple)
    include_off = spec.get("include_trailing_off", True)
    if not isinstance(include_off, bool):
        raise ValueError("include_trailing_off must be boolean")
    choices = spec.get("trailing_choices")
    if choices is None:
        if "trailing_pct" not in axes:
            choices = [{"pct": config["trailing_pct"], "enabled": config["trailing_enabled"]}]
        else:
            enabled_zero = config["trailing_enabled"] and config["trailing_pct"] == 0
            choices = [
                {"pct": value, "enabled": value != 0 or enabled_zero}
                for value in axis_values(canonical["trailing_pct"])
            ]
    if not isinstance(choices, list) or not choices or len(choices) > 1001:
        raise ValueError("Choose bounded explicit trailing state/value pairs")
    pairs = set()
    for choice in choices:
        if not isinstance(choice, dict) or set(choice) != {"enabled", "pct"}:
            raise ValueError("A trailing choice requires enabled and pct")
        validate_config(
            {**config, "trailing_enabled": choice["enabled"], "trailing_pct": choice["pct"]}
        )
        if not include_off and not choice["enabled"]:
            raise ValueError("Disabled trailing choice contradicts include_trailing_off=false")
        pairs.add((choice["enabled"], choice["pct"]))
    if include_off:
        pairs.add((False, 0))
    if len(pairs) > 1001:
        raise ValueError("At most 1001 explicit trailing state/value choices are supported")
    result = {
        "mode": mode,
        "axes": canonical,
        "mode_strategies": strategies,
        "include_trailing_off": include_off,
        "rank_by": spec.get("rank_by", "balance"),
        "trailing_choices": [{"enabled": enabled, "pct": pct} for enabled, pct in sorted(pairs)],
        "experiment_version": EXPERIMENT_VERSION,
    }
    if hold_axis == "hold_minutes":
        result["hold_axis"] = hold_axis
    if result["rank_by"] not in ("return", "drawdown", "balance"):
        raise ValueError("Rank by return, drawdown or balance")
    dimensions = grid_dimensions(result)
    total = math.prod(len(d) for d in dimensions)
    if total > MAX_CONFIGURATIONS:
        raise ValueError(f"Grid has {total} settings; maximum is {MAX_CONFIGURATIONS}")
    excluded = spec.get("exclude_indices", [])
    if (
        not isinstance(excluded, list)
        or len(excluded) > MAX_CONFIGURATIONS
        or any(
            isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < total for i in excluded
        )
    ):
        raise ValueError("Invalid previously evaluated grid indices")
    result["exclude_indices"] = sorted(set(excluded))
    if "parent_job_id" in spec:
        parent = spec["parent_job_id"]
        if not isinstance(parent, str):
            raise ValueError("parent_job_id must identify a recorded search job")
        try:
            result["parent_job_id"] = UUID(parent).hex
        except ValueError as error:
            raise ValueError("parent_job_id must be a valid job identifier") from error
    available = total - len(result["exclude_indices"])
    if not available:
        raise ValueError("Every configuration in these boundaries was already tested")
    default = {"quick": 25, "full": 250, "auto": 100, "exhaustive": available}[mode]
    budget = spec.get("budget", min(default, available))
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= max_budget:
        raise ValueError(f"Search budget must be 1–{max_budget}")
    if mode == "exhaustive" and (available > max_budget or budget < available):
        raise ValueError(
            "Exhaustive mode must cover the full remaining grid within the worker budget"
        )
    result["budget"] = min(budget, available)
    return result


def grid_dimensions(spec):
    dimensions = [
        axis_values(spec["axes"][spec.get("hold_axis", key) if key == "hold_sessions" else key])
        for key in AXES
    ]
    trailing = [(choice["pct"], choice["enabled"]) for choice in spec["trailing_choices"]]
    dimensions[3] = trailing
    return dimensions + [spec["mode_strategies"]]


def coordinates(index, dimensions):
    result = []
    for dimension in reversed(dimensions):
        result.append(index % len(dimension))
        index //= len(dimension)
    return list(reversed(result))


def grid_config(index, spec, base):
    dimensions = grid_dimensions(spec)
    point = [
        dimension[coordinate]
        for dimension, coordinate in zip(dimensions, coordinates(index, dimensions), strict=True)
    ]
    return validate_config(
        {
            **base,
            "target_pct": point[0],
            "stop_pct": point[1],
            spec.get("hold_axis", "hold_sessions"): point[2],
            "trailing_pct": point[3][0],
            "trailing_enabled": point[3][1],
            "modes": point[4],
        }
    )


def score(summary, rank_by):
    if rank_by == "drawdown":
        return -summary["max_drawdown_pct"]
    if rank_by == "return":
        return summary["net_return_pct"]
    return summary["net_return_pct"] - summary["max_drawdown_pct"]


def evenly(indices, count):
    if len(indices) <= count:
        return indices
    if count == 1:
        return [indices[len(indices) // 2]]
    return [indices[round(i * (len(indices) - 1) / (count - 1))] for i in range(count)]


def numeric_distance(left_index, right_index, dimensions):
    """Numerical axis steps only, within one trigger strategy and trailing state."""
    left, right = coordinates(left_index, dimensions), coordinates(right_index, dimensions)
    left_trail, right_trail = dimensions[3][left[3]], dimensions[3][right[3]]
    if dimensions[4][left[4]] != dimensions[4][right[4]] or left_trail[1] != right_trail[1]:
        return math.inf
    distance = sum(abs(a - b) for a, b in zip(left[:3], right[:3], strict=True))
    if left_trail[1]:
        magnitudes = sorted({value for value, enabled in dimensions[3] if enabled})
        distance += abs(magnitudes.index(left_trail[0]) - magnitudes.index(right_trail[0]))
    return distance


def extend_search_plan(spec, dimensions, available, rows, plan, incumbent_rows=()):
    """One deterministic broad/focus planner for search and causal selection.

    Only already evaluated rows choose focused work. Replaying recorded rows
    reconstructs the same remaining plan after a checkpoint interruption.
    """
    broad_count = max(1, spec["budget"] // 2) if spec["mode"] == "auto" else spec["budget"]
    if not plan:
        return evenly(available, broad_count)
    if len(rows) < len(plan):
        return plan
    leaders = sorted([*incumbent_rows, *rows], key=lambda row: (-row["score"], row["config_id"]))[
        :5
    ]
    tested = {row["grid_index"] for row in rows}
    candidates = [index for index in available if index not in tested]
    candidates.sort(
        key=lambda index: (
            min(numeric_distance(index, row["grid_index"], dimensions) for row in leaders),
            index,
        )
    )
    return plan + candidates[: spec["budget"] - len(rows)]


def search_preflight(spec, config):
    spec = validate_search(spec, config)
    total = math.prod(len(d) for d in grid_dimensions(spec))
    return {
        "specification": spec,
        "grid_count": total,
        "previously_tested": len(spec["exclude_indices"]),
        "planned_evaluations": spec["budget"],
        "policy_version": POLICY_VERSION,
        "experiment_version": EXPERIMENT_VERSION,
        "ranking_definition": {
            "return": "Net marked portfolio return (%)",
            "drawdown": "Lowest daily drawdown (%)",
            "balance": "Net return minus maximum drawdown (percentage points)",
        }[spec["rank_by"]],
    }


def validate_specification(kind, spec, config, evidence):
    if kind == "backtest":
        if spec:
            raise ValueError("Backtest does not accept an experiment specification")
        return {}
    if kind == "optimize":
        return validate_search(spec, config)
    if kind == "research":
        return validate_research(spec, config, evidence)
    if kind == "sensitivity":
        if not isinstance(spec, dict) or set(spec) - {"variants"}:
            raise ValueError("Sensitivity accepts only variants")
        return {"variants": validate_variants(spec.get("variants", []), config)}
    raise ValueError("Choose backtest, optimize, research or sensitivity")


def run_search(
    signals,
    snapshot,
    base,
    spec,
    *,
    progress=None,
    checkpoint=None,
    saved=None,
    parent_rows=None,
    parent_reports=None,
):
    spec = validate_search(spec, base)
    prepared = prepare_evaluation(signals, snapshot)
    dimensions = grid_dimensions(spec)
    total = math.prod(len(d) for d in dimensions)
    excluded = set(spec["exclude_indices"])
    available = [i for i in range(total) if i not in excluded]
    if parent_rows is not None and (
        not isinstance(parent_rows, (list, tuple)) or len(parent_rows) > MAX_CONFIGURATIONS
    ):
        raise ValueError("Parent evidence must be a bounded list of configuration rows")
    inherited = {}
    for row in parent_rows or []:
        index = row.get("grid_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < total:
            raise ValueError("Parent evidence has an invalid grid index")
        expected = grid_config(index, spec, base)
        if row.get("config") != expected or row.get("config_id") != identity(expected):
            raise ValueError("Parent configuration identity does not match this grid")
        if row.get("score") != score(row["summary"], spec["rank_by"]):
            raise ValueError("Parent evidence uses a different ranking definition")
        if index in inherited and inherited[index] != row:
            raise ValueError("Conflicting parent evidence for one configuration")
        inherited[index] = row
    if set(inherited) != excluded:
        raise ValueError("Follow-up exclusions require the complete verified parent evidence")
    inherited_rows = [inherited[index] for index in sorted(inherited)]
    parent_fingerprint = identity(inherited_rows)
    if saved and (
        saved.get("parent_evidence_id") != parent_fingerprint
        or saved.get("experiment_version") != EXPERIMENT_VERSION
    ):
        raise ValueError("Checkpoint parent evidence or experiment semantics changed")
    rows = list((saved or {}).get("rows", []))
    plan = list((saved or {}).get("plan", []))
    if (
        len(rows) > spec["budget"]
        or len({row["grid_index"] for row in rows}) != len(rows)
        or any(row["grid_index"] in excluded for row in rows)
    ):
        raise ValueError("Checkpoint repeats previously tested configurations")
    if (
        len(plan) > spec["budget"]
        or len(set(plan)) != len(plan)
        or any(index not in available for index in plan)
    ):
        raise ValueError("Checkpoint has an invalid remaining search plan")
    for position, row in enumerate(rows):
        expected = grid_config(row["grid_index"], spec, base)
        if (
            position >= len(plan)
            or plan[position] != row["grid_index"]
            or row.get("config") != expected
            or row.get("config_id") != identity(expected)
            or row.get("score") != score(row["summary"], spec["rank_by"])
        ):
            raise ValueError("Checkpoint configuration evidence does not match its search plan")
    broad_count = max(1, spec["budget"] // 2) if spec["mode"] == "auto" else spec["budget"]
    while len(rows) < spec["budget"]:
        plan = extend_search_plan(spec, dimensions, available, rows, plan, inherited_rows)
        index = plan[len(rows)]
        config = grid_config(index, spec, base)

        def beat(done, count):
            if progress:
                progress(len(rows) + done / max(1, count), spec["budget"])

        report = evaluate(
            signals, snapshot, config, prepared=prepared, summary_only=True, progress=beat
        )
        rows.append(
            {
                "grid_index": index,
                "config_id": identity(config),
                "config": config,
                "summary": report["summary"],
                "score": score(report["summary"], spec["rank_by"]),
                "stage": "broad" if len(rows) < broad_count else "focused",
            }
        )
        if checkpoint and (len(rows) % 5 == 0 or len(rows) == spec["budget"]):
            checkpoint(
                {
                    "rows": rows,
                    "plan": plan,
                    "parent_evidence_id": parent_fingerprint,
                    "experiment_version": EXPERIMENT_VERSION,
                },
                {"stage": "search", "completed": len(rows), "total": spec["budget"]},
            )
    cumulative = [*inherited_rows, *rows]
    ranked = sorted(cumulative, key=lambda row: (-row["score"], row["config_id"]))
    winner = ranked[0]
    alternatives = []
    for candidate in (
        min(
            cumulative,
            key=lambda row: (row["summary"]["max_drawdown_pct"], -row["score"], row["config_id"]),
        ),
        min(
            cumulative,
            key=lambda row: (
                -(row["summary"]["win_rate_pct"] or 0),
                -row["score"],
                row["config_id"],
            ),
        ),
    ):
        if candidate["config_id"] != winner["config_id"] and candidate not in alternatives:
            alternatives.append(candidate)
    chosen = [winner] + alternatives
    reports = {}
    for row in chosen:
        if row["grid_index"] in inherited:
            report = (parent_reports or {}).get(row["config_id"])
            if (
                report is None
                or report.get("config") != row["config"]
                or report.get("summary") != row["summary"]
                or report.get("policy_version") != policy_for_snapshot(snapshot)
            ):
                raise ValueError("Selected incumbent requires its exact verified parent report")
            reports[row["config_id"]] = report
            continue
        reports[row["config_id"]] = evaluate(
            signals,
            snapshot,
            row["config"],
            prepared=prepared,
            progress=lambda done, count: (
                progress(spec["budget"], spec["budget"]) if progress else None
            ),
        )
    neighborhoods = []
    for row in chosen:
        # Inactive trailing magnitudes are identity metadata, not independent
        # numerical neighbors. Collapse that coordinate for neighborhood counts.
        distinct = {}
        for candidate in cumulative:
            if numeric_distance(candidate["grid_index"], row["grid_index"], dimensions) <= 1:
                cfg = candidate["config"]
                key = (
                    cfg["target_pct"],
                    cfg["stop_pct"],
                    cfg[spec.get("hold_axis", "hold_sessions")],
                    cfg["trailing_pct"] if cfg["trailing_enabled"] else 0,
                )
                previous = distinct.get(key)
                if previous is None or candidate["config_id"] < previous["config_id"]:
                    distinct[key] = candidate
        nearby = sorted(distinct.values(), key=lambda candidate: candidate["config_id"])
        share = sum(r["summary"]["net_return_pct"] > 0 for r in nearby) / len(nearby)
        neighborhoods.append(
            {
                "config_id": row["config_id"],
                "tested_count": len(nearby),
                "neighbor_config_ids": [candidate["config_id"] for candidate in nearby],
                "basis": "Numeric parameter steps within the same trigger strategy and trailing state",
                "profitable_share_pct": share * 100,
                "median_return_pct": median(r["summary"]["net_return_pct"] for r in nearby),
                "median_drawdown_pct": median(r["summary"]["max_drawdown_pct"] for r in nearby),
                "finding": "Needs more neighboring samples"
                if len(nearby) < 3
                else "At least 60% of tested numeric neighbors were profitable"
                if share >= 0.6
                else "Fewer than 60% of tested numeric neighbors were profitable",
            }
        )
    enough = len(signals) >= 30 and winner["summary"]["closed_trades"] >= 30
    verdict = (
        "final_insufficient_evidence"
        if not enough
        else "final_no_reliable_edge"
        if winner["summary"]["net_return_pct"] <= 0
        else "final_useful_result"
    )
    untested = total - len(cumulative)
    follow_up = None
    if untested:
        follow_up = {
            **spec,
            "exclude_indices": sorted(spec["exclude_indices"] + [r["grid_index"] for r in rows]),
            "budget": min(spec["budget"], untested),
        }
    findings = [
        "This search pass completed. Recommendation and neighborhood findings use the cumulative tested investigation; ranking is historical selection, not an independent holdout.",
        "Fewer than 30 completed trades/signals is insufficient sample; more settings do not add independent observations."
        if not enough
        else "Historical sample available; test the exact choice on a later period before making an inference.",
        "Neighborhood statistics use all evaluated rows, independent of displayed filters.",
    ]
    report = reports[winner["config_id"]]
    return {
        **report,
        "experiment": {
            "kind": "optimize",
            "experiment_version": EXPERIMENT_VERSION,
            "parent_evidence_id": parent_fingerprint,
            "state": verdict,
            "specification": spec,
            "counts": {
                "grid": total,
                "evaluated_this_pass": len(rows),
                "evaluated_all_passes": len(cumulative),
                "inherited_rows": len(inherited_rows),
                "broad": broad_count,
                "stored_rows": len(cumulative),
                "stored_pass_rows": len(rows),
                "remaining": untested,
            },
            "recommendation_id": winner["config_id"],
            "alternatives": [r["config_id"] for r in alternatives],
            "rows": ranked,
            "pass_rows": rows,
            "pass_recommendation_id": min(rows, key=lambda row: (-row["score"], row["config_id"]))[
                "config_id"
            ],
            "selected_reports": reports,
            "neighborhoods": neighborhoods,
            "categorical_comparisons": [
                {
                    "anchor_config_id": row["config_id"],
                    "compared_config_ids": sorted(
                        candidate["config_id"]
                        for candidate in cumulative
                        if candidate["config_id"] != row["config_id"]
                        and all(candidate["config"][key] == row["config"][key] for key in AXES[:3])
                        and (
                            candidate["config"]["modes"] != row["config"]["modes"]
                            or candidate["config"]["trailing_enabled"]
                            != row["config"]["trailing_enabled"]
                        )
                    ),
                    "basis": "Other trigger/trailing states at the same target, stop and hold; not numeric neighborhood evidence",
                }
                for row in chosen
            ],
            "findings": findings,
            "next_action": "Inspect remaining untested settings or stop this investigation"
            if untested
            else "Collect more independent dated signals"
            if not enough
            else "Reconsider the scanner or setup; no positive marked return was found"
            if winner["summary"]["net_return_pct"] <= 0
            else "Test the exact setting later",
            "follow_up": follow_up,
        },
    }


def validate_variants(variants, config):
    if not isinstance(variants, list) or len(variants) > 5:
        raise ValueError("At most five sensitivity variants are supported")
    if not variants:
        variants = [
            {"name": "Double fees", "changes": {"cost_bps": min(500, config["cost_bps"] * 2)}},
            {
                "name": "Higher slippage",
                "changes": {"slippage_bps": min(500, config["slippage_bps"] + 10)},
            },
            {"name": "Reverse signal priority", "changes": {"entry_priority": "reversed"}},
        ]
    output = []
    allowed = {
        "cost_bps",
        "slippage_bps",
        "entry_priority",
        "priority_seed",
        "target_pct",
        "stop_pct",
        "hold_sessions",
        "hold_minutes",
        "trade_horizon",
        "entry_time",
        "exit_time",
    }
    for variant in variants:
        if (
            not isinstance(variant, dict)
            or set(variant) != {"name", "changes"}
            or not isinstance(variant["name"], str)
            or not 1 <= len(variant["name"]) <= 80
        ):
            raise ValueError("Sensitivity variants require a short name and changes")
        if not isinstance(variant["changes"], dict) or set(variant["changes"]) - allowed:
            raise ValueError(
                "Sensitivity may change costs, slippage, priority, target, stop or hold"
            )
        validate_config({**config, **variant["changes"]})
        output.append(variant)
    return output


def validate_research(spec, config, evidence):
    allowed = {
        "intent",
        "scheme",
        "train_end",
        "test_end",
        "gap_sessions",
        "folds",
        "min_train_closed",
        "search",
        "variants",
        "prior_explored",
    }
    if not isinstance(spec, dict) or set(spec) - allowed:
        raise ValueError("Unknown chronological research setting")
    result = {
        "intent": spec.get("intent", "fixed_setup"),
        "scheme": spec.get("scheme", "holdout"),
        "train_end": spec.get("train_end"),
        "test_end": spec.get("test_end", evidence["snapshot"]["sessions"][-1]),
        "gap_sessions": spec.get("gap_sessions", 5),
        "folds": spec.get("folds", 1),
        "min_train_closed": spec.get("min_train_closed", 10),
        "prior_explored": spec.get("prior_explored", True),
        "variants": validate_variants(spec.get("variants", []), config),
    }
    if result["intent"] not in ("fixed_setup", "select_earlier") or result["scheme"] not in (
        "holdout",
        "walk_forward",
    ):
        raise ValueError(
            "Choose fixed-setup or earlier-selection intent and holdout or walk-forward"
        )
    for key, maximum in (("folds", 5), ("gap_sessions", 252), ("min_train_closed", 25000)):
        number = result[key]
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not (1 if key == "folds" else 0) <= number <= maximum
        ):
            raise ValueError(f"Invalid {key}")
    if not isinstance(result["prior_explored"], bool):
        raise ValueError("prior_explored must be boolean")
    if result["scheme"] == "holdout" and result["folds"] != 1:
        raise ValueError("Holdout has exactly one later window")
    if result["intent"] == "select_earlier":
        result["search"] = validate_search(spec.get("search", {}), config, max_budget=1000)
        if result["search"]["exclude_indices"] or "parent_job_id" in result["search"]:
            raise ValueError(
                "Earlier selection requires a fresh training search, not exclusions from another investigation"
            )
    elif "search" in spec:
        raise ValueError("Fixed-setup intent does not select parameters")
    research_windows(evidence["signals"], evidence["snapshot"], result)
    return result


def research_windows(signals, snapshot, spec):
    sessions = snapshot["sessions"]
    session_set = set(sessions)
    if spec["train_end"] not in sessions or spec["test_end"] not in sessions:
        raise ValueError("Training and testing cutoffs must be recorded exchange sessions")
    train_index = sessions.index(spec["train_end"])
    start = train_index + 1 + spec["gap_sessions"]
    end = sessions.index(spec["test_end"])
    count = end - start + 1
    if count < spec["folds"] * 2 or not any(s["date"] <= spec["train_end"] for s in signals):
        raise ValueError(
            "Choose an earlier period with signals and at least two later sessions per fold"
        )
    windows = []
    for fold in range(spec["folds"]):
        first = start + count * fold // spec["folds"]
        last = start + count * (fold + 1) // spec["folds"] - 1
        training = first - spec["gap_sessions"] - 1
        potential = sum(
            s["date"] in session_set and sessions[first] <= s["date"] < sessions[last]
            for s in signals
        )
        if not potential:
            raise ValueError(f"Later window {fold + 1} has no possible next-session entries")
        windows.append(
            {
                "fold": fold + 1,
                "train_from": sessions[0],
                "train_end": sessions[training],
                "gap_from": sessions[training + 1] if spec["gap_sessions"] else None,
                "gap_to": sessions[first - 1] if spec["gap_sessions"] else None,
                "test_from": sessions[first],
                "test_end": sessions[last],
                "train_sessions": training + 1,
                "gap_sessions": spec["gap_sessions"],
                "test_sessions": last - first + 1,
                "training_signals": sum(s["date"] <= sessions[training] for s in signals),
                "possible_entries": potential,
            }
        )
    return windows


def run_sensitivity(signals, snapshot, config, spec, *, progress=None):
    prepared = prepare_evaluation(signals, snapshot)
    total = 1 + len(spec["variants"])
    baseline = evaluate(
        signals,
        snapshot,
        config,
        prepared=prepared,
        progress=lambda done, count: progress(done / max(1, count), total) if progress else None,
    )
    variants = []
    for i, variant in enumerate(spec["variants"]):
        cfg = validate_config({**config, **variant["changes"]})
        report = evaluate(
            signals,
            snapshot,
            cfg,
            prepared=prepared,
            progress=lambda done, count, index=i: (
                progress(1 + index + done / max(1, count), total) if progress else None
            ),
        )
        variants.append(
            {
                **variant,
                "config_id": identity(cfg),
                "report": report,
                "return_change_pp": report["summary"]["net_return_pct"]
                - baseline["summary"]["net_return_pct"],
            }
        )
    return {
        **baseline,
        "experiment": {
            "kind": "sensitivity",
            "variants": variants,
            "findings": [
                "Each variant changes an assumption around the fixed baseline; no replacement winner was selected.",
                "Sensitivity describes fragility on the same explored observations, not independent evidence.",
            ],
        },
    }


def run_research(signals, snapshot, config, spec, *, progress=None, checkpoint=None, saved=None):
    from research.data import cutoff_snapshot

    if saved and saved.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("Research checkpoint experiment version changed")
    windows = research_windows(signals, snapshot, spec)
    records = dict((saved or {}).get("records", {}))
    folds = list((saved or {}).get("folds", []))
    training_count = spec.get("search", {}).get("budget", 0)
    total = len(windows) * (training_count + 1 + len(spec["variants"]))
    completed = len(records)
    prepared_cache = {}

    def calculate(key, candidates, prices, cfg, history, summary_only=False):
        nonlocal completed
        if key not in records:
            cache_key = "train" if summary_only else "test"
            if cache_key not in prepared_cache:
                prepared_cache[cache_key] = prepare_evaluation(
                    candidates, prices, signal_history=history
                )
            report = evaluate(
                candidates,
                prices,
                cfg,
                prepared=prepared_cache[cache_key],
                summary_only=summary_only,
                progress=lambda done, count: (
                    progress(completed + done / max(1, count), total) if progress else None
                ),
            )
            records[key] = (
                {"summary": report["summary"], "config": report["config"]}
                if summary_only
                else report
            )
            completed += 1
            if checkpoint and (completed % 5 == 0 or not summary_only):
                checkpoint(
                    {"records": records, "folds": folds, "experiment_version": EXPERIMENT_VERSION},
                    {"stage": "chronological research", "completed": completed, "total": total},
                )
        return records[key]

    for window in windows[len(folds) :]:
        prepared_cache.clear()
        number = window["fold"]
        training_prices = cutoff_snapshot(snapshot, window["train_end"])
        earlier = [s for s in signals if s["date"] <= window["train_end"]]
        selected, ranks = config, []
        if spec["intent"] == "select_earlier":
            search = spec["search"]
            dimensions = grid_dimensions(search)
            count = math.prod(len(d) for d in dimensions)
            excluded = set(search["exclude_indices"])
            available = [i for i in range(count) if i not in excluded]
            plan = []
            broad_count = (
                max(1, search["budget"] // 2) if search["mode"] == "auto" else search["budget"]
            )
            while len(ranks) < search["budget"]:
                plan = extend_search_plan(search, dimensions, available, ranks, plan)
                index = plan[len(ranks)]
                cfg = grid_config(index, search, config)
                report = calculate(
                    f"{number}:train:{index}", earlier, training_prices, cfg, earlier, True
                )
                ranks.append(
                    {
                        "grid_index": index,
                        "config_id": identity(cfg),
                        "config": cfg,
                        "summary": report["summary"],
                        "score": score(report["summary"], search["rank_by"]),
                        "stage": "broad" if len(ranks) < broad_count else "focused",
                    }
                )
            ranks.sort(key=lambda row: (-row["score"], row["config_id"]))
            eligible = [
                r for r in ranks if r["summary"]["closed_trades"] >= spec["min_train_closed"]
            ]
            selected = eligible[0]["config"] if eligible else None
        if selected is None:
            folds.append(
                {
                    "window": window,
                    "training_ranks": ranks,
                    "selected_config": None,
                    "test_report": None,
                    "sensitivities": [],
                    "finding": "Insufficient earlier completed trades; no winner or later result invented",
                }
            )
        else:
            later_prices = cutoff_snapshot(snapshot, window["test_end"])
            candidates = [
                s for s in signals if window["test_from"] <= s["date"] <= window["test_end"]
            ]
            history = [s for s in signals if s["date"] <= window["test_end"]]
            first_index = later_prices["sessions"].index(window["test_from"])
            later_prices["sessions"] = later_prices["sessions"][
                max(0, first_index - TRIGGER_WARMUP_SESSIONS) :
            ]
            allowed = set(later_prices["sessions"])
            later_prices["bars"] = {
                symbol: {day: bar for day, bar in series.items() if day[:10] in allowed}
                for symbol, series in later_prices["bars"].items()
            }
            if "timeline" in later_prices:
                later_prices["timeline"] = [
                    stamp for stamp in later_prices["timeline"] if stamp[:10] in allowed
                ]
            if "required_timestamps" in later_prices:
                later_prices["required_timestamps"] = {
                    symbol: [stamp for stamp in slots if stamp[:10] in allowed]
                    for symbol, slots in later_prices["required_timestamps"].items()
                }
            if "required_dates" in later_prices:
                later_prices["required_dates"] = {
                    symbol: [day for day in days if day in allowed]
                    for symbol, days in later_prices["required_dates"].items()
                }
            later_prices.pop("coverage", None)
            report = calculate(f"{number}:test", candidates, later_prices, selected, history)
            variants = []
            for i, variant in enumerate(spec["variants"]):
                cfg = validate_config({**selected, **variant["changes"]})
                changed = calculate(
                    f"{number}:sensitivity:{i}", candidates, later_prices, cfg, history
                )
                variants.append(
                    {
                        **variant,
                        "report": changed,
                        "return_change_pp": changed["summary"]["net_return_pct"]
                        - report["summary"]["net_return_pct"],
                    }
                )
            folds.append(
                {
                    "window": window,
                    "training_ranks": ranks,
                    "selected_config": selected,
                    "test_report": report,
                    "sensitivities": variants,
                    "finding": report["summary"]["sample_adequacy"],
                }
            )
        if checkpoint:
            checkpoint(
                {"records": records, "folds": folds, "experiment_version": EXPERIMENT_VERSION},
                {"stage": "fold completed", "completed": len(records), "total": total},
            )
    reports = [fold["test_report"] for fold in folds if fold["test_report"]]
    return {
        "config": config,
        "policy_version": policy_for_snapshot(snapshot),
        "metric_basis": "independent_fold_minute_marked"
        if "timeline" in snapshot
        else "independent_fold_daily_marked",
        "summary": {
            "evaluated_folds": len(reports),
            "closed_trades": sum(r["summary"]["closed_trades"] for r in reports),
            "mean_fold_return_pct": sum(r["summary"]["net_return_pct"] for r in reports)
            / len(reports)
            if reports
            else None,
        },
        "equity_curve": [],
        "ledger": [],
        "coverage": snapshot.get("coverage", {}),
        "limits": [
            "Every fold starts with fresh capital; the fold curves are not one funded continuous account.",
            "CSV history does not establish point-in-time scanner membership or absence of selection bias.",
        ],
        "experiment": {
            "kind": "research",
            "experiment_version": EXPERIMENT_VERSION,
            "trigger_warmup_sessions": TRIGGER_WARMUP_SESSIONS,
            "intent": spec["intent"],
            "specification": spec,
            "folds": folds,
            "findings": [
                "Earlier selection uses only signals and adjustment evidence available at each cutoff."
                if spec["intent"] == "select_earlier"
                else "The supplied setup was held fixed; earlier eligibility thresholds did not reselect or reject it.",
                "Later windows are disjoint; sensitivity does not select a replacement winner.",
            ],
        },
    }
