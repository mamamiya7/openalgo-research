"""Imported broker history corrections, with recorded stand-in responses only."""

from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from broker.flattrade.api import data as flattrade


def flat_history(monkeypatch, rows, interval="D"):
    monkeypatch.setattr(flattrade, "get_token", lambda *_: "controlled-token")
    monkeypatch.setattr(flattrade, "get_br_symbol", lambda *_: "SENSEX")
    monkeypatch.setattr(flattrade, "get_api_response", lambda *_args, **_kwargs: rows)
    monkeypatch.setenv("BROKER_API_KEY", "controlled-client:::unused")
    return flattrade.BrokerData("unused").get_history(
        "SENSEX", "BSE_INDEX", interval, "2026-01-22", "2026-01-22"
    )


def daily_candle(**changes):
    return dict(
        into="100",
        inth="101",
        intl="99",
        intc="102",
        intv="0",
        oi="0",
        ssboe=str(int(datetime(2026, 1, 22).timestamp())),
        **changes,
    )


def test_flattrade_repairs_index_range_without_changing_open_or_close(monkeypatch):
    result = flat_history(monkeypatch, [daily_candle()])
    assert result.iloc[0][["open", "high", "low", "close"]].tolist() == [100, 102, 99, 102]


@pytest.mark.parametrize("bad", [None, "", "nan", "inf", "-inf"])
def test_flattrade_bad_row_does_not_destroy_good_history(monkeypatch, bad):
    invalid = daily_candle()
    invalid["intc"] = bad
    result = flat_history(monkeypatch, [invalid, daily_candle()])
    assert len(result) == 1
    assert result.iloc[0]["close"] == 102


def test_flattrade_preopen_and_negative_volume_keep_valid_execution_bars(monkeypatch):
    rows = [
        {
            "into": "100",
            "inth": "100",
            "intl": "100",
            "intc": "100",
            "intv": "0",
            "time": "22-01-2026 09:14:00",
        },
        {
            "into": "100",
            "inth": "102",
            "intl": "99",
            "intc": "101",
            "intv": "-50",
            "time": "22-01-2026 09:15:00",
        },
    ]
    result = flat_history(monkeypatch, rows, "1m")
    assert len(result) == 1
    assert result.iloc[0]["timestamp"] == int(datetime(2026, 1, 22, 9, 15).timestamp())
    assert result.iloc[0]["volume"] == 0


def test_fivepaisa_latest_candle_outside_requested_range_is_not_used(monkeypatch):
    # This broker reads its three-part application id at import time.
    monkeypatch.setenv("BROKER_API_KEY", "controlled-app:::controlled-user:::controlled-client")
    from broker.fivepaisa.api import data as fivepaisa

    rows = [
        ["2026-01-21T00:00:00", 100, 103, 98, 101, 100],
        ["2026-01-22T00:00:00", 100, 103, 98, 101, 100],
        ["2026-01-23T00:00:00", 100, 103, 98, 101, 100],
    ]
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"status": "success", "data": {"candles": rows}},
    )
    monkeypatch.setattr(fivepaisa, "get_token", lambda *_: "controlled-token")
    monkeypatch.setattr(
        fivepaisa,
        "get_httpx_client",
        lambda: SimpleNamespace(get=lambda *_args, **_kwargs: response),
    )
    result = fivepaisa.BrokerData("unused").get_history(
        "NIFTY", "NSE_INDEX", "D", "2026-01-22", "2026-01-22"
    )
    assert len(result) == 1
    assert (
        pd.Timestamp(result.iloc[0]["timestamp"], unit="s", tz="UTC")
        .tz_convert("Asia/Kolkata")
        .date()
        .isoformat()
        == "2026-01-22"
    )
