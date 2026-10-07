"""Bounded durable stock-feature acquisition through the native market facade.

Per-symbol completed evidence is content addressed. Checkpoints carry references
and one active native acquisition state, never accumulated price arrays. The
shared native acquisition layer owns and closes its archive/client resources.
"""

from copy import deepcopy

from research.market_series import normalize_descriptor
from research.stock_features import (
    MAX_FEATURE_BARS,
    MAX_SYMBOLS,
    VERSION,
    WARMUP_SESSIONS,
    fingerprint,
    validate_bundle,
    validate_record,
)
from services import scanner_research_service as service


def _identity(evidence):
    return fingerprint(
        {
            key: evidence[key]
            for key in ("portfolio", "strategies", "signals", "snapshot", "versions")
        }
    )


def _symbols(evidence):
    result = sorted(
        {row["symbol"] for strategy in evidence["strategies"] for row in strategy["signals"]}
    )
    if not 1 <= len(result) <= MAX_SYMBOLS:
        raise ValueError(f"Stock-feature research supports at most {MAX_SYMBOLS} symbols")
    return result


def prepare_context(
    store, owner, evidence, *, saved, checkpoint, progress, cancelled, activity=None
):
    from services.research_benchmarks import prepare

    symbols = _symbols(evidence)
    binding = fingerprint(
        {
            "version": VERSION,
            "owner": owner,
            "execution_inputs_id": _identity(evidence),
            "symbols": symbols,
            "warmup_sessions": WARMUP_SESSIONS,
        }
    )
    recovering = saved is not None and saved.get("phase") == "stock_feature_prices"
    if recovering:
        if (
            set(saved)
            != {
                "phase",
                "version",
                "binding",
                "inputs_artifact",
                "completed_symbols",
                "current_symbol",
                "market_checkpoint",
                "complete",
                "state_id",
            }
            or saved.get("version") != VERSION
            or saved.get("binding") != binding
            or saved.get("state_id")
            != fingerprint({key: value for key, value in saved.items() if key != "state_id"})
            or not isinstance(saved["completed_symbols"], dict)
            or not set(saved["completed_symbols"]).issubset(symbols)
            or type(saved["complete"]) is not bool
        ):
            raise ValueError("Saved stock-feature preparation belongs to different inputs")
        recorded = service.read_artifact(store, saved["inputs_artifact"])
        if _identity(recorded) != _identity(evidence):
            raise ValueError("Saved stock-feature execution evidence changed")
        evidence = recorded
    retained = evidence.get("automatic_stock_features")
    if retained is not None:
        validate_bundle(retained)
        if set(symbols) - set(retained["series"]):
            raise ValueError("Saved stock features do not cover these signal symbols")
        if recovering and (
            not saved["complete"] or set(saved["completed_symbols"]) != set(symbols)
        ):
            raise ValueError("Saved stock-feature completion is inconsistent")
        return evidence
    if evidence.get("replay_origin") or evidence.get("parent_result_artifact"):
        raise ValueError("Exact replay needs the original saved stock-feature prices")
    sessions = evidence["snapshot"]["sessions"]
    if not sessions or len(symbols) * (len(sessions) + WARMUP_SESSIONS) > MAX_FEATURE_BARS:
        raise ValueError("Stock-feature history exceeds its total session-bar limit")
    if recovering and saved["complete"]:
        raise ValueError("Completed stock-feature evidence is missing")
    state = (
        deepcopy(saved)
        if recovering
        else {
            "phase": "stock_feature_prices",
            "version": VERSION,
            "binding": binding,
            "inputs_artifact": service.save_artifact(store, evidence),
            "completed_symbols": {},
            "current_symbol": None,
            "market_checkpoint": None,
            "complete": False,
        }
    )
    completed = state["completed_symbols"]
    expected_prefix = symbols[: len(completed)]
    if set(completed) != set(expected_prefix) or any(
        not isinstance(record, dict) or set(record) != {"inputs_artifact"}
        for record in completed.values()
    ):
        raise ValueError("Saved stock-feature symbol progress changed")
    remaining = symbols[len(completed) :]
    if state["current_symbol"] not in (None, remaining[0] if remaining else None):
        raise ValueError("Saved stock-feature acquisition belongs to another symbol")
    if state["market_checkpoint"] is not None and state["current_symbol"] is None:
        raise ValueError("Saved stock-feature acquisition has no active symbol")
    # Verify retained leaves before starting any new broker work on resume.
    feature_bars = 0
    for symbol, reference in completed.items():
        record = validate_record(symbol, service.read_artifact(store, reference["inputs_artifact"]))
        feature_bars += len(record["evidence"]["required_dates"])
        if feature_bars > MAX_FEATURE_BARS:
            raise ValueError("Stock-feature history exceeds its total session-bar limit")
    capital = evidence["portfolio"]["capital"]
    report_bounds = {
        "summary": {"initial_capital": capital},
        "equity_curve": [{"date": day, "equity": capital} for day in sessions],
    }

    def persist():
        state.pop("state_id", None)
        state["state_id"] = fingerprint(state)
        if activity:
            activity(
                {
                    "stage": "features",
                    "features": {
                        "total_symbols": len(symbols),
                        "completed_symbols": len(completed),
                    },
                    "prices": {"current_symbol": state["current_symbol"]},
                }
            )
        checkpoint(
            deepcopy(state), {"completed": len(completed), "total": len(symbols), "stage": "prices"}
        )

    for symbol in remaining:
        cancelled()
        if state["current_symbol"] is None:
            state["current_symbol"] = symbol
            state["market_checkpoint"] = None
            persist()
        if activity:
            activity({"stage": "features", "prices": {"current_symbol": symbol}})

        def save_market(native_state, counts):
            state["market_checkpoint"] = deepcopy(native_state)
            persist()

        record = prepare(
            store,
            owner,
            report_bounds,
            normalize_descriptor(
                {"symbol": symbol, "exchange": "NSE", "interval": "D", "role": "feature_warmup"}
            ),
            saved=state["market_checkpoint"],
            checkpoint=save_market,
            progress=lambda done, total: progress(
                len(completed) + min(1, max(0, done / max(1, total))), len(symbols)
            ),
            cancelled=cancelled,
            warmup_sessions=WARMUP_SESSIONS,
        )
        validate_record(symbol, record)
        feature_bars += len(record["evidence"]["required_dates"])
        if feature_bars > MAX_FEATURE_BARS:
            raise ValueError("Stock-feature history exceeds its total session-bar limit")
        completed[symbol] = {"inputs_artifact": service.save_artifact(store, record)}
        state["current_symbol"], state["market_checkpoint"] = None, None
        persist()
        progress(len(completed), len(symbols))
    cancelled()
    payload = {
        "version": VERSION,
        "series": {
            symbol: service.read_artifact(store, completed[symbol]["inputs_artifact"])
            for symbol in symbols
        },
    }
    bundle = validate_bundle({**payload, "id": fingerprint(payload)})
    result = {**evidence, "automatic_stock_features": bundle}
    state["inputs_artifact"] = service.save_artifact(store, result)
    state["complete"] = True
    persist()
    return result
