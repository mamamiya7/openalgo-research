"""Reusable setup dates describe sessions while intraday evidence retains instants."""

from copy import deepcopy

import pytest

from services.research_chosen_setups import _snapshot
from services.research_decisions import IDENTITY


def member(first, last):
    return {
        **dict.fromkeys(IDENTITY, "identity"),
        **dict.fromkeys(
            (
                "report_job_id",
                "report_result_artifact",
                "analysis_job_id",
                "analysis_artifact",
                "trial_number",
            )
        ),
        "name": "Recorded rules",
        "report_context": {
            "inputs_artifact": "inputs",
            "dates": {"from": "2026-01-05", "to": "2026-01-27"},
            "evaluation_basis": {
                "status": "verified",
                "period": {"from": first, "to": last},
                "comparison": {"currency": "INR"},
            },
        },
    }


@pytest.mark.parametrize(
    "first,last",
    [
        ("2026-01-05T09:16:00+05:30", "2026-01-27T10:29:00+05:30"),
        ("2026-01-05", "2026-01-27"),
    ],
)
def test_snapshot_dates_are_sessions_without_rewriting_evidence(first, last):
    current = member(first, last)
    original = deepcopy(current)
    output = _snapshot(current, {"capital": 10000, "strategies": []})
    assert output["dates"] == {"from": "2026-01-05", "to": "2026-01-27"}
    assert current == original


@pytest.mark.parametrize("first", ["2026-01-05T00:00:00Z", "2026-01-05T09:16:00", "not-a-date"])
def test_unknown_exchange_timestamp_is_not_silently_truncated(first):
    with pytest.raises(ValueError):
        _snapshot(member(first, "2026-01-27"), {"capital": 10000, "strategies": []})
