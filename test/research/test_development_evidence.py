"""Complete actual account history, source binding and bounded optional retention."""

import copy
from datetime import datetime, timedelta

import pytest

from research.connectors.vectorbt_portfolio import _curve, validate_config
from research.development_evidence import (
    MAX_EVALUATIONS,
    MAX_METADATA_BYTES,
    MAX_TOTAL_BYTES,
    build,
    plan,
    verify_shard,
)
from research.evaluation_basis import build_evaluation_basis
from research.report_contract import fingerprint


def inputs(*, minute=False):
    keys = ["2026-01-05", "2026-01-07", "2026-01-09"]
    if minute:
        keys = [
            "2026-01-05T09:15:00+05:30",
            "2026-01-05T09:19:00+05:30",
            "2026-01-07T10:01:00+05:30",
        ]
    sessions = sorted({key[:10] for key in keys})
    strategy = {
        "id": "a",
        "name": "Scanner A",
        "allocation_pct": 100,
        "config": validate_config({"hold_sessions": 1, "order_size_pct": 100, "cost_bps": 0}),
        "signals": [{"symbol": "AAA", "date": sessions[0], "row": 2}],
    }
    prices = {
        "sessions": sessions,
        "provenance": {"interval": "1m" if minute else "D", "exchange": "NSE", "synthetic": True},
        "bars": {"AAA": {key: {"open": 100, "high": 110, "low": 90, "close": 101} for key in keys}},
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in sessions},
    }
    if minute:
        prices["timeline"] = keys
        strategy["signals"][0]["timestamp"] = keys[0]
        strategy["config"] = validate_config(
            {"trade_horizon": "intraday", "hold_minutes": 1, "order_size_pct": 100, "cost_bps": 0}
        )
    return {
        "snapshot": prices,
        "portfolio": {"capital": 1000},
        "strategies": [strategy],
        "versions": {"engine": "vectorbt", "engine_version": "0.28.5"},
    }


def report_and_kwargs(*, minute=False, equities=(1010, 990, 1050)):
    evidence = inputs(minute=minute)
    timeline = evidence["snapshot"]["timeline" if minute else "sessions"]
    settings = [
        {key: row[key] for key in ("id", "name", "allocation_pct", "config")}
        for row in evidence["strategies"]
    ]
    report = {
        "strategies": copy.deepcopy(settings),
        "config": {"initial_capital": 1000},
        "evaluation_basis": build_evaluation_basis(evidence, period="selection"),
        "equity_curve": _curve(
            timeline, equities, [min(value, 900) for value in equities], [1, 1, 1], 1000, minute
        ),
    }
    kwargs = {
        "evaluation_id": fingerprint({"settings": settings, "period": "search", "stress": False}),
        "binding": {"inputs_artifact": "sha256-source", "search_identity": "sha256-search"},
        "settings": settings,
        "hypothesis": {"id": "none"},
        "period": "search",
        "stress": False,
        "expected_timeline": timeline,
    }
    return report, kwargs


def descriptor(shard):
    return {
        key: copy.deepcopy(shard[key])
        for key in (
            "id",
            "evaluation_id",
            "config_id",
            "binding",
            "period",
            "stress",
            "evaluation_basis_id",
        )
    }


@pytest.mark.parametrize("minute", [False, True])
def test_retains_all_actual_irregular_marks_without_resampling_or_rounded_returns(minute):
    report, kwargs = report_and_kwargs(minute=minute)
    original = copy.deepcopy((report, kwargs))
    shard = build(report, **kwargs)
    assert shard["curve"] == report["equity_curve"]
    assert shard["timeline"] == kwargs["expected_timeline"]
    assert shard["account"] == {"currency": "INR", "initial_capital": 1000.0}
    assert shard["simple_returns"] == pytest.approx([0.01, 990 / 1010 - 1, 1050 / 990 - 1])
    compounded = 1000
    for value in shard["simple_returns"]:
        compounded *= 1 + value
    assert compounded == pytest.approx(1050)
    assert verify_shard(shard, descriptor=descriptor(shard)) is True
    assert build(report, **kwargs) == shard
    assert (report, kwargs) == original


def test_zero_equity_retains_loss_and_explicit_unavailable_next_return():
    report, kwargs = report_and_kwargs(equities=(1000, 0, 0))
    shard = build(report, **kwargs)
    assert shard["simple_returns"] == [0, -1, None]
    assert shard["curve"][1]["equity"] == 0
    assert "zero-denominator-unavailable" in shard["return_policy"]
    assert verify_shard(shard, descriptor=descriptor(shard))


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "reordered",
        "duplicate",
        "extra",
        "minute_date",
        "missing_timestamp",
        "naive_timestamp",
    ],
)
def test_incomplete_or_wrong_actual_observations_are_rejected(damage):
    report, kwargs = report_and_kwargs(minute=True)
    curve = report["equity_curve"]
    if damage == "missing":
        curve.pop()
    elif damage == "reordered":
        curve.reverse()
    elif damage == "duplicate":
        curve[1] = copy.deepcopy(curve[0])
    elif damage == "extra":
        curve.append(copy.deepcopy(curve[-1]))
    elif damage == "minute_date":
        curve[0]["date"] = "2026-01-06"
    elif damage == "missing_timestamp":
        curve[0].pop("timestamp")
    else:
        kwargs["expected_timeline"][0] = "2026-01-05T09:15:00"
    with pytest.raises(ValueError):
        build(report, **kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("equity", float("nan")),
        ("equity", float("inf")),
        ("equity", -1),
        ("cash", float("-inf")),
        ("cash", -1),
        ("cash", 100000),
        ("drawdown_pct", float("nan")),
        ("drawdown_pct", 101),
        ("drawdown_pct", 20),
        ("open_positions", -1),
        ("open_positions", 1.5),
        ("open_positions", True),
    ],
)
def test_account_values_are_finite_and_coherent(field, value):
    report, kwargs = report_and_kwargs()
    report["equity_curve"][0][field] = value
    with pytest.raises(ValueError):
        build(report, **kwargs)


@pytest.mark.parametrize(
    "damage",
    [
        "capital",
        "unverified",
        "basis_id",
        "observation_id",
        "comparison_id",
        "settings",
        "condition",
        "no_curve",
    ],
)
def test_conflicting_account_basis_or_actual_settings_are_rejected(damage):
    report, kwargs = report_and_kwargs()
    basis = report["evaluation_basis"]
    if damage == "capital":
        report["config"]["initial_capital"] = 2000
    elif damage == "unverified":
        basis["status"] = "unverified"
    elif damage == "basis_id":
        basis["evidence_id"] = "unrelated"
    elif damage == "observation_id":
        basis["observations_id"] = "unrelated"
    elif damage == "comparison_id":
        basis["comparison"]["id"] = "unrelated"
    elif damage == "settings":
        report["strategies"][0]["config"]["hold_sessions"] = 2
    elif damage == "condition":
        kwargs["settings"][0]["entry_condition"] = {"id": "rsi"}
    else:
        report.pop("equity_curve")
    with pytest.raises(ValueError):
        build(report, **kwargs)


@pytest.mark.parametrize("field", ["binding", "hypothesis", "period", "stress"])
def test_each_development_context_affects_deterministic_identity(field):
    report, kwargs = report_and_kwargs()
    first = build(report, **kwargs)
    changes = {
        "binding": {"inputs_artifact": "other-source", "search_identity": "other-search"},
        "hypothesis": {"id": "rsi"},
        "period": "check1",
        "stress": True,
    }
    kwargs[field] = changes[field]
    second = build(report, **kwargs)
    assert second["id"] != first["id"]
    assert second["curve"] == first["curve"]


def test_changed_frozen_source_and_settings_cannot_reuse_original_descriptor():
    report, kwargs = report_and_kwargs()
    first = build(report, **kwargs)
    evidence = inputs()
    evidence["strategies"][0]["signals"][0]["row"] = 3
    report["evaluation_basis"] = build_evaluation_basis(evidence, period="selection")
    second = build(report, **kwargs)
    assert first["evaluation_basis_id"] != second["evaluation_basis_id"]
    assert first["id"] != second["id"]
    with pytest.raises(ValueError, match="conflicts"):
        verify_shard(second, descriptor=descriptor(first))


@pytest.mark.parametrize(
    "field", ["simple_returns", "timeline", "account", "return_policy", "extra"]
)
def test_rehashed_tampering_does_not_satisfy_contract(field):
    report, kwargs = report_and_kwargs()
    shard = build(report, **kwargs)
    if field == "simple_returns":
        shard[field][0] = 0
    elif field == "timeline":
        shard[field][1] = shard[field][0]
    elif field == "account":
        shard[field]["currency"] = "USD"
    elif field == "return_policy":
        shard[field] = "invented_return"
    else:
        shard[field] = "unadmitted metadata"
    shard["id"] = fingerprint({key: value for key, value in shard.items() if key != "id"})
    with pytest.raises(ValueError):
        verify_shard(shard, descriptor=descriptor(shard))


def test_shard_hash_and_required_descriptor_fields_are_not_optional():
    report, kwargs = report_and_kwargs()
    shard = build(report, **kwargs)
    frozen = descriptor(shard)
    shard["curve"][0]["equity"] += 1
    with pytest.raises(ValueError, match="identity"):
        verify_shard(shard, descriptor=frozen)
    shard = build(report_and_kwargs()[0], **report_and_kwargs()[1])
    frozen = descriptor(shard)
    frozen.pop("binding")
    with pytest.raises(ValueError, match="binding"):
        verify_shard(shard, descriptor=frozen)


@pytest.mark.parametrize("period", ["final", "evaluation"])
def test_reserved_final_is_never_admitted_to_development_corpus(period):
    report, kwargs = report_and_kwargs()
    kwargs["period"] = period
    with pytest.raises(ValueError, match="reserved final"):
        build(report, **kwargs)


def test_oversized_metadata_is_unavailable_instead_of_partially_retained():
    report, kwargs = report_and_kwargs()
    kwargs["hypothesis"]["description"] = "a" * MAX_METADATA_BYTES
    with pytest.raises(ValueError, match="metadata limit"):
        build(report, **kwargs)


def test_final_basis_cannot_be_relabelled_as_search_and_retained():
    report, kwargs = report_and_kwargs()
    report["evaluation_basis"] = build_evaluation_basis(inputs(), period="evaluation")
    with pytest.raises(ValueError, match="reserved final"):
        build(report, **kwargs)


def test_cost_scenario_must_agree_with_actual_settings_even_with_valid_basis_hash():
    report, kwargs = report_and_kwargs()
    report["strategies"][0]["config"]["cost_bps"] = 20
    kwargs["settings"] = copy.deepcopy(report["strategies"])
    with pytest.raises(ValueError, match="cost scenario"):
        build(report, **kwargs)


@pytest.mark.parametrize(
    "value",
    [None, [], {"snapshot": None}, {"snapshot": inputs()["snapshot"], "automatic_recipe": None}],
)
def test_malformed_evidence_is_explicitly_unavailable(value):
    assert plan(value, 1) == {**plan({}, 1), "reason": "unverified_timeline"}


def test_metadata_and_curves_are_not_shared_with_future_caller_mutations():
    report, kwargs = report_and_kwargs()
    retained = build(report, **kwargs)
    before = copy.deepcopy(retained)
    kwargs["binding"]["inputs_artifact"] = "new source"
    kwargs["settings"][0]["config"]["cost_bps"] = 999
    kwargs["hypothesis"]["id"] = "other"
    report["evaluation_basis"]["comparison"]["currency"] = "other"
    report["equity_curve"][0]["equity"] = 9999
    assert retained == before
    assert verify_shard(retained, descriptor=descriptor(before))


@pytest.mark.parametrize("key", ["inputs_artifact", "search_identity"])
def test_immutable_source_and_search_bindings_are_required(key):
    report, kwargs = report_and_kwargs()
    kwargs["binding"].pop(key)
    with pytest.raises(ValueError, match="source binding"):
        build(report, **kwargs)


def stock_condition(hypothesis):
    return {
        "hypothesis": copy.deepcopy(hypothesis),
        "bundle_id": "a" * 64,
        "gate_id": "b" * 64,
        "counts": {"allowed": 1, "filtered": 0, "unknown": 0, "original_excluded": 0, "pending": 0},
    }


def test_optional_actual_stock_receipt_retained_and_independent_of_report_mutation():
    report, kwargs = report_and_kwargs()
    report["automatic_stock_condition"] = stock_condition(kwargs["hypothesis"])
    shard = build(report, **kwargs)
    assert shard["stock_condition"] == report["automatic_stock_condition"]
    assert verify_shard(shard, descriptor=descriptor(shard))
    report["automatic_stock_condition"]["counts"]["allowed"] = 999
    assert shard["stock_condition"]["counts"]["allowed"] == 1


@pytest.mark.parametrize(
    "damage",
    [
        "missing_key",
        "extra",
        "hypothesis",
        "bundle_id",
        "gate_id",
        "missing_count",
        "negative",
        "nonfinite",
        "bool_count",
        "huge_total",
        "null",
    ],
)
def test_actual_stock_receipt_must_have_bounded_native_shape_and_same_hypothesis(damage):
    report, kwargs = report_and_kwargs()
    receipt = stock_condition(kwargs["hypothesis"])
    if damage == "missing_key":
        receipt.pop("counts")
    elif damage == "extra":
        receipt["invented"] = "metadata"
    elif damage == "hypothesis":
        receipt["hypothesis"] = {"id": "rsi"}
    elif damage in ("bundle_id", "gate_id"):
        receipt[damage] = "not-a-hash"
    elif damage == "missing_count":
        receipt["counts"].pop("unknown")
    elif damage in ("negative", "nonfinite", "bool_count"):
        receipt["counts"]["allowed"] = {
            "negative": -1,
            "nonfinite": float("nan"),
            "bool_count": True,
        }[damage]
    elif damage == "huge_total":
        receipt["counts"].update(allowed=20_000, filtered=20_000)
    else:
        receipt = None
    report["automatic_stock_condition"] = receipt
    with pytest.raises(ValueError):
        build(report, **kwargs)


@pytest.mark.parametrize("damage", ["hypothesis", "receipt_hypothesis", "receipt_counts"])
def test_rehashed_stock_hypothesis_or_receipt_conflict_cannot_verify(damage):
    report, kwargs = report_and_kwargs()
    report["automatic_stock_condition"] = stock_condition(kwargs["hypothesis"])
    shard = build(report, **kwargs)
    if damage == "hypothesis":
        shard["hypothesis"] = {"id": "rsi"}
    elif damage == "receipt_hypothesis":
        shard["stock_condition"]["hypothesis"] = {"id": "rsi"}
    else:
        shard["stock_condition"]["counts"]["allowed"] = -1
    shard["id"] = fingerprint({key: value for key, value in shard.items() if key != "id"})
    with pytest.raises(ValueError):
        verify_shard(shard, descriptor=descriptor(shard))


def test_plan_uses_union_observations_and_excludes_reserved_final():
    evidence = inputs(minute=True)
    evidence["automatic_recipe"] = {
        "periods": {
            "search": {"from": "2026-01-05", "to": "2026-01-05"},
            "check1": {"from": "2026-01-07", "to": "2026-01-07"},
            "final": {"from": "2026-01-09", "to": "2026-01-09"},
        }
    }
    before = copy.deepcopy(evidence)
    admitted = plan(evidence, 100)
    assert admitted["status"] == "available"
    assert admitted["per_period_counts"] == {"search": 2, "check1": 1}
    assert admitted["estimated_total_marks"] == 200
    assert admitted["reserved_final_excluded"] is True
    assert admitted["estimated_total_bytes"] <= MAX_TOTAL_BYTES
    assert evidence == before


@pytest.mark.parametrize("count", [0, -1, True, MAX_EVALUATIONS + 1, 1.5])
def test_invalid_evaluation_budget_is_optional_unavailable(count):
    assert plan(inputs(), count)["reason"] == "evaluation_limit"


def test_large_minute_unions_are_preflighted_before_any_scoring():
    evidence = inputs(minute=True)
    start = datetime.fromisoformat("2026-01-05T09:15:00+05:30")
    evidence["snapshot"]["timeline"] = [
        (start + timedelta(minutes=i)).isoformat() for i in range(20_000)
    ]
    result = plan(evidence, MAX_EVALUATIONS)
    assert result["status"] == "unavailable"
    assert result["reason"] == "total_observation_limit"
    assert result["estimated_total_marks"] == 6_400_000


def test_missing_and_duplicate_timeline_cannot_claim_available_development_evidence():
    assert plan({}, 1)["reason"] == "unverified_timeline"
    evidence = inputs()
    evidence["snapshot"]["sessions"].append(evidence["snapshot"]["sessions"][-1])
    assert plan(evidence, 1)["reason"] == "unverified_timeline"


def test_offset_equivalent_minute_keys_cannot_be_double_counted():
    evidence = inputs(minute=True)
    evidence["snapshot"]["timeline"] = ["2026-01-05T09:15:00+05:30", "2026-01-05T03:45:00+00:00"]
    assert plan(evidence, 1)["reason"] == "unverified_timeline"
