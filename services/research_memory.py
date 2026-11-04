"""Bounded same-study research memory from existing immutable supervisor ledgers."""

import json

from sqlalchemy import select

from database.research_db import (
    ResearchJourney,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchSupervisor,
)
from research.report_contract import settings_identity

VERSION = "research-study-memory-v1"
MAX_ENTRIES = 24


def read(store, owner, experiment_id, inputs_artifact=None, limit=MAX_ENTRIES):
    """Prior results are context, never an independent validation observation."""
    if type(limit) is not int or not 1 <= limit <= MAX_ENTRIES:
        raise ValueError("Research memory needs a bounded entry limit")
    with store.sessions() as db:
        experiment = db.get(ResearchLibraryExperiment, experiment_id)
        if experiment is None or experiment.owner != owner:
            raise LookupError("Research study not found")
        rows = db.scalars(
            select(ResearchSupervisor)
            .join(ResearchLibraryJob, ResearchLibraryJob.job_id == ResearchSupervisor.root_job_id)
            .where(
                ResearchLibraryJob.experiment_id == experiment_id, ResearchSupervisor.owner == owner
            )
            .order_by(ResearchSupervisor.updated_at.desc(), ResearchSupervisor.id)
            .limit(MAX_ENTRIES)
        ).all()
        saved = [(row.id, row.updated_at, row.state) for row in rows]
        journey = db.get(ResearchJourney, experiment_id)
        journey_state = json.loads(journey.state) if journey and journey.owner == owner else {}
    entries = []
    for supervisor_id, updated_at, raw in saved:
        state = json.loads(raw)
        context = state.get("research_context", {})
        old_inputs = context.get("inputs_artifact")
        same_inputs = bool(inputs_artifact and old_inputs == inputs_artifact)
        for proposal in reversed(state.get("proposals", [])):
            if not proposal.get("outcome"):
                continue
            from services.research_experiments import CONFIGURATION_VERSION, configuration_identity

            feedback = proposal.get("experiment", {}).get("feedback", {})
            exact_id = settings_identity(proposal["settings"])
            configuration_id = feedback.get("configuration_id")
            if (
                not configuration_id
                and feedback.get("validated_settings_id", exact_id) == exact_id
                and (
                    proposal["remedy"] != "funding_and_size"
                    or feedback.get("validated_settings_id") == exact_id
                )
            ):
                configuration_id = configuration_identity(
                    proposal["settings"], engine=context.get("contract", {}).get("engine")
                )
            entries.append(
                {
                    "supervisor_id": supervisor_id,
                    "job_id": proposal.get("job_id"),
                    "updated_at": updated_at,
                    "remedy": proposal["remedy"],
                    "settings_id": proposal.get("experiment", {})
                    .get("feedback", {})
                    .get("validated_settings_id")
                    or settings_identity(proposal["settings"]),
                    "configuration_id": configuration_id,
                    "configuration_version": feedback.get(
                        "configuration_version", CONFIGURATION_VERSION
                    ),
                    "funding_source_configuration_id": (
                        proposal.get("candidate", {}).get("funding_source_configuration_id")
                        or configuration_identity(
                            proposal["settings"], engine=context.get("contract", {}).get("engine")
                        )
                    )
                    if proposal["remedy"] == "funding_and_size"
                    else None,
                    "outcome": proposal["outcome"],
                    "no_effect": proposal.get("no_effect", {}).get("status") == "unchanged",
                    "reasons": [str(r)[:300] for r in proposal.get("reasons", [])[:3]],
                    "inputs_artifact": old_inputs,
                    "same_inputs": same_inputs,
                    "contract_id": context.get("contract", {}).get("id"),
                    "independently_validated": False,
                    "retest_reason": "Exact observations and reviewed constraints are unchanged"
                    if same_inputs
                    else "New or differently bound observations require fresh validation; prior outcomes are context only",
                }
            )
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break
    # The journey stores compact full-search receipts before replacing an
    # editable plan. No huge report artifacts need loading merely for memory.
    for receipt in journey_state.get("history", [])[-MAX_ENTRIES:]:
        if receipt.get("outcome") not in ("validated", "rejected", "blocked"):
            continue
        same_inputs = bool(inputs_artifact and receipt.get("inputs_artifact") == inputs_artifact)
        entries.append(
            {
                "job_id": receipt.get("job_id"),
                "updated_at": receipt.get("updated_at", 0),
                "remedy": "full_search",
                "settings_id": None,
                "outcome": receipt["outcome"],
                "no_effect": False,
                "reasons": [str(r)[:300] for r in receipt.get("reasons", [])[:3]],
                "inputs_artifact": receipt.get("inputs_artifact"),
                "same_inputs": same_inputs,
                "contract_id": receipt.get("contract_id"),
                "independently_validated": False,
                "retest_reason": "Exact observations and reviewed constraints are unchanged"
                if same_inputs
                else "New observations require fresh validation; previous searches are context only",
            }
        )
    entries.sort(key=lambda row: row["updated_at"], reverse=True)
    return {
        "version": VERSION,
        "experiment_id": experiment_id,
        "entries": entries[:limit],
        "scope": "Same-study historical decisions; no independent-evidence claim",
    }


def matching(memory, remedy, settings, contract_id):
    """Suppress only an exact same-input/settings/contract negative repeat."""
    point = settings_identity(settings)
    return next(
        (
            row
            for row in memory.get("entries", [])
            if row["same_inputs"]
            and row["remedy"] == remedy
            and row["settings_id"] == point
            and row.get("contract_id") == contract_id
            and row["outcome"] in ("rejected", "blocked")
        ),
        None,
    )
