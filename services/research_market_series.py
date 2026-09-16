"""Acquire bounded auxiliary prices through the ordinary OpenAlgo history/archive."""

import time

from research.market_series import freeze_series, plan_series
from services.research_historify import NativeHistorifyArchive
from services.research_native_prices import acquire_market_series_prices


def acquire_series(
    descriptor,
    required_dates,
    calendar,
    *,
    archive_path,
    receipts_dir,
    credentials=None,
    prior=None,
    checkpoint=None,
    progress=None,
    activity=None,
    cancelled=None,
    history=None,
    max_requests=500,
    max_seconds=1800,
    clock=time.monotonic,
):
    """Return frozen evidence and a separate durable, bounded acquisition state.

    Missing candles stay absent. The caller owns job continuation and receipt
    persistence; neither this scope nor the native archive retains connections.
    Volume/open interest are not admitted as observed features because legacy
    archive zeros do not establish that a broker supplied those fields.
    """
    from services.research_acquisition import acquisition_receipts

    plan = plan_series(descriptor, required_dates, calendar)
    descriptor = plan["descriptor"]
    archive = NativeHistorifyArchive(
        archive_path, interval=descriptor["interval"], exchange=descriptor["exchange"]
    )
    snapshot = acquire_market_series_prices(
        plan,
        calendar,
        reader=archive.read,
        writer=archive.write,
        credentials=credentials,
        archive_dir=receipts_dir,
        prior=prior,
        checkpoint=checkpoint,
        progress=progress,
        activity=activity,
        cancelled=cancelled,
        history=history,
        max_requests=max_requests,
        max_seconds=max_seconds,
        clock=clock,
    )
    evidence = freeze_series(
        descriptor, required_dates, calendar, snapshot["bars"][descriptor["symbol"]]
    )
    return {
        "evidence": evidence,
        "checkpoint": snapshot["acquisition_checkpoint"],
        "batch_pending": snapshot["provenance"]["batch_pending"],
        "hard_failures": snapshot["provenance"]["hard_failures"],
        "acquisition_receipts": acquisition_receipts(snapshot, receipts_dir),
    }
