"""Content-complete, bounded reuse of pure validation within one worker job."""

import gc
import weakref
from contextvars import copy_context
from copy import deepcopy

import pytest
from test_stock_market_context import bundle, refreeze, resign

from research import canonical_json
from research import stock_market_context as context


@pytest.fixture(scope="module")
def retained():
    return bundle()[0]


def count_records(monkeypatch):
    calls = []
    original = context.stock.validate_record

    def record(symbol, value):
        calls.append(symbol)
        return original(symbol, value)

    monkeypatch.setattr(context.stock, "validate_record", record)
    return calls


def test_public_calls_stay_fully_validating_and_scope_compares_content(retained, monkeypatch):
    calls = count_records(monkeypatch)
    assert context.validate_bundle(retained) is retained
    context.validate_bundle(retained)
    assert calls == ["AAA", "AAA"]
    with context._bundle_validation_scope():
        context.validate_bundle(retained)
        copied = deepcopy(retained)
        assert context.validate_bundle(copied) is copied
        assert calls == ["AAA"] * 3
    context.validate_bundle(retained)
    assert calls == ["AAA"] * 4


@pytest.mark.parametrize("change", ["price", "receipt", "calendar", "identity"])
def test_changed_content_cannot_use_previous_admission(retained, change):
    incoming = deepcopy(retained)
    with context._bundle_validation_scope():
        context.validate_bundle(incoming)
        record = incoming["series"]["AAA"]
        if change == "price":
            next(iter(record["evidence"]["bars"].values()))["close"] += 0.125
        elif change == "receipt":
            record["acquisition_receipts"].append({"sha256": "wrong", "receipt": {}})
        elif change == "calendar":
            record["calendar"]["session_hours"].clear()
        else:
            incoming["id"] = "0" * 64
        with pytest.raises(ValueError):
            context.validate_bundle(incoming)


def test_different_valid_content_is_readmitted_and_only_one_signature_is_retained(
    retained, monkeypatch
):
    changed = deepcopy(retained)
    bar = next(iter(changed["series"]["AAA"]["evidence"]["bars"].values()))
    bar["close"] += 0.01
    refreeze(changed["series"]["AAA"])
    resign(changed)
    calls = count_records(monkeypatch)
    with context._bundle_validation_scope() as owner:
        context.validate_bundle(retained)
        original_bytes = owner._encoded
        context.validate_bundle(changed)
        assert owner._encoded != original_bytes
        context.validate_bundle(changed)
        assert calls == ["AAA", "AAA"]
        context.validate_bundle(retained)
        assert calls == ["AAA"] * 3


def test_tuple_with_identical_json_cannot_impersonate_valid_list(retained):
    incoming = deepcopy(retained)
    assert incoming["series"]["AAA"]["acquisition_receipts"] == []
    with context._bundle_validation_scope():
        context.validate_bundle(incoming)
        incoming["series"]["AAA"]["acquisition_receipts"] = ()
        with pytest.raises(ValueError, match="acquisition receipts"):
            context.validate_bundle(incoming)


def test_changed_classifier_policy_is_checked_even_on_equal_content(retained, monkeypatch):
    with context._bundle_validation_scope():
        context.validate_bundle(retained)
        monkeypatch.setattr(context, "_market_context", lambda: {"changed": True})
        with pytest.raises(ValueError, match="classifier recipe changed"):
            context.validate_bundle(retained)


def test_bounds_and_custom_values_fall_back_to_full_validation(retained, monkeypatch):
    calls = count_records(monkeypatch)
    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_ASCII_BYTES", 64)
    with context._bundle_validation_scope() as owner:
        context.validate_bundle(retained)
        context.validate_bundle(retained)
        assert owner._encoded is None
    assert calls == ["AAA", "AAA"]


def test_mutation_during_first_admission_is_not_remembered(retained, monkeypatch):
    incoming = deepcopy(retained)
    original = context.bounded_canonical_bytes
    first = True

    def encode(value, **kwargs):
        nonlocal first
        raw = original(value, **kwargs)
        if first:
            first = False
            bar = next(iter(incoming["series"]["AAA"]["evidence"]["bars"].values()))
            bar["close"] += 0.01
            refreeze(incoming["series"]["AAA"])
            resign(incoming)
        return raw

    monkeypatch.setattr(context, "bounded_canonical_bytes", encode)
    calls = count_records(monkeypatch)
    with context._bundle_validation_scope() as owner:
        context.validate_bundle(incoming)
        assert owner._encoded is None
        context.validate_bundle(incoming)
        assert owner._encoded is not None
    assert calls == ["AAA", "AAA"]


def test_nested_error_and_copied_context_cannot_retain_an_active_approval(retained, monkeypatch):
    calls = count_records(monkeypatch)
    with context._bundle_validation_scope() as outer:
        context.validate_bundle(retained)
        with pytest.raises(RuntimeError, match="stop"):
            with context._bundle_validation_scope() as inner:
                context.validate_bundle(retained)
                carried = copy_context()
                raise RuntimeError("stop")
        assert inner._encoded is None
        assert context._bundle_validation.get() is outer
        context.validate_bundle(retained)
        assert calls == ["AAA", "AAA"]
    assert outer._encoded is None
    assert context._bundle_validation.get() is None
    carried.run(context.validate_bundle, retained)
    assert calls == ["AAA"] * 3
    assert inner._encoded is None


def test_job_decorator_releases_on_error_and_after_repeated_jobs(retained):
    owners = []

    @context._with_bundle_validation
    def job(*, fail):
        owner = context._bundle_validation.get()
        owners.append(weakref.ref(owner))
        context.validate_bundle(retained)
        assert owner._encoded is not None
        if fail:
            raise RuntimeError("job failed")

    for index in range(100):
        if index % 2:
            with pytest.raises(RuntimeError, match="job failed"):
                job(fail=True)
        else:
            job(fail=False)
        assert context._bundle_validation.get() is None
    gc.collect()
    assert all(ref() is None for ref in owners)
