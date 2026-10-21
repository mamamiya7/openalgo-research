"""Owned saved-account alternatives; no inputs, history reads or calculation."""

import re

from database.research_db import ResearchJob
from services import scanner_research_service as artifacts

VERSION = "research-tradeoffs-view-v1"
MAX_RESULT_BYTES = 64 * 1024 * 1024


class TradeoffsChanged(ValueError):
    pass


def read(store, owner, job_id, *, expected_result=None):
    if expected_result is not None and (
        not isinstance(expected_result, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_result)
    ):
        raise ValueError("Invalid saved alternatives query")
    job = artifacts.get_job(store, owner, job_id)
    pointer = job.result_artifact
    if expected_result is not None and expected_result != pointer:
        raise TradeoffsChanged("Saved results changed. Reopen alternatives to continue.")
    receipt = {
        "version": VERSION,
        "job_id": job_id,
        "result_artifact": pointer,
        "available": False,
        "reason": "Alternatives are available for completed automatic studies.",
        "analysis": None,
    }
    if job.status == "completed" and pointer:
        try:
            bundle = artifacts.read_artifact(store, pointer, max_bytes=MAX_RESULT_BYTES)
            if not isinstance(bundle, dict) or bundle.get("kind") != "portfolio_optimize":
                raise ValueError("Saved result is not an optimization study")
            from research.research_tradeoffs import analyze

            analysis = analyze(bundle.get("result"))
            readable = analysis.get("status") in {"available", "no_eligible_alternatives"}
            receipt.update(
                available=readable,
                analysis=analysis,
                reason=None
                if readable
                else "No comparable development alternatives qualify for this saved study.",
            )
        except (ValueError, KeyError, TypeError, OSError, OverflowError):
            receipt["reason"] = (
                "Saved alternatives could not be verified. Your results are unchanged."
            )
    with store.sessions() as db:
        current = db.get(ResearchJob, job_id)
        if current is None or current.owner != owner:
            raise LookupError("Research run not found")
        if current.result_artifact != pointer or current.status != job.status:
            raise TradeoffsChanged("Saved results changed. Reopen alternatives to continue.")
    return receipt
