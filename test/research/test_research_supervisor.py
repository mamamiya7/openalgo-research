"""Small saved-metadata tests for durable research authority and worker continuation."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from database.research_db import ResearchStore, ResearchSupervisor
from research.engine import config_defaults
from research.execution_policy import normalize_policy
from research.report_contract import fingerprint, settings_identity
from services import research_mcp
from services import research_supervisor as supervisor

OWNER = "research-owner"
CONVERSATION = "research-conversation"
ROOT = "a" * 32
ARTIFACT = "a" * 64


def fixture_report(*, mode="exploratory_only", attempts=400):
    settings = [
        {
            "id": "one",
            "name": "Saved setup",
            "allocation_pct": 100,
            "config": config_defaults(),
            "execution_policy": normalize_policy(
                {
                    "version": "research-execution-aware-v5",
                    "allocation_mode": "original",
                    "capacity_mode": "prior20_broker_reported",
                    "correlation_mode": "prior60_positive_080",
                }
            ),
        }
    ]
    recipe = {
        "version": "automatic-hypotheses-v16",
        "validation_mode": mode,
        "baseline": deepcopy(settings),
        "ranges": [
            {
                "id": "one",
                "search": {
                    "target_pct": {"min": 5, "max": 15, "step": 5},
                    "stop_pct": {"min": 3, "max": 7, "step": 2},
                    "hold_sessions": {"min": 3, "max": 7, "step": 2},
                },
            }
        ],
    }
    return {
        "strategies": settings,
        "summary": {"initial_capital": 100000},
        "automatic_research": {
            "recipe": recipe,
            "counts": {"attempts_reserved": attempts, "simulations": attempts},
            "capital_planning": {
                "diagnosis": {"categories": {"cash_shortfall": 4, "position_limit": 2}}
            },
            "decision": {
                "checks": [{"kind": "higher_costs", "passed": False}],
                "prefix_diagnostics": [{"period": "fit1", "passed": False}],
            },
            "automatic_design": {"selected": {"largest_symbol_positive_profit_fraction": 0.7}},
        },
    }


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "research")
    store.initialize()
    report = fixture_report()
    parent = SimpleNamespace(id=ROOT, result_artifact=ARTIFACT, owner=OWNER, status="completed")

    def read_parent(_store, owner, identifier):
        if owner != OWNER or identifier != ROOT:
            raise LookupError("Saved study not found")
        value = deepcopy(report)
        return (
            parent,
            {"inputs_artifact": "e" * 64, "result": value},
            value,
            value["automatic_research"]["recipe"],
        )

    monkeypatch.setattr(supervisor, "_parent", read_parent)
    jobs, artifacts, submissions = {}, {}, []
    lock = Lock()
    from services import research_portfolio

    evidence = {"portfolio": {"capital": 100000}, "versions": {}}
    monkeypatch.setattr(
        research_portfolio, "replay_inputs", lambda *a, **k: (parent, deepcopy(evidence))
    )
    monkeypatch.setattr(
        supervisor.service, "register_source", lambda *a, **k: (None, {"id": "b" * 32})
    )

    def submit(_store, owner, source_id, config, *, request_id, kind, specification):
        with lock:
            existing = next((j for j in jobs.values() if j.request_id == request_id), None)
            if existing:
                return {"id": existing.id, "status": existing.status}
            identifier = f"{len(jobs) + 1:032x}"
            jobs[identifier] = SimpleNamespace(
                id=identifier,
                owner=owner,
                status="queued",
                result_artifact=None,
                request_id=request_id,
            )
            submissions.append(identifier)
            return {"id": identifier, "status": "queued"}

    def get_job(_store, owner, identifier):
        if identifier == ROOT and owner == OWNER:
            return parent
        if identifier not in jobs or jobs[identifier].owner != owner:
            raise LookupError("Saved job not found")
        return jobs[identifier]

    monkeypatch.setattr(supervisor.service, "submit", submit)
    monkeypatch.setattr(supervisor.service, "get_job", get_job)
    monkeypatch.setattr(
        supervisor.service, "job_receipt", lambda s, j: {"id": j.id, "status": j.status}
    )
    monkeypatch.setattr(
        supervisor.service, "read_artifact", lambda s, digest: deepcopy(artifacts[digest])
    )
    try:
        yield SimpleNamespace(
            store=store,
            report=report,
            parent=parent,
            jobs=jobs,
            artifacts=artifacts,
            submissions=submissions,
        )
    finally:
        store.close()


def open_plan(fixture, **kwargs):
    return supervisor.open_supervisor(
        fixture.store,
        OWNER,
        ROOT,
        conversation_id=CONVERSATION,
        request_id="open-research-plan",
        **kwargs,
    )


def propose(fixture, plan, remedy="funding_and_size"):
    return supervisor.propose(
        fixture.store,
        OWNER,
        plan["id"],
        remedy,
        "Saved native evidence supports this bounded test",
        conversation_id=CONVERSATION,
    )


def advance(fixture, plan):
    return supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)


def mutate(fixture, plan, callback):
    import json

    with fixture.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, plan["id"])
        state = json.loads(row.state)
        callback(state)
        supervisor._put(row, state)


def complete(fixture, plan, job_id, *, accepted=False):
    current = supervisor.status(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    item = next(p for p in current["proposals"] if p["job_id"] == job_id)
    native = {
        "strategies": deepcopy(item["settings"]),
        "summary": {"initial_capital": 100000},
        "portfolio_constraint_evidence": {"status": "supported"},
    }
    outcome = "validated" if accepted else "rejected"
    reasons = [] if accepted else ["Cost challenge failed"]
    operating = {
        "version": "research-operating-validation-v1",
        "outcome": outcome,
        "historically_validated": accepted,
        "independently_validated": False,
        "selected_config_id": settings_identity(native["strategies"]),
        "capital": 100000,
        "selected_report_id": fingerprint(native),
        "reasons": reasons,
        "diagnostics": [{"period": f"fit{i}", "passed": accepted} for i in range(1, 4)],
    }
    operating["id"] = fingerprint(operating)
    conclusion = {
        "version": "research-supervisor-validation-v1",
        "outcome": outcome,
        "historically_validated": accepted,
        "independently_validated": False,
        "selected_config_id": operating["selected_config_id"],
        "capital": 100000,
        "native_report_id": fingerprint(native),
        "operating_validation": operating,
        "supervisor_id": plan["id"],
        "proposal_id": item["id"],
        "source_result_artifact": ARTIFACT,
        "scope": "Historical research only",
        "reasons": reasons,
    }
    conclusion["id"] = fingerprint(conclusion)
    digest = fingerprint({"job_id": job_id, "conclusion": conclusion})
    fixture.artifacts[digest] = {"result": {**native, "supervisor_validation": conclusion}}
    fixture.jobs[job_id].status = "completed"
    fixture.jobs[job_id].result_artifact = digest
    return conclusion


def test_authority_is_owner_and_conversation_bound_and_parent_cannot_reset_budget(ledger):
    plan = open_plan(ledger)
    assert open_plan(ledger)["id"] == plan["id"]
    assert (
        supervisor.open_supervisor(
            ledger.store, OWNER, ROOT, conversation_id=CONVERSATION, request_id="another-request"
        )["id"]
        == plan["id"]
    )
    for owner, conversation in (("other-owner", CONVERSATION), (OWNER, "another-chat")):
        with pytest.raises(LookupError):
            supervisor.status(ledger.store, owner, plan["id"], conversation_id=conversation)
    with pytest.raises(LookupError):
        supervisor.open_supervisor(
            ledger.store, OWNER, ROOT, conversation_id="another-chat", request_id="another-request"
        )
    with ledger.store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchSupervisor)) == 1


def test_open_rejects_changed_objective_limits_and_request_collision(ledger):
    open_plan(ledger)
    with pytest.raises(ValueError):
        open_plan(ledger, objective="maximum_profit")
    with pytest.raises(ValueError):
        open_plan(ledger, max_experiments=2)
    ledger.parent.result_artifact = "c" * 64
    with pytest.raises(ValueError, match="different frozen"):
        open_plan(ledger)


def test_reservation_preserves_exact_512_ceiling_and_repeated_proposals_are_free(ledger):
    ledger.report["automatic_research"]["counts"].update(attempts_reserved=495, simulations=495)
    plan = open_plan(ledger)
    first = propose(ledger, plan)
    assert first["proposal"]["reserved_accounts"] == 17
    assert propose(ledger, plan)["reused"] is True
    current = supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert current["remaining_accounts"] == 0
    with pytest.raises(ValueError, match="512"):
        propose(ledger, plan, "reduce_concentration")
    assert len(ledger.submissions) == 0


def test_failed_experiment_releases_only_unspent_reservations(ledger):
    plan = open_plan(ledger)
    propose(ledger, plan)
    running = advance(ledger, plan)
    job_id = running["proposals"][0]["job_id"]
    mutate(ledger, plan, lambda state: state["proposals"][0].update(spent=14))
    complete(ledger, plan, job_id)
    finished = advance(ledger, plan)
    assert finished["remaining_accounts"] == 512 - 400 - 14
    assert finished["status"] == "awaiting_plan"


@pytest.mark.parametrize(
    "missing_kind",
    [
        "unavailable_sizing_intent",
        "unexplained_execution_limit",
        "evidence_gap_requests",
        "unavailable_evidence",
    ],
)
def test_missing_evidence_and_only_fixed_limits_never_trigger_more_capital(ledger, missing_kind):
    categories = ledger.report["automatic_research"]["capital_planning"]["diagnosis"]["categories"]
    categories[missing_kind] = 1
    plan = open_plan(ledger)
    assert plan["diagnosis"]["missing_evidence"] is True
    with pytest.raises(ValueError, match="does not support"):
        propose(ledger, plan)
    clean = fixture_report()
    clean["automatic_research"]["capital_planning"]["diagnosis"]["categories"] = {
        "position_limit": 12,
        "liquidity_limit": 3,
    }
    clean["automatic_research"]["decision"] = {"checks": [], "prefix_diagnostics": []}
    clean["automatic_research"].pop("automatic_design")
    diagnosis = supervisor._diagnosis(clean)
    assert diagnosis["permitted_remedies"] == []
    assert diagnosis["fixed_limits"] == {"position_limit": 12, "liquidity_limit": 3}


def test_diagnosis_does_not_mutate_saved_financial_evidence():
    report = fixture_report()
    report["automatic_research"]["operating_validation"] = {
        "diagnostics": [{"period": "fit2", "passed": False}]
    }
    before = deepcopy(report)
    supervisor._diagnosis(report)
    assert report == before


@pytest.mark.parametrize("wrapped", [False, True])
def test_native_capital_diagnosis_envelope_keeps_actionable_and_fixed_blockers(wrapped):
    report = fixture_report()
    counts = {"cash_shortfall": 1, "percentage_policy": 6, "position_limit": 1}
    report["automatic_research"]["decision"] = {
        "capital_diagnosis": {"action": "bounded_actions_complete", "categories": counts}
        if wrapped
        else counts
    }
    assert "funding_and_size" in supervisor._diagnosis(report)["permitted_remedies"]
    assert supervisor._diagnosis(report)["fixed_limits"]["position_limit"] == 1
    counts["evidence_gap_requests"] = 1
    assert supervisor._diagnosis(report)["missing_evidence"]
    assert supervisor._diagnosis(report)["permitted_remedies"] == []


@pytest.mark.parametrize("mode", ["chronological", "historical_only"])
def test_exposed_later_results_cannot_select_performance_remedies(ledger, mode):
    ledger.report["automatic_research"]["recipe"]["validation_mode"] = mode
    plan = open_plan(ledger)
    for remedy in ("lower_turnover", "reduce_concentration", "temporal_stability"):
        with pytest.raises(ValueError, match="Later results"):
            propose(ledger, plan, remedy)
    assert propose(ledger, plan)["proposal"]["reserved_accounts"] == 24


def test_declared_holding_remedy_uses_available_grid_without_changing_hard_limits(ledger):
    report = ledger.report
    recipe = report["automatic_research"]["recipe"]
    changed = supervisor._settings_for(report, recipe, "lower_turnover")
    assert changed[0]["config"]["hold_sessions"] == 7
    original = deepcopy(report["strategies"])
    original[0]["config"]["hold_sessions"] = 7
    assert changed == original


@pytest.mark.parametrize("axis", ["hold_sessions", "hold_minutes"])
@pytest.mark.parametrize(
    ("value", "remedy", "expected"),
    [
        (46, "lower_turnover", 47),
        (46, "temporal_stability", 35),
        (47, "lower_turnover", 59),
        (47, "temporal_stability", 35),
        (23, "lower_turnover", 35),
        (83, "temporal_stability", 71),
        (10, "lower_turnover", 23),
        (90, "temporal_stability", 83),
    ],
)
def test_holding_remedy_uses_nearest_strictly_directional_frozen_point(
    axis, value, remedy, expected
):
    report = fixture_report()
    recipe = report["automatic_research"]["recipe"]
    report["strategies"][0]["config"][axis] = value
    recipe["ranges"][0]["search"] = {axis: {"min": 23, "max": 83, "step": 12}}
    original = deepcopy(report)
    changed = supervisor._settings_for(report, recipe, remedy)
    assert changed[0]["config"][axis] == expected
    assert 23 <= expected <= 83 and (expected - 23) % 12 == 0
    assert (expected > value) if remedy == "lower_turnover" else (expected < value)
    expected_settings = deepcopy(report["strategies"])
    expected_settings[0]["config"][axis] = expected
    assert changed == expected_settings
    assert report == original


@pytest.mark.parametrize(
    ("value", "remedy", "domain"),
    [
        (83, "lower_turnover", {"min": 23, "max": 83, "step": 12}),
        (23, "temporal_stability", {"min": 23, "max": 83, "step": 12}),
        (90, "lower_turnover", {"min": 23, "max": 83, "step": 12}),
        (10, "temporal_stability", {"min": 23, "max": 83, "step": 12}),
        (46, "lower_turnover", {"min": 23, "max": 83, "step": 0}),
        (46, "lower_turnover", {"min": 83, "max": 23, "step": 12}),
        (46, "lower_turnover", {"min": 23, "max": 83, "step": float("nan")}),
        (46, "lower_turnover", {"min": 23.5, "max": 83.5, "step": 12}),
    ],
)
def test_holding_remedy_does_not_invent_a_point_outside_frozen_domain(value, remedy, domain):
    report = fixture_report()
    recipe = report["automatic_research"]["recipe"]
    report["strategies"][0]["config"]["hold_sessions"] = value
    recipe["ranges"][0]["search"]["hold_sessions"] = domain
    with pytest.raises(ValueError, match="No registered holding-period remedy"):
        supervisor._settings_for(report, recipe, remedy)


def test_concurrent_identical_proposals_and_advances_share_one_job(ledger):
    plan = open_plan(ledger)
    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(lambda _: propose(ledger, plan), range(2)))
    assert {v["proposal"]["id"] for v in values} == {values[0]["proposal"]["id"]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(lambda _: advance(ledger, plan), range(2)))
    assert len(ledger.submissions) == 1
    assert len({v["proposals"][0]["job_id"] for v in values}) == 1


def test_worker_completion_queues_next_predeclared_experiment(ledger):
    plan = open_plan(ledger)
    propose(ledger, plan)
    propose(ledger, plan, "reduce_concentration")
    running = advance(ledger, plan)
    job_id = running["proposals"][0]["job_id"]
    complete(ledger, plan, job_id)
    supervisor.job_finished(ledger.store, OWNER, job_id)
    current = supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert current["proposals"][0]["outcome"] == "rejected"
    assert current["proposals"][1]["job_id"]
    assert len(ledger.submissions) == 2


def test_accepted_completion_saves_once_and_does_not_queue_later_candidates(ledger, monkeypatch):
    plan = open_plan(ledger)
    propose(ledger, plan)
    propose(ledger, plan, "reduce_concentration")
    running = advance(ledger, plan)
    job_id = running["proposals"][0]["job_id"]
    complete(ledger, plan, job_id, accepted=True)
    calls = []
    monkeypatch.setattr(
        research_mcp,
        "save_accepted_result",
        lambda *args, **kwargs: (
            calls.append(args) or {"experiment_id": "e" * 32, "choice_id": "f" * 32}
        ),
    )
    supervisor.job_finished(ledger.store, OWNER, job_id)
    supervisor.job_finished(ledger.store, OWNER, job_id)
    current = supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert current["status"] == "saved"
    assert current["accepted_job_id"] == job_id
    assert len(calls) == 1
    assert len(ledger.submissions) == 1


def test_forged_completed_outcome_never_becomes_accepted(ledger):
    plan = open_plan(ledger)
    propose(ledger, plan)
    running = advance(ledger, plan)
    job_id = running["proposals"][0]["job_id"]
    complete(ledger, plan, job_id, accepted=True)
    digest = ledger.jobs[job_id].result_artifact
    ledger.artifacts[digest]["result"]["supervisor_validation"]["supervisor_id"] = "another-plan"
    with pytest.raises(ValueError):
        advance(ledger, plan)
    current = supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert current["accepted_job_id"] is None


def test_stale_completion_cannot_regress_saved_terminal_state(ledger, monkeypatch):
    plan = open_plan(ledger)
    propose(ledger, plan)
    running = advance(ledger, plan)
    job_id = running["proposals"][0]["job_id"]
    complete(ledger, plan, job_id, accepted=True)
    original = supervisor.service.read_artifact

    def finish_during_read(store, digest):
        value = original(store, digest)

        def publish(state):
            state["proposals"][0]["outcome"] = "validated"
            state["accepted_job_id"] = job_id
            state["saved_result"] = {"experiment_id": "e" * 32, "choice_id": "f" * 32}
            state["status"] = "saved"

        mutate(ledger, plan, publish)
        return value

    monkeypatch.setattr(supervisor.service, "read_artifact", finish_during_read)
    current = advance(ledger, plan)
    assert current["status"] == "saved"
    assert current["saved_result"]["choice_id"] == "f" * 32


@pytest.mark.parametrize("simulations", [None, True, -1, 401, "400"])
def test_missing_or_invalid_original_simulation_count_cannot_reset_lost_allowance(
    ledger, simulations
):
    ledger.report["automatic_research"]["counts"]["simulations"] = simulations
    with pytest.raises(ValueError):
        open_plan(ledger)


def test_original_lost_ceiling_survives_reservation_and_durable_recovery(ledger):
    ledger.report["automatic_research"]["counts"].update(attempts_reserved=400, simulations=337)
    plan = open_plan(ledger)
    item = propose(ledger, plan)["proposal"]
    supervisor.reserve_account(ledger.store, OWNER, plan["id"], item["id"])
    with pytest.raises(ValueError, match="64 interrupted"):
        supervisor.reserve_account(ledger.store, OWNER, plan["id"], item["id"])
    mutate(ledger, plan, lambda state: state["proposals"][0].update(completed=1))
    supervisor.reserve_account(ledger.store, OWNER, plan["id"], item["id"])
    with pytest.raises(ValueError, match="64 interrupted"):
        supervisor.reserve_account(ledger.store, OWNER, plan["id"], item["id"])
    current = supervisor.status(ledger.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert current["lost_accounts"] == 64
    assert current["proposals"][0]["spent"] == 2
    assert open_plan(ledger)["lost_accounts"] == 64


def test_new_verified_cost_failure_changes_next_experiment_and_binds_seed(ledger):
    plan = open_plan(ledger)
    initial = propose(ledger, plan)
    running = advance(ledger, plan)
    job_id = running["proposals"][0]["job_id"]
    complete(ledger, plan, job_id)
    digest = ledger.jobs[job_id].result_artifact
    bundle = ledger.artifacts[digest]
    native = bundle["result"]
    validation = native.pop("supervisor_validation")
    original_limits = deepcopy(native["strategies"][0]["execution_policy"]["risk_limits"])
    native["summary"]["initial_capital"] = 200000.0
    native["strategies"][0]["config"]["initial_capital"] = 200000.0
    operating = validation["operating_validation"]
    operating["diagnostics"] = [{"period": f"fit{i}", "passed": True} for i in range(1, 4)]
    operating["decision"] = {
        "checks": [{"kind": "higher_costs", "passed": False}],
        "capital_diagnosis": {},
    }
    operating.update(
        capital=200000.0,
        selected_config_id=settings_identity(native["strategies"]),
        selected_report_id=fingerprint(native),
    )
    operating["id"] = fingerprint({k: v for k, v in operating.items() if k != "id"})
    validation.update(
        capital=200000.0,
        selected_config_id=operating["selected_config_id"],
        native_report_id=fingerprint(native),
    )
    validation["id"] = fingerprint({k: v for k, v in validation.items() if k != "id"})
    native["supervisor_validation"] = validation
    new_digest = fingerprint(bundle)
    ledger.artifacts[new_digest] = deepcopy(bundle)
    ledger.jobs[job_id].result_artifact = new_digest
    current = advance(ledger, plan)
    assert current["status"] == "awaiting_plan"
    assert current["diagnosis"]["permitted_remedies"] == ["lower_turnover"]
    candidate = propose(ledger, plan, "lower_turnover")["proposal"]
    assert candidate["seed_job_id"] == job_id
    assert candidate["source_result_artifact"] == new_digest
    assert candidate["settings"][0]["config"]["initial_capital"] == 200000.0
    assert candidate["settings"][0]["config"]["hold_sessions"] == 7
    assert candidate["settings"][0]["execution_policy"]["risk_limits"] == original_limits
    assert (
        ledger.report["automatic_research"]["recipe"]["baseline"][0]["config"]["initial_capital"]
        == 100000
    )
    assert candidate["id"] != initial["proposal"]["id"]
