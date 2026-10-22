"""Overlapped context responses retain ordinary ordered native admission."""

import time
from copy import deepcopy
from threading import Event

import pytest

from research.market_series import normalize_descriptor, plan_series
from services.research_market_series import StockFeaturePreparationScope
from services.research_native_prices import acquire_market_series_prices

DAYS = ["2025-01-06", "2026-01-06"]


def execute(
    tmp_path,
    *,
    concurrent=False,
    failure=None,
    failure_at=1,
    budget=500,
    cached=False,
    expire_after_response=False,
):
    from test_native_price_acquisition import candle

    descriptor = normalize_descriptor(
        {"symbol": "AAA", "exchange": "NSE", "interval": "D", "role": "feature_warmup"}
    )
    calendar = {
        "sessions": DAYS,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in DAYS},
        "provenance": {"calendar_basis": "openalgo-market-calendar-v1"},
    }
    bars = {day: candle(day) for day in DAYS} if cached else {}
    calls, batches, writes, checkpoints = [], [], [], []
    second_started = Event()
    now = [0.0]

    def reader(symbol, first, last):
        return [deepcopy(row) for day, row in sorted(bars.items()) if first <= day <= last]

    def writer(symbol, rows):
        writes.append(deepcopy(rows))
        for row in rows:
            day = next(day for day in DAYS if candle(day)["timestamp"] == row["timestamp"])
            bars[day] = deepcopy(row)

    def history(**request):
        day = request["start_date"]
        calls.append(day)
        if concurrent:
            if day == DAYS[1]:
                second_started.set()
            elif budget > 1:
                assert second_started.wait(2), "Missing windows did not overlap"
        if expire_after_response:
            now[0] = 2.0
        if failure and day == DAYS[failure_at]:
            if isinstance(failure, Exception):
                raise failure
            return (
                False,
                {"message": "Session expired" if failure == 401 else "Unavailable"},
                failure,
            )
        return True, {"data": [candle(day)]}, 200

    def batch(fetch, requests, *, check):
        assert len(requests) == 2
        check()
        batches.append([request["start_date"] for request in requests])
        with StockFeaturePreparationScope(["AAA"], []) as scope:
            return scope.history_batch(fetch, requests, check=check)

    def credentials():
        assert not cached, "Cache reuse must not ask for credentials"
        return {"broker": "controlled", "auth_token": "test-only"}

    response = acquire_market_series_prices(
        plan_series(descriptor, DAYS, calendar),
        calendar,
        reader=reader,
        writer=writer,
        credentials=credentials,
        archive_dir=tmp_path.resolve(),
        history=history,
        history_batch=batch if concurrent else None,
        max_requests=budget,
        max_seconds=1 if expire_after_response else 1800,
        clock=(lambda: now[0]) if expire_after_response else time.monotonic,
        checkpoint=lambda state: checkpoints.append(deepcopy(state)),
    )
    return response, calls, batches, writes, checkpoints


@pytest.mark.parametrize("failure", [None, 401, 429, 502, RuntimeError("adapter failure")])
def test_ordered_batches_keep_exact_snapshot_receipts_checkpoints_and_failures(tmp_path, failure):
    original = execute(tmp_path / "serial", failure=failure)
    actual = execute(tmp_path / "overlap", concurrent=True, failure=failure)
    assert actual[0] == original[0]
    assert actual[1] == original[1] == DAYS
    assert actual[2] == [DAYS]
    assert actual[3:] == original[3:]


def test_request_budget_does_not_prefetch_an_unadmitted_call(tmp_path):
    response, calls, batches, _, _ = execute(tmp_path, concurrent=True, budget=1)
    assert calls == DAYS[:1] and not batches
    assert response["provenance"]["batch_pending"] is True
    assert response["acquisition_checkpoint"]["completed_windows"] == [["AAA", DAYS[0], DAYS[0]]]


def test_complete_coverage_needs_no_batch_broker_or_credentials(tmp_path):
    original = execute(tmp_path / "serial", cached=True)
    actual = execute(tmp_path / "overlap", concurrent=True, cached=True)
    assert actual[0] == original[0]
    assert not actual[1] and not actual[2] and not actual[3]


def test_adapter_timeout_still_propagates_in_original_request_order(tmp_path):
    with pytest.raises(TimeoutError, match="adapter deadline"):
        execute(tmp_path, concurrent=True, failure=TimeoutError("adapter deadline"))


def test_first_failure_never_admits_its_already_requested_sibling(tmp_path):
    original = execute(tmp_path / "serial", failure=401, failure_at=0)
    actual = execute(tmp_path / "overlap", concurrent=True, failure=401, failure_at=0)
    assert actual[0] == original[0]
    assert original[1] == DAYS[:1] and actual[1] == DAYS
    assert not actual[3]
    assert not actual[0]["acquisition_checkpoint"]["completed_windows"]
    assert actual[4] == original[4]


def test_soft_deadline_finishes_only_returned_pair_and_never_dispatches_third(
    tmp_path, monkeypatch
):
    import sys

    days = ["2024-01-08", "2025-01-06", "2026-01-06"]
    monkeypatch.setattr(sys.modules[__name__], "DAYS", days)
    response, calls, batches, writes, _ = execute(
        tmp_path, concurrent=True, expire_after_response=True
    )
    assert calls == days[:2] and batches == [days[:2]] and len(writes) == 2
    state = response["acquisition_checkpoint"]
    assert state["completed_windows"] == [["AAA", day, day] for day in days[:2]]
    assert state["batch_pending"] is True and state["continuation_reason"] == "time_budget"
