"""Observed matches need a fresh, unchanged failed native gate before pruning."""

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_operating_validation import Engine, audit, plan, report
from test_supervisor_effect import proposal
from test_supervisor_effect import report as economic_report

from research.economic_repetition import observed, trade_observation
from research.operating_validation import run as validate
from research.report_contract import fingerprint
from services import research_supervisor_program as program


def augmented(rows, profit=5, hypothesis=None):
    value = report(rows, profit, hypothesis)
    economic = economic_report()
    value.update(
        {key: val for key, val in economic.items() if key not in value and key != "strategies"}
    )
    value["policy_version"] = "fixed-native-policy"
    return value


class ObservedEngine(Engine):
    def __call__(self, rows, hypothesis, period, stress):
        value = super().__call__(rows, hypothesis, period, stress)
        economic = economic_report()
        value.update(
            {key: val for key, val in economic.items() if key not in value and key != "strategies"}
        )
        value["policy_version"] = "fixed-native-policy"
        return value


def prior():
    value = plan()
    selected = augmented(value["selected_settings"])
    conclusion, state = validate(
        value, selected_report=selected, evaluate=ObservedEngine(bad_cost=True), audit=audit
    )
    assert conclusion["outcome"] == "rejected"
    return {
        "job_id": "old-checked-child",
        "artifact": "a" * 64,
        "checkpoint": "b" * 64,
        "report": selected,
        "plan": value,
        "validation": state,
    }


def candidate(previous):
    inputs = deepcopy(previous["plan"]["inputs"])
    inputs["operating_point"]["settings"][0]["config"]["target_pct"] = 11
    from research.operating_validation import build_plan

    value = build_plan(**inputs)
    chosen = augmented(value["selected_settings"])
    engine = ObservedEngine(bad_cost=True)
    state = {
        "version": "research-operating-validation-v1",
        "plan_id": value["id"],
        "selected_report_id": fingerprint(chosen),
        "reports": {},
        "active_request": None,
        "conclusion": None,
    }
    for request in value["requests"][:2]:
        native = engine(
            deepcopy(request["settings"]),
            request["hypothesis"],
            request["period"],
            request["stress"],
        )
        state["reports"][request["id"]] = {"report": native, "report_id": fingerprint(native)}
    return chosen, value, state, engine


def test_matching_normal_fills_are_observations_not_validation_equivalence():
    seed = economic_report()
    changed = deepcopy(seed)
    changed["ledger"][0]["execution_details"]["total_stop_risk_before"] = 100
    assert observed(seed) == observed(changed)
    changed["summary"]["initial_capital"] = 200000
    assert observed(seed) != observed(changed)
    assert trade_observation(seed) == trade_observation(changed)
    changed["ledger"][0]["fees"] += 1
    assert trade_observation(seed) != trade_observation(changed)


@pytest.mark.parametrize(
    "field",
    [
        "engine_records",
        "coverage",
        "missing_marks",
        "evaluation_basis",
        "research_opportunity_basis",
    ],
)
def test_unknown_financial_or_input_difference_cannot_be_ignored(field):
    seed = economic_report()
    changed = deepcopy(seed)
    if isinstance(changed[field], dict):
        changed[field]["unknown"] = 1
    else:
        changed[field].append("unknown")
    assert observed(seed) != observed(changed)


def test_matching_failed_cost_gate_rejects_without_claiming_full_validation():
    previous = prior()
    chosen, value, state, _ = candidate(previous)
    effect = program._repeated_cost_conclusion(chosen, value, state, previous, proposal(), audit)
    assert effect["outcome"] == "rejected" and not effect["historically_validated"]
    assert effect["accounts"] == 2 and effect["diagnostics"] == []
    proof = effect["no_effect"]
    assert proof["kind"] == "repeated_failed_gate" and proof["probe_accounts"] == 3
    assert proof["skipped_validation_accounts"] == 11
    assert (
        proof["full_validation_completed"] is False
        and proof["all_scenario_equivalence_proven"] is False
    )
    assert proof["matched_job_id"] == previous["job_id"]
    restored = json.loads(json.dumps(effect))
    assert restored == effect and effect["id"] == fingerprint(
        {k: v for k, v in effect.items() if k != "id"}
    )


@pytest.mark.parametrize(
    "difference",
    [
        "capital",
        "baseline",
        "hypothesis",
        "cost_cashflow",
        "cost_quantity",
        "normal_cashflow",
        "native_constraints",
    ],
)
def test_normal_match_cannot_prune_changed_stress_capital_or_constraint_results(difference):
    previous = prior()
    chosen, value, state, _ = candidate(previous)
    cost = state["reports"][value["requests"][1]["id"]]["report"]
    if difference == "capital":
        value["requests"][0]["settings"][0]["config"]["initial_capital"] += 1
    elif difference == "baseline":
        value["requests"][0]["hypothesis"] = {"id": "different"}
    elif difference == "hypothesis":
        value["inputs"]["hypothesis"] = {"id": "different"}
    elif difference == "cost_cashflow":
        cost["summary"]["net_return_pct"] += 1
    elif difference == "cost_quantity":
        cost["ledger"][0]["quantity"] += 1
    elif difference == "normal_cashflow":
        chosen["summary"]["net_return_pct"] += 1
    else:
        cost["portfolio_constraint_evidence"] = {"status": "incomplete"}
        previous["validation"]["reports"][previous["plan"]["requests"][1]["id"]]["report"][
            "portfolio_constraint_evidence"
        ] = {"status": "incomplete"}
    assert (
        program._repeated_cost_conclusion(
            chosen,
            value,
            state,
            previous,
            proposal(),
            lambda row, request: row["portfolio_constraint_evidence"],
        )
        is None
    )


def test_no_witness_resumes_exact_prefix_without_extra_accounts_or_weaker_checks():
    previous = prior()
    chosen, value, state, engine = candidate(previous)
    old_cost = previous["validation"]["reports"][previous["plan"]["requests"][1]["id"]]["report"]
    old_cost["summary"]["net_return_pct"] -= 1
    assert (
        program._repeated_cost_conclusion(chosen, value, state, previous, proposal(), audit) is None
    )
    assert len(engine.calls) == 2
    conclusion, restored = validate(
        value, selected_report=chosen, evaluate=engine, audit=audit, saved=state
    )
    assert len(engine.calls) == 13 and len(restored["reports"]) == 13
    assert conclusion["accounts"] == 13 and conclusion["outcome"] == "rejected"
    assert len(conclusion["diagnostics"]) == 3


def test_strict_or_blocked_plans_do_not_allow_early_rejection():
    previous = prior()
    chosen, value, state, _ = candidate(previous)
    value["blocked_reasons"] = ["Full reserve unavailable"]
    assert (
        program._repeated_cost_conclusion(chosen, value, state, previous, proposal(), audit) is None
    )
    value["blocked_reasons"] = []
    value["inputs"]["validation_mode"] = "chronological"
    assert (
        program._repeated_cost_conclusion(chosen, value, state, previous, proposal(), audit) is None
    )


@pytest.mark.parametrize("missing", [None, "checkpoint", "policy", "identity", "wrong_owner"])
def test_prior_source_must_be_owned_sealed_and_completely_replayable(tmp_path, missing):
    from database.research_db import (
        ResearchExperiment,
        ResearchJob,
        ResearchStore,
        ResearchSupervisor,
    )
    from services import scanner_research_service as service

    store = ResearchStore(tmp_path / "isolated")
    store.initialize()
    old = prior()
    chosen, _, _, _ = candidate(old)
    owner, supervisor_id, root_id, child_id = "owner", "a" * 32, "b" * 32, "c" * 32
    recipe = {"version": "automatic-hypotheses-v16", "validation_mode": "exploratory_only"}
    root_report = augmented(old["plan"]["baseline_settings"], profit=1)
    root_report["automatic_research"] = {
        "recipe": recipe,
        "counts": {"attempts_reserved": 2, "simulations": 2},
    }
    try:
        root_artifact = service.save_artifact(store, {"result": root_report})
        marker = {
            "supervisor_id": supervisor_id,
            "proposal_id": "old-proposal",
            "root_job_id": root_id,
        }
        completed = program._validated_report(
            old["report"], old["validation"]["conclusion"], recipe, marker, root_artifact
        )
        artifact = service.save_artifact(store, {"result": completed})
        checkpoint = service.save_artifact(
            store,
            {
                "identity": "d" * 64,
                "policy_version": "fixed-native-policy"
                if missing != "policy"
                else "other-native-policy",
                "state": {
                    "phase": "calculation",
                    "calculation": {
                        "proposal_id": "old-proposal",
                        "source_result_artifact": root_artifact,
                        "validation_plan": old["plan"],
                        "validation": old["validation"],
                    },
                },
            },
        )
        now = time.time()
        with store.sessions.begin() as db:
            for job_id, result, job_owner in (
                (root_id, root_artifact, owner),
                (child_id, artifact, "other-owner" if missing == "wrong_owner" else owner),
            ):
                db.add(
                    ResearchJob(
                        id=job_id,
                        owner=job_owner,
                        source_id="source",
                        config="{}",
                        status="completed",
                        progress=100,
                        result_artifact=result,
                        created_at=now,
                        updated_at=now,
                    )
                )
            db.add(
                ResearchExperiment(
                    job_id=child_id,
                    kind="portfolio_backtest",
                    specification="{}",
                    identity="e" * 64 if missing == "identity" else "d" * 64,
                    checkpoint=None if missing == "checkpoint" else checkpoint,
                )
            )
            db.add(
                ResearchSupervisor(
                    id=supervisor_id,
                    owner=owner,
                    root_job_id=root_id,
                    root_artifact=root_artifact,
                    conversation_id="conversation",
                    request_id="source-proof-test",
                    updated_at=now,
                    state=json.dumps(
                        {
                            "proposals": [
                                {"id": "old-proposal", "outcome": "rejected", "job_id": child_id}
                            ]
                        }
                    ),
                )
            )
        accounts = SimpleNamespace(audit=audit)
        marker = {
            "supervisor_id": supervisor_id,
            "proposal_id": "new-proposal",
            "root_job_id": root_id,
        }
        if missing == "wrong_owner":
            with pytest.raises(LookupError):
                list(program._repeat_sources(store, owner, marker, chosen, accounts))
        else:
            sources = list(program._repeat_sources(store, owner, marker, chosen, accounts))
            assert len(sources) == (1 if missing is None else 0)
            if sources:
                assert (
                    sources[0]["artifact"] == artifact
                    and sources[0]["validation"] == old["validation"]
                )
    finally:
        store.close()
