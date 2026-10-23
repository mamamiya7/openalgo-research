"""Bounded capital planning for fixed initial tickets; no account calculations or I/O."""

from collections import Counter
from copy import deepcopy
from math import ceil, isfinite

from research.execution_policy import normalize_limits
from research.report_contract import fingerprint, settings_identity

VERSION = "research-capital-planning-v1"
MAX_NATIVE_CAPITAL = 1_000_000_000


def _number(value, label, *, positive=True):
    if type(value) not in (int, float) or not isfinite(value) or (positive and value <= 0):
        raise ValueError(f"Capital planning needs finite {label}")
    return float(value)


def _seal(value):
    return {**value, "id": fingerprint(value)}


def descriptor():
    return _seal(
        {
            "version": VERSION,
            "max_accounts": 4,
            "multipliers": [1.0, 1.25, 1.5, 2.0],
            "rounding_rupees": 1000,
            "ticket_basis": "Original capital, strategy allocation and order size define fixed initial rupee tickets; later account equity compounds the ticket fraction.",
            "scope": "Separate capital-planning variant of frozen selected rules, not selected-setup replay, minimum capital or independent validation.",
            "risk": "All original hard account risk limits remain unchanged; fixed tickets may still be clipped or blocked.",
            "representation": "Only account amounts within the native 1 to 1,000,000,000 rupee capital range and preserving active original tickets within the 0.01% to 100% order-size range are tested; inactive zero allocations retain zero tickets, and tickets are never rounded or increased.",
        }
    )


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


def plan(baseline, selected, capital, risk_limits=None, *, max_accounts=4):
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
    candidates = [ceil(seed * factor / 1000) * 1000 for factor in descriptor()["multipliers"]]
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
            "version": VERSION,
            "policy_id": descriptor()["id"],
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
            continue
        if status == "pending" and not quantity:
            blockers["unavailable_entry_period"] += 1
            continue
        if quantity:
            admitted += 1
            if "requested_quantity" not in details:
                blockers["unavailable_execution_evidence"] += 1
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
    return _seal(
        {
            "version": VERSION,
            "plan_id": plan["id"],
            "capital": capital,
            "ledger_id": fingerprint(report["ledger"]),
            "summary": deepcopy(report["summary"]),
            "admitted_requests": admitted,
            "clipped_requests": clipped,
            "blockers": dict(sorted(blockers.items())),
            "verified_sufficient": admitted > 0 and not blockers,
        }
    )


def finish(plan, receipts):
    _validate_plan(plan)
    if not isinstance(receipts, list) or len(receipts) > len(plan["candidate_capitals"]):
        raise ValueError("Capital planning exceeded its frozen account budget")
    sufficient = None
    for index, receipt in enumerate(receipts):
        if (
            sufficient is not None
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
    return _seal(
        {
            "version": VERSION,
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
            "policy": descriptor(),
            "independently_validated": False,
            "scope": "Sufficient only for recorded eligible requests in this fixed-initial-ticket historical planning variant; not minimum capital or an exact replay of the selected setup.",
        }
    )
