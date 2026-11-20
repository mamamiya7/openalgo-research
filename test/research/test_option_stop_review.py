"""A model stop is an auditable per-option judgment, separate from exhaustion."""

from copy import deepcopy

import pytest

from services import research_stop_review as review
from services.agent import research_decision as decision


def evidence(count=9):
    return {
        "diagnosis": {"permitted_remedies": ["temporal_stability"]},
        "experiment_options": [
            {
                "id": f"candidate-{i}",
                "remedy": "temporal_stability",
                "change": {"hold_sessions": i + 20},
            }
            for i in range(1, count + 1)
        ],
        "experiment_frontier": {
            "version": "research-checked-frontier-v1",
            "id": "immutable-frontier",
            "feedback": {"complete": True, "checked_branches": 1},
            "branches": [
                {
                    "job_id": "saved-native-account",
                    "scorecard": {
                        "checks": [
                            {
                                "key": "neighboring_settings",
                                "passed": False,
                                "facts": {"tested": 4, "passing": 0},
                            },
                            {
                                "key": "sample_and_distribution",
                                "passed": True,
                                "facts": {"closed_trades": 35},
                            },
                        ]
                    },
                }
            ],
        },
    }


def row(number):
    return {
        "option": number,
        "evidence_ref": "b1/neighboring_settings",
        "reason": f"The {number + 20}-session alternative needs support absent from the recorded neighborhood; this is a skip judgment, not proof it fails.",
    }


def stop(rows):
    return {
        "remedy": "stop",
        "candidate_id": None,
        "reason": "The remaining complete options lack sufficient supported mechanisms.",
        "option_reviews": rows,
    }


def test_blanket_family_stop_is_rejected_even_with_a_plausible_summary():
    with pytest.raises(review.IncompleteStopReview, match="each remaining option"):
        decision.validate_decision(
            {k: v for k, v in stop([]).items() if k != "option_reviews"}, evidence()
        )


def test_nine_options_require_two_charged_reviews_and_complete_coverage():
    value = evidence()
    value["stop_review_context"] = review.context(value)
    first = decision.validate_decision(stop([row(i) for i in range(1, 7)]), value)
    assert first["stop_review"]["complete"] is False
    assert first["stop_review"]["unreviewed_options"] == [7, 8, 9]
    with pytest.raises(review.IncompleteStopReview, match="every remaining option"):
        review.validate_complete(value, first["stop_review"])
    value["stop_review_context"] = review.context(value, first["stop_review"])
    final = decision.validate_decision(stop([row(i) for i in (7, 8, 9)]), value)
    assert len(review.validate_complete(value, final["stop_review"])["reviews"]) == 9
    assert final["stop_review"]["complete"] is True
    assert final["stop_review"]["unreviewed_options"] == []


@pytest.mark.parametrize(
    "bad", ["duplicate", "unknown", "passing", "generic", "missing", "too_many", "outside"]
)
def test_incomplete_or_unverifiable_option_justifications_are_rejected(bad):
    rows = [row(1), row(2)]
    if bad == "duplicate":
        rows[1]["option"] = 1
    if bad == "unknown":
        rows[0]["evidence_ref"] = "b2/higher_costs"
    if bad == "passing":
        rows[0]["evidence_ref"] = "b1/sample_and_distribution"
    if bad == "generic":
        rows[1]["reason"] = rows[0]["reason"]
    if bad == "missing":
        rows[0].pop("reason")
    if bad == "too_many":
        rows = [row(i) for i in range(1, 8)]
    if bad == "outside":
        rows[0]["option"] = 10
    with pytest.raises(review.IncompleteStopReview):
        decision.validate_decision(stop(rows), evidence())


def test_a_changed_complete_frontier_invalidates_previously_recorded_skips():
    value = evidence(2)
    completed = decision.validate_decision(stop([row(1), row(2)]), value)["stop_review"]
    changed = deepcopy(value)
    changed["experiment_options"][0]["id"] = "different-complete-capital-and-sizing"
    assert review.context(changed, completed)["recorded_reviews"] == []
    with pytest.raises(review.IncompleteStopReview, match="stale"):
        review.validate_complete(changed, completed)


def test_partial_review_can_choose_a_test_instead_of_manufacturing_a_stop():
    value = evidence()
    first = decision.validate_decision(stop([row(1)]), value)["stop_review"]
    value["stop_review_context"] = review.context(value, first)
    chosen = {
        "remedy": "temporal_stability",
        "candidate_id": "candidate-9",
        "reason": "A distinct horizon can still resolve an unsupported assumption.",
    }
    assert decision.validate_decision(chosen, value) == chosen
    assert decision.MAX_OUTPUT_TOKENS == 600 and decision.MAX_RESPONSE_CHARS == 4000


def test_saved_review_cannot_relabel_an_unreviewed_complete_configuration():
    value = evidence(2)
    completed = decision.validate_decision(stop([row(1), row(2)]), value)["stop_review"]
    completed["option_ids"][0] = "another-capital-and-sizing-configuration"
    with pytest.raises(review.IncompleteStopReview, match="every exact option"):
        review.validate_complete(value, completed)


def test_stop_execution_cannot_bypass_the_review_guard(monkeypatch):
    from services import research_supervisor

    current = {**evidence(2), "status": "awaiting_plan"}
    monkeypatch.setattr(research_supervisor, "status", lambda *a, **kw: current)
    monkeypatch.setattr(
        research_supervisor, "propose", lambda *a, **kw: pytest.fail("No trade-setting proposal")
    )
    partial = decision.validate_decision(stop([row(1)]), current)
    with pytest.raises(review.IncompleteStopReview):
        decision.execute_plan(
            None, "owner", "supervisor", conversation_id="saved", decision=partial
        )
    complete = decision.validate_decision(stop([row(1), row(2)]), current)
    assert decision.execute_plan(
        None, "owner", "supervisor", conversation_id="saved", decision=complete
    )["explicit_stop"]
