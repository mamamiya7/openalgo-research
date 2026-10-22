"""Compact descriptive attribution after selection, using frozen research prices.

No downloader, account simulation, selection policy or retained cache lives here.
The existing market-condition classifier runs once per reported period.
"""

from research.market_conditions import build_conditions
from research.stock_market_context import market_view

VERSION = "research-market-suitability-v1"
AUTOMATIC_VERSION = "automatic-hypotheses-v16"
LIMITATIONS = (
    "Describes saved trades after costs; average trade returns are not filtered portfolio returns.",
    "Trend and volatility overlap; their trade counts must not be added together.",
    "The selected entry filters and portfolio limits affect which trades appear in each group.",
    "Final-period observations do not choose settings or a preferred market condition.",
)


def build_suitability(report, context):
    """Summarise existing attribution without changing any account or decision."""
    result = {"version": VERSION, "status": "unavailable", "limitations": list(LIMITATIONS)}
    if not isinstance(context, dict):
        return {**result, "reason": "Saved market context is unavailable for this run."}
    try:
        market = market_view(context)
    except (ValueError, KeyError, TypeError, OverflowError):
        return {**result, "reason": "Saved market context could not be verified for this run."}

    statuses = []
    validation = report.get("validation")
    for name, account in (
        ("development", report),
        ("final", validation.get("result") if isinstance(validation, dict) else None),
    ):
        if not isinstance(account, dict):
            continue
        try:
            conditions = build_conditions(account, market["evidence"], market["calendar"])
        except (ValueError, KeyError, TypeError, OverflowError):
            continue
        result[name] = {key: conditions[key] for key in ("dates", "coverage", "cohorts", "finding")}
        statuses.append(conditions["status"])

    if any(status != "unavailable" for status in statuses):
        result["status"] = (
            "available"
            if len(statuses) == 2 and all(s == "available" for s in statuses)
            else "partial"
        )
    if result["status"] == "unavailable":
        result["reason"] = (
            "Not enough classified market history is available for these saved trades."
        )
    elif "development" not in result or "final" not in result:
        result["reason"] = "Market-condition details are unavailable for one of the saved periods."
    return result


def enrich(result, evidence):
    """Add report-only context to current runs; leave older saved recipes alone."""
    findings = result.get("automatic_research")
    if not isinstance(findings, dict) or findings.get("version") != AUTOMATIC_VERSION:
        return
    findings["market_suitability"] = build_suitability(
        result, evidence.get("automatic_stock_market_context")
    )
