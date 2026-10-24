"""Capital decisions preserve all constraint evidence and stop noncash escalation."""

from copy import deepcopy

import pytest
from test_capital_planning import prepared, report

from research.capital_planning import (
    DECISION_VERSION,
    VERSION,
    account_receipt,
    descriptor,
    finish,
    plan,
    settings_for,
    should_continue,
)
from research.report_contract import fingerprint


def programme(*, version=DECISION_VERSION):
    original = prepared()
    return plan(original["baseline"], original["selected"], 100000, version=version)


def tested(
    value,
    *,
    constraints=(),
    quantity=15,
    requested=30,
    index=0,
    status="closed",
    reason="hold; close exit",
):
    native = report(
        value,
        quantity=quantity,
        requested=requested,
        constraints=list(constraints),
        status=status,
        reason=reason,
    )
    amount = value["candidate_capitals"][index]
    native["summary"]["initial_capital"] = amount
    return account_receipt(native, value, amount)


def test_only_cash_shortfalls_continue_to_next_frozen_amount_and_stop_after_success():
    value = programme()
    first = tested(value, constraints=["shared_cash"])
    assert first["diagnosis"]["action"] == "test_higher_capital"
    assert should_continue(first)
    assert not first["verified_sufficient"]
    second = tested(value, quantity=30, index=1)
    assert not should_continue(second)
    result = finish(value, [first, second])
    assert result["verified_capital"] == value["candidate_capitals"][1]
    assert result["diagnosis"]["action"] == "funding_verified"
    assert result["independently_validated"] is False


@pytest.mark.parametrize(
    "constraint,category",
    [
        ("open_positions", "position_limit"),
        ("daily_volume_participation", "liquidity_limit"),
        ("trade_stop_risk", "risk_or_allocation_policy"),
        ("total_stop_risk", "risk_or_allocation_policy"),
        ("portfolio_symbol_allocation", "risk_or_allocation_policy"),
        ("portfolio_cluster_allocation", "risk_or_allocation_policy"),
        ("unknown_limit", "unexplained_execution_limit"),
    ],
)
def test_noncash_constraints_stop_increases_without_relaxing_limits_or_claiming_funding(
    constraint, category
):
    value = programme()
    before = deepcopy(value)
    receipt = tested(value, constraints=[constraint])
    diagnosis = receipt["diagnosis"]
    assert diagnosis["action"] == "review_noncash_constraints"
    assert diagnosis["categories"] == {category: 1}
    assert diagnosis["constraint_counts"] == {constraint: 1}
    assert diagnosis["noncash_blocker_requests"] == 1
    assert not should_continue(receipt)
    result = finish(value, [receipt])
    assert result["status"] == "no_verified_amount"
    assert result["verified_capital"] is None
    assert result["estimated_capital"] == value["seed_capital"]
    assert result["tested_range"] == [value["candidate_capitals"][0]] * 2
    assert value == before
    settings = settings_for(
        value["candidate_capitals"][0],
        value["baseline"],
        value["selected"],
        original_capital=100000,
    )
    assert settings[0]["execution_policy"]["risk_limits"] == value["risk_limits"]


def test_mixed_causes_keep_every_tag_and_never_mask_cash_or_allocation_with_position_priority():
    value = programme()
    tags = [
        "shared_cash",
        "open_positions",
        "portfolio_symbol_allocation",
        "portfolio_cluster_allocation",
        "trade_stop_risk",
    ]
    receipt = tested(value, constraints=tags)
    diagnosis = receipt["diagnosis"]
    assert diagnosis["constraint_counts"] == dict.fromkeys(tags, 1)
    assert diagnosis["categories"] == {
        "cash_shortfall": 1,
        "position_limit": 1,
        "risk_or_allocation_policy": 1,
    }
    assert diagnosis["cash_shortfall_requests"] == diagnosis["noncash_blocker_requests"] == 1
    assert not should_continue(receipt)
    assert "may overlap" in diagnosis["scope"]
    # Existing backward-compatible display counts remain intact; they are not
    # substituted for the full multi-cause decision evidence.
    assert receipt["blockers"] == {"position_count": 1}


@pytest.mark.parametrize("kind", ["ticket", "price", "execution", "empty"])
def test_affordability_missing_evidence_and_absence_of_requests_never_trigger_more_funds(kind):
    value = programme()
    if kind == "ticket":
        receipt = tested(
            value,
            quantity=0,
            requested=0,
            status="skipped",
            reason="Execution limits cannot admit one whole share: requested_cash_budget",
        )
    else:
        native = report(value)
        if kind == "price":
            native["ledger"].append(
                {"quantity": 0, "status": "excluded", "reason": "Broker price unavailable: AAA"}
            )
        elif kind == "execution":
            native["ledger"][0].pop("execution_details")
        else:
            native["ledger"] = []
        receipt = account_receipt(native, value, value["candidate_capitals"][0])
    assert not receipt["verified_sufficient"]
    assert not should_continue(receipt)
    assert finish(value, [receipt])["status"] == "no_verified_amount"


def test_finish_rejects_accounts_after_noncash_stop_and_does_not_erase_prior_valid_cash_checks():
    value = programme()
    first = tested(value, constraints=["shared_cash"])
    second = tested(value, constraints=["open_positions"], index=1)
    assert finish(value, [first, second])["diagnosis"]["action"] == "review_noncash_constraints"
    third = tested(value, quantity=30, index=2)
    with pytest.raises(ValueError, match="frozen sequence"):
        finish(value, [first, second, third])


def test_legacy_descriptor_and_receipts_keep_their_recorded_contract():
    old = programme(version=VERSION)
    assert descriptor()["id"] == "dfcde6cc8970e9d004b98447bab731175ab430b161803ab5059ed2d5ca2f0ff2"
    assert "decision_policy" not in descriptor()
    receipt = tested(old, constraints=["open_positions"])
    assert "diagnosis" not in receipt
    assert should_continue(receipt)
    second = tested(old, quantity=30, index=1)
    result = finish(old, [receipt, second])
    assert result["status"] == "verified_sufficient"
    assert "diagnosis" not in result
    assert old["policy_id"] != programme()["policy_id"]


def test_frozen_account_bound_stops_cash_escalation_without_inventing_verified_capital():
    value = programme()
    receipts = [
        tested(value, constraints=["shared_cash"], index=index)
        for index in range(len(value["candidate_capitals"]))
    ]
    result = finish(value, receipts)
    assert result["diagnosis"]["action"] == "account_budget_exhausted"
    assert result["diagnosis"]["stop_increases"]
    assert result["status"] == "no_verified_amount"
    assert result["verified_capital"] is None
    assert result["accounts"] == receipts


@pytest.mark.parametrize("damage", ["action", "cash", "noncash", "constraints", "categories"])
def test_resealed_inconsistent_decision_cannot_change_automatic_stopping(damage):
    value = programme()
    receipt = tested(value, constraints=["shared_cash", "open_positions"])
    diagnosis = receipt["diagnosis"]
    if damage == "action":
        diagnosis["action"] = "test_higher_capital"
    elif damage == "cash":
        diagnosis["cash_shortfall_requests"] += 1
    elif damage == "noncash":
        diagnosis["noncash_blocker_requests"] = 0
    elif damage == "constraints":
        diagnosis["constraint_counts"]["shared_cash"] = -1
    else:
        diagnosis["categories"]["position_limit"] = True
    receipt["id"] = fingerprint({key: item for key, item in receipt.items() if key != "id"})
    with pytest.raises(ValueError):
        should_continue(receipt)
