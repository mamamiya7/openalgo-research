"""Exact no-effect rejection, charged probe recovery and unchanged validation gates."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from database.research_db import ResearchRequest, ResearchStore, ResearchSupervisor
from research.report_contract import fingerprint, settings_identity
from services import research_supervisor as supervisor
from services import research_supervisor_program as program


def report():
    return {
        "summary": {"initial_capital": 100000, "net_pnl": 12},
        "equity_curve": [{"date": "2026-01-02", "equity": 100012, "cash": 100012}],
        "ledger": [
            {
                "symbol": "ABC",
                "quantity": 1,
                "status": "closed",
                "fees": 2,
                "pnl": 12,
                "execution_details": {"filled_quantity": 1},
            }
        ],
        "per_strategy": [{"id": "one", "net_pnl": 12}],
        "engine_records": {"orders": [{"size": 1, "price": 100, "fees": 1}]},
        "coverage": {"id": "prices"},
        "missing_marks": [],
        "evaluation_basis": {"id": "same-frozen-window"},
        "research_opportunity_basis": {"id": "same-opportunities"},
        "automatic_stock_condition": {"hypothesis": {"id": "none"}},
        "portfolio_constraint_evidence": {"status": "supported"},
        "portfolio": {"capital": 100000, "engine": "vectorbt"},
        "strategies": [
            {
                "id": "one",
                "name": "ABC",
                "allocation_pct": 100,
                "config": {"initial_capital": 100000},
                "execution_policy": {"allocation_mode": "original"},
            }
        ],
    }


def proposal():
    return {
        "remedy": "reduce_concentration",
        "seed_job_id": "a" * 32,
        "source_result_artifact": "b" * 64,
    }


def changed(seed):
    value = deepcopy(seed)
    value["strategies"][0]["execution_policy"]["allocation_mode"] = (
        "active_prior60_cluster_inverse_vol"
    )
    value["ledger"][0]["execution_details"]["allocation_weight"] = 1.0
    value["ledger"][0]["execution_details"]["allocation_feature_id"] = "different-policy-receipt"
    return value


def test_exact_economic_noop_is_rejected_without_claiming_validation():
    seed = report()
    value = changed(seed)
    result = program._no_effect_conclusion(
        value, seed, proposal(), {"validation_mode": "exploratory_only"}
    )
    assert result["outcome"] == "rejected"
    assert not result["historically_validated"] and not result["independently_validated"]
    assert result["accounts"] == 0 and result["diagnostics"] == []
    assert result["no_effect"]["probe_accounts"] == 1
    assert result["no_effect"]["additional_validation_accounts"] == 0
    assert result["selected_report_id"] == fingerprint(value)
    assert result["id"] == fingerprint({k: v for k, v in result.items() if k != "id"})
    assert result["no_effect"]["id"] == fingerprint(
        {k: v for k, v in result["no_effect"].items() if k != "id"}
    )
    assert seed == report()


@pytest.mark.parametrize(
    "field",
    [
        "summary",
        "equity_curve",
        "ledger",
        "per_strategy",
        "engine_records",
        "coverage",
        "missing_marks",
        "evaluation_basis",
        "research_opportunity_basis",
        "automatic_stock_condition",
    ],
)
def test_equal_profit_cannot_hide_execution_or_input_changes(field):
    seed = report()
    value = changed(seed)
    if isinstance(value[field], dict):
        value[field]["changed"] = True
    else:
        value[field].append({"changed": True})
    assert (
        program._no_effect_conclusion(
            value, seed, proposal(), {"validation_mode": "exploratory_only"}
        )
        is None
    )


def test_unknown_detail_and_nonallocation_constraint_changes_are_not_ignored():
    seed = report()
    for key in ("allocation_future_unknown", "filled_quantity", "daily_buy_budget"):
        value = changed(seed)
        value["ledger"][0]["execution_details"][key] = 2
        assert (
            program._no_effect_conclusion(
                value, seed, proposal(), {"validation_mode": "exploratory_only"}
            )
            is None
        )


def test_settings_bound_gate_receipts_do_not_hide_exact_economic_equality():
    seed = report()
    seed["automatic_stock_condition"]["gate_id"] = "a" * 64
    value = changed(seed)
    value["automatic_stock_condition"]["gate_id"] = "b" * 64
    result = program._no_effect_conclusion(
        value, seed, proposal(), {"validation_mode": "exploratory_only"}
    )
    assert result["no_effect"]["seed_gate_id"] == "a" * 64
    assert result["no_effect"]["selected_gate_id"] == "b" * 64
    value["automatic_stock_condition"]["hypothesis"] = {"id": "different-filter"}
    assert (
        program._no_effect_conclusion(
            value, seed, proposal(), {"validation_mode": "exploratory_only"}
        )
        is None
    )


@pytest.mark.parametrize(
    "mode,remedy",
    [
        ("chronological", "reduce_concentration"),
        ("historical_only", "lower_turnover"),
        ("exploratory_only", "funding_and_size"),
    ],
)
def test_funding_and_nonexploratory_validation_are_never_shortened(mode, remedy):
    seed = report()
    p = {**proposal(), "remedy": remedy}
    assert program._no_effect_conclusion(changed(seed), seed, p, {"validation_mode": mode}) is None


@pytest.mark.parametrize("missing", ["engine_records", "ledger", "evaluation_basis"])
def test_incomplete_economic_evidence_cannot_prove_no_effect(missing):
    seed = report()
    value = changed(seed)
    seed.pop(missing)
    assert (
        program._no_effect_conclusion(
            value, seed, proposal(), {"validation_mode": "exploratory_only"}
        )
        is None
    )


def test_probe_is_charged_and_reopens_without_challenges_or_recalculation(tmp_path, monkeypatch):
    from services import research_portfolio

    store = ResearchStore(tmp_path / "isolated")
    store.initialize()
    seed = report()
    seed["automatic_design"] = {"largest_symbol_positive_profit_fraction": 0.7}
    recipe = {
        "version": "automatic-hypotheses-v16",
        "validation_mode": "exploratory_only",
        "ranges": [],
    }
    seed["automatic_research"] = {
        "recipe": recipe,
        "counts": {"attempts_reserved": 2, "simulations": 2},
    }
    root = SimpleNamespace(id="a" * 32, result_artifact="b" * 64)
    original = {"portfolio": {"engine": "vectorbt", "capital": 100000}, "versions": {}}
    monkeypatch.setattr(
        supervisor,
        "_parent",
        lambda *a: (root, {"inputs_artifact": "c" * 64}, deepcopy(seed), deepcopy(recipe)),
    )
    monkeypatch.setattr(supervisor.service, "get_job", lambda *a: root)
    monkeypatch.setattr(program.service, "read_artifact", lambda *a: deepcopy(original))
    monkeypatch.setattr(program.service, "save_artifact", lambda *a: "d" * 64)
    monkeypatch.setattr(research_portfolio, "validate_submission", lambda *a: None)
    monkeypatch.setattr(
        research_portfolio, "_prepare_inputs", lambda *a, **kw: (deepcopy(original), None, None)
    )
    monkeypatch.setattr(
        program,
        "validate",
        lambda *a, **kw: pytest.fail("No-effect candidate ran challenge validation"),
    )
    calls = []

    class Accounts:
        def __init__(self, *a):
            self.context = SimpleNamespace(catalog=lambda: [{"id": "none"}])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def calculate(self, settings, *a):
            calls.append(1)
            value = changed(seed)
            value.pop("automatic_research")
            value["strategies"] = deepcopy(settings)
            return value

        def audit(self, value, request):
            assert settings_identity(value["strategies"]) == settings_identity(request["settings"])
            return value["portfolio_constraint_evidence"]

    monkeypatch.setattr("research.supervisor_accounts.FrozenAccounts", Accounts)
    owner, conversation = "small-owner", "small-chat"
    try:
        opened = supervisor.open_supervisor(
            store, owner, root.id, conversation_id=conversation, request_id="noop-small-case"
        )
        p = supervisor.propose(
            store,
            owner,
            opened["id"],
            "reduce_concentration",
            "Native concentration failure",
            conversation_id=conversation,
        )["proposal"]
        child = "e" * 32
        with store.sessions.begin() as db:
            db.add(
                ResearchRequest(
                    owner=owner, token="supervisor_" + p["id"], job_id=child, payload_hash="f" * 64
                )
            )
        evidence = {
            "supervisor_program": {
                "version": supervisor.VERSION,
                "supervisor_id": opened["id"],
                "proposal_id": p["id"],
                "root_job_id": root.id,
            }
        }
        snapshots = []
        kwargs = {
            "checkpoint": lambda value, *_: snapshots.append(deepcopy(value)),
            "progress": lambda *a: None,
            "cancelled": lambda: None,
            "job_id": child,
        }
        result, _ = program.run(store, owner, evidence, {}, saved=None, **kwargs)
        value = result["supervisor_validation"]
        assert len(calls) == 1 and value["no_effect"]["status"] == "unchanged"
        assert value["operating_validation"]["accounts"] == 0
        assert (
            supervisor._verified_conclusion(result, opened["id"], p["id"], root.id, store, owner)
            == value
        )
        before = supervisor.status(store, owner, opened["id"], conversation_id=conversation)[
            "proposals"
        ][0]
        assert before["spent"] == before["completed"] == 1
        restored, _ = program.run(store, owner, evidence, {}, saved=snapshots[-1], **kwargs)
        assert restored == result and len(calls) == 1
        after = supervisor.status(store, owner, opened["id"], conversation_id=conversation)[
            "proposals"
        ][0]
        assert after["spent"] == after["completed"] == 1
    finally:
        store.close()


def blocked_evidence():
    chosen = report()
    selected_id = settings_identity(chosen["strategies"])
    plan = {
        "selected_config_id": selected_id,
        "capital": chosen["summary"]["initial_capital"],
        "inputs": {"capacity_counts": {"admitted_requests": 21, "percentage_policy": 41}},
        "unresolved_constraints": {"percentage_policy": 41},
    }
    plan["id"] = fingerprint(plan)
    conclusion = {
        "version": "research-operating-validation-v1",
        "outcome": "blocked",
        "historically_validated": False,
        "independently_validated": False,
        "plan_id": plan["id"],
        "selected_config_id": selected_id,
        "capital": chosen["summary"]["initial_capital"],
        "selected_report_id": fingerprint(chosen),
        "reasons": ["Native percentage sizing constraints remain unresolved"],
    }
    conclusion["id"] = fingerprint(conclusion)
    return chosen, conclusion, plan


def test_blocked_feasibility_is_bound_to_native_settings_and_outer_seal():
    chosen, conclusion, plan = blocked_evidence()
    result = program._validated_report(
        chosen,
        conclusion,
        {"validation_mode": "exploratory_only"},
        {"supervisor_id": "supervisor", "proposal_id": "proposal"},
        "root",
        feasibility_plan=plan,
    )
    value = result["supervisor_validation"]
    assert value["outcome"] == "blocked" and not value["historically_validated"]
    assert not value["independently_validated"]
    assert value["operating_validation"] == conclusion
    assert value["feasibility"] == {
        "version": "research-feasibility-v1",
        "source_settings_id": settings_identity(chosen["strategies"]),
        "counts": {"admitted_requests": 21, "percentage_policy": 41},
        "unresolved_constraints": {"percentage_policy": 41},
        "scope": "feasibility_only",
    }
    assert value["id"] == fingerprint({k: v for k, v in value.items() if k != "id"})
    plan["inputs"]["capacity_counts"]["percentage_policy"] = 0
    assert value["feasibility"]["counts"]["percentage_policy"] == 41
    altered = deepcopy(value)
    altered["feasibility"]["counts"]["percentage_policy"] = 0
    assert altered["id"] != fingerprint({k: v for k, v in altered.items() if k != "id"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("selected_config_id", "other"),
        ("capital", 99999),
        ("id", "other"),
        ("inputs", {"capacity_counts": "unknown"}),
        ("unresolved_constraints", []),
    ],
)
def test_inconsistent_feasibility_plan_cannot_be_exported(field, value):
    chosen, conclusion, plan = blocked_evidence()
    plan[field] = value
    with pytest.raises(ValueError, match="does not match"):
        program._validated_report(
            chosen,
            conclusion,
            {"validation_mode": "exploratory_only"},
            {"supervisor_id": "s", "proposal_id": "p"},
            "root",
            feasibility_plan=plan,
        )


def test_missing_native_capacity_is_preserved_as_unavailable_not_invented():
    chosen, conclusion, plan = blocked_evidence()
    plan["inputs"]["capacity_counts"] = None
    plan["unresolved_constraints"] = {}
    plan["id"] = fingerprint({k: v for k, v in plan.items() if k != "id"})
    conclusion["plan_id"] = plan["id"]
    result = program._validated_report(
        chosen,
        conclusion,
        {"validation_mode": "exploratory_only"},
        {"supervisor_id": "s", "proposal_id": "p"},
        "root",
        feasibility_plan=plan,
    )
    assert result["supervisor_validation"]["feasibility"]["counts"] is None
    assert result["supervisor_validation"]["outcome"] == "blocked"


@pytest.mark.parametrize(
    "outcome,remedy,mode,allowed",
    [
        ("blocked", "funding_and_size", "exploratory_only", True),
        ("blocked", "lower_turnover", "exploratory_only", False),
        ("blocked", "reduce_concentration", "exploratory_only", False),
        ("blocked", "temporal_stability", "exploratory_only", False),
        ("blocked", "funding_and_size", "chronological", False),
        ("blocked", "funding_and_size", "historical_only", False),
        ("validated", "funding_and_size", "exploratory_only", False),
    ],
)
def test_blocked_seed_is_feasibility_only_without_later_period_selection(
    outcome, remedy, mode, allowed
):
    if allowed:
        assert program._requires_feasibility_seed(
            {"outcome": outcome}, {"remedy": remedy}, {"validation_mode": mode}
        )
    else:
        with pytest.raises(ValueError):
            program._requires_feasibility_seed(
                {"outcome": outcome}, {"remedy": remedy}, {"validation_mode": mode}
            )


def test_rejected_seed_retains_ordinary_development_authority():
    assert (
        program._requires_feasibility_seed(
            {"outcome": "rejected"},
            {"remedy": "lower_turnover"},
            {"validation_mode": "exploratory_only"},
        )
        is False
    )


def test_matching_fills_with_different_stop_risk_still_require_validation():
    seed = report()
    seed["ledger"][0]["execution_details"]["total_stop_risk_before"] = 721.39
    value = changed(seed)
    value["ledger"][0]["execution_details"]["total_stop_risk_before"] = 718.68
    assert (
        program._no_effect_conclusion(
            value, seed, proposal(), {"validation_mode": "exploratory_only"}
        )
        is None
    )
