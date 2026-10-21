"""Offline native procedure qualification, grouped by independent dataset.

Sampler programmes reuse each dataset; they are sensitivity checks, never extra
independent market observations. The canonical programme stays primary. This
module cannot select a replacement strategy or attach confidence to a live run.
"""

from __future__ import annotations

import re
from copy import deepcopy

from research.procedure_seed_calibration import seed_plan, verify_seed_plan
from research.report_contract import fingerprint
from research.search_calibration import binomial_upper
from research.search_calibration import compile_plan as base_plan
from research.search_calibration import run as base_run
from research.search_calibration import verify_plan as verify_base_plan

VERSION = "research-native-procedure-calibration-v1"
LIMITATION = (
    "Native synthetic-market engineering qualification only. Repeated sampler programmes "
    "on one dataset are dependent and count once. Not general FDR control, a probability "
    "of profit, independent real-market replication or a replacement trading recommendation."
)


def _seal(body):
    return {**deepcopy(body), "id": fingerprint(body)}


def _verify_seal(value):
    if not isinstance(value, dict) or value.get("id") != fingerprint(
        {key: item for key, item in value.items() if key != "id"}
    ):
        raise ValueError("Native calibration evidence changed its seal")


def compile_plan(*, code_identity, runner_identity, replicates=1, procedure_seeds=(0, 1, 2)):
    indices = list(procedure_seeds)
    if indices != [0, 1, 2] or any(type(index) is not int for index in indices):
        raise ValueError("Declare canonical and both complete alternate sampler programmes")
    original = base_plan(
        code_identity=code_identity,
        runner_identity=runner_identity,
        execution_model="native_synthetic_market",
        replicates=replicates,
    )
    return _seal(
        {
            "version": VERSION,
            "scope": "offline_native_full_procedure_seed_qualification",
            "base_plan": original,
            "seed_plans": [seed_plan(index) for index in indices],
            "primary_programme": 0,
            "maximum_programmes": len(original["cases"]) * len(indices),
            "policy": (
                "Complete every declared dataset and sampler programme. No null dataset may "
                "produce a changed recommendation in any programme; at least one positive "
                "control must support changed settings in all programmes. Preserve the "
                "canonical choice; alternate programmes never choose a luckier replacement."
            ),
            "limitation": LIMITATION,
        }
    )


def verify_plan(plan):
    _verify_seal(plan)
    try:
        original = verify_base_plan(plan["base_plan"])
        rebuilt = compile_plan(
            code_identity=original["code_identity"],
            runner_identity=original["runner_identity"],
            replicates=original["replicates"],
            procedure_seeds=tuple(verify_seed_plan(row)["index"] for row in plan["seed_plans"]),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete native calibration plan") from error
    if rebuilt != plan:
        raise ValueError("Native calibration declaration does not reproduce")
    return deepcopy(plan)


def _native_facts(row, case, context):
    """Validate explicit runner facts; the trusted runner owns their native proof."""
    if not isinstance(row, dict) or row.get("status") != "completed":
        return None
    facts = row.get("native_procedure")
    if not isinstance(facts, dict):
        return None
    try:
        _verify_seal(facts)
        if (
            facts["execution_model"] != "native_synthetic_market"
            or facts["native_execution"] is not True
            or facts["seed_plan_id"] != context["id"]
            or type(facts["data_seed"]) is not int
            or facts["data_seed"] != case["data_seed"]
            or type(facts["constraint_audits"]) is not int
            or facts["constraint_audits"] != row["account_count"]
            or not 1 <= facts["constraint_audits"] <= 512
            or type(facts.get("native_calculations")) is not int
            or type(facts.get("native_reuses")) is not int
            or not 0 <= facts["native_calculations"] <= row["account_count"]
            or not 0 <= facts["native_reuses"] <= row["account_count"]
            or facts["native_calculations"] + facts["native_reuses"] != row["account_count"]
            or facts.get("recovery_verified") is not True
            or (case["kind"] == "power" and facts.get("adverse_final_verified") is not True)
            or type(facts.get("resumed_evaluations")) is not int
            or not 1 <= facts["resumed_evaluations"] <= row["account_count"]
            or facts.get("resume_prefix_calls") != facts["resumed_evaluations"]
            or type(facts.get("resume_prefix_calls")) is not int
            or not isinstance(facts.get("resume_prefix_history_id"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", facts["resume_prefix_history_id"])
            or not isinstance(facts["dataset_id"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", facts["dataset_id"])
            or not isinstance(facts["history_id"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", facts["history_id"])
            or not isinstance(facts["fit_evidence"], dict)
            or set(facts["fit_evidence"]) != set(context["fit_sampler_seeds"])
        ):
            return None
        for name, seed in context["fit_sampler_seeds"].items():
            fit = facts["fit_evidence"][name]
            expected_count = (
                68
                if name in ("fit1", "fit2", "fit3", "search")
                or row["seed_checks_status"] == "completed"
                else 0
            )
            if (
                type(fit["sampler_seed"]) is not int
                or fit["sampler_seed"] != seed
                or type(fit["proposals"]) is not int
                or fit["proposals"] != expected_count
                or fit["status"] != ("completed" if expected_count else "not_run")
                or not isinstance(fit["trials_id"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", fit["trials_id"])
                or (
                    expected_count
                    and (
                        not isinstance(fit["selected_config_id"], str)
                        or not re.fullmatch(r"[a-f0-9]{64}", fit["selected_config_id"])
                    )
                )
                or (not expected_count and fit["selected_config_id"] is not None)
            ):
                return None
    except (KeyError, TypeError, ValueError):
        return None
    return deepcopy(facts)


def _receipt(plan, rows):
    original = plan["base_plan"]
    if not isinstance(rows, list) or len(rows) != len(original["cases"]):
        raise ValueError("Native calibration dataset coverage changed")
    # Reuse the established complete-controller outcome checks separately for
    # each programme; never pool their denominators in the grouped statistics.
    programs = {}
    for context in plan["seed_plans"]:
        index = context["index"]
        callbacks = {}
        for case, row in zip(original["cases"], rows, strict=True):
            if not isinstance(row, dict) or row.get("case_id") != case["id"]:
                raise ValueError("Native calibration dataset identity changed")
            if not isinstance(row.get("programmes"), list) or len(row["programmes"]) != 3:
                raise ValueError("Native calibration programme coverage changed")
            item = row["programmes"][index]
            if not isinstance(item, dict) or item.get("seed_plan_id") != context["id"]:
                raise ValueError("Native calibration sampler identity changed")
            callbacks[case["id"]] = item.get("outcome")
        programs[index] = base_run(original, lambda case, _, values=callbacks: values[case["id"]])
    canonical = []
    for position, (case, row) in enumerate(zip(original["cases"], rows, strict=True)):
        values = []
        dataset_ids = set()
        for context in plan["seed_plans"]:
            index = context["index"]
            outcome = programs[index]["cases"][position]
            raw = row["programmes"][index].get("outcome")
            if isinstance(raw, dict) and raw.get("status") == "invalid":
                if set(raw) != {"case_id", "status", "reason"} or raw["case_id"] != case["id"]:
                    raise ValueError("Invalid native calibration outcome changed")
                outcome = deepcopy(raw)
            facts = _native_facts(raw, case, context)
            if outcome["status"] == "completed" and facts is None:
                outcome = {"case_id": case["id"], "status": "invalid", "reason": "native_proof"}
            if outcome["status"] == "completed" and facts is not None:
                dataset_ids.add(facts["dataset_id"])
                outcome = {**outcome, "native_procedure": facts}
            values.append({"seed_plan_id": context["id"], "outcome": outcome})
        complete = all(item["outcome"]["status"] == "completed" for item in values)
        complete = complete and len(dataset_ids) == 1
        changes = [
            item["outcome"]["selected_config_id"] != item["outcome"]["baseline_config_id"]
            for item in values
            if item["outcome"]["status"] == "completed"
        ]
        choices = [
            item["outcome"]["selected_config_id"]
            for item in values
            if item["outcome"]["status"] == "completed"
        ]
        canonical.append(
            {
                "case_id": case["id"],
                "status": "completed" if complete else "incomplete",
                "programmes": values,
                "any_changed": any(changes) if complete else None,
                "all_changed": all(changes) if complete else None,
                "decision_agreement": len(set(changes)) == 1 if complete else None,
                "configuration_agreement": len(set(choices)) == 1 if complete else None,
                "canonical_config_id": choices[0] if complete else None,
                "replacement_selected": False,
            }
        )
    strata = []
    for original_stratum in programs[0]["strata"]:
        selected = [
            row
            for case, row in zip(original["cases"], canonical, strict=True)
            if case["scenario"] == original_stratum["scenario"]
        ]
        complete = [row for row in selected if row["status"] == "completed"]
        null = original_stratum["kind"] == "null"
        events = sum(row["any_changed"] if null else row["all_changed"] for row in complete)
        covered = len(complete) == len(selected)
        strata.append(
            {
                "scenario": original_stratum["scenario"],
                "kind": original_stratum["kind"],
                "planned_datasets": len(selected),
                "completed_datasets": len(complete),
                "completed_programmes": sum(
                    item["outcome"]["status"] == "completed"
                    for row in selected
                    for item in row["programmes"]
                ),
                "any_seed_false_recommendations": events if null else None,
                "all_seed_positive_controls": events if not null else None,
                "observed_dataset_rate": events / len(complete) if complete else None,
                "one_sided_95pct_upper": binomial_upper(events, len(complete))
                if covered and null
                else None,
                "status": "incomplete"
                if not covered
                else "passed"
                if (events == 0 if null else events > 0)
                else "failed",
            }
        )
    status = (
        "incomplete"
        if any(row["status"] == "incomplete" for row in strata)
        else ("passed" if all(row["status"] == "passed" for row in strata) else "failed")
    )
    return _seal(
        {
            "version": VERSION,
            "plan": plan,
            "status": status,
            "cases": canonical,
            "strata": strata,
            "independent_run_unit": "one generated dataset, across all declared sampler programmes",
            "qualified_scope": "declared_native_synthetic_engineering_checks_only"
            if status == "passed"
            else None,
            "limitation": LIMITATION,
        }
    )


def build_receipt(plan, rows):
    """Reconcile all predeclared programmes, including retained errors/partials."""
    return _receipt(verify_plan(plan), deepcopy(rows))


def run(plan, runner, *, observe=None):
    plan = verify_plan(plan)
    if not callable(runner):
        raise ValueError("Native calibration requires a trusted explicit runner")
    if observe is not None and not callable(observe):
        raise ValueError("Native calibration observer must be callable")
    rows = []
    for case in plan["base_plan"]["cases"]:
        programmes = []
        for context in plan["seed_plans"]:
            try:
                outcome = runner(
                    deepcopy(case),
                    deepcopy(plan["base_plan"]),
                    calibration_seed_plan=deepcopy(context),
                )
            except Exception:
                outcome = {"case_id": case["id"], "status": "error"}
            programmes.append({"seed_plan_id": context["id"], "outcome": outcome})
            if observe is not None:
                observe(deepcopy(case), deepcopy(context), deepcopy(programmes[-1]))
        rows.append({"case_id": case["id"], "programmes": programmes})
    return _receipt(plan, rows)


def verify_receipt(receipt):
    _verify_seal(receipt)
    try:
        rebuilt = _receipt(verify_plan(receipt["plan"]), receipt["cases"])
    except (KeyError, TypeError) as error:
        raise ValueError("Incomplete native calibration receipt") from error
    if rebuilt != receipt:
        raise ValueError("Native calibration receipt does not reproduce")
    return deepcopy(receipt)
