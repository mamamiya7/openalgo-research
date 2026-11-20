"""Correction calls consume a separate bounded annex without rewriting history."""

from copy import deepcopy

import pytest
from test_hands_off_plan import context, proposal

from research import planning_recovery as recovery
from research.hands_off_plan import compile_plan

BOUNDARY = "search_seed2:11"
CHECKPOINT, REQUEST, AUTHORITY = "a" * 64, "b" * 64, "explicit-user-recovery"


def book():
    result = {
        "version": "hands-off-fixed-signals-v1",
        "calls_spent": 11,
        "records": {
            f"completed:{n}": {
                "phase": "completed",
                "request_id": f"{n:064x}",
                "plan": {"id": f"{n + 1:064x}"},
            }
            for n in range(10)
        },
        "active": BOUNDARY,
    }
    result["records"][BOUNDARY] = {"phase": "requesting", "request_id": REQUEST}
    return result


def admitted():
    native = book()
    annex = recovery.authorize(
        native,
        checkpoint_id=CHECKPOINT,
        request_id=REQUEST,
        authority_id=AUTHORITY,
        job_id="j" * 32,
    )
    receipt = recovery.legacy_rejection(
        annex,
        checkpoint_id=CHECKPOINT,
        request_id=REQUEST,
        authority_id=AUTHORITY,
        error="AI proposed a value outside the frozen data/search domain",
    )
    return native, recovery.attest_rejection(native, annex, receipt, boundary_key=BOUNDARY)


def rejected():
    return recovery.rejection_receipt(
        "completed invalid model output",
        proposal(),
        context(),
        reason_code="invalid_plan",
        error="AI proposed a value outside the frozen data/search domain",
        model_id=1,
        model="configured/model",
        usage={"completion_calls": 1},
    )


def test_legacy_attestation_pins_exact_request_and_does_not_invent_missing_response():
    native = book()
    before = deepcopy(native)
    native, annex = admitted()
    receipt = annex["rejections"][BOUNDARY]["receipt"]
    assert receipt["response_sha256"] is None and receipt["proposal"] is None
    assert receipt["provenance"]["source"] == "operator_attested_legacy"
    assert receipt["provenance"]["stream_closed"] is None
    assert native == before
    assert recovery.accounting(native, annex) == {
        "initial_calls": 11,
        "correction_calls": 0,
        "spent": 11,
        "remaining_global": 13,
        "remaining_corrections": 2,
    }
    with pytest.raises(ValueError, match="checkpoint/request"):
        recovery.legacy_rejection(
            annex,
            checkpoint_id="c" * 64,
            request_id=REQUEST,
            authority_id=AUTHORITY,
            error="known invalid",
        )


def test_correction_charged_before_provider_and_normal_twelfth_boundary_still_fits_global_24():
    native, annex = admitted()
    before = deepcopy(native)
    reserved = recovery.reserve_correction(
        native, annex, boundary_key=BOUNDARY, request_id="c" * 64
    )
    assert recovery.accounting(native, reserved)["spent"] == 12
    assert reserved["corrections"][-1]["phase"] == "requesting"
    compiled = compile_plan(proposal(), context())
    completed = recovery.complete_correction(
        reserved, reserved["corrections"][-1]["id"], plan=compiled
    )
    assert completed["corrections"][-1]["plan"] == compiled
    assert native == before
    native["calls_spent"] += 1
    native["records"]["search_seed2:39"] = {"phase": "completed", "request_id": "d" * 64}
    counts = recovery.accounting(native, completed)
    assert counts["initial_calls"] == 12 and counts["spent"] == 13
    assert counts["remaining_global"] == 11
    assert compiled["budget"] == {"proposals": 408, "max_simulations": 512}


def test_two_known_rejections_can_be_corrected_but_third_call_is_not_granted():
    native, annex = admitted()
    for n in range(2):
        annex = recovery.reserve_correction(
            native, annex, boundary_key=BOUNDARY, request_id=f"{n + 20:064x}"
        )
        annex = recovery.complete_correction(
            annex, annex["corrections"][-1]["id"], rejection=rejected()
        )
    assert recovery.accounting(native, annex)["spent"] == 13
    assert recovery.accounting(native, annex)["remaining_corrections"] == 0
    with pytest.raises(ValueError, match="allowance is exhausted"):
        recovery.reserve_correction(native, annex, boundary_key=BOUNDARY, request_id="e" * 64)


def test_interrupted_correction_is_charged_and_never_blindly_retried():
    native, annex = admitted()
    annex = recovery.reserve_correction(native, annex, boundary_key=BOUNDARY, request_id="c" * 64)
    with pytest.raises(ValueError, match="interrupted correction"):
        recovery.reserve_correction(native, annex, boundary_key=BOUNDARY, request_id="d" * 64)
    assert recovery.accounting(native, annex)["spent"] == 12


def test_uncertain_original_request_without_attestation_cannot_receive_correction():
    native = book()
    annex = recovery.authorize(
        native, checkpoint_id=CHECKPOINT, request_id=REQUEST, authority_id=AUTHORITY
    )
    with pytest.raises(ValueError, match="uncertain provider"):
        recovery.reserve_correction(native, annex, boundary_key=BOUNDARY, request_id="d" * 64)


def test_attestation_does_not_change_completed_original_plans_or_allow_foreign_request():
    native, annex = admitted()
    with pytest.raises(ValueError, match="exact unresolved"):
        recovery.attest_rejection(native, annex, rejected(), boundary_key="completed:1")
    changed = deepcopy(native)
    changed["records"][BOUNDARY]["request_id"] = "e" * 64
    with pytest.raises(ValueError, match="request identity changed"):
        recovery.accounting(changed, annex)


def test_completed_correction_is_idempotent_and_cannot_be_replaced():
    native, annex = admitted()
    annex = recovery.reserve_correction(native, annex, boundary_key=BOUNDARY, request_id="c" * 64)
    key = annex["corrections"][-1]["id"]
    plan = compile_plan(proposal(), context())
    completed = recovery.complete_correction(annex, key, plan=plan)
    assert recovery.complete_correction(completed, key, plan=plan) == completed
    with pytest.raises(ValueError, match="cannot be replaced"):
        recovery.complete_correction(completed, key, rejection=rejected())
    with pytest.raises(ValueError, match="cannot be replaced"):
        recovery.reserve_correction(native, completed, boundary_key=BOUNDARY, request_id="d" * 64)


def test_annex_tampering_and_native_accounting_refund_are_rejected():
    native, annex = admitted()
    changed = deepcopy(annex)
    changed["max_corrections"] = 3
    with pytest.raises(ValueError, match="annex changed"):
        recovery.verify(changed, native)
    native["calls_spent"] = 10
    native["records"].pop("completed:0")
    with pytest.raises(ValueError, match="refund"):
        recovery.accounting(native, annex)


def test_safe_receipt_excludes_boolean_nonfinite_and_overflowing_numeric_payloads():
    invalid = proposal(
        domains={"scan": {"hold_sessions": {"min": True, "max": float("inf"), "step": 10**1000}}}
    )
    receipt = recovery.rejection_receipt(
        "invalid",
        invalid,
        context(),
        reason_code="invalid_plan",
        error="invalid numeric domain",
        model_id=1,
        model="configured/model",
        usage={},
    )
    assert receipt["proposal"]["domains"]["scan"]["hold_sessions"] == {}
