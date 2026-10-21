"""Generated market, genuine accounting and full-procedure calibration checks."""

from copy import deepcopy

import pytest
from native_calibration_runner import (
    GENERATOR_IDENTITY,
    RUNNER_IDENTITY,
    NativeAccountCache,
    market_paths,
    prepared,
    run_case,
)
from test_vectorbt_portfolio import assert_reconciles

from research.automatic_protocol import slice_period
from research.connectors.vectorbt_portfolio import evaluate
from research.execution_features import attach as attach_atr
from research.portfolio_constraint_evidence import assess
from research.portfolio_execution_features import attach as attach_portfolio
from research.search_calibration import SCENARIOS, compile_plan


def plan():
    return compile_plan(
        code_identity="native-calibration-test-source",
        runner_identity=RUNNER_IDENTITY,
        execution_model="native_synthetic_market",
        replicates=1,
    )


@pytest.mark.parametrize("scenario,kind", SCENARIOS)
def test_market_generator_is_prefix_causal_and_independent_of_symbol_order(scenario, kind):
    days = [f"2026-01-{day:02d}" for day in range(1, 29)]
    kwargs = {"data_seed": 1303, "scenario": scenario}
    full = market_paths(days, ("AAA", "BBB"), **kwargs)
    prefix = market_paths(days[:10], ("BBB", "AAA"), **kwargs)
    assert {
        symbol: {day: bars[day] for day in days[:10]} for symbol, bars in full.items()
    } == prefix
    other = market_paths(days, ("AAA", "BBB"), **{**kwargs, "data_seed": 1304})
    assert full != other
    assert full["AAA"] != full["BBB"]
    assert all(
        bar["open"] == bar["high"] == bar["low"] == bar["close"] > 0
        for bars in full.values()
        for bar in bars.values()
    )


@pytest.mark.parametrize("scenario,kind", SCENARIOS)
def test_generated_returns_have_declared_conditional_drift(scenario, kind):
    days = [f"2026-01-{day:02d}" for day in range(1, 29)]
    paths = market_paths(days, ("AAA", "BBB"), data_seed=42, scenario=scenario)
    for bars in paths.values():
        initial = 100_000_000 if scenario == "sparse_null" else 100
        prices = [initial, *[bars[day]["close"] for day in days]]
        returns = [right / left - 1 for left, right in zip(prices[:-1], prices[1:], strict=True)]
        if kind == "power":
            assert min(returns) > 0
        elif scenario != "correlated_null":
            magnitude = 0.00035 if scenario == "shifted_null" else 0.001
            assert all(abs(abs(item) - magnitude) < 1e-12 for item in returns)
    # Under null the sign is a fresh symmetric random draw, never a function of
    # scanner opportunities, indicators, fitted parameters or prior prices.


def test_declared_adverse_final_never_changes_development_market_prefix():
    days = [f"2026-01-{day:02d}" for day in range(1, 29)]
    kwargs = {"data_seed": 42, "scenario": "persistent_edge"}
    original = market_paths(days, ("AAA", "BBB"), **kwargs)
    adverse = market_paths(days, ("AAA", "BBB"), **kwargs, adverse_from=days[20])
    for symbol in original:
        assert all(original[symbol][day] == adverse[symbol][day] for day in days[:20])
        prices = [adverse[symbol][day]["close"] for day in days[19:]]
        assert all(right < left for left, right in zip(prices[:-1], prices[1:], strict=True))


@pytest.fixture(scope="module")
def fixture():
    return prepared(plan()["cases"][-1])


def test_fixture_never_attests_synthetic_volume_as_broker_observed(fixture):
    assert fixture["snapshot"]["provenance"]["synthetic"]
    assert fixture["snapshot"]["provenance"]["generator"] == GENERATOR_IDENTITY
    features = fixture["portfolio_execution_features"]
    assert features["counts"]["capacity_available"] == 0
    assert features["counts"]["correlation_available"] > 0
    assert all(
        row["execution_policy"]["capacity_mode"] == "disabled" for row in fixture["strategies"]
    )
    assert all(
        record["reviewed_profile"] is None and record["volume_unit"] == "unknown"
        for rows in features["volume_records"].values()
        for record in rows.values()
    )


def test_sparse_null_keeps_valid_source_chronology_but_has_no_executable_whole_shares():
    case = next(case for case in plan()["cases"] if case["scenario"] == "sparse_null")
    fixture = prepared(case)
    assert len({signal["date"] for signal in fixture["signals"]}) == 160
    assert {signal["symbol"] for signal in fixture["signals"]} == {"AAA", "BBB"}
    current = attach_portfolio(attach_atr(slice_period(fixture, "search")))
    report = evaluate(current["strategies"], current["snapshot"], fixture["portfolio"]["capital"])
    assert report["summary"]["closed_trades"] == 0
    assert report["summary"]["net_return_pct"] == 0
    assert_reconciles(report)
    assert assess(report, current["portfolio_execution_features"])["status"] == "supported"


def test_actual_native_market_accounts_reconcile_and_audit(fixture):
    original = deepcopy(fixture)
    current = attach_portfolio(attach_atr(slice_period(fixture, "search")))
    report = evaluate(current["strategies"], current["snapshot"], fixture["portfolio"]["capital"])
    assert report["policy_version"] == "vectorbt-joint-causal-bars-v5"
    assert report["summary"]["closed_trades"] > 5
    assert report["summary"]["net_return_pct"] > 0
    assert_reconciles(report)
    audit = assess(report, current["portfolio_execution_features"])
    assert audit["status"] == "supported", audit
    assert "test_double" not in audit
    assert audit["filled_entries"] > 5
    assert fixture == original


def test_cache_reuses_only_pristine_genuine_native_reports_and_evicts_within_bounds(fixture):
    current = attach_portfolio(attach_atr(slice_period(fixture, "search")))
    rows, snapshot, capital = (
        current["strategies"],
        current["snapshot"],
        fixture["portfolio"]["capital"],
    )
    with NativeAccountCache(max_entries=1, max_bytes=4 * 1024**2) as cache:
        cache.bind("dataset-a")
        first = cache.evaluate(rows, snapshot, capital)
        original = deepcopy(first)
        first["summary"]["net_return_pct"] = 999
        first["controller_only_mutation"] = True
        progress = []
        reused = cache.evaluate(
            rows, snapshot, capital, progress=lambda *value: progress.append(value)
        )
        assert reused == original
        assert progress == [(1, 1)]
        assert cache.calculations == cache.reuses == 1
        assert_reconciles(reused)
        assert assess(reused, current["portfolio_execution_features"])["status"] == "supported"
        changed = cache.evaluate(rows, snapshot, capital + 1)
        assert changed["config"]["initial_capital"] == capital + 1
        assert cache.calculations == 2 and len(cache.reports) == 1
        assert 0 < cache.bytes <= cache.max_bytes
        assert cache.evaluate(rows, snapshot, capital) == original
        assert cache.calculations == 3 and cache.reuses == 1
        with pytest.raises(ValueError, match="cannot cross datasets"):
            cache.bind("dataset-b")
    assert cache.closed and not cache.reports and cache.bytes == 0
    with pytest.raises(ValueError, match="open dataset scope"):
        cache.evaluate(rows, snapshot, capital)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_entries": 0},
        {"max_entries": 513},
        {"max_entries": True},
        {"max_bytes": 0},
        {"max_bytes": 64 * 1024**2 + 1},
    ],
)
def test_cache_limits_are_strict_and_bounded(limits):
    with pytest.raises(ValueError, match="bound is invalid"):
        NativeAccountCache(**limits)


def test_cache_scope_closes_after_error():
    cache = NativeAccountCache()
    with pytest.raises(RuntimeError, match="scope error"), cache:
        cache.bind("dataset-a")
        raise RuntimeError("scope error")
    assert cache.closed and not cache.reports and cache.bytes == 0


@pytest.mark.parametrize("change", ["runner_identity", "execution_model"])
def test_runner_rejects_wrong_declared_execution_before_preparing(change):
    from research.report_contract import fingerprint

    frozen = plan()
    frozen[change] = "controlled_account_model" if change == "execution_model" else "wrong-runner"
    frozen["id"] = fingerprint({key: value for key, value in frozen.items() if key != "id"})
    with pytest.raises(ValueError, match="native-synthetic declaration"):
        run_case(frozen["cases"][0], frozen)


def test_runner_refuses_an_unplanned_dataset():
    frozen = plan()
    case = deepcopy(frozen["cases"][0])
    case["data_seed"] += 1
    with pytest.raises(ValueError, match="not frozen"):
        run_case(case, frozen)
