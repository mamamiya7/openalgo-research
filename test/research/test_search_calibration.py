"""Contract mathematics/coverage tests; callbacks here are not actual optimisers."""

from copy import deepcopy

import pytest

from research.report_contract import fingerprint
from research.search_calibration import (
    binomial_upper,
    compile_plan,
    run,
    verify_plan,
    verify_receipt,
)


def plan(**kwargs):
    return compile_plan(
        code_identity="test-code", runner_identity="contract-fixture-only", **kwargs
    )


def outcome(case, declaration, *, changed=None, completed_seeds=True):
    if changed is None:
        changed = case["kind"] == "power"
    return {
        "case_id": case["id"],
        "status": "completed",
        "baseline_config_id": "baseline",
        "selected_config_id": "changed" if changed else "baseline",
        "proposal_count": declaration[
            "proposal_budget" if completed_seeds else "primary_proposal_budget"
        ],
        "seed_checks_status": "completed" if completed_seeds else "not_run",
        "account_count": 400 if completed_seeds else 270,
        "sampler_seeds": declaration["sampler_seeds"],
        "procedure_completed": True,
        "development_selection_frozen": True,
        "final_excluded_from_selection": True,
        "evidence_id": fingerprint(case),
    }


def reseal(value):
    value["id"] = fingerprint({key: item for key, item in value.items() if key != "id"})
    return value


def test_predeclares_independent_data_seeds_and_bounded_model_scope():
    declaration = plan()
    assert verify_plan(declaration) == declaration
    assert len(declaration["cases"]) == 20
    data_seeds = [case["data_seed"] for case in declaration["cases"]]
    assert len(set(data_seeds)) == 20
    assert not set(data_seeds) & set(declaration["sampler_seeds"])
    assert declaration["execution_model"] == "controlled_account_model"
    assert "Not general" in declaration["limitation"]
    assert declaration == plan()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"replicates": 0},
        {"replicates": 33},
        {"replicates": True},
        {"sampler_seeds": (0, 0)},
        {"sampler_seeds": (0,)},
        {"sampler_seeds": (0, True)},
        {"max_accounts": 0},
        {"proposal_budget": 0},
        {"execution_model": "live_broker"},
        {"primary_proposal_budget": 409},
    ],
)
def test_invalid_or_unbounded_declarations_rejected(kwargs):
    with pytest.raises(ValueError):
        plan(**kwargs)


def test_complete_suite_retains_every_case_and_stratifies_not_trials():
    declaration = plan(replicates=4)
    receipt = run(declaration, outcome)
    assert verify_receipt(receipt) == receipt
    assert receipt["status"] == "passed"
    assert len(receipt["cases"]) == 20
    for stratum in receipt["strata"][:4]:
        assert stratum["planned_runs"] == stratum["completed_runs"] == 4
        assert stratum["false_recommendations"] == 0
        assert stratum["observed_rate"] == 0
        assert stratum["one_sided_95pct_upper"] == pytest.approx(1 - 0.05**0.25)
    assert receipt["strata"][-1]["false_recommendations"] is None
    assert receipt["strata"][-1]["one_sided_95pct_upper"] is None


def test_declared_early_gate_skip_is_complete_not_truncated():
    receipt = run(
        plan(),
        lambda case, declaration: outcome(
            case, declaration, completed_seeds=case["kind"] == "power"
        ),
    )
    assert receipt["status"] == "passed"
    assert verify_receipt(receipt) == receipt


def test_false_recommendation_fails_qualification_without_hiding_denominator():
    receipt = run(
        plan(),
        lambda case, declaration: outcome(
            case, declaration, changed=case["kind"] == "power" or case["replicate"] == 0
        ),
    )
    assert receipt["status"] == "failed"
    assert receipt["qualified_scope"] is None
    assert all(
        row["false_recommendations"] == 1 and row["observed_rate"] == 0.25
        for row in receipt["strata"][:4]
    )
    assert verify_receipt(receipt) == receipt


def test_always_baseline_cannot_pass_positive_control():
    receipt = run(plan(), lambda case, declaration: outcome(case, declaration, changed=False))
    assert receipt["status"] == "failed"
    assert receipt["strata"][-1]["status"] == "failed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("proposal_count", 0),
        ("proposal_count", 407),
        ("account_count", 513),
        ("account_count", True),
        ("procedure_completed", False),
        ("development_selection_frozen", False),
        ("final_excluded_from_selection", False),
        ("sampler_seeds", [0, 2, 1]),
        ("case_id", "another"),
        ("evidence_id", ""),
        ("seed_checks_status", "pending"),
    ],
)
def test_partial_or_incompatible_procedure_not_safe_rejection(field, value):
    def runner(case, declaration):
        result = outcome(case, declaration)
        if case["scenario"] == "independent_null" and case["replicate"] == 0:
            result[field] = value
        return result

    receipt = run(plan(), runner)
    assert receipt["status"] == "incomplete"
    assert receipt["strata"][0]["completed_runs"] == 3
    assert receipt["strata"][0]["one_sided_95pct_upper"] is None
    assert verify_receipt(receipt) == receipt


def test_callback_errors_are_retained_not_omitted_and_do_not_leak_text():
    calls = []

    def runner(case, declaration):
        calls.append(case["id"])
        if case["replicate"] == 0:
            raise RuntimeError("private-credential-string")
        return outcome(case, declaration)

    receipt = run(plan(), runner)
    assert receipt["status"] == "incomplete"
    assert len(calls) == 20
    assert sum(row["status"] == "error" for row in receipt["cases"]) == 5
    assert "private-credential" not in str(receipt)
    assert verify_receipt(receipt) == receipt


@pytest.mark.parametrize("count", [1, 4, 20, 32])
def test_zero_event_bound_is_exact_and_not_zero_confidence(count):
    assert binomial_upper(0, count) == pytest.approx(1 - 0.05 ** (1 / count))
    assert binomial_upper(0, count) > 0


def test_nonzero_exact_binomial_bound_solves_lower_tail():
    import math

    probability = binomial_upper(2, 20)
    tail = sum(
        math.comb(20, index) * probability**index * (1 - probability) ** (20 - index)
        for index in range(3)
    )
    assert tail == pytest.approx(0.05)
    assert probability > 0.1
    assert binomial_upper(20, 20) == 1
    assert binomial_upper(0, 20) == pytest.approx(0.13910834066826516)


@pytest.mark.parametrize(
    "args",
    [
        (0, 0),
        (2, 1),
        (-1, 20),
        (True, 20),
        (0, True),
        (0, 33),
        (0, 20, float("nan")),
        (0, 20, 1),
        (0, 20, True),
    ],
)
def test_invalid_binomial_inputs(args):
    with pytest.raises(ValueError):
        binomial_upper(*args)


def test_rehashed_plan_cannot_drop_a_case_or_change_independence_claim():
    declaration = plan()
    declaration["cases"].pop()
    with pytest.raises(ValueError):
        verify_plan(reseal(declaration))
    declaration = plan()
    declaration["independence"] = "99% confidence in a live strategy"
    with pytest.raises(ValueError):
        verify_plan(reseal(declaration))


@pytest.mark.parametrize("mutation", ["bound", "omit", "duplicate", "model", "relabel"])
def test_receipt_recomputation_rejects_rehashed_summary_tampering(mutation):
    receipt = run(plan(), outcome)
    if mutation == "bound":
        receipt["strata"][0]["one_sided_95pct_upper"] = 0
    elif mutation == "omit":
        receipt["cases"].pop()
    elif mutation == "duplicate":
        receipt["cases"][1] = deepcopy(receipt["cases"][0])
    elif mutation == "model":
        receipt["plan"]["execution_model"] = "native_synthetic_market"
    else:
        receipt["qualified_scope"] = "live_probability_of_profit"
    with pytest.raises(ValueError):
        verify_receipt(reseal(receipt))


def test_callbacks_receive_copies_not_mutable_frozen_plan():
    declaration = plan()
    original = deepcopy(declaration)

    def runner(case, copied):
        result = outcome(case, copied)
        copied.clear()
        case.clear()
        return result

    assert run(declaration, runner)["status"] == "passed"
    assert declaration == original


def test_adversarial_sampler_seed_near_data_seed_cannot_create_replicate_collision():
    declaration = plan(replicates=32, sampler_seeds=(0, 1_001_012, 1_001_013))
    seeds = [case["data_seed"] for case in declaration["cases"]]
    assert len(seeds) == len(set(seeds)) == 160
    assert not set(seeds) & set(declaration["sampler_seeds"])


def test_cli_runs_explicit_callback_and_exclusively_preserves_receipt(tmp_path, monkeypatch):
    import json
    import sys
    from types import ModuleType

    from tools.research_calibration import main

    module = ModuleType("calibration_contract_fixture")
    module.run_case = outcome
    monkeypatch.setitem(sys.modules, module.__name__, module)
    destination = tmp_path / "receipt.json"
    arguments = [
        "--runner",
        f"{module.__name__}:run_case",
        "--runner-identity",
        "contract-fixture-not-native",
        "--code-identity",
        "contract-fixture-code",
        "--execution-model",
        "controlled_account_model",
        "--replicates",
        "1",
        "--output",
        str(destination),
    ]
    assert main(arguments) == 0
    saved = destination.read_bytes()
    receipt = verify_receipt(json.loads(saved))
    assert receipt["plan"]["execution_model"] == "controlled_account_model"
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    assert destination.read_bytes() == saved


def test_cli_failure_keeps_all_cases_and_returns_nonzero(tmp_path, monkeypatch):
    import json
    import sys
    from types import ModuleType

    from tools.research_calibration import main

    module = ModuleType("failed_calibration_contract_fixture")
    module.run_case = lambda case, declaration: {"case_id": case["id"], "status": "incomplete"}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    destination = tmp_path / "failed.json"
    assert (
        main(
            [
                "--runner",
                f"{module.__name__}:run_case",
                "--runner-identity",
                "contract-fixture-not-native",
                "--code-identity",
                "contract-fixture-code",
                "--execution-model",
                "controlled_account_model",
                "--replicates",
                "1",
                "--output",
                str(destination),
            ]
        )
        == 1
    )
    receipt = verify_receipt(json.loads(destination.read_text(encoding="utf-8")))
    assert receipt["status"] == "incomplete"
    assert len(receipt["cases"]) == 5
