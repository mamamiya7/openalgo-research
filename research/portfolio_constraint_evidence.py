"""Compact, read-only reconciliation of native development constraint evidence.

The caller owns period admission. This module does not inspect performance,
select settings, consume final-period outcomes or establish statistical edge.
"""

import math
import re
from datetime import date
from numbers import Real

from research.execution_policy import CAPACITY_VERSION, normalize_policy
from research.portfolio import fingerprint
from research.portfolio_execution_features import (
    VERSION as FEATURE_VERSION,
)
from research.portfolio_execution_features import (
    validate_capacity_entry,
    validate_correlation_entry,
)
from research.stock_features import _json_size

VERSION = "research-portfolio-constraint-evidence-v1"
MAX_ROWS = 25_000
MAX_BYTES = 32 * 1024**2
MAX_NUMBER = 10**18


def _number(value, *, positive=False, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or not 0 <= value <= MAX_NUMBER
        or (positive and value <= 0)
        or (integer and int(value) != value)
        or (integer and value > 2**53 - 1)
    ):
        raise ValueError("constraint_numeric_evidence_invalid")
    return value


def _same(left, right):
    return math.isclose(_number(left), _number(right), rel_tol=1e-9, abs_tol=1e-7)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("constraint_entry_date_invalid")
    return value


def assess(report, features):
    """Reconcile one already admitted development account against frozen inputs.

    Unsupported/missing report structure or contradictory receipts are invalid.
    Explicit unavailable-volume/correlation exclusions are incomplete, even if
    the remaining account made money. A supported result proves only recorded
    constraint arithmetic and native fill reconciliation under the model.
    """
    counts = {"filled_entries": 0, "unavailable_entries": 0, "capacity_days": 0}
    feature_id = features.get("id") if isinstance(features, dict) else None
    modes, limits, report_id = {}, None, None

    def result(status, reasons):
        payload = {
            "version": VERSION,
            "status": status,
            "reasons": sorted(set(reasons)),
            **counts,
            "feature_id": feature_id if _digest(feature_id) else None,
            "report_evidence_id": report_id,
            "modes": modes,
            "risk_limits": limits,
            "scope": "caller_admitted_development_account",
            "qualification": "recorded_constraints_and_native_fills_only",
            "limitations": [
                "Not recommendation calibration, market edge or executable-liquidity proof."
            ],
        }
        return {**payload, "id": fingerprint(payload)}

    try:
        if not isinstance(report, dict) or not isinstance(features, dict):
            raise ValueError("constraint_report_or_features_missing")
        _json_size(features, 64 * 1024**2)
        if (
            features.get("version") != FEATURE_VERSION
            or not _digest(feature_id)
            or feature_id != fingerprint({k: v for k, v in features.items() if k != "id"})
        ):
            raise ValueError("constraint_feature_identity_invalid")
        ledger, strategies, execution = (
            report.get("ledger"),
            report.get("strategies"),
            report.get("execution"),
        )
        orders = report.get("engine_records", {}).get("orders")
        if (
            not isinstance(ledger, list)
            or len(ledger) > MAX_ROWS
            or not isinstance(strategies, list)
            or not 1 <= len(strategies) <= 20
            or not isinstance(execution, dict)
            or not isinstance(orders, list)
            or len(orders) > 2 * MAX_ROWS
        ):
            raise ValueError("constraint_report_structure_or_bound_invalid")
        subset = {
            "policy_version": report.get("policy_version"),
            "ledger": ledger,
            "strategies": strategies,
            "execution": execution,
            "orders": orders,
        }
        _json_size(subset, MAX_BYTES)
        if (
            report.get("policy_version")
            not in ("vectorbt-joint-causal-bars-v5", "vectorbt-joint-causal-bars-v6")
            or execution.get("engine") != "vectorbt"
            or execution.get("adapter_version")
            != (
                "vectorbt-portfolio-adapter-v6"
                if report.get("policy_version") == "vectorbt-joint-causal-bars-v6"
                else "vectorbt-portfolio-adapter-v5"
            )
            or execution.get("shared_cash") is not True
        ):
            raise ValueError("constraint_native_policy_invalid")
        by_strategy = {}
        for strategy in strategies:
            identity = strategy.get("id")
            if (
                not isinstance(identity, str)
                or not 1 <= len(identity) <= 100
                or identity in by_strategy
            ):
                raise ValueError("constraint_strategy_identity_invalid")
            policy = normalize_policy(strategy.get("execution_policy"))
            if policy != strategy.get("execution_policy") or policy["version"] != (
                "research-execution-aware-v5"
                if report.get("policy_version") == "vectorbt-joint-causal-bars-v6"
                else CAPACITY_VERSION
            ):
                raise ValueError("constraint_strategy_policy_invalid")
            current_modes = {key: policy[key] for key in ("capacity_mode", "correlation_mode")}
            if policy["version"] == "research-execution-aware-v5":
                current_modes["allocation_mode"] = policy["allocation_mode"]
            if modes and (modes != current_modes or limits != policy["risk_limits"]):
                raise ValueError("constraint_account_policy_mismatch")
            modes, limits = current_modes, policy["risk_limits"]
            by_strategy[identity] = strategy
        if execution.get("risk_limits") != limits or any(
            execution.get(key) != value for key, value in modes.items()
        ):
            raise ValueError("constraint_declared_limits_mismatch")
        allocation_on = modes.get("allocation_mode") == "prior60_cluster_inverse_vol"
        if (
            "allocation_mode" in modes
            and execution.get("allocation", {}).get("version") != "research-cluster-allocation-v1"
        ):
            raise ValueError("constraint_allocation_declaration_mismatch")
        capacity_on = modes["capacity_mode"] == "prior20_broker_reported"
        correlation_on = modes["correlation_mode"] == "prior60_positive_080"
        if capacity_on:
            declaration = execution.get("capacity", {})
            if (
                declaration.get("version") != "research-daily-share-capacity-v1"
                or declaration.get("max_daily_volume_participation_pct")
                != limits["max_daily_volume_participation_pct"]
            ):
                raise ValueError("constraint_capacity_declaration_mismatch")
        elif "capacity" in execution:
            raise ValueError("constraint_disabled_capacity_receipt")
        if correlation_on:
            declaration = execution.get("correlation", {})
            if (
                declaration.get("version") != "research-positive-correlation-cap-v1"
                or declaration.get("threshold") != 0.8
                or declaration.get("observations") != 60
                or declaration.get("max_correlated_exposure_pct")
                != limits["max_correlated_exposure_pct"]
            ):
                raise ValueError("constraint_correlation_declaration_mismatch")
        elif "correlation" in execution:
            raise ValueError("constraint_disabled_correlation_receipt")
        entered, columns = {}, set()
        for row in ledger:
            col = row.get("engine_column")
            _number(col, integer=True)
            if col in columns or col >= len(ledger):
                raise ValueError("constraint_duplicate_or_invalid_ledger_column")
            columns.add(col)
            if row.get("strategy_id") not in by_strategy:
                raise ValueError("constraint_ledger_strategy_missing")
            quantity = _number(row.get("quantity"), integer=True)
            reason = row.get("reason")
            if row.get("status") not in {"pending", "closed", "skipped", "excluded"}:
                raise ValueError("constraint_ledger_status_invalid")
            if isinstance(reason, str) and reason.startswith("Portfolio entry evidence"):
                raise ValueError("constraint_skipped_feature_identity_invalid")
            unavailable = isinstance(reason, str) and any(
                code in reason
                for code in (
                    "capacity_unavailable",
                    "correlation_unavailable",
                    "allocation_unavailable",
                )
            )
            if unavailable:
                counts["unavailable_entries"] += 1
                if quantity > 0:
                    raise ValueError("constraint_unavailable_entry_was_filled")
            if quantity == 0:
                if row.get("entry_date") is not None:
                    raise ValueError("constraint_zero_quantity_has_entry")
                continue
            if row["status"] in {"skipped", "excluded"}:
                raise ValueError("constraint_skipped_entry_was_filled")
            day = _day(row.get("entry_date"))
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not re.fullmatch(
                r"[A-Z0-9][A-Z0-9 &._-]{0,99}", symbol
            ):
                raise ValueError("constraint_entry_symbol_invalid")
            details = row.get("execution_details")
            if not isinstance(details, dict) or details.get("filled_quantity") != quantity:
                raise ValueError("constraint_filled_quantity_mismatch")
            _number(details["filled_quantity"], positive=True, integer=True)
            _number(details.get("requested_quantity"), positive=True, integer=True)
            if details["requested_quantity"] < quantity:
                raise ValueError("constraint_filled_above_request")
            _number(details.get("opening_equity"), positive=True)
            _number(row.get("entry_raw_price"), positive=True)
            _number(row.get("entry_price"), positive=True)
            entered[col] = (row, day, symbol, details)
        counts["filled_entries"] = len(entered)
        buys, order_ids = [], set()
        for order in orders:
            identity, col, side = order.get("id"), order.get("col"), order.get("side")
            _number(identity, integer=True)
            _number(col, integer=True)
            _number(order.get("idx"), integer=True)
            _number(order.get("size"), positive=True, integer=True)
            _number(order.get("price"), positive=True)
            _number(order.get("fees"))
            if (
                identity in order_ids
                or col not in columns
                or type(side) is not int
                or side not in (0, 1)
            ):
                raise ValueError("constraint_native_order_identity_invalid")
            order_ids.add(identity)
            if side == 0:
                buys.append(order)
        if len(buys) != len(entered) or {order["col"] for order in buys} != set(entered):
            raise ValueError("constraint_native_buys_do_not_match_entries")
        held_by_order, positions, allocation_before = {}, {}, {}
        audit_cash = (
            _number(report.get("config", {}).get("initial_capital"), positive=True)
            if allocation_on
            else None
        )
        for order in sorted(orders, key=lambda item: item["id"]):
            col = order["col"]
            if order["side"] == 0:
                held_by_order[order["id"]] = sorted({entered[held][2] for held in positions})
                if allocation_on:
                    row, day, symbol, _ = entered[col]
                    point = features.get("allocation", {}).get(day)
                    from research.portfolio_allocation import validate as validate_allocation

                    validate_allocation(
                        point,
                        symbol=symbol,
                        entry_session=day,
                        decision_at=row.get("entry_timestamp") or day + "T00:00:00+05:30",
                        required_symbols=held_by_order[order["id"]],
                    )
                    cluster = next(group for group in point["clusters"] if symbol in group)
                    marks = features.get("allocation_marks", {}).get(
                        row.get("entry_timestamp") or day, {}
                    )
                    if not _same(marks.get(symbol), row["entry_raw_price"]):
                        raise ValueError("constraint_allocation_entry_mark_mismatch")
                    values = [
                        (
                            entered[held][2],
                            size * _number(marks.get(entered[held][2]), positive=True),
                        )
                        for held, size in positions.items()
                    ]
                    allocation_before[order["id"]] = {
                        "equity": audit_cash + sum(value for _, value in values),
                        "symbol": sum(value for name, value in values if name == symbol),
                        "cluster": sum(value for name, value in values if name in cluster),
                    }
                if col in positions:
                    raise ValueError("constraint_native_duplicate_open_lot")
                positions[col] = order["size"]
                if allocation_on:
                    audit_cash -= order["size"] * order["price"] + order["fees"]
            else:
                if col not in positions or positions[col] != order["size"]:
                    raise ValueError("constraint_native_sell_does_not_match_open_lot")
                del positions[col]
                if allocation_on:
                    audit_cash += order["size"] * order["price"] - order["fees"]
        daily, bought_columns = {}, set()
        for order in sorted(buys, key=lambda item: item["id"]):
            col = order["col"]
            if col in bought_columns:
                raise ValueError("constraint_duplicate_native_buy")
            bought_columns.add(col)
            row, day, symbol, details = entered[col]
            quantity = row["quantity"]
            if order["size"] != quantity or not _same(order["price"], row["entry_price"]):
                raise ValueError("constraint_native_fill_mismatch")
            cfg = by_strategy[row["strategy_id"]]["config"]
            fee, slip = _number(cfg["cost_bps"]) / 10000, _number(cfg["slippage_bps"]) / 10000
            raw = row["entry_raw_price"]
            if not _same(raw * (1 + slip), order["price"]) or not _same(
                quantity * order["price"] * fee, order["fees"]
            ):
                raise ValueError("constraint_native_entry_cost_mismatch")
            key = symbol + "|" + day
            # Daily features must be complete on a prior session; midnight is a
            # conservative bound when the report has no exact entry clock.
            decision = row.get("entry_timestamp") or day + "T00:00:00+05:30"
            if capacity_on:
                saved = features.get("capacity", {}).get(key, {})
                feature = saved.get("feature")
                validate_capacity_entry(
                    feature, symbol=symbol, entry_session=day, decision_at=decision
                )
                assessment = saved.get("assessment")
                if assessment is not None and feature["source_id"] != assessment.get("id"):
                    raise ValueError("constraint_capacity_source_mismatch")
                if details.get("capacity_feature_id") != feature["id"] or not _same(
                    details.get("capacity_mean_daily_shares"), feature["mean_daily_shares"]
                ):
                    raise ValueError("constraint_capacity_feature_mismatch")
                budget = (
                    feature["mean_daily_shares"]
                    * limits["max_daily_volume_participation_pct"]
                    / 100
                )
                current = daily.setdefault(
                    (day, symbol),
                    {
                        "bought": 0,
                        "fills": 0,
                        "mean": feature["mean_daily_shares"],
                        "source": feature["source_id"],
                        "budget": budget,
                    },
                )
                if (
                    current["source"] != feature["source_id"]
                    or current["mean"] != feature["mean_daily_shares"]
                ):
                    raise ValueError("constraint_capacity_same_day_source_mismatch")
                remaining = max(0, budget - current["bought"])
                if (
                    not _same(details.get("daily_participation_share_budget"), budget)
                    or not _same(details.get("daily_bought_shares_before"), current["bought"])
                    or not _same(details.get("remaining_participation_shares"), remaining)
                    or quantity > math.floor(remaining + 1e-9)
                ):
                    raise ValueError("constraint_capacity_budget_or_quantity_invalid")
                current["bought"] += quantity
                current["fills"] += 1
            if correlation_on:
                feature = features.get("correlation", {}).get(key)
                validate_correlation_entry(
                    feature,
                    symbol=symbol,
                    entry_session=day,
                    decision_at=decision,
                    required_symbols=[symbol, *held_by_order[order["id"]]],
                )
                if details.get("correlation_feature_id") != feature["id"]:
                    raise ValueError("constraint_correlation_feature_mismatch")
                existing = _number(details.get("correlated_exposure_before"))
                fraction = limits["max_correlated_exposure_pct"] / 100
                friction = raw * (1 + slip) * (1 + fee) - raw
                expected = max(
                    0,
                    (details["opening_equity"] * fraction - existing) / (raw + fraction * friction),
                )
                observed = _number(details.get("correlated_capacity_shares"))
                if not _same(observed, expected) or quantity > math.floor(observed + 1e-9):
                    raise ValueError("constraint_correlated_budget_or_quantity_invalid")
            if allocation_on:
                from research.portfolio_allocation import validate as validate_allocation

                feature = validate_allocation(
                    features.get("allocation", {}).get(day),
                    symbol=symbol,
                    entry_session=day,
                    decision_at=decision,
                    required_symbols=held_by_order[order["id"]],
                )
                cluster = next(group for group in feature["clusters"] if symbol in group)
                weight = feature["weights"][symbol]
                if (
                    details.get("allocation_feature_id") != feature["id"]
                    or details.get("allocation_cluster") != cluster
                    or not _same(details.get("allocation_weight"), weight)
                ):
                    raise ValueError("constraint_allocation_feature_mismatch")
                observed_before = allocation_before[order["id"]]
                if (
                    not _same(details["opening_equity"], observed_before["equity"])
                    or not _same(details.get("symbol_before"), observed_before["symbol"])
                    or not _same(
                        details.get("allocation_cluster_before"), observed_before["cluster"]
                    )
                ):
                    raise ValueError("constraint_allocation_held_marks_mismatch")
                friction = raw * (1 + slip) * (1 + fee) - raw
                for name, fraction, existing in (
                    (
                        "symbol",
                        limits["max_gross_exposure_pct"] / 100 * weight,
                        _number(details.get("symbol_before")),
                    ),
                    (
                        "cluster",
                        limits["max_gross_exposure_pct"] / 100 / len(feature["clusters"]),
                        _number(details.get("allocation_cluster_before")),
                    ),
                ):
                    expected = max(
                        0,
                        (details["opening_equity"] * fraction - existing)
                        / (raw + fraction * friction),
                    )
                    observed = _number(details.get("allocation_" + name + "_capacity_shares"))
                    if not _same(expected, observed) or quantity > math.floor(observed + 1e-9):
                        raise ValueError("constraint_allocation_budget_or_quantity_invalid")
        if capacity_on:
            receipts = execution["capacity"].get("daily_symbols")
            if not isinstance(receipts, list) or len(receipts) > MAX_ROWS:
                raise ValueError("constraint_daily_capacity_receipts_invalid")
            seen = set()
            for receipt in receipts:
                key = (_day(receipt.get("date")), receipt.get("symbol"))
                if key in seen or key not in daily:
                    raise ValueError("constraint_duplicate_or_unknown_daily_capacity")
                seen.add(key)
                current = daily[key]
                if (
                    receipt.get("source_id") != current["source"]
                    or not _same(receipt.get("mean_daily_shares"), current["mean"])
                    or not _same(receipt.get("share_budget"), current["budget"])
                    or not _same(receipt.get("bought_shares"), current["bought"])
                    or not _same(
                        receipt.get("remaining_shares"),
                        max(0, current["budget"] - current["bought"]),
                    )
                    or type(receipt.get("buy_fills")) is not int
                    or receipt["buy_fills"] != current["fills"]
                ):
                    raise ValueError("constraint_daily_capacity_reconciliation_failed")
            if seen != set(daily):
                raise ValueError("constraint_daily_capacity_receipt_missing")
        counts["capacity_days"] = len(daily)
        report_id = fingerprint(subset)
        return result(
            "incomplete" if counts["unavailable_entries"] else "supported",
            ["constraint_observations_unavailable"] if counts["unavailable_entries"] else [],
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        AttributeError,
        RecursionError,
        OSError,
    ) as error:
        reason = (
            str(error)
            if isinstance(error, ValueError) and str(error).startswith("constraint_")
            else "constraint_evidence_malformed"
        )
        return result("invalid", [reason])
