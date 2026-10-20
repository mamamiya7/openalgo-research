"""Versioned finite momentum expansion over the existing retained price context.

The v8 catalogue remains an unchanged prefix. Four explicit stock choices add
10/60-session absolute momentum at thresholds0/2; no indicator discovery,
download, price buffer or learned transform is introduced.
"""

from copy import deepcopy

from research import joint_stock_market as joint
from research import stock_features as finite
from research import stock_indicators as indicators
from research import stock_relative_strength as stock
from research.portfolio import fingerprint

FEATURE_VERSION = "research-expanded-stock-market-features-v1"
ENTRY_VERSION = "research-joint-entry-conditions-v2"
MARKETS = joint.MARKETS
CAUSAL_RECEIPT_VERSION = "research-expanded-causal-membership-v1"
_EXIT_ONLY = (
    "cost_bps",
    "hold_minutes",
    "hold_sessions",
    "stop_pct",
    "target_pct",
    "trailing_enabled",
    "trailing_pct",
)


def additional_stock_hypotheses():
    return [
        {
            "id": f"momentum_{lookback}_gt{threshold}",
            "family": "momentum",
            "label": f"{lookback}-session momentum above {threshold}%",
            "lookback": lookback,
            "threshold": float(threshold),
        }
        for lookback in (10, 60)
        for threshold in (0, 2)
    ]


def _joint_rule(rule, market):
    return {
        "id": f"{rule['id']}__{market['id']}",
        "family": f"{rule['family']}:{market['id']}",
        "label": rule["label"]
        if market["condition"] is None
        else f"{rule['label']} · {market['label']}",
        "stock_hypothesis": deepcopy(rule),
        "market": deepcopy(market["condition"]),
    }


def catalog():
    return joint.catalog() + [
        _joint_rule(rule, market) for market in MARKETS for rule in additional_stock_hypotheses()
    ]


def normalize_hypothesis(value):
    if isinstance(value, dict):
        for expected in catalog():
            if value.get("id") == expected["id"] and fingerprint(value) == fingerprint(expected):
                return expected
    raise ValueError("Choose a supported expanded stock and market condition")


def recipe():
    return {
        **joint.recipe(),
        "version": FEATURE_VERSION,
        "parent_feature_version": joint.FEATURE_VERSION,
        "gate_identity": {
            "version": CAUSAL_RECEIPT_VERSION,
            "component_source": "Exact recorded context, hypothesis, execution calendar/timeline, original cohort and native entry-schedule settings",
            "component_receipt": "Complete causal membership/features/recipe; omit only id, execution_inputs_id and conditioned_strategies_id",
            "exit_only_fields": list(_EXIT_ONLY),
            "full_account_binding": "Outer execution_inputs_id and conditioned_strategies_id retain the complete actual account settings and exits",
            "cache_policy": "Component identities are independent of which exit-only settings first populated the job-local gate cache",
        },
        "absolute_momentum_expansion": {
            "lookbacks": [10, 60],
            "thresholds_pct": [0.0, 2.0],
            "formula": "100 * (last_close / close_lookback_sessions_ago - 1)",
            "observations": "lookback + 1 consecutive prior completed session closes",
            "comparison": "Strict above; relative or absolute tolerance1e-12 counts as equal and does not pass",
            "availability": finite.recipe()["availability"],
            "warmup_sessions": finite.WARMUP_SESSIONS,
        },
    }


def markers(settings, hypothesis, bundle_id=None):
    selected = normalize_hypothesis(hypothesis)
    rows = deepcopy(settings)
    for row in rows:
        row.pop("entry_condition", None)
        if selected["id"] != "none":
            row["entry_condition"] = {"version": ENTRY_VERSION, "hypothesis": deepcopy(selected)}
    return rows


def _apply_stock(evidence, bundle, hypothesis, validator):
    if hypothesis["id"] not in {row["id"] for row in additional_stock_hypotheses()}:
        return stock._apply_with_validator(evidence, bundle, hypothesis, validator)
    # The public joint normalizer already checks the exact registered stock
    # descriptor. Retain this check at the private seam as well.
    selected = next(
        (
            row
            for row in additional_stock_hypotheses()
            if row["id"] == hypothesis["id"] and fingerprint(row) == fingerprint(hypothesis)
        ),
        None,
    )
    if selected is None:
        raise ValueError("Choose a recorded expanded momentum hypothesis")
    return finite._apply(
        evidence,
        bundle,
        selected,
        feature_fn=finite._feature,
        markers_fn=lambda rows, rule: markers(rows, _joint_rule(rule, MARKETS[0])),
        compare_fn=indicators._compare,
        calculation_version=FEATURE_VERSION,
        feature_recipe=recipe(),
        bundle_validator=validator,
    )


def _combine(evidence, bundle_id, selected, feature_recipe, stock_receipt, market_receipts):
    # Native component receipts bind complete account settings. Those bindings
    # cannot be reused across exit-only candidates: the shared cache rebinds the
    # outer receipt, not its nested opaque IDs. Canonicalize only this new
    # feature version's causal components, while retaining every full account
    # binding in the outer receipt. The v8 identity contract stays untouched.
    causal_source = fingerprint(
        {
            "version": CAUSAL_RECEIPT_VERSION,
            "bundle_id": bundle_id,
            "hypothesis": selected,
            "engine": evidence["portfolio"]["engine"],
            "execution_schedule": {
                "interval": evidence["snapshot"]["provenance"]["interval"],
                **{
                    key: evidence["snapshot"][key]
                    for key in ("sessions", "session_hours", "timeline")
                    if key in evidence["snapshot"]
                },
            },
            "versions": evidence["versions"],
            "signals": evidence["signals"],
            "schedule": [
                {
                    "id": row["id"],
                    "signals": row["signals"],
                    "config": {
                        key: value for key, value in row["config"].items() if key not in _EXIT_ONLY
                    },
                }
                for row in evidence["strategies"]
            ],
        }
    )

    def causal_receipt(receipt):
        identity = fingerprint(
            {
                "version": CAUSAL_RECEIPT_VERSION,
                "causal_inputs_id": causal_source,
                "receipt": {
                    key: value
                    for key, value in receipt.items()
                    if key not in ("id", "execution_inputs_id", "conditioned_strategies_id")
                },
            }
        )
        return {**receipt, "id": identity}

    rows, receipt = joint._combine(
        evidence,
        bundle_id,
        selected,
        feature_recipe,
        causal_receipt(stock_receipt),
        [causal_receipt(row) for row in market_receipts],
        calculation_version=FEATURE_VERSION,
        markers_fn=markers,
    )
    receipt.pop("id")
    receipt["causal_inputs_id"] = causal_source
    return rows, {**receipt, "id": fingerprint(receipt)}


def apply(evidence, bundle, hypothesis):
    from research.condition_replay import apply as market_gate
    from research.stock_market_context import market_view, stock_view, validate_bundle

    selected = normalize_hypothesis(hypothesis)
    feature_recipe = recipe()
    validate_bundle(bundle)
    _, stock_receipt = _apply_stock(
        evidence, stock_view(bundle), selected["stock_hypothesis"], stock.validate_bundle
    )
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


class PreparedJointContext(joint.PreparedJointContext):
    """Reuse the same one-owner detached projection and explicit close lifetime."""

    __slots__ = ()

    def apply(self, evidence, bundle, hypothesis):
        return self._apply_with(
            evidence,
            bundle,
            hypothesis,
            normalizer=normalize_hypothesis,
            recipe_fn=recipe,
            stock_gate=_apply_stock,
            combine_fn=_combine,
        )
