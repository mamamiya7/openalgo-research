"""Pure feedback/catalogue checks; no engine, prices, provider, disk or database."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from research.engine import config_defaults
from research.execution_policy import normalize_policy
from research.execution_search import EXECUTION_CHOICES
from research.report_contract import fingerprint, settings_identity
from services.research_experiments import (
    CONFIGURATION_VERSION,
    MAX_HISTORY,
    VERSION,
    build,
    canonical_configuration,
    configuration_identity,
)

SOURCE = "a" * 64


@pytest.fixture
def case():
    cfg = config_defaults()
    cfg.update(
        hold_sessions=46,
        initial_capital=491000.0,
        order_size_pct=1.5,
        target_pct=50.0,
        stop_pct=45.0,
        trailing_enabled=True,
        trailing_pct=6.25,
        cost_bps=10,
    )
    settings = [
        {
            "id": "scan",
            "name": "Scanner",
            "allocation_pct": 100.0,
            "config": cfg,
            "execution_policy": normalize_policy(
                {
                    "version": "research-execution-aware-v5",
                    "allocation_mode": "original",
                    "capacity_mode": "prior20_broker_reported",
                    "correlation_mode": "prior60_positive_080",
                }
            ),
        }
    ]
    days, cursor = [], date(2026, 1, 1)
    while len(days) < 100:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    report = {
        "strategies": settings,
        "summary": {
            "accepted_trades": 29,
            "closed_trades": 29,
            "pending_trades": 0,
            "unfunded_pending": 0,
            "net_return_pct": 1.9,
        },
        "equity_curve": [{"date": day} for day in days],
        "ledger": [
            {
                "strategy_id": "scan",
                "status": "closed",
                "entry_date": days[0],
                "exit_date": days[held],
                "outcome": "trailing_stop" if index < 27 else "target",
                "fees": 10,
            }
            for index, held in enumerate([38, 30, *([10] * 27)])
        ],
        "automatic_stock_condition": {"hypothesis": {"id": "none"}},
        "automatic_design": {
            "qualified": False,
            "reasons": [
                "Fewer than 30 closed trades",
                "More than half of profit comes from one stock",
            ],
            "largest_symbol_positive_profit_fraction": 0.7,
        },
        "supervisor_validation": {
            "operating_validation": {
                "decision": {
                    "checks": [
                        {
                            "kind": "higher_costs",
                            "passed": False,
                            "summary": {"net_return_pct": 1.8},
                            "reasons": ["Cost-stressed account did not improve over baseline"],
                        },
                        {"kind": "neighboring_settings", "passed": False},
                    ],
                    "prefix_diagnostics": [
                        {
                            "period": f"fit{i}",
                            "passed": False,
                            "reasons": ["Net return did not improve on matched-capital baseline"],
                        }
                        for i in range(1, 4)
                    ],
                    "capital_diagnosis": {"admitted_requests": 29, "position_limit": 16},
                }
            }
        },
    }
    recipe = {
        "ranges": [
            {
                "id": "scan",
                "search": {
                    "hold_sessions": {"min": 23, "max": 83, "step": 12},
                    "target_pct": {"min": 25.0, "max": 100.0, "step": 12.5},
                    "stop_pct": {"min": 22.5, "max": 90.0, "step": 11.25},
                },
            }
        ],
        "trailing_research": {
            "strategies": [{"id": "scan", "choices": [0, 3.12, 4.69, 6.26, 7.83, 9.4, 10.97]}]
        },
        "execution_research": {
            "allocation_modes": ["original", "active_prior60_cluster_inverse_vol"],
            "strategies": [
                {
                    "id": "scan",
                    "execution_choices": deepcopy(list(EXECUTION_CHOICES)),
                    "original": {"target_pct": 50.0, "stop_pct": 45.0},
                }
            ],
        },
        "automatic_design": {"minimum_closed_trades": 30},
    }
    proposed = deepcopy(settings)
    proposed[0]["config"]["hold_sessions"] = 47
    proof = {
        "version": "research-remedy-effect-v1",
        "status": "unchanged",
        "seed_result_artifact": SOURCE,
        "selected_report_id": "b" * 64,
        "probe_accounts": 1,
        "additional_validation_accounts": 0,
    }
    proof["id"] = fingerprint(proof)
    proposals = [
        {
            "remedy": "lower_turnover",
            "settings": proposed,
            "outcome": "rejected",
            "no_effect": proof,
        }
    ]
    return report, recipe, proposals


def catalog(case, **kwargs):
    report, recipe, proposals = case
    return build(report, recipe, proposals, source_artifact=SOURCE, **kwargs)


def test_feedback_uses_actual_exit_mechanisms_and_full_failed_check_reasons(case):
    output = catalog(case)
    fact = output["feedback"]["exit_evidence"][0]
    assert fact["maximum_observed_holding"] == 38
    assert fact["exit_outcomes"] == {"trailing_stop": 27, "target": 2}
    assert fact["closed_trades"] == 29 and fact["complete"]
    assert output["feedback"]["sample_shortfall"] == 1
    failures = output["feedback"]["failed_checks"]
    assert {row["kind"] for row in failures} == {
        "higher_costs",
        "neighboring_settings",
        "temporal_stability",
        "sample_and_distribution",
    }
    assert (
        "matched-capital baseline"
        in next(row for row in failures if row["kind"] == "temporal_stability")["reasons"][0]
    )
    assert output["feedback"]["no_effect_directions"][0]["tested"] == 47


def test_nonbinding47_and59_are_pruned_but_effect_relevant23_and35_remain(case):
    options = catalog(case)["options"]
    holding = [option for option in options if option["change"]["axis"] == "hold_sessions"]
    assert {option["change"]["to"] for option in holding} == {23, 35}
    assert all(option["change"]["to"] < 38 for option in holding)
    assert any(row["method"] == "longer_holding" for row in catalog(case)["unavailable"])
    assert not any(
        option["settings"][0]["config"]["hold_sessions"] in (47, 59, 71, 83) for option in options
    )


def test_options_are_bounded_bound_to_source_and_concrete_settings_and_never_claim_success(case):
    original = deepcopy(case)
    first = catalog(case)
    assert first["version"] == VERSION and len(first["options"]) == 8
    assert case == original
    for option in first["options"]:
        assert set(option) == {"id", "remedy", "settings", "change", "expected_effect", "reason"}
        assert option["expected_effect"].startswith("Prediction, not a result:")
        assert option["id"] == fingerprint(
            {
                "version": VERSION,
                "source_artifact": SOURCE,
                "remedy": option["remedy"],
                "settings_id": settings_identity(option["settings"]),
            }
        )
    second = build(*case, source_artifact="c" * 64)
    assert {option["id"] for option in first["options"]}.isdisjoint(
        option["id"] for option in second["options"]
    )
    assert catalog(case) == first


def test_causal_allocation_and_material_frozen_exit_variants_are_supported(case):
    options = catalog(case)["options"]
    assert any(
        option["remedy"] == "reduce_concentration"
        and option["settings"][0]["execution_policy"]["allocation_mode"]
        == "active_prior60_cluster_inverse_vol"
        for option in options
    )
    trailing = [option for option in options if option["change"]["axis"] == "trailing_choice"]
    assert {option["change"]["to"] for option in trailing} == {10.97, 9.4, 0.0}
    assert not any(option["change"].get("to") == 6.26 for option in trailing)
    assert any(
        option["change"]["axis"] == "execution_choice" and option["change"]["to"] == "atr_2_4"
        for option in options
    )


def test_capital_trade_size_style_signals_and_risk_remain_fixed(case):
    base = case[0]["strategies"][0]
    for option in catalog(case)["options"]:
        row = option["settings"][0]
        assert row["id"] == base["id"] and row.get("entry_condition") == base.get("entry_condition")
        assert row["execution_policy"]["risk_limits"] == base["execution_policy"]["risk_limits"]
        for key in (
            "initial_capital",
            "order_size_pct",
            "trade_horizon",
            "entry_time",
            "exit_time",
        ):
            assert row["config"].get(key) == base["config"].get(key)
        if option["change"]["axis"] == "hold_sessions":
            assert row["config"]["hold_sessions"] in (23, 35, 47, 59, 71, 83)


def test_exact_negative_is_removed_across_remedy_names_but_pending_is_reconstructible(case):
    chosen = catalog(case)["options"][0]
    report, recipe, proposals = case
    pending = {"remedy": chosen["remedy"], "settings": chosen["settings"], "outcome": None}
    assert chosen in build(report, recipe, proposals + [pending], source_artifact=SOURCE)["options"]
    negative = {**pending, "remedy": "lower_turnover", "outcome": "rejected"}
    assert chosen["id"] not in {
        option["id"]
        for option in build(report, recipe, proposals + [negative], source_artifact=SOURCE)[
            "options"
        ]
    }


def test_memory_suppresses_exact_same_input_negative_and_allows_new_data_retest(case):
    chosen = catalog(case)["options"][0]
    memory = {
        "entries": [
            {
                "same_inputs": True,
                "settings_id": settings_identity(chosen["settings"]),
                "outcome": "rejected",
            }
        ]
    }
    assert chosen["id"] not in {option["id"] for option in catalog(case, memory=memory)["options"]}
    memory["entries"][0]["same_inputs"] = False
    assert chosen in catalog(case, memory=memory)["options"]
    with pytest.raises(ValueError, match="bounded"):
        catalog(case, memory={"entries": [{}] * (MAX_HISTORY + 1)})


def test_inactive_trailing_distance_is_equivalent_without_mutating_complete_settings(case):
    first = deepcopy(case[0]["strategies"])
    first[0]["config"].update(trailing_enabled=False, trailing_pct=0.0)
    second = deepcopy(first)
    second[0]["config"]["trailing_pct"] = 10.97
    original = deepcopy(second)
    assert settings_identity(first) != settings_identity(second)
    assert configuration_identity(first) == configuration_identity(second)
    assert "trailing_pct" not in canonical_configuration(second)[0]["config"]
    assert second == original


@pytest.mark.parametrize(
    "location,key,value",
    [
        ("config", "trailing_pct", 10.97),
        ("config", "hold_sessions", 23),
        ("config", "initial_capital", 100000.0),
        ("config", "order_size_pct", 3.0),
        ("config", "cost_bps", 20.0),
        ("execution_policy", "allocation_mode", "active_prior60_cluster_inverse_vol"),
    ],
)
def test_complete_comparison_preserves_active_execution_controls(case, location, key, value):
    first = case[0]["strategies"]
    second = deepcopy(first)
    second[0][location][key] = value
    assert configuration_identity(first, engine="vectorbt") != configuration_identity(
        second, engine="vectorbt"
    )


def test_active_stop_risk_constraints_and_atr_parameters_never_collapse(case):
    first = deepcopy(case[0]["strategies"])
    first[0]["execution_policy"].update(
        exit_mode="atr", atr_period=14, atr_stop_multiple=2, atr_target_multiple=4
    )
    second = deepcopy(first)
    second[0]["execution_policy"]["risk_limits"]["max_total_stop_risk_pct"] = 4
    assert configuration_identity(first, engine="vectorbt") != configuration_identity(
        second, engine="vectorbt"
    )
    third = deepcopy(first)
    third[0]["execution_policy"]["atr_stop_multiple"] = 3
    assert configuration_identity(first, engine="vectorbt") != configuration_identity(
        third, engine="vectorbt"
    )


def test_percent_exit_fields_are_inactive_only_for_supported_vectorbt_atr(case):
    first = deepcopy(case[0]["strategies"])
    first[0]["execution_policy"].update(
        exit_mode="atr", atr_period=14, atr_stop_multiple=2, atr_target_multiple=4
    )
    second = deepcopy(first)
    second[0]["config"].update(stop_pct=22.5, target_pct=100.0)
    assert configuration_identity(first, engine="vectorbt") == configuration_identity(
        second, engine="vectorbt"
    )
    assert configuration_identity(first) != configuration_identity(second)
    assert configuration_identity(first, engine="nautilus") != configuration_identity(
        second, engine="nautilus"
    )
    for settings in (first, second):
        settings[0]["execution_policy"]["exit_mode"] = "percent"
    assert configuration_identity(first, engine="vectorbt") != configuration_identity(
        second, engine="vectorbt"
    )


def test_canonical_negative_is_pruned_but_a_distinct_holding_horizon_remains(case):
    report, recipe, proposals = case
    report["strategies"][0]["config"].update(trailing_enabled=False, trailing_pct=9.4)
    chosen = next(option for option in catalog(case)["options"] if option["change"].get("to") == 23)
    prior = deepcopy(chosen["settings"])
    prior[0]["config"]["trailing_pct"] = 0.0
    proposals.append({"remedy": "temporal_stability", "settings": prior, "outcome": "rejected"})
    options = catalog(case)["options"]
    assert not any(option["change"].get("to") == 23 for option in options)
    assert any(option["change"].get("to") == 35 for option in options)


def test_same_holding_value_with_different_active_trailing_is_a_distinct_configuration(case):
    chosen = next(option for option in catalog(case)["options"] if option["change"].get("to") == 23)
    prior = deepcopy(chosen["settings"])
    prior[0]["config"]["trailing_pct"] = 9.4
    case[2].append({"remedy": "temporal_stability", "settings": prior, "outcome": "rejected"})
    assert chosen in catalog(case)["options"]


def test_atr_equivalent_percent_parameters_are_not_retested_on_vectorbt(case):
    case[0]["portfolio"] = {"engine": "vectorbt"}
    chosen = next(
        option
        for option in catalog(case)["options"]
        if option["change"]["axis"] == "execution_choice"
    )
    prior = deepcopy(chosen["settings"])
    prior[0]["config"].update(stop_pct=22.5, target_pct=100.0)
    case[2].append({"remedy": "lower_turnover", "settings": prior, "outcome": "rejected"})
    assert not any(
        option["change"]["axis"] == "execution_choice" for option in catalog(case)["options"]
    )


def test_canonical_memory_is_bound_to_same_inputs_identity_version_and_engine(case):
    chosen = catalog(case)["options"][0]
    entry = {
        "same_inputs": True,
        "outcome": "rejected",
        "configuration_version": CONFIGURATION_VERSION,
        "configuration_id": configuration_identity(chosen["settings"]),
    }
    memory = {"entries": [entry]}
    assert chosen not in catalog(case, memory=memory)["options"]
    entry["same_inputs"] = False
    assert chosen in catalog(case, memory=memory)["options"]
    entry["same_inputs"] = True
    entry["configuration_version"] = "unknown-version"
    assert chosen in catalog(case, memory=memory)["options"]
    entry["configuration_version"] = CONFIGURATION_VERSION
    entry["configuration_id"] = configuration_identity(chosen["settings"], engine="nautilus")
    assert chosen in catalog(case, memory=memory)["options"]


@pytest.mark.parametrize("cause", ["timing", "pending", "curve"])
def test_incomplete_holding_evidence_cannot_justify_effectful_holding_claim(case, cause):
    report, _, _ = case
    if cause == "timing":
        report["ledger"][0]["exit_date"] = None
    elif cause == "pending":
        report["summary"]["pending_trades"] = 1
    else:
        report["equity_curve"] = []
    output = catalog(case)
    assert not output["feedback"]["no_effect_directions"]
    assert not any(option["change"]["axis"] == "hold_sessions" for option in output["options"])


def test_zero_fee_or_no_cost_challenge_does_not_generate_cost_exit_hypotheses(case):
    for row in case[0]["ledger"]:
        row["fees"] = 0
    output = catalog(case)
    assert all(option["remedy"] != "lower_turnover" for option in output["options"])
    assert any(row["method"] == "cost_exit_changes" for row in output["unavailable"])


def test_registered_horizon_exits_can_justify_a_material_longer_hold(case):
    report, _, proposals = case
    proposals.clear()
    report["ledger"][0].update(outcome="hold", exit_date=report["equity_curve"][46]["date"])
    output = catalog(case)
    longer = [
        option
        for option in output["options"]
        if option["change"].get("axis") == "hold_sessions" and option["change"]["to"] > 46
    ]
    assert len(longer) == 1 and longer[0]["change"]["to"] == 83


def test_funding_is_a_native_programme_before_performance_not_an_invented_cash_amount(case):
    report, _, proposals = case
    decision = report["supervisor_validation"]["operating_validation"]["decision"]
    decision["capital_diagnosis"] = {"cash_shortfall": 4}
    option = catalog(case)["options"][0]
    assert option["remedy"] == "funding_and_size"
    assert option["settings"] == report["strategies"]
    assert option["change"]["current_capital"] == 491000
    assert "not invented" in option["expected_effect"]
    pending = {
        "remedy": "funding_and_size",
        "settings": deepcopy(option["settings"]),
        "outcome": None,
    }
    assert (
        option in build(report, case[1], proposals + [pending], source_artifact=SOURCE)["options"]
    )
    original_request = deepcopy(option["settings"])
    original_request[0]["config"]["initial_capital"] = 100000
    done = {
        "remedy": "funding_and_size",
        "settings": original_request,
        "outcome": "rejected",
        "experiment": {
            "feedback": {"validated_settings_id": settings_identity(report["strategies"])}
        },
    }
    assert not any(
        row["remedy"] == "funding_and_size"
        for row in build(report, case[1], proposals + [done], source_artifact=SOURCE)["options"]
    )


def test_missing_sizing_evidence_and_fixed_position_limits_do_not_request_extra_money(case):
    decision = case[0]["supervisor_validation"]["operating_validation"]["decision"]
    decision["capital_diagnosis"] = {"cash_shortfall": 4, "unavailable_sizing_intent": 2}
    assert not any(option["remedy"] == "funding_and_size" for option in catalog(case)["options"])
    decision["capital_diagnosis"] = {"position_limit": 16}
    assert not any(option["remedy"] == "funding_and_size" for option in catalog(case)["options"])


def blocked_case(case, counts=None):
    report, _, _ = case
    report["portfolio"] = {"engine": "vectorbt"}
    counts = counts or {"admitted_requests": 21, "percentage_policy": 41}
    report["supervisor_validation"].update(
        outcome="blocked",
        feasibility={
            "version": "research-feasibility-v1",
            "scope": "feasibility_only",
            "source_settings_id": settings_identity(report["strategies"]),
            "counts": deepcopy(counts),
            "unresolved_constraints": {
                key: value for key, value in counts.items() if key != "admitted_requests"
            },
        },
    )
    return case


def test_changed_complete_execution_setting_readmits_funding_after_prior_repair(case):
    report, recipe, proposals = blocked_case(case)
    old = deepcopy(report["strategies"])
    old[0]["config"].update(hold_sessions=20, trailing_enabled=True, trailing_pct=6.25)
    report["strategies"][0]["config"].update(
        hold_sessions=40, trailing_enabled=False, trailing_pct=0.0
    )
    report["supervisor_validation"]["feasibility"]["source_settings_id"] = settings_identity(
        report["strategies"]
    )
    proposals.append(
        {
            "remedy": "funding_and_size",
            "settings": old,
            "outcome": "rejected",
            "experiment": {
                "feedback": {
                    "validated_settings_id": settings_identity(old),
                    "configuration_version": CONFIGURATION_VERSION,
                    "configuration_id": configuration_identity(old, engine="vectorbt"),
                }
            },
        }
    )
    output = build(report, recipe, proposals, source_artifact=SOURCE)
    assert [row["remedy"] for row in output["options"]] == ["funding_and_size"]
    option = output["options"][0]
    assert option["settings"] == report["strategies"]
    assert option["funding_source_configuration_id"] == configuration_identity(
        report["strategies"], engine="vectorbt"
    )
    assert option["change"]["diagnosis"] == {"percentage_policy": 41}
    assert output["feedback"]["mode"] == "feasibility_only"
    assert "exit_evidence" not in output["feedback"]
    assert "failed_checks" not in output["feedback"]


@pytest.mark.parametrize(
    "matching", ["request", "effective_source", "actual_result", "legacy_actual"]
)
def test_same_complete_source_or_funded_result_does_not_blindly_repeat_repair(case, matching):
    report, recipe, proposals = blocked_case(case)
    source = deepcopy(report["strategies"])
    if matching != "request":
        source[0]["config"]["initial_capital"] = 100000
    prior = {"remedy": "funding_and_size", "settings": source, "outcome": "rejected"}
    if matching == "effective_source":
        prior["candidate"] = {
            "funding_source_configuration_id": configuration_identity(
                report["strategies"], engine="vectorbt"
            )
        }
    if matching == "actual_result":
        prior["experiment"] = {
            "feedback": {
                "configuration_version": CONFIGURATION_VERSION,
                "configuration_id": configuration_identity(report["strategies"], engine="vectorbt"),
            }
        }
    if matching == "legacy_actual":
        prior["experiment"] = {
            "feedback": {"validated_settings_id": settings_identity(report["strategies"])}
        }
    output = build(report, recipe, proposals + [prior], source_artifact=SOURCE)
    assert output["options"] == []
    assert "complete source or funded configuration" in output["unavailable"][0]["reason"]


def test_changed_settings_do_not_inherit_another_settings_funding_memory(case):
    report, recipe, proposals = blocked_case(case)
    old = deepcopy(report["strategies"])
    old[0]["config"]["hold_sessions"] = 20
    entry = {
        "remedy": "funding_and_size",
        "same_inputs": True,
        "outcome": "rejected",
        "configuration_version": CONFIGURATION_VERSION,
        "configuration_id": configuration_identity(old, engine="vectorbt"),
    }
    assert build(report, recipe, proposals, source_artifact=SOURCE, memory={"entries": [entry]})[
        "options"
    ]
    entry["configuration_id"] = configuration_identity(report["strategies"], engine="vectorbt")
    assert (
        build(report, recipe, proposals, source_artifact=SOURCE, memory={"entries": [entry]})[
            "options"
        ]
        == []
    )
    entry["same_inputs"] = False
    assert build(report, recipe, proposals, source_artifact=SOURCE, memory={"entries": [entry]})[
        "options"
    ]


@pytest.mark.parametrize("bad", ["missing", "source", "version", "scope", "negative", "unresolved"])
def test_blocked_missing_or_unbound_feasibility_never_advertises_performance(case, bad):
    blocked_case(case)
    feasibility = case[0]["supervisor_validation"]["feasibility"]
    if bad == "missing":
        del case[0]["supervisor_validation"]["feasibility"]
    elif bad == "source":
        feasibility["source_settings_id"] = "f" * 64
    elif bad == "version":
        feasibility["version"] = "unknown"
    elif bad == "scope":
        feasibility["scope"] = "performance_selection"
    elif bad == "negative":
        feasibility["counts"]["percentage_policy"] = -1
    else:
        feasibility["unresolved_constraints"]["percentage_policy"] = 999
    output = catalog(case)
    assert output["options"] == []
    assert output["feedback"]["mode"] == "feasibility_only"
    assert "feasibility evidence" in output["unavailable"][0]["reason"]


@pytest.mark.parametrize(
    "blocker",
    [
        "unavailable_sizing_intent",
        "unexplained_execution_limit",
        "evidence_gap_requests",
        "unavailable_evidence",
    ],
)
def test_missing_sizing_or_market_evidence_cannot_be_repaired_by_extra_funds(case, blocker):
    blocked_case(case, {"admitted_requests": 21, "percentage_policy": 41, blocker: 1})
    output = catalog(case)
    assert output["options"] == []
    assert "cannot invent" in output["unavailable"][0]["reason"]


def test_blocked_later_period_mode_exposes_only_feasibility_repair_even_with_positive_return(case):
    blocked_case(case)
    case[1]["validation_mode"] = "chronological"
    case[0]["summary"]["net_return_pct"] = 99.0
    assert [row["remedy"] for row in catalog(case)["options"]] == ["funding_and_size"]
    assert "native_fees" not in catalog(case)["feedback"]


def test_fixed_only_capacity_block_cannot_relax_limits_or_create_performance_hypothesis(case):
    blocked_case(case, {"admitted_requests": 21, "position_limit": 4})
    output = catalog(case)
    assert output["options"] == []
    assert "fixed position/liquidity limits cannot be relaxed" in output["unavailable"][0]["reason"]


def test_unsupported_execution_or_allocation_methods_are_never_introduced(case):
    execution = case[1]["execution_research"]
    execution["allocation_modes"] = ["unknown_method"]
    execution["strategies"][0]["execution_choices"] = [{"id": "invented", "exit_mode": "atr"}]
    output = catalog(case)
    assert not any(
        option["change"]["axis"] in ("allocation_mode", "execution_choice")
        for option in output["options"]
    )


def test_bad_source_filter_or_unbounded_history_is_rejected(case):
    with pytest.raises(ValueError, match="immutable"):
        build(*case, source_artifact="unbound")
    with pytest.raises(ValueError, match="bounded"):
        build(case[0], case[1], [{}] * (MAX_HISTORY + 1), source_artifact=SOURCE)
    case[0]["automatic_stock_condition"]["hypothesis"]["id"] = "new_filter"
    with pytest.raises(ValueError, match="original"):
        catalog(case)
