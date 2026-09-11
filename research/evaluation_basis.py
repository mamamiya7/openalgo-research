"""Recorded evaluation inputs, independent of settings selection or fill outcomes.

``verified`` means the descriptor was built from this evaluation's retained inputs.
It does not certify broker prices, cross-engine parity or an untouched holdout.
All state is per-call; hashing streams bounded, already-admitted research inputs.
"""

from __future__ import annotations

import hashlib
import json

from research.portfolio import requirement_strategies
from research.portfolio_coverage import POLICY_VERSION as COHORT_POLICY

VERSION = "research-evaluation-basis-v1"
IDENTITIES = (
    "source_id",
    "cohort_id",
    "observations_id",
    "prices_id",
    "calendar_id",
    "instruments_id",
)


def _digest(value):
    digest = hashlib.sha256()
    for part in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(
        value
    ):
        digest.update(part.encode())
    return digest.hexdigest()


def _fields(value, keys):
    return {key: value[key] for key in keys if key in value}


def build_evaluation_basis(evidence, *, period):
    """Describe prepared period evidence without altering its signals or policies.

    Order and source-row identity matter for the shared-cash competition rule.
    Full CSV/source artifact hashes do not belong here: they include other periods.
    Admission uses the existing maximum-window prepared mask, never realized fills.
    """
    from research.connectors.vectorbt_portfolio import _schedule

    if period not in ("selection", "evaluation", "full"):
        raise ValueError("Choose a recorded evaluation period")
    snapshot, portfolio = evidence["snapshot"], evidence["portfolio"]
    provenance = snapshot["provenance"]
    interval = provenance["interval"]
    timeline = snapshot["timeline" if interval == "1m" else "sessions"]
    if not timeline:
        raise ValueError("Evaluation identity needs recorded observations")
    index = {key: i for i, key in enumerate(timeline)}
    requirements = {
        row["id"]: row["config"]
        for row in requirement_strategies(portfolio, evidence["strategies"])
    }
    sources, cohort, symbols = [], [], set()
    counts = {"eligible": 0, "excluded": 0, "pending": 0}
    for strategy in evidence["strategies"]:
        observations, admission = [], []
        for signal in strategy["signals"]:
            symbols.add(signal["symbol"])
            observations.append(_fields(signal, ("symbol", "date", "timestamp", "row")))
            entry, reason = _schedule(
                signal, snapshot, requirements[strategy["id"]], timeline, index, interval == "1m"
            )
            state = "excluded" if reason else "eligible" if entry >= 0 else "pending"
            counts[state] += 1
            # The retained evidence/ledger owns exact exclusion reasons. Their
            # wording (possibly referring to an unused later holding window)
            # cannot change the identity of an otherwise identical period mask.
            admission.append({"state": state, "entry": timeline[entry] if entry >= 0 else None})
        sources.append({"strategy_id": strategy["id"], "signals": observations})
        cohort.append({"strategy_id": strategy["id"], "admission": admission})

    source_id = _digest(sources)
    observation_slots = set(timeline)
    sessions = snapshot["sessions"]
    # Exclude other periods and diagnostic/acquisition timestamps. Retain every
    # frozen bar on the actual evaluation timeline, including absence via its keys.
    prices = {
        symbol: {
            key: bar
            for key, bar in snapshot["bars"].get(symbol, {}).items()
            if key in observation_slots
        }
        for symbol in sorted(symbols)
    }
    identities = {
        "source_id": source_id,
        "cohort_id": _digest(
            {
                "source_id": source_id,
                "policy": evidence.get("signal_coverage", {}).get("policy_version", COHORT_POLICY),
                "cohort": cohort,
            }
        ),
        "observations_id": _digest({"interval": interval, "timeline": timeline}),
        "prices_id": _digest(
            {
                "bars": prices,
                "basis": _fields(
                    provenance,
                    (
                        "exchange",
                        "interval",
                        "native_price_policy",
                        "adjustment_basis",
                        "synthetic",
                    ),
                ),
            }
        ),
        "calendar_id": _digest(
            {
                "sessions": sessions,
                "session_hours": {
                    day: snapshot.get("session_hours", {}).get(day) for day in sessions
                },
                "basis": _fields(
                    provenance,
                    ("exchange", "calendar_basis", "calendar_admission", "temporal_version"),
                ),
            }
        ),
        "instruments_id": _digest(
            {
                "instruments": {
                    symbol: snapshot.get("instruments", {}).get(symbol)
                    for symbol in sorted(symbols)
                },
                "eligibility_policy": snapshot.get("instrument_eligibility", {}).get(
                    "policy_version"
                ),
            }
        ),
    }
    period_info = {
        "kind": period,
        "from": timeline[0],
        "to": timeline[-1],
        "observations": len(timeline),
        "interval": interval,
    }
    comparison = {
        "period": period,
        "currency": "INR",  # This connector contract admits long NSE cash equity only.
        "capital": float(portfolio["capital"]),
        "execution": _fields(
            evidence["versions"],
            ("engine", "engine_version", "adapter_version", "policy_version", "portfolio_version"),
        ),
        "costs": [
            {
                "strategy_id": row["id"],
                "cost_bps": float(row["config"].get("cost_bps", 0)),
                "slippage_bps": float(row["config"].get("slippage_bps", 0)),
            }
            for row in evidence["strategies"]
        ],
    }
    return {
        "version": VERSION,
        "status": "verified",
        **identities,
        "evidence_id": _digest({"version": VERSION, **identities}),
        "period": period_info,
        "admission": counts,
        "comparison": {**comparison, "id": _digest({"version": VERSION, **comparison})},
    }


def comparison_status(left, right):
    """Gate like-for-like deltas; mismatched reports may still be inspected."""
    if any(
        not isinstance(item, dict)
        or item.get("version") != VERSION
        or item.get("status") != "verified"
        or not all(item.get(key) for key in (*IDENTITIES, "evidence_id"))
        or not isinstance(item.get("comparison"), dict)
        or not item["comparison"].get("id")
        for item in (left, right)
    ):
        return {"compatible": False, "differences": ["unverified"]}
    differences = [key.removesuffix("_id") for key in IDENTITIES if left[key] != right[key]]
    for key in ("period", "currency", "capital", "execution", "costs"):
        if left["comparison"].get(key) != right["comparison"].get(key):
            differences.append(key)
    # IDs must agree as well: an unfamiliar future context cannot pass merely
    # because its displayed fields happen to match.
    if left["evidence_id"] != right["evidence_id"] and not differences:
        differences.append("evidence")
    if left["comparison"]["id"] != right["comparison"]["id"] and not differences:
        differences.append("comparison_context")
    return {"compatible": not differences, "differences": differences}
