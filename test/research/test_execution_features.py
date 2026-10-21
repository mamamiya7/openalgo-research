"""Causal native ATR preparation, shared unknown cohort and immutable recovery."""

import gc
import tracemalloc
from copy import deepcopy

import pytest
from test_stock_features import execution
from test_stock_market_context import bundle, refreeze, resign

from research import execution_features as features
from research.portfolio import fingerprint


def prepared(*, minute=False, no_hours=False):
    context, _ = bundle()
    evidence = execution(*context["series"].values(), minute=minute)
    if no_hours:
        evidence["snapshot"].pop("session_hours", None)
    evidence["automatic_stock_market_context"] = context
    return features.prepare(evidence, context), context


def seal(receipt):
    receipt["id"] = fingerprint({key: value for key, value in receipt.items() if key != "id"})


def test_preparation_preserves_frozen_source_and_actual_native_atr_math():
    from openalgo import ta

    context, evidence = bundle()
    before = deepcopy(evidence)
    result = features.prepare(evidence, context)
    assert evidence == before
    assert result["strategies"] is evidence["strategies"]
    receipt = result["execution_features"]
    attached = features.attach(result)
    feature = attached["strategies"][0]["signals"][0]["execution_atr"]
    series = context["series"]["AAA"]["evidence"]
    days = [day for day in series["required_dates"] if day < feature["entry_session"]][-100:]
    bars = [series["bars"][day] for day in days]
    expected = ta.atr(
        [bar["high"] for bar in bars],
        [bar["low"] for bar in bars],
        [bar["close"] for bar in bars],
        period=14,
    )[-1]
    assert feature["value"] == pytest.approx(float(expected))
    assert feature["observations"] == 100
    assert (feature["from"], feature["to"]) == (days[0], days[-1])
    assert receipt["counts"] == {
        "opportunities": 1,
        "common_excluded": 0,
        "derived": 1,
        "available": 1,
    }
    assert "bars" not in str(receipt.keys())
    assert features.prepare(result, context) == result
    assert evidence == before


@pytest.mark.parametrize("minute,no_hours", [(False, False), (False, True), (True, False)])
def test_daily_minute_and_legacy_daily_without_explicit_hours(minute, no_hours):
    result, _ = prepared(minute=minute, no_hours=no_hours)
    actual = features.attach(result)
    feature = actual["strategies"][0]["signals"][0]["execution_atr"]
    assert feature["value"] > 0 and feature["to"] < feature["entry_session"]
    assert features.attach(actual) == actual


def test_current_future_prices_and_outcomes_never_change_prior_atr():
    result, context = prepared()
    before = features.attach(result)["strategies"][0]["signals"][0]["execution_atr"]
    changed = deepcopy(context)
    for record in [changed["market"], *changed["series"].values()]:
        for day, bar in record["evidence"]["bars"].items():
            if day >= before["entry_session"]:
                bar.update(open=1000, high=1100, low=900, close=1050)
        refreeze(record)
    resign(changed)
    evidence = {key: value for key, value in result.items() if key != "execution_features"}
    evidence["automatic_stock_market_context"] = changed
    after = features.attach(features.prepare(evidence, changed))["strategies"][0]["signals"][0][
        "execution_atr"
    ]
    assert after == before


@pytest.mark.parametrize("problem", ["missing", "zero"])
def test_missing_or_zero_atr_is_a_common_exclusion_even_for_percentage_reference(problem):
    result, context = prepared()
    expected = features.attach(result)["strategies"][0]["signals"][0]["execution_atr"]
    changed = deepcopy(context)
    record = changed["series"]["AAA"]
    if problem == "missing":
        record["evidence"]["bars"].pop(expected["from"])
    else:
        for bar in record["evidence"]["bars"].values():
            bar.update(open=100, high=100, low=100, close=100)
    refreeze(record)
    resign(changed)
    evidence = {key: value for key, value in result.items() if key != "execution_features"}
    evidence["automatic_stock_market_context"] = changed
    prepared_evidence = features.prepare(evidence, changed)
    assert not evidence["strategies"][0]["signals"][0].get("research_exclusion")
    masked = features.attach(prepared_evidence)
    signal = masked["strategies"][0]["signals"][0]
    assert "Execution ATR unavailable" in signal["research_exclusion"]
    assert "execution_atr" not in signal
    assert prepared_evidence["execution_features"]["counts"]["common_excluded"] == 1


def test_all_admitted_clock_branches_share_atr_missingness(monkeypatch):
    context, _ = bundle()
    context = deepcopy(context)
    evidence = execution(*context["series"].values(), minute=True)
    day = evidence["strategies"][0]["signals"][0]["date"]
    evidence["snapshot"]["session_hours"][day]["close"] = "09:20"
    evidence["snapshot"]["timeline"] = [
        slot
        for slot in evidence["snapshot"]["timeline"]
        if slot[:10] != day or slot[11:16] < "09:20"
    ]
    for record in [context["market"], *context["series"].values()]:
        record["calendar"]["session_hours"][day]["close"] = "09:20"
        if record is context["series"]["AAA"]:
            record["evidence"]["bars"].pop(day)
        refreeze(record)
    resign(context)
    from research.automatic_timing import descriptor

    timing = descriptor(evidence)
    timing["strategies"][0]["branches"].append(
        {
            "id": "later",
            "label": "Controlled admitted late clock",
            "changes": {"entry_time": "09:30"},
        }
    )
    monkeypatch.setattr("research.automatic_timing.descriptor", lambda *_a, **_k: deepcopy(timing))
    result = features.prepare(evidence, context)
    receipt = result["execution_features"]
    assert receipt["counts"]["derived"] == 2
    assert receipt["counts"]["available"] == 1
    assert receipt["counts"]["common_excluded"] == 1
    assert (
        "Execution ATR unavailable"
        in features.attach(result)["strategies"][0]["signals"][0]["research_exclusion"]
    )


def test_duplicate_source_rows_and_sliced_periods_preserve_stable_opportunities():
    result, context = prepared()
    source = {key: value for key, value in result.items() if key != "execution_features"}
    source["strategies"] = deepcopy(source["strategies"])
    source["strategies"][0]["signals"] *= 2
    result = features.prepare(source, context)
    assert result["execution_features"]["counts"]["derived"] == 1
    assert len(features.attach(result)["strategies"][0]["signals"]) == 2
    subset = {**result, "snapshot": deepcopy(result["snapshot"])}
    subset["snapshot"]["sessions"] = subset["snapshot"]["sessions"][10:]
    subset["snapshot"]["session_hours"] = {
        day: value
        for day, value in subset["snapshot"]["session_hours"].items()
        if day in subset["snapshot"]["sessions"]
    }
    assert (
        features.attach(subset)["strategies"][0]["signals"][0]["execution_atr"]
        == features.attach(result)["strategies"][0]["signals"][0]["execution_atr"]
    )


def test_attach_preserves_original_and_gated_exclusion_reasons():
    result, _ = prepared()
    changed = {**result, "strategies": deepcopy(result["strategies"])}
    changed["strategies"][0]["signals"][0]["research_exclusion"] = "Chosen entry condition not met"
    assert (
        features.attach(changed)["strategies"][0]["signals"][0]["research_exclusion"]
        == "Chosen entry condition not met"
    )


@pytest.mark.parametrize("change", ["hash", "feature", "mask", "source", "calendar", "opportunity"])
def test_edited_receipts_sources_and_rehashed_features_are_rejected(change):
    result, context = prepared()
    changed = deepcopy(result)
    receipt = changed["execution_features"]
    feature_key = next(iter(receipt["features"]))
    if change == "hash":
        receipt["id"] = "0" * 64
    elif change == "feature":
        receipt["features"][feature_key]["feature"]["value"] += 1
        seal(receipt)
    elif change == "mask":
        next(iter(receipt["opportunities"]["scanner"].values()))["common_exclusion"] = "fake"
        seal(receipt)
    elif change == "source":
        changed["automatic_stock_market_context"]["id"] = "0" * 64
    elif change == "calendar":
        day = changed["snapshot"]["sessions"][0]
        changed["snapshot"]["session_hours"][day]["open"] = "09:16"
    else:
        changed["strategies"][0]["signals"][0]["row"] += 1
    with pytest.raises(ValueError):
        features.attach(changed)
    if change == "feature":
        # Even edited feature+envelope self-hashes cannot reproduce the source.
        inner = receipt["features"][feature_key]["feature"]
        seal(inner)
        seal(receipt)
        with pytest.raises(ValueError, match="reproduce"):
            features.prepare(changed, context)


def test_100_attachments_do_not_recompute_indicators_or_retain_price_buffers(
    monkeypatch, record_property
):
    import psutil

    result, _ = prepared()
    expected = features.attach(result)
    monkeypatch.setattr(
        features.indicators, "_feature", lambda *_a, **_k: pytest.fail("trial recomputed ATR")
    )
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    gc.collect()
    before = handles()
    tracemalloc.start()
    try:
        baseline, _ = tracemalloc.get_traced_memory()
        for _ in range(100):
            assert features.attach(result) == expected
        gc.collect()
        retained, peak = tracemalloc.get_traced_memory()
        after = handles()
        record_property("handles_before", before)
        record_property("handles_after", after)
        record_property("retained_allocation_bytes", retained - baseline)
        record_property("peak_allocation_bytes", peak - baseline)
        assert after <= before + 4
        assert retained - baseline < 128 * 1024
        assert peak - baseline < 2 * 1024**2
    finally:
        tracemalloc.stop()
