"""Canonical opt-in execution controls; legacy strategies acquire no defaults."""

from math import isfinite

VERSION = "research-execution-aware-v1"
RISK_VERSION = "research-risk-limits-v1"
EXECUTION_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v2"
POLICY_VERSION = "vectorbt-joint-causal-bars-v2"
PRIORITY_VERSION = "research-execution-aware-v2"
PRIORITY_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v3"
PRIORITY_POLICY_VERSION = "vectorbt-joint-causal-bars-v3"
TURNOVER_VERSION = "research-execution-aware-v3"
TURNOVER_RISK_VERSION = "research-risk-limits-v2"
TURNOVER_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v4"
TURNOVER_POLICY_VERSION = "vectorbt-joint-causal-bars-v4"
CAPACITY_VERSION = "research-execution-aware-v4"
CAPACITY_RISK_VERSION = "research-risk-limits-v3"
CAPACITY_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v5"
CAPACITY_POLICY_VERSION = "vectorbt-joint-causal-bars-v5"
ALLOCATION_VERSION = "research-execution-aware-v5"
ALLOCATION_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v6"
ALLOCATION_POLICY_VERSION = "vectorbt-joint-causal-bars-v6"
ENTRY_PRIORITIES = ("source", "reversed_source", "lower_stop_risk")


def _number(value, name, minimum, maximum, *, integer=False, exclusive_min=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < minimum
        or (exclusive_min and value == minimum)
        or value > maximum
        or (integer and int(value) != value)
    ):
        raise ValueError(f"Execution {name} is outside its supported bounds")
    return int(value) if integer else float(value)


def normalize_limits(raw=None):
    defaults = {
        "version": RISK_VERSION,
        "max_gross_exposure_pct": 100.0,
        "max_symbol_exposure_pct": 25.0,
        "max_open_positions": 10,
        "max_trade_risk_pct": 1.0,
        "max_total_stop_risk_pct": 5.0,
    }
    if raw is None:
        return defaults
    if isinstance(raw, dict) and raw.get("version") in (
        TURNOVER_RISK_VERSION,
        CAPACITY_RISK_VERSION,
    ):
        defaults.update(version=raw["version"], max_daily_buy_turnover_pct=100.0)
        if raw["version"] == CAPACITY_RISK_VERSION:
            defaults.update(
                max_daily_volume_participation_pct=1.0, max_correlated_exposure_pct=25.0
            )
    if (
        not isinstance(raw, dict)
        or set(raw) - set(defaults)
        or raw.get("version", RISK_VERSION) != defaults["version"]
    ):
        raise ValueError("Choose supported versioned execution risk limits")
    result = {**defaults, **raw}
    for name in result.keys() - {
        "version",
        "max_open_positions",
        "max_daily_buy_turnover_pct",
        "max_daily_volume_participation_pct",
    }:
        result[name] = _number(result[name], name, 0, 100, exclusive_min=True)
    if "max_daily_buy_turnover_pct" in result:
        result["max_daily_buy_turnover_pct"] = _number(
            result["max_daily_buy_turnover_pct"],
            "max_daily_buy_turnover_pct",
            0,
            1000,
            exclusive_min=True,
        )
    if "max_daily_volume_participation_pct" in result:
        result["max_daily_volume_participation_pct"] = _number(
            result["max_daily_volume_participation_pct"],
            "max_daily_volume_participation_pct",
            0,
            10,
            exclusive_min=True,
        )
    result["max_open_positions"] = _number(
        result["max_open_positions"], "max_open_positions", 1, 200, integer=True
    )
    return result


def normalize_policy(raw=None):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Choose a supported execution policy")
    version = raw.get("version", VERSION)
    allowed = {
        "version",
        "exit_mode",
        "sizing",
        "risk_limits",
        "atr_period",
        "atr_stop_multiple",
        "atr_target_multiple",
        "risk_budget_fraction",
    }
    if version == ALLOCATION_VERSION:
        mode = raw.get("allocation_mode")
        if mode not in (
            "original",
            "prior60_cluster_inverse_vol",
            "active_prior60_cluster_inverse_vol",
        ):
            raise ValueError("Choose a supported portfolio allocation mode")
    if version in (PRIORITY_VERSION, TURNOVER_VERSION, CAPACITY_VERSION, ALLOCATION_VERSION):
        allowed.add("entry_priority")
    if version in (CAPACITY_VERSION, ALLOCATION_VERSION):
        allowed.update(("capacity_mode", "correlation_mode"))
    if version == ALLOCATION_VERSION:
        allowed.add("allocation_mode")
    if set(raw) - allowed or version not in (
        VERSION,
        PRIORITY_VERSION,
        TURNOVER_VERSION,
        CAPACITY_VERSION,
        ALLOCATION_VERSION,
    ):
        raise ValueError("Choose a supported execution policy")
    exit_mode, sizing = raw.get("exit_mode", "percent"), raw.get("sizing", "allocation")
    if exit_mode not in ("percent", "atr") or sizing not in ("allocation", "stop_risk"):
        raise ValueError("Choose percentage or ATR exits and allocation or stop-risk sizing")
    raw_limits = raw.get("risk_limits")
    if version in (TURNOVER_VERSION, CAPACITY_VERSION, ALLOCATION_VERSION) and raw_limits is None:
        raw_limits = {
            "version": CAPACITY_RISK_VERSION
            if version in (CAPACITY_VERSION, ALLOCATION_VERSION)
            else TURNOVER_RISK_VERSION
        }
    limits = normalize_limits(raw_limits)
    expected_limits = {
        TURNOVER_VERSION: TURNOVER_RISK_VERSION,
        CAPACITY_VERSION: CAPACITY_RISK_VERSION,
        ALLOCATION_VERSION: CAPACITY_RISK_VERSION,
    }.get(version, RISK_VERSION)
    if limits["version"] != expected_limits:
        raise ValueError("Execution policy and risk-limit versions must match")
    result = {
        "version": version,
        "exit_mode": exit_mode,
        "sizing": sizing,
        "risk_limits": limits,
    }
    if version == ALLOCATION_VERSION:
        result["allocation_mode"] = mode
    if version in (CAPACITY_VERSION, ALLOCATION_VERSION):
        capacity = raw.get("capacity_mode")
        correlation = raw.get("correlation_mode")
        if capacity not in ("prior20_broker_reported", "disabled") or correlation not in (
            "prior60_positive_080",
            "disabled",
        ):
            raise ValueError(
                "Capacity-aware execution requires explicit supported capacity and correlation modes"
            )
        if capacity == correlation == "disabled":
            raise ValueError(
                "Capacity-aware execution must enable capacity or correlation controls"
            )
        result.update(capacity_mode=capacity, correlation_mode=correlation)
    if version in (PRIORITY_VERSION, TURNOVER_VERSION, CAPACITY_VERSION, ALLOCATION_VERSION):
        priority = raw.get("entry_priority", "source")
        if priority not in ENTRY_PRIORITIES:
            raise ValueError("Choose a supported same-opening entry priority")
        result["entry_priority"] = priority
    if sizing == "stop_risk":
        result["risk_budget_fraction"] = _number(
            raw.get("risk_budget_fraction", 0.5), "risk_budget_fraction", 0, 1, exclusive_min=True
        )
    elif "risk_budget_fraction" in raw:
        raise ValueError("Allocation sizing does not accept a stop-risk budget fraction")
    if exit_mode == "percent":
        if set(raw) & {"atr_period", "atr_stop_multiple", "atr_target_multiple"}:
            raise ValueError("Percentage exits do not accept ATR settings")
        return result
    if raw.get("atr_period", 14) != 14 or isinstance(raw.get("atr_period", 14), bool):
        raise ValueError("Execution ATR requires the pinned period14")
    result.update(
        atr_period=14,
        atr_stop_multiple=_number(raw.get("atr_stop_multiple", 2), "atr_stop_multiple", 1, 3),
        atr_target_multiple=_number(raw.get("atr_target_multiple", 3), "atr_target_multiple", 1, 6),
    )
    return result
