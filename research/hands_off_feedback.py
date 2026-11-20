"""Bounded, prefix-only evidence for the next hands-off planning decision.

This is a projection of completed checkpoint accounts, never a new calculation
or validation verdict. Oversized histories require a real saved-evidence pointer.
"""

import json
from copy import deepcopy
from decimal import Decimal
from math import isfinite, prod

from research.report_contract import settings_identity

VERSION = "hands-off-feedback-v1"
MAX_BYTES = 40_000
MAX_PROPOSALS = 68
MAX_RECORDS = 12
MAX_STRATEGIES = 8
SUMMARY = {
    "initial_capital",
    "final_equity",
    "realized_equity",
    "net_return_pct",
    "net_pnl",
    "max_drawdown_pct",
    "accepted_trades",
    "closed_trades",
    "pending_trades",
    "unfunded_pending",
    "skipped_trades",
    "excluded_signals",
    "win_rate_pct",
    "profit_factor",
    "sample_adequacy",
    "total_costs",
    "total_cost",
    "costs",
    "gross_pnl",
    "sharpe",
    "sharpe_ratio",
}
DESIGN = {
    "version",
    "qualified",
    "reasons",
    "closed_trades",
    "entry_dates",
    "calendar_bins",
    "active_bins",
    "largest_bin_trade_fraction",
    "largest_symbol_positive_profit_fraction",
    "capital_use",
    "independently_validated",
}
DIAGNOSTICS = {
    "exit_outcomes",
    "skipped_reasons",
    "maximum_observed_holding_sessions",
    "max_open_positions",
    "complete_signal_count_in_fit",
    "scope",
}


def _small(value, depth=0):
    """Reject unbounded/non-JSON metadata instead of silently dropping details."""
    if depth > 6:
        raise ValueError("Feedback metadata exceeds its nesting bound")
    if value is None or isinstance(value, bool):
        return value
    if type(value) in (int, float):
        if not isfinite(value):
            raise ValueError("Feedback evidence contains a nonfinite value")
        return value
    if isinstance(value, str) and len(value) <= 1000:
        return value
    if isinstance(value, list) and len(value) <= 64:
        return [_small(v, depth + 1) for v in value]
    if isinstance(value, dict) and len(value) <= 64:
        if any(not isinstance(k, str) or len(k) > 128 for k in value):
            raise ValueError("Feedback evidence contains an invalid field")
        return {k: _small(v, depth + 1) for k, v in value.items()}
    raise ValueError("Feedback evidence exceeds its metadata bound")


def _settings(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_STRATEGIES:
        raise ValueError("Feedback requires one to eight complete strategies")
    result = []
    identities = set()
    for row in rows:
        if not isinstance(row, dict) or not all(
            k in row for k in ("id", "name", "allocation_pct", "config")
        ):
            raise ValueError("Feedback is missing complete strategy settings")
        if not isinstance(row["id"], str) or row["id"] in identities or not row["id"]:
            raise ValueError("Feedback strategy identities changed")
        identities.add(row["id"])
        result.append(
            _small(
                {
                    k: row[k]
                    for k in ("id", "name", "allocation_pct", "config", "execution_policy")
                    if k in row
                }
            )
        )
    return result


def _changes(before, after, path=""):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else key
            if key not in before or key not in after:
                result.append(
                    {
                        "path": child,
                        "before": deepcopy(before.get(key)),
                        "after": deepcopy(after.get(key)),
                        "before_present": key in before,
                        "after_present": key in after,
                    }
                )
            else:
                result.extend(_changes(before[key], after[key], child))
        return result
    return (
        []
        if before == after
        else [{"path": path, "before": deepcopy(before), "after": deepcopy(after)}]
    )


def _strategy_changes(anchor, actual):
    return _changes({r["id"]: r for r in anchor}, {r["id"]: r for r in actual})


def _values(domain):
    if not isinstance(domain, dict) or set(domain) != {"min", "max", "step"}:
        raise ValueError("Feedback search domain is not a compiled numerical range")
    low, high, step = (Decimal(str(domain[k])) for k in ("min", "max", "step"))
    if not all(v.is_finite() for v in (low, high, step)) or step <= 0 or low > high:
        raise ValueError("Feedback search domain is invalid")
    count = int((high - low) // step) + 1
    if count > 1001:
        raise ValueError("Feedback search domain exceeds its numeric bound")
    return tuple(low + i * step for i in range(count))


def _grid(plan, anchor, configurations):
    domains = plan.get("domains", {})
    if plan.get("action") != "search" or not domains or not anchor:
        return {
            "proof": "not_a_frozen_numeric_search",
            "tested_values": {},
            "untested_count": None,
            "exhausted": None,
        }
    axes, allowed = [], []
    by_id = {r["id"]: r for r in anchor}
    for sid, fields in sorted(domains.items()):
        if sid not in by_id or not isinstance(fields, dict):
            raise ValueError("Feedback domain changed its anchor strategy cohort")
        for field, domain in sorted(fields.items()):
            if field != "allocation_pct" and field not in by_id[sid]["config"]:
                raise ValueError("Feedback domain has no corresponding complete setting")
            axes.append((sid, field))
            allowed.append(_values(domain))
    tested, tuples, matching = {f"{sid}.{field}": set() for sid, field in axes}, set(), 0
    controlled = {name: set() for name in tested}
    for candidate in configurations:
        rows = deepcopy(candidate["strategies"])
        observed = {r["id"]: r for r in rows}
        coordinates, in_domain = [], True
        if set(observed) != set(by_id):
            raise ValueError("Feedback account changed its strategy cohort")
        for (sid, field), values in zip(axes, allowed, strict=True):
            source = observed[sid] if field == "allocation_pct" else observed[sid]["config"]
            if type(source.get(field)) not in (int, float):
                raise ValueError("Feedback account is missing a numeric domain setting")
            value = Decimal(str(source[field]))
            tested[f"{sid}.{field}"].add(value)
            coordinates.append(value)
            if value not in values:
                in_domain = False
            source[field] = (
                by_id[sid]["allocation_pct"]
                if field == "allocation_pct"
                else by_id[sid]["config"][field]
            )
        if in_domain and not _strategy_changes(anchor, rows):
            matching += 1
            tuples.add(tuple(coordinates))
            for (sid, field), value in zip(axes, coordinates, strict=True):
                controlled[f"{sid}.{field}"].add(value)
    size = prod(map(len, allowed))
    # No enumeration of a large Cartesian product is required to prove coverage:
    # all observed tuples are admissible and distinct, so cardinality is enough.
    proven = matching > 0
    return {
        "proof": "complete_configurations_preserve_all_frozen_controls"
        if proven
        else "frozen_controls_or_domain_membership_not_proven",
        "tested_values": {k: [float(v) for v in sorted(values)] for k, values in tested.items()},
        "tested_values_scope": "All reported outcomes, including broader approved-domain fills",
        "controlled_tested_values": {
            k: [float(v) for v in sorted(values)] for k, values in controlled.items()
        },
        "cartesian_size": size,
        "distinct_tested_tuples": len(tuples),
        "matching_configurations": matching,
        "outside_controlled_domain": len(configurations) - matching,
        "untested_count": size - len(tuples) if proven else None,
        "exhausted": len(tuples) == size if proven else None,
    }


def _compact(candidate, anchor, numbers):
    if not isinstance(candidate, dict) or not isinstance(candidate.get("summary"), dict):
        raise ValueError("Feedback is missing a measured account summary")
    strategies = _settings(candidate.get("strategies"))
    if settings_identity(strategies) != candidate.get("config_id"):
        raise ValueError("Feedback configuration identity disagrees with its complete settings")
    score = candidate.get("score")
    if type(score) not in (int, float) or not isfinite(score):
        raise ValueError("Feedback is missing its measured account score")
    row = {
        "config_id": candidate["config_id"],
        "proposal_indices": numbers,
        "strategies": strategies,
        "score": score,
        "summary": _small({k: v for k, v in candidate["summary"].items() if k in SUMMARY}),
        "changes_from_anchor": _strategy_changes(anchor, strategies) if anchor else None,
    }
    for key, fields in (("automatic_design", DESIGN), ("research_diagnostics", DIAGNOSTICS)):
        value = candidate.get(key)
        if value is not None and not isinstance(value, dict):
            raise ValueError("Feedback diagnostics are malformed")
        projected = {k: v for k, v in value.items() if k in fields} if value is not None else None
        if (
            key == "research_diagnostics"
            and projected is not None
            and "skipped_reasons" in projected
        ):
            histogram = projected["skipped_reasons"]
            if (
                not isinstance(histogram, dict)
                or len(histogram) > 64
                or any(
                    not isinstance(reason, str)
                    or not reason
                    or len(reason) > 1000
                    or type(count) is not int
                    or count < 0
                    for reason, count in histogram.items()
                )
            ):
                raise ValueError("Feedback skipped reasons need bounded complete text and counts")
            projected["skipped_reasons"] = [
                {"reason": reason, "count": count} for reason, count in histogram.items()
            ]
        row[key] = _small(projected) if projected is not None else None
    return row


def build_feedback(current, prior_records, prefix_end):
    """Return all actual distinct accounts for each prior AI block in one fit.

    ``current`` is that fit's checkpoint, not the whole multi-fit study. For an
    oversized history ``feedback_evidence_ref`` must identify its already saved
    checkpoint/artifact. Missing trial/account evidence always fails closed.
    """
    if (
        not isinstance(current, dict)
        or not isinstance(prior_records, list)
        or len(prior_records) > MAX_RECORDS
    ):
        raise ValueError("Feedback requires a bounded single-fit checkpoint")
    if type(prefix_end) is not int or not 0 <= prefix_end <= MAX_PROPOSALS:
        raise ValueError("Feedback prefix is outside the native fit budget")
    records = []
    for record in prior_records:
        if (
            not isinstance(record, dict)
            or type(record.get("index")) is not int
            or not 0 <= record["index"] < MAX_PROPOSALS
        ):
            raise ValueError("Feedback planning history is malformed")
        if 0 <= record["index"] < prefix_end:
            if record.get("phase") != "completed" or not isinstance(record.get("plan"), dict):
                raise ValueError("Feedback cannot claim an incomplete AI plan was executed")
            records.append(record)
    records.sort(key=lambda r: r["index"])
    if (
        len({r["index"] for r in records}) != len(records)
        or len({r.get("fit") for r in records}) > 1
    ):
        raise ValueError("Feedback mixes different fits or duplicate AI boundaries")
    trials = current.get("trials")
    candidates = current.get("candidates")
    if (
        not isinstance(trials, list)
        or not isinstance(candidates, dict)
        or len(trials) > MAX_PROPOSALS
        or len(candidates) > MAX_PROPOSALS
    ):
        raise ValueError("Feedback is missing its native trial/account history")
    prefix = {}
    for trial in trials:
        if not isinstance(trial, dict) or type(trial.get("number")) is not int:
            raise ValueError("Feedback trial history is malformed")
        number = trial["number"]
        if 0 <= number < prefix_end:
            if number in prefix:
                raise ValueError("Feedback trial numbers are duplicated")
            prefix[number] = trial
    blocks = []
    for position, record in enumerate(records):
        start = record["index"]
        end = records[position + 1]["index"] if position + 1 < len(records) else prefix_end
        groups, reused, sources = {}, 0, {}
        for number in range(start, end):
            trial = prefix.get(number)
            if (
                trial is None
                or trial.get("state") != "complete"
                or type(trial.get("reused")) is not bool
            ):
                raise ValueError("Feedback is missing a completed proposal in the claimed block")
            config_id = trial.get("config_id")
            if not isinstance(config_id, str):
                raise ValueError("Feedback proposal has no complete configuration identity")
            candidate = candidates.get(config_id)
            if (
                not isinstance(candidate, dict)
                or candidate.get("config_id") != config_id
                or type(candidate.get("trial_number")) is not int
                or not 0 <= candidate["trial_number"] <= number
            ):
                raise ValueError("Feedback is missing the actual prefix account for a proposal")
            if candidate.get("score") != trial.get("value"):
                raise ValueError("Feedback trial score disagrees with its completed account")
            groups.setdefault(config_id, []).append(number)
            source = trial.get("proposal_source", "unspecified")
            if source not in ("ai_domain", "approved_domain", "unspecified"):
                raise ValueError("Feedback has an unknown proposal source")
            sources.setdefault(config_id, {}).setdefault(source, []).append(number)
            reused += trial["reused"]
        plan = record["plan"]
        anchor = _settings(plan["strategies"]) if plan.get("strategies") else []
        rows = [_compact(candidates[key], anchor, numbers) for key, numbers in groups.items()]
        for row in rows:
            row["proposal_sources"] = sources[row["config_id"]]
        block = {
            "start": start,
            "end_exclusive": end,
            "plan": {
                k: deepcopy(plan[k])
                for k in (
                    "id",
                    "action",
                    "method",
                    "hypothesis",
                    "reason",
                    "expected_evidence",
                    "stop_rule",
                    "domains",
                    "base_id",
                    "search_identity",
                )
                if k in plan
            },
            "anchor_strategies": anchor,
            "anchor_source": "ai_plan" if anchor else None,
            "completed_proposals": end - start,
            "distinct_configurations": len(rows),
            "reused_proposals": reused,
            "fresh_proposals": end - start - reused,
            "configuration_changes": sum(bool(row["changes_from_anchor"]) for row in rows)
            if anchor
            else None,
            "configurations": rows,
            "rows_complete": True,
            "omitted_rows": 0,
            "grid": _grid(plan, anchor, rows),
        }
        blocks.append(block)
    result = {
        "version": VERSION,
        "prefix_end": prefix_end,
        "fit": records[0].get("fit") if records else None,
        "scope": "Completed accounts from this fit's training prefix only; not validation or later-fit evidence",
        "projection": "Complete strategies and allowlisted economic, risk, design and ledger diagnostics; no bars, curves or full trade ledger",
        "history_complete": True,
        "rows_complete": True,
        "configuration_details_complete": True,
        "omitted_rows": 0,
        "blocks": blocks,
    }
    if len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode()) <= MAX_BYTES:
        return result
    reference = current.get("feedback_evidence_ref")
    if not isinstance(reference, dict) or not reference:
        raise ValueError(
            "Oversized feedback needs a real saved checkpoint/artifact evidence reference"
        )
    reference = _small(reference)
    result["evidence_refs"] = [reference]
    result["configuration_details_complete"] = False
    result["configuration_reconstructable"] = True
    for block in blocks:
        rows = block["configurations"]
        if not block["anchor_strategies"] and rows:
            block["anchor_strategies"] = deepcopy(rows[0]["strategies"])
            block["anchor_source"] = "measured_configuration"
            block["anchor_config_id"] = rows[0]["config_id"]
        block["configuration_details_complete"] = False
        block["aggregates"] = {
            "minimum_score": min((r["score"] for r in rows), default=None),
            "maximum_score": max((r["score"] for r in rows), default=None),
            "qualified_configurations": sum(
                r["automatic_design"].get("qualified") is True
                for r in rows
                if r["automatic_design"] is not None
            ),
        }
        paths, values, value_indices = [], [], {}
        for row in rows:
            actual = row.pop("strategies")
            changes = _strategy_changes(block["anchor_strategies"], actual)
            row.pop("changes_from_anchor")
            encoded_changes = []
            for change in changes:
                if change["path"] not in paths:
                    paths.append(change["path"])
                value_index = -1
                if change.get("after_present") is not False:
                    encoded_value = json.dumps(
                        change["after"], sort_keys=True, separators=(",", ":"), allow_nan=False
                    )
                    if encoded_value not in value_indices:
                        value_indices[encoded_value] = len(values)
                        values.append(change["after"])
                    value_index = value_indices[encoded_value]
                encoded_changes.append([paths.index(change["path"]), value_index])
            row["configuration_changes"] = encoded_changes
            row["configuration_ref"] = {
                "evidence_ref": 0,
                "config_id": row["config_id"],
            }
        block["configuration_encoding"] = "anchor_indexed_changes"
        block["configuration_paths"] = paths
        block["configuration_values"] = values
        block["configuration_patch_format"] = (
            "Each [path_index,value_index] sets the shared value at that anchor path; value_index -1 deletes it. All other anchor fields remain unchanged."
        )
    if len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode()) <= MAX_BYTES:
        return result
    result["diagnostics_complete"] = False
    result["diagnostic_omission_notice"] = (
        "Additional reason texts remain in saved evidence; omitted reasons are not zero occurrences."
    )
    texts, text_indices = [], {}

    def text_reference(text):
        if text not in text_indices:
            text_indices[text] = len(texts)
            texts.append(text)
        return {"text_ref": text_indices[text]}

    for block in blocks:
        for row in block["configurations"]:
            design = row["automatic_design"]
            if design is not None:
                row["automatic_design"] = {
                    key: value
                    for key, value in design.items()
                    if key not in ("calendar_bins", "version", "reasons")
                }
                row["automatic_design"].update(
                    reasons=[text_reference(reason) for reason in design.get("reasons", [])],
                    details_complete=False,
                    details_in_configuration_ref=True,
                )
            diagnostics = row["research_diagnostics"]
            if diagnostics is None or not diagnostics.get("skipped_reasons"):
                continue
            reasons = diagnostics["skipped_reasons"]
            dominant = max(reasons, key=lambda reason: reason["count"])
            additional = [reason for reason in reasons if reason is not dominant]
            diagnostics["skipped_reasons"] = [
                {**text_reference(dominant["reason"]), "count": dominant["count"]}
            ]
            diagnostics["additional_skipped_reasons"] = {
                "distinct_reasons": len(additional),
                "total_count": sum(reason["count"] for reason in additional),
                "complete_details": False,
                "details_in_configuration_ref": True,
            }
            diagnostics["diagnostics_complete"] = False
    result["text_catalog"] = texts
    if len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode()) > MAX_BYTES:
        schemas = []
        for block in blocks:
            for row in block["configurations"]:
                for key in ("summary", "automatic_design", "research_diagnostics"):
                    metrics = row[key]
                    if metrics is None:
                        continue
                    names = sorted(metrics)
                    if names not in schemas:
                        schemas.append(names)
                    row[key] = {
                        "schema": schemas.index(names),
                        "values": [metrics[name] for name in names],
                    }
        result["metric_schemas"] = schemas
        result["metric_encoding"] = (
            "Each metric object's schema indexes metric_schemas; pair those exact field names with its aligned values. No metric values are omitted or rounded. Null is an actual recorded null; absent fields are absent from that schema."
        )
    if len(json.dumps(result, separators=(",", ":"), allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("Feedback aggregate exceeds its bounded model input")
    return result
