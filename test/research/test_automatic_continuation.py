"""Small durable decision tests; no optimizer, broker or external provider."""

# ruff: noqa: F811 -- imported isolated ledger fixture

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_research_supervisor import (  # noqa: F401
    CONVERSATION,
    OWNER,
    ROOT,
    complete,
    ledger,
    mutate,
)

from database.research_db import ResearchDecisionJob, ResearchSupervisor, ResearchWorker
from research.report_contract import fingerprint
from services import research_automation as auto
from services import research_supervisor as supervisor


def start(ledger, monkeypatch, **kwargs):
    from services.agent import builder

    monkeypatch.setattr(
        builder, "resolve_model", lambda *a: SimpleNamespace(litellm_id="test/model")
    )
    monkeypatch.setattr(
        auto,
        "_evidence",
        lambda store, owner, current: {
            "diagnosis": current["diagnosis"],
            "remaining_accounts": current["remaining_accounts"],
        },
    )
    with ledger.store.sessions.begin() as db:
        lease = db.get(ResearchWorker, 1)
        lease.token, lease.heartbeat = "worker-one", time.time()
    return auto.start(
        ledger.store,
        OWNER,
        ROOT,
        conversation_id=CONVERSATION,
        request_id="automatic-small-case",
        **kwargs,
    )


def state(ledger, plan):
    return supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)


def test_confirmed_command_submits_native_funding_without_more_model_prose(ledger, monkeypatch):
    plan = start(ledger, monkeypatch)
    assert plan["capital_policy_version"] == "research-capital-planning-v8"
    assert auto.run_one(ledger.store, "worker-one")
    result = state(ledger, plan)
    assert result["status"] == "running" and len(ledger.submissions) == 1
    assert result["proposals"][0]["remedy"] == "funding_and_size"
    assert result["automation"]["calls_spent"] == 0
    assert result["proposals"][0]["capital_policy_version"] == result["capital_policy_version"]
    assert not auto.run_one(ledger.store, "worker-one")
    assert len(ledger.submissions) == 1
    same = auto.start(
        ledger.store, OWNER, ROOT, conversation_id=CONVERSATION, request_id="automatic-small-case"
    )
    assert same["id"] == plan["id"] and len(ledger.submissions) == 1


def test_typed_model_decision_is_debited_once_and_native_action_is_actual(ledger, monkeypatch):
    plan = start(ledger, monkeypatch, model_id=1)
    calls = []

    def choose(evidence, model_id):
        calls.append(model_id)
        assert state(ledger, plan)["automation"]["calls_spent"] == 1
        return {
            "remedy": "funding_and_size",
            "reason": "Verified affordability constraint",
            "provider_called": True,
            "selection": "model",
            "usage": {},
        }

    assert auto.run_one(ledger.store, "worker-one", choose=choose)
    assert calls == [1] and len(ledger.submissions) == 1
    assert state(ledger, plan)["automation"]["decisions"][0]["provider_called"]


def test_provider_failure_never_retries_or_submits_and_keeps_debit(ledger, monkeypatch):
    plan = start(ledger, monkeypatch, model_id=1)

    def fail(*a):
        raise RuntimeError("secret upstream details")

    assert auto.run_one(ledger.store, "worker-one", choose=fail)
    result = state(ledger, plan)
    assert result["status"] == "stopped" and result["automation"]["calls_spent"] == 1
    assert "secret" not in json.dumps(result) and not ledger.submissions
    assert not auto.run_one(ledger.store, "worker-one", choose=fail)


def test_invalid_model_action_cannot_relax_limits_or_queue_work(ledger, monkeypatch):
    plan = start(ledger, monkeypatch, model_id=1)
    assert auto.run_one(
        ledger.store,
        "worker-one",
        choose=lambda *a: {
            "remedy": "change_risk_limit",
            "reason": "pretend",
            "settings": {"capital": 1e9},
        },
    )
    assert state(ledger, plan)["status"] == "stopped" and not ledger.submissions


def test_durable_decision_recovers_without_another_provider_call(ledger, monkeypatch):
    plan = start(ledger, monkeypatch)
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        saved = json.loads(row.state)
        saved["automation"]["decisions"].append(
            {
                "remedy": "funding_and_size",
                "reason": "Saved native decision",
                "seed_artifact": plan["source_result_artifact"],
                "provider_called": False,
            }
        )
        supervisor._put(row, saved)
        db.get(ResearchDecisionJob, plan["id"]).phase = "applying"
    assert auto.run_one(
        ledger.store, "worker-one", choose=lambda *a: pytest.fail("Repeated provider")
    )
    assert len(ledger.submissions) == 1


def test_no_effect_skip_keeps_verified_seed_and_selects_an_untried_remedy(ledger, monkeypatch):
    plan = start(ledger, monkeypatch, model_id=1, max_experiments=3)
    selected = []

    def choose(evidence, model_id):
        remedy = "reduce_concentration" if not selected else "lower_turnover"
        selected.append(remedy)
        return {
            "remedy": remedy,
            "reason": "Recorded development evidence supports this registered remedy",
            "provider_called": True,
            "selection": "model",
            "usage": {},
        }

    assert auto.run_one(ledger.store, "worker-one", choose=choose)
    current = state(ledger, plan)
    original_diagnosis = deepcopy(current["diagnosis"])
    job_id = current["proposals"][0]["job_id"]
    mutate(ledger, plan, lambda s: s["proposals"][0].update(spent=1, completed=1))
    complete(ledger, plan, job_id)
    report = ledger.artifacts[ledger.jobs[job_id].result_artifact]["result"]
    conclusion = report["supervisor_validation"]
    conclusion["no_effect"] = {"status": "unchanged"}
    conclusion["id"] = fingerprint({k: v for k, v in conclusion.items() if k != "id"})
    # The rejected probe must not replace the prior fully checked evidence with
    # its intentionally absent cost/prefix checks or repeat its model decision.
    advanced = supervisor.advance(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert advanced["source_result_artifact"] == plan["source_result_artifact"]
    assert advanced["seed_job_id"] == ROOT
    assert advanced["diagnosis"] == original_diagnosis
    assert advanced["proposals"][0]["spent"] == 1
    assert advanced["lost_accounts"] == 0
    assert auto.run_one(ledger.store, "worker-one", choose=choose)
    result = state(ledger, plan)
    assert selected == ["reduce_concentration", "lower_turnover"]
    assert len(ledger.submissions) == 2
    assert result["proposals"][1]["remedy"] == "lower_turnover"
    assert result["automation"]["calls_spent"] == 2


def test_uncertain_call_after_restart_stops_without_refund(ledger, monkeypatch):
    plan = start(ledger, monkeypatch, model_id=1)
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        saved = json.loads(row.state)
        saved["automation"]["calls_spent"] = 1
        supervisor._put(row, saved)
        job = db.get(ResearchDecisionJob, plan["id"])
        job.phase, job.claim_token = "deciding", "old-worker"
    assert auto.run_one(
        ledger.store, "worker-one", choose=lambda *a: pytest.fail("Repeated uncertain call")
    )
    assert state(ledger, plan)["automation"]["calls_spent"] == 1
    assert state(ledger, plan)["status"] == "stopped" and not ledger.submissions


def test_stopped_and_cancelled_work_never_opens_a_provider(ledger, monkeypatch):
    ledger.report["automatic_research"]["capital_planning"]["diagnosis"]["categories"] = {
        "position_limit": 5
    }
    ledger.report["automatic_research"]["decision"] = {}
    ledger.report["automatic_research"]["automatic_design"] = {}
    plan = start(ledger, monkeypatch)
    assert state(ledger, plan)["diagnosis"]["permitted_remedies"] == []
    assert auto.run_one(
        ledger.store, "worker-one", choose=lambda *a: pytest.fail("Unsupported decision")
    )
    assert not ledger.submissions
    assert not auto.run_one(ledger.store, "worker-one", stop_requested=lambda: True)


def test_unavailable_grid_routes_are_removed_before_advertisement(ledger, monkeypatch):
    recipe = ledger.report["automatic_research"]["recipe"]
    hold = ledger.report["strategies"][0]["config"]["hold_sessions"]
    recipe["ranges"][0]["search"]["hold_sessions"] = {"min": hold, "max": hold, "step": 1}
    ledger.report["strategies"][0]["execution_policy"]["allocation_mode"] = (
        "active_prior60_cluster_inverse_vol"
    )
    plan = start(ledger, monkeypatch)
    assert plan["diagnosis"]["permitted_remedies"] == ["funding_and_size"]
    assert set(plan["diagnosis"]["unavailable_remedies"]) == {
        "lower_turnover",
        "temporal_stability",
        "reduce_concentration",
    }


def test_recorded_development_cost_drag_can_justify_test_without_faking_failure(
    ledger, monkeypatch
):
    ledger.report["summary"]["net_return_pct"] = 2.0
    findings = ledger.report["automatic_research"]
    findings["capital_planning"]["diagnosis"]["categories"] = {}
    findings["automatic_design"] = {}
    findings["decision"] = {
        "checks": [{"kind": "higher_costs", "passed": True, "summary": {"net_return_pct": 1.0}}]
    }
    ledger.report["strategies"][0]["config"]["cost_bps"] = 8
    plan = start(ledger, monkeypatch)
    assert plan["diagnosis"]["permitted_remedies"] == ["lower_turnover"]
    assert findings["decision"]["checks"][0]["passed"] is True
    assert "development_opportunity" in plan["diagnosis"]
    strict = supervisor._available_diagnosis(
        ledger.report, {**findings["recipe"], "validation_mode": "chronological"}
    )
    assert strict["permitted_remedies"] == []


def test_same_lease_concurrency_cannot_double_debit_model_call(ledger, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from services.agent import research_decision

    plan = start(ledger, monkeypatch, model_id=1)
    entered, release = Event(), Event()

    def paused_rule(evidence):
        entered.set()
        assert release.wait(5)
        return None

    monkeypatch.setattr(research_decision, "native_decision", paused_rule)

    def choose(*a):
        return {
            "remedy": "funding_and_size",
            "reason": "Native affordability evidence",
            "provider_called": True,
            "selection": "model",
            "usage": {},
        }

    monkeypatch.setattr(research_decision, "select_decision", choose)
    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="research-decision-claim-test"
    ) as pool:
        running = pool.submit(auto.run_one, ledger.store, "worker-one")
        try:
            assert entered.wait(5)
            assert not auto.run_one(ledger.store, "worker-one")
        finally:
            release.set()
        assert running.result(timeout=5)
    assert state(ledger, plan)["automation"]["calls_spent"] == 1
    assert len(ledger.submissions) == 1


def test_definite_expired_auth_refreshes_once_and_keeps_both_model_debits(ledger, monkeypatch):
    from services.agent import chatgpt_oauth, research_decision

    plan = start(ledger, monkeypatch, model_id=1)
    calls, refreshes = [], []
    monkeypatch.setattr(
        chatgpt_oauth, "refresh_access_token", lambda: (refreshes.append(1) or True, None)
    )

    def choose(*a):
        calls.append(1)
        if len(calls) == 1:
            raise research_decision.DecisionAuthenticationExpired("Expired")
        return {
            "remedy": "funding_and_size",
            "reason": "Native affordability evidence",
            "provider_called": True,
            "usage": {},
        }

    assert auto.run_one(ledger.store, "worker-one", choose=choose)
    assert state(ledger, plan)["automation"]["calls_spent"] == 1
    assert state(ledger, plan)["automatic_phase"] == "queued" and not ledger.submissions
    assert auto.run_one(ledger.store, "worker-one", choose=choose)
    assert state(ledger, plan)["automation"]["calls_spent"] == 2
    assert len(refreshes) == 1 and len(ledger.submissions) == 1


def test_auth_failure_cannot_retry_past_remaining_model_allowance(ledger, monkeypatch):
    from services.agent import chatgpt_oauth, research_decision

    plan = start(ledger, monkeypatch, model_id=1, max_model_calls=1)
    monkeypatch.setattr(
        chatgpt_oauth, "refresh_access_token", lambda: pytest.fail("No allowance for retry")
    )

    def expired(*a):
        raise research_decision.DecisionAuthenticationExpired("Expired")

    assert auto.run_one(ledger.store, "worker-one", choose=expired)
    assert state(ledger, plan)["status"] == "stopped"
    assert state(ledger, plan)["automation"]["calls_spent"] == 1
    assert not auto.run_one(ledger.store, "worker-one", choose=expired)
