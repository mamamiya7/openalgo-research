"""Lossless bounded configuration comparisons for research decisions; no I/O."""

from copy import deepcopy

from research.report_contract import settings_identity

VERSION = "research-configuration-context-v1"


def difference(before, after, path=()):
    """Complete add/remove/replace operations, including previously inactive fields."""
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            location = [*path, key]
            if key not in after:
                result.append({"op": "remove", "path": location})
            elif key not in before:
                result.append({"op": "add", "path": location, "value": deepcopy(after[key])})
            else:
                result.extend(difference(before[key], after[key], tuple(location)))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            change
            for index, (first, second) in enumerate(zip(before, after, strict=True))
            for change in difference(first, second, (*path, index))
        ]
    return (
        []
        if type(before) is type(after) and before == after
        else [{"op": "replace", "path": list(path), "value": deepcopy(after)}]
    )


def build(current_settings, history, options):
    """One complete base plus lossless patches, not disconnected axis values.

    Every identity is the exact native settings identity; semantic-equivalence
    identities are separate and never replace native financial bindings.
    """
    if not isinstance(current_settings, list) or not 1 <= len(current_settings) <= 8:
        raise ValueError("Decision comparisons require complete current settings")
    if len(history) > 24 or len(options) > 8:
        raise ValueError("Configuration comparison exceeds its retained bound")
    base_id = settings_identity(current_settings)
    configurations = {base_id: {"settings": deepcopy(current_settings)}}

    def register(settings):
        identifier = settings_identity(settings)
        if identifier not in configurations:
            configurations[identifier] = {
                "base": base_id,
                "operations": difference(current_settings, settings),
            }
        return identifier

    previous = []
    for record in history:
        settings = record["settings"]
        previous.append(
            {
                **{k: deepcopy(v) for k, v in record.items() if k != "settings"},
                "configuration_id": register(settings),
            }
        )
    candidates = []
    for option in options:
        settings = option["settings"]
        closest = sorted(
            (
                (len(difference(record["settings"], settings)), index, record)
                for index, record in enumerate(history)
            ),
            key=lambda item: (item[0], item[1]),
        )[:2]
        candidates.append(
            {
                "candidate_id": option["id"],
                "configuration_id": register(settings),
                "closest_previous": [
                    {
                        "evidence_id": row["evidence_id"],
                        "configuration_id": register(row["settings"]),
                        "operations": difference(row["settings"], settings),
                    }
                    for _, _, row in closest
                ],
            }
        )
    return {
        "version": VERSION,
        "current": base_id,
        "configurations": configurations,
        "previous": previous,
        "candidates": candidates,
        "interpretation": "A configuration with a base is reconstructed by applying ALL operations to that complete base. Paths are key/index arrays. Shared holding values alone do not establish equivalent configurations. Observed-execution matches with different risk states do not prove equivalence.",
    }
