"""Pure, versioned conditional trailing states for automatic VectorBT research.

Off is one state, not a distance grid with an unused parameter. The untouched
baseline is evaluated separately; its dormant distance remains exact evidence.
"""

from copy import deepcopy
from math import prod

from research.connectors.optuna_portfolio import _values
from research.report_contract import settings_identity

VERSION = "automatic-trailing-states-v1"


def strategy_domain(config):
    """Bound positive distances around the saved distance, or the stop distance."""
    from research.automatic_protocol import _range

    return _range(config["trailing_pct"] or config["stop_pct"], 99)


def descriptor(strategies):
    return {
        "version": VERSION,
        "strategies": [
            {
                "id": row["id"],
                "domain": strategy_domain(row["config"]),
                "choices": [0.0, *_values(strategy_domain(row["config"]))],
            }
            for row in strategies
        ],
        "disabled": "trailing_enabled=false; trailing_pct=0; original baseline retained separately",
        "screening": "each condition family sees off, low enabled and high enabled before refinement",
        "stability": "activation toggles and adjacent active distances; bounded deterministic subset",
    }


def recipe(strategies):
    """Public frozen planning contract shared by preflight and execution."""
    return descriptor(strategies)


def axes(specification):
    return {
        f"{row['id']}.trailing_choice": {"type": "categorical", "choices": row["choices"]}
        for row in specification["strategies"]
    }


def apply_choices(strategies, params, specification):
    """Materialize one valid enabled/distance state per complete portfolio."""
    domains = {row["id"]: row for row in specification["strategies"]}
    # Source signals remain read-only, as in the shared Optuna adapter. Only
    # mutable settings need copying before the causal gate binds the proposal.
    result = [{**row, "config": deepcopy(row["config"])} for row in strategies]
    for row in result:
        value = params[f"{row['id']}.trailing_choice"]
        if isinstance(value, bool) or value not in domains[row["id"]]["choices"]:
            raise ValueError("Automatic trailing state must belong to its frozen choices")
        row["config"].update(trailing_enabled=value > 0, trailing_pct=float(value))
    return result


def grid_size(numeric_axes, specification):
    return prod(len(_values(domain)) for domain in numeric_axes.values()) * prod(
        len(row["choices"]) for row in specification["strategies"]
    )


def stability_neighbors(settings, ranges, specification):
    """Challenge activation first; disabled distances never create duplicates."""
    from research.automatic_stability import MAX_NEIGHBORS, neighbors

    domains = {row["id"]: row for row in specification["strategies"]}
    numeric = neighbors(settings, ranges)
    distances = []
    for direction in (-1, 1):
        for index, row in enumerate(settings):
            cfg = row["config"]
            if not cfg["trailing_enabled"]:
                continue
            choices = domains[row["id"]]["choices"][1:]
            if cfg["trailing_pct"] not in choices:
                raise ValueError("Stability settings must lie on their frozen trailing grid")
            coordinate = choices.index(cfg["trailing_pct"]) + direction
            if not 0 <= coordinate < len(choices):
                continue
            changed = deepcopy(settings)
            changed[index]["config"]["trailing_pct"] = choices[coordinate]
            distances.append(
                {
                    "config_id": settings_identity(changed),
                    "strategy_id": row["id"],
                    "axis": "trailing_pct",
                    "from": cfg["trailing_pct"],
                    "to": choices[coordinate],
                    "strategies": changed,
                }
            )
    toggles = []
    for index, row in enumerate(settings):
        cfg = row["config"]
        changed = deepcopy(settings)
        distance = 0.0 if cfg["trailing_enabled"] else domains[row["id"]]["choices"][1]
        changed[index]["config"].update(trailing_enabled=distance > 0, trailing_pct=distance)
        toggles.append(
            {
                "config_id": settings_identity(changed),
                "strategy_id": row["id"],
                "axis": "trailing_enabled",
                "from": cfg["trailing_enabled"],
                "to": distance > 0,
                "strategies": changed,
            }
        )
    # Challenge activation, an active distance, and another exit before repeating
    # a dimension. Generic neighbor truncation cannot hide trailing on an 8-leg book.
    selected = []
    for index in range(max(len(toggles), len(distances), len(numeric["neighbors"]))):
        for candidates in (toggles, distances, numeric["neighbors"]):
            if index < len(candidates):
                selected.append(candidates[index])
    available = len(toggles) + len(distances) + numeric["available"]
    return {
        "neighbors": selected[:MAX_NEIGHBORS],
        "available": available,
        "truncated": available > MAX_NEIGHBORS,
    }
