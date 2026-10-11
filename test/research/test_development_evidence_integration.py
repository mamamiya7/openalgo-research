"""Real seeded Optuna retains development histories without changing selection."""

from copy import deepcopy

import pytest
from test_automatic_hypotheses import Engine, Stopped, prepared

from database.research_db import ResearchStore
from research.automatic_hypotheses import (
    JOINT_VERSION,
    TIMING_VERSION,
    TRAILING_VERSION,
    policy_for_version,
    run,
)
from research.automatic_protocol import slice_period
from research.connectors.vectorbt_portfolio import _curve
from research.evaluation_basis import build_evaluation_basis
from research.report_contract import fingerprint
from services import scanner_research_service as artifacts
from services.research_development_evidence import DevelopmentEvidenceWriter


@pytest.fixture
def store(tmp_path):
    instance = ResearchStore(tmp_path / "research")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


class AccountEngine(Engine):
    def __call__(self, rows, snapshot, capital, progress=None):
        report = super().__call__(rows, snapshot, capital, progress)
        keys = snapshot["sessions"]
        final = capital * (1 + report["summary"]["net_return_pct"] / 100)
        equity = [capital + (final - capital) * (i + 1) / len(keys) for i in range(len(keys))]
        report["config"] = {"initial_capital": capital}
        report["equity_curve"] = _curve(keys, equity, equity, [0] * len(keys), capital, False)
        return report


def writer_for(store, value, saved=None):
    source = artifacts.save_artifact(store, value)
    version = value["automatic_recipe"]["version"]
    stock = value.get(
        "automatic_stock_market_context"
        if version in (JOINT_VERSION, TRAILING_VERSION, TIMING_VERSION)
        else "automatic_stock_features"
    )
    binding = {
        "inputs_artifact": source,
        "search_identity": fingerprint({"recipe": value["automatic_recipe"], "stock": stock}),
    }
    return DevelopmentEvidenceWriter(
        store,
        value,
        binding=binding,
        max_evaluations=policy_for_version(value["automatic_recipe"]["version"])["max_simulations"],
        saved=saved,
    )


def execute(value, engine, writer=None, *, saved=None, stop=False):
    checkpoints = []

    def checkpoint(state, counts):
        if writer is not None:
            writer.reconcile(state["evaluations"])
        checkpoints.append(
            {"calculation": deepcopy(state), "manifest": writer.snapshot() if writer else None}
        )
        if stop and state["simulations"] == 3:
            raise Stopped()

    try:
        result = run(
            value,
            evaluate=engine,
            saved=saved,
            checkpoint=checkpoint,
            progress=lambda *args: None,
            cancelled=lambda: None,
            retain_evaluation=writer.retain if writer else None,
            defer_study_analysis=True,
        )
    except Stopped:
        result = None
    return result, checkpoints[-1]


def test_candidate_corpus_pause_resume_preserves_proposals_selection_and_engine_calls(store):
    value = prepared()
    original = deepcopy(value)
    baseline_engine = AccountEngine(value)
    baseline, _ = execute(value, baseline_engine)
    engine = AccountEngine(value)
    writer = writer_for(store, value)
    _, stopped = execute(value, engine, writer, stop=True)
    first_calls = len(engine.calls)
    resumed_writer = writer_for(store, value, saved=stopped["manifest"])
    resumed, checkpoint = execute(value, engine, resumed_writer, saved=stopped["calculation"])
    assert fingerprint(resumed) == fingerprint(baseline)
    assert engine.calls == baseline_engine.calls
    assert first_calls == 3
    assert value == original
    manifest = resumed_writer.snapshot(complete=True)
    assert manifest["status"] == "complete"
    assert len(manifest["entries"]) == sum(
        row["evaluation_basis"]["period"]["kind"] != "evaluation"
        for row in checkpoint["calculation"]["evaluations"].values()
    )
    assert len(manifest["entries"]) == len({call for call in engine.calls if call[0] != "final"})
    assert {row["period"] for row in manifest["entries"].values()} == {"search", "check1", "check2"}
    assert any(row["stress"] for row in manifest["entries"].values())
    assert all(row["status"] == "retained" for row in manifest["entries"].values())
    assert "equity_curve" not in str(checkpoint["manifest"])
    for row in manifest["entries"].values():
        shard = artifacts.read_artifact(store, row["inputs_artifact"], max_bytes=24 * 1024 * 1024)
        assert len(shard["curve"]) == len(shard["timeline"]) == len(shard["simple_returns"])
        assert shard["period"] != "final"


def test_actual_native_vectorbt_curve_is_retained_without_sampling(store):
    from research.connectors.vectorbt_portfolio import evaluate

    value = prepared()
    current = slice_period(value, "search")
    report = evaluate(current["strategies"], current["snapshot"], value["portfolio"]["capital"])
    report["evaluation_basis"] = build_evaluation_basis(current, period="selection")
    writer = writer_for(store, value)
    descriptor = writer.retain(
        report,
        evaluation_id="native-account",
        search_identity=writer.binding["search_identity"],
        settings=report["strategies"],
        hypothesis={"id": "none"},
        period="search",
        stress=False,
        expected_timeline=current["snapshot"]["sessions"],
    )
    assert descriptor["status"] == "retained"
    shard = artifacts.read_artifact(store, descriptor["inputs_artifact"])
    assert shard["curve"] == report["equity_curve"]
    assert len(shard["curve"]) == len(current["snapshot"]["sessions"])


@pytest.mark.parametrize("current_recipe", [False, True])
def test_native_service_freezes_before_scoring_and_preserves_evidence_through_resume(
    store, monkeypatch, current_recipe
):
    from research.research_mandate import verify
    from services.research_portfolio import run as service_run

    if current_recipe:
        from test_automatic_timing import prepared as current_prepared

        value = current_prepared()
    else:
        value = prepared()
    spec = {"portfolio": value["portfolio"], "versions": value["versions"]}
    engine = AccountEngine(value)
    checkpoints = []
    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *args: None)
    monkeypatch.setattr("services.research_stock_features.prepare_context", lambda *a, **kw: a[2])
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *args: None)

    def evaluate(*args, **kwargs):
        assert checkpoints[-1]["research_mandate"]["stage"] == "pre_outcome"
        return engine(*args, **kwargs)

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", evaluate)

    # The preparation checkpoints have a None calculation, unlike worker state.
    def stop_after_three(state, counts):
        checkpoints.append(deepcopy(state))
        if (state.get("calculation") or {}).get("simulations") == 3:
            raise Stopped()

    args = {
        "progress": lambda *a: None,
        "cancelled": lambda: None,
        "job_id": "isolated-job",
        "defer_study_analysis": True,
    }
    with pytest.raises(Stopped):
        service_run(store, "test-owner", value, spec, checkpoint=stop_after_three, **args)
    stopped = checkpoints[-1]
    assert stopped["development_evidence"]["status"] == "collecting"
    assert verify(
        stopped["research_mandate"], evidence=value, inputs_artifact=stopped["inputs_artifact"]
    )

    # Interrupt during the wrapper's preparation checkpoint, before any new
    # engine call. This used to drop the calculation's additional evidence.
    def stop_in_preparation(state, counts):
        checkpoints.append(deepcopy(state))
        assert state["development_evidence"] == stopped["development_evidence"]
        assert state["research_mandate"] == stopped["research_mandate"]
        raise Stopped()

    with pytest.raises(Stopped):
        service_run(
            store, "test-owner", value, spec, saved=stopped, checkpoint=stop_in_preparation, **args
        )
    assert len(engine.calls) == 3
    result, returned_inputs = service_run(
        store,
        "test-owner",
        value,
        spec,
        saved=checkpoints[-1],
        checkpoint=lambda state, counts: checkpoints.append(deepcopy(state)),
        **args,
    )
    assert returned_inputs == value
    assert result["development_evidence"]["status"] == "complete"
    assert result["research_mandate"] == stopped["research_mandate"]
    assert len(engine.calls) <= value["automatic_recipe"]["budget"]["max_simulations"]
    if not current_recipe:
        assert len(engine.calls) == 85


def test_legacy_service_resume_does_not_backfill_or_claim_complete_histories(store, monkeypatch):
    from services.research_portfolio import run as service_run

    value = prepared()
    engine = AccountEngine(value)
    _, legacy = execute(value, engine, stop=True)
    source = artifacts.save_artifact(store, value)
    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *args: None)
    monkeypatch.setattr("services.research_stock_features.prepare_context", lambda *a, **kw: a[2])
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *args: None)
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", engine)
    result, _ = service_run(
        store,
        "test-owner",
        value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        saved={
            "phase": "calculation",
            "inputs_artifact": source,
            "calculation": legacy["calculation"],
        },
        checkpoint=lambda *a: None,
        progress=lambda *a: None,
        cancelled=lambda: None,
        job_id="isolated-legacy",
        defer_study_analysis=True,
    )
    assert len(engine.calls) == 85
    assert "development_evidence" not in result
    assert "research_mandate" not in result
