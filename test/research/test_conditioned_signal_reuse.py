"""Only the private native gate shares frozen, pre-encoded conditioned rows."""

import json

import pytest
from test_joint_stock_market import prepared

from research import canonical_json
from research import joint_stock_market as joint
from research.gate_input_binding import prepared_gate_inputs
from research.portfolio import fingerprint
from research.prepared_calculation import prepared_calculation


@pytest.mark.parametrize("rule_index", [1, 24])
def test_conditioned_rows_survive_marker_copy_by_identity_with_exact_receipt(
    monkeypatch, rule_index
):
    evidence = prepared()
    source = evidence["automatic_stock_market_context"]
    rule = joint.catalog()[rule_index]
    frozen, original = [], joint.freeze_gate_signals

    def freeze(rows):
        value = original(rows)
        frozen.append(value)
        return value

    monkeypatch.setattr(joint, "freeze_gate_signals", freeze)
    with joint.PreparedJointContext(source) as gate:
        expected = gate.apply(evidence, source, rule)
        expected_signals = expected[0][0]["signals"]
        assert type(expected_signals) is list
        with prepared_calculation(evidence["snapshot"]) as market:
            with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}) as bound:
                actual = gate.apply(bound, source, rule)
                signals = actual[0][0]["signals"]
                assert signals is frozen[-1]  # markers did not clone the signal tree
                registered = canonical_json._REGISTERED_SIGNAL_LISTS.get()[id(signals)]
                assert registered[0] is signals
                assert (
                    registered[1]
                    == json.dumps(
                        expected_signals, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode()
                )
                assert actual == expected
                assert fingerprint(actual) == fingerprint(expected)
                with pytest.raises(TypeError, match="immutable"):
                    signals.clear()
                with pytest.raises(TypeError, match="immutable"):
                    signals[0]["row"] = 999
        # Public owner.apply still returns detached mutable values.
        public = gate.apply(evidence, source, rule)
        public[0][0]["signals"].clear()
        assert gate.apply(evidence, source, rule) == expected


@pytest.mark.parametrize("identifier", ["none", "momentum_20_gt2__all", "rsi_14_gt50__trend_up"])
def test_joint_reuses_only_unchanged_owned_rows_and_detaches_exclusions(monkeypatch, identifier):
    from research import expanded_stock_market as expanded

    evidence = prepared()
    # Include an original exclusion as well as the fixture's pending last row.
    evidence["strategies"][0]["signals"][0]["research_exclusion"] = "original exclusion"
    source = evidence["automatic_stock_market_context"]
    rule = next(row for row in expanded.catalog() if row["id"] == identifier)
    copied, original = [], joint.copy_gate_signal

    def copy(signal):
        copied.append(signal)
        return original(signal)

    monkeypatch.setattr(joint, "copy_gate_signal", copy)
    with expanded.PreparedJointContext(source) as gate:
        expected = gate.apply(evidence, source, rule)
        assert len(copied) == len(evidence["strategies"][0]["signals"])
        copied.clear()
        with prepared_calculation(evidence["snapshot"]) as market:
            with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}) as bound:
                actual = gate.apply(bound, source, rule)
                assert actual == expected
                assert fingerprint(actual) == fingerprint(expected)
                states = [row["state"] for row in actual[1]["membership"]]
                assert len(copied) == sum(state in ("unknown", "filtered") for state in states)
                for state, before, after in zip(
                    states, bound["strategies"][0]["signals"], actual[0][0]["signals"], strict=True
                ):
                    assert (after is before) == (state not in ("unknown", "filtered"))
                if identifier == "none":
                    assert not copied
                elif identifier == "momentum_20_gt2__all":
                    assert copied  # exclusion-writing paths still detach
        copied.clear()
        public = gate.apply(evidence, source, rule)
        assert len(copied) == len(evidence["strategies"][0]["signals"])
        public[0][0]["signals"][0]["row"] = 999
        assert evidence["strategies"][0]["signals"][0]["row"] != 999
