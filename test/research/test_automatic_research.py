"""Selection, final-period isolation and recovery with real seeded Optuna."""

from copy import deepcopy

import pytest
from test_automatic_protocol import evidence

from research.automatic_protocol import compile_recipe
from research.automatic_research import run
from research.report_contract import settings_identity


def prepared():
    value = evidence(config={"hold_sessions": 1, "target_pct": 4, "stop_pct": 2})
    value["automatic_recipe"] = compile_recipe(value)
    return value


class Stopped(Exception):
    pass


class Engine:
    def __init__(self, value, *, final_loss=False, one_date=False, all_cash=False):
        self.recipe = value["automatic_recipe"]
        self.final_loss, self.one_date, self.all_cash = final_loss, one_date, all_cash
        self.calls = []

    def __call__(self, rows, snapshot, capital, progress=None):
        settings = [
            {k: deepcopy(row[k]) for k in ("id", "name", "allocation_pct", "config")}
            for row in rows
        ]
        period = next(
            name
            for name, span in self.recipe["periods"].items()
            if snapshot["sessions"][0] == span["from"]
        )
        self.calls.append((period, settings_identity(settings)))
        if progress:
            progress(1, 1)
        cfg = rows[0]["config"]
        # Deliberately smooth objective; changing the final period must never
        # change the chosen settings or Optuna's sequence of proposals.
        profit = 15 - abs(cfg["target_pct"] - 6) * 2 - cfg["cost_bps"] / 100
        if self.final_loss and period == "final" and cfg["target_pct"] != 4:
            profit = -50
        if self.all_cash:
            profit = 0
        return {
            "strategies": settings,
            "summary": {
                "net_return_pct": profit,
                "max_drawdown_pct": 2,
                "closed_trades": 0 if self.all_cash else 10,
            },
            "ledger": [
                {"status": "closed", "entry_date": snapshot["sessions"][0 if self.one_date else i]}
                for i in range(10)
            ],
        }


def execute(value, engine, *, saved=None, stop=None):
    checkpoints = []

    def checkpoint(state, counts):
        checkpoints.append(deepcopy(state))
        if stop and stop(state):
            raise Stopped()

    try:
        result = run(
            value,
            evaluate=engine,
            saved=saved,
            checkpoint=checkpoint,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    except Stopped:
        return None, checkpoints[-1]
    return result, checkpoints[-1] if checkpoints else saved


def test_real_optuna_search_compares_baseline_and_freezes_choice_before_final_outcome():
    value = prepared()
    good, state = execute(value, Engine(value))
    bad, _ = execute(value, Engine(value, final_loss=True))
    finding = good["automatic_research"]
    assert finding["status"] == "supported"
    assert not finding["selected_is_baseline"]
    assert finding["counts"]["proposals"] == 50
    assert finding["counts"]["simulations"] <= 70
    assert finding["selected_config_id"] == bad["automatic_research"]["selected_config_id"]
    assert bad["automatic_research"]["status"] == "not_supported"
    assert bad["automatic_research"]["final"]["summary"]["net_return_pct"] == -50
    assert good["strategies"] == bad["strategies"]
    assert [t["params"] for t in good["experiment"]["trials"]] == [
        t["params"] for t in bad["experiment"]["trials"]
    ]
    assert state["selection"]["config_id"] == finding["selected_config_id"]


@pytest.mark.parametrize("checkpoint_stage", ["baseline", "search", "selection", "final"])
def test_resume_retains_completed_native_work_at_each_stage(checkpoint_stage):
    value = prepared()
    engine = Engine(value)
    conditions = {
        "baseline": lambda s: s["simulations"] == 1,
        "search": lambda s: (
            s.get("search_checkpoint") and len(s["search_checkpoint"]["trials"]) == 7
        ),
        "selection": lambda s: s["selection"] is not None,
        "final": lambda s: s["final_report"] is not None,
    }
    partial, saved = execute(value, engine, stop=conditions[checkpoint_stage])
    assert partial is None
    calls_before = list(engine.calls)
    result, finished = execute(value, engine, saved=saved)
    assert engine.calls[: len(calls_before)] == calls_before
    expected_engine = Engine(value)
    expected, _ = execute(value, expected_engine)
    assert engine.calls == expected_engine.calls
    assert result["summary"] == expected["summary"]
    assert result["automatic_research"] == expected["automatic_research"]
    assert finished["simulations"] == len(engine.calls)
    again, _ = execute(value, engine, saved=finished)
    assert len(engine.calls) == finished["simulations"]
    assert again["automatic_research"] == result["automatic_research"]


@pytest.mark.parametrize("options", [{"one_date": True}, {"all_cash": True}])
def test_crowded_single_date_or_zero_trades_cannot_win(options):
    value = prepared()
    engine = Engine(value, **options)
    result, state = execute(value, engine)
    assert result["automatic_research"]["selected_is_baseline"]
    assert result["automatic_research"]["status"] == "inconclusive"
    # Final unchanged settings evaluated once even when no candidate qualified.
    assert len([call for call in engine.calls if call[0] == "final"]) == 1
    assert state["simulations"] <= 70


def test_checkpoint_cannot_change_prices_or_completed_evidence():
    value = prepared()
    _, saved = execute(value, Engine(value), stop=lambda s: s["simulations"] == 1)
    corrupted = deepcopy(saved)
    corrupted["simulations"] += 1
    with pytest.raises(ValueError, match="checkpoint inputs or evidence changed"):
        execute(value, Engine(value), saved=corrupted)
    changed = deepcopy(value)
    changed["snapshot"]["bars"]["AAA"][changed["snapshot"]["sessions"][0]]["close"] += 1
    with pytest.raises(ValueError, match="recipe changed"):
        execute(changed, Engine(changed), saved=saved)
