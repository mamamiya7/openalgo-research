"""Bounded hypotheses from observed native feedback; no calculation or I/O.

Exit evidence identifies a mechanism worth testing, not an improved result.
The supervisor must rebind the selected option and run its unchanged validation.
"""

import re
from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from math import isfinite

from research.execution_search import EXECUTION_CHOICES
from research.report_contract import fingerprint, settings_identity

VERSION = "research-evidence-experiments-v1"
MAX_OPTIONS = 8
MAX_HISTORY = 24
MAX_LEDGER = 25000
MAX_CURVE = 500000
ALLOCATION = "active_prior60_cluster_inverse_vol"
NEGATIVE = ("rejected", "blocked", "failed")
CONFIGURATION_VERSION = "research-effective-settings-v1"


def canonical_configuration(settings, *, engine=None):
    """Complete settings with only demonstrably inactive controls omitted.

    This is a planning equivalence, not a financial report comparison or a
    relaxed acceptance identity. The original settings are never modified.
    Active trailing controls remain because they also change remaining stop
    risk, even when a particular saved account happens to fill the same trades.
    """
    if settings_identity(settings) is None or len(settings) > 8:
        raise ValueError("Configuration comparison needs complete bounded settings")
    result = deepcopy(settings)
    for row in result:
        config = row.get("config")
        if not isinstance(config, dict):
            raise ValueError("Configuration comparison needs complete strategy controls")
        if config.get("trailing_enabled") is False:
            config.pop("trailing_pct", None)
        policy = row.get("execution_policy", {})
        if (
            engine == "vectorbt"
            and isinstance(policy, dict)
            and policy.get("exit_mode") == "atr"
            and policy.get("version") in {f"research-execution-aware-v{i}" for i in range(1, 6)}
        ):
            # VectorBT protection() initializes both exits from causal ATR;
            # risk() and remaining_stop_risk() consume those initialized exits.
            # Percentage exits therefore do not affect exits, sizing or risk.
            config.pop("stop_pct", None)
            config.pop("target_pct", None)
    return result


def configuration_identity(settings, *, engine=None):
    """Engine/version-bound planning identity; native settings IDs stay exact."""
    return fingerprint(
        {
            "version": CONFIGURATION_VERSION,
            "engine": engine,
            "strategies": canonical_configuration(settings, engine=engine),
        }
    )


def _funding_attempted(settings, proposals, entries, *, engine):
    """A repair is consumed for its complete request/result, not forever.

    Capital, sizing and every active execution/risk control stay in this
    identity. A changed horizon or trailing policy can create new affordability
    constraints and must be diagnosed independently of an earlier repair.
    """
    exact = settings_identity(settings)
    effective = configuration_identity(settings, engine=engine)
    for row in proposals:
        feedback = row.get("experiment", {}).get("feedback", {})
        if row.get("remedy") != "funding_and_size" or not (
            row.get("outcome") in (*NEGATIVE, "validated") or feedback.get("validated_settings_id")
        ):
            continue
        source_id = row.get("funding_source_configuration_id") or row.get("candidate", {}).get(
            "funding_source_configuration_id"
        )
        if source_id == effective or feedback.get("validated_settings_id") == exact:
            return True
        if (
            feedback.get("configuration_version") == CONFIGURATION_VERSION
            and feedback.get("configuration_id") == effective
        ):
            return True
        request = row.get("settings")
        if (
            isinstance(request, list)
            and request
            and configuration_identity(request, engine=engine) == effective
        ):
            return True
    return any(
        row.get("remedy") == "funding_and_size"
        and row.get("same_inputs") is True
        and row.get("outcome") in (*NEGATIVE, "validated")
        and (
            row.get("settings_id") == exact
            or row.get("funding_source_configuration_id") == effective
            or (
                row.get("configuration_version") == CONFIGURATION_VERSION
                and row.get("configuration_id") == effective
            )
        )
        for row in entries
    )


def _grid(domain):
    if not isinstance(domain, dict) or set(domain) != {"min", "max", "step"}:
        return []
    try:
        low, high, step = (Decimal(str(domain[key])) for key in ("min", "max", "step"))
        if not all(number.is_finite() for number in (low, high, step)) or step <= 0 or high < low:
            return []
        count = (high - low) / step
        if count != int(count) or not 0 <= count < 256:
            return []
        return [float(low + step * i) for i in range(int(count) + 1)]
    except (ValueError, TypeError, ArithmeticError):
        return []


def _decision(report):
    findings = report.get("automatic_research", {})
    validation = report.get("supervisor_validation", {}).get("operating_validation", {})
    return validation.get("decision") or findings.get("decision", {}), validation


def _failures(report):
    decision, validation = _decision(report)
    records = []
    for row in decision.get("checks", [])[:8]:
        if row.get("passed") is False:
            records.append(
                {
                    "kind": row.get("kind", "native_check"),
                    "reasons": [str(r)[:300] for r in row.get("reasons", [])[:3]],
                }
            )
    windows = decision.get("prefix_diagnostics", []) or validation.get("diagnostics", [])
    failed = [row for row in windows[:8] if row.get("passed") is False]
    if failed:
        records.append(
            {
                "kind": "temporal_stability",
                "periods": [row.get("period") for row in failed],
                "reasons": list(
                    dict.fromkeys(
                        str(r)[:300] for row in failed for r in row.get("reasons", [])[:2]
                    )
                )[:3],
            }
        )
    design = report.get("automatic_design", {})
    if design.get("qualified") is False:
        records.append(
            {
                "kind": "sample_and_distribution",
                "reasons": [str(r)[:300] for r in design.get("reasons", [])[:3]],
            }
        )
    return records[:8]


def _exit_evidence(report, settings):
    ledger, curve = report.get("ledger", []), report.get("equity_curve", [])
    if (
        not isinstance(ledger, list)
        or len(ledger) > MAX_LEDGER
        or not isinstance(curve, list)
        or len(curve) > MAX_CURVE
    ):
        raise ValueError("Native exit evidence exceeds the bounded research catalogue")
    dates = sorted({row["date"][:10] for row in curve if isinstance(row.get("date"), str)})
    sessions = {day: index for index, day in enumerate(dates)}
    closed_total = sum(row.get("status") == "closed" for row in ledger)
    summary = report.get("summary", {})
    complete_account = (
        summary.get("closed_trades") == closed_total
        and summary.get("accepted_trades") == closed_total
        and summary.get("pending_trades", 0) == 0
        and summary.get("unfunded_pending", 0) == 0
    )
    result = {}
    for strategy in settings:
        cfg = strategy["config"]
        axis = "hold_minutes" if cfg.get("hold_minutes") is not None else "hold_sessions"
        rows = [
            row
            for row in ledger
            if row.get("strategy_id") == strategy["id"] and row.get("status") == "closed"
        ]
        durations = []
        for row in rows:
            try:
                if axis == "hold_minutes":
                    entered, exited = (
                        datetime.fromisoformat(row[key])
                        for key in ("entry_timestamp", "exit_timestamp")
                    )
                    held = (exited - entered).total_seconds() / 60
                else:
                    held = sessions[row["exit_date"][:10]] - sessions[row["entry_date"][:10]]
                if isfinite(held) and held >= 0:
                    durations.append(held)
            except (KeyError, ValueError, TypeError):
                continue
        opened = any(
            row.get("strategy_id") == strategy["id"]
            and row.get("status") in ("open", "pending")
            and (row.get("quantity") or 0) > 0
            for row in ledger
        )
        result[strategy["id"]] = {
            "strategy_id": strategy["id"],
            "axis": axis,
            "limit": cfg.get(axis),
            "closed_trades": len(rows),
            "observed_holding_count": len(durations),
            "maximum_observed_holding": max(durations) if durations else None,
            "complete": bool(rows)
            and len(durations) == len(rows)
            and not opened
            and complete_account,
            "exit_outcomes": dict(
                Counter(str(row.get("outcome") or "unknown") for row in rows).most_common(8)
            ),
            "durations": durations,
        }
    return result


def _same_boundary(base, candidate):
    if len(base) != len(candidate):
        return False
    for before, after in zip(base, candidate, strict=True):
        if any(
            before.get(key) != after.get(key)
            for key in ("id", "name", "allocation_pct", "entry_condition")
        ):
            return False
        first, second = before["config"], after["config"]
        if any(
            first.get(key) != second.get(key)
            for key in (
                "initial_capital",
                "order_size_pct",
                "trade_horizon",
                "entry_time",
                "exit_time",
            )
        ):
            return False
        if before["execution_policy"].get("risk_limits") != after["execution_policy"].get(
            "risk_limits"
        ):
            return False
    return True


def _no_effect_directions(proposals, base, exits, source_artifact):
    suppressed, observations = set(), []
    for proposal in proposals:
        proof = proposal.get("no_effect", {})
        if not (
            proof.get("version") == "research-remedy-effect-v1"
            and proof.get("status") == "unchanged"
            and proof.get("seed_result_artifact") == source_artifact
            and proof.get("id") == fingerprint({k: v for k, v in proof.items() if k != "id"})
        ):
            continue
        tested = proposal.get("settings")
        if not isinstance(tested, list) or not _same_boundary(base, tested):
            continue
        changes = []
        for before, after in zip(base, tested, strict=True):
            axis = exits[before["id"]]["axis"]
            expected = deepcopy(before)
            expected["config"][axis] = after["config"].get(axis)
            if after != before and after == expected:
                changes.append((before, after, axis))
            elif after != before:
                changes.append(None)
        if len(changes) != 1 or changes[0] is None:
            continue
        before, after, axis = changes[0]
        fact = exits[before["id"]]
        original, tested_value = before["config"].get(axis), after["config"].get(axis)
        if not (type(original) in (int, float) and type(tested_value) in (int, float)):
            continue
        if fact["complete"] and fact["maximum_observed_holding"] < original < tested_value:
            suppressed.add(before["id"])
            observations.append(
                {
                    "strategy_id": before["id"],
                    "direction": "longer",
                    "from": original,
                    "tested": tested_value,
                    "observed_maximum": fact["maximum_observed_holding"],
                    "reason": "The tested increase had exactly no economic effect; every observed exit was earlier than the original horizon. Further increases lack a supported exit-delay mechanism. Different windows could still exclude different signals; no untested equality is claimed.",
                }
            )
    return suppressed, observations[:8]


def build(report, recipe, proposals, *, source_artifact, memory=None):
    """Offer at most eight concrete, immutable-source-bound native experiments.

    ``memory`` is the bounded same-study snapshot, whose ``same_inputs`` flag is
    established by its owner. All options remain hypotheses until native tests.
    Caller owns recipe/contract admission and must rebuild before execution.
    """
    if not isinstance(source_artifact, str) or not re.fullmatch(r"[a-f0-9]{64}", source_artifact):
        raise ValueError("Experiments require the exact immutable source artifact")
    if not isinstance(proposals, list) or len(proposals) > MAX_HISTORY:
        raise ValueError("Experiment feedback needs a bounded proposal ledger")
    entries = (memory or {}).get("entries", [])
    if not isinstance(entries, list) or len(entries) > MAX_HISTORY:
        raise ValueError("Experiment memory exceeds its bounded allowance")
    base = report.get("strategies")
    if not isinstance(base, list) or not 1 <= len(base) <= 8 or not settings_identity(base):
        raise ValueError("Experiments require complete native strategy settings")
    if (
        any(row.get("entry_condition") for row in base)
        or report.get("automatic_stock_condition", {}).get("hypothesis", {"id": "none"}).get("id")
        != "none"
    ):
        raise ValueError("Experiments preserve the original supplied signals")
    base = deepcopy(base)
    engine = report.get("portfolio", {}).get("engine") or recipe.get("engine")
    failures = _failures(report)
    exits = _exit_evidence(report, base)
    suppressed, no_effect = _no_effect_directions(proposals, base, exits, source_artifact)
    negative = {
        settings_identity(row["settings"])
        for row in proposals
        if isinstance(row.get("settings"), list) and row.get("outcome") in NEGATIVE
    }
    negative.update(
        row.get("settings_id")
        for row in entries
        if row.get("same_inputs") is True and row.get("outcome") in NEGATIVE
    )
    equivalent_negative = {
        configuration_identity(row["settings"], engine=engine)
        for row in proposals
        if isinstance(row.get("settings"), list) and row.get("outcome") in NEGATIVE
    }
    equivalent_negative.update(
        row.get("configuration_id")
        for row in entries
        if row.get("same_inputs") is True
        and row.get("outcome") in NEGATIVE
        and row.get("configuration_version") == CONFIGURATION_VERSION
    )
    domains = {row["id"]: row.get("search", {}) for row in recipe.get("ranges", [])}
    trailing = {
        row["id"]: row.get("choices", [])
        for row in recipe.get("trailing_research", {}).get("strategies", [])
    }
    execution = recipe.get("execution_research", {})
    definitions = {row["id"]: row for row in execution.get("strategies", [])}
    kinds = {row["kind"] for row in failures}
    cost_rows = [
        row for row in _decision(report)[0].get("checks", []) if row.get("kind") == "higher_costs"
    ]
    ordinary = report.get("summary", {}).get("net_return_pct")
    cost_drag = any(
        type(row.get("summary", {}).get("net_return_pct")) in (int, float)
        and type(ordinary) in (int, float)
        and row["summary"]["net_return_pct"] < ordinary
        for row in cost_rows
    )
    fees = sum(
        float(row.get("fees") or 0)
        for row in report.get("ledger", [])
        if row.get("status") == "closed"
    )
    if not isfinite(fees) or fees < 0:
        raise ValueError("Native fee evidence is invalid")
    cost_hypothesis = fees > 0 and bool(cost_rows) and ("higher_costs" in kinds or cost_drag)
    temporal_hypothesis = "temporal_stability" in kinds or "sample_and_distribution" in kinds
    feedback = {
        "failed_checks": failures,
        "native_fees": fees,
        "sample_shortfall": max(
            0,
            recipe.get("automatic_design", {}).get("minimum_closed_trades", 30)
            - report.get("summary", {}).get("closed_trades", 0),
        ),
        "exit_evidence": [
            {key: value for key, value in fact.items() if key != "durations"}
            for fact in exits.values()
        ],
        "no_effect_directions": no_effect,
        "excluded_setting_ids": sorted(value for value in negative if value)[:MAX_HISTORY],
        "excluded_configuration_ids": sorted(value for value in equivalent_negative if value)[
            :MAX_HISTORY
        ],
        "prediction_policy": "Observed exits justify a test mechanism, never a promised P&L improvement or validation pass",
    }
    unavailable, options, seen = [], [], {settings_identity(base), *negative}
    equivalent_seen = {configuration_identity(base, engine=engine), *equivalent_negative}

    def add(remedy, candidate, change, expected_effect, reason, *, native_funding=False):
        identifier = settings_identity(candidate)
        effective = configuration_identity(candidate, engine=engine)
        if ((identifier in seen or effective in equivalent_seen) and not native_funding) or len(
            options
        ) >= MAX_OPTIONS:
            return
        if not _same_boundary(base, candidate):
            raise ValueError("An experiment changed fixed signals, style, funds or risk limits")
        seen.add(identifier)
        equivalent_seen.add(effective)
        body = {
            "version": VERSION,
            "source_artifact": source_artifact,
            "remedy": remedy,
            "settings_id": identifier,
        }
        options.append(
            {
                "id": fingerprint(body),
                "remedy": remedy,
                "settings": candidate,
                "change": change,
                "expected_effect": "Prediction, not a result: " + expected_effect,
                "reason": reason[:1000],
                **(
                    {"funding_source_configuration_id": configuration_identity(base, engine=engine)}
                    if native_funding
                    else {}
                ),
            }
        )

    decision, _ = _decision(report)
    counts = decision.get("capital_diagnosis") or report.get("automatic_research", {}).get(
        "capital_planning", {}
    ).get("diagnosis", {}).get("categories", {})
    if isinstance(counts.get("categories"), dict):
        counts = counts["categories"]
    blocked = report.get("supervisor_validation", {}).get("outcome") == "blocked"
    if blocked:
        feasibility = report["supervisor_validation"].get("feasibility", {})
        valid = (
            isinstance(feasibility, dict)
            and feasibility.get("version") == "research-feasibility-v1"
            and feasibility.get("scope") == "feasibility_only"
            and feasibility.get("source_settings_id") == settings_identity(base)
            and isinstance(feasibility.get("counts"), dict)
            and len(feasibility["counts"]) <= 24
            and all(
                type(value) is int and 0 <= value <= MAX_LEDGER
                for value in feasibility["counts"].values()
            )
            and isinstance(feasibility.get("unresolved_constraints"), dict)
            and all(
                type(value) is int
                and 0 <= value <= MAX_LEDGER
                and feasibility["counts"].get(key) == value
                for key, value in feasibility["unresolved_constraints"].items()
            )
        )
        # No performance inference is permitted from an account that never
        # entered its complete native validation programme.
        feedback = {
            "mode": "feasibility_only",
            "feasibility": deepcopy(feasibility) if valid else None,
            "prediction_policy": "Blocked-account return and exit outcomes cannot justify a performance hypothesis; restore native feasibility before complete validation",
        }
        if not valid:
            return {
                "version": VERSION,
                "feedback": feedback,
                "options": [],
                "unavailable": [
                    {
                        "method": "funding_and_size",
                        "reason": "The blocked account lacks complete source-bound native feasibility evidence",
                    }
                ],
            }
        counts = feasibility["counts"]
    funding_tried = _funding_attempted(base, proposals, entries, engine=engine)
    repairable = {
        key: counts.get(key, 0)
        for key in ("cash_shortfall", "whole_share_affordability", "percentage_policy")
        if counts.get(key, 0)
    }
    missing = any(
        counts.get(key, 0)
        for key in (
            "unavailable_sizing_intent",
            "unexplained_execution_limit",
            "evidence_gap_requests",
            "unavailable_evidence",
        )
    )
    if repairable and not missing and not funding_tried:
        add(
            "funding_and_size",
            deepcopy(base),
            {
                "axis": "native_funding_and_size",
                "current_capital": base[0]["config"]["initial_capital"],
                "diagnosis": repairable,
            },
            "The existing native funding programme will estimate capital and feasible ticket size, then validate its chosen account under every fixed risk limit; the amount is not invented here",
            "Native affordability or percentage-sizing counts require the existing mechanical repair before performance changes",
            native_funding=True,
        )
    elif funding_tried:
        unavailable.append(
            {
                "method": "funding_and_size",
                "reason": "The native funding programme already attempted this complete source or funded configuration; repeating its repair without a changed execution setting would be a blind rerun",
            }
        )
    elif missing:
        unavailable.append(
            {
                "method": "funding_and_size",
                "reason": "Saved evidence or sizing intent is unavailable; additional money cannot invent it",
            }
        )

    if blocked:
        if not repairable and not missing and not funding_tried:
            unavailable.append(
                {
                    "method": "funding_and_size",
                    "reason": "The blocked account exposes no supported cash, whole-share or percentage-sizing repair; fixed position/liquidity limits cannot be relaxed",
                }
            )
        return {
            "version": VERSION,
            "feedback": feedback,
            "options": options,
            "unavailable": unavailable[:8],
        }

    # Only horizons earlier than at least one observed exit can affect those
    # executions. A nearest legal point outside that range is not useful.
    for index, row in enumerate(base):
        fact = exits[row["id"]]
        axis, current = fact["axis"], row["config"].get(fact["axis"])
        if row["id"] in suppressed:
            unavailable.append(
                {
                    "method": "longer_holding",
                    "strategy_id": row["id"],
                    "reason": no_effect[0]["reason"],
                }
            )
        if not fact["complete"]:
            unavailable.append(
                {
                    "method": "holding_change",
                    "strategy_id": row["id"],
                    "reason": "Complete native entry/exit timing is unavailable; no holding-effect claim can be made",
                }
            )
            continue
        if temporal_hypothesis and type(current) in (int, float):
            points = [
                point
                for point in _grid(domains.get(row["id"], {}).get(axis))
                if point.is_integer()
                and point < current
                and any(held > point for held in fact["durations"])
            ]
            for point in points[:2]:
                changed = deepcopy(base)
                changed[index]["config"][axis] = int(point)
                affected = sum(held > point for held in fact["durations"])
                add(
                    "temporal_stability",
                    changed,
                    {"strategy_id": row["id"], "axis": axis, "from": current, "to": int(point)},
                    f"An earlier horizon intersects {affected} of {fact['closed_trades']} observed exits and may release capacity sooner; re-entry, profit and costs require calculation",
                    "Recorded prefix or sample checks failed; test an effect-relevant frozen holding point rather than another nonbinding increase",
                )
        if cost_hypothesis and row["id"] not in suppressed and type(current) in (int, float):
            # A longer limit can delay a recorded horizon-driven exit; early
            # target/stop exits provide no such evidence for a longer hold.
            horizon = sum(
                count
                for outcome, count in fact["exit_outcomes"].items()
                if outcome in ("hold", "time", "time_exit", "holding_limit", "max_hold")
            )
            if horizon:
                points = [
                    point
                    for point in _grid(domains.get(row["id"], {}).get(axis))
                    if point.is_integer() and point > current
                ]
                if points:
                    changed = deepcopy(base)
                    changed[index]["config"][axis] = int(points[-1])
                    add(
                        "lower_turnover",
                        changed,
                        {
                            "strategy_id": row["id"],
                            "axis": axis,
                            "from": current,
                            "to": int(points[-1]),
                        },
                        f"A longer horizon could delay {horizon} observed time exits; turnover and net costs may change",
                        "A recorded higher-cost check and horizon exits justify a bounded cost-reduction hypothesis",
                    )

    concentration = report.get("automatic_design", {}).get(
        "largest_symbol_positive_profit_fraction"
    )
    if (
        type(concentration) in (int, float)
        and concentration > 0.5
        and ALLOCATION in execution.get("allocation_modes", [])
    ):
        candidate = deepcopy(base)
        for row in candidate:
            row["execution_policy"]["allocation_mode"] = ALLOCATION
        add(
            "reduce_concentration",
            candidate,
            {"axis": "allocation_mode", "to": ALLOCATION},
            "Causal entry-only weights may spread allocation across contemporaneously eligible stocks; whole-share and hard-risk limits can make this ineffective",
            f"Native distribution evidence attributes {100 * concentration:.1f}% of positive profit to one stock; test the already-supported allocation method",
        )

    # Prioritize active observed exit mechanisms; avoid dormant tiny distance
    # nudges. No candidate outcome, future bar or new P&L is consulted.
    if cost_hypothesis:
        for index, row in enumerate(base):
            cfg, fact = row["config"], exits[row["id"]]
            if cfg.get("trailing_enabled") and fact["exit_outcomes"].get("trailing_stop", 0):
                choices = trailing.get(row["id"], [])
                farther = sorted(
                    {
                        value
                        for value in choices
                        if type(value) in (int, float)
                        and isfinite(value)
                        and value > cfg["trailing_pct"]
                    },
                    reverse=True,
                )
                choices = [*farther[:2], *([0.0] if 0.0 in choices else [])]
                for value in choices:
                    candidate = deepcopy(base)
                    candidate[index]["config"].update(
                        trailing_enabled=value > 0, trailing_pct=float(value)
                    )
                    add(
                        "lower_turnover",
                        candidate,
                        {
                            "strategy_id": row["id"],
                            "axis": "trailing_choice",
                            "from": cfg["trailing_pct"],
                            "to": value,
                        },
                        f"Changing the registered trailing state may delay some of {fact['exit_outcomes']['trailing_stop']} trailing exits; losses, holding occupancy and total costs can also increase",
                        "Higher-cost evidence failed or records cost drag, and trailing stops are an observed dominant exit mechanism; this is a hypothesis requiring unchanged validation",
                    )
            if (
                cfg.get("target_pct") is not None
                and fact["exit_outcomes"].get("target", 0)
                and row["execution_policy"].get("exit_mode") == "percent"
            ):
                farther = [
                    value
                    for value in _grid(domains.get(row["id"], {}).get("target_pct"))
                    if value > cfg["target_pct"]
                ]
                if farther:
                    candidate = deepcopy(base)
                    candidate[index]["config"]["target_pct"] = farther[-1]
                    add(
                        "lower_turnover",
                        candidate,
                        {
                            "strategy_id": row["id"],
                            "axis": "target_pct",
                            "from": cfg["target_pct"],
                            "to": farther[-1],
                        },
                        f"A farther target may delay {fact['exit_outcomes']['target']} recorded target exits; another exit may still trigger first",
                        "Recorded target exits and higher-cost evidence justify testing the frozen wider target",
                    )
            definition = definitions.get(row["id"], {})
            branches = definition.get("execution_choices", [])
            branch = next(
                (
                    item
                    for item in reversed(branches)
                    if item.get("exit_mode") == "atr" and item in EXECUTION_CHOICES
                ),
                None,
            )
            if (
                branch
                and row["execution_policy"].get("exit_mode") != "atr"
                and fact["closed_trades"]
            ):
                candidate = deepcopy(base)
                policy = candidate[index]["execution_policy"]
                for key in ("atr_period", "atr_stop_multiple", "atr_target_multiple"):
                    policy.pop(key, None)
                policy.update({key: value for key, value in branch.items() if key != "id"})
                for key in ("target_pct", "stop_pct"):
                    candidate[index]["config"][key] = definition["original"][key]
                add(
                    "lower_turnover",
                    candidate,
                    {
                        "strategy_id": row["id"],
                        "axis": "execution_choice",
                        "from": row["execution_policy"].get("exit_mode"),
                        "to": branch["id"],
                    },
                    "The registered prior-data ATR exit may change stopping/target frequency; existing native risk and missing-feature exclusions remain authoritative",
                    "Use an existing frozen volatility-scaled exit as a cost hypothesis; do not install a new method or claim that it improves returns",
                )
    else:
        unavailable.append(
            {
                "method": "cost_exit_changes",
                "reason": "No recorded fee and higher-cost evidence supports changing exits for a cost hypothesis",
            }
        )
    return {
        "version": VERSION,
        "feedback": feedback,
        "options": options,
        "unavailable": unavailable[:8],
    }
