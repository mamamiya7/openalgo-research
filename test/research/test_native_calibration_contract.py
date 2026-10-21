"""Grouped receipt contracts; the callbacks here do not simulate native fills."""

from copy import deepcopy

import pytest

from research.native_calibration import compile_plan, run, verify_plan, verify_receipt
from research.report_contract import fingerprint


def sealed(body):
    return {**deepcopy(body), "id": fingerprint(body)}


def declaration(**kwargs):
    return compile_plan(
        code_identity="contract-only", runner_identity="not-a-native-runner", **kwargs
    )


def controlled_contract_outcome(case, plan, *, calibration_seed_plan):
    """Explicit fake facts only to test validation; never a qualification runner."""
    changed = case["kind"] == "power"
    count = 408 if changed else 272
    context = calibration_seed_plan
    facts = sealed(
        {
            "execution_model": "native_synthetic_market",
            "native_execution": True,
            "seed_plan_id": context["id"],
            "data_seed": case["data_seed"],
            "constraint_audits": 400 if changed else 290,
            "native_calculations": 350 if changed else 240,
            "native_reuses": 50,
            "recovery_verified": True,
            "adverse_final_verified": changed,
            "resumed_evaluations": 42,
            "resume_prefix_calls": 42,
            "resume_prefix_history_id": fingerprint("controlled prefix"),
            "dataset_id": fingerprint({"case": case, "fixture": "contract-only"}),
            "history_id": fingerprint({"case": case, "context": context}),
            "fit_evidence": {
                name: {
                    "sampler_seed": seed,
                    "proposals": 68 if changed or name in ("fit1", "fit2", "fit3", "search") else 0,
                    "status": "completed"
                    if changed or name in ("fit1", "fit2", "fit3", "search")
                    else "not_run",
                    "trials_id": fingerprint({"name": name, "seed": seed}),
                    "selected_config_id": fingerprint("choice")
                    if changed or name in ("fit1", "fit2", "fit3", "search")
                    else None,
                }
                for name, seed in context["fit_sampler_seeds"].items()
            },
        }
    )
    return {
        "case_id": case["id"],
        "status": "completed",
        "baseline_config_id": "baseline",
        "selected_config_id": "chosen" if changed else "baseline",
        "proposal_count": count,
        "seed_checks_status": "completed" if changed else "not_run",
        "account_count": facts["constraint_audits"],
        "sampler_seeds": plan["sampler_seeds"],
        "procedure_completed": True,
        "development_selection_frozen": True,
        "final_excluded_from_selection": True,
        "evidence_id": fingerprint(facts),
        "native_procedure": facts,
    }


def reseal(value):
    value["id"] = fingerprint({key: item for key, item in value.items() if key != "id"})
    return value


def test_complete_grouped_plan_counts_datasets_not_sampler_programmes():
    plan = declaration(replicates=2)
    assert verify_plan(plan) == plan
    assert plan["maximum_programmes"] == 30
    receipt = run(plan, controlled_contract_outcome)
    assert verify_receipt(receipt) == receipt
    assert receipt["status"] == "passed"
    for row in receipt["strata"]:
        assert row["planned_datasets"] == row["completed_datasets"] == 2
        assert row["completed_programmes"] == 6
        if row["kind"] == "null":
            assert row["any_seed_false_recommendations"] == 0
            assert row["one_sided_95pct_upper"] == pytest.approx(1 - 0.05**0.5)
    assert all(not row["replacement_selected"] for row in receipt["cases"])


def test_seed_decision_agreement_is_not_configuration_agreement_or_replacement():
    def callback(case, plan, *, calibration_seed_plan):
        value = controlled_contract_outcome(case, plan, calibration_seed_plan=calibration_seed_plan)
        if case["kind"] == "power":
            value["selected_config_id"] = f"settings-{calibration_seed_plan['index']}"
        return value

    receipt = run(declaration(), callback)
    assert receipt["status"] == "passed"
    row = receipt["cases"][-1]
    assert row["decision_agreement"] is True
    assert row["configuration_agreement"] is False
    assert row["canonical_config_id"] == "settings-0"
    assert row["replacement_selected"] is False


@pytest.mark.parametrize("index", [0, 1, 2])
def test_one_null_changed_programme_is_one_dataset_failure(index):
    def callback(case, plan, *, calibration_seed_plan):
        value = controlled_contract_outcome(case, plan, calibration_seed_plan=calibration_seed_plan)
        if case["scenario"] == "independent_null" and calibration_seed_plan["index"] == index:
            value["selected_config_id"] = "false-positive"
        return value

    receipt = run(declaration(), callback)
    assert receipt["status"] == "failed"
    assert receipt["strata"][0]["any_seed_false_recommendations"] == 1
    assert receipt["strata"][0]["completed_datasets"] == 1
    assert receipt["strata"][0]["completed_programmes"] == 3
    assert verify_receipt(receipt) == receipt


def test_always_baseline_or_unstable_positive_controls_cannot_pass():
    def callback(case, plan, *, calibration_seed_plan):
        value = controlled_contract_outcome(case, plan, calibration_seed_plan=calibration_seed_plan)
        if calibration_seed_plan["index"] == 1:
            value["selected_config_id"] = value["baseline_config_id"]
        return value

    receipt = run(declaration(), callback)
    assert receipt["status"] == "failed"
    assert receipt["cases"][-1]["decision_agreement"] is False
    assert receipt["cases"][-1]["canonical_config_id"] == "chosen"


@pytest.mark.parametrize(
    "bad",
    [
        "error",
        "incomplete",
        "missing_audit",
        "wrong_data",
        "wrong_seed",
        "partial_fit",
        "native_flag",
        "recovery",
        "prefix_calls",
        "data_seed_bool",
    ],
)
def test_invalid_programme_never_becomes_a_safe_null(bad):
    def callback(case, plan, *, calibration_seed_plan):
        value = controlled_contract_outcome(case, plan, calibration_seed_plan=calibration_seed_plan)
        if case["scenario"] != "independent_null" or calibration_seed_plan["index"] != 1:
            return value
        if bad == "error":
            raise RuntimeError("sensitive private test path")
        if bad == "incomplete":
            value["status"] = "incomplete"
        else:
            facts = value["native_procedure"]
            if bad == "missing_audit":
                facts["constraint_audits"] -= 1
            elif bad == "wrong_data":
                facts["dataset_id"] = fingerprint("different market")
            elif bad == "wrong_seed":
                facts["fit_evidence"]["fit1"]["sampler_seed"] = 0
            elif bad == "partial_fit":
                facts["fit_evidence"]["fit1"]["proposals"] = 67
            elif bad == "native_flag":
                facts["native_execution"] = False
            elif bad == "recovery":
                facts["recovery_verified"] = False
            elif bad == "prefix_calls":
                facts["resume_prefix_calls"] = 0
            else:
                facts["data_seed"] = float(case["data_seed"])
            reseal(facts)
        return value

    receipt = run(declaration(), callback)
    assert receipt["status"] == "incomplete"
    assert receipt["strata"][0]["completed_datasets"] == 0
    assert receipt["strata"][0]["one_sided_95pct_upper"] is None
    assert receipt["cases"][0]["any_changed"] is None
    assert "sensitive private" not in str(receipt)
    assert verify_receipt(receipt) == receipt


@pytest.mark.parametrize("field", ["drop", "pool", "replacement", "scope", "plan", "seed_order"])
def test_rehashed_receipt_cannot_rewrite_summary_or_coverage(field):
    receipt = run(declaration(), controlled_contract_outcome)
    if field == "drop":
        receipt["cases"].pop()
    elif field == "pool":
        receipt["strata"][0]["completed_datasets"] = 3
    elif field == "replacement":
        receipt["cases"][0]["replacement_selected"] = True
    elif field == "scope":
        receipt["qualified_scope"] = "live-confidence"
    elif field == "plan":
        receipt["plan"]["seed_plans"].pop()
        reseal(receipt["plan"])
    else:
        receipt["cases"][0]["programmes"].reverse()
    with pytest.raises(ValueError):
        verify_receipt(reseal(receipt))


@pytest.mark.parametrize("indices", [(0,), (0, 2, 1), (0, 1, True), (0, 1, 2.0), (0, 1, 2, 3)])
def test_only_complete_fixed_bounded_seed_programmes_are_admitted(indices):
    with pytest.raises(ValueError):
        declaration(procedure_seeds=indices)


def test_runner_and_observer_receive_detached_data_and_every_error_is_retained():
    plan = declaration()
    original = deepcopy(plan)
    seen = []

    def observe(case, context, value):
        seen.append((case["id"], context["index"]))
        case.clear()
        context.clear()
        value.clear()

    receipt = run(plan, controlled_contract_outcome, observe=observe)
    assert len(seen) == len(set(seen)) == 15
    assert plan == original and receipt["status"] == "passed"


def test_cli_preserves_frozen_plan_and_individual_programmes(tmp_path, monkeypatch):
    import json
    import sys
    from types import ModuleType

    from tools.research_calibration import main

    module = ModuleType("native_contract_callback_not_engine")
    module.run_case = controlled_contract_outcome
    monkeypatch.setitem(sys.modules, module.__name__, module)
    path = tmp_path / "receipt.json"
    args = [
        "--runner",
        module.__name__ + ":run_case",
        "--runner-identity",
        "contract-only",
        "--code-identity",
        "fixture-code",
        "--execution-model",
        "native_synthetic_market",
        "--replicates",
        "1",
        "--whole-procedure-seeds",
        "--output",
        str(path),
    ]
    assert main(args) == 0
    assert len(list((tmp_path / "receipt.json.programmes").glob("*.json"))) == 15
    assert verify_plan(json.loads((tmp_path / "receipt.json.plan.json").read_text()))
    saved = path.read_bytes()
    with pytest.raises(SystemExit):
        main(args)
    assert path.read_bytes() == saved


def test_interrupted_plan_path_cannot_be_overwritten(tmp_path):
    from tools.research_calibration import main

    destination = tmp_path / "receipt.json"
    frozen = tmp_path / "receipt.json.plan.json"
    frozen.write_text("original incomplete declaration")
    with pytest.raises(SystemExit):
        main(
            [
                "--runner",
                "missing:callback",
                "--runner-identity",
                "x",
                "--code-identity",
                "y",
                "--execution-model",
                "native_synthetic_market",
                "--whole-procedure-seeds",
                "--output",
                str(destination),
            ]
        )
    assert frozen.read_text() == "original incomplete declaration"
