"""Read-only method coverage from one owned, sealed calculation checkpoint.

No candle inputs, history shards, broker service or numerical runtime are read.
The immutable checkpoint and its mandate/manifest provide the saved provenance;
the pure coverage contract verifies their metadata before describing actual work.
"""

import json
import re

from database.research_db import ResearchExperiment, ResearchJob
from services import scanner_research_service as artifacts

VERSION = "research-method-coverage-view-v1"
MAX_ENVELOPE_BYTES = 34 * 1024 * 1024
SUPPORTED_RECIPES = (
    "automatic-hypotheses-v8",
    "automatic-hypotheses-v9",
    "automatic-hypotheses-v10",
    "automatic-hypotheses-v11",
)


class CoverageChanged(ValueError):
    pass


def _complete(calculation):
    if not isinstance(calculation, dict) or not isinstance(calculation.get("fits"), dict):
        return False
    proposal_limit = (
        68
        if calculation.get("version") == "automatic-hypotheses-v11"
        else 64
        if calculation.get("version") == "automatic-hypotheses-v10"
        else 48
    )
    return (
        all(
            isinstance(calculation["fits"].get(name), dict)
            and isinstance(calculation["fits"][name].get("trials"), list)
            and len(calculation["fits"][name]["trials"]) == proposal_limit
            for name in ("fit1", "fit2", "fit3", "search")
        )
        and isinstance(calculation.get("folds"), list)
        and len(calculation["folds"]) == 3
        and bool(calculation.get("selection"))
        and bool(calculation.get("final_report"))
    )


def read(store, owner, job_id, *, expected_checkpoint=None):
    if expected_checkpoint is not None and (
        not isinstance(expected_checkpoint, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_checkpoint)
    ):
        raise ValueError("Invalid saved method checkpoint")
    job = artifacts.get_job(store, owner, job_id)
    with store.sessions() as db:
        native = db.get(ResearchExperiment, job_id)
        checkpoint_id = native.checkpoint if native else None
        identity = native.identity if native else None
        kind = native.kind if native else None
        specification = native.specification if native else None
    if expected_checkpoint is not None and expected_checkpoint != checkpoint_id:
        raise CoverageChanged("Saved method details changed. Reopen the details to continue.")
    receipt = {
        "version": VERSION,
        "job_id": job_id,
        "available": False,
        "reason": "Method details are not available for this saved run.",
        "checkpoint_artifact": checkpoint_id,
        "result_artifact": job.result_artifact,
        "coverage": None,
    }
    if kind != "portfolio_optimize" or not checkpoint_id:
        return receipt
    try:
        configured = json.loads(specification or "{}")
        if not isinstance(configured, dict) or not isinstance(configured.get("portfolio"), dict):
            raise ValueError("Saved method specification changed")
        automatic = configured["portfolio"].get("automatic_research")
        if not isinstance(automatic, dict) or automatic.get("version") not in SUPPORTED_RECIPES:
            return receipt
        envelope = artifacts.read_artifact(store, checkpoint_id, max_bytes=MAX_ENVELOPE_BYTES)
        if not isinstance(envelope, dict):
            raise ValueError("Saved calculation provenance changed")
        state = envelope.get("state")
        if envelope.get("identity") != identity or not isinstance(state, dict):
            raise ValueError("Saved calculation provenance changed")
        if state.get("phase") != "calculation" or not state.get("calculation"):
            return receipt
        mandate, calculation, manifest = (
            state.get("research_mandate"),
            state.get("calculation"),
            state.get("development_evidence"),
        )
        if isinstance(calculation, dict) and calculation.get("version") != automatic["version"]:
            raise ValueError("Saved method programme changed")
        if (
            not isinstance(mandate, dict)
            or not isinstance(mandate.get("source"), dict)
            or mandate.get("source", {}).get("inputs_artifact") != state.get("inputs_artifact")
        ):
            raise ValueError("Saved method inputs changed")
        if job.status == "completed" and not _complete(calculation):
            raise ValueError("The completed method record is incomplete")
        from research.method_coverage import build

        coverage = build(mandate, calculation, manifest)
        receipt.update(
            available=coverage.get("status") == "available",
            reason=None if coverage.get("status") == "available" else receipt["reason"],
            coverage=coverage if coverage.get("status") == "available" else None,
        )
    except (ValueError, KeyError, TypeError, OSError, OverflowError, ArithmeticError):
        receipt["reason"] = (
            "The saved method details could not be verified. Your saved results are unchanged."
        )
    # A resume or publication can advance pointers while the immutable old
    # checkpoint is being read. Never present it as the current run's coverage.
    with store.sessions() as db:
        current_job = db.get(ResearchJob, job_id)
        current = db.get(ResearchExperiment, job_id)
        if not current_job or current_job.owner != owner:
            raise LookupError("Research run not found")
        if (
            not current
            or current.checkpoint != checkpoint_id
            or current.identity != identity
            or current_job.status != job.status
            or current_job.result_artifact != job.result_artifact
        ):
            raise CoverageChanged("Saved method details changed. Reopen the details to continue.")
    return receipt
