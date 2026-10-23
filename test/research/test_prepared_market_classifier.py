"""Private causal classification and catalogue reuse preserve exact public bytes."""

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from test_regimes import after, growth
from test_regimes import evidence as market_evidence
from test_stock_market_context import bundle

from research import expanded_stock_market as expanded
from research import regimes
from research import stock_indicators as indicators
from research import stock_market_context as context_module
from research.market_series import fingerprint


@pytest.fixture(scope="module")
def series():
    return market_evidence(
        growth([0.003 + (index % 5) * 0.0002 for index in range(229)]), missing=(202,)
    )


def test_private_classifier_matches_every_subset_and_reuses_features_and_input_ids(
    series, monkeypatch
):
    decisions = [after(series, index) for index in (40, 149, 180, 203, 220)]
    expected = regimes.classify(series, decisions)
    feature_calls, identity_calls = [], []
    feature_fn, identity_fn = regimes._features, regimes._input_id

    def features(source):
        feature_calls.append(1)
        return feature_fn(source)

    def identity(source, days):
        identity_calls.append(1)
        return identity_fn(source, days)

    monkeypatch.setattr(regimes, "_features", features)
    monkeypatch.setattr(regimes, "_input_id", identity)
    with regimes._PreparedClassifier(series) as classifier:
        assert classifier.classify(decisions) == expected
        assert classifier.classify(decisions) == expected
        assert classifier.classify(decisions[1:3])["timeline"] == expected["timeline"][1:3]
        assert len(feature_calls) == 1
        assert len(identity_calls) == len(decisions)


def test_private_classifier_source_and_results_are_detached(series):
    source = deepcopy(series)
    decisions = [after(source, 180)]
    expected = regimes.classify(source, decisions)
    with regimes._PreparedClassifier(source) as classifier:
        first = classifier.classify(decisions)
        source["bars"].clear()
        first["timeline"][0]["features"]["close"] = -1
        first["timeline"][0]["history"]["missing_dates"].append("bad")
        assert classifier.classify(decisions) == expected
    with pytest.raises(ValueError, match="released"):
        classifier.classify(decisions)


def test_future_observations_never_change_an_earlier_private_or_public_row(series):
    revised = deepcopy(series)
    for day, bar in revised["bars"].items():
        if day > revised["required_dates"][180]:
            for key in ("open", "high", "low", "close"):
                bar[key] *= 2
    revised["id"] = fingerprint({key: value for key, value in revised.items() if key != "id"})
    decisions = [after(series, index) for index in (180, 220)]
    original = regimes.classify(series, decisions)
    changed = regimes.classify(revised, decisions)
    assert original["timeline"][0] == changed["timeline"][0]
    assert original["timeline"][1] != changed["timeline"][1]
    with (
        regimes._PreparedClassifier(series) as first,
        regimes._PreparedClassifier(revised) as second,
    ):
        assert first.classify(decisions) == original
        assert second.classify(decisions) == changed


@pytest.mark.parametrize("problem", ["availability", "timezone", "chronology", "identity"])
def test_original_series_validation_precedes_private_calculation(series, problem):
    source = deepcopy(series)
    day = source["required_dates"][0]
    if problem == "availability":
        source["available_at"][day] = "not a timestamp"
    elif problem == "timezone":
        source["available_at"][day] = day + "T15:30:00+00:00"
    elif problem == "chronology":
        source["required_dates"].reverse()
    else:
        source["id"] = "0" * 64
    with pytest.raises(ValueError):
        regimes.classify(source, [after(series, 180)])
    with pytest.raises(ValueError):
        regimes._PreparedClassifier(source)


@pytest.mark.parametrize(
    "decisions", [[], ["2024-01-01"], ["bad"], ["2024-01-01T09:15:00+05:30"] * 2]
)
def test_decision_validation_runs_for_each_private_request(series, decisions):
    with regimes._PreparedClassifier(series) as classifier:
        with pytest.raises(ValueError):
            classifier.classify(decisions)
        with pytest.raises(ValueError):
            regimes.classify(series, decisions)


def test_classifier_cache_is_bounded_and_cannot_cross_threads(series, monkeypatch):
    monkeypatch.setattr(regimes, "MAX_SESSIONS", 3)
    with regimes._PreparedClassifier(series) as classifier:
        start = datetime.fromisoformat(after(series, 180))
        for index in range(10):
            classifier.classify([(start + timedelta(seconds=index)).isoformat()])
        assert len(classifier._PreparedClassifier__rows) == 3
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(ValueError, match="owning thread"):
                executor.submit(classifier.classify, [start.isoformat()]).result(10)


def test_invalid_original_schema_cannot_be_normalized_during_fallback(series, monkeypatch):
    source = deepcopy(series)
    source["required_dates"] = tuple(source["required_dates"])
    monkeypatch.setattr(regimes, "bounded_canonical_snapshot", lambda *a, **kw: None)
    with pytest.raises(ValueError):
        regimes._PreparedClassifier(source)


def test_expanded_owner_preserves_catalogue_recipe_normalization_and_gate_bytes(monkeypatch):
    source, evidence = bundle()
    selected = next(row for row in expanded.catalog() if row["id"] == "relative_20_gt0__trend_up")
    catalogue, recipe = expanded.catalog(), expanded.recipe()
    expected = expanded.apply(evidence, source, selected)
    original = indicators.metadata.version
    reads = []

    def installed(name):
        reads.append(name)
        return original(name)

    monkeypatch.setattr(indicators.metadata, "version", installed)
    with expanded.PreparedJointContext(source) as owner:
        entered = len(reads)
        assert expanded.apply(evidence, source, selected) == expected
        assert owner.apply(evidence, source, selected) == expected
        assert owner.apply(evidence, source, selected) == expected
        for row in catalogue:
            assert expanded.normalize_hypothesis(row) == row
        assert expanded.catalog() == catalogue
        assert expanded.recipe() == recipe
        first = expanded.catalog()
        first[0]["id"] = "changed"
        assert expanded.catalog() == catalogue
        assert len(reads) == entered
        copied = copy_context()
    indicators.runtime_version()
    assert len(reads) == entered + 1
    copied.run(indicators.runtime_version)
    assert len(reads) == entered + 2


def test_sdk_pin_is_verified_before_scope_and_other_threads_still_check(monkeypatch):
    calls = []

    def version(name):
        calls.append(name)
        return "2.0.5"

    monkeypatch.setattr(indicators.metadata, "version", version)
    with indicators._pinned_runtime():
        for _ in range(5):
            assert indicators.runtime_version() == "2.0.5"
        assert len(calls) == 1
        with ThreadPoolExecutor(max_workers=1) as executor:
            copied = copy_context()
            assert executor.submit(copied.run, indicators.runtime_version).result(10) == "2.0.5"
        assert len(calls) == 2
    assert indicators.runtime_version() == "2.0.5"
    assert len(calls) == 3
    monkeypatch.setattr(indicators.metadata, "version", lambda name: "unsupported")
    with pytest.raises(ValueError, match="recorded OpenAlgo SDK"):
        with indicators._pinned_runtime():
            pytest.fail("An invalid runtime entered the private scope")


def test_nested_scopes_and_exception_restore_runtime_and_catalogue_context(monkeypatch):
    source, _ = bundle()
    catalogue = expanded.catalog()
    reads = []
    original = indicators.metadata.version

    def installed(name):
        reads.append(name)
        return original(name)

    monkeypatch.setattr(indicators.metadata, "version", installed)
    with expanded.PreparedJointContext(source) as outer:
        entered = len(reads)
        with pytest.raises(RuntimeError, match="interrupted"):
            with expanded.PreparedJointContext(source):
                assert len(reads) == entered + 1
                assert expanded.catalog() == catalogue
                raise RuntimeError("interrupted")
        assert expanded._prepared_catalogue.get() is outer
        assert expanded.catalog() == catalogue
        assert indicators.runtime_version() == "2.0.5"
        assert len(reads) == entered + 1
    assert expanded._prepared_catalogue.get() is None
    assert indicators._runtime_pin.get() is None


def test_private_market_projection_rejects_another_source_identity():
    source, _ = bundle()
    views = context_module._PreparedViews(source)
    try:
        with pytest.raises(ValueError, match="exact privately owned projection"):
            views._PreparedViews__classify(deepcopy(source["market"]["evidence"]), [])
    finally:
        views.close()


def test_market_classifier_owners_are_released_after_success_and_failure(series):
    references = []
    for index in range(10):
        try:
            with regimes._PreparedClassifier(series) as classifier:
                references.append(weakref.ref(classifier))
                classifier.classify([after(series, 180)])
                if index % 2:
                    raise RuntimeError("interrupted")
        except RuntimeError:
            pass
        classifier.close()
        del classifier
    gc.collect()
    assert all(reference() is None for reference in references)
