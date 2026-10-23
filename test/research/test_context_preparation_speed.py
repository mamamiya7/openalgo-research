"""Shared preparation work retains exact identities, bounds and semantic checks."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest
from test_stock_market_context import bundle, refreeze, resign

from research import canonical_json, market_series
from research import stock_features as stock
from research import stock_market_context as context


@pytest.fixture(scope="module")
def retained():
    return bundle(symbols=("AAA", "BBB"))[0]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def test_one_payload_supplies_exact_size_identity_and_final_scope_signature(retained, monkeypatch):
    calls = []
    original = context.bounded_canonical_bytes

    def encode(value, **kwargs):
        calls.append(value)
        return original(value, **kwargs)

    monkeypatch.setattr(context, "bounded_canonical_bytes", encode)
    original_size = stock._json_size

    def size(value, maximum):
        assert value is not retained, "Repeated full-bundle size serialization"
        return original_size(value, maximum)

    monkeypatch.setattr(stock, "_json_size", size)
    with context._bundle_validation_scope() as owner:
        assert context.validate_bundle(retained) is retained
        assert len(calls) == 2
        raw, identifier = owner._encoded
        assert raw == canonical({key: value for key, value in retained.items() if key != "id"})
        assert len(raw) + 72 == len(canonical(retained))
        assert identifier == retained["id"] == sha256(raw).hexdigest()
        assert context.validate_bundle(deepcopy(retained)) == retained
        assert len(calls) == 3
    assert owner._encoded is None


@pytest.mark.parametrize("change", ["hours", "metadata", "receipt", "price"])
def test_every_record_stays_semantically_validated_after_shared_encoding(retained, change):
    value = deepcopy(retained)
    record = value["series"]["BBB"]
    if change == "hours":
        record["calendar"]["session_hours"][value["execution_sessions"][0]]["close"] = "15:29"
    elif change == "metadata":
        record["calendar"]["provenance"]["calendar_source"] = "another-source"
    elif change == "receipt":
        receipt = {
            "version": "openalgo-market-series-receipt-v1",
            "descriptor": record["evidence"]["descriptor"],
            "symbol": "BBB",
            "exchange": "NSE",
            "interval": "D",
            "accepted_slots": ["2020-01-01"],
        }
        record["acquisition_receipts"] = [
            {"receipt": receipt, "sha256": market_series.fingerprint(receipt)}
        ]
    else:
        next(iter(record["evidence"]["bars"].values()))["low"] = -1
    resign(value)
    with pytest.raises(ValueError):
        context.validate_bundle(value)


def test_calendar_compatibility_never_copies_session_history(retained, monkeypatch):
    stock_calendar = retained["series"]["AAA"]["calendar"]
    market_calendar = retained["market"]["calendar"]
    before = deepcopy((stock_calendar, market_calendar))
    monkeypatch.setattr(context, "deepcopy", lambda *_args: pytest.fail("Copied calendar history"))
    context._compatible_calendar(stock_calendar, market_calendar, stock_calendar["sessions"])
    assert (stock_calendar, market_calendar) == before


def test_size_and_custom_type_fallback_preserve_original_rejections(retained, monkeypatch):
    monkeypatch.setattr(canonical_json, "MAX_MEDIUM_ASCII_BYTES", 64)
    with context._bundle_validation_scope() as owner:
        context.validate_bundle(retained)
        assert owner._encoded is None
    value = deepcopy(retained)
    value["series"]["BBB"]["acquisition_receipts"] = ()
    with pytest.raises(ValueError, match="acquisition receipts"):
        context.validate_bundle(value)
    monkeypatch.setattr(stock, "MAX_BUNDLE_BYTES", len(canonical(retained)) - 1)
    with pytest.raises(ValueError, match="bounded byte allowance"):
        context.validate_bundle(retained)


def test_stock_record_parses_each_close_once_without_changing_plan_identity(retained, monkeypatch):
    record = retained["series"]["AAA"]
    calls = []
    original = market_series._close_at

    def closing(day, hours):
        calls.append(day)
        return original(day, hours)

    monkeypatch.setattr(market_series, "_close_at", closing)
    assert stock.validate_record("AAA", record) is record
    assert calls == record["calendar"]["sessions"]
    plan, closes = market_series._planned_series(
        record["evidence"]["descriptor"], record["evidence"]["required_dates"], record["calendar"]
    )
    assert plan == market_series.plan_series(
        record["evidence"]["descriptor"], record["evidence"]["required_dates"], record["calendar"]
    )
    assert closes == record["evidence"]["available_at"]


def test_receipt_size_encoding_keeps_exact_hash_and_rejects_a_forged_receipt(retained):
    record = deepcopy(retained["series"]["AAA"])
    receipt = {
        "version": "openalgo-market-series-receipt-v1",
        "descriptor": record["evidence"]["descriptor"],
        "symbol": "AAA",
        "exchange": "NSE",
        "interval": "D",
        "accepted_slots": list(record["evidence"]["required_dates"]),
    }
    record["acquisition_receipts"] = [
        {"receipt": receipt, "sha256": market_series.fingerprint(receipt)}
    ]
    size, raw = stock._json_size_and_encoding(receipt, stock.MAX_RECEIPT_BYTES)
    assert size == len(canonical(receipt))
    assert raw == canonical(receipt)
    assert stock.validate_record("AAA", record) is record
    receipt["accepted_slots"].pop()
    with pytest.raises(ValueError, match="integrity changed"):
        stock.validate_record("AAA", record)


def test_full_scanner_final_symbol_cannot_hide_behind_a_previous_signature(monkeypatch):
    value, _ = bundle(symbols=tuple(f"STOCK{i}" for i in range(146)), count=360)
    assert len(value["series"]["STOCK145"]["calendar"]["sessions"]) == 300
    calls = []
    original = stock.validate_record

    def validate(symbol, record):
        calls.append(symbol)
        return original(symbol, record)

    monkeypatch.setattr(stock, "validate_record", validate)
    with context._bundle_validation_scope() as owner:
        context.validate_bundle(value)
        assert owner._encoded is not None
        assert owner._encoded[0] == canonical(
            {key: item for key, item in value.items() if key != "id"}
        )
        assert len(calls) == 146
        context.validate_bundle(deepcopy(value))
        assert len(calls) == 146
        last = value["series"]["STOCK145"]
        bar = next(iter(last["evidence"]["bars"].values()))
        bar["close"] += 0.01
        refreeze(last)
        resign(value)
        context.validate_bundle(value)
        assert len(calls) == 292
        last["calendar"]["provenance"]["calendar_source"] = "forged"
        resign(value)
        with pytest.raises(ValueError, match="provenance changed"):
            context.validate_bundle(value)
        assert len(calls) == 438
        monkeypatch.setattr(canonical_json, "MAX_MEDIUM_ASCII_BYTES", 64)
        assert context._validation_encoding(value) is None
