"""Frozen historical validation of one capacity-selected account operating point.

This module owns no engine, search, I/O or financial limit. Callers retain native
account admission and the global 512-attempt fence, including interrupted calls.
"""

from copy import deepcopy
from math import isfinite

from research.automatic_decision import conclude
from research.automatic_decision import descriptor as decision_descriptor
from research.automatic_design import descriptor as design_descriptor
from research.automatic_design import reasons as design_reasons
from research.automatic_research import _entry_dates, _support
from research.report_contract import fingerprint, settings_identity

VERSION = "research-operating-validation-v1"
BUDGET_BLOCK = "The unchanged remaining budget cannot complete all frozen validation accounts"


def _seal(value):
    return {**value, "id": fingerprint(value)}


def descriptor():
    return _seal(
        {
            "version": VERSION,
            "max_exploratory_accounts": 13,
            "max_chronological_accounts": 21,
            "decision_policy_id": decision_descriptor()["id"],
            "design_policy_id": design_descriptor()["id"],
            "selection": "Freeze one capacity-selected point; no reranking or replacement after challenges.",
            "baseline": "Original settings and rules at the identical operating-point capital.",
            "scope": "Historical validation on reused source data; never independent or a trading recommendation.",
            "budget": "Retain all 408 proposals and the global 512-attempt ceiling; block rather than truncate validation.",
        }
    )


def _settings(rows, capital):
    value = deepcopy(rows)
    if not settings_identity(value):
        raise ValueError("Operating validation needs complete frozen settings")
    for row in value:
        row["config"]["initial_capital"] = capital
    return value


def _stress(rows):
    value = deepcopy(rows)
    for row in value:
        cost = row["config"]["cost_bps"]
        row["config"]["cost_bps"] = max(2 * cost, cost + 5)
    return value


def build_plan(
    *,
    operating_point,
    baseline_settings,
    hypothesis,
    baseline_hypothesis,
    neighbors,
    periods,
    validation_mode,
    remaining_accounts,
    capacity_counts=None,
):
    """Freeze settings, account identities and periods before any new outcomes.

    ``neighbors`` is the existing recommendation-plan neighbor list. ``periods``
    supplies search plus three prefixes (exploratory/historical) or three later
    windows and final (chronological). Period values are opaque frozen caller keys.
    """
    if validation_mode not in ("exploratory_only", "historical_only", "chronological"):
        raise ValueError("Unknown operating validation mode")
    if type(remaining_accounts) is not int or not 0 <= remaining_accounts <= 512:
        raise ValueError("Operating validation needs the remaining global account allowance")
    capital = operating_point.get("capital")
    if type(capital) not in (int, float) or not isfinite(capital) or not 1 <= capital <= 1e9:
        raise ValueError("Operating validation capital exceeds native representation")
    selected = _settings(operating_point["settings"], capital)
    baseline = _settings(baseline_settings, capital)
    limits = selected[0]["execution_policy"]["risk_limits"]
    if any(row["execution_policy"]["risk_limits"] != limits for row in selected + baseline):
        raise ValueError("Operating validation cannot change hard account limits")
    if {r["id"] for r in selected} != {r["id"] for r in baseline}:
        raise ValueError("Operating validation changed the baseline strategy cohort")
    if not isinstance(neighbors, list) or len(neighbors) > 4:
        raise ValueError("Operating validation permits at most four frozen neighbors")
    nearby = []
    for item in neighbors:
        rows = _settings(item["strategies"] if isinstance(item, dict) else item, capital)
        if any(row["execution_policy"]["risk_limits"] != limits for row in rows):
            raise ValueError("Neighbor changed a hard account limit")
        nearby.append(rows)
    if len({settings_identity(rows) for rows in nearby}) != len(nearby):
        raise ValueError("Operating validation neighbors must be distinct")
    strict = validation_mode != "exploratory_only"
    period_key = "later" if strict else "prefixes"
    if (
        not isinstance(periods, dict)
        or "search" not in periods
        or len(periods.get(period_key, [])) != 3
    ):
        raise ValueError("Operating validation needs all three frozen diagnostic windows")
    if strict and "final" not in periods:
        raise ValueError("Chronological operating validation needs its frozen final window")
    period_names = [periods["search"], *periods[period_key]]
    if strict:
        period_names.append(periods["final"])
    if len({fingerprint(period) for period in period_names}) != len(period_names):
        raise ValueError("Operating validation needs distinct frozen window identities")
    maximum = 21 if strict else 13
    blocked = []
    if remaining_accounts < maximum:
        blocked.append(
            "The unchanged global account budget cannot fund the complete validation programme"
        )
    original_capacity_counts = deepcopy(capacity_counts)
    if capacity_counts is None:
        blocked.append("Native capacity diagnosis is unavailable")
        capacity_counts = {}
    elif not isinstance(capacity_counts, dict):
        raise ValueError("Native capacity diagnosis must be recorded counts")
    if any(type(v) is not int or v < 0 for v in capacity_counts.values()):
        raise ValueError("Invalid native capacity counts")
    if not capacity_counts.get("admitted_requests"):
        blocked.append("No supported native request was admitted at this operating point")
    unresolved = {
        key: capacity_counts.get(key, 0)
        for key in (
            "cash_shortfall",
            "whole_share_affordability",
            "percentage_policy",
            "unavailable_sizing_intent",
            "unexplained_execution_limit",
        )
        if capacity_counts.get(key, 0)
    }
    if unresolved:
        blocked.append(
            "The operating point retains unresolved funding, sizing or unexplained execution constraints"
        )
    requests = []

    def add(role, rows, period, stress=False):
        value = {
            "role": role,
            "settings": deepcopy(rows),
            "hypothesis": deepcopy(
                baseline_hypothesis if role.endswith("baseline") else hypothesis
            ),
            "period": deepcopy(period),
            "stress": stress,
        }
        value["settings_id"] = settings_identity(_stress(rows) if stress else rows)
        requests.append(_seal(value))

    add("full_baseline", baseline, periods["search"])
    add("cost_selected", selected, periods["search"], True)
    add("cost_baseline", baseline, periods["search"], True)
    for index, rows in enumerate(nearby):
        add(f"neighbor_{index}", rows, periods["search"])
    for index, period in enumerate(periods[period_key]):
        for stress in (False, True) if strict else (False,):
            kind = "stress" if stress else "normal"
            add(f"window_{index}_{kind}_selected", selected, period, stress)
            add(f"window_{index}_{kind}_baseline", baseline, period, stress)
    if strict:
        add("final_selected", selected, periods["final"])
        add("final_baseline", baseline, periods["final"])
    inputs = {
        "operating_point": deepcopy(operating_point),
        "baseline_settings": deepcopy(baseline_settings),
        "hypothesis": deepcopy(hypothesis),
        "baseline_hypothesis": deepcopy(baseline_hypothesis),
        "neighbors": deepcopy(neighbors),
        "periods": deepcopy(periods),
        "validation_mode": validation_mode,
        "remaining_accounts": remaining_accounts,
        "capacity_counts": original_capacity_counts,
    }
    return _seal(
        {
            "version": VERSION,
            "policy": descriptor(),
            "inputs": inputs,
            "capital": capital,
            "selected_settings": selected,
            "baseline_settings": baseline,
            "selected_config_id": settings_identity(selected),
            "requests": requests,
            "max_additional_accounts": maximum,
            "blocked_reasons": blocked,
            "unresolved_constraints": unresolved,
            "capacity_counts": deepcopy(capacity_counts),
            "independently_validated": False,
            "selection_changed": False,
        }
    )


def _verify_plan(plan):
    if not isinstance(plan, dict) or plan != build_plan(**plan.get("inputs", {})):
        raise ValueError("The frozen operating validation plan changed")


def _row(report, request, audit):
    if settings_identity(report.get("strategies")) != request["settings_id"]:
        raise ValueError("Native operating validation settings changed")
    if report["summary"]["initial_capital"] != request["settings"][0]["config"]["initial_capital"]:
        raise ValueError("Native operating validation capital changed")
    if report.get("automatic_stock_condition", {}).get("hypothesis") != request["hypothesis"]:
        raise ValueError("Native operating validation hypothesis changed")
    verified = audit(report, request)
    if verified != report.get("portfolio_constraint_evidence"):
        raise ValueError("Native operating validation constraint evidence changed")
    dates = report.get("entry_dates", report.get("automatic_design", {}).get("entry_dates"))
    if dates is None:
        dates = _entry_dates(report)
    if type(dates) is not int or dates < 0:
        raise ValueError("Native operating validation entry dates are unavailable")
    row = {
        "summary": report["summary"],
        "entry_dates": dates,
        "automatic_design": report.get("automatic_design"),
        "portfolio_constraint_evidence": verified,
        "config_id": request["settings_id"],
    }
    # Validate receipt identities even for rejected designs; never trust a flag.
    if request["role"] in ("full_selected", "cost_selected") or request["role"].startswith(
        "neighbor_"
    ):
        design_reasons(row, design_descriptor())
    return row


def run(
    plan,
    *,
    selected_report,
    evaluate,
    audit,
    saved=None,
    checkpoint=None,
    strict_assessment=None,
    cancelled=None,
    available_accounts=None,
):
    """Evaluate a fixed programme and semantically verify recovery without search.

    ``evaluate(settings,hypothesis,period,stress)`` applies the existing cost
    transformation when stress is true. ``audit(report,request)`` recomputes the
    native constraint receipt. Strict mode also requires the caller's unchanged
    recommendation_evidence.assess over these exact reports.
    """
    _verify_plan(plan)
    if not callable(audit):
        raise ValueError("Operating validation requires native constraint recomputation")
    if saved is None:
        state = {
            "version": VERSION,
            "plan_id": plan["id"],
            "selected_report_id": fingerprint(
                {k: v for k, v in selected_report.items() if k != "automatic_research"}
            ),
            "reports": {},
            "active_request": None,
            "conclusion": None,
        }
    else:
        state = saved
    if (
        set(state)
        != {"version", "plan_id", "selected_report_id", "reports", "active_request", "conclusion"}
        or state["version"] != VERSION
        or state["plan_id"] != plan["id"]
    ):
        raise ValueError("The saved operating validation programme changed")
    if state["selected_report_id"] != fingerprint(
        {k: v for k, v in selected_report.items() if k != "automatic_research"}
    ):
        raise ValueError("The reused operating-point native report changed")
    if not isinstance(state["reports"], dict):
        raise ValueError("The saved operating validation accounts changed")
    roles = [request["id"] for request in plan["requests"]]
    # Canonical JSON sorts object keys; the prefix is defined by frozen request
    # IDs, never dictionary insertion order after saving and reopening.
    if set(state["reports"]) != set(roles[: len(state["reports"])]):
        raise ValueError(
            "Saved operating validation accounts changed order or continued past the plan"
        )
    if state["active_request"] is not None and (
        len(state["reports"]) == len(roles)
        or state["active_request"] != roles[len(state["reports"])]
    ):
        raise ValueError("Saved operating validation active request changed")
    if state["conclusion"] is not None and (
        not isinstance(state["conclusion"], dict)
        or state["conclusion"].get("id")
        != fingerprint({k: v for k, v in state["conclusion"].items() if k != "id"})
        or len(state["reports"]) < len(roles)
        and state["conclusion"].get("outcome") != "blocked"
    ):
        raise ValueError("The saved operating validation conclusion changed")

    def persist():
        if checkpoint:
            checkpoint(state)

    def finish(value):
        result = _seal(
            {
                "version": VERSION,
                "plan_id": plan["id"],
                "selected_config_id": plan["selected_config_id"],
                "capital": plan["capital"],
                "selected_report_id": state["selected_report_id"],
                "independently_validated": False,
                "selection_changed": False,
                "scope": descriptor()["scope"],
                **value,
            }
        )
        if state["conclusion"] is not None and state["conclusion"] != result:
            raise ValueError("The saved operating validation conclusion changed")
        state["conclusion"] = result
        persist()
        return result, state

    blocked = list(plan["blocked_reasons"])
    strict = plan["inputs"]["validation_mode"] != "exploratory_only"
    if strict and not callable(strict_assessment):
        blocked.append("Unchanged chronological recommendation assessment is unavailable")
    if blocked:
        if state["reports"]:
            raise ValueError("Blocked operating validation unexpectedly contains account results")
        return finish({"outcome": "blocked", "historically_validated": False, "reasons": blocked})
    full_request = {
        "role": "full_selected",
        "settings": plan["selected_settings"],
        "settings_id": plan["selected_config_id"],
        "period": plan["inputs"]["periods"]["search"],
        "stress": False,
        "hypothesis": plan["inputs"]["hypothesis"],
    }
    selected = _row(selected_report, full_request, audit)
    rows, reports = {"full_selected": selected}, {"full_selected": selected_report}
    for index, request in enumerate(plan["requests"]):
        if cancelled:
            cancelled()
        item = state["reports"].get(request["id"])
        if item is None:
            if state["conclusion"] is not None:
                return finish(
                    {
                        "outcome": "blocked",
                        "historically_validated": False,
                        "reasons": [BUDGET_BLOCK],
                    }
                )
            if available_accounts and available_accounts() < len(plan["requests"]) - index:
                return finish(
                    {
                        "outcome": "blocked",
                        "historically_validated": False,
                        "reasons": [BUDGET_BLOCK],
                    }
                )
            state["active_request"] = request["id"]
            persist()
            report = evaluate(
                deepcopy(request["settings"]),
                deepcopy(request["hypothesis"]),
                deepcopy(request["period"]),
                request["stress"],
            )
            item = {"report": report, "report_id": fingerprint(report)}
            # Do not checkpoint an unverified report after a native audit failure.
            row = _row(report, request, audit)
            state["reports"][request["id"]] = item
            state["active_request"] = None
            persist()
        else:
            report = item["report"]
            if set(item) != {"report", "report_id"} or item["report_id"] != fingerprint(report):
                raise ValueError("A saved operating validation report changed")
            row = _row(report, request, audit)
        rows[request["role"]] = row
        reports[request["role"]] = report
    failures = design_reasons(selected, design_descriptor())
    if selected["portfolio_constraint_evidence"].get("status") != "supported":
        failures.append("The operating point lacks supported native constraints")
    if any(
        rows[role]["portfolio_constraint_evidence"].get("status") != "supported"
        for role in ("full_baseline", "cost_baseline")
    ):
        failures.append("A matched-capital baseline lacks supported native constraints")
    diagnostics = []
    for i in range(3):
        for kind in ("normal", "stress") if strict else ("normal",):
            row, baseline = rows[f"window_{i}_{kind}_selected"], rows[f"window_{i}_{kind}_baseline"]
            problems = _support(row["summary"], baseline["summary"], dates=row["entry_dates"])
            if row["summary"]["net_return_pct"] <= baseline["summary"]["net_return_pct"]:
                problems.append("Net return did not improve on the matched-capital baseline")
            if any(
                r["portfolio_constraint_evidence"].get("status") != "supported"
                for r in (row, baseline)
            ):
                problems.append("Native financial constraints are not supported")
            diagnostics.append(
                {
                    "period": deepcopy(
                        plan["inputs"]["periods"]["later" if strict else "prefixes"][i]
                    ),
                    "stress": kind == "stress",
                    "passed": not problems,
                    "reasons": problems,
                    "independently_validated": False,
                }
            )
    chronological = None
    if strict:
        chronological = strict_assessment(reports, plan)
        if not isinstance(chronological, dict) or chronological.get("status") != "supported":
            failures.append(
                "The unchanged chronological recommendation assessment did not support this point"
            )
        if any(not row["passed"] for row in diagnostics):
            failures.append("A frozen chronological or higher-cost window failed")
        final, reference = rows["final_selected"], rows["final_baseline"]
        if (
            _support(final["summary"], reference["summary"], dates=final["entry_dates"])
            or final["summary"]["net_return_pct"] <= reference["summary"]["net_return_pct"]
            or any(
                r["portfolio_constraint_evidence"].get("status") != "supported"
                for r in (final, reference)
            )
        ):
            failures.append("The exact final-period matched-capital comparison failed")
    decision = conclude(
        policy=decision_descriptor(),
        selected=selected,
        baseline=rows["full_baseline"],
        stress=rows["cost_selected"],
        stress_baseline=rows["cost_baseline"],
        neighbors=[rows[f"neighbor_{i}"] for i in range(len(plan["inputs"]["neighbors"]))],
        prefixes=diagnostics,
        seed_choices=[],
        is_baseline=False,
        independently_validated=False,
        validation_passed=False,
        capital_diagnosis=deepcopy(plan["capacity_counts"]),
    )
    if not decision["historical_candidate"]:
        failures.extend(decision["reasons"])
    return finish(
        {
            "outcome": "rejected" if failures else "validated",
            "historically_validated": not failures,
            "reasons": failures,
            "decision": decision,
            "chronological": chronological,
            "diagnostics": diagnostics,
            "accounts": len(state["reports"]),
            "reused_selected_report_id": state["selected_report_id"],
        }
    )
