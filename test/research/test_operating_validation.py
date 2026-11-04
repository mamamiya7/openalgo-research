"""Tiny controlled reports test a fixed operating point, never a scanner search."""

import json
from copy import deepcopy

import pytest
from test_automatic_decision import account

from research.operating_validation import build_plan, descriptor, run
from research.report_contract import fingerprint


def settings(target=10):
    return [
        {
            "id": "scan",
            "name": "scan",
            "allocation_pct": 100,
            "config": {
                "initial_capital": 100000,
                "order_size_pct": 3,
                "target_pct": target,
                "stop_pct": 5,
                "cost_bps": 2,
                "slippage_bps": 3,
            },
            "execution_policy": {"sizing": "cash_budget", "risk_limits": {"max_open_positions": 5}},
        }
    ]


def plan(**changes):
    inputs = {
        "operating_point": {"capital": 200000, "settings": settings()},
        "baseline_settings": settings(8),
        "hypothesis": {"id": "chosen"},
        "baseline_hypothesis": {"id": "none"},
        "neighbors": [{"strategies": settings(x)} for x in (9, 11, 12, 13)],
        "periods": {"search": "search", "prefixes": ["fit1", "fit2", "fit3"]},
        "validation_mode": "exploratory_only",
        "remaining_accounts": 13,
        "capacity_counts": {
            "admitted_requests": 30,
            "eligible_requests": 35,
            "position_limit": 5,
            "unavailable_evidence": 1,
        },
    }
    inputs.update(changes)
    return build_plan(**inputs)


def audit(report, request):
    return {"status": "supported", "summary_id": fingerprint(report["summary"])}


def report(rows, profit=5, hypothesis=None):
    value = account(profit)
    value["strategies"] = deepcopy(rows)
    value["summary"]["initial_capital"] = rows[0]["config"]["initial_capital"]
    value["portfolio_constraint_evidence"] = audit(value, {})
    value["automatic_stock_condition"] = {"hypothesis": hypothesis or {"id": "chosen"}}
    return value


class Engine:
    def __init__(self, bad_cost=False):
        self.calls = []
        self.bad_cost = bad_cost

    def __call__(self, rows, hypothesis, period, stress):
        self.calls.append((deepcopy(rows), hypothesis, period, stress))
        if stress:
            for row in rows:
                cost = row["config"]["cost_bps"]
                row["config"]["cost_bps"] = max(2 * cost, cost + 5)
        profit = -1 if hypothesis["id"] == "none" else -2 if stress and self.bad_cost else 5
        return report(rows, profit, hypothesis)


def execute(value=None, **kwargs):
    value = value or plan()
    engine = kwargs.pop("evaluate", Engine())
    conclusion, state = run(
        value,
        selected_report=report(value["selected_settings"]),
        evaluate=engine,
        audit=audit,
        **kwargs,
    )
    return conclusion, state, engine


def test_full_same_capital_programme_keeps_original_baseline_size_and_risk_caps():
    value = plan()
    before = deepcopy(value)
    result, state, engine = execute(value)
    assert len(engine.calls) == len(state["reports"]) == 13
    assert value == before
    assert all(rows[0]["config"]["initial_capital"] == 200000 for rows, *_ in engine.calls)
    assert value["baseline_settings"][0]["config"]["order_size_pct"] == 3
    assert all(
        rows[0]["execution_policy"]["risk_limits"] == {"max_open_positions": 5}
        for rows, *_ in engine.calls
    )
    assert result["outcome"] == "validated"
    assert result["historically_validated"]
    assert not result["independently_validated"]
    assert not result["selection_changed"]
    assert result["decision"]["outcome"] == "insufficient_evidence"
    assert result["decision"]["seed_diagnostics"]["independent_confirmations"] == 0
    assert descriptor()["max_exploratory_accounts"] == 13


def test_cost_failure_rejects_exact_point_without_trying_alternate_operating_points():
    value = plan()
    result, _, engine = execute(value, evaluate=Engine(bad_cost=True))
    assert len(engine.calls) == 13
    assert result["outcome"] == "rejected"
    assert not result["historically_validated"]
    assert result["selected_config_id"] == value["selected_config_id"]
    assert not result["selection_changed"]


def test_complete_recovery_recomputes_receipts_with_zero_new_accounts():
    value = plan()
    expected, state, _ = execute(value)
    recovered, changed, engine = execute(value, saved=json.loads(json.dumps(state, sort_keys=True)))
    assert recovered == expected
    assert changed == state
    assert engine.calls == []


def test_pause_before_request_and_exception_after_start_recover_exact_order():
    value = plan()
    retained = []
    engine = Engine()

    def failing(rows, hypothesis, period, stress):
        if len(engine.calls) == 2:
            raise InterruptedError("process stopped before account returned")
        return engine(rows, hypothesis, period, stress)

    with pytest.raises(InterruptedError):
        execute(value, evaluate=failing, checkpoint=lambda state: retained.append(deepcopy(state)))
    saved = retained[-1]
    assert len(saved["reports"]) == 2
    assert saved["active_request"] == value["requests"][2]["id"]
    result, state, resumed = execute(value, saved=saved)
    assert len(resumed.calls) == 11
    assert result["outcome"] == "validated"
    assert state["active_request"] is None
    with pytest.raises(InterruptedError):
        execute(value, cancelled=lambda: (_ for _ in ()).throw(InterruptedError("pause")))


@pytest.mark.parametrize("remaining", [0, 12])
def test_inadequate_budget_blocks_complete_programme_without_truncation(remaining):
    result, _, engine = execute(plan(remaining_accounts=remaining))
    assert result["outcome"] == "blocked"
    assert engine.calls == []


@pytest.mark.parametrize(
    "cause",
    [
        "cash_shortfall",
        "percentage_policy",
        "whole_share_affordability",
        "unavailable_sizing_intent",
        "unexplained_execution_limit",
    ],
)
def test_unresolved_actual_sizing_or_cash_cannot_be_promoted(cause):
    result, _, engine = execute(plan(capacity_counts={"admitted_requests": 30, cause: 1}))
    assert result["outcome"] == "blocked"
    assert engine.calls == []


def test_position_liquidity_and_excluded_missing_records_do_not_erase_tradable_cohort():
    value = plan(
        capacity_counts={
            "admitted_requests": 30,
            "position_limit": 4,
            "liquidity_limit": 2,
            "unavailable_evidence": 3,
        }
    )
    result, _, _ = execute(value)
    assert result["historically_validated"]
    assert not result.get("verified_sufficient", False)
    assert value["capacity_counts"]["unavailable_evidence"] == 3


def test_wrong_report_capital_and_changed_hard_limits_fail_closed():
    value = plan()
    with pytest.raises(ValueError, match="settings changed"):
        run(value, selected_report=report(settings()), evaluate=Engine(), audit=audit)
    rows = settings()
    rows[0]["execution_policy"]["risk_limits"]["max_open_positions"] = 9
    with pytest.raises(ValueError, match="hard account"):
        plan(baseline_settings=rows)


def test_resealed_plan_report_conclusion_and_missing_account_prefix_are_rejected():
    value = plan()
    _, complete, _ = execute(value)
    changed = deepcopy(value)
    changed["capital"] = 1
    changed["id"] = fingerprint({k: v for k, v in changed.items() if k != "id"})
    with pytest.raises(ValueError, match="plan changed"):
        execute(changed)
    changed = deepcopy(complete)
    item = next(iter(changed["reports"].values()))
    item["report"]["summary"]["net_return_pct"] = 999
    item["report_id"] = fingerprint(item["report"])
    with pytest.raises(ValueError, match="constraint evidence changed"):
        execute(value, saved=changed)
    changed = deepcopy(complete)
    changed["conclusion"]["outcome"] = "rejected"
    changed["conclusion"]["id"] = fingerprint(
        {k: v for k, v in changed["conclusion"].items() if k != "id"}
    )
    with pytest.raises(ValueError, match="conclusion changed"):
        execute(value, saved=changed)
    changed = deepcopy(complete)
    del changed["reports"][value["requests"][0]["id"]]
    with pytest.raises(ValueError, match="order"):
        execute(value, saved=changed)


def test_strict_requires_all21_accounts_and_unchanged_recommendation_assessment():
    kwargs = {
        "validation_mode": "chronological",
        "periods": {"search": "search", "later": ["check1", "check2", "check3"], "final": "final"},
    }
    small = plan(**kwargs, remaining_accounts=20)
    result, _, engine = execute(small, strict_assessment=lambda *_: {"status": "supported"})
    assert result["outcome"] == "blocked"
    assert not engine.calls
    value = plan(**kwargs, remaining_accounts=21)
    result, _, engine = execute(value)
    assert result["outcome"] == "blocked"
    assert not engine.calls
    seen = []

    def strict(reports, frozen):
        seen.append(set(reports))
        assert frozen == value
        return {"status": "supported"}

    result, _, engine = execute(value, strict_assessment=strict)
    assert len(engine.calls) == 21
    assert len(seen[0]) == 22
    assert result["historically_validated"]
    assert not result["independently_validated"]
    result, _, _ = execute(value, strict_assessment=lambda *_: {"status": "insufficient"})
    assert result["outcome"] == "rejected"


def test_runtime_account_allowance_blocks_before_starting_partial_validation():
    result, saved, engine = execute(available_accounts=lambda: 12)
    assert result["outcome"] == "blocked"
    assert not engine.calls
    reopened, _, engine = execute(saved=saved, available_accounts=lambda: 512)
    assert reopened == result
    assert not engine.calls


def test_no_ignored_neighbor_duplication_or_missing_windows():
    with pytest.raises(ValueError, match="distinct"):
        plan(neighbors=[{"strategies": settings(9)}] * 2)
    with pytest.raises(ValueError, match="three"):
        plan(periods={"search": "search", "prefixes": ["fit1"]})
    with pytest.raises(ValueError, match="distinct frozen"):
        plan(periods={"search": "search", "prefixes": ["fit1", "fit1", "fit1"]})


def test_absent_capacity_diagnosis_reopens_as_blocked_without_accounts():
    value = plan(capacity_counts=None)
    result, state, engine = execute(value)
    assert result["outcome"] == "blocked"
    assert not engine.calls
    repeated, _, engine = execute(value, saved=json.loads(json.dumps(state, sort_keys=True)))
    assert repeated == result
    assert not engine.calls


def test_historical_mode_uses_all_strict_windows_without_independent_claim():
    value = plan(
        validation_mode="historical_only",
        remaining_accounts=21,
        periods={"search": "search", "later": ["check1", "check2", "check3"], "final": "final"},
    )
    result, _, engine = execute(value, strict_assessment=lambda *_: {"status": "supported"})
    assert len(engine.calls) == 21
    assert result["historically_validated"]
    assert not result["independently_validated"]


def test_reused_native_report_and_hypothesis_are_exactly_pinned():
    value = plan()
    _, state, _ = execute(value)
    changed = report(value["selected_settings"], profit=6)
    with pytest.raises(ValueError, match="native report changed"):
        run(value, selected_report=changed, evaluate=Engine(), audit=audit, saved=state)
    changed = report(value["selected_settings"], hypothesis={"id": "other"})
    with pytest.raises(ValueError, match="hypothesis changed"):
        run(value, selected_report=changed, evaluate=Engine(), audit=audit)
