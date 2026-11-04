"""Impossible fixed-signal samples stop selection without relaxing acceptance."""

from copy import deepcopy

import pytest
from test_research_supervisor import CONVERSATION, OWNER, ROOT, ledger  # noqa: F401

from research.automatic_design import descriptor
from research.report_contract import fingerprint
from services import research_supervisor as supervisor


def sealed(value):
    return {**value, "id": fingerprint(value)}


def case(count):
    recipe = sealed({
        "version": "automatic-hypotheses-v16",
        "automatic_design": descriptor(),
        "decision_preflight": sealed({"signal_count": count}),
        "execution_intent": {"version": "fixed-signals-v1", "engine": "vectorbt", "hypothesis_ids": ["none"]},
    })
    return recipe, {"contract": sealed({"version": "research-execution-playbook-v1", "signal_policy": "fixed_signals", "engine": "vectorbt"})}


@pytest.mark.parametrize("count", [0, 1, 10, 29])
def test_fewer_opportunities_than_required_trades_has_exact_reason(count):
    recipe, context = case(count)
    before = deepcopy(recipe)
    reason = supervisor._structural_sample_stop(recipe, context)
    assert f"at most {count} closed trades" in reason
    assert "requires at least 30" in reason
    assert "More signal data is required" in reason
    assert recipe == before


@pytest.mark.parametrize("count", [30, 31, 90, True, -1, None])
def test_sufficient_or_unknown_bound_does_not_stop(count):
    assert supervisor._structural_sample_stop(*case(count)) is None


@pytest.mark.parametrize("part", ["recipe", "preflight", "policy", "signals", "engine", "hypothesis"])
def test_unknown_or_changed_contract_is_not_an_impossibility_proof(part):
    recipe, context = case(10)
    if part == "recipe":
        recipe["id"] = "changed"
    elif part == "preflight":
        recipe["decision_preflight"]["signal_count"] = 9
        recipe = sealed({k: v for k, v in recipe.items() if k != "id"})
    elif part == "policy":
        recipe["automatic_design"]["minimum_closed_trades"] = 10
        recipe = sealed({k: v for k, v in recipe.items() if k != "id"})
    elif part == "signals":
        context["contract"]["signal_policy"] = "generated_signals"
    elif part == "engine":
        context["contract"]["engine"] = "nautilus"
    else:
        recipe["execution_intent"]["hypothesis_ids"] = ["other"]
        recipe = sealed({k: v for k, v in recipe.items() if k != "id"})
    assert supervisor._structural_sample_stop(recipe, context) is None


def test_catalogue_does_not_construct_or_offer_futile_experiments(monkeypatch):
    from services import research_experiments

    recipe, context = case(10)
    monkeypatch.setattr(research_experiments, "build", lambda *a, **k: pytest.fail("Do not build futile settings"))
    result = supervisor._experiment_catalog({}, recipe, {"research_context": context})
    assert result["options"] == []
    assert result["stop_reason"] == result["feedback"]["structural_stop_reason"]
    assert result["unavailable"][0]["method"] == "fixed_signal_sample"


def test_structural_stop_does_not_revisit_checked_branches(monkeypatch):
    state = {"continuation_policy": supervisor.ADAPTIVE_POLICY,
             "validation_mode": "exploratory_only", "status": "awaiting_plan",
             "experiment_options": [], "diagnosis": {"missing_evidence": False},
             "lost_accounts": 0, "automation": {},
             "experiment_feedback": {"structural_stop_reason": "Ten opportunities cannot meet thirty trades"}}
    monkeypatch.setattr(supervisor, "status", lambda *a, **k: state)
    assert supervisor.resume_frontier(None, "owner", "id", conversation_id="case") is state


def test_initial_stopped_grant_persists_specific_cause(ledger, monkeypatch):
    recipe, context = case(10)
    original=ledger.report['automatic_research']['recipe']
    recipe=sealed({**original, **{k:v for k,v in recipe.items() if k!='id'}})
    ledger.report['automatic_research']['recipe']=recipe
    monkeypatch.setattr(supervisor, '_research_context', lambda *a: (context, {'entries':[]}))
    monkeypatch.setattr(supervisor, '_reviewed_diagnosis', lambda *a: {'permitted_remedies':['lower_turnover'], 'missing_evidence':False})
    current=supervisor.open_supervisor(ledger.store,OWNER,ROOT,conversation_id=CONVERSATION,
        request_id='specific_structural_stop',research_context=context,
        experiment_policy=supervisor.EXPERIMENT_POLICY,continuation_policy=supervisor.ADAPTIVE_POLICY)
    assert current['status']=='stopped'
    assert current['stopping_reason']==current['continuation_stop_reason']
    assert 'at most 10 closed trades' in current['stopping_reason']
    assert not current['proposals']
