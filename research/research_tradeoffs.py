"""Bounded exploratory trade-offs from already saved development accounts.

This view never runs an engine, reads histories, changes a recommendation or uses
final-period outcomes. Recorded metadata can establish a comparable display, not
that an alternative passed the selected account's validation challenges.
"""

import json
import math
import re
from copy import deepcopy

from research.evaluation_basis import IDENTITIES
from research.evaluation_basis import VERSION as BASIS_VERSION
from research.report_contract import fingerprint, settings_identity

VERSION = "research-development-tradeoffs-v1"
MAX_ROWS = 1000
MAX_BYTES = 16 * 1024**2
MAX_CHOICES = 4
SUPPORTED = tuple(f"automatic-hypotheses-v{number}" for number in range(8, 14))
LIMITATIONS = [
    "Exploratory final-development-fit accounts only; alternatives have not inherited the selected setup's later-window, cost, neighbour or uncertainty checks.",
    "Closed-trade count is recorded activity, not turnover, liquidity, independent observations or statistical confidence.",
    "Recorded execution limits and support qualify this display, not executable capacity or future performance. No account history was inspected here.",
    "Only evaluated settings are compared; this is not an exhaustive or universal optimum. The saved recommendation is unchanged.",
]


def _bounded(value):
    size = 0
    for part in json.JSONEncoder(allow_nan=True).iterencode(value):
        size += len(part.encode())
        if size > MAX_BYTES:
            raise ValueError("Saved trade-off metadata exceeds its size limit")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _metrics(summary, entry_dates=None):
    if not isinstance(summary, dict):
        raise ValueError("Recorded account summary is unavailable")
    keys = ("net_return_pct", "max_drawdown_pct", "closed_trades")
    if any(not _finite(summary.get(key)) for key in keys):
        raise ValueError("Return, drawdown or closed-trade count is unavailable")
    count = summary["closed_trades"]
    if summary["max_drawdown_pct"] < 0 or count < 0 or int(count) != count:
        raise ValueError("Recorded drawdown or closed-trade count is invalid")
    if entry_dates is not None and (type(entry_dates) is not int or not 0 <= entry_dates <= count):
        raise ValueError("Recorded distinct closed-entry-date count is invalid")
    return {**{key: summary[key] for key in keys}, "entry_dates": entry_dates}


def _condition(hypothesis, condition_version):
    if (
        not isinstance(hypothesis, dict)
        or not isinstance(hypothesis.get("id"), str)
        or not 1 <= len(hypothesis["id"]) <= 200
    ):
        raise ValueError("The recorded condition identity is unavailable")
    return (
        {"version": condition_version, "hypothesis": hypothesis}
        if hypothesis["id"] != "none"
        else None
    )


def _basis(value, hypothesis, condition_version):
    if (
        not isinstance(value, dict)
        or value.get("version") != BASIS_VERSION
        or value.get("status") != "verified"
        or any(
            not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key])
            for key in IDENTITIES
        )
        or value.get("evidence_id")
        != fingerprint({"version": BASIS_VERSION, **{key: value[key] for key in IDENTITIES}})
        or not isinstance(value.get("period"), dict)
        or value["period"].get("kind") != "selection"
        or not isinstance(value.get("comparison"), dict)
    ):
        raise ValueError("Verified development-period input metadata is unavailable")
    comparison = deepcopy(value["comparison"])
    identity = comparison.pop("id", None)
    if identity != fingerprint({"version": BASIS_VERSION, **comparison}):
        raise ValueError("Recorded account comparison metadata changed")
    if (
        comparison.get("period") != "selection"
        or comparison.get("currency") != "INR"
        or not _finite(comparison.get("capital"))
        or comparison["capital"] <= 0
        or not isinstance(comparison.get("execution"), dict)
        or comparison["execution"].get("engine") != "vectorbt"
        or any(
            not isinstance(comparison["execution"].get(key), str)
            or not comparison["execution"][key]
            for key in ("engine_version", "adapter_version", "policy_version", "portfolio_version")
        )
        or not isinstance(comparison.get("costs"), list)
        or not comparison["costs"]
    ):
        raise ValueError("Recorded account assumptions are unavailable")
    condition = comparison["execution"].pop("entry_conditions", None)
    if condition != _condition(hypothesis, condition_version):
        raise ValueError("Recorded entry condition differs from its declared hypothesis")
    return comparison


def _settings(rows, originals, comparison, version, condition):
    if (
        not isinstance(rows, list)
        or len(rows) != len(originals)
        or not 1 <= len(rows) <= 8
        or [row.get("id") for row in rows] != [row.get("id") for row in originals]
    ):
        raise ValueError("Recorded strategy identities differ from the frozen reference")
    expected_costs = []
    for row, original in zip(rows, originals, strict=True):
        if row.get("entry_condition") != condition:
            raise ValueError("Strategy condition markers differ from the recorded account gate")
        cfg, original_cfg = row.get("config"), original.get("config")
        if not isinstance(cfg, dict) or not isinstance(original_cfg, dict):
            raise ValueError("Recorded strategy settings are unavailable")
        if any(
            cfg.get(key) != original_cfg.get(key)
            for key in (
                "initial_capital",
                "cost_bps",
                "slippage_bps",
                "max_exposure_pct",
                "exposure_fill_mode",
                "priority_seed",
                "entry_priority",
                "modes",
            )
        ):
            raise ValueError("Fixed execution settings or strategy costs differ from the reference")
        if cfg.get("initial_capital", comparison["capital"]) != comparison["capital"]:
            raise ValueError("Starting capital differs from the recorded account")
        expected_costs.append(
            {
                "strategy_id": row["id"],
                "cost_bps": cfg.get("cost_bps", 0),
                "slippage_bps": cfg.get("slippage_bps", 0),
            }
        )
        if version in SUPPORTED[2:]:
            from research.execution_policy import normalize_policy

            policy, original_policy = row.get("execution_policy"), original.get("execution_policy")
            if (
                not isinstance(policy, dict)
                or not isinstance(original_policy, dict)
                or normalize_policy(policy) != policy
                or normalize_policy(original_policy) != original_policy
                or policy["version"] != original_policy["version"]
                or policy["risk_limits"] != original_policy["risk_limits"]
                or comparison.get("risk_limits") != policy["risk_limits"]
            ):
                raise ValueError("Fixed execution ceilings differ from the frozen reference")
            for actual, ceiling in (
                (row.get("allocation_pct"), original.get("allocation_pct")),
                (cfg.get("order_size_pct"), original_cfg.get("order_size_pct")),
            ):
                if not _finite(actual) or not _finite(ceiling) or not 0 <= actual <= ceiling:
                    raise ValueError("Candidate sizing exceeds its frozen declaration")
        elif row.get("execution_policy") != original.get("execution_policy"):
            raise ValueError("Execution policies differ from the frozen reference")
        elif row.get("allocation_pct") != original.get("allocation_pct") or cfg.get(
            "order_size_pct"
        ) != original_cfg.get("order_size_pct"):
            raise ValueError("Recorded sizing differs from this recipe's fixed declaration")
    if comparison["costs"] != expected_costs:
        raise ValueError("Recorded strategy costs disagree with account metadata")


def _clocks(rows, originals, recipe):
    """Only frozen clock alternatives may explain a different admission identity."""
    keys = ("entry_time", "exit_time")
    actual = tuple(tuple(row["config"].get(key) for key in keys) for row in rows)
    original_clocks = tuple(tuple(row["config"].get(key) for key in keys) for row in originals)
    if actual == original_clocks:
        return actual
    spec = recipe.get("timing_research")
    if (
        not isinstance(spec, dict)
        or spec.get("version") != "automatic-timing-states-v1"
        or spec.get("interval") != "1m"
        or not isinstance(spec.get("strategies"), list)
        or len(spec["strategies"]) != len(originals)
    ):
        raise ValueError("Changed entry or exit clocks lack frozen timing admission")
    for row, original, chosen in zip(rows, originals, actual, strict=True):
        if chosen == tuple(original["config"].get(key) for key in keys):
            continue
        definitions = [item for item in spec["strategies"] if item.get("id") == row["id"]]
        if len(definitions) != 1:
            raise ValueError("The changed clock has no unique frozen source strategy")
        item = definitions[0]
        frozen_original = {
            key: original["config"][key] for key in keys if key in original["config"]
        }
        if (
            item.get("original") != frozen_original
            or not isinstance(item.get("branches"), list)
            or not 1 <= len(item["branches"]) <= 4
        ):
            raise ValueError("The frozen original clock or bounded branches changed")
        admitted = False
        for branch in item["branches"]:
            changes = branch.get("changes")
            if not isinstance(changes, dict) or set(changes) - set(keys):
                raise ValueError("The timing branch contains unsupported changes")
            configured = {**frozen_original, **changes}
            if chosen == tuple(configured.get(key) for key in keys):
                admitted = True
        if not admitted:
            raise ValueError("The changed clock is outside its frozen timing branches")
    return actual


def _dominates(left, right):
    a, b = left["metrics"], right["metrics"]
    return (
        a["net_return_pct"] >= b["net_return_pct"]
        and a["max_drawdown_pct"] <= b["max_drawdown_pct"]
        and a["closed_trades"] <= b["closed_trades"]
        and any(a[key] != b[key] for key in ("net_return_pct", "max_drawdown_pct", "closed_trades"))
    )


def analyze(result):
    """Describe comparable observed trade-offs without selecting new settings."""
    output = {
        "version": VERSION,
        "status": "unavailable",
        "scope": "Saved final development fit only; final-period outcomes are excluded",
        "metric_basis": {
            "return": "Recorded native account return after the saved costs",
            "risk": "Recorded native maximum drawdown",
            "activity": "Recorded closed-trade count, not turnover",
            "balanced": "Recorded return minus maximum drawdown, in percentage points",
            "eligibility": "Positive return, drawdown at most 25%, at least five closed trades on five distinct entry dates, and matching recorded account assumptions",
        },
        "selected_config_id": None,
        "baseline": None,
        "counts": {"recorded": 0, "eligible": 0, "frontier": 0, "excluded": 0, "duplicate_rows": 0},
        "choices": [],
        "unavailable": [],
        "limitations": list(LIMITATIONS),
        "reasons": [],
    }
    try:
        automatic = result.get("automatic_research", {})
        recipe = automatic.get("recipe", {})
        version = automatic.get("version")
        rows = result.get("experiment", {}).get("rows")
        if version not in SUPPORTED or recipe.get("version") != version:
            raise ValueError("This saved study does not have a supported automatic recipe")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise ValueError(
                "Saved development candidate rows are unavailable or exceed their bound"
            )
        originals = recipe.get("baseline")
        if not isinstance(originals, list) or not 1 <= len(originals) <= 8:
            raise ValueError("Frozen original settings are unavailable")
        _bounded([rows, originals, result.get("evaluation_basis"), result.get("strategies")])
        selected = automatic.get("selected_config_id")
        if selected != settings_identity(result.get("strategies")):
            raise ValueError("The saved selected settings identity changed")
        output["selected_config_id"] = selected
        condition_version = (
            "research-joint-entry-conditions-v1"
            if version == SUPPORTED[0]
            else "research-joint-entry-conditions-v2"
        )
        selected_hypothesis = result.get("automatic_stock_condition", {}).get("hypothesis")
        anchor = _basis(
            result.get("evaluation_basis"),
            selected_hypothesis,
            condition_version,
        )
        _settings(
            result["strategies"],
            originals,
            anchor,
            version,
            _condition(selected_hypothesis, condition_version),
        )
        anchor_clocks = _clocks(result["strategies"], originals, recipe)
        baseline_id = settings_identity(originals)
        baseline = {
            "config_id": baseline_id,
            "selected": selected == baseline_id,
            "metrics": None,
            "reasons": [],
            "scope": "Recorded unchanged development summary; no alternative validation or history inspection",
        }
        try:
            baseline["metrics"] = _metrics(automatic.get("baseline"))
        except ValueError as error:
            baseline["reasons"].append(str(error))
        output["baseline"] = baseline
        output["counts"]["recorded"] = len(rows)
        grouped = {}
        for row in rows:
            identity = row.get("config_id") if isinstance(row, dict) else None
            grouped.setdefault(identity, []).append(row)
        eligible = []
        for identity, duplicates in grouped.items():
            try:
                row = duplicates[0]
                if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
                    raise ValueError("Recorded candidate identity is unavailable")
                if any(item != row for item in duplicates[1:]):
                    raise ValueError("Duplicate candidate identities have conflicting saved facts")
                output["counts"]["duplicate_rows"] += len(duplicates) - 1
                if identity != settings_identity(row.get("strategies")):
                    raise ValueError("Recorded candidate settings identity changed")
                hypothesis = row.get("automatic_stock_condition", {}).get("hypothesis")
                if hypothesis != row.get("hypothesis"):
                    raise ValueError("Recorded candidate condition is inconsistent")
                comparison = _basis(
                    row.get("evaluation_basis"),
                    hypothesis,
                    condition_version,
                )
                if comparison != anchor:
                    raise ValueError("Capital, costs, execution versions or fixed ceilings differ")
                basis, reference = row["evaluation_basis"], result["evaluation_basis"]
                if basis["period"] != reference["period"] or any(
                    basis[key] != reference[key] for key in IDENTITIES if key != "cohort_id"
                ):
                    raise ValueError(
                        "Development dates, original signals or observed input identities differ"
                    )
                _settings(
                    row["strategies"],
                    originals,
                    comparison,
                    version,
                    _condition(hypothesis, condition_version),
                )
                clocks = _clocks(row["strategies"], originals, recipe)
                if (
                    basis["cohort_id"] != reference["cohort_id"]
                    and hypothesis == selected_hypothesis
                    and clocks == anchor_clocks
                ):
                    raise ValueError(
                        "Admission cohort changed without a different declared gate or admitted clock"
                    )
                metrics = _metrics(row.get("summary"), row.get("entry_dates"))
                reasons = []
                if metrics["net_return_pct"] <= 0:
                    reasons.append("Return after recorded costs is not positive")
                if metrics["max_drawdown_pct"] > 25:
                    reasons.append("Drawdown exceeds the recorded research display limit of 25%")
                if (
                    metrics["closed_trades"] < 5
                    or metrics["entry_dates"] is None
                    or metrics["entry_dates"] < 5
                ):
                    reasons.append(
                        "Fewer than five closed trades or five recorded distinct entry dates"
                    )
                if reasons:
                    output["unavailable"].append({"config_id": identity, "reasons": reasons})
                    continue
                eligible.append({"config_id": identity, "metrics": metrics})
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                output["unavailable"].append({"config_id": identity, "reasons": [str(error)]})
        reference_metrics = baseline["metrics"]
        reference_row = {"metrics": reference_metrics} if reference_metrics is not None else None
        reference_dominated = {
            row["config_id"]
            for row in eligible
            if reference_row is not None
            and (
                _dominates(reference_row, row)
                or all(
                    reference_metrics[key] == row["metrics"][key]
                    for key in ("net_return_pct", "max_drawdown_pct", "closed_trades")
                )
            )
        }
        baseline["dominates_or_matches_candidates"] = len(reference_dominated)
        frontier = [
            row
            for row in eligible
            if row["config_id"] not in reference_dominated
            and not any(_dominates(other, row) for other in eligible)
        ]
        frontier.sort(key=lambda row: row["config_id"])
        roles = {
            "balanced": lambda row: (
                -(row["metrics"]["net_return_pct"] - row["metrics"]["max_drawdown_pct"]),
                row["config_id"],
            ),
            "best_return": lambda row: (-row["metrics"]["net_return_pct"], row["config_id"]),
            "lower_drawdown": lambda row: (row["metrics"]["max_drawdown_pct"], row["config_id"]),
            "fewer_closed_trades": lambda row: (row["metrics"]["closed_trades"], row["config_id"]),
        }
        chosen = {}
        for role, order in roles.items():
            if frontier:
                row = min(frontier, key=order)
                chosen.setdefault(row["config_id"], {**row, "roles": []})["roles"].append(role)
        output["choices"] = [
            {
                **deepcopy(row),
                "selected_existing": row["config_id"] == selected,
                "validation": "exploratory_development_only",
            }
            for row in chosen.values()
        ]
        output["counts"].update(
            eligible=len(eligible), frontier=len(frontier), excluded=len(output["unavailable"])
        )
        output["unavailable"].sort(key=lambda row: str(row["config_id"]))
        if reference_dominated and not frontier:
            output["reasons"].append(
                "Unchanged settings match or improve every otherwise eligible alternative on recorded return, drawdown and closed-trade count"
            )
        output["status"] = "available" if frontier else "no_eligible_alternatives"
        return output
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        output["reasons"] = [str(error)]
        return output
