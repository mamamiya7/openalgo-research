"""Role-specific windows share observed prices without widening stock lookback."""

from copy import deepcopy

import pytest
from test_stock_features import execution, record

from research import regimes, stock_relative_strength
from research.market_series import fingerprint, freeze_series, slice_series
from research.stock_market_context import (
    VERSION,
    freeze_bundle,
    market_view,
    stock_view,
    validate_bundle,
)


def fixtures(*, symbols=("AAA",), missing=(), count=220):
    values = [100 + i / 3 + (i % 7) / 10 for i in range(count)]
    full = record("NIFTY", count=count, closes=values, missing=missing)
    full["evidence"] = freeze_series(
        stock_relative_strength.BENCHMARK_DESCRIPTOR,
        full["calendar"]["sessions"],
        full["calendar"],
        full["evidence"]["bars"],
    )
    full["calendar"]["provenance"]["warmup_sessions"] = 160
    stocks = {}
    for symbol in symbols:
        item = record(symbol, count=count, closes=[v * 1.1 for v in values])
        days = item["calendar"]["sessions"][60:]
        item["evidence"] = slice_series(item["evidence"], days[0], days[-1])
        item["calendar"]["sessions"] = days
        item["calendar"]["session_hours"] = {
            day: item["calendar"]["session_hours"][day] for day in days
        }
        item["calendar"]["provenance"]["warmup_sessions"] = 100
        stocks[symbol] = item
    evidence = execution(*stocks.values())
    return stocks, full, evidence


def bundle(**kwargs):
    stocks, market, evidence = fixtures(**kwargs)
    return freeze_bundle(stocks, market, evidence["snapshot"]["sessions"]), evidence


def resign(value):
    value["id"] = fingerprint({k: v for k, v in value.items() if k != "id"})
    return value


def refreeze(record):
    value = record["evidence"]
    record["evidence"] = freeze_series(
        value["descriptor"], record["calendar"]["sessions"], record["calendar"], value["bars"]
    )


def test_exact_role_windows_and_old_v4_contract_remain_unchanged():
    context, evidence = bundle(symbols=("AAA", "BBB"))
    assert context["version"] == VERSION
    projected = stock_view(context)
    assert stock_relative_strength.validate_bundle(projected)
    assert len(projected["series"]["AAA"]["evidence"]["required_dates"]) == 160
    assert len(projected["benchmark"]["evidence"]["required_dates"]) == 160
    assert len(context["market"]["evidence"]["required_dates"]) == 220
    assert len(evidence["snapshot"]["sessions"]) == 60
    assert projected["benchmark"]["calendar"] == context["series"]["AAA"]["calendar"]
    assert (
        projected["benchmark"]["acquisition_receipts"] == context["market"]["acquisition_receipts"]
    )
    invalid_old = {**projected, "benchmark": context["market"]}
    with pytest.raises(ValueError, match="identical recorded session"):
        stock_relative_strength.validate_bundle(resign(invalid_old))
    projected["series"]["AAA"]["evidence"]["bars"].clear()
    assert context["series"]["AAA"]["evidence"]["bars"]


@pytest.mark.parametrize("hypothesis", stock_relative_strength.catalog(), ids=lambda row: row["id"])
def test_all_existing_stock_gates_keep_their_observed_causal_values(hypothesis):
    context, evidence = bundle()
    projected = stock_view(context)
    ordinary = deepcopy(projected)
    # An independently frozen100-session Nifty preparation produces identical
    # observations; only acquisition receipts differ in real preparation.
    refreeze(ordinary["benchmark"])
    resign(ordinary)
    before = stock_relative_strength.apply(evidence, ordinary, hypothesis)
    after = stock_relative_strength.apply(evidence, projected, hypothesis)
    assert before == after


def test_regime_matches_native_classifier_and_context_contract():
    context, evidence = bundle()
    market = market_view(context)
    from services.research_regimes import request_context

    assert market["context"] == request_context()
    at = evidence["snapshot"]["sessions"][0] + "T09:15:00+05:30"
    expected = regimes.classify(context["market"]["evidence"], [at])
    assert regimes.classify(market["evidence"], [at]) == expected
    assert expected["timeline"][0]["status"] == "available"
    assert expected["timeline"][0]["history"]["recorded_sessions"] == 150


def test_market_recipe_is_retained_and_cannot_be_replaced_on_replay(monkeypatch):
    context, _ = bundle()
    recorded = deepcopy(context["market_context"])
    assert market_view(context)["context"] == recorded
    changed = deepcopy(recorded["recipe"])
    changed["purpose"] = "different classifier"
    monkeypatch.setattr(regimes, "recipe", lambda: changed)
    with pytest.raises(ValueError, match="classifier recipe changed"):
        market_view(context)
    assert context["market_context"] == recorded


def test_future_prices_and_prefix_cannot_change_earlier_stock_or_market_decisions():
    original, evidence = bundle()
    decision = evidence["snapshot"]["sessions"][20] + "T09:15:00+05:30"
    changed = deepcopy(original)
    day = decision[:10]
    for item in [changed["market"], *changed["series"].values()]:
        for stamp, bar in item["evidence"]["bars"].items():
            if stamp >= day:
                bar.update(open=900, high=902, low=899, close=901)
        refreeze(item)
    resign(changed)
    assert regimes.classify(market_view(original)["evidence"], [decision]) == regimes.classify(
        market_view(changed)["evidence"], [decision]
    )
    hypothesis = next(
        row for row in stock_relative_strength.catalog() if row["id"] == "relative_20_gt0"
    )
    before = stock_relative_strength.apply(evidence, stock_view(original), hypothesis)[1][
        "membership"
    ]
    assert (
        stock_relative_strength.apply(evidence, stock_view(changed), hypothesis)[1]["membership"]
        == before
    )
    prefix, _ = bundle(count=181)
    assert regimes.classify(market_view(prefix)["evidence"], [decision]) == regimes.classify(
        market_view(original)["evidence"], [decision]
    )


def test_observed_gap_is_unknown_only_for_the_dependent_window():
    context, evidence = bundle(missing=(100,))
    at = evidence["snapshot"]["sessions"][20] + "T09:15:00+05:30"
    row = regimes.classify(market_view(context)["evidence"], [at])["timeline"][0]
    assert row["status"] == "unknown" and len(row["history"]["missing_dates"]) == 1
    hypothesis = next(
        row for row in stock_relative_strength.catalog() if row["id"] == "relative_20_gt0"
    )
    receipt = stock_relative_strength.apply(evidence, stock_view(context), hypothesis)[1]
    assert receipt["counts"]["unknown"] == 0
    missing, _ = bundle(missing=(178,))
    receipt = stock_relative_strength.apply(evidence, stock_view(missing), hypothesis)[1]
    assert receipt["counts"]["unknown"] == 1


@pytest.mark.parametrize("change", ["calendar_gap", "hours", "source", "wrong_index", "hash"])
def test_context_validation_refuses_calendar_instrument_and_integrity_changes(change):
    context, _ = bundle()
    if change == "calendar_gap":
        market = context["market"]
        day = market["calendar"]["sessions"].pop(20)
        market["calendar"]["session_hours"].pop(day)
        market["evidence"]["bars"].pop(day)
        refreeze(market)
    elif change == "hours":
        day = context["execution_sessions"][0]
        context["market"]["calendar"]["session_hours"][day]["close"] = "15:29"
        refreeze(context["market"])
    elif change == "source":
        context["market"]["calendar"]["provenance"]["calendar_source"] = "another-calendar"
    elif change == "wrong_index":
        context["market"]["evidence"]["descriptor"]["symbol"] = "BANKNIFTY"
        refreeze(context["market"])
    else:
        context["id"] = "0" * 64
    if change != "hash":
        resign(context)
    with pytest.raises(ValueError):
        validate_bundle(context)


@pytest.mark.parametrize("change", ["instrument", "window", "hash"])
def test_cross_instrument_or_session_receipts_cannot_be_attached(change):
    context, _ = bundle()
    descriptor = context["market"]["evidence"]["descriptor"]
    receipt = {
        "version": "openalgo-market-series-receipt-v1",
        "descriptor": descriptor,
        "symbol": "NIFTY",
        "exchange": "NSE_INDEX",
        "interval": "D",
        "accepted_slots": context["execution_sessions"][:1],
    }
    if change == "instrument":
        receipt["symbol"] = "AAA"
    elif change == "window":
        receipt["accepted_slots"] = ["2024-01-01"]
    context["market"]["acquisition_receipts"] = [
        {"receipt": receipt, "sha256": "0" * 64 if change == "hash" else fingerprint(receipt)}
    ]
    resign(context)
    with pytest.raises(ValueError, match="receipt"):
        validate_bundle(context)
