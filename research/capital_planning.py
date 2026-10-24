"""Bounded capital planning for fixed initial tickets; no account calculations or I/O."""

from collections import Counter
from copy import deepcopy
from math import ceil, isfinite

from research.execution_policy import normalize_limits
from research.report_contract import fingerprint, settings_identity

VERSION = "research-capital-planning-v1"
DECISION_VERSION = "research-capital-planning-v2"
MAX_NATIVE_CAPITAL = 1_000_000_000
SELECTED_VERSION = "research-capital-planning-v3"


def _number(value, label, *, positive=True):
    if type(value) not in (int, float) or not isfinite(value) or (positive and value <= 0):
        raise ValueError(f"Capital planning needs finite {label}")
    return float(value)


def _seal(value):
    return {**value, "id": fingerprint(value)}


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


def selected_account_descriptor():
    """A separate capacity experiment, never a replacement for winner validation."""
    return _seal(
        {
            "version": SELECTED_VERSION,
            "max_accounts": 4,
            "size_multipliers": [1.0, 0.5, 0.25],
            "cash_buffer_fraction": 0.01,
            "rounding_rupees": 1000,
            "capital_basis": "Selected percentage sizing and fee/slippage-adjusted historical entry prices, never original fixed rupee tickets or profit.",
            "routing": "Whole-share affordability tests the frozen larger account; shared cash or percentage risk clipping tests smaller frozen cash and risk targets. Missing evidence and position/liquidity exclusions remain recorded and never relax hard limits.",
            "operating_point": "On the identical recorded opportunity cohort, minimize cash-shortfall plus whole-share-affordability plus percentage-policy constrained requests; then maximize admitted requests, prefer less capital and the earlier frozen option. Ignore profit. A tested operating point with exclusions is not verified complete funding or performance promotion.",
            "scope": "Bounded historical capacity variants. A feasible account is not a minimum, performance recommendation or independent validation; any changed settings require unchanged performance and chronological validation before promotion.",
        }
    )


def selected_account_plan(selected, capital, entry_prices, *, max_accounts=4):
    """Freeze all account/size variants before reading native capacity outcomes.

    ``entry_prices`` maps strategy IDs to raw eligible historical entry prices.
    These prices describe planning capacity only, not a tradable hindsight sizing
    policy. ATR stops cannot be inferred from prices; native execution remains the
    authority for their risk capacity.
    """
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
            "version": SELECTED_VERSION,
            "policy_id": selected_account_descriptor()["id"],
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
    ):
        raise ValueError("The selected account plan differs from its declared policy")


def selected_account_settings(value, index):
    _validate_selected_plan(value)
    if type(index) is not int or not 0 <= index < len(value["options"]):
        raise ValueError("Selected account option was not frozen")
    option = value["options"][index]
    result = deepcopy(value["selected"])
    for row in result:
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


def selected_account_receipt(report, value, index):
    _validate_selected_plan(value)
    settings = selected_account_settings(value, index)
    option = value["options"][index]
    if _number(report["summary"]["initial_capital"], "native initial capital") != option["capital"]:
        raise ValueError("Native selected capital differs from its frozen option")
    # Orchestration independently verifies full normalized settings and native
    # constraint evidence; preserve exact ledger/summary identities here too.
    counts, tags = Counter(), Counter()
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
    for row in report["ledger"]:
        if row.get("strategy_id") is not None and row["strategy_id"] not in active:
            counts["inactive_strategy"] += 1
            continue
        status, reason = row.get("status"), str(row.get("reason") or "").lower()
        if status not in ("closed", "open", "pending", "excluded", "skipped"):
            raise ValueError("Selected account request status is unavailable")
        if status == "excluded":
            counts[
                "rule_or_window_exclusion"
                if "not met:" in reason or reason.startswith("maximum holding window crosses")
                else "unavailable_evidence"
            ] += 1
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
            continue
        counts["eligible_requests"] += 1
        if quantity:
            counts["admitted_requests"] += 1
        if quantity and "requested_quantity" not in details:
            counts["unavailable_evidence"] += 1
        if status == "skipped" or quantity < requested:
            counts["constrained_requests"] += 1
            causes = set(constraints)
            tags.update(causes)
            if "shared_cash" in causes:
                counts["cash_shortfall"] += 1
            if causes & policy_tags:
                counts["percentage_policy"] += 1
            if "open_positions" in causes:
                counts["position_limit"] += 1
            if "daily_volume_participation" in causes:
                counts["liquidity_limit"] += 1
            if not requested and not causes:
                counts["whole_share_affordability"] += 1
            if "unavailable" in reason or "missing" in reason:
                counts["unavailable_evidence"] += 1
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
    complete = (
        counts["admitted_requests"] > 0
        and not counts["constrained_requests"]
        and not counts["unavailable_evidence"]
    )
    return _seal(
        {
            "version": SELECTED_VERSION,
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


def _selected_sequence(value, receipts):
    _validate_selected_plan(value)
    if not isinstance(receipts, list) or len(receipts) > value["max_accounts"]:
        raise ValueError("Selected account plan exceeded its frozen budget")
    next_index = 0
    for receipt in receipts:
        if (
            next_index is None
            or not isinstance(receipt, dict)
            or receipt.get("id")
            != fingerprint({key: item for key, item in receipt.items() if key != "id"})
            or receipt.get("plan_id") != value["id"]
            or receipt.get("option_index") != next_index
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
        next_index = _selected_route(value, receipt)
    return next_index


def _selected_route(value, receipt):
    index, counts = receipt["option_index"], receipt["counts"]
    if receipt["verified_sufficient"]:
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
    action = (
        "funding_verified"
        if verified
        else "bounded_actions_complete"
        if counts.get("cash_shortfall") or counts.get("percentage_policy")
        else "retain_limits_and_exclude_unavailable"
    )
    return _seal(
        {
            "version": SELECTED_VERSION,
            "plan_id": value["id"],
            "status": "verified_sufficient" if verified else "no_verified_amount",
            "estimated_capital": value["estimated_capital"],
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
                    if key not in ("admitted_requests", "eligible_requests") and count > 0
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
            "representation_limited": value["representation_limited"],
            "reference_tickets": [],
            "policy": selected_account_descriptor(),
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
            "scope": selected_account_descriptor()["scope"],
        }
    )
