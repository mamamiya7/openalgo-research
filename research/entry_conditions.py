"""Versioned fixed causal entry rules; no files, services or retained caches."""

from copy import deepcopy

from research.condition_replay import normalize as normalize_rule
from research.portfolio import fingerprint
from research.regimes import recipe

VERSION = "research-entry-conditions-v1"
STOCK_VERSION = "research-stock-entry-conditions-v1"
INDICATOR_VERSION = "research-stock-entry-conditions-v2"
RELATIVE_VERSION = "research-stock-entry-conditions-v3"
JOINT_VERSION = "research-joint-entry-conditions-v1"
STOCK_VERSIONS = (STOCK_VERSION, INDICATOR_VERSION, RELATIVE_VERSION, JOINT_VERSION)


def stock_module(version):
    if version == JOINT_VERSION:
        from research import joint_stock_market

        return joint_stock_market
    if version == RELATIVE_VERSION:
        from research import stock_relative_strength

        return stock_relative_strength
    if version == INDICATOR_VERSION:
        from research import stock_indicators

        return stock_indicators
    from research import stock_features

    return stock_features


def normalize(value, strategies, engine="vectorbt"):
    if isinstance(value, dict) and value.get("version") in STOCK_VERSIONS:
        normalize_hypothesis = stock_module(value["version"]).normalize_hypothesis

        if engine != "vectorbt" or set(value) != {"version", "hypothesis"}:
            raise ValueError("Choose a supported stock condition and VectorBT execution")
        hypothesis = normalize_hypothesis(value["hypothesis"])
        if hypothesis["id"] == "none":
            raise ValueError("Remove the stock condition for an unfiltered strategy")
        return {"version": value["version"], "hypothesis": hypothesis}
    if engine != "vectorbt":
        raise ValueError("Entry conditions currently need the VectorBT portfolio engine")
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "recipe", "rules"}
        or value["version"] != VERSION
        or value["recipe"] != recipe()
        or not isinstance(value["rules"], list)
        or not 1 <= len(value["rules"]) <= len(strategies)
    ):
        raise ValueError("Choose supported saved entry conditions and their recorded recipe")
    rules = [normalize_rule(rule, strategies) for rule in value["rules"]]
    ids = [rule["strategy_id"] for rule in rules]
    if len(set(ids)) != len(ids):
        raise ValueError("Use at most one entry condition for each strategy")
    by_id = {rule["strategy_id"]: rule for rule in rules}
    return {
        "version": VERSION,
        "recipe": deepcopy(recipe()),
        "rules": [
            {key: by_id[row["id"]][key] for key in ("strategy_id", "dimension", "regime")}
            for row in strategies
            if row["id"] in by_id
        ],
    }


def markers(strategies, descriptor):
    """Bind report settings to rules without changing legacy unfiltered hashes."""
    if descriptor and descriptor.get("version") in STOCK_VERSIONS:
        stock_markers = stock_module(descriptor["version"]).markers

        return stock_markers(strategies, descriptor["hypothesis"])
    rows = deepcopy(strategies)
    by_id = {rule["strategy_id"]: rule for rule in descriptor["rules"]} if descriptor else {}
    for row in rows:
        row.pop("entry_condition", None)
        rule = by_id.get(row["id"])
        if rule:
            row["entry_condition"] = {
                "dimension": rule["dimension"],
                "regime": rule["regime"],
                "recipe_id": descriptor["recipe"]["id"],
            }
    return rows


def apply(evidence, market):
    """Gate original signals, never filter a recorded trade ledger."""
    if evidence["portfolio"]["entry_conditions"].get("version") in STOCK_VERSIONS:
        stock_apply = stock_module(evidence["portfolio"]["entry_conditions"]["version"]).apply

        descriptor = normalize(
            evidence["portfolio"]["entry_conditions"],
            evidence["strategies"],
            evidence["portfolio"]["engine"],
        )
        current = deepcopy(evidence)
        current["frozen_prices"] = True
        rows, receipt = stock_apply(current, market, descriptor["hypothesis"])
        current["strategies"] = markers(rows, descriptor)
        current["entry_condition_gates"] = [receipt]
        context_key = (
            "automatic_stock_market_context"
            if descriptor["version"] == JOINT_VERSION
            else "automatic_stock_features"
        )
        current[context_key] = market
        return current
    from research.condition_replay import apply as gate
    from research.market_series import validate_series
    from services.research_regimes import DESCRIPTOR, request_context

    descriptor = normalize(
        evidence["portfolio"]["entry_conditions"],
        evidence["strategies"],
        evidence["portfolio"]["engine"],
    )
    if market.get("context") != request_context():
        raise ValueError("The saved entry-condition market recipe changed")
    validate_series(market["evidence"])
    if market["evidence"]["descriptor"] != DESCRIPTOR:
        raise ValueError("Entry conditions need the recorded Nifty market history")
    current, receipts = deepcopy(evidence), []
    current["frozen_prices"] = True
    for rule in descriptor["rules"]:
        rows, receipt = gate(current, market, rule)
        current["strategies"] = rows
        receipts.append(receipt)
    current["strategies"] = markers(current["strategies"], descriptor)
    current["entry_condition_gates"] = receipts
    current["entry_condition_market"] = market
    return current


def enrich(report, evidence):
    descriptor = evidence["portfolio"].get("entry_conditions")
    if not descriptor:
        return
    report["strategies"] = markers(report["strategies"], descriptor)
    report["entry_conditions"] = deepcopy(descriptor)
    if descriptor.get("version") in STOCK_VERSIONS:
        gate = evidence["entry_condition_gates"][0]
        report["automatic_stock_condition"] = {
            "hypothesis": deepcopy(descriptor["hypothesis"]),
            "bundle_id": evidence[
                "automatic_stock_market_context"
                if descriptor["version"] == JOINT_VERSION
                else "automatic_stock_features"
            ]["id"],
            "gate_id": gate["id"],
            "counts": deepcopy(gate["counts"]),
        }
        report["entry_condition_counts"] = []
    else:
        report["entry_condition_counts"] = [
            {"strategy_id": row["condition"]["strategy_id"], **row["counts"]}
            for row in evidence["entry_condition_gates"]
        ]
    basis = report.get("evaluation_basis")
    if basis:
        comparison = basis["comparison"]
        comparison["execution"]["entry_conditions"] = descriptor
        comparison["id"] = fingerprint(
            {"version": basis["version"], **{k: v for k, v in comparison.items() if k != "id"}}
        )
