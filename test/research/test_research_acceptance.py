"""Acceptance-proof tests only; controlled receipts establish no market edge."""

from copy import deepcopy

import pytest
from test_automatic_decision import account
from test_automatic_design import BINS, OPPORTUNITY_DATES, balanced
from test_research_playbook import reviewed

from research.automatic_decision import conclude
from research.automatic_decision import descriptor as decision_policy
from research.automatic_design import assess
from research.automatic_design import descriptor as design_policy
from research.automatic_protocol import compile_recipe
from research.report_contract import fingerprint, settings_identity
from services.research_playbook import (
    HISTORICAL_DISCLOSURE,
    describe,
    validate_full_search_result,
)


def seal(value):
    return {**value, "id": fingerprint({k: v for k, v in value.items() if k != "id"})}


def accepted(*, changed=True, strict=False):
    evidence = reviewed()
    contract = describe(evidence["portfolio"])
    recipe = compile_recipe(evidence)
    if strict:
        recipe = seal({**recipe, "validation_mode": "historical_only"})
    native = balanced()
    native.update(
        strategies=deepcopy(evidence["portfolio"]["strategies"]),
        execution={"engine": "vectorbt"},
        engine_records={"orders": []},
        policy_version="controlled-proof-test",
        evaluation_basis={"evidence_id": "frozen-inputs"},
        automatic_stock_condition={"hypothesis": {"id": "none"}},
    )
    native["summary"]["initial_capital"] = evidence["portfolio"]["capital"]
    native["automatic_design"] = assess(
        native, BINS, design_policy(), opportunity_dates=OPPORTUNITY_DATES
    )
    projection = {
        "policy_version": native["policy_version"],
        "ledger": native["ledger"],
        "strategies": native["strategies"],
        "execution": native["execution"],
        "orders": [],
    }
    native["portfolio_constraint_evidence"] = seal(
        {
            "version": "research-portfolio-constraint-evidence-v1",
            "status": "supported",
            "reasons": [],
            "report_evidence_id": fingerprint(projection),
            "risk_limits": deepcopy(contract["risk_limits"]),
        }
    )
    config_id = settings_identity(native["strategies"])
    chosen = account(5)
    chosen["config_id"] = config_id
    prefixes = [
        {
            "period": f"fit{i}",
            "summary": deepcopy(chosen["summary"]),
            "entry_dates": 30,
            "independently_validated": False,
        }
        for i in range(1, 4)
    ]
    decision = conclude(
        policy=decision_policy(),
        selected=chosen,
        baseline=account(-1),
        stress=account(3),
        stress_baseline=account(-2),
        neighbors=[account(4)] * 4,
        prefixes=prefixes if not changed and not strict else [],
        seed_choices=[],
        is_baseline=False,
    )
    verdict = {
        "version": "research-operating-validation-v1",
        "outcome": "validated" if changed else "unchanged",
        "capital": native["summary"]["initial_capital"],
        "selected_config_id": config_id,
        "historically_validated": True,
        "independently_validated": False,
        "reasons": [] if changed else decision["reasons"],
        "accounts": 21 if changed and strict else 13 if changed else 0,
    }
    if changed:
        verdict.update(
            selected_report_id=fingerprint(native),
            reused_selected_report_id=fingerprint(native),
            selection_changed=False,
            decision=deepcopy(decision),
            diagnostics=[
                {
                    "period": f"{'check' if strict else 'fit'}{i}",
                    "stress": stress,
                    "passed": True,
                    "reasons": [],
                    "independently_validated": False,
                }
                for i in range(1, 4)
                for stress in ((False, True) if strict else (False,))
            ],
            chronological={"status": "supported", "qualified": True, "reasons": []}
            if strict
            else None,
        )
    else:
        verdict["primary_decision_id"] = decision["id"]
    findings = {
        "recipe": recipe,
        "decision": decision,
        "operating_validation": seal(verdict),
        "selected_config_id": config_id,
        "counts": {"proposals": 408, "simulations": 440, "attempts_reserved": 440},
    }
    if strict:
        findings.update(
            walk_forward={"folds": [{"eligible": True, "reasons": []} for _ in range(3)]},
            final={"historical_checks_passed": True},
            recommendation_checks={"status": "supported", "qualified": True},
        )
    report = deepcopy(native)
    report["automatic_research"] = findings
    report["experiment"] = {
        "recommendation_id": config_id,
        "selected_strategies": deepcopy(native["strategies"]),
        "selected_reports": {config_id: deepcopy(native)},
    }
    return report, contract


@pytest.mark.parametrize(
    "changed,strict", [(True, False), (False, False), (True, True), (False, True)]
)
def test_exact_changed_or_unchanged_account_accepts_without_calculation(changed, strict):
    report, contract = accepted(changed=changed, strict=strict)
    before = deepcopy(report)
    result = validate_full_search_result(report, contract)
    assert result["historically_validated"] and not result["independently_validated"]
    assert result["disclosures"] == [HISTORICAL_DISCLOSURE]
    assert result["config_id"] == settings_identity(report["strategies"])
    assert report == before


@pytest.mark.parametrize(
    "target",
    [
        "ledger",
        "summary",
        "settings",
        "audit",
        "native-seal",
        "validation-seal",
        "partial-search",
        "exceeded-budget",
        "missing-diagnostic",
        "duplicate-diagnostic",
        "failed-cost",
        "failed-neighbors",
        "missing-neighbors",
        "failed-design",
        "changed-risk",
        "changed-style",
        "changed-signals",
    ],
)
def test_changed_acceptance_fails_closed_on_incomplete_or_changed_proof(target):
    report, contract = accepted()
    findings = report["automatic_research"]
    verdict = findings["operating_validation"]
    if target == "ledger":
        report["ledger"][0]["pnl"] += 1
    elif target == "summary":
        report["summary"]["net_return_pct"] += 1
    elif target == "settings":
        report["strategies"][0]["config"]["order_size_pct"] += 1
    elif target == "audit":
        report["portfolio_constraint_evidence"]["status"] = "incomplete"
    elif target == "native-seal":
        verdict["selected_report_id"] = "0" * 64
    elif target == "validation-seal":
        verdict["accounts"] = 12
    elif target == "partial-search":
        findings["counts"]["proposals"] = 407
    elif target == "exceeded-budget":
        findings["counts"]["attempts_reserved"] = 513
    elif target == "missing-diagnostic":
        verdict["diagnostics"].pop()
    elif target == "duplicate-diagnostic":
        verdict["diagnostics"][1] = deepcopy(verdict["diagnostics"][0])
    elif target in ("failed-cost", "failed-neighbors", "missing-neighbors"):
        chosen = findings["decision"]
        if target == "missing-neighbors":
            chosen["checks"].pop()
        else:
            chosen["checks"][0 if target == "failed-cost" else 1]["passed"] = False
        findings["decision"] = seal(chosen)
        verdict["decision"] = deepcopy(findings["decision"])
    elif target == "failed-design":
        report["automatic_design"]["qualified"] = False
    elif target == "changed-risk":
        report["strategies"][0]["execution_policy"]["risk_limits"]["max_open_positions"] += 1
    elif target == "changed-style":
        report["strategies"][0]["config"]["trade_horizon"] = "intraday"
    else:
        report["automatic_stock_condition"]["hypothesis"]["id"] = "momentum"
    if target != "validation-seal":
        findings["operating_validation"] = seal(verdict)
    with pytest.raises(ValueError):
        validate_full_search_result(report, contract)


@pytest.mark.parametrize(
    "cause",
    [
        "decision-binding",
        "missing-prefix",
        "failed-prefix-flag",
        "failed-gate",
        "fake-no-result",
        "not-passing",
    ],
)
def test_unchanged_primary_does_not_turn_missing_or_failed_checks_into_acceptance(cause):
    report, contract = accepted(changed=False)
    findings = report["automatic_research"]
    decision, verdict = findings["decision"], findings["operating_validation"]
    if cause == "decision-binding":
        verdict["primary_decision_id"] = "0" * 64
    elif cause == "missing-prefix":
        decision["prefix_diagnostics"].pop()
    elif cause == "failed-prefix-flag":
        decision["prefix_diagnostics"][0]["passed"] = False
    elif cause == "failed-gate":
        decision["checks"][0]["passed"] = False
    elif cause == "fake-no-result":
        verdict["outcome"] = "blocked"
    else:
        decision["historical_candidate"] = False
    findings["decision"] = seal(decision)
    if cause != "decision-binding":
        verdict["primary_decision_id"] = findings["decision"]["id"]
    findings["operating_validation"] = seal(verdict)
    with pytest.raises(ValueError):
        validate_full_search_result(report, contract)


@pytest.mark.parametrize("changed", [True, False])
def test_strict_route_retains_later_window_validation(changed):
    report, contract = accepted(changed=changed, strict=True)
    findings = report["automatic_research"]
    if changed:
        findings["operating_validation"]["chronological"]["qualified"] = False
        findings["operating_validation"] = seal(findings["operating_validation"])
    else:
        findings["walk_forward"]["folds"][0]["eligible"] = False
    with pytest.raises(ValueError):
        validate_full_search_result(report, contract)
