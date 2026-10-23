"""Private, validated entry evidence shared by one prepared feature owner.

Ordinary caller dictionaries never gain admission by an id, flag or object key.
Only this module's detached immutable JSON representation carries the capability.
Its lifetime is the existing bounded prepared receipt, with no global registry.
"""

_TOKEN = object()


def _readonly(*_args, **_kwargs):
    raise TypeError("Owned entry evidence is immutable")


class _OwnedDict(dict):
    __slots__ = ("_entry_version", "_bounds", "_token")
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly
    __setattr__ = __delattr__ = _readonly

    def __deepcopy__(self, _memo):
        return self

    def __copy__(self):
        return self


class _OwnedList(list):
    __slots__ = ("_bounds", "_token")
    __setattr__ = __delattr__ = _readonly
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = (
        _readonly
    )
    __iadd__ = __imul__ = _readonly

    def __deepcopy__(self, _memo):
        return self

    def __copy__(self):
        return self


def is_immutable_json(value):
    return type(value) in (_OwnedDict, _OwnedList) and getattr(value, "_token", None) is _TOKEN


def is_owned_entry(value, version):
    return (
        is_immutable_json(value)
        and type(value) is _OwnedDict
        and value._entry_version is not None
        and value._entry_version == version
    )


def owned_json_bounds(value):
    """Precomputed conservative nodes/depth/default-ASCII-byte bound, or None."""
    return value._bounds if is_immutable_json(value) else None


def _bounds(value):
    if is_immutable_json(value):
        return value._bounds
    if isinstance(value, str):
        return 1, 0, 2 + (6 if value.isascii() else 12) * len(value)
    return 1, 0, len(str(value)) if value is not None else 4


def _capture(value):
    if is_immutable_json(value):
        return value
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("Owned evidence requires exact JSON object keys")
        result = _OwnedDict({key: _capture(item) for key, item in value.items()})
        object.__setattr__(result, "_entry_version", None)
        children = [_bounds(item) for item in result.values()]
        keys = [_bounds(key) for key in result]
        bounds = (
            1 + sum(item[0] for item in children) + len(keys),
            1 + max((item[1] for item in children), default=-1),
            2 + sum(item[2] for item in children + keys) + 4 * len(keys),
        )
        object.__setattr__(result, "_bounds", bounds)
        object.__setattr__(result, "_token", _TOKEN)
        return result
    if type(value) is list:
        result = _OwnedList(_capture(item) for item in value)
        children = [_bounds(item) for item in result]
        object.__setattr__(
            result,
            "_bounds",
            (
                1 + sum(item[0] for item in children),
                1 + max((item[1] for item in children), default=-1),
                2 + sum(item[2] for item in children) + 2 * len(children),
            ),
        )
        object.__setattr__(result, "_token", _TOKEN)
        return result
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("Entry evidence must retain its plain JSON schema")


def admit_entry(value, version):
    """Capture and fully validate a private receipt entry before sharing it."""
    if is_owned_entry(value, version):
        return value
    owned = _capture(value)
    if type(owned) is not _OwnedDict or owned.get("version") != version:
        raise ValueError("Entry evidence changed its recorded version")
    day = owned.get("entry_session", "")
    if not isinstance(day, str):
        raise ValueError("Entry evidence needs its recorded ISO entry session")
    binding = {"entry_session": day, "decision_at": day + "T23:59:00+05:30"}
    if version == "research-entry-atr-v1":
        from research.execution_features import validate_entry_feature

        validate_entry_feature(owned, symbol=owned.get("symbol"), **binding)
    elif version == "research-entry-capacity-v1":
        from research.portfolio_execution_features import validate_capacity_entry

        validate_capacity_entry(owned, symbol=owned.get("symbol"), **binding)
    elif version == "research-entry-correlation-v1":
        from research.portfolio_execution_features import validate_correlation_entry

        validate_correlation_entry(
            owned, symbol=owned.get("symbol"), required_symbols=(), **binding
        )
    elif version == "research-cluster-allocation-v1":
        from research.portfolio_allocation import validate

        validate(owned, symbol=next(iter(owned.get("weights", {})), None), **binding)
    else:
        raise ValueError("Choose a supported owned entry evidence version")
    object.__setattr__(owned, "_entry_version", version)
    return owned
