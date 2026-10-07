"""Only committed exact trials are inspectable; inspection never resumes work."""

# ruff: noqa: F811 -- shared isolated Flask fixtures
import copy
import json

import pytest
from test_jobs import app, client
from test_optuna_portfolio import engine, search_request

from database.research_db import ResearchExperiment, ResearchJob, ResearchStudyProposal
from research.connectors.optuna_portfolio import _fingerprint, run_search
from research.connectors.registry import policy_for_request
from services import research_partial_study as partial
from services import scanner_research_service as service

JOB = "c" * 32
OWNER = "research-test"


def publish(store, evidence, calculation, *, status="paused", phase="calculation"):
    spec = {"portfolio": evidence["portfolio"], "versions": evidence["versions"]}
    inputs = service.save_artifact(store, evidence)
    envelope = {
        "identity": "e" * 64,
        "policy_version": policy_for_request(evidence["snapshot"], spec),
        "state": {"phase": phase, "inputs_artifact": inputs, "calculation": calculation},
    }
    digest = service.save_artifact(store, envelope)
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=JOB,
                owner=OWNER,
                source_id="a" * 32,
                config="{}",
                status=status,
                progress=70,
                created_at=1,
                updated_at=2,
            )
        )
        db.add(
            ResearchExperiment(
                job_id=JOB,
                kind="portfolio_optimize",
                identity=envelope["identity"],
                specification=json.dumps(spec),
                checkpoint=digest,
                counts="{}",
            )
        )
    return digest, envelope


@pytest.fixture
def saved(app, search_request):
    request = copy.deepcopy(search_request)
    request["specification"]["trials"] = 4
    checkpoints = []
    run_search(**request, evaluate=engine, checkpoint=lambda value, _: checkpoints.append(value))
    evidence = {
        "portfolio": {
            "engine": "vectorbt",
            "capital": request["capital"],
            "optimization": request["specification"],
        },
        "strategies": request["strategies"],
        "snapshot": request["snapshot"],
        "versions": request["execution"],
    }
    store = app.extensions["research_store"]
    digest, envelope = publish(store, evidence, checkpoints[1])
    return store, evidence, checkpoints, digest, envelope


def get(client, query=""):
    return client.get(f"/scanner-research/api/portfolio/jobs/{JOB}/partial-study{query}")


def replace(store, envelope):
    digest = service.save_artifact(store, envelope)
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, JOB).checkpoint = digest
    return digest


@pytest.mark.parametrize("status", ["paused", "cancelled", "failed", "interrupted"])
def test_exact_committed_trials_without_mutation_or_report(saved, client, monkeypatch, status):
    store, _, checkpoints, digest, envelope = saved
    with store.sessions.begin() as db:
        db.get(ResearchJob, JOB).status = status
    before = service.read_artifact(store, digest)
    monkeypatch.setattr(
        "research.connectors.optuna_portfolio.run_search",
        lambda *a, **k: pytest.fail("Inspection executed Optuna"),
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a, **k: pytest.fail("Inspection used a broker"),
    )
    response = get(client)
    assert response.status_code == 200 and response.json["available"], response.json
    assert response.headers["Cache-Control"] == "private, no-store"
    receipt = response.json
    assert receipt["counts"] == {"proposed": 2, "evaluated": 2, "reused": 0, "rejected": 0}
    assert receipt["rows"][0]["summary"] == checkpoints[1]["rows"][0]["summary"]
    assert receipt["rows"][0]["strategies"] == checkpoints[1]["rows"][0]["strategies"]
    assert receipt["checkpoint_artifact"] == digest
    assert not {
        "recommendation_id",
        "winner_report",
        "selected_reports",
        "report",
        "result",
        "ledger",
    }.intersection(receipt)
    assert service.read_artifact(store, digest) == before == envelope
    assert service.get_job(store, OWNER, JOB).status == status
    assert service.get_job(store, OWNER, JOB).result_artifact is None


def test_auth_owner_and_terminal_gate(saved, app, client):
    assert (
        app.test_client()
        .get(f"/scanner-research/api/portfolio/jobs/{JOB}/partial-study")
        .status_code
        == 401
    )
    with client.session_transaction() as session:
        session["user"] = "other"
    assert get(client).status_code == 404
    with client.session_transaction() as session:
        session["user"] = OWNER
    store = saved[0]
    for status in ("queued", "running", "completed"):
        with store.sessions.begin() as db:
            db.get(ResearchJob, JOB).status = status
        assert not get(client).json["available"]


def test_pagination_pins_immutable_snapshot(saved, client):
    store, _, checkpoints, digest, envelope = saved
    first = get(client, "?limit=1").json
    assert first["next_offset"] == 1 and first["total_rows"] == 2
    second = get(client, f"?limit=1&offset=1&expected_checkpoint={digest}").json
    assert second["rows"][0]["trial_number"] == 1 and second["next_offset"] is None
    assert get(client, "?offset=1").status_code == 400
    for query in (
        "?limit=101",
        "?limit=0",
        "?limit=-1",
        "?offset=0.5",
        "?limit=1&limit=2",
        "?unrecognized=1",
        "?expected_checkpoint=bad",
    ):
        assert get(client, query).status_code == 400
    envelope["state"]["calculation"] = checkpoints[2]
    replace(store, envelope)
    changed = get(client, f"?offset=1&expected_checkpoint={digest}")
    assert changed.status_code == 409 and changed.json["code"] == "partial_study_checkpoint_changed"


@pytest.mark.parametrize("what", ["none", "prices", "initial", "missing"])
def test_before_search_has_no_fabricated_trials(saved, client, what):
    store, _, _, digest, envelope = saved
    if what == "none":
        with store.sessions.begin() as db:
            db.get(ResearchExperiment, JOB).checkpoint = None
    elif what == "missing":
        with store.sessions.begin() as db:
            db.get(ResearchExperiment, JOB).checkpoint = "f" * 64
    else:
        if what == "prices":
            envelope["state"]["phase"] = "prices"
        else:
            envelope["state"]["calculation"] = None
        replace(store, envelope)
    receipt = get(client).json
    assert not receipt["available"] and receipt["reason"] and receipt["rows"] == []


@pytest.mark.parametrize("what", ["hash", "summary", "uncommitted", "params", "identity", "inputs"])
def test_checkpoint_hash_and_scientific_binding_are_verified(saved, client, what):
    store, _, _, _, envelope = saved
    calculation = envelope["state"]["calculation"]
    if what == "hash":
        calculation["rows"][0]["summary"]["net_return_pct"] += 1
    elif what == "summary":
        calculation["rows"][0]["summary"]["net_return_pct"] += 1
        calculation["checkpoint_id"] = _fingerprint(
            {k: v for k, v in calculation.items() if k != "checkpoint_id"}
        )
    elif what == "uncommitted":
        calculation["trials"] = calculation["trials"][:1]
        calculation["checkpoint_id"] = _fingerprint(
            {k: v for k, v in calculation.items() if k != "checkpoint_id"}
        )
    elif what == "params":
        calculation["trials"][0]["params"]["breakout.target_pct"] = 500
        calculation["checkpoint_id"] = _fingerprint(
            {k: v for k, v in calculation.items() if k != "checkpoint_id"}
        )
    elif what == "identity":
        envelope["identity"] = "f" * 64
    else:
        evidence = service.read_artifact(store, envelope["state"]["inputs_artifact"])
        evidence["snapshot"]["bars"]["FIRST"]["2026-01-06"]["close"] += 1
        envelope["state"]["inputs_artifact"] = service.save_artifact(store, evidence)
    replace(store, envelope)
    receipt = get(client).json
    assert not receipt["available"] and "verified" in receipt["reason"]


@pytest.mark.parametrize("stage", ["search", "selection", "final"])
def test_automatic_inspection_returns_only_search_evidence(app, client, stage):
    from test_automatic_research import Engine, execute, prepared

    evidence = prepared()
    stop = {
        "search": lambda state: (
            state.get("search_checkpoint") and len(state["search_checkpoint"]["trials"]) == 3
        ),
        "selection": lambda state: state["selection"] is not None,
        "final": lambda state: state["final_report"] is not None,
    }[stage]
    _, calculation = execute(evidence, Engine(evidence), stop=stop)
    store = app.extensions["research_store"]
    publish(store, evidence, calculation, status="cancelled")
    receipt = get(client).json
    assert receipt["available"] and receipt["automatic"] and receipt["scope"] == "search", receipt
    source = calculation["search_checkpoint"] or calculation["search_result"]["experiment"]
    assert receipt["counts"]["proposed"] == len(source["trials"])
    assert not {"selection", "final_report", "evaluations", "selected_report"}.intersection(receipt)
    assert [row["config_id"] for row in receipt["rows"]] == [
        row["config_id"] for row in sorted(source["rows"], key=lambda row: row["trial_number"])
    ][:25]


def test_automatic_baseline_only_is_not_search_result(app, client):
    from test_automatic_research import Engine, execute, prepared

    evidence = prepared()
    _, calculation = execute(
        evidence, Engine(evidence), stop=lambda state: state["simulations"] == 1
    )
    publish(app.extensions["research_store"], evidence, calculation)
    receipt = get(client).json
    assert not receipt["available"] and receipt["rows"] == []


def test_automatic_market_condition_search_checkpoint(app, client):
    from test_automatic_conditions import execute, prepared
    from test_automatic_research import Engine

    evidence = prepared()
    _, calculation = execute(
        evidence,
        Engine(evidence),
        stop=lambda state: (
            state.get("search_checkpoint") and len(state["search_checkpoint"]["trials"]) == 2
        ),
    )
    publish(app.extensions["research_store"], evidence, calculation)
    receipt = get(client).json
    assert receipt["available"] and receipt["automatic"] and receipt["scope"] == "search", receipt
    assert receipt["counts"]["proposed"] == 2


def test_reused_proposals_have_one_row_and_no_extra_evaluation(app, client, search_request):
    request = copy.deepcopy(search_request)
    request["strategies"][0]["search"] = {"target_pct": {"min": 2, "max": 4, "step": 2}}
    request["strategies"][1]["search"] = {}
    request["specification"].update(sampler="tpe", trials=8)
    checkpoints = []
    run_search(**request, evaluate=engine, checkpoint=lambda value, _: checkpoints.append(value))
    evidence = {
        "portfolio": {
            "engine": "vectorbt",
            "capital": request["capital"],
            "optimization": request["specification"],
        },
        "strategies": request["strategies"],
        "snapshot": request["snapshot"],
        "versions": request["execution"],
    }
    publish(app.extensions["research_store"], evidence, checkpoints[-1])
    receipt = get(client).json
    assert receipt["available"], receipt
    assert receipt["counts"] == {"proposed": 8, "evaluated": 2, "reused": 6, "rejected": 0}
    assert receipt["total_rows"] == 2


def test_rejected_allocations_are_counted_without_fabricated_results(app, client, search_request):
    request = copy.deepcopy(search_request)
    request["strategies"][0]["search"] = {"allocation_pct": {"min": 25, "max": 100, "step": 25}}
    request["strategies"][1]["search"] = {"allocation_pct": {"min": 50, "max": 100, "step": 25}}
    request["specification"]["trials"] = 1
    checkpoints = []
    for seed in range(10):
        request["specification"]["seed"] = seed
        try:
            run_search(
                **request, evaluate=engine, checkpoint=lambda value, _: checkpoints.append(value)
            )
        except ValueError as error:
            assert "No feasible" in str(error)
            break
    else:
        pytest.fail("No seeded rejected proposal found")
    evidence = {
        "portfolio": {
            "engine": "vectorbt",
            "capital": request["capital"],
            "optimization": request["specification"],
        },
        "strategies": request["strategies"],
        "snapshot": request["snapshot"],
        "versions": request["execution"],
    }
    publish(app.extensions["research_store"], evidence, checkpoints[-1], status="failed")
    receipt = get(client).json
    assert receipt["available"] and receipt["rows"] == [] and receipt["total_rows"] == 0
    assert receipt["counts"] == {"proposed": 1, "evaluated": 0, "reused": 0, "rejected": 1}


def test_concurrent_checkpoint_publication_fails_closed(saved, client, monkeypatch):
    store, _, checkpoints, _, envelope = saved
    validate = partial._validated_rows

    def changed(*args):
        result = validate(*args)
        envelope["state"]["calculation"] = checkpoints[2]
        replace(store, envelope)
        return result

    monkeypatch.setattr(partial, "_validated_rows", changed)
    assert get(client).status_code == 409


def test_legacy_checkpoint_without_search_metadata_remains_readable(saved, client):
    store, _, _, _, envelope = saved
    calculation = envelope["state"]["calculation"]
    for field in ("search_identity_version", "search_identity", "specification", "proposal_budget"):
        calculation.pop(field)
    calculation["checkpoint_id"] = _fingerprint(
        {k: v for k, v in calculation.items() if k != "checkpoint_id"}
    )
    replace(store, envelope)
    assert get(client).json["available"]


def test_concurrent_resume_fails_closed(saved, client, monkeypatch):
    store = saved[0]
    validate = partial._validated_rows

    def resumed(*args):
        result = validate(*args)
        with store.sessions.begin() as db:
            db.get(ResearchJob, JOB).status = "queued"
        return result

    monkeypatch.setattr(partial, "_validated_rows", resumed)
    assert get(client).status_code == 409


def test_repeated_success_and_integrity_failure_release_resources(saved, client):
    import gc

    import psutil

    process = psutil.Process()
    for _ in range(5):
        assert get(client).json["available"]
    gc.collect()
    handles = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    rss = process.memory_info().rss
    for _ in range(100):
        assert get(client).json["available"]
    store, _, _, _, envelope = saved
    envelope["identity"] = "f" * 64
    replace(store, envelope)
    for _ in range(100):
        assert not get(client).json["available"]
    gc.collect()
    after = process.num_handles() if hasattr(process, "num_handles") else process.num_fds()
    assert after <= handles + 2
    assert process.memory_info().rss - rss < 8 * 1024 * 1024


def test_uncommitted_operational_observation_is_not_trial_evidence(saved, client):
    store = saved[0]
    with store.sessions.begin() as db:
        db.add(
            ResearchStudyProposal(
                execution_id="a" * 32,
                job_id=JOB,
                number=2,
                config_id="f" * 64,
                params="{}",
                state="evaluated",
                value=999,
                reused=False,
                started_at=1,
                finished_at=2,
                observed_at=2,
                checkpointed=False,
            )
        )
    receipt = get(client).json
    assert receipt["available"] and receipt["counts"]["proposed"] == 2
    assert all(row["config_id"] != "f" * 64 for row in receipt["rows"])


def test_native_reserved_study_inspects_only_selection_period(app, client, monkeypatch):
    from test_study_continuation import setup

    store, _, job_id = setup(app, client, monkeypatch)
    with store.sessions.begin() as db:
        db.get(ResearchJob, job_id).status = "paused"
    receipt = partial.read(store, OWNER, job_id)
    assert receipt["available"] and receipt["scope"] == "selection", receipt
    assert receipt["counts"]["proposed"] == 2
    assert not {"reserved_evaluation", "validation", "ledger", "winner_report"}.intersection(
        receipt
    )


@pytest.mark.parametrize(
    "field,value", [("state", None), ("calculation", []), ("calculation", "invalid")]
)
def test_malformed_checkpoint_shapes_are_unavailable(saved, client, field, value):
    store, _, _, _, envelope = saved
    if field == "state":
        envelope[field] = value
    else:
        envelope["state"][field] = value
    replace(store, envelope)
    response = get(client)
    assert response.status_code == 200 and not response.json["available"]
