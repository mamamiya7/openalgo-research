"""V14 frozen masks, saved native facts and controlled full service recovery.

Synthetic economics/audit stubs exercise orchestration only. The genuine native
scenario suite independently qualifies actual constrained fills and account marks.
No normal account, broker credential or external history is used here.
"""

# ruff: noqa: F811
from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_priority_research import PriorityAccountEngine
from test_sensitivity_native import prepared

from research.automatic_protocol import SENSITIVITY_VERSION, compile_recipe
from research.automatic_walk_forward import sensitivity_window
from research.connectors.optuna_portfolio import _fingerprint
from research.evaluation_basis import build_evaluation_basis
from research.expanded_stock_market import PreparedJointContext
from research.method_coverage import build as method_coverage
from research.portfolio import fingerprint
from research.recommendation_sensitivity import NAMES
from research.report_contract import settings_identity
from research.research_mandate import build as build_mandate

pytestmark = pytest.mark.timeout(600)


def test_v13_frozen_contracts_match_the_previous_installed_source():
    from test_portfolio_capacity_integration import prepared as previous_prepared

    # Generated against 7493ab38c using its original research/service modules.
    value = previous_prepared()
    expected = {
        "portfolio": "a11cb980128893c8f81b8566757003d0ea69868484c755f8fada80279b37aeda",
        "automatic_recipe": "a3b43948f37202b9e1386416cba3003a48b432df86ab7fb7658582ec4fe5c2f3",
        "versions": "676132dcb0aca4fa41e1cfe3ea87fad1f8d6339ed0ab3f12f3b3cf70c302da40",
        "execution_features": "eb843c86aaf21804a4e7d2606b6c22da805447b89ddfaa8989f6438c57781052",
        "portfolio_execution_features": "732d4bdb2b067e6d15d43ca8bbc6cbe89fc16fe5986b2d0d125e1a08fff3c8d1",
    }
    assert {key: fingerprint(value[key]) for key in expected} == expected


def test_recipe_freezes_prespecified_masks_bounds_and_mandate_before_scores():
    value = prepared()
    original = deepcopy(value)
    recipe = value["automatic_recipe"]
    assert recipe["version"] == SENSITIVITY_VERSION
    assert recipe["budget"]["proposals"] == 272
    assert recipe["budget"]["max_simulations"] == 384
    assert recipe["budget"]["max_sensitivity_accounts"] == 8
    assert recipe["recommendation_sensitivity"]["status"] == "available"
    assert recipe == compile_recipe(value)
    manifest = build_mandate(value, inputs_artifact="a" * 64)
    roles = {row["name"]: row["role"] for row in manifest["validation"]["periods"]}
    for scenario in recipe["recommendation_sensitivity"]["scenarios"]:
        name = scenario["name"]
        scoped = sensitivity_window(value, name)
        basis = build_evaluation_basis(scoped, period="selection")
        assert basis["source_id"] == scenario["source_id"]
        assert basis["observations_id"] == scenario["observations_id"]
        assert roles[name] == "development_sensitivity"
        assert scoped["snapshot"]["sessions"][-1] < recipe["periods"]["final"]["from"]
        assert {row["symbol"] for row in scoped["signals"]} == set(scenario["symbols"])
        assert scoped["portfolio"]["capital"] == value["portfolio"]["capital"]
        assert [row["allocation_pct"] for row in scoped["strategies"]] == [
            row["allocation_pct"] for row in value["strategies"]
        ]
    assert value == original


def test_full_service_checks_masks_before_final_resumes_and_rejects_changed_facts(
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

    def audit(report, features):
        condition = report.get("automatic_stock_condition", {}).get("hypothesis", {})
        if condition.get("id") != "none":
            assert report["evaluation_basis"]["comparison"]["execution"].get("entry_conditions")
        body = {
            "version": "research-portfolio-constraint-evidence-v1",
            "status": "supported",
            "feature_id": features["id"],
            "report_evidence_id": fingerprint(
                {
                    key: report[key]
                    for key in ("summary", "equity_curve", "evaluation_basis", "strategies")
                }
            ),
            "unavailable_entries": 0,
            "scope": "caller_admitted_development_account",
            "qualification": "recorded_constraints_and_native_fills_only",
        }
        return {**body, "id": fingerprint(body)}

    monkeypatch.setattr("research.portfolio_constraint_evidence.assess", audit)
    latest = prefix = None
    pause = True
    scenario_loss = False
    scenario_calls = []
    final_choices = []
    engine = PriorityAccountEngine(value)
    baseline_id = settings_identity(value["automatic_recipe"]["baseline"])

    class Paused(Exception):
        pass

    def account(rows, snapshot, capital, progress=None):
        nonlocal pause, prefix
        symbols = {signal["symbol"] for row in rows for signal in row["signals"]}
        first, last = snapshot["sessions"][0], snapshot["sessions"][-1]
        # The first complementary symbol challenge has fewer symbols than the
        # unmasked source, regardless of the actual entry-condition admissions.
        main_fit_complete = (
            len(latest["calculation"]["fits"].get("search", {}).get("trials", [])) == 68
        )
        masked = main_fit_complete and (
            len(symbols) == 1
            or any(
                row["axis"] == "dates" and row["from"] == first and row["to"] == last
                for row in value["automatic_recipe"]["recommendation_sensitivity"]["scenarios"]
            )
        )
        if masked:
            assert latest["calculation"]["selection"] is None
            if pause:
                prefix = deepcopy(latest)
                pause = False
                raise Paused
            scenario_calls.append((symbols, first, last))
        report = engine(rows, snapshot, capital, progress)
        if (
            masked
            and scenario_loss
            and first
            == value["automatic_recipe"]["recommendation_sensitivity"]["scenarios"][-1]["from"]
        ):
            # A strategy that fails the later development half must be rejected.
            _loss(report, capital, 0.25)
        if engine.calls[-1]["period"] == "final":
            calculation = latest["calculation"]
            checks = calculation["recommendation_checks"]
            selected = calculation["selection"]
            final_choices.append((selected["config_id"], deepcopy(checks)))
            if not scenario_loss:
                assert checks["sensitivity"]["status"] == "supported", checks
                assert not selected["is_baseline"]
            _loss(report, capital, 0.4 if engine.calls[-1]["settings_id"] == baseline_id else 0.6)
        return report

    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", account)

    def checkpoint(state, counts):
        nonlocal latest
        latest = state

    spec = {"portfolio": value["portfolio"], "versions": value["versions"]}
    kwargs = {
        "checkpoint": checkpoint,
        "progress": lambda *a: None,
        "cancelled": lambda: None,
        "job_id": "e" * 32,
        "defer_study_analysis": True,
    }
    with pytest.raises(Paused):
        run(store, "research-test", value, spec, **kwargs)
    assert prefix is not None and not scenario_calls
    result, _ = run(store, "research-test", value, spec, saved=prefix, **kwargs)
    assert value == original
    findings = result["automatic_research"]
    sensitivity = findings["recommendation_checks"]["sensitivity"]
    assert sensitivity["status"] == "supported", sensitivity
    assert [row["name"] for row in sensitivity["scenarios"]] == list(NAMES)
    assert len(scenario_calls) == 8
    assert findings["counts"]["proposals"] == 272
    assert findings["counts"]["simulations"] <= 384
    assert findings["status"] != "supported", "Final loss cannot select or rescue the setup"
    assert final_choices and all(
        choice == findings["selected_config_id"] for choice, _ in final_choices
    )
    assert result["development_evidence"]["status"] == "complete"
    manifest = latest["development_evidence"]
    periods = [row["period"] for row in manifest["entries"].values()]
    assert all(periods.count(name) == 2 for name in NAMES)
    coverage = method_coverage(latest["research_mandate"], latest["calculation"], manifest)
    assert coverage["source"]["recipe_version"] == SENSITIVITY_VERSION
    complete = deepcopy(latest)
    before = len(engine.calls)
    repeated, _ = run(store, "research-test", value, spec, saved=complete, **kwargs)
    assert repeated == result and len(engine.calls) == before

    changed = deepcopy(complete)
    calculation = changed["calculation"]
    scenario_row = next(
        row for row in calculation["evaluations"].values() if "sensitivity_evidence" in row
    )
    scenario_row["entry_dates"] += 1
    calculation["checkpoint_id"] = _fingerprint(
        {key: item for key, item in calculation.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="sensitivity account facts changed"):
        run(store, "research-test", value, spec, saved=changed, **kwargs)
    assert len(engine.calls) == before

    # Reuse the paused, pre-challenge fixture to isolate a regime-shift failure.
    # Synthetic engine economics change only the uncommitted masked accounts;
    # no real saved result or previously committed account is overwritten.
    scenario_loss = True
    scenario_calls.clear()
    final_choices.clear()
    failed, _ = run(store, "research-test", value, spec, saved=deepcopy(prefix), **kwargs)
    assert len(scenario_calls) == 8
    assert failed["automatic_research"]["selected_is_baseline"]
    assert (
        failed["automatic_research"]["recommendation_checks"]["sensitivity"]["status"]
        == "not_supported"
    )


def _loss(report, capital, loss):
    curve = report["equity_curve"]
    for index, point in enumerate(curve):
        ratio = index / (len(curve) - 1)
        equity = float(capital) * (1 - loss * ratio)
        point.update(equity=equity, cash=equity, drawdown_pct=loss * ratio * 100)
    report["summary"].update(net_return_pct=-loss * 100, max_drawdown_pct=loss * 100)
