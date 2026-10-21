"""Compact constraint verification uses actual native v5 fills."""

from copy import deepcopy

import pytest
from test_capacity_allocation_native import capacity_policy, rows, sealed_features
from test_vectorbt_portfolio import DAYS, snapshot

from research.connectors import vectorbt_portfolio as native
from research.portfolio import fingerprint
from research.portfolio_constraint_evidence import assess
from research.portfolio_execution_features import VERSION

pytestmark = pytest.mark.timeout(240)


def frozen_features(current):
    capacity, correlation = {}, {}
    for strategy in current:
        for signal in strategy["signals"]:
            key = signal["symbol"] + "|" + DAYS[1]
            capacity[key] = {"feature": deepcopy(signal["entry_capacity"])}
            correlation[key] = deepcopy(signal["entry_correlation"])
    payload = {
        "version": VERSION,
        "context_id": "d" * 64,
        "atr_id": "e" * 64,
        "capacity": capacity,
        "correlation": correlation,
        "volume_records": {},
        "opportunities": {},
        "counts": {},
    }
    return {**payload, "id": fingerprint(payload)}


@pytest.fixture(scope="module")
def completed():
    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))
    report = native.evaluate(current, snapshot(), 1000)
    return report, frozen_features(current)


def test_native_joint_fills_and_shared_budget_are_supported(completed):
    report, features = completed
    result = assess(report, features)
    assert result["status"] == "supported", result
    assert result["filled_entries"] == 2
    assert result["capacity_days"] == 1
    assert result["unavailable_entries"] == 0
    assert result["feature_id"] == features["id"]
    assert len(str(result)) < 3000
    assert "ledger" not in result


@pytest.mark.parametrize(
    "field,value",
    [
        ("capacity_feature_id", "0" * 64),
        ("capacity_mean_daily_shares", 501),
        ("daily_participation_share_budget", 6),
        ("daily_bought_shares_before", 1),
        ("remaining_participation_shares", 6),
        ("filled_quantity", 4),
        ("correlation_feature_id", "0" * 64),
        ("correlated_capacity_shares", 1000),
        ("correlated_exposure_before", 1000),
        ("opening_equity", float("nan")),
        ("requested_quantity", True),
        ("opening_equity", 10**300),
    ],
)
def test_tampered_per_entry_budget_or_identity_is_invalid(completed, field, value):
    report, features = completed
    changed = deepcopy(report)
    changed["ledger"][0]["execution_details"][field] = value
    assert assess(changed, features)["status"] == "invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("bought_shares", 4),
        ("buy_fills", 1),
        ("share_budget", 6),
        ("source_id", "0" * 64),
        ("mean_daily_shares", 501),
        ("remaining_shares", 1),
        ("share_budget", float("inf")),
        ("buy_fills", True),
    ],
)
def test_daily_receipt_reconciles_actual_buys(completed, field, value):
    report, features = completed
    changed = deepcopy(report)
    changed["execution"]["capacity"]["daily_symbols"][0][field] = value
    assert assess(changed, features)["status"] == "invalid"


def test_daily_budget_is_shared_and_not_counted_once_per_strategy(completed):
    report, features = completed
    changed = deepcopy(report)
    changed["execution"]["capacity"]["daily_symbols"] *= 2
    assert assess(changed, features)["status"] == "invalid"
    changed = deepcopy(report)
    changed["execution"]["capacity"]["daily_symbols"] = []
    assert assess(changed, features)["status"] == "invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        "buy_quantity",
        "buy_price",
        "buy_fee",
        "missing_buy",
        "duplicate_buy",
        "duplicate_order_id",
        "ledger_quantity",
        "ledger_column",
    ],
)
def test_engine_fills_and_ledger_must_reconcile(completed, mutation):
    report, features = completed
    changed = deepcopy(report)
    buys = [order for order in changed["engine_records"]["orders"] if order["side"] == 0]
    if mutation == "buy_quantity":
        buys[0]["size"] += 1
    elif mutation == "buy_price":
        buys[0]["price"] += 1
    elif mutation == "buy_fee":
        buys[0]["fees"] += 1
    elif mutation == "missing_buy":
        changed["engine_records"]["orders"].remove(buys[0])
    elif mutation == "duplicate_buy":
        duplicate = deepcopy(buys[0])
        duplicate["id"] = 100
        changed["engine_records"]["orders"].append(duplicate)
    elif mutation == "duplicate_order_id":
        buys[1]["id"] = buys[0]["id"]
    elif mutation == "ledger_quantity":
        changed["ledger"][0]["quantity"] += 1
    else:
        changed["ledger"][1]["engine_column"] = changed["ledger"][0]["engine_column"]
    assert assess(changed, features)["status"] == "invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        "report_policy",
        "adapter",
        "mode",
        "limit",
        "strategy_limit",
        "strategy_id",
        "feature_id",
        "feature_source",
    ],
)
def test_forged_policies_and_frozen_sources_fail_closed(completed, mutation):
    report, features = deepcopy(completed)
    if mutation == "report_policy":
        report["policy_version"] = "vectorbt-joint-causal-bars-v4"
    elif mutation == "adapter":
        report["execution"]["adapter_version"] = "vectorbt-portfolio-adapter-v4"
    elif mutation == "mode":
        report["execution"]["capacity_mode"] = "disabled"
    elif mutation == "limit":
        report["execution"]["risk_limits"]["max_daily_volume_participation_pct"] = 2
    elif mutation == "strategy_limit":
        report["strategies"][1]["execution_policy"]["risk_limits"][
            "max_daily_volume_participation_pct"
        ] = 2
    elif mutation == "strategy_id":
        report["strategies"][1]["id"] = report["strategies"][0]["id"]
    elif mutation == "feature_id":
        features["id"] = "0" * 64
    else:
        feature = features["capacity"]["AAA|" + DAYS[1]]["feature"]
        feature["source_id"] = "f" * 64
        feature["id"] = fingerprint({k: v for k, v in feature.items() if k != "id"})
        features["id"] = fingerprint({k: v for k, v in features.items() if k != "id"})
    assert assess(report, features)["status"] == "invalid"


def test_explicit_unavailable_observation_is_incomplete_not_a_positive_recommendation():
    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))
    features = frozen_features(current)
    current[0]["signals"][0].pop("entry_capacity")
    current[0]["signals"][0]["research_exclusion"] = (
        "capacity_unavailable: twenty prior sessions required"
    )
    report = native.evaluate(current, snapshot(), 1000)
    result = assess(report, features)
    assert result["status"] == "incomplete", result
    assert result["unavailable_entries"] == 1
    assert result["filled_entries"] == 1


def test_missing_used_feature_invalid_even_with_resealed_root(completed):
    report, features = deepcopy(completed)
    features["capacity"] = {}
    features["id"] = fingerprint({k: v for k, v in features.items() if k != "id"})
    assert assess(report, features)["status"] == "invalid"


def test_audit_does_not_mutate_or_read_performance_final_outcomes(completed):
    report, features = deepcopy(completed)
    before = deepcopy((report, features))
    expected = assess(report, features)
    assert (report, features) == before
    report["summary"] = {"net_return_pct": 1000000}
    report["final"] = {"this_must_not_be_consumed": float("nan")}
    assert assess(report, features) == expected


def test_native_fill_order_is_used_for_reversed_priority():
    policy = capacity_policy(capacity=True, correlation=True)
    policy["entry_priority"] = "reversed_source"
    current = sealed_features(rows(execution=policy))
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [2, 3]
    assert assess(report, frozen_features(current))["status"] == "supported"


def test_correlation_only_mode_is_audited_without_capacity_receipt():
    current = sealed_features(rows(execution=capacity_policy(capacity=False, correlation=True)))
    report = native.evaluate(current, snapshot(), 1000)
    result = assess(report, frozen_features(current))
    assert result["status"] == "supported", result
    assert result["capacity_days"] == 0


def test_capacity_only_mode_is_audited_without_correlation_receipt():
    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=False)))
    report = native.evaluate(current, snapshot(), 1000)
    assert assess(report, frozen_features(current))["status"] == "supported"


def test_oversized_ledgers_and_missing_features_return_compact_invalid(completed):
    report, features = deepcopy(completed)
    report["ledger"] *= 13000
    result = assess(report, features)
    assert result["status"] == "invalid"
    assert len(str(result)) < 3000
    assert assess({}, None)["status"] == "invalid"


def test_source_identity_rejection_cannot_pass_as_a_valid_partial_account():
    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))
    features = frozen_features(current)
    current[0]["signals"][0].pop("entry_capacity")
    report = native.evaluate(current, snapshot(), 1000)
    assert assess(report, features)["status"] == "invalid"


def test_actual_sells_are_unrestricted_and_not_recounted_as_buys():
    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))
    for strategy in current:
        strategy["config"].update(hold_sessions=1, cost_bps=10, slippage_bps=10)
    report = native.evaluate(current, snapshot(), 1000)
    assert any(order["side"] == 1 for order in report["engine_records"]["orders"])
    assert assess(report, frozen_features(current))["status"] == "supported"


def test_correlation_unavailable_is_incomplete():
    current = sealed_features(rows(execution=capacity_policy(capacity=False, correlation=True)))
    features = frozen_features(current)
    current[0]["signals"][0]["research_exclusion"] = (
        "correlation_unavailable: aligned prior returns required"
    )
    report = native.evaluate(current, snapshot(), 1000)
    assert assess(report, features)["status"] == "incomplete"


@pytest.mark.parametrize("mutation", ["future_available", "future_to", "assessment_source"])
def test_resealed_future_or_wrong_producer_binding_is_invalid(completed, mutation):
    report, features = deepcopy(completed)
    item = features["capacity"]["AAA|" + DAYS[1]]
    feature = item["feature"]
    if mutation == "future_available":
        feature["available_at"] = DAYS[1] + "T16:00:00+05:30"
    elif mutation == "future_to":
        feature["to"] = DAYS[1]
    else:
        item["assessment"] = {"id": "a" * 64}
    if mutation != "assessment_source":
        feature["id"] = fingerprint({k: v for k, v in feature.items() if k != "id"})
        for row in report["ledger"]:
            row["execution_details"]["capacity_feature_id"] = feature["id"]
    features["id"] = fingerprint({k: v for k, v in features.items() if k != "id"})
    assert assess(report, features)["status"] == "invalid"


def test_held_symbol_pair_cannot_disappear_from_resealed_correlation():
    current = sealed_features(
        rows(symbols=("AAA", "BBB"), execution=capacity_policy(capacity=True, correlation=True))
    )
    report = native.evaluate(current, snapshot(), 1000)
    features = frozen_features(current)
    feature = features["correlation"]["BBB|" + DAYS[1]]
    del feature["correlations"]["AAA"]
    feature["id"] = fingerprint({k: v for k, v in feature.items() if k != "id"})
    report["ledger"][1]["execution_details"]["correlation_feature_id"] = feature["id"]
    features["id"] = fingerprint({k: v for k, v in features.items() if k != "id"})
    assert assess(report, features)["status"] == "invalid"
