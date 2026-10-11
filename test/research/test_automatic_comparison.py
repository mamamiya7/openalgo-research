"""Saved final curves never interpolate or compare incompatible evidence."""

from copy import deepcopy

import pytest

from research.automatic_comparison import MAX_POINTS, build
from research.evaluation_basis import IDENTITIES, VERSION


def report(count=3):
    return {
        "evaluation_basis": {
            "version": VERSION,
            "status": "verified",
            "evidence_id": "evidence",
            **{key: key for key in IDENTITIES},
            "period": {"kind": "evaluation", "from": "2026-08-01", "to": "2026-09-01"},
            "comparison": {
                "id": "comparison",
                "period": "evaluation",
                "capital": 10000,
                "currency": "INR",
                "execution": {"engine": "vectorbt"},
                "costs": [],
            },
        },
        "equity_curve": [
            {"timestamp": f"bar-{index}", "equity": 10000 + index} for index in range(count)
        ],
    }


def test_actual_equity_returns_same_basis_and_immutable_inputs():
    original, selected = report(), report()
    selected["equity_curve"][-1]["equity"] = 10500
    before = deepcopy((original, selected))
    output = build(original, selected)
    assert output["status"] == "available"
    assert output["chart"]["figure"]["data"][1]["y"][-1] == pytest.approx(5)
    assert output["evidence_id"] == "evidence"
    assert (original, selected) == before


@pytest.mark.parametrize(
    "key", [*IDENTITIES, "costs", "capital", "currency", "period", "execution"]
)
def test_incompatible_basis_has_no_comparison_chart(key):
    original, selected = report(), report()
    location = (
        selected["evaluation_basis"]
        if key in IDENTITIES
        else selected["evaluation_basis"]["comparison"]
    )
    location[key] = "different"
    output = build(original, selected)
    assert output["status"] == "unavailable" and "chart" not in output


def test_different_times_and_nonfinite_equity_are_not_filled():
    original, selected = report(), report()
    selected["equity_curve"][0]["timestamp"] = "other"
    assert build(original, selected)["status"] == "unavailable"
    selected = report()
    selected["equity_curve"][0]["equity"] = float("nan")
    assert build(original, selected)["status"] == "unavailable"


def test_bounded_shared_sampling_preserves_first_and_last_recorded_points():
    original = report(MAX_POINTS + 800)
    output = build(original, deepcopy(original))
    assert output["observations"] == MAX_POINTS + 800
    assert output["displayed"] == MAX_POINTS
    series = output["chart"]["figure"]["data"]
    assert series[0]["x"] == series[1]["x"]
    assert series[0]["x"][0] == "bar-0" and series[0]["x"][-1] == f"bar-{MAX_POINTS + 799}"
    assert build(report(1), report(1))["displayed"] == 1


def test_legacy_report_without_recorded_basis_does_not_invent_a_curve():
    assert build({}, report())["status"] == "unavailable"
