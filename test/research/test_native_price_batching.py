"""Fewer native requests with the exact same required candles and recovery rules."""

import copy
from datetime import date, timedelta

import pytest
from test_native_price_acquisition import candle
from test_native_price_activity import NativeArchive, prices

from research import portfolio as portfolio_contract
from services import research_native_prices as acquisition
from services.research_acquisition import _save_receipt


def trading_days(count):
    result, day = [], date(2026, 1, 5)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def fixture(path, *, cached=False):
    native = NativeArchive(path, symbols=("AAA",))
    days = trading_days(25)
    slots = [days[n] for n in (1, 2, 3, 8, 9, 10, 15, 16, 17)]
    native.calendar["sessions"] = days
    native.plan = {"interval": "D", "required_dates": {"AAA": slots}}
    native.slots = slots
    # A range fetch includes intervening candles; only required rows are admitted.
    native.responses["AAA"] = [candle(day) for day in days]
    if cached:
        native.rows["AAA"] = [candle(slots[n], close=75) for n in (1, 4)]
    return native


@pytest.mark.parametrize("cached", [False, True])
def test_nearby_windows_reduce_calls_without_expanding_calculation_or_overwriting_cache(
    tmp_path, monkeypatch, cached
):
    baseline = fixture(tmp_path / "baseline", cached=cached)
    windows = acquisition._windows
    with monkeypatch.context() as old:
        old.setattr(
            acquisition,
            "_windows",
            lambda *args, **kwargs: windows(*args, **{**kwargs, "max_gap": 0}),
        )
        expected = baseline.run()
    grouped = fixture(tmp_path / "grouped", cached=cached)
    result = grouped.run()
    assert len(grouped.calls) == 1 < len(baseline.calls)
    assert len(grouped.reads) == 2 < len(baseline.reads)
    assert len(grouped.saved) < len(baseline.saved)
    for field in (
        "bars",
        "raw_bars",
        "sessions",
        "required_dates",
        "data_requirements",
    ):
        assert result[field] == expected[field]
    assert {k: v for k, v in result["coverage"].items() if k != "provenance"} == {
        k: v for k, v in expected["coverage"].items() if k != "provenance"
    }
    assert (
        result["provenance"]["acquisition_identity"]
        == expected["provenance"]["acquisition_identity"]
    )
    assert result["provenance"]["quality_findings"] == []
    assert prices(result)["required_candles"] == prices(result)["available_candles"] == 9
    assert prices(result)["downloaded_candles"] == (7 if cached else 9)
    assert prices(result)["cached_candles"] == (2 if cached else 0)
    assert len(grouped.rows["AAA"]) == 9  # No extra gap candles written to Historify.
    assert set(result["bars"]["AAA"]) == set(grouped.slots)


def test_fully_cached_disjoint_daily_windows_need_one_read_and_no_broker(tmp_path):
    native = fixture(tmp_path)
    native.rows = copy.deepcopy(native.responses)
    native.run(history=lambda **_: pytest.fail("Cached data must not download"))
    assert native.reads == ["AAA"]


@pytest.mark.parametrize("later_hold, expected_extra", [(5, 0), (45, 40)])
def test_changed_settings_reuse_existing_candles_and_download_only_longer_hold(
    tmp_path, later_hold, expected_extra
):
    native = NativeArchive(tmp_path, symbols=("AAA",))
    days = trading_days(60)
    native.calendar["sessions"] = days
    native.responses["AAA"] = [candle(day) for day in days]

    def planned(hold, target, size):
        request = portfolio_contract.normalize(
            {
                "strategies": [
                    {
                        "id": "signals",
                        "name": "Signals",
                        "source_id": "a" * 32,
                        "allocation_pct": 100,
                        "config": {
                            "hold_sessions": hold,
                            "target_pct": target,
                            "order_size_pct": size,
                        },
                    }
                ]
            }
        )
        strategy = {**request["strategies"][0], "signals": native.signals}
        return portfolio_contract.price_plan(request, [strategy], native.calendar)

    native.plan = planned(5, 6, 10)
    first = native.run()
    assert prices(first)["downloaded_candles"] == 6
    old_bars = copy.deepcopy(first["bars"]["AAA"])
    native.calls.clear()
    native.reads.clear()
    native.plan = planned(later_hold, 9, 2)
    second = native.run()  # A different run, sharing Historify, without a prior checkpoint.
    assert prices(second)["cached_candles"] == 6
    assert prices(second)["downloaded_candles"] == expected_extra
    assert len(native.calls) == (1 if expected_extra else 0)
    assert all(second["bars"]["AAA"][day] == values for day, values in old_bars.items())
    assert len(native.rows["AAA"]) == 6 + expected_extra


def test_grouped_cache_progress_survives_time_budget_without_rechecking(tmp_path):
    native = fixture(tmp_path)
    native.after_read = native.advance
    first = native.run(max_seconds=1)
    assert first["provenance"]["batch_pending"] is True
    assert native.reads == ["AAA"] and not native.calls
    native.now = 0
    native.after_read = lambda *_: None
    result = native.run(prior=first["acquisition_checkpoint"])
    assert native.reads == ["AAA", "AAA"]  # The second read is download verification.
    assert native.calls == ["AAA"]
    assert result["provenance"]["acquisition_status"] == "complete"


def test_planner_never_bridges_confirmed_completed_windows():
    days = trading_days(12)
    positions = {day: index for index, day in enumerate(days)}
    slots = [days[1], days[2], days[6], days[7]]
    assert list(acquisition._windows(slots, positions, "D", max_gap=5)) == [(days[1], days[7])]
    assert list(
        acquisition._windows(slots, positions, "D", max_gap=5, barriers=[(days[3], days[5])])
    ) == [(days[1], days[2]), (days[6], days[7])]


def test_resume_retains_an_old_empty_middle_window_between_unfinished_ranges(tmp_path):
    native = fixture(tmp_path)
    native.after_read = native.advance
    first = native.run(max_seconds=1)
    prior = first["acquisition_checkpoint"]
    middle = native.slots[3:6]
    receipt = {
        "version": "openalgo-native-price-receipt-v1",
        "native_price_policy": acquisition.POLICY,
        "provider": "controlled via OpenAlgo history",
        "broker": "controlled",
        "symbol": "AAA",
        "exchange": "NSE",
        "interval": "D",
        "requested_from": middle[0],
        "requested_to": middle[-1],
        "outcome": "success",
        "http_status": 200,
        "observations": [],
        "rejected_observations": [],
        "accepted_slots": [],
    }
    digest = _save_receipt(tmp_path, receipt)
    prior["completed_windows"] = [["AAA", middle[0], middle[-1]]]
    prior["receipts"].append(
        {
            "sha256": digest,
            "broker": "controlled",
            "symbol": "AAA",
            "date": middle[-1],
            "outcome": "success",
            "rows": 0,
            "admitted_rows": 0,
        }
    )
    native.now = 0
    native.after_read = lambda *_: None
    requested = []

    def history(**request):
        requested.append((request["start_date"], request["end_date"]))
        # Even if the broker now supplies those formerly empty dates, keep the
        # recorded gap fixed and request only the unfinished outer ranges.
        return True, {"data": native.responses["AAA"]}, 200

    result = native.run(prior=prior, history=history)
    assert requested == [(native.slots[0], native.slots[2]), (native.slots[6], native.slots[8])]
    assert set(result["bars"]["AAA"]) == set(native.slots) - set(middle)
    assert prices(result)["unavailable_candles"] == 3
    assert prices(result)["pending_windows"] == 0
    assert result["provenance"]["acquisition_status"] == "partial"
    assert (
        result["provenance"]["acquisition_identity"] == first["provenance"]["acquisition_identity"]
    )
    assert (tmp_path / f"{digest}.json").read_bytes()  # Original empty evidence is retained.


def test_configured_daily_gap_boundary_counts_intervening_trading_sessions():
    days = trading_days(30)
    positions = {day: index for index, day in enumerate(days)}
    limit = acquisition.DAILY_REQUEST_GAP
    assert limit == 20
    assert list(
        acquisition._windows(
            [days[1], days[limit + 2]],
            positions,
            "D",
            max_gap=limit,
        )
    ) == [(days[1], days[limit + 2])]
    assert list(
        acquisition._windows(
            [days[1], days[limit + 3]],
            positions,
            "D",
            max_gap=limit,
        )
    ) == [(days[1], days[1]), (days[limit + 3], days[limit + 3])]


def test_daily_request_size_and_gap_limits_still_apply():
    days = trading_days(280)
    positions = {day: index for index, day in enumerate(days)}
    windows = list(acquisition._windows(days, positions, "D", max_gap=5))
    assert len(windows) == 2
    assert all(
        (date.fromisoformat(last) - date.fromisoformat(first)).days < 300 for first, last in windows
    )
    assert list(acquisition._windows([days[1], days[8]], positions, "D", max_gap=5)) == [
        (days[1], days[1]),
        (days[8], days[8]),
    ]


def test_minute_resolution_and_windows_are_unchanged_by_daily_batching(tmp_path, monkeypatch):
    first = NativeArchive(tmp_path / "baseline", "1m")
    with monkeypatch.context() as old:
        old.setattr(acquisition, "DAILY_REQUEST_GAP", 0)
        expected = first.run()
    second = NativeArchive(tmp_path / "grouped", "1m")
    assert second.run() == expected
    assert second.calls == first.calls and second.reads == first.reads


def test_cancellation_after_grouped_response_retains_receipt_before_any_write(tmp_path):
    native = fixture(tmp_path)

    # Stop at the first persisted source receipt, before ingestion.
    def checkpoint(state):
        native.saved.append(copy.deepcopy(state))
        if any(receipt["broker"] for receipt in state["receipts"]):
            native.cancelled = True

    with pytest.raises(InterruptedError):
        native.run(checkpoint=checkpoint)
    assert len(native.calls) == 1
    assert native.saved[-1]["receipts"] and not native.saved[-1]["bars"]["AAA"]
    assert not native.rows["AAA"]
