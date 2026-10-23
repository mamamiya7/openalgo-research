"""Discarded presentation is optional; native scientific evidence is identical."""

import copy
from contextvars import Context

import pytest
from test_vectorbt_portfolio import DAYS, candle, snapshot, strategy

from research import analytics
from research.connectors import vectorbt_portfolio as adapter


@pytest.mark.timeout(240)
@pytest.mark.parametrize("minute", [False, True])
def test_discarded_trial_keeps_exact_native_account_evidence(minute, monkeypatch):
    pytest.importorskip("vectorbt")
    prices = snapshot(minute=minute)
    for key in prices["bars"]["AAA"]:
        if key.startswith(DAYS[1]):
            prices["bars"]["AAA"][key] = candle(100, 111, 99, 108)
    strategies = [
        strategy("a", target_pct=10, stop_pct=20),
        strategy("b", target_pct=30, stop_pct=20),
    ]
    full = adapter.evaluate(strategies, prices, 1000)
    assert len(full["analysis"]["metrics"]) == 121
    assert full["analysis"]["charts"]
    original = analytics.build_analysis

    def no_discarded_presentation(*args, **kwargs):
        raise AssertionError("A discarded trial must not build presentation")

    with monkeypatch.context() as scoped:
        scoped.setattr(analytics, "build_analysis", no_discarded_presentation)
        with adapter.discard_trial_analysis():
            trial = adapter.evaluate(strategies, prices, 1000)
    assert trial == {key: value for key, value in full.items() if key != "analysis"}
    # The next explicit/selected report has the original full native metrics,
    # charts, and account evidence, with no sticky omission after a trial.
    restored = adapter.evaluate(strategies, prices, 1000)
    assert restored == full
    assert analytics.build_analysis is original


@pytest.mark.parametrize(
    "points, message",
    [
        ([{"date": "invalid", "equity": 100}], "dated account-equity"),
        (
            [{"date": "2026-01-06", "equity": 100}, {"date": "2026-01-05", "equity": 99}],
            "not chronological",
        ),
        ([{"date": "2026-01-05", "equity": float("nan")}], "finite marked account equity"),
    ],
)
def test_discarded_analysis_keeps_same_account_input_rejections(points, message):
    report = {"equity_curve": points, "summary": {"initial_capital": 100}}
    for check in (analytics.build_analysis, analytics.validate_analysis_evidence):
        with pytest.raises(ValueError, match=message):
            check(copy.deepcopy(report))


def test_discarded_analysis_keeps_native_runtime_pin(monkeypatch):
    vbt = pytest.importorskip("vectorbt")
    monkeypatch.setattr(vbt, "__version__", "unsupported")
    report = {
        "equity_curve": [{"date": "2026-01-05", "equity": 100}],
        "summary": {"initial_capital": 100},
        "execution": {"engine": "vectorbt"},
    }
    for check in (analytics.build_analysis, analytics.validate_analysis_evidence):
        with pytest.raises(ValueError, match="pinned VectorBT"):
            check(copy.deepcopy(report))


def test_trial_scope_is_nested_exception_safe_and_context_local():
    assert not adapter._DISCARD_TRIAL_ANALYSIS.get()
    with pytest.raises(RuntimeError, match="interrupted"):
        with adapter.discard_trial_analysis():
            assert adapter._DISCARD_TRIAL_ANALYSIS.get()
            assert not Context().run(adapter._DISCARD_TRIAL_ANALYSIS.get)
            with adapter.discard_trial_analysis():
                assert adapter._DISCARD_TRIAL_ANALYSIS.get()
            assert adapter._DISCARD_TRIAL_ANALYSIS.get()
            raise RuntimeError("interrupted")
    assert not adapter._DISCARD_TRIAL_ANALYSIS.get()
