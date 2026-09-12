"""Bounded presentation of exact saved reports; never evaluate or acquire data."""

from __future__ import annotations

import math

from research.analytics import MAX_CHART_POINTS
from research.evaluation_basis import comparison_status

VERSION = "research-comparison-presentation-v1"
MAX_MEMBERS = 4
MAX_METRICS = 512
SUMMARY_METRICS = (
    ("net_return_pct", "Return", "percent"),
    ("max_drawdown_pct", "Max drawdown", "percent"),
    ("win_rate_pct", "Win rate", "percent"),
    ("profit_factor", "Profit factor", "number"),
    ("closed_trades", "Closed trades", "number"),
    ("net_pnl", "Net P&L", "money"),
    ("initial_capital", "Starting capital", "money"),
)
FORMATS = {"percent", "money", "number", "text"}


def _number(value):
    try:
        return (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
    except OverflowError:
        return False


def _scalar(value, fmt):
    if fmt == "text":
        return value if isinstance(value, str) and len(value) <= 1024 else None
    return value if _number(value) else None


def _basis(member):
    context = member.get("report_context") or {}
    return context.get("evaluation_basis") or {}


def _currency(member):
    value = (_basis(member).get("comparison") or {}).get("currency")
    return (
        value
        if isinstance(value, str)
        and len(value) == 3
        and value.isascii()
        and value.isupper()
        and value.isalpha()
        else None
    )


def _catalog(member):
    analysis = member.get("analysis") or {}
    entries = analysis.get("catalog") or []
    if not isinstance(entries, list) or len(entries) > MAX_METRICS:
        raise ValueError("Saved comparison statistics exceed the supported limit")
    catalog = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("key"), str)
            or not 0 < len(entry["key"]) <= 256
            or entry.get("format") not in FORMATS
            or entry["key"] in catalog
            or any(
                not isinstance(entry.get(key, ""), str) or len(entry.get(key, "")) > limit
                for key, limit in (("label", 256), ("description", 4000), ("source", 512))
            )
        ):
            raise ValueError("Saved comparison statistics have an invalid definition")
        catalog[entry["key"]] = entry
    return catalog


def _metric(
    members, reference, key, label, fmt, source, description, comparable, currency, catalogs=None
):
    values, unavailable = {}, {}
    definition = catalogs[reference["id"]][key] if catalogs else None
    for member in members:
        identity = member["id"]
        evidence = member.get("analysis") or {} if source == "analysis" else member
        metrics = evidence.get("metrics" if source == "analysis" else "summary") or {}
        value = _scalar(metrics.get(key), fmt)
        reason = (evidence.get("unavailable") or {}).get(key)
        if catalogs and any(
            catalogs[identity][key].get(field, "") != definition.get(field, "")
            for field in ("format", "description", "source")
        ):
            value, reason = None, "This saved statistic uses a different definition."
        if fmt == "money" and currency is None:
            value, reason = None, "Currency is not recorded for every report."
        values[identity] = value
        if value is None:
            unavailable[identity] = (
                reason[:4000]
                if isinstance(reason, str) and reason
                else "Not available in this saved report."
            )
    deltas = None
    if comparable and fmt != "text":
        base = values[reference["id"]]
        deltas = {}
        for identity, value in values.items():
            delta = value - base if _number(value) and _number(base) else None
            deltas[identity] = delta if _number(delta) else None
    return {
        "key": key,
        "label": label,
        "format": fmt,
        "source": source,
        "description": description,
        "values": values,
        "deltas": deltas,
        "unavailable": unavailable,
    }


def _cumulative(members, compatible):
    result = {"id": "comparison-cumulative", "title": "Cumulative return", "status": "unavailable"}
    if not compatible:
        return {**result, "reason": "These reports do not have matching test conditions."}
    traces = []
    for member in members:
        chart = member.get("cumulative") or {}
        data = (chart.get("figure") or {}).get("data")
        if (
            chart.get("id") != "account-cumulative"
            or chart.get("status") != "available"
            or not isinstance(data, list)
            or len(data) != 1
        ):
            return {
                **result,
                "reason": "A saved return chart is unavailable for one or more reports.",
            }
        trace = data[0]
        if not isinstance(trace, dict):
            return {
                **result,
                "reason": "A saved return chart is unavailable for one or more reports.",
            }
        x, y = trace.get("x"), trace.get("y")
        if (
            not isinstance(x, list)
            or not isinstance(y, list)
            or not 0 < len(x) <= MAX_CHART_POINTS
            or len(x) != len(y)
            or any(not isinstance(at, str) or not 0 < len(at) <= 64 for at in x)
            or any(value is not None and not _number(value) for value in y)
            or not any(_number(value) for value in y)
        ):
            return {
                **result,
                "reason": "A saved return chart is unavailable for one or more reports.",
            }
        # These are already returns from original starting capital. In particular,
        # never rebase to the first sampled point or join two curves by row index.
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": member["name"],
                "x": list(x),
                "y": list(y),
                "connectgaps": False,
            }
        )
    return {
        **result,
        "status": "available",
        "figure": {
            "data": traces,
            "layout": {
                "autosize": True,
                "margin": {"l": 60, "r": 25, "t": 20, "b": 50},
                "hovermode": "closest",
                "showlegend": True,
                "yaxis": {"title": "%"},
            },
        },
    }


def build_comparison(members, reference_member_id):
    """Keep original values, gate differences, and copy retained return traces.

    Members arrive from validated pinned reports, reduced to scalar statistics
    and one bounded chart each. The caller freezes this output at admission.
    """
    if (
        not isinstance(members, list)
        or not 2 <= len(members) <= MAX_MEMBERS
        or any(
            not isinstance(m, dict)
            or not isinstance(m.get("id"), str)
            or not isinstance(m.get("name"), str)
            for m in members
        )
    ):
        raise ValueError("Choose two to four saved reports")
    ids = [member["id"] for member in members]
    if len(set(ids)) != len(ids) or reference_member_id not in ids:
        raise ValueError("Choose distinct reports and a reference from the selection")
    reference = next(member for member in members if member["id"] == reference_member_id)
    currencies = [_currency(member) for member in members]
    known = {value for value in currencies if value is not None}
    if len(known) > 1:
        raise ValueError("Choose reports in the same currency")
    currency = currencies[0] if all(currencies) else None
    differences = []
    for member in members:
        status = comparison_status(_basis(reference), _basis(member))
        codes = list(status["differences"])
        if currency is None and "currency" not in codes:
            codes.append("currency")
        if codes:
            differences.append({"member_id": member["id"], "codes": codes})
    compatible = not differences
    metrics = [
        _metric(
            members,
            reference,
            key,
            label,
            fmt,
            "summary",
            "Original saved summary.",
            compatible,
            currency,
        )
        for key, label, fmt in SUMMARY_METRICS
    ]
    catalogs = {member["id"]: _catalog(member) for member in members}
    versions = [(member.get("analysis") or {}).get("version") for member in members]
    same_version = bool(versions[0]) and all(version == versions[0] for version in versions)
    for key, entry in catalogs[reference_member_id].items():
        # Summary copies would repeat the same numbers and conceal the distinction
        # between original calculation and separately versioned analysis.
        if key in {"account_" + item[0] for item in SUMMARY_METRICS}:
            continue
        if not all(key in catalog for catalog in catalogs.values()):
            continue
        same_definition = all(
            all(
                catalog[key].get(field, "") == entry.get(field, "")
                for field in ("format", "description", "source")
            )
            for catalog in catalogs.values()
        )
        row = _metric(
            members,
            reference,
            key,
            entry.get("label") or key,
            entry["format"],
            "analysis",
            entry.get("description", ""),
            compatible and same_version and same_definition,
            currency,
            catalogs,
        )
        if compatible and not (same_version and same_definition):
            row["description"] += (
                " Saved analysis versions or definitions differ; no delta is calculated."
            )
        metrics.append(row)
    if len(metrics) > MAX_METRICS:
        raise ValueError("Saved comparison statistics exceed the supported limit")
    return {
        "version": VERSION,
        "currency": currency,
        "compatible": compatible,
        "differences": differences,
        "metrics": metrics,
        "cumulative": _cumulative(members, compatible),
    }
