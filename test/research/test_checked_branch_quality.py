"""Checked branch quality changes planning, never acceptance or account debits."""

# ruff: noqa: F401,F811 -- shared isolated ledger/native programme fixtures
from copy import deepcopy
from types import SimpleNamespace

from test_adaptive_continuation import change, checked_branches, current
from test_feedback_replan import stopped
from test_research_supervisor import CONVERSATION, OWNER, complete, ledger
from test_validation_scorecard import native, reject

from research.report_contract import fingerprint, settings_identity
from services import research_automation as automation
from services import research_supervisor as supervisor


def measured(fixture, job_id, template):
    """Attach real-format development measurements to an isolated bound job."""
    job = fixture.jobs[job_id]
    result = fixture.artifacts[job.result_artifact]["result"]
    for key in ("summary", "automatic_design", "portfolio_constraint_evidence"):
        result[key] = deepcopy(template[key])
    conclusion = result["supervisor_validation"]
    plain = {k: v for k, v in result.items() if k != "supervisor_validation"}
    conclusion.update(
        native_report_id=fingerprint(plain),
        selected_config_id=settings_identity(result["strategies"]),
        capital=result["summary"]["initial_capital"],
        reasons=deepcopy(template["supervisor_validation"]["reasons"]),
    )
    operating = deepcopy(template["supervisor_validation"]["operating_validation"])
    operating.update(
        selected_report_id=conclusion["native_report_id"],
        selected_config_id=conclusion["selected_config_id"],
        capital=conclusion["capital"],
    )
    operating["id"] = fingerprint({k: v for k, v in operating.items() if k != "id"})
    conclusion["operating_validation"] = operating
    conclusion["id"] = fingerprint({k: v for k, v in conclusion.items() if k != "id"})
    return result


def third(fixture, plan, *, pending=False):
    job_id = "3" * 32

    def append(state):
        proposal = deepcopy(state["proposals"][-1])
        proposal.update(id="3" * 64, job_id=job_id, spent=14, completed=14,
                        outcome=None if pending else "rejected")
        proposal["settings"][0]["config"]["hold_sessions"] = 9
        state["proposals"].append(proposal)
        state["experiment_policy"] = supervisor.EXPERIMENT_POLICY

    change(fixture, plan, append)
    fixture.jobs[job_id] = SimpleNamespace(id=job_id, owner=OWNER, status="queued", result_artifact=None)
    complete(fixture, plan, job_id)
    return job_id


def test_frontier_prefers_older_strict_gate_improvement_without_extra_accounts(checked_branches, native):
    fixture, plan, first, last = checked_branches
    recent = third(fixture, plan)
    measured(fixture, first, reject(native))
    measured(fixture, recent, reject(native, cost=True, prefix=True, design=True))
    # Both branches have an affordable untested setting; the recent one is worse.
    fixture.artifacts[fixture.jobs[recent].result_artifact]["result"]["strategies"][0]["config"]["hold_sessions"] = 7
    # Rebind after changing the fixture's declared complete configuration.
    measured(fixture, recent, reject(native, cost=True, prefix=True, design=True))
    before = current(fixture, plan)
    after = supervisor.resume_frontier(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert after["seed_job_id"] == first
    assert after["remaining_accounts"] == before["remaining_accounts"]
    assert after["proposals"] == before["proposals"]
    assert after["frontier_transitions"][-1]["validation_scorecard"]["eligible"]


def test_worse_new_result_does_not_displace_stronger_checked_seed(checked_branches, native):
    fixture, plan, first, last = checked_branches
    measured(fixture, first, reject(native))
    incoming = third(fixture, plan, pending=True)
    measured(fixture, incoming, reject(native, cost=True, prefix=True, design=True))

    def running(state):
        state.update(status="running", latest_seed_job_id=first,
                     latest_source_result_artifact=fixture.jobs[first].result_artifact)

    change(fixture, plan, running)
    before = current(fixture, plan)
    after = supervisor.advance(fixture.store, OWNER, plan["id"], conversation_id=CONVERSATION)
    assert after["seed_job_id"] == first
    assert after["proposals"][-1]["outcome"] == "rejected"
    assert after["proposals"][-1]["planning_branch"]["retained_job_id"] == first
    assert after["remaining_accounts"] == before["remaining_accounts"]
    assert not after.get("accepted_job_id")


def test_model_receives_recorded_neighbor_counts_not_just_rejected_label(checked_branches, native):
    fixture, plan, first, last = checked_branches
    measured(fixture, first, reject(native))
    change(fixture, plan, lambda state: state.update(experiment_policy=supervisor.EXPERIMENT_POLICY))
    value = automation._evidence(fixture.store, OWNER, current(fixture, plan))
    entry = value["configuration_context"]["previous"][0]
    card = entry["validation_scorecard"]
    assert card["eligible"] and card["independently_validated"] is False
    neighbors = next(row for row in card["checks"] if row["key"] == "neighboring_settings")
    assert neighbors["facts"]["passing"] == 2 and neighbors["facts"]["tested"] == 4
    assert neighbors["thresholds"]["minimum_neighbor_pass_fraction"] == .75
