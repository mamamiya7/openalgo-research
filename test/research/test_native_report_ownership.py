"""Native exclusive transfer keeps exact checks, types, hashes and public fallback."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from test_capacity_allocation_native import capacity_policy, rows, sealed_features
from test_portfolio_constraint_evidence import frozen_features
from test_vectorbt_portfolio import snapshot

from research import canonical_json
from research import native_report_ownership as ownership
from research import portfolio_constraint_evidence as constraints
from research.connectors import vectorbt_portfolio as native
from research.entry_conditions import VERSION, enrich
from research.portfolio_execution_features import _PreparedPortfolioFeatures
from research.regimes import recipe


def test_actual_native_transfer_reads_current_enriched_strategies_and_keeps_receipt_ids():
    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))
    features = frozen_features(current)
    with _PreparedPortfolioFeatures(features) as prepared:
        with ownership.native_report_scope():
            report = native.evaluate(current, snapshot(), 1000)
            original = report["strategies"]
            descriptor = {
                "version": VERSION,
                "recipe": recipe(),
                "rules": [{"strategy_id": original[0]["id"], "dimension": "trend", "regime": "up"}],
            }
            enrich(
                report, {"portfolio": {"entry_conditions": descriptor}, "entry_condition_gates": []}
            )
            assert report["strategies"] is not original
            expected = constraints.assess(report, features)
            assert expected["status"] == "supported"
            with patch.object(
                constraints,
                "bounded_canonical_snapshot",
                side_effect=AssertionError("defensive copy"),
            ):
                actual = prepared.assess(report, features)
            assert actual == expected
            # Single-use ownership: a second assessment takes the public path.
            with patch.object(
                constraints,
                "bounded_canonical_snapshot",
                wraps=canonical_json.bounded_canonical_snapshot,
            ) as copied:
                assert prepared.assess(report, features) == expected
                assert copied.call_count == 1
        changed = deepcopy(report)
        changed["ledger"][0]["quantity"] += 1
        assert prepared.assess(changed, features)["status"] == "invalid"


def test_exclusive_serializer_encodes_original_tree_and_rejects_tuple_coercion():
    value = {"rows": [{"value": 1.2, "flags": [True, None]}]}
    expected = canonical_json.bounded_canonical_bytes(value)
    original_encoder = canonical_json.json.dumps
    with patch.object(canonical_json.json, "dumps", wraps=original_encoder) as encoded:
        assert canonical_json._exclusive_canonical_bytes(value, max_bytes=1024) == expected
        assert encoded.call_args.args[0] is value
    invalid = {"list": (1, 2)}
    assert canonical_json._exclusive_canonical_bytes(invalid, max_bytes=1024) is None
    assert type(invalid["list"]) is tuple
    assert canonical_json._exclusive_canonical_bytes(value, max_bytes=1) is None
    cycle = []
    cycle.append(cycle)
    assert canonical_json._exclusive_canonical_bytes(cycle, max_bytes=1024) is None
    assert canonical_json._exclusive_canonical_bytes({"x": float("nan")}, max_bytes=1024) is None


def test_tokens_bind_scope_thread_and_source_and_cannot_be_reused():
    report = {"ledger": [{"source_row": 1}]}
    assert ownership.take_native_report(report) is None
    with ownership.native_report_scope():
        ownership.publish_native_report(report)
        assert ownership.take_native_report(deepcopy(report)) is None
        token = ownership.take_native_report(report)
        assert ownership.consume_native_report(token, report)
        assert not ownership.consume_native_report(token, report)
        assert ownership.take_native_report(report) is None
        ownership.publish_native_report(report)
        assert ownership.take_native_report(report) is None
    with ownership.native_report_scope():
        ownership.publish_native_report(report)
        token = ownership.take_native_report(report)
        with patch.object(ownership, "get_ident", return_value=-1):
            assert not ownership.consume_native_report(token, report)
    assert not ownership.consume_native_report(token, report)
    assert not ownership.consume_native_report({"report": report}, report)


@pytest.mark.parametrize("alias", [[1], {"row": 1}, (1,), type("CustomInt", (int,), {})(1)])
def test_source_row_aliases_decline_ownership(alias):
    report = {"ledger": [{"source_row": alias}]}
    with ownership.native_report_scope():
        assert ownership.publish_native_report(report) is report
        assert ownership.take_native_report(report) is None


def test_scope_closes_on_error_and_restores_outer_ownership():
    outer = {"ledger": [{"source_row": 1}]}
    with ownership.native_report_scope():
        ownership.publish_native_report(outer)
        with pytest.raises(RuntimeError, match="cancelled"):
            with ownership.native_report_scope():
                inner = {"ledger": [{"source_row": 2}]}
                ownership.publish_native_report(inner)
                token = ownership.take_native_report(inner)
                raise RuntimeError("cancelled")
        assert not ownership.consume_native_report(token, inner)
        assert ownership.consume_native_report(ownership.take_native_report(outer), outer)
    assert ownership._CURRENT.get() is None
