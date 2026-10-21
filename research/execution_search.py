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
        "version": VERSION,
        "risk_limits": limits,
        "strategies": originals,
        "atr": "Wilder ATR14 from prior observed daily OHLC at each recorded native entry; missing ATR excludes the common source opportunity",
        "ceilings": "Allocation and order size never exceed each original declaration; user account, symbol, position and stop-risk caps remain fixed",
        "stop_risk_target": "One half of the fixed user trade-risk ceiling, further constrained by original cash ceilings and all portfolio hard limits; never a loss guarantee",
        "inactive_axes": "ATR exits restore original percentage target and stop, so ignored numeric choices share one account identity",
        "sizing_control": "For each training-fit winner, retain filters and exits but restore original allocation, order size and allocation sizing under the same hard limits; development only",
        "screening": "Four registered ATR probes and four registered portfolio probes per independent fit, then joint refinement",
    }
    return {**value, "id": fingerprint(value)}


def _validate(specification):
    from research.execution_policy import normalize_limits, normalize_policy

    if (
        not isinstance(specification, dict)
        or specification.get("version") != VERSION
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
        ):
            raise ValueError("The frozen execution reference policy changed")


def axes(specification):
    _validate(specification)
    return {
        f"{row['id']}.{name}": {"type": "categorical", "choices": [item["id"] for item in row[key]]}
        for row in specification["strategies"]
        for name, key in (
            ("execution_choice", "execution_choices"),
            ("portfolio_choice", "portfolio_choices"),
        )
    }


def apply_choices(strategies, params, specification):
    from research.execution_policy import normalize_policy

    _validate(specification)
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
            }
        )
    return result


def probes(midpoint, specification):
    """Four exits and four sizing choices; all strategies receive each probe."""
    _validate(specification)
    base = dict(midpoint, hypothesis="none")
    for row in specification["strategies"]:
        base[f"{row['id']}.execution_choice"] = "percent"
        base[f"{row['id']}.portfolio_choice"] = "original"
    return [
        {**base, **{f"{row['id']}.{axis}": choice["id"] for row in specification["strategies"]}}
        for axis, choices in (
            ("execution_choice", EXECUTION_CHOICES[1:]),
            ("portfolio_choice", PORTFOLIO_CHOICES[1:]),
        )
        for choice in choices
    ]
