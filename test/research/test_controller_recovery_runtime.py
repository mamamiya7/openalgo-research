"""Checkpoint-only controller recovery, feedback and accounting; no provider/engine."""

# ruff: noqa: F811 -- shared pure checkpoint fixture
from copy import deepcopy

import pytest
from test_hands_off_runtime import case, invoke, proposal  # noqa: F401

from research import hands_off_runtime as runtime
from research import planning_recovery as recovery
from research.hands_off_plan import compile_plan
from research.report_contract import fingerprint, settings_identity
from services.agent.research_planning import CompletedPlanRejection


def controller(callback, *, legacy=None):
    callback.controller_update = {"id": "authorized-controller-update", "job_id": "j" * 32}
    if legacy:
        callback.controller_update["known_rejection"] = legacy
    callback.feedback_reference = lambda: {"checkpoint": "a" * 64, "job_id": "j" * 32}
    return callback


def normalize_accounts(case):
    current = case[1]
    candidates = {}
    for row in current["candidates"].values():
        row["config_id"] = settings_identity(row["strategies"])
        candidates[row["config_id"]] = row
    current["candidates"] = candidates
    for trial in current["trials"]:
        row = next(c for c in candidates.values() if c["trial_number"] == trial["number"])
        trial.update(config_id=row["config_id"], state="complete", reused=False)


def known_rejection(evidence):
    return CompletedPlanRejection(
        recovery.rejection_receipt(
            "completed bad domain",
            proposal(),
            evidence["planning_context"],
            reason_code="invalid_plan",
            error="AI proposed a value outside the frozen data/search domain",
            model_id=1,
            model="configured/model",
            usage={"completion_calls": 1},
        )
    )


def legacy_pending(case):
    """Ten immutable completed protocol records and one known lost-response slot."""
    captures = []

    def interrupted(evidence):
        captures.append(deepcopy(evidence))
        raise InterruptedError("Old provider response was not retained")

    with pytest.raises(InterruptedError):
        invoke(case, interrupted)
    book = case[0]["hands_off_planning"]
    row = deepcopy(book["records"]["fit1:2"])
    for fit in ("fit2", "fit3", "search", "search_seed1", "search_seed2"):
        for index in (2, 7):
            key = f"{fit}:{index}"
            book["records"][key] = {
                **deepcopy(row),
                "fit": fit,
                "index": index,
                "phase": "completed",
                "request_id": fingerprint({"fixture": key}),
                "plan": compile_plan(proposal(), captures[0]["planning_context"]),
            }
    book["calls_spent"] = len(book["records"])
    return {
        "boundary_key": "fit1:2",
        "checkpoint_id": "a" * 64,
        "request_id": row["request_id"],
        "error": "AI proposed a value outside the frozen data/search domain",
    }


def add_failed_block(case):
    current = case[1]
    failed = deepcopy(next(iter(current["candidates"].values())))
    failed["strategies"][0]["config"]["hold_sessions"] = 6
    failed.update(trial_number=2, score=-9, config_id=settings_identity(failed["strategies"]))
    failed["summary"].update(net_return_pct=-3.0, max_drawdown_pct=5.0)
    current["candidates"][failed["config_id"]] = failed
    for number in range(2, 7):
        current["trials"].append(
            {
                "number": number,
                "config_id": failed["config_id"],
                "params": {"scan.hold_sessions": 6},
                "value": failed["score"],
                "state": "complete",
                "reused": number != 2,
                "proposal_source": "ai_domain",
            }
        )
    return failed


def test_legacy_known_rejection_recovers_without_rewriting_old_plans_and_replays_zero_calls(case):
    normalize_accounts(case)
    legacy = legacy_pending(case)
    state, current, _ = case
    old_records, old_trials = (
        deepcopy(state["hands_off_planning"]["records"]),
        deepcopy(current["trials"]),
    )
    calls, writes = [], []

    def choose(evidence):
        calls.append(deepcopy(evidence))
        saved = writes[-1]["planning_recovery"]
        assert recovery.accounting(state["hands_off_planning"], saved)["spent"] == 12
        assert saved["corrections"][-1]["phase"] == "requesting"
        assert (
            evidence["rejected_proposals"][0]["provenance"]["source"] == "operator_attested_legacy"
        )
        return compile_plan(proposal(), evidence["planning_context"])

    controller(choose, legacy=legacy)
    record, _ = invoke(case, choose, persist=lambda: writes.append(deepcopy(state)))
    assert record["recovered"] and record["phase"] == "completed"
    assert state["hands_off_planning"]["records"] == old_records
    assert current["trials"] == old_trials
    assert state["hands_off_planning"]["active"] is None
    runtime.verify_book(state["hands_off_planning"], state["planning_recovery"])

    def forbidden(_evidence):
        pytest.fail("Replaying corrected plan contacted provider")

    controller(forbidden, legacy=legacy)
    reopened, _ = invoke(case, forbidden)
    assert reopened == record and len(calls) == 1
    assert (
        recovery.accounting(state["hands_off_planning"], state["planning_recovery"])["spent"] == 12
    )


def test_new_completed_rejection_is_charged_then_corrected_inside_existing_allowance(case):
    normalize_accounts(case)
    calls = []

    def choose(evidence):
        calls.append(deepcopy(evidence))
        if len(calls) == 1:
            raise known_rejection(evidence)
        assert evidence["rejected_proposals"][0]["provenance"]["stream_closed"] is True
        return compile_plan(proposal(), evidence["planning_context"])

    record, _ = invoke(case, controller(choose))
    assert record["recovered"] and len(calls) == 2
    assert case[0]["hands_off_planning"]["calls_spent"] == 1
    assert (
        recovery.accounting(case[0]["hands_off_planning"], case[0]["planning_recovery"])["spent"]
        == 2
    )
    assert record["plan"]["budget"] == {"proposals": 408, "max_simulations": 512}


@pytest.mark.parametrize("stage", ["normal", "correction"])
def test_uncertain_exception_is_charged_and_cannot_trigger_a_blind_retry(case, stage):
    normalize_accounts(case)
    calls = []

    def choose(evidence):
        calls.append(True)
        if stage == "correction" and len(calls) == 1:
            raise known_rejection(evidence)
        raise InterruptedError("Unknown transport outcome")

    with pytest.raises(InterruptedError):
        invoke(case, controller(choose))
    expected = 1 if stage == "normal" else 2
    assert len(calls) == expected

    def forbidden(_):
        pytest.fail("Uncertain request was retried")

    with pytest.raises(ValueError, match="interrupted|blind"):
        invoke(case, controller(forbidden))
    book = case[0]["hands_off_planning"]
    spent = (
        recovery.accounting(book, case[0]["planning_recovery"])["spent"]
        if stage == "correction"
        else book["calls_spent"]
    )
    assert spent == expected


def test_recovered_boundary_then_normal_twelfth_call_preserves_budget_and_failed_trial_feedback(
    case,
):
    normalize_accounts(case)
    legacy = legacy_pending(case)
    first, _ = invoke(
        case, controller(lambda e: compile_plan(proposal(), e["planning_context"]), legacy=legacy)
    )
    assert first["recovered"]
    failed = add_failed_block(case)
    captures = []

    def choose(evidence):
        captures.append(deepcopy(evidence))
        return compile_plan(
            proposal(action="defer", method=None, domains={}), evidence["planning_context"]
        )

    record, _ = invoke(case, controller(choose, legacy=legacy), index=7)
    assert record["plan"]["action"] == "defer"
    evidence = captures[0]
    assert all(c["config_id"] != failed["config_id"] for c in evidence["observed_candidates"])
    block = evidence["feedback"]["blocks"][0]
    assert block["completed_proposals"] == 5 and block["distinct_configurations"] == 1
    assert block["reused_proposals"] == 4 and block["fresh_proposals"] == 1
    assert block["configurations"][0]["config_id"] == failed["config_id"]
    assert block["configurations"][0]["summary"]["net_return_pct"] == -3
    assert block["configurations"][0]["strategies"][0]["config"]["hold_sessions"] == 6
    assert recovery.accounting(case[0]["hands_off_planning"], case[0]["planning_recovery"]) == {
        "initial_calls": 12,
        "correction_calls": 1,
        "spent": 13,
        "remaining_global": 11,
        "remaining_corrections": 1,
    }
    assert case[0]["attempts"] == case[0]["simulations"] == 3


def test_correction_is_verified_before_any_completed_plan_is_persisted(case):
    normalize_accounts(case)
    calls, writes = [], []

    def choose(evidence):
        calls.append(True)
        if len(calls) == 1:
            raise known_rejection(evidence)
        compiled = compile_plan(proposal(), evidence["planning_context"])
        compiled["strategies"][0]["config"]["initial_capital"] *= 2
        return compiled

    with pytest.raises(ValueError, match="frozen-domain validation"):
        invoke(case, controller(choose), persist=lambda: writes.append(deepcopy(case[0])))
    assert case[0]["planning_recovery"]["corrections"][-1]["phase"] == "requesting"
    assert all(
        row["phase"] != "completed"
        for saved in writes
        for row in saved.get("planning_recovery", {}).get("corrections", [])
    )
    assert len(calls) == 2


def test_feedback_checkpoint_reference_is_pinned_across_completed_boundary_replay(case):
    normalize_accounts(case)
    references = []

    def ref():
        references.append(True)
        return {"checkpoint": f"{len(references):064x}", "job_id": "j" * 32}

    choose = controller(lambda e: compile_plan(proposal(), e["planning_context"]))
    choose.feedback_reference = ref
    original, _ = invoke(case, choose)
    original_hash = original["request_id"]
    assert case[0]["planning_feedback_refs"]["fit1:2"]["checkpoint"] == f"{1:064x}"

    def forbidden(_):
        pytest.fail("A completed boundary cannot call the provider")

    replay = controller(forbidden)
    replay.feedback_reference = ref
    restored, _ = invoke(case, replay)
    assert restored == original and restored["request_id"] == original_hash
    assert len(references) == 1
