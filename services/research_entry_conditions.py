"""Prepare one pinned native Nifty context outside the broker-facing web process."""

from services import scanner_research_service as service


def prepare_context(store, owner, evidence, *, saved, checkpoint, progress, cancelled):
    from research.market_series import validate_series
    from services.research_regimes import DESCRIPTOR, request_context, run_context

    retained = evidence.get("entry_condition_market")
    if retained is not None:
        from services.research_benchmarks import _validate_receipts

        validate_series(retained["evidence"])
        _validate_receipts(retained["acquisition_receipts"])
        if (
            retained.get("context") != request_context()
            or retained["evidence"]["descriptor"] != DESCRIPTOR
        ):
            raise ValueError("The saved entry-condition market inputs changed")
        return evidence
    if evidence.get("frozen_prices") and evidence.get("replay_origin"):
        raise ValueError("Exact replay needs the original saved entry-condition market history")
    inputs_id = service.save_artifact(store, evidence)
    sessions = evidence["snapshot"]["sessions"]
    capital = evidence["portfolio"]["capital"]
    # The existing reporting acquisition path only needs bounds here; these
    # constant marks are not simulated returns or published research evidence.
    bounds = {
        "summary": {"initial_capital": capital},
        "equity_curve": [{"date": day, "equity": capital} for day in sessions],
    }

    def persist(market_state, counts):
        checkpoint(
            {
                "phase": "condition_prices",
                "inputs_artifact": inputs_id,
                "market_checkpoint": market_state,
            },
            {**counts, "completed": 58, "total": 100, "stage": "prices"},
        )

    context = run_context(
        store,
        owner,
        bounds,
        saved=(saved or {}).get("market_checkpoint")
        if (saved or {}).get("phase") == "condition_prices"
        else None,
        checkpoint=persist,
        progress=progress,
        cancelled=cancelled,
    )
    return {**evidence, "entry_condition_market": context}
