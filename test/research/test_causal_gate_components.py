"""Discarded native component accounts never affect the full expanded result."""

from contextvars import copy_context

import pytest
from test_joint_stock_market import prepared

from research import condition_replay, stock_features
from research import expanded_stock_market as expanded
from research.causal_gate_components import causal_components_only, components_only
from research.gate_input_binding import prepared_gate_inputs
from research.portfolio import fingerprint
from research.prepared_calculation import prepared_calculation


@pytest.mark.parametrize(
    "identifier",
    [
        "none",
        "rsi_14_gt50__trend_up",
        "ema20_and_relative20_gt0__trend_up",
        "momentum_10_gt2__volatility_high",
        "momentum_60_gt0__all",
    ],
)
def test_components_only_preserves_complete_expanded_rows_and_receipt(monkeypatch, identifier):
    evidence = prepared()
    source = evidence["automatic_stock_market_context"]
    rule = next(row for row in expanded.catalog() if row["id"] == identifier)
    with expanded.PreparedJointContext(source) as gate:
        expected = gate.apply(evidence, source, rule)
        assert type(expected[0][0]["signals"]) is list

        def discarded(*args, **kwargs):
            raise AssertionError("Discarded component account output was constructed")

        with monkeypatch.context() as patch:
            for module in (stock_features, condition_replay):
                patch.setattr(module, "copy_gate_signal", discarded)
                patch.setattr(module, "freeze_gate_signals", discarded)
                patch.setattr(module, "execution_inputs_id", discarded)
            with prepared_calculation(evidence["snapshot"]) as market:
                with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}) as bound:
                    actual = gate.apply(bound, source, rule)
        assert actual == expected
        assert fingerprint(actual) == fingerprint(expected)
        assert not components_only()
        public = gate.apply(evidence, source, rule)
        public[0][0]["signals"].clear()
        assert gate.apply(evidence, source, rule) == expected


def test_component_scope_requires_private_binding_and_expires_copied_context():
    assert not components_only()
    with causal_components_only():
        assert not components_only()
    evidence = prepared()
    with prepared_calculation(evidence["snapshot"]) as market:
        with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}):
            with pytest.raises(RuntimeError, match="stop"):
                with causal_components_only():
                    assert components_only()
                    inherited = copy_context()
                    raise RuntimeError("stop")
            assert not components_only()
            assert not inherited.run(components_only)


def test_components_only_keeps_current_causality_admission():
    evidence = prepared()
    source = evidence["automatic_stock_market_context"]
    rule = next(row for row in expanded.catalog() if row["id"] == "rsi_14_gt50__trend_up")
    evidence["frozen_prices"] = False
    with expanded.PreparedJointContext(source) as gate:
        with prepared_calculation(evidence["snapshot"]) as market:
            with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}) as bound:
                with pytest.raises(ValueError, match="frozen.*prices"):
                    gate.apply(bound, source, rule)
                assert not components_only()
