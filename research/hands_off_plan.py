"""Pure, versioned compiler from bounded AI hypotheses to existing Optuna axes.

The server supplies immutable settings, signal identity and admissible domains.
No prices, provider, calculation, validation-threshold or budget mutation lives here.
"""

from copy import deepcopy
from decimal import Decimal
from math import isfinite, prod

from research.connectors.optuna_portfolio import AXIS_BOUNDS, _axis, _values, validate_specification
from research.engine import validate_config
from research.report_contract import fingerprint, settings_identity

VERSION = "hands-off-plan-v1"
CONTEXT_VERSION = "hands-off-context-v1"
MAX_STRATEGIES = 8
MAX_AXIS_VALUES = 1001
MAX_HISTORY = 24
FIELDS = {
    "version",
    "action",
    "method",
    "hypothesis",
    "reason",
    "expected_evidence",
    "stop_rule",
    "domains",
}
EXIT_AXES = frozenset({"hold_sessions", "hold_minutes", "stop_pct", "target_pct", "trailing_pct"})
SIZE_AXES = frozenset({"order_size_pct", "allocation_pct"})
METHODS = {
    "holding_and_exits": EXIT_AXES,
    "sizing_and_allocation": SIZE_AXES,
    "joint_trading": EXIT_AXES | SIZE_AXES,
}


def _text(value, field, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Hands-off {field} needs bounded explanatory text")
    return value.strip()


def _numbers(axis, supplied):
    if axis not in AXIS_BOUNDS:
        raise ValueError("Hands-off research cannot change filters, capital, costs or risk limits")
    if isinstance(supplied, dict):
        supplied = _values(_axis(axis, supplied))
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= MAX_AXIS_VALUES:
        raise ValueError("Hands-off domains need a bounded nonempty value list")
    low, high = AXIS_BOUNDS[axis]
    if any(
        type(v) not in (int, float) or not isfinite(v) or not low <= v <= high for v in supplied
    ):
        raise ValueError("Hands-off domains contain an invalid numeric value")
    if axis in {"hold_sessions", "hold_minutes"} and any(int(v) != v for v in supplied):
        raise ValueError("Hands-off holding domains require whole periods")
    if len({Decimal(str(v)) for v in supplied}) != len(supplied):
        raise ValueError("Hands-off domains cannot repeat equivalent numbers")
    return sorted(supplied)


def _settings(settings):
    if not isinstance(settings, list) or not 1 <= len(settings) <= MAX_STRATEGIES:
        raise ValueError("Hands-off research needs one to eight complete strategies")
    if settings_identity(settings) is None:
        raise ValueError("Hands-off research needs complete strategy settings")
    ids, result = set(), []
    for row in settings:
        identity = row["id"]
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("Hands-off strategy identities must be distinct")
        ids.add(identity)
        if "entry_condition" in row:
            raise ValueError("Hands-off trading research keeps the original scan signals")
        config = validate_config(row["config"])
        if config != row["config"]:
            raise ValueError("Hands-off settings must be frozen canonical configurations")
        value = {k: deepcopy(row[k]) for k in ("id", "name", "allocation_pct", "config")}
        if "execution_policy" in row:
            value["execution_policy"] = deepcopy(row["execution_policy"])
        result.append(value)
    return result


def build_context(
    settings, domains, *, signal_id, style, risk_limits, locked_fields=None, prior_domains=None
):
    """Freeze server-authorized numeric values before asking a configured model.

    ``domains`` maps strategy IDs to axes, whose values are lists or native
    min/max/step ranges. ``locked_fields`` maps IDs to lists of additionally
    locked axes. ``prior_domains`` contains earlier complete search receipts;
    only matching base identities can establish an equivalent test.
    """
    settings = _settings(settings)
    _text(signal_id, "signal identity", 128)
    if style not in ("positional", "intraday") or not isinstance(risk_limits, dict):
        raise ValueError("Hands-off research needs frozen style and risk limits")
    if not isinstance(domains, dict) or set(domains) - {r["id"] for r in settings}:
        raise ValueError("Hands-off domains changed the original strategy cohort")
    locks = {} if locked_fields is None else deepcopy(locked_fields)
    if not isinstance(locks, dict) or set(locks) - {r["id"] for r in settings}:
        raise ValueError("Hands-off field locks changed the strategy cohort")
    canonical = {}
    for row in settings:
        identity = row["id"]
        fields = locks.get(identity, [])
        if not isinstance(fields, list) or any(not isinstance(f, str) for f in fields):
            raise ValueError("Hands-off field locks need explicit field names")
        axes = domains.get(identity, {})
        if not isinstance(axes, dict) or len(axes) > len(AXIS_BOUNDS):
            raise ValueError("Hands-off domain axes exceed supported controls")
        canonical[identity] = {}
        for axis, supplied in axes.items():
            if axis in fields:
                raise ValueError("Hands-off domain attempts to unlock a fixed user setting")
            if axis == "trailing_pct" and not row["config"]["trailing_enabled"]:
                raise ValueError("Disabled trailing controls cannot be searched")
            if axis == "hold_sessions" and (
                style == "intraday" or row["config"].get("hold_minutes") is not None
            ):
                raise ValueError("Inactive session holding cannot be searched")
            if axis == "hold_minutes" and row["config"].get("hold_minutes") is None:
                raise ValueError("Inactive minute holding cannot be searched")
            if (
                axis in ("stop_pct", "target_pct")
                and row.get("execution_policy", {}).get("exit_mode") == "atr"
            ):
                raise ValueError("Percentage exits do not control an ATR configuration")
            canonical[identity][axis] = _numbers(axis, supplied)
        if row.get("execution_policy", {}).get("risk_limits", risk_limits) != risk_limits:
            raise ValueError("Hands-off research cannot replace the account risk limits")
    prior = [] if prior_domains is None else deepcopy(prior_domains)
    if not isinstance(prior, list) or len(prior) > MAX_HISTORY:
        raise ValueError("Hands-off planning history exceeds its bound")
    base = {
        "engine": "vectorbt",
        "signal_id": signal_id,
        "style": style,
        "settings": settings,
        "risk_limits": deepcopy(risk_limits),
    }
    body = {
        "version": CONTEXT_VERSION,
        **base,
        "base_id": fingerprint(base),
        "domains": canonical,
        "locked_fields": locks,
        "prior_domains": prior,
        "budget": {"proposals": 408, "max_simulations": 512},
    }
    return {**body, "id": fingerprint(body)}


def _verify(context):
    if not isinstance(context, dict) or context.get("version") != CONTEXT_VERSION:
        raise ValueError("Unsupported hands-off planning context")
    supplied = {k: v for k, v in context.items() if k != "id"}
    if context.get("id") != fingerprint(supplied):
        raise ValueError("Hands-off planning context changed")
    rebuilt = build_context(
        context["settings"],
        context["domains"],
        signal_id=context["signal_id"],
        style=context["style"],
        risk_limits=context["risk_limits"],
        locked_fields=context["locked_fields"],
        prior_domains=context["prior_domains"],
    )
    if rebuilt != context:
        raise ValueError("Hands-off planning context is not canonical")


def compile_plan(value, context):
    """Compile one model proposal; caller owns accounting, execution and validation."""
    _verify(context)
    if not isinstance(value, dict) or set(value) != FIELDS or value.get("version") != VERSION:
        raise ValueError("Hands-off AI must return the exact versioned plan schema")
    if value["action"] not in ("search", "defer", "stop"):
        raise ValueError("Hands-off AI can search, defer or stop only")
    result = {
        key: _text(value[key], key)
        for key in ("hypothesis", "reason", "expected_evidence", "stop_rule")
    }
    domains = value["domains"]
    if not isinstance(domains, dict):
        raise ValueError("Hands-off search domains must be an object")
    if value["action"] in ("stop", "defer"):
        if domains or value["method"] is not None:
            raise ValueError("A hands-off non-search decision cannot execute a method or search")
        body = {
            "version": VERSION,
            "action": value["action"],
            "decision_scope": "whole_study" if value["action"] == "stop" else "training_block",
            "method": None,
            **result,
            "domains": {},
            "context_id": context["id"],
            "base_id": context["base_id"],
        }
        return {**body, "id": fingerprint(body)}
    method = value["method"]
    if not isinstance(method, str) or method not in METHODS or not domains:
        raise ValueError("Choose a registered hands-off research family")
    settings = deepcopy(context["settings"])
    by_id = {r["id"]: r for r in settings}
    canonical, axes = {}, []
    if set(domains) - set(by_id):
        raise ValueError("AI search changed the original strategy cohort")
    for identity in sorted(domains):
        proposed = domains[identity]
        if not isinstance(proposed, dict) or not proposed:
            raise ValueError("Each planned strategy needs supported numerical axes")
        canonical[identity] = {}
        for axis in sorted(proposed):
            if axis not in METHODS[method] or axis not in context["domains"][identity]:
                raise ValueError("AI search exceeds the permitted or unlocked research family")
            checked = _axis(axis, proposed[axis])
            values = _values(checked)
            allowed = {Decimal(str(x)) for x in context["domains"][identity][axis]}
            if len(values) > MAX_AXIS_VALUES or any(Decimal(str(v)) not in allowed for v in values):
                raise ValueError("AI proposed a value outside the frozen data/search domain")
            current = (
                by_id[identity]["allocation_pct"]
                if axis == "allocation_pct"
                else by_id[identity]["config"][axis]
            )
            if all(Decimal(str(v)) == Decimal(str(current)) for v in values):
                raise ValueError("AI search must compare distinct meaningful settings")
            canonical[identity][axis] = checked
            axes.append(values)
        by_id[identity]["search"] = deepcopy(canonical[identity])
    if prod(map(len, axes)) < 2:
        raise ValueError("AI search must compare distinct meaningful settings")
    for row in settings:
        row.setdefault("search", {})
    validate_specification(
        {"sampler": "tpe", "trials": 408, "objective": "balanced", "seed": 0}, settings
    )
    search_id = fingerprint({"base_id": context["base_id"], "domains": canonical})
    for previous in context["prior_domains"]:
        if not isinstance(previous, dict):
            raise ValueError("Hands-off prior experiment receipt is malformed")
        if (
            previous.get("base_id") == context["base_id"]
            and previous.get("search_identity") == search_id
            and previous.get("exhausted") is True
        ):
            raise ValueError("This complete hands-off search domain was exhausted")
        if (
            previous.get("base_id") == context["base_id"]
            and previous.get("no_effect_proven") is True
        ):
            old = previous.get("domains", {})
            if (
                old
                and set(old) == set(canonical)
                and all(
                    set(old[sid]) == set(canonical[sid])
                    and all(
                        set(map(Decimal, map(str, _values(_axis(axis, canonical[sid][axis])))))
                        <= set(map(Decimal, map(str, _values(_axis(axis, old[sid][axis])))))
                        for axis in canonical[sid]
                    )
                    for sid in canonical
                )
            ):
                raise ValueError("Saved feedback proves this complete search domain had no effect")
    body = {
        "version": VERSION,
        "action": "search",
        "decision_scope": "training_block",
        "method": method,
        **result,
        "domains": canonical,
        "context_id": context["id"],
        "base_id": context["base_id"],
        "search_identity": search_id,
        "strategies": settings,
        "budget": deepcopy(context["budget"]),
        "validation": "unchanged_native_checks",
    }
    return {**body, "id": fingerprint(body)}
