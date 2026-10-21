"""Share-volume admission cannot promote legacy zeros or use a future candle."""

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from research.volume_admission import (
    ATTESTATION_VERSION,
    MAX_BYTES,
    MAX_ROWS,
    MAX_SHARES,
    RECEIPT_VERSION,
    SOURCE_VERSION,
    VERSION,
    assess,
    explain_legacy_snapshot,
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def inputs():
    days = []
    day = date(2026, 1, 5)
    while len(days) < 35:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    calendar = {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }
    receipt = {
        "version": RECEIPT_VERSION,
        "instrument": {"symbol": "AAA", "exchange": "NSE", "asset_class": "cash_equity"},
        "provider": {"name": "reviewed_fixture_provider", "adapter": "reviewed_fixture_adapter_v1"},
        "interval": "D",
        "rows": [
            {
                "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000 + i,
            }
            for i, day in enumerate(days)
        ],
        "volume_supplied": dict.fromkeys(days, True),
        "available_at": {day: day + "T15:30:00+05:30" for day in days},
    }
    source = {
        "version": SOURCE_VERSION,
        "receipt": receipt,
        "receipt_sha256": digest(receipt),
        "attestation": {
            "version": ATTESTATION_VERSION,
            "receipt_sha256": digest(receipt),
            "volume_unit": "shares",
            "price_adjustment_basis": "raw",
            "quantity_adjustment_basis": "raw",
            "quantity_comparable_from": days[0],
            "quantity_comparable_through": days[-1],
            "basis_evidence_id": "a" * 64,
            "attested_by": "reviewed_fixture_basis_contract_v1",
            "instrument_id": digest(receipt["instrument"]),
            "adapter": receipt["provider"]["adapter"],
            "compatible_quantity_basis": True,
            "calendar_id": digest(
                {
                    "sessions": days,
                    "session_hours": calendar["session_hours"],
                    "calendar_basis": "openalgo-market-calendar-v1",
                    "exchange": "NSE",
                }
            ),
        },
    }
    return source, calendar, days[25], days[25] + "T09:15:00+05:30"


def reseal(source):
    source["receipt_sha256"] = digest(source["receipt"])
    source["attestation"]["receipt_sha256"] = source["receipt_sha256"]


def unavailable(result, code):
    assert result["version"] == VERSION
    assert result["status"] == "unavailable"
    assert result["mean_daily_shares"] is None
    assert code in result["reason_codes"]
    assert result["id"] == digest({key: value for key, value in result.items() if key != "id"})


def test_prior_twenty_expected_sessions_are_qualified_without_mutating_or_retaining_inputs():
    source, calendar, entry, decision = inputs()
    before = deepcopy((source, calendar))
    result = assess(source, calendar, entry, decision)
    assert result["status"] == "qualified"
    assert result["reason_codes"] == []
    assert result["qualification_basis"] == "declared_producer_contract_only"
    assert "do not authenticate" in result["limitations"][0]
    assert result["mean_daily_shares"] == sum(range(1005, 1025)) / 20
    assert result["window"]["sessions"] == calendar["sessions"][5:25]
    assert result["window"]["observations"] == 20
    assert result["window"]["to"] < entry
    assert len(json.dumps(result)) < 3000
    assert (source, calendar) == before
    source["receipt"]["provider"]["name"] = "edited"
    calendar["sessions"].clear()
    assert result["source"]["provider"]["name"] == "reviewed_fixture_provider"
    assert len(result["window"]["sessions"]) == 20


def test_actual_supplied_zero_is_valid_and_is_never_confused_with_unknown_zero():
    source, calendar, entry, decision = inputs()
    source["receipt"]["rows"][5]["volume"] = 0
    reseal(source)
    qualified = assess(source, calendar, entry, decision)
    assert qualified["status"] == "qualified"
    assert qualified["mean_daily_shares"] == (sum(range(1005, 1025)) - 1005) / 20
    source["receipt"]["volume_supplied"][calendar["sessions"][5]] = False
    reseal(source)
    unavailable(assess(source, calendar, entry, decision), "volume_supplied_field_unknown")


def test_all_twenty_observed_zero_sessions_have_a_valid_zero_average():
    source, calendar, entry, decision = inputs()
    for row in source["receipt"]["rows"]:
        row["volume"] = 0
    reseal(source)
    result = assess(source, calendar, entry, decision)
    assert result["status"] == "qualified" and result["mean_daily_shares"] == 0


def test_missing_expected_session_cannot_be_replaced_with_older_observation():
    source, calendar, entry, decision = inputs()
    del source["receipt"]["rows"][13]
    reseal(source)
    unavailable(assess(source, calendar, entry, decision), "missing_prior_session")


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_prior_session_is_unavailable_even_if_the_values_agree(conflict):
    source, calendar, entry, decision = inputs()
    duplicate = dict(source["receipt"]["rows"][13])
    if conflict:
        duplicate["volume"] += 7
    source["receipt"]["rows"].append(duplicate)
    reseal(source)
    unavailable(assess(source, calendar, entry, decision), "duplicate_prior_observation")


def test_entry_and_future_prices_volume_and_availability_cannot_change_the_causal_window():
    source, calendar, entry, decision = inputs()
    result = assess(source, calendar, entry, decision)
    for row in source["receipt"]["rows"][25:]:
        row.update(volume=MAX_SHARES, close=999999)
    for day in calendar["sessions"][25:]:
        source["receipt"]["available_at"][day] = "2099-01-01T15:30:00+05:30"
        source["receipt"]["volume_supplied"][day] = False
    reseal(source)
    changed = assess(source, calendar, entry, decision)
    assert changed["status"] == "qualified"
    assert changed["window"] == result["window"]
    assert changed["mean_daily_shares"] == result["mean_daily_shares"]
    assert changed["source"]["receipt_sha256"] != result["source"]["receipt_sha256"]


def test_receipt_reordering_does_not_change_observed_window_or_mean():
    source, calendar, entry, decision = inputs()
    result = assess(source, calendar, entry, decision)
    source["receipt"]["rows"].reverse()
    reseal(source)
    changed = assess(source, calendar, entry, decision)
    assert changed["window"] == result["window"]
    assert changed["mean_daily_shares"] == result["mean_daily_shares"]


@pytest.mark.parametrize("change", ["remove_day", "change_hours", "shift_entry_hours"])
def test_recorded_calendar_cannot_be_reshaped_to_reinterpret_the_window(change):
    source, calendar, entry, decision = inputs()
    if change == "remove_day":
        calendar["sessions"].remove(calendar["sessions"][12])
    elif change == "change_hours":
        calendar["session_hours"][calendar["sessions"][12]]["close"] = "15:29"
    else:
        calendar["session_hours"][entry]["open"] = "09:14"
    unavailable(assess(source, calendar, entry, decision), "calendar_identity_mismatch")


@pytest.mark.parametrize("value", [True, -1, 0.5, MAX_SHARES + 1, "100"])
def test_share_quantities_require_finite_nonnegative_whole_numbers_with_a_safe_bound(value):
    source, calendar, entry, decision = inputs()
    source["receipt"]["rows"][5]["volume"] = value
    reseal(source)
    unavailable(assess(source, calendar, entry, decision), "volume_quantity_invalid")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_native_observations_never_reach_an_average(value):
    source, calendar, entry, decision = inputs()
    source["receipt"]["rows"][5]["volume"] = value
    unavailable(assess(source, calendar, entry, decision), "source_malformed_or_oversized")


@pytest.mark.parametrize(
    "key,value",
    [
        ("volume_unit", "lots"),
        ("volume_unit", "unknown"),
        ("price_adjustment_basis", "provider-native"),
        ("quantity_adjustment_basis", "split_adjusted"),
        ("quantity_comparable_from", "2026-02-01"),
        ("quantity_comparable_through", "2026-01-05"),
        ("compatible_quantity_basis", False),
        ("compatible_quantity_basis", "true"),
    ],
)
def test_volume_units_and_compatible_share_basis_need_explicit_covered_attestation(key, value):
    source, calendar, entry, decision = inputs()
    source["attestation"][key] = value
    unavailable(
        assess(source, calendar, entry, decision),
        "volume_unit_unqualified" if key == "volume_unit" else "quantity_basis_unqualified",
    )


def test_compatible_split_adjusted_quantity_and_price_bases_can_be_attested():
    source, calendar, entry, decision = inputs()
    source["attestation"].update(
        price_adjustment_basis="split_adjusted", quantity_adjustment_basis="split_adjusted"
    )
    assert assess(source, calendar, entry, decision)["status"] == "qualified"


@pytest.mark.parametrize(
    "key,value",
    [
        ("exchange", "NSE_INDEX"),
        ("exchange", "BSE"),
        ("asset_class", "future"),
        ("symbol", "../AAA"),
    ],
)
def test_indices_noncash_and_ambiguous_instruments_are_not_share_capacity_inputs(key, value):
    source, calendar, entry, decision = inputs()
    source["receipt"]["instrument"][key] = value
    reseal(source)
    unavailable(assess(source, calendar, entry, decision), "instrument_or_interval_unqualified")


@pytest.mark.parametrize("value", ["unknown", "OpenAlgo Historify", ""])
def test_a_cache_label_does_not_establish_provider_semantics(value):
    source, calendar, entry, decision = inputs()
    source["receipt"]["provider"]["name"] = value
    reseal(source)
    unavailable(assess(source, calendar, entry, decision), "volume_source_unknown")


@pytest.mark.parametrize(
    "kind", ["price", "quantity", "attestation_hash", "basis_id", "instrument", "adapter"]
)
def test_receipt_or_attestation_integrity_corruption_is_rejected(kind):
    source, calendar, entry, decision = inputs()
    if kind in {"price", "quantity"}:
        source["receipt"]["rows"][5]["close" if kind == "price" else "volume"] += 1
        code = "receipt_identity_mismatch"
    elif kind in {"instrument", "adapter"}:
        source["attestation"]["instrument_id" if kind == "instrument" else "adapter"] = "wrong"
        code = "basis_attestation_unqualified"
    else:
        source["attestation"][
            "receipt_sha256" if kind == "attestation_hash" else "basis_evidence_id"
        ] = "z" * 64
        code = "basis_attestation_unqualified"
    unavailable(assess(source, calendar, entry, decision), code)


@pytest.mark.parametrize(
    "change,code",
    [
        ("early_available", "completed_observation_unavailable"),
        ("late_available", "completed_observation_unavailable"),
        ("naive_available", "observation_malformed"),
        ("missing_available", "observation_malformed"),
        ("timestamp_bool", "observation_timestamp_invalid"),
        ("timestamp_outside_session", "missing_prior_session"),
        ("malformed_price", "observation_ohlc_invalid"),
    ],
)
def test_completed_close_and_timestamp_admission_is_not_inferred_from_a_volume_number(change, code):
    source, calendar, entry, decision = inputs()
    day = calendar["sessions"][5]
    if change == "early_available":
        source["receipt"]["available_at"][day] = day + "T09:15:00+05:30"
    elif change == "late_available":
        source["receipt"]["available_at"][day] = entry + "T09:16:00+05:30"
    elif change == "naive_available":
        source["receipt"]["available_at"][day] = day + "T15:30:00"
    elif change == "missing_available":
        del source["receipt"]["available_at"][day]
    elif change == "timestamp_bool":
        source["receipt"]["rows"][5]["timestamp"] = True
    elif change == "timestamp_outside_session":
        source["receipt"]["rows"][5]["timestamp"] -= 24 * 3600
    else:
        source["receipt"]["rows"][5]["close"] = 999
    reseal(source)
    unavailable(assess(source, calendar, entry, decision), code)


@pytest.mark.parametrize(
    "decision",
    [
        "2026-02-09T09:14:00+05:30",
        "2026-02-09T15:31:00+05:30",
        "2026-02-10T09:15:00+05:30",
    ],
)
def test_entry_decision_must_belong_to_the_recorded_entry_session(decision):
    source, calendar, entry, _ = inputs()
    assert entry == "2026-02-09"
    unavailable(assess(source, calendar, entry, decision), "entry_decision_outside_session")


@pytest.mark.parametrize(
    "kind,code",
    [
        ("missing_warmup", "insufficient_prior_sessions"),
        ("index_calendar", "calendar_unqualified"),
        ("duplicate_calendar", "calendar_unqualified"),
        ("naive_decision", "calendar_or_decision_malformed"),
        ("too_many_rows", "receipt_contract_unqualified"),
        ("too_many_calendar_days", "calendar_unqualified"),
        ("oversized", "source_malformed_or_oversized"),
    ],
)
def test_bounded_calendars_sources_and_decisions_stop_safely(kind, code):
    source, calendar, entry, decision = inputs()
    if kind == "missing_warmup":
        entry = calendar["sessions"][19]
        decision = entry + "T09:15:00+05:30"
    elif kind == "index_calendar":
        calendar["provenance"]["exchange"] = "NSE_INDEX"
    elif kind == "duplicate_calendar":
        calendar["sessions"].insert(3, calendar["sessions"][3])
    elif kind == "naive_decision":
        decision = decision[:19]
    elif kind == "too_many_rows":
        source["receipt"]["rows"] *= MAX_ROWS // len(source["receipt"]["rows"]) + 1
    elif kind == "too_many_calendar_days":
        calendar["sessions"] = ["2026-01-05"] * (MAX_ROWS + 1)
    else:
        source["receipt"]["provider"]["name"] = "x" * MAX_BYTES
    unavailable(assess(source, calendar, entry, decision), code)


class DoNotWalk(list):
    def __iter__(self):
        raise AssertionError("Legacy admission must not walk candle arrays")


def test_legacy_helper_never_walks_prices_or_promotes_positive_or_normalized_zero_volume():
    snapshot = {
        "bars": DoNotWalk([{"volume": 9999}]),
        "provenance": {"unavailable_fields": ["volume", "oi"]},
    }
    result = explain_legacy_snapshot(snapshot)
    unavailable(result, "volume_not_retained")
    assert "volume_source_unknown" in result["reason_codes"]
    receipt = {"broker": "fyers", "observations": DoNotWalk([{"volume": 0}])}
    result = explain_legacy_snapshot(receipt)
    unavailable(result, "volume_unit_unqualified")
    assert "volume_source_unknown" not in result["reason_codes"]
    assert "volume_supplied_field_unknown" in result["reason_codes"]
    assert "quantity_basis_unqualified" in result["reason_codes"]


@pytest.mark.parametrize("value", [None, [], "legacy", 0])
def test_legacy_helper_handles_missing_or_unqualified_source_shapes(value):
    unavailable(explain_legacy_snapshot(value), "source_contract_unqualified")
