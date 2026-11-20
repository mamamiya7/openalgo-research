"""Observed execution fingerprints, never certificates of validation equivalence.

Normal fills cannot prove that costs, risk audits or neighboring settings match.
These pure projections only find candidates for a separately verified failed-gate
witness; the original native acceptance programme remains the sole validator.
"""

from research.report_contract import fingerprint

VERSION = "research-economic-observation-v1"
ALLOCATION_ANNOTATIONS = frozenset(
    (
        "allocation_active_symbols",
        "allocation_cluster",
        "allocation_cluster_before",
        "allocation_cluster_capacity_shares",
        "allocation_cluster_count",
        "allocation_feature_id",
        "allocation_opening_equity",
        "allocation_symbol_capacity_shares",
        "allocation_weight",
    )
)


def observed(report):
    """Strict same-account financial observation, excluding named bookkeeping.

    The total stop-risk-before value can differ with changed exit controls even
    when realized fills match. It is deliberately excluded here only because this
    fingerprint NEVER authorizes acceptance or rejects a candidate by itself.
    """
    keys = (
        "summary",
        "equity_curve",
        "ledger",
        "per_strategy",
        "engine_records",
        "coverage",
        "missing_marks",
        "evaluation_basis",
        "research_opportunity_basis",
        "automatic_stock_condition",
    )
    if not isinstance(report, dict) or any(key not in report for key in keys):
        return None
    if not isinstance(report["ledger"], list) or not report["ledger"]:
        return None
    condition = report["automatic_stock_condition"]
    if not isinstance(condition, dict) or "hypothesis" not in condition:
        return None
    value = {key: report[key] for key in keys if key != "ledger"}
    value["automatic_stock_condition"] = {k: v for k, v in condition.items() if k != "gate_id"}
    ledger = []
    for row in report["ledger"]:
        if not isinstance(row, dict):
            return None
        item = dict(row)
        if isinstance(item.get("execution_details"), dict):
            item["execution_details"] = {
                k: v
                for k, v in item["execution_details"].items()
                if k not in ALLOCATION_ANNOTATIONS and k != "total_stop_risk_before"
            }
        ledger.append(item)
    value["ledger"] = ledger
    return fingerprint(value)


def trade_observation(report):
    """Exact realized fill/cost observation; capital/risk context stays separate.

    PnL is omitted because the recorded quantities, prices and fees define these
    cash flows while accumulation can introduce insignificant floating differences.
    No rounding is used and this cannot certify risk or scenario equivalence.
    """
    fields = (
        "source_row",
        "strategy_id",
        "symbol",
        "side",
        "status",
        "outcome",
        "signal_date",
        "signal_timestamp",
        "entry_date",
        "exit_date",
        "exit_timing",
        "entry_price",
        "entry_raw_price",
        "exit_price",
        "exit_raw_price",
        "quantity",
        "fees",
    )
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("ledger"), list)
        or not report["ledger"]
    ):
        return None
    if any(not isinstance(row, dict) for row in report["ledger"]):
        return None
    return fingerprint([{key: row.get(key) for key in fields} for row in report["ledger"]])
