"""Fixed-definition indexing preserves public mutation and provider semantics."""

from copy import deepcopy
from datetime import datetime

import pytest
from test_stock_features import record

from research import stock_features as legacy
from research import stock_indicators as indicators
from research import stock_relative_strength as relative
from research.prepared_stock_catalogue import component_if_owned, prepared_catalogues


@pytest.mark.parametrize("module", [legacy, indicators, relative])
def test_every_rule_normalizes_exactly_and_public_results_remain_mutable(module):
    expected = module.catalog()
    with prepared_catalogues():
        for rule in expected:
            actual = module.normalize_hypothesis(rule)
            assert actual == rule
            actual["threshold"] = "caller mutation"
            assert module.normalize_hypothesis(rule) == rule
        public = module.catalog()
        public[-1]["threshold"] = "changed public catalogue"
        with pytest.raises(ValueError):
            module.normalize_hypothesis(public[-1])
        assert module.catalog() == expected


def test_changed_catalogue_provider_is_observed_instead_of_serving_old_definitions(monkeypatch):
    old = next(row for row in indicators.catalog() if row["id"] == "rsi_14_gt50")
    changed = {**old, "threshold": 51.0}
    original = indicators.catalog
    replacement = [{**row} for row in original()]
    replacement = [changed if row["id"] == old["id"] else row for row in replacement]
    with prepared_catalogues():
        assert indicators.normalize_hypothesis(old) == old
        monkeypatch.setattr(indicators, "catalog", lambda: deepcopy(replacement))
        assert component_if_owned("indicator", old["id"]) is None
        assert indicators.normalize_hypothesis(changed) == changed
        with pytest.raises(ValueError):
            indicators.normalize_hypothesis(old)


def test_conjunction_does_not_reconstruct_catalogue_for_each_observation(monkeypatch):
    prices = record()
    day = prices["calendar"]["sessions"][-1]
    decision = datetime.fromisoformat(day + "T09:15:00+05:30")
    rule = next(row for row in indicators.catalog() if row["id"] == "ema20_and_rsi14_gt50")
    expected = indicators._feature(prices, decision, rule)
    calls, original = [], indicators.catalog

    def catalog():
        calls.append(True)
        return original()

    monkeypatch.setattr(indicators, "catalog", catalog)
    with prepared_catalogues():
        initial = len(calls)
        assert indicators._feature(prices, decision, rule) == expected
        assert indicators._feature(prices, decision, rule) == expected
        assert len(calls) == initial


def test_exception_closes_index_and_copied_context_cannot_extend_its_lifetime():
    from contextvars import copy_context

    with pytest.raises(RuntimeError, match="cancelled"):
        with prepared_catalogues() as owner:
            copied = copy_context()
            assert component_if_owned("relative", "relative_20_gt0")
            raise RuntimeError("cancelled")
    assert owner.closed and not owner.rules
    assert copied.run(component_if_owned, "relative", "relative_20_gt0") is None
