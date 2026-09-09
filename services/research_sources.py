"""Native queued source operations; credentials are resolved only during execution."""

import os
from pathlib import Path


class AcquisitionBatchPending(Exception):
    """A completed bounded download pass has saved more work for the worker."""


def source_capabilities(
    *, store=None, owner=None, broker_session=False, broker_name=None, preview=False
):
    """Native storage and account prerequisites; no token decryption or download."""
    from research.evidence_import import official_bundle_status

    directory = os.getenv("RESEARCH_PUBLIC_EVIDENCE_DIR")
    extension = os.getenv("RESEARCH_PUBLIC_EXTENSION_DIR")
    public = {"available": False}
    if directory:
        try:
            public = {"available": True, **official_bundle_status(directory, extension)}
        except (ValueError, OSError) as error:
            public["message"] = str(error)
    archive = {"configured": False, "stored_available": False, "can_prepare": False}
    try:
        target, _ = acquisition_paths(store)
        archive.update(configured=True, stored_available=target.is_file())
    except (ValueError, OSError):
        pass
    broker = {
        "provider": broker_name or "",
        "configured": False,
        "connected": False,
        "state": "sign_in_required",
        "action_url": "/broker",
        "message": "Connect your broker in OpenAlgo to download missing prices.",
    }
    if preview:
        broker.pop("action_url", None)
        broker.update(
            state="preview",
            message="OpenAlgo price history is available in the native app.",
        )
    elif owner:
        from sqlalchemy import select
        from sqlalchemy.exc import SQLAlchemyError
        from sqlalchemy.orm import Session

        from database.auth_db import Auth, engine

        try:
            # Read the provider and presence of usable auth, never the token itself.
            with Session(engine) as db:
                authenticated_provider = db.scalar(
                    select(Auth.broker)
                    .where(
                        Auth.name == owner,
                        Auth.is_revoked.is_(False),
                        Auth.auth.is_not(None),
                        Auth.auth != "",
                    )
                    .limit(1)
                )
            if authenticated_provider and not broker_name:
                broker["provider"] = authenticated_provider
            provider = broker["provider"]
            broker["configured"] = bool(provider and _broker_module_exists(provider))
            broker["connected"] = bool(
                broker_session and broker["configured"] and authenticated_provider == provider
            )
            if broker["connected"]:
                from database.master_contract_status_db import MasterContractStatus
                from database.master_contract_status_db import engine as master_engine

                with Session(master_engine) as db:
                    master = db.get(MasterContractStatus, provider)
                    ready = bool(
                        master
                        and master.is_ready
                        and master.status == "success"
                        and str(master.total_symbols).isdigit()
                        and int(master.total_symbols) > 0
                    )
                broker.pop("action_url", None)
                broker.update(
                    state="ready_to_download" if ready else "preparing_symbols",
                    message="" if ready else "Wait for OpenAlgo's symbol list to finish loading.",
                )
        except SQLAlchemyError:
            broker.pop("action_url", None)
            broker.update(
                state="setup_required", message="OpenAlgo is preparing its broker records."
            )
    if not preview and not archive["configured"]:
        broker.update(
            configured=False,
            state="setup_required",
            message="Check the OpenAlgo price database configuration.",
        )
    archive["can_prepare"] = bool(
        not preview
        and archive["configured"]
        and (archive["stored_available"] or broker["state"] == "ready_to_download")
    )
    return {
        "public": public,
        "history": archive,
        "evidence_update_available": bool(
            public["available"] and extension and public.get("extension_supported")
        ),
        "broker": broker,
    }


def _broker_module_exists(name):
    import re

    return bool(
        isinstance(name, str)
        and re.fullmatch(r"[a-z][a-z0-9_]*", name)
        and (Path(__file__).resolve().parents[1] / "broker" / name / "api" / "data.py").is_file()
    )


def resolve_broker_session(owner):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from database.auth_db import Auth, decrypt_token, engine

    # A short-lived independent session avoids importing a cached live token into
    # job metadata or retaining a scoped connection on the calculation thread.
    with Session(engine) as db:
        record = db.scalar(select(Auth).where(Auth.name == owner))
        if (
            record is None
            or record.is_revoked
            or not record.auth
            or not _broker_module_exists(record.broker)
        ):
            raise ValueError(
                "Some prices are missing from OpenAlgo. Connect your broker and retry to download them."
            )
        return {
            "auth_token": decrypt_token(record.auth),
            "feed_token": decrypt_token(record.feed_token) if record.feed_token else None,
            "broker": record.broker,
        }


def acquisition_paths(store):
    """Use the installation's native archive; isolation is a development policy."""
    if store is None:
        raise ValueError("Research store context is required")
    root = Path(__file__).resolve().parents[1]
    native = Path(os.getenv("HISTORIFY_DATABASE_PATH") or "db/historify.duckdb")
    target = (native if native.is_absolute() else root / native).resolve()
    configured = os.getenv("RESEARCH_HISTORIFY_DATABASE_PATH")
    if configured:
        requested = Path(configured)
        requested = (requested if requested.is_absolute() else root / requested).resolve()
        if requested != target:
            raise ValueError("Research must use the configured OpenAlgo Historify database")
    if target == store.root.resolve() or (target.exists() and not target.is_file()):
        raise ValueError("Historify must name a database file")
    if os.getenv("RESEARCH_REQUIRE_ISOLATED_ARCHIVE", "").lower() in {"1", "true"}:
        if not target.is_relative_to(store.root.resolve()):
            raise ValueError(
                "Development requires an isolated Historify archive inside research storage"
            )
    return target, store.root / "acquisition-receipts"


def update_evidence(end_date, progress=None, cancelled=None, *, store=None):
    from research.evidence_import import extend_official_bundle

    base, extension = (
        os.getenv("RESEARCH_PUBLIC_EVIDENCE_DIR"),
        os.getenv("RESEARCH_PUBLIC_EXTENSION_DIR"),
    )
    if not base or not extension:
        raise ValueError(
            "Configure the reviewed public bundle and a separate RESEARCH_PUBLIC_EXTENSION_DIR before updating evidence."
        )
    if store and not Path(extension).resolve().is_relative_to(store.root.resolve()):
        raise ValueError(
            "RESEARCH_PUBLIC_EXTENSION_DIR must be inside the isolated research directory for managed storage admission."
        )
    return extend_official_bundle(base, extension, end_date, progress=progress, cancelled=cancelled)


def acquire_source(
    store, owner, evidence, *, saved=None, checkpoint=None, progress=None, cancelled=None
):
    if (
        evidence["snapshot"].get("provenance", {}).get("native_price_policy")
        == "openalgo-native-history-v1"
    ):
        return _acquire_native_source(
            store,
            owner,
            evidence,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
        )
    from research.evidence_import import public_snapshot
    from services.research_acquisition import acquisition_receipts, native_historify_read
    from services.scanner_research_service import read_artifact, register_source, save_artifact

    target, receipts_dir = acquisition_paths(store)
    from services.research_checkpoint import MinuteCheckpointWriter, unpack_checkpoint

    checkpoint_writer = MinuteCheckpointWriter(store, receipts_dir, saved)
    saved = unpack_checkpoint(store, saved)
    if saved:
        import hashlib

        from services.research_acquisition import _save_receipt
        from services.scanner_research_service import encoded

        receipts_dir.mkdir(parents=True, exist_ok=True)
        for item in saved.get("acquisition_receipts", []):
            if hashlib.sha256(encoded(item["receipt"])).hexdigest() != item["sha256"]:
                raise ValueError("Restored acquisition receipt failed integrity check")
            _save_receipt(receipts_dir, item["receipt"])
    mode = evidence["snapshot"].get("provenance", {}).get("acquisition_mode")
    legacy = mode not in {"stored_only", "stored_then_broker"}

    def credentials():
        if mode == "stored_only":
            raise ValueError(
                "Some stored prices are missing. Choose automatic OpenAlgo prices to download them."
            )
        return resolve_broker_session(owner)

    reference_id = saved.get("reference_artifact") if saved else None
    if reference_id:
        reference = read_artifact(store, reference_id)
    else:
        base = os.getenv("RESEARCH_PUBLIC_EVIDENCE_DIR")
        if not base:
            raise ValueError("Configure official evidence before comparing broker observations.")
        reference = public_snapshot(
            evidence["signals"],
            base,
            progress=(lambda done, total: progress(done, max(1, total) * 20)) if progress else None,
            extension_dir=os.getenv("RESEARCH_PUBLIC_EXTENSION_DIR"),
        )
        reference_id = save_artifact(store, reference)

    def persist(state):
        if checkpoint:
            if state.get("mode") == "historify-minute-v1":
                checkpoint(
                    {
                        "reference_artifact": reference_id,
                        "acquisition_manifest": checkpoint_writer.pack(state),
                    },
                    state.get(
                        "progress",
                        {
                            "completed": len(state["completed_windows"]),
                            "total": state.get("total_windows", 1),
                        },
                    ),
                )
                return
            checkpoint(
                {
                    "reference_artifact": reference_id,
                    "acquisition": state,
                    "acquisition_receipts": acquisition_receipts(
                        {"provenance": {"broker_receipts": state["receipts"]}}, receipts_dir
                    ),
                },
                state.get(
                    "progress",
                    {
                        "completed": len(state["completed_windows"]),
                        "total": state.get("total_windows", len(state["completed_windows"])),
                    },
                ),
            )

    plan = None
    if evidence.get("data_request") and not legacy:
        from research.requirements import build_plan

        plan = build_plan(evidence["signals"], evidence["data_request"], reference)
    if plan and plan["interval"] == "1m":
        from services.research_historify import native_historify_write
        from services.research_intraday import acquire_intraday

        snapshot = acquire_intraday(
            evidence["signals"],
            plan,
            reference,
            reader=lambda symbol, first, last: native_historify_read(
                symbol, first, last, target, interval="1m"
            ),
            writer=lambda symbol, rows: native_historify_write(symbol, rows, target, interval="1m"),
            credentials=credentials,
            archive_dir=receipts_dir,
            prior=saved.get("acquisition") if saved else None,
            checkpoint=persist,
            progress=progress,
            cancelled=cancelled,
        )
    else:
        snapshot = _acquire_daily(
            owner,
            evidence,
            mode,
            legacy,
            credentials,
            target,
            receipts_dir,
            reference,
            saved,
            persist,
            progress,
            cancelled,
            required_dates=plan["required_dates"] if plan else None,
        )
    if plan:
        snapshot["data_requirements"] = plan
    snapshot.pop("acquisition_checkpoint")
    if snapshot["provenance"]["acquisition_status"] != "complete":
        if snapshot["provenance"].get("batch_pending") and checkpoint:
            raise AcquisitionBatchPending()
        unresolved = {
            item["kind"]
            for item in snapshot["provenance"].get("quality_findings", [])
            if not item.get("resolved_by_later_attempt")
        }
        if "auth_expired" in unresolved:
            raise ValueError("Broker session expired. Reconnect in OpenAlgo and retry.")
        if "rate_limited" in unresolved:
            raise ValueError("The broker rate limit was reached. Retry the missing dates shortly.")
        if "deadline" in unresolved:
            raise ValueError("Download reached its time limit. Resume the remaining dates.")
        raise ValueError(
            "Some required prices are unavailable from OpenAlgo. Resume to retry the missing prices."
        )
    prepared = {
        **evidence,
        "snapshot": snapshot,
        "acquisition_receipts": acquisition_receipts(snapshot, receipts_dir),
        "reference_artifact": reference_id,
    }
    return register_source(store, owner, prepared, publish=False)


def _acquire_daily(
    owner,
    evidence,
    mode,
    legacy,
    credentials,
    target,
    receipts_dir,
    reference,
    saved,
    persist,
    progress,
    cancelled,
    *,
    required_dates=None,
):
    from services.research_acquisition import (
        acquire_history,
        acquisition_receipts,
        native_historify_ingest,
        native_historify_read,
    )

    source_access = (
        resolve_broker_session(owner)
        if legacy
        else {
            "broker": None,
            "reader": lambda symbol, first, last: native_historify_read(
                symbol, first, last, target
            ),
            "credentials": credentials,
        }
    )
    snapshot = acquire_history(
        evidence["signals"],
        **source_access,
        archive_dir=receipts_dir,
        reference_snapshot=reference,
        progress=progress,
        cancelled=cancelled,
        writer=lambda symbol, rows: native_historify_ingest(symbol, rows, target),
        prior=saved.get("acquisition") if saved else None,
        on_checkpoint=persist,
        required_dates=required_dates,
    )
    if snapshot["provenance"].get("batch_pending"):
        return snapshot
    state = snapshot["acquisition_checkpoint"]
    # Failed windows remain retryable even when another symbol supplied usable
    # records. Only a fully traversed acquisition is registered as this attempt's
    # result; exact successful windows survive in its checkpoint.
    successes = {(r[0], r[1], r[2]) for r in state["completed_windows"]}
    latest_failures = [
        receipt
        for item in acquisition_receipts(snapshot, receipts_dir)
        for receipt in [item["receipt"]]
        if receipt.get("outcome") not in ("success", "empty")
        and (receipt.get("symbol"), receipt.get("requested_from"), receipt.get("requested_to"))
        not in successes
    ]
    if (legacy and latest_failures) or snapshot["coverage"]["status"] == "blocked":
        messages = snapshot["coverage"].get("warnings", [])
        raise ValueError(
            " ".join(messages)
            or "Some requested price windows failed; retry the missing windows from the saved checkpoint."
        )
    if snapshot["provenance"]["acquisition_status"] != "complete":
        unresolved = {
            item["kind"]
            for item in snapshot["provenance"].get("quality_findings", [])
            if not item.get("resolved_by_later_attempt")
        }
        if "auth_expired" in unresolved:
            raise ValueError("Broker session expired. Reconnect in OpenAlgo and retry.")
        if "rate_limited" in unresolved:
            raise ValueError("The broker rate limit was reached. Retry the missing dates shortly.")
        if "deadline" in unresolved:
            raise ValueError("Download reached its time limit. Resume the remaining dates.")
        raise ValueError("Some requested prices are still unavailable. Retry the missing dates.")
    return snapshot


def _acquire_native_source(
    store, owner, evidence, *, saved=None, checkpoint=None, progress=None, cancelled=None
):
    """Coordinate native calendar, history and storage for newly uploaded signals.

    The recorded policy selects this path. Existing public-qualified sources and
    their recovery checkpoints retain their original interpretation above.
    """
    import hashlib

    from research.requirements import build_plan
    from services.research_acquisition import _save_receipt, acquisition_receipts
    from services.research_checkpoint import MinuteCheckpointWriter, unpack_checkpoint
    from services.research_historify import native_historify_read, native_historify_write
    from services.research_native_calendar import native_calendar_snapshot
    from services.research_native_prices import acquire_native_prices
    from services.scanner_research_service import (
        encoded,
        read_artifact,
        register_source,
        save_artifact,
    )

    target, receipts_dir = acquisition_paths(store)
    writer = MinuteCheckpointWriter(store, receipts_dir, saved)
    restored = unpack_checkpoint(store, saved)
    reference_id = restored.get("reference_artifact") if restored else None
    calendar = (
        read_artifact(store, reference_id)
        if reference_id
        else native_calendar_snapshot(evidence["signals"], evidence["data_request"])
    )
    if reference_id:
        from research.calendar_coverage import check_saved_calendar

        check_saved_calendar(calendar)
    reference_id = reference_id or save_artifact(store, calendar)
    if restored:
        receipts_dir.mkdir(parents=True, exist_ok=True)
        for item in restored.get("acquisition_receipts", []):
            if hashlib.sha256(encoded(item["receipt"])).hexdigest() != item["sha256"]:
                raise ValueError("Restored acquisition receipt failed integrity check")
            _save_receipt(receipts_dir, item["receipt"])

    def credentials():
        if evidence["snapshot"]["provenance"].get("acquisition_mode") == "stored_only":
            raise ValueError(
                "Some stored prices are missing. Use automatic OpenAlgo prices to download them."
            )
        return resolve_broker_session(owner)

    def persist(state):
        if checkpoint:
            checkpoint(
                {"reference_artifact": reference_id, "acquisition_manifest": writer.pack(state)},
                state["progress"],
            )

    plan = build_plan(evidence["signals"], evidence["data_request"], calendar)
    interval = plan["interval"]
    snapshot = acquire_native_prices(
        evidence["signals"],
        plan,
        calendar,
        reader=lambda symbol, first, last: native_historify_read(
            symbol, first, last, target, interval=interval
        ),
        writer=lambda symbol, rows: native_historify_write(symbol, rows, target, interval=interval),
        credentials=credentials,
        archive_dir=receipts_dir,
        prior=restored.get("acquisition") if restored else None,
        checkpoint=persist,
        progress=progress,
        cancelled=cancelled,
    )
    snapshot.pop("acquisition_checkpoint")
    snapshot["data_requirements"] = plan
    provenance = snapshot["provenance"]
    if provenance.get("batch_pending"):
        raise AcquisitionBatchPending()
    if provenance.get("hard_failures") or provenance.get("acquisition_status") == "failed":
        kinds = {item["kind"] for item in provenance.get("hard_failures", [])}
        if "auth_expired" in kinds:
            raise ValueError("Broker session expired. Reconnect in OpenAlgo and retry.")
        if "rate_limited" in kinds:
            raise ValueError("The broker rate limit was reached. Retry shortly.")
        if "deadline" in kinds:
            raise ValueError("Download reached its time limit. Resume the remaining dates.")
        raise ValueError("Some price requests failed. Resume to retry them.")
    prepared = {
        **evidence,
        "snapshot": snapshot,
        "acquisition_receipts": acquisition_receipts(snapshot, receipts_dir),
        "reference_artifact": reference_id,
    }
    return register_source(store, owner, prepared, publish=False)
