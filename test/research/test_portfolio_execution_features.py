"""Genuine frozen price/volume derivation through native constrained fills."""

from copy import deepcopy
from math import cos, pi, sin

import pytest
from test_capacity_allocation_native import capacity_policy
from test_stock_features import execution
from test_stock_market_context import bundle, refreeze, resign

from research import execution_features as atr
from research import portfolio_execution_features as features
from research import volume_provenance as volume
from research.connectors.vectorbt_portfolio import evaluate
from research.engine import validate_config
from research.portfolio import fingerprint

pytestmark = pytest.mark.timeout(120)


def source(*, symbols=("AAA", "BBB"), minute=False, quantity=300, count=220):
    context, _ = bundle(symbols=symbols, count=count)
    for symbol, record in context["series"].items():
        value = 100.0
        for index, day in enumerate(record["calendar"]["sessions"]):
            change = 0.003 * sin(2 * pi * index / 10)
            if symbol == "CCC":
                change = 0.003 * cos(2 * pi * index / 10)
            elif symbol == "DDD":
                change = -change
            elif symbol == "EEE":
                change = 0.0
            if index:
                value *= 1 + change
            record["evidence"]["bars"][day].update(
                open=value,
                high=value + 1,
                low=value - 1,
                close=value,
            )
        refreeze(record)
    resign(context)
    evidence = execution(*context["series"].values(), minute=minute)
    evidence["automatic_stock_market_context"] = context
    for strategy in evidence["strategies"]:
        strategy["execution_policy"] = capacity_policy(capacity=True, correlation=True)
        strategy["config"] = validate_config(
            {
                **strategy["config"],
                "cost_bps": 0,
                "order_size_pct": 30,
                "stop_pct": 20,
                "target_pct": 500,
            }
        )
    evidence["snapshot"]["provenance"].update(
        provider="controlled-test",
        exchange="NSE",
        adjustment_basis="synthetic",
        calendar_basis="explicit-test-sessions",
        synthetic=True,
    )
    if not minute:
        evidence["snapshot"]["bars"] = {
            symbol: {
                day: deepcopy(record["evidence"]["bars"][day])
                for day in evidence["snapshot"]["sessions"]
            }
            for symbol, record in context["series"].items()
        }
    records = {}
    for symbol, record in context["series"].items():
        rows = {
            day: {**deepcopy(bar), "volume": quantity, "oi": 0}
            for day, bar in record["evidence"]["bars"].items()
        }
        records[symbol] = volume.capture_rows(
            symbol,
            list(rows.values()),
            rows,
            broker="fyers",
            adapter_sha256=volume.REVIEWED_ADAPTER_SHA256,
            captured_at="2026-10-21T00:00:00+00:00",
        )
    return evidence, context, records


def prepare(evidence, context, records):
    return features.prepare(atr.prepare(evidence, context), context, records)


def feature(result, symbol="AAA", kind="correlation"):
    observations = result["portfolio_execution_features"][kind]
    key = next(key for key in observations if key.startswith(symbol + "|"))
    value = observations[key]
    return value["feature"] if kind == "capacity" else value


def reseal(value):
    value["id"] = fingerprint({key: item for key, item in value.items() if key != "id"})


def fresh(result):
    return {
        key: deepcopy(value)
        for key, value in result.items()
        if key not in ("execution_features", "portfolio_execution_features")
    }


def test_derives_exact_prior_sixty_returns_and_known_dependence():
    evidence, context, records = source(symbols=("AAA", "BBB", "CCC", "DDD", "EEE"))
    before = deepcopy((evidence, context, records))
    result = prepare(evidence, context, records)
    point = feature(result)
    days = context["series"]["AAA"]["calendar"]["sessions"]
    expected = [day for day in days if day < point["entry_session"]][-61:]
    assert point["observations"] == 60
    assert (point["from"], point["to"]) == (expected[0], expected[-1])
    assert len(expected) == 61
    assert point["correlations"]["AAA"] == 1
    assert point["correlations"]["BBB"] == pytest.approx(1)
    assert point["correlations"]["CCC"] == pytest.approx(0, abs=1e-10)
    assert point["correlations"]["DDD"] == pytest.approx(-1)
    assert point["correlations"]["EEE"] is None
    cap = feature(result, kind="capacity")
    assert cap["observations"] == 20 and cap["mean_daily_shares"] == 300
    assert cap["basis"] == "provider_returned_historical_coordinates"
    assert (evidence, context, records) == before


def test_full_builder_attach_and_native_capacity_consume_duplicate_symbol_buys():
    evidence, context, records = source(symbols=("AAA",))
    strategy = evidence["strategies"][0]
    strategy["signals"] *= 2
    strategy["execution_policy"] = capacity_policy(
        correlation=True, max_correlated_exposure_pct=100
    )
    prepared = prepare(evidence, context, records)
    attached = features.attach(atr.attach(prepared))
    assert all(signal.get("entry_capacity") for signal in attached["strategies"][0]["signals"])
    report = evaluate(attached["strategies"], attached["snapshot"], 1000)
    assert sum(row["quantity"] for row in report["ledger"]) == 3
    assert report["execution"]["capacity"]["daily_symbols"][0]["bought_shares"] == 3
    assert "daily_volume_participation" in report["ledger"][-1]["execution_details"]["constraints"]
    assert features.prepare(prepared, context) == prepared
    assert features.attach(attached) == attached


def test_full_builder_native_correlation_clips_a_second_related_symbol():
    evidence, context, records = source(quantity=100000)
    evidence["strategies"][0]["execution_policy"] = capacity_policy(
        correlation=True, max_correlated_exposure_pct=25
    )
    attached = features.attach(atr.attach(prepare(evidence, context, records)))
    report = evaluate(attached["strategies"], attached["snapshot"], 1000)
    assert report["ledger"][0]["quantity"] > 0
    assert report["ledger"][1]["quantity"] == 0
    assert "correlated_exposure" in report["ledger"][1]["reason"]


def test_constant_pair_is_unknown_and_skips_only_when_that_pair_is_held():
    evidence, context, records = source(symbols=("AAA", "EEE"), quantity=100000)
    evidence["strategies"][0]["execution_policy"] = capacity_policy(
        correlation=True, max_correlated_exposure_pct=100
    )
    attached = features.attach(atr.attach(prepare(evidence, context, records)))
    assert all(not row.get("research_exclusion") for row in attached["strategies"][0]["signals"])
    report = evaluate(attached["strategies"], attached["snapshot"], 1000)
    assert report["ledger"][0]["quantity"] > 0
    assert report["ledger"][1]["quantity"] == 0
    assert "correlation_unavailable" in report["ledger"][1]["reason"]
    one = deepcopy(attached["strategies"])
    one[0]["signals"] = one[0]["signals"][1:]
    assert evaluate(one, attached["snapshot"], 1000)["ledger"][0]["quantity"] > 0


def test_current_and_future_price_volume_changes_leave_prior_features_identical():
    evidence, context, records = source()
    before = prepare(evidence, context, records)
    entry = feature(before)["entry_session"]
    changed = deepcopy(context)
    volumes = deepcopy(records)
    for record in [changed["market"], *changed["series"].values()]:
        for day, bar in record["evidence"]["bars"].items():
            if day >= entry:
                bar.update(open=900, high=901, low=899, close=900)
        refreeze(record)
    for values in volumes.values():
        for day, row in values.items():
            if day >= entry:
                row["candle"]["volume"] = 10**12
                row["id"] = "bad-future-seal"
    resign(changed)
    new_source = fresh(evidence)
    new_source["automatic_stock_market_context"] = changed
    after = prepare(new_source, changed, volumes)
    for symbol in ("AAA", "BBB"):
        assert feature(before, symbol) == feature(after, symbol)
        assert feature(before, symbol, "capacity") == feature(after, symbol, "capacity")


def test_prefix_invariance_and_older_observations_cannot_leak_into_exact_window():
    evidence, context, records = source()
    before = prepare(evidence, context, records)
    entry = feature(before)["entry_session"]
    changed = deepcopy(context)
    for record in changed["series"].values():
        days = [day for day in record["calendar"]["sessions"] if day < entry][-61:]
        for day, bar in record["evidence"]["bars"].items():
            if day < days[0]:
                bar.update(open=700, high=701, low=699, close=700)
        refreeze(record)
    resign(changed)
    new_source = fresh(evidence)
    new_source["automatic_stock_market_context"] = changed
    # This test isolates the correlation window; ATR deliberately uses a longer history.
    after = prepare(new_source, changed, records)
    assert feature(after) == feature(before)


def test_shorter_recorded_execution_prefix_has_identical_entry_features():
    full = prepare(*source())
    prefix = prepare(*source(count=181))
    for symbol in ("AAA", "BBB"):
        assert feature(prefix, symbol) == feature(full, symbol)
        assert feature(prefix, symbol, "capacity") == feature(full, symbol, "capacity")


def test_nonfinite_derived_return_is_unavailable_even_with_finite_positive_closes():
    _, context, _ = source(symbols=("AAA",))
    record = deepcopy(context["series"]["AAA"])
    day = record["calendar"]["sessions"][120]
    earlier = record["calendar"]["sessions"][59:120]
    record["evidence"]["bars"][earlier[0]]["close"] = 1e-300
    record["evidence"]["bars"][earlier[1]]["close"] = 1e300
    assert features._returns(record, day) is None


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -1, 0, True, 2**53])
def test_capacity_entry_rejects_nonfinite_or_unsupported_quantity(value):
    result = prepare(*source(symbols=("AAA",)))
    point = deepcopy(feature(result, kind="capacity"))
    point["mean_daily_shares"] = value
    if value == value and value != float("inf"):
        reseal(point)
    with pytest.raises(ValueError):
        features.validate_capacity_entry(
            point,
            symbol="AAA",
            entry_session=point["entry_session"],
            decision_at=point["entry_session"] + "T09:15:00+05:30",
        )


@pytest.mark.parametrize("problem", ["missing", "delayed"])
def test_missing_expected_close_or_late_availability_is_not_skipped(problem):
    evidence, context, records = source()
    initial = prepare(evidence, context, records)
    point = feature(initial)
    changed = deepcopy(context)
    item = changed["series"]["AAA"]
    if problem == "missing":
        item["evidence"]["bars"].pop(point["from"])
        refreeze(item)
    else:
        item["evidence"]["available_at"][point["to"]] = point["entry_session"] + "T10:00:00+05:30"
        reseal(item["evidence"])
    resign(changed)
    new_source = fresh(evidence)
    new_source["automatic_stock_market_context"] = changed
    if problem == "delayed":
        # freeze_series' public contract itself rejects edited availability before calculation.
        with pytest.raises(ValueError):
            prepare(new_source, changed, records)
    else:
        result = prepare(new_source, changed, records)
        assert feature(result) is None
        assert (
            "correlation_unavailable"
            in features.attach(result)["strategies"][0]["signals"][0]["research_exclusion"]
        )


@pytest.mark.parametrize(
    "mutation", ["hash", "context", "source_opportunity", "source_prices", "rehashed_feature"]
)
def test_altered_saved_sources_and_features_are_rejected(mutation):
    evidence, context, records = source()
    result = prepare(evidence, context, records)
    changed = deepcopy(result)
    receipt = changed["portfolio_execution_features"]
    if mutation == "hash":
        receipt["id"] = "0" * 64
    elif mutation == "context":
        changed["automatic_stock_market_context"]["id"] = "0" * 64
    elif mutation == "source_opportunity":
        changed["strategies"][0]["signals"][0]["row"] += 1
    elif mutation == "source_prices":
        changed_context = deepcopy(context)
        record = changed_context["series"]["AAA"]
        record["evidence"]["bars"][feature(result)["to"]]["close"] += 0.1
        refreeze(record)
        resign(changed_context)
        with pytest.raises(ValueError):
            features.prepare(changed, changed_context, records)
        return
    else:
        value = feature(changed)
        value["correlations"]["BBB"] = 0.1
        reseal(value)
        reseal(receipt)
        with pytest.raises(ValueError, match="reproduce"):
            features.prepare(changed, context, records)
        return
    with pytest.raises(ValueError):
        features.attach(changed)


@pytest.mark.parametrize("kind", ["capacity", "correlation"])
@pytest.mark.parametrize(
    "change", ["symbol", "date", "future", "naive", "hash", "extra", "source_id"]
)
def test_entry_validator_rejects_cross_boundaries_and_changed_metadata(kind, change):
    evidence, context, records = source()
    result = prepare(evidence, context, records)
    original = feature(result, kind=kind)
    point = deepcopy(original)
    if change == "symbol":
        point["symbol"] = "BBB"
    elif change == "date":
        point["entry_session"] = "2025-12-31"
    elif change == "future":
        point["available_at"] = point["entry_session"] + "T10:00:00+05:30"
    elif change == "naive":
        point["available_at"] = point["to"] + "T15:30:00"
    elif change == "extra":
        point["private"] = "unrecognized"
    elif change == "source_id":
        point["source_id"] = "invalid"
    else:
        point["id"] = "0" * 64
    if change != "hash":
        reseal(point)
    validate = (
        features.validate_capacity_entry
        if kind == "capacity"
        else features.validate_correlation_entry
    )
    kwargs = {} if kind == "capacity" else {"required_symbols": ["AAA", "BBB"]}
    with pytest.raises(ValueError):
        validate(
            point,
            symbol="AAA",
            entry_session=original["entry_session"],
            decision_at=original["entry_session"] + "T09:15:00+05:30",
            **kwargs,
        )


def test_common_clock_missing_volume_masks_original_and_later_clock(monkeypatch):
    from research.automatic_timing import descriptor

    evidence, context, records = source(symbols=("AAA",), minute=True)
    day = evidence["strategies"][0]["signals"][0]["date"]
    evidence["snapshot"]["session_hours"][day]["close"] = "09:20"
    evidence["snapshot"]["timeline"] = [
        slot
        for slot in evidence["snapshot"]["timeline"]
        if slot[:10] != day or slot[11:16] < "09:20"
    ]
    for record in [context["market"], *context["series"].values()]:
        record["calendar"]["session_hours"][day]["close"] = "09:20"
        refreeze(record)
    resign(context)
    timing = descriptor(evidence)
    timing["strategies"][0]["branches"].append(
        {
            "id": "later",
            "label": "Controlled late clock",
            "changes": {"entry_time": "09:30"},
        }
    )
    monkeypatch.setattr("research.automatic_timing.descriptor", lambda *_a, **_k: deepcopy(timing))
    # Original same-day entry needs prior20 only. Late clock shifts to the next
    # session, whose prior20 includes this absent signal-day volume observation.
    records["AAA"].pop(day)
    result = prepare(evidence, context, records)
    counts = result["portfolio_execution_features"]["counts"]
    assert counts["entry_windows"] == 2 and counts["capacity_available"] == 1
    original = features.attach(atr.attach(result))["strategies"][0]["signals"][0]
    assert "capacity_unavailable" in original["research_exclusion"]
    later = deepcopy(result)
    later["strategies"][0]["config"]["entry_time"] = "09:30"
    assert (
        "capacity_unavailable"
        in features.attach(later)["strategies"][0]["signals"][0]["research_exclusion"]
    )


def test_bounded_work_rejects_before_feature_allocation(monkeypatch):
    evidence, context, records = source()
    value = atr.prepare(evidence, context)
    monkeypatch.setattr(features, "MAX_PAIRS", 1)
    with pytest.raises(ValueError, match="bounded"):
        features.prepare(value, context, records)


def test_manual_execution_uses_actual_clock_without_requiring_automatic_atr_context():
    evidence, context, records = source()
    automatic = prepare(evidence, context, records)
    manual = features.prepare(evidence, context, records)
    assert manual["portfolio_execution_features"]["atr_id"] is None
    assert "execution_features" not in manual
    assert feature(manual) == feature(automatic)
    assert feature(manual, kind="capacity") == feature(automatic, kind="capacity")
    report = evaluate(features.attach(manual)["strategies"], manual["snapshot"], 1000)
    assert report["execution"]["capacity"]["daily_symbols"]


def test_portfolio_receipt_rejects_resealed_different_original_clock_union():
    result = prepare(*source())
    changed = deepcopy(result)
    changed["execution_features"]["counts"]["opportunities"] += 1
    reseal(changed["execution_features"])
    with pytest.raises(ValueError):
        features.attach(changed)


def test_volume_ohlc_must_match_the_exact_frozen_warmup_source():
    evidence, context, records = source(symbols=("AAA",))
    first = prepare(evidence, context, records)
    last = feature(first, kind="capacity")["to"]
    changed = deepcopy(records)
    record = changed["AAA"][last]
    record["candle"]["close"] += 0.1
    record["candle_id"] = volume.fingerprint(record["candle"])
    record["id"] = volume.fingerprint({key: value for key, value in record.items() if key != "id"})
    assert volume.validate_observation(record, "AAA", last)
    result = prepare(evidence, context, changed)
    assert feature(result, kind="capacity") is None
    assert (
        "capacity_unavailable"
        in features.attach(result)["strategies"][0]["signals"][0]["research_exclusion"]
    )


@pytest.mark.parametrize("quantity", [0, 300])
def test_zero_capacity_or_unknown_broker_profile_remains_unavailable(quantity):
    evidence, context, records = source(symbols=("AAA",), quantity=quantity)
    if quantity:
        rows = {day: record["candle"] for day, record in records["AAA"].items()}
        records["AAA"] = volume.capture_rows(
            "AAA",
            list(rows.values()),
            rows,
            broker="zerodha",
            adapter_sha256=volume.REVIEWED_ADAPTER_SHA256,
            captured_at="2026-10-21T00:00:00+00:00",
        )
    result = prepare(evidence, context, records)
    assert feature(result, kind="capacity") is None
    assert feature(result) is not None
    actual = features.attach(result)["strategies"][0]["signals"][0]
    assert "capacity_unavailable" in actual["research_exclusion"]


def test_resume_reproduces_exact_frozen_receipts_and_attachment_is_read_only(monkeypatch):
    evidence, context, records = source()
    result = prepare(evidence, context, records)
    before = deepcopy(result)
    assert features.prepare(deepcopy(result), deepcopy(context)) == result
    attached = features.attach(result)
    monkeypatch.setattr(
        features, "_returns", lambda *_a, **_k: pytest.fail("trial recomputed returns")
    )
    for _ in range(20):
        assert features.attach(result) == attached
    assert result == before
