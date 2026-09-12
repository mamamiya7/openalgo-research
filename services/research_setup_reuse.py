"""Read-only changes to an immutable chosen setup before native draft admission.

This helper owns no launch, data preparation or metadata writes. The chosen-setup
service owns the revision fence, displaced draft and frozen parent version.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date

from services import research_library as library
from services import scanner_research_service as service


def _source_summary(source):
    details = source.get("receipt", {})
    if not isinstance(details, dict) or details.get("input_type") == "portfolio":
        raise ValueError("Choose a saved signal CSV, not a combined portfolio")
    for field in ("signal_count", "symbol_count"):
        if type(details.get(field)) is not int or not 1 <= details[field] <= 25000:
            raise ValueError("This saved CSV has no valid signal receipt. Upload the CSV again.")
    try:
        first, last = (date.fromisoformat(details[key]) for key in ("date_from", "date_to"))
        if first > last or details["symbol_count"] > details["signal_count"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "This saved CSV has no valid signal receipt. Upload the CSV again."
        ) from None
    return {
        "source_id": source["id"],
        "filename": details.get("filename") or source.get("filename") or "Saved CSV",
        "signal_count": details["signal_count"],
        "symbol_count": details["symbol_count"],
        "date_from": first.isoformat(),
        "date_to": last.isoformat(),
    }


def reuse_draft(store, owner, draft, *, replacements=None, mode="backtest"):
    """Return a detached reusable draft and compact, explicit input changes.

    Replacement keys identify existing strategies, never array positions. All
    sources are owner-checked through native receipts. Replacing a source resets
    the shared date filter so the new input reaches the existing setup preflight;
    reserve/evaluate intent remains for that preflight to derive fresh periods.
    Search starts empty even if an older chosen draft retained obsolete axes.
    """
    if mode not in ("backtest", "optimize"):
        raise ValueError("Choose backtest or optimize for this setup")
    replacements = {} if replacements is None else replacements
    if not isinstance(replacements, dict) or len(replacements) > 8:
        raise ValueError("Replace at most eight strategy CSVs")
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or not re.fullmatch(r"[a-f0-9]{32}", value)
        for key, value in replacements.items()
    ):
        raise ValueError("Use a strategy identifier and a saved CSV for each replacement")

    result = library.normalize_draft(store, owner, draft)
    rows = result["portfolio"]["strategies"]
    if not rows or any(not row["source_id"] for row in rows):
        raise ValueError("The chosen setup needs its original signal CSVs")
    if set(replacements) - {row["id"] for row in rows}:
        raise ValueError("Choose a strategy from this saved setup")

    summaries = {
        source_id: _source_summary(source) for source_id, source in result["sources"].items()
    }
    changes = {"sources": [], "fields": [], "rules_unchanged": True}
    receipts = deepcopy(result["sources"])
    for row in rows:
        original = row["source_id"]
        replacement = replacements.get(row["id"], original)
        if replacement == original:
            continue
        if replacement not in receipts:
            receipts[replacement] = service.source_receipt(store, owner, replacement)
        source = receipts[replacement]
        if source.get("provenance", {}).get("synthetic") is True:
            raise ValueError("Choose a signal CSV for OpenAlgo prices, not a synthetic demo")
        summary = _source_summary(source)
        changes["sources"].append(
            {
                "strategy_id": row["id"],
                "name": row["name"],
                "before": summaries[original],
                "after": summary,
            }
        )
        row["source_id"] = replacement

    if changes["sources"]:
        for key in ("date_from", "date_to"):
            if key in result["portfolio"]:
                changes["fields"].append(
                    {"key": key, "before": result["portfolio"].pop(key), "after": None}
                )
    original_mode = "optimize" if result["optimizing"] else "backtest"
    if original_mode != mode:
        changes["fields"].append({"key": "mode", "before": original_mode, "after": mode})
    if any(row["search"] for row in rows):
        changes["fields"].append(
            {
                "key": "search",
                "before": "Previous ranges",
                "after": "Choose ranges in Settings" if mode == "optimize" else None,
            }
        )
    for row in rows:
        row["search"] = {}
    result["optimizing"] = mode == "optimize"
    result["portfolio"].pop("optimization", None)
    # Keep only referenced receipts; no stale saved source can leak into reuse.
    result["sources"] = {row["source_id"]: receipts[row["source_id"]] for row in rows}
    library._bounded_json(result)
    return {"draft": result, "changes": changes}
