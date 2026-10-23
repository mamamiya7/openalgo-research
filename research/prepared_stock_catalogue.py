"""Fixed stock rule indices owned by one prepared native calculation.

No user-input cache: definitions are bounded, detached and never exposed as
mutable objects. Public catalogue functions retain their ordinary fresh output.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from threading import get_ident

from research.canonical_json import bounded_canonical_snapshot
from research.owned_entry_features import _capture
from research.portfolio import fingerprint

_current = ContextVar("prepared_stock_catalogue", default=None)


def _providers():
    from research import stock_features, stock_indicators, stock_relative_strength

    return (
        stock_features.catalog,
        stock_indicators.catalog,
        stock_relative_strength.catalog,
        stock_indicators.runtime_version,
        stock_indicators._installed_runtime_version,
        stock_indicators.TESTED_SDK_VERSION,
    )


class _Definitions:
    def __init__(self):
        from research import stock_features, stock_indicators, stock_relative_strength

        self.thread, self.closed = get_ident(), False
        self.providers = _providers()
        self.runtime = stock_indicators.runtime_version()
        captured = bounded_canonical_snapshot(
            {
                "legacy": stock_features.catalog(),
                "indicator": stock_indicators.catalog(),
                "relative": stock_relative_strength.catalog(),
            },
            max_bytes=1024 * 1024,
        )
        if captured is None:
            raise ValueError("The fixed stock catalogue exceeds its owned byte allowance")
        owned, _ = captured
        self.rules = {}
        for kind, rows in owned.items():
            if len(rows) > 64 or len({row["id"] for row in rows}) != len(rows):
                raise ValueError("The fixed stock catalogue changed its bounded identities")
            self.rules[kind] = {row["id"]: (row, _capture(row), fingerprint(row)) for row in rows}

    def active(self):
        from research import stock_indicators

        return (
            not self.closed
            and self.thread == get_ident()
            and self.providers == _providers()
            and stock_indicators.runtime_version() == self.runtime
        )

    def close(self):
        self.closed = True
        self.rules.clear()
        self.providers = None


@contextmanager
def prepared_catalogues():
    owner = _Definitions()
    token = _current.set(owner)
    try:
        yield owner
    finally:
        owner.close()
        _current.reset(token)


def normalize_if_owned(kind, value):
    owner = _current.get()
    if owner is None or not owner.active():
        return False, None
    expected = (
        owner.rules[kind].get(value.get("id"))
        if isinstance(value, dict) and isinstance(value.get("id"), str)
        else None
    )
    if expected is not None and fingerprint(value) == expected[2]:
        return True, deepcopy(expected[0])
    return True, None


def component_if_owned(kind, identifier):
    owner = _current.get()
    if owner is None or not owner.active():
        return None
    return owner.rules[kind][identifier][1]
