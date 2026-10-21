"""Pure mask and evidence gates; native execution is qualified by controller tests."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from research.development_evidence import build
from research.evaluation_basis import IDENTITIES
from research.evaluation_basis import VERSION as BASIS_VERSION
from research.recommendation_sensitivity import NAMES, assess, build_plan
from research.report_contract import fingerprint


def evidence(count=80, symbols=("AAA", "BBB", "CCC", "DDD"), minute=False):
    first = date(2026, 1, 1)
    sessions = [(first + timedelta(days=index)).isoformat() for index in range(count)]
    signals = [
        {"symbol": symbol, "date": day, "row": index * len(symbols) + offset + 1}
        for index, day in enumerate(sessions)
        for offset, symbol in enumerate(symbols)
    ]
    return {
        "strategies": [{"id": "a", "signals": signals}],
        "snapshot": {
            "sessions": sessions,
            "provenance": {"interval": "1m" if minute else "D"},
            **({"timeline": [f"{day}T15:29:00+05:30" for day in sessions]} if minute else {}),
        },
    }


def plan_for(value):
    sessions = value["snapshot"]["sessions"]
    return build_plan(value, {"from": sessions[0], "to": sessions[-1]})


def account(scenario, *, candidate=True, edge=0.001, minute=False):
    first, last = date.fromisoformat(scenario["from"]), date.fromisoformat(scenario["to"])
    dates = [
        (first + timedelta(days=index)).isoformat() for index in range((last - first).days + 1)
    ]
    keys = [f"{day}T15:29:00+05:30" for day in dates] if minute else dates
    config = {"cost_bps": 0, "slippage_bps": 0, "variant": "selected" if candidate else "reference"}
    settings = [{"id": "a", "name": "A", "allocation_pct": 100, "config": config}]
    comparison = {
        "period": "selection",
        "currency": "INR",
        "capital": 1000.0,
        "execution": {
            "engine": "vectorbt",
            "adapter_version": "vectorbt-portfolio-adapter-v5",
            "portfolio_features": {"id": fingerprint("features"), "version": "fixture-v1"},
        },
        "costs": [{"strategy_id": "a", "cost_bps": 0.0, "slippage_bps": 0.0}],
    }
    comparison["id"] = fingerprint({"version": BASIS_VERSION, **comparison})
    basis = {
        "version": BASIS_VERSION,
        "status": "verified",
        **{key: fingerprint({"key": key, "source": scenario["source_id"]}) for key in IDENTITIES},
    }
    basis["source_id"] = scenario["source_id"]
    basis["observations_id"] = fingerprint({"interval": "1m" if minute else "D", "timeline": keys})
    basis["evidence_id"] = fingerprint(
        {"version": BASIS_VERSION, **{key: basis[key] for key in IDENTITIES}}
    )
    basis["comparison"] = comparison
    basis["period"] = {
        "kind": "selection",
        "from": keys[0],
        "to": keys[-1],
        "observations": len(keys),
        "interval": "1m" if minute else "D",
    }
    curve, equity, peak = [], 1000, 1000
    for index, key in enumerate(keys):
        gain = (0.001 if index % 2 else -0.0005) + (edge if candidate else 0)
        equity *= 1 + gain
        peak = max(peak, equity)
        curve.append(
            {
                "date": key[:10],
                **({"timestamp": key} if minute else {}),
                "equity": equity,
                "cash": 0,
                "drawdown_pct": (peak - equity) / peak * 100,
                "open_positions": 1,
            }
        )
    return build(
        {
            "strategies": settings,
            "config": {"initial_capital": 1000},
            "evaluation_basis": basis,
            "equity_curve": curve,
        },
        evaluation_id=fingerprint({"scenario": scenario["id"], "candidate": candidate}),
        binding={"inputs_artifact": "fixture-inputs", "search_identity": "fixture-v14"},
        settings=settings,
        hypothesis={"id": "none"},
        period=scenario["name"],
        stress=False,
        expected_timeline=keys,
    )


def audit():
    # Shape-only contract fixture: no claim these synthetic marks prove native fills.
    value = {
        "version": "research-portfolio-constraint-evidence-v1",
        "status": "supported",
        "feature_id": fingerprint("features"),
        "scope": "caller_admitted_development_account",
        "qualification": "recorded_constraints_and_native_fills_only",
        "unavailable_entries": 0,
    }
    return {**value, "id": fingerprint(value)}


def pairs_for(plan, *, edge=0.001, minute=False):
    return [
        {
            "scenario": scenario["name"],
            "candidate": account(scenario, edge=edge, minute=minute),
            "baseline": account(scenario, candidate=False, minute=minute),
            "candidate_support": {"closed_trades": 10, "entry_dates": 10},
            "baseline_support": {"closed_trades": 10, "entry_dates": 10},
            "candidate_constraints": audit(),
            "baseline_constraints": audit(),
        }
        for scenario in plan["scenarios"]
    ]


def reseal(value):
    value["id"] = fingerprint({key: item for key, item in value.items() if key != "id"})
    return value


def rebuild(shard, mutation):
    value = deepcopy(shard)
    mutation(value)
    basis = value["evaluation_basis"]
    basis["comparison"]["id"] = fingerprint(
        {
            "version": BASIS_VERSION,
            **{key: item for key, item in basis["comparison"].items() if key != "id"},
        }
    )
    basis["evidence_id"] = fingerprint(
        {"version": BASIS_VERSION, **{key: basis[key] for key in IDENTITIES}}
    )
    return build(
        {
            "strategies": value["settings"],
            "config": {"initial_capital": value["account"]["initial_capital"]},
            "evaluation_basis": basis,
            "equity_curve": value["curve"],
        },
        evaluation_id=value["evaluation_id"],
        binding=value["binding"],
        settings=value["settings"],
        hypothesis=value["hypothesis"],
        period=value["period"],
        stress=value["stress"],
        expected_timeline=value["timeline"],
    )


def test_plan_is_deterministic_complementary_and_chronological_without_outcomes():
    value = evidence()
    before = deepcopy(value)
    plan = plan_for(value)
    assert plan["status"] == "available"
    assert plan["maximum_accounts"] == 8
    assert [row["name"] for row in plan["scenarios"]] == list(NAMES)
    a, b, early, late = plan["scenarios"]
    assert not set(a["symbols"]) & set(b["symbols"])
    assert sorted(a["symbols"] + b["symbols"]) == plan["search"]["symbols"]
    assert early["to"] < late["from"]
    assert early["sessions"] + late["sessions"] == 80
    assert value == before
    assert plan_for(value) == plan


def test_prices_marks_final_outcomes_and_features_never_enter_plan():
    original = evidence()
    plan = plan_for(original)
    original["snapshot"]["bars"] = {"AAA": {"2099-01-01": {"close": float("nan")}}}
    original["final_report"] = {"return_pct": -99}
    original["portfolio_execution_features"] = {"outcome": 1e100}
    assert plan_for(original) == plan


def test_future_signal_and_calendar_extension_do_not_change_frozen_prefix_plan():
    original = evidence()
    period = {
        "from": original["snapshot"]["sessions"][0],
        "to": original["snapshot"]["sessions"][-1],
    }
    expected = build_plan(original, period)
    original["snapshot"]["sessions"].append("2027-01-01")
    original["strategies"][0]["signals"].append(
        {"symbol": "FUTURE", "date": "2027-01-01", "row": 999}
    )
    assert build_plan(original, period) == expected


def test_symbol_selection_is_permutation_invariant_but_original_row_order_is_bound():
    original = evidence()
    before = plan_for(original)
    original["strategies"][0]["signals"].reverse()
    after = plan_for(original)
    assert [row["symbols"] for row in before["scenarios"]] == [
        row["symbols"] for row in after["scenarios"]
    ]
    assert before["id"] != after["id"]


def test_timing_union_source_rows_own_planned_membership():
    original = evidence()
    expected = plan_for(original)
    original["timing_cohort"] = {"strategies": deepcopy(original["strategies"])}
    original["strategies"][0]["signals"] = []
    assert plan_for(original) == expected


@pytest.mark.parametrize("count,symbols", [(39, ("AAA", "BBB")), (80, ("AAA",)), (80, ())])
def test_missing_history_or_symbol_breadth_never_silently_omits_a_required_challenge(
    count, symbols
):
    plan = plan_for(evidence(count=count, symbols=symbols))
    assert len(plan["scenarios"]) == 4
    assert plan["status"] == "unavailable"
    result = assess(plan, [])
    assert result["status"] == "insufficient"
    assert not result["qualified"]


@pytest.mark.parametrize("count", [40, 41, 80, 81])
def test_exact_minimum_and_odd_session_halves(count):
    plan = plan_for(evidence(count=count))
    assert plan["status"] == "available"
    assert [row["sessions"] for row in plan["scenarios"][2:]] == [count // 2, count - count // 2]


@pytest.mark.parametrize("minute", [False, True])
def test_supported_actual_pairs_are_deterministic_and_keep_qualified_claims_descriptive(minute):
    plan = plan_for(evidence(minute=minute))
    pairs = pairs_for(plan, minute=minute)
    before = deepcopy(pairs)
    result = assess(plan, pairs)
    assert result["status"] == "supported", result
    assert result["qualified"]
    assert len(result["scenarios"]) == 4
    assert result["scenarios"][0]["return_edge_pct"] > 0
    assert "not independent samples" in result["limitations"][0]
    assert pairs == before
    assert assess(plan, pairs) == result


@pytest.mark.parametrize("edge", [0, -0.0001, -0.001])
def test_no_edge_or_negative_edge_is_not_recommended(edge):
    plan = plan_for(evidence())
    result = assess(plan, pairs_for(plan, edge=edge))
    assert result["status"] == "not_supported"
    assert not result["qualified"]


@pytest.mark.parametrize("index", range(4))
def test_one_weak_symbol_group_or_shifted_date_half_blocks_the_full_winner(index):
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    pairs[index]["candidate"] = account(plan["scenarios"][index], edge=-0.001)
    result = assess(plan, pairs)
    assert result["status"] == "not_supported"
    assert any(plan["scenarios"][index]["name"] in reason for reason in result["reasons"])


@pytest.mark.parametrize("role", ["candidate", "baseline"])
@pytest.mark.parametrize(
    "support", [{"closed_trades": 4, "entry_dates": 4}, {"closed_trades": 10, "entry_dates": 4}]
)
def test_sparse_actual_native_support_stays_insufficient(role, support):
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    pairs[0][f"{role}_support"] = support
    result = assess(plan, pairs)
    assert result["status"] == "insufficient"
    assert not result["qualified"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"closed_trades": True, "entry_dates": 5},
        {"closed_trades": 5, "entry_dates": 6},
        {"closed_trades": 100, "entry_dates": 81},
        {"closed_trades": -1, "entry_dates": 0},
    ],
)
def test_invalid_caller_verified_support_is_unsupported(value):
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    pairs[0]["candidate_support"] = value
    assert assess(plan, pairs)["status"] == "unsupported"


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "hash",
        "source",
        "observations",
        "final_kind",
        "stress",
        "settings",
        "hypothesis",
        "binding",
        "cost",
        "audit_hash",
        "audit_feature",
        "audit_missing",
    ],
)
def test_missing_tampered_or_incomparable_actual_evidence_is_unsupported(damage):
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    if damage == "missing":
        pairs.pop()
    elif damage == "duplicate":
        pairs[1] = deepcopy(pairs[0])
    elif damage == "hash":
        pairs[0]["candidate"]["curve"][0]["equity"] += 1
    elif damage == "source":
        for role in ("candidate", "baseline"):
            pairs[0][role] = rebuild(
                pairs[0][role],
                lambda row: row["evaluation_basis"].update(source_id=fingerprint("other subset")),
            )
    elif damage == "observations":
        pairs[0]["candidate"]["evaluation_basis"]["observations_id"] = "changed"
    elif damage == "final_kind":
        pairs[0]["candidate"]["evaluation_basis"]["period"]["kind"] = "evaluation"
        reseal(pairs[0]["candidate"])
    elif damage == "stress":
        pairs[0]["candidate"] = rebuild(pairs[0]["candidate"], lambda row: row.update(stress=True))
    elif damage == "settings":
        pairs[0]["candidate"] = rebuild(
            pairs[0]["candidate"], lambda row: row["settings"][0]["config"].update(variant="other")
        )
    elif damage == "hypothesis":
        pairs[0]["candidate"] = rebuild(
            pairs[0]["candidate"], lambda row: row.update(hypothesis={"id": "other"})
        )
    elif damage == "binding":
        pairs[0]["candidate"] = rebuild(
            pairs[0]["candidate"], lambda row: row["binding"].update(inputs_artifact="other")
        )
    elif damage == "cost":
        pairs[0]["candidate"]["evaluation_basis"]["comparison"]["costs"][0]["cost_bps"] = 99
    elif damage == "audit_hash":
        pairs[0]["candidate_constraints"]["qualification"] = "changed"
    elif damage == "audit_feature":
        pairs[0]["candidate_constraints"]["feature_id"] = fingerprint("other features")
        reseal(pairs[0]["candidate_constraints"])
    elif damage == "audit_missing":
        pairs[0]["candidate_constraints"] = None
    assert assess(plan, pairs)["status"] == "unsupported"


@pytest.mark.parametrize("status", ["incomplete", "invalid"])
def test_unsupported_native_constraints_cannot_be_offset_by_great_returns(status):
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    pairs[0]["candidate_constraints"]["status"] = status
    reseal(pairs[0]["candidate_constraints"])
    assert assess(plan, pairs)["status"] == "insufficient"


@pytest.mark.parametrize(
    "damage", ["hash", "outside", "symbol_partition", "date_overlap", "scenario_duplicate"]
)
def test_changed_plan_never_becomes_supported_even_if_rehashed(damage):
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    if damage == "hash":
        plan["policy"]["minimum_sessions"] = 1
    elif damage == "outside":
        plan["scenarios"][0]["to"] = "2099-01-01"
    elif damage == "symbol_partition":
        plan["scenarios"][0]["symbols"] = plan["scenarios"][1]["symbols"]
    elif damage == "date_overlap":
        plan["scenarios"][2]["to"] = plan["scenarios"][3]["from"]
    elif damage == "scenario_duplicate":
        plan["scenarios"][1] = deepcopy(plan["scenarios"][0])
    if damage != "hash":
        for scenario in plan["scenarios"]:
            reseal(scenario)
        reseal(plan)
    assert assess(plan, pairs)["status"] == "unsupported"


def test_pairs_can_arrive_out_of_order_without_changing_result():
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    assert assess(plan, pairs) == assess(plan, list(reversed(pairs)))


def test_explicit_not_run_does_not_claim_missing_calculations_were_performed():
    plan = plan_for(evidence())
    result = assess(plan)
    assert result["status"] == "not_run"
    assert result["scenarios"] == []
    assert not result["qualified"]
    assert "Earlier development" in result["reasons"][0]
    assert assess(plan, [])["status"] == "unsupported"


def test_consistent_but_foreign_pair_cannot_mix_another_study_into_the_challenges():
    plan = plan_for(evidence())
    pairs = pairs_for(plan)
    for role in ("candidate", "baseline"):
        pairs[1][role] = rebuild(
            pairs[1][role], lambda row: row["binding"].update(inputs_artifact="foreign")
        )
    assert assess(plan, pairs)["status"] == "unsupported"


def test_identical_correlated_symbol_group_account_marks_do_not_inflate_observation_counts():
    plan = plan_for(evidence(symbols=("CORRELATED_A", "CORRELATED_B")))
    result = assess(plan, pairs_for(plan))
    assert result["status"] == "supported"
    a, b = result["scenarios"][:2]
    assert a["days"] == b["days"] == 80
    assert "independent" in result["limitations"][0]
    assert "effective_samples" not in result


def test_minute_union_with_an_unobserved_session_is_unavailable_before_accounts_run():
    original = evidence(minute=True)
    original["snapshot"]["timeline"].pop(12)
    plan = plan_for(original)
    assert plan["status"] == "unavailable"
    assert any("absent daily returns are not invented" in reason for reason in plan["reasons"])
    assert plan["scenarios"][2]["status"] == "unavailable"
    assert plan["scenarios"][3]["status"] == "available"
    assert assess(plan)["status"] == "insufficient"


def test_irregular_minute_counts_with_all_sessions_observed_keep_exact_timeline_identity():
    original = evidence(minute=True)
    original["snapshot"]["timeline"].insert(
        1, original["snapshot"]["sessions"][0] + "T15:30:00+05:30"
    )
    plan = plan_for(original)
    assert plan["status"] == "available"
    scenario = plan["scenarios"][0]
    assert scenario["observations_id"] == fingerprint(
        {"interval": "1m", "timeline": original["snapshot"]["timeline"]}
    )


def test_duplicate_or_reordered_calendar_and_oversized_signals_are_rejected():
    original = evidence()
    original["snapshot"]["sessions"][1] = original["snapshot"]["sessions"][0]
    with pytest.raises(ValueError, match="sessions"):
        plan_for(original)
    original = evidence()
    original["strategies"][0]["signals"] *= 80
    with pytest.raises(ValueError, match="signal limit"):
        plan_for(original)


def test_symbol_limit_matches_the_qualified_native_stock_context_profile():
    original = evidence(count=40, symbols=tuple(f"S{index:03d}" for index in range(201)))
    with pytest.raises(ValueError, match="symbol limit"):
        plan_for(original)
