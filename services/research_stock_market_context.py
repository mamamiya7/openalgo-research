"""Durable role-specific context over native stock and index acquisition.

The native acquisition services own archive/HTTP cleanup. This wrapper owns no
connections, executors or global cache. Checkpoints retain content references,
including the one active acquisition checkpoint, rather than copied price arrays.
"""

from copy import deepcopy

from research import stock_features, stock_relative_strength
from research.market_series import fingerprint
from research.stock_market_context import (
    KEY,
    MARKET_WARMUP_SESSIONS,
    VERSION,
    freeze_bundle,
    validate_bundle,
)
from services import research_stock_features
from services import scanner_research_service as service

PHASE = "stock_market_context_prices"


def _read(store, reference):
    value = service.read_artifact(store, reference)
    if fingerprint(value) != reference:
        raise ValueError("Saved stock-market artifact integrity changed")
    return value


def _stock_only(evidence):
    current = dict(evidence)
    incoming = current.get("automatic_stock_features")
    if incoming is not None:
        if incoming.get("version") == stock_relative_strength.VERSION:
            stock_relative_strength.validate_bundle(incoming)
            payload = {"version": stock_features.VERSION, "series": incoming["series"]}
            current["automatic_stock_features"] = {**payload, "id": fingerprint(payload)}
        else:
            stock_features.validate_bundle(incoming)
    return current


def execution_hours(store, evidence):
    """Use only recorded hours; old daily snapshots can reference their calendar.

    The daily acquisition assembler formerly omitted session_hours. Its saved
    native calendar already records them under a verified content address. Read
    that original reference without changing the execution artifact or identity.
    Missing/partial minute hours and partial daily maps are never inferred.
    """
    snapshot = evidence["snapshot"]
    if "session_hours" in snapshot:
        return snapshot["session_hours"]
    if snapshot.get("provenance", {}).get("interval") != "D" or not evidence.get(
        "reference_artifact"
    ):
        return {}
    calendar = _read(store, evidence["reference_artifact"])
    sessions, recorded = snapshot["sessions"], calendar.get("sessions", [])
    replay = bool(evidence.get("replay_origin") or evidence.get("parent_result_artifact"))
    start = recorded.index(sessions[0]) if sessions and sessions[0] in recorded else -1
    compatible = recorded == sessions or (
        replay and start >= 0 and recorded[start : start + len(sessions)] == sessions
    )
    if not compatible or (
        calendar.get("provenance", {}).get("calendar_basis")
        != snapshot.get("provenance", {}).get("calendar_basis")
    ):
        from research.failures import ResearchFailure

        raise ResearchFailure(
            "execution_calendar_reference_mismatch",
            "The saved execution calendar belongs to different sessions. "
            "Start a new run with these signals; this saved run is unchanged.",
        )
    return calendar.get("session_hours", {})


def _check_execution(bundle, evidence, symbols, *, store=None):
    validate_bundle(bundle)
    recorded, current = bundle["execution_sessions"], evidence["snapshot"]["sessions"]
    replay = bool(evidence.get("replay_origin") or evidence.get("parent_result_artifact"))
    matches = set(bundle["series"]) == set(symbols) and recorded == current
    if replay:
        start = recorded.index(current[0]) if current and current[0] in recorded else -1
        matches = (
            start >= 0
            and recorded[start : start + len(current)] == current
            and set(symbols).issubset(bundle["series"])
        )
    if not matches:
        raise ValueError("Saved stock-market context belongs to different execution inputs")
    from research.failures import check_execution_hours

    check_execution_hours(
        bundle["market"]["calendar"]["session_hours"], execution_hours(store, evidence), current
    )


def prepare_context(
    store, owner, evidence, *, saved, checkpoint, progress, cancelled, activity=None
):
    from services.research_regimes import run_context

    symbols = research_stock_features._symbols(evidence)
    incoming = evidence.get(KEY)
    if incoming is not None:
        _check_execution(incoming, evidence, symbols, store=store)
    recovering = saved is not None and saved.get("phase") == PHASE
    if saved is not None and not recovering and saved.get("phase") != "calculation":
        raise ValueError("Saved stock-market preparation belongs to different inputs")
    if (saved or {}).get("phase") == "calculation" and saved.get("calculation") is not None:
        if incoming is None:
            raise ValueError("Saved calculation needs the original stock-market context")
    if incoming is not None and not recovering:
        # Admission above checked the complete retained context and its execution
        # binding. No caller code runs in between. Recovery alone needs a new
        # binding and reference reads; ordinary saved calculation must not hash
        # the same full execution input and validate the same context twice.
        return evidence
    source_id = (
        saved.get("source_stock_bundle_id")
        if recovering
        else evidence.get("automatic_stock_features", {}).get("id")
    )
    binding = fingerprint(
        {
            "version": VERSION,
            "owner": owner,
            "execution_inputs_id": research_stock_features._identity(evidence),
            "source_stock_bundle_id": source_id,
            "stock_warmup": stock_features.WARMUP_SESSIONS,
            "market_warmup": MARKET_WARMUP_SESSIONS,
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
                "reference_artifact",
                "inputs_artifact",
                "stock_checkpoint",
                "market_checkpoint",
                "complete",
                "state_id",
            }
            or saved.get("version") != VERSION
            or saved.get("binding") != binding
            or saved.get("state_id")
            != fingerprint({k: v for k, v in saved.items() if k != "state_id"})
            or type(saved["complete"]) is not bool
            or any(
                saved[key] is not None
                and (not isinstance(saved[key], dict) or set(saved[key]) != {"inputs_artifact"})
                for key in ("stock_checkpoint", "market_checkpoint")
            )
        ):
            raise ValueError("Saved stock-market preparation belongs to different inputs")
        original = _read(store, saved["reference_artifact"])
        recorded = _read(store, saved["inputs_artifact"])
        if (
            any(
                research_stock_features._identity(value)
                != research_stock_features._identity(evidence)
                for value in (original, recorded)
            )
            or original.get("automatic_stock_features", {}).get("id") != source_id
        ):
            raise ValueError("Saved stock-market execution evidence changed")
        retained = recorded.get(KEY)
        if incoming is not None and (retained is None or incoming["id"] != retained["id"]):
            raise ValueError("Saved stock-market context changed")
        if evidence.get("automatic_stock_features", {}).get("id") not in (
            source_id,
            recorded.get("automatic_stock_features", {}).get("id"),
        ):
            raise ValueError("Saved stock-market stock evidence changed")
        evidence = recorded
        incoming = retained
    else:
        original = evidence
    if incoming is not None:
        _check_execution(incoming, evidence, symbols, store=store)
        if recovering and not saved["complete"]:
            raise ValueError("Saved stock-market completion is inconsistent")
        return evidence
    if evidence.get("replay_origin") or evidence.get("parent_result_artifact"):
        raise ValueError("Exact replay needs the original saved stock-market context")
    sessions = evidence["snapshot"]["sessions"]
    if (
        not sessions
        or len(symbols) * (len(sessions) + stock_features.WARMUP_SESSIONS)
        + (len(sessions) + MARKET_WARMUP_SESSIONS)
        > stock_features.MAX_FEATURE_BARS
    ):
        raise ValueError("Stock-market history exceeds its total session-bar limit")
    if recovering and saved["complete"]:
        raise ValueError("Completed stock-market evidence is missing")
    state = (
        deepcopy(saved)
        if recovering
        else {
            "phase": PHASE,
            "version": VERSION,
            "binding": binding,
            "source_stock_bundle_id": source_id,
            "reference_artifact": service.save_artifact(store, original),
            "inputs_artifact": service.save_artifact(store, evidence),
            "stock_checkpoint": None,
            "market_checkpoint": None,
            "complete": False,
        }
    )
    total = len(symbols) + 1

    def persist(completed):
        state.pop("state_id", None)
        state["state_id"] = fingerprint(state)
        checkpoint(deepcopy(state), {"completed": completed, "total": total, "stage": "prices"})

    child = {
        key: _read(store, state[key]["inputs_artifact"]) if state[key] is not None else None
        for key in ("stock_checkpoint", "market_checkpoint")
    }
    working = _stock_only(evidence)
    if child["market_checkpoint"] is not None and "automatic_stock_features" not in working:
        raise ValueError("Saved market acquisition has no completed stock evidence")

    def stock_checkpoint(native, counts):
        state["stock_checkpoint"] = {"inputs_artifact": service.save_artifact(store, native)}
        persist(counts["completed"])

    def stock_activity(event):
        if activity:
            event = deepcopy(event)
            if "features" in event:
                event["features"]["total_symbols"] = total
                event["features"].update(scanner_symbols=len(symbols), context_symbols=1)
            activity(event)

    working = research_stock_features.prepare_context(
        store,
        owner,
        working,
        saved=child["stock_checkpoint"],
        checkpoint=stock_checkpoint,
        progress=lambda done, _: progress(done, total),
        cancelled=cancelled,
        activity=stock_activity,
    )
    stock_bundle = working["automatic_stock_features"]
    if any(
        row["calendar"]["sessions"][stock_features.WARMUP_SESSIONS :] != sessions
        for row in stock_bundle["series"].values()
    ):
        raise ValueError("Stock context does not match the execution session window")
    state["inputs_artifact"] = service.save_artifact(store, working)
    persist(len(symbols))
    cancelled()
    if activity:
        activity(
            {
                "stage": "features",
                "features": {
                    "total_symbols": total,
                    "completed_symbols": len(symbols),
                    "scanner_symbols": len(symbols),
                    "context_symbols": 1,
                },
                "prices": {"current_symbol": "NIFTY"},
            }
        )

    def market_checkpoint(native, counts):
        state["market_checkpoint"] = {"inputs_artifact": service.save_artifact(store, native)}
        persist(len(symbols) + counts["completed"])

    capital = working["portfolio"]["capital"]
    bounds = {
        "summary": {"initial_capital": capital},
        "equity_curve": [{"date": day, "equity": capital} for day in sessions],
    }
    # This is the established 160-session regime preparation over the same
    # native index facade. Its calendar/receipt recovery is retained by reference.
    market = run_context(
        store,
        owner,
        bounds,
        saved=child["market_checkpoint"],
        checkpoint=market_checkpoint,
        progress=lambda done, count: progress(
            len(symbols) + min(1, max(0, done / max(1, count))), total
        ),
        cancelled=cancelled,
    )
    cancelled()
    market = {key: market[key] for key in ("evidence", "calendar", "acquisition_receipts")}
    bundle = freeze_bundle(stock_bundle["series"], market, sessions)
    result = {**original, KEY: bundle}
    _check_execution(bundle, result, symbols, store=store)
    state["inputs_artifact"] = service.save_artifact(store, result)
    state["complete"] = True
    persist(total)
    progress(total, total)
    if activity:
        activity(
            {
                "stage": "features",
                "features": {
                    "total_symbols": total,
                    "completed_symbols": total,
                    "scanner_symbols": len(symbols),
                    "context_symbols": 1,
                },
                "prices": {"current_symbol": None},
            }
        )
    return result
