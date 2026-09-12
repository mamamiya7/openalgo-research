"""Financial presentation boundaries for pinned reports, without engine or I/O."""

import copy
import json

import pytest

from research.comparison import MAX_METRICS, build_comparison
from research.evaluation_basis import IDENTITIES, VERSION


def member(identity="a", **summary):
    basis = {
        "version": VERSION,
        "status": "verified",
        **{key: key + "-same" for key in IDENTITIES},
        "evidence_id": "same-evidence",
        "comparison": {
            "id": "same-conditions",
            "period": "selection",
            "currency": "INR",
            "capital": 100000,
            "execution": {"engine": "vectorbt"},
            "costs": [],
        },
    }
    return {
        "id": identity,
        "name": "Saved " + identity,
        "report_context": {"evaluation_basis": basis},
        "summary": {
            "net_return_pct": 4.5,
            "net_pnl": 4500,
            "initial_capital": 100000,
            "closed_trades": 21,
            "max_drawdown_pct": 2.25,
            **summary,
        },
        "analysis": {
            "version": "v2",
            "metrics": {"sharpe": 1.2},
            "unavailable": {},
            "catalog": [
                {
                    "key": "sharpe",
                    "label": "Sharpe",
                    "format": "number",
                    "description": "Daily marked returns",
                    "source": "Account",
                }
            ],
        },
        "cumulative": {
            "id": "account-cumulative",
            "status": "available",
            "figure": {
                "data": [{"x": ["2026-01-05", "2026-01-07"], "y": [-1.5, 4.5]}],
                "layout": {},
            },
        },
    }


def metric(result, key):
    return next(row for row in result["metrics"] if row["key"] == key)


def test_exact_returns_reference_deltas_and_sampled_dates_are_preserved_without_mutation():
    a, b = member(), member("b", net_return_pct=-3.25, closed_trades=17)
    b["cumulative"]["figure"]["data"][0] = {
        "x": ["2026-01-05", "2026-01-06", "2026-01-07"],
        "y": [2.5, None, -3.25],
    }
    original = copy.deepcopy([a, b])
    result = build_comparison([a, b], "b")
    assert result["compatible"] is True
    assert metric(result, "net_return_pct")["values"] == {"a": 4.5, "b": -3.25}
    # Difference is percentage points, not a percent improvement over the reference.
    assert metric(result, "net_return_pct")["deltas"] == {"a": 7.75, "b": 0}
    assert metric(result, "closed_trades")["deltas"] == {"a": 4, "b": 0}
    for i, saved in enumerate(original):
        trace = result["cumulative"]["figure"]["data"][i]
        assert trace["x"] == saved["cumulative"]["figure"]["data"][0]["x"]
        assert trace["y"] == saved["cumulative"]["figure"]["data"][0]["y"]
        assert trace["connectgaps"] is False
    assert [a, b] == original
    result["cumulative"]["figure"]["data"][0]["y"][0] = 0
    assert [a, b] == original


@pytest.mark.parametrize("field", [*IDENTITIES, "period", "capital", "execution", "costs"])
def test_any_incompatible_member_blocks_all_group_deltas_and_overlay(field):
    a, b, c = member(), member("b"), member("c")
    basis = c["report_context"]["evaluation_basis"]
    if field in IDENTITIES:
        basis[field] = "other"
    else:
        basis["comparison"][field] = "other"
    result = build_comparison([a, b, c], "a")
    assert result["compatible"] is False
    assert result["differences"] == [{"member_id": "c", "codes": [field.removesuffix("_id")]}]
    assert all(row["deltas"] is None for row in result["metrics"])
    assert result["cumulative"]["status"] == "unavailable"
    assert metric(result, "net_return_pct")["values"]["c"] == 4.5


def test_known_currency_mismatch_rejects_and_missing_currency_suppresses_all_money():
    a, b = member(), member("b")
    b["report_context"]["evaluation_basis"]["comparison"]["currency"] = "USD"
    with pytest.raises(ValueError, match="same currency"):
        build_comparison([a, b], "a")
    b["report_context"]["evaluation_basis"] = {"version": VERSION, "status": "unverified"}
    result = build_comparison([a, b], "a")
    assert result["currency"] is None
    assert not result["compatible"]
    assert metric(result, "net_pnl")["values"] == {"a": None, "b": None}
    assert metric(result, "net_return_pct")["values"] == {"a": 4.5, "b": 4.5}
    assert result["cumulative"]["status"] == "unavailable"


@pytest.mark.parametrize("change", ["version", "description", "source", "format"])
def test_native_analysis_definitions_and_versions_must_match_for_metric_deltas(change):
    a, b = member(), member("b")
    if change == "version":
        b["analysis"]["version"] = "v1"
    else:
        b["analysis"]["catalog"][0][change] = "percent" if change == "format" else "Different"
    result = build_comparison([a, b], "a")
    assert result["compatible"]
    assert metric(result, "net_return_pct")["deltas"] == {"a": 0, "b": 0}
    assert metric(result, "sharpe")["deltas"] is None
    assert metric(result, "sharpe")["values"]["a"] == 1.2
    assert metric(result, "sharpe")["values"]["b"] == (1.2 if change == "version" else None)


def test_undefined_original_metrics_are_not_imputed_from_curves_and_are_json_safe():
    a, b = member(profit_factor=float("inf")), member("b", net_return_pct=True)
    b["analysis"]["metrics"]["sharpe"] = None
    b["analysis"]["unavailable"]["sharpe"] = "Not enough sessions."
    result = build_comparison([a, b], "a")
    assert metric(result, "profit_factor")["values"]["a"] is None
    assert metric(result, "net_return_pct")["values"]["b"] is None
    assert metric(result, "sharpe")["deltas"]["b"] is None
    assert metric(result, "sharpe")["unavailable"]["b"] == "Not enough sessions."
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "change", ["missing", "too_many", "nan", "empty", "extra_trace", "null_trace", "long_date"]
)
def test_missing_or_invalid_chart_never_becomes_a_synthetic_return_series(change):
    a, b = member(), member("b")
    data = b["cumulative"]["figure"]["data"]
    if change == "missing":
        b["cumulative"] = None
    elif change == "too_many":
        data[0].update(x=["2026-01-05"] * 1201, y=[1] * 1201)
    elif change == "nan":
        data[0]["y"][0] = float("nan")
    elif change == "empty":
        data[0].update(x=[], y=[])
    elif change == "null_trace":
        data[0] = None
    elif change == "long_date":
        data[0]["x"][0] = "2" * 65
    else:
        data.append(copy.deepcopy(data[0]))
    result = build_comparison([a, b], "a")
    assert result["compatible"]
    assert result["cumulative"]["status"] == "unavailable"
    assert metric(result, "net_return_pct")["values"] == {"a": 4.5, "b": 4.5}


def test_membership_and_catalog_are_bounded_and_ambiguous_catalog_rejected():
    for members, reference in [
        ([member()], "a"),
        ([member()] * 2, "a"),
        ([member(str(i)) for i in range(5)], "0"),
        ([member(), member("b")], "missing"),
    ]:
        with pytest.raises(ValueError):
            build_comparison(members, reference)
    a, b = member(), member("b")
    a["analysis"]["catalog"] *= 2
    with pytest.raises(ValueError, match="invalid definition"):
        build_comparison([a, b], "a")
    a["analysis"]["catalog"] *= MAX_METRICS
    with pytest.raises(ValueError, match="supported limit"):
        build_comparison([a, b], "a")
