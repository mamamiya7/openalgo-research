"""Entry filters survive native decisions and reuse without rewriting evidence."""

# ruff: noqa: F811 -- shared isolated native fixture

import copy
import json

import pytest
from sqlalchemy import select
from test_condition_replay import result, run, saved  # noqa: F401

from database.research_db import ResearchChosenSetup, ResearchLibraryJob, ResearchSetupVersion
from research.evaluation_basis import comparison_status
from research.regimes import recipe
from services import research_chosen_setups as chosen
from services import research_condition_replay as conditions
from services import research_decisions as decisions
from services import research_library as library
from services import research_shortlist as shortlist
from services import scanner_research_service as service
from services.research_setup_reuse import retained_entry_conditions

pytestmark = pytest.mark.timeout(600)


def conditional(saved):
    store, parent, request = saved
    child = conditions.submit(store, "owner", parent.id, request, request_id="conditional-setup")
    run(store)
    result(store, child["id"])
    with store.sessions() as db:
        link = db.scalar(select(ResearchLibraryJob).where(ResearchLibraryJob.job_id == child["id"]))
        return store, parent, child["id"], link.experiment_id, link.version_id


def test_legacy_condition_copy_restore_and_shortlist_preserve_exact_entry_rules(saved):
    store, parent, child_id, experiment, version_id = conditional(saved)
    job = service.get_job(store, "owner", child_id)
    original = service.encoded(service.read_artifact(store, job.result_artifact))
    expected = {
        "version": "research-entry-conditions-v1",
        "recipe": recipe(),
        "rules": [{"strategy_id": "a", "dimension": "trend", "regime": "up"}],
    }
    bookmark = shortlist.save_candidate(store, "owner", experiment, {"job_id": child_id})
    assert bookmark["candidate"]["source_job_id"] == child_id
    assert shortlist.get_candidate(store, "owner", experiment, bookmark["candidate"]["id"])[
        "available"
    ]
    copied = library.from_job(
        store, "owner", {"job_id": child_id, "mode": "optimize", "request_id": "copy-condition"}
    )
    assert copied["draft"]["portfolio"]["entry_conditions"] == expected
    assert copied["draft"]["optimizing"]
    before = library.get_version(store, "owner", experiment, version_id)
    current = library.get_experiment(store, "owner", experiment)
    restored = library.restore_version(
        store, "owner", experiment, version_id, {"revision": current["revision"]}
    )
    assert restored["draft"]["portfolio"]["entry_conditions"] == expected
    assert library.get_version(store, "owner", experiment, version_id) == before
    baseline = service.read_artifact(store, parent.result_artifact)["result"]
    filtered = service.read_artifact(store, job.result_artifact)["result"]
    assert not comparison_status(filtered["evaluation_basis"], baseline["evaluation_basis"])[
        "compatible"
    ]
    assert service.encoded(service.read_artifact(store, job.result_artifact)) == original


def test_legacy_condition_keep_choice_exact_replay_pins_filter_not_unfiltered_parent(saved):
    store, parent, child_id, experiment, _ = conditional(saved)
    kept = decisions.save_direct_decision(
        store,
        "owner",
        experiment,
        child_id,
        {
            "revision": 0,
            "state": "keep",
            "reason": "Exploratory rising-market setup",
            "request_id": "keep-condition",
        },
    )
    assert kept["event"]["source_job_id"] == child_id != parent.id
    context = chosen.context(
        store, "owner", experiment, decision_id=kept["decision"]["id"], event_id=kept["event"]["id"]
    )
    assert context["eligibility"]["available"], context
    choice = chosen.choose(
        store,
        "owner",
        experiment,
        {
            "revision": context["revision"],
            "decision_id": kept["decision"]["id"],
            "event_id": kept["event"]["id"],
            "name": "Rising entries",
            "request_id": "choose-condition",
        },
    )
    assert choice["choice"]["entry_conditions"]["rules"][0]["regime"] == "up"
    with store.sessions() as db:
        row = db.get(ResearchChosenSetup, choice["choice"]["id"])
        version = db.get(ResearchSetupVersion, row.version_id)
        chosen.validate_version(row, version)
        detached = copy.deepcopy(version)
        portfolio = json.loads(detached.portfolio)
        portfolio["entry_conditions"]["rules"][0]["regime"] = "down"
        detached.portfolio = service.encoded(portfolio).decode()
        with pytest.raises(ValueError, match="frozen decision"):
            chosen.validate_version(row, detached)
    with store.sessions.begin() as db:
        row = db.get(ResearchChosenSetup, choice["choice"]["id"])
        version = db.get(ResearchSetupVersion, row.version_id)
        before = row.snapshot, version.portfolio, version.draft
        snapshot, portfolio, draft = (
            json.loads(row.snapshot),
            json.loads(version.portfolio),
            json.loads(version.draft),
        )
        for descriptor in (
            snapshot["entry_conditions"],
            portfolio["entry_conditions"],
            draft["portfolio"]["entry_conditions"],
        ):
            descriptor["rules"][0]["regime"] = "down"
        row.snapshot, version.portfolio, version.draft = (
            service.encoded(item).decode() for item in (snapshot, portfolio, draft)
        )
    with pytest.raises(ValueError, match="original saved evidence"):
        chosen.preview_use(store, "owner", experiment, {"choice_id": choice["choice"]["id"]})
    with store.sessions.begin() as db:
        row = db.get(ResearchChosenSetup, choice["choice"]["id"])
        version = db.get(ResearchSetupVersion, row.version_id)
        row.snapshot, version.portfolio, version.draft = before
    replay = chosen.replay_choice(
        store,
        "owner",
        experiment,
        {
            "revision": choice["revision"],
            "choice_id": choice["choice"]["id"],
            "request_id": "replay-condition-choice",
        },
    )
    run(store)
    repeated = result(store, replay["job"]["id"])
    previous = result(store, child_id)
    assert repeated["condition_replay"] == previous["condition_replay"]
    assert repeated["ledger"] == previous["ledger"]


def test_fixed_condition_choice_preview_use_new_csv_and_backup_are_recipe_bound(saved, tmp_path):
    from database.research_db import ResearchStore
    from research.portfolio import execution_versions, normalize
    from services.research_regimes import retained_conditions
    from services.research_storage import _references, backup_store, restore_store

    store, parent, request = saved
    bundle = service.read_artifact(store, parent.result_artifact)
    evidence = service.read_artifact(store, bundle["inputs_artifact"])
    rows = evidence["strategies"][0]["signals"]
    csv = "Date,Symbol\n" + "".join(f"{row['date']},{row['symbol']}\n" for row in rows)
    source = service.create_source(store, "owner", csv.encode(), "broker", defer_preparation=True)
    evidence["portfolio"]["strategies"][0]["source_id"] = source["id"]
    evidence["strategies"][0]["source_id"] = source["id"]
    evidence["portfolio"]["entry_conditions"] = {
        "version": "research-entry-conditions-v1",
        "recipe": recipe(),
        "rules": [{"strategy_id": "a", "dimension": "trend", "regime": "up"}],
    }
    evidence["portfolio"] = normalize(evidence["portfolio"])
    evidence["versions"] = execution_versions(evidence["portfolio"])
    evidence["entry_condition_market"] = retained_conditions(
        store, request["analysis_artifact"], parent.result_artifact
    )
    _, registered = service.register_source(store, "owner", evidence)
    job = service.submit(
        store,
        "owner",
        registered["id"],
        {"initial_capital": 10000},
        kind="portfolio_backtest",
        specification={"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    )
    run(store)
    result(store, job["id"])
    experiment = library.from_job(
        store,
        "owner",
        {"job_id": job["id"], "mode": "backtest", "request_id": "fixed-condition-library"},
    )
    kept = decisions.save_direct_decision(
        store,
        "owner",
        experiment["id"],
        job["id"],
        {
            "revision": 0,
            "state": "keep",
            "reason": "Fixed entry rule",
            "request_id": "keep-fixed-condition",
        },
    )
    selected = chosen.choose(
        store,
        "owner",
        experiment["id"],
        {
            "revision": experiment["revision"],
            "decision_id": kept["decision"]["id"],
            "event_id": kept["event"]["id"],
            "name": "Fixed rising rule",
            "request_id": "choose-fixed-condition",
        },
    )
    replacement = service.create_source(
        store,
        "owner",
        b"Date,Symbol\n2026-07-01,AAA\n2026-07-02,BBB\n",
        "broker",
        defer_preparation=True,
    )
    spec = {
        "choice_id": selected["choice"]["id"],
        "mode": "optimize",
        "sources": {"a": replacement["id"]},
    }
    preview = chosen.preview_use(store, "owner", experiment["id"], spec)
    assert (
        preview["draft"]["portfolio"]["entry_conditions"]
        == evidence["portfolio"]["entry_conditions"]
    )
    assert preview["draft"]["portfolio"]["strategies"][0]["entry_condition"]["regime"] == "up"
    used = chosen.use_choice(
        store,
        "owner",
        experiment["id"],
        {**spec, "revision": selected["revision"], "request_id": "use-fixed-new-csv"},
    )
    assert used["experiment"]["draft"] == preview["draft"]
    _references(store.engine)
    backup_store(store, tmp_path / "fixed-backup")
    restore_store(tmp_path / "fixed-backup", tmp_path / "fixed-restored")
    restored = ResearchStore(tmp_path / "fixed-restored")
    try:
        restored.initialize()
        current = chosen.context(restored, "owner", experiment["id"])["current"]
        assert current["entry_conditions"] == selected["choice"]["entry_conditions"]
    finally:
        restored.close()


def test_real_fixed_condition_optuna_alternative_replay_shortlist_and_choose_preserve_identity(
    saved,
):
    from research.portfolio import execution_versions, normalize
    from research.report_contract import settings_identity
    from services import research_candidates as candidates
    from services.research_regimes import retained_conditions

    store, parent, request = saved
    bundle = service.read_artifact(store, parent.result_artifact)
    evidence = service.read_artifact(store, bundle["inputs_artifact"])
    portfolio = evidence["portfolio"]
    descriptor = {
        "version": "research-entry-conditions-v1",
        "recipe": recipe(),
        "rules": [{"strategy_id": "a", "dimension": "trend", "regime": "up"}],
    }
    portfolio["entry_conditions"] = descriptor
    portfolio["strategies"][0]["config"]["hold_sessions"] = 4
    portfolio["strategies"][0]["search"] = {"hold_sessions": {"min": 2, "max": 6, "step": 2}}
    portfolio["optimization"] = {
        "sampler": "grid",
        "trials": 3,
        "objective": "balanced",
        "seed": 11,
    }
    evidence["portfolio"] = normalize(portfolio)
    evidence["strategies"][0].update(evidence["portfolio"]["strategies"][0])
    evidence["versions"] = execution_versions(evidence["portfolio"])
    evidence["entry_condition_market"] = retained_conditions(
        store, request["analysis_artifact"], parent.result_artifact
    )
    _, source = service.register_source(store, "owner", evidence)
    submitted = service.submit(
        store,
        "owner",
        source["id"],
        {"initial_capital": 10000},
        kind="portfolio_optimize",
        specification={"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    )
    run(store)
    study = result(store, submitted["id"])
    experiment = library.from_job(
        store,
        "owner",
        {"job_id": submitted["id"], "mode": "backtest", "request_id": "fixed-grid-library"},
    )
    rows = study["experiment"]["rows"]
    assert len(rows) == 3
    assert study["experiment"]["recommendation_id"] == settings_identity(study["strategies"])
    for row in rows:
        assert row["config_id"] == settings_identity(row["strategies"])
        assert row["strategies"][0]["entry_condition"]["regime"] == "up"
    alternative = next(
        row for row in rows if row["config_id"] != study["experiment"]["recommendation_id"]
    )
    prepared = candidates.prepare(store, "owner", submitted["id"], alternative["config_id"])
    assert prepared["status"] in ("queued", "running"), prepared
    run(store)
    report_id = prepared["report_job_id"]
    replayed = result(store, report_id)
    assert replayed["summary"] == alternative["summary"]
    assert replayed["entry_conditions"] == descriptor
    assert settings_identity(replayed["strategies"]) == alternative["config_id"]
    bookmark = shortlist.save_candidate(
        store,
        "owner",
        experiment["id"],
        {"job_id": report_id, "config_id": alternative["config_id"]},
    )
    assert bookmark["candidate"]["config_id"] == alternative["config_id"]
    kept = decisions.save_direct_decision(
        store,
        "owner",
        experiment["id"],
        report_id,
        {
            "revision": 0,
            "state": "keep",
            "reason": "Fixed condition trial",
            "request_id": "keep-grid-condition",
        },
        config_id=alternative["config_id"],
    )
    current = library.get_experiment(store, "owner", experiment["id"])
    selected = chosen.choose(
        store,
        "owner",
        experiment["id"],
        {
            "revision": current["revision"],
            "decision_id": kept["decision"]["id"],
            "event_id": kept["event"]["id"],
            "name": "Fixed grid choice",
            "request_id": "choose-grid-condition",
        },
    )
    assert selected["choice"]["entry_conditions"] == descriptor
    assert selected["choice"]["config_id"] == alternative["config_id"]


@pytest.mark.parametrize(
    "tamper", ["missing-gate", "result-rule", "input-rule", "missing-result-rule"]
)
def test_conditional_rules_reject_missing_or_tampered_evidence(saved, tamper):
    store, _, child_id, _, _ = conditional(saved)
    job = service.get_job(store, "owner", child_id)
    bundle = copy.deepcopy(service.read_artifact(store, job.result_artifact))
    evidence = service.read_artifact(store, bundle["inputs_artifact"])
    if tamper == "missing-gate":
        evidence.pop("condition_gate")
    elif tamper == "result-rule":
        bundle["result"]["condition_replay"]["condition"]["regime"] = "down"
    elif tamper == "input-rule":
        evidence["condition_replay"]["condition"]["regime"] = "down"
    else:
        bundle["result"].pop("condition_replay")
    bundle["inputs_artifact"] = service.save_artifact(store, evidence)
    with pytest.raises(ValueError, match="entry.condition|membership"):
        retained_entry_conditions(store, bundle)


@pytest.mark.parametrize("origin", ["replay_origin", "matched_baseline_origin"])
def test_conditional_canonical_source_requires_selection_provenance(saved, monkeypatch, origin):
    from services import research_shortlist, research_validation

    store, parent, child_id, experiment, version_id = conditional(saved)
    job = service.get_job(store, "owner", child_id)
    actual_read = service.read_artifact
    bundle = copy.deepcopy(actual_read(store, job.result_artifact))
    bundle["result"][origin] = {"period": "evaluation"}
    monkeypatch.setattr(
        service,
        "read_artifact",
        lambda archive, key: (
            copy.deepcopy(bundle) if key == job.result_artifact else actual_read(archive, key)
        ),
    )
    with pytest.raises(ValueError, match="selection-period"):
        research_validation.canonical_source(store, "owner", experiment, child_id)
    with pytest.raises(ValueError, match="selection-period"):
        research_shortlist.save_candidate(store, "owner", experiment, {"job_id": child_id})
