"""Frozen source gaps remain exclusions; unknown execution gaps remain blocking."""

# ruff: noqa: F811 -- imported isolated supervisor ledger fixture
import json
from copy import deepcopy

import pytest
from test_research_supervisor import fixture_report
from test_research_supervisor import ledger as supervisor_ledger  # noqa: F401

from research.report_contract import fingerprint
from research.user_flow import normalize
from services import research_experiments
from services import research_supervisor as supervisor


@pytest.fixture
def case(monkeypatch):
    report = fixture_report()
    report["automatic_research"]["capital_planning"]["diagnosis"]["categories"] = {
        "cash_shortfall": 1,
        "unavailable_evidence": 4,
        "evidence_gap_requests": 4,
    }
    signals = [
        {"strategy_id": "one", "symbol": f"S{n}", "date": "2026-01-05", "row": n + 1}
        for n in range(6)
    ]
    exclusions = [
        {
            "strategy_id": row["strategy_id"],
            "symbol": row["symbol"],
            "date": row["date"],
            "reason": "Broker price unavailable: frozen window",
        }
        for row in signals[:4]
    ]
    report["ledger"] = [
        {
            "strategy_id": row["strategy_id"],
            "symbol": row["symbol"],
            "signal_date": row["date"],
            "source_row": row["row"],
            "status": "excluded",
            "reason": "Broker price unavailable: frozen window",
        }
        for row in signals[:4]
    ]
    inputs = {
        "signals": signals,
        "signal_coverage": {
            "policy_version": "portfolio-fixed-price-cohort-v1",
            "eligible_signals": 2,
            "excluded_signals": 4,
            "pending_signals": 0,
            "exclusions": exclusions,
        },
    }
    monkeypatch.setattr(supervisor.service, "read_artifact", lambda *a: deepcopy(inputs))
    proof = supervisor._frozen_exclusions(None, {"inputs_artifact": "a" * 64})
    assert proof is not None
    return report, inputs, proof


def test_four_exact_frozen_exclusions_do_not_globally_veto_supported_remedies(case):
    report, _, proof = case
    original = deepcopy(report)
    diagnosis = supervisor._diagnosis(report, exclusions=proof)
    assert diagnosis["missing_evidence"] is False
    assert diagnosis["evidence_status"]["proven_preexcluded_requests"] == 4
    assert set(diagnosis["permitted_remedies"]) == {
        "funding_and_size",
        "lower_turnover",
        "reduce_concentration",
        "temporal_stability",
    }
    assert report == original  # Raw counts, ledger, excluded coverage and verdicts remain exact.


def test_malformed_exclusion_proof_never_grants_missing_evidence_an_exemption(case):
    report, _, proof = case
    proof["entries"][0] = {}
    proof["id"] = fingerprint({key: value for key, value in proof.items() if key != "id"})
    diagnosis = supervisor._diagnosis(report, exclusions=proof)
    assert diagnosis["missing_evidence"] is True
    assert diagnosis["evidence_status"]["proven_preexcluded_requests"] == 0


@pytest.mark.parametrize(
    "reason",
    [
        "Broker price requirement not met: unavailable history",
        "Unknown risk evidence not met: missing execution metadata",
        "Unrecognized prefix: Entry condition not met: missing prices",
    ],
)
def test_arbitrary_not_met_text_is_not_a_benign_native_rule_exclusion(case, reason):
    report, _, proof = case
    report["ledger"][0]["reason"] = reason
    before = deepcopy(report)
    diagnosis = supervisor._diagnosis(report, exclusions=proof)
    assert diagnosis["missing_evidence"] is True
    assert diagnosis["evidence_status"]["unknown_excluded_requests"] == 1
    assert diagnosis["evidence_status"]["proven_preexcluded_requests"] == 3
    assert report == before


@pytest.mark.parametrize(
    "reason",
    [
        "Joint entry condition not met: selected native rule",
        "Stock condition not met: selected native rule",
        "Entry condition not met: selected native rule",
        "Maximum holding window crosses the search period end (2026-10-23)",
    ],
)
def test_known_native_rule_or_window_prefix_remains_a_separate_exclusion(case, reason):
    report, _, proof = case
    row = deepcopy(report["ledger"][0])
    row.update(source_row=99, reason=reason)
    report["ledger"].append(row)
    diagnosis = supervisor._diagnosis(report, exclusions=proof)
    assert diagnosis["missing_evidence"] is False
    assert diagnosis["evidence_status"]["proven_preexcluded_requests"] == 4
    assert diagnosis["evidence_status"]["unknown_excluded_requests"] == 0


@pytest.mark.parametrize(
    "change",
    ["no_proof", "unknown_gap", "sizing", "unexplained", "different_reason", "different_signal"],
)
def test_unknown_required_execution_evidence_remains_a_global_stop(case, change):
    report, _, proof = case
    counts = report["automatic_research"]["capital_planning"]["diagnosis"]["categories"]
    if change == "no_proof":
        proof = None
    elif change == "unknown_gap":
        counts["evidence_gap_requests"] += 1
        counts["unavailable_evidence"] += 1
    elif change == "sizing":
        counts["unavailable_sizing_intent"] = 1
    elif change == "unexplained":
        counts["unexplained_execution_limit"] = 1
    elif change == "different_reason":
        report["ledger"][0]["reason"] = "Execution risk unavailable"
    else:
        report["ledger"][0]["source_row"] = 99
    diagnosis = supervisor._diagnosis(report, exclusions=proof)
    assert diagnosis["missing_evidence"] and diagnosis["permitted_remedies"] == []


@pytest.mark.parametrize("change", ["coverage_total", "ambiguous_signal", "unknown_policy"])
def test_preparation_proof_never_invents_usable_or_excluded_coverage(case, monkeypatch, change):
    _, inputs, _ = case
    if change == "coverage_total":
        inputs["signal_coverage"]["eligible_signals"] += 1
    elif change == "ambiguous_signal":
        inputs["signals"][1]["symbol"] = inputs["signals"][0]["symbol"]
    else:
        inputs["signal_coverage"]["policy_version"] = "unknown-policy"
    monkeypatch.setattr(supervisor.service, "read_artifact", lambda *a: deepcopy(inputs))
    assert supervisor._frozen_exclusions(None, {"inputs_artifact": "a" * 64}) is None


def catalog_current(report, proof, *, adjustable=None):
    return {
        "proposals": [],
        "source_result_artifact": "b" * 64,
        "frozen_exclusions": proof,
        "research_context": {
            "flow": normalize()
            if adjustable is None
            else normalize({**normalize(), "adjustable": adjustable}),
            "baseline_settings": deepcopy(report["strategies"]),
        },
    }


def test_catalog_does_not_mislabel_unsupported_domain_as_user_locks(case, monkeypatch):
    report, _, proof = case
    expected = "No effect-relevant setting remains in the supported domain"
    monkeypatch.setattr(
        research_experiments,
        "build",
        lambda *a, **kw: {
            "options": [],
            "feedback": {},
            "unavailable": [],
            "stop_reason": expected,
        },
    )
    result = supervisor._experiment_catalog(
        report, report["automatic_research"]["recipe"], catalog_current(report, proof)
    )
    assert result["stop_reason"] == expected
    assert "fixed settings" not in result["stop_reason"]


def test_catalog_claims_user_locks_only_when_they_filter_an_actual_option(case, monkeypatch):
    report, _, proof = case
    settings = deepcopy(report["strategies"])
    settings[0]["config"]["hold_sessions"] += 1
    monkeypatch.setattr(
        research_experiments,
        "build",
        lambda *a, **kw: {
            "options": [{"remedy": "temporal_stability", "settings": settings}],
            "feedback": {},
            "unavailable": [],
        },
    )
    result = supervisor._experiment_catalog(
        report,
        report["automatic_research"]["recipe"],
        catalog_current(report, proof, adjustable=[]),
    )
    assert result["options"] == [] and "fixed settings" in result["stop_reason"]


def test_missing_required_evidence_reason_has_priority_over_permissions(case, monkeypatch):
    report, _, proof = case
    report["automatic_research"]["capital_planning"]["diagnosis"]["categories"][
        "unavailable_sizing_intent"
    ] = 1
    monkeypatch.setattr(
        research_experiments,
        "build",
        lambda *a, **kw: {
            "options": [],
            "feedback": {},
            "unavailable": [{"reason_code": "fixed_settings"}],
        },
    )
    result = supervisor._experiment_catalog(
        report,
        report["automatic_research"]["recipe"],
        catalog_current(report, proof, adjustable=[]),
    )
    assert "Required execution or sizing evidence" in result["stop_reason"]
    assert "unavailable_sizing_intent=1" in result["stop_reason"]
    assert "fixed settings" not in result["stop_reason"]


@pytest.mark.parametrize("spent,prior_stop", [(0, None), (1, None), (0, "model"), (0, "native")])
def test_catalog_upgrade_refreshes_only_unused_stopped_grant_without_resetting_budgets(
    supervisor_ledger, case, monkeypatch, spent, prior_stop
):
    from test_automatic_continuation import start
    from test_research_supervisor import CONVERSATION, OWNER, ROOT

    from database.research_db import ResearchDecisionJob, ResearchSupervisor
    from services import research_automation

    ledger = supervisor_ledger
    report, _, _ = case
    ledger.report.clear()
    ledger.report.update(deepcopy(report))
    initial = start(ledger, monkeypatch, model_id=1)
    with ledger.store.sessions.begin() as db:
        row = db.get(ResearchSupervisor, initial["id"])
        saved = json.loads(row.state)
        saved.update(
            status="stopped",
            stopping_reason="Old blanket evidence-gap veto",
            decision_catalog_version="executable-development-remedies-v3",
        )
        saved.pop("frozen_exclusions", None)
        saved["diagnosis"] = {"missing_evidence": True, "permitted_remedies": []}
        saved["automation"]["calls_spent"] = spent
        if prior_stop:
            prior = {"selection": prior_stop, "reason": "A recorded justified stop remains final"}
            if prior_stop == "model":
                saved["stop_decision"] = prior
            else:
                saved["automation"]["decisions"] = [{**prior, "remedy": "stop"}]
        original_attempts, original_simulations = (
            saved["original_attempts"],
            saved["original_simulations"],
        )
        supervisor._put(row, saved)
        db.get(ResearchDecisionJob, initial["id"]).phase = "stopped"
    refreshed = research_automation.start(
        ledger.store,
        OWNER,
        ROOT,
        conversation_id=CONVERSATION,
        request_id="automatic-small-case",
        model_id=1,
    )
    assert refreshed["status"] == ("planning" if spent == 0 and not prior_stop else "stopped")
    assert refreshed["automation"]["calls_spent"] == spent
    assert refreshed["original_attempts"] == original_attempts
    assert refreshed["original_simulations"] == original_simulations
    assert refreshed["remaining_accounts"] == 512 - original_attempts
    assert ledger.submissions == []
    if spent == 0 and not prior_stop:
        assert refreshed["decision_catalog_version"] == supervisor.DECISION_CATALOG_VERSION
        assert refreshed["diagnosis"]["missing_evidence"] is False
    with pytest.raises(ValueError, match="different frozen model allowance"):
        research_automation.start(
            ledger.store,
            OWNER,
            ROOT,
            conversation_id=CONVERSATION,
            request_id="automatic-small-case",
            model_id=2,
        )
