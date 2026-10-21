"""Predeclared search-path sensitivity on one frozen development data set.

The primary seed is never replaced by a lucky repeat. Repeats can only veto it.
These overlapping training observations do not correct adaptive selection bias
or estimate the probability of future profit. No resources are retained here.
"""

import math
import re
from copy import deepcopy

from research.recommendation_evidence import BLOCK_LENGTHS, MIN_DAYS, _interval, _pair
from research.report_contract import fingerprint

VERSION = "research-search-seed-evidence-v1"
PLAN_VERSION = "research-search-seed-plan-v1"
ACCOUNT_VERSION = "research-search-seed-account-v1"
SEEDS = (0, 1, 2)
REPEAT_FITS = ("search_seed1", "search_seed2")
MIN_EDGE_FRACTION = 0.5
LIMITATIONS = [
    "Same-data search repeats test search-path sensitivity, not independent replication or search-adjusted confidence.",
    "All searches share the prespecified screening programme; only adaptive refinements depend on the seed.",
    "Seed 0 stays primary. Repeats may reject a changed setup; they never choose a better-looking seed.",
]


def policy(*, screening_proposals=50):
    if type(screening_proposals) is not int or not 1 <= screening_proposals <= 50:
        raise ValueError("Search-repeat screening count is outside its fixed bounds")
    value = {
        "version": PLAN_VERSION,
        "seeds": list(SEEDS),
        "primary_seed": 0,
        "data_period": "search",
        "proposals_per_search": 68,
        "screening_proposals_per_search": screening_proposals,
        "adaptive_proposals_per_search": 68 - screening_proposals,
        "maximum_additional_accounts": 136,
        "minimum_edge_fraction": MIN_EDGE_FRACTION,
        "minimum_sessions": MIN_DAYS,
        "minimum_closed_trades": 5,
        "minimum_entry_dates": 5,
        "policy": "Three fixed-seed training searches on the same frozen data, domains, capital, limits and costs. All winners need native support, positive paired lower ranges and positive return-minus-drawdown advantage; the smallest advantage must be at least half the largest. Repeat winners cannot replace the primary choice. Earlier failures skip repeats; missing evidence keeps unchanged settings.",
        "limitations": list(LIMITATIONS),
    }
    return {**value, "id": fingerprint(value)}


def account_facts(report, plan):
    """Seal actual native support and audit once, reusable across search seeds."""
    from research.automatic_research import _entry_dates

    body = {
        "version": ACCOUNT_VERSION,
        "plan_id": plan["id"],
        "basis_id": fingerprint(report["evaluation_basis"]),
        "support": {
            "closed_trades": report["summary"]["closed_trades"],
            "entry_dates": _entry_dates(report),
        },
        "constraints": deepcopy(report["portfolio_constraint_evidence"]),
    }
    return {**body, "id": fingerprint(body)}


def assess(plan, runs=None, baseline=None):
    """Verify three native retained winners; no final evidence is accepted."""
    if not isinstance(plan, dict) or plan != policy(
        screening_proposals=plan.get("screening_proposals_per_search")
    ):
        raise ValueError("The frozen search-repeat policy changed")
    value = {
        "version": VERSION,
        "plan_id": plan["id"],
        "status": "not_run",
        "qualified": False,
        "reasons": [],
        "runs": [],
        "min_edge_fraction": None,
        "limitations": list(LIMITATIONS),
    }

    def finish():
        return {**value, "id": fingerprint(value)}

    if runs is None:
        value["reasons"] = [
            "Earlier development checks did not support a changed setup; search repeats were skipped"
        ]
        return finish()
    try:
        if not isinstance(runs, list) or len(runs) != len(SEEDS):
            raise ValueError("All three declared search winners are required")
        bindings = []
        sparse = weak = False
        for seed, run in zip(SEEDS, runs, strict=True):
            if (
                run.get("seed") != seed
                or type(run.get("seed")) is not int
                or type(run.get("proposals")) is not int
                or type(run.get("adaptive_proposals")) is not int
                or run.get("proposals") != 68
                or run.get("adaptive_proposals") != plan["adaptive_proposals_per_search"]
                or type(run.get("unique_accounts")) is not int
                or not 1 <= run["unique_accounts"] <= 68
                or not isinstance(run.get("search_id"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", run["search_id"])
            ):
                raise ValueError("Search-repeat history is incomplete or incompatible")
            shard = run.get("account")
            pair = _pair(shard, baseline, period="search")
            facts = shard.get("search_seed_evidence")
            if (
                not isinstance(facts, dict)
                or facts != run.get("facts")
                or facts.get("version") != ACCOUNT_VERSION
                or facts.get("plan_id") != plan["id"]
                or facts.get("basis_id") != fingerprint(shard["evaluation_basis"])
                or facts.get("id") != fingerprint({k: v for k, v in facts.items() if k != "id"})
            ):
                raise ValueError("Search-repeat account facts changed")
            support, audit = facts["support"], facts["constraints"]
            if any(
                type(support.get(k)) is not int or support[k] < 0
                for k in ("closed_trades", "entry_dates")
            ):
                raise ValueError("Search-repeat support is invalid")
            if (
                audit.get("status") != "supported"
                or audit.get("id") != fingerprint({k: v for k, v in audit.items() if k != "id"})
                or audit.get("feature_id")
                != shard["evaluation_basis"]["comparison"]["execution"]
                .get("portfolio_features", {})
                .get("id")
            ):
                raise ValueError("Search-repeat native constraint evidence is unavailable")
            bindings.append((shard["binding"], shard["account"]))
            enough = (
                pair["days"] >= MIN_DAYS
                and support["closed_trades"] >= 5
                and support["entry_dates"] >= 5
            )
            intervals = (
                [_interval(pair["_edges"], length) for length in BLOCK_LENGTHS] if enough else []
            )
            lower = min((row["lower_bps"] for row in intervals), default=None)
            passed = enough and pair["passes"] and lower is not None and lower > 0
            sparse = sparse or not enough
            weak = weak or not passed
            value["runs"].append(
                {
                    "seed": seed,
                    "config_id": shard["config_id"],
                    "proposals": 68,
                    "adaptive_proposals": plan["adaptive_proposals_per_search"],
                    "unique_accounts": run["unique_accounts"],
                    "search_id": run["search_id"],
                    "return_pct": pair["candidate_return_pct"],
                    "max_drawdown_pct": pair["candidate_max_drawdown_pct"],
                    "edge_pct": pair["return_minus_drawdown_edge_pct"],
                    "lower_edge_pct": lower / 100 if lower is not None else None,
                    "evidence_id": shard["id"],
                    "support": deepcopy(support),
                    "passes": passed,
                }
            )
        if any(item != bindings[0] for item in bindings):
            raise ValueError("Search repeats used different frozen account inputs")
        edges = [row["edge_pct"] for row in value["runs"]]
        if all(math.isfinite(edge) and edge > 0 for edge in edges):
            value["min_edge_fraction"] = min(edges) / max(edges)
        stable = (
            value["min_edge_fraction"] is not None
            and value["min_edge_fraction"] >= MIN_EDGE_FRACTION
        )
        value["qualified"] = not sparse and not weak and stable
        value["status"] = (
            "supported" if value["qualified"] else "insufficient" if sparse else "not_supported"
        )
        if sparse:
            value["reasons"].append(
                "A search repeat has too few comparable development sessions or completed trades"
            )
        elif weak:
            value["reasons"].append(
                "A search repeat did not support an improvement over unchanged settings"
            )
        if not stable:
            value["reasons"].append("The improvement depends too strongly on the search path")
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        value.update(status="unsupported", qualified=False, reasons=[str(error)])
    return finish()
