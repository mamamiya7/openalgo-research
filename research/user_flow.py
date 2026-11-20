"""Reviewed user authority, separate from financial validation and diagnostics."""

from copy import deepcopy

VERSION = "research-user-flow-v1"
FIELDS = (
    "capital",
    "order_size_pct",
    "hold_sessions",
    "stop_pct",
    "target_pct",
    "trailing_pct",
    "allocation_pct",
)


def normalize(value=None):
    if value is None:
        value = {
            "version": VERSION,
            "intent": "improve",
            "involvement": "hands_off",
            "adjustable": list(FIELDS),
        }
    if not isinstance(value, dict) or set(value) != {
        "version",
        "intent",
        "involvement",
        "adjustable",
    }:
        raise ValueError("Choose a supported research flow")
    if (
        value["version"] != VERSION
        or value["intent"] not in ("replay", "improve")
        or value["involvement"] not in ("hands_off", "guided", "hands_on")
    ):
        raise ValueError("Choose a supported research intent and involvement")
    fields = value["adjustable"]
    if (
        not isinstance(fields, list)
        or any(not isinstance(k, str) or k not in FIELDS for k in fields)
        or len(set(fields)) != len(fields)
    ):
        raise ValueError("Choose supported adjustable trading settings")
    if value["intent"] == "improve" and value["involvement"] == "hands_on":
        raise ValueError("Hands-on runs your exact settings; choose Replay or Guided research")
    return {
        "version": VERSION,
        "intent": value["intent"],
        "involvement": value["involvement"],
        "adjustable": [k for k in FIELDS if k in fields] if value["intent"] == "improve" else [],
    }


def enforce(settings, baseline, flow):
    """Apply locks to research candidates. Validation stress accounts stay untouched."""
    allowed = set(normalize(flow)["adjustable"])
    original = {r["id"]: r for r in baseline}
    if [r["id"] for r in settings] != [r["id"] for r in baseline]:
        raise ValueError("Research cannot change the imported strategy cohort")
    result = deepcopy(settings)
    for row in result:
        source = original[row["id"]]
        cfg, prior = row["config"], source["config"]
        policy, previous = row.get("execution_policy", {}), source.get("execution_policy", {})
        for field in ("order_size_pct", "stop_pct", "target_pct"):
            if field not in allowed:
                cfg[field] = prior[field]
        config_locks = {
            "capital": ("initial_capital",),
            "hold_sessions": (
                "hold_sessions",
                "hold_minutes",
                "trade_horizon",
                "entry_time",
                "exit_time",
            ),
            "trailing_pct": ("trailing_pct", "trailing_enabled"),
        }
        policy_locks = {
            "order_size_pct": ("sizing", "risk_budget_fraction"),
            "stop_pct": ("exit_mode", "atr_period", "atr_stop_multiple"),
            "target_pct": ("exit_mode", "atr_period", "atr_target_multiple"),
            "allocation_pct": ("allocation_mode",),
        }
        for field, keys in config_locks.items():
            if field not in allowed:
                for key in keys:
                    if key in prior:
                        cfg[key] = deepcopy(prior[key])
                    else:
                        cfg.pop(key, None)
        for field, keys in policy_locks.items():
            if field not in allowed:
                for key in keys:
                    if key in previous:
                        policy[key] = deepcopy(previous[key])
                    else:
                        policy.pop(key, None)
        if "allocation_pct" not in allowed:
            row["allocation_pct"] = source["allocation_pct"]
            for key in ("entry_priority", "priority_seed"):
                if key in prior:
                    cfg[key] = deepcopy(prior[key])
            if "entry_priority" in previous:
                policy["entry_priority"] = previous["entry_priority"]
        # Hard boundaries are never AI-editable, independently of toggles.
        for key in ("cost_bps", "slippage_bps"):
            cfg[key] = prior[key]
        if "execution_policy" in source:
            policy["risk_limits"] = deepcopy(previous["risk_limits"])
            for key in ("capacity_mode", "correlation_mode"):
                if key in previous:
                    policy[key] = previous[key]
            row["execution_policy"] = policy
    return result


def permitted(settings, baseline, flow):
    return settings == enforce(settings, baseline, flow)


def locked_axes(settings, flow):
    allowed = set(normalize(flow)["adjustable"])
    return {
        r["id"]: [k for k in FIELDS if k not in allowed and k != "capital"]
        + (["hold_minutes"] if "hold_sessions" not in allowed else [])
        for r in settings
    }
