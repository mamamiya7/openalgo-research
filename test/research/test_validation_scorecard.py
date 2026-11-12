"""Controlled native-format reports, no prices, search, broker or provider calls."""

import json
from copy import deepcopy

import pytest
from test_operating_validation import execute, plan, report

from research.report_contract import fingerprint
from services.research_validation_scorecard import build, dominates, rank


def seal(value):
    value["id"] = fingerprint({key: item for key, item in value.items() if key != "id"})
    return value


def reseal(value):
    native = value["supervisor_validation"]
    operating = native["operating_validation"]
    seal(operating["decision"])
    seal(operating)
    seal(native)
    return value


@pytest.fixture
def native():
    # Execute the real programme with its existing fake account engine. This
    # establishes the actual native conclusion shape and full challenge count.
    value = plan()
    operating, _, engine = execute(value)
    assert len(engine.calls) == 13
    result = report(value["selected_settings"])
    result["supervisor_validation"] = seal(
        {
            "version": "research-supervisor-validation-v1",
            "outcome": operating["outcome"],
            "independently_validated": False,
            "selected_config_id": operating["selected_config_id"],
            "native_report_id": operating["selected_report_id"],
            "operating_validation": operating,
            "reasons": operating["reasons"],
        }
    )
    return result


def reject(value, *, cost=False, neighbor=True, prefix=False, design=False):
    value = deepcopy(value)
    native = value["supervisor_validation"]
    operating = native["operating_validation"]
    native["outcome"] = operating["outcome"] = "rejected"
    operating["decision"]["historical_candidate"] = False
    for check in operating["decision"]["checks"]:
        failed = cost if check["kind"] == "higher_costs" else neighbor
        check["passed"] = not failed
        if check["kind"] == "neighboring_settings":
            check["passing"] = 2 if failed else 4
    operating["diagnostics"][0]["passed"] = not prefix
    value["automatic_design"]["qualified"] = not design
    return reseal(value)


def test_actual_native_format_preserves_measurements_without_accepting(native):
    near = reject(native)
    original = deepcopy(near)
    value = build(near)
    assert value["eligible"]
    assert value["scope"] == "same_history_development"
    assert value["independently_validated"] is False
    gates = {row["key"]: row for row in value["checks"]}
    assert gates["neighboring_settings"]["facts"]["tested"] == 4
    assert gates["neighboring_settings"]["facts"]["passing"] == 2
    assert "median_return_edge_pct" in gates["neighboring_settings"]["facts"]
    assert gates["neighboring_settings"]["thresholds"]["minimum_neighbor_pass_fraction"] == 0.75
    assert gates["higher_costs"]["facts"]["net_return_pct"] == 5
    assert (
        gates["sample_and_distribution"]["thresholds"]["maximum_symbol_positive_profit_fraction"]
        == 0.5
    )
    assert value["failed_gate_keys"] == ["neighboring_settings", "overall_native"]
    assert near == original
    assert near["supervisor_validation"]["outcome"] == "rejected"


def test_near_miss_dominates_earlier_failed_checks_despite_lower_profit(native):
    earlier = reject(native, cost=True, prefix=True, design=True)
    near = reject(native)
    earlier["summary"]["net_return_pct"] = 99
    near["summary"]["net_return_pct"] = 0.1
    assert rank(near) < rank(earlier)
    assert dominates(near, earlier)
    assert dominates(build(near), build(earlier))
    assert not dominates(earlier, near)


def test_equal_or_tradeoff_failures_do_not_override_recency(native):
    first, second = reject(native), reject(native, cost=True, neighbor=False)
    assert not dominates(first, first)
    assert not dominates(first, second)
    assert not dominates(second, first)
    higher_profit = deepcopy(first)
    higher_profit["summary"]["net_return_pct"] = 1000
    assert rank(first) == rank(higher_profit)
    assert not dominates(higher_profit, first)


def test_distinct_declared_prefixes_make_branches_incomparable(native):
    earlier, near = reject(native, cost=True), reject(native)
    near["supervisor_validation"]["operating_validation"]["diagnostics"][0]["period"] = (
        "different-search-prefix"
    )
    reseal(near)
    assert rank(near) is not None
    assert not dominates(near, earlier)


def test_new_native_failure_or_different_gate_set_cannot_count_as_dominance(native):
    earlier, near = reject(native, cost=True), reject(native)
    near["supervisor_validation"]["reasons"] = ["New matched baseline support failure"]
    reseal(near)
    assert rank(near) < rank(earlier)
    assert not dominates(near, earlier)
    card = build(reject(native))
    card["checks"].pop()
    assert not dominates(card, build(earlier))


@pytest.mark.parametrize(
    "kind",
    [
        "blocked",
        "no_effect",
        "later",
        "stress",
        "independent",
        "top_independent",
        "legacy",
        "missing_prefix",
        "missing_cost",
        "missing_neighbors",
        "partial_accounts",
        "unknown_policy",
        "unsupported",
        "unsealed",
        "changed_receipt",
        "nonfinite",
    ],
)
def test_unavailable_evidence_never_exports_performance(native, kind):
    value = deepcopy(native)
    envelope = value["supervisor_validation"]
    operating = envelope["operating_validation"]
    if kind == "blocked":
        envelope["outcome"] = operating["outcome"] = "blocked"
    elif kind == "no_effect":
        operating["no_effect"] = {"status": "unchanged"}
    elif kind == "later":
        operating["chronological"] = {"status": "supported", "secret_return": 919191}
    elif kind == "stress":
        operating["diagnostics"][0]["stress"] = True
    elif kind == "independent":
        operating["independently_validated"] = True
    elif kind == "top_independent":
        value["independently_validated"] = True
    elif kind == "legacy":
        operating.pop("chronological")
    elif kind == "missing_prefix":
        operating["diagnostics"].pop()
    elif kind == "missing_cost":
        operating["decision"]["checks"][0].pop("summary")
    elif kind == "missing_neighbors":
        operating["decision"]["checks"][1].pop("passing")
    elif kind == "partial_accounts":
        operating["accounts"] -= 1
    elif kind == "unknown_policy":
        operating["decision"]["policy_id"] = "unknown"
    elif kind == "unsupported":
        value["portfolio_constraint_evidence"]["status"] = "incomplete"
    elif kind == "unsealed":
        envelope.pop("native_report_id")
    elif kind == "nonfinite":
        value["summary"]["net_return_pct"] = float("inf")
    if kind != "changed_receipt":
        reseal(value)
    else:
        operating["decision"]["checks"][0]["passed"] = False
    scorecard = build(value)
    assert scorecard["eligible"] is False
    assert scorecard["summary"] == {} and scorecard["checks"] == []
    assert "919191" not in json.dumps(scorecard)
    assert rank(value) is None
    assert not dominates(value, native) and not dominates(native, value)


def test_unknown_metrics_remain_unknown_and_free_text_is_bounded(native):
    value = reject(native)
    value["supervisor_validation"]["reasons"] = ["x" * 1000] * 5
    reseal(value)
    card = build(value)
    assert card["eligible"]
    assert all(len(row["reasons"]) <= 3 for row in card["checks"])
    assert all(len(reason) <= 240 for row in card["checks"] for reason in row["reasons"])
    assert "matched_baseline_net_return_pct" not in json.dumps(card)
    assert all(row["facts"] == {} for row in card["checks"] if row["key"].startswith("prefix_"))
