"""Prespecified symbol/date challenges using actual development account replays.

This module never manufactures subset returns from a full-account curve. The
caller executes each masked account with fresh capital and normal costs, using
the ordinary maximum-horizon purge, and retains its verified development shard.
The caller also verifies native constraint audits and support counts against the
actual saved report. Shards do not contain native orders or closed-trade ledgers,
so this contract cannot independently reconstruct those two report facts.

Passing these four deterministic challenges is descriptive robustness evidence,
not calibrated confidence, independent replication or adaptive-search correction.
"""

from __future__ import annotations

from datetime import date

from research.recommendation_evidence import _pair
from research.report_contract import fingerprint

VERSION = "research-recommendation-sensitivity-v1"
NAMES = ("sens_symbols_a", "sens_symbols_b", "sens_dates_early", "sens_dates_late")
MIN_SESSIONS = 20
MIN_CLOSED_TRADES = 5
MIN_ENTRY_DATES = 5
MAX_SESSIONS = 4096
MAX_SIGNALS = 25_000
MAX_SYMBOLS = 200
MAX_ACCOUNTS = 8
LIMITATION = (
    "Four prespecified development-only masked account challenges; not independent "
    "samples, search-adjusted confidence, a probability of future profit or exhaustive "
    "date, symbol, seed or regime stability."
)


def _seal(value):
    return {**value, "id": fingerprint(value)}


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Sensitivity needs canonical recorded session dates")
    return value


def _observations(evidence, first, last):
    snapshot = evidence["snapshot"]
    interval = snapshot["provenance"]["interval"]
    if interval not in ("D", "1m"):
        raise ValueError("Sensitivity interval is unsupported")
    timeline = snapshot["timeline" if interval == "1m" else "sessions"]
    if not isinstance(timeline, list) or len(timeline) > 100_000:
        raise ValueError("Sensitivity observation limit exceeded")
    return interval, [key for key in timeline if first <= key[:10] <= last]


def _sources(evidence, first, last, symbols=None):
    # Timing-union membership is the source actually used by slice_period.
    rows = evidence.get("timing_cohort", {}).get("strategies", evidence["strategies"])
    sources, count = [], 0
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 8
        or any(not isinstance(row.get("signals"), list) for row in rows)
        or sum(len(row["signals"]) for row in rows) > MAX_SIGNALS
    ):
        raise ValueError("Sensitivity source strategy or signal limit exceeded")
    for row in rows:
        signals = []
        for signal in row["signals"]:
            if first <= signal["date"] <= last and (symbols is None or signal["symbol"] in symbols):
                count += 1
                if count > MAX_SIGNALS:
                    raise ValueError("Sensitivity signal limit exceeded")
                signals.append(
                    {
                        key: signal[key]
                        for key in ("symbol", "date", "timestamp", "row")
                        if key in signal
                    }
                )
        sources.append({"strategy_id": row["id"], "signals": signals})
    return sources


def build_plan(evidence, search_period):
    """Freeze four masks before looking at prices, account marks or outcomes.

    ``search_period`` is the already compiled final-development search period.
    Only its bounds, original signal identities and recorded observation calendar
    enter the plan. Hash-ranked symbols are alternated into complementary groups;
    calendar halves are chronological. Missing breadth is explicitly insufficient.
    """
    first, last = _day(search_period["from"]), _day(search_period["to"])
    if first > last:
        raise ValueError("Sensitivity search bounds are reversed")
    sessions = [day for day in evidence["snapshot"]["sessions"] if first <= day <= last]
    if not sessions or len(sessions) > MAX_SESSIONS or sessions != sorted(set(sessions)):
        raise ValueError("Sensitivity recorded sessions are missing, reordered or excessive")
    for day in sessions:
        _day(day)
    original = _sources(evidence, first, last)
    symbols = sorted({signal["symbol"] for row in original for signal in row["signals"]})
    if any(not isinstance(symbol, str) or not symbol or len(symbol) > 128 for symbol in symbols):
        raise ValueError("Sensitivity original symbol identity is invalid")
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError("Sensitivity symbol limit exceeded")
    ranked = sorted(symbols, key=lambda symbol: (fingerprint({"symbol": symbol}), symbol))
    midpoint = len(sessions) // 2
    designs = (
        (NAMES[0], "symbols", sessions, sorted(ranked[::2])),
        (NAMES[1], "symbols", sessions, sorted(ranked[1::2])),
        (NAMES[2], "dates", sessions[:midpoint], symbols),
        (NAMES[3], "dates", sessions[midpoint:], symbols),
    )
    scenarios, reasons = [], []
    for name, axis, days, selected in designs:
        missing = []
        if axis == "symbols" and len(symbols) < 2:
            missing.append("At least two original symbols are needed for complementary groups")
        if len(days) < MIN_SESSIONS:
            missing.append(f"At least {MIN_SESSIONS} recorded sessions are needed per challenge")
        start, stop = (days[0], days[-1]) if days else (first, first)
        source = _sources(evidence, start, stop, set(selected))
        if not any(row["signals"] for row in source):
            missing.append("This prespecified challenge has no original scanner opportunities")
        interval, timeline = _observations(evidence, start, stop)
        if sorted({key[:10] for key in timeline}) != days:
            missing.append(
                "Recorded account observations do not cover every planned session; absent daily returns are not invented"
            )
        scenarios.append(
            _seal(
                {
                    "name": name,
                    "axis": axis,
                    "from": start,
                    "to": stop,
                    "sessions": len(days),
                    "symbols": selected,
                    "source_id": fingerprint(source),
                    "observations_id": fingerprint({"interval": interval, "timeline": timeline}),
                    "status": "unavailable" if missing else "available",
                    "reasons": missing,
                }
            )
        )
        reasons.extend(f"{name}: {reason}" for reason in missing)
    interval, timeline = _observations(evidence, first, last)
    return _seal(
        {
            "version": VERSION,
            "status": "unavailable" if reasons else "available",
            "reasons": reasons,
            "search": {
                "from": first,
                "to": last,
                "sessions": len(sessions),
                "symbols": symbols,
                "source_id": fingerprint(original),
                "observations_id": fingerprint({"interval": interval, "timeline": timeline}),
            },
            "scenarios": scenarios,
            "maximum_accounts": MAX_ACCOUNTS,
            "policy": {
                "symbol_partition": "sha256-symbol-order-alternating-complementary-halves",
                "date_partition": "chronological-recorded-session-halves",
                "purge": "existing-full-maximum-horizon-and-timing-union-purge",
                "account": "fresh-original-capital-same-allocations-limits-and-normal-costs",
                "minimum_sessions": MIN_SESSIONS,
                "minimum_closed_trades": MIN_CLOSED_TRADES,
                "minimum_entry_dates": MIN_ENTRY_DATES,
                "selection": "Require all four supported positive return-minus-drawdown improvements; otherwise retain the unchanged reference",
                "final": "All final-period observations and outcomes excluded",
            },
            "limitations": [LIMITATION],
        }
    )


def _verified_plan(plan):
    if (
        not isinstance(plan, dict)
        or plan.get("version") != VERSION
        or plan.get("id") != fingerprint({key: value for key, value in plan.items() if key != "id"})
        or plan.get("maximum_accounts") != MAX_ACCOUNTS
    ):
        raise ValueError("Sensitivity plan identity is missing or changed")
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, list) or [row.get("name") for row in scenarios] != list(NAMES):
        raise ValueError("Sensitivity requires the exact four prespecified scenarios")
    for row in scenarios:
        if row.get("id") != fingerprint({key: value for key, value in row.items() if key != "id"}):
            raise ValueError("Sensitivity scenario identity changed")
        if not plan["search"]["from"] <= row["from"] <= row["to"] <= plan["search"]["to"]:
            raise ValueError("Sensitivity scenario is outside final-development search")
    search = plan["search"]
    symbols = search["symbols"]
    if (
        not isinstance(symbols, list)
        or symbols != sorted(set(symbols))
        or len(symbols) > MAX_SYMBOLS
    ):
        raise ValueError("Sensitivity original symbol declaration is invalid")
    ranked = sorted(symbols, key=lambda symbol: (fingerprint({"symbol": symbol}), symbol))
    for index, row in enumerate(scenarios[:2]):
        if (
            row["axis"] != "symbols"
            or row["symbols"] != sorted(ranked[index::2])
            or any(row[key] != search[key] for key in ("from", "to", "sessions", "observations_id"))
        ):
            raise ValueError("Sensitivity complementary symbol design changed")
    early, late = scenarios[2:]
    if (
        any(row["axis"] != "dates" or row["symbols"] != symbols for row in (early, late))
        or early["from"] != search["from"]
        or late["to"] != search["to"]
        or early["sessions"] != search["sessions"] // 2
        or late["sessions"] != search["sessions"] - search["sessions"] // 2
        or (early["sessions"] and early["to"] >= late["from"])
    ):
        raise ValueError("Sensitivity chronological half design changed")
    return scenarios


def _support(value, scenario):
    if not isinstance(value, dict) or set(value) != {"closed_trades", "entry_dates"}:
        raise ValueError("Sensitivity needs caller-verified native support counts")
    if any(type(value[key]) is not int or not 0 <= value[key] <= MAX_SIGNALS for key in value):
        raise ValueError("Sensitivity native support counts are invalid")
    if value["entry_dates"] > scenario["sessions"] or value["entry_dates"] > value["closed_trades"]:
        raise ValueError("Sensitivity native entry-date support is inconsistent")
    return value["closed_trades"] >= MIN_CLOSED_TRADES and value["entry_dates"] >= MIN_ENTRY_DATES


def _constraints(value, shard):
    feature_id = (
        shard["evaluation_basis"]["comparison"]["execution"].get("portfolio_features", {}).get("id")
    )
    if (
        not isinstance(value, dict)
        or value.get("version") != "research-portfolio-constraint-evidence-v1"
        or value.get("id") != fingerprint({key: item for key, item in value.items() if key != "id"})
        or not feature_id
        or value.get("feature_id") != feature_id
        or value.get("scope") != "caller_admitted_development_account"
        or value.get("qualification") != "recorded_constraints_and_native_fills_only"
    ):
        raise ValueError("Sensitivity native constraint audit identity is incompatible")
    return value.get("status") == "supported" and value.get("unavailable_entries") == 0


def assess(plan, pairs=None):
    """Assess four actual masked account pairs, never full-curve approximations.

    Each pair names ``scenario`` and supplies ``candidate``/``baseline`` verified
    ordinary development shards, ``candidate_support``/``baseline_support`` native
    closed-trade and entry-date counts and corresponding ``*_constraints`` audits.
    Support/report audit binding must already be verified by the owning controller.
    No recommendation is made from a missing or unsupported challenge.
    """
    result = {
        "version": VERSION,
        "plan_id": plan.get("id") if isinstance(plan, dict) else None,
        "status": "unsupported",
        "qualified": False,
        "reasons": [],
        "scenarios": [],
        "limitations": [
            LIMITATION,
            "Native support and audit-to-report binding are verified by the owning calculation controller, not reconstructed from account curves.",
        ],
    }
    try:
        scenarios = _verified_plan(plan)
        if plan.get("status") != "available" or any(
            row["status"] != "available" for row in scenarios
        ):
            return _seal({**result, "status": "insufficient", "reasons": list(plan["reasons"])})
        if pairs is None:
            return _seal(
                {
                    **result,
                    "status": "not_run",
                    "reasons": ["Earlier development checks did not support a changed setup"],
                }
            )
        if not isinstance(pairs, list) or len(pairs) != len(NAMES):
            raise ValueError("Sensitivity needs all four actual development account pairs")
        if len({row.get("scenario") for row in pairs}) != len(NAMES):
            raise ValueError("Sensitivity challenge was duplicated")
        indexed = {row["scenario"]: row for row in pairs}
        settings, hypotheses, frozen, frozen_binding, feature_ids = {}, {}, None, None, set()
        insufficient, failed = [], []
        for scenario in scenarios:
            name = scenario["name"]
            item = indexed[name]
            pair = _pair(item["candidate"], item["baseline"], period=name)
            support_ok = True
            for role in ("candidate", "baseline"):
                shard = item[role]
                basis = shard["evaluation_basis"]
                if (
                    basis["source_id"] != scenario["source_id"]
                    or basis["observations_id"] != scenario["observations_id"]
                ):
                    raise ValueError(
                        "Sensitivity actual source or observations differ from the prespecified mask"
                    )
                if (
                    pair["from"] != scenario["from"]
                    or pair["to"] != scenario["to"]
                    or pair["days"] != scenario["sessions"]
                ):
                    raise ValueError(
                        "Sensitivity actual account dates differ from the prespecified challenge"
                    )
                opportunity = shard.get("research_opportunity_basis")
                if opportunity is not None and opportunity["source_id"] != scenario["source_id"]:
                    raise ValueError("Sensitivity original opportunity mask changed")
                facts = shard.get("sensitivity_evidence")
                if facts is not None and (
                    facts.get("plan_id") != plan["id"]
                    or facts.get("scenario_id") != scenario["id"]
                    or facts.get("support") != item[f"{role}_support"]
                    or facts.get("constraints") != item[f"{role}_constraints"]
                ):
                    raise ValueError(
                        "Sensitivity supplied native facts differ from retained account evidence"
                    )
                if role in settings and (
                    settings[role] != shard["config_id"] or hypotheses[role] != shard["hypothesis"]
                ):
                    raise ValueError(
                        "Sensitivity settings or hypothesis changed between challenges"
                    )
                settings[role], hypotheses[role] = shard["config_id"], shard["hypothesis"]
                if frozen_binding is not None and frozen_binding != shard["binding"]:
                    raise ValueError("Sensitivity frozen study inputs changed between challenges")
                frozen_binding = shard["binding"]
                comparison = dict(basis["comparison"])
                comparison.pop("id", None)
                comparison.pop("period", None)
                # Declared candidate gates may differ from the unchanged reference.
                execution = dict(comparison["execution"])
                execution.pop("entry_conditions", None)
                comparison["execution"] = execution
                if frozen is not None and frozen != comparison:
                    raise ValueError(
                        "Sensitivity assumptions, costs or limits changed between challenges"
                    )
                frozen = comparison
                feature_ids.add(execution.get("portfolio_features", {}).get("id"))
                support_ok = _support(item[f"{role}_support"], scenario) and support_ok
                support_ok = _constraints(item[f"{role}_constraints"], shard) and support_ok
            public = {key: value for key, value in pair.items() if not key.startswith("_")}
            result["scenarios"].append(
                {
                    "name": name,
                    "scenario_id": scenario["id"],
                    "axis": scenario["axis"],
                    "symbols": list(scenario["symbols"]),
                    **public,
                    "passes": pair["passes"] and support_ok,
                    "support_sufficient": support_ok,
                    "candidate_evidence_id": item["candidate"]["id"],
                    "baseline_evidence_id": item["baseline"]["id"],
                }
            )
            if not support_ok or pair["days"] < MIN_SESSIONS:
                insufficient.append(
                    f"{name}: insufficient native trade/date support or constraint evidence"
                )
            if not pair["passes"]:
                failed.append(
                    f"{name}: positive return/risk improvement over the unchanged reference was not supported"
                )
        if len(feature_ids) != 1:
            raise ValueError("Sensitivity accounts used different frozen portfolio features")
        reasons = insufficient + failed
        return _seal(
            {
                **result,
                "status": "insufficient"
                if insufficient
                else "not_supported"
                if failed
                else "supported",
                "qualified": not reasons,
                "reasons": reasons,
            }
        )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
        return _seal({**result, "reasons": [str(error)]})
