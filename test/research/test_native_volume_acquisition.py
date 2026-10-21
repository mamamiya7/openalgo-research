"""One native downloader upgrades missing provenance once, then reuses it."""

import copy

import pytest
from test_native_price_acquisition import DAYS, SIGNALS, calendar, candle, plan

from services.research_native_prices import acquire_market_series_prices, acquire_native_prices
from services.research_volume_provenance import NativeVolumeProvenance


def setup(tmp_path, *, cached=True, broker="fyers", missing_volume=False):
    stored = {day: candle(day) for day in DAYS[1:]} if cached else {}
    calls = []
    cache = NativeVolumeProvenance(tmp_path / "volume", "a" * 64)

    def reader(symbol, first, last):
        return [copy.deepcopy(row) for day, row in stored.items() if first <= day <= last]

    def writer(symbol, rows):
        for row in rows:
            from services.research_acquisition import trading_date

            stored[trading_date(row["timestamp"])] = copy.deepcopy(row)

    def history(**request):
        calls.append(request)
        rows = [candle(day) for day in DAYS[1:]]
        if missing_volume:
            for row in rows:
                del row["volume"]
        return True, {"data": rows}, 200

    opts = {
        "reader": reader,
        "writer": writer,
        "history": history,
        "credentials": lambda: {"broker": broker, "auth_token": "private-not-saved"},
        "archive_dir": str(tmp_path / "receipts"),
        "volume_capture": cache.capture,
        "volume_lookup": cache.lookup,
        "require_volume_provenance": True,
    }
    return opts, stored, calls


def test_legacy_cached_ohlc_upgraded_once_and_reused_on_new_run(tmp_path):
    opts, stored, calls = setup(tmp_path)
    before = copy.deepcopy(stored)
    first = acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    assert len(calls) == 1
    assert stored == before
    assert set(first["volume_provenance"]["sources"]["AAA"]) == set(DAYS[1:])
    restarted = NativeVolumeProvenance(tmp_path / "volume", "a" * 64)
    opts.update(volume_capture=restarted.capture, volume_lookup=restarted.lookup)
    second = acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    assert len(calls) == 1
    assert first["volume_provenance"] == second["volume_provenance"]
    assert first["bars"] == second["bars"]
    assert "private-not-saved" not in str(second)


def test_replaced_historify_volume_requires_refresh_not_reused_attestation(tmp_path):
    opts, stored, calls = setup(tmp_path)
    acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    stored[DAYS[1]]["volume"] = 17
    second = acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    assert len(calls) == 2
    assert stored[DAYS[1]]["volume"] == 100
    assert second["volume_provenance"]["sources"]["AAA"][DAYS[1]]["candle"]["volume"] == 100


@pytest.mark.parametrize("broker,missing_volume", [("zerodha", False), ("fyers", True)])
def test_unknown_units_or_absent_field_is_explicit_without_endless_download(
    tmp_path, broker, missing_volume
):
    opts, _, calls = setup(tmp_path, broker=broker, missing_volume=missing_volume)
    first = acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    second = acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    assert len(calls) == 1
    source = first["volume_provenance"]["sources"]["AAA"][DAYS[1]]
    assert source["volume_unit"] == (
        "unknown" if broker == "zerodha" else "provider_returned_shares"
    )
    assert source["volume_supplied"] is (not missing_volume)
    assert first["volume_provenance"] == second["volume_provenance"]


def test_legacy_path_is_unchanged_and_does_not_fetch_cached_prices(tmp_path):
    opts, _, calls = setup(tmp_path)
    for key in ("volume_capture", "volume_lookup", "require_volume_provenance"):
        del opts[key]
    result = acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    assert calls == []
    assert "volume_provenance" not in result
    assert "volume_provenance" not in result["acquisition_checkpoint"]


def test_provenance_is_not_recorded_until_native_readback_matches(tmp_path):
    opts, _, _ = setup(tmp_path, cached=False)
    opts["writer"] = lambda symbol, rows: None
    with pytest.raises(ValueError, match="Stored OpenAlgo prices differ"):
        acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    assert not (tmp_path / "volume").exists()


def test_missing_callbacks_for_required_provenance_fail_before_history(tmp_path):
    opts, _, calls = setup(tmp_path)
    del opts["volume_capture"]
    with pytest.raises(ValueError, match="needs native capture and lookup"):
        acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    assert calls == []


def test_provenance_survives_completed_checkpoint_resume(tmp_path):
    opts, _, calls = setup(tmp_path, cached=False)
    first = acquire_native_prices(SIGNALS, plan(), calendar(), **opts)
    second = acquire_native_prices(
        SIGNALS, plan(), calendar(), prior=first["acquisition_checkpoint"], **opts
    )
    assert len(calls) == 1
    assert first["volume_provenance"] == second["volume_provenance"]


def test_market_series_uses_same_capture_callbacks(tmp_path):
    from research.market_series import plan_series

    opts, _, calls = setup(tmp_path, cached=False)
    # This is the same native path used by auxiliary stock feature warmup.
    descriptor = {"symbol": "AAA", "exchange": "NSE", "interval": "D", "role": "feature_warmup"}
    own_plan = plan_series(descriptor, DAYS[1:], calendar())
    result = acquire_market_series_prices(own_plan, calendar(), **opts)
    assert len(calls) == 1
    assert result["volume_provenance"]["sources"]["AAA"]
