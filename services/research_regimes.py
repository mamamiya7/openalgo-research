"""Saved historical market-condition context through the native analysis worker."""

from research.market_conditions import VERSION, build_conditions
from research.market_series import normalize_descriptor, validate_series
from services import scanner_research_service as service
from services.research_benchmarks import _validate_receipts, prepare

DESCRIPTOR = normalize_descriptor(
    {"symbol": "NIFTY", "exchange": "NSE_INDEX", "interval": "D", "role": "benchmark"}
)
WARMUP = 160


def request_context():
    from research.regimes import recipe

    return {"version": VERSION, "recipe": recipe()}


def retained_conditions(store, artifact, parent_artifact):
    bundle = service.read_artifact(store, artifact)
    if (
        bundle.get("kind") != "portfolio_analysis"
        or bundle.get("parent_result_artifact") != parent_artifact
    ):
        raise ValueError("Saved market conditions belong to another report")
    market = bundle.get("result", {}).get("market_conditions_evidence")
    if not isinstance(market, dict) or market.get("context") != request_context():
        raise ValueError("The saved market-condition recipe changed")
    validate_series(market["evidence"])
    if market["evidence"]["descriptor"] != DESCRIPTOR:
        raise ValueError("The saved market-condition instrument changed")
    _validate_receipts(market["acquisition_receipts"])
    return market


def run_context(
    store, owner, original, *, saved=None, checkpoint=None, progress=None, cancelled=None
):
    try:
        market = prepare(
            store,
            owner,
            original,
            DESCRIPTOR,
            saved=saved,
            checkpoint=checkpoint,
            progress=progress,
            cancelled=cancelled,
            warmup_sessions=WARMUP,
        )
    except ValueError as exc:
        raise ValueError(str(exc).replace("benchmark", "market conditions")) from exc
    return {**market, "context": request_context()}


def enrich(result, original, market):
    result["market_conditions_evidence"] = market
    result["analysis"]["market_conditions"] = build_conditions(
        original, market["evidence"], market["calendar"]
    )
    later = original.get("validation", {}).get("result")
    if later:
        result["validation"]["market_conditions"] = build_conditions(
            later, market["evidence"], market["calendar"]
        )
