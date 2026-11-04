"""Small end-to-end remedy receipts; no optimizer or scanner is run."""

import json
from copy import deepcopy

import pytest
from test_operating_validation import Engine, audit, plan, report

from research.operating_loop import run
from research.operating_validation import descriptor
from research.report_contract import fingerprint, settings_identity


def case(monkeypatch, *, bad_cost=False, remaining=13):
    value = plan(remaining_accounts=remaining)
    point = value["inputs"]["operating_point"]
    point["settings"] = value["selected_settings"]
    point["option_index"] = 1
    native = report(point["settings"])
    native["evaluation_basis"] = {"evidence_id": "frozen-native-basis"}
    original = report(value["inputs"]["baseline_settings"], -1, {"id": "none"})
    primary_id = settings_identity(original["strategies"])
    original["automatic_research"] = {
        "selected_config_id": primary_id,
        "decision": {"outcome": "insufficient_evidence", "selected_config_id": primary_id},
        "counts": {"proposals": 408},
        "capital_planning": {"operating_point": point},
    }
    primary_row = {"config_id": primary_id, "strategies": deepcopy(original["strategies"])}
    original["experiment"] = {
        "recommendation_id": primary_id,
        "objective_winner_id": primary_id,
        "rows": [primary_row],
        "trials": [{"number": 65}],
        "selected_reports": {primary_id: deepcopy(original)},
    }
    state = {
        "selection": {"hypothesis": {"id": "chosen"}},
        "simulations": 4,
        "attempts": 4,
        "automatic_actions": {
            "accounts": [
                {
                    "report": native,
                    "receipt": {"option_index": 1, "counts": value["capacity_counts"]},
                }
            ]
        },
    }
    recipe = {
        "operating_validation": descriptor(),
        "validation_mode": "exploratory_only",
        "ranges": [],
    }
    monkeypatch.setattr(
        "research.operating_loop.challenges", lambda *_: {"neighbors": value["inputs"]["neighbors"]}
    )
    engine = Engine(bad_cost=bad_cost)
    frozen = deepcopy(original)

    def execute(saved=None):
        def calculate(*args):
            result = engine(*args)
            state["simulations"] += 1
            state["attempts"] += 1
            return result

        if saved is not None:
            state.clear()
            state.update(saved)
        return run(
            deepcopy(frozen),
            state=state,
            recipe=recipe,
            baseline=value["inputs"]["baseline_settings"],
            baseline_hypothesis={"id": "none"},
            available_accounts=lambda: max(0, remaining - (state["attempts"] - 4)),
            evaluate=calculate,
            audit=audit,
            persist=lambda: None,
            control=lambda: None,
        )

    return execute, state, engine, frozen


def test_success_selects_the_actual_checked_account_and_preserves_search(monkeypatch):
    execute, state, engine, original = case(monkeypatch)
    result = execute()
    conclusion = result["automatic_research"]["operating_validation"]
    assert conclusion["outcome"] == "validated"
    assert result["summary"]["initial_capital"] == 200000
    assert result["strategies"][0]["config"]["initial_capital"] == 200000
    assert result["experiment"]["recommendation_id"] == settings_identity(result["strategies"])
    assert (
        result["experiment"]["primary_recommendation_id"]
        == original["experiment"]["recommendation_id"]
    )
    assert (
        result["experiment"]["objective_winner_id"] == original["experiment"]["objective_winner_id"]
    )
    assert result["experiment"]["trials"] == original["experiment"]["trials"]
    assert result["automatic_research"]["counts"]["proposals"] == 408
    assert not result["automatic_research"]["independently_validated"]
    assert result["experiment"]["rows"] == original["experiment"]["rows"]
    row = result["experiment"]["operating_candidate"]
    assert row["trial_number"] is None and row["provenance"] == "automatic_operating_validation"
    assert row["operating_validation_id"] == conclusion["id"]
    assert conclusion["selected_report_id"] == fingerprint(
        {
            k: v
            for k, v in result["experiment"]["selected_reports"][row["config_id"]].items()
            if k != "automatic_research"
        }
    )
    assert len(engine.calls) == 13
    saved = json.loads(json.dumps(state, sort_keys=True))
    engine.calls.clear()
    reopened = execute(saved)
    assert reopened == result and engine.calls == []


@pytest.mark.parametrize(
    "remaining,bad_cost,outcome", [(13, True, "rejected"), (12, False, "blocked")]
)
def test_failed_or_unfunded_checks_retain_original_financial_report(
    monkeypatch, remaining, bad_cost, outcome
):
    execute, _, engine, original = case(monkeypatch, bad_cost=bad_cost, remaining=remaining)
    result = execute()
    assert result["automatic_research"]["operating_validation"]["outcome"] == outcome
    assert result["summary"] == original["summary"]
    assert result["strategies"] == original["strategies"]
    assert result["experiment"]["rows"] == original["experiment"]["rows"]
    assert len(engine.calls) == (13 if bad_cost else 0)


def test_recovery_cannot_change_capacity_selected_point(monkeypatch):
    execute, state, _, _ = case(monkeypatch)
    execute()
    state["automatic_actions"]["accounts"][0]["receipt"]["counts"]["cash_shortfall"] = 1
    with pytest.raises(ValueError, match="programme changed"):
        execute()


def test_identical_point_reuses_primary_checks_without_replacing_recorded_trial(monkeypatch):
    execute, state, engine, original = case(monkeypatch)
    original["strategies"] = deepcopy(
        state["automatic_actions"]["accounts"][0]["report"]["strategies"]
    )
    original["summary"] = deepcopy(state["automatic_actions"]["accounts"][0]["report"]["summary"])
    original["evaluation_basis"] = deepcopy(
        state["automatic_actions"]["accounts"][0]["report"]["evaluation_basis"]
    )
    original["automatic_research"]["selected_config_id"] = settings_identity(original["strategies"])
    original["automatic_research"]["decision"].update(
        id="frozen-primary-decision", reasons=[], historical_candidate=False
    )
    # case() freezes the original object passed into execute by closure.
    result = execute()
    assert result["automatic_research"]["operating_validation"]["outcome"] == "unchanged"
    assert engine.calls == []
    assert result["experiment"]["rows"] == original["experiment"]["rows"]
