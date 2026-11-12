"""Settings presentation uses existing native bindings; never runs calculations."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from research.report_contract import fingerprint, settings_identity
from services import research_journey as journey


@pytest.fixture
def evidence(monkeypatch):
    strategies = [{"id": "s", "name": "Saved stocks", "allocation_pct": 100,
                   "config": {"order_size_pct": 3, "hold_sessions": 20,
                              "trailing_enabled": True, "trailing_pct": 6.25},
                   "execution_policy": {"exit_mode": "percent"}}]
    original = {"summary": {"initial_capital": 100000}, "strategies": strategies,
                "automatic_research": {"recipe": {"version": "automatic-hypotheses-v16"}}}
    root = {"job_id": "root", "result": original}
    root_id = fingerprint(root)
    report = {"summary": {"initial_capital": 517000}, "strategies": deepcopy(strategies)}
    report["strategies"][0]["config"]["order_size_pct"] = 1.5
    native_id, settings_id = fingerprint(report), settings_identity(report["strategies"])
    operating = {"selected_report_id": native_id, "selected_config_id": settings_id,
                 "capital": 517000, "outcome": "rejected", "historically_validated": False,
                 "reasons": ["Nearby settings did not pass"]}
    operating["id"] = fingerprint(operating)
    validation = {"version": "research-supervisor-validation-v1", "supervisor_id": "sup",
                  "proposal_id": "p", "source_result_artifact": root_id,
                  "native_report_id": native_id, "selected_config_id": settings_id,
                  "capital": 517000, "independently_validated": False,
                  "outcome": "rejected", "historically_validated": False,
                  "operating_validation": operating, "reasons": ["Nearby settings did not pass"]}
    validation["id"] = fingerprint(validation)
    report["supervisor_validation"] = validation
    bundle = {"job_id": "child", "result": report}
    result_id = fingerprint(bundle)
    jobs = {"root": SimpleNamespace(id="root", status="completed", result_artifact=root_id),
            "child": SimpleNamespace(id="child", status="completed", result_artifact=result_id)}
    artifacts = {root_id: root, result_id: bundle}
    monkeypatch.setattr(journey.service, "get_job", lambda store, owner, key: jobs[key])
    monkeypatch.setattr(journey.service, "read_artifact", lambda store, key: artifacts[key])
    current = {"id": "sup", "root_job_id": "root", "proposals": []}
    proposal = {"id": "p", "job_id": "child", "seed_job_id": "root",
                "source_result_artifact": root_id, "outcome": "rejected",
                "reasons": ["Nearby settings did not pass"],
                "experiment": {"change": {"axis": "native_funding_and_size", "current_capital": 100000}}}
    return current, proposal, jobs, report, artifacts, bundle


def display(evidence):
    current, proposal, *_ = evidence
    return journey._decision_display(None, "owner", current,
                                     {"remedy": "funding_and_size", "reason": "Repair affordability", "selection": "native"},
                                     proposal)


def test_native_funding_settings_show_actual_capital_and_size_not_declared_intent(evidence):
    shown = display(evidence)
    assert shown["selection"] == "native" and shown["outcome"] == "rejected"
    assert shown["settings_change"] == {"status": "verified", "rows": [
        {"strategy_id": None, "strategy": "Account", "field": "capital", "before": 100000, "after": 517000},
        {"strategy_id": "s", "strategy": "Saved stocks", "field": "order_size_pct", "before": 3, "after": 1.5}]}
    assert shown["reasons"] == ["Nearby settings did not pass"]


def test_pending_attempt_stays_proposed_without_result_reads(evidence, monkeypatch):
    evidence[2]["child"].status = "running"
    monkeypatch.setattr(journey.service, "read_artifact", lambda *a: pytest.fail("No pending-result read"))
    assert display(evidence)["settings_change"] == {"status": "proposed", "rows": []}


def test_modified_native_settings_fail_existing_binding_and_never_show_as_actual(evidence):
    evidence[3]["summary"]["initial_capital"] = 1
    assert display(evidence)["settings_change"] == {"status": "unavailable", "rows": []}


def test_completed_blocked_attempt_does_not_display_financial_settings_as_verified(evidence):
    value = evidence[3]["supervisor_validation"]
    value["outcome"] = "blocked"
    value["id"] = fingerprint({k: v for k, v in value.items() if k != "id"})
    assert display(evidence)["settings_change"] == {"status": "blocked", "rows": []}


def test_cross_job_bundle_cannot_be_used_as_verified_settings(evidence):
    evidence[5]["job_id"] = "another-job"
    assert display(evidence)["settings_change"] == {"status": "unavailable", "rows": []}


def test_stop_has_no_proposed_or_verified_setting_claim(evidence):
    current = evidence[0]
    stop = {"remedy": "stop", "selection": "model", "reason": "No distinct supported test remains"}
    assert journey._decision_display(None, "owner", current, stop, {}) == stop


def test_same_named_strategies_keep_separate_settings_identity():
    report = {"summary": {"initial_capital": 100000}, "strategies": [
        {"id": "first", "name": "Same name", "config": {"hold_sessions": 10}},
        {"id": "second", "name": "Same name", "config": {"hold_sessions": 20}},
    ]}
    values = journey._decision_settings(report)
    assert values[("first", "hold_sessions")] == 10
    assert values[("second", "hold_sessions")] == 20
