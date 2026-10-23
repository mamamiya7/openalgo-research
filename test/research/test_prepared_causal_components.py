"""Daily membership reuse preserves fresh complete causal/account identities."""

from copy import deepcopy

import pytest
from test_joint_stock_market import prepared

from research import causal_gate_components as components
from research import expanded_stock_market as expanded
from research.gate_input_binding import prepared_gate_inputs
from research.owned_entry_features import _capture, is_immutable_json
from research.portfolio import fingerprint
from research.prepared_calculation import prepared_calculation
from research.stock_market_context import _PreparedViews


def test_daily_components_reuse_across_sizing_but_rebind_all_full_ids(monkeypatch):
    evidence = prepared()
    source = evidence["automatic_stock_market_context"]
    evidence["strategies"][0]["signals"] = _capture(evidence["strategies"][0]["signals"])
    rule = next(row for row in expanded.catalog() if row["id"] == "rsi_14_gt50__trend_up")
    calls = []
    stock_apply, market_apply = _PreparedViews.apply_stock, _PreparedViews.apply_market

    def stock_counted(self, *args, **kwargs):
        calls.append("stock")
        return stock_apply(self, *args, **kwargs)

    def market_counted(self, *args, **kwargs):
        calls.append("market")
        return market_apply(self, *args, **kwargs)

    monkeypatch.setattr(_PreparedViews, "apply_stock", stock_counted)
    monkeypatch.setattr(_PreparedViews, "apply_market", market_counted)
    changed = deepcopy(evidence)
    changed["strategies"][0]["config"].update(order_size=12345, hold_sessions=9, stop_pct=7)
    with expanded.PreparedJointContext(source) as gate:
        expected = gate.apply(changed, source, rule)
        calls.clear()
        with prepared_calculation(evidence["snapshot"]) as market:

            def run(value):
                with prepared_gate_inputs({**value, "snapshot": market.snapshot}) as bound:
                    return gate.apply(bound, source, rule)

            first = run(evidence)
            assert calls == ["stock", "market"]
            second = run(changed)
            assert calls == ["stock", "market"]  # neither membership gate repeated
            assert second == expected
            assert fingerprint(second) == fingerprint(expected)
            assert second[1]["execution_inputs_id"] != first[1]["execution_inputs_id"]
            assert second[1]["causal_inputs_id"] != first[1]["causal_inputs_id"]
            assert second[1]["stock_gate_id"] != first[1]["stock_gate_id"]
            cache = gate._PreparedJointContext__components
            assert len(cache.rows) == 2
            assert all(is_immutable_json(item[0]) for item in cache.rows.values())
            with pytest.raises(TypeError):
                next(iter(cache.rows.values()))[0]["membership"].clear()
            # A new immutable source with changed exclusion cannot inherit a mask.
            excluded = deepcopy(changed)
            excluded["strategies"][0]["signals"] = _capture(
                [
                    {**dict(signal), "research_exclusion": "fresh exclusion"}
                    for signal in changed["strategies"][0]["signals"]
                ]
            )
            excluded_actual = run(excluded)
            assert calls == ["stock", "market", "stock", "market"]
            excluded_expected = gate.apply(excluded, source, rule)
            assert excluded_actual == excluded_expected
    assert not cache.rows and not cache.sources and not cache.snapshots
    assert cache.bytes == cache.snapshot_bytes == 0 and cache.closed


def test_component_cache_declines_public_minute_changed_providers_and_current_invalid_input(
    monkeypatch,
):
    evidence = prepared()
    source = evidence["automatic_stock_market_context"]
    evidence["strategies"][0]["signals"] = _capture(evidence["strategies"][0]["signals"])
    rule = expanded.catalog()[1]
    with expanded.PreparedJointContext(source) as gate:
        gate.apply(evidence, source, rule)
        cache = gate._PreparedJointContext__components
        assert not cache.rows
        with prepared_calculation(evidence["snapshot"]) as market:
            with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}) as bound:
                gate.apply(bound, source, rule)
                assert len(cache.rows) == 1
                altered = {**bound, "frozen_prices": False}
                with pytest.raises(ValueError, match="frozen.*prices"):
                    gate.apply(altered, source, rule)
                assert not components.components_only()
                assert cache.key(bound, "stock", rule) is None  # outside component scope
                with components.causal_components_only():
                    minute = {**bound, "snapshot": _capture({"provenance": {"interval": "1m"}})}
                    assert cache.key(minute, "stock", rule) is None
                # Replacing a native provider declines reuse instead of retaining old results.
                from research import stock_features

                original = stock_features._feature
                calls = []

                def replacement(*args, **kwargs):
                    calls.append(1)
                    return original(*args, **kwargs)

                monkeypatch.setattr(stock_features, "_feature", replacement)
                gate.apply(bound, source, rule)
                assert calls


def test_component_bounds_lru_and_subclass_heap_charges(monkeypatch):
    monkeypatch.setattr(components, "MAX_COMPONENTS", 2)
    cache = components.DailyComponents()
    signals = _capture([{"symbol": "AAA", "date": "2026-01-01", "row": 1}])
    snapshots = [
        _capture({"sessions": [str(i)], "provenance": {"interval": "D"}}) for i in range(3)
    ]
    receipt = {"membership": [{"state": "allowed", "feature": {"value": 1.0}}]}
    assert components._heap_bytes(_capture(receipt)) > 3 * components.sys.getsizeof({})
    for i, snapshot in enumerate(snapshots):
        evidence = {"snapshot": snapshot, "strategies": [{"id": "one", "signals": signals}]}
        key = (id(snapshot), "stock", str(i), (("one", id(signals)),))
        cached = cache.put(key, evidence, receipt)
        assert cached == receipt
        assert len(cache.rows) <= 2 and len(cache.snapshots) <= 2
        assert cache.bytes <= components.MAX_COMPONENT_BYTES
        assert cache.snapshot_bytes <= components.MAX_SNAPSHOT_BYTES
    assert id(snapshots[0]) not in cache.snapshots
    monkeypatch.setattr(components, "MAX_COMPONENT_BYTES", 1)
    assert (
        cache.put((id(snapshots[-1]), "stock", "over", (("one", id(signals)),)), evidence, receipt)
        is receipt
    )
    cache.close()
    assert not cache.rows and not cache.sources and not cache.snapshots
