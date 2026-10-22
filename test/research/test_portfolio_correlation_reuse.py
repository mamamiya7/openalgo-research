"""Day-local reuse must retain the exact ordered NumPy pair calculations."""

from copy import deepcopy

import numpy as np

from research import portfolio_execution_features as features
from research.portfolio import fingerprint


def original_correlation(symbol, day, prepared):
    own = prepared.get(symbol)
    if own is None:
        return None
    values, identities = {}, {}
    for other, candidate in prepared.items():
        rho = None
        if other == symbol:
            rho = 1.0
        elif candidate is not None and candidate["dates"] == own["dates"]:
            left, right = np.asarray(own["returns"]), np.asarray(candidate["returns"])
            if np.std(left) > 1e-14 and np.std(right) > 1e-14:
                rho = float(np.clip(np.corrcoef(left, right)[0, 1], -1, 1))
        values[other] = rho
        identities[other] = candidate["source_id"] if candidate is not None else None
    payload = {
        "version": features.CORRELATION_VERSION,
        "symbol": symbol,
        "entry_session": day,
        "from": own["dates"][0],
        "to": own["dates"][-1],
        "observations": 60,
        "available_at": max(value["available_at"] for value in prepared.values() if value),
        "source_id": fingerprint(identities),
        "correlations": values,
    }
    return {**payload, "id": fingerprint(payload)}


def observations(seed):
    random = np.random.default_rng(seed)
    dates = [f"session-{i:02}" for i in range(61)]
    prepared = {}
    for symbol, returns in (
        ("AAA", random.normal(0, 0.01, 60).tolist()),
        ("BBB", random.normal(0, 0.01, 60).tolist()),
        ("FLAT", [0.0] * 60),
        ("OTHER_CALENDAR", random.normal(0, 0.01, 60).tolist()),
    ):
        prepared[symbol] = {
            "dates": ["different", *dates[1:]] if symbol == "OTHER_CALENDAR" else dates,
            "returns": returns,
            "available_at": "2026-10-21T15:30:00+05:30",
            "source_id": fingerprint({symbol: returns}),
        }
    prepared["MISSING"] = None
    return prepared


def test_shared_day_inputs_preserve_exact_entries_and_seals_across_windows():
    for seed in (1, 2, 42):
        prepared = observations(seed)
        before = deepcopy(prepared)
        shared = features._correlation_inputs(prepared)
        for symbol in prepared:
            expected = original_correlation(symbol, "2026-10-22", prepared)
            assert (
                features._correlation(symbol, "2026-10-22", {}, prepared, _inputs=shared)
                == expected
            )
            assert features._correlation(symbol, "2026-10-22", {}, prepared) == expected
        assert prepared == before


def test_day_reuse_reduces_std_work_to_once_per_observed_symbol(monkeypatch):
    prepared = observations(7)
    original_std = np.std
    calls = []

    def counted(values):
        calls.append(values)
        return original_std(values)

    monkeypatch.setattr(np, "std", counted)
    shared = features._correlation_inputs(prepared)
    for symbol in prepared:
        features._correlation(symbol, "2026-10-22", {}, prepared, _inputs=shared)
    assert len(calls) == 4


def test_missing_day_stays_unavailable():
    prepared = {"AAA": None, "BBB": None}
    shared = features._correlation_inputs(prepared)
    assert features._correlation("AAA", "2026-10-22", {}, prepared, _inputs=shared) is None
