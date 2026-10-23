"""Fused admission keeps exact receipts, validation and unusual-input fallback."""

import gc
import hashlib
import json
from copy import deepcopy
from io import StringIO

import pytest
import test_portfolio_constraint_evidence as constraint_cases
from test_capacity_allocation_native import capacity_policy, rows, sealed_features
from test_vectorbt_portfolio import DAYS

from research import canonical_json
from research import portfolio_constraint_evidence as constraints
from research import portfolio_execution_features as features
from research.portfolio import fingerprint


class CustomDict(dict):
    pass


@pytest.fixture(scope="module")
def completed():
    return constraint_cases.completed.__wrapped__()


def outcome(call):
    try:
        return ("return", call())
    except Exception as error:
        return (type(error), str(error))


def resign(value):
    value["id"] = fingerprint({key: item for key, item in value.items() if key != "id"})


@pytest.fixture(params=["capacity", "correlation"])
def entry(request):
    strategy = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))[0]
    kind = request.param
    value = strategy["signals"][0]["entry_" + kind]
    check = getattr(features, "validate_" + kind + "_entry")
    arguments = {
        "symbol": "AAA",
        "entry_session": DAYS[1],
        "decision_at": DAYS[1] + "T09:15:00+05:30",
    }
    if kind == "correlation":
        arguments["required_symbols"] = ["AAA"]
    return value, check, arguments


@pytest.mark.parametrize(
    "problem",
    [
        "none",
        "seal",
        "source",
        "future",
        "shape",
        "tuple",
        "custom",
        "cycle",
        "nonfinite",
        "oversize",
        "depth",
        "native_number",
    ],
)
def test_entry_acceptance_and_errors_match_original_path(entry, problem, monkeypatch):
    value, check, arguments = deepcopy(entry)
    if problem == "seal":
        value["id"] = "0" * 64
    elif problem == "source":
        value["source_id"] = False
        resign(value)
    elif problem == "future":
        value["available_at"] = DAYS[2] + "T16:00:00+05:30"
        resign(value)
    elif problem == "shape":
        value["unexpected"] = 1
        resign(value)
    elif problem == "native_number":
        import numpy as np

        if "mean_daily_shares" in value:
            value["mean_daily_shares"] = np.float64(value["mean_daily_shares"])
        else:
            value["correlations"]["AAA"] = np.float64(1)
        resign(value)
    elif problem in ("tuple", "custom", "cycle", "nonfinite", "oversize", "depth"):
        extra = {
            "tuple": (1, 2),
            "custom": CustomDict(a=1),
            "nonfinite": float("nan"),
            "oversize": "x" * 66000,
        }.get(problem)
        if problem == "cycle":
            extra = []
            extra.append(extra)
        elif problem == "depth":
            extra = 1
            for _ in range(70):
                extra = [extra]
        value["unexpected"] = extra
        if problem not in ("cycle", "nonfinite"):
            resign(value)
    actual = outcome(lambda: check(value, **arguments))
    monkeypatch.setattr(features, "_bounded_sealed_identity", lambda *a, **kw: None)
    assert actual == outcome(lambda: check(value, **arguments))


def test_entry_fast_path_keeps_schema_and_dynamic_bindings_fresh(entry, monkeypatch):
    value, check, arguments = entry

    def forbidden(*args, **kwargs):
        raise AssertionError("duplicate size/hash traversal")

    monkeypatch.setattr(features, "_json_size", forbidden)
    monkeypatch.setattr(features, "fingerprint", forbidden)
    assert check(value, **arguments) is value
    with pytest.raises(ValueError):
        check(value, **{**arguments, "symbol": "BBB"})
    with pytest.raises(ValueError):
        check(value, **{**arguments, "decision_at": "1900-01-01T00:00:00+05:30"})
    if "required_symbols" in arguments:
        with pytest.raises(ValueError, match="held symbol"):
            check(value, **{**arguments, "required_symbols": ["MISSING"]})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"ascii": '\\"\n\x00'},
        {"unicode": "\u2603\U0001f680"},
        {"tuple": (1, 2)},
        {"custom": CustomDict(a=1)},
    ],
)
@pytest.mark.parametrize("offset", [-1, 0, 1000])
def test_sealed_size_digest_and_capture_match_streaming(payload, offset, monkeypatch):
    value = {**payload, "id": fingerprint(payload)}
    maximum = (
        len(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        + offset
    )
    with StringIO() as capture:
        actual = outcome(lambda: features._sealed_identity(value, maximum, _capture=capture))
        actual_capture = capture.getvalue()
    monkeypatch.setattr(features, "_bounded_sealed_identity", lambda *a, **kw: None)
    with StringIO() as capture:
        assert actual == outcome(
            lambda: features._sealed_identity(value, maximum, _capture=capture)
        )
        assert actual_capture == capture.getvalue()


@pytest.mark.parametrize(
    "problem",
    [
        "none",
        "quantity",
        "order",
        "daily",
        "policy",
        "tuple",
        "custom",
        "cycle",
        "nonfinite",
        "depth",
        "missing_feature",
    ],
)
def test_constraint_receipts_match_unfused_checks(completed, problem, monkeypatch):
    report, frozen = deepcopy(completed)
    if problem == "quantity":
        report["ledger"][0]["quantity"] += 1
    elif problem == "order":
        report["engine_records"]["orders"][0]["fees"] += 1
    elif problem == "daily":
        report["execution"]["capacity"]["daily_symbols"][0]["bought_shares"] += 1
    elif problem == "policy":
        report["policy_version"] = "unknown"
    elif problem == "missing_feature":
        frozen["capacity"] = {}
        resign(frozen)
    elif problem in ("tuple", "custom", "cycle", "nonfinite", "depth"):
        extra = {"tuple": (1, 2), "custom": CustomDict(a=1), "nonfinite": float("nan")}.get(problem)
        if problem == "cycle":
            extra = []
            extra.append(extra)
        elif problem == "depth":
            extra = 1
            for _ in range(70):
                extra = [extra]
        report["strategies"][0]["unused_metadata"] = extra
    actual = constraints.assess(report, frozen)
    monkeypatch.setattr(constraints, "bounded_canonical_snapshot", lambda *a, **kw: None)
    assert actual == constraints.assess(report, frozen)
    if actual["status"] == "invalid":
        assert actual["report_evidence_id"] is None


def test_constraint_owned_bytes_are_hashed_after_all_fresh_checks(completed, monkeypatch):
    report, frozen = deepcopy(completed)
    captured = []
    original = constraints.bounded_canonical_snapshot

    def snapshot(value, **kwargs):
        result = original(value, **kwargs)
        captured.append(result)
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("duplicate subset size traversal")

    monkeypatch.setattr(constraints, "bounded_canonical_snapshot", snapshot)
    monkeypatch.setattr(constraints, "_json_size", forbidden)
    result = constraints.assess(report, frozen)
    assert result["status"] == "supported"
    owned, raw = captured[0]
    assert owned["ledger"] is not report["ledger"]
    assert result["report_evidence_id"] == hashlib.sha256(raw).hexdigest()
    import numpy as np

    observed = owned["ledger"][1]["execution_details"]["gross_before"]
    assert type(observed) is np.float64
    report["ledger"][0]["quantity"] += 1
    assert constraints.assess(report, frozen)["status"] == "invalid"


def test_custom_report_retains_original_access_and_fallback(completed, monkeypatch):
    report, frozen = deepcopy(completed)
    report = CustomDict(report)
    original = constraints.assess(report, frozen)

    def forbidden(*args, **kwargs):
        raise AssertionError("custom report must retain its original admission")

    monkeypatch.setattr(constraints, "bounded_canonical_snapshot", forbidden)
    assert constraints.assess(report, frozen) == original


def test_changed_custom_report_access_keeps_original_policy_check_sequence(completed, monkeypatch):
    class ChangingReport(dict):
        def get(self, key, default=None):
            if key == "policy_version":
                self.reads += 1
                if self.reads > 1:
                    return "unknown"
            return super().get(key, default)

    report, frozen = deepcopy(completed)
    report = ChangingReport(report)
    report.reads = 0
    actual = constraints.assess(report, frozen)
    assert actual["status"] == "invalid"
    actual_reads = report.reads
    report.reads = 0
    monkeypatch.setattr(constraints, "bounded_canonical_snapshot", lambda *a, **kw: None)
    assert constraints.assess(report, frozen) == actual
    assert report.reads == actual_reads


@pytest.mark.parametrize("bound", [1, 100000])
def test_constraint_bounds_and_forced_optional_fallback(completed, bound, monkeypatch):
    report, frozen = completed
    monkeypatch.setattr(constraints, "MAX_BYTES", bound)
    actual = constraints.assess(report, frozen)
    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_NODES", 1)
    assert constraints.assess(report, frozen) == actual


def test_repeated_constraint_calls_do_not_retain_captured_trees(completed, monkeypatch):
    class OwnedDict(dict):
        pass

    import weakref

    references = []
    original = constraints.bounded_canonical_snapshot

    def snapshot(value, **kwargs):
        owned, raw = original(value, **kwargs)
        owned = OwnedDict(owned)
        references.append(weakref.ref(owned))
        return owned, raw

    monkeypatch.setattr(constraints, "bounded_canonical_snapshot", snapshot)
    for _ in range(20):
        assert constraints.assess(*completed)["status"] == "supported"
    gc.collect()
    assert all(reference() is None for reference in references)
