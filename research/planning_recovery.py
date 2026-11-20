"""Pure receipts and bounded correction accounting; never execute or contact AI.

Normal twelve-boundary programme evidence is immutable. A separately authorized
annex charges at most two known-rejection corrections to the same 24-call total.
An uncertain request is never inferred to be a completed rejection.
"""

import hashlib
import math
import re
from copy import deepcopy

from research.report_contract import fingerprint

VERSION = "planning-recovery-v2"
REJECTION_VERSION = "completed-plan-rejection-v1"
NORMAL_LIMIT = 12
CORRECTION_LIMIT = 2
GLOBAL_LIMIT = 24
_HEX = re.compile(r"[a-f0-9]{64}")


def _digest(value, label):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"Recovery needs the exact {label} identity")
    return value


def _seal(value):
    return {**value, "id": fingerprint(value)}


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def rejection_receipt(text, proposal, context, *, reason_code, error, model_id, model, usage):
    """Retain a safe numeric projection, not raw model text or unknown fields."""
    if reason_code not in ("invalid_json", "invalid_plan"):
        raise ValueError("Unsupported completed-plan rejection")
    safe = None
    if isinstance(proposal, dict):
        safe = {
            key: proposal[key]
            for key, choices in (
                ("version", ("hands-off-plan-v1",)),
                ("action", ("search", "defer", "stop")),
                ("method", ("holding_and_exits", "sizing_and_allocation", "joint_trading", None)),
            )
            if proposal.get(key) in choices
        }
        domains = proposal.get("domains")
        if isinstance(domains, dict):
            safe["domains"] = {}
            for sid, supported in context.get("domains", {}).items():
                fields = domains.get(sid)
                if not isinstance(fields, dict):
                    continue
                for axis in supported:
                    spec = fields.get(axis)
                    if not isinstance(spec, dict):
                        continue
                    values = {k: spec[k] for k in ("min", "max", "step") if _finite(spec.get(k))}
                    safe["domains"].setdefault(sid, {})[axis] = values
    # Compiler errors use local templates, but preserve no control characters or
    # unbounded exception text in the durable feedback/progress surface.
    message = " ".join(str(error).split())[:240]
    return _seal(
        {
            "version": REJECTION_VERSION,
            "context_id": context.get("id"),
            "reason_code": reason_code,
            "error": message or "The completed research plan failed local validation",
            "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "proposal": safe,
            "provenance": {
                "model_id": model_id,
                "model": model,
                "usage": {
                    k: usage.get(k, 0)
                    for k in ("completion_calls", "input_tokens", "output_tokens")
                },
                "provider_completed": True,
                "stream_closed": True,
            },
        }
    )


def _book(book):
    if (
        not isinstance(book, dict)
        or type(book.get("calls_spent")) is not int
        or not 0 <= book["calls_spent"] <= NORMAL_LIMIT
        or not isinstance(book.get("records"), dict)
        or book["calls_spent"] != len(book["records"])
        or any(not isinstance(row, dict) for row in book["records"].values())
    ):
        raise ValueError("Recovery cannot change the original model accounting")


def _receipt(receipt):
    if not isinstance(receipt, dict) or receipt.get("version") != REJECTION_VERSION:
        raise ValueError("Correction requires a known completed-plan rejection")
    if receipt.get("id") != fingerprint({k: v for k, v in receipt.items() if k != "id"}):
        raise ValueError("The completed rejection receipt changed")
    provenance = receipt.get("provenance", {})
    if (
        receipt.get("reason_code") not in ("invalid_plan", "invalid_json")
        or not isinstance(receipt.get("error"), str)
        or not 1 <= len(receipt["error"]) <= 240
        or provenance.get("provider_completed") is not True
    ):
        raise ValueError("Correction requires a completed response and bounded reason")
    if provenance.get("source") == "operator_attested_legacy":
        if (
            receipt.get("response_sha256") is not None
            or receipt.get("proposal") is not None
            or provenance.get("stream_closed") is not None
        ):
            raise ValueError("Legacy attestation cannot invent the missing provider response")
        for field in ("checkpoint_id", "request_id"):
            _digest(provenance.get(field), field)
        if not isinstance(provenance.get("authority_id"), str) or not provenance["authority_id"]:
            raise ValueError("Legacy rejection needs explicit recovery authority")
    else:
        _digest(receipt.get("response_sha256"), "response")
        if provenance.get("stream_closed") is not True:
            raise ValueError("An unclosed response cannot be corrected automatically")
    return receipt


def authorize(book, *, checkpoint_id, request_id, authority_id, job_id=None):
    """Create a server-owned grant pinned to the explicit recovery admission."""
    _book(book)
    _digest(checkpoint_id, "checkpoint")
    _digest(request_id, "request")
    if not isinstance(authority_id, str) or not 1 <= len(authority_id) <= 128:
        raise ValueError("Recovery needs explicit bounded authorization")
    if job_id is not None and (not isinstance(job_id, str) or not 1 <= len(job_id) <= 64):
        raise ValueError("Recovery needs its exact job identity")
    if (
        sum(
            r.get("request_id") == request_id and r.get("phase") == "requesting"
            for r in book["records"].values()
        )
        != 1
    ):
        raise ValueError("Recovery admission needs its exact unresolved request")
    grant = _seal(
        {
            "checkpoint_id": checkpoint_id,
            "request_id": request_id,
            "authority_id": authority_id,
            "job_id": job_id,
            "initial_calls": book["calls_spent"],
        }
    )
    return _seal(
        {
            "version": VERSION,
            "authorization": grant,
            "max_corrections": CORRECTION_LIMIT,
            "rejections": {},
            "corrections": [],
        }
    )


def verify(annex, book=None):
    if (
        not isinstance(annex, dict)
        or set(annex)
        != {"version", "authorization", "max_corrections", "rejections", "corrections", "id"}
        or annex.get("version") != VERSION
        or annex.get("max_corrections") != CORRECTION_LIMIT
        or annex.get("id") != fingerprint({k: v for k, v in annex.items() if k != "id"})
    ):
        raise ValueError("The authorized recovery annex changed")
    grant = annex["authorization"]
    if (
        not isinstance(grant, dict)
        or set(grant)
        != {"checkpoint_id", "request_id", "authority_id", "job_id", "initial_calls", "id"}
        or grant.get("id") != fingerprint({k: v for k, v in grant.items() if k != "id"})
        or type(grant.get("initial_calls")) is not int
        or not 1 <= grant["initial_calls"] <= NORMAL_LIMIT
        or not isinstance(grant.get("authority_id"), str)
        or not 1 <= len(grant["authority_id"]) <= 128
    ):
        raise ValueError("The recovery authorization changed")
    _digest(grant["checkpoint_id"], "checkpoint")
    _digest(grant["request_id"], "request")
    rejections, corrections = annex["rejections"], annex["corrections"]
    if not isinstance(rejections, dict) or len(rejections) > NORMAL_LIMIT:
        raise ValueError("Recovery rejection history exceeds its bounded programme")
    for key, value in rejections.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 64 or not isinstance(value, dict):
            raise ValueError("Invalid recovery boundary")
        _digest(value.get("request_id"), "request")
        _receipt(value.get("receipt"))
    if not isinstance(corrections, list) or len(corrections) > CORRECTION_LIMIT:
        raise ValueError("The two-call correction allowance is exhausted")
    for index, row in enumerate(corrections):
        if not isinstance(row, dict) or row.get("phase") not in (
            "requesting",
            "completed",
            "rejected",
        ):
            raise ValueError("Invalid correction reservation")
        _digest(row.get("request_id"), "correction request")
        if row.get("boundary_key") not in rejections:
            raise ValueError("Correction has no known rejected boundary")
        expected = fingerprint(
            {
                "authorization_id": grant["id"],
                "number": index + 1,
                "boundary_key": row["boundary_key"],
                "request_id": row["request_id"],
                "rejection_id": row.get("rejection_id"),
            }
        )
        if row.get("id") != expected:
            raise ValueError("The correction reservation identity changed")
        if row["phase"] == "rejected":
            _receipt(row.get("rejection"))
        elif row["phase"] == "completed":
            plan = row.get("plan")
            if not isinstance(plan, dict) or plan.get("action") not in ("search", "defer", "stop"):
                raise ValueError("Correction has no admitted model plan")
            _digest(plan.get("id"), "compiled plan")
        if row["phase"] == "requesting" and index != len(corrections) - 1:
            raise ValueError("An uncertain correction cannot be retried")
    if book is not None:
        _book(book)
        if book["calls_spent"] < grant["initial_calls"]:
            raise ValueError("Recovery cannot refund prior model calls")
        if (
            sum(row.get("request_id") == grant["request_id"] for row in book["records"].values())
            != 1
        ):
            raise ValueError("The authorized original request identity changed")
        for key, value in rejections.items():
            if book["records"].get(key, {}).get("request_id") != value["request_id"]:
                raise ValueError("The original rejected request identity changed")
    return annex


def legacy_rejection(annex, *, checkpoint_id, request_id, authority_id, error):
    """Explicitly attest the old lost response; never fabricate its body/digest."""
    verify(annex)
    grant = annex["authorization"]
    if any(
        grant[k] != v
        for k, v in {
            "checkpoint_id": checkpoint_id,
            "request_id": request_id,
            "authority_id": authority_id,
        }.items()
    ):
        raise ValueError("Legacy rejection does not match the authorized checkpoint/request")
    return _seal(
        {
            "version": REJECTION_VERSION,
            "context_id": None,
            "reason_code": "invalid_plan",
            "error": " ".join(str(error).split())[:240],
            "response_sha256": None,
            "proposal": None,
            "provenance": {
                "source": "operator_attested_legacy",
                "authority_id": authority_id,
                "checkpoint_id": checkpoint_id,
                "request_id": request_id,
                "provider_completed": True,
                "stream_closed": None,
            },
        }
    )


def attest_rejection(book, annex, receipt, *, boundary_key):
    verify(annex, book)
    _receipt(receipt)
    row = book["records"].get(boundary_key)
    if not row or row.get("phase") != "requesting":
        raise ValueError("Only the exact unresolved original request admits rejection attestation")
    if receipt["provenance"].get("source") == "operator_attested_legacy":
        grant = annex["authorization"]
        if (
            any(
                receipt["provenance"].get(k) != grant[k]
                for k in ("checkpoint_id", "request_id", "authority_id")
            )
            or row["request_id"] != grant["request_id"]
        ):
            raise ValueError("The legacy rejection pins do not match the original request")
    value = {"request_id": row["request_id"], "receipt": deepcopy(receipt)}
    previous = annex["rejections"].get(boundary_key)
    if previous is not None and previous != value:
        raise ValueError("The known rejection cannot be replaced")
    result = {k: deepcopy(v) for k, v in annex.items() if k != "id"}
    result["rejections"][boundary_key] = value
    return _seal(result)


def accounting(book, annex):
    verify(annex, book)
    normal, corrections = book["calls_spent"], len(annex["corrections"])
    spent = normal + corrections
    if spent > NORMAL_LIMIT + CORRECTION_LIMIT or spent > GLOBAL_LIMIT:
        raise ValueError("Recovery exceeds the unchanged global model allowance")
    return {
        "initial_calls": normal,
        "correction_calls": corrections,
        "spent": spent,
        "remaining_global": GLOBAL_LIMIT - spent,
        "remaining_corrections": CORRECTION_LIMIT - corrections,
    }


def reserve_correction(book, annex, *, boundary_key, request_id):
    counts = accounting(book, annex)
    _digest(request_id, "correction request")
    if boundary_key not in annex["rejections"]:
        raise ValueError("An uncertain provider request cannot be retried")
    if not counts["remaining_corrections"] or not counts["remaining_global"]:
        raise ValueError("The bounded correction allowance is exhausted")
    if any(row["phase"] == "requesting" for row in annex["corrections"]):
        raise ValueError("An interrupted correction remains charged and cannot be blindly retried")
    previous = [row for row in annex["corrections"] if row["boundary_key"] == boundary_key]
    if previous and previous[-1]["phase"] == "completed":
        raise ValueError("An admitted corrected plan cannot be replaced")
    receipt = (
        previous[-1]["rejection"] if previous else annex["rejections"][boundary_key]["receipt"]
    )
    row = {
        "boundary_key": boundary_key,
        "request_id": request_id,
        "rejection_id": receipt["id"],
        "phase": "requesting",
    }
    row["id"] = fingerprint(
        {
            "authorization_id": annex["authorization"]["id"],
            "number": len(annex["corrections"]) + 1,
            **{k: row[k] for k in ("boundary_key", "request_id", "rejection_id")},
        }
    )
    result = {k: deepcopy(v) for k, v in annex.items() if k != "id"}
    result["corrections"].append(row)
    return _seal(result)


def complete_correction(annex, correction_id, *, plan=None, rejection=None):
    verify(annex)
    if (plan is None) == (rejection is None):
        raise ValueError("Record one compiled correction or one known rejection")
    result = {k: deepcopy(v) for k, v in annex.items() if k != "id"}
    row = next((r for r in result["corrections"] if r["id"] == correction_id), None)
    if row is None:
        raise ValueError("No reserved correction matches this completed response")
    field, phase, value = (
        ("plan", "completed", plan) if plan is not None else ("rejection", "rejected", rejection)
    )
    if row["phase"] != "requesting":
        if row["phase"] == phase and row.get(field) == value:
            return deepcopy(annex)
        raise ValueError("A completed correction outcome cannot be replaced")
    row.update(phase=phase, **{field: deepcopy(value)})
    sealed = _seal(result)
    verify(sealed)
    return sealed
