"""Private causal observation reuse preserves fresh native account admission."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
import test_prepared_joint_context as context_cases

from research import stock_features as stock
from research import stock_market_context as context
from research import stock_relative_strength as relative


@pytest.fixture(scope="module")
def saved():
    return context_cases.saved_context.__wrapped__()


@contextmanager
def views(source):
    owner = context._PreparedViews(source)
    try:
        yield owner
    finally:
        owner.close()


def hypothesis(identifier="momentum_5_gt0"):
    return next(row for row in relative.catalog() if row["id"] == identifier)


def private_cache(owner):
    return owner._PreparedViews__features, owner._PreparedViews__stock


@pytest.mark.parametrize(
    "identifier", ["momentum_5_gt0", "relative_20_gt0", "ema20_and_relative20_gt0"]
)
def test_exit_policy_changes_reuse_features_with_exact_public_receipts(
    saved, monkeypatch, identifier
):
    source, evidence = saved
    rule = hypothesis(identifier)
    changed = deepcopy(evidence)
    for strategy in changed["strategies"]:
        strategy["config"]["cost_bps"] += 7
    projected = context.stock_view(source)
    expected = relative.apply(changed, projected, rule)
    calls = []
    original = relative._feature

    def feature(*args):
        calls.append((args[0], args[2].isoformat(), deepcopy(args[3])))
        return original(*args)

    monkeypatch.setattr(relative, "_feature", feature)
    with views(source) as owner:
        first = owner.apply_stock(evidence, rule)
        initial = len(calls)
        assert initial > 0
        rows, receipt = owner.apply_stock(changed, rule)
        assert (rows, receipt) == expected
        assert len(calls) == initial
        first[1]["membership"][0]["feature"]["value"] = -999999
        rows[0]["signals"].clear()
        receipt["membership"].clear()
        assert owner.apply_stock(changed, rule) == expected


@pytest.mark.parametrize("missing", [(100,), (178,)])
def test_unknown_results_remain_exact_and_are_reused(missing, monkeypatch):
    from test_stock_market_context import bundle

    source, evidence = bundle(missing=missing)
    rule = hypothesis("ema20_and_relative20_gt0")
    expected = relative.apply(evidence, context.stock_view(source), rule)
    calls, original = [], relative._feature

    def feature(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(relative, "_feature", feature)
    with views(source) as owner:
        assert owner.apply_stock(evidence, rule) == expected
        initial = len(calls)
        assert owner.apply_stock(evidence, rule) == expected
        assert len(calls) == initial


@pytest.mark.parametrize("problem", ["frozen", "engine", "interval", "symbols"])
def test_current_account_admission_stays_fresh_after_hit(saved, problem):
    source, evidence = saved
    rule = hypothesis()
    with views(source) as owner:
        owner.apply_stock(evidence, rule)
        changed = deepcopy(evidence)
        if problem == "frozen":
            changed["frozen_prices"] = False
        elif problem == "engine":
            changed["portfolio"]["engine"] = "unsupported"
        elif problem == "interval":
            changed["snapshot"]["provenance"]["interval"] = "unsupported"
        else:
            changed["strategies"][0]["signals"][0]["symbol"] = "MISSING"
        with pytest.raises(ValueError):
            owner.apply_stock(changed, rule)


def test_source_mutation_cannot_change_owned_feature_results(saved):
    source, evidence = deepcopy(saved)
    with views(source) as owner:
        expected = owner.apply_stock(evidence, hypothesis())
        next(iter(source["series"].values()))["evidence"]["bars"].clear()
        assert owner.apply_stock(evidence, hypothesis()) == expected


def test_generic_gate_receives_no_cache_capability_and_remains_fresh(saved):
    source, evidence = saved
    calls = []

    def generic(current, projected, rule, validator):
        calls.append(True)
        return relative._apply_with_validator(current, projected, rule, validator)

    with views(source) as owner:
        expected = owner.apply_stock(evidence, hypothesis(), _gate=generic)
        assert owner.apply_stock(evidence, hypothesis(), _gate=generic) == expected
        cache, _ = private_cache(owner)
        assert len(calls) == 2 and not cache._PreparedFeatureCache__entries


def fake_cache(saved, monkeypatch):
    source, _ = saved
    calls = []

    def feature(record, decision, rule):
        calls.append((record, decision, deepcopy(rule)))
        return {"value": rule["threshold"], "nested": {"items": []}}, None

    monkeypatch.setattr(stock, "_feature", feature)
    owner = context._PreparedViews(source)
    cache, projected = private_cache(owner)
    symbol, record = next(iter(projected["series"].items()))
    day = record["calendar"]["sessions"][-1]
    at = datetime.fromisoformat(day + "T09:15:00+05:30")

    def resolve(*, when=at, rule=None, bundle=projected, selected_record=record):
        return cache.resolve(
            bundle, symbol, selected_record, when, rule or hypothesis(), feature, "v1"
        )

    return owner, cache, resolve, calls, at, projected, record


def test_full_hypothesis_and_exact_timestamp_keys_and_lru_eviction(saved, monkeypatch):
    monkeypatch.setattr(stock, "MAX_PREPARED_FEATURES", 2)
    owner, cache, resolve, calls, at, _, _ = fake_cache(saved, monkeypatch)
    try:
        first = resolve()
        first[0]["nested"]["items"].append("caller mutation")
        assert resolve()[0]["nested"]["items"] == []
        assert len(calls) == 1
        changed = deepcopy(hypothesis())
        changed["threshold"] += 1
        resolve(rule=changed)
        resolve(when=at + timedelta(minutes=1))
        assert len(calls) == 3
        assert len(cache._PreparedFeatureCache__entries) == 2
        resolve()
        assert len(calls) == 4
    finally:
        owner.close()


def test_heap_byte_bound_evicts_without_retaining_large_results(saved, monkeypatch):
    owner, cache, resolve, calls, at, _, _ = fake_cache(saved, monkeypatch)
    try:
        resolve()
        maximum = cache._PreparedFeatureCache__bytes + 100
        monkeypatch.setattr(stock, "MAX_PREPARED_FEATURE_BYTES", maximum)
        for index in range(1, 20):
            resolve(when=at + timedelta(minutes=index))
            assert cache._PreparedFeatureCache__bytes <= maximum
            assert len(cache._PreparedFeatureCache__entries) <= 1
        monkeypatch.setattr(stock, "MAX_PREPARED_FEATURE_BYTES", 1)
        resolve(when=at + timedelta(minutes=21))
        # A result which cannot fit is returned freshly without admission.
        assert len(calls) == 21
        assert cache._PreparedFeatureCache__bytes <= maximum
    finally:
        owner.close()
    assert cache._PreparedFeatureCache__bytes == 0
    assert not cache._PreparedFeatureCache__entries


def test_wrong_projection_or_record_never_gets_identity_approval(saved, monkeypatch):
    owner, cache, resolve, calls, _, projected, record = fake_cache(saved, monkeypatch)
    try:
        resolve()
        resolve(bundle=deepcopy(projected))
        resolve(selected_record=deepcopy(record))
        assert len(calls) == 3
        resolve()
        assert len(calls) == 3
    finally:
        owner.close()


def test_changed_provider_is_not_served_from_previous_results(saved, monkeypatch):
    owner, cache, resolve, calls, at, projected, record = fake_cache(saved, monkeypatch)
    try:
        resolve()

        def changed(*args):
            return {"value": 999}, None

        monkeypatch.setattr(stock, "_feature", changed)
        symbol = next(iter(projected["series"]))
        assert cache.resolve(projected, symbol, record, at, hypothesis(), changed, "v1")[0] == {
            "value": 999
        }
    finally:
        owner.close()


def test_scope_error_expired_context_and_owner_thread_cleanup(saved, monkeypatch):
    owner, cache, resolve, calls, _, _, _ = fake_cache(saved, monkeypatch)
    try:
        with pytest.raises(RuntimeError):
            with stock._prepared_feature_scope(cache) as captured:
                resolve()
                raise RuntimeError("controlled gate error")
        assert captured.closed and captured.cache is None
        assert not cache._PreparedFeatureCache__entries
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ValueError, match="synchronous live owner"):
                pool.submit(resolve).result()
        resolve()
        assert len(calls) == 2
    finally:
        owner.close()
    with pytest.raises(ValueError, match="released"):
        owner.apply_stock(saved[1], hypothesis())
    assert cache._PreparedFeatureCache__bundle is None
