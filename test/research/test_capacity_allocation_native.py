"""Real native fill accounting; derived-feature admission is tested separately."""

import gc
import time
from copy import deepcopy

import pytest
from test_execution_aware_native import controlled, policy
from test_turnover_native import turnover_policy
from test_vectorbt_portfolio import DAYS, assert_reconciles, candle, signal, snapshot

from research.connectors import vectorbt_portfolio as native
from research.execution_policy import CAPACITY_RISK_VERSION, CAPACITY_VERSION, normalize_policy
from research.portfolio import fingerprint

pytestmark = pytest.mark.timeout(240)


def capacity_policy(*, capacity=True, correlation=False, **limits):
    old = policy()
    return normalize_policy(
        {
            **old,
            "version": CAPACITY_VERSION,
            "entry_priority": "source",
            "capacity_mode": "prior20_broker_reported" if capacity else "disabled",
            "correlation_mode": "prior60_positive_080" if correlation else "disabled",
            "risk_limits": {
                **old["risk_limits"],
                "version": CAPACITY_RISK_VERSION,
                "max_daily_buy_turnover_pct": 1000,
                "max_daily_volume_participation_pct": 1,
                "max_correlated_exposure_pct": 50,
                **limits,
            },
        }
    )


@pytest.fixture
def admitted(monkeypatch):
    """Isolate native execution from the independently sealed feature producer.

    These small receipts stand in for already admitted derived observations.
    Production validators and causal inputs have their own integration cases.
    """
    from research import portfolio_execution_features as features

    def capacity(value, **kwargs):
        if not isinstance(value, dict) or value.get("invalid"):
            raise ValueError("capacity_unavailable: qualified prior observations missing")
        return value

    def correlation(value, *, required_symbols, **kwargs):
        if not isinstance(value, dict) or value.get("invalid"):
            raise ValueError("correlation_unavailable: causal observation missing")
        if any(value["correlations"].get(symbol) is None for symbol in required_symbols):
            raise ValueError("correlation_unavailable: incomplete held-symbol pairs")
        return value

    monkeypatch.setattr(features, "validate_capacity_entry", capacity)
    monkeypatch.setattr(features, "validate_correlation_entry", correlation)


def rows(*, minute=False, symbols=("AAA", "AAA"), order_size=30, execution=None, volume=500):
    result = []
    for index, symbol in enumerate(symbols):
        event = signal(symbol, timestamp="09:15" if minute else None)
        event["entry_capacity"] = {
            "mean_daily_shares": volume,
            "id": "a" * 64,
            "source_id": "c" * 64,
        }
        event["entry_correlation"] = {
            "correlations": dict.fromkeys(set(symbols), 1.0),
            "id": "b" * 64,
        }
        result.append(
            controlled(
                str(index),
                100,
                execution=deepcopy(execution or capacity_policy()),
                signals=[event],
                stop_pct=20,
                target_pct=500,
                hold_sessions=3,
                order_size_pct=order_size,
            )
        )
    return result


def receipt(report, day=DAYS[1], symbol="AAA"):
    return next(
        row
        for row in report["execution"]["capacity"]["daily_symbols"]
        if row["date"] == day and row["symbol"] == symbol
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("max_daily_volume_participation_pct", 0),
        ("max_daily_volume_participation_pct", 10.01),
        ("max_daily_volume_participation_pct", True),
        ("max_daily_volume_participation_pct", float("nan")),
        ("max_correlated_exposure_pct", 0),
        ("max_correlated_exposure_pct", 100.01),
        ("max_correlated_exposure_pct", False),
        ("max_correlated_exposure_pct", float("inf")),
    ],
)
def test_limits_are_strictly_positive_bounded_and_versioned(name, value):
    with pytest.raises(ValueError):
        capacity_policy(**{name: value})


def test_modes_are_explicit_and_new_limits_cannot_leak_into_old_versions():
    for overrides in (
        {"capacity_mode": None},
        {"correlation_mode": "automatic"},
        {"version": "research-execution-aware-v3"},
        {"risk_limits": policy()["risk_limits"]},
    ):
        with pytest.raises(ValueError):
            normalize_policy({**capacity_policy(), **overrides})
    with pytest.raises(ValueError):
        capacity_policy(capacity=False, correlation=False)
    normalized = normalize_policy(
        {
            "version": CAPACITY_VERSION,
            "capacity_mode": "prior20_broker_reported",
            "correlation_mode": "prior60_positive_080",
        }
    )
    assert normalized["risk_limits"]["max_daily_volume_participation_pct"] == 1
    assert normalized["risk_limits"]["max_correlated_exposure_pct"] == 25


def test_policy_roundtrip_and_registry_record_native_v5_without_changing_old_requests():
    from research.connectors.registry import policy_for_request

    execution = capacity_policy(capacity=True, correlation=True)
    assert normalize_policy(deepcopy(execution)) == execution
    spec = {"portfolio": {"strategies": [{"execution_policy": execution}]}}
    assert policy_for_request(snapshot(), spec) == native.CAPACITY_POLICY_VERSION
    spec["portfolio"]["strategies"][0]["execution_policy"] = turnover_policy(50)
    assert policy_for_request(snapshot(), spec) == native.TURNOVER_POLICY_VERSION
    assert fingerprint(execution) != fingerprint(
        spec["portfolio"]["strategies"][0]["execution_policy"]
    )


@pytest.mark.parametrize("minute", [False, True])
def test_duplicate_signals_share_actual_filled_symbol_day_budget(admitted, minute):
    current, prices = rows(minute=minute), snapshot(minute=minute)
    before = deepcopy((current, prices))
    report = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 2]
    used = receipt(report, DAYS[0] if minute else DAYS[1])
    assert used["share_budget"] == used["bought_shares"] == 5
    assert used["remaining_shares"] == 0 and used["buy_fills"] == 2
    assert report["ledger"][1]["execution_details"]["daily_bought_shares_before"] == 3
    assert report["policy_version"] == native.CAPACITY_POLICY_VERSION
    assert report["execution"]["adapter_version"] == native.CAPACITY_ADAPTER_VERSION
    assert (current, prices) == before
    assert_reconciles(report)


def test_fractional_volume_budget_never_rounds_up_whole_shares(admitted):
    report = native.evaluate(rows(volume=499), snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 1]
    assert receipt(report)["remaining_shares"] == pytest.approx(0.99)


def test_same_opening_priority_controls_shared_symbol_capacity(admitted):
    execution = capacity_policy()
    execution["entry_priority"] = "reversed_source"
    report = native.evaluate(rows(execution=execution), snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [2, 3]
    assert report["ledger"][0]["execution_details"]["entry_rank"] == 2
    assert receipt(report)["bought_shares"] == 5


def test_fees_and_slippage_cannot_expand_share_or_cash_caps(admitted):
    current = rows(order_size=100)
    current[0]["config"].update(cost_bps=100, slippage_bps=100)
    report = native.evaluate(current[:1], snapshot(), 1000)
    first = report["ledger"][0]
    assert first["quantity"] == 5 and first["entry_price"] == 101
    assert receipt(report)["bought_shares"] == 5
    assert first["fees"] > 0
    assert_reconciles(report)


def test_native_rejected_buy_never_reserves_capacity(admitted, monkeypatch):
    from vectorbt.portfolio import nb

    original, calls = nb.order_nb, []

    def reject_first(**kwargs):
        calls.append(kwargs)
        return original(**{**kwargs, "reject_prob": 1.0 if len(calls) == 1 else 0.0})

    monkeypatch.setattr(nb, "order_nb", reject_first)
    report = native.evaluate(rows(order_size=100), snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [0, 5]
    assert receipt(report)["bought_shares"] == 5 and receipt(report)["buy_fills"] == 1


def test_invalid_feature_is_explicit_skip_not_unlimited_capacity(admitted):
    current = rows(order_size=100)
    current[0]["signals"][0].pop("entry_capacity")
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [0, 5]
    assert "capacity_unavailable" in report["ledger"][0]["reason"]


@pytest.mark.parametrize("field,value", [("mean_daily_shares", 600), ("source_id", "d" * 64)])
def test_symbol_day_observations_cannot_change_after_first_fill(admitted, field, value):
    current = rows()
    current[1]["signals"][0]["entry_capacity"][field] = value
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 0]
    assert "observations disagree" in report["ledger"][1]["reason"]
    assert receipt(report)["bought_shares"] == 3


def test_same_day_sale_does_not_replenish_but_next_session_resets(admitted):
    current = rows(minute=True, order_size=100, symbols=("AAA", "AAA", "AAA"))
    prices = snapshot(minute=True)
    for index, event in enumerate(current):
        event["config"]["target_pct"] = 10
        if index == 1:
            event["signals"][0]["timestamp"] = DAYS[0] + "T09:16:00+05:30"
        elif index == 2:
            event["signals"][0].update(date=DAYS[1], timestamp=DAYS[1] + "T09:15:00+05:30")
    for key in prices["timeline"]:
        prices["bars"]["AAA"][key] = candle(100, 111, 99, 100)
    report = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == [5, 0, 5]
    assert all(row["outcome"] == "target" for row in (report["ledger"][0], report["ledger"][2]))
    assert (
        receipt(report, DAYS[0])["bought_shares"] == receipt(report, DAYS[1])["bought_shares"] == 5
    )
    assert_reconciles(report)


@pytest.mark.parametrize(
    "rho,expected", [(1.0, [3, 2]), (0.8, [3, 2]), (0.7999, [3, 3]), (-1.0, [3, 3])]
)
def test_positive_direct_correlation_caps_marked_exposure(admitted, rho, expected):
    current = rows(
        symbols=("AAA", "BBB"), execution=capacity_policy(capacity=False, correlation=True)
    )
    current[1]["signals"][0]["entry_correlation"]["correlations"]["AAA"] = rho
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == expected
    assert "capacity" not in report["execution"]
    assert report["execution"]["correlation"]["threshold"] == 0.8
    assert_reconciles(report)


def test_missing_held_pair_skips_entry_without_inventing_correlation(admitted):
    current = rows(
        symbols=("AAA", "BBB"), execution=capacity_policy(capacity=False, correlation=True)
    )
    current[1]["signals"][0]["entry_correlation"]["correlations"].pop("AAA")
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 0]
    assert "correlation_unavailable" in report["ledger"][1]["reason"]


def test_correlated_exposure_uses_friction_adjusted_prospective_equity(admitted):
    current = rows(
        symbols=("AAA", "BBB"), execution=capacity_policy(capacity=False, correlation=True)
    )
    current[1]["config"].update(cost_bps=100, slippage_bps=100)
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 1]
    last = report["ledger"][1]["execution_details"]
    assert last["correlated_exposure_before"] == 300
    assert 1 < last["correlated_capacity_shares"] < 2


def test_correlation_is_direct_not_transitive_and_never_gives_negative_credit(admitted):
    current = rows(
        symbols=("AAA", "BBB", "CCC"),
        order_size=20,
        execution=capacity_policy(capacity=False, correlation=True),
    )
    current[1]["signals"][0]["entry_correlation"]["correlations"]["AAA"] = 0.9
    current[2]["signals"][0]["entry_correlation"]["correlations"].update(AAA=0.9, BBB=-0.9)
    prices = snapshot(symbols=("AAA", "BBB", "CCC"))
    report = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == [2, 2, 2]
    assert report["ledger"][2]["execution_details"]["correlated_exposure_before"] == 200


def test_opening_exit_releases_correlated_exposure_but_no_capacity(admitted):
    current = rows(
        minute=True,
        symbols=("AAA", "BBB"),
        order_size=100,
        execution=capacity_policy(capacity=False, correlation=True),
    )
    current[0]["config"]["target_pct"] = 10
    current[1]["signals"][0]["timestamp"] = DAYS[0] + "T09:16:00+05:30"
    prices = snapshot(minute=True)
    prices["bars"]["AAA"][prices["timeline"][2]] = candle(120)
    report = native.evaluate(current, prices, 1000)
    assert [row["quantity"] for row in report["ledger"]] == [5, 5]
    assert report["ledger"][0]["exit_timing"] == "open"
    assert report["ledger"][1]["execution_details"]["correlated_exposure_before"] == 0
    assert_reconciles(report)


def test_correlation_counts_same_symbol_across_strategies_and_requires_account_modes_match(
    admitted,
):
    current = rows(execution=capacity_policy(capacity=False, correlation=True))
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 2]
    current[1]["execution_policy"]["capacity_mode"] = "prior20_broker_reported"
    with pytest.raises(ValueError, match="same execution policy"):
        native.evaluate(current, snapshot(), 1000)


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("max_symbol_exposure_pct", 20, 2),
        ("max_gross_exposure_pct", 20, 2),
        ("max_trade_risk_pct", 2, 1),
        ("max_daily_buy_turnover_pct", 20, 2),
    ],
)
def test_capacity_composes_with_existing_hard_limits(admitted, name, value, expected):
    current = rows(order_size=100, execution=capacity_policy(**{name: value}))
    report = native.evaluate(current[:1], snapshot(), 1000)
    assert report["ledger"][0]["quantity"] == expected
    assert receipt(report)["bought_shares"] == expected


def test_v3_report_identity_stays_exact():
    current = [
        controlled(execution=turnover_policy(50), stop_pct=20, target_pct=500, hold_sessions=3)
    ]
    report = native.evaluate(current, snapshot(), 1000)
    assert report["policy_version"] == native.TURNOVER_POLICY_VERSION
    assert "capacity" not in report["execution"] and "correlation" not in report["execution"]
    assert fingerprint(report) == "0a8d24028c126591d6884d8a6d5886945b39a8d690d09cf81d3be99207bd04fa"


def sealed_features(current, *, day=DAYS[1]):
    from research.portfolio_execution_features import CAPACITY_VERSION, CORRELATION_VERSION, _seal

    symbols = [row["signals"][0]["symbol"] for row in current]
    for row in current:
        event = row["signals"][0]
        common = {
            "symbol": event["symbol"],
            "entry_session": day,
            "from": "2025-10-01",
            "to": "2026-01-02",
            "available_at": "2026-01-02T15:30:00+05:30",
            "source_id": "c" * 64,
        }
        event["entry_capacity"] = _seal(
            {
                **common,
                "version": CAPACITY_VERSION,
                "observations": 20,
                "mean_daily_shares": 500,
                "basis": "provider_returned_historical_coordinates",
            }
        )
        event["entry_correlation"] = _seal(
            {
                **common,
                "version": CORRELATION_VERSION,
                "observations": 60,
                "correlations": dict.fromkeys(symbols, 1.0),
            }
        )
    return current


def test_real_sealed_feature_validators_connect_to_native_caps():
    current = sealed_features(rows(execution=capacity_policy(capacity=True, correlation=True)))
    before = deepcopy(current)
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 2]
    assert receipt(report)["bought_shares"] == 5
    assert current == before
    assert_reconciles(report)


@pytest.mark.parametrize(
    "mutation",
    ["tamper_volume", "future_volume", "wrong_symbol", "future_correlation", "missing_pair"],
)
def test_real_admission_rejects_changed_or_unavailable_evidence_before_order(mutation):
    from research.portfolio_execution_features import _seal

    current = sealed_features(
        rows(symbols=("AAA", "BBB"), execution=capacity_policy(capacity=True, correlation=True))
    )
    event = current[1]["signals"][0]
    if mutation == "tamper_volume":
        event["entry_capacity"]["mean_daily_shares"] = 10000
    else:
        key = (
            "entry_capacity"
            if mutation in ("future_volume", "wrong_symbol")
            else "entry_correlation"
        )
        value = {k: v for k, v in event[key].items() if k != "id"}
        if mutation == "wrong_symbol":
            value["symbol"] = "AAA"
        elif mutation == "missing_pair":
            value["correlations"].pop("AAA")
        else:
            value["available_at"] = DAYS[1] + "T15:30:00+05:30"
        event[key] = _seal(value)
    report = native.evaluate(current, snapshot(), 1000)
    assert [row["quantity"] for row in report["ledger"]] == [3, 0]
    assert report["ledger"][1]["status"] == "skipped"
    assert report["execution"]["activity"]["buy_fills"] == 1


def test_100_native_capacity_accounts_release_handles_and_memory(admitted, record_property):
    import psutil

    current = rows(
        symbols=("AAA", "BBB"),
        execution=capacity_policy(capacity=True, correlation=True),
        minute=True,
    )
    prices = snapshot(minute=True)
    expected = native.evaluate(current, prices, 1000)
    for _ in range(5):
        assert native.evaluate(current, prices, 1000) == expected
    gc.collect()
    process = psutil.Process()
    handles = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    before, rss, started, samples = handles(), process.memory_info().rss, time.perf_counter(), []
    for index in range(100):
        actual = native.evaluate(current, prices, 1000)
        assert actual == expected
        del actual
        if (index + 1) % 25 == 0:
            gc.collect()
            samples.append(process.memory_info().rss)
    gc.collect()
    record_property("accounts", 100)
    record_property("seconds", time.perf_counter() - started)
    record_property("handles_before", before)
    record_property("handles_after", handles())
    record_property("rss_change_bytes", process.memory_info().rss - rss)
    record_property("rss_samples", samples)
    assert handles() <= before + 4
    assert samples[-1] - samples[1] < 16 * 1024**2
