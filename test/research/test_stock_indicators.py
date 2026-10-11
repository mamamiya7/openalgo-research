"""Native numerical references and causal invariants for bounded stock hypotheses."""

from copy import deepcopy
from datetime import datetime
from math import sqrt

import pytest
from openalgo import ta
from test_stock_features import bundle, dates, execution, record

from research import stock_features as legacy
from research import stock_indicators as indicators
from research.market_series import freeze_series
from research.report_contract import settings_identity


def hypothesis(identifier):
    return next(row for row in indicators.catalog() if row["id"] == identifier)


def calculate(stock, identifier, *, minute=False):
    rows, receipt = indicators.apply(
        execution(stock, minute=minute), bundle(stock), hypothesis(identifier)
    )
    return rows, receipt, receipt["membership"][0]


def refreeze(stock):
    series = stock["evidence"]
    stock["evidence"] = freeze_series(
        series["descriptor"], series["required_dates"], stock["calendar"], series["bars"]
    )
    return stock


def rsi_reference(closes, period):
    changes = [right - left for left, right in zip(closes[:-1], closes[1:], strict=True)]
    gain = sum(max(0, item) for item in changes[:period]) / period
    loss = sum(max(0, -item) for item in changes[:period]) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(0, change)) / period
        loss = (loss * (period - 1) + max(0, -change)) / period
    return 100 if loss == 0 else 100 - 100 / (1 + gain / loss)


def atr_reference(bars, period):
    ranges = [bars[0]["high"] - bars[0]["low"]]
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    value = sum(ranges[:period]) / period
    for item in ranges[period:]:
        value = (value * (period - 1) + item) / period
    return value


def test_catalog_is_bounded_exact_and_markers_are_portable_without_changing_old_descriptors():
    choices = indicators.catalog()
    assert len(choices) == len({row["id"] for row in choices}) == 18
    assert choices[:7] == legacy.catalog()
    assert len({row["family"] for row in choices}) == 7
    assert all(len(row.get("components", [])) <= 2 for row in choices)
    assert indicators.runtime_version() == "2.0.5"
    settings = execution(record())["strategies"]
    original = deepcopy(settings)
    chosen = hypothesis("ema20_and_rsi14_gt50")
    first = indicators.markers(settings, chosen, "a" * 64)
    second = indicators.markers(settings, chosen, "b" * 64)
    assert first == second and settings == original
    assert first[0]["entry_condition"] == {
        "version": indicators.ENTRY_VERSION,
        "hypothesis": chosen,
    }
    assert settings_identity(first) != settings_identity(settings)
    assert indicators.markers(first, hypothesis("none")) == original
    chosen["components"].append("atr_14_pct_lt4")
    with pytest.raises(ValueError, match="recorded catalog"):
        indicators.normalize_hypothesis(chosen)
    invalid = hypothesis("rsi_14_gt50")
    invalid["lookback"] = 21
    with pytest.raises(ValueError, match="recorded catalog"):
        indicators.normalize_hypothesis(invalid)


@pytest.mark.parametrize("identifier", [row["id"] for row in legacy.catalog()])
def test_all_legacy_feature_values_and_decisions_are_unchanged(identifier):
    stock = record(closes=[100 + i / 10 for i in range(130)])
    value = execution(stock)
    _, old = legacy.apply(value, bundle(stock), hypothesis(identifier))
    _, new, _ = calculate(stock, identifier)
    assert new["membership"] == old["membership"]
    assert new["counts"] == old["counts"]
    assert new["version"] == legacy.VERSION
    assert new["calculation_version"] == indicators.FEATURE_VERSION
    assert new["feature_recipe"]["dependency"] == {"package": "openalgo", "version": "2.0.5"}


@pytest.mark.parametrize("identifier", ["rsi_14_gt50", "rsi_14_gt60", "rsi_14_lt30", "rsi_7_lt30"])
def test_rsi_matches_hand_wilder_recursion_and_native_api(identifier):
    closes = [100 + i / 10 + (i % 7 - 3) * 2 for i in range(130)]
    _, _, row = calculate(record(closes=closes), identifier)
    feature, period = row["feature"], hypothesis(identifier)["lookback"]
    assert feature["value"] == pytest.approx(rsi_reference(closes[20:120], period), abs=1e-12)
    assert feature["value"] == float(ta.rsi(closes[20:120], period=period)[-1])
    assert feature["observations"] == 100
    assert feature["from"] == dates()[20] and feature["to"] == dates()[119]


@pytest.mark.parametrize(
    "identifier,latest", [("bbands_20_gt_upper2", 110), ("bbands_20_lt_lower2", 90)]
)
def test_bollinger_bands_match_hand_population_variance_and_native_api(identifier, latest):
    closes = [100.0] * 130
    closes[119] = latest
    _, receipt, row = calculate(record(closes=closes), identifier)
    feature = row["feature"]
    middle = (19 * 100 + latest) / 20
    variance = (19 * (100 - middle) ** 2 + (latest - middle) ** 2) / 20
    assert feature["middle"] == pytest.approx(middle)
    assert feature["upper"] == pytest.approx(middle + 2 * sqrt(variance))
    assert feature["lower"] == pytest.approx(middle - 2 * sqrt(variance))
    assert [feature[key] for key in ("upper", "middle", "lower")] == [
        float(values[-1]) for values in ta.bbands(closes[100:120], period=20, std_dev=2)
    ]
    assert receipt["counts"]["allowed"] == 1 and feature["observations"] == 20


def test_atr_matches_hand_wilder_ranges_including_gap_and_native_api():
    closes = [100 + i / 10 + (i % 9 - 4) * 3 for i in range(130)]
    stock = record(closes=closes)
    _, _, row = calculate(stock, "atr_14_pct_lt4")
    bars = [stock["evidence"]["bars"][day] for day in dates()[20:120]]
    expected = atr_reference(bars, 14)
    assert row["feature"]["atr"] == pytest.approx(expected, abs=1e-12)
    assert row["feature"]["value"] == pytest.approx(100 * expected / closes[119])
    assert row["feature"]["atr"] == float(
        ta.atr(
            [bar["high"] for bar in bars], [bar["low"] for bar in bars], closes[20:120], period=14
        )[-1]
    )


def test_native_flat_rsi_is_disclosed_and_retained_while_degenerate_bands_are_unknown():
    stock = record()
    _, receipt, row = calculate(stock, "rsi_14_gt60")
    assert row["feature"]["value"] == 100
    assert row["feature"]["flat_window"] is True and receipt["counts"]["allowed"] == 1
    assert "including a completely flat window" in indicators.recipe()["rsi"]["flat_prices"]
    for identifier in ("bbands_20_gt_upper2", "bbands_20_lt_lower2"):
        rows, receipt, row = calculate(stock, identifier)
        assert receipt["counts"]["unknown"] == 1 and row["feature"] is None
        assert "no usable width" in rows[0]["signals"][0]["research_exclusion"]


def test_zero_atr_is_known_and_exact_threshold_equality_never_passes_either_direction():
    stock = record()
    for identifier in ("atr_14_pct_lt2", "atr_14_pct_gt2"):
        _, receipt, row = calculate(stock, identifier)
        assert row["feature"]["value"] == 2 and receipt["counts"]["filtered"] == 1
    for bar in stock["evidence"]["bars"].values():
        bar.update(open=100, high=100, low=100, close=100)
    _, receipt, row = calculate(refreeze(stock), "atr_14_pct_lt2")
    assert row["feature"]["atr"] == 0 and receipt["counts"]["allowed"] == 1
    for identifier in ("rsi_14_gt50", "rsi_14_lt30", "bbands_20_lt_lower2"):
        chosen = hypothesis(identifier)
        assert not indicators._compare({"value": chosen["threshold"]}, chosen)
        assert not indicators._compare({"value": chosen["threshold"] + 1e-13}, chosen)


@pytest.mark.parametrize(
    "identifier",
    [
        "rsi_14_gt50",
        "rsi_7_lt30",
        "bbands_20_gt_upper2",
        "bbands_20_lt_lower2",
        "atr_14_pct_lt4",
        "ema20_and_rsi14_gt50",
        "momentum5_and_atr14_lt4",
    ],
)
def test_prefix_and_future_perturbation_cannot_change_previous_decisions(identifier):
    closes = [100 + i / 10 + (i % 7 - 3) for i in range(130)]
    before = record(closes=closes)
    future = record(closes=closes[:120] + [9000.0] * 10)
    prefix = record(closes=closes[:121], count=121)
    source = execution(before)
    receipts = [
        indicators.apply(source, bundle(stock), hypothesis(identifier))[1]
        for stock in (before, future, prefix)
    ]
    assert receipts[0]["membership"] == receipts[1]["membership"] == receipts[2]["membership"]
    assert receipts[0]["counts"] == receipts[1]["counts"] == receipts[2]["counts"]


@pytest.mark.parametrize("identifier", ["rsi_14_gt50", "bbands_20_gt_upper2", "atr_14_pct_lt4"])
def test_intraday_entry_uses_only_prior_session_closes(identifier):
    closes = [100 + i / 10 for i in range(130)]
    first = record(closes=closes)
    second = record(closes=closes[:119] + [9000.0] * 11)
    _, _, a = calculate(first, identifier, minute=True)
    _, _, b = calculate(second, identifier, minute=True)
    assert a == b and a["feature"]["to"] == dates()[118]
    assert a["decision_at"] == dates()[119] + "T09:16:00+05:30"


@pytest.mark.parametrize(
    "identifier,index", [("rsi_14_gt50", 98), ("atr_14_pct_lt4", 98), ("bbands_20_gt_upper2", 18)]
)
def test_complete_finite_warmup_is_required(identifier, index):
    stock = record()
    rows, receipt = indicators.apply(
        execution(stock, indexes=(index,), start=0), bundle(stock), hypothesis(identifier)
    )
    assert receipt["counts"]["unknown"] == 1
    assert "earlier consecutive" in rows[0]["signals"][0]["research_exclusion"]


@pytest.mark.parametrize("identifier", ["rsi_14_gt50", "atr_14_pct_lt4", "bbands_20_gt_upper2"])
def test_missing_expected_session_is_unknown_and_unavailable_close_is_not_read(identifier):
    stock = record(missing=(118,))
    _, receipt, row = calculate(stock, identifier)
    assert receipt["counts"]["unknown"] == 1 and dates()[118] in row["reason"]
    stock = record()
    decision = datetime.fromisoformat(dates()[120] + "T09:15:00+05:30")
    stock["evidence"]["available_at"][dates()[119]] = dates()[120] + "T10:00:00+05:30"
    feature, reason = indicators._feature(stock, decision, hypothesis(identifier))
    assert feature is None and "not available" in reason


@pytest.mark.parametrize("identifier", ["ema20_and_rsi14_gt50", "momentum5_and_atr14_lt4"])
def test_and_requires_all_declared_components_known_and_true(identifier):
    rising = record(closes=[100 + i / 10 for i in range(130)])
    _, positive, row = calculate(rising, identifier)
    assert positive["counts"]["allowed"] == 1
    assert len(row["feature"]["components"]) == 2
    assert all(component["passed"] for component in row["feature"]["components"])
    _, flat, row = calculate(record(), identifier)
    assert flat["counts"]["filtered"] == 1 and row["feature"]["passed"] is False
    # The first condition can already be false; a missing second input is still unknown.
    _, missing, row = calculate(record(missing=(40,)), identifier)
    assert missing["counts"]["unknown"] == 1 and row["feature"] is None
    assert dates()[40] in row["reason"]


def test_legacy_exclusions_pending_entries_and_exact_dependency_guard_survive(monkeypatch):
    stock = record()
    source = execution(stock, indexes=(119, 129))
    source["strategies"][0]["signals"][0]["research_exclusion"] = "Original execution exclusion"
    rows, receipt = indicators.apply(source, bundle(stock), hypothesis("rsi_14_gt50"))
    assert receipt["counts"]["original_excluded"] == receipt["counts"]["pending"] == 1
    assert rows[0]["signals"] == source["strategies"][0]["signals"]
    monkeypatch.setattr(indicators.metadata, "version", lambda name: "2.0.6")
    with pytest.raises(ValueError, match="recorded OpenAlgo SDK"):
        indicators.apply(source, bundle(stock), hypothesis("none"))
