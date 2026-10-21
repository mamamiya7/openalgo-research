"""Frozen native clock states, fixed opportunity purging and bounded probes.

No outcomes define these choices. Daily observations never request minute data.
The original reference remains exact; only registered clock changes are applied.
"""

from copy import deepcopy

from research.portfolio import fingerprint
from research.report_contract import settings_identity

VERSION = "automatic-timing-states-v1"


def descriptor(evidence, *, strict=False):
    from research.timing_search import (
        COHORT_VERSION,
        UNION_VERSION,
        _intact,
        freeze_cohort,
        propose,
    )
    from research.timing_search import VERSION as CHOICES_VERSION

    interval = evidence["snapshot"]["provenance"]["interval"]
    if strict:
        from research.portfolio import interval_for

        if interval != interval_for(evidence["portfolio"], evidence["strategies"]):
            raise ValueError("Automatic timing prices must match the source-required interval")
    source = evidence.get("timing_descriptor")
    plan, cohort = evidence.get("timing_union"), evidence.get("timing_cohort")
    if source is not None:
        _intact(source, CHOICES_VERSION)
        if source != propose(evidence["portfolio"], evidence["strategies"], evidence["snapshot"]):
            raise ValueError("Frozen timing choices differ from native source inputs")
        if source["status"] not in ("ready", "not_applicable"):
            raise ValueError("Automatic timing needs complete recorded native clock windows")
    elif interval == "1m" and strict:
        raise ValueError("Automatic timing needs its frozen native timing descriptor")
    active = bool(source and source["status"] == "ready")
    if active:
        if plan is None:
            raise ValueError("Automatic timing needs its shared native union")
        _intact(plan, UNION_VERSION)
        if plan["status"] != "ready" or plan["descriptor_id"] != source["id"]:
            raise ValueError("Automatic timing needs its exact ready native union")
        if cohort is not None:
            _intact(cohort, COHORT_VERSION)
            if cohort["plan_id"] != plan["id"] or cohort["snapshot_id"] != fingerprint(
                evidence["snapshot"]
            ):
                raise ValueError("Automatic timing cohort differs from frozen prices")
        if strict and (cohort is None or cohort != freeze_cohort(evidence, source, plan)):
            raise ValueError("Automatic timing needs its exact shared price-complete cohort")
        if strict and not cohort["counts"]["eligible"]:
            raise ValueError("No shared price-complete timing opportunities remain")
    definitions = {row["id"]: row["branches"] for row in source["strategies"]} if source else {}
    return {
        "version": VERSION,
        "interval": interval,
        "descriptor_id": source["id"] if source else None,
        "union_id": plan["id"] if active else None,
        "cohort_id": cohort["id"] if active and cohort else None,
        "strategies": [
            {
                "id": row["id"],
                "branches": deepcopy(
                    definitions.get(
                        row["id"], [{"id": "original", "label": "Original timing", "changes": {}}]
                    )
                ),
                "original": {
                    key: row["config"][key]
                    for key in ("entry_time", "exit_time")
                    if key in row["config"]
                },
            }
            for row in evidence["strategies"]
        ],
        "policy": "same source opportunities; all scheduled branch deadlines purged together; missing evidence excluded across branches; rule filters remain branch-specific",
        "screening": "Per expanding fit: 32 original-clock family screens; 4 momentum probes; up to 4 clock probes; 4 ATR exit and 4 portfolio probes; 2 global entry-priority probes; at least 18 joint refinement proposals within 68 total"
        if evidence["portfolio"].get("automatic_research", {}).get("version")
        in (
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
        )
        else "Per expanding fit: 32 original-clock family screens; 4 momentum probes; up to 4 clock probes; 4 ATR exit and 4 portfolio probes; at least 16 joint refinement proposals within 64 total"
        if evidence["portfolio"].get("automatic_research", {}).get("version")
        == "automatic-hypotheses-v10"
        else "Per expanding fit: 32 original-clock family screens; 4 targeted momentum probes; up to 4 independent legal-grid clock probes; at least 8 seeded joint refinement proposals within 48 total"
        if evidence["portfolio"].get("automatic_research", {}).get("version")
        == "automatic-hypotheses-v9"
        else "Per expanding fit: 32 original-clock family screens; up to 8 independent legal-grid clock probes; at least 8 seeded joint refinement proposals within 48 total"
        if evidence["portfolio"].get("automatic_research", {}).get("version")
        == "automatic-hypotheses-v8"
        else "96 original-clock condition screens; up to 24 independent legal-grid clock probes; remaining seeded joint refinement within 128 proposals",
    }


def axes(specification):
    return {
        f"{row['id']}.timing_choice": {
            "type": "categorical",
            "choices": [branch["id"] for branch in row["branches"]],
        }
        for row in specification["strategies"]
    }


def _clock_config(config, item, branch):
    """Reset registered clocks, preserving exact original field absence.

    Native config normalization includes both clocks whenever any timing field
    is supplied. An empty original clock dictionary therefore denotes a CSV-only
    timing intent over a config without those normalized default fields.
    """
    from research.engine import validate_config

    result = deepcopy(config)
    for key in ("entry_time", "exit_time"):
        if key not in item["original"]:
            result.pop(key, None)
    if not item["original"]:
        result.pop("trade_horizon", None)
        result.pop("hold_minutes", None)
    result.update(item["original"])
    result.update(branch["changes"])
    return validate_config(result) if branch["changes"] else result


def apply_choices(strategies, params, specification):
    definitions = {row["id"]: row for row in specification["strategies"]}
    result = [{**row, "config": deepcopy(row["config"])} for row in strategies]
    for row in result:
        item = definitions[row["id"]]
        branch = next(
            (
                branch
                for branch in item["branches"]
                if branch["id"] == params[f"{row['id']}.timing_choice"]
            ),
            None,
        )
        if branch is None:
            raise ValueError("Automatic timing state must belong to its frozen choices")
        row["config"] = _clock_config(row["config"], item, branch)
    return result


def probes(axes_spec, specification):
    """One independent clock flip per strategy; all other axes use legal midpoints."""
    from research.connectors.optuna_portfolio import _values

    original = {"hypothesis": "none"}
    for name, domain in axes_spec.items():
        values = domain["choices"] if domain.get("type") == "categorical" else _values(domain)
        original[name] = "original" if name.endswith(".timing_choice") else values[len(values) // 2]
    return [
        {**original, f"{row['id']}.timing_choice": branch["id"]}
        for row in specification["strategies"]
        for branch in row["branches"]
        if branch["id"] != "original"
    ]


def period_strategies(evidence, first, last, name):
    """Common source cohort, purged by every scheduled maximum branch window."""
    cohort = {row["id"]: row["signals"] for row in evidence["timing_cohort"]["strategies"]}
    windows = {
        (row["strategy_id"], row["signal_index"]): row["windows"]
        for row in evidence["timing_union"]["opportunities"]
    }
    rows, purged = [], 0
    for row in evidence["strategies"]:
        signals = []
        for index, original in enumerate(cohort[row["id"]]):
            if not first <= original["date"] <= last:
                continue
            signal = dict(original)
            if not signal.get("research_exclusion") and any(
                branch["state"] == "scheduled"
                and (branch["entry"][:10] > last or branch["deadline"][:10] > last)
                for branch in windows[row["id"], index].values()
            ):
                signal["research_exclusion"] = (
                    f"Maximum timing union holding window crosses the {name} period end ({last})"
                )
                purged += 1
            signals.append(signal)
        rows.append({**row, "signals": signals})
    return rows, purged


def stability_neighbors(settings, ranges, specification, trailing):
    from research.automatic_trailing import stability_neighbors as exit_neighbors

    exits = exit_neighbors(settings, ranges, trailing)
    clocks = []
    for index, row in enumerate(settings):
        item = next(item for item in specification["strategies"] if item["id"] == row["id"])
        current = {key: row["config"].get(key) for key in ("entry_time", "exit_time")}
        for branch in item["branches"]:
            changes = {**item["original"], **branch["changes"]}
            changed_clocks = {key: changes.get(key) for key in ("entry_time", "exit_time")}
            if current == changed_clocks:
                continue
            changed = deepcopy(settings)
            changed[index]["config"] = _clock_config(row["config"], item, branch)
            clocks.append(
                {
                    "config_id": settings_identity(changed),
                    "strategy_id": row["id"],
                    "axis": "timing_choice",
                    "from": current,
                    "to": branch["id"],
                    "strategies": changed,
                }
            )
    mixed = []
    for index in range(max(len(clocks), len(exits["neighbors"]))):
        for candidates in (clocks, exits["neighbors"]):
            if index < len(candidates):
                mixed.append(candidates[index])
    return {
        "neighbors": mixed[:8],
        "available": len(clocks) + exits["available"],
        "truncated": len(clocks) + exits["available"] > 8,
    }
