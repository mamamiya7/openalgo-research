"""Offline full-procedure qualification; never confidence for a live strategy.

Each observation is one completed declared optimisation programme on one independently
generated controlled dataset. Trials, trades and repeated sampler seeds are not
replicates. The runner owns dataset generation and the actual controller invocation;
this contract checks coverage and retains its explicit execution-model declaration.
"""

from __future__ import annotations

import math
from copy import deepcopy

from research.report_contract import fingerprint

VERSION = "research-search-calibration-v1"
MAX_REPLICATES = 32
MAX_CASES = 160
SCENARIOS = (
    ("independent_null", "null"),
    ("correlated_null", "null"),
    ("shifted_null", "null"),
    ("sparse_null", "null"),
    ("persistent_edge", "power"),
)
EXECUTION_MODELS = ("controlled_account_model", "native_synthetic_market")
LIMITATION = (
    "Offline engineering qualification under the declared controlled models only. "
    "Not general false-discovery-rate control, search-adjusted live confidence, "
    "a probability of future profit or independent real-market replication."
)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _label(value, maximum=160):
    return isinstance(value, str) and 0 < len(value) <= maximum


def _sealed(body):
    return {**deepcopy(body), "id": fingerprint(body)}


def _check_seal(value):
    if not isinstance(value, dict) or value.get("id") != fingerprint(
        {key: item for key, item in value.items() if key != "id"}
    ):
        raise ValueError("Calibration evidence changed its seal")


def compile_plan(
    *,
    code_identity,
    runner_identity,
    execution_model="controlled_account_model",
    replicates=4,
    sampler_seeds=(0, 1, 2),
    recipe_version="automatic-hypotheses-v15",
    proposal_budget=408,
    primary_proposal_budget=272,
    max_accounts=512,
):
    """Freeze complete coverage and model scope before any outcomes are read."""
    seeds = list(sampler_seeds)
    if (
        not _label(code_identity, 128)
        or not _label(runner_identity)
        or execution_model not in EXECUTION_MODELS
        or not _integer(replicates, 1, MAX_REPLICATES)
        or not 2 <= len(seeds) <= 8
        or any(not _integer(seed, 0, 2**31 - 1) for seed in seeds)
        or len(set(seeds)) != len(seeds)
        or not _label(recipe_version, 80)
        or not _integer(proposal_budget, 1, 4096)
        or not _integer(primary_proposal_budget, 1, proposal_budget)
        or not _integer(max_accounts, 1, 4096)
    ):
        raise ValueError("Invalid bounded calibration declaration")
    cases = []
    used_data_seeds = set()
    for scenario_index, (scenario, kind) in enumerate(SCENARIOS):
        for replicate in range(replicates):
            # Separate from optimisation randomness and from every other case.
            data_seed = 1_000_003 + scenario_index * 1009 + replicate * 101
            while data_seed in seeds or data_seed in used_data_seeds:
                data_seed += 1
            used_data_seeds.add(data_seed)
            body = {
                "scenario": scenario,
                "kind": kind,
                "replicate": replicate,
                "data_seed": data_seed,
            }
            cases.append(_sealed(body))
    return _sealed(
        {
            "version": VERSION,
            "scope": "offline_controlled_full_procedure",
            "code_identity": code_identity,
            "runner_identity": runner_identity,
            "execution_model": execution_model,
            "recipe_version": recipe_version,
            "sampler_seeds": seeds,
            "proposal_budget": proposal_budget,
            "primary_proposal_budget": primary_proposal_budget,
            "max_accounts": max_accounts,
            "replicates": replicates,
            "independence": "Independent generated datasets within each declared model stratum; runner must establish this assumption",
            "policy": "All declared cases complete; zero null changed recommendations and at least one positive-control changed recommendation. Never tune thresholds to this receipt or discard failed cases.",
            "cases": cases,
            "limitation": LIMITATION,
        }
    )


def verify_plan(plan):
    _check_seal(plan)
    try:
        rebuilt = compile_plan(
            **{
                key: plan[key]
                for key in (
                    "code_identity",
                    "runner_identity",
                    "execution_model",
                    "replicates",
                    "sampler_seeds",
                    "recipe_version",
                    "proposal_budget",
                    "primary_proposal_budget",
                    "max_accounts",
                )
            }
        )
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete calibration plan") from error
    if rebuilt != plan:
        raise ValueError("Calibration plan does not reproduce its frozen declaration")
    return deepcopy(plan)


def binomial_upper(successes, count, confidence=0.95):
    """Exact one-sided Clopper-Pearson bound for IID Bernoulli model runs.

    Counts here are false recommendations, including zero; this bound describes
    only repeated runs under one declared data-generating model.
    """
    if (
        not _integer(count, 1, MAX_REPLICATES)
        or not _integer(successes, 0, count)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 < confidence < 1
    ):
        raise ValueError("Invalid independent-run binomial counts")
    if successes == count:
        return 1.0
    alpha = 1 - confidence
    if successes == 0:
        return -math.expm1(math.log(alpha) / count)

    def cdf(probability):
        return sum(
            math.comb(count, index) * probability**index * (1 - probability) ** (count - index)
            for index in range(successes + 1)
        )

    lower, upper = successes / count, 1.0
    for _ in range(80):
        midpoint = (lower + upper) / 2
        if cdf(midpoint) > alpha:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2


def _outcome(case, plan, outcome):
    """Canonical compact facts; raw account histories stay with their owner."""
    if not isinstance(outcome, dict) or outcome.get("case_id") != case["id"]:
        return {"case_id": case["id"], "status": "invalid", "reason": "missing_or_wrong_case"}
    if outcome.get("status") in ("error", "incomplete"):
        return {
            "case_id": case["id"],
            "status": outcome["status"],
            "reason": "runner_did_not_complete",
        }
    valid = (
        outcome.get("status") == "completed"
        and _label(outcome.get("baseline_config_id"), 128)
        and _label(outcome.get("selected_config_id"), 128)
        and outcome.get("seed_checks_status") in ("completed", "not_run")
        and outcome.get("proposal_count")
        == plan[
            "proposal_budget"
            if outcome.get("seed_checks_status") == "completed"
            else "primary_proposal_budget"
        ]
        and type(outcome.get("proposal_count")) is int
        and _integer(outcome.get("account_count"), 1, plan["max_accounts"])
        and outcome.get("sampler_seeds") == plan["sampler_seeds"]
        and outcome.get("development_selection_frozen") is True
        and outcome.get("procedure_completed") is True
        and outcome.get("final_excluded_from_selection") is True
        and _label(outcome.get("evidence_id"), 128)
    )
    if not valid:
        return {
            "case_id": case["id"],
            "status": "invalid",
            "reason": "procedure_coverage_or_evidence",
        }
    keys = (
        "case_id",
        "status",
        "baseline_config_id",
        "selected_config_id",
        "proposal_count",
        "seed_checks_status",
        "account_count",
        "sampler_seeds",
        "development_selection_frozen",
        "procedure_completed",
        "final_excluded_from_selection",
        "evidence_id",
    )
    return deepcopy({key: outcome[key] for key in keys})


def _receipt(plan, outcomes):
    if not isinstance(outcomes, list) or len(outcomes) != len(plan["cases"]):
        raise ValueError("Calibration case coverage changed")
    canonical = [
        _outcome(case, plan, outcome) for case, outcome in zip(plan["cases"], outcomes, strict=True)
    ]
    # Invalid/error canonical rows are already minimal and must survive verify.
    for index, outcome in enumerate(outcomes):
        if isinstance(outcome, dict) and outcome.get("status") == "invalid":
            expected = {"case_id", "status", "reason"}
            if set(outcome) != expected or outcome.get("case_id") != plan["cases"][index]["id"]:
                raise ValueError("Invalid calibration outcome changed")
            canonical[index] = deepcopy(outcome)
    strata = []
    for scenario, kind in SCENARIOS:
        rows = [
            outcome
            for case, outcome in zip(plan["cases"], canonical, strict=True)
            if case["scenario"] == scenario
        ]
        completed = [row for row in rows if row["status"] == "completed"]
        changes = sum(row["selected_config_id"] != row["baseline_config_id"] for row in completed)
        complete = len(completed) == len(rows)
        strata.append(
            {
                "scenario": scenario,
                "kind": kind,
                "planned_runs": len(rows),
                "completed_runs": len(completed),
                "changed_recommendations": changes,
                "false_recommendations": changes if kind == "null" else None,
                "observed_rate": changes / len(completed) if completed else None,
                "one_sided_95pct_upper": binomial_upper(changes, len(completed))
                if complete and kind == "null"
                else None,
                "bound_scope": "Only IID independent datasets within this declared controlled model; no pooling across models",
                "status": "incomplete"
                if not complete
                else "passed"
                if (changes == 0 if kind == "null" else changes > 0)
                else "failed",
            }
        )
    status = (
        "incomplete"
        if any(row["status"] == "incomplete" for row in strata)
        else "passed"
        if all(row["status"] == "passed" for row in strata)
        else "failed"
    )
    return _sealed(
        {
            "version": VERSION,
            "plan": plan,
            "status": status,
            "qualified_scope": "declared_controlled_model_engineering_checks_only"
            if status == "passed"
            else None,
            "cases": canonical,
            "strata": strata,
            "independent_run_unit": "one complete procedure on one independently generated dataset",
            "limitation": LIMITATION,
        }
    )


def run(plan, runner):
    """Execute every frozen case; an error is retained, never counted as safe."""
    plan = verify_plan(plan)
    if not callable(runner):
        raise ValueError("Calibration requires an explicit full-procedure runner")
    outcomes = []
    for case in plan["cases"]:
        try:
            outcome = runner(deepcopy(case), deepcopy(plan))
        except Exception:
            # Do not retain arbitrary exception text: runners may touch private
            # fixture paths. Failure type/coverage is sufficient for this receipt.
            outcome = {"case_id": case["id"], "status": "error"}
        outcomes.append(_outcome(case, plan, outcome))
    return _receipt(plan, outcomes)


def verify_receipt(receipt):
    _check_seal(receipt)
    try:
        rebuilt = _receipt(verify_plan(receipt["plan"]), receipt["cases"])
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete calibration receipt") from error
    if rebuilt != receipt:
        raise ValueError("Calibration receipt does not reproduce its declared cases")
    return deepcopy(receipt)
