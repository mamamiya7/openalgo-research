"""Observed-volume provenance is exact evidence, not reconstructed liquidity."""

import copy
from datetime import date, datetime, timedelta

import pytest

from research import volume_provenance as volume
from services.research_volume_provenance import NativeVolumeProvenance


def calendar():
    days = [
        (date(2026, 1, 1) + timedelta(days=n)).isoformat()
        for n in range(45)
        if (date(2026, 1, 1) + timedelta(days=n)).weekday() < 5
    ]
    return {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        "provenance": {"calendar_basis": "openalgo-market-calendar-v1", "exchange": "NSE"},
    }


def candle(day, quantity=1000):
    return {
        "timestamp": int(datetime.fromisoformat(day + "T00:00:00+05:30").timestamp()),
        "open": 100,
        "high": 102,
        "low": 99,
        "close": 101,
        "volume": quantity,
        "oi": 0,
    }


def sources(broker="fyers"):
    rows = [candle(day) for day in calendar()["sessions"]]
    accepted = dict(zip(calendar()["sessions"], rows, strict=True))
    return volume.capture_rows(
        "AAA",
        rows,
        accepted,
        broker=broker,
        adapter_sha256=volume.REVIEWED_ADAPTER_SHA256,
        captured_at="2026-10-21T00:00:00+00:00",
    )


def assess(records=None):
    return volume.assess_observed(
        sources() if records is None else records,
        calendar(),
        "AAA",
        calendar()["sessions"][20],
        calendar()["sessions"][20] + "T09:15:00+05:30",
    )


def reseal(record):
    record["id"] = volume.fingerprint({key: value for key, value in record.items() if key != "id"})


def test_completed_twenty_sessions_are_an_explicit_model_not_strict_admission():
    result = assess()
    assert result["status"] == "available"
    assert result["mean_daily_shares"] == 1000
    assert result["qualification"] == "historical_simulation_assumption_only"
    assert result["basis"] == "provider_returned_historical_coordinates"
    assert result["source_id"] == result["window"]["observations_id"]
    assert result["window"]["to"] == calendar()["sessions"][19]
    assert (
        "Not point-in-time corporate-action or executable-liquidity proof." in result["limitations"]
    )


def test_entry_and_future_volume_cannot_change_assessment():
    records = sources()
    old = assess(records)
    for day in calendar()["sessions"][20:]:
        records[day]["candle"]["volume"] = 10**12
        records[day]["id"] = "bad"  # These observations must never be consumed.
    assert assess(records) == old


def test_missing_prior_session_is_not_skipped():
    records = sources()
    del records[calendar()["sessions"][12]]
    assert assess(records)["reason_codes"] == ["prior_volume_missing_or_changed"]


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("provider", "zerodha", "volume_producer_profile_unqualified"),
        ("adapter_sha256", "0" * 64, "volume_producer_profile_unqualified"),
        ("reviewed_profile", None, "volume_producer_profile_unqualified"),
        ("volume_supplied", False, "volume_supplied_field_unknown"),
        ("volume_unit", "lots", "volume_producer_profile_unqualified"),
        ("basis", "raw", "volume_coordinate_basis_unqualified"),
        ("adjustment", "split_adjusted", "volume_coordinate_basis_unqualified"),
        ("availability_assumption", "observed_ticks", "volume_coordinate_basis_unqualified"),
    ],
)
def test_semantic_changes_fail_closed_even_when_resealed(key, value, reason):
    records = sources()
    record = records[calendar()["sessions"][2]]
    record[key] = value
    reseal(record)
    assert assess(records)["reason_codes"] == [reason]


@pytest.mark.parametrize("quantity", [0, 1.5, 2**53, -1, float("nan"), True])
def test_share_count_bounds_and_explicit_zero(quantity):
    records = sources()
    record = records[calendar()["sessions"][2]]
    if quantity is True:
        assert not volume.matches(record, {**record["candle"], "volume": quantity}, "AAA")
        return
    if quantity < 0 or quantity != quantity:
        with pytest.raises(ValueError):
            volume.candle({**record["candle"], "volume": quantity})
        return
    record["candle"]["volume"] = float(quantity)
    record["candle_id"] = volume.fingerprint(record["candle"])
    reseal(record)
    result = assess(records)
    assert result["status"] == ("available" if quantity == 0 else "unavailable")


def test_absent_volume_is_not_relabelled_observed_zero():
    day = calendar()["sessions"][0]
    raw = candle(day)
    del raw["volume"]
    records = volume.capture_rows(
        "AAA",
        [raw],
        {day: candle(day, 0)},
        broker="fyers",
        adapter_sha256=volume.REVIEWED_ADAPTER_SHA256,
        captured_at="2026-10-21T00:00:00+00:00",
    )
    assert records[day]["volume_supplied"] is False
    assert not volume.validate_observation(records[day], "AAA", day)


def test_unsupported_broker_keeps_evidence_but_never_becomes_capacity():
    records = sources("zerodha")
    assert records[calendar()["sessions"][0]]["volume_unit"] == "unknown"
    assert assess(records)["status"] == "unavailable"


def test_modified_ohlc_invalidates_seal():
    records = sources()
    records[calendar()["sessions"][0]]["candle"]["close"] = 100
    assert assess(records)["status"] == "unavailable"


@pytest.mark.parametrize(
    "decision",
    [
        "2026-01-29T09:14:59+05:30",
        "2026-01-29T15:30:01+05:30",
        "2026-01-29T09:15:00+00:00",
        "broken",
    ],
)
def test_invalid_decision_never_admits(decision):
    assert (
        volume.assess_observed(sources(), calendar(), "AAA", calendar()["sessions"][20], decision)[
            "status"
        ]
        == "unavailable"
    )


def test_captured_at_is_audit_time_not_fabricated_historical_observation():
    records = sources()
    assert all(row["captured_at"].startswith("2026-10-21") for row in records.values())
    assert assess(records)["status"] == "available"
    assert assess(records)["qualification"] == "historical_simulation_assumption_only"


def test_cache_reuses_exact_rows_across_restart_and_rejects_archive_replacement(tmp_path):
    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    rows = [candle(day)]
    captured = cache.capture("AAA", rows, {day: rows[0]}, broker="fyers")
    restarted = NativeVolumeProvenance(tmp_path, "a" * 64)
    assert restarted.lookup("AAA", day, day, rows) == captured
    assert restarted.lookup("AAA", day, day, [{**rows[0], "volume": 1001}]) == {}
    assert restarted.lookup("AAA", day, day, [{**rows[0], "close": 100}]) == {}
    assert NativeVolumeProvenance(tmp_path, "b" * 64).lookup("AAA", day, day, rows) == {}


def test_cache_does_not_upgrade_legacy_archive_or_cross_symbol(tmp_path):
    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    rows = [candle(day)]
    assert cache.lookup("AAA", day, day, rows) == {}
    cache.capture("AAA", rows, {day: rows[0]}, broker="fyers")
    assert cache.lookup("BBB", day, day, rows) == {}
    assert cache.lookup("AAA", day, day, rows, exchange="NSE_INDEX") == {}


def test_updated_adapter_fails_closed_without_discarding_supplied_mask(tmp_path, monkeypatch):
    monkeypatch.setattr(
        NativeVolumeProvenance, "_adapter_digest", staticmethod(lambda broker: "c" * 64)
    )
    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    row = candle(day)
    record = cache.capture("AAA", [row], {day: row}, broker="fyers")[day]
    assert record["volume_supplied"] is True
    assert record["reviewed_profile"] is None
    assert record["volume_unit"] == "unknown"


@pytest.mark.parametrize("payload", ["corrupt", "[]", "null", "123", '"text"'])
def test_cache_corruption_unavailable_and_recapture_repairs(tmp_path, payload):
    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    row = candle(day)
    cache.capture("AAA", [row], {day: row}, broker="fyers")
    path = next(tmp_path.glob("*.json"))
    path.write_text(payload, encoding="utf-8")
    assert cache.lookup("AAA", day, day, [row]) == {}
    assert cache.capture("AAA", [row], {day: row}, broker="fyers")
    assert cache.lookup("AAA", day, day, [row])


def test_duplicate_archive_rows_fail_closed(tmp_path):
    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    row = candle(day)
    cache.capture("AAA", [row], {day: row}, broker="fyers")
    assert cache.lookup("AAA", day, day, [row, row]) == {}


def test_failed_replace_cleans_temporary_file(tmp_path, monkeypatch):
    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    row = candle(day)

    def fail(*_):
        raise OSError("controlled failure")

    monkeypatch.setattr(type(tmp_path), "replace", fail)
    with pytest.raises(OSError):
        cache.capture("AAA", [row], {day: row}, broker="fyers")
    assert list(tmp_path.iterdir()) == []


def test_disk_bound_fails_without_writing_partial_file(tmp_path, monkeypatch):
    from services import research_volume_provenance as service

    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    row = candle(day)
    monkeypatch.setattr(service, "MAX_CACHE_BYTES", 10)
    # A directory with no entries still needs its own new payload counted.
    with pytest.raises(ValueError):
        cache.capture("AAA", [row], {day: row}, broker="fyers")
    assert list(tmp_path.iterdir()) == []


def test_source_inputs_are_not_mutated():
    records = sources()
    before = copy.deepcopy(records)
    assess(records)
    assert records == before


def test_share_quantity_is_validated_at_observation_boundary():
    day = calendar()["sessions"][0]
    record = sources()[day]
    assert volume.validate_observation(record, "AAA", day)
    for quantity in (1.5, 2**53):
        changed = copy.deepcopy(record)
        changed["candle"]["volume"] = quantity
        changed["candle_id"] = volume.fingerprint(changed["candle"])
        reseal(changed)
        assert not volume.validate_observation(changed, "AAA", day)


def test_output_stays_bounded_for_invalid_large_symbol():
    result = volume.assess_observed(
        {},
        calendar(),
        "X" * 100000,
        calendar()["sessions"][20],
        calendar()["sessions"][20] + "T09:15:00+05:30",
    )
    assert result["status"] == "unavailable"
    assert result["symbol"] is None
    assert len(volume.encoded(result)) < 3000


def test_oversized_capture_rejected_before_any_cache_write(tmp_path):
    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    with pytest.raises(ValueError):
        cache.capture(
            "AAA", [candle(calendar()["sessions"][0])] * (volume.MAX_ROWS + 1), {}, broker="fyers"
        )
    assert list(tmp_path.iterdir()) == []


def test_cache_file_count_and_total_bound_preserve_existing_index(tmp_path, monkeypatch):
    from services import research_volume_provenance as service

    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    row = candle(day)
    first = cache.capture("AAA", [row], {day: row}, broker="fyers")
    monkeypatch.setattr(service, "MAX_CACHE_FILES", 1)
    with pytest.raises(ValueError, match="storage allocation"):
        cache.capture("BBB", [row], {day: row}, broker="fyers")
    assert cache.lookup("AAA", day, day, [row]) == first
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_resource_repetitions_release_handles_and_bound_retained_memory(tmp_path, record_property):
    import gc
    import tracemalloc

    import psutil

    cache = NativeVolumeProvenance(tmp_path, "a" * 64)
    day = calendar()["sessions"][0]
    row = candle(day)
    cache.capture("AAA", [row], {day: row}, broker="fyers")
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    gc.collect()
    before = handles()
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    for _ in range(100):
        cache.capture("AAA", [row], {day: row}, broker="fyers")
        assert cache.lookup("AAA", day, day, [row])
        assert cache.lookup("AAA", day, day, [{**row, "volume": 10}]) == {}
    path = next(tmp_path.glob("*.json"))
    path.write_text("bad-json", encoding="utf-8")
    for _ in range(100):
        assert cache.lookup("AAA", day, day, [row]) == {}
    gc.collect()
    retained = tracemalloc.get_traced_memory()[0] - baseline
    tracemalloc.stop()
    after = handles()
    record_property("handles_before", before)
    record_property("handles_after", after)
    record_property("retained_bytes", retained)
    assert after <= before + 1
    assert retained < 256 * 1024
    assert len(list(tmp_path.iterdir())) == 1
