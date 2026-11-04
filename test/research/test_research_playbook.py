"""Reviewed intent remains fixed through native proposal and update memory."""

# ruff: noqa: F811 -- imported isolated ledger fixture

import json
import time
from copy import deepcopy

import pytest
from test_available_history_protocol import available
from test_research_supervisor import (
    ARTIFACT,
    CONVERSATION,
    OWNER,
    ROOT,
    complete,
    ledger,
    open_plan,
)

from database.research_db import (
    ResearchJourney,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchSupervisor,
)
from research.automatic_protocol import compile_recipe, normalize_auto
from research.automatic_timing import _clock_config
from research.automatic_walk_forward import _context, _intent_program, _screening_program
from research.report_contract import settings_identity
from services import research_memory as memory
from services import research_playbook as playbook
from services import research_supervisor as supervisor


def reviewed():
    value = available()
    value["portfolio"] = playbook.intent(value["portfolio"], style="positional")
    value["strategies"] = [
        {**old, **new}
        for old, new in zip(value["strategies"], value["portfolio"]["strategies"], strict=True)
    ]
    return value


def test_new_intent_freezes_signals_style_risk_engine_and_full_budget():
    value = reviewed()
    before = deepcopy(value)
    recipe = compile_recipe(value)
    assert value == before
    assert recipe["execution_intent"]["hypothesis_ids"] == ["none"]
    assert recipe["budget"]["proposals"] == 408
    assert recipe["budget"]["max_simulations"] == 512
    assert recipe["budget"]["fits"] == 6
    contract = playbook.describe(value["portfolio"])
    playbook.validate_settings(contract, value["portfolio"]["strategies"])
    assert contract["objective"] == "robust_improvement"
    assert contract["capital_policy"] == "estimate_required_without_user_ceiling"
    for row in recipe["timing_research"]["strategies"]:
        config = value["portfolio"]["strategies"][0]["config"]
        for branch in row["branches"]:
            assert _clock_config(config, row, branch).get("trade_horizon") != "intraday"


def test_fixed_signal_program_removes_discretionary_filters_and_uses_refinement_slots():
    from research.automatic_timing import axes as timing_axes
    from research.automatic_trailing import axes as trailing_axes
    from research.connectors.optuna_portfolio import _prepare
    from research.execution_search import axes as execution_axes

    value = reviewed()
    recipe = compile_recipe(value)
    # Use native declared axes and catalogue, including every pre-existing
    # family; only the new opt-in restricts what the optimiser can select.
    axes = {
        **_prepare(value["strategies"])[1],
        **timing_axes(recipe["timing_research"]),
        **trailing_axes(recipe["trailing_research"]),
        **execution_axes(recipe["execution_research"]),
    }
    entries = _context(recipe["version"]).catalog()
    screening = _screening_program(
        recipe["version"], entries, axes, recipe["timing_research"], recipe["execution_research"]
    )
    chosen, reduced = _intent_program(recipe, entries, screening)
    assert [row["id"] for row in chosen] == ["none"]
    assert all(row["hypothesis"] == "none" for row in reduced)
    assert len(reduced) < len(screening) < 68
    assert _intent_program({}, entries, screening) == (entries, screening)


@pytest.mark.parametrize("kind", ["style", "filter", "risk", "identity", "objective", "engine"])
def test_remedies_cannot_change_reviewed_boundaries(kind):
    portfolio = reviewed()["portfolio"]
    contract, settings = playbook.describe(portfolio), deepcopy(portfolio["strategies"])
    if kind == "style":
        settings[0]["config"]["trade_horizon"] = "intraday"
    elif kind == "filter":
        settings[0]["entry_condition"] = {"hypothesis": {"id": "momentum_10_gt0__all"}}
    elif kind == "risk":
        settings[0]["execution_policy"]["risk_limits"]["max_open_positions"] = 100
    elif kind == "identity":
        settings[0]["id"] = "different-source"
    else:
        contract[kind] = "changed"
    with pytest.raises(ValueError):
        playbook.validate_settings(contract, settings)


def test_legacy_automatic_settings_and_bad_optins_are_unchanged():
    legacy = {"version": "automatic-hypotheses-v16"}
    assert normalize_auto(legacy) == legacy
    assert "execution_intent" not in compile_recipe(available())
    for flag in (True, "anything", None):
        with pytest.raises(ValueError):
            normalize_auto({**legacy, "intent_policy": flag})


def _owned_study(store):
    with store.sessions.begin() as db:
        db.add(
            ResearchLibraryExperiment(
                id="study",
                owner=OWNER,
                name="Study",
                draft="{}",
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        db.add(ResearchLibraryJob(experiment_id="study", job_id=ROOT, created_at=time.time()))


def test_same_study_memory_blocks_exact_negative_point_but_new_data_needs_fresh_validation(ledger):
    _owned_study(ledger.store)
    plan = open_plan(ledger)
    settings = ledger.report["strategies"]
    contract_id = "c" * 64
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        state = json.loads(row.state)
        state["research_context"] = {"inputs_artifact": "e" * 64, "contract": {"id": contract_id}}
        state["proposals"] = [
            {
                "remedy": "lower_turnover",
                "settings": settings,
                "outcome": "rejected",
                "no_effect": {"status": "unchanged"},
                "reasons": ["Identical execution"],
            }
        ]
        supervisor._put(row, state)
        db.add(
            ResearchJourney(
                experiment_id="study",
                owner=OWNER,
                phase="none",
                updated_at=time.time(),
                state=json.dumps(
                    {
                        "history": [
                            {
                                "job_id": ROOT,
                                "inputs_artifact": "e" * 64,
                                "contract_id": contract_id,
                                "outcome": "rejected",
                                "updated_at": time.time(),
                                "reasons": ["No passing setup"],
                            }
                        ]
                    }
                ),
            )
        )
    prior = memory.read(ledger.store, OWNER, "study", "e" * 64)
    match = memory.matching(prior, "lower_turnover", settings, contract_id)
    assert match["no_effect"] and match["settings_id"] == settings_identity(settings)
    assert any(row["remedy"] == "full_search" for row in prior["entries"])
    assert not any(row["independently_validated"] for row in prior["entries"])
    changed = memory.read(ledger.store, OWNER, "study", "f" * 64)
    assert memory.matching(changed, "lower_turnover", settings, contract_id) is None
    assert all(not row["same_inputs"] for row in changed["entries"])
    with pytest.raises(LookupError):
        memory.read(ledger.store, "other-owner", "study")


def test_review_context_is_bound_to_owned_study_and_exact_native_inputs(ledger, monkeypatch):
    value = reviewed()
    _owned_study(ledger.store)
    contract = playbook.describe(value["portfolio"])
    ledger.report["strategies"] = value["portfolio"]["strategies"]
    ledger.artifacts["e" * 64] = value
    context = {"experiment_id": "study", "inputs_artifact": "e" * 64, "contract": contract}
    plan = open_plan(ledger, research_context=context)
    assert plan["research_context"] == context
    proposal = supervisor.propose(
        ledger.store,
        OWNER,
        plan["id"],
        "funding_and_size",
        "Native cash shortfall",
        conversation_id=CONVERSATION,
    )["proposal"]
    assert proposal["experiment"]["problem"]
    assert proposal["experiment"]["hypothesis"]
    assert proposal["experiment"]["expected_evidence"]
    with pytest.raises(ValueError, match="observations changed"):
        open_plan(ledger, research_context={**context, "inputs_artifact": ARTIFACT})
    with pytest.raises(ValueError, match="different frozen"):
        open_plan(ledger)
    running = supervisor.advance(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    job_id = running["proposals"][0]["job_id"]
    complete(ledger, plan, job_id, accepted=True)
    finished = supervisor.advance(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    feedback = finished["proposals"][0]["experiment"]["feedback"]
    assert feedback["outcome"] == "validated" and feedback["reasons"] == []
    assert feedback["validated_settings_id"] == settings_identity(proposal["settings"])


def test_prior_exact_failure_is_removed_from_the_native_permitted_catalogue(ledger):
    value = reviewed()
    ledger.report["strategies"] = value["portfolio"]["strategies"]
    context = {"contract": playbook.describe(value["portfolio"])}
    recipe = ledger.report["automatic_research"]["recipe"]
    candidate = supervisor._settings_for(ledger.report, recipe, "funding_and_size")
    prior = {
        "entries": [
            {
                "same_inputs": True,
                "remedy": "funding_and_size",
                "settings_id": settings_identity(candidate),
                "contract_id": context["contract"]["id"],
                "outcome": "rejected",
            }
        ]
    }
    diagnosis = supervisor._reviewed_diagnosis(ledger.report, recipe, context, prior)
    assert "funding_and_size" not in diagnosis["permitted_remedies"]
    assert "already failed" in diagnosis["unavailable_remedies"]["funding_and_size"]
    prior["entries"][0]["same_inputs"] = False
    assert (
        "funding_and_size"
        in supervisor._reviewed_diagnosis(ledger.report, recipe, context, prior)[
            "permitted_remedies"
        ]
    )
