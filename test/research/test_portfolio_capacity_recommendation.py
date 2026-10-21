"""Positive v13 selection with controlled accounts and a stubbed constraint audit.

Real frozen causal features, Optuna, development-history storage/readback and
recommendation assessment run here. Synthetic account economics and the explicit
audit stub isolate controller selection from native execution correctness; the
separate native fill and constraint-audit suites qualify actual order arithmetic.
This test is not broker verification or evidence of financial edge.
"""

# ruff: noqa: F811
from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_portfolio_capacity_integration import prepared
from test_priority_research import PriorityAccountEngine

from research.connectors.optuna_portfolio import _fingerprint
from research.expanded_stock_market import PreparedJointContext
from research.portfolio import fingerprint
from research.report_contract import settings_identity
from services.research_development_evidence import DevelopmentEvidenceWriter

pytestmark = pytest.mark.timeout(600)


def test_v13_reads_real_histories_qualifies_before_final_and_recovers_exactly(
    app, client, monkeypatch
):
    from services.research_portfolio import run

    value = prepared()
    original = deepcopy(value)
    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: None)
    monkeypatch.setattr("research.automatic_walk_forward.prepare_method_gate", PreparedJointContext)

    # The controlled economics do not produce native order records. Keep this
    # isolated admission stub visible rather than pretending to audit fake fills.
    def supported_audit(report, features):
        hypothesis = report.get("automatic_stock_condition", {}).get("hypothesis", {})
        if hypothesis.get("id") != "none":
            marker = report["evaluation_basis"]["comparison"]["execution"].get("entry_conditions")
            assert marker is not None, (
                "Constraint auditing must follow canonical condition enrichment"
            )
            assert all(row.get("entry_condition") == marker for row in report["strategies"])
        body = {
            "version": "controlled-portfolio-constraint-audit-v1",
            "status": "supported",
            "feature_id": features["id"],
            "report_evidence_id": fingerprint(
                {
                    "summary": report["summary"],
                    "curve": report["equity_curve"],
                    "basis": report["evaluation_basis"],
                    "strategies": report["strategies"],
                }
            ),
        }
        return {**body, "id": fingerprint(body)}

    monkeypatch.setattr("research.portfolio_constraint_evidence.assess", supported_audit)
    real_read = DevelopmentEvidenceWriter.read
    reads = []

    def read_history(self, evaluation_id):
        shard = real_read(self, evaluation_id)
        assert shard is not None, "Controller requested an unavailable saved development account"
        assert shard["period"] != "final"
        declaration = shard["evaluation_basis"]["comparison"]["execution"]["portfolio_features"]
        assert declaration["id"] == value["portfolio_execution_features"]["id"]
        assert all(
            mode["capacity_mode"] == "prior20_broker_reported" for mode in declaration["modes"]
        )
        reads.append((evaluation_id, shard["period"]))
        return shard

    monkeypatch.setattr(DevelopmentEvidenceWriter, "read", read_history)
    saved, final_decisions = None, []
    baseline_id = settings_identity(value["automatic_recipe"]["baseline"])

    def on_call(call):
        if call["period"] == "final":
            calculation = saved["calculation"]
            selected = calculation["selection"]
            checks = calculation["recommendation_checks"]
            assert reads, "v13 must receive the service's real saved-history reader"
            assert checks["status"] == "supported", checks
            assert not selected["is_baseline"]
            constraints = checks["portfolio_constraints"]
            assert constraints["status"] == "supported"
            assert constraints["development_accounts"] == 16
            assert len(constraints["evidence_ids"]) == 16
            assert all(constraints["evidence_ids"])
            final_decisions.append((selected["config_id"], deepcopy(checks)))

    engine = PriorityAccountEngine(value, on_call=on_call)

    def account(rows, snapshot, capital, progress=None):
        report = engine(rows, snapshot, capital, progress)
        if engine.calls[-1]["period"] == "final":
            # Deliberately make the final candidate worse than the reference.
            # This can reject its validation status, never choose new settings.
            loss = 0.4 if engine.calls[-1]["settings_id"] == baseline_id else 0.6
            curve = report["equity_curve"]
            for index, point in enumerate(curve):
                ratio = index / (len(curve) - 1)
                equity = float(capital) * (1 - loss * ratio)
                point.update(equity=equity, cash=equity, drawdown_pct=loss * ratio * 100)
            report["summary"].update(net_return_pct=-loss * 100, max_drawdown_pct=loss * 100)
        return report

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", account)

    def checkpoint(state, counts):
        nonlocal saved
        saved = state

    spec = {"portfolio": value["portfolio"], "versions": value["versions"]}
    kwargs = {
        "progress": lambda *a: None,
        "cancelled": lambda: None,
        "job_id": "c" * 32,
        "defer_study_analysis": True,
    }
    result, _ = run(store, "research-test", value, spec, checkpoint=checkpoint, **kwargs)
    assert value == original
    findings = result["automatic_research"]
    checks = findings["recommendation_checks"]
    assert reads and {period for _, period in reads} >= {"search", "check1", "check2", "check3"}
    assert checks["status"] == "supported", checks
    assert not findings["selected_is_baseline"]
    assert final_decisions and all(
        config_id == findings["selected_config_id"] and before == checks
        for config_id, before in final_decisions
    )
    assert findings["status"] != "supported", "Bad final outcomes must fail validation"
    assert findings["final"]["summary"]["net_return_pct"] == -60
    assert result["development_evidence"]["status"] == "complete"
    assert all(
        row["period"] != "final" for row in result["development_evidence"]["entries"].values()
    )
    assert findings["counts"]["proposals"] == 272
    assert len(engine.calls) <= 384
    assert settings_identity(result["strategies"]) == findings["selected_config_id"]
    completed = deepcopy(saved)
    before_calls = len(engine.calls)
    repeated, _ = run(
        store,
        "research-test",
        value,
        spec,
        saved=deepcopy(completed),
        checkpoint=checkpoint,
        **kwargs,
    )
    assert repeated == result and len(engine.calls) == before_calls

    # Even a checkpoint whose outer self-hash has been recomputed cannot replace
    # the admitted audit inside the selected native report.
    changed = deepcopy(completed)
    calculation = changed["calculation"]
    calculation["selected_report"]["portfolio_constraint_evidence"]["status"] = "incomplete"
    calculation["checkpoint_id"] = _fingerprint(
        {key: item for key, item in calculation.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="constraint evidence changed"):
        run(store, "research-test", value, spec, saved=changed, checkpoint=checkpoint, **kwargs)
    assert len(engine.calls) == before_calls
