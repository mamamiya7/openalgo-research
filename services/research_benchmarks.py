"""Prepare separate native market evidence for an explicit saved-report overlay."""

from research.benchmark import normalize_benchmark, report_dates
from research.report_contract import fingerprint
from services import scanner_research_service as service


def retained_benchmark(store, artifact, parent_artifact, descriptor):
    """Read only a benchmark pinned to this exact owner-checked parent result."""
    from research.market_series import validate_series

    bundle = service.read_artifact(store, artifact)
    if (
        bundle.get("kind") != "portfolio_analysis"
        or bundle.get("parent_result_artifact") != parent_artifact
    ):
        raise ValueError("The saved benchmark belongs to another report")
    evidence = bundle.get("result", {}).get("benchmark_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("The saved analysis has no benchmark evidence")
    validate_series(evidence)
    if evidence["descriptor"] != descriptor:
        raise ValueError("The saved benchmark instrument changed")
    receipts = bundle.get("result", {}).get("benchmark_receipts", [])
    _validate_receipts(receipts)
    return {"evidence": evidence, "acquisition_receipts": receipts}


def _validate_receipts(receipts):
    import hashlib

    if not isinstance(receipts, list) or len(receipts) > 1000:
        raise ValueError("Invalid bounded benchmark receipt list")
    total = 0
    for row in receipts:
        payload = service.encoded(row["receipt"])
        total += len(payload)
        if total > 128 * 1024**2 or hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ValueError("Benchmark receipt integrity check failed")


def prepare(
    store,
    owner,
    report,
    descriptor,
    *,
    saved=None,
    checkpoint=None,
    progress=None,
    cancelled=None,
    warmup_sessions=1,
):
    from research.market_series import validate_series
    from services.research_acquisition import _save_receipt, acquisition_receipts
    from services.research_market_series import acquire_series
    from services.research_native_calendar import native_calendar_window
    from services.research_sources import (
        AcquisitionBatchPending,
        acquisition_paths,
        resolve_broker_session,
    )

    descriptor = normalize_benchmark(descriptor)
    reports = [report]
    if report.get("validation", {}).get("result"):
        reports.append(report["validation"]["result"])
    ranges = [report_dates(value) for value in reports]
    first, last = min(row[0] for row in ranges), max(row[1] for row in ranges)
    if type(warmup_sessions) is not int or not 1 <= warmup_sessions <= 252:
        raise ValueError("Choose 1–252 preceding market sessions")
    binding_data = {"descriptor": descriptor, "dates": [first, last]}
    if warmup_sessions != 1:
        binding_data["warmup_sessions"] = warmup_sessions
    binding = fingerprint(binding_data)
    if saved and (saved.get("phase") != "benchmark" or saved.get("binding") != binding):
        raise ValueError("Saved benchmark preparation belongs to different inputs")
    state = saved or {"phase": "benchmark", "binding": binding}
    if state.get("inputs_artifact"):
        recorded = service.read_artifact(store, state["inputs_artifact"])
        evidence = recorded["evidence"]
        validate_series(evidence)
        _validate_receipts(recorded["acquisition_receipts"])
        if evidence["descriptor"] != descriptor:
            raise ValueError("Saved benchmark preparation changed instrument")
        return recorded
    calendar_id = state.get("reference_artifact")
    calendar = (
        service.read_artifact(store, calendar_id)
        if calendar_id
        else native_calendar_window(first, last, warmup_sessions=warmup_sessions)
    )
    calendar_id = calendar_id or service.save_artifact(store, calendar)
    target, receipts = acquisition_paths(store)
    # Receipt sidecars are a mutable operational cache. Retain their exact bytes
    # in the durable checkpoint, so backup/restore can reconstruct them too.
    if state.get("acquisition_receipts"):
        _validate_receipts(state["acquisition_receipts"])
        receipts.mkdir(parents=True, exist_ok=True)
        for item in state["acquisition_receipts"]:
            if _save_receipt(receipts, item["receipt"]) != item["sha256"]:
                raise ValueError("Saved benchmark receipt changed during recovery")

    def persist(acquisition):
        if checkpoint:
            checkpoint(
                {
                    "phase": "benchmark",
                    "binding": binding,
                    "reference_artifact": calendar_id,
                    "acquisition": acquisition,
                    "acquisition_receipts": acquisition_receipts(
                        {"provenance": {"broker_receipts": acquisition["receipts"]}}, receipts
                    ),
                },
                {"completed": 0, "total": 1, "stage": "prices"},
            )

    # One daily reference has at most 3000 rows; the shared acquisition core owns
    # request budgets and archive connection cleanup, just like execution prices.
    response = acquire_series(
        descriptor,
        calendar["sessions"],
        calendar,
        archive_path=target,
        receipts_dir=receipts,
        credentials=lambda: resolve_broker_session(owner),
        prior=state.get("acquisition"),
        checkpoint=persist,
        progress=progress,
        cancelled=cancelled,
    )
    if response["batch_pending"]:
        raise AcquisitionBatchPending()
    if response["hard_failures"]:
        if any(row.get("kind") == "auth_expired" for row in response["hard_failures"]):
            raise ValueError(
                "Reconnect your broker, then retry the benchmark. Your report is saved."
            )
        raise ValueError(
            "The benchmark could not be downloaded. Retry when the broker is available."
        )
    evidence = response["evidence"]
    validate_series(evidence)
    recorded = {"evidence": evidence, "acquisition_receipts": response["acquisition_receipts"]}
    if warmup_sessions != 1:
        recorded["calendar"] = calendar
    _validate_receipts(recorded["acquisition_receipts"])
    artifact = service.save_artifact(store, recorded)
    if checkpoint:
        checkpoint(
            {
                "phase": "benchmark",
                "binding": binding,
                "reference_artifact": calendar_id,
                "inputs_artifact": artifact,
            },
            {"completed": 1, "total": 1, "stage": "prices"},
        )
    return recorded
