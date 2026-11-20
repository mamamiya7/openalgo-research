"""Small frozen accounts: native SDK, semantic differences and fail-closed admission."""

import copy
import json

import pytest
from test_vectorbt_portfolio import DAYS, candle, signal, strategy
from test_vectorbt_portfolio import snapshot as base_snapshot

from research.connectors import openalgo_portfolio as adapter
from research.connectors import openalgo_runtime as runtime

native = pytest.mark.skipif(
    not runtime.status()["available"], reason="optional SDK overlay not installed"
)


def snapshot():
    value = base_snapshot()
    value["instruments"] = {
        s: {"currency": "INR", "tick_size": "0.05", "lot_size": 1, "price_precision": 2}
        for s in value["bars"]
    }
    return value


def test_overlapping_repeated_signals_are_not_merged_or_excluded():
    rows = [strategy(signals=[signal(), signal(day=1, row=3)], hold_sessions=2)]
    before = copy.deepcopy(rows)
    with pytest.raises(ValueError, match="overlapping repeated signals"):
        adapter.validate(rows, snapshot(), 1000)
    assert before == rows


def test_cross_strategy_same_symbol_overlap_is_also_rejected():
    with pytest.raises(ValueError, match="overlapping repeated signals"):
        adapter.validate([strategy("a"), strategy("b")], snapshot(), 1000)


def test_maximum_search_holding_is_checked_before_acquiring_prices():
    from research.connectors.optuna_portfolio import requirement_strategies

    rows = [strategy(signals=[signal(), signal(day=2)])]
    rows[0]["search"] = {"hold_sessions": {"min": 1, "max": 2, "step": 1}}
    adapter.validate_signal_windows(rows, DAYS)
    required = requirement_strategies(rows, {"sampler": "grid", "trials": 2})
    with pytest.raises(ValueError, match="overlapping repeated signals"):
        adapter.validate_signal_windows(required, DAYS)


def test_metadata_only_partial_install_is_not_available(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(runtime, "sdk_path", lambda: tmp_path)
    monkeypatch.setattr(
        runtime.metadata,
        "distributions",
        lambda **kw: [SimpleNamespace(metadata={"Name": "openalgo"}, version="2.2.0")],
    )
    assert runtime.status()["available"] is False


def test_missing_common_coverage_fails_without_inventing_prices():
    prices = snapshot()
    del prices["bars"]["BBB"][DAYS[1]]
    rows = [strategy("a"), strategy("b", signals=[signal("BBB", day=1)])]
    original = copy.deepcopy(prices)
    with pytest.raises(ValueError, match="shared frozen daily coverage"):
        adapter.validate(rows, prices, 1000)
    assert prices == original


def test_repeated_overlap_rejected_before_worker_starts(monkeypatch):
    monkeypatch.setattr(
        runtime.subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("Incompatible repeated signals must never start an account"),
    )
    with pytest.raises(ValueError, match="overlapping repeated signals"):
        runtime.evaluate([strategy(signals=[signal(), signal(day=1)])], snapshot(), 1000)


@native
def test_native_target_and_contract_note_totals_reconcile():
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[1]] = candle(100, 112, 99, 108)
    result = runtime.evaluate([strategy(target_pct=10)], prices, 1000)
    row = result["ledger"][0]
    assert row["status"] == "closed"
    assert row["exit_price"] == 110
    assert row["pnl"] == pytest.approx(result["summary"]["net_pnl"], abs=0.01)
    for point in result["equity_curve"]:
        own = result["per_strategy"][0]["equity_curve"]
        attributed = next(p for p in own if p["date"] == point["date"])
        assert attributed["net_pnl"] == pytest.approx(point["equity"] - 1000, abs=0.01)


@native
def test_cancellation_reaps_worker_and_removes_owned_scratch():
    before = set((runtime.ROOT / ".agent-native/runtime-jobs").glob("openalgo-*"))

    def cancel(*args):
        raise InterruptedError("test cancellation")

    with pytest.raises(InterruptedError, match="cancellation"):
        runtime.evaluate([strategy()], snapshot(), 1000, progress=cancel)
    assert set((runtime.ROOT / ".agent-native/runtime-jobs").glob("openalgo-*")) == before


@pytest.mark.parametrize(
    "config", [{"cost_bps": 10}, {"slippage_bps": 10}, {"trailing_pct": 5}, {"hold_minutes": 5}]
)
def test_unsupported_financial_settings_fail_closed(config):
    with pytest.raises(ValueError):
        adapter.validate([strategy(**config)], snapshot(), 1000)


@native
def test_native_shared_account_reconciles_and_keeps_core_sdk_pin():
    from importlib import metadata

    version = metadata.version("openalgo")
    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(110)
    rows = [strategy("a"), strategy("b", signals=[signal("BBB")])]
    result = runtime.evaluate(rows, prices, 1000)
    assert metadata.version("openalgo") == version == "2.0.5"
    assert result["execution"]["engine"] == "openalgo"
    assert result["execution"]["engine_version"] == "2.2.0"
    assert result["summary"]["closed_trades"] == 2
    assert all(r["quantity"] == 4 for r in result["ledger"])
    assert sum(r["net_pnl"] for r in result["per_strategy"]) == pytest.approx(
        result["summary"]["net_pnl"], abs=0.02
    )
    assert sum(r["fees"] for r in result["ledger"]) > 0
    json.dumps(result, allow_nan=False)


@native
def test_nonoverlapping_repeated_signals_keep_distinct_source_rows():
    rows = [strategy(signals=[signal(), signal(day=2, row=9)], allocation=100, hold_sessions=1)]
    result = runtime.evaluate(rows, snapshot(), 1000)
    assert [r["source_row"] for r in result["ledger"]] == [2, 9]
    assert result["ledger"][0]["status"] == "closed"
    assert result["ledger"][1]["quantity"] > 0
    assert result["ledger"][1]["status"] == "pending"
    assert result["ledger"][1]["exit_date"] is None


@native
@pytest.mark.timeout(180)
def test_opening_exit_difference_is_explicit_against_vectorbt():
    # A fresh Windows runner compiles VectorBT's account kernels on this test.
    # Allow that one-time startup; keep every execution/price assertion below.
    from research.connectors.vectorbt_portfolio import evaluate

    prices = snapshot()
    prices["bars"]["AAA"][DAYS[2]] = candle(105, 111, 104, 110)
    rows = [strategy(stop_pct=50, target_pct=100)]
    current = evaluate(rows, prices, 1000)
    optional = runtime.evaluate(rows, prices, 1000)
    assert current["ledger"][0]["exit_price"] == 110
    assert optional["ledger"][0]["exit_price"] == 105
    assert optional["execution"]["holding_exit"] == "opening_of_entry_session_plus_hold_sessions"
    assert any("unlike VectorBT" in s for s in optional["limits"])
