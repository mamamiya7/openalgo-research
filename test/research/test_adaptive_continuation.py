"""Explicit adaptive authority consumes existing budgets; it never starts a scan."""

# ruff: noqa: F811 -- imported isolated ledger fixtures
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_feedback_replan import concrete_option, stopped  # noqa: F401
from test_research_supervisor import CONVERSATION, OWNER, complete, ledger  # noqa: F401

from database.research_db import ResearchDecisionJob, ResearchSupervisor, ResearchWorker
from services import research_automation as automation
from services import research_supervisor as supervisor

POLICY = "adaptive-feedback-v1"


def current(fixture, plan):
    return supervisor.status(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)


def change(fixture, plan, callback):
    with fixture.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        state = json.loads(row.state)
        callback(state)
        supervisor._put(row, state)


def resume(fixture, plan, **kwargs):
    return supervisor.replan(
        fixture.store,
        OWNER,
        plan["id"],
        conversation_id=CONVERSATION,
        request_id="explicit-adaptive-continuation",
        continuation_policy=POLICY,
        **kwargs,
    )


def test_explicit_adaptive_replan_uses_remaining_budget_after_old_caps(stopped):
    fixture, plan = stopped
    change(fixture, plan, lambda s: s["automation"].update(calls_spent=3))
    before = current(fixture, plan)
    result = resume(fixture, plan)
    assert before["remaining_accounts"] == result["remaining_accounts"] == 95
    assert result["original_attempts"] == before["original_attempts"] == 400
    assert result["original_simulations"] == before["original_simulations"]
    assert result["proposals"] == before["proposals"]
    assert result["automation"]["calls_spent"] == 3
    assert result["max_experiments"] == supervisor.ADAPTIVE_EXPERIMENT_LIMIT == 24
    assert result["automation"]["max_model_calls"] == supervisor.ADAPTIVE_EXPERIMENT_LIMIT
    assert result["status"] == "awaiting_plan" and result["automatic_phase"] == "queued"
    assert not fixture.submissions


def test_adaptive_resume_is_idempotent_and_retains_actual_history(stopped):
    fixture, plan = stopped
    before = current(fixture, plan)
    first = resume(fixture, plan)
    again = resume(fixture, plan)
    assert again["replans"] == first["replans"]
    assert len(again["replans"]) == 1
    assert again["max_experiments"] == first["max_experiments"]
    assert again["automation"] == first["automation"]
    assert again["proposals"] == before["proposals"]
    assert again["remaining_accounts"] == before["remaining_accounts"]
    assert not fixture.submissions


@pytest.mark.parametrize("blocked", ["missing", "pending", "running", "budget", "later"])
def test_adaptive_policy_cannot_relax_native_evidence_or_validation_authority(stopped, blocked):
    fixture, plan = stopped

    def alter(state):
        if blocked == "missing":
            state["diagnosis"]["missing_evidence"] = True
        if blocked == "pending":
            state["proposals"][-1]["outcome"] = None
        if blocked == "running":
            state["status"] = "running"
        if blocked == "budget":
            # Twelve accounts remain; a full non-funding validation needs14.
            state["original_attempts"] = state["original_simulations"] = 483
        if blocked == "later":
            state["validation_mode"] = "later_period"

    change(fixture, plan, alter)
    before = current(fixture, plan)
    with pytest.raises(ValueError):
        resume(fixture, plan)
    after = current(fixture, plan)
    assert after["automation"] == before["automation"]
    assert after["proposals"] == before["proposals"]
    assert after["remaining_accounts"] == before["remaining_accounts"]
    assert not fixture.submissions


def test_adaptive_authority_remains_owner_and_conversation_bound(stopped):
    fixture, plan = stopped
    for owner, conversation in [("another-owner", CONVERSATION), (OWNER, "another-chat")]:
        with pytest.raises(LookupError):
            supervisor.replan(
                fixture.store,
                owner,
                plan["id"],
                conversation_id=conversation,
                request_id="explicit-adaptive-continuation",
                continuation_policy=POLICY,
            )
    assert not fixture.submissions


def test_adaptive_resume_cannot_invent_experiments_when_catalogue_is_empty(stopped, monkeypatch):
    fixture, plan = stopped
    monkeypatch.setattr(
        supervisor,
        "_experiment_catalog",
        lambda *a: {"options": [], "feedback": {}, "unavailable": ["No registered effect remains"]},
    )
    before = current(fixture, plan)
    with pytest.raises(ValueError, match="No supported"):
        resume(fixture, plan)
    assert current(fixture, plan)["automation"] == before["automation"]
    assert not fixture.submissions


def test_ordinary_replan_cannot_silently_upgrade_exhausted_authority(stopped):
    fixture, plan = stopped
    change(fixture, plan, lambda s: s["automation"].update(calls_spent=3))
    before = deepcopy(current(fixture, plan))
    with pytest.raises(ValueError):
        supervisor.replan(
            fixture.store,
            OWNER,
            plan["id"],
            conversation_id=CONVERSATION,
            request_id="legacy-no-silent-expansion",
        )
    assert current(fixture, plan)["automation"] == before["automation"]
    with fixture.store.sessions() as db:
        assert db.get(ResearchDecisionJob, plan["id"]).phase == "stopped"


@pytest.mark.parametrize("remaining", [13, 14, 16, 17])
def test_only_fully_affordable_native_checks_are_advertised(stopped, monkeypatch, remaining):
    fixture, plan = stopped
    resume(fixture, plan)
    settings = deepcopy(fixture.report["strategies"])
    performance_settings = deepcopy(settings)
    performance_settings[0]["config"]["hold_sessions"] = 3
    monkeypatch.setattr(
        supervisor,
        "_experiment_catalog",
        lambda *a: {
            "options": [
                concrete_option(a[2]["source_result_artifact"], "funding_and_size", settings),
                concrete_option(
                    a[2]["source_result_artifact"], "temporal_stability", performance_settings
                ),
            ],
            "feedback": {},
        },
    )

    def spend(state):
        state["original_attempts"] = state["original_simulations"] = 512 - 17 - remaining

    change(fixture, plan, spend)
    result = current(fixture, plan)
    assert result["remaining_accounts"] == remaining
    remedies = [o["remedy"] for o in result["experiment_options"]]
    if remaining < 14:
        assert remedies == []
        assert "14-account" in result["continuation_stop_reason"]
        assert str(remaining) in result["continuation_stop_reason"]
        assert "validation was not reduced" in result["continuation_stop_reason"]
    elif remaining < 17:
        assert remedies == ["temporal_stability"]
    else:
        assert remedies == ["funding_and_size", "temporal_stability"]
    assert result["automation"]["calls_spent"] == 1
    assert not fixture.submissions


def test_evidence_exhaustion_has_a_different_reason_from_budget_exhaustion(stopped, monkeypatch):
    fixture, plan = stopped
    resume(fixture, plan)
    monkeypatch.setattr(
        supervisor,
        "_experiment_catalog",
        lambda *a: {"options": [], "feedback": {}, "unavailable": []},
    )
    result = current(fixture, plan)
    assert result["remaining_accounts"] == 95
    assert result["experiment_options"] == []
    assert "No untried supported experiment" in result["continuation_stop_reason"]
    assert result["experiment_frontier"]["feedback"]["complete"] is True
    assert "cannot fund" not in result["continuation_stop_reason"]
    assert not fixture.submissions


def enable_worker(fixture, monkeypatch):
    with fixture.store.sessions.begin() as db:
        lease = db.get(ResearchWorker, 1)
        lease.token, lease.heartbeat = "adaptive-worker", time.time()
    monkeypatch.setattr(
        automation,
        "_evidence",
        lambda store, owner, saved: {
            "diagnosis": saved["diagnosis"],
            "experiment_options": [
                {k: v for k, v in option.items() if k != "settings"}
                for option in saved["experiment_options"]
            ],
        },
    )


def provide_diagnosis_evidence(fixture):
    """Seal the existing tiny report's explicit failed challenges for the new chooser."""
    from research.automatic_decision import descriptor
    from research.report_contract import fingerprint, settings_identity

    report = fixture.report
    report["summary"].update(net_return_pct=1.0, max_drawdown_pct=1.0, closed_trades=35)
    findings = report["automatic_research"]
    recipe = findings["recipe"]
    policy = descriptor()
    recipe["automatic_decision"] = policy
    recipe["id"] = fingerprint({k: v for k, v in recipe.items() if k != "id"})
    selected = settings_identity(report["strategies"])
    recorded = findings["decision"]
    recorded.update(
        version=policy["version"],
        policy_id=policy["id"],
        independently_validated=False,
        selected_config_id=selected,
        checks=[
            {"kind": "higher_costs", "passed": False, "summary": {"net_return_pct": 0.5}},
            {
                "kind": "neighboring_settings",
                "passed": False,
                "tested": 4,
                "passing": 0,
                "median_return_edge_pct": -1.0,
            },
        ],
    )
    recorded["id"] = fingerprint({k: v for k, v in recorded.items() if k != "id"})
    findings.update(
        version="automatic-hypotheses-v16",
        independently_validated=False,
        final=None,
        selected_config_id=selected,
    )


def diagnosed_choice(fixture, plan, evidence, candidate):
    from services import research_failure_diagnosis as failure

    saved = current(fixture, plan)
    evidence["experiment_frontier"] = deepcopy(saved["experiment_frontier"])
    evidence["failure_diagnosis_version"] = failure.VERSION
    evidence["failure_diagnosis_context"] = failure.context(evidence)
    allowed = evidence["failure_diagnosis_context"]["options"][candidate["id"]]
    assert allowed["allowed_targets"] and allowed["allowed_causes"]
    return {
        "target_ref": allowed["allowed_targets"][0],
        "cause": allowed["allowed_causes"][0],
        "prediction": "The registered holding boundary may release occupied capacity and improve the recorded failed cost comparison.",
        "falsifier": "If the selected trades or failed cost comparison do not change, this holding mechanism has no demonstrated repair effect.",
    }


def test_affordability_stop_does_not_spend_another_model_call(stopped, monkeypatch):
    fixture, plan = stopped
    resume(fixture, plan)
    change(
        fixture,
        plan,
        lambda state: state.update(original_attempts=483, original_simulations=483),
    )
    enable_worker(fixture, monkeypatch)
    assert automation.run_one(
        fixture.store,
        "adaptive-worker",
        choose=lambda *a: pytest.fail("A model cannot make12 accounts pay for14 checks"),
    )
    result = current(fixture, plan)
    assert result["status"] == result["automatic_phase"] == "stopped"
    assert result["automation"]["calls_spent"] == 1
    assert "12" in result["stopping_reason"] and "14-account" in result["stopping_reason"]
    assert not result["automation"].get("attention")
    assert not fixture.submissions


def test_blanket_model_stop_needs_option_reviews_despite_remaining_registered_options(
    stopped, monkeypatch
):
    fixture, plan = stopped
    resume(fixture, plan)
    enable_worker(fixture, monkeypatch)
    before = current(fixture, plan)
    assert before["experiment_options"] and before["remaining_accounts"] == 95
    decisions = []
    reason = (
        "The cost check failed and remaining exit-delay settings add no supported new mechanism"
    )

    def choose(*args):
        decisions.append(args)
        return {
            "remedy": "stop",
            "candidate_id": None,
            "reason": reason,
            "provider_called": True,
            "selection": "model",
        }

    assert automation.run_one(
        fixture.store,
        "adaptive-worker",
        choose=choose,
    )
    result = current(fixture, plan)
    assert result["automatic_phase"] == result["status"] == "stopped"
    assert result["automation"]["calls_spent"] == 2
    assert result["remaining_accounts"] == before["remaining_accounts"]
    assert result["proposals"] == before["proposals"]
    assert "no model stop was accepted" in result["stopping_reason"]
    assert result["stopping"]["kind"] == "stop_review_incomplete"
    assert result["stopping"]["decision_source"] == "controller"
    assert not result.get("stop_decision")
    assert result["automation"]["decisions"][-1]["remedy"] == "stop"
    assert (
        result["automation"]["decisions"][-1]["stop_review_rejected"]["evidence_id"]
        == result["automation"]["decisions"][-1]["evidence_context"]["id"]
    )
    assert result["automation"].get("attention")
    assert not automation.run_one(fixture.store, "adaptive-worker", choose=choose)
    assert len(decisions) == 1
    assert current(fixture, plan)["automation"]["calls_spent"] == 2
    assert not fixture.submissions

    # Explicitly resuming must not replay the rejected intent or refund it.
    # This legacy stop-only fixture began with an empty contract. An executable
    # next experiment needs a genuine sealed contract over its existing settings.
    from services import research_playbook
    from services.agent import research_decision

    settings = fixture.report["strategies"]
    provide_diagnosis_evidence(fixture)
    original_catalogue = supervisor._experiment_catalog

    def with_axis(*args):
        catalogue = original_catalogue(*args)
        for option in catalogue["options"]:
            option["change"] = {
                "axis": "hold_sessions",
                "to": option["settings"][0]["config"]["hold_sessions"],
            }
        return catalogue

    monkeypatch.setattr(supervisor, "_experiment_catalog", with_axis)
    contract = research_playbook.describe(
        {
            "engine": "vectorbt",
            "strategies": settings,
            "risk_limits": settings[0]["execution_policy"]["risk_limits"],
            "automatic_research": {"intent_policy": research_playbook.INTENT_POLICY},
        }
    )
    change(fixture, plan, lambda saved: saved["research_context"].update(contract=contract))
    resumed = supervisor.replan(
        fixture.store,
        OWNER,
        plan["id"],
        conversation_id=CONVERSATION,
        request_id="explicit-resume-after-incomplete-stop",
        continuation_policy=POLICY,
    )
    assert resumed["automation"]["calls_spent"] == 2
    assert resumed["automation"]["max_model_calls"] == result["automation"]["max_model_calls"]
    selections = []
    execution_errors = []
    execute = research_decision.execute_plan

    def traced_execute(*args, **kwargs):
        try:
            return execute(*args, **kwargs)
        except Exception as error:
            execution_errors.append((type(error).__name__, str(error)))
            raise

    monkeypatch.setattr(research_decision, "execute_plan", traced_execute)

    def choose_next(evidence, _model_id):
        option = evidence["experiment_options"][0]
        selections.append(option["id"])
        return {
            "remedy": option["remedy"],
            "candidate_id": option["id"],
            "reason": "Test the remaining registered holding change with unchanged validation",
            "diagnosis": diagnosed_choice(fixture, plan, evidence, option),
            "selection": "model",
            "provider_called": True,
        }

    assert automation.run_one(fixture.store, "adaptive-worker", choose=choose_next)
    recovered = current(fixture, plan)
    assert len(selections) == len(fixture.submissions) == 1, execution_errors
    assert recovered["automation"]["calls_spent"] == 3
    assert recovered["automation"]["decisions"][-2]["stop_review_rejected"]
    assert recovered["automation"]["decisions"][-1]["candidate_id"] == selections[0]
    assert recovered["proposals"][-1]["decision_diagnosis"]["candidate_id"] == selections[0]
    assert not automation.run_one(fixture.store, "adaptive-worker", choose=choose_next)
    assert len(selections) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"remedy": "stop", "candidate_id": None, "reason": ""},
        {"remedy": "stop", "candidate_id": None, "reason": "   "},
        {"remedy": "stop", "reason": "A candidate identity field is missing"},
        {"remedy": "stop", "candidate_id": "c" * 64, "reason": "A stop cannot submit an option"},
    ],
)
def test_malformed_stop_fails_closed_without_spending_native_budget(stopped, monkeypatch, invalid):
    fixture, plan = stopped
    resume(fixture, plan)
    enable_worker(fixture, monkeypatch)
    before = current(fixture, plan)
    assert automation.run_one(fixture.store, "adaptive-worker", choose=lambda *args: invalid)
    result = current(fixture, plan)
    assert result["status"] == result["automatic_phase"] == "stopped"
    assert result["automation"]["calls_spent"] == 2
    assert result["remaining_accounts"] == before["remaining_accounts"]
    assert result["proposals"] == before["proposals"]
    assert result["automation"].get("attention")
    assert not result.get("stop_decision")
    assert not fixture.submissions
    assert not automation.run_one(
        fixture.store,
        "adaptive-worker",
        choose=lambda *args: pytest.fail("Invalid advice cannot silently retry"),
    )


def test_model_safety_limit_is_reported_separately_from_native_budget(stopped, monkeypatch):
    fixture, plan = stopped
    resume(fixture, plan)
    change(fixture, plan, lambda state: state["automation"].update(calls_spent=24))
    enable_worker(fixture, monkeypatch)
    assert automation.run_one(
        fixture.store,
        "adaptive-worker",
        choose=lambda *a: pytest.fail("An exhausted pinned model cannot be called again"),
    )
    result = current(fixture, plan)
    assert result["status"] == result["automatic_phase"] == "stopped"
    assert result["remaining_accounts"] == 95
    assert result["automation"]["calls_spent"] == 24
    assert "AI call budget exhausted (24/24)" in result["stopping_reason"]
    assert "not a ChatGPT conclusion" in result["stopping_reason"]
    assert result["stopping"]["kind"] == "model_budget_exhausted"
    assert result["stopping"]["decision_source"] == "controller"
    assert result["stopping"]["model_budget_exhausted"] is True
    assert "95" in result["stopping_reason"]
    assert not result["automation"].get("attention")
    assert not fixture.submissions


def test_rejected_native_result_automatically_tests_a_different_registered_change(
    stopped, monkeypatch
):
    from services import research_playbook

    fixture, plan = stopped
    resume(fixture, plan)
    provide_diagnosis_evidence(fixture)
    settings = fixture.report["strategies"]
    contract = research_playbook.describe(
        {
            "engine": "vectorbt",
            "strategies": settings,
            "risk_limits": settings[0]["execution_policy"]["risk_limits"],
            "automatic_research": {"intent_policy": research_playbook.INTENT_POLICY},
        }
    )

    def normalize(state):
        state["research_context"]["contract"] = contract
        for proposal in state["proposals"]:
            proposal.setdefault("job_id", None)

    change(fixture, plan, normalize)

    def catalogue(report, recipe, saved):
        used = {p.get("candidate", {}).get("id") for p in saved["proposals"] if p.get("outcome")}
        options = []
        for holding in (3, 5):
            settings = deepcopy(fixture.report["strategies"])
            settings[0]["config"]["hold_sessions"] = holding
            if holding == 5:
                settings[0]["config"]["target_pct"] = 15
            option = concrete_option(
                saved["source_result_artifact"], "temporal_stability", settings
            )
            if option["id"] in used:
                continue
            options.append(
                {
                    **option,
                    "change": {"axis": "hold_sessions", "to": holding},
                    "expected_effect": "A different observed holding boundary may alter execution",
                    "reason": "The previous cost check failed; this registered boundary is untested",
                }
            )
        return {"options": options, "feedback": {}}

    monkeypatch.setattr(supervisor, "_experiment_catalog", catalogue)
    enable_worker(fixture, monkeypatch)
    choices = []

    def choose(evidence, model_id):
        if choices:
            prior = current(fixture, plan)["proposals"][-1]
            assert prior["outcome"] == "rejected"
            assert prior["reasons"] == ["Cost challenge failed"]
        candidate = evidence["experiment_options"][0]
        choices.append(candidate["id"])
        return {
            "remedy": candidate["remedy"],
            "candidate_id": candidate["id"],
            "reason": candidate["reason"],
            "diagnosis": diagnosed_choice(fixture, plan, evidence, candidate),
            "provider_called": True,
        }

    assert automation.run_one(fixture.store, "adaptive-worker", choose=choose)
    first = current(fixture, plan)
    assert first["status"] == "running" and len(fixture.submissions) == 1
    assert (
        first["proposals"][-1]["decision_diagnosis"]["version"] == "research-failure-diagnosis-v1"
    )
    first_job = first["proposals"][-1]["job_id"]
    change(fixture, plan, lambda state: state["proposals"][-1].update(spent=14, completed=14))
    complete(fixture, plan, first_job)
    # The next worker tick consumes the sealed rejection and chooses the next
    # untested option without a user replan or new grant.
    assert automation.run_one(fixture.store, "adaptive-worker", choose=choose)
    after = current(fixture, plan)
    assert after["proposals"][-2]["outcome"] == "rejected"
    assert after["proposals"][-2]["reasons"] == ["Cost challenge failed"]
    assert len(choices) == len(set(choices)) == 2
    assert choices == [p["candidate"]["id"] for p in after["proposals"][-2:]]
    assert after["status"] == "running" and len(fixture.submissions) == 2
    assert after["automation"]["calls_spent"] == 3
    assert after["original_attempts"] == 400
    assert after["remaining_accounts"] == 95 - 14 - 14
    assert len(after["replans"]) == 1
    assert after["proposals"][-2]["settings"][0]["config"]["hold_sessions"] == 3
    assert after["proposals"][-1]["settings"][0]["config"]["hold_sessions"] == 5
    assert all(
        p["decision_diagnosis"]["candidate_id"] == p["candidate"]["id"]
        for p in after["proposals"][-2:]
    )


@pytest.fixture
def checked_branches(stopped, monkeypatch):
    from services import research_playbook

    fixture, plan = stopped
    resume(fixture, plan)
    first_job, last_job = "1" * 32, "2" * 32

    def prepare(state):
        state["research_context"]["contract"]["id"] = "fixed-test-contract"
        for proposal, job_id, holding, spent in zip(
            state["proposals"], [first_job, last_job], [7, 3], [16, 14], strict=True
        ):
            proposal["settings"][0]["config"]["hold_sessions"] = holding
            proposal.update(job_id=job_id, spent=spent, completed=spent)

    change(fixture, plan, prepare)
    for job_id in [first_job, last_job]:
        fixture.jobs[job_id] = SimpleNamespace(
            id=job_id, owner=OWNER, status="queued", result_artifact=None
        )
        complete(fixture, plan, job_id)

    def stopped_latest(state):
        state.update(
            status="stopped",
            latest_seed_job_id=last_job,
            latest_source_result_artifact=fixture.jobs[last_job].result_artifact,
            stopping_reason="The latest development branch has no remaining options",
        )

    change(fixture, plan, stopped_latest)
    with fixture.store.sessions.begin() as db:
        db.get(ResearchDecisionJob, plan["id"]).phase = "stopped"
    monkeypatch.setattr(research_playbook, "validate_settings", lambda *a: None)
    monkeypatch.setattr(
        supervisor,
        "_reviewed_diagnosis",
        lambda *a, **k: {"missing_evidence": False, "permitted_remedies": ["temporal_stability"]},
    )

    def catalogue(report, recipe, saved):
        options = []
        if report["strategies"][0]["config"]["hold_sessions"] == 7:
            settings = deepcopy(report["strategies"])
            settings[0]["config"]["hold_sessions"] = 5
            settings[0]["config"]["target_pct"] = 15
            options.append(
                {
                    **concrete_option(
                        saved["source_result_artifact"], "temporal_stability", settings
                    ),
                    "change": {"axis": "hold_sessions", "from": 7, "to": 5},
                    "expected_effect": "A separate registered holding boundary may change execution",
                    "reason": "An untested sibling remains after the most recent branch exhausted",
                }
            )
        return {"options": options, "feedback": {}, "unavailable": []}

    monkeypatch.setattr(supervisor, "_experiment_catalog", catalogue)
    return fixture, plan, first_job, last_job


def test_frontier_exposes_earlier_checked_options_without_automatically_selecting_a_seed(
    checked_branches,
):
    fixture, plan, first_job, last_job = checked_branches
    before = current(fixture, plan)
    assert before["seed_job_id"] == last_job
    assert {option["origin_job_id"] for option in before["experiment_options"]} == {first_job}
    result = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert result["seed_job_id"] == last_job
    assert result["source_result_artifact"] == before["source_result_artifact"]
    assert result["status"] == result["automatic_phase"] == "stopped"
    assert (
        result["experiment_options"][0]["origin_result_artifact"]
        == fixture.jobs[first_job].result_artifact
    )
    assert result["remaining_accounts"] == before["remaining_accounts"] == 82
    assert result["original_attempts"] == before["original_attempts"] == 400
    assert result["proposals"] == before["proposals"]
    assert result["automation"] == before["automation"]
    assert not result.get("frontier_transitions")
    assert not fixture.submissions


def test_frontier_cannot_override_a_saved_explicit_model_stop(checked_branches):
    fixture, plan, first_job, last_job = checked_branches
    before = current(fixture, plan)
    reason = "Previous cost and sample failures provide no justified new experiment"

    def explicit_stop(state):
        state["stop_decision"] = {
            "selection": "model",
            "reason": reason,
            "seed_artifact": before["source_result_artifact"],
        }
        state["stopping_reason"] = reason

    change(fixture, plan, explicit_stop)
    saved = current(fixture, plan)
    result = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert result["status"] == result["automatic_phase"] == "stopped"
    assert result["seed_job_id"] == last_job
    assert result["source_result_artifact"] == saved["source_result_artifact"]
    assert result["stop_decision"] == saved["stop_decision"]
    assert result["stopping_reason"] == reason
    assert result["remaining_accounts"] == saved["remaining_accounts"] == 82
    assert result["proposals"] == saved["proposals"]
    assert result["automation"] == saved["automation"]
    assert not result.get("frontier_transitions")
    assert not fixture.submissions


def test_frontier_does_not_reuse_a_no_effect_probe_as_a_checked_seed(checked_branches):
    fixture, plan, first_job, last_job = checked_branches
    change(
        fixture,
        plan,
        lambda state: state["proposals"][0].update(no_effect={"status": "unchanged"}),
    )
    before = current(fixture, plan)
    result = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert result["seed_job_id"] == last_job
    assert not result.get("frontier_transitions")
    assert result["proposals"] == before["proposals"]
    assert result["remaining_accounts"] == before["remaining_accounts"]
    assert result["experiment_options"] == []
    assert not fixture.submissions


def test_frontier_cannot_use_later_period_outcomes_to_select_a_sibling(checked_branches):
    fixture, plan, first_job, last_job = checked_branches
    change(fixture, plan, lambda state: state.update(validation_mode="chronological"))
    before = current(fixture, plan)
    result = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert result["seed_job_id"] == last_job
    assert not result.get("frontier_transitions")
    assert result["proposals"] == before["proposals"]
    assert result["remaining_accounts"] == before["remaining_accounts"]
    assert not fixture.submissions


def test_frontier_requires_full_validation_reservation_even_on_earlier_branches(checked_branches):
    fixture, plan, first_job, last_job = checked_branches
    change(
        fixture,
        plan,
        lambda state: state.update(original_attempts=469, original_simulations=469),
    )
    before = current(fixture, plan)
    assert before["remaining_accounts"] == 13
    result = supervisor.resume_frontier(
        fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION
    )
    assert result["seed_job_id"] == last_job
    assert not result.get("frontier_transitions")
    assert result["remaining_accounts"] == before["remaining_accounts"]
    assert result["automation"] == before["automation"]
    assert "13" in result["continuation_stop_reason"]
    assert "14" in result["continuation_stop_reason"]
    assert not fixture.submissions
