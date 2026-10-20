"""Private v8 projections preserve native gate bytes without repeated history walks."""

import gc
import json
import time
import tracemalloc
import weakref
from copy import deepcopy

import pytest
from test_stock_features import execution
from test_stock_market_context import bundle, refreeze, resign

from research import joint_stock_market as joint
from research import stock_market_context as context_module
from research import stock_relative_strength as relative


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def selected(identifier):
    return next(row for row in joint.catalog() if row["id"] == identifier)


@pytest.fixture(scope="module")
def saved_context():
    return bundle()


@pytest.mark.parametrize("hypothesis", joint.catalog(), ids=lambda row: row["id"])
def test_every_registered_gate_preserves_rows_and_whole_receipt_bytes(saved_context, hypothesis):
    source, evidence = saved_context
    expected = canonical(joint.apply(evidence, source, hypothesis))
    with joint.PreparedJointContext(source) as prepared:
        assert canonical(prepared.apply(evidence, source, hypothesis)) == expected


@pytest.mark.parametrize("minute", [False, True])
@pytest.mark.parametrize("missing", [(), (100,), (178,)])
def test_native_schedule_gaps_and_conjunction_unknown_policy_are_unchanged(minute, missing):
    source, _ = bundle(missing=missing)
    evidence = execution(*source["series"].values(), minute=minute)
    hypothesis = selected("ema20_and_relative20_gt0__trend_up")
    with joint.PreparedJointContext(source) as prepared:
        before = deepcopy(evidence)
        assert canonical(prepared.apply(evidence, source, hypothesis)) == canonical(
            joint.apply(evidence, source, hypothesis)
        )
        assert evidence == before


def test_preparation_detaches_caller_prices_calendars_and_receipts(saved_context):
    source, evidence = deepcopy(saved_context)
    hypothesis = selected("relative_20_gt0__trend_up")
    expected = canonical(joint.apply(evidence, source, hypothesis))
    with joint.PreparedJointContext(source) as prepared:
        record = source["series"]["AAA"]
        record["evidence"]["bars"].clear()
        record["calendar"]["session_hours"].clear()
        record["acquisition_receipts"].append({"corrupt": True})
        source["market"]["evidence"]["bars"].clear()
        assert canonical(prepared.apply(evidence, source, hypothesis)) == expected
        with pytest.raises(ValueError):
            joint.PreparedJointContext(source)


def test_outputs_are_fresh_and_other_context_cannot_impersonate_source(saved_context):
    source, evidence = saved_context
    hypothesis = selected("ema20_and_relative20_gt0__trend_up")
    with joint.PreparedJointContext(source) as prepared:
        rows, receipt = prepared.apply(evidence, source, hypothesis)
        expected = canonical((rows, receipt))
        rows[0]["signals"].clear()
        receipt["membership"].clear()
        receipt["feature_recipe"]["stock"].clear()
        assert canonical(prepared.apply(evidence, source, hypothesis)) == expected
        with pytest.raises(ValueError, match="original source object"):
            prepared.apply(evidence, deepcopy(source), hypothesis)
        unsupported = deepcopy(hypothesis)
        unsupported["market"] = {"dimension": "trend", "regime": "crisis"}
        with pytest.raises(ValueError, match="supported joint"):
            prepared.apply(evidence, source, unsupported)


def test_full_history_validation_occurs_at_preparation_not_each_gate(saved_context, monkeypatch):
    source, evidence = saved_context
    expected = canonical(joint.apply(evidence, source, selected("relative_20_gt0__trend_up")))
    calls = {"context": 0, "stock": 0}
    original_context = context_module.validate_bundle
    original_stock = relative.validate_bundle

    def validate_context(value):
        calls["context"] += 1
        return original_context(value)

    def validate_stock(value):
        calls["stock"] += 1
        return original_stock(value)

    monkeypatch.setattr(context_module, "validate_bundle", validate_context)
    monkeypatch.setattr(relative, "validate_bundle", validate_stock)
    with joint.PreparedJointContext(source) as prepared:
        assert calls == {"context": 1, "stock": 1}
        for hypothesis in joint.catalog()[:12]:
            prepared.apply(evidence, source, hypothesis)
        assert (
            canonical(prepared.apply(evidence, source, selected("relative_20_gt0__trend_up")))
            == expected
        )
        assert calls == {"context": 1, "stock": 1}
        # The unchanged public gate still treats all incoming dictionaries as untrusted.
        joint.apply(evidence, source, selected("none"))
        assert calls["context"] > 1 and calls["stock"] > 1


@pytest.mark.parametrize("minute", [False, True])
def test_actual_timing_and_original_exclusions_are_not_cached(saved_context, minute):
    source, _ = saved_context
    evidence = execution(*source["series"].values(), minute=minute)
    hypothesis = selected("momentum_5_gt0__volatility_normal")
    with joint.PreparedJointContext(source) as prepared:
        first = prepared.apply(evidence, source, hypothesis)[1]
        changed = deepcopy(evidence)
        changed["strategies"][0]["config"]["entry_time"] = "09:30"
        changed["strategies"][0]["signals"][0]["research_exclusion"] = "Saved original exclusion"
        actual = prepared.apply(changed, source, hypothesis)
        assert canonical(actual) == canonical(joint.apply(changed, source, hypothesis))
        assert actual[1]["execution_inputs_id"] != first["execution_inputs_id"]
        assert actual[1]["membership"][0]["state"] == "original_excluded"


@pytest.mark.parametrize("minute", [False, True])
def test_future_closes_cannot_change_earlier_prepared_membership(saved_context, minute):
    source, _ = deepcopy(saved_context)
    evidence = execution(*source["series"].values(), minute=minute)
    hypothesis = selected("relative_20_gt0__trend_up")
    with joint.PreparedJointContext(source) as prepared:
        before = prepared.apply(evidence, source, hypothesis)[1]["membership"]
    cutoff = before[0]["decision_at"][:10]
    changed = deepcopy(source)
    for record in [changed["market"], *changed["series"].values()]:
        for day, bar in record["evidence"]["bars"].items():
            if day >= cutoff:
                bar.update(open=900, high=902, low=899, close=901)
        refreeze(record)
    resign(changed)
    with joint.PreparedJointContext(changed) as prepared:
        assert prepared.apply(evidence, changed, hypothesis)[1]["membership"] == before


@pytest.mark.parametrize("problem", ["integrity", "symbols", "bars", "bytes"])
def test_unsupported_and_oversized_contexts_are_rejected_before_use(
    saved_context, monkeypatch, problem
):
    source, _ = deepcopy(saved_context)
    if problem == "integrity":
        source["id"] = "0" * 64
    elif problem == "symbols":
        monkeypatch.setattr(context_module.stock, "MAX_SYMBOLS", 0)
    elif problem == "bars":
        monkeypatch.setattr(context_module.stock, "MAX_FEATURE_BARS", 10)
    else:
        monkeypatch.setattr(context_module.stock, "MAX_BUNDLE_BYTES", 512)
        # Size rejection precedes a potentially large detached allocation.
        monkeypatch.setattr(
            context_module, "deepcopy", lambda _value: pytest.fail("copied oversized context")
        )
    with pytest.raises(ValueError):
        joint.PreparedJointContext(source)


def test_100_reuses_release_memory_and_exception_paths(saved_context, record_property):
    source, evidence = saved_context
    hypothesis = selected("none")
    prepared = joint.PreparedJointContext(source)
    expected = canonical(prepared.apply(evidence, source, hypothesis))
    import psutil

    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    gc.collect()
    handles_before = handles()
    rss_before = process.memory_info().rss
    tracemalloc.start()
    try:
        baseline, _ = tracemalloc.get_traced_memory()
        for _ in range(100):
            assert canonical(prepared.apply(evidence, source, hypothesis)) == expected
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
    with pytest.raises(ValueError, match="released"):
        prepared.apply(evidence, source, hypothesis)
    reference = weakref.ref(prepared)
    del prepared
    gc.collect()
    assert reference() is None
    with pytest.raises(RuntimeError, match="cancelled"):
        with joint.PreparedJointContext(source) as interrupted:
            raise RuntimeError("cancelled")
    with pytest.raises(ValueError, match="released"):
        interrupted.apply(evidence, source, hypothesis)


def test_five_real_gate_benchmark_reports_actual_matching_work(saved_context, record_property):
    source, evidence = saved_context
    hypothesis = selected("relative_20_gt0__trend_up")
    expected = canonical(joint.apply(evidence, source, hypothesis))
    started = time.perf_counter()
    for _ in range(5):
        assert canonical(joint.apply(evidence, source, hypothesis)) == expected
    legacy_seconds = time.perf_counter() - started
    started = time.perf_counter()
    with joint.PreparedJointContext(source) as prepared:
        preparation_seconds = time.perf_counter() - started
        started = time.perf_counter()
        for _ in range(5):
            assert canonical(prepared.apply(evidence, source, hypothesis)) == expected
        prepared_seconds = time.perf_counter() - started
    print(
        {
            "legacy_5_gates_seconds": legacy_seconds,
            "preparation_seconds": preparation_seconds,
            "prepared_5_gates_seconds": prepared_seconds,
        }
    )
    record_property("legacy_5_gates_seconds", legacy_seconds)
    record_property("preparation_seconds", preparation_seconds)
    record_property("prepared_5_gates_seconds", prepared_seconds)
    assert legacy_seconds > 0 and prepared_seconds > 0 and preparation_seconds > 0
