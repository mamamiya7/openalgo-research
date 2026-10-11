"""Durable broker-backed stock and benchmark context through native acquisition.

The old per-stock preparation remains unchanged. This wrapper retains its state
by content address and adds one shared NIFTY daily series, never another price
downloader or a module-level cache. Checkpoints contain references and one active
native benchmark state rather than accumulated market arrays.
"""

from copy import deepcopy

from research.market_series import normalize_descriptor
from research.stock_features import MAX_FEATURE_BARS, WARMUP_SESSIONS, fingerprint
from research.stock_features import VERSION as STOCK_VERSION
from services import research_stock_features
from services import scanner_research_service as service

VERSION = "research-stock-context-v2"
PHASE = "stock_context_prices"


def prepare_context(
    store, owner, evidence, *, saved, checkpoint, progress, cancelled, activity=None
):
    from research.stock_relative_strength import validate_bundle
    from services.research_benchmarks import prepare

    symbols = research_stock_features._symbols(evidence)
    descriptor = normalize_descriptor(
        {"symbol": "NIFTY", "exchange": "NSE_INDEX", "interval": "D", "role": "benchmark"}
    )
    incoming = evidence.get("automatic_stock_features")
    if incoming is not None:
        if incoming.get("version") == VERSION:
            validate_bundle(incoming)
        else:
            research_stock_features.validate_bundle(incoming)
        if set(symbols) - set(incoming["series"]):
            raise ValueError("Saved stock context does not cover these signal symbols")
    recovering = saved is not None and saved.get("phase") == PHASE
    # A calculation checkpoint exists just before context acquisition begins.
    # Later calculation checkpoints must retain the original complete context.
    # The runner normalizes completed executable-price preparation to no state.
    if saved is not None and not recovering and saved.get("phase") != "calculation":
        raise ValueError("Saved stock-context preparation belongs to different inputs")
    if (saved or {}).get("phase") == "calculation" and saved.get("calculation") is not None:
        if incoming is None or incoming.get("version") != VERSION:
            raise ValueError("Saved calculation needs the original stock and benchmark context")
    origin_id = (
        saved.get("source_stock_bundle_id")
        if recovering
        else incoming.get("id")
        if incoming is not None
        else None
    )
    binding = fingerprint(
        {
            "version": VERSION,
            "owner": owner,
            "execution_inputs_id": research_stock_features._identity(evidence),
            "source_stock_bundle_id": origin_id,
            "benchmark": descriptor,
            "warmup_sessions": WARMUP_SESSIONS,
        }
    )
    if recovering:
        if (
            set(saved)
            != {
                "phase",
                "version",
                "binding",
                "source_stock_bundle_id",
                "inputs_artifact",
                "stock_checkpoint",
                "benchmark_checkpoint",
                "complete",
                "state_id",
            }
            or saved.get("version") != VERSION
            or saved.get("binding") != binding
            or saved.get("state_id")
            != fingerprint({key: value for key, value in saved.items() if key != "state_id"})
            or type(saved["complete"]) is not bool
            or (
                saved["stock_checkpoint"] is not None
                and (
                    not isinstance(saved["stock_checkpoint"], dict)
                    or set(saved["stock_checkpoint"]) != {"inputs_artifact"}
                )
            )
            or (
                saved["benchmark_checkpoint"] is not None
                and not isinstance(saved["benchmark_checkpoint"], dict)
            )
        ):
            raise ValueError("Saved stock-context preparation belongs to different inputs")
        recorded = service.read_artifact(store, saved["inputs_artifact"])
        if research_stock_features._identity(recorded) != research_stock_features._identity(
            evidence
        ):
            raise ValueError("Saved stock-context execution evidence changed")
        recorded_bundle = recorded.get("automatic_stock_features")
        if incoming is not None and incoming["id"] not in (
            origin_id,
            recorded_bundle.get("id") if recorded_bundle else None,
        ):
            raise ValueError("Saved stock-context prices changed")
        evidence = recorded
        incoming = recorded_bundle
    if incoming is not None and incoming.get("version") == VERSION:
        validate_bundle(incoming)
        if recovering and not saved["complete"]:
            raise ValueError("Saved stock-context completion is inconsistent")
        return evidence
    if evidence.get("replay_origin") or evidence.get("parent_result_artifact"):
        raise ValueError("Exact replay needs the original saved stock and benchmark context")
    if (len(symbols) + 1) * (
        len(evidence["snapshot"]["sessions"]) + WARMUP_SESSIONS
    ) > MAX_FEATURE_BARS:
        raise ValueError("Stock and benchmark history exceeds its total session-bar limit")
    if recovering and saved["complete"]:
        raise ValueError("Completed stock-context evidence is missing")
    state = (
        deepcopy(saved)
        if recovering
        else {
            "phase": PHASE,
            "version": VERSION,
            "binding": binding,
            "source_stock_bundle_id": origin_id,
            "inputs_artifact": service.save_artifact(store, evidence),
            "stock_checkpoint": None,
            "benchmark_checkpoint": None,
            "complete": False,
        }
    )
    total = len(symbols) + 1

    def persist(completed):
        state.pop("state_id", None)
        state["state_id"] = fingerprint(state)
        checkpoint(deepcopy(state), {"completed": completed, "total": total, "stage": "prices"})

    stock_saved = None
    if state["stock_checkpoint"] is not None:
        reference = state["stock_checkpoint"]["inputs_artifact"]
        stock_saved = service.read_artifact(store, reference)
        if fingerprint(stock_saved) != reference:
            raise ValueError("Saved stock-context checkpoint integrity changed")
    if state["benchmark_checkpoint"] is not None and (
        incoming is None or incoming.get("version") != STOCK_VERSION
    ):
        raise ValueError("Saved benchmark context has no completed stock evidence")

    def stock_checkpoint(native_state, counts):
        state["stock_checkpoint"] = {"inputs_artifact": service.save_artifact(store, native_state)}
        persist(counts["completed"])

    def stock_activity(event):
        if activity:
            event = deepcopy(event)
            if "features" in event:
                event["features"]["total_symbols"] = total
            activity(event)

    stock_evidence = research_stock_features.prepare_context(
        store,
        owner,
        evidence,
        saved=stock_saved,
        checkpoint=stock_checkpoint,
        progress=lambda done, _: progress(done, total),
        cancelled=cancelled,
        activity=stock_activity,
    )
    state["inputs_artifact"] = service.save_artifact(store, stock_evidence)
    persist(len(symbols))
    sessions = stock_evidence["snapshot"]["sessions"]
    capital = stock_evidence["portfolio"]["capital"]
    bounds = {
        "summary": {"initial_capital": capital},
        "equity_curve": [{"date": day, "equity": capital} for day in sessions],
    }
    cancelled()
    if activity:
        activity(
            {
                "stage": "features",
                "features": {"total_symbols": total, "completed_symbols": len(symbols)},
                "prices": {"current_symbol": "NIFTY"},
            }
        )

    def benchmark_checkpoint(native_state, counts):
        state["benchmark_checkpoint"] = deepcopy(native_state)
        persist(len(symbols) + counts["completed"])

    try:
        benchmark = prepare(
            store,
            owner,
            bounds,
            descriptor,
            saved=state["benchmark_checkpoint"],
            checkpoint=benchmark_checkpoint,
            progress=lambda done, count: progress(
                len(symbols) + min(1, max(0, done / max(1, count))), total
            ),
            cancelled=cancelled,
            warmup_sessions=WARMUP_SESSIONS,
        )
    except ValueError as exc:
        if str(exc) == "Reconnect your broker, then retry the benchmark. Your report is saved.":
            raise ValueError(
                "Reconnect your broker, then resume research. Stock price progress is saved."
            ) from exc
        if str(exc) == (
            "The benchmark could not be downloaded. Retry when the broker is available."
        ):
            raise ValueError(
                "NIFTY prices could not be downloaded. Resume when your broker is available. "
                "Stock price progress is saved."
            ) from exc
        raise
    cancelled()
    payload = {
        "version": VERSION,
        "series": stock_evidence["automatic_stock_features"]["series"],
        "benchmark": benchmark,
    }
    bundle = validate_bundle({**payload, "id": fingerprint(payload)})
    result = {**stock_evidence, "automatic_stock_features": bundle}
    state["inputs_artifact"] = service.save_artifact(store, result)
    state["complete"] = True
    persist(total)
    progress(total, total)
    if activity:
        activity(
            {
                "stage": "features",
                "features": {"total_symbols": total, "completed_symbols": total},
                "prices": {"current_symbol": None},
            }
        )
    return result
