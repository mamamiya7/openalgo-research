"""Bounded capital planning for fixed initial tickets; no account calculations or I/O."""

import re
from collections import Counter
from copy import deepcopy
from math import ceil, floor, isclose, isfinite

from research.execution_policy import normalize_limits
from research.report_contract import fingerprint, settings_identity

VERSION = "research-capital-planning-v1"
DECISION_VERSION = "research-capital-planning-v2"
MAX_NATIVE_CAPITAL = 1_000_000_000
SELECTED_VERSION = "research-capital-planning-v3"
INTENDED_VERSION = "research-capital-planning-v4"
CLOSED_LOOP_VERSION = "research-capital-planning-v5"
FEASIBLE_VERSION = "research-capital-planning-v6"
CAUSAL_VERSION = "research-capital-planning-v7"
ATR_VERSION = "research-capital-planning-v8"
MAX_REQUEST_PRICES = 100_000


def _number(value, label, *, positive=True):
    if type(value) not in (int, float) or not isfinite(value) or (positive and value <= 0):
        raise ValueError(f"Capital planning needs finite {label}")
    return float(value)


def _seal(value):
    return {**value, "id": fingerprint(value)}


def selected_account_risks(report, selected):
    """Retain native prior-data ATR unit risk, never P&L or an invented ATR.

    The caller verifies the native report/audit binding. This additional exact
    settings binding prevents reusing one exit configuration's unit risk for a
    different stop multiple, cost model or strategy configuration.
    """
    identity = settings_identity(selected)
    if identity is None or settings_identity(report.get("strategies")) != identity:
        raise ValueError("Native ATR risk must match the exact selected settings")
    ledger = report.get("ledger", [])
    if not isinstance(ledger, list) or len(ledger) > MAX_REQUEST_PRICES:
        raise ValueError("Native ATR risk observations exceed their bounded cohort")
    definitions = {row["id"]: row for row in selected}
    observations = {}
    duplicates = set()
    for row in ledger:
        if not isinstance(row, dict):
            raise ValueError("Native ATR observations need recorded ledger rows")
        strategy = definitions.get(row.get("strategy_id"))
        details = row.get("execution_details") or {}
        atr = details.get("atr") or {}
        if (
            strategy is None
            or strategy["execution_policy"]["exit_mode"] != "atr"
            or details.get("exit_mode") != "atr"
            or atr.get("version") != "research-entry-atr-v1"
            or atr.get("symbol") != row.get("symbol")
        ):
            continue
        available, entry = atr.get("available_at"), atr.get("entry_session")
        if (
            not isinstance(available, str)
            or not isinstance(entry, str)
            or re.match(r"\d{4}-\d{2}-\d{2}T", available) is None
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry)
            or available[:10] >= entry
        ):
            continue
        risk = details.get("initial_risk_per_share")
        if type(risk) not in (int, float) or not isfinite(risk) or risk <= 0:
            continue
        key = request_identity(row)
        if key in observations:
            duplicates.add(key)
        observations[key] = {"strategy_id": row["strategy_id"], "risk_per_share": float(risk)}
    return _seal(
        {
            "settings_id": identity,
            "observations": {
                key: value for key, value in observations.items() if key not in duplicates
            },
        }
    )


def _checked_request_risks(selected, evidence):
    identity = settings_identity(selected)
    if evidence is None:
        evidence = _seal({"settings_id": identity, "observations": {}})
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"id", "settings_id", "observations"}
        or evidence.get("settings_id") != identity
        or evidence.get("id") != fingerprint({k: v for k, v in evidence.items() if k != "id"})
        or not isinstance(evidence.get("observations"), dict)
        or len(evidence["observations"]) > MAX_REQUEST_PRICES
    ):
        raise ValueError("Native ATR risk evidence changed its exact settings binding")
    strategies = {row["id"] for row in selected if row["execution_policy"]["exit_mode"] == "atr"}
    for key, observation in evidence["observations"].items():
        if (
            not isinstance(key, str)
            or re.fullmatch(r"[a-f0-9]{64}", key) is None
            or not isinstance(observation, dict)
            or set(observation) != {"strategy_id", "risk_per_share"}
            or observation["strategy_id"] not in strategies
        ):
            raise ValueError("Native ATR risk needs a known strategy and request identity")
        _number(observation["risk_per_share"], "native ATR risk per share")
    return deepcopy(evidence)


def descriptor(version=VERSION):
    if version not in (VERSION, DECISION_VERSION):
        raise ValueError("Choose a recorded capital-planning policy")
    value = {
        "version": version,
        "max_accounts": 4,
        "multipliers": [1.0, 1.25, 1.5, 2.0],
        "rounding_rupees": 1000,
        "ticket_basis": "Original capital, strategy allocation and order size define fixed initial rupee tickets; later account equity compounds the ticket fraction.",
        "scope": "Separate capital-planning variant of frozen selected rules, not selected-setup replay, minimum capital or independent validation.",
        "risk": "All original hard account risk limits remain unchanged; fixed tickets may still be clipped or blocked.",
        "representation": "Only account amounts within the native 1 to 1,000,000,000 rupee capital range and preserving active original tickets within the 0.01% to 100% order-size range are tested; inactive zero allocations retain zero tickets, and tickets are never rounded or increased.",
    }
    if version == DECISION_VERSION:
        value["decision_policy"] = {
            "cash": "Only recorded shared-cash shortfalls justify testing a higher account amount automatically.",
            "noncash": "Fixed risk, allocation, position and liquidity limits, whole-share ticket affordability and unavailable evidence require a separate decision; increasing funds is not a cash-only solution.",
            "stopping": "Stop at verified funding, any noncash blocker, no recorded cash shortfall, or the frozen account bound. Preserve every recorded constraint; overlapping tags are not proof of a unique binding cause.",
        }
    return _seal(value)


def _rows(baseline, selected):
    if (
        not isinstance(baseline, list)
        or not isinstance(selected, list)
        or not 1 <= len(baseline) <= 8
        or len(baseline) != len(selected)
        or [row["id"] for row in baseline] != [row["id"] for row in selected]
        or len({row["id"] for row in baseline}) != len(baseline)
    ):
        raise ValueError("Capital planning needs matching frozen strategy rows")


def plan(baseline, selected, capital, risk_limits=None, *, max_accounts=4, version=VERSION):
    _rows(baseline, selected)
    capital = _number(capital, "original capital")
    if type(max_accounts) is not int or max_accounts not in (3, 4):
        raise ValueError("Capital planning supports three or four reserved accounts")
    limits = normalize_limits(risk_limits or baseline[0]["execution_policy"]["risk_limits"])
    tickets, losses, trade_capitals, order_cap_floors = [], [], [], []
    for original, chosen in zip(baseline, selected, strict=True):
        if any(row["execution_policy"]["risk_limits"] != limits for row in (original, chosen)):
            raise ValueError("Capital planning cannot change frozen account risk limits")
        allocation = (
            _number(original["allocation_pct"], "strategy allocation", positive=False) / 100
        )
        order = _number(original["config"]["order_size_pct"], "original order size") / 100
        if not 0 <= allocation <= 1 or order > 1:
            raise ValueError("Capital planning original cash percentages exceed their bounds")
        ticket = capital * allocation * order
        tickets.append({"strategy_id": original["id"], "initial_ticket": ticket})
        if not allocation:
            continue
        cfg = chosen["config"]
        fee = _number(cfg.get("cost_bps", 0), "costs", positive=False) / 10000
        slip = _number(cfg.get("slippage_bps", 0), "slippage", positive=False) / 10000
        if not 0 <= fee < 1 or not 0 <= slip < 1:
            raise ValueError("Capital planning costs exceed supported bounds")
        if chosen["execution_policy"]["exit_mode"] == "percent":
            stop = _number(cfg["stop_pct"], "stop percentage") / 100
            if stop >= 1:
                raise ValueError("Capital planning stop must remain below the entry price")
            loss_fraction = 1 - (1 - stop) * (1 - slip) * (1 - fee) / ((1 + slip) * (1 + fee))
        else:
            loss_fraction = 1.0
        losses.append(ticket * loss_fraction)
        risk_fraction = (
            _number(chosen["execution_policy"]["risk_budget_fraction"], "selected risk fraction")
            if chosen["execution_policy"]["sizing"] == "stop_risk"
            else 1.0
        )
        if risk_fraction > 1:
            raise ValueError("Selected risk sizing exceeds the frozen trade-risk ceiling")
        trade_capitals.append(
            ticket * loss_fraction / (limits["max_trade_risk_pct"] / 100 * risk_fraction)
        )
        order_cap_floors.append(capital * order)
    if not losses:
        raise ValueError("Capital planning requires at least one positive initial ticket")
    maximum_ticket = max(row["initial_ticket"] for row in tickets)
    maximum_loss = max(losses)
    positions = limits["max_open_positions"]
    seed = max(
        max(order_cap_floors),
        maximum_ticket / (limits["max_gross_exposure_pct"] / 100),
        maximum_ticket / (limits["max_symbol_exposure_pct"] / 100),
        max(trade_capitals),
        positions * maximum_loss / (limits["max_total_stop_risk_pct"] / 100),
        positions * maximum_ticket / (limits["max_gross_exposure_pct"] / 100),
    )
    seed = ceil(round(seed / 1000, 10)) * 1000
    policy = descriptor(version)
    candidates = [ceil(seed * factor / 1000) * 1000 for factor in policy["multipliers"]]
    # Tiny ticket sizes can round neighbouring candidates to the same amount.
    candidates = sorted(set(candidates))[:max_accounts]
    representable = [
        amount
        for amount in candidates
        if 1 <= amount <= MAX_NATIVE_CAPITAL
        and all(
            not row["allocation_pct"]
            or 0.01 <= row["config"]["order_size_pct"] * capital / amount <= 100
            for row in baseline
        )
    ]
    return _seal(
        {
            "version": version,
            "policy_id": policy["id"],
            "original_capital": capital,
            "baseline": deepcopy(baseline),
            "selected": deepcopy(selected),
            "selected_config_id": settings_identity(selected),
            "risk_limits": limits,
            "tickets": tickets,
            "seed_capital": seed,
            "candidate_capitals": representable,
            "representation_limited": len(representable) != len(candidates),
            "max_accounts": max_accounts,
            "seed_basis": "Conservative initial-ticket and modeled stop-risk capacity for the configured position ceiling; an estimate, not a verified funding requirement.",
        }
    )


def _validate_plan(value):
    if not isinstance(value, dict) or value.get("id") != fingerprint(
        {key: item for key, item in value.items() if key != "id"}
    ):
        raise ValueError("The frozen capital plan changed")
    expected = plan(
        value["baseline"],
        value["selected"],
        value["original_capital"],
        value["risk_limits"],
        max_accounts=value["max_accounts"],
        version=value["version"],
    )
    if value != expected:
        raise ValueError("The frozen capital plan differs from its declared policy")


def settings_for(capital, baseline, selected, *, original_capital=None):
    _rows(baseline, selected)
    capital = _number(capital, "planned capital")
    if not 1 <= capital <= MAX_NATIVE_CAPITAL:
        raise ValueError("Planned capital exceeds the supported native account range")
    if original_capital is None:
        original_capital = baseline[0]["config"]["initial_capital"]
    original_capital = _number(original_capital, "original capital")
    result = deepcopy(selected)
    for original, row in zip(baseline, result, strict=True):
        fraction = (
            _number(original["config"]["order_size_pct"], "original order size")
            * original_capital
            / capital
        )
        if original["allocation_pct"] == 0:
            fraction = original["config"]["order_size_pct"]
        if not 0.01 <= fraction <= 100:
            raise ValueError("Planned capital cannot represent the original fixed ticket")
        if original["execution_policy"]["risk_limits"] != row["execution_policy"]["risk_limits"]:
            raise ValueError("Capital planning cannot change fixed risk limits")
        row["allocation_pct"] = original["allocation_pct"]
        row["config"].update(initial_capital=capital, order_size_pct=fraction)
    return result


def account_receipt(report, plan, capital):
    _validate_plan(plan)
    capital = _number(capital, "tested capital")
    if capital not in plan["candidate_capitals"]:
        raise ValueError("The capital account was not frozen before testing")
    if _number(report["summary"]["initial_capital"], "native initial capital") != capital:
        raise ValueError("Native account capital differs from the frozen plan")
    blockers, clipped, admitted = Counter(), 0, 0
    constraint_counts, category_counts = Counter(), Counter()
    cash_requests, noncash_requests = 0, 0
    inactive = {row["strategy_id"] for row in plan["tickets"] if row["initial_ticket"] == 0}
    for row in report["ledger"]:
        if row.get("strategy_id") in inactive:
            continue
        reason = str(row.get("reason") or "").lower()
        status = row.get("status")
        if status not in ("closed", "open", "pending", "excluded", "skipped"):
            raise ValueError("Native capital request status is unavailable")
        details = row.get("execution_details") or {}
        quantity = _number(row.get("quantity", 0), "native quantity", positive=False)
        if quantity < 0:
            raise ValueError("Capital planning cannot inspect negative native quantities")
        if status == "excluded":
            if "not met:" in reason or reason.startswith("maximum holding window crosses"):
                continue
            blockers["unavailable_evidence"] += 1
            category_counts["unavailable_evidence"] += 1
            noncash_requests += 1
            continue
        if status == "pending" and not quantity:
            blockers["unavailable_entry_period"] += 1
            category_counts["unavailable_entry_period"] += 1
            noncash_requests += 1
            continue
        if quantity:
            admitted += 1
            if "requested_quantity" not in details:
                blockers["unavailable_execution_evidence"] += 1
                category_counts["unavailable_execution_evidence"] += 1
                noncash_requests += 1
        requested = _number(
            details.get("requested_quantity", quantity), "requested quantity", positive=False
        )
        if requested < 0 or requested < quantity:
            raise ValueError("Native requested quantities disagree with actual fills")
        constraints = details.get("constraints", [])
        if not isinstance(constraints, list) or any(
            not isinstance(item, str) for item in constraints
        ):
            raise ValueError("Native capital constraints are unavailable")
        blocked = status == "skipped" or quantity < requested
        if blocked:
            clipped += int(quantity > 0)
            tags = set(constraints)
            constraint_counts.update(tags)
            categories = set()
            if "shared_cash" in tags:
                cash_requests += 1
                categories.add("cash_shortfall")
            if "open_positions" in tags:
                categories.add("position_limit")
            if "daily_volume_participation" in tags:
                categories.add("liquidity_limit")
            policy_tags = {
                "gross_exposure",
                "strategy_allocation",
                "symbol_exposure",
                "trade_stop_risk",
                "total_stop_risk",
                "correlated_exposure",
                "daily_buy_turnover",
                "risk_sizing_target",
                "portfolio_symbol_allocation",
                "portfolio_cluster_allocation",
            }
            if tags & policy_tags:
                categories.add("risk_or_allocation_policy")
            if tags - policy_tags - {"shared_cash", "open_positions", "daily_volume_participation"}:
                categories.add("unexplained_execution_limit")
            if "unavailable" in reason or "missing" in reason:
                categories.add("unavailable_evidence")
            if not requested or ("requested_cash_budget" in reason and not tags):
                categories.add("fixed_ticket_affordability")
            if not categories:
                categories.add("unexplained_execution_limit")
            category_counts.update(categories)
            noncash_requests += int(bool(categories - {"cash_shortfall"}))
            if "open_positions" in tags:
                blockers["position_count"] += 1
            elif "daily_volume_participation" in tags:
                blockers["liquidity"] += 1
            elif "unavailable" in reason or "missing" in reason:
                blockers["unavailable_evidence"] += 1
            elif not requested or ("requested_cash_budget" in reason and not tags):
                blockers["fixed_ticket_cannot_fund_share"] += 1
            elif tags & {
                "shared_cash",
                "gross_exposure",
                "strategy_allocation",
                "symbol_exposure",
                "trade_stop_risk",
                "total_stop_risk",
                "correlated_exposure",
                "daily_buy_turnover",
                "risk_sizing_target",
                "portfolio_symbol_allocation",
                "portfolio_cluster_allocation",
            }:
                blockers["funding_or_percentage_limit"] += 1
            else:
                blockers["other_execution_limit"] += 1
    value = {
        "version": plan["version"],
        "plan_id": plan["id"],
        "capital": capital,
        "ledger_id": fingerprint(report["ledger"]),
        "summary": deepcopy(report["summary"]),
        "admitted_requests": admitted,
        "clipped_requests": clipped,
        "blockers": dict(sorted(blockers.items())),
        "verified_sufficient": admitted > 0 and not blockers,
    }
    if plan["version"] == DECISION_VERSION:
        value["diagnosis"] = _diagnosis(
            value["verified_sufficient"],
            cash_requests,
            noncash_requests,
            constraint_counts,
            category_counts,
        )
    return _seal(value)


def _diagnosis(verified, cash, noncash, constraints, categories):
    action = (
        "funding_verified"
        if verified
        else "review_noncash_constraints"
        if noncash
        else "test_higher_capital"
        if cash
        else "insufficient_funding_evidence"
    )
    reasons = {
        "funding_verified": "All recorded eligible requests were funded in this historical planning variant.",
        "review_noncash_constraints": "Recorded requests also hit policy, ticket or evidence constraints. Preserve those limits and resolve the noncash causes before recommending more money.",
        "test_higher_capital": "Recorded requests have shared-cash shortfalls without a recorded noncash blocker. Test the next frozen amount within the existing budget.",
        "insufficient_funding_evidence": "No complete eligible fills or attributable shared-cash shortfalls establish a reason to increase funds.",
    }
    return {
        "action": action,
        "stop_increases": action != "test_higher_capital",
        "cash_shortfall_requests": cash,
        "noncash_blocker_requests": noncash,
        "constraint_counts": dict(sorted(constraints.items())),
        "categories": dict(sorted(categories.items())),
        "reason": reasons[action],
        "scope": "All recorded clipping or rejection tags are preserved; they may overlap and do not establish a unique causal bottleneck. No fixed limit is relaxed.",
    }


def should_continue(receipt):
    if not isinstance(receipt, dict) or receipt.get("id") != fingerprint(
        {key: value for key, value in receipt.items() if key != "id"}
    ):
        raise ValueError("Recorded capital account evidence changed")
    if receipt.get("version") == VERSION:
        return not receipt["verified_sufficient"]
    if receipt.get("version") != DECISION_VERSION:
        raise ValueError("Recorded capital decision policy changed")
    diagnosis = receipt.get("diagnosis", {})
    for name in ("cash_shortfall_requests", "noncash_blocker_requests"):
        if type(diagnosis.get(name)) is not int or diagnosis[name] < 0:
            raise ValueError("Recorded capital diagnosis counts changed")
    for name in ("constraint_counts", "categories"):
        values = diagnosis.get(name)
        if not isinstance(values, dict) or any(
            not isinstance(key, str) or type(value) is not int or value <= 0
            for key, value in values.items()
        ):
            raise ValueError("Recorded capital constraint counts changed")
    if diagnosis["categories"].get("cash_shortfall", 0) != diagnosis["cash_shortfall_requests"]:
        raise ValueError("Recorded capital cash-shortfall counts disagree")
    noncash_count = sum(
        value for key, value in diagnosis["categories"].items() if key != "cash_shortfall"
    )
    if not diagnosis["noncash_blocker_requests"] <= noncash_count or bool(noncash_count) != bool(
        diagnosis["noncash_blocker_requests"]
    ):
        raise ValueError("Recorded capital noncash-blocker counts disagree")
    expected = _diagnosis(
        receipt["verified_sufficient"],
        diagnosis["cash_shortfall_requests"],
        diagnosis["noncash_blocker_requests"],
        diagnosis["constraint_counts"],
        diagnosis["categories"],
    )
    if diagnosis != expected:
        raise ValueError("Recorded capital decision changed")
    return not diagnosis["stop_increases"]


def finish(plan, receipts):
    _validate_plan(plan)
    if not isinstance(receipts, list) or len(receipts) > len(plan["candidate_capitals"]):
        raise ValueError("Capital planning exceeded its frozen account budget")
    sufficient, stopped = None, False
    for index, receipt in enumerate(receipts):
        if (
            stopped
            or not isinstance(receipt, dict)
            or receipt.get("plan_id") != plan["id"]
            or receipt.get("capital") != plan["candidate_capitals"][index]
            or receipt.get("id")
            != fingerprint({key: value for key, value in receipt.items() if key != "id"})
            or receipt.get("verified_sufficient")
            != (receipt.get("admitted_requests", 0) > 0 and not receipt.get("blockers"))
        ):
            raise ValueError("Recorded capital accounts differ from their frozen sequence")
        if receipt["verified_sufficient"]:
            sufficient = receipt["capital"]
        stopped = not should_continue(receipt)
    diagnosis = None
    if plan["version"] == DECISION_VERSION:
        diagnosis = (
            deepcopy(receipts[-1]["diagnosis"]) if receipts else _diagnosis(False, 0, 0, {}, {})
        )
        if not plan["candidate_capitals"]:
            diagnosis.update(
                action="unsupported_capital_representation",
                stop_increases=True,
                reason="No frozen account amount can preserve the original ticket within supported account-capital and order-size ranges. No additional account is admitted.",
            )
        elif receipts and not stopped and len(receipts) == len(plan["candidate_capitals"]):
            diagnosis.update(
                action="account_budget_exhausted",
                stop_increases=True,
                reason="The recorded capital amounts were tested without establishing sufficient funding. The frozen account budget is complete; no further increase is admitted.",
            )
    return _seal(
        {
            "version": plan["version"],
            "plan_id": plan["id"],
            "status": "verified_sufficient" if sufficient is not None else "no_verified_amount",
            "estimated_capital": plan["seed_capital"],
            "reference_tickets": deepcopy(plan["tickets"]),
            "original_capital": plan["original_capital"],
            "tested_range": [receipts[0]["capital"], receipts[-1]["capital"]] if receipts else None,
            "representation_limited": plan["representation_limited"],
            "representation_reason": (
                "The estimated amount cannot preserve the original initial ticket within supported account-capital and order-size ranges; no funding account was tested."
                if not plan["candidate_capitals"]
                else "Some planned account amounts could not preserve the original initial ticket within supported account-capital and order-size ranges."
                if plan["representation_limited"]
                else None
            ),
            "verified_capital": sufficient,
            "accounts": deepcopy(receipts),
            "policy": descriptor(plan["version"]),
            **({"diagnosis": diagnosis} if diagnosis is not None else {}),
            "independently_validated": False,
            "scope": "Sufficient only for recorded eligible requests in this fixed-initial-ticket historical planning variant; not minimum capital or an exact replay of the selected setup.",
        }
    )


def selected_account_descriptor(version=SELECTED_VERSION):
    """A separate capacity experiment, never a replacement for winner validation."""
    if version not in (
        SELECTED_VERSION,
        INTENDED_VERSION,
        CLOSED_LOOP_VERSION,
        FEASIBLE_VERSION,
        CAUSAL_VERSION,
        ATR_VERSION,
    ):
        raise ValueError("Selected account policy version is unsupported")
    return _seal(
        {
            "version": version,
            "max_accounts": 4,
            "size_multipliers": [1.0, 0.5, 0.25]
            if version not in (FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
            else "Original size, analytically feasible single-ticket factor, then a conservative concurrent-slot factor; all frozen before native results.",
            "cash_buffer_fraction": 0.01,
            "rounding_rupees": 1000,
            "capital_basis": "Selected percentage sizing and fee/slippage-adjusted historical entry prices, never original fixed rupee tickets or profit.",
            "routing": "Whole-share affordability tests the frozen larger account; shared cash or percentage risk clipping tests smaller frozen cash and risk targets. Missing evidence and position/liquidity exclusions remain recorded and never relax hard limits.",
            "operating_point": "On the identical recorded opportunity cohort, minimize cash-shortfall plus whole-share-affordability plus percentage-policy constrained requests; then maximize admitted requests, prefer less capital and the earlier frozen option. Ignore profit. A tested operating point with exclusions is not verified complete funding or performance promotion.",
            "scope": "Bounded historical capacity variants. A feasible account is not a minimum, performance recommendation or independent validation; any changed settings require unchanged performance and chronological validation before promotion.",
            **(
                {
                    "quantity_basis": "For stop-risk sizing, intended units are the minimum of the recorded cash ceiling and the native friction-adjusted risk target. Fulfilling that target is not a shortage. Missing exact entry prices or risk evidence cannot establish sufficiency. Raw native constraint tags remain diagnostic, including nonbinding limits below the cash ceiling."
                }
                if version
                in (
                    INTENDED_VERSION,
                    CLOSED_LOOP_VERSION,
                    FEASIBLE_VERSION,
                    CAUSAL_VERSION,
                    ATR_VERSION,
                )
                else {}
            ),
            **(
                {
                    "capital_floor": "Freeze a separate fee/slippage-adjusted whole-share, exposure and stop-risk capital floor for each actual cash-size and risk-target multiplier, rounded to rupees 1000 with a one-percent buffer, never below original capital.",
                    "capacity_rank": "Prefer verified sufficiency, then known evidence, fewer unique constrained requests and evidence gaps, more admitted requests, less capital and earlier frozen option. Raw overlapping cause counts and profit never choose the account. All reported selections and diagnosis refer to the same chosen receipt.",
                    "request_prices": "Exact eligible historical opening prices keyed by frozen strategy/symbol/signal-time/source-row identity; missing observations remain unavailable, never inferred from later or maximum prices.",
                }
                if version in (CLOSED_LOOP_VERSION, FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
                else {}
            ),
            **(
                {
                    "feasible_sizing": "Bound cash and percentage-stop sizing by prospective fee/slippage-adjusted native exposure and stop-risk ceilings, retaining a one-percent buffer. A second size reserves capacity equally across the existing hard position allowance, conservatively treating positions as related. Never change allocation weights or any hard limit. ATR stop capacity remains native-only; do not infer ATR from prices.",
                    "representation": "Omit only size options whose active ticket would fall below the native 0.01-percent representation floor. Preserve the original and same-size capital accounts, all exclusions and the existing maximum four-account allowance. Analytical feasibility is an estimate; only native receipts establish funding.",
                }
                if version in (FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
                else {}
            ),
            **(
                {
                    "constraint_diagnosis": "For a positive intended whole-share request with zero native fill and verified open_positions_before at or above the unchanged hard position allowance, retain the position exclusion and every raw constraint tag without treating overlapping cash/percentage tags as a repairable funding cause. Unknown or incomplete position evidence retains the conservative original diagnosis. This never establishes complete funding or relaxes performance validation.",
                }
                if version in (CAUSAL_VERSION, ATR_VERSION)
                else {}
            ),
            **(
                {
                    "atr_floor": "Use positive native prior-data ATR risk per share, bound to the exact selected settings, to estimate one-share capital for each actual cash/risk multiplier. Missing ATR remains unknown; never infer it from prices or profit.",
                    "affordability_recovery": "After a native whole-share failure, consider only untried larger frozen capital options whose estimated cash and risk budgets can admit an observed zero-unit request. Position/liquidity limits and every validation gate remain unchanged; estimates never establish a pass.",
                }
                if version == ATR_VERSION
                else {}
            ),
        }
    )


def selected_account_plan(
    selected,
    capital,
    entry_prices,
    *,
    max_accounts=4,
    version=SELECTED_VERSION,
    request_prices=None,
    request_risks=None,
):
    """Freeze all account/size variants before reading native capacity outcomes.

    ``entry_prices`` maps strategy IDs to raw eligible historical entry prices.
    These prices describe planning capacity only, not a tradable hindsight sizing
    policy. ATR stops cannot be inferred from prices; native execution remains the
    authority for their risk capacity.
    """
    capacity_policy = selected_account_descriptor(version)
    if version in (CLOSED_LOOP_VERSION, FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION):
        return _closed_loop_plan(
            selected,
            capital,
            entry_prices,
            max_accounts,
            request_prices,
            version=version,
            request_risks=request_risks,
        )
    if request_risks is not None:
        raise ValueError("Native ATR risk observations require the v8 capital policy")
    if request_prices is not None:
        raise ValueError("Frozen request prices require the closed-loop capital policy")
    _rows(selected, selected)
    capital = _number(capital, "selected capital")
    if (
        not 1 <= capital <= MAX_NATIVE_CAPITAL
        or type(max_accounts) is not int
        or max_accounts not in (3, 4)
    ):
        raise ValueError("Selected account planning exceeds supported bounds")
    if not isinstance(entry_prices, dict) or set(entry_prices) != {row["id"] for row in selected}:
        raise ValueError("Selected account planning needs prices for each frozen strategy")
    limits = normalize_limits(selected[0]["execution_policy"]["risk_limits"])
    prices, floors, missing = {}, [capital], []
    for row in selected:
        if row["execution_policy"]["risk_limits"] != limits:
            raise ValueError("Selected account planning cannot change account risk limits")
        observed = entry_prices[row["id"]]
        if not isinstance(observed, list):
            raise ValueError("Selected entry prices must be lists")
        prices[row["id"]] = sorted({_number(item, "historical entry price") for item in observed})
        allocation = _number(row["allocation_pct"], "selected allocation", positive=False) / 100
        order = _number(row["config"]["order_size_pct"], "selected order size") / 100
        if not 0 <= allocation <= 1 or not 0.0001 <= order <= 1:
            raise ValueError("Selected account percentages exceed supported bounds")
        if not allocation:
            continue
        if not observed:
            missing.append(row["id"])
            continue
        cfg, policy = row["config"], row["execution_policy"]
        fee = _number(cfg.get("cost_bps", 0), "costs", positive=False) / 10000
        slip = _number(cfg.get("slippage_bps", 0), "slippage", positive=False) / 10000
        if not 0 <= fee < 1 or not 0 <= slip < 1:
            raise ValueError("Selected account costs exceed supported bounds")
        raw = max(prices[row["id"]])
        cash = raw * (1 + slip) * (1 + fee)
        # Native exposure capacities include prospective entry friction in
        # account equity. Known fixed percentage ceilings must also afford one
        # whole share; more money never changes their percentages.
        for name in (
            "max_gross_exposure_pct",
            "max_symbol_exposure_pct",
            "max_correlated_exposure_pct",
        ):
            if name in limits:
                fraction = limits[name] / 100
                floors.append((raw / fraction + cash - raw) / 0.99)
        # Worst frozen reduced order must still afford one whole share. This is
        # an analytical floor, not proof that all concurrent orders can fill.
        smallest = 0.25 if max_accounts == 4 else 0.5
        floors.append(cash / (allocation * order * smallest * 0.99))
        if policy["exit_mode"] == "percent":
            stop = _number(cfg["stop_pct"], "selected stop percentage") / 100
            if stop >= 1:
                raise ValueError("Selected stop must remain below entry price")
            loss = cash - raw * (1 + slip) * (1 - stop) * (1 - slip) * (1 - fee)
            target = limits["max_trade_risk_pct"] / 100
            if policy["sizing"] == "stop_risk":
                target *= _number(policy["risk_budget_fraction"], "selected risk fraction")
            smallest = 0.25 if max_accounts == 4 else 0.5
            floors.append((loss / (target * smallest) + cash - raw) / 0.99)
            floors.append((loss / (limits["max_total_stop_risk_pct"] / 100) + cash - raw) / 0.99)
    estimate = max(capital, ceil(max(floors) / 1000) * 1000)
    usable = min(estimate, MAX_NATIVE_CAPITAL)
    options = [{"capital": capital, "size_multiplier": 1.0, "purpose": "selected_account"}]
    options.append(
        {"capital": usable, "size_multiplier": 1.0, "purpose": "whole_share_affordability"}
    )
    options.extend(
        {"capital": usable, "size_multiplier": factor, "purpose": "reduce_cash_and_risk_demand"}
        for factor in (0.5, 0.25)
    )
    options = options[:max_accounts]
    return _seal(
        {
            "version": version,
            "policy_id": capacity_policy["id"],
            "selected": deepcopy(selected),
            "selected_config_id": settings_identity(selected),
            "original_capital": capital,
            "risk_limits": limits,
            "entry_prices": prices,
            "missing_price_strategies": missing,
            "estimated_capital": estimate,
            "representation_limited": estimate > MAX_NATIVE_CAPITAL,
            "max_accounts": max_accounts,
            "options": options,
        }
    )


def _option_floor(selected, capital, prices, multiplier, request_risks=None):
    floors = [capital]
    for row in selected:
        allocation = row["allocation_pct"] / 100
        observed = prices[row["id"]]
        if not allocation or not observed:
            continue
        cfg, policy = row["config"], row["execution_policy"]
        limits = policy["risk_limits"]
        raw = max(observed)
        fee, slip = cfg.get("cost_bps", 0) / 10000, cfg.get("slippage_bps", 0) / 10000
        cash = raw * (1 + slip) * (1 + fee)
        for name in (
            "max_gross_exposure_pct",
            "max_symbol_exposure_pct",
            "max_correlated_exposure_pct",
        ):
            if name in limits:
                floors.append((raw / (limits[name] / 100) + cash - raw) / 0.99)
        order = cfg["order_size_pct"] / 100 * multiplier
        floors.append(cash / (allocation * order * 0.99))
        if policy["exit_mode"] == "percent":
            stop = cfg["stop_pct"] / 100
            loss = cash - raw * (1 + slip) * (1 - stop) * (1 - slip) * (1 - fee)
            target = limits["max_trade_risk_pct"] / 100
            if policy["sizing"] == "stop_risk":
                target *= policy["risk_budget_fraction"] * multiplier
            floors.append((loss / target + cash - raw) / 0.99)
            floors.append((loss / (limits["max_total_stop_risk_pct"] / 100) + cash - raw) / 0.99)
        elif request_risks is not None:
            risks = [
                observation["risk_per_share"]
                for observation in request_risks["observations"].values()
                if observation["strategy_id"] == row["id"]
            ]
            if risks:
                loss = max(risks)
                target = limits["max_trade_risk_pct"] / 100
                if policy["sizing"] == "stop_risk":
                    target *= policy["risk_budget_fraction"] * multiplier
                floors.append((loss / target + cash - raw) / 0.99)
                floors.append(
                    (loss / (limits["max_total_stop_risk_pct"] / 100) + cash - raw) / 0.99
                )
    return max(capital, ceil(max(floors) / 1000) * 1000)


def _feasible_size_factors(selected):
    """Freeze percentage capacities from settings alone, never from profitability.

    The first reduction fits a new ticket into an empty account. The second
    reserves the same hard capacity across the maximum permitted open positions,
    including the worst case of related/same-symbol positions. This estimate
    cannot promise that observed prices, turnover or an ATR risk target will fit;
    the unchanged native engine and receipts remain the only admission authority.
    """
    candidates = []
    active = [row for row in selected if row["allocation_pct"] > 0]
    for concurrent in (False, True):
        factor = 0.5
        for row in active:
            cfg, policy = row["config"], row["execution_policy"]
            limits = policy["risk_limits"]
            slots = limits["max_open_positions"] if concurrent else 1
            allocation = row["allocation_pct"] / 100
            ticket = allocation * cfg["order_size_pct"] / 100
            fee = cfg.get("cost_bps", 0) / 10000
            slip = cfg.get("slippage_bps", 0) / 10000
            cash = (1 + slip) * (1 + fee)
            friction = cash - 1
            exposure = (
                min(
                    allocation,
                    *(
                        limits[name] / 100
                        for name in (
                            "max_gross_exposure_pct",
                            "max_symbol_exposure_pct",
                            "max_correlated_exposure_pct",
                        )
                        if name in limits
                    ),
                )
                / slots
            )
            # Native limits use equity *after* prospective entry friction.
            budget = cash * exposure / (1 + exposure * friction)
            if policy["exit_mode"] == "percent":
                stop = cfg["stop_pct"] / 100
                loss = cash - (1 + slip) * (1 - stop) * (1 - slip) * (1 - fee)
                risk = min(
                    limits["max_trade_risk_pct"] / 100,
                    limits["max_total_stop_risk_pct"] / 100 / slots,
                )
                budget = min(budget, cash * risk / (loss + risk * friction))
            factor = min(factor, budget * 0.99 / ticket)
        if candidates:
            factor = min(factor, candidates[-1] / 2)
        # Do not invent a rounded-up size that no longer fits the frozen cap.
        if active and all(
            0.01 <= row["config"]["order_size_pct"] * factor <= 100 for row in active
        ):
            candidates.append(factor)
    return candidates


def _closed_loop_plan(
    selected,
    capital,
    entry_prices,
    max_accounts,
    request_prices,
    *,
    version=CLOSED_LOOP_VERSION,
    request_risks=None,
):
    # Reuse the unchanged v4 admission for settings, price lists and hard caps;
    # replace only its quarter-size blanket estimate with per-option floors.
    previous = selected_account_plan(
        selected, capital, entry_prices, max_accounts=max_accounts, version=INTENDED_VERSION
    )
    observations = {} if request_prices is None else request_prices
    if (
        type(observations) is not dict
        or len(observations) > MAX_REQUEST_PRICES
        or any(
            type(key) is not str or re.fullmatch(r"[a-f0-9]{64}", key) is None
            for key in observations
        )
    ):
        raise ValueError("Frozen request prices exceed their registered identity bounds")
    observations = {
        key: _number(price, "frozen request opening price") for key, price in observations.items()
    }
    if version != ATR_VERSION and request_risks is not None:
        raise ValueError("Native ATR risk observations require the v8 capital policy")
    if version == ATR_VERSION:
        request_risks = _checked_request_risks(selected, request_risks)
    factors = (
        [1.0, *_feasible_size_factors(previous["selected"])]
        if version in (FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
        else [1.0, 0.5, 0.25]
    )
    estimates = [
        previous["original_capital"],
        *[
            _option_floor(
                selected,
                previous["original_capital"],
                previous["entry_prices"],
                factor,
                request_risks=request_risks,
            )
            for factor in factors
        ],
    ][:max_accounts]
    options = [deepcopy(previous["options"][0])]
    options.extend(
        {
            "capital": min(estimates[index], MAX_NATIVE_CAPITAL),
            "size_multiplier": factor,
            "purpose": "whole_share_affordability" if index == 1 else "reduce_cash_and_risk_demand",
        }
        for index, factor in enumerate(factors[: max_accounts - 1], 1)
    )
    options[0]["capital"] = min(estimates[0], MAX_NATIVE_CAPITAL)
    return _seal(
        {
            **{key: value for key, value in previous.items() if key != "id"},
            "version": version,
            "policy_id": selected_account_descriptor(version)["id"],
            "options": options,
            "option_estimates": estimates,
            "estimated_capital": estimates[1],
            "representation_limited": any(amount > MAX_NATIVE_CAPITAL for amount in estimates),
            "request_prices": observations,
            **({"request_risks": request_risks} if version == ATR_VERSION else {}),
        }
    )


def _validate_selected_plan(value):
    if not isinstance(value, dict) or value.get("id") != fingerprint(
        {key: item for key, item in value.items() if key != "id"}
    ):
        raise ValueError("The frozen selected account plan changed")
    if value != selected_account_plan(
        value["selected"],
        value["original_capital"],
        value["entry_prices"],
        max_accounts=value["max_accounts"],
        version=value["version"],
        request_prices=value.get("request_prices"),
        request_risks=value.get("request_risks"),
    ):
        raise ValueError("The selected account plan differs from its declared policy")


def selected_account_settings(value, index):
    _validate_selected_plan(value)
    if type(index) is not int or not 0 <= index < len(value["options"]):
        raise ValueError("Selected account option was not frozen")
    option = value["options"][index]
    result = deepcopy(value["selected"])
    for row in result:
        if (
            value["version"] in (FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
            and not row["allocation_pct"]
        ):
            # An inactive strategy has no ticket to resize. Keep its valid
            # configuration representable rather than letting it veto an
            # independently feasible active account.
            row["config"]["initial_capital"] = option["capital"]
            continue
        fraction = row["config"]["order_size_pct"] * option["size_multiplier"]
        if not 0.01 <= fraction <= 100:
            raise ValueError("Selected reduced size exceeds native representation")
        row["config"].update(initial_capital=option["capital"], order_size_pct=fraction)
        # Lower both competing targets once. Their native minimum determines
        # units; reducing only a nonbinding cash ceiling leaves risk sizing
        # unchanged. Account hard risk ceilings and allocation do not change.
        if row["execution_policy"]["sizing"] == "stop_risk":
            fraction = row["execution_policy"]["risk_budget_fraction"] * option["size_multiplier"]
            if not 0 < fraction <= 1:
                raise ValueError("Selected reduced risk target exceeds native representation")
            row["execution_policy"]["risk_budget_fraction"] = fraction
    return result


def request_identity(row):
    """Exact source opportunity identity shared by frozen observation producers."""
    return fingerprint(
        {
            key: row.get(key)
            for key in ("strategy_id", "symbol", "signal_date", "signal_timestamp", "source_row")
        }
    )


def _intended_units(row, details, settings, requested, *, request_prices=None):
    """Recover the native target, never infer an absent historical opening."""
    policy = settings["execution_policy"]
    if policy["sizing"] != "stop_risk" or not requested:
        return requested
    try:
        raw = row.get("entry_raw_price")
        if raw is None and request_prices is not None:
            raw = request_prices.get(request_identity(row))
        raw = _number(raw, "native raw entry price")
        equity = _number(details["opening_equity"], "native opening equity", positive=False)
        loss = _number(details["initial_risk_per_share"], "native stop loss per share")
        target = policy["risk_limits"]["max_trade_risk_pct"] / 100 * policy["risk_budget_fraction"]
        reported = _number(details["target_stop_risk_pct"], "native stop risk target") / 100
        if details.get("sizing") != "stop_risk" or not isclose(
            reported, target, rel_tol=1e-12, abs_tol=0
        ):
            return None
        cfg = settings["config"]
        friction = raw * (1 + cfg["slippage_bps"] / 10000) * (1 + cfg["cost_bps"] / 10000) - raw
        units = max(0.0, equity * target / (loss + target * friction))
        return min(requested, floor(units + 1e-9))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def selected_account_receipt(report, value, index):
    _validate_selected_plan(value)
    settings = selected_account_settings(value, index)
    option = value["options"][index]
    if _number(report["summary"]["initial_capital"], "native initial capital") != option["capital"]:
        raise ValueError("Native selected capital differs from its frozen option")
    # Orchestration independently verifies full normalized settings and native
    # constraint evidence; preserve exact ledger/summary identities here too.
    counts, tags = Counter(), Counter()
    gap_requests = set()
    intended_policy = value["version"] in (
        INTENDED_VERSION,
        CLOSED_LOOP_VERSION,
        FEASIBLE_VERSION,
        CAUSAL_VERSION,
        ATR_VERSION,
    )
    whole_share_observations = {}
    by_strategy = {row["id"]: row for row in settings}
    active = {row["id"] for row in settings if row["allocation_pct"] > 0}
    policy_tags = {
        "gross_exposure",
        "strategy_allocation",
        "symbol_exposure",
        "trade_stop_risk",
        "total_stop_risk",
        "correlated_exposure",
        "daily_buy_turnover",
        "portfolio_symbol_allocation",
        "portfolio_cluster_allocation",
    }
    for row_index, row in enumerate(report["ledger"]):
        if row.get("strategy_id") is not None and row["strategy_id"] not in active:
            counts["inactive_strategy"] += 1
            continue
        status, reason = row.get("status"), str(row.get("reason") or "").lower()
        if status not in ("closed", "open", "pending", "excluded", "skipped"):
            raise ValueError("Selected account request status is unavailable")
        if status == "excluded":
            category = (
                "rule_or_window_exclusion"
                if "not met:" in reason or reason.startswith("maximum holding window crosses")
                else "unavailable_evidence"
            )
            counts[category] += 1
            if category == "unavailable_evidence":
                gap_requests.add(row_index)
            continue
        details = row.get("execution_details") or {}
        quantity = _number(row.get("quantity", 0), "native quantity", positive=False)
        requested = _number(
            details.get("requested_quantity", quantity), "requested quantity", positive=False
        )
        if quantity < 0 or requested < quantity:
            raise ValueError("Selected account requested quantities disagree with fills")
        constraints = details.get("constraints", [])
        if not isinstance(constraints, list) or any(
            not isinstance(item, str) for item in constraints
        ):
            raise ValueError("Selected account constraints are unavailable")
        if status == "pending" and not quantity:
            counts["unavailable_evidence"] += 1
            gap_requests.add(row_index)
            continue
        counts["eligible_requests"] += 1
        if quantity:
            counts["admitted_requests"] += 1
        if (quantity or intended_policy) and "requested_quantity" not in details:
            counts["unavailable_evidence"] += 1
            gap_requests.add(row_index)
        intended = requested
        if intended_policy:
            tags.update(set(constraints))
            strategy = by_strategy.get(row.get("strategy_id"))
            intended = (
                _intended_units(
                    row,
                    details,
                    strategy,
                    requested,
                    request_prices=value.get("request_prices")
                    if value["version"]
                    in (CLOSED_LOOP_VERSION, FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
                    else None,
                )
                if strategy is not None
                else None
            )
            if intended is None:
                counts["unavailable_evidence"] += 1
                counts["unavailable_sizing_intent"] += 1
                gap_requests.add(row_index)
                continue
            if quantity > intended:
                raise ValueError("Native filled quantity exceeds its frozen sizing intent")
            if quantity == intended and 0 < intended < requested:
                counts["fulfilled_risk_targets"] += 1
        if status == "skipped" or quantity < intended:
            counts["constrained_requests"] += 1
            causes = set(constraints)
            if intended_policy and not intended:
                counts["whole_share_affordability"] += 1
                if value["version"] == ATR_VERSION:
                    observation = _whole_share_observation(row, details, strategy, value, index)
                    if observation is not None:
                        whole_share_observations[request_identity(row)] = observation
                continue
            if not intended_policy:
                tags.update(causes)
            before = details.get("open_positions_before")
            fixed_position_block = (
                value["version"] in (CAUSAL_VERSION, ATR_VERSION)
                and not quantity
                and intended > 0
                and "open_positions" in causes
                and strategy is not None
                and type(before) in (int, float)
                and 0 <= before <= 200
                and isfinite(before)
                and before == int(before)
                and before >= strategy["execution_policy"]["risk_limits"]["max_open_positions"]
            )
            if "shared_cash" in causes and not fixed_position_block:
                counts["cash_shortfall"] += 1
            if causes & policy_tags and not fixed_position_block:
                counts["percentage_policy"] += 1
            if "open_positions" in causes:
                counts["position_limit"] += 1
            if "daily_volume_participation" in causes:
                counts["liquidity_limit"] += 1
            if not requested and not causes:
                counts["whole_share_affordability"] += 1
            if "unavailable" in reason or "missing" in reason:
                counts["unavailable_evidence"] += 1
                gap_requests.add(row_index)
            if (
                causes
                - policy_tags
                - {
                    "shared_cash",
                    "open_positions",
                    "daily_volume_participation",
                    "risk_sizing_target",
                    "requested_cash_budget",
                }
                or not causes
                and requested
            ):
                counts["unexplained_execution_limit"] += 1
                gap_requests.add(row_index)
    if value["version"] in (CLOSED_LOOP_VERSION, FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION):
        counts["evidence_gap_requests"] = len(gap_requests)
    complete = (
        counts["admitted_requests"] > 0
        and not counts["constrained_requests"]
        and not counts["unavailable_evidence"]
    )
    return _seal(
        {
            "version": value["version"],
            "plan_id": value["id"],
            "option_index": index,
            **deepcopy(option),
            "settings_id": settings_identity(settings),
            "ledger_id": fingerprint(report["ledger"]),
            "cohort_id": fingerprint(
                [
                    {
                        key: row.get(key)
                        for key in (
                            "strategy_id",
                            "symbol",
                            "signal_date",
                            "signal_timestamp",
                            "source_row",
                        )
                    }
                    for row in report["ledger"]
                ]
            ),
            "summary": deepcopy(report["summary"]),
            "counts": dict(sorted(counts.items())),
            **(
                {"whole_share_observations": whole_share_observations}
                if value["version"] == ATR_VERSION
                else {}
            ),
            "admitted_requests": counts["admitted_requests"],
            "clipped_requests": counts["constrained_requests"],
            "blockers": {
                key: count
                for key, count in counts.items()
                if key
                in (
                    "cash_shortfall",
                    "percentage_policy",
                    "position_limit",
                    "liquidity_limit",
                    "whole_share_affordability",
                    "unavailable_evidence",
                    "unexplained_execution_limit",
                )
            },
            "constraint_counts": dict(sorted(tags.items())),
            "verified_sufficient": complete,
            "performance_validated": False,
        }
    )


def _whole_share_observation(row, details, strategy, plan, index):
    if strategy is None or set(details.get("constraints", [])) & {
        "open_positions",
        "daily_volume_participation",
        "unavailable",
        "missing",
    }:
        return None
    price = plan.get("request_prices", {}).get(request_identity(row))
    risk, equity = details.get("initial_risk_per_share"), details.get("opening_equity")
    if any(
        type(value) not in (int, float) or not isfinite(value) or value <= 0
        for value in (price, risk, equity)
    ):
        return None
    return {
        "strategy_id": strategy["id"],
        "price": float(price),
        "risk_per_share": float(risk),
        "opening_equity_ratio": equity / plan["options"][index]["capital"],
    }


def _affordability_recovery(value, receipt, tried):
    observations = receipt.get("whole_share_observations", {})
    if not isinstance(observations, dict) or not observations:
        return None
    used = {settings_identity(selected_account_settings(value, index)) for index in tried}
    candidates = []
    for index, option in enumerate(value["options"]):
        if index in tried or option["capital"] <= receipt["capital"]:
            continue
        try:
            settings = selected_account_settings(value, index)
        except ValueError:
            continue
        if settings_identity(settings) in used:
            continue
        definitions = {row["id"]: row for row in settings}
        addresses = False
        for observation in observations.values():
            strategy = definitions[observation["strategy_id"]]
            cfg, policy = strategy["config"], strategy["execution_policy"]
            equity = option["capital"] * observation["opening_equity_ratio"]
            unit_cash = (
                observation["price"]
                * (1 + cfg.get("slippage_bps", 0) / 10000)
                * (1 + cfg.get("cost_bps", 0) / 10000)
            )
            cash = equity * strategy["allocation_pct"] / 100 * cfg["order_size_pct"] / 100
            target = policy["risk_limits"]["max_trade_risk_pct"] / 100
            if policy["sizing"] == "stop_risk":
                target *= policy["risk_budget_fraction"]
            # Native one-share limits use prospective equity after entry costs.
            risk = (equity - (unit_cash - observation["price"])) * min(
                target, policy["risk_limits"]["max_total_stop_risk_pct"] / 100
            )
            if cash >= unit_cash and risk >= observation["risk_per_share"]:
                addresses = True
                break
        if addresses:
            candidates.append((option["capital"], index))
    return min(candidates)[1] if candidates else None


def _selected_sequence(value, receipts):
    _validate_selected_plan(value)
    if not isinstance(receipts, list) or len(receipts) > value["max_accounts"]:
        raise ValueError("Selected account plan exceeded its frozen budget")
    next_index = 0
    tried = set()
    for receipt in receipts:
        if (
            next_index is None
            or not isinstance(receipt, dict)
            or receipt.get("id")
            != fingerprint({key: item for key, item in receipt.items() if key != "id"})
            or receipt.get("plan_id") != value["id"]
            or receipt.get("option_index") != next_index
            or value["version"]
            in (
                INTENDED_VERSION,
                CLOSED_LOOP_VERSION,
                FEASIBLE_VERSION,
                CAUSAL_VERSION,
                ATR_VERSION,
            )
            and receipt.get("version") != value["version"]
        ):
            raise ValueError("Selected account receipts differ from their routed sequence")
        if receipt.get("cohort_id") != receipts[0].get("cohort_id"):
            raise ValueError("Selected account variants changed the opportunity cohort")
        option = value["options"][next_index]
        if any(receipt.get(key) != item for key, item in option.items()) or receipt.get(
            "settings_id"
        ) != settings_identity(selected_account_settings(value, next_index)):
            raise ValueError("Selected account receipt settings changed")
        counts = receipt.get("counts", {})
        if not isinstance(counts, dict) or any(
            type(item) is not int or item < 0 for item in counts.values()
        ):
            raise ValueError("Selected account diagnosis counts changed")
        verified = (
            counts.get("admitted_requests", 0) > 0
            and not counts.get("constrained_requests", 0)
            and not counts.get("unavailable_evidence", 0)
        )
        if (
            receipt.get("verified_sufficient") != verified
            or receipt.get("performance_validated") is not False
        ):
            raise ValueError("Selected account sufficiency changed")
        tried.add(next_index)
        next_index = _selected_route(value, receipt, tried=tried)
    return next_index


def _selected_route(value, receipt, *, tried=()):
    index, counts = receipt["option_index"], receipt["counts"]
    if receipt["verified_sufficient"]:
        return None
    if value["version"] == ATR_VERSION:
        if counts.get("whole_share_affordability"):
            recovery = _affordability_recovery(value, receipt, tried)
            if recovery is not None:
                return recovery
        if counts.get("cash_shortfall") or counts.get("percentage_policy"):
            used = {settings_identity(selected_account_settings(value, i)) for i in tried}
            for target in range(max(2, index + 1), len(value["options"])):
                if target in tried:
                    continue
                try:
                    candidate = selected_account_settings(value, target)
                except ValueError:
                    continue
                if settings_identity(candidate) not in used:
                    return target
        return None
    # A mixed ledger may contain a permitted remedy for some requests and
    # immutable exclusions for others. Never discard the latter to claim success.
    if (
        counts.get("whole_share_affordability")
        and index == 0
        and value["options"][1]["capital"] > receipt["capital"]
    ):
        return 1
    if counts.get("cash_shortfall") or counts.get("percentage_policy"):
        target = max(2, index + 1)
        if target < len(value["options"]):
            # Never reduce a representable size below the native minimum.
            try:
                selected_account_settings(value, target)
            except ValueError:
                return None
            return target
    return None


def selected_account_next(value, receipts):
    return _selected_sequence(value, receipts)


def _closed_loop_rank(receipt):
    counts = receipt["counts"]
    gaps = counts.get("evidence_gap_requests", 0)
    return (
        not receipt["verified_sufficient"],
        bool(gaps),
        counts.get("constrained_requests", 0),
        gaps,
        -counts.get("admitted_requests", 0),
        receipt["capital"],
        receipt["option_index"],
    )


def selected_account_finish(value, receipts):
    next_index = _selected_sequence(value, receipts)
    if next_index is not None:
        raise ValueError("Selected account planning ended before its recorded action")
    last = receipts[-1] if receipts else None
    verified = bool(last and last["verified_sufficient"])
    feasible = next(
        (item for item in reversed(receipts) if item["counts"].get("admitted_requests")), None
    )
    counts = deepcopy(last["counts"]) if last else {}
    operating = min(
        (item for item in receipts if item["counts"].get("admitted_requests")),
        key=lambda item: (
            sum(
                item["counts"].get(key, 0)
                for key in ("cash_shortfall", "whole_share_affordability", "percentage_policy")
            ),
            -item["counts"].get("admitted_requests", 0),
            item["capital"],
            item["option_index"],
        ),
        default=None,
    )
    if value["version"] in (CLOSED_LOOP_VERSION, FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION):
        chosen = min(receipts, key=_closed_loop_rank, default=None)
        last = chosen
        verified = bool(chosen and chosen["verified_sufficient"])
        feasible = chosen if chosen and chosen["counts"].get("admitted_requests") else None
        operating = feasible
        counts = deepcopy(chosen["counts"]) if chosen else {}
    action = (
        "funding_verified"
        if verified
        else "bounded_actions_complete"
        if counts.get("cash_shortfall") or counts.get("percentage_policy")
        else "retain_limits_and_exclude_unavailable"
    )
    return _seal(
        {
            "version": value["version"],
            "plan_id": value["id"],
            "status": "verified_sufficient" if verified else "no_verified_amount",
            "estimated_capital": value["option_estimates"][last["option_index"]]
            if value["version"]
            in (CLOSED_LOOP_VERSION, FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
            and last
            else value["estimated_capital"],
            "original_capital": value["original_capital"],
            "recommended_capital": last["capital"] if verified else None,
            "verified_capital": last["capital"] if verified else None,
            "feasible_capital": feasible["capital"] if feasible else None,
            "recommended_settings": selected_account_settings(value, last["option_index"])
            if verified
            else None,
            "operating_point": {
                "capital": operating["capital"],
                "settings": selected_account_settings(value, operating["option_index"]),
                "option_index": operating["option_index"],
                "remaining_exclusions": {
                    key: count
                    for key, count in operating["counts"].items()
                    if key
                    not in ("admitted_requests", "eligible_requests", "fulfilled_risk_targets")
                    and count > 0
                },
                "performance_validated": False,
                "scope": "Capacity-tested historical operating point on the same opportunity cohort; unchanged performance and chronological gates are required before promotion.",
            }
            if operating
            else None,
            "accounts": deepcopy(receipts),
            "attempts": len(receipts),
            "tested_range": [
                min(item["capital"] for item in receipts),
                max(item["capital"] for item in receipts),
            ]
            if receipts
            else None,
            "representation_limited": value["option_estimates"][last["option_index"]]
            > MAX_NATIVE_CAPITAL
            if value["version"]
            in (CLOSED_LOOP_VERSION, FEASIBLE_VERSION, CAUSAL_VERSION, ATR_VERSION)
            and last
            else value["representation_limited"],
            "reference_tickets": [],
            "policy": selected_account_descriptor(value["version"]),
            "diagnosis": {
                "action": action,
                "stop_increases": True,
                "categories": counts,
                "constraint_counts": deepcopy(last["constraint_counts"]) if last else {},
                "reason": "The bounded native account actions are complete. Any unavailable evidence, fixed position/liquidity limits and remaining clipping stay visible; no limit was relaxed.",
                "cash_shortfall_requests": counts.get("cash_shortfall", 0),
                "noncash_blocker_requests": counts.get("constrained_requests", 0),
            },
            "independently_validated": False,
            "performance_validated": False,
            "scope": selected_account_descriptor(value["version"])["scope"],
        }
    )
