"""Bounded saved-data evidence and canonical chosen-result helpers."""

# ruff: noqa: F811 -- isolated native fixtures
import copy
import json
from types import SimpleNamespace

import pytest
from test_jobs import app, client  # noqa: F401

from services import research_mcp as api

OWNER = "research-test"
JOB = "a" * 32


def saved_report():
    return {
        "strategies": [
            {
                "id": "one",
                "name": "Saved setup",
                "allocation_pct": 100,
                "config": {
                    "initial_capital": 32000,
                    "order_size_pct": 3,
                    "stop_pct": 5,
                    "target_pct": 8,
                    "credentials": "DO-NOT-EXPOSE",
                },
            }
        ],
        "summary": {"initial_capital": 32000, "net_return_pct": 4, "closed_trades": 20},
        "evaluation_basis": {"evidence_id": "e" * 64},
        "automatic_research": {
            "status": "inconclusive",
            "recipe": {
                "budget": {"max_simulations": 512, "proposals": 408},
                "validation_mode": "exploratory_only",
                "snapshot": {"broker_credentials": "DO-NOT-EXPOSE"},
            },
            "counts": {"simulations": 337, "attempts_reserved": 346},
            "decision_preflight": {
                "goal": "Preserve risk limits",
                "objective": "Return minus drawdown",
            },
            "decision": {
                "outcome": "insufficient_evidence",
                "independently_validated": False,
                "prefix_diagnostics": [{"period": "fit1", "passed": True}],
                "checks": [
                    {"kind": "higher_costs", "passed": False, "reasons": ["Cost fragility"]}
                ],
                "reasons": ["Independent evidence is unavailable"],
            },
            "capital_planning": {
                "status": "no_verified_amount",
                "feasible_capital": 32000,
                "diagnosis": {"categories": {"position_limit": 3, "unavailable_sizing_intent": 1}},
                "operating_point": {"capital": 32000, "settings": []},
            },
            "operating_validation": {
                "outcome": "rejected",
                "historically_validated": False,
                "reasons": ["Cost challenge failed"],
            },
            "automatic_design": {
                "selected": {"qualified": False, "reasons": ["Insufficient entry sessions"]}
            },
            "ledger": [{"apikey": "DO-NOT-EXPOSE"}],
            "checkpoint": "DO-NOT-EXPOSE",
        },
    }


def fake_completed(monkeypatch, report):
    job = SimpleNamespace(id=JOB, owner=OWNER, status="completed", result_artifact="b" * 64)
    monkeypatch.setattr(
        api.research,
        "get_job",
        lambda store, owner, identifier: (
            job if owner == OWNER else (_ for _ in ()).throw(LookupError("not found"))
        ),
    )
    monkeypatch.setattr(api.research, "read_artifact", lambda *a: {"result": report})
    return job


def test_evidence_has_native_budget_failed_checks_and_no_private_payload(monkeypatch):
    report = saved_report()
    before = copy.deepcopy(report)
    fake_completed(monkeypatch, report)
    evidence = api.decision_evidence(None, OWNER, JOB)
    assert evidence["budget"]["remaining_accounts"] == 166  # Includes nine lost reservations.
    assert evidence["selected_settings"][0]["config"]["order_size_pct"] == 3
    assert evidence["preflight"]["objective"] == "Return minus drawdown"
    assert evidence["decision"]["checks"][0]["passed"] is False
    assert evidence["operating_validation"]["outcome"] == "rejected"
    assert evidence["automatic_design"]["selected"]["qualified"] is False
    assert evidence["capital_planning"]["diagnosis"]["categories"]["unavailable_sizing_intent"] == 1
    assert evidence["validation_scope"]["prefixes_are_acceptance_gates"] is False
    assert evidence["validation_scope"]["prefixes_are_independent"] is False
    assert "DO-NOT-EXPOSE" not in json.dumps(evidence)
    assert report == before
    with pytest.raises(LookupError):
        api.decision_evidence(None, "other-account", JOB)


@pytest.mark.parametrize("attempts", [None, True, -1, "346"])
def test_unknown_budget_never_becomes_permission_to_compute(monkeypatch, attempts):
    report = saved_report()
    report["automatic_research"]["counts"]["attempts_reserved"] = attempts
    fake_completed(monkeypatch, report)
    assert api.decision_evidence(None, OWNER, JOB)["budget"]["remaining_accounts"] is None


def test_materialization_uses_exact_saved_identity_and_canonical_admission(monkeypatch):
    from research.report_contract import settings_identity
    from services import research_candidates

    report = saved_report()
    config = settings_identity(report["strategies"])
    report["experiment"] = {"recommendation_id": config}
    fake_completed(monkeypatch, report)
    calls = []
    monkeypatch.setattr(
        research_candidates,
        "prepare",
        lambda *args: (
            calls.append(args)
            or {"config_id": args[-1], "status": "ready", "report_job_id": "c" * 32}
        ),
    )
    receipt = api.materialize_candidate(None, OWNER, JOB)
    assert receipt["config_id"] == config
    assert calls == [(None, OWNER, JOB, config)]
    with pytest.raises(ValueError, match="exact saved"):
        api.materialize_candidate(None, OWNER, JOB, "invented")
    assert "research_materialize_candidate" not in api.TOOLS
    assert "research_save_chosen_result" not in api.TOOLS


def sealed_supervisor(report):
    from research.report_contract import fingerprint, settings_identity

    native = {
        k: v
        for k, v in report.items()
        if k not in ("supervisor_validation", "automatic_research", "experiment")
    }
    operating = {
        "version": "research-operating-validation-v1",
        "outcome": "validated",
        "reasons": [],
        "historically_validated": True,
        "independently_validated": False,
        "selected_report_id": fingerprint(native),
        "selected_config_id": settings_identity(native["strategies"]),
        "capital": native["summary"]["initial_capital"],
    }
    operating["id"] = fingerprint(operating)
    receipt = {
        "version": "research-supervisor-validation-v1",
        "outcome": "validated",
        "reasons": [],
        "historically_validated": True,
        "independently_validated": False,
        "native_report_id": fingerprint(native),
        "selected_config_id": operating["selected_config_id"],
        "capital": operating["capital"],
        "operating_validation": operating,
    }
    receipt["id"] = fingerprint(receipt)
    return {**report, "supervisor_validation": receipt}


@pytest.mark.parametrize("change", ["capital", "native_report_id", "outcome", "id"])
def test_automatic_save_rejects_tampered_or_unvalidated_native_receipt(monkeypatch, change):
    report = sealed_supervisor(saved_report())
    report["supervisor_validation"][change] = "tampered"
    fake_completed(monkeypatch, report)
    with pytest.raises(ValueError, match="exact native supervisor validation"):
        api.save_accepted_result(None, OWNER, JOB, name="Checked", request_id="auto-save-case")


def test_exact_keep_save_reopen_is_idempotent_without_new_calculations(app, client, monkeypatch):
    from test_chosen_setups import ready

    from services import research_candidates
    from services import scanner_research_service as service
    from services import scanner_research_worker as worker

    store, experiment, _, child, members, _, payload = ready(app, client, monkeypatch)
    # Only three tiny fixture proposals were run. From this point onward neither
    # saving nor reopening may admit another numerical account.
    monkeypatch.setattr(research_candidates, "prepare", lambda *a: pytest.fail("new report"))
    monkeypatch.setattr(worker, "run_one", lambda *a: pytest.fail("new calculation"))
    result = api.save_chosen_result(store, OWNER, experiment, **payload)
    verification = result["verification"]
    assert verification["matched"] is True
    assert verification["new_calculations"] == 0
    assert verification["report_job_id"] == child
    assert verification["config_id"] == members[1]["config_id"]
    assert api.save_chosen_result(store, OWNER, experiment, **payload)["reused"] is True
    reopened = api.reopen_chosen_result(store, OWNER, experiment)
    assert reopened["verification"] == verification
    assert reopened["summary"] == members[1]["snapshot"]["summary"]
    with pytest.raises(LookupError):
        api.reopen_chosen_result(store, "another-owner", experiment)
    # Attach a fixture validation receipt to the actual three-proposal native
    # report. This tests persistence, not the numerical validator (separate tests).
    from database.research_db import ResearchJob

    native_job = service.get_job(store, OWNER, child)
    bundle = service.read_artifact(store, native_job.result_artifact)
    bundle["result"] = sealed_supervisor(bundle["result"])
    artifact = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.get(ResearchJob, child).result_artifact = artifact
    automatic = api.save_accepted_result(
        store, OWNER, child, name="Automatically checked setup", request_id="auto-save-test-case"
    )
    assert automatic["choice_id"] == automatic["choice"]["id"]
    assert automatic["verification"]["new_calculations"] == 0
    assert automatic["verification"]["capital"] == reopened["verification"]["capital"]
    assert automatic["verification"]["config_id"] == reopened["verification"]["config_id"]
    assert (
        api.save_accepted_result(
            store,
            OWNER,
            child,
            name="Automatically checked setup",
            request_id="auto-save-test-case",
        )["choice"]["id"]
        == automatic["choice"]["id"]
    )
    assert (
        api.reopen_chosen_result(store, OWNER, experiment)["verification"]
        == automatic["verification"]
    )
    # A standalone saved native account exercises container creation without
    # another engine call or copying a runtime database.
    from database.research_db import ResearchExperiment

    standalone_bundle = copy.deepcopy(bundle)
    standalone_bundle["job_id"] = "d" * 32
    standalone_bundle["result"].pop("replay_origin", None)
    standalone_bundle["result"] = sealed_supervisor(standalone_bundle["result"])
    standalone_artifact = service.save_artifact(store, standalone_bundle)
    with store.sessions.begin() as db:
        original = db.get(ResearchJob, child)
        db.add(
            ResearchJob(
                id="d" * 32,
                owner=OWNER,
                source_id=original.source_id,
                config=original.config,
                status="completed",
                progress=100,
                created_at=original.created_at,
                updated_at=original.updated_at,
                result_artifact=standalone_artifact,
            )
        )
        original_metadata = db.get(ResearchExperiment, child)
        db.add(
            ResearchExperiment(
                job_id="d" * 32,
                kind="portfolio_backtest",
                specification=original_metadata.specification,
                identity="d" * 64,
            )
        )
    standalone = api.save_accepted_result(
        store, OWNER, "d" * 32, name="Standalone checked setup", request_id="auto-save-standalone"
    )
    assert standalone["experiment_id"] != experiment
    assert standalone["verification"]["report_job_id"] == "d" * 32
    assert (
        api.save_accepted_result(
            store,
            OWNER,
            "d" * 32,
            name="Standalone checked setup",
            request_id="auto-save-standalone",
        )["choice"]["id"]
        == standalone["choice"]["id"]
    )
    # Native report replacement is rejected even if the chosen setup persists.
    with store.sessions.begin() as db:
        db.get(ResearchJob, child).result_artifact = "f" * 64
    with pytest.raises(ValueError, match="unavailable|changed|missing or damaged"):
        api.reopen_chosen_result(store, OWNER, experiment)
