"""Actual native v5 masked accounts; all OHLCV is explicitly controlled data."""

from copy import deepcopy

import pytest
from test_portfolio_capacity_integration import prepared as previous_prepared
from test_stock_market_context import refreeze, resign
from test_vectorbt_portfolio import assert_reconciles

from research.automatic_protocol import SENSITIVITY_VERSION, compile_recipe
from research.automatic_research import _entry_dates
from research.automatic_walk_forward import sensitivity_window
from research.connectors.vectorbt_portfolio import evaluate
from research.development_evidence import build
from research.evaluation_basis import build_evaluation_basis
from research.execution_features import attach as attach_atr
from research.execution_features import prepare as prepare_atr
from research.portfolio import execution_versions, normalize
from research.portfolio_constraint_evidence import assess as audit_constraints
from research.portfolio_execution_features import attach as attach_portfolio
from research.portfolio_execution_features import prepare as prepare_portfolio
from research.recommendation_sensitivity import assess
from research.report_contract import fingerprint
from research.volume_provenance import REVIEWED_ADAPTER_SHA256, capture_rows

pytestmark = pytest.mark.timeout(240)


def prepared(*, missing_volume=()):
    """Export the genuine two-symbol v14 service fixture for controller checks."""
    value = previous_prepared()
    value["portfolio"]["automatic_research"] = {"version": SENSITIVITY_VERSION}
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
        row["signals"].extend(
            {**deepcopy(signal), "symbol": "BBB", "row": signal["row"] + 1000}
            for signal in list(row["signals"])
        )
    value["signals"] = [
        dict(signal, strategy_id=row["id"])
        for row in value["strategies"]
        for signal in row["signals"]
    ]
    context = value["automatic_stock_market_context"]
    context["series"]["BBB"] = deepcopy(context["series"]["AAA"])
    context["series"]["BBB"]["evidence"]["descriptor"]["symbol"] = "BBB"
    refreeze(context["series"]["BBB"])
    resign(context)
    snapshot = value["snapshot"]
    snapshot["bars"]["BBB"] = deepcopy(snapshot["bars"]["AAA"])
    snapshot["session_hours"] = {
        day: {"open": "09:15", "close": "15:30"} for day in snapshot["sessions"]
    }
    value["versions"] = execution_versions(value["portfolio"])
    for key in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        value.pop(key, None)
    value = prepare_atr(value, context)
    records = {}
    for symbol, record in context["series"].items():
        if symbol in missing_volume:
            continue
        rows = {
            day: {**deepcopy(bar), "volume": 100_000, "oi": 0}
            for day, bar in record["evidence"]["bars"].items()
        }
        records[symbol] = capture_rows(
            symbol,
            list(rows.values()),
            rows,
            broker="fyers",
            adapter_sha256=REVIEWED_ADAPTER_SHA256,
            captured_at="2026-10-21T00:00:00+00:00",
        )
    value = prepare_portfolio(value, context, records)
    value["automatic_recipe"] = compile_recipe(value)
    return value


def accounts(value, *, order_size=None):
    """Execute eight fresh native accounts, never slice completed full curves."""
    plan = value["automatic_recipe"]["recommendation_sensitivity"]
    pairs, reports = [], []
    for scenario in plan["scenarios"]:
        window = sensitivity_window(value, scenario["name"])
        opportunity = build_evaluation_basis(window, period="selection")
        pair = {"scenario": scenario["name"]}
        for role in ("candidate", "baseline"):
            current = deepcopy(window)
            if role == "candidate" and order_size is not None:
                for strategy in current["strategies"]:
                    strategy["config"]["order_size_pct"] = order_size
            current = attach_portfolio(attach_atr(current))
            report = evaluate(
                current["strategies"], current["snapshot"], value["portfolio"]["capital"]
            )
            report["evaluation_basis"] = build_evaluation_basis(current, period="selection")
            report["research_opportunity_basis"] = opportunity
            audit = audit_constraints(report, current["portfolio_execution_features"])
            assert audit["status"] != "invalid", audit
            support = {
                "closed_trades": report["summary"]["closed_trades"],
                "entry_dates": _entry_dates(report),
            }
            facts = {
                "version": "research-sensitivity-account-evidence-v1",
                "plan_id": plan["id"],
                "scenario_id": scenario["id"],
                "period": scenario["name"],
                "basis_id": fingerprint(report["evaluation_basis"]),
                "support": support,
                "constraints": audit,
            }
            report["sensitivity_evidence"] = {**facts, "id": fingerprint(facts)}
            timeline = current["snapshot"]["sessions"]
            pair[role] = build(
                report,
                evaluation_id=fingerprint({"scenario": scenario["id"], "role": role}),
                binding={"inputs_artifact": "controlled-native-v14", "search_identity": plan["id"]},
                settings=report["strategies"],
                hypothesis={"id": "none"},
                period=scenario["name"],
                stress=False,
                expected_timeline=timeline,
            )
            pair[f"{role}_support"] = support
            pair[f"{role}_constraints"] = audit
            reports.append(report)
        pairs.append(pair)
    return pairs, reports


@pytest.fixture(scope="module")
def native_no_edge():
    value = prepared()
    original = deepcopy(value)
    pairs, reports = accounts(value)
    assert value == original
    return value, pairs, reports


def test_real_native_identical_settings_do_not_support_a_recommendation(native_no_edge):
    value, pairs, reports = native_no_edge
    result = assess(value["automatic_recipe"]["recommendation_sensitivity"], pairs)
    assert len(reports) == 8
    assert result["status"] == "not_supported", result
    assert not result["qualified"]
    assert all(row["return_edge_pct"] == 0 for row in result["scenarios"])
    assert all(row["support_sufficient"] for row in result["scenarios"])


def test_every_masked_account_reconciles_actual_native_fills_and_keeps_constraints(native_no_edge):
    value, pairs, reports = native_no_edge
    for report in reports:
        assert report["policy_version"] == "vectorbt-joint-causal-bars-v5"
        assert_reconciles(report)
        assert report["execution"]["risk_limits"] == value["portfolio"]["risk_limits"]
    assert all(
        pair[f"{role}_constraints"]["status"] == "supported"
        for pair in pairs
        for role in ("candidate", "baseline")
    )


def test_retained_native_facts_cannot_be_replaced_by_checkpoint_support_counts(native_no_edge):
    value, original, _ = native_no_edge
    pairs = deepcopy(original)
    pairs[0]["candidate_support"]["closed_trades"] += 1
    result = assess(value["automatic_recipe"]["recommendation_sensitivity"], pairs)
    assert result["status"] == "unsupported"
    assert any("retained account" in reason for reason in result["reasons"])


def test_scope_keeps_original_capital_allocations_and_exact_source_masks(native_no_edge):
    value, pairs, _ = native_no_edge
    plan = value["automatic_recipe"]["recommendation_sensitivity"]
    for scenario, pair in zip(plan["scenarios"], pairs, strict=True):
        scope = sensitivity_window(value, scenario["name"])
        assert scope["portfolio"]["capital"] == value["portfolio"]["capital"]
        assert [row["allocation_pct"] for row in scope["strategies"]] == [
            row["allocation_pct"] for row in value["strategies"]
        ]
        assert {signal["symbol"] for signal in scope["signals"]} <= set(scenario["symbols"])
        for role in ("candidate", "baseline"):
            assert pair[role]["evaluation_basis"]["source_id"] == scenario["source_id"]
            assert pair[role]["evaluation_basis"]["observations_id"] == scenario["observations_id"]


def test_date_halves_purge_full_maximum_holding_window_and_do_not_reuse_realized_trades(
    native_no_edge,
):
    value, _, reports = native_no_edge
    plan = value["automatic_recipe"]["recommendation_sensitivity"]
    early = next(row for row in plan["scenarios"] if row["name"] == "sens_dates_early")
    scope = sensitivity_window(value, early["name"])
    assert scope["automatic_period"]["purged_signals"] > 0
    assert any(signal.get("research_exclusion") for signal in scope["signals"])
    for report in reports[4:6]:
        for trade in report["ledger"]:
            if trade["status"] == "closed":
                assert trade["exit_date"] <= early["to"]
    assert reports[4]["equity_curve"][0]["date"] == early["from"]


def test_actual_changed_sizing_gets_its_own_native_account_and_verified_delta():
    value = prepared()
    pairs, reports = accounts(value, order_size=20)
    result = assess(value["automatic_recipe"]["recommendation_sensitivity"], pairs)
    assert result["status"] in ("supported", "not_supported"), result
    for row, pair in zip(result["scenarios"], pairs, strict=True):
        candidate, baseline = pair["candidate"], pair["baseline"]
        capital = candidate["account"]["initial_capital"]
        actual = (
            (candidate["curve"][-1]["equity"] - baseline["curve"][-1]["equity"]) / capital * 100
        )
        assert row["return_edge_pct"] == pytest.approx(actual)
        assert candidate["config_id"] != baseline["config_id"]
    for report in reports:
        assert_reconciles(report)


def test_missing_volume_model_support_is_insufficient_even_with_complete_price_bars():
    value = prepared(missing_volume=("AAA",))
    assert value["automatic_recipe"]["recommendation_sensitivity"]["status"] == "available"
    pairs, _ = accounts(value)
    result = assess(value["automatic_recipe"]["recommendation_sensitivity"], pairs)
    assert result["status"] == "insufficient", result
    assert not result["qualified"]
    assert any(pair["candidate_support"]["closed_trades"] == 0 for pair in pairs)
    counts = value["portfolio_execution_features"]["counts"]
    assert counts["capacity_available"] < counts["entry_windows"]


def test_final_period_outcomes_never_enter_earlier_scope_or_native_history(native_no_edge):
    value, _, _ = native_no_edge
    scenario = value["automatic_recipe"]["recommendation_sensitivity"]["scenarios"][0]
    expected = sensitivity_window(value, scenario["name"])
    changed = deepcopy(value)
    first = changed["automatic_recipe"]["periods"]["final"]["from"]
    for series in changed["snapshot"]["bars"].values():
        for day, bar in series.items():
            if day >= first:
                bar.update(open=1, high=1, low=1, close=1)
    # A genuinely changed full frozen input must be declared as a new recipe;
    # the sensitivity masks and earlier executable observations still agree.
    with pytest.raises(ValueError, match="frozen inputs changed"):
        sensitivity_window(changed, scenario["name"])
    changed.pop("automatic_recipe")
    changed["automatic_recipe"] = compile_recipe(changed)
    assert (
        changed["automatic_recipe"]["recommendation_sensitivity"]
        == value["automatic_recipe"]["recommendation_sensitivity"]
    )
    actual = sensitivity_window(changed, scenario["name"])
    assert actual["strategies"] == expected["strategies"]
    assert actual["snapshot"] == expected["snapshot"]


def test_symbol_mask_preserves_empty_strategy_definition_and_unallocated_capital():
    value = prepared()
    original = value["strategies"][0]
    value["strategies"] = [
        {
            **deepcopy(original),
            "id": f"strategy_{index}",
            "name": symbol,
            "allocation_pct": 50,
            "signals": [
                deepcopy(signal) for signal in original["signals"] if signal["symbol"] == symbol
            ],
        }
        for index, symbol in enumerate(("AAA", "BBB"))
    ]
    value["portfolio"]["strategies"] = [
        {key: deepcopy(item) for key, item in row.items() if key != "signals"}
        for row in value["strategies"]
    ]
    value["portfolio"] = normalize(value["portfolio"])
    value["signals"] = [
        dict(signal, strategy_id=row["id"])
        for row in value["strategies"]
        for signal in row["signals"]
    ]
    context = value["automatic_stock_market_context"]
    records = value["portfolio_execution_features"]["volume_records"]
    for key in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        value.pop(key, None)
    value = prepare_portfolio(prepare_atr(value, context), context, records)
    value["automatic_recipe"] = compile_recipe(value)
    scenario = value["automatic_recipe"]["recommendation_sensitivity"]["scenarios"][0]
    scope = sensitivity_window(value, scenario["name"])
    assert len(scope["strategies"]) == 2
    assert sorted(len(row["signals"]) for row in scope["strategies"])[0] == 0
    assert [row["allocation_pct"] for row in scope["strategies"]] == [50, 50]
    assert build_evaluation_basis(scope, period="selection")["source_id"] == scenario["source_id"]
    attached = attach_portfolio(attach_atr(scope))
    report = evaluate(attached["strategies"], attached["snapshot"], value["portfolio"]["capital"])
    assert len(report["strategies"]) == 2
    assert [row["allocation_pct"] for row in report["strategies"]] == [50, 50]
    assert report["config"]["initial_capital"] == value["portfolio"]["capital"]
    assert_reconciles(report)
    assert audit_constraints(report, value["portfolio_execution_features"])["status"] == "supported"
