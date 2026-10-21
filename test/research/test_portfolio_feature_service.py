"""Real source sidecars freeze through the application service, without brokers."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_capacity_allocation_native import capacity_policy
from test_portfolio_execution_features import source

from research import execution_features as atr
from research.portfolio import fingerprint
from services import research_portfolio_features as service
from services.research_volume_provenance import NativeVolumeProvenance


def configured(tmp_path, monkeypatch, *, symbols=("AAA", "BBB"), capture=True):
    evidence, context, observations = source(symbols=symbols, count=190)
    evidence = atr.prepare(evidence, context)
    store = SimpleNamespace(root=tmp_path / "research")
    store.root.mkdir()
    target = (tmp_path / "isolated.duckdb").resolve()
    native_rows = {
        symbol: {day: deepcopy(record["candle"]) for day, record in rows.items()}
        for symbol, rows in observations.items()
    }
    calls = []

    class Archive:
        def __init__(self, path, *, interval, exchange):
            assert Path(path).resolve() == target
            assert (interval, exchange) == ("D", "NSE")

        def read_many(self, windows, *, check=None):
            assert len(windows) <= 16
            if check:
                check()
            calls.append(deepcopy(windows))
            return [
                [deepcopy(row) for day, row in native_rows[symbol].items() if first <= day <= last]
                for symbol, first, last in windows
            ]

    from services import research_sources

    monkeypatch.setattr(
        research_sources, "acquisition_paths", lambda _: (target, tmp_path / "receipts")
    )
    monkeypatch.setattr(service, "NativeHistorifyArchive", Archive)
    provenance = NativeVolumeProvenance(store.root / "volume-provenance", fingerprint(str(target)))
    if capture:
        for symbol, rows in native_rows.items():
            provenance.capture(symbol, list(rows.values()), rows, broker="fyers")
    return store, evidence, context, native_rows, calls, provenance, target


def test_matching_native_sidecars_freeze_capacity_and_correlation(tmp_path, monkeypatch):
    store, evidence, _, _, calls, _, _ = configured(tmp_path, monkeypatch)
    before = deepcopy(evidence)
    result = service.prepare(store, evidence, cancelled=lambda: None)
    receipt = result["portfolio_execution_features"]
    assert receipt["counts"]["capacity_available"] == 2
    assert receipt["counts"]["correlation_available"] == 2
    assert len(calls) == 1
    assert set(receipt["volume_records"]) == {"AAA", "BBB"}
    for item in receipt["capacity"].values():
        assert item["assessment"]["status"] == "available"
        assert item["feature"]["source_id"] == item["assessment"]["id"]
    assert evidence == before


def test_frozen_resume_reproduces_without_native_read_or_credentials(tmp_path, monkeypatch):
    store, evidence, _, _, calls, _, _ = configured(tmp_path, monkeypatch)
    frozen = service.prepare(store, evidence, cancelled=lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Saved research must not read native prices or credentials")

    from services import research_sources

    monkeypatch.setattr(service, "NativeHistorifyArchive", forbidden)
    monkeypatch.setattr(research_sources, "acquisition_paths", forbidden)
    monkeypatch.setattr(research_sources, "resolve_broker_session", forbidden)
    monkeypatch.setattr(NativeVolumeProvenance, "lookup", forbidden)
    result = service.prepare(store, frozen, cancelled=lambda: None)
    assert result == frozen
    assert len(calls) == 1


def test_changed_saved_volume_evidence_does_not_reproduce_on_resume(tmp_path, monkeypatch):
    store, evidence, _, _, calls, _, _ = configured(tmp_path, monkeypatch)
    frozen = service.prepare(store, evidence, cancelled=lambda: None)
    symbol = "AAA"
    signal_day = evidence["strategies"][0]["signals"][0]["date"]
    frozen["portfolio_execution_features"]["volume_records"][symbol][signal_day]["candle"][
        "volume"
    ] += 1
    with pytest.raises(ValueError, match="do not reproduce"):
        service.prepare(store, frozen, cancelled=lambda: None)
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["volume", "open", "close", "timestamp"])
def test_replaced_native_values_cannot_reuse_frozen_provenance(tmp_path, monkeypatch, field):
    store, evidence, context, rows, _, _, _ = configured(tmp_path, monkeypatch, symbols=("AAA",))
    signal_day = evidence["strategies"][0]["signals"][0]["date"]
    days = context["series"]["AAA"]["calendar"]["sessions"]
    # Entry is the following daily session; signal day belongs to its prior20.
    assert signal_day in days
    rows["AAA"][signal_day][field] += 1
    with pytest.raises(ValueError, match="Broker volume could not support"):
        service.prepare(store, evidence, cancelled=lambda: None)


def test_archive_and_sidecar_changed_together_still_need_the_frozen_ohlc(tmp_path, monkeypatch):
    store, evidence, _, rows, _, cache, _ = configured(tmp_path, monkeypatch, symbols=("AAA",))
    signal_day = evidence["strategies"][0]["signals"][0]["date"]
    rows["AAA"][signal_day]["close"] += 0.5
    cache.capture("AAA", list(rows["AAA"].values()), rows["AAA"], broker="fyers")
    with pytest.raises(ValueError, match="Broker volume could not support"):
        service.prepare(store, evidence, cancelled=lambda: None)


def test_legacy_volume_without_sidecar_has_actionable_error_and_no_download(tmp_path, monkeypatch):
    store, evidence, _, _, calls, _, _ = configured(tmp_path, monkeypatch, capture=False)
    from services import research_sources

    def forbidden(*args, **kwargs):
        raise AssertionError("Feature freeze must not introduce a second downloader")

    monkeypatch.setattr(research_sources, "resolve_broker_session", forbidden)
    with pytest.raises(ValueError, match="20 completed NSE equity sessions"):
        service.prepare(store, evidence, cancelled=lambda: None)
    assert len(calls) == 1
    assert not (store.root / "volume-provenance").exists()


def test_unknown_broker_units_do_not_turn_numeric_volume_into_capacity(tmp_path, monkeypatch):
    store, evidence, _, rows, _, cache, _ = configured(tmp_path, monkeypatch, capture=False)
    for symbol, candles in rows.items():
        cache.capture(symbol, list(candles.values()), candles, broker="zerodha")
    with pytest.raises(ValueError, match="supported broker"):
        service.prepare(store, evidence, cancelled=lambda: None)


def test_one_unavailable_symbol_is_excluded_without_losing_good_sources(tmp_path, monkeypatch):
    store, evidence, _, rows, _, _, _ = configured(tmp_path, monkeypatch)
    signal_day = evidence["strategies"][0]["signals"][0]["date"]
    rows["AAA"][signal_day]["volume"] += 1
    result = service.prepare(store, evidence, cancelled=lambda: None)
    receipt = result["portfolio_execution_features"]
    assert receipt["counts"]["capacity_available"] == 1
    assert (
        next(value for key, value in receipt["capacity"].items() if key.startswith("AAA|"))[
            "feature"
        ]
        is None
    )
    reasons = [row["reason"] for rows in receipt["opportunities"].values() for row in rows.values()]
    assert sum(reason is not None and "capacity_unavailable" in reason for reason in reasons) == 1


def test_correlation_only_preparation_needs_no_archive_or_broker(tmp_path, monkeypatch):
    store, evidence, _, _, calls, _, _ = configured(tmp_path, monkeypatch, capture=False)
    for strategy in evidence["strategies"]:
        strategy["execution_policy"] = capacity_policy(capacity=False, correlation=True)
    from services import research_sources

    def forbidden(*args, **kwargs):
        raise AssertionError("Correlation-only features require no new archive or broker access")

    monkeypatch.setattr(service, "NativeHistorifyArchive", forbidden)
    monkeypatch.setattr(research_sources, "acquisition_paths", forbidden)
    monkeypatch.setattr(research_sources, "resolve_broker_session", forbidden)
    result = service.prepare(store, evidence, cancelled=lambda: None)
    assert result["portfolio_execution_features"]["counts"]["correlation_available"] == 2
    assert result["portfolio_execution_features"]["volume_records"] == {}
    assert calls == []


def test_cancelled_preparation_stops_between_native_batches(tmp_path, monkeypatch):
    symbols = tuple(f"STOCK{number:02}" for number in range(17))
    store, evidence, _, _, calls, _, _ = configured(tmp_path, monkeypatch, symbols=symbols)

    def cancelled():
        if calls:
            raise InterruptedError("Controlled cancellation after first native batch")

    with pytest.raises(InterruptedError):
        service.prepare(store, evidence, cancelled=cancelled)
    assert len(calls) == 1
    assert len(calls[0]) == 16


def test_seventeen_symbols_use_sixteen_then_one_bounded_native_read(tmp_path, monkeypatch):
    symbols = tuple(f"STOCK{number:02}" for number in range(17))
    store, evidence, _, _, calls, _, _ = configured(tmp_path, monkeypatch, symbols=symbols)
    result = service.prepare(store, evidence, cancelled=lambda: None)
    assert [len(batch) for batch in calls] == [16, 1]
    assert result["portfolio_execution_features"]["counts"]["capacity_available"] == 17


def test_parent_stock_warmup_scope_upgrades_once_then_reuses_source(tmp_path, monkeypatch):
    from services import research_market_series as market

    _, context, observations = source(symbols=("AAA",), count=190)
    record = context["series"]["AAA"]
    calendar = record["calendar"]
    dates = record["evidence"]["required_dates"]
    native_rows = {day: deepcopy(value["candle"]) for day, value in observations["AAA"].items()}
    calls = []
    target = (tmp_path / "isolated.duckdb").resolve()

    class Archive:
        def __init__(self, path, **kwargs):
            self._expected_path = Path(path).resolve()
            assert self._expected_path == target

        def read(self, symbol, first, last):
            return [deepcopy(row) for day, row in native_rows.items() if first <= day <= last]

        def read_many(self, windows, *, check=None):
            assert len(windows) <= 16
            if check:
                check()
            return [self.read(symbol, first, last) for symbol, first, last in windows]

        def write(self, symbol, rows):
            for row in rows:
                from services.research_acquisition import trading_date

                native_rows[trading_date(row["timestamp"])] = deepcopy(row)
            return len(rows)

    def history(**request):
        calls.append(request)
        return (
            True,
            {
                "data": [
                    deepcopy(row)
                    for day, row in native_rows.items()
                    if request["start_date"] <= day <= request["end_date"]
                ]
            },
            200,
        )

    monkeypatch.setattr(market, "NativeHistorifyArchive", Archive)

    def acquire():
        with market.StockFeaturePreparationScope(
            ["AAA"],
            calendar["sessions"][100:],
            calendar=calendar,
            volume_root=tmp_path / "research" / "volume-provenance",
        ) as scope:
            return market.acquire_series(
                record["evidence"]["descriptor"],
                dates,
                calendar,
                archive_path=target,
                receipts_dir=tmp_path / "receipts",
                credentials=lambda: {"broker": "fyers", "auth_token": "controlled"},
                scope=scope,
                history=history,
            )

    first = acquire()
    assert len(calls) == 1
    second = acquire()
    assert len(calls) == 1
    assert first["evidence"] == second["evidence"]
    assert first["checkpoint"]["volume_provenance"] == second["checkpoint"]["volume_provenance"]
