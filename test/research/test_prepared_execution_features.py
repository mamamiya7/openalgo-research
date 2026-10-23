"""Owned ATR receipts reuse only full-receipt admission, never candidate checks."""

import gc
import weakref
from copy import deepcopy

import pytest
from test_execution_features import prepared

from research import execution_features as features
from research.portfolio import fingerprint


@pytest.fixture(scope="module", params=[(False, False), (True, False), (False, True)])
def evidence(request):
    minute, no_hours = request.param
    return prepared(minute=minute, no_hours=no_hours)[0]


def test_owned_and_public_attachments_are_exact_with_one_whole_receipt_validation(evidence, monkeypatch):
    expected = features.attach(evidence)
    original = features._intact
    intact_calls, entry_calls = [], []
    entry_check = features.validate_entry_feature
    def intact(receipt):
        intact_calls.append(receipt)
        assert receipt is not evidence["execution_features"]
        return original(receipt)
    def entry(*args, **kwargs):
        entry_calls.append(kwargs)
        return entry_check(*args, **kwargs)
    monkeypatch.setattr(features, "_intact", intact)
    with features._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
        assert len(intact_calls) == 1
        monkeypatch.setattr(features, "validate_entry_feature", entry)
        for _ in range(3):
            attached = owner.attach(evidence)
            assert attached == expected
            assert attached["execution_features"] is evidence["execution_features"]
        assert len(intact_calls) == 1
        assert len(entry_calls) == 3


def test_source_and_returned_feature_mutations_cannot_change_private_receipt(evidence):
    current = deepcopy(evidence)
    receipt = current["execution_features"]
    expected = features.attach(current)["strategies"]
    with features._PreparedExecutionFeatures(receipt) as owner:
        attached = owner.attach(current)
        next(iter(receipt["features"].values()))["feature"]["value"] += 123
        attached["strategies"][0]["signals"][0]["execution_atr"]["value"] += 456
        assert owner.attach(current)["strategies"] == expected
        with pytest.raises(ValueError, match="original source object"):
            owner.attach({**current, "execution_features": deepcopy(receipt)})
        with pytest.raises(ValueError):
            features.attach(current)
    with pytest.raises(ValueError, match="released"):
        owner.attach(current)


@pytest.mark.parametrize("problem", ["interval", "context", "calendar", "opportunity"])
def test_candidate_specific_source_and_schedule_checks_remain_fresh(evidence, problem):
    current = deepcopy(evidence)
    with features._PreparedExecutionFeatures(current["execution_features"]) as owner:
        if problem == "interval":
            snapshot = current["snapshot"]
            snapshot["provenance"]["interval"] = "D" if snapshot["provenance"]["interval"] == "1m" else "1m"
        elif problem == "context":
            current["automatic_stock_market_context"]["id"] = "0" * 64
        elif problem == "calendar":
            snapshot = current["snapshot"]
            day = snapshot["sessions"][0]
            snapshot.setdefault("session_hours", {})[day] = {"open": "09:16", "close": "15:30"}
        else:
            current["strategies"][0]["signals"][0]["row"] += 1
        with pytest.raises(ValueError):
            owner.attach(current)
        with pytest.raises(ValueError):
            features.attach(current)


def test_each_actual_entry_still_runs_causal_feature_validation(evidence, monkeypatch):
    with features._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
        def reject(*args, **kwargs):
            raise ValueError("fresh causal entry check")
        monkeypatch.setattr(features, "validate_entry_feature", reject)
        with pytest.raises(ValueError, match="fresh causal entry check"):
            owner.attach(evidence)


@pytest.mark.parametrize("problem", ["identity", "version", "mask", "counts", "nonfinite", "bound"])
def test_untrusted_receipts_are_checked_before_ownership(evidence, problem, monkeypatch):
    receipt = deepcopy(evidence["execution_features"])
    if problem == "identity":
        receipt["id"] = "0" * 64
    elif problem == "version":
        receipt["version"] = "unsupported"
    elif problem == "mask":
        next(iter(next(iter(receipt["opportunities"].values())).values()))["common_exclusion"] = "fake mask"
    elif problem == "counts":
        receipt["counts"]["opportunities"] += 1
    elif problem == "nonfinite":
        receipt["unexpected"] = float("nan")
    else:
        monkeypatch.setattr(features, "MAX_BYTES", 10)
    with pytest.raises((ValueError, TypeError)):
        features._PreparedExecutionFeatures(receipt)


def test_stream_fallback_is_exact_and_always_closes_capture(evidence, monkeypatch):
    expected = features.attach(evidence)
    captures = []
    original = features.StringIO
    def stream():
        value = original()
        captures.append(value)
        return value
    monkeypatch.setattr(features, "bounded_canonical_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(features, "StringIO", stream)
    with features._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
        assert owner.attach(evidence) == expected
    monkeypatch.setattr(features, "MAX_BYTES", 10)
    with pytest.raises(ValueError, match="bounded byte allowance"):
        features._PreparedExecutionFeatures(evidence["execution_features"])
    # The original-schema byte check now rejects oversized input before even
    # opening a capture; the admitted fallback's stream is still always closed.
    assert len(captures) == 1 and all(stream.closed for stream in captures)


def test_validation_uses_the_same_owned_capture_even_if_caller_changes(evidence, monkeypatch):
    current = deepcopy(evidence)
    expected = features.attach(current)["strategies"]
    receipt = current["execution_features"]
    original = features._intact
    def during_validation(owned):
        assert owned is not receipt
        next(iter(receipt["features"].values()))["feature"]["value"] += 999
        return original(owned)
    monkeypatch.setattr(features, "_intact", during_validation)
    with features._PreparedExecutionFeatures(receipt) as owner:
        assert owner.attach(current)["strategies"] == expected


@pytest.mark.parametrize("problem", ["tuple", "custom_list", "custom_mapping", "key"])
def test_fallback_does_not_normalize_invalid_schema_into_valid_owned_json(evidence, problem):
    class RejectedList(list):
        def __ne__(self, other):
            return True
    class RejectedMapping(dict):
        def __ne__(self, other):
            return True
    current = deepcopy(evidence)
    receipt = current["execution_features"]
    opportunity = next(iter(next(iter(receipt["opportunities"].values())).values()))
    if problem == "tuple":
        opportunity["required"] = tuple(opportunity["required"])
    elif problem == "custom_list":
        opportunity["required"] = RejectedList(opportunity["required"])
    elif problem == "custom_mapping":
        receipt["counts"] = RejectedMapping(receipt["counts"])
    else:
        name = next(iter(receipt["features"]))
        receipt["features"][7] = receipt["features"].pop(name)
    receipt["id"] = fingerprint({key: value for key, value in receipt.items() if key != "id"})
    with pytest.raises((ValueError, TypeError)):
        features.attach(current)
    with pytest.raises((ValueError, TypeError)):
        features._PreparedExecutionFeatures(receipt)


def test_nested_original_exclusion_never_exposes_the_private_receipt(evidence):
    current = deepcopy(evidence)
    receipt = current["execution_features"]
    opportunity = next(iter(next(iter(receipt["opportunities"].values())).values()))
    opportunity["original_exclusion"] = {"reasons": ["original"]}
    receipt["id"] = fingerprint({key: value for key, value in receipt.items() if key != "id"})
    expected = features.attach(current)["strategies"]
    with features._PreparedExecutionFeatures(receipt) as owner:
        attached = owner.attach(current)
        attached["strategies"][0]["signals"][0]["research_exclusion"]["reasons"].append("changed")
        opportunity["original_exclusion"]["reasons"].append("caller changed")
        assert owner.attach(current)["strategies"] == expected


def test_owned_projection_never_escapes_even_for_nested_untrusted_feature_values(evidence, monkeypatch):
    # No relaxed validation is used: inject a nested returned value only after
    # construction to exercise the defensive copy at the ownership boundary.
    original = features.validate_entry_feature
    nested = {"items": []}
    with features._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
        def decorated(value, **kwargs):
            original(value, **kwargs)
            return {**value, "test_nested": nested}
        monkeypatch.setattr(features, "validate_entry_feature", decorated)
        first = owner.attach(evidence)
        first["strategies"][0]["signals"][0]["execution_atr"]["test_nested"]["items"].append(1)
        assert nested == {"items": []}
        assert owner.attach(evidence)["strategies"][0]["signals"][0]["execution_atr"]["test_nested"] == {"items": []}


def test_repeated_owners_release_references_on_success_and_exception(evidence):
    references = []
    for index in range(100):
        try:
            with features._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
                references.append(weakref.ref(owner))
                if index % 2:
                    raise RuntimeError("cancelled")
        except RuntimeError:
            pass
        with pytest.raises(ValueError, match="released"):
            owner.attach(evidence)
        owner.close()
        del owner
    gc.collect()
    assert all(reference() is None for reference in references)
