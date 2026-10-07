"""Earlier-period search never observes held-out market prices or positions."""

import copy

import pytest
from test_vectorbt_portfolio import candle, snapshot, strategy

from database.research_db import ResearchStore
from research import portfolio
from research.portfolio_validation import partition
from services.research_portfolio import run


def inputs():
    days = [f"2026-01-{day:02d}" for day in range(5, 16)]
    prices = snapshot(symbols=("AAA",), days=days)
    rows = [
        strategy(
            "a",
            allocation=100,
            signals=[
                {"symbol": "AAA", "date": day, "row": n + 2} for n, day in enumerate(days[:-1])
            ],
        )
    ]
    raw = {
        "capital": 10000,
        "strategies": [{k: v for k, v in row.items() if k != "signals"} for row in rows],
        "optimization": {"sampler": "tpe", "trials": 3, "seed": 0},
        "validation": {"train_pct": 70},
    }
    raw["strategies"][0].update(
        source_id="a" * 32, search={"target_pct": {"min": 1, "max": 3, "step": 1}}
    )
    checked = portfolio.normalize(raw)
    rows = [{**row, **checked["strategies"][i]} for i, row in enumerate(rows)]
    return {
        "portfolio": checked,
        "strategies": rows,
        "signals": rows[0]["signals"],
        "snapshot": prices,
        "versions": portfolio.execution_versions(checked),
        "frozen_prices": True,
    }


def test_partition_excludes_cross_boundary_holds_and_keeps_later_prices_out():
    evidence = inputs()
    training, testing, info = partition(evidence)
    assert info["train_to"] == "2026-01-11"
    assert info["test_from"] == "2026-01-12"
    assert max(training["snapshot"]["bars"]["AAA"]) == info["train_to"]
    assert min(testing["snapshot"]["bars"]["AAA"]) == info["test_from"]
    assert training["signal_coverage"]["excluded_signals"] == 2
    assert all("boundary" in row["reason"] for row in training["signal_coverage"]["exclusions"])
    assert not any(signal.get("research_exclusion") for signal in evidence["signals"])


def test_changing_later_prices_cannot_change_selected_parameters(tmp_path):
    store = ResearchStore(tmp_path / "research")
    store.initialize()

    def execute(evidence):
        spec = {"portfolio": evidence["portfolio"], "versions": evidence["versions"]}
        return run(
            store,
            "owner",
            evidence,
            spec,
            checkpoint=lambda *a: None,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )[0]

    original = inputs()
    changed = copy.deepcopy(original)
    for day in changed["snapshot"]["bars"]["AAA"]:
        if day >= "2026-01-12":
            changed["snapshot"]["bars"]["AAA"][day] = candle(100, high=130, low=50, close=70)
    first, second = execute(original), execute(changed)
    assert first["strategies"] == second["strategies"]
    assert first["summary"] == second["summary"]
    assert first["experiment"]["rows"] == second["experiment"]["rows"]
    assert first["validation"]["result"]["summary"] != second["validation"]["result"]["summary"]
    assert first["validation"]["result"]["summary"]["initial_capital"] == 10000


def test_holdout_requires_enough_distinct_signal_dates():
    value = inputs()
    value["signals"] = value["signals"][:4]
    with pytest.raises(ValueError, match="five different dates"):
        partition(value)
