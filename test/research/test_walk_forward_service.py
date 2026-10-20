"""Native service persistence and exact dates around the expanding-fit worker."""

from contextlib import AbstractContextManager
from copy import deepcopy

import pytest
from test_automatic_walk_forward import AccountEngine, Stopped, prepared, synthetic_gate

from database.research_db import ResearchStore
from research.automatic_protocol import slice_period
from research.evaluation_basis import build_evaluation_basis
from research.report_contract import fingerprint
from services import scanner_research_service as artifacts
from services.research_development_evidence import DevelopmentEvidenceWriter
from services.research_partial_study import _search_inputs, _validated_rows
from services.research_portfolio import run

pytestmark = pytest.mark.timeout(300)


class SyntheticContext(AbstractContextManager):
    apply = staticmethod(synthetic_gate)

    def __exit__(self, *_error):
        return None


@pytest.fixture
def store(tmp_path):
    instance = ResearchStore(tmp_path / "research")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


def test_service_keeps_all_fit_histories_mandate_and_active_dates_on_resume(store, monkeypatch):
    value = prepared()
    spec = {"portfolio": value["portfolio"], "versions": value["versions"]}
    engine = AccountEngine(value)
    latest = None
    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *a: None)
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: None)
    monkeypatch.setattr(
        "research.automatic_walk_forward.prepare_gate", lambda _: SyntheticContext()
    )

    def evaluate(*args, **kwargs):
        assert latest["research_mandate"]["stage"] == "pre_outcome"
        return engine(*args, **kwargs)

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", evaluate)

    def checkpoint(state, counts):
        nonlocal latest
        latest = state
        calculation = state.get("calculation") or {}
        if (
            calculation.get("active_fit") == "fit2"
            and len(calculation["fits"]["fit2"]["trials"]) == 3
        ):
            raise Stopped()

    args = {
        "progress": lambda *a: None,
        "cancelled": lambda: None,
        "job_id": "isolated-v8",
        "defer_study_analysis": True,
    }
    with pytest.raises(Stopped):
        run(store, "test-owner", value, spec, checkpoint=checkpoint, **args)
    stopped = deepcopy(latest)
    calls = len(engine.calls)
    mandate = deepcopy(stopped["research_mandate"])
    assert stopped["development_evidence"]["status"] == "collecting"
    selected, calculation, scope, automatic, completed = _search_inputs(
        value, stopped["calculation"]
    )
    rows, counts = _validated_rows(selected, calculation, completed)
    assert scope == "fit2" and automatic and not completed
    assert counts["proposed"] == 3 and rows
    assert (
        selected["snapshot"]["sessions"][-1] == value["automatic_recipe"]["periods"]["fit2"]["to"]
    )
    assert (
        selected["snapshot"]["sessions"][-1] < value["automatic_recipe"]["periods"]["search"]["to"]
    )

    def resume_checkpoint(state, counts):
        nonlocal latest
        latest = state

    result, returned = run(
        store, "test-owner", value, spec, saved=stopped, checkpoint=resume_checkpoint, **args
    )
    assert returned == value
    assert result["research_mandate"] == mandate
    manifest = result["development_evidence"]
    assert manifest["status"] == "complete"
    assert len(engine.calls) > calls
    assert len(engine.calls) == latest["calculation"]["simulations"] <= 320
    assert {row["period"] for row in manifest["entries"].values()} == {
        "fit1",
        "fit2",
        "fit3",
        "check1",
        "check2",
        "check3",
        "search",
    }
    assert all(row["status"] == "retained" for row in manifest["entries"].values())
    assert all(row["period"] != "final" for row in manifest["entries"].values())
    # A v8 checkpoint cannot silently take the legacy no-history recovery path.
    damaged = {key: item for key, item in stopped.items() if key != "development_evidence"}
    with pytest.raises(ValueError, match="manifest"):
        run(store, "test-owner", value, spec, saved=damaged, checkpoint=resume_checkpoint, **args)


def test_actual_vectorbt_accounts_use_exact_purged_training_test_and_final_windows(store):
    from research.connectors.vectorbt_portfolio import evaluate

    value = prepared()
    source = artifacts.save_artifact(store, value)
    binding = {
        "inputs_artifact": source,
        "search_identity": fingerprint(
            {
                "recipe": value["automatic_recipe"],
                "stock": value["automatic_stock_market_context"],
            }
        ),
    }
    writer = DevelopmentEvidenceWriter(store, value, binding=binding, max_evaluations=320)
    for name, bounds in value["automatic_recipe"]["periods"].items():
        current = slice_period(value, name)
        assert current["snapshot"]["sessions"][0] == bounds["from"]
        assert current["snapshot"]["sessions"][-1] == bounds["to"]
        report = evaluate(current["strategies"], current["snapshot"], value["portfolio"]["capital"])
        report["evaluation_basis"] = build_evaluation_basis(
            current, period="evaluation" if name == "final" else "selection"
        )
        assert report["config"]["initial_capital"] == value["portfolio"]["capital"]
        assert len(report["equity_curve"]) == bounds["sessions"]
        if name != "final":
            row = writer.retain(
                report,
                evaluation_id=name,
                search_identity=binding["search_identity"],
                settings=report["strategies"],
                hypothesis={"id": "none"},
                period=name,
                stress=False,
                expected_timeline=current["snapshot"]["sessions"],
            )
            assert row["status"] == "retained"
    assert len(writer.snapshot(complete=True)["entries"]) == 7
