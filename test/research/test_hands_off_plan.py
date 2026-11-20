"""Authority, complete-domain identity and economically inactive control checks."""

from copy import deepcopy

import pytest

from research.engine import config_defaults
from research.hands_off_plan import VERSION, build_context, compile_plan


def context(**changes):
    settings = [
        {"id": "scan", "name": "Original scan", "allocation_pct": 100, "config": config_defaults()}
    ]
    values = {
        "settings": settings,
        "domains": {
            "scan": {
                "hold_sessions": [2, 4, 6, 8],
                "stop_pct": [2, 5, 10],
                "order_size_pct": [5, 10, 15],
            }
        },
        "signal_id": "a" * 64,
        "style": "positional",
        "risk_limits": {},
    }
    values.update(changes)
    return build_context(**values)


def proposal(**changes):
    value = {
        "version": VERSION,
        "action": "search",
        "method": "holding_and_exits",
        "hypothesis": "Longer supported holding may reduce observed turnover",
        "reason": "Measured costs consume the baseline return",
        "expected_evidence": "Compare after-cost return, occupancy and unchanged validation",
        "stop_rule": "Stop if changed holding does not change execution or checks remain unsupported",
        "domains": {"scan": {"hold_sessions": {"min": 2, "max": 8, "step": 2}}},
    }
    value.update(changes)
    return value


def test_compiled_search_preserves_complete_baseline_and_native_budget():
    original = context()
    before = deepcopy(original)
    result = compile_plan(proposal(), original)
    assert result["budget"] == {"proposals": 408, "max_simulations": 512}
    assert result["strategies"][0]["config"] == original["settings"][0]["config"]
    assert result["strategies"][0]["search"]["hold_sessions"] == {"min": 2, "max": 8, "step": 2}
    assert original == before


def test_ai_can_propose_a_coupled_exit_and_sizing_experiment():
    result = compile_plan(
        proposal(
            method="joint_trading",
            domains={
                "scan": {
                    "hold_sessions": {"min": 2, "max": 6, "step": 2},
                    "order_size_pct": {"min": 5, "max": 15, "step": 5},
                }
            },
        ),
        context(),
    )
    assert set(result["strategies"][0]["search"]) == {"hold_sessions", "order_size_pct"}


def test_fixed_new_stop_can_be_coupled_with_a_varying_holding_domain():
    result = compile_plan(
        proposal(
            domains={
                "scan": {
                    "hold_sessions": {"min": 2, "max": 6, "step": 2},
                    "stop_pct": {"min": 2, "max": 2, "step": 1},
                }
            }
        ),
        context(),
    )
    assert result["domains"]["scan"]["stop_pct"]["min"] == 2


@pytest.mark.parametrize(
    "domains",
    [
        {"other": {"hold_sessions": {"min": 2, "max": 8, "step": 2}}},
        {"scan": {"cost_bps": {"min": 0, "max": 10, "step": 5}}},
        {"scan": {"hold_sessions": {"min": 2, "max": 7, "step": 1}}},
        {"scan": {"hold_sessions": {"min": 2, "max": 8, "step": True}}},
        {"scan": {"hold_sessions": {"min": 2, "max": float("nan"), "step": 2}}},
        {"scan": {"hold_sessions": {"min": 2, "max": 2, "step": 1}}},
    ],
)
def test_malicious_or_ineffective_model_domains_fail_closed(domains):
    with pytest.raises(ValueError):
        compile_plan(proposal(domains=domains), context())


def test_user_lock_and_changed_context_cannot_be_bypassed():
    with pytest.raises(ValueError, match="unlock"):
        context(locked_fields={"scan": ["hold_sessions"]})
    value = context()
    value["settings"][0]["config"]["cost_bps"] = 0
    with pytest.raises(ValueError, match="context changed"):
        compile_plan(proposal(), value)


def test_search_identity_deduplicates_method_names_and_complete_domains():
    original = context()
    first = compile_plan(proposal(), original)
    previous = [
        {
            "base_id": first["base_id"],
            "search_identity": first["search_identity"],
            "exhausted": True,
        }
    ]
    with pytest.raises(ValueError, match="exhausted"):
        compile_plan(proposal(method="joint_trading"), context(prior_domains=previous))
    changed = deepcopy(original["settings"])
    changed[0]["config"]["order_size_pct"] = 15
    second = compile_plan(proposal(), context(settings=changed, prior_domains=previous))
    assert second["search_identity"] != first["search_identity"]


def test_saved_no_effect_domain_blocks_a_narrower_equivalent_search():
    old = compile_plan(proposal(), context())
    previous = [{"base_id": old["base_id"], "no_effect_proven": True, "domains": old["domains"]}]
    with pytest.raises(ValueError, match="no effect"):
        compile_plan(
            proposal(domains={"scan": {"hold_sessions": {"min": 4, "max": 6, "step": 2}}}),
            context(prior_domains=previous),
        )


def test_a_previous_plan_does_not_exhaust_its_unseen_configurations():
    old = compile_plan(proposal(), context())
    previous = [
        {
            "base_id": old["base_id"],
            "search_identity": old["search_identity"],
            "phase": "completed",
            "no_effect": True,
            "domains": old["domains"],
        }
    ]
    result = compile_plan(proposal(), context(prior_domains=previous))
    assert result["search_identity"] == old["search_identity"]


def test_reasoned_stop_cannot_embed_an_executable_search():
    result = compile_plan(proposal(action="stop", method=None, domains={}), context())
    assert result["action"] == "stop" and "strategies" not in result
    with pytest.raises(ValueError, match="cannot execute"):
        compile_plan(proposal(action="stop", method=None), context())


def test_explicit_defer_is_block_scoped_and_cannot_embed_a_search():
    result = compile_plan(proposal(action="defer", method=None, domains={}), context())
    assert result["action"] == "defer"
    assert result["decision_scope"] == "training_block"
    assert "strategies" not in result and "search_identity" not in result
    assert (
        compile_plan(proposal(action="stop", method=None, domains={}), context())["decision_scope"]
        == "whole_study"
    )
    for change in (
        {"domains": {"scan": {"hold_sessions": {"min": 2, "max": 8, "step": 2}}}},
        {"method": "holding_and_exits"},
    ):
        with pytest.raises(ValueError, match="cannot execute"):
            compile_plan({**proposal(action="defer", method=None, domains={}), **change}, context())


def test_deferred_native_continuation_does_not_claim_a_domain_was_exhausted():
    original = context()
    deferred = compile_plan(proposal(action="defer", method=None, domains={}), original)
    proposed = compile_plan(proposal(), context(prior_domains=[deferred]))
    assert proposed["search_identity"] == compile_plan(proposal(), original)["search_identity"]


def test_disabled_or_inactive_controls_are_not_available_to_ai():
    with pytest.raises(ValueError, match="Disabled trailing"):
        context(domains={"scan": {"trailing_pct": [1, 2, 3]}})
    with pytest.raises(ValueError, match="Inactive minute"):
        context(domains={"scan": {"hold_minutes": [5, 10]}})


def test_unknown_plan_fields_cannot_change_validation_or_submit_code():
    for key, value in (
        ("validation", {"minimum_trades": 1}),
        ("code", "execute"),
        ("engine", "openalgo"),
    ):
        with pytest.raises(ValueError, match="exact versioned"):
            compile_plan({**proposal(), key: value}, context())
