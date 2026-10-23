"""Run-owned frozen portfolio receipts preserve native accounts and all checks."""

import gc
import weakref
from contextlib import nullcontext
from copy import deepcopy

import pytest
from test_portfolio_execution_features import prepare, source

from research import execution_features as atr
from research import portfolio_constraint_evidence as constraints
from research import portfolio_execution_features as features
from research.connectors.vectorbt_portfolio import evaluate


@pytest.fixture(scope="module", params=[False, True], ids=["daily", "minute"])
def evidence(request):
    current, context, records = source(minute=request.param, quantity=100_000)
    if request.param:
        snapshot = current["snapshot"]
        first = min(signal["date"] for row in current["strategies"] for signal in row["signals"])
        start = snapshot["sessions"].index(first)
        snapshot["sessions"] = snapshot["sessions"][start : start + 2]
        snapshot["session_hours"] = {
            day: snapshot["session_hours"][day] for day in snapshot["sessions"]
        }
        snapshot["timeline"] = [
            stamp for stamp in snapshot["timeline"] if stamp[:10] in snapshot["sessions"]
        ]
        snapshot["provenance"]["temporal_version"] = "minute-open-v1"
        snapshot["bars"] = {
            symbol: {
                stamp: {**deepcopy(record["evidence"]["bars"][stamp[:10]]), "timestamp": stamp}
                for stamp in snapshot["timeline"]
            }
            for symbol, record in context["series"].items()
        }
    return atr.attach(prepare(current, context, records))


def test_native_accounts_and_constraint_receipt_are_exactly_equal(evidence):
    original = deepcopy(evidence)
    expected = features.attach(evidence)
    before = evaluate(expected["strategies"], expected["snapshot"], 1000)
    expected_audit = constraints.assess(before, evidence["portfolio_execution_features"])
    assert expected_audit["status"] != "invalid", expected_audit
    with features._PreparedPortfolioFeatures(evidence["portfolio_execution_features"]) as owner:
        actual = owner.attach(evidence)
        assert actual == expected
        after = evaluate(actual["strategies"], actual["snapshot"], 1000)
        assert after == before
        assert owner.assess(after, evidence["portfolio_execution_features"]) == expected_audit
        changed = deepcopy(after)
        changed["ledger"][0]["quantity"] += 1
        rejected = constraints.assess(changed, evidence["portfolio_execution_features"])
        assert rejected["status"] == "invalid"
        assert owner.assess(changed, evidence["portfolio_execution_features"]) == rejected
    assert evidence == original


def test_only_the_whole_frozen_seal_is_reused(evidence, monkeypatch):
    source_receipt = evidence["portfolio_execution_features"]
    calls = []
    original_seal = features._sealed_identity

    def counted(value, maximum, **kwargs):
        calls.append(value["id"])
        return original_seal(value, maximum, **kwargs)

    monkeypatch.setattr(features, "_sealed_identity", counted)
    with features._PreparedPortfolioFeatures(source_receipt) as owner:
        for _ in range(3):
            attached = owner.attach(evidence)
            # A malformed candidate report still undergoes all report checks.
            result = owner.assess({}, source_receipt)
            assert result["status"] == "invalid"
            assert "constraint_report_structure_or_bound_invalid" in result["reasons"]
        assert len(calls) == 1
        # Every entry retains its own independent causal feature validation.
        monkeypatch.setattr(
            features,
            "validate_capacity_entry",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("entry still checked")),
        )
        with pytest.raises(ValueError, match="entry still checked"):
            owner.attach(evidence)
        assert attached["strategies"]


def test_caller_and_returned_signal_mutation_cannot_change_owned_projection(evidence):
    current = deepcopy(evidence)
    receipt = current["portfolio_execution_features"]
    expected = features.attach(current)["strategies"]
    with features._PreparedPortfolioFeatures(receipt) as owner:
        first = owner.attach(current)
        next(iter(receipt["capacity"].values()))["feature"]["mean_daily_shares"] = 1
        with pytest.raises(TypeError, match="immutable"):
            first["strategies"][0]["signals"][0]["entry_capacity"]["mean_daily_shares"] = 2
        assert owner.attach(current)["strategies"] == expected
        with pytest.raises(ValueError, match="original source object"):
            owner.attach({**current, "portfolio_execution_features": deepcopy(receipt)})
        with pytest.raises(ValueError, match="original source object"):
            owner.assess({}, deepcopy(receipt))
        with pytest.raises(ValueError, match="features changed"):
            features.attach(current)


@pytest.mark.parametrize("problem", ["identity", "version", "nonfinite", "bound"])
def test_untrusted_receipts_are_fully_checked_before_preparation(evidence, problem, monkeypatch):
    receipt = deepcopy(evidence["portfolio_execution_features"])
    if problem == "identity":
        receipt["id"] = "0" * 64
    elif problem == "version":
        receipt["version"] = "unknown"
    elif problem == "nonfinite":
        receipt["unexpected"] = float("nan")
    else:
        monkeypatch.setattr(features, "MAX_BYTES", 10)
    captures = []
    real_capture = features.StringIO

    def capture():
        value = real_capture()
        captures.append(value)
        return value

    monkeypatch.setattr(features, "StringIO", capture)
    with pytest.raises(ValueError):
        features._PreparedPortfolioFeatures(receipt)
    assert all(value.closed for value in captures)


@pytest.mark.parametrize("problem", ["atr", "context", "opportunity"])
def test_candidate_specific_bindings_are_never_reused(evidence, problem):
    current = deepcopy(evidence)
    with features._PreparedPortfolioFeatures(current["portfolio_execution_features"]) as owner:
        if problem == "atr":
            current["execution_features"]["id"] = "0" * 64
        elif problem == "context":
            current["automatic_stock_market_context"]["id"] = "0" * 64
        else:
            current["strategies"][0]["signals"][0]["symbol"] = "OTHER"
        with pytest.raises(ValueError):
            owner.attach(current)
        with pytest.raises(ValueError):
            features.attach(current)


@pytest.mark.parametrize("minute", [False, True], ids=["daily", "minute"])
def test_native_cluster_allocation_and_tampered_fills_stay_identical(minute):
    from test_portfolio_wide_allocation import account

    prepared, expected, _, _ = account(minute=minute)
    current = atr.attach(prepared)
    receipt = prepared["portfolio_execution_features"]
    before = evaluate(expected["strategies"], expected["snapshot"], 10000)
    with features._PreparedPortfolioFeatures(receipt) as owner:
        actual = owner.attach(current)
        assert actual == expected
        after = evaluate(actual["strategies"], actual["snapshot"], 10000)
        assert after == before
        assert owner.assess(after, receipt) == constraints.assess(before, receipt)
        changed = deepcopy(after)
        filled = next(row for row in changed["ledger"] if row["quantity"])
        filled["execution_details"]["allocation_weight"] = 0.99
        rejected = constraints.assess(changed, receipt)
        assert rejected["status"] != "supported"
        assert owner.assess(changed, receipt) == rejected


def test_success_exception_and_repeated_close_release_the_owned_tree(evidence):
    references = []
    for index in range(100):
        with pytest.raises(RuntimeError) if index % 2 else nullcontext():
            with features._PreparedPortfolioFeatures(
                evidence["portfolio_execution_features"]
            ) as owner:
                references.append(weakref.ref(owner))
                if index % 2:
                    raise RuntimeError("cancelled or failed")
        with pytest.raises(ValueError, match="released"):
            owner.attach(evidence)
        with pytest.raises(ValueError, match="released"):
            owner.assess({}, evidence["portfolio_execution_features"])
        owner.close()
        del owner
    gc.collect()
    assert all(reference() is None for reference in references)
