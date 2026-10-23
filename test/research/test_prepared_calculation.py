"""Small detached-input, validation parity and lifetime checks; no optimiser."""

import copy
from unittest.mock import patch

import numpy as np
import pytest
from test_vectorbt_portfolio import DAYS, signal, snapshot, strategy

from research.data import validate_snapshot
from research.prepared_calculation import (
    PreparedCalculation,
    PreparedCalculationWindows,
    prepared_calculation,
    prepared_market,
)


@pytest.mark.parametrize("minute", [False, True])
def test_coverage_matches_boundary_and_reuses_unique_symbol_arrays(minute):
    source = snapshot(minute=minute)
    keys = source.get("timeline", DAYS)
    del source["bars"]["AAA"][keys[1]]
    source["provenance"]["quality_findings"] = [{"symbol": "AAA", "reason": "bad"}]
    rows = [signal(), signal("BBB", 1), signal("ABSENT", 1)]
    expected = validate_snapshot(source, rows)
    with prepared_calculation(source) as owner:
        assert owner.coverage(rows) == expected
        assert owner.ohlc.shape == (len(keys), 2, 4)
        lots = [(0, rows[0]), (0, rows[0]), (1, rows[-1])]
        close = owner.close_for(lots)
        np.testing.assert_equal(close[:, 0], close[:, 1])
        assert np.isnan(close[:, 2]).all()
        assert np.isnan(close[1, 0])
        # Public receipts remain independent, ordinary JSON-compatible data.
        report = owner.coverage(rows)
        report["provenance"]["provider"] = "changed"
        assert owner.coverage(rows) == expected


def test_source_mutation_is_detached_and_changed_evidence_requires_new_admission():
    source = snapshot()
    day = DAYS[0]
    with prepared_calculation(source) as owner:
        assert prepared_market(owner.snapshot) is owner
        assert prepared_market(source) is None
        source["bars"]["AAA"][day]["open"] = -1
        assert owner.snapshot["bars"]["AAA"][day]["open"] == 100
        with pytest.raises(TypeError):
            owner.snapshot["bars"]["AAA"][day]["open"] = -1
        with pytest.raises(ValueError):
            owner.ohlc.setflags(write=True)
        with pytest.raises(ValueError, match="malformed OHLC"):
            with prepared_calculation(source):
                pytest.fail("changed malformed evidence was admitted")
        assert prepared_market(owner.snapshot) is owner


def test_validation_runs_once_per_owner_not_per_candidate_and_closes_on_failure():
    source = snapshot()
    frozen = None
    with patch("research.prepared_calculation.validate_snapshot", wraps=validate_snapshot) as check:
        with pytest.raises(RuntimeError, match="test failure"):
            with prepared_calculation(source) as owner:
                frozen = owner.snapshot
                for row in (signal(), signal(day=1), signal("BBB", 2)):
                    assert owner.coverage([row]) == validate_snapshot(source, [row])
                assert check.call_count == 1
                raise RuntimeError("test failure")
    assert prepared_market(frozen) is None
    assert owner.snapshot is None and owner.ohlc is None
    with pytest.raises(RuntimeError, match="closed"):
        owner.coverage([signal()])


def test_nested_owners_restore_outer_without_cross_snapshot_reuse():
    source = snapshot()
    with prepared_calculation(source) as outer:
        with prepared_calculation(copy.deepcopy(source)) as inner:
            assert prepared_market(inner.snapshot) is inner
            assert prepared_market(outer.snapshot) is None
        assert prepared_market(outer.snapshot) is outer


def test_required_daily_subset_and_missing_findings_match():
    source = snapshot()
    source["required_dates"] = {"AAA": [DAYS[1], DAYS[3]]}
    del source["bars"]["AAA"][DAYS[3]]
    with prepared_calculation(source) as owner:
        for rows in ([signal()], [signal("BBB")], [signal("ABSENT")]):
            assert owner.coverage(rows) == validate_snapshot(source, rows)


def test_bound_rejects_before_dense_allocation():
    with patch("research.prepared_calculation.MAX_MARKET_CELLS", 1):
        with pytest.raises(ValueError, match="bounded cell limit"):
            with prepared_calculation(snapshot()):
                pytest.fail("oversize owner admitted")


def test_reopening_changed_valid_prices_creates_fresh_numeric_inputs():
    source = snapshot()
    with prepared_calculation(source) as first:
        previous = first.ohlc[0, 0, 3]
    source["bars"]["AAA"][DAYS[0]] = {"open": 200, "high": 200, "low": 200, "close": 200}
    with prepared_calculation(source) as second:
        assert second.ohlc[0, 0, 3] == 200
        assert second.ohlc[0, 0, 3] != previous


def test_invalid_signal_dependent_required_minute_slots_still_rejected():
    source = snapshot(minute=True)
    source["required_timestamps"] = {"AAA": ["2026-01-05T08:00:00+05:30"]}
    with prepared_calculation(source) as owner:
        with pytest.raises(ValueError, match="recorded timeline"):
            owner.coverage([signal()])
        assert owner.coverage([signal("BBB")]) == validate_snapshot(source, [signal("BBB")])


def test_period_owners_can_be_activated_alternately_and_closed_idempotently():
    first, second = PreparedCalculation(snapshot()), PreparedCalculation(snapshot())
    try:
        for owner in (first, second, first):
            other = first if owner is second else second
            with owner.activate():
                assert prepared_market(owner.snapshot) is owner
                assert prepared_market(other.snapshot) is None
            assert prepared_market(owner.snapshot) is None
        with first.activate():
            with pytest.raises(RuntimeError, match="interrupted"):
                with second.activate():
                    raise RuntimeError("interrupted")
            assert prepared_market(first.snapshot) is first
    finally:
        first.close()
        second.close()
        second.close()
    with pytest.raises(RuntimeError, match="closed"):
        with first.activate():
            pytest.fail("closed owner activated")


def test_two_period_registry_evicts_readmits_and_releases_on_exception():
    source = snapshot()
    with patch("research.prepared_calculation.validate_snapshot", wraps=validate_snapshot) as check:
        with pytest.raises(RuntimeError, match="abort"):
            with PreparedCalculationWindows() as periods:
                first = periods.admit("first", source)
                second = periods.admit("second", source)
                assert periods.get("first") is first
                third = periods.admit("third", source)
                assert second.ohlc is None
                periods.evict("first")
                reopened = periods.admit("first", source)
                assert reopened is not first and first.ohlc is None
                assert check.call_count == 4
                with reopened.activate():
                    raise RuntimeError("abort")
        assert third.ohlc is None and reopened.ohlc is None
        assert prepared_market(reopened.snapshot) is None


def test_native_repeated_candidates_reuse_prices_but_recheck_holding_and_capital():
    from research.connectors import vectorbt_portfolio as adapter

    source = snapshot()
    del source["bars"]["AAA"][DAYS[3]]
    with patch("research.prepared_calculation.validate_snapshot", wraps=validate_snapshot) as check:
        with prepared_calculation(source) as owner:
            with patch.object(adapter, "_schedule", wraps=adapter._schedule) as schedule:
                for _ in range(2):
                    adapter.validate([strategy(hold_sessions=1)], owner.snapshot, 100_000)
                with pytest.raises(ValueError, match="complete prices"):
                    adapter.validate([strategy(hold_sessions=2)], owner.snapshot, 100_000)
                with pytest.raises(ValueError, match="capital"):
                    adapter.validate([strategy()], owner.snapshot, 0)
                assert schedule.call_count == 3
            assert check.call_count == 1


def test_automatic_run_owns_registry_only_for_native_connector(monkeypatch):
    from contextlib import nullcontext

    from research import automatic_walk_forward as automatic
    from research.connectors import vectorbt_portfolio as adapter

    monkeypatch.setattr(automatic, "_context", lambda _: object())
    monkeypatch.setattr(automatic, "prepare_gate", lambda _: nullcontext())
    captured = []

    def small_run(evidence, **kwargs):
        registry = kwargs["prepared_calculations"]
        if registry is not None:
            captured.append(registry.admit("test", snapshot()))
        raise RuntimeError("small run stopped")

    monkeypatch.setattr(automatic, "_run", small_run)
    for evaluate in (adapter.evaluate, lambda *_: None):
        with pytest.raises(RuntimeError, match="small run stopped"):
            automatic.run(
                {"portfolio": {}},
                evaluate=evaluate,
                checkpoint=lambda *_: None,
                progress=lambda *_: None,
                cancelled=lambda: None,
            )
    assert len(captured) == 1 and captured[0].ohlc is None


def test_sparse_supported_snapshot_declines_dense_reuse_without_changing_admission():
    from research.connectors import vectorbt_portfolio as adapter

    source = snapshot()
    source["bars"]["BBB"] = {DAYS[0]: source["bars"]["BBB"][DAYS[0]]}
    expected = adapter.validate([strategy()], source, 100_000)
    with patch("research.prepared_calculation.MAX_MARKET_CELLS", 4):
        with PreparedCalculationWindows() as periods:
            assert periods.admit("sparse", source) is None
            assert periods.get("sparse") is None
            assert adapter.validate([strategy()], source, 100_000) == expected
            source["bars"]["BBB"][DAYS[0]]["open"] = -1
            with pytest.raises(ValueError, match="malformed OHLC"):
                adapter.validate([strategy()], source, 100_000)
            periods.evict("sparse")


def test_numeric_packing_cache_requires_owned_evidence_is_bounded_and_cleared():
    from test_owned_entry_features import entry

    from research.owned_entry_features import admit_entry
    from research.portfolio_execution_features import CORRELATION_VERSION

    original = entry(CORRELATION_VERSION)
    owned = admit_entry(original, CORRELATION_VERSION)
    with prepared_calculation(snapshot()) as market:

        def factory():
            return np.array([1.0, 0.5]), ("CCC",)

        with patch("research.prepared_calculation.MAX_NUMERIC_FEATURES", 1):
            with patch("research.prepared_calculation.MAX_NUMERIC_FEATURE_BYTES", 1000):
                first = market.memoize_entry_feature(owned, "correlation", factory)
                assert (
                    market.memoize_entry_feature(
                        owned, "correlation", lambda: pytest.fail("repacked")
                    )
                    is first
                )
                with pytest.raises(ValueError):
                    first[0].setflags(write=True)
                other = market.memoize_entry_feature(owned, "other", factory)
                assert other is not first and len(market._numeric_features) == 1
        calls = []

        def mutable_factory():
            calls.append(1)
            return original["correlations"]["BBB"]

        assert market.memoize_entry_feature(original, "correlation", mutable_factory) == 0.5
        original["correlations"]["BBB"] = 0.25
        assert market.memoize_entry_feature(original, "correlation", mutable_factory) == 0.25
        assert len(calls) == 2
    assert not market._numeric_features and market._numeric_feature_bytes == 0


def test_owned_snapshot_retains_json_hash_and_full_validation_outside_active_owner():
    from research.connectors import vectorbt_portfolio as adapter
    from research.owned_entry_features import is_owned_entry, owned_json_bounds
    from research.report_contract import fingerprint

    source = snapshot()
    expected = fingerprint({"snapshot": source, "other": [1, "test"]})
    with prepared_calculation(source) as market:
        owned = market.snapshot
        assert isinstance(owned, dict) and isinstance(owned["sessions"], list)
        assert fingerprint({"snapshot": owned, "other": [1, "test"]}) == expected
        assert owned_json_bounds(owned) is not None
        assert not is_owned_entry(owned, None)
        assert validate_snapshot(owned, [signal()]) == validate_snapshot(source, [signal()])
    assert prepared_market(owned) is None
    # An immutable representation alone never grants an active native bypass.
    with patch.object(adapter, "validate_snapshot", wraps=validate_snapshot) as validate:
        adapter.validate([strategy()], owned, 100_000)
        assert validate.call_count == 1


def test_native_period_detaches_static_gate_fields_and_releases_on_failure(monkeypatch):
    from test_automatic_walk_forward import prepared

    from research import automatic_walk_forward as automatic
    from research.connectors import vectorbt_portfolio as adapter
    from research.owned_entry_features import is_immutable_json

    source = prepared()
    original = copy.deepcopy(source)
    captured = []

    def stop_at_gate(current, *_args, **_kwargs):
        owner = prepared_market(current["snapshot"])
        assert owner is not None
        captured.append(owner)
        for field in ("signals", "versions"):
            assert is_immutable_json(current[field])
            assert current[field] is not source[field]
            with pytest.raises(TypeError):
                if isinstance(current[field], dict):
                    current[field]["changed"] = True
                else:
                    current[field].append({})
        raise RuntimeError("stop before native calculation")

    monkeypatch.setattr(automatic, "_gate", stop_at_gate)
    with pytest.raises(RuntimeError, match="stop before native calculation"):
        automatic.run(
            source,
            evaluate=adapter.evaluate,
            checkpoint=lambda *_: None,
            progress=lambda *_: None,
            cancelled=lambda: None,
        )
    assert source == original
    assert captured and captured[0].snapshot is None
