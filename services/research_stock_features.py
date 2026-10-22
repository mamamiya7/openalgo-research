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

_CACHE_CHECKPOINT_BATCH_SIZE = 16


def _cache_only_checkpoint(native_state, symbol):
    """Identify a complete native cache read; every broker boundary stays durable."""
    if _CACHE_CHECKPOINT_BATCH_SIZE <= 1 or not isinstance(native_state, dict):
        return False
    if set(native_state) != {
        "phase",
        "binding",
        "reference_artifact",
        "acquisition",
        "acquisition_receipts",
    } or any(
        not isinstance(native_state[key], str) or len(native_state[key]) != 64
        for key in ("binding", "reference_artifact")
    ):
        return False
    acquisition = native_state.get("acquisition")
    if not isinstance(acquisition, dict):
        return False
    progress = acquisition.get("progress", {})
    sources, bars = acquisition.get("source_counts", {}), acquisition.get("bars", {})
    if not all(isinstance(value, dict) for value in (progress, sources, bars)):
        return False
    counts = sources.get(symbol, {})
    required = progress.get("required_dates")
    return (
        native_state.get("phase") == "benchmark"
        and progress.get("phase") == "checking_prices"
        and type(required) is int
        and required > 0
        and progress.get("covered_dates") == required
        and acquisition.get("batch_pending") is False
        and acquisition.get("hard_failures") == []
        and acquisition.get("transport_attempts") == []
        and acquisition.get("completed_windows") == []
        and set(sources) == {symbol}
        and counts == {"cached": required, "downloaded": 0}
        and set(bars) == {symbol}
        and isinstance(bars[symbol], dict)
        and len(bars[symbol]) == required
        and isinstance(acquisition.get("receipts"), list)
        and all(isinstance(row, dict) and not row.get("broker") for row in acquisition["receipts"])
    )


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
    from services.research_market_series import StockFeaturePreparationScope

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
    volume_required = any(
        row.get("execution_policy", {}).get("capacity_mode") == "prior20_broker_reported"
        for row in evidence["strategies"]
    )
    with StockFeaturePreparationScope(
        remaining,
        sessions,
        **({"volume_root": store.root / "volume-provenance"} if volume_required else {}),
    ) as scope:
        # Verify retained leaves before starting any new broker work on resume.
        feature_bars = 0
        for symbol, reference in completed.items():
            record = validate_record(
                symbol, service.read_artifact(store, reference["inputs_artifact"])
            )
            scope.calendar(sessions[0], sessions[-1], WARMUP_SESSIONS, recorded=record["calendar"])
            feature_bars += len(record["evidence"]["required_dates"])
            if feature_bars > MAX_FEATURE_BARS:
                raise ValueError("Stock-feature history exceeds its total session-bar limit")
        capital = evidence["portfolio"]["capital"]
        report_bounds = {
            "summary": {"initial_capital": capital},
            "equity_curve": [{"date": day, "equity": capital} for day in sessions],
        }

        def announce():
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

        def persist():
            state.pop("state_id", None)
            state["state_id"] = fingerprint(state)
            announce()
            checkpoint(
                deepcopy(state),
                {"completed": len(completed), "total": len(symbols), "stage": "prices"},
            )

        cached_pending = 0
        for symbol in remaining:
            cancelled()
            if state["current_symbol"] is None:
                state["current_symbol"] = symbol
                state["market_checkpoint"] = None
                if not cached_pending:
                    persist()
            if activity:
                activity({"stage": "features", "prices": {"current_symbol": symbol}})

            cache_only = False
            cache_identity = None

            def save_market(native_state, counts, *, _symbol=symbol):
                nonlocal cache_only, cache_identity, cached_pending
                if _cache_only_checkpoint(native_state, _symbol):
                    cache_only = True
                    cache_identity = (
                        native_state.get("binding"),
                        native_state.get("reference_artifact"),
                    )
                    return
                if (
                    cache_only
                    and set(native_state)
                    == {"phase", "binding", "reference_artifact", "inputs_artifact"}
                    and native_state["phase"] == "benchmark"
                    and (native_state["binding"], native_state["reference_artifact"])
                    == cache_identity
                    and isinstance(native_state["inputs_artifact"], str)
                    and len(native_state["inputs_artifact"]) == 64
                    and counts.get("completed") == 1
                    and counts.get("total") == 1
                ):
                    # Benchmark admission already validated and durably saved its
                    # complete record. Keep this reference in the bounded group.
                    return
                cache_only = False
                cache_identity = None
                state["market_checkpoint"] = deepcopy(native_state)
                persist()
                cached_pending = 0

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
                scope=scope,
            )
            validate_record(symbol, record)
            scope.finish(symbol)
            scope.calendar(sessions[0], sessions[-1], WARMUP_SESSIONS, recorded=record["calendar"])
            feature_bars += len(record["evidence"]["required_dates"])
            if feature_bars > MAX_FEATURE_BARS:
                raise ValueError("Stock-feature history exceeds its total session-bar limit")
            completed[symbol] = {"inputs_artifact": service.save_artifact(store, record)}
            state["current_symbol"], state["market_checkpoint"] = None, None
            if cache_only:
                cached_pending += 1
            if not cache_only or cached_pending >= _CACHE_CHECKPOINT_BATCH_SIZE:
                persist()
                cached_pending = 0
            else:
                # Only recovery publication is grouped. Keep visible symbol
                # progress current, and still validate every retained record.
                announce()
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
