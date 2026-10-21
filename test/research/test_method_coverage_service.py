"""Owned metadata-only method inspection; never load candles or recalculate."""

# ruff: noqa: F811 -- isolated shared Flask fixtures

import copy
import gc
import json
import time
import tracemalloc

import pytest
from sqlalchemy import event
from test_jobs import app, client  # noqa: F401

from database.research_db import ResearchExperiment, ResearchJob
from services import research_method_coverage as view
from services import scanner_research_service as artifacts

OWNER = "research-test"
JOB = "a" * 32
IDENTITY = "b" * 64


def path(job_id=JOB):
    return f"/scanner-research/api/portfolio/jobs/{job_id}/method-coverage"


@pytest.fixture(scope="module")
def metadata():
    from test_method_coverage import prepared_metadata

    return prepared_metadata()


def seed(app, metadata, *, status="paused", recipe="automatic-hypotheses-v8"):
    mandate, calculation, manifest = copy.deepcopy(metadata)
    store = app.extensions["research_store"]
    state = {
        "phase": "calculation",
        "inputs_artifact": mandate["source"]["inputs_artifact"],
        "research_mandate": mandate,
        "calculation": calculation,
        "development_evidence": manifest,
    }
    envelope = {"identity": IDENTITY, "state": state}
    digest = artifacts.save_artifact(store, envelope)
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=JOB,
                owner=OWNER,
                source_id="c" * 64,
                config="{}",
                status=status,
                created_at=time.time(),
                updated_at=time.time(),
            )
        )
        db.add(
            ResearchExperiment(
                job_id=JOB,
                kind="portfolio_optimize",
                identity=IDENTITY,
                checkpoint=digest,
                specification=json.dumps(
                    {"portfolio": {"automatic_research": {"version": recipe}}}
                ),
            )
        )
    return store, envelope, digest


def publish(store, envelope):
    digest = artifacts.save_artifact(store, envelope)
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, JOB).checkpoint = digest
    return digest


def test_actual_metadata_builder_through_owned_http_view(app, client, metadata, monkeypatch):
    store, envelope, digest = seed(app, metadata)
    old = copy.deepcopy(envelope)
    files_before = set((store.root / "artifacts").iterdir())
    read = artifacts.read_artifact
    seen = []

    def bounded_read(actual_store, actual_digest, **kwargs):
        assert actual_digest == digest  # No inputs or retained account-history shard.
        assert kwargs["max_bytes"] == view.MAX_ENVELOPE_BYTES
        seen.append(actual_digest)
        return read(actual_store, actual_digest, **kwargs)

    monkeypatch.setattr(artifacts, "read_artifact", bounded_read)
    response = client.get(path())
    assert response.status_code == 200 and response.json["available"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json["coverage"]["registry"]["family_count"] == 32
    assert (
        response.json["coverage"]["source"]["checkpoint_id"]
        == envelope["state"]["calculation"]["checkpoint_id"]
    )
    assert seen == [digest] and envelope == old
    assert set((store.root / "artifacts").iterdir()) == files_before


def test_execution_method_metadata_uses_sixty_four_trial_fits_without_price_reads(
    app, client, monkeypatch
):
    from test_method_coverage import prepared_metadata

    metadata = prepared_metadata(version="automatic-hypotheses-v10", proposals=40)
    store, _, digest = seed(app, metadata, recipe="automatic-hypotheses-v10")
    original_read = artifacts.read_artifact

    def bounded_read(actual_store, actual_digest, **kwargs):
        assert actual_store is store and actual_digest == digest
        return original_read(actual_store, actual_digest, **kwargs)

    monkeypatch.setattr(artifacts, "read_artifact", bounded_read)
    response = client.get(path())
    assert response.status_code == 200 and response.json["available"], response.json
    coverage = response.json["coverage"]
    assert coverage["totals"]["proposals"] == 40
    assert coverage["source"]["recipe_version"] == "automatic-hypotheses-v10"
    assert all(fit["proposal_limit"] == 64 for fit in coverage["fits"])
    assert coverage["registry"]["hypothesis_count"] == 108


def test_completed_execution_metadata_requires_sixty_four_trials_in_every_fit():
    calculation = {
        "version": "automatic-hypotheses-v10",
        "fits": {name: {"trials": [None] * 64} for name in ("fit1", "fit2", "fit3", "search")},
        "folds": [None] * 3,
        "selection": {"saved": True},
        "final_report": {"saved": True},
    }
    assert view._complete(calculation)
    calculation["fits"]["search"]["trials"] = [None] * 48
    assert not view._complete(calculation)


def test_auth_and_owner_checked_before_any_metadata_read(app, client, metadata, monkeypatch):
    seed(app, metadata)
    monkeypatch.setattr(
        artifacts, "read_artifact", lambda *a, **k: pytest.fail("Read other owner's metadata")
    )
    assert app.test_client().get(path()).status_code == 401
    with client.session_transaction() as session:
        session["user"] = "another-account"
    assert client.get(path()).status_code == 404


@pytest.mark.parametrize(
    "query", ["extra=1", "expected_checkpoint=no", "expected_checkpoint=0&expected_checkpoint=1"]
)
def test_method_queries_are_bounded_and_unambiguous(app, client, metadata, query):
    seed(app, metadata)
    assert client.get(path() + "?" + query).status_code == 400


def test_old_recipe_and_missing_checkpoint_do_not_guess_coverage(
    app, client, metadata, monkeypatch
):
    store, _, _ = seed(app, metadata, recipe="automatic-hypotheses-v7")
    monkeypatch.setattr(
        artifacts, "read_artifact", lambda *a, **k: pytest.fail("Loaded old recipe")
    )
    assert client.get(path()).json["available"] is False
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, JOB).checkpoint = None
    assert client.get(path()).json["available"] is False


@pytest.mark.parametrize(
    "problem",
    [
        "identity",
        "inputs",
        "missing-mandate",
        "missing-manifest",
        "calculation",
        "specification",
        "fits",
    ],
)
def test_unverifiable_metadata_has_no_invented_counts(app, client, metadata, problem):
    store, envelope, _ = seed(app, metadata, status="completed" if problem == "fits" else "paused")
    if problem == "identity":
        envelope["identity"] = "d" * 64
    elif problem == "inputs":
        envelope["state"]["inputs_artifact"] = "d" * 64
    elif problem.startswith("missing-"):
        envelope["state"].pop(
            "research_mandate" if problem == "missing-mandate" else "development_evidence"
        )
    elif problem == "calculation":
        envelope["state"]["calculation"] = []
    elif problem == "fits":
        envelope["state"]["calculation"]["fits"] = []
    else:
        with store.sessions.begin() as db:
            db.get(ResearchExperiment, JOB).specification = "[]"
    publish(store, envelope)
    response = client.get(path())
    assert response.status_code == 200 and not response.json["available"]
    assert response.json["coverage"] is None


def test_completed_run_cannot_present_a_partial_fit_as_complete(app, client, metadata):
    seed(app, metadata, status="completed")
    response = client.get(path())
    assert response.status_code == 200 and not response.json["available"]


def test_expected_checkpoint_is_fenced_before_read(app, client, metadata, monkeypatch):
    seed(app, metadata)
    monkeypatch.setattr(
        artifacts, "read_artifact", lambda *a, **k: pytest.fail("Read stale checkpoint")
    )
    response = client.get(path() + "?expected_checkpoint=" + "0" * 64)
    assert response.status_code == 409 and response.json["code"] == "method_coverage_changed"


@pytest.mark.parametrize("change", ["checkpoint", "result", "status", "owner"])
def test_publication_races_do_not_mix_method_evidence(app, client, metadata, monkeypatch, change):
    store, _, _ = seed(app, metadata)
    original = artifacts.read_artifact

    def concurrent(*args, **kwargs):
        value = original(*args, **kwargs)
        with store.sessions.begin() as db:
            if change == "checkpoint":
                db.get(ResearchExperiment, JOB).checkpoint = "d" * 64
            else:
                job = db.get(ResearchJob, JOB)
                if change == "result":
                    job.result_artifact = "d" * 64
                elif change == "status":
                    job.status = "running"
                else:
                    job.owner = "another-account"
        return value

    monkeypatch.setattr(artifacts, "read_artifact", concurrent)
    response = client.get(path())
    assert response.status_code == (404 if change == "owner" else 409)


def test_100_success_and_rejection_reads_release_sessions_files_and_memory(
    app, metadata, monkeypatch, record_property
):
    import psutil

    store, envelope, digest = seed(app, metadata)
    opened, closed = [], []
    event.listen(store.engine, "connect", lambda *args: opened.append(1))
    event.listen(store.engine, "close", lambda *args: closed.append(1))
    original = artifacts.read_artifact
    reject = False

    def read(*args, **kwargs):
        value = original(*args, **kwargs)
        if reject:
            value["identity"] = "d" * 64
        return value

    monkeypatch.setattr(artifacts, "read_artifact", read)
    assert view.read(store, OWNER, JOB)["available"]
    process = psutil.Process()
    handles = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    gc.collect()
    tracemalloc.start()
    baseline = tracemalloc.take_snapshot()
    for _ in range(100):
        reject = False
        assert view.read(store, OWNER, JOB)["available"]
        reject = True
        assert not view.read(store, OWNER, JOB)["available"]
    gc.collect()
    retained = sum(
        max(0, stat.size_diff)
        for stat in tracemalloc.take_snapshot().compare_to(baseline, "lineno")
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    after = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    record_property("handles_before", handles)
    record_property("handles_after", after)
    record_property("retained_bytes", retained)
    record_property("peak_bytes", peak)
    assert len(opened) == len(closed)
    assert after <= handles + 1 and retained < 512 * 1024
    assert artifacts.read_artifact(store, digest)["state"] == envelope["state"]


@pytest.mark.timeout(300)
def test_real_seeded_service_history_has_coverage_for_all_four_fits(app, client, monkeypatch):
    """Real Optuna and immutable histories; synthetic accounts isolate metadata."""
    from test_automatic_walk_forward import AccountEngine, prepared, synthetic_prepare_gate

    from services.research_portfolio import run

    value = prepared()
    store = app.extensions["research_store"]
    monkeypatch.setattr("research.automatic_walk_forward.prepare_gate", synthetic_prepare_gate)
    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *a: None)
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: None)
    engine = AccountEngine(value)
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", engine)
    saved = None

    def checkpoint(state, _counts):
        nonlocal saved
        saved = state

    result, _ = run(
        store,
        OWNER,
        value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        checkpoint=checkpoint,
        progress=lambda *a: None,
        cancelled=lambda: None,
        job_id=JOB,
        defer_study_analysis=True,
    )
    assert result["development_evidence"]["status"] == "complete"
    metadata = saved["research_mandate"], saved["calculation"], saved["development_evidence"]
    store, envelope, digest = seed(app, metadata, status="completed")
    native_calls = len(engine.calls)
    original = artifacts.read_artifact

    def only_metadata(actual_store, actual_digest, **kwargs):
        assert actual_digest == digest, "Method inspection loaded candles or a history shard"
        return original(actual_store, actual_digest, **kwargs)

    monkeypatch.setattr(artifacts, "read_artifact", only_metadata)
    response = client.get(path())
    assert response.status_code == 200 and response.json["available"], response.json
    coverage = response.json["coverage"]
    assert coverage["totals"]["proposals"] == 192
    assert [fit["id"] for fit in coverage["fits"]] == ["fit1", "fit2", "fit3", "search"]
    assert all(fit["status"] == "complete" and fit["proposals"] == 48 for fit in coverage["fits"])
    assert coverage["totals"]["evaluated"] == sum(
        len(fit["candidates"]) for fit in saved["calculation"]["fits"].values()
    )
    assert coverage["totals"]["retained_histories"] == coverage["totals"]["evaluated"]
    assert len(engine.calls) == native_calls
