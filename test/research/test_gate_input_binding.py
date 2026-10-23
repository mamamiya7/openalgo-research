"""Exact scoped gate bindings without trusting any mutable caller identity."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from test_evaluation_basis import complete
from test_portfolio_coverage import evidence as coverage_evidence
from test_vectorbt_portfolio import signal, snapshot, strategy

from research.gate_input_binding import (
    FIELDS,
    execution_inputs_id,
    freeze_gate_signals,
    prepared_gate_inputs,
)
from research.portfolio import fingerprint
from research.prepared_calculation import prepared_calculation


def source():
    return complete(coverage_evidence([strategy(signals=[signal(), signal("BBB")])], snapshot()))


def test_scoped_input_hash_is_exact_and_rechecks_replaced_fields():
    evidence = source()
    original = deepcopy(evidence)
    expected = fingerprint({key: evidence[key] for key in FIELDS})
    with prepared_calculation(evidence["snapshot"]) as market:
        with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}) as bound:
            assert execution_inputs_id(bound) == expected
            with patch(
                "research.portfolio.fingerprint", side_effect=AssertionError("duplicate hash")
            ):
                assert execution_inputs_id(dict(bound)) == expected
            with pytest.raises(TypeError):
                bound["strategies"][0]["config"]["target_pct"] = 17
            changed = {**bound, "portfolio": {**bound["portfolio"], "capital": 12345}}
            assert execution_inputs_id(changed) == fingerprint(
                {key: changed[key] for key in FIELDS}
            )
            assert execution_inputs_id(changed) != expected
        assert evidence == original


def test_market_bytes_reuse_is_bounded_owned_and_clears_on_failure():
    evidence = source()
    with pytest.raises(RuntimeError, match="abort"):
        with prepared_calculation(evidence["snapshot"]) as market:
            from research import canonical_json

            original = canonical_json.bounded_canonical_fragments
            encoded_snapshots = []

            def encoder(value, **kwargs):
                if value is market.snapshot:
                    encoded_snapshots.append(1)
                return original(value, **kwargs)

            with patch.object(canonical_json, "bounded_canonical_fragments", encoder):
                for capital in (10000, 20000):
                    current = {
                        **evidence,
                        "snapshot": market.snapshot,
                        "portfolio": {**evidence["portfolio"], "capital": capital},
                    }
                    with prepared_gate_inputs(current) as bound:
                        assert execution_inputs_id(bound) == fingerprint(
                            {key: current[key] for key in FIELDS}
                        )
                assert len(encoded_snapshots) == 1
            raise RuntimeError("abort")
    assert market._canonical_snapshot_bytes is None
    evidence["portfolio"]["capital"] = 17_000
    assert execution_inputs_id(evidence) == fingerprint({key: evidence[key] for key in FIELDS})


def test_ordinary_inputs_keep_mutability_and_full_hashing():
    evidence = source()
    with prepared_gate_inputs(evidence) as same:
        assert same is evidence
        first = execution_inputs_id(same)
        same["strategies"][0]["signals"][0]["row"] = 91
        assert execution_inputs_id(same) != first


def test_one_use_strategy_wrapper_is_not_cached_but_signal_bytes_reuse():
    from research.owned_entry_features import _capture

    evidence = source()
    evidence["strategies"][0]["signals"] = _capture(
        [{**signal(), "row": index} for index in range(80)]
    )
    currents = [
        {
            **evidence,
            "strategies": [
                {
                    **evidence["strategies"][0],
                    "config": {**evidence["strategies"][0]["config"], "target_pct": target},
                }
            ],
        }
        for target in (4, 9)
    ]
    expected = [fingerprint({key: current[key] for key in FIELDS}) for current in currents]
    with prepared_calculation(evidence["snapshot"]) as market:
        with patch.object(
            market, "memoize_canonical_json", wraps=market.memoize_canonical_json
        ) as memo:
            for current, identity in zip(currents, expected, strict=True):
                with prepared_gate_inputs({**current, "snapshot": market.snapshot}) as bound:
                    ancestor = bound["strategies"]
                    child = ancestor[0]["signals"]
                    assert execution_inputs_id(bound) == identity
                    assert id(child) in market._canonical_lists
                    assert not any(call.args[0] is ancestor for call in memo.call_args_list)
                    assert id(ancestor) not in market._canonical_features
        assert len(market._canonical_lists) == 1


def test_actual_prepared_joint_gate_matches_original_receipts_exactly():
    from test_joint_stock_market import prepared

    from research import joint_stock_market as joint

    evidence = prepared()
    context = evidence["automatic_stock_market_context"]
    with joint.PreparedJointContext(context) as gate:
        with prepared_calculation(evidence["snapshot"]) as market:
            for rule in joint.catalog()[1:5]:
                expected = gate.apply(evidence, context, rule)
                with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}) as bound:
                    actual = gate.apply(bound, context, rule)
                assert actual == expected and fingerprint(actual) == fingerprint(expected)


def test_conditioned_lists_freeze_and_encode_once_only_in_private_gate_scope():
    from research import canonical_json

    evidence = source()
    plain = [dict(evidence["strategies"][0]["signals"][0], research_exclusion="condition")]
    assert freeze_gate_signals(plain) is plain
    original = canonical_json.prepared_canonical_fragments
    with prepared_calculation(evidence["snapshot"]) as market:
        with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}):
            with patch.object(
                canonical_json, "prepared_canonical_fragments", wraps=original
            ) as encode:
                owned = freeze_gate_signals(plain)
                assert owned == plain and owned is not plain
                assert freeze_gate_signals(owned) is owned
                assert deepcopy(owned) is owned
                for target in (4, 9):
                    value = {"signals": owned, "target": target}
                    assert fingerprint(value) == fingerprint({"signals": plain, "target": target})
                assert sum(call.args[0] is owned for call in encode.call_args_list) == 1
            with pytest.raises(TypeError):
                owned[0]["research_exclusion"] = "changed"
            plain[0]["research_exclusion"] = "changed"
            assert fingerprint(owned) != fingerprint(plain)
        assert canonical_json._REGISTERED_SIGNAL_LISTS.get() is None


def test_conditioned_list_registration_budget_falls_back_without_changing_hashes():
    from research import canonical_json

    evidence = source()
    with prepared_calculation(evidence["snapshot"]) as market:
        with prepared_gate_inputs({**evidence, "snapshot": market.snapshot}):
            lists = [freeze_gate_signals([{"row": index}]) for index in range(10)]
            assert len(canonical_json._REGISTERED_SIGNAL_LISTS.get()) == 8
            assert fingerprint(lists[-1]) == fingerprint([{"row": 9}])
            assert freeze_gate_signals([{"unsupported": (1, 2)}]) == [{"unsupported": (1, 2)}]
