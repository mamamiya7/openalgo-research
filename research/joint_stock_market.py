"""Finite causal stock AND market gates over one retained native context.

Each component judges the original opportunities independently; unknown inputs
dominate false inputs. The shared-cash engine then calculates the gated account.
No downloader, process or global cache is owned here. A prepared v8 context
retains one bounded private snapshot for the lifetime of its calculation job.
"""

from copy import deepcopy

from research import stock_relative_strength as stock
from research.gate_input_binding import (
    _BINDING,
    FIELDS,
    copy_gate_signal,
    execution_inputs_id,
    freeze_gate_signals,
)
from research.market_series import fingerprint as receipt_fingerprint
from research.owned_entry_features import is_immutable_json
from research.portfolio import fingerprint
from research.regimes import recipe as market_recipe

FEATURE_VERSION = "research-joint-stock-market-features-v1"
ENTRY_VERSION = "research-joint-entry-conditions-v1"
MARKETS = (
    {"id": "all", "label": "Any recorded market", "condition": None},
    {
        "id": "trend_up",
        "label": "Rising Nifty trend",
        "condition": {"dimension": "trend", "regime": "up"},
    },
    {
        "id": "volatility_normal",
        "label": "Normal Nifty volatility",
        "condition": {"dimension": "volatility", "regime": "normal"},
    },
    {
        "id": "volatility_high",
        "label": "High Nifty volatility",
        "condition": {"dimension": "volatility", "regime": "high"},
    },
)


def catalog():
    return [
        {
            "id": "none"
            if rule["id"] == "none" and market["id"] == "all"
            else f"{rule['id']}__{market['id']}",
            "family": f"{rule['family']}:{market['id']}",
            "label": rule["label"]
            if market["condition"] is None
            else f"{rule['label']} · {market['label']}",
            "stock_hypothesis": deepcopy(rule),
            "market": deepcopy(market["condition"]),
        }
        for market in MARKETS
        for rule in stock.catalog()
    ]


def normalize_hypothesis(value):
    if isinstance(value, dict):
        for expected in catalog():
            if value.get("id") == expected["id"] and fingerprint(value) == fingerprint(expected):
                return expected
    raise ValueError("Choose a supported joint stock and market condition")


def recipe():
    from research.stock_market_context import VERSION

    return {
        "version": FEATURE_VERSION,
        "context_version": VERSION,
        "stock": stock.recipe(),
        "market": market_recipe(),
        "combination": "Stock AND one registered market dimension; both judged on original signals",
        "availability": "Observed completed sessions available before actual entry; no future close",
        "unknown": "Unknown dominates filtered; original price exclusions and pending entries retained",
        "stock_warmup_sessions": 100,
        "market_warmup_sessions": 160,
        "market_presets": deepcopy(list(MARKETS)),
    }


def markers(settings, hypothesis, bundle_id=None):
    selected = normalize_hypothesis(hypothesis)
    rows = deepcopy(settings)
    for row in rows:
        row.pop("entry_condition", None)
        if selected["id"] != "none":
            row["entry_condition"] = {"version": ENTRY_VERSION, "hypothesis": deepcopy(selected)}
    return rows


def apply(evidence, bundle, hypothesis):
    from research.condition_replay import apply as market_gate
    from research.stock_market_context import market_view, stock_view, validate_bundle

    selected = normalize_hypothesis(hypothesis)
    feature_recipe = recipe()  # Also fences the stock SDK version.
    validate_bundle(bundle)
    _, stock_receipt = stock.apply(evidence, stock_view(bundle), selected["stock_hypothesis"])
    market_receipts = []
    if selected["market"] is not None:
        market = market_view(bundle)
        for strategy in evidence["strategies"]:
            _, receipt = market_gate(
                evidence, market, {"strategy_id": strategy["id"], **selected["market"]}
            )
            market_receipts.append(receipt)
    return _combine(
        evidence, bundle["id"], selected, feature_recipe, stock_receipt, market_receipts
    )


def _combine(
    evidence,
    bundle_id,
    selected,
    feature_recipe,
    stock_receipt,
    market_receipts,
    *,
    calculation_version=FEATURE_VERSION,
    markers_fn=markers,
):
    """Identical joint membership and receipt construction for both input paths."""
    binding = _BINDING.get()
    share_owned_rows = binding is not None and all(
        evidence[field] is binding[0][field] for field in FIELDS
    )
    membership = {
        (row["strategy_id"], row["signal_index"]): row for row in stock_receipt["membership"]
    }
    market_rows = {}
    for receipt in market_receipts:
        market_rows.update(
            {
                (row["strategy_id"], row["signal_index"]): row
                for row in receipt["membership"]
                if row["strategy_id"] == receipt["condition"]["strategy_id"]
            }
        )
    counts = dict.fromkeys(("allowed", "filtered", "unknown", "original_excluded", "pending"), 0)
    rows, combined = [], []
    for original in evidence["strategies"]:
        signals = []
        for index, signal in enumerate(original["signals"]):
            key = (original["id"], index)
            first = membership[key]
            second = market_rows.get(key)
            states = [first["state"]] + ([second["state"]] if second else [])
            if "original_excluded" in states:
                state = "original_excluded"
            elif "pending" in states:
                state = "pending"
            elif "unknown" in states:
                state = "unknown"
            elif "filtered" in states:
                state = "filtered"
            elif all(value == "allowed" for value in states):
                state = "allowed"
            else:
                raise ValueError("The joint condition returned an unsupported membership state")
            # The private native binding owns immutable input rows. If the
            # gate adds no exclusion, retaining that row avoids copying and
            # then freezing an identical entry-feature tree. Public mutable
            # inputs and rows receiving an exclusion still detach normally.
            retained = (
                signal
                if share_owned_rows
                and state not in ("unknown", "filtered")
                and is_immutable_json(signal)
                else copy_gate_signal(signal)
            )
            if state == "unknown":
                retained["research_exclusion"] = (
                    first.get("reason")
                    if first["state"] == "unknown"
                    else "Market condition unavailable in saved history"
                )
            elif state == "filtered":
                retained["research_exclusion"] = (
                    f"Joint entry condition not met: {selected['label']}"
                )
            counts[state] += 1
            combined.append(
                {
                    "strategy_id": original["id"],
                    "signal_index": index,
                    "symbol": signal["symbol"],
                    "signal_date": signal["date"],
                    "source_row": signal.get("row"),
                    "scheduled_at": first.get("scheduled_at") or (second or {}).get("scheduled_at"),
                    "decision_at": first.get("decision_at") or (second or {}).get("decision_at"),
                    "stock_state": first["state"],
                    "market_state": second["state"] if second else "not_required",
                    "state": state,
                }
            )
            signals.append(retained)
        rows.append(
            {
                **deepcopy({key: value for key, value in original.items() if key != "signals"}),
                "signals": freeze_gate_signals(signals),
            }
        )
    rows = markers_fn(rows, selected)
    receipt = {
        "version": calculation_version,
        "calculation_version": calculation_version,
        "hypothesis": selected,
        "bundle_id": bundle_id,
        "execution_inputs_id": execution_inputs_id(evidence),
        "conditioned_strategies_id": fingerprint(rows),
        "counts": counts,
        "membership": combined,
        "stock_gate_id": stock_receipt["id"],
        "market_gate_ids": [row["id"] for row in market_receipts],
        "feature_recipe": feature_recipe,
        "timing": feature_recipe["availability"],
        "unknown_policy": feature_recipe["unknown"],
    }
    # This fresh bounded scientific receipt contains membership and descriptors,
    # while account/strategy bindings above retain their separate checked path.
    return rows, {**receipt, "id": receipt_fingerprint(receipt)}


class PreparedJointContext:
    """Bounded v8 job-local context; public legacy gates remain fully validating.

    Construction detaches and validates once. The supplied bundle object is an
    identity capability, not a hash-based cache key: later edits to that caller's
    object cannot change the privately owned prices. No projection or source
    dictionary is exposed, and every returned gate/receipt is a fresh value.
    """

    __slots__ = ("__source", "__views", "__weakref__")

    def __init__(self, bundle):
        from research.stock_market_context import _PreparedViews

        views = _PreparedViews(bundle)
        self.__source = bundle
        self.__views = views

    def apply(self, evidence, bundle, hypothesis):
        return self._apply_with(evidence, bundle, hypothesis)

    def _apply_with(
        self,
        evidence,
        bundle,
        hypothesis,
        *,
        normalizer=normalize_hypothesis,
        recipe_fn=recipe,
        stock_gate=None,
        combine_fn=_combine,
    ):
        """Private dependency seam; callers never receive the owned price views."""
        if self.__views is None:
            raise ValueError("The prepared stock-market context has been released")
        if bundle is not self.__source:
            raise ValueError("Prepared stock-market gates require the original source object")
        selected = normalizer(hypothesis)
        feature_recipe = recipe_fn()
        stock_receipt = self._component_receipt(
            "stock",
            evidence,
            selected["stock_hypothesis"],
            lambda: self.__views.apply_stock(
                evidence, selected["stock_hypothesis"], _gate=stock_gate
            )[1],
        )
        market_receipts = []
        if selected["market"] is not None:
            for strategy in evidence["strategies"]:
                condition = {"strategy_id": strategy["id"], **selected["market"]}
                receipt = self._component_receipt(
                    "market",
                    evidence,
                    condition,
                    lambda condition=condition: self.__views.apply_market(evidence, condition)[1],
                )
                market_receipts.append(receipt)
        return combine_fn(
            evidence,
            self.__views.bundle_id,
            selected,
            feature_recipe,
            stock_receipt,
            market_receipts,
        )

    def _component_receipt(self, kind, evidence, descriptor, producer):
        return producer()

    def close(self):
        if self.__views is not None:
            self.__views.close()
            self.__views = None
        self.__source = None

    def __enter__(self):
        if self.__views is None:
            raise ValueError("The prepared stock-market context has been released")
        return self

    def __exit__(self, *_error):
        self.close()
