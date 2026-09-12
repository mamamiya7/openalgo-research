"""Native chosen setups freeze exact Keep rules separately from saved candidates."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import copy
import json

import pytest
from sqlalchemy import func, select
from test_decisions import body, native_setup
from test_jobs import app, client  # noqa: F401
from test_library import OWNER
from test_portfolio_workflow import uploaded
from test_report_period_workflow import run_worker

from database.research_db import (
    ResearchChosenRequest,
    ResearchChosenSetup,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchSetupVersion,
    ResearchStore,
)
from research.report_contract import settings_identity
from services import research_chosen_setups as chosen
from services import research_decisions as decisions
from services import research_library as library
from services import scanner_research_service as service
from services.research_storage import _references, backup_store, restore_store


def ready(app, client, monkeypatch, *, direct=True):
    experiment, comparison, members, job, child, _ = native_setup(app, client, monkeypatch)
    store = app.extensions["research_store"]
    if direct:
        kept = decisions.save_direct_decision(
            store, OWNER, experiment, job, body(), config_id=members[1]["config_id"]
        )
    else:
        kept = decisions.save_decision(
            store, OWNER, experiment, comparison["id"], members[1]["id"], body()
        )
    context = chosen.context(
        store, OWNER, experiment, decision_id=kept["decision"]["id"], event_id=kept["event"]["id"]
    )
    payload = {
        "revision": context["revision"],
        "request_id": "choose-rules-first",
        "decision_id": kept["decision"]["id"],
        "event_id": kept["event"]["id"],
        "name": "My swing setup",
    }
    return store, experiment, job, child, members, kept, payload


@pytest.mark.parametrize("direct", [False, True])
def test_choice_freezes_exact_alternative_without_overwriting_draft(
    app, client, monkeypatch, direct
):
    store, experiment, job, child, members, kept, payload = ready(
        app, client, monkeypatch, direct=direct
    )
    previous = library.get_experiment(store, OWNER, experiment)["draft"]
    result = chosen.choose(store, OWNER, experiment, payload)
    assert not result["reused"]
    choice = result["choice"]
    assert choice["config_id"] == members[1]["config_id"] != members[0]["config_id"]
    assert settings_identity(choice["strategies"]) == choice["config_id"]
    assert choice["usable"] and choice["period"] == "selection"
    frozen = library.get_version(store, OWNER, experiment, choice["version"]["id"])
    assert frozen["parent_job_id"] == job and frozen["parent_trial_id"] == choice["config_id"]
    assert not frozen["draft"]["optimizing"]
    assert all(not row["search"] for row in frozen["portfolio"]["strategies"])
    assert frozen["portfolio"]["validation"] == previous["portfolio"]["validation"]
    from services.research_portfolio import resolve_inputs

    fresh_inputs = resolve_inputs(store, OWNER, library.portfolio_payload(frozen["draft"]))
    assert not set(fresh_inputs).intersection(
        {"frozen_prices", "replay_origin", "matched_baseline_origin"}
    )
    assert fresh_inputs["snapshot"]["bars"] == {}
    assert library.get_experiment(store, OWNER, experiment)["draft"] == previous
    monkeypatch.setattr(
        service, "read_artifact", lambda *a: pytest.fail("List or retry read result artifact")
    )
    assert chosen.choose(store, OWNER, experiment, payload)["reused"]
    assert library.list_experiments(store, OWNER)["items"][0]["chosen_setup"]["id"] == choice["id"]
    assert chosen.history(store, OWNER, experiment)["items"][0]["id"] == choice["id"]
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 1


def test_use_replaces_only_explicit_csv_and_preserves_edited_draft(app, client, monkeypatch):
    store, experiment, _, _, _, _, payload = ready(app, client, monkeypatch)
    result = chosen.choose(store, OWNER, experiment, payload)
    choice = result["choice"]
    before = library.get_experiment(store, OWNER, experiment)
    edited = copy.deepcopy(before["draft"])
    edited["portfolio"]["capital"] = 543210
    before = library.save_draft(
        store, OWNER, experiment, {"revision": before["revision"], "draft": edited}
    )
    source = uploaded(client, b"Date,Symbol\n2026-02-02,AAA\n2026-02-03,AAA\n")
    strategy = choice["strategies"][0]["id"]
    selection = {"choice_id": choice["id"], "mode": "optimize", "sources": {strategy: source}}
    preview = chosen.preview_use(store, OWNER, experiment, selection)
    assert preview["draft"]["optimizing"] and preview["changes"]["rules_unchanged"]
    assert preview["draft"]["portfolio"]["validation"] == {"mode": "reserve", "train_pct": 70}
    assert preview["changes"]["sources"][0]["after"]["source_id"] == source
    assert settings_identity(preview["draft"]["portfolio"]["strategies"]) == choice["config_id"]
    data = {**selection, "revision": before["revision"], "request_id": "reuse-choice-first"}
    used = chosen.use_choice(store, OWNER, experiment, data)
    assert used["experiment"]["draft"] == preview["draft"]
    preserved = library.get_version(store, OWNER, experiment, used["preserved_version_id"])
    assert preserved["draft"] == edited
    applied = library.get_version(store, OWNER, experiment, used["applied_version_id"])
    assert applied["parent_version_id"] == choice["version"]["id"]
    newer = copy.deepcopy(used["experiment"]["draft"])
    newer["portfolio"]["name"] = "Later edits"
    library.save_draft(store, OWNER, experiment, {"revision": used["revision"], "draft": newer})
    again = chosen.use_choice(store, OWNER, experiment, data)
    assert again["reused"] and again["experiment"]["draft"] == newer
    _references(store.engine)


def test_replay_choice_uses_frozen_alternative_and_atomic_request(app, client, monkeypatch):
    store, experiment, job, _, _, _, payload = ready(app, client, monkeypatch)
    result = chosen.choose(store, OWNER, experiment, payload)
    choice = result["choice"]
    data = {
        "revision": result["revision"],
        "request_id": "replay-choice-first",
        "choice_id": choice["id"],
    }
    original_draft = library.get_experiment(store, OWNER, experiment)["draft"]
    replay = chosen.replay_choice(store, OWNER, experiment, data)
    assert replay["job"]["status"] == "queued" and not replay["reused"]
    run_worker(store)
    report = service.read_artifact(
        store, service.get_job(store, OWNER, replay["job"]["id"]).result_artifact
    )["result"]
    assert settings_identity(report["strategies"]) == choice["config_id"]
    assert report["replay_origin"]["parent_job_id"] == job
    assert library.get_experiment(store, OWNER, experiment)["draft"] == original_draft
    monkeypatch.setattr(
        "services.research_portfolio.replay_inputs",
        lambda *a, **kw: pytest.fail("Accepted replay recalculated"),
    )
    again = chosen.replay_choice(store, OWNER, experiment, data)
    assert again["reused"] and again["job"]["id"] == replay["job"]["id"]


def test_replay_publication_failure_rolls_back_job_version_and_receipt(app, client, monkeypatch):
    store, experiment, _, _, _, _, payload = ready(app, client, monkeypatch)
    result = chosen.choose(store, OWNER, experiment, payload)
    models = (ResearchJob, ResearchSetupVersion, ResearchChosenRequest)
    with store.sessions() as db:
        before = [db.scalar(select(func.count()).select_from(model)) for model in models]
    original = chosen._remember

    def fail(db, owner, exp, token, raw, digest, kind, choice, response):
        original(db, owner, exp, token, raw, digest, kind, choice, response)
        raise ValueError("Receipt publication failed")

    monkeypatch.setattr(chosen, "_remember", fail)
    with pytest.raises(ValueError, match="Receipt publication failed"):
        chosen.replay_choice(
            store,
            OWNER,
            experiment,
            {
                "revision": result["revision"],
                "request_id": "replay-choice-failure",
                "choice_id": result["choice"]["id"],
            },
        )
    with store.sessions() as db:
        assert [db.scalar(select(func.count()).select_from(model)) for model in models] == before
        assert db.get(ResearchLibraryExperiment, experiment).revision == result["revision"]


def test_chosen_history_keep_changes_archive_and_retry_survive_populated_restore(
    app, client, monkeypatch, tmp_path
):
    store, experiment, job, _, members, kept, payload = ready(app, client, monkeypatch)
    first = chosen.choose(store, OWNER, experiment, payload)
    choice = first["choice"]
    used_request = {
        "revision": first["revision"],
        "request_id": "use-for-backup",
        "choice_id": choice["id"],
    }
    used = chosen.use_choice(store, OWNER, experiment, used_request)
    replay_request = {
        "revision": used["revision"],
        "request_id": "replay-for-backup",
        "choice_id": choice["id"],
    }
    replay = chosen.replay_choice(store, OWNER, experiment, replay_request)
    run_worker(store)
    second = chosen.choose(
        store,
        OWNER,
        experiment,
        {
            **payload,
            "revision": replay["experiment"]["revision"],
            "request_id": "second-choice-backup",
            "name": "New deliberate choice",
        },
    )
    assert chosen.history(store, OWNER, experiment)["total"] == 2
    with pytest.raises(ValueError, match="earlier choice"):
        chosen.preview_use(store, OWNER, experiment, {"choice_id": choice["id"]})
    decisions.save_direct_decision(
        store,
        OWNER,
        experiment,
        job,
        body(token="reject-after-choice", revision=1, state="reject"),
        config_id=members[1]["config_id"],
    )
    assert not chosen.context(store, OWNER, experiment)["current"]["usable"]
    library.update_experiment(
        store, OWNER, experiment, {"revision": second["revision"], "archived": True}
    )
    backup = tmp_path.with_name(tmp_path.name + "-chosen-backup")
    backup_store(store, backup)
    destination = tmp_path.with_name(tmp_path.name + "-chosen-restored")
    restore_store(backup, destination)
    restored = ResearchStore(destination)
    restored.initialize()
    try:
        assert chosen.history(restored, OWNER, experiment)["total"] == 2
        assert not chosen.context(restored, OWNER, experiment)["current"]["usable"]
        assert chosen.choose(restored, OWNER, experiment, payload)["reused"]
        assert chosen.use_choice(restored, OWNER, experiment, used_request)["reused"]
        assert (
            chosen.replay_choice(restored, OWNER, experiment, replay_request)["job"]["id"]
            == replay["job"]["id"]
        )
        frozen = library.get_version(restored, OWNER, experiment, choice["version"]["id"])
        assert settings_identity(frozen["portfolio"]["strategies"]) == choice["config_id"]
        report = decisions.event_report(
            restored, OWNER, experiment, kept["decision"]["id"], kept["event"]["id"]
        )
        assert report["available"]
    finally:
        restored.close()


def test_choices_fence_owner_revision_request_and_changed_keep_during_preparation(
    app, client, monkeypatch
):
    store, experiment, job, _, members, _, payload = ready(app, client, monkeypatch)
    with pytest.raises(LookupError):
        chosen.choose(store, "another-user", experiment, payload)
    with pytest.raises(library.RevisionConflict):
        chosen.choose(store, OWNER, experiment, {**payload, "revision": payload["revision"] - 1})
    original = chosen._prepared_rules

    def changed(*args):
        result = original(*args)
        decisions.save_direct_decision(
            store,
            OWNER,
            experiment,
            job,
            body(token="changed-while-choosing", revision=1, state="revisit"),
            config_id=members[1]["config_id"],
        )
        return result

    monkeypatch.setattr(chosen, "_prepared_rules", changed)
    with pytest.raises(ValueError, match="decision changed"):
        chosen.choose(store, OWNER, experiment, payload)
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 0
        assert db.get(ResearchLibraryExperiment, experiment).revision == payload["revision"]


def test_chosen_restore_validation_rejects_tampered_version_and_orphan_receipt(
    app, client, monkeypatch
):
    store, experiment, _, _, _, _, payload = ready(app, client, monkeypatch)
    saved = chosen.choose(store, OWNER, experiment, payload)
    choice = saved["choice"]
    assert choice["source_result_artifact"] in _references(store.engine)
    with store.sessions.begin() as db:
        version = db.get(ResearchSetupVersion, choice["version"]["id"])
        original = version.portfolio
        altered = json.loads(original)
        altered["strategies"][0]["allocation_pct"] = 99
        version.portfolio = service.encoded(altered).decode()
    with pytest.raises(ValueError, match="rules differ"):
        _references(store.engine)
    with store.sessions.begin() as db:
        db.get(ResearchSetupVersion, choice["version"]["id"]).portfolio = original
        db.delete(db.get(ResearchChosenRequest, (OWNER, payload["request_id"])))
    with pytest.raises(ValueError, match="Keep evidence"):
        _references(store.engine)


def test_repeated_chosen_metadata_retry_and_error_release_connections(app, client, monkeypatch):
    import gc

    import psutil
    from sqlalchemy import event

    store, experiment, _, _, _, _, payload = ready(app, client, monkeypatch)
    result = chosen.choose(store, OWNER, experiment, payload)
    choice = result["choice"]
    monkeypatch.setattr(
        service, "read_artifact", lambda *a: pytest.fail("Metadata/retry read financial evidence")
    )
    active = set()

    def connected(connection, _record):
        active.add(id(connection))

    def closed(connection, _record):
        active.discard(id(connection))

    event.listen(store.engine, "connect", connected)
    event.listen(store.engine, "close", closed)
    process = psutil.Process()
    measure = process.num_handles if hasattr(process, "num_handles") else process.num_fds
    gc.collect()
    before = measure(), process.memory_info().rss
    try:
        for _ in range(100):
            assert chosen.context(store, OWNER, experiment)["current"]["id"] == choice["id"]
            assert chosen.choose(store, OWNER, experiment, payload)["reused"]
            with pytest.raises(LookupError):
                chosen.context(store, "other-user", experiment)
            with pytest.raises(decisions.DecisionRequestConflict):
                chosen.choose(store, OWNER, experiment, {**payload, "name": "Changed identity"})
            assert not active
        gc.collect()
        assert measure() <= before[0] + 3
        assert process.memory_info().rss <= before[1] + 16 * 1024**2
    finally:
        event.remove(store.engine, "connect", connected)
        event.remove(store.engine, "close", closed)


def test_baseline_can_be_chosen_without_manufacturing_a_study_or_comparison(
    app, client, monkeypatch
):
    from test_portfolio_validation import inputs
    from test_report_period_workflow import draft

    from research.portfolio_coverage import prepare

    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda _store, _owner, evidence, **_: prepare(
            {**evidence, "snapshot": inputs()["snapshot"], "frozen_prices": True}
        ),
    )
    configuration = draft(client, False)
    experiment = library.create_experiment(
        store, OWNER, {"name": "Baseline rules", "draft": configuration}
    )
    run = library.run_experiment(
        store,
        OWNER,
        experiment["id"],
        {"revision": experiment["revision"], "request_id": "native-baseline-run"},
    )
    run_worker(store)
    kept = decisions.save_direct_decision(store, OWNER, experiment["id"], run["job"]["id"], body())
    result = chosen.choose(
        store,
        OWNER,
        experiment["id"],
        {
            "revision": run["experiment"]["revision"],
            "request_id": "choose-native-baseline",
            "decision_id": kept["decision"]["id"],
            "event_id": kept["event"]["id"],
            "name": "Chosen baseline",
        },
    )
    assert result["choice"]["trial_number"] is None
    version = library.get_version(store, OWNER, experiment["id"], result["choice"]["version"]["id"])
    assert version["parent_trial_id"] is None
    assert version["portfolio"]["validation"] == configuration["portfolio"]["validation"]
    _references(store.engine)


def test_concurrent_choose_retries_are_exactly_once_and_history_is_bounded(
    app, client, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    store, experiment, _, _, _, _, payload = ready(app, client, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(
            pool.map(lambda _: chosen.choose(store, OWNER, experiment, payload), range(2))
        )
    assert answers[0]["choice"]["id"] == answers[1]["choice"]["id"]
    assert sorted(answer["reused"] for answer in answers) == [False, True]
    monkeypatch.setattr(chosen, "MAX_CHOICES", 1)
    with pytest.raises(ValueError, match="history limit"):
        chosen.choose(
            store,
            OWNER,
            experiment,
            {**payload, "revision": answers[0]["revision"], "request_id": "another-chosen-request"},
        )
    assert chosen.history(store, OWNER, experiment)["total"] == 1


def test_original_result_change_during_choice_is_fenced(app, client, monkeypatch):
    store, experiment, job, _, _, _, payload = ready(app, client, monkeypatch)
    original = chosen._prepared_rules

    def changed(*args):
        prepared = original(*args)
        with store.sessions.begin() as db:
            db.get(ResearchJob, job).result_artifact = "f" * 64
        return prepared

    monkeypatch.setattr(chosen, "_prepared_rules", changed)
    with pytest.raises(ValueError, match="original result changed"):
        chosen.choose(store, OWNER, experiment, payload)
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchChosenSetup)) == 0


def test_native_chosen_routes_expose_choice_preview_use_and_replay(app, client, monkeypatch):
    store, experiment, _, _, _, kept, payload = ready(app, client, monkeypatch)
    path = f"/scanner-research/api/library/experiments/{experiment}/chosen-setup"
    eligibility = client.get(
        path, query_string={"decision_id": kept["decision"]["id"], "event_id": kept["event"]["id"]}
    )
    assert eligibility.status_code == 200 and eligibility.json["eligibility"]["available"]
    assert client.get(path, query_string={"unexpected": "field"}).status_code == 400
    response = client.post(path, json=payload)
    assert response.status_code == 201, response.json
    choice = response.json["choice"]
    assert client.get(path + "/history").json["total"] == 1
    preview = client.post(path + "/preview", json={"choice_id": choice["id"]})
    assert preview.status_code == 200, preview.json
    use = client.post(
        path + "/use",
        json={
            "revision": response.json["revision"],
            "request_id": "native-use-choice",
            "choice_id": choice["id"],
        },
    )
    assert use.status_code in (200, 201), use.json
    replay = client.post(
        path + "/replay",
        json={
            "revision": use.json["revision"],
            "request_id": "native-replay-choice",
            "choice_id": choice["id"],
        },
    )
    assert replay.status_code == 201, replay.json
    run_worker(store)
    assert service.get_job(store, OWNER, replay.json["job"]["id"]).status == "completed"
