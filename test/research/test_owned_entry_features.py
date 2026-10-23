"""Small canonical, admission and fresh-binding checks for owned entry evidence."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from research import execution_features as atr
from research import portfolio_allocation as allocation
from research import portfolio_execution_features as portfolio
from research.canonical_json import bounded_canonical_bytes, bounded_canonical_snapshot
from research.owned_entry_features import (
    _OwnedDict,
    admit_entry,
    is_owned_entry,
    owned_json_bounds,
)
from research.portfolio import fingerprint

DAY = "2026-01-07"
DECISION = DAY + "T09:15:00+05:30"


def entry(kind):
    common = {
        "version": kind,
        "entry_session": DAY,
        "from": "2025-01-01",
        "to": "2026-01-06",
        "available_at": "2026-01-06T15:30:00+05:30",
    }
    if kind == atr.ENTRY_VERSION:
        common.update(
            symbol="AAA",
            atr_period=14,
            value=2.0,
            observations=100,
            basis_id="a" * 64,
            recipe_id="b" * 64,
        )
    elif kind == portfolio.CAPACITY_VERSION:
        common.update(
            symbol="AAA",
            observations=20,
            source_id="a" * 64,
            mean_daily_shares=1000.0,
            basis="provider_returned_historical_coordinates",
        )
    elif kind == portfolio.CORRELATION_VERSION:
        common.update(
            symbol="AAA",
            observations=60,
            source_id="a" * 64,
            correlations={"AAA": 1.0, "BBB": 0.5, "CCC": None},
        )
    else:
        common.update(
            observations=60,
            source_id="a" * 64,
            weights={"AAA": 0.5, "BBB": 0.5},
            clusters=[["AAA", "BBB"]],
            unavailable=["CCC"],
            shrinkage=0.1,
        )
    return {**common, "id": fingerprint(common)}


def validate(value, kind, **overrides):
    binding = {"symbol": "AAA", "entry_session": DAY, "decision_at": DECISION, **overrides}
    if kind == atr.ENTRY_VERSION:
        return atr.validate_entry_feature(value, **binding)
    if kind == portfolio.CAPACITY_VERSION:
        return portfolio.validate_capacity_entry(value, **binding)
    if kind == portfolio.CORRELATION_VERSION:
        binding.setdefault("required_symbols", ())
        return portfolio.validate_correlation_entry(value, **binding)
    return allocation.validate(value, **binding)


KINDS = (
    atr.ENTRY_VERSION,
    portfolio.CAPACITY_VERSION,
    portfolio.CORRELATION_VERSION,
    allocation.VERSION,
)


@pytest.mark.parametrize("kind", KINDS)
def test_owned_entries_keep_exact_json_and_canonical_capture_contract(kind):
    original = entry(kind)
    owned = admit_entry(original, kind)
    assert owned == original and is_owned_entry(owned, kind)
    assert deepcopy(owned) is owned
    assert validate(owned, kind) is owned
    assert json.dumps(owned, sort_keys=True) == json.dumps(original, sort_keys=True)
    assert bounded_canonical_bytes(owned) == bounded_canonical_bytes(original)
    captured, raw = bounded_canonical_snapshot(owned)
    assert type(captured) is dict and captured == original and captured is not owned
    assert raw == bounded_canonical_bytes(original)
    nodes, depth, size = owned_json_bounds(owned)
    assert nodes > 1 and depth > 0 and size >= len(json.dumps(owned, sort_keys=True))
    original["available_at"] = "2099-01-01T00:00:00+05:30"
    assert validate(owned, kind) is owned
    with pytest.raises(TypeError, match="immutable"):
        owned["id"] = "tampered"
    with pytest.raises(TypeError, match="immutable"):
        owned._entry_version = "forged"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["symbol", "entry_session", "decision_at"])
def test_owned_entries_recheck_actual_candidate_bindings(kind, change):
    owned = admit_entry(entry(kind), kind)
    changed = {
        "symbol": "ABSENT",
        "entry_session": "2026-01-08",
        "decision_at": "2025-01-01T09:15:00+05:30",
    }
    with pytest.raises(ValueError):
        validate(owned, kind, **{change: changed[change]})


@pytest.mark.parametrize("kind", KINDS)
def test_ordinary_tampered_or_uninitialised_container_cannot_gain_admission(kind):
    original = entry(kind)
    original["id"] = "bad"
    with pytest.raises(ValueError):
        admit_entry(original, kind)
    forged = _OwnedDict(entry(kind))
    assert not is_owned_entry(forged, kind) and owned_json_bounds(forged) is None


def test_owned_nested_values_are_immutable_and_required_held_symbols_remain_fresh():
    corr = admit_entry(entry(portfolio.CORRELATION_VERSION), portfolio.CORRELATION_VERSION)
    with pytest.raises(TypeError):
        corr["correlations"]["BBB"] = 0.0
    with pytest.raises(ValueError, match="held symbol"):
        validate(corr, portfolio.CORRELATION_VERSION, required_symbols=["CCC"])
    owned = admit_entry(entry(allocation.VERSION), allocation.VERSION)
    with pytest.raises(TypeError):
        owned["clusters"][0].append("CCC")
    with pytest.raises(TypeError):
        owned["clusters"].clear()
    with pytest.raises(ValueError, match="held symbol"):
        validate(owned, allocation.VERSION, required_symbols=["CCC"])


def test_owned_validators_skip_static_seals_but_mutable_evidence_retains_checks():
    for kind in KINDS:
        original = entry(kind)
        owned = admit_entry(original, kind)
        module = (
            atr
            if kind == atr.ENTRY_VERSION
            else allocation
            if kind == allocation.VERSION
            else portfolio
        )
        with patch.object(module, "fingerprint", side_effect=AssertionError("repeated seal")):
            assert validate(owned, kind) is owned
        changed = deepcopy(original)
        changed["id"] = "tampered"
        with pytest.raises(ValueError):
            validate(changed, kind)


def test_prepared_atr_owner_lazily_shares_only_admitted_immutable_entries():
    from test_execution_features import prepared

    evidence, _ = prepared()
    expected = atr.attach(evidence)
    with atr._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
        first = owner.attach(evidence)
        second = owner.attach(evidence)
        assert first == second == expected
        left = first["strategies"][0]["signals"][0]["execution_atr"]
        right = second["strategies"][0]["signals"][0]["execution_atr"]
        assert left is right and is_owned_entry(left, atr.ENTRY_VERSION)
        assert deepcopy(left) is left


def test_prepared_portfolio_owner_lazily_shares_capacity_and_correlation():
    from test_portfolio_execution_features import prepare, source

    evidence, context, records = source()
    evidence = prepare(evidence, context, records)
    expected = portfolio.attach(evidence)
    with portfolio._PreparedPortfolioFeatures(evidence["portfolio_execution_features"]) as owner:
        first = owner.attach(evidence)
        second = owner.attach(evidence)
        assert first == second == expected
        for key, version in (
            ("entry_capacity", portfolio.CAPACITY_VERSION),
            ("entry_correlation", portfolio.CORRELATION_VERSION),
        ):
            left = first["strategies"][0]["signals"][0][key]
            right = second["strategies"][0]["signals"][0][key]
            assert left is right and is_owned_entry(left, version)
