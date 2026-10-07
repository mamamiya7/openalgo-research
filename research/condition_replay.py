"""Causal entry gates over all saved signals before a native account simulation.

No trade is removed from an existing ledger. Original signals and price exclusions
survive; VectorBT must calculate the account again after the gate is applied.
"""

from copy import deepcopy
from datetime import datetime

from research.market_conditions import LABELS
from research.portfolio import fingerprint

VERSION = "research-condition-replay-v1"
ALLOWED = {"trend": {"up", "down", "range"}, "volatility": {"normal", "high"}}


def normalize(value, strategies):
    if not isinstance(value, dict) or set(value) != {"strategy_id", "dimension", "regime"}:
        raise ValueError("Choose one strategy and one supported entry condition")
    dimension, regime = value["dimension"], value["regime"]
    if not isinstance(dimension, str) or dimension not in ALLOWED:
        raise ValueError("Choose a trend or volatility condition")
    if not isinstance(regime, str) or regime not in ALLOWED[dimension]:
        raise ValueError("Choose a supported trend or volatility condition")
    if not isinstance(value["strategy_id"], str) or value["strategy_id"] not in {
        item["id"] for item in strategies
    }:
        raise ValueError("Choose a strategy from this saved portfolio")
    return {**value, "label": LABELS[dimension][regime]}


def apply(evidence, market, condition):
    from research.connectors.vectorbt_portfolio import _schedule
    from research.regimes import classify, recipe

    if evidence["portfolio"]["engine"] != "vectorbt":
        raise ValueError("Condition replay currently requires a saved VectorBT portfolio")
    if not evidence.get("frozen_prices"):
        raise ValueError("Condition replay requires exact saved execution prices")
    if market.get("context", {}).get("recipe") != recipe():
        raise ValueError("The saved market-condition recipe changed")
    selected = normalize(condition, evidence["strategies"])
    snapshot, calendar = evidence["snapshot"], market["calendar"]
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    calendar_days = set(calendar["sessions"])
    market_days = market["evidence"]["required_dates"]
    scheduled, decisions = {}, set()
    for strategy in evidence["strategies"]:
        if strategy["id"] != selected["strategy_id"]:
            continue
        for i, signal in enumerate(strategy["signals"]):
            entry, reason = _schedule(signal, snapshot, strategy["config"], timeline, index, minute)
            decision, scheduled_at = None, None
            if entry >= 0 and not reason:
                day = timeline[entry][:10]
                hours = calendar.get("session_hours", {}).get(day)
                if day in calendar_days and hours and market_days[0] <= day <= market_days[-1]:
                    opening = f"{day}T{hours['open']}:00+05:30"
                    closing = f"{day}T{hours['close']}:00+05:30"
                    scheduled_at = timeline[entry] if minute else opening
                    # Daily context is the same throughout a recorded session.
                    # Classify once per session, never use its unfinished close.
                    if (
                        datetime.fromisoformat(opening)
                        <= datetime.fromisoformat(scheduled_at)
                        < datetime.fromisoformat(closing)
                    ):
                        decision = opening
                        decisions.add(decision)
            scheduled[i] = (entry, reason, decision, scheduled_at)
    classified = classify(market["evidence"], sorted(decisions)) if decisions else None
    by_time = {row["decision_at"]: row for row in classified["timeline"]} if classified else {}
    counts = dict.fromkeys(
        ("allowed", "filtered", "unknown", "original_excluded", "pending", "unaffected"), 0
    )
    strategies, membership = [], []
    for strategy in evidence["strategies"]:
        signals = []
        for i, original in enumerate(strategy["signals"]):
            signal = deepcopy(original)
            item = {
                "strategy_id": strategy["id"],
                "signal_index": i,
                "symbol": original["symbol"],
                "signal_date": original["date"],
                "source_row": original.get("row"),
            }
            if strategy["id"] != selected["strategy_id"]:
                state = "unaffected"
            else:
                entry, reason, decision, scheduled_at = scheduled[i]
                row = by_time.get(decision)
                item.update(
                    scheduled_at=scheduled_at,
                    decision_at=decision,
                    observed=row[selected["dimension"]] if row else "unknown",
                    input_id=row["input_id"] if row else None,
                )
                if reason:
                    state = "original_excluded"
                elif entry < 0:
                    state = "pending"
                elif row is None or row[selected["dimension"]] == "unknown":
                    state = "unknown"
                    signal["research_exclusion"] = (
                        "Entry condition unavailable in saved market history"
                    )
                elif row[selected["dimension"]] != selected["regime"]:
                    state = "filtered"
                    signal["research_exclusion"] = f"Entry condition not met: {selected['label']}"
                else:
                    state = "allowed"
            counts[state] += 1
            membership.append({**item, "state": state})
            signals.append(signal)
        strategies.append({**deepcopy(strategy), "signals": signals})
    receipt = {
        "version": VERSION,
        "condition": selected,
        "recipe_id": recipe()["id"],
        "market_evidence_id": market["evidence"]["id"],
        "execution_inputs_id": fingerprint(
            {
                key: evidence[key]
                for key in ("portfolio", "strategies", "signals", "snapshot", "versions")
            }
        ),
        "counts": counts,
        "membership": membership,
        "conditioned_strategies_id": fingerprint(strategies),
        "timing": "Entry gate from index closes available before the recorded session opening",
        "unknown_policy": "exclude; preserve original price exclusions and pending entries",
    }
    return strategies, {**receipt, "id": fingerprint(receipt)}
