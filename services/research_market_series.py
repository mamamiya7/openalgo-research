"""Acquire bounded auxiliary prices through the ordinary OpenAlgo history/archive."""

import time
from copy import deepcopy

from research.market_series import freeze_series, plan_series
from services.research_historify import (
    ARCHIVE_READ_BATCH_SIZE,
    NativeHistorifyArchive,
    _read_bounds,
)
from services.research_native_prices import acquire_market_series_prices


class StockFeaturePreparationScope:
    """One preparation's frozen calendar and at most sixteen borrowed windows.

    Rows are ordinary native OHLC inputs, qualified separately for every saved
    instrument. No connection, DataFrame or scope is retained outside this
    preparation. Download verification deliberately bypasses these borrowed
    rows and reads the durable native archive again.
    """

    def __init__(self, symbols, sessions, *, calendar=None):
        from research.stock_features import MAX_SYMBOLS, WARMUP_SESSIONS

        if len(symbols) > MAX_SYMBOLS or len(set(symbols)) != len(symbols):
            raise ValueError("Invalid bounded stock-feature preparation scope")
        self._pending = list(symbols)
        self._sessions = list(sessions)
        self._warmup = WARMUP_SESSIONS
        self._calendar = None
        self._binding = None
        self._archive_path = None
        self._rows = {}
        self._closed = False
        if calendar is not None:
            self._retain_calendar(calendar)

    def _retain_calendar(self, calendar):
        if (
            not isinstance(calendar, dict)
            or calendar.get("sessions", [])[self._warmup :] != self._sessions
            or calendar.get("provenance", {}).get("warmup_sessions", self._warmup) != self._warmup
        ):
            raise ValueError("Stock-feature calendar changed its required 100-session warmup")
        if self._calendar is not None and calendar != self._calendar:
            from research.failures import ResearchFailure, canonical_hours

            affected = [
                day
                for day in self._calendar["sessions"]
                if self._calendar["session_hours"].get(day)
                != calendar.get("session_hours", {}).get(day)
            ]
            details = None
            if affected:
                day = affected[0]
                details = {
                    "first_affected_session": day,
                    "expected_hours": canonical_hours(self._calendar["session_hours"].get(day)),
                    "actual_hours": canonical_hours(calendar.get("session_hours", {}).get(day)),
                    "mismatch_count": len(affected),
                }
            raise ResearchFailure(
                "stock_feature_calendar_mismatch",
                "Saved stock-feature calendars or session hours differ. "
                "Start a new run after reviewing the calendar; this saved run is unchanged.",
                details=details,
            )
        if self._calendar is None:
            self._calendar = deepcopy(calendar)

    def calendar(self, first, last, warmup_sessions, *, recorded=None):
        from services.research_native_calendar import native_calendar_window

        if self._closed:
            raise ValueError("Stock-feature preparation scope is closed")
        binding = (first, last, warmup_sessions)
        if (
            warmup_sessions != self._warmup
            or not self._sessions
            or [first, last] != [self._sessions[0], self._sessions[-1]]
            or self._binding not in (None, binding)
        ):
            raise ValueError("Stock-feature preparation changed its required window")
        self._binding = binding
        if recorded is not None:
            self._retain_calendar(recorded)
        elif self._calendar is None:
            self._retain_calendar(
                native_calendar_window(first, last, warmup_sessions=warmup_sessions)
            )
        return deepcopy(self._calendar)

    def read_many(self, archive, descriptor, calendar, requests, *, check=None):
        if self._closed:
            raise ValueError("Stock-feature preparation scope is closed")
        self._retain_calendar(calendar)
        symbol = descriptor["symbol"]
        if (
            descriptor["exchange"] != "NSE"
            or descriptor["interval"] != "D"
            or descriptor["role"] != "feature_warmup"
            or symbol not in self._pending
            or len(requests) > ARCHIVE_READ_BATCH_SIZE
            or any(row[0] != symbol for row in requests)
        ):
            raise ValueError("Stock-feature cache scope changed its instrument or read bound")
        if self._archive_path not in (None, archive._expected_path):
            raise ValueError("Stock-feature cache scope changed its native archive")
        self._archive_path = archive._expected_path
        if check:
            check()
        if symbol not in self._rows:
            # A retained active checkpoint may resume in the middle of a window.
            # Prefetch the same full frozen window, then return its exact slices.
            first, last = calendar["sessions"][0], calendar["sessions"][-1]
            group = self._pending[:ARCHIVE_READ_BATCH_SIZE]
            if symbol not in group:
                raise ValueError("Stock-feature cache scope changed symbol order")
            rows = archive.read_many([(item, first, last) for item in group], check=check)
            self._rows = dict(zip(group, rows, strict=True))
        results = []
        full_lower, full_upper, _ = _read_bounds(
            calendar["sessions"][0], calendar["sessions"][-1], "D"
        )
        for _, first, last in requests:
            lower, upper, _ = _read_bounds(first, last, "D")
            if lower < full_lower or upper > full_upper:
                raise ValueError("Stock-feature cache read escaped its frozen window")
            results.append([row for row in self._rows[symbol] if lower <= row["timestamp"] < upper])
        if check:
            check()
        return results

    def finish(self, symbol):
        self._rows.pop(symbol, None)
        if symbol in self._pending:
            self._pending.remove(symbol)

    def close(self):
        self._rows.clear()
        self._pending.clear()
        self._sessions.clear()
        self._calendar = None
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def acquire_series(
    descriptor,
    required_dates,
    calendar,
    *,
    archive_path,
    receipts_dir,
    credentials=None,
    prior=None,
    checkpoint=None,
    progress=None,
    activity=None,
    cancelled=None,
    history=None,
    max_requests=500,
    max_seconds=1800,
    clock=time.monotonic,
    scope=None,
):
    """Return frozen evidence and a separate durable, bounded acquisition state.

    Missing candles stay absent. The caller owns job continuation and receipt
    persistence; neither this scope nor the native archive retains connections.
    Volume/open interest are not admitted as observed features because legacy
    archive zeros do not establish that a broker supplied those fields.
    """
    from services.research_acquisition import acquisition_receipts

    plan = plan_series(descriptor, required_dates, calendar)
    descriptor = plan["descriptor"]
    archive = NativeHistorifyArchive(
        archive_path, interval=descriptor["interval"], exchange=descriptor["exchange"]
    )

    def read_many(requests, *, check=None):
        return scope.read_many(archive, descriptor, calendar, requests, check=check)

    snapshot = acquire_market_series_prices(
        plan,
        calendar,
        reader=archive.read,
        reader_many=read_many if scope is not None else archive.read_many,
        writer=archive.write,
        credentials=credentials,
        archive_dir=receipts_dir,
        prior=prior,
        checkpoint=checkpoint,
        progress=progress,
        activity=activity,
        cancelled=cancelled,
        history=history,
        max_requests=max_requests,
        max_seconds=max_seconds,
        clock=clock,
    )
    evidence = freeze_series(
        descriptor, required_dates, calendar, snapshot["bars"][descriptor["symbol"]]
    )
    return {
        "evidence": evidence,
        "checkpoint": snapshot["acquisition_checkpoint"],
        "batch_pending": snapshot["provenance"]["batch_pending"],
        "hard_failures": snapshot["provenance"]["hard_failures"],
        "acquisition_receipts": acquisition_receipts(snapshot, receipts_dir),
    }
