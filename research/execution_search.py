"""Finite execution and risk choices under frozen user-declared ceilings.

No prices are read here. ATR observations are prepared once by execution_features
and applied to actual native entry times by the calculation worker.
"""

import re
from copy import deepcopy
from decimal import Decimal
from math import isfinite

from research.report_contract import fingerprint

VERSION = "research-execution-search-v1"
PRIORITY_SEARCH_VERSION = "research-execution-search-v2"
TURNOVER_SEARCH_VERSION = "research-execution-search-v3"
CAPACITY_SEARCH_VERSION = "research-execution-search-v4"
ALLOCATION_SEARCH_VERSION = "research-execution-search-v5"
ALLOCATION_AXIS = "portfolio.allocation_mode"
ALLOCATION_MODES = ("original", "prior60_cluster_inverse_vol")
ALLOCATION_POLICY = "One account-wide entry-only cluster inverse-volatility policy from exactly60 prior aligned returns, covariance shrunk10% to diagonal, positive regularized correlation>=0.8 connected components; equal gross-cap cluster budgets and inverse-volatility weights within each cluster. No matrix inversion, held-position rebalance or future inputs; unavailable inputs fail closed."
ENTRY_PRIORITIES = ("source", "reversed_source", "lower_stop_risk")
PRIORITY_AXIS = "portfolio.entry_priority"
ENTRY_PRIORITY_POLICY = "One account-wide entry ordering, frozen across strategies: source order, reversed source order, or lower modeled stop-loss fraction of entry cash cost first. Source order breaks ties. Only information available at each entry may order competing signals; chronology and cash release are unchanged."
EXECUTION_CHOICES = (
    {"id": "percent", "exit_mode": "percent"},
    {
        "id": "atr_1_2",
        "exit_mode": "atr",
        "atr_period": 14,
        "atr_stop_multiple": 1.0,
        "atr_target_multiple": 2.0,
    },
    {
        "id": "atr_1_3",
        "exit_mode": "atr",
        "atr_period": 14,
        "atr_stop_multiple": 1.0,
        "atr_target_multiple": 3.0,
    },
    {
        "id": "atr_2_3",
        "exit_mode": "atr",
        "atr_period": 14,
        "atr_stop_multiple": 2.0,
        "atr_target_multiple": 3.0,
    },
    {
        "id": "atr_2_4",
        "exit_mode": "atr",
        "atr_period": 14,
        "atr_stop_multiple": 2.0,
        "atr_target_multiple": 4.0,
    },
)
PORTFOLIO_CHOICES = (
    {"id": "original", "fraction": 1.0, "sizing": "allocation"},
    {"id": "allocation_75", "fraction": 0.75, "sizing": "allocation"},
    {"id": "allocation_50", "fraction": 0.5, "sizing": "allocation"},
    {"id": "stop_risk", "fraction": 1.0, "sizing": "stop_risk", "risk_budget_fraction": 0.5},
    {"id": "stop_risk_50", "fraction": 0.5, "sizing": "stop_risk", "risk_budget_fraction": 0.5},
)


def descriptor(evidence):
    from research.execution_policy import normalize_limits, normalize_policy

    portfolio = evidence["portfolio"]
    allocation = (
        portfolio.get("automatic_research", {}).get("version") == "automatic-hypotheses-v16"
    )
    capacity = portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v13",
        "automatic-hypotheses-v14",
        "automatic-hypotheses-v15",
        "automatic-hypotheses-v16",
    )
    turnover = portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
        "automatic-hypotheses-v14",
        "automatic-hypotheses-v15",
        "automatic-hypotheses-v16",
    )
    priority = portfolio.get("automatic_research", {}).get("version") in (
        "automatic-hypotheses-v11",
        "automatic-hypotheses-v12",
        "automatic-hypotheses-v13",
        "automatic-hypotheses-v14",
        "automatic-hypotheses-v15",
        "automatic-hypotheses-v16",
    )
    limits = normalize_limits(portfolio.get("risk_limits"))
    if portfolio.get("risk_limits") != limits:
        raise ValueError("Execution research needs frozen canonical account risk limits")
    originals = []
    for row in evidence["strategies"]:
        policy = normalize_policy(row.get("execution_policy"))
        if (
            policy != row.get("execution_policy")
            or policy["risk_limits"] != limits
            or policy["exit_mode"] != "percent"
            or policy["sizing"] != "allocation"
            or (allocation and policy.get("allocation_mode") != "original")
            or (
                priority
                and (
                    policy["version"]
                    != (
                        "research-execution-aware-v5"
                        if allocation
                        else "research-execution-aware-v4"
                        if capacity
                        else "research-execution-aware-v3"
                        if turnover
                        else "research-execution-aware-v2"
                    )
                    or policy.get("entry_priority") != "source"
                )
            )
            or (not priority and policy["version"] != "research-execution-aware-v1")
        ):
            raise ValueError(
                "Execution research needs the original percentage-exit allocation baseline"
            )
        originals.append(
            {
                "id": row["id"],
                "original": {
                    "allocation_pct": row["allocation_pct"],
                    "order_size_pct": row["config"]["order_size_pct"],
                    "target_pct": row["config"]["target_pct"],
                    "stop_pct": row["config"]["stop_pct"],
                    "execution_policy": deepcopy(policy),
                },
                "execution_choices": deepcopy(list(EXECUTION_CHOICES)),
                "portfolio_choices": deepcopy(list(PORTFOLIO_CHOICES)),
            }
        )
    if not 1 <= len(originals) <= 8 or len({row["id"] for row in originals}) != len(originals):
        raise ValueError("Execution research requires one to eight distinct strategies")
    value = {
        "version": ALLOCATION_SEARCH_VERSION
        if allocation
        else CAPACITY_SEARCH_VERSION
        if capacity
        else TURNOVER_SEARCH_VERSION
        if turnover
        else PRIORITY_SEARCH_VERSION
        if priority
        else VERSION,
        "risk_limits": limits,
        "strategies": originals,
        "atr": "Wilder ATR14 from prior observed daily OHLC at each recorded native entry; missing ATR excludes the common source opportunity",
        "ceilings": "Allocation and order size never exceed each original declaration; user account, symbol, position and stop-risk caps remain fixed",
        "stop_risk_target": "One half of the fixed user trade-risk ceiling, further constrained by original cash ceilings and all portfolio hard limits; never a loss guarantee",
        "inactive_axes": "ATR exits restore original percentage target and stop, so ignored numeric choices share one account identity",
        "sizing_control": "For each training-fit winner, retain filters and exits but restore original allocation, order size and allocation sizing under the same hard limits; development only",
        "screening": "Four registered ATR probes and four registered portfolio probes per independent fit, then joint refinement",
    }
    if allocation:
        value.update(allocation_modes=list(ALLOCATION_MODES), allocation_policy=ALLOCATION_POLICY)
    if priority:
        value.update(
            entry_priorities=list(ENTRY_PRIORITIES),
            entry_priority_policy=ENTRY_PRIORITY_POLICY,
            screening="Four registered ATR probes, four portfolio probes and two global priority probes per independent fit, then joint refinement",
        )
    if turnover:
        value["turnover_policy"] = (
            "Fixed user daily buy-notional ceiling on first-opening account equity; actual slipped buys exclude fees; exits never consume or replenish the budget. Never optimised."
        )
    return {**value, "id": fingerprint(value)}


def _validate(specification):
    from research.execution_policy import normalize_limits, normalize_policy

    if (
        not isinstance(specification, dict)
        or specification.get("version")
        not in (
            VERSION,
            PRIORITY_SEARCH_VERSION,
            TURNOVER_SEARCH_VERSION,
            CAPACITY_SEARCH_VERSION,
            ALLOCATION_SEARCH_VERSION,
        )
        or specification.get("id")
        != fingerprint({key: value for key, value in specification.items() if key != "id"})
        or not isinstance(specification.get("strategies"), list)
        or not 1 <= len(specification["strategies"]) <= 8
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or row.get("execution_choices") != list(EXECUTION_CHOICES)
            or row.get("portfolio_choices") != list(PORTFOLIO_CHOICES)
            for row in specification["strategies"]
        )
    ):
        raise ValueError("The frozen execution choices changed")
    rows = specification["strategies"]
    if specification["version"] == ALLOCATION_SEARCH_VERSION and (
        specification.get("allocation_modes") != list(ALLOCATION_MODES)
        or specification.get("allocation_policy") != ALLOCATION_POLICY
    ):
        raise ValueError("The frozen account allocation choices changed")
    allocation = specification["version"] == ALLOCATION_SEARCH_VERSION
    capacity = specification["version"] in (CAPACITY_SEARCH_VERSION, ALLOCATION_SEARCH_VERSION)
    turnover = specification["version"] in (
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    )
    priority = specification["version"] in (
        PRIORITY_SEARCH_VERSION,
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    )
    if priority and (
        specification.get("entry_priorities") != list(ENTRY_PRIORITIES)
        or specification.get("entry_priority_policy") != ENTRY_PRIORITY_POLICY
    ):
        raise ValueError("The frozen account entry-priority choices changed")
    if normalize_limits(specification.get("risk_limits")) != specification.get(
        "risk_limits"
    ) or len({row.get("id") for row in rows}) != len(rows):
        raise ValueError("The frozen execution strategies or risk limits changed")
    for row in rows:
        original = row.get("original")
        if (
            not isinstance(row.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["id"])
            or set(row) != {"id", "original", "execution_choices", "portfolio_choices"}
            or not isinstance(original, dict)
            or set(original)
            != {"allocation_pct", "order_size_pct", "target_pct", "stop_pct", "execution_policy"}
        ):
            raise ValueError("The frozen original execution settings changed")
        for name, low, high in (
            ("allocation_pct", 0, 100),
            ("order_size_pct", 0.01, 100),
            ("target_pct", 0.01, 500),
            ("stop_pct", 0.01, 99),
        ):
            number = original[name]
            if (
                type(number) not in (int, float)
                or not isfinite(number)
                or not low <= number <= high
            ):
                raise ValueError("The frozen original execution ceiling is invalid")
        policy = normalize_policy(original["execution_policy"])
        if (
            policy != original["execution_policy"]
            or policy["risk_limits"] != specification["risk_limits"]
            or policy["exit_mode"] != "percent"
            or policy["sizing"] != "allocation"
            or (allocation and policy.get("allocation_mode") != "original")
            or (
                priority
                and (
                    policy["version"]
                    != (
                        "research-execution-aware-v5"
                        if allocation
                        else "research-execution-aware-v4"
                        if capacity
                        else "research-execution-aware-v3"
                        if turnover
                        else "research-execution-aware-v2"
                    )
                    or policy.get("entry_priority") != "source"
                )
            )
            or (not priority and policy["version"] != "research-execution-aware-v1")
        ):
            raise ValueError("The frozen execution reference policy changed")


def axes(specification):
    _validate(specification)
    result = {
        f"{row['id']}.{name}": {"type": "categorical", "choices": [item["id"] for item in row[key]]}
        for row in specification["strategies"]
        for name, key in (
            ("execution_choice", "execution_choices"),
            ("portfolio_choice", "portfolio_choices"),
        )
    }
    if specification["version"] in (
        PRIORITY_SEARCH_VERSION,
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    ):
        result[PRIORITY_AXIS] = {"type": "categorical", "choices": list(ENTRY_PRIORITIES)}
    if specification["version"] == ALLOCATION_SEARCH_VERSION:
        result[ALLOCATION_AXIS] = {"type": "categorical", "choices": list(ALLOCATION_MODES)}
    return result


def apply_choices(strategies, params, specification):
    from research.execution_policy import normalize_policy

    _validate(specification)
    allocation = specification["version"] == ALLOCATION_SEARCH_VERSION
    capacity = specification["version"] in (CAPACITY_SEARCH_VERSION, ALLOCATION_SEARCH_VERSION)
    turnover = specification["version"] in (
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    )
    priority = specification["version"] in (
        PRIORITY_SEARCH_VERSION,
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    )
    chosen_allocation = params.get(ALLOCATION_AXIS)
    if allocation and chosen_allocation not in ALLOCATION_MODES:
        raise ValueError("Allocation mode must belong to its frozen account choices")
    chosen_priority = params.get(PRIORITY_AXIS)
    if priority and chosen_priority not in ENTRY_PRIORITIES:
        raise ValueError("Entry priority must belong to its frozen account choices")
    definitions = {row["id"]: row for row in specification["strategies"]}
    if set(definitions) != {row["id"] for row in strategies}:
        raise ValueError("Execution choices belong to different strategies")
    result = [{**row, "config": deepcopy(row["config"])} for row in strategies]
    for row in result:
        definition = definitions[row["id"]]
        execution = next(
            (
                choice
                for choice in definition["execution_choices"]
                if choice["id"] == params.get(f"{row['id']}.execution_choice")
            ),
            None,
        )
        sizing = next(
            (
                choice
                for choice in definition["portfolio_choices"]
                if choice["id"] == params.get(f"{row['id']}.portfolio_choice")
            ),
            None,
        )
        if execution is None or sizing is None:
            raise ValueError("Execution and portfolio states must belong to their frozen choices")
        original = definition["original"]
        fraction = Decimal(str(sizing["fraction"]))
        row["allocation_pct"] = float(Decimal(str(original["allocation_pct"])) * fraction)
        row["config"]["order_size_pct"] = max(
            0.01, float(Decimal(str(original["order_size_pct"])) * fraction)
        )
        row["execution_policy"] = normalize_policy(
            {
                **{key: value for key, value in execution.items() if key != "id"},
                "sizing": sizing["sizing"],
                **(
                    {"risk_budget_fraction": sizing["risk_budget_fraction"]}
                    if sizing["sizing"] == "stop_risk"
                    else {}
                ),
                "risk_limits": specification["risk_limits"],
                **({"allocation_mode": chosen_allocation} if allocation else {}),
                **(
                    {
                        key: original["execution_policy"][key]
                        for key in ("capacity_mode", "correlation_mode")
                    }
                    if capacity
                    else {}
                ),
                **(
                    {
                        "version": "research-execution-aware-v5"
                        if allocation
                        else "research-execution-aware-v4"
                        if capacity
                        else "research-execution-aware-v3"
                        if turnover
                        else "research-execution-aware-v2",
                        "entry_priority": chosen_priority,
                    }
                    if priority
                    else {}
                ),
            }
        )
        if execution["exit_mode"] == "atr":
            for key in ("target_pct", "stop_pct"):
                row["config"][key] = original[key]
    return result


def original_sizing(settings, specification):
    """Matched development control: change sizing only, never chosen exits."""
    from research.execution_policy import normalize_policy

    _validate(specification)
    definitions = {row["id"]: row["original"] for row in specification["strategies"]}
    if len(settings) != len(definitions) or {row["id"] for row in settings} != set(definitions):
        raise ValueError("The sizing control belongs to different strategies")
    result = deepcopy(settings)
    for row in result:
        original = definitions[row["id"]]
        if (
            normalize_policy(row.get("execution_policy"))["risk_limits"]
            != specification["risk_limits"]
        ):
            raise ValueError("The sizing control must retain its frozen hard risk limits")
        row["allocation_pct"] = original["allocation_pct"]
        row["config"]["order_size_pct"] = original["order_size_pct"]
        row["execution_policy"] = normalize_policy(
            {
                **{
                    key: value
                    for key, value in row["execution_policy"].items()
                    if key != "risk_budget_fraction"
                },
                "sizing": "allocation",
                **(
                    {"allocation_mode": "original"}
                    if specification["version"] == ALLOCATION_SEARCH_VERSION
                    else {}
                ),
            }
        )
    if specification["version"] in (
        PRIORITY_SEARCH_VERSION,
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    ) and (
        len({row["execution_policy"].get("entry_priority") for row in result}) != 1
        or any(
            row["execution_policy"].get("version")
            != (
                "research-execution-aware-v5"
                if specification["version"] == ALLOCATION_SEARCH_VERSION
                else "research-execution-aware-v4"
                if specification["version"] == CAPACITY_SEARCH_VERSION
                else "research-execution-aware-v3"
                if specification["version"] == TURNOVER_SEARCH_VERSION
                else "research-execution-aware-v2"
            )
            for row in result
        )
    ):
        raise ValueError("The sizing control must retain one account entry priority")
    return result


def probes(midpoint, specification):
    """Four exits and four sizing choices; all strategies receive each probe."""
    _validate(specification)
    base = dict(midpoint, hypothesis="none")
    for row in specification["strategies"]:
        base[f"{row['id']}.execution_choice"] = "percent"
        base[f"{row['id']}.portfolio_choice"] = "original"
    if specification["version"] in (
        PRIORITY_SEARCH_VERSION,
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    ):
        base[PRIORITY_AXIS] = "source"
    if specification["version"] == ALLOCATION_SEARCH_VERSION:
        base[ALLOCATION_AXIS] = "original"
    result = [
        {**base, **{f"{row['id']}.{axis}": choice["id"] for row in specification["strategies"]}}
        for axis, choices in (
            ("execution_choice", EXECUTION_CHOICES[1:]),
            ("portfolio_choice", PORTFOLIO_CHOICES[1:]),
        )
        for choice in choices
    ]
    if specification["version"] in (
        PRIORITY_SEARCH_VERSION,
        TURNOVER_SEARCH_VERSION,
        CAPACITY_SEARCH_VERSION,
        ALLOCATION_SEARCH_VERSION,
    ):
        result.extend({**base, PRIORITY_AXIS: value} for value in ENTRY_PRIORITIES[1:])
    if specification["version"] == ALLOCATION_SEARCH_VERSION:
        # Replace a redundant reduced-allocation screen; total search work is unchanged.
        replaced = next(
            i
            for i, row in enumerate(result)
            if row.get(f"{specification['strategies'][0]['id']}.portfolio_choice")
            == "allocation_50"
        )
        result[replaced] = {**base, ALLOCATION_AXIS: "prior60_cluster_inverse_vol"}
    return result


def priority_counts(trials):
    """Proposal-linked counts only; controls and final accounts are separate."""
    if (
        not isinstance(trials, list)
        or len(trials) > 68
        or any(
            not isinstance(trial, dict)
            or not isinstance(trial.get("params"), dict)
            or trial.get("params", {}).get(PRIORITY_AXIS) not in ENTRY_PRIORITIES
            or type(trial.get("reused")) is not bool
            for trial in trials
        )
    ):
        raise ValueError("Saved account entry-priority counts changed")
    result = []
    for priority in ENTRY_PRIORITIES:
        chosen = [trial for trial in trials if trial["params"][PRIORITY_AXIS] == priority]
        reused = sum(trial["reused"] for trial in chosen)
        result.append(
            {
                "id": priority,
                "proposed": len(chosen),
                "evaluated": len(chosen) - reused,
                "reused": reused,
            }
        )
    return result
