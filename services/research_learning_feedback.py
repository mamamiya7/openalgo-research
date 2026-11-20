"""Compact observed feedback for diagnosis, never a validation or pruning rule."""

from collections import defaultdict
from copy import deepcopy

from research.economic_repetition import observed, trade_observation
from services.research_validation_scorecard import build as scorecard

VERSION = "research-learning-feedback-v1"


def summarize(report, *, job_id, origin_job_id=None, diagnosis=None):
    """Discard ledgers immediately after deriving bounded observational identities."""
    card = scorecard(report)
    summary = report.get("summary", {})
    conclusion = report.get("supervisor_validation", {})
    checks = {
        row["key"]: {"passed": row["passed"], "facts": deepcopy(row.get("facts", {}))}
        for row in card.get("checks", [])
    }
    feasibility = conclusion.get("feasibility", {})
    operating = conclusion.get("operating_validation", {})
    counts = feasibility.get("counts") or operating.get("decision", {}).get("capital_diagnosis")
    if (
        isinstance(counts, dict)
        and counts
        and all(type(v) is int and v >= 0 for v in counts.values())
    ):
        native_complete = bool(card.get("eligible"))
        native_blocked = (
            feasibility.get("version") == "research-feasibility-v1"
            and feasibility.get("scope") == "feasibility_only"
        )
        if native_complete or native_blocked:
            unresolved = {
                key: counts[key]
                for key in (
                    "cash_shortfall",
                    "whole_share_affordability",
                    "percentage_policy",
                    "unavailable_sizing_intent",
                    "unexplained_execution_limit",
                )
                if counts.get(key, 0)
            }
            repaired = bool(
                native_complete and counts.get("admitted_requests", 0) > 0 and not unresolved
            )
            checks["feasibility"] = {"passed": repaired, "facts": deepcopy(counts)}
            checks["operating_capacity"] = deepcopy(checks["feasibility"])
    return {
        "job_id": job_id,
        "origin_job_id": origin_job_id,
        "outcome": conclusion.get("outcome"),
        "normal_account_id": observed(report),
        "cash_trade_id": trade_observation(report),
        "capital": summary.get("initial_capital"),
        "net_pnl": summary.get("net_pnl"),
        "net_return_pct": summary.get("net_return_pct"),
        "closed_trades": summary.get("closed_trades"),
        "complete_validation": card.get("eligible", False),
        "checks": checks,
        "diagnosis": (
            {
                key: deepcopy(diagnosis[key])
                for key in ("target_ref", "cause", "prediction", "falsifier")
                if key in diagnosis
            }
            if diagnosis
            else None
        ),
        "repetition_witness": deepcopy(conclusion.get("no_effect")),
    }


def build(observations, *, branch_cards=None):
    if not isinstance(observations, list) or len(observations) > 24:
        raise ValueError("Learning feedback exceeds the existing24-experiment history")
    accounts, trades = defaultdict(list), defaultdict(list)
    by_job = {row["job_id"]: row for row in observations}
    reference_checks = {
        job_id: {
            check["key"]: {"passed": check["passed"], "facts": deepcopy(check.get("facts", {}))}
            for check in card.get("checks", [])
        }
        for job_id, card in (branch_cards or {}).items()
    }
    hypotheses = []
    for row in observations:
        # Blocked accounts may inform feasibility only, not performance patterns.
        if row["outcome"] != "blocked":
            for key, groups in (("normal_account_id", accounts), ("cash_trade_id", trades)):
                if row[key]:
                    groups[row[key]].append(row)
        diagnosis = row.get("diagnosis")
        if diagnosis:
            key = diagnosis["target_ref"].split("/", 1)[-1]
            origin = by_job.get(row.get("origin_job_id"), {})
            before = origin.get("checks", {}).get(key) or reference_checks.get(
                row.get("origin_job_id"), {}
            ).get(key)
            after = row["checks"].get(key)
            hypotheses.append(
                {
                    "job_id": row["job_id"],
                    "origin_job_id": row.get("origin_job_id"),
                    "diagnosis": diagnosis,
                    "target_check": key,
                    "before": before,
                    "after": after,
                    "outcome": row["outcome"],
                    "assessment": (
                        "target_still_failed"
                        if after and after["passed"] is False
                        else "target_passed_but_native_acceptance_still_required"
                        if after and after["passed"] is True
                        else "target_unmeasured_or_partial_rejection"
                    ),
                    "repetition_witness": row["repetition_witness"],
                }
            )

    def repeated(groups):
        return [
            {
                "observation_id": identity,
                "branches": [
                    {
                        key: row[key]
                        for key in (
                            "job_id",
                            "capital",
                            "net_pnl",
                            "net_return_pct",
                            "closed_trades",
                            "complete_validation",
                        )
                    }
                    for row in group
                ],
            }
            for identity, group in groups.items()
            if len(group) > 1
        ]

    return {
        "version": VERSION,
        "scope": "Observed historical accounts only; no independent validation or all-scenario equivalence",
        "normal_account_matches": repeated(accounts),
        "cash_trade_matches": repeated(trades),
        "hypothesis_results": hypotheses,
        "repetition_witnesses": [
            {"job_id": row["job_id"], "witness": row["repetition_witness"]}
            for row in observations
            if row["repetition_witness"]
        ],
        "policy": "Same cash trades at different capital can change percentage returns without finding better trades. A normal match alone cannot skip stress or risk validation. Use native failed-gate witnesses to reject repeated failures, never to accept a setup.",
    }
