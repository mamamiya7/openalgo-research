"""Canonical opt-in execution controls; legacy strategies acquire no defaults."""

from math import isfinite

VERSION = "research-execution-aware-v1"
RISK_VERSION = "research-risk-limits-v1"
EXECUTION_ADAPTER_VERSION = "vectorbt-portfolio-adapter-v2"
POLICY_VERSION = "vectorbt-joint-causal-bars-v2"


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
    if (
        not isinstance(raw, dict)
        or set(raw) - set(defaults)
        or raw.get("version", RISK_VERSION) != RISK_VERSION
    ):
        raise ValueError("Choose supported versioned execution risk limits")
    result = {**defaults, **raw}
    for name in result.keys() - {"version", "max_open_positions"}:
        result[name] = _number(result[name], name, 0, 100, exclusive_min=True)
    result["max_open_positions"] = _number(
        result["max_open_positions"], "max_open_positions", 1, 200, integer=True
    )
    return result


def normalize_policy(raw=None):
    if raw is None:
        raw = {}
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
    if not isinstance(raw, dict) or set(raw) - allowed or raw.get("version", VERSION) != VERSION:
        raise ValueError("Choose a supported execution policy")
    exit_mode, sizing = raw.get("exit_mode", "percent"), raw.get("sizing", "allocation")
    if exit_mode not in ("percent", "atr") or sizing not in ("allocation", "stop_risk"):
        raise ValueError("Choose percentage or ATR exits and allocation or stop-risk sizing")
    result = {
        "version": VERSION,
        "exit_mode": exit_mode,
        "sizing": sizing,
        "risk_limits": normalize_limits(raw.get("risk_limits")),
    }
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
