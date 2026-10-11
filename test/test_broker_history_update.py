"""Upstream candle fixes exercised without contacting a broker."""
from types import SimpleNamespace

import pandas as pd
import pytest

from broker.angel.api import data as angel
from broker.shoonya.api.data import BrokerData


@pytest.mark.parametrize("stamp", ["2026-01-22T09:15:00+05:30", "2026-01-22T09:15:00"])
def test_angel_epoch_preserves_ist_instant(stamp):
    actual = angel._angel_timestamps_to_epoch(pd.Series([stamp]))
    assert actual.iloc[0] == int(pd.Timestamp("2026-01-22T03:45:00Z").timestamp())


@pytest.mark.parametrize("end,expected", [("2026-01-22", "2026-01-22 23:59"), ("2026-10-07", "2026-10-07 10:42")])
def test_angel_request_window_uses_ist_today(monkeypatch, end, expected):
    class Clock:
        def __new__(cls, value):
            return pd.Timestamp(value)

        @staticmethod
        def now(tz):
            assert tz == "Asia/Kolkata"
            return pd.Timestamp("2026-10-07 10:42:37", tz=tz)

    monkeypatch.setattr(angel, "pd", SimpleNamespace(Timestamp=Clock, Timedelta=pd.Timedelta))
    start, stop = angel._angel_history_window("2026-01-21", end)
    assert start == pd.Timestamp("2026-01-21 00:00")
    assert stop == pd.Timestamp(expected)
    assert start.tz is None and stop.tz is None


def test_shoonya_repairs_invalid_bars_without_rewriting_valid_bar():
    rows = pd.DataFrame([
        dict(open=100., high=95., low=106., close=105., volume=-42),
        dict(open=100., high=110., low=90., close=105., volume=42),
        dict(open=float("nan"), high=110., low=90., close=105., volume=42),
    ])
    original = rows.copy(deep=True)
    repaired = BrokerData._repair_candles(rows, "NSE", "TCS", "1m")
    assert len(repaired) == 2
    assert repaired.iloc[0].to_dict() == dict(open=100., high=106., low=95., close=105., volume=0)
    pd.testing.assert_series_equal(repaired.iloc[1], original.iloc[1])
    pd.testing.assert_frame_equal(rows, original)


def test_shoonya_all_missing_bars_remain_empty():
    frame = pd.DataFrame([dict(open=None, high=None, low=None, close=None, volume=0)])
    assert BrokerData._repair_candles(frame, "NSE", "TCS", "D").empty
