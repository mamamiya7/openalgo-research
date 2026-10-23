"""Exact daily attachment reuse, never caller-mutable or minute scheduling."""

from copy import deepcopy

import pytest
from test_execution_features import prepared
from test_portfolio_execution_features import prepare, source

from research import execution_features as atr
from research import portfolio_execution_features as portfolio
from research.owned_entry_features import _capture, is_immutable_json
from research.prepared_daily_signals import DailySignals


def immutable_inputs(evidence):
    evidence = deepcopy(evidence)
    evidence["snapshot"] = _capture(evidence["snapshot"])
    for row in evidence["strategies"]:
        row["signals"] = _capture(row["signals"])
    return evidence


def test_daily_atr_reuses_only_identical_detached_sources_and_keeps_context_checks(monkeypatch):
    evidence = immutable_inputs(prepared()[0])
    expected = atr.attach(evidence)
    original = atr.validate_entry_feature
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    with atr._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
        monkeypatch.setattr(atr, "validate_entry_feature", counted)
        first = owner.attach(evidence)
        assert first == expected
        initial_checks = len(calls)
        assert initial_checks > 0
        changed = {
            **evidence,
            "strategies": [
                {
                    **evidence["strategies"][0],
                    "config": {
                        **evidence["strategies"][0]["config"],
                        "hold_sessions": 9,
                        "stop_pct": 7,
                    },
                }
            ],
        }
        again = owner.attach(changed)
        assert len(calls) == initial_checks
        assert again["strategies"][0]["signals"] is first["strategies"][0]["signals"]
        assert again == atr.attach(changed)
        assert is_immutable_json(again["strategies"][0]["signals"])
        with pytest.raises(TypeError):
            again["strategies"][0]["signals"][0]["row"] = 999
        wrong_context = {**evidence, "automatic_stock_market_context": {"id": "0" * 64}}
        with pytest.raises(ValueError, match="context"):
            owner.attach(wrong_context)
        # A new immutable tree with changed identity cannot inherit admission.
        changed_signal = dict(evidence["strategies"][0]["signals"][0])
        changed_signal["row"] += 1
        wrong = {
            **evidence,
            "strategies": [{**evidence["strategies"][0], "signals": _capture([changed_signal])}],
        }
        with pytest.raises(ValueError, match="opportunity"):
            owner.attach(wrong)
    assert not owner._PreparedExecutionFeatures__daily._rows


def test_daily_portfolio_modes_and_source_bindings_are_checked_before_reuse(monkeypatch):
    evidence, context, records = source(quantity=100_000)
    evidence = immutable_inputs(atr.attach(prepare(evidence, context, records)))
    expected = portfolio.attach(evidence)
    original, calls = portfolio.validate_capacity_entry, []

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    with portfolio._PreparedPortfolioFeatures(evidence["portfolio_execution_features"]) as owner:
        monkeypatch.setattr(portfolio, "validate_capacity_entry", counted)
        first = owner.attach(evidence)
        initial_checks = len(calls)
        assert initial_checks > 0 and first == expected
        again = owner.attach(evidence)
        assert len(calls) == initial_checks
        assert again["strategies"][0]["signals"] is first["strategies"][0]["signals"]
        strategy = evidence["strategies"][0]
        changed = {
            **evidence,
            "strategies": [
                {
                    **strategy,
                    "execution_policy": {
                        **strategy["execution_policy"],
                        "capacity_mode": "none",
                    },
                }
            ],
        }
        different = owner.attach(changed)
        assert different == portfolio.attach(changed)
        assert different["strategies"][0]["signals"] is not first["strategies"][0]["signals"]
        wrong = {**evidence, "execution_features": {"id": "0" * 64}}
        with pytest.raises(ValueError, match="clock"):
            owner.attach(wrong)
    assert not owner._PreparedPortfolioFeatures__daily._rows


@pytest.mark.parametrize("minute", [False, True])
def test_mutable_inputs_and_minute_scheduling_never_reuse(minute, monkeypatch):
    evidence = prepared(minute=minute)[0]
    if minute:
        evidence = immutable_inputs(evidence)
    calls, original = [], atr.validate_entry_feature

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    with atr._PreparedExecutionFeatures(evidence["execution_features"]) as owner:
        monkeypatch.setattr(atr, "validate_entry_feature", counted)
        first = owner.attach(evidence)
        before = len(calls)
        again = owner.attach(evidence)
        assert len(calls) > before
        assert again == first
        assert not owner._PreparedExecutionFeatures__daily._rows


def test_bounds_eviction_and_close_release_all_strong_references(monkeypatch):
    from research import prepared_daily_signals as module

    snapshot = _capture({"provenance": {"interval": "D"}})
    signals = _capture([{"date": "2026-01-01"}])
    strategy = {"id": "one", "signals": signals}
    owner = DailySignals()

    def schedule():
        return None

    monkeypatch.setattr(module, "MAX_LISTS", 1)
    first = owner.key(snapshot, strategy, schedule)
    result = owner.put(first, snapshot, signals, [{"a": 1}])
    assert owner.get(first, snapshot, signals) is result
    other = owner.key(snapshot, {**strategy, "id": "two"}, schedule)
    owner.put(other, snapshot, signals, [{"a": 2}])
    assert owner.get(first, snapshot, signals) is None and len(owner._rows) == 1
    monkeypatch.setattr(module, "MAX_BYTES", 1)
    raw = [{"large": "payload"}]
    assert owner.put(first, snapshot, signals, raw) is raw
    owner.close()
    assert not owner._rows and owner._bytes == 0
    assert not owner._snapshots and owner._snapshot_bytes == 0


def test_snapshot_retention_is_bounded_and_provider_replacement_does_not_reuse():
    owner = DailySignals()
    signals = _capture([{"date": "2026-01-01"}])
    strategy = {"id": "one", "signals": signals}

    def schedule():
        return None

    snapshots = [_capture({"provenance": {"interval": "D"}, "period": i}) for i in range(3)]
    first = owner.key(snapshots[0], strategy, schedule)
    for snapshot in snapshots:
        key = owner.key(snapshot, strategy, schedule)
        owner.put(key, snapshot, signals, [{"entry": 1}])
    assert len(owner._snapshots) == 2
    assert owner.get(first, snapshots[0], signals) is None
    current = owner.key(snapshots[-1], strategy, schedule)

    def replacement():
        return None

    changed = owner.key(snapshots[-1], strategy, replacement)
    assert owner.get(current, snapshots[-1], signals) is not None
    assert owner.get(changed, snapshots[-1], signals) is None
    owner.close()
