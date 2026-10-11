"""Hand-checked price ratios, causal gates and frozen benchmark integrity."""

from copy import deepcopy
from datetime import datetime

import pytest
from test_stock_features import bundle as old_bundle
from test_stock_features import dates, execution, record

from research import stock_indicators as indicators
from research import stock_relative_strength as relative
from research.market_series import fingerprint, freeze_series


def hypothesis(identifier):
    return next(row for row in relative.catalog() if row["id"] == identifier)


def benchmark(*, closes=None, missing=(), count=130):
    value = record("NIFTY", closes=closes, missing=missing, count=count)
    series = value["evidence"]
    value["evidence"] = freeze_series(
        relative.BENCHMARK_DESCRIPTOR, series["required_dates"], value["calendar"], series["bars"]
    )
    return value


def bundle(stock, nifty=None):
    payload = {
        "version": relative.VERSION,
        "series": {stock["evidence"]["descriptor"]["symbol"]: stock},
        "benchmark": benchmark() if nifty is None else nifty,
    }
    return {**payload, "id": fingerprint(payload)}


def calculate(stock, nifty, identifier, *, minute=False):
    rows, receipt = relative.apply(
        execution(stock, minute=minute), bundle(stock, nifty), hypothesis(identifier)
    )
    return rows, receipt, receipt["membership"][0]


def refreeze(value):
    series = value["evidence"]
    value["evidence"] = freeze_series(
        series["descriptor"], series["required_dates"], value["calendar"], series["bars"]
    )
    return value


def test_catalog_identity_portable_markers_and_pinned_recipe():
    choices = relative.catalog()
    assert len(choices) == len({row["id"] for row in choices}) == 23
    assert len({row["family"] for row in choices}) == 8
    assert choices[:18] == indicators.catalog()
    assert relative.runtime_version() == "2.0.5"
    assert relative.recipe()["benchmark"] == relative.BENCHMARK_DESCRIPTOR
    assert (
        "not stock return minus benchmark return"
        in relative.recipe()["relative_strength"]["interpretation"]
    )
    source = execution(record())["strategies"]
    original = deepcopy(source)
    chosen = hypothesis("ema20_and_relative20_gt0")
    assert chosen["components"] == ["ema_above_20", "relative_20_gt0"]
    marked = relative.markers(source, chosen, "a" * 64)
    assert marked == relative.markers(source, chosen, "b" * 64)
    assert marked[0]["entry_condition"] == {"version": relative.ENTRY_VERSION, "hypothesis": chosen}
    assert relative.markers(marked, hypothesis("none")) == original and source == original
    chosen["components"].append("atr_14_pct_lt4")
    with pytest.raises(ValueError, match="recorded catalog"):
        relative.normalize_hypothesis(chosen)


@pytest.mark.parametrize("identifier", [row["id"] for row in indicators.catalog()])
def test_original_eighteen_conditions_keep_identical_math_without_benchmark_prices(identifier):
    stock = record(closes=[100 + i / 10 + (i % 7 - 3) for i in range(130)])
    missing = benchmark(missing=tuple(range(130)))
    _, old = indicators.apply(execution(stock), old_bundle(stock), hypothesis(identifier))
    _, new, _ = calculate(stock, missing, identifier)
    assert new["membership"] == old["membership"] and new["counts"] == old["counts"]
    assert new["calculation_version"] == relative.FEATURE_VERSION


@pytest.mark.parametrize("lookback", [5, 20, 60])
def test_relative_ratio_has_exact_horizon_and_differs_from_subtracting_returns(lookback):
    closes, index = [100.0] * 130, [1000.0] * 130
    closes[119], index[119] = 120, 1100
    stock, nifty = record(closes=closes), benchmark(closes=index)
    _, receipt, row = calculate(stock, nifty, f"relative_{lookback}_gt0")
    feature = row["feature"]
    assert feature["value"] == pytest.approx(100 * (1.2 / 1.1 - 1))
    assert feature["value"] != pytest.approx(20 - 10)
    assert feature["stock_return_pct"] == pytest.approx(20)
    assert feature["benchmark_return_pct"] == pytest.approx(10)
    assert feature["stock_first_close"] == 100 and feature["benchmark_first_close"] == 1000
    assert feature["close"] == 120 and feature["benchmark_close"] == 1100
    assert feature["observations"] == lookback + 1
    assert feature["from"] == dates()[119 - lookback] and feature["to"] == dates()[119]
    assert feature["available_at"] == dates()[119] + "T15:30:00+05:30"
    assert receipt["counts"]["allowed"] == 1


@pytest.mark.parametrize(
    "stock_last,index_last,expected", [(102, 100, 2), (90, 80, 12.5), (110, 120, -100 / 12)]
)
def test_threshold_and_declining_market_interpretation(stock_last, index_last, expected):
    closes, index = [100.0] * 130, [100.0] * 130
    closes[119], index[119] = stock_last, index_last
    _, receipt, row = calculate(record(closes=closes), benchmark(closes=index), "relative_20_gt2")
    assert row["feature"]["value"] == pytest.approx(expected)
    assert receipt["counts"]["allowed" if expected > 2 else "filtered"] == 1
    for identifier in ("relative_5_gt0", "relative_20_gt2"):
        chosen = hypothesis(identifier)
        assert not indicators._compare({"value": chosen["threshold"] + 1e-13}, chosen)


@pytest.mark.parametrize("which", ["stock", "benchmark"])
@pytest.mark.parametrize("offset", [60, 90, 118, 119])
def test_missing_any_required_observation_never_skips_or_fills(which, offset):
    stock = record(missing=(offset,) if which == "stock" else ())
    nifty = benchmark(missing=(offset,) if which == "benchmark" else ())
    rows, receipt, row = calculate(stock, nifty, "relative_60_gt0")
    assert receipt["counts"]["unknown"] == 1 and row["feature"] is None
    assert dates()[offset] in rows[0]["signals"][0]["research_exclusion"]


def test_missing_outside_required_window_does_not_change_ratio_basis():
    source = record(closes=[100 + i for i in range(130)])
    a = calculate(source, benchmark(), "relative_5_gt0")[2]
    b = calculate(source, benchmark(missing=(0, 40, 113, 125)), "relative_5_gt0")[2]
    assert a == b


def test_both_instruments_full_observations_bind_basis_even_when_middle_close_is_not_formula_input():
    stock, nifty = record(closes=[100 + i / 10 for i in range(130)]), benchmark()
    before = calculate(stock, nifty, "relative_20_gt0")[2]["feature"]
    for changed, key in ((deepcopy(stock), "stock"), (deepcopy(nifty), "benchmark")):
        bar = changed["evidence"]["bars"][dates()[110]]
        bar.update(open=105, high=107, low=104, close=106)
        refreeze(changed)
        after = calculate(
            changed if key == "stock" else stock,
            changed if key == "benchmark" else nifty,
            "relative_20_gt0",
        )[2]["feature"]
        assert before["value"] == after["value"] and before["basis_id"] != after["basis_id"]


@pytest.mark.parametrize(
    "identifier",
    ["relative_5_gt0", "relative_20_gt2", "relative_60_gt0", "ema20_and_relative20_gt0"],
)
def test_prefix_future_and_intraday_closes_cannot_leak_into_entry(identifier):
    closes = [100 + i for i in range(130)]
    stock, nifty = record(closes=closes), benchmark()
    future_stock = record(closes=closes[:120] + [9000.0] * 10)
    future_nifty = benchmark(closes=[100.0] * 120 + [10000.0] * 10)
    a = calculate(stock, nifty, identifier)[2]
    b = calculate(future_stock, future_nifty, identifier)[2]
    prefix = bundle(record(closes=closes[:121], count=121), benchmark(count=121))
    c = relative.apply(execution(stock), prefix, hypothesis(identifier))[1]["membership"][0]
    assert a == b == c
    minute_stock = record(closes=closes[:119] + [9000.0] * 11)
    minute_nifty = benchmark(closes=[100.0] * 119 + [10000.0] * 11)
    a = calculate(stock, nifty, identifier, minute=True)[2]
    b = calculate(minute_stock, minute_nifty, identifier, minute=True)[2]
    assert a == b and a["feature"]["to"] == dates()[118]
    assert a["decision_at"] == dates()[119] + "T09:16:00+05:30"


@pytest.mark.parametrize("which", ["stock", "benchmark"])
def test_delayed_close_is_unknown_even_if_bar_is_present(which):
    stock, nifty = record(), benchmark()
    record_to_delay = stock if which == "stock" else nifty
    record_to_delay["evidence"]["available_at"][dates()[119]] = dates()[120] + "T10:00:00+05:30"
    feature, reason = relative._feature(
        stock,
        nifty,
        datetime.fromisoformat(dates()[120] + "T09:15:00+05:30"),
        hypothesis("relative_5_gt0"),
    )
    assert feature is None and "not available" in reason


def test_conjunction_is_all_known_and_true_and_unknown_dominates_false():
    stock = record(closes=[100 + i / 10 for i in range(130)])
    _, receipt, row = calculate(stock, benchmark(), "ema20_and_relative20_gt0")
    assert receipt["counts"]["allowed"] == 1 and row["feature"]["passed"] is True
    assert all(item["passed"] for item in row["feature"]["components"])
    _, receipt, row = calculate(
        stock, benchmark(closes=[100 + i for i in range(130)]), "ema20_and_relative20_gt0"
    )
    assert receipt["counts"]["filtered"] == 1 and row["feature"]["passed"] is False
    _, receipt, row = calculate(record(), benchmark(missing=(118,)), "ema20_and_relative20_gt0")
    assert receipt["counts"]["unknown"] == 1 and "Nifty" in row["reason"]


def test_insufficient_warmup_is_unknown():
    stock = record()
    _, receipt = relative.apply(
        execution(stock, indexes=(40,), start=0), bundle(stock), hypothesis("relative_60_gt0")
    )
    assert receipt["counts"]["unknown"] == 1
    assert "61 earlier consecutive" in receipt["membership"][0]["reason"]


@pytest.mark.parametrize("tamper", ["bundle", "instrument", "receipt", "calendar", "availability"])
def test_frozen_context_refuses_tampered_or_wrong_instrument_evidence(tamper):
    stock, nifty = record(), benchmark()
    if tamper == "instrument":
        series = nifty["evidence"]
        nifty["evidence"] = freeze_series(
            {**relative.BENCHMARK_DESCRIPTOR, "symbol": "NIFTYBANK"},
            series["required_dates"],
            nifty["calendar"],
            series["bars"],
        )
    elif tamper == "receipt":
        receipt = {
            "version": "openalgo-market-series-receipt-v1",
            "descriptor": nifty["evidence"]["descriptor"],
            "symbol": "NIFTY",
            "exchange": "NSE",
            "interval": "D",
        }
        nifty["acquisition_receipts"] = [{"sha256": fingerprint(receipt), "receipt": receipt}]
    elif tamper == "calendar":
        nifty["calendar"]["session_hours"][dates()[0]]["open"] = "09:20"
        refreeze(nifty)
    elif tamper == "availability":
        nifty["calendar"]["session_hours"][dates()[0]]["close"] = "15:00"
    value = bundle(stock, nifty)
    if tamper == "bundle":
        value["benchmark"]["evidence"]["bars"][dates()[0]]["close"] = 100.5
    with pytest.raises(ValueError):
        relative.validate_bundle(value)


def test_sdk_fence_fails_before_opportunities_are_gated(monkeypatch):
    monkeypatch.setattr(indicators.metadata, "version", lambda package: "2.0.6")
    with pytest.raises(ValueError, match="recorded OpenAlgo SDK"):
        relative.apply(execution(record()), bundle(record()), hypothesis("relative_5_gt0"))


@pytest.mark.parametrize("which", ["stock", "benchmark"])
def test_extreme_positive_prices_are_unknown_not_overflow_or_fabricated_return(which):
    stock, nifty = record(), benchmark()
    extreme = stock if which == "stock" else nifty
    for index, price in ((114, 1e-300), (119, 1e300)):
        extreme["evidence"]["bars"][dates()[index]].update(
            open=price, high=price, low=price, close=price
        )
    refreeze(extreme)
    _, receipt, row = calculate(stock, nifty, "relative_5_gt0")
    assert receipt["counts"]["unknown"] == 1 and row["feature"] is None
    assert "finite calculation bounds" in row["reason"]


def test_resources_are_call_local_and_inputs_are_immutable():
    stock, nifty = record(closes=[100 + i for i in range(130)]), benchmark()
    source, context = execution(stock), bundle(stock, nifty)
    originals = deepcopy((source, context))
    first = relative.apply(source, context, hypothesis("relative_20_gt0"))
    for _ in range(30):
        assert relative.apply(source, context, hypothesis("relative_20_gt0")) == first
    assert (source, context) == originals
