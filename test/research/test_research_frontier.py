"""Every checked branch contributes legal alternatives before a global stop."""

from copy import deepcopy

import pytest

from research.engine import config_defaults
from research.execution_policy import normalize_policy
from research.report_contract import fingerprint, settings_identity
from services import research_frontier as frontier
from services.research_experiments import VERSION as CATALOG_VERSION


def settings(**changes):
    config = {
        **config_defaults(),
        "initial_capital": 1000000,
        "hold_sessions": 71,
        "trailing_enabled": True,
        "trailing_pct": 6.25,
        **changes,
    }
    return [
        {
            "id": "scan",
            "name": "Saved scan",
            "allocation_pct": 100,
            "config": config,
            "execution_policy": normalize_policy(
                {
                    "version": "research-execution-aware-v5",
                    "allocation_mode": "original",
                    "capacity_mode": "prior20_broker_reported",
                    "correlation_mode": "prior60_positive_080",
                }
            ),
        }
    ]


def branch(number, source, changes=(), *, remedy="temporal_stability", missing=False, stop=None):
    artifact = f"{number:064x}"
    options = []
    for change in changes:
        candidate = deepcopy(source)
        candidate[0]["config"].update(change)
        option_id = fingerprint(
            {
                "version": CATALOG_VERSION,
                "source_artifact": artifact,
                "remedy": remedy,
                "settings_id": settings_identity(candidate),
            }
        )
        options.append(
            {
                "id": option_id,
                "remedy": remedy,
                "settings": candidate,
                "reason": "Test the measured failure mechanism",
                "change": change,
                "expected_effect": "Prediction, not a tested result",
            }
        )
    return {
        "job_id": f"job{number}",
        "result_artifact": artifact,
        "report": {"strategies": deepcopy(source), "portfolio": {"engine": "vectorbt"}},
        "scorecard": {
            "eligible": True,
            "failed_gate_keys": ["neighboring_settings"],
            "checks": [
                {
                    "key": "neighboring_settings",
                    "passed": False,
                    "facts": {"passing": 2, "tested": 4},
                }
            ],
        },
        "catalog": {
            "options": options,
            "feedback": {"evidence_status": {"missing_required": missing}},
            "unavailable": [],
            "stop_reason": stop,
        },
    }


def test_all_six_real_shaped_alternatives_survive_across_three_checked_branches():
    funded = settings()
    off = settings(trailing_enabled=False)
    current = settings(trailing_enabled=False, hold_sessions=23)
    branches = [
        branch(1, funded, [{"trailing_pct": p} for p in (4.69, 9.4, 10.97)]),
        branch(2, off, [{"hold_sessions": n} for n in (35, 47)]),
        branch(3, current, [{"hold_sessions": 83}]),
    ]
    original = deepcopy(branches)
    result = frontier.build(branches, remaining_accounts=70)
    assert len(result["options"]) == 6 and result["stop_reason"] is None
    assert {o["origin_job_id"] for o in result["options"]} == {"job1", "job2", "job3"}
    assert all(o["required_accounts"] == 14 for o in result["options"])
    assert all(
        o["origin_result_artifact"] in {b["result_artifact"] for b in branches}
        for o in result["options"]
    )
    assert all(
        b["scorecard"]["checks"][0]["facts"] == {"passing": 2, "tested": 4}
        for b in result["branches"]
    )
    assert result["feedback"]["complete"] is True
    assert branches == original


def test_effective_config_dedup_retains_all_origins_without_profit_ranking():
    first = branch(1, settings(trailing_enabled=False), [{"hold_sessions": 35, "trailing_pct": 1}])
    second = branch(
        2,
        settings(trailing_enabled=False),
        [{"hold_sessions": 35, "trailing_pct": 99}],
        remedy="lower_turnover",
    )
    second["scorecard"]["summary"] = {"net_return_pct": 10000}
    result = frontier.build([first, second], remaining_accounts=28)
    assert len(result["options"]) == 1
    chosen = result["options"][0]
    assert chosen["id"] == first["catalog"]["options"][0]["id"]
    assert chosen["origin_job_id"] == "job1"
    assert len(chosen["origin_refs"]) == 2
    assert {r["remedy"] for r in chosen["origin_refs"]} == {"temporal_stability", "lower_turnover"}
    assert result["feedback"]["equivalent_options"] == 1


def test_all_actual_checked_results_exclude_equivalent_performance_retests():
    proposed = branch(
        1, settings(trailing_enabled=False), [{"hold_sessions": 35, "trailing_pct": 1}]
    )
    already_measured = branch(
        2, settings(trailing_enabled=False, hold_sessions=35, trailing_pct=99)
    )
    result = frontier.build([proposed, already_measured], remaining_accounts=100)
    assert result["options"] == []
    assert result["feedback"]["already_tested_options"] == 1
    assert result["unavailable"][0]["reason_code"] == "already_tested_configuration"


def test_active_trailing_risk_and_capital_remain_complete_configuration_distinctions():
    first = branch(1, settings(), [{"hold_sessions": 35, "trailing_pct": 4}])
    second = branch(2, settings(), [{"hold_sessions": 35, "trailing_pct": 9}])
    third = branch(3, settings(initial_capital=2000000), [{"hold_sessions": 35, "trailing_pct": 4}])
    result = frontier.build([first, second, third], remaining_accounts=100)
    assert len(result["options"]) == 3


def test_native_funding_action_is_distinct_even_when_input_settings_have_a_checked_result():
    funding = branch(1, settings(), [{}], remedy="funding_and_size")
    performance = branch(2, settings(hold_sessions=23), [{"hold_sessions": 71}])
    result = frontier.build([funding, performance], remaining_accounts=17)
    assert len(result["options"]) == 1
    assert result["options"][0]["remedy"] == "funding_and_size"
    assert result["options"][0]["required_accounts"] == 17


@pytest.mark.parametrize("remaining,expected", [(13, 0), (14, 1), (16, 1), (17, 2)])
def test_affordability_requires_the_full_unchanged_14_or17_account_reserve(remaining, expected):
    performance = branch(1, settings(), [{"hold_sessions": 35}])
    funding = branch(2, settings(), [{}], remedy="funding_and_size")
    result = frontier.build([performance, funding], remaining_accounts=remaining)
    assert len(result["options"]) == expected
    assert all(o["required_accounts"] <= remaining for o in result["options"])
    if remaining == 13:
        assert "complete 14-account experiment" in result["stop_reason"]
        assert "validation was not reduced" in result["stop_reason"]


def test_nonexploratory_frozen_validation_reserve_is_not_lowered():
    result = frontier.build(
        [branch(1, settings(), [{"hold_sessions": 35}])],
        remaining_accounts=23,
        validation_mode="historical_only",
    )
    assert result["options"] == []
    assert result["unavailable"][0]["required_accounts"] == 24


def test_one_missing_or_locked_branch_does_not_hide_another_usable_branch():
    blocked = branch(
        1,
        settings(),
        [{"hold_sessions": 35}],
        missing=True,
        stop="Required sizing evidence missing",
    )
    locked = branch(2, settings(), stop="No option respects fixed holding")
    locked["catalog"]["unavailable"] = [
        {"reason_code": "fixed_settings", "reason": "Holding is locked"}
    ]
    usable = branch(3, settings(), [{"hold_sessions": 47}])
    result = frontier.build([blocked, locked, usable], remaining_accounts=14)
    assert len(result["options"]) == 1 and result["options"][0]["origin_job_id"] == "job3"
    assert result["branches"][0]["missing_required_evidence"] is True
    assert result["branches"][1]["unavailable"][0]["reason_code"] == "fixed_settings"
    assert result["stop_reason"] is None


def test_all_missing_branches_have_a_truthful_evidence_stop():
    result = frontier.build([branch(1, settings(), missing=True)], remaining_accounts=100)
    assert "lack required execution or sizing evidence" in result["stop_reason"]
    assert "fixed settings" not in result["stop_reason"]


def test_origin_identity_tampering_and_oversized_review_fail_explicitly():
    source = branch(1, settings(), [{"hold_sessions": 35}])
    source["catalog"]["options"][0]["id"] = "f" * 64
    with pytest.raises(ValueError, match="exact origin catalogue"):
        frontier.build([source], remaining_accounts=100)
    with pytest.raises(ValueError, match="review is incomplete"):
        frontier.build([branch(n, settings()) for n in range(26)], remaining_accounts=100)
    too_many = branch(1, settings(), [{"hold_sessions": n} for n in range(1, 10)])
    with pytest.raises(ValueError, match="review is incomplete"):
        frontier.build([too_many], remaining_accounts=100)


def test_all200_bounded_options_are_returned_without_silent_truncation():
    branches = [
        branch(n, settings(), [{"hold_sessions": 1 + n * 8 + i} for i in range(8)])
        for n in range(25)
    ]
    # The source account already measures71; leave that known coordinate out.
    for b in branches:
        for c in b["catalog"]["options"]:
            if c["settings"][0]["config"]["hold_sessions"] == 71:
                c["settings"][0]["config"]["hold_sessions"] = 250
                c["id"] = fingerprint(
                    {
                        "version": CATALOG_VERSION,
                        "source_artifact": b["result_artifact"],
                        "remedy": c["remedy"],
                        "settings_id": settings_identity(c["settings"]),
                    }
                )
    result = frontier.build(branches, remaining_accounts=512)
    assert len(result["options"]) == 200
    assert result["feedback"]["supplied_options"] == 200 and result["feedback"]["complete"]
