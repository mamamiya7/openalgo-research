"""Hand-checkable causal stock features over native-shaped controlled observations."""

from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from research.engine import validate_config
from research.market_series import freeze_series
from research.report_contract import settings_identity
from research.stock_features import (
    ENTRY_VERSION,
    VERSION,
    apply,
    catalog,
    fingerprint,
    markers,
    normalize_hypothesis,
    validate_bundle,
)


def dates(count=130):
    result, day = [], date(2025, 1, 1)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


def record(symbol="AAA", *, closes=None, missing=(), count=130):
    days = dates(count)
    values = closes if closes is not None else [100.0] * count
    calendar = {
        "sessions": days,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        "provenance": {"calendar_basis": "openalgo-market-calendar-v1", "exchange": "NSE"},
    }
    bars = {
        day: {
            "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
            "open": value,
            "high": value + 1,
            "low": value - 1,
            "close": value,
        }
        for i, (day, value) in enumerate(zip(days, values, strict=True))
        if i not in missing
    }
    series = freeze_series(
        {"symbol": symbol, "exchange": "NSE", "interval": "D", "role": "feature_warmup"},
        days,
        calendar,
        bars,
    )
    return {"evidence": series, "calendar": calendar, "acquisition_receipts": []}


def bundle(*records):
    payload = {
        "version": VERSION,
        "series": {row["evidence"]["descriptor"]["symbol"]: row for row in records},
    }
    return {**payload, "id": fingerprint(payload)}


def execution(*records, indexes=(119,), minute=False, start=100):
    days = records[0]["calendar"]["sessions"]
    sessions = days[start:]
    signals = [
        {"symbol": row["evidence"]["descriptor"]["symbol"], "date": days[i], "row": i + 2}
        for row in records
        for i in indexes
    ]
    config = validate_config({"hold_sessions": 2})
    snapshot = {
        "sessions": sessions,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in sessions},
        "provenance": {"interval": "1m" if minute else "D"},
        "bars": {},
    }
    if minute:
        config = validate_config({"trade_horizon": "intraday", "hold_minutes": 5})
        snapshot["timeline"] = [
            (datetime.fromisoformat(day + "T09:15:00+05:30") + timedelta(minutes=i)).isoformat()
            for day in sessions
            for i in range(375)
        ]
        for signal in signals:
            signal["timestamp"] = signal["date"] + "T09:15:00+05:30"
    strategy = {
        "id": "scanner",
        "name": "Scanner",
        "allocation_pct": 100,
        "config": config,
        "signals": signals,
    }
    return {
        "portfolio": {"engine": "vectorbt", "capital": 100000},
        "strategies": [strategy],
        "signals": [dict(row, strategy_id="scanner") for row in signals],
        "snapshot": snapshot,
        "versions": {"test": "controlled"},
        "frozen_prices": True,
    }


def hypothesis(identifier):
    return next(row for row in catalog() if row["id"] == identifier)


def test_catalog_is_finite_exact_and_portable_settings_do_not_depend_on_price_bundle():
    choices = catalog()
    assert len(choices) == 7 and len({row["id"] for row in choices}) == 7
    source = execution(record())
    raw = [
        {key: row[key] for key in ("id", "name", "allocation_pct", "config")}
        for row in source["strategies"]
    ]
    assert settings_identity(markers(raw, hypothesis("none"))) == settings_identity(raw)
    a = markers(raw, hypothesis("ema_above_10"), "a" * 64)
    b = markers(raw, hypothesis("ema_above_10"), "b" * 64)
    assert a == b and settings_identity(a) != settings_identity(raw)
    assert a[0]["entry_condition"] == {
        "version": ENTRY_VERSION,
        "hypothesis": hypothesis("ema_above_10"),
    }
    choices[1]["lookback"] = 9
    with pytest.raises(ValueError, match="recorded catalog"):
        normalize_hypothesis(choices[1])
    assert catalog()[1]["lookback"] == 10


def test_ema_seed_and_one_step_recurrence_match_hand_calculation():
    closes = [100.0] * 130
    closes[119] = 110
    stock = record(closes=closes)
    source = execution(stock)
    original = deepcopy(source)
    rows, receipt = apply(source, bundle(stock), hypothesis("ema_above_10"))
    feature = receipt["membership"][0]["feature"]
    expected_ema = (2 / 11) * 110 + (9 / 11) * 100
    assert feature["ema"] == pytest.approx(expected_ema)
    assert feature["value"] == pytest.approx(100 * (110 / expected_ema - 1))
    assert feature["observations"] == 50
    assert feature["from"] == dates()[70] and feature["to"] == dates()[119]
    assert receipt["counts"]["allowed"] == 1
    assert "research_exclusion" not in rows[0]["signals"][0]
    assert rows == markers(rows, hypothesis("ema_above_10"))
    assert source == original


def test_momentum_uses_exact_session_lag_and_strict_threshold_including_float_ties():
    closes = [100.0] * 130
    closes[119] = 102
    stock = record(closes=closes)
    source = execution(stock)
    _, positive = apply(source, bundle(stock), hypothesis("momentum_5_gt0"))
    filtered, threshold = apply(source, bundle(stock), hypothesis("momentum_5_gt2"))
    feature = threshold["membership"][0]["feature"]
    assert feature["from"] == dates()[114]
    assert feature["value"] == pytest.approx(2)
    assert positive["counts"]["allowed"] == 1
    assert threshold["counts"]["filtered"] == 1
    assert "not met" in filtered[0]["signals"][0]["research_exclusion"]


@pytest.mark.parametrize(
    "identifier", ["ema_above_10", "ema_above_20", "momentum_5_gt0", "momentum_20_gt2"]
)
def test_prefix_and_future_price_perturbations_leave_prior_membership_unchanged(identifier):
    closes = [100 + i / 10 for i in range(130)]
    original = record(closes=closes)
    source = execution(original)
    _, before = apply(source, bundle(original), hypothesis(identifier))
    future = record(closes=closes[:120] + [9000.0] * 10)
    _, after = apply(source, bundle(future), hypothesis(identifier))
    prefix = record(closes=closes[:121], count=121)
    _, shortened = apply(source, bundle(prefix), hypothesis(identifier))
    assert before["membership"] == after["membership"] == shortened["membership"]
    assert before["counts"] == after["counts"] == shortened["counts"]
    assert (
        before["bundle_id"] != after["bundle_id"]
    )  # Different archives remain different evidence.


def test_intraday_decision_cannot_see_same_session_close():
    closes = [100.0] * 130
    closes[118] = 90
    closes[119] = 9000
    stock = record(closes=closes)
    _, receipt = apply(execution(stock, minute=True), bundle(stock), hypothesis("momentum_5_gt0"))
    row = receipt["membership"][0]
    assert row["decision_at"] == dates()[119] + "T09:16:00+05:30"
    assert row["feature"]["to"] == dates()[118]
    assert row["feature"]["value"] == pytest.approx(-10)
    assert row["state"] == "filtered"


@pytest.mark.parametrize(
    "identifier,index", [("ema_above_10", 48), ("ema_above_20", 98), ("momentum_20_gt0", 19)]
)
def test_insufficient_contiguous_warmup_is_unknown(identifier, index):
    stock = record()
    rows, receipt = apply(
        execution(stock, indexes=(index,), start=0), bundle(stock), hypothesis(identifier)
    )
    assert receipt["counts"]["unknown"] == 1
    assert receipt["membership"][0]["feature"] is None
    assert "earlier consecutive" in rows[0]["signals"][0]["research_exclusion"]


def test_missing_expected_session_is_not_replaced_by_an_older_observed_row():
    stock = record(missing=(118,))
    rows, receipt = apply(execution(stock), bundle(stock), hypothesis("momentum_5_gt0"))
    assert receipt["counts"]["unknown"] == 1
    assert dates()[118] in rows[0]["signals"][0]["research_exclusion"]
    assert len(rows[0]["signals"]) == 1


@pytest.mark.parametrize("identifier", ["ema_above_10", "ema_above_20", "momentum_5_gt0"])
def test_flat_prices_are_known_and_not_above_the_threshold(identifier):
    stock = record()
    _, receipt = apply(execution(stock), bundle(stock), hypothesis(identifier))
    assert receipt["counts"]["filtered"] == 1
    assert receipt["counts"]["unknown"] == 0
    assert receipt["membership"][0]["feature"]["value"] == pytest.approx(0)


def test_original_exclusions_and_pending_signals_survive_gating_and_none_is_unchanged():
    stock = record(missing=(118,))
    source = execution(stock, indexes=(117, 119, 129))
    source["strategies"][0]["signals"][0]["research_exclusion"] = (
        "Recorded missing execution prices"
    )
    rows, receipt = apply(source, bundle(stock), hypothesis("momentum_5_gt0"))
    assert receipt["counts"] == {
        "allowed": 0,
        "filtered": 0,
        "unknown": 1,
        "original_excluded": 1,
        "pending": 1,
    }
    assert rows[0]["signals"][0]["research_exclusion"] == "Recorded missing execution prices"
    assert "research_exclusion" not in rows[0]["signals"][-1]
    baseline, original = apply(source, bundle(stock), hypothesis("none"))
    assert baseline == source["strategies"]
    assert original["counts"]["allowed"] == 1


def test_bundle_rejects_changed_prices_wrong_instrument_and_inconsistent_availability():
    value = bundle(record())
    validate_bundle(value)
    changed = deepcopy(value)
    changed["series"]["AAA"]["evidence"]["bars"][dates()[0]]["close"] += 0.5
    with pytest.raises(ValueError, match="evidence changed"):
        validate_bundle(changed)
    wrong = {"version": VERSION, "series": {"OTHER": value["series"]["AAA"]}}
    with pytest.raises(ValueError, match="exact NSE"):
        validate_bundle({**wrong, "id": fingerprint(wrong)})
    changed = deepcopy(value)
    series = changed["series"]["AAA"]["evidence"]
    series["available_at"][dates()[0]] = dates()[0] + "T09:15:00+05:30"
    series["id"] = fingerprint({k: v for k, v in series.items() if k != "id"})
    changed["id"] = fingerprint({k: v for k, v in changed.items() if k != "id"})
    with pytest.raises(ValueError, match="availability changed"):
        validate_bundle(changed)


def test_receipt_integrity_and_symbol_identity_are_checked():
    stock = record()
    receipt = {
        "version": "openalgo-market-series-receipt-v1",
        "descriptor": stock["evidence"]["descriptor"],
        "symbol": "AAA",
        "exchange": "NSE",
        "interval": "D",
    }
    stock["acquisition_receipts"] = [{"sha256": fingerprint(receipt), "receipt": receipt}]
    validate_bundle(bundle(stock))
    stock["acquisition_receipts"][0]["receipt"]["symbol"] = "BBB"
    with pytest.raises(ValueError, match="integrity"):
        validate_bundle(bundle(stock))
    stock["acquisition_receipts"][0]["sha256"] = fingerprint(receipt)
    with pytest.raises(ValueError, match="another instrument"):
        validate_bundle(bundle(stock))


def test_feature_bounds_and_missing_symbol_membership_fail_before_gating(monkeypatch):
    stock = record()
    source = execution(stock)
    with pytest.raises(ValueError, match="every requested"):
        apply(source, bundle(record("BBB")), hypothesis("none"))
    monkeypatch.setattr("research.stock_features.MAX_FEATURE_BARS", 129)
    with pytest.raises(ValueError, match="session-bar limit"):
        validate_bundle(bundle(stock))


def test_nonfrozen_or_unsupported_engine_cannot_use_feature_filters():
    stock = record()
    source = execution(stock)
    source["frozen_prices"] = False
    with pytest.raises(ValueError, match="frozen"):
        apply(source, bundle(stock), hypothesis("ema_above_10"))
    source["frozen_prices"] = True
    source["portfolio"]["engine"] = "nautilus"
    with pytest.raises(ValueError, match="VectorBT"):
        apply(source, bundle(stock), hypothesis("ema_above_10"))
