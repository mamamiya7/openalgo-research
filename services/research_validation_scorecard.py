"""Compact development-only observations from completed native validation.

No engine, provider, storage or acceptance decision is owned here. The caller
must verify the immutable report/job binding before presenting these facts.
Unknown or later-period evidence is deliberately unavailable, never zero.
"""

from math import isfinite

from research.report_contract import fingerprint

VERSION = "research-validation-scorecard-v1"
SCOPE = "same_history_development"
SUMMARY_FIELDS = (
    "initial_capital",
    "net_return_pct",
    "max_drawdown_pct",
    "closed_trades",
    "net_pnl",
)


def _number(value):
    return type(value) in (int, float) and isfinite(value)


def _summary(value):
    return {key: value[key] for key in SUMMARY_FIELDS if _number(value.get(key))}


def _reasons(value):
    if not isinstance(value, list):
        return []
    return [item[:240] for item in value[:3] if isinstance(item, str)]


def _identity(value, depth=0):
    """Bounded period identity only: never export arbitrary period payloads."""
    if isinstance(value, str) and len(value) <= 128:
        return value
    if depth < 2 and isinstance(value, list) and len(value) <= 8:
        return [_identity(item, depth + 1) for item in value]
    if depth < 2 and isinstance(value, dict) and len(value) <= 8:
        if any(not isinstance(key, str) or len(key) > 64 for key in value):
            raise ValueError("Invalid period identity")
        return {key: _identity(item, depth + 1) for key, item in value.items()}
    raise ValueError("Unavailable period identity")


def _native(report):
    if not isinstance(report, dict):
        raise ValueError("No completed native report")
    validation = report.get("supervisor_validation", {})
    operating = validation.get("operating_validation", {})
    if (
        validation.get("version") != "research-supervisor-validation-v1"
        or operating.get("version") != "research-operating-validation-v1"
        or validation.get("outcome") not in ("rejected", "validated")
        or operating.get("outcome") not in ("rejected", "validated")
        or validation.get("independently_validated") is not False
        or operating.get("independently_validated") is not False
        or operating.get("selection_changed") is not False
        or "chronological" not in operating
        or operating["chronological"] is not None
        or report.get("independently_validated") is True
        or report.get("validation_mode", "exploratory_only") != "exploratory_only"
        or validation.get("no_effect")
        or operating.get("no_effect")
        or report.get("portfolio_constraint_evidence", {}).get("status") != "supported"
    ):
        raise ValueError("Only completed exploratory native validation is comparable")
    if (
        not isinstance(validation.get("native_report_id"), str)
        or len(validation["native_report_id"]) != 64
        or operating.get("selected_report_id") != validation["native_report_id"]
        or not validation.get("selected_config_id")
        or operating.get("selected_config_id") != validation["selected_config_id"]
        or operating.get("id") != fingerprint({k: v for k, v in operating.items() if k != "id"})
        or validation.get("id") != fingerprint({k: v for k, v in validation.items() if k != "id"})
    ):
        raise ValueError("Native validation receipts are missing or changed")
    decision, design = operating.get("decision", {}), report.get("automatic_design", {})
    from research.automatic_decision import descriptor as decision_policy
    from research.automatic_design import descriptor as design_policy

    policies = decision_policy(), design_policy()
    if (
        decision.get("version") != "research-automatic-decision-v1"
        or decision.get("policy_id") != policies[0]["id"]
        or design.get("version") != "research-automatic-design-v1"
        or design.get("policy_id") != policies[1]["id"]
        or decision.get("independently_validated") is not False
        or design.get("independently_validated") is not False
        or type(design.get("qualified")) is not bool
        or type(decision.get("historical_candidate")) is not bool
    ):
        raise ValueError("Native policy measurements are incomplete or unknown")
    checks = decision.get("checks", [])
    if not isinstance(checks, list) or len(checks) != 2:
        raise ValueError("Both complete native challenge checks are required")
    checks = {row["kind"]: row for row in checks if isinstance(row, dict) and "kind" in row}
    if set(checks) != {"higher_costs", "neighboring_settings"}:
        raise ValueError("Native challenge identities are incomplete")
    neighbors = checks["neighboring_settings"]
    tested, passing = neighbors.get("tested"), neighbors.get("passing")
    if (
        type(tested) is not int
        or not 0 <= tested <= 4
        or type(passing) is not int
        or not 0 <= passing <= tested
        or (tested > 0 and not _number(neighbors.get("median_return_edge_pct")))
        or type(operating.get("accounts")) is not int
        or operating.get("accounts") != 9 + tested
        or any(type(row.get("passed")) is not bool for row in checks.values())
        or not _number(checks["higher_costs"].get("summary", {}).get("net_return_pct"))
    ):
        raise ValueError("Challenge measurements or completed account count are incomplete")
    prefixes = operating.get("diagnostics")
    if (
        not isinstance(prefixes, list)
        or len(prefixes) != 3
        or any(
            not isinstance(row, dict)
            or row.get("stress") is not False
            or row.get("independently_validated") is not False
            or type(row.get("passed")) is not bool
            for row in prefixes
        )
    ):
        raise ValueError("Three completed exploratory prefixes are required")
    periods = [_identity(row.get("period")) for row in prefixes]
    if len({fingerprint(period) for period in periods}) != 3:
        raise ValueError("Exploratory prefixes must be distinct")
    if any(not _number(report.get("summary", {}).get(key)) for key in SUMMARY_FIELDS[:4]):
        raise ValueError("Current native account measurements are incomplete")
    for envelope in (validation, operating):
        reasons = envelope.get("reasons")
        if (
            not isinstance(reasons, list)
            or len(reasons) > 32
            or any(not isinstance(reason, str) or len(reason) > 1024 for reason in reasons)
        ):
            raise ValueError("Native failure evidence exceeds its bounded contract")
    return validation, operating, decision, design, checks, prefixes, periods, policies


def _initial_feedback(report, unavailable):
    """Expose sealed search challenges without comparing them to a full child check.

    Search-prefix diagnostics are not the separate complete operating programme.
    They may inform the next hypothesis, never make the root rankable/accepted.
    """
    from research.automatic_decision import descriptor
    from research.report_contract import settings_identity

    if not isinstance(report, dict) or "supervisor_validation" in report:
        raise ValueError("Not an initial search report")
    findings = report.get("automatic_research", {})
    recipe = findings.get("recipe", {})
    policy = descriptor()
    decision = findings.get("decision", {})
    if (
        findings.get("version") != "automatic-hypotheses-v16"
        or recipe.get("version") != "automatic-hypotheses-v16"
        or recipe.get("validation_mode") != "exploratory_only"
        or recipe.get("automatic_decision") != policy
        or recipe.get("id") != fingerprint({k: v for k, v in recipe.items() if k != "id"})
        or decision.get("version") != policy["version"]
        or decision.get("policy_id") != policy["id"]
        or decision.get("id") != fingerprint({k: v for k, v in decision.items() if k != "id"})
        or decision.get("independently_validated") is not False
        or findings.get("independently_validated") is not False
        or findings.get("final") is not None
        or decision.get("selected_config_id") != settings_identity(report.get("strategies"))
        or findings.get("selected_config_id") != decision.get("selected_config_id")
        or any(not _number(report.get("summary", {}).get(k)) for k in SUMMARY_FIELDS[:4])
    ):
        raise ValueError("Initial search challenge evidence is unavailable or changed")
    checks = decision.get("checks")
    if not isinstance(checks, list) or len(checks) not in (0, 2):
        raise ValueError("Both recorded initial challenges are required")
    if not checks:
        if decision.get("historical_candidate") is not False or not decision.get("reasons"):
            raise ValueError("Missing initial candidate admission measurements")
        return {
            **unavailable,
            "feedback_available": True,
            "evidence_kind": "initial_search_admission",
            "summary": _summary(report["summary"]),
            "checks": [
                {
                    "key": "initial_candidate_support",
                    "passed": False,
                    "facts": {"historical_candidate": False, "challenges_run": False},
                    "thresholds": {},
                    "reasons": _reasons(decision["reasons"]),
                }
            ],
            "failed_gate_keys": ["initial_candidate_support"],
            "source": {
                "selected_config_id": decision["selected_config_id"],
                "decision_id": decision["id"],
                "recipe_id": recipe["id"],
            },
            "unavailable_reason": "Initial candidate failed admission; cost and neighbor challenges were not run and remain unknown",
        }
    checks = {r["kind"]: r for r in checks if isinstance(r, dict) and "kind" in r}
    if set(checks) != {"higher_costs", "neighboring_settings"} or any(
        type(r.get("passed")) is not bool for r in checks.values()
    ):
        raise ValueError("Initial challenge measurements are incomplete")
    cost, neighbor = checks["higher_costs"], checks["neighboring_settings"]
    tested, passing = neighbor.get("tested"), neighbor.get("passing")
    if (
        type(tested) is not int
        or not 1 <= tested <= 4
        or type(passing) is not int
        or not 0 <= passing <= tested
        or not _number(neighbor.get("median_return_edge_pct"))
        or not _number(cost.get("summary", {}).get("net_return_pct"))
    ):
        raise ValueError("Initial challenge outcomes are incomplete")
    rows = [
        {
            "key": "higher_costs",
            "passed": cost["passed"],
            "facts": _summary(cost["summary"]),
            "thresholds": {
                "maximum_drawdown_pct": policy["maximum_drawdown_pct"],
                "positive_net_return_required": True,
                "matched_baseline_improvement_required": True,
            },
            "reasons": _reasons(cost.get("reasons")),
        },
        {
            "key": "neighboring_settings",
            "passed": neighbor["passed"],
            "facts": {k: neighbor[k] for k in ("tested", "passing", "median_return_edge_pct")},
            "thresholds": {
                k: policy[k]
                for k in (
                    "minimum_neighbors",
                    "minimum_neighbor_pass_fraction",
                    "minimum_neighbor_median_return_edge_fraction",
                )
            },
            "reasons": _reasons(decision.get("reasons")) if not neighbor["passed"] else [],
        },
    ]
    operating = findings.get("operating_validation")
    if operating is not None:
        if (
            not isinstance(operating, dict)
            or operating.get("version") != "research-operating-validation-v1"
            or operating.get("id") != fingerprint({k: v for k, v in operating.items() if k != "id"})
            or operating.get("independently_validated") is not False
            or operating.get("outcome") not in ("blocked", "rejected", "validated")
        ):
            raise ValueError("Initial operating evidence changed")
        rows.append(
            {
                "key": "operating_capacity",
                "passed": operating["outcome"] == "validated",
                "facts": {"outcome": operating["outcome"], "capital": operating.get("capital")},
                "thresholds": {},
                "reasons": _reasons(operating.get("reasons")),
            }
        )
    return {
        **unavailable,
        "feedback_available": True,
        "evidence_kind": "initial_search_challenges",
        "summary": _summary(report["summary"]),
        "checks": rows,
        "failed_gate_keys": [r["key"] for r in rows if not r["passed"]],
        "source": {
            "selected_config_id": decision["selected_config_id"],
            "decision_id": decision["id"],
            "recipe_id": recipe["id"],
        },
        "unavailable_reason": "Recorded search challenges inform hypotheses; full child validation is required for branch comparison and acceptance",
    }


def build(report):
    """Summarize only completed exploratory checks; preserve every gate result."""
    unavailable = {
        "version": VERSION,
        "scope": SCOPE,
        "independently_validated": False,
        "eligible": False,
        "summary": {},
        "checks": [],
        "acceptance_policy": "Native validation alone accepts; this scorecard never does",
    }
    try:
        validation, operating, decision, design, checks, prefixes, periods, policies = _native(
            report
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        try:
            return _initial_feedback(report, unavailable)
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        return {
            **unavailable,
            "unavailable_reason": "Completed exploratory native measurements unavailable",
        }

    def gate(key, passed, facts=None, thresholds=None, reasons=None):
        return {
            "key": key,
            "passed": passed,
            "facts": facts or {},
            "thresholds": thresholds or {},
            "reasons": _reasons(reasons),
        }

    neighbor = checks["neighboring_settings"]
    rows = [
        gate(
            "higher_costs",
            checks["higher_costs"]["passed"],
            _summary(checks["higher_costs"]["summary"]),
            {
                "maximum_drawdown_pct": policies[0]["maximum_drawdown_pct"],
                "positive_net_return_required": True,
                "matched_baseline_improvement_required": True,
            },
            checks["higher_costs"].get("reasons"),
        ),
        gate(
            "neighboring_settings",
            neighbor["passed"],
            {
                key: neighbor[key]
                for key in ("tested", "passing", "median_return_edge_pct")
                if key in neighbor
            },
            {
                key: policies[0][key]
                for key in (
                    "minimum_neighbors",
                    "minimum_neighbor_pass_fraction",
                    "minimum_neighbor_median_return_edge_fraction",
                )
            },
        ),
        gate(
            "sample_and_distribution",
            design["qualified"],
            {
                key: design[key]
                for key in (
                    "closed_trades",
                    "entry_dates",
                    "active_bins",
                    "largest_bin_trade_fraction",
                    "largest_symbol_positive_profit_fraction",
                )
                if _number(design.get(key))
            },
            {
                key: policies[1][key]
                for key in (
                    "minimum_closed_trades",
                    "minimum_entry_dates",
                    "minimum_active_bins",
                    "maximum_bin_trade_fraction",
                    "maximum_symbol_positive_profit_fraction",
                )
            },
            design.get("reasons"),
        ),
        gate(
            "overall_native",
            validation["outcome"] == "validated",
            reasons=validation.get("reasons"),
        ),
    ]
    rows.extend(
        gate(f"prefix_{index}", row["passed"], reasons=row.get("reasons"))
        for index, row in enumerate(prefixes)
    )
    failed = [row["key"] for row in rows if not row["passed"]]
    return {
        **unavailable,
        "eligible": True,
        "feedback_available": True,
        "evidence_kind": "complete_operating_validation",
        "summary": _summary(report["summary"]),
        "checks": rows,
        "failed_gate_keys": failed,
        "failed_gates": len(failed),
        "passed_gates": len(rows) - len(failed),
        "total_gates": len(rows),
        "evidence_count": operating["accounts"],
        "comparison_key": fingerprint(
            {"policies": [p["id"] for p in policies], "prefixes": periods}
        ),
        "source": {
            "native_report_id": validation.get("native_report_id"),
            "selected_config_id": validation.get("selected_config_id"),
            "plan_id": operating.get("plan_id"),
        },
        "native_failure_keys": sorted(
            {
                fingerprint(reason)
                for envelope in (validation, operating)
                for reason in envelope["reasons"]
            }
        ),
        "measurement_policy": "Facts are recorded observations; missing baseline and prefix returns remain unknown",
    }


def _card(value):
    return value if isinstance(value, dict) and value.get("version") == VERSION else build(value)


def rank(report):
    """Lower is better, development gates only; never rank by profit."""
    value = _card(report)
    if not value.get("eligible"):
        return None
    return value["failed_gates"], -value["passed_gates"], -value["evidence_count"]


def dominates(first, second):
    """Strict gate improvement with no regression; incomparable branches stay put.

    Accepts raw reports or scorecards already produced by ``build``. Callers
    additionally enforce identical saved inputs and verified source ownership.
    """
    left, right = _card(first), _card(second)
    if not left.get("eligible") or not right.get("eligible"):
        return False
    if left["comparison_key"] != right["comparison_key"] or {
        row["key"] for row in left["checks"]
    } != {row["key"] for row in right["checks"]}:
        return False
    return set(left["failed_gate_keys"]) < set(right["failed_gate_keys"]) and set(
        left["native_failure_keys"]
    ) <= set(right["native_failure_keys"])
