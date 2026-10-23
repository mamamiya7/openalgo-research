"""Single-use native row bounds preserve strict codec and financial admission."""

import sys
from copy import deepcopy
from unittest.mock import patch

import pytest

from research import canonical_json as codec
from research import native_report_ownership as ownership


def projection(row):
    row = {"source_row": 1, **row}
    report = {"ledger": [row]}
    return report, {"ledger": report["ledger"]}


def capability(report, value):
    ownership.publish_native_report(report)
    return ownership.checked_native_projection(ownership.take_native_report(report), report, value)


@pytest.mark.parametrize(
    "row",
    [
        {"price": 1.2, "quantity": 0, "reason": "excluded", "modes": ["Bypass"], "entry": None},
        {"reason": 'é 😀 \\ "', "flags": [True, False, None, 12, 1.5]},
        {"nested": {"deeper": [1, {"value": 2}]}},
        {"tuple": (1, 2)},
        {"bad": float("nan")},
        {"bad": float("inf")},
        {"bad": type("CustomInt", (int,), {})(1)},
        {"bad": type("CustomStr", (str,), {})("x")},
    ],
)
def test_checked_projection_preserves_exact_bytes_and_nested_custom_fallback(row):
    report, value = projection(row)
    expected = codec._exclusive_canonical_bytes(value, max_bytes=8192)
    with ownership.native_report_scope():
        cap = capability(report, value)
        assert (
            codec._exclusive_canonical_bytes(value, max_bytes=8192, _checked_projection=cap)
            == expected
        )
    if "nested" in row or any(key in row for key in ("tuple", "bad")):
        assert ownership._flat_row_bounds(report["ledger"][0]) is None


@pytest.mark.parametrize(
    "details",
    [
        None,
        {
            "filled_quantity": 0,
            "requested_quantity": 10,
            "opening_equity": 1000.0,
            "constraints": ["shared_cash", "open_positions"],
            "allocation_cluster": ["AAA", "BBB"],
            "atr": None,
            "clipped": True,
        },
    ],
)
def test_checked_flat_rows_remove_recursive_scalar_work_and_keep_original_encoder_input(details):
    report, value = projection(
        {
            "quantity": 0,
            "fees": 0.0,
            "modes": ["Bypass"],
            "reason": "excluded",
            "execution_details": details,
        }
    )

    def encode(cap=None):
        calls = []

        def collect(frame, event, _arg):
            if (
                event == "call"
                and frame.f_code.co_name == "copy"
                and frame.f_code.co_filename.endswith("canonical_json.py")
            ):
                calls.append(1)

        previous = sys.getprofile()
        sys.setprofile(collect)
        try:
            raw = codec._exclusive_canonical_bytes(value, max_bytes=8192, _checked_projection=cap)
        finally:
            sys.setprofile(previous)
        return raw, len(calls)

    expected, ordinary_calls = encode()
    with ownership.native_report_scope():
        cap = capability(report, value)
        original = codec.json.dumps
        with patch.object(codec.json, "dumps", wraps=original) as encoded:
            actual, checked_calls = encode(cap)
        assert encoded.call_args.args[0] is value
    assert actual == expected
    assert checked_calls == 3  # projection, ledger list, checked ledger row
    assert checked_calls < ordinary_calls


@pytest.mark.parametrize(
    "details",
    [
        {"atr": {"value": 1.0}},
        {"flags": [[1]]},
        {"execution_details": {}},
        {"bad": float("nan")},
        {"bad": float("inf")},
        {"bad": (1, 2)},
        type("CustomDict", (dict,), {})({"value": 1.0}),
    ],
)
def test_execution_details_unsupported_or_deeper_children_keep_original_fallback(details):
    report, value = projection({"execution_details": details})
    assert ownership._flat_row_bounds(report["ledger"][0]) is None
    expected = codec._exclusive_canonical_bytes(value, max_bytes=8192)
    with ownership.native_report_scope():
        cap = capability(report, value)
        assert (
            codec._exclusive_canonical_bytes(value, max_bytes=8192, _checked_projection=cap)
            == expected
        )


@pytest.mark.parametrize("depth", [3, 4, 5, 6])
@pytest.mark.parametrize("nodes", [17, 18])
def test_one_details_mapping_preserves_exact_depth_and_bound_accounting(depth, nodes):
    report, value = projection(
        {
            "execution_details": {"flags": [True, None, "é😀"], "quantity": 0},
            "later_list": ["Bypass"],
        }
    )
    expected = codec._canonical_bytes(
        value, nodes, 8192, allow_tuples=False, _borrow=True, _maximum_depth=depth
    )
    with ownership.native_report_scope():
        cap = capability(report, value)
        lookup = ownership.checked_projection_bounds(cap, value)
        actual = codec._canonical_bytes(
            value,
            nodes,
            8192,
            allow_tuples=False,
            _borrow=True,
            _maximum_depth=depth,
            _checked_bounds=lookup,
        )
    assert actual == expected
    assert (actual is not None) == (depth >= 5 and nodes >= 18)


def test_execution_details_cycle_declines_without_unbounded_recursion():
    details = {}
    details["execution_details"] = details
    report, value = projection({"execution_details": details})
    assert ownership._flat_row_bounds(report["ledger"][0]) is None
    with ownership.native_report_scope():
        cap = capability(report, value)
        assert (
            codec._exclusive_canonical_bytes(value, max_bytes=8192, _checked_projection=cap) is None
        )


@pytest.mark.parametrize(
    "nodes,depth,maximum", [(4, 64, 8192), (64, 2, 8192), (64, 64, 1), (64, 64, 8192)]
)
def test_exact_node_depth_and_ascii_bounds_match_original_scanner(nodes, depth, maximum):
    report, value = projection({"reason": "é😀", "flags": [False, None, 4.0]})
    expected = codec._canonical_bytes(
        value,
        nodes,
        maximum,
        allow_tuples=False,
        _borrow=True,
        _maximum_depth=depth,
        allow_native_float64=True,
    )
    with ownership.native_report_scope():
        cap = capability(report, value)
        lookup = ownership.checked_projection_bounds(cap, value)
        actual = codec._canonical_bytes(
            value,
            nodes,
            maximum,
            allow_tuples=False,
            _borrow=True,
            _maximum_depth=depth,
            allow_native_float64=True,
            _checked_bounds=lookup,
        )
        assert actual == expected


def test_native_float64_finite_admission_is_preserved():
    import numpy as np

    for number in (np.float64(1.2), np.float64("nan")):
        report, value = projection({"value": number})
        expected = codec._exclusive_canonical_bytes(value, max_bytes=8192)
        with ownership.native_report_scope():
            cap = capability(report, value)
            assert (
                codec._exclusive_canonical_bytes(value, max_bytes=8192, _checked_projection=cap)
                == expected
            )


def test_projection_capability_requires_exact_root_scope_thread_and_single_use():
    report, value = projection({"quantity": 0})
    assert ownership.checked_native_projection(None, report, value) is None
    with ownership.native_report_scope():
        cap = capability(report, value)
        assert ownership.checked_projection_bounds(cap, deepcopy(value)) is None
        assert ownership.checked_projection_bounds(cap, value) is None
    with ownership.native_report_scope():
        cap = capability(report, value)
        lookup = ownership.checked_projection_bounds(cap, value)
        assert lookup(report["ledger"][0]) is not None
        assert lookup(deepcopy(report["ledger"][0])) is None
        assert ownership.checked_projection_bounds(cap, value) is None
        with patch.object(ownership, "get_ident", return_value=-1):
            assert lookup(report["ledger"][0]) is None
    assert lookup(report["ledger"][0]) is None
    with ownership.native_report_scope():
        cap = capability(report, value)
    assert ownership.checked_projection_bounds(cap, value) is None
    assert ownership.checked_projection_bounds({"projection": value}, value) is None


def test_current_bad_native_financial_rows_remain_fully_audited():
    from test_capacity_allocation_native import capacity_policy, rows, sealed_features
    from test_portfolio_constraint_evidence import frozen_features
    from test_vectorbt_portfolio import snapshot

    from research.connectors import vectorbt_portfolio as native
    from research.portfolio_constraint_evidence import assess
    from research.portfolio_execution_features import _PreparedPortfolioFeatures

    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))
    features = frozen_features(current)
    with _PreparedPortfolioFeatures(features) as prepared:
        with ownership.native_report_scope():
            report = native.evaluate(current, snapshot(), 1000)
            report["ledger"][0]["quantity"] += 1
            expected = assess(report, features)
            assert expected["status"] == "invalid"
            assert prepared.assess(report, features) == expected
