"""Native NumPy pair batching preserves exact causal correlation receipts."""

from copy import deepcopy

import numpy as np
import pytest
from test_portfolio_correlation_reuse import observations

from research import portfolio_execution_features as features


@pytest.mark.parametrize("seed", [7, 22, 42])
def test_batched_ordered_pairs_and_receipt_seals_are_exact_across_batch_boundaries(seed):
    prepared = observations(seed)
    random = np.random.default_rng(seed)
    for index in range(24):
        prepared[f"S{index:02}"] = {
            **prepared["AAA"],
            "returns": (random.normal(size=60) * 10 ** (index / 2 - 8)).tolist(),
        }
    prepared["IDENTICAL"] = deepcopy(prepared["AAA"])
    prepared["NEGATIVE"] = {**prepared["AAA"], "returns": [-v for v in prepared["AAA"]["returns"]]}
    before = deepcopy(prepared)
    inputs = features._correlation_inputs(prepared)
    requested = list(reversed(prepared))
    batched = features._batched_correlation_pairs(prepared, inputs, requested)
    original = {}
    for symbol in requested:
        expected = features._correlation(
            symbol, "2026-10-22", {}, prepared, _inputs=inputs, _pairs=original
        )
        actual = features._correlation(
            symbol, "2026-10-22", {}, prepared, _inputs=inputs, _pairs=batched
        )
        assert actual == expected
    assert prepared == before
    assert batched == original


def test_batch_uses_bounded_numpy_work_without_scalar_corrcoef(monkeypatch):
    prepared = observations(4)
    for index in range(30):
        prepared[str(index)] = {
            **prepared["AAA"],
            "returns": np.roll(prepared["AAA"]["returns"], index).tolist(),
        }
    inputs = features._correlation_inputs(prepared)
    native_matmul = np.matmul
    sizes = []

    def counted(left, right):
        sizes.append(left.shape[0])
        return native_matmul(left, right)

    monkeypatch.setattr(np, "matmul", counted)
    monkeypatch.setattr(np, "corrcoef", lambda *a, **kw: pytest.fail("Scalar covariance repeated"))
    pairs = features._batched_correlation_pairs(prepared, inputs, list(prepared))
    assert len(sizes) > 1 and max(sizes) <= 128
    assert all(value is not None for value in pairs.values())
    assert ("AAA", "FLAT") not in pairs
    assert ("AAA", "OTHER_CALENDAR") not in pairs


def test_batch_rejects_work_beyond_existing_pair_bound(monkeypatch):
    prepared = observations(3)
    monkeypatch.setattr(features, "MAX_PAIRS", 4)
    with pytest.raises(ValueError, match="bounded"):
        features._batched_correlation_pairs(
            prepared, features._correlation_inputs(prepared), ["AAA"]
        )
