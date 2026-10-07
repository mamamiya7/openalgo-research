"""Capability routing over existing research jobs and immutable market inputs.

Package metadata is safe in the web process. Numerical engines are imported only
by their adapters when the external worker calculates a result.
"""

from importlib import metadata

from research.engine import policy_for_snapshot, validate_config
from research.experiments import grid_dimensions
from research.experiments import validate_specification as legacy_specification

CONTRACT_VERSION = "openalgo-research-connectors-v1"
TESTED_PACKAGES = {"vectorbt": "0.28.5", "optuna": "5.0.0"}
EXECUTION_FIELDS = {
    "engine",
    "optimizer",
    "contract_version",
    "engine_version",
    "adapter_version",
    "optimizer_version",
    "optimizer_adapter_version",
}


def package_status(name):
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        installed = None
    tested = TESTED_PACKAGES[name]
    return {
        "installed_version": installed,
        "tested_version": tested,
        "available": installed == tested,
    }


def catalog():
    return {
        "contract_version": CONTRACT_VERSION,
        "engines": [
            {
                "id": "scanner",
                "name": "Scanner",
                "available": True,
                "intervals": ["D", "1m"],
                "markets": ["NSE"],
                "input_types": ["scanner_signals"],
                "job_kinds": ["backtest", "optimize", "sensitivity", "research"],
                "account": "long_cash",
                "shared_cash": True,
                "margin": False,
            },
            {
                "id": "vectorbt",
                "name": "VectorBT",
                **package_status("vectorbt"),
                "intervals": ["D"],
                "markets": ["NSE"],
                "input_types": ["scanner_signals"],
                "job_kinds": ["backtest", "optimize"],
                "account": "long_cash",
                "shared_cash": True,
                "margin": False,
                "scanner_modes": ["Bypass"],
                "entry_priorities": ["csv"],
                "execution_note": "Daily signals; VectorBT execution rules apply.",
            },
            {
                "id": "nautilus",
                "name": "NautilusTrader",
                "available": False,
                "reason": "Connector not implemented",
                "job_kinds": [],
            },
        ],
        "optimizers": [
            {"id": "native", "name": "Existing search", "available": True, "engines": ["scanner"]},
            {
                "id": "optuna",
                "name": "Optuna",
                **package_status("optuna"),
                "engines": ["scanner", "vectorbt"],
                "samplers": ["grid"],
            },
        ],
    }


def split_specification(spec):
    if not isinstance(spec, dict):
        raise ValueError("Research specification must be an object")
    return spec.get("execution"), {key: value for key, value in spec.items() if key != "execution"}


def _require_package(name):
    state = package_status(name)
    if not state["available"]:
        raise ValueError(
            f"{name} connector requires {name} {state['tested_version']} in the research runtime"
        )
    return state["installed_version"]


def validate_capabilities(kind, spec, config, signals=None):
    """Pin installed adapters and reject unsupported requests before acquisition.

    An absent execution field retains the exact legacy request identity. Auto
    preserves the scanner's recorded fill policy: switching to VectorBT's
    different execution rules requires choosing that engine explicitly.
    """
    execution, body = split_specification(spec)
    if execution is None:
        if "execution" in spec:
            raise ValueError("Execution selection must be an object")
        return spec
    if not isinstance(execution, dict) or set(execution) - EXECUTION_FIELDS:
        raise ValueError("Unknown research connector setting")
    engine = execution.get("engine", "auto")
    if engine == "auto":
        engine = "scanner"
    if engine not in ("scanner", "vectorbt"):
        raise ValueError("Choose an available research engine; NautilusTrader is not connected yet")
    if kind not in ("backtest", "optimize"):
        raise ValueError("Connector runs currently support Backtest and Optimize")
    optimizer = execution.get(
        "optimizer", "optuna" if engine == "vectorbt" and kind == "optimize" else "native"
    )
    if optimizer not in ("native", "optuna") or (kind != "optimize" and optimizer != "native"):
        raise ValueError("Choose a supported optimizer for an Optimize run")
    if engine == "vectorbt" and kind == "optimize" and optimizer != "optuna":
        raise ValueError("VectorBT optimization requires the Optuna connector")
    cfg = validate_config(config)
    pinned = {"engine": engine, "optimizer": optimizer, "contract_version": CONTRACT_VERSION}
    if engine == "vectorbt":
        from .vectorbt_adapter import ADAPTER_VERSION
        from .vectorbt_adapter import validate_config as validate_vectorbt

        validate_vectorbt(cfg, signals=signals)
        pinned.update(engine_version=_require_package("vectorbt"), adapter_version=ADAPTER_VERSION)
    else:
        from research.engine import POLICY_VERSION
        from research.intraday import POLICY_VERSION as MINUTE_POLICY_VERSION

        pinned.update(
            engine_version=f"{POLICY_VERSION}+{MINUTE_POLICY_VERSION}",
            adapter_version="scanner-bridge-v1",
        )
    if optimizer == "optuna":
        from .optuna_adapter import ADAPTER_VERSION, validate_specification

        pinned.update(
            optimizer_version=_require_package("optuna"), optimizer_adapter_version=ADAPTER_VERSION
        )
        body = validate_specification(body, cfg)
    elif kind == "optimize":
        from research.experiments import validate_search

        body = validate_search(body, cfg)
    elif body:
        raise ValueError("Backtest accepts only its execution selection")
    if engine == "vectorbt" and kind == "optimize":
        # Validate the union of allowed settings, not only the first trial.
        from .vectorbt_adapter import validate_config as validate_vectorbt

        dimensions = grid_dimensions(body)
        for modes in dimensions[4]:
            validate_vectorbt({**cfg, "modes": modes}, signals=signals)
        for pct, enabled in dimensions[3]:
            validate_vectorbt(
                {**cfg, "trailing_pct": pct, "trailing_enabled": enabled}, signals=signals
            )
        if body.get("hold_axis", "hold_sessions") != "hold_sessions":
            raise ValueError("VectorBT connector requires daily holding sessions")
    if (
        engine == "scanner"
        and optimizer == "native"
        and not set(execution) - {"engine", "optimizer"}
    ):
        # Preserve native continuation and saved identities through their
        # existing worker path, including when the UI explicitly chooses Auto.
        return body
    for key, value in execution.items():
        if key in ("engine", "optimizer"):
            continue
        if pinned.get(key) != value:
            raise ValueError(
                "Research connector version changed; review a new run before continuing"
            )
    return {**body, "execution": pinned}


def validate_specification(kind, spec, config, evidence):
    canonical = validate_capabilities(kind, spec, config, signals=evidence["signals"])
    execution, body = split_specification(canonical)
    if execution is None:
        return legacy_specification(kind, body, config, evidence)
    if execution["engine"] == "vectorbt":
        from .vectorbt_adapter import validate

        cfg = validate_config(config)
        if kind == "optimize":
            from research.experiments import axis_values

            cfg = {**cfg, "hold_sessions": int(max(axis_values(body["axes"]["hold_sessions"])))}
        validate(evidence["signals"], evidence["snapshot"], cfg)
    return canonical


def policy_for_request(snapshot, spec):
    if isinstance(spec, dict) and "portfolio" in spec:
        if spec["portfolio"].get("engine") == "nautilus":
            from research.connectors.nautilus_portfolio import POLICY_VERSION

            return POLICY_VERSION
        from .vectorbt_portfolio import POLICY_VERSION

        return POLICY_VERSION
    execution = spec.get("execution") if isinstance(spec, dict) else None
    if execution and execution.get("engine") == "vectorbt":
        from .vectorbt_adapter import POLICY_VERSION

        return POLICY_VERSION
    return policy_for_snapshot(snapshot)


def validate_input_scope(signals, sessions, spec):
    """Check computable adapter limits as soon as the calendar is available."""
    execution, _ = split_specification(spec)
    if execution and execution.get("engine") == "vectorbt":
        from .vectorbt_adapter import MAX_MATRIX_CELLS

        if len(signals) * len(sessions) > MAX_MATRIX_CELLS:
            raise ValueError("VectorBT connector matrix limit reached; use a smaller signal sample")


def evaluate(signals, snapshot, config, *, execution, progress=None):
    if execution["engine"] == "vectorbt":
        from .vectorbt_adapter import evaluate as calculate
    elif execution["engine"] == "scanner":
        from research.engine import evaluate as calculate
    else:
        raise ValueError("Unknown research engine")
    result = calculate(signals, snapshot, config, progress=progress)
    actual = result.get("execution", {})
    if (
        execution["engine"] == "vectorbt"
        and actual.get("engine_version") != execution["engine_version"]
    ):
        raise ValueError("Loaded engine version does not match this run's recorded version")
    result["execution"] = {**actual, **execution}
    return result


def run(signals, snapshot, config, kind, spec, *, progress=None, checkpoint=None, saved=None):
    canonical = validate_specification(
        kind, spec, config, {"signals": signals, "snapshot": snapshot}
    )
    execution, body = split_specification(canonical)
    if execution is None:
        raise ValueError("A connector run needs a resolved execution selection")

    def calculate(own_signals, own_snapshot, own_config, progress=None):
        return evaluate(
            own_signals, own_snapshot, own_config, execution=execution, progress=progress
        )

    if kind == "optimize":
        if execution["optimizer"] == "optuna":
            from .optuna_adapter import run_search

            return run_search(
                signals,
                snapshot,
                config,
                body,
                evaluate=calculate,
                execution=execution,
                progress=progress,
                checkpoint=checkpoint,
                saved=saved,
            )
        from research.experiments import run_search

        result = run_search(
            signals, snapshot, config, body, progress=progress, checkpoint=checkpoint, saved=saved
        )
        result["execution"] = dict(execution)
        return result
    return calculate(signals, snapshot, config, progress=progress)
