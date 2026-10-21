"""Bounded development-only recommendation checks over actual account histories.

Circular blocks resample paired account-day returns, keeping each shared-cash
account and the candidate/baseline dates together. The percentile ranges describe
this observed pair under short-range stationary dependence; they are not calibrated
confidence, a probability of profit, or an adjustment for adaptive search. They
never create executable trades. No final-period history is accepted here.
"""

from __future__ import annotations

import math
import random
from copy import deepcopy
from statistics import median

from research.development_evidence import build, verify_shard
from research.report_contract import fingerprint

VERSION = "research-recommendation-evidence-v1"
MIN_DAYS = 60
MAX_DAYS = 4096
MAX_MARKS = 100_000
RESAMPLES = 512
SEED = 0
BLOCK_LENGTHS = (5, 10)
MAX_NEIGHBOURS = 4
MIN_NEIGHBOURS = 2
METHOD_SOURCE = "https://stat.ethz.ch/R-manual/R-patched/library/boot/html/tsboot.html"
DEPENDENCE_SOURCE = "https://www.stat.cmu.edu/~cshalizi/uADA/16/lectures/26.pdf"
LIMITATION = (
    "Conditional descriptive paired-account ranges; approximate short-range "
    "stationarity is assumed, longer dependence and regime shifts may remain. "
    "Not search-adjusted confidence, a probability of future profit or a universal optimum."
)


def account_evidence(report, *, period, binding, stress=False):
    """Build the existing verifiable shard in memory; persistence stays with caller."""
    if period not in ("search", "check1", "check2", "check3"):
        raise ValueError("Recommendation evidence cannot include final-period accounts")
    timeline = [point.get("timestamp", point.get("date")) for point in report["equity_curve"]]
    hypothesis = report.get("automatic_stock_condition", {}).get("hypothesis", {"id": "none"})
    return build(
        report,
        evaluation_id=fingerprint(
            {
                "settings": report["strategies"],
                "hypothesis": hypothesis,
                "period": period,
                "stress": stress,
            }
        ),
        binding=binding,
        settings=report["strategies"],
        hypothesis=hypothesis,
        period=period,
        stress=stress,
        expected_timeline=timeline,
    )


def _verified(shard, period, stress):
    if (
        not isinstance(shard, dict)
        or shard.get("period") != period
        or shard.get("stress") is not stress
    ):
        raise ValueError("Development period or cost scenario is missing or incompatible")
    if shard.get("evaluation_basis", {}).get("period", {}).get("kind") != "selection":
        raise ValueError("Only selection-period observations qualify")
    if not isinstance(shard.get("timeline"), list) or len(shard["timeline"]) > MAX_MARKS:
        raise ValueError("Recommendation observation limit exceeded")
    descriptor = {
        key: shard.get(key)
        for key in (
            "id",
            "evaluation_id",
            "config_id",
            "binding",
            "period",
            "stress",
            "evaluation_basis_id",
        )
    }
    verify_shard(shard, descriptor=descriptor)
    # Last actual mark per observed session; no interpolation or artificial closes.
    daily = {}
    for point in shard["curve"]:
        daily[point["date"]] = point["equity"]
    if len(daily) > MAX_DAYS:
        raise ValueError("Recommendation daily observation limit exceeded")
    previous, returns = shard["account"]["initial_capital"], []
    for equity in daily.values():
        if previous <= 0:
            raise ValueError("Daily return is unavailable after account equity reached zero")
        value = equity / previous - 1
        if not math.isfinite(value):
            raise ValueError("Daily return is unavailable")
        returns.append(value)
        previous = equity
    curve = shard["curve"]
    total_return = (curve[-1]["equity"] / shard["account"]["initial_capital"] - 1) * 100
    if not math.isfinite(total_return):
        raise ValueError("Derived account return exceeds finite diagnostic bounds")
    return {
        "dates": list(daily),
        "returns": returns,
        "return_pct": total_return,
        "max_drawdown_pct": max(point["drawdown_pct"] for point in curve),
    }


def _comparison(shard, *, declared_conditions=False):
    """Compare verified facts; only an exactly declared gate is a policy axis."""
    value = deepcopy(shard["evaluation_basis"]["comparison"])
    if declared_conditions:
        value.pop("id")
        execution = value.get("execution")
        if not isinstance(execution, dict):
            raise ValueError("Native execution evidence is unavailable")
        condition = execution.pop("entry_conditions", None)
        if condition is not None and condition != {
            "version": "research-joint-entry-conditions-v2",
            "hypothesis": shard["hypothesis"],
        }:
            raise ValueError("Execution condition does not match its declared hypothesis")
    return value


def _pair(candidate, baseline, *, period, stress=False):
    actual, original = _verified(candidate, period, stress), _verified(baseline, period, stress)
    if candidate["timeline"] != baseline["timeline"] or candidate["binding"] != baseline["binding"]:
        raise ValueError("Paired accounts do not share exact observations and frozen inputs")
    if candidate["account"] != baseline["account"]:
        raise ValueError("Paired accounts do not share starting capital and currency")
    left, right = candidate["evaluation_basis"], baseline["evaluation_basis"]
    for name in (
        "source_id",
        "observations_id",
        "prices_id",
        "calendar_id",
        "instruments_id",
    ):
        if left.get(name) != right.get(name):
            raise ValueError(f"Paired accounts have incompatible {name} evidence")
    opportunity = candidate.get("research_opportunity_basis")
    original_opportunity = baseline.get("research_opportunity_basis")
    if opportunity is not None or original_opportunity is not None:
        if not isinstance(opportunity, dict) or opportunity != original_opportunity:
            raise ValueError("Paired accounts have incompatible original opportunity evidence")
    elif left["cohort_id"] != right["cohort_id"]:
        raise ValueError(
            "Different admitted cohorts need the same verified original opportunity basis"
        )
    if _comparison(candidate, declared_conditions=opportunity is not None) != _comparison(
        baseline, declared_conditions=opportunity is not None
    ):
        raise ValueError("Paired accounts have incompatible native execution, costs or limits")
    edge = [a - b for a, b in zip(actual["returns"], original["returns"], strict=True)]
    actual_score = actual["return_pct"] - actual["max_drawdown_pct"]
    original_score = original["return_pct"] - original["max_drawdown_pct"]
    return {
        "period": period,
        "days": len(edge),
        "from": actual["dates"][0],
        "to": actual["dates"][-1],
        "candidate_return_pct": actual["return_pct"],
        "baseline_return_pct": original["return_pct"],
        "return_edge_pct": actual["return_pct"] - original["return_pct"],
        "return_minus_drawdown_edge_pct": actual_score - original_score,
        "candidate_max_drawdown_pct": actual["max_drawdown_pct"],
        "opportunities": {
            "basis_id": fingerprint(opportunity) if opportunity is not None else None,
            "candidate_admission_id": left["cohort_id"],
            "baseline_admission_id": right["cohort_id"],
            "policy": (
                "Same frozen original opportunity pool; actual admissions may differ through declared settings or causal conditions."
                if opportunity is not None
                else "Matching actual admission cohorts; original opportunity attestation unavailable."
            ),
        },
        "passes": actual["return_pct"] > 0
        and actual_score > original_score
        and actual["max_drawdown_pct"] <= 25,
        "_edges": edge,
        "_dates": actual["dates"],
    }


def _stress_pair(candidate, baseline, stress_candidate, stress_baseline, *, period):
    result = _pair(stress_candidate, stress_baseline, period=period, stress=True)
    for normal, stressed in ((candidate, stress_candidate), (baseline, stress_baseline)):
        if (
            normal["binding"] != stressed["binding"]
            or normal["timeline"] != stressed["timeline"]
            or normal["account"] != stressed["account"]
        ):
            raise ValueError("Cost perturbation changed frozen inputs, dates or account basis")
        left, right = normal["evaluation_basis"], stressed["evaluation_basis"]
        for name in (
            "source_id",
            "cohort_id",
            "observations_id",
            "prices_id",
            "calendar_id",
            "instruments_id",
        ):
            if left[name] != right[name]:
                raise ValueError("Cost perturbation changed its observed source")
        a = left["comparison"]
        b = right["comparison"]
        if {key: value for key, value in a.items() if key not in ("id", "costs")} != {
            key: value for key, value in b.items() if key not in ("id", "costs")
        }:
            raise ValueError("Cost perturbation changed execution assumptions")
        normal_costs, stressed_costs = a["costs"], b["costs"]
        if len(normal_costs) != len(stressed_costs) or not normal_costs:
            raise ValueError("Cost perturbation is missing comparable strategy costs")
        for before, after in zip(normal_costs, stressed_costs, strict=True):
            if (
                before["strategy_id"] != after["strategy_id"]
                or before["slippage_bps"] != after["slippage_bps"]
                or after["cost_bps"] < max(2 * before["cost_bps"], before["cost_bps"] + 5)
            ):
                raise ValueError("Cost perturbation is not the declared higher-cost scenario")
        if normal["hypothesis"] != stressed["hypothesis"]:
            raise ValueError("Cost perturbation changed its selected condition")
        if normal.get("research_opportunity_basis") != stressed.get("research_opportunity_basis"):
            raise ValueError("Cost perturbation changed the original opportunity evidence")
        settings = deepcopy(normal["settings"])
        if len(settings) != len(stressed["settings"]):
            raise ValueError("Cost perturbation changed its selected strategies")
        for before, after in zip(settings, stressed["settings"], strict=True):
            before["config"]["cost_bps"] = after["config"]["cost_bps"]
        if settings != stressed["settings"]:
            raise ValueError("Cost perturbation changed rules other than declared fees")
    return result


def _public(pair):
    return {key: value for key, value in pair.items() if not key.startswith("_")}


def _percentile(values, fraction):
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _interval(edges, block_length):
    """Fixed-seed circular paired block means; memory is O(days + resamples)."""
    rng = random.Random(SEED + block_length)
    size = len(edges)
    # Prefix sums avoid copying or looping over individual marks in every draw.
    doubled = edges + edges
    prefix = [0.0]
    for value in doubled:
        prefix.append(prefix[-1] + value)
        if not math.isfinite(prefix[-1]):
            raise ValueError("Paired return accumulation exceeds finite diagnostic bounds")
    draws = []
    for _ in range(RESAMPLES):
        total, remaining = 0.0, size
        while remaining:
            take = min(block_length, remaining)
            start = rng.randrange(size)
            total += prefix[start + take] - prefix[start]
            remaining -= take
        value = total / size * 10_000
        if not math.isfinite(value):
            raise ValueError("Paired resampling exceeds finite diagnostic bounds")
        draws.append(value)
    draws.sort()
    return {
        "block_sessions": block_length,
        "full_block_equivalents": size // block_length,
        "lower_bps": _percentile(draws, 0.025),
        "median_bps": _percentile(draws, 0.5),
        "upper_bps": _percentile(draws, 0.975),
    }


def assess(*, candidate, baseline, folds, stress_candidate, stress_baseline, sensitivity=None):
    """Conservatively qualify one development winner; caller freezes or falls back.

    All accounts are existing verified development shards. ``folds`` contains
    exactly three disjoint chronological check1/2/3 pairs, each with candidate,
    baseline, stress_candidate and stress_baseline. They evaluate the adaptive
    refitting procedure, whose winner may differ between folds. ``sensitivity``
    contains 2–4 distinct actual prespecified one-step neighbour search accounts,
    on the same hypothesis and hard constraints (the controller owns that design).
    No trade/trial/symbol count is treated as an independent sample count.
    """
    result = {
        "version": VERSION,
        "status": "unsupported",
        "qualified": False,
        "reasons": [],
        "policy": {
            "minimum_sessions": MIN_DAYS,
            "minimum_neighbours": MIN_NEIGHBOURS,
            "block_sessions": list(BLOCK_LENGTHS),
            "resamples_per_block": RESAMPLES,
            "seed": SEED,
            "selection": "Require positive return/risk improvement, all later development and higher-cost checks, positive paired lower ranges at both block lengths and stable measured neighbours; otherwise keep unchanged settings.",
            "scope": "Development observations only; all final-period results excluded.",
        },
        "limitations": [
            LIMITATION,
            "Neighbour checks describe only the prespecified local perturbations; no exhaustive parameter, date or symbol stability claim.",
        ],
        "sources": [METHOD_SOURCE, DEPENDENCE_SOURCE],
    }
    try:
        main = _pair(candidate, baseline, period="search")
        stressed = _stress_pair(
            candidate, baseline, stress_candidate, stress_baseline, period="search"
        )
        result.update(development=_public(main), higher_costs=_public(stressed))
        if not isinstance(folds, list) or len(folds) != 3:
            raise ValueError("Three later development fold accounts are required")
        fold_records, fold_days, last = [], 0, None
        for index, fold in enumerate(folds, 1):
            period = f"check{index}"
            normal = _pair(fold["candidate"], fold["baseline"], period=period)
            cost = _stress_pair(
                fold["candidate"],
                fold["baseline"],
                fold["stress_candidate"],
                fold["stress_baseline"],
                period=period,
            )
            for name in ("candidate", "baseline", "stress_candidate", "stress_baseline"):
                if (
                    fold[name]["binding"] != candidate["binding"]
                    or fold[name]["account"] != candidate["account"]
                ):
                    raise ValueError(
                        "Development folds do not share original source binding and starting cash"
                    )
            if fold["baseline"]["settings"] != baseline["settings"]:
                raise ValueError("Development folds changed the unchanged reference settings")
            fold_comparison = _comparison(
                fold["candidate"],
                declared_conditions="research_opportunity_basis" in fold["candidate"],
            )
            main_comparison = _comparison(
                candidate, declared_conditions="research_opportunity_basis" in candidate
            )
            for name in ("execution", "costs", "currency", "capital"):
                if fold_comparison.get(name) != main_comparison.get(name):
                    raise ValueError(
                        "Development folds changed common account execution or cost assumptions"
                    )
            if last is not None and normal["from"] <= last:
                raise ValueError("Development fold observation windows overlap or are reordered")
            if not set(normal["_dates"]) <= set(main["_dates"]):
                raise ValueError(
                    "Development fold dates fall outside the development search window"
                )
            last = normal["to"]
            fold_days += normal["days"]
            fold_records.append(
                {"id": period, "normal": _public(normal), "higher_costs": _public(cost)}
            )
        result["folds"] = fold_records
        result["procedure_sessions"] = fold_days
        if (
            main["days"] < MIN_DAYS
            or fold_days < MIN_DAYS
            or any(row["normal"]["days"] < 10 for row in fold_records)
        ):
            result.update(
                status="insufficient",
                reasons=[
                    "Too few distinct observed development sessions for the declared paired-block and fold checks."
                ],
            )
            return result
        if (
            not isinstance(sensitivity, list)
            or not MIN_NEIGHBOURS <= len(sensitivity) <= MAX_NEIGHBOURS
        ):
            result.update(
                status="insufficient",
                reasons=[
                    "At least two and at most four actual prespecified neighbour accounts are required."
                ],
            )
            return result
        neighbours, seen = [], {candidate["config_id"]}
        hard_limits = {
            row["id"]: row.get("execution_policy", {}).get("risk_limits")
            for row in candidate["settings"]
        }
        for shard in sensitivity:
            if shard["config_id"] in seen or shard["hypothesis"] != candidate["hypothesis"]:
                raise ValueError(
                    "Neighbour accounts must have distinct settings and the selected condition"
                )
            seen.add(shard["config_id"])
            if {
                row["id"]: row.get("execution_policy", {}).get("risk_limits")
                for row in shard["settings"]
            } != hard_limits:
                raise ValueError("Neighbour perturbation changed hard portfolio limits")
            measured = _pair(shard, baseline, period="search")
            neighbours.append({"config_id": shard["config_id"], **_public(measured)})
        neighbour_passes = sum(row["passes"] for row in neighbours)
        median_edge = median(row["return_edge_pct"] for row in neighbours)
        stable = (
            neighbour_passes >= math.ceil(0.75 * len(neighbours))
            and median_edge >= 0.5 * main["return_edge_pct"]
        )
        result["sensitivity"] = {
            "status": "supported" if stable else "not_supported",
            "tested": len(neighbours),
            "passing": neighbour_passes,
            "median_return_edge_pct": median_edge,
            "required_median_edge_fraction": 0.5,
            "neighbours": neighbours,
        }
        intervals = [_interval(main["_edges"], length) for length in BLOCK_LENGTHS]
        mean_edge_bps = math.fsum(main["_edges"]) / main["days"] * 10_000
        if not math.isfinite(mean_edge_bps):
            raise ValueError("Paired mean exceeds finite diagnostic bounds")
        result["uncertainty"] = {
            "method": "paired_circular_block_percentile_range",
            "statistic": "Mean candidate minus baseline daily account return, in basis points per observed session",
            "daily_marks": "Last actual recorded account mark per session, starting from the original cash; no interpolation",
            "sessions": main["days"],
            "mean_daily_edge_bps": mean_edge_bps,
            "percentiles": [2.5, 97.5],
            "ranges": intervals,
            "interpretation": LIMITATION,
        }
        reasons = []
        if not main["passes"] or main["return_edge_pct"] <= 0:
            reasons.append(
                "The selected development account did not improve positive return and return minus drawdown over unchanged settings."
            )
        if not stressed["passes"]:
            reasons.append(
                "The development improvement did not survive the declared higher-cost scenario."
            )
        if any(
            not row["normal"]["passes"] or not row["higher_costs"]["passes"] for row in fold_records
        ):
            reasons.append(
                "At least one later development refit or its higher-cost check did not support improvement."
            )
        if any(row["lower_bps"] <= 0 for row in intervals):
            reasons.append(
                "Paired development return ranges include no improvement at one or both declared block lengths."
            )
        if not stable:
            reasons.append(
                "Actual neighbouring settings did not retain the declared local improvement support."
            )
        result.update(
            status="not_supported" if reasons else "supported",
            qualified=not reasons,
            reasons=reasons,
        )
        return result
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        result.update(
            status="unsupported",
            qualified=False,
            reasons=[f"Comparable development evidence is unavailable: {error}"],
        )
        return result
