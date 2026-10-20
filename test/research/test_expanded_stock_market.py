"""Actual finite momentum maths, causal gates and versioned fixed-rule replay."""

import gc
import tracemalloc
import weakref
from bisect import bisect_left
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime

import pytest
from test_stock_features import execution, record
from test_stock_market_context import bundle, refreeze, resign

from research import expanded_stock_market as expanded
from research import joint_stock_market as joint
from research import stock_features as finite
from research import stock_market_context as context_module
from research import stock_relative_strength as stock
from research.entry_conditions import apply as fixed_apply
from research.entry_conditions import enrich, normalize, stock_module
from research.portfolio import fingerprint
from research.stock_market_context import stock_view


def selected(identifier):
    return next(row for row in expanded.catalog() if row["id"] == identifier)


def stock_receipt(source, evidence, identifier):
    return expanded._apply_stock(
        evidence,
        stock_view(source),
        selected(identifier)["stock_hypothesis"],
        stock.validate_bundle,
    )[1]


def prices(source, changes=None):
    """Retain ordinary observed bars; change specified stock closes only."""
    source = deepcopy(source)
    item = source["series"]["AAA"]
    for day, bar in item["evidence"]["bars"].items():
        close = (changes or {}).get(day, 100.0)
        bar.update(open=close, high=close + 1, low=close - 1, close=close)
    refreeze(item)
    return resign(source)


@pytest.fixture(scope="module")
def saved_context():
    return bundle()


def test_exact_legacy_prefix_four_new_stock_choices_and_no_new_families():
    values = expanded.catalog()
    assert len(values) == len({row["id"] for row in values}) == 108
    assert len({row["family"] for row in values}) == 32
    assert values[:92] == joint.catalog()
    assert fingerprint(values[:92]) == (
        "bd3cd74734aac954372b379f88c4fb45b74f18de797824e2e389c5831ff87c28"
    )
    assert {row["id"] for row in values[92:]} == {
        f"momentum_{lookback}_gt{threshold}__{market['id']}"
        for lookback in (10, 60)
        for threshold in (0, 2)
        for market in joint.MARKETS
    }
    before = deepcopy(values)
    values[92]["stock_hypothesis"]["lookback"] = 99
    with pytest.raises(ValueError, match="supported expanded"):
        expanded.normalize_hypothesis(values[92])
    assert expanded.catalog() == before
    assert expanded.recipe()["stock_warmup_sessions"] == 100
    assert expanded.recipe()["market_warmup_sessions"] == 160


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("none", "70fcc7e25b214716c21210a29f45f92635315df963eb554cd0173d0f20a43d9f"),
        (
            "momentum_20_gt2__all",
            "47b5c5d3c6b0672c53b511df89937a69acc4cc392ee4d5354236e786ba587791",
        ),
        (
            "relative_20_gt0__trend_up",
            "f16049ab74b15d232924f73ecdac7bd631e56f6b7b6bc3f622f00720bfcb05b0",
        ),
    ],
)
def test_legacy_gate_and_recipe_hashes_are_unchanged(saved_context, identifier, expected):
    source, evidence = saved_context
    assert fingerprint(joint.recipe()) == (
        "4aa9269632ad862aba003d2955c006ca3759548664a7150870c6b9b7126cae60"
    )
    assert fingerprint(joint.apply(evidence, source, selected(identifier))) == expected
    with joint.PreparedJointContext(source) as prepared:
        assert fingerprint(prepared.apply(evidence, source, selected(identifier))) == expected


@pytest.mark.parametrize("lookback", [10, 60])
@pytest.mark.parametrize(
    "threshold,last,passes", [(0, 100, False), (2, 102, False), (2, 103, True)]
)
def test_actual_new_finite_momentum_and_strict_threshold_equality(
    saved_context, lookback, threshold, last, passes
):
    source, evidence = saved_context
    identifier = f"momentum_{lookback}_gt{threshold}__all"
    initial = stock_receipt(source, evidence, identifier)["membership"][0]
    days = source["series"]["AAA"]["calendar"]["sessions"]
    end = bisect_left(days, initial["decision_at"][:10])
    changed = prices(source, {days[end - 1]: last})
    receipt = stock_receipt(changed, evidence, identifier)
    member = receipt["membership"][0]
    feature = member["feature"]
    assert feature["observations"] == lookback + 1
    assert feature["from"] == days[end - lookback - 1]
    assert feature["to"] == days[end - 1]
    assert feature["value"] == pytest.approx(100 * (last / 100 - 1))
    assert member["state"] == ("allowed" if passes else "filtered")
    assert feature["to"] < member["decision_at"][:10]
    assert feature["available_at"] <= member["decision_at"]
    assert expanded.apply(evidence, changed, selected(identifier))[1]["counts"] == receipt["counts"]


def test_61_consecutive_sessions_are_required_and_missing_is_not_filled(saved_context):
    source, evidence = saved_context
    identifier = "momentum_60_gt0__all"
    initial = stock_receipt(source, evidence, identifier)["membership"][0]
    changed = prices(source)
    item = changed["series"]["AAA"]
    days = item["calendar"]["sessions"]
    end = bisect_left(days, initial["decision_at"][:10])
    missing = days[end - 61]
    item["evidence"]["bars"].pop(missing)
    refreeze(item)
    resign(changed)
    row = stock_receipt(changed, evidence, identifier)["membership"][0]
    assert row["state"] == "unknown" and missing in row["reason"]
    assert stock_receipt(changed, evidence, "momentum_10_gt0__all")["counts"]["unknown"] == 0
    short = record(count=61)
    feature, reason = finite._feature(
        short,
        datetime.fromisoformat(short["calendar"]["sessions"][-1] + "T09:15:00+05:30"),
        selected(identifier)["stock_hypothesis"],
    )
    assert feature is None and "61 earlier consecutive" in reason


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize("hypothesis", expanded.catalog()[92:], ids=lambda row: row["id"])
def test_new_public_and_private_gates_are_byte_identical(saved_context, minute, hypothesis):
    source, _ = saved_context
    evidence = execution(*source["series"].values(), minute=minute)
    original = deepcopy(evidence)
    expected = expanded.apply(evidence, source, hypothesis)
    with expanded.PreparedJointContext(source) as prepared:
        assert prepared.apply(evidence, source, hypothesis) == expected
    assert evidence == original


@pytest.mark.parametrize("minute", [False, True])
def test_same_and_future_session_closes_cannot_change_earlier_momentum(saved_context, minute):
    source, _ = saved_context
    evidence = execution(*source["series"].values(), minute=minute)
    hypothesis = selected("momentum_60_gt2__trend_up")
    before = expanded.apply(evidence, source, hypothesis)[1]["membership"]
    changed = deepcopy(source)
    cutoff = before[0]["decision_at"][:10]
    for item in [changed["market"], *changed["series"].values()]:
        for day, bar in item["evidence"]["bars"].items():
            if day >= cutoff:
                bar.update(open=900, high=902, low=899, close=901)
        refreeze(item)
    resign(changed)
    assert expanded.apply(evidence, changed, hypothesis)[1]["membership"] == before
    with expanded.PreparedJointContext(changed) as prepared:
        assert prepared.apply(evidence, changed, hypothesis)[1]["membership"] == before


def test_fixed_expanded_rule_replays_with_new_marker_and_shared_context(saved_context):
    source, evidence = deepcopy(saved_context)
    hypothesis = selected("momentum_60_gt0__all")
    descriptor = {"version": expanded.ENTRY_VERSION, "hypothesis": hypothesis}
    assert stock_module(expanded.ENTRY_VERSION) is expanded
    evidence["portfolio"]["entry_conditions"] = normalize(descriptor, evidence["strategies"])
    original = deepcopy(evidence)
    result = fixed_apply(evidence, source)
    assert result["automatic_stock_market_context"] == source
    assert "automatic_stock_features" not in result
    assert result["entry_condition_gates"][0]["calculation_version"] == expanded.FEATURE_VERSION
    report = {"strategies": result["strategies"]}
    enrich(report, result)
    assert report["entry_conditions"] == descriptor
    assert report["automatic_stock_condition"]["bundle_id"] == source["id"]
    assert report["strategies"][0]["entry_condition"] == descriptor
    assert evidence == original


def cached_gate(evidence, source, hypothesis, cache, prepared):
    from research.automatic_hypotheses import _gate

    return _gate(
        evidence,
        source,
        hypothesis,
        cache,
        "search",
        apply_fn=prepared.apply,
        markers_fn=expanded.markers,
        calculation_version=expanded.FEATURE_VERSION,
        trailing_activation=True,
    )


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize(
    "identifier",
    ["ema_above_10__all", "relative_20_gt0__trend_up", "momentum_60_gt2__volatility_normal"],
)
def test_exit_only_cache_warm_eviction_and_reconstruction_keep_exact_gate_identity(
    saved_context, monkeypatch, minute, identifier
):
    from research import automatic_hypotheses

    monkeypatch.setattr(automatic_hypotheses, "MAX_GATE_CACHE", 1)
    source, _ = saved_context
    first = execution(*source["series"].values(), minute=minute)
    changed = deepcopy(first)
    cfg = changed["strategies"][0]["config"]
    cfg.update(target_pct=7, stop_pct=3, cost_bps=23, trailing_enabled=True, trailing_pct=0.75)
    cfg["hold_minutes" if minute else "hold_sessions"] = 9
    hypothesis = selected(identifier)
    expected = expanded.apply(changed, source, hypothesis)
    with expanded.PreparedJointContext(source) as prepared:
        cache = OrderedDict()
        original = cached_gate(first, source, hypothesis, cache, prepared)
        warm = cached_gate(changed, source, hypothesis, cache, prepared)
        assert warm == expected
        assert original[1]["id"] != warm[1]["id"]
        for key in ("causal_inputs_id", "stock_gate_id", "market_gate_ids"):
            assert original[1][key] == warm[1][key]
        assert original[1]["execution_inputs_id"] != warm[1]["execution_inputs_id"]
        assert original[1]["conditioned_strategies_id"] != warm[1]["conditioned_strategies_id"]
        cached_gate(changed, source, selected("momentum_5_gt0__all"), cache, prepared)
        assert len(cache) == 1
        assert cached_gate(changed, source, hypothesis, cache, prepared) == expected
    with expanded.PreparedJointContext(source) as reconstructed:
        assert cached_gate(changed, source, hypothesis, OrderedDict(), reconstructed) == expected
    assert expanded.recipe()["gate_identity"]["version"] == expanded.CAUSAL_RECEIPT_VERSION


def test_canonical_components_keep_schedule_cohort_and_observed_features_bound(saved_context):
    source, _ = saved_context
    evidence = execution(*source["series"].values(), minute=True)
    hypothesis = selected("momentum_60_gt0__trend_up")
    baseline = expanded.apply(evidence, source, hypothesis)
    with expanded.PreparedJointContext(source) as prepared:
        cache = OrderedDict()
        assert cached_gate(evidence, source, hypothesis, cache, prepared) == baseline
        later = deepcopy(evidence)
        later["strategies"][0]["config"]["entry_time"] = "09:30"
        shifted = cached_gate(later, source, hypothesis, cache, prepared)
        assert shifted == expanded.apply(later, source, hypothesis)
        assert shifted[1]["causal_inputs_id"] != baseline[1]["causal_inputs_id"]
        assert shifted[1]["stock_gate_id"] != baseline[1]["stock_gate_id"]
        assert (
            shifted[1]["membership"][0]["scheduled_at"]
            != baseline[1]["membership"][0]["scheduled_at"]
        )
        excluded = deepcopy(evidence)
        excluded["strategies"][0]["signals"][0]["research_exclusion"] = "Recorded price gap"
        omitted = cached_gate(excluded, source, hypothesis, cache, prepared)
        assert omitted == expanded.apply(excluded, source, hypothesis)
        assert omitted[1]["counts"]["original_excluded"] == 1
        assert omitted[1]["causal_inputs_id"] != baseline[1]["causal_inputs_id"]
    changed = deepcopy(source)
    record = changed["series"]["AAA"]
    day = max(
        day
        for day in record["evidence"]["bars"]
        if day < baseline[1]["membership"][0]["decision_at"][:10]
    )
    record["evidence"]["bars"][day]["close"] += 0.1
    refreeze(record)
    resign(changed)
    observed = expanded.apply(evidence, changed, hypothesis)
    assert observed[1]["causal_inputs_id"] != baseline[1]["causal_inputs_id"]
    assert observed[1]["stock_gate_id"] != baseline[1]["stock_gate_id"]
    # Account-only input changes remain bound by the complete outer receipt,
    # even when they do not alter a causal condition or entry schedule.
    capital = deepcopy(evidence)
    capital["portfolio"]["capital"] += 100
    fresh = expanded.apply(capital, source, hypothesis)
    assert fresh[1]["causal_inputs_id"] == baseline[1]["causal_inputs_id"]
    assert fresh[1]["id"] != baseline[1]["id"]


def test_prepared_owner_validates_once_detaches_and_releases_all_paths(
    saved_context, monkeypatch, record_property
):
    import psutil

    source, evidence = deepcopy(saved_context)
    hypothesis = selected("momentum_60_gt0__all")
    expected = expanded.apply(evidence, source, hypothesis)
    count = {"context": 0, "stock": 0}
    native_context, native_stock = context_module.validate_bundle, stock.validate_bundle

    def validate_context(value):
        count["context"] += 1
        return native_context(value)

    def validate_stock(value):
        count["stock"] += 1
        return native_stock(value)

    monkeypatch.setattr(context_module, "validate_bundle", validate_context)
    monkeypatch.setattr(stock, "validate_bundle", validate_stock)
    prepared = expanded.PreparedJointContext(source)
    assert count == {"context": 1, "stock": 1}
    source["series"]["AAA"]["evidence"]["bars"].clear()
    with pytest.raises(ValueError, match="original source object"):
        prepared.apply(evidence, deepcopy(source), hypothesis)
    # Warm native imports before measuring the repeated allocation lifetime.
    assert prepared.apply(evidence, source, hypothesis) == expected
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    gc.collect()
    handles_before = handles()
    rss_before = process.memory_info().rss
    tracemalloc.start()
    try:
        baseline, _ = tracemalloc.get_traced_memory()
        for _ in range(100):
            assert prepared.apply(evidence, source, hypothesis) == expected
        gc.collect()
        retained, peak = tracemalloc.get_traced_memory()
        handles_after = handles()
        rss_after = process.memory_info().rss
        record_property("handles_before", handles_before)
        record_property("handles_after", handles_after)
        record_property("retained_allocation_bytes", retained - baseline)
        record_property("peak_allocation_bytes", peak - baseline)
        record_property("rss_change_bytes", rss_after - rss_before)
        assert handles_after <= handles_before + 4
        assert retained - baseline < 128 * 1024
        assert peak - baseline < 2 * 1024**2
        assert rss_after - rss_before < 8 * 1024**2
    finally:
        tracemalloc.stop()
        prepared.close()
    assert count == {"context": 1, "stock": 1}
    prepared.close()
    with pytest.raises(ValueError, match="released"):
        prepared.apply(evidence, source, hypothesis)
    reference = weakref.ref(prepared)
    del prepared
    gc.collect()
    assert reference() is None
    source, evidence = saved_context
    with pytest.raises(RuntimeError, match="cancelled"):
        with expanded.PreparedJointContext(source) as interrupted:
            raise RuntimeError("cancelled")
    with pytest.raises(ValueError, match="released"):
        interrupted.apply(evidence, source, hypothesis)
