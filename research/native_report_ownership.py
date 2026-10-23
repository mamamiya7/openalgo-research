"""Single-use synchronous ownership of a freshly produced native report.

Only the private automatic calculation scope can receive the producer's report.
No caller dictionary, id, annotation or retained mutable-object cache is trusted.
"""

import math
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import get_ident

_CURRENT = ContextVar("native_report_ownership", default=None)
_TOKEN = object()


class _ReportLease:
    __slots__ = ("report", "scope", "thread", "used")

    def __init__(self, report, scope, token):
        if token is not _TOKEN:
            raise ValueError("Native report ownership requires its producer")
        self.report, self.scope, self.thread, self.used = report, scope, get_ident(), False

    def consume(self, report):
        valid = (
            not self.used
            and self.report is report
            and self.scope.active
            and _CURRENT.get() is self.scope
            and self.thread == get_ident()
        )
        self.used = True
        self.report = self.scope = None
        return valid


class _Capture:
    __slots__ = ("report", "thread", "active", "published")

    def __init__(self):
        self.report, self.thread, self.active = None, get_ident(), True
        self.published = False

    def publish(self, report):
        if not self.active or self.thread != get_ident() or self.published:
            return
        self.published = True
        # This field is copied verbatim from source signals by _report. Reject
        # mutable/custom aliases rather than claiming exclusive ownership.
        if (
            type(report) is dict
            and type(report.get("ledger")) is list
            and all(
                type(row) is dict
                and type(row.get("source_row")) in (type(None), str, int, float, bool)
                for row in report["ledger"]
            )
        ):
            self.report = report

    def take(self, report):
        if not self.active or self.thread != get_ident() or self.report is not report:
            return None
        self.report = None
        return _ReportLease(report, self, _TOKEN)

    def close(self):
        self.active, self.report = False, None


@contextmanager
def native_report_scope():
    scope = _Capture()
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        scope.close()
        _CURRENT.reset(token)


def capture_native_report(enabled):
    def decorate(function):
        if not enabled:
            return function

        @wraps(function)
        def captured(*args, **kwargs):
            with native_report_scope():
                return function(*args, **kwargs)

        return captured

    return decorate


def publish_native_report(report):
    scope = _CURRENT.get()
    if scope is not None:
        scope.publish(report)
    return report


def take_native_report(report):
    scope = _CURRENT.get()
    return scope.take(report) if scope is not None else None


def consume_native_report(lease, report):
    return type(lease) is _ReportLease and lease.consume(report)


def _flat_scalar_allowance(value):
    """The canonical encoder's exact scalar admission and conservative bound."""
    kind = type(value)
    if value is None:
        return 4
    if kind is bool:
        return 5
    if kind is str:
        return (6 if value.isascii() else 12) * len(value) + 2
    if kind is int:
        return value.bit_length() // 3 + 2
    native = getattr(sys.modules.get("numpy"), "float64", None)
    if kind is float or (kind is native and isinstance(value, float)):
        return 32 if math.isfinite(value) else None
    return None


def _flat_row_bounds(row, *, _allow_details=True):
    """Admit scalar rows plus one flat native execution-details mapping.

    Ledger rows with no filled execution details dominate automatic accounts.
    One field loop replaces recursive scalar calls, not any financial audit.
    Lists contain only exact scalar leaves. Only ``execution_details`` may
    contain one additional exact dictionary; deeper/custom values retain the
    original scanner. This fixed two-level shape cannot recurse indefinitely.
    """
    if type(row) is not dict:
        return None
    nodes, height, size = 1, 1 if row else 0, 2 + len(row) * 2
    for key, value in row.items():
        if type(key) is not str:
            return None
        nodes += 2
        size += (6 if key.isascii() else 12) * len(key) + 2
        if _allow_details and key == "execution_details" and type(value) is dict:
            details = _flat_row_bounds(value, _allow_details=False)
            if details is None:
                return None
            detail_nodes, detail_height, detail_size = details
            nodes += detail_nodes - 1  # value's root was already counted above
            height = max(height, detail_height + 1)
            size += detail_size
        elif type(value) is list:
            size += 2 + len(value)
            nodes += len(value)
            if value:
                height = max(height, 2)
            for child in value:
                allowance = _flat_scalar_allowance(child)
                if allowance is None:
                    return None
                size += allowance
        else:
            allowance = _flat_scalar_allowance(value)
            if allowance is None:
                return None
            size += allowance
    return nodes, height, size


class _CheckedProjection:
    __slots__ = ("projection", "rows", "scope", "thread", "used", "token")

    def __init__(self, projection, rows, scope, token):
        if token is not _TOKEN:
            raise ValueError("Checked native bounds require their exclusive producer")
        self.projection, self.rows, self.scope = projection, rows, scope
        self.thread, self.used, self.token = get_ident(), False, token


def checked_native_projection(lease, report, projection):
    """Transfer one native lease into one bounded, synchronous codec capability."""
    if type(lease) is not _ReportLease:
        return None
    scope = lease.scope
    if not lease.consume(report):
        return None
    ledger = report.get("ledger")
    if (
        type(projection) is not dict
        or type(ledger) is not list
        or projection.get("ledger") is not ledger
        or len(ledger) > 25_000
    ):
        return None
    checked = {}
    for row in ledger:
        bounds = _flat_row_bounds(row)
        if bounds is not None:
            checked[id(row)] = (row, bounds)
    return _CheckedProjection(projection, checked, scope, _TOKEN)


def checked_projection_bounds(capability, projection):
    """Return bounds only for exact rows in this one consumed native projection."""
    if type(capability) is not _CheckedProjection:
        return None
    scope = capability.scope
    valid = (
        capability.token is _TOKEN
        and not capability.used
        and capability.projection is projection
        and capability.thread == get_ident()
        and scope is not None
        and scope.active
        and _CURRENT.get() is scope
    )
    capability.used = True
    rows = capability.rows if valid else None
    capability.rows = capability.scope = capability.projection = None
    if rows is None:
        return None

    def lookup(item):
        if not scope.active or _CURRENT.get() is not scope or scope.thread != get_ident():
            return None
        found = rows.get(id(item))
        return found[1] if found is not None and found[0] is item else None

    return lookup
