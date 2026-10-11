"""Explicit timing-union acquisition, separate from existing v2-v6 recipes.

The caller supplies a frozen native calendar. Native Historify/history services
own all archive and network resources; this scope retains only bounded local
planning/checkpoint metadata. The v7 worker opts in once before calculation.
"""

from copy import deepcopy

from research.portfolio import fingerprint
from services import scanner_research_service as service

VERSION = "research-timing-price-union-v1"
PHASE = "timing_union_prices"
READY_PHASE = "timing_union_ready"


def prepare(
    store,
    owner,
    evidence,
    calendar,
    *,
    descriptor=None,
    saved=None,
    checkpoint,
    progress,
    cancelled,
    activity=None,
):
    """Acquire one explicit timing union, then freeze its common price cohort.

    Existing v2-v6 submission and price preparation never call this opt-in
    foundation. Every continuation is bound to the exact source, native calendar,
    proposed branches and required-price union before any archive or broker IO.
    """
    from research.portfolio import execution_versions
    from research.timing_search import build_union, freeze_cohort, propose, validate_union

    if evidence["portfolio"].get("engine") != "vectorbt":
        raise ValueError(
            "Timing union currently requires the qualified VectorBT NSE cash-equity engine"
        )
    execution_versions(evidence["portfolio"], evidence["versions"])

    strategies = evidence["strategies"]
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= 8:
        raise ValueError("Timing union supports one to eight native strategies")
    if len(evidence["signals"]) > 25000 or sum(len(row["signals"]) for row in strategies) > 25000:
        raise ValueError("Timing union supports at most 25000 normalized observations")
    if evidence["signals"] != [
        dict(signal, strategy_id=row["id"]) for row in strategies for signal in row["signals"]
    ]:
        raise ValueError("Timing union source observations do not match the native strategies")
    sessions = calendar.get("sessions", [])
    if not isinstance(sessions, list) or not 1 <= len(sessions) <= 3000:
        raise ValueError("Timing union requires one to 3000 recorded native sessions")
    hours, provenance = calendar.get("session_hours", {}), calendar.get("provenance", {})
    if not isinstance(hours, dict) or not isinstance(provenance, dict):
        raise ValueError("Timing union requires recorded native session hours and provenance")
    admitted_hours = {}
    for day in sessions:
        item = hours.get(day, {})
        if not isinstance(item, dict):
            raise ValueError(
                "Timing union requires recorded native session opening and closing hours"
            )
        admitted_hours[day] = {key: item.get(key) for key in ("open", "close")}
    calendar_metadata = {}
    for key in ("exchange", "calendar_basis", "calendar_admission"):
        if key in provenance:
            if not isinstance(provenance[key], str) or len(provenance[key]) > 256:
                raise ValueError("Timing union calendar identity exceeds its metadata bound")
            calendar_metadata[key] = provenance[key]
    calendar = {
        "sessions": list(sessions),
        "session_hours": admitted_hours,
        "bars": {},
        "provenance": calendar_metadata,
    }
    descriptor = (
        propose(evidence["portfolio"], evidence["strategies"], calendar)
        if descriptor is None
        else descriptor
    )
    plan = build_union(evidence["portfolio"], evidence["strategies"], calendar, descriptor)
    validate_union(evidence["portfolio"], evidence["strategies"], calendar, descriptor, plan)
    if plan.get("status") != "ready":
        raise ValueError(plan.get("reason") or "These inputs do not admit timing research")
    descriptor = deepcopy(descriptor)
    from research.connectors.vectorbt_portfolio import MAX_MATRIX_CELLS

    if (
        len(plan.get("timeline", calendar["sessions"])) * len(evidence["signals"])
        > MAX_MATRIX_CELLS
    ):
        raise ValueError("This timing union is too large for one native calculation")
    contract = {
        "version": VERSION,
        "source_id": fingerprint(
            {
                "portfolio": {
                    key: evidence["portfolio"][key]
                    for key in (
                        "version",
                        "engine",
                        "capital",
                        "optimization",
                        "automatic_research",
                        "validation",
                        "date_from",
                        "date_to",
                        "entry_conditions",
                    )
                    if key in evidence["portfolio"]
                },
                "versions": evidence["versions"],
                "strategies": [
                    {
                        **{
                            key: row[key]
                            for key in (
                                "id",
                                "source_id",
                                "allocation_pct",
                                "config",
                                "search",
                                "entry_condition",
                                "original_csv_sha256",
                                "original_signals_sha256",
                            )
                            if key in row
                        },
                        "signals": [
                            {
                                key: signal[key]
                                for key in (
                                    "symbol",
                                    "date",
                                    "timestamp",
                                    "row",
                                    "research_exclusion",
                                )
                                if key in signal
                            }
                            for signal in row["signals"]
                        ],
                    }
                    for row in evidence["strategies"]
                ],
            }
        ),
        "calendar_id": fingerprint(calendar),
        "descriptor": descriptor,
        "plan": plan,
    }
    binding = fingerprint(contract)
    cancelled()
    if saved:
        if (
            saved.get("phase") not in (PHASE, READY_PHASE)
            or saved.get("binding") != binding
            or service.read_artifact(store, saved.get("timing_contract", {}).get("inputs_artifact"))
            != contract
            or service.read_artifact(store, saved.get("reference_artifact")) != calendar
        ):
            raise ValueError("Saved timing union source, calendar or candidate contract changed")
        if saved["phase"] == READY_PHASE:
            retained = service.read_artifact(store, saved["inputs_artifact"])
            if retained.get("timing_union_binding") != binding:
                raise ValueError("Saved timing cohort belongs to different immutable inputs")
            return retained
    from services.research_acquisition import _save_receipt, acquisition_receipts
    from services.research_checkpoint import MinuteCheckpointWriter, unpack_checkpoint
    from services.research_historify import NativeHistorifyArchive
    from services.research_native_prices import acquire_native_prices
    from services.research_sources import (
        AcquisitionBatchPending,
        acquisition_paths,
        resolve_broker_session,
    )

    contract_id = (
        saved["timing_contract"]["inputs_artifact"]
        if saved
        else service.save_artifact(store, contract)
    )
    reference_id = saved["reference_artifact"] if saved else service.save_artifact(store, calendar)
    target, receipts_dir = acquisition_paths(store)
    writer = MinuteCheckpointWriter(store, receipts_dir, saved)
    restored = unpack_checkpoint(store, saved) if saved else None
    if restored:
        receipts_dir.mkdir(parents=True, exist_ok=True)
        for item in restored.get("acquisition_receipts", []):
            if fingerprint(item["receipt"]) != item["sha256"]:
                raise ValueError("Saved timing acquisition receipt changed")
            _save_receipt(receipts_dir, item["receipt"])

    def persist(state):
        with service.artifact_publication(store):
            manifest = writer.pack(state)
        checkpoint(
            {
                "phase": PHASE,
                "binding": binding,
                "timing_contract": {"inputs_artifact": contract_id},
                "reference_artifact": reference_id,
                "acquisition_manifest": manifest,
            },
            {**state["progress"], "stage": "prices"},
        )

    archive = NativeHistorifyArchive(target, interval=plan["interval"])
    snapshot = acquire_native_prices(
        evidence["signals"],
        plan,
        calendar,
        reader=archive.read,
        reader_many=archive.read_many,
        writer=archive.write,
        credentials=lambda: resolve_broker_session(owner),
        archive_dir=receipts_dir,
        prior=restored.get("acquisition") if restored else None,
        checkpoint=persist,
        progress=progress,
        cancelled=cancelled,
        **({"activity": activity} if activity else {}),
    )
    snapshot.pop("acquisition_checkpoint")
    snapshot["data_requirements"] = plan
    provenance = snapshot["provenance"]
    if provenance.get("batch_pending"):
        raise AcquisitionBatchPending()
    if provenance.get("hard_failures") or provenance.get("acquisition_status") == "failed":
        kinds = {item["kind"] for item in provenance.get("hard_failures", [])}
        if "auth_expired" in kinds:
            raise ValueError("Reconnect your broker in OpenAlgo, then resume this run.")
        raise ValueError(
            "Some required timing-union prices could not be downloaded. Resume to retry."
        )
    prepared = {
        **evidence,
        "snapshot": snapshot,
        "reference_artifact": reference_id,
        "acquisition_receipts": acquisition_receipts(snapshot, receipts_dir),
        "timing_contract": {"inputs_artifact": contract_id},
        "timing_union_binding": binding,
        "timing_descriptor": descriptor,
        "timing_union": plan,
    }
    prepared["timing_cohort"] = freeze_cohort(prepared, descriptor, plan)
    prepared["frozen_prices"] = True
    inputs_id = service.save_artifact(store, prepared)
    checkpoint(
        {
            "phase": READY_PHASE,
            "binding": binding,
            "timing_contract": {"inputs_artifact": contract_id},
            "reference_artifact": reference_id,
            "inputs_artifact": inputs_id,
        },
        {"completed": 100, "total": 100, "stage": "prepared"},
    )
    cancelled()
    return prepared
