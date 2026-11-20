"""Observations inform the selector without authorizing any validation shortcut."""

from copy import deepcopy

from services import research_learning_feedback as feedback


def account(capital=100000, *, fee=2):
    return {
        "summary": {
            "initial_capital": capital,
            "net_pnl": 50,
            "net_return_pct": 50 / capital * 100,
            "closed_trades": 1,
        },
        "ledger": [
            {
                "source_row": 1,
                "symbol": "EXAMPLE",
                "entry_date": "2026-01-02",
                "exit_date": "2026-01-05",
                "quantity": 5,
                "entry_price": 100,
                "exit_price": 110,
                "fees": fee,
                "pnl": 50,
                "status": "closed",
            }
        ],
        "equity_curve": [capital, capital + 50],
        "per_strategy": [],
        "engine_records": [],
        "coverage": {},
        "missing_marks": [],
        "evaluation_basis": {},
        "research_opportunity_basis": {},
        "automatic_stock_condition": {"hypothesis": {"id": "none"}},
        "supervisor_validation": {"outcome": "rejected"},
    }


def test_same_cash_trades_at_different_capital_are_observed_not_declared_equivalent(monkeypatch):
    monkeypatch.setattr(feedback, "scorecard", lambda report: {"eligible": True, "checks": []})
    result = feedback.build(
        [
            feedback.summarize(account(), job_id="one"),
            feedback.summarize(account(200000), job_id="two"),
        ]
    )
    assert len(result["cash_trade_matches"]) == 1
    assert result["normal_account_matches"] == []
    assert [b["capital"] for b in result["cash_trade_matches"][0]["branches"]] == [100000, 200000]
    assert "cannot skip stress" in result["policy"]
    assert not any(key in result for key in ("accepted", "equivalent", "skip_validation"))


def test_changed_cash_costs_and_blocked_accounts_do_not_form_a_profit_pattern(monkeypatch):
    monkeypatch.setattr(feedback, "scorecard", lambda report: {"eligible": True, "checks": []})
    blocked = account()
    blocked["supervisor_validation"]["outcome"] = "blocked"
    result = feedback.build(
        [
            feedback.summarize(account(), job_id="one"),
            feedback.summarize(account(fee=3), job_id="two"),
            feedback.summarize(blocked, job_id="blocked"),
        ]
    )
    assert result["cash_trade_matches"] == result["normal_account_matches"] == []


def test_better_measurement_with_a_failed_gate_is_not_a_confirmed_repair(monkeypatch):
    monkeypatch.setattr(feedback, "scorecard", lambda report: report["card"])
    origin, changed = account(), deepcopy(account())
    origin["card"] = {
        "eligible": True,
        "checks": [{"key": "higher_costs", "passed": False, "facts": {"net_return_pct": 1}}],
    }
    changed["card"] = {
        "eligible": True,
        "checks": [{"key": "higher_costs", "passed": False, "facts": {"net_return_pct": 2}}],
    }
    diagnosis = {
        "target_ref": "b1/higher_costs",
        "cause": "exit_churn",
        "prediction": "Improve cost performance",
        "falsifier": "Unchanged native cost check remains failed",
    }
    result = feedback.build(
        [
            feedback.summarize(origin, job_id="before"),
            feedback.summarize(
                changed, job_id="after", origin_job_id="before", diagnosis=diagnosis
            ),
        ]
    )["hypothesis_results"][0]
    assert result["assessment"] == "target_still_failed"
    assert result["before"]["facts"]["net_return_pct"] == 1
    assert result["after"]["facts"]["net_return_pct"] == 2


def test_initial_root_failure_is_preserved_when_root_is_not_a_child_proposal(monkeypatch):
    monkeypatch.setattr(feedback, "scorecard", lambda report: {"eligible": False, "checks": []})
    report = account()
    report["supervisor_validation"]["outcome"] = "blocked"
    diagnosis = {"target_ref": "b1/neighboring_settings", "cause": "exit_sensitivity"}
    result = feedback.build(
        [feedback.summarize(report, job_id="blocked", origin_job_id="root", diagnosis=diagnosis)],
        branch_cards={
            "root": {
                "checks": [
                    {
                        "key": "neighboring_settings",
                        "passed": False,
                        "facts": {"passing": 2, "tested": 4},
                    }
                ]
            }
        },
    )["hypothesis_results"][0]
    assert result["before"]["facts"] == {"passing": 2, "tested": 4}
    assert result["after"] is None
    assert result["assessment"] == "target_unmeasured_or_partial_rejection"


def test_native_funding_repair_can_fix_feasibility_while_performance_stays_rejected(monkeypatch):
    monkeypatch.setattr(
        feedback,
        "scorecard",
        lambda report: {"eligible": report.get("complete", False), "checks": []},
    )
    before, after = account(), account()
    before["supervisor_validation"].update(
        outcome="blocked",
        feasibility={
            "version": "research-feasibility-v1",
            "scope": "feasibility_only",
            "counts": {"admitted_requests": 40, "cash_shortfall": 5},
        },
    )
    after["complete"] = True
    after["supervisor_validation"]["operating_validation"] = {
        "decision": {
            "capital_diagnosis": {
                "admitted_requests": 42,
                "position_limit": 3,
                "evidence_gap_requests": 0,
            }
        }
    }
    diagnosis = {"target_ref": "b1/feasibility", "cause": "affordability_and_sizing"}
    result = feedback.build(
        [
            feedback.summarize(before, job_id="before"),
            feedback.summarize(after, job_id="after", origin_job_id="before", diagnosis=diagnosis),
        ]
    )["hypothesis_results"][0]
    assert result["before"]["passed"] is False and result["after"]["passed"] is True
    assert result["outcome"] == "rejected"
    assert result["assessment"] == "target_passed_but_native_acceptance_still_required"
