"""Job-owned bounded broker overlap, with no native DB or broker requests."""

from threading import Barrier, Event, Lock, get_ident

import pytest

from services.research_market_series import StockFeaturePreparationScope


def requests(*symbols):
    return [
        {
            "symbol": symbol,
            "exchange": "NSE",
            "interval": "D",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
        }
        for symbol in symbols
    ]


def test_two_calls_overlap_but_return_in_request_order_and_reuse_one_executor():
    rendezvous = Barrier(2, timeout=2)
    first_returned = Event()
    lock = Lock()
    active, maximum, callers = 0, 0, set()
    main = get_ident()
    checks = []

    def history(**request):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            callers.add(get_ident())
        try:
            rendezvous.wait()
            if request["symbol"] == "AAA":
                assert first_returned.wait(2)
            else:
                first_returned.set()
            return True, {"data": [request["symbol"]]}, 200
        finally:
            with lock:
                active -= 1

    scope = StockFeaturePreparationScope(["AAA", "BBB"], [])
    assert scope._history_executor is None
    with scope:
        expected = [(True, {"data": [symbol]}, 200) for symbol in ("AAA", "BBB")]
        assert (
            scope.history_batch(
                history, requests("AAA", "BBB"), check=lambda: checks.append(get_ident())
            )
            == expected
        )
        executor = scope._history_executor
        threads = set(executor._threads)
        assert scope.history_batch(history, requests("AAA", "BBB")) == expected
        assert scope._history_executor is executor
        assert not scope._history_futures
    assert maximum == 2 and active == 0 and len(callers) == 2 and main not in callers
    assert len(checks) >= 2 and set(checks) == {main}
    assert all(not thread.is_alive() for thread in threads)
    assert scope._history_executor is None and scope._history_futures == []


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        requests("AAA", "BBB", "AAA"),
        requests("OTHER"),
        [{"symbol": "AAA", "exchange": "NSE", "interval": "1m"}],
        [{"symbol": "AAA", "exchange": "NSE_INDEX", "interval": "D"}],
        [None],
    ],
)
def test_invalid_scope_never_starts_threads_or_history(invalid):
    scope = StockFeaturePreparationScope(["AAA", "BBB"], [])
    with scope:
        with pytest.raises(ValueError, match="native daily scope"):
            scope.history_batch(lambda **_: pytest.fail("Invalid request cannot run"), invalid)
        assert scope._history_executor is None
    with pytest.raises(ValueError, match="closed"):
        scope.history_batch(lambda **_: None, requests("AAA"))


def test_finished_symbol_cannot_be_downloaded_again():
    with StockFeaturePreparationScope(["AAA"], []) as scope:
        scope.finish("AAA")
        with pytest.raises(ValueError, match="native daily scope"):
            scope.history_batch(lambda **_: None, requests("AAA"))


def test_unexpected_history_exception_clears_futures_and_context_joins_threads():
    scope = StockFeaturePreparationScope(["AAA", "BBB"], [])
    rendezvous = Barrier(2, timeout=2)
    threads = set()

    def history(**request):
        rendezvous.wait()
        if request["symbol"] == "AAA":
            raise RuntimeError("controlled history failure")
        return False, {"message": "unavailable"}, 503

    with pytest.raises(RuntimeError, match="controlled history failure"):
        with scope:
            try:
                scope.history_batch(history, requests("AAA", "BBB"))
            finally:
                threads.update(scope._history_executor._threads)
                assert not scope._history_futures
    assert scope._closed and scope._history_executor is None
    assert all(not thread.is_alive() for thread in threads)


def test_cancellation_is_checked_while_waiting_and_context_joins_inflight_history():
    entered, release = Event(), Event()
    scope = StockFeaturePreparationScope(["AAA", "BBB"], [])
    main, checks = get_ident(), []
    threads = set()

    def history(**_):
        entered.set()
        assert release.wait(2)
        return True, {"data": []}, 200

    def check():
        checks.append(get_ident())
        if entered.is_set():
            release.set()
            raise InterruptedError("controlled cancellation")

    with pytest.raises(InterruptedError, match="controlled cancellation"):
        with scope:
            try:
                scope.history_batch(history, requests("AAA", "BBB"), check=check)
            finally:
                threads.update(scope._history_executor._threads)
                assert not scope._history_futures
    assert len(checks) >= 2 and set(checks) == {main}
    assert scope._closed and scope._history_executor is None
    assert all(not thread.is_alive() for thread in threads)


def test_cancel_before_dispatch_keeps_executor_lazy():
    with StockFeaturePreparationScope(["AAA"], []) as scope:

        def check():
            raise InterruptedError("before broker")

        with pytest.raises(InterruptedError, match="before broker"):
            scope.history_batch(
                lambda **_: pytest.fail("Cancelled request cannot run"),
                requests("AAA"),
                check=check,
            )
        assert scope._history_executor is None and not scope._history_futures


def test_overlapping_calls_still_obey_the_native_history_rate_limiter(monkeypatch):
    import time

    from services import history_service

    monkeypatch.setattr(history_service, "_MIN_HISTORY_INTERVAL", 0.035)
    monkeypatch.setattr(history_service, "_next_history_slot", 0.0)
    starts = []

    def history(**request):
        history_service._enforce_rate_limit(background=True)
        starts.append(time.monotonic())
        time.sleep(0.06)
        return True, {"data": [request["symbol"]]}, 200

    with StockFeaturePreparationScope(["AAA", "BBB"], []) as scope:
        for _ in range(2):
            assert len(scope.history_batch(history, requests("AAA", "BBB"))) == 2
    assert len(starts) == 4
    assert all(later - earlier >= 0.03 for earlier, later in zip(starts, starts[1:], strict=False))
