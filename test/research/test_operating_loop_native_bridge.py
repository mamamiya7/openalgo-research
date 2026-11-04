"""Two native remedy accounts qualify callback wiring without running search."""

# ruff: noqa: F811 -- explicit preparation-only shared fixtures
from copy import deepcopy

import pytest
from test_automatic_actions_runtime import acting  # noqa: F401
from test_automatic_decision_runtime import deciding  # noqa: F401
from test_automatic_design_runtime import designed  # noqa: F401
from test_automatic_walk_forward import execute
from test_available_history_runtime import available  # noqa: F401

from research.automatic_protocol import compile_recipe
from research.connectors.vectorbt_portfolio import evaluate as native_evaluate
from research.portfolio import execution_versions, normalize
from research.report_contract import settings_identity


@pytest.mark.timeout(240)
def test_operating_callbacks_use_native_changed_capital_stress_and_prefix_basis(
    acting, monkeypatch
):
    evidence = deepcopy(acting)
    evidence["portfolio"]["automatic_research"]["action_policy"] = "automatic_actions_v3"
    evidence["portfolio"] = normalize(evidence["portfolio"])
    evidence["versions"] = execution_versions(evidence["portfolio"])
    evidence.pop("automatic_recipe")
    evidence["automatic_recipe"] = compile_recipe(evidence)
    calls = []
    checked = []

    def native(rows, snapshot, capital, progress=None):
        calls.append((capital, len(snapshot["sessions"])))
        return native_evaluate(rows, snapshot, capital, progress=progress)

    def primary(_evidence, *, state, baseline, none, measured, **kwargs):
        measured(baseline, none, "search", report_slot="selected_report")
        report = state["selected_report"]
        identity = settings_identity(report["strategies"])
        state["selection"] = {
            "strategies": deepcopy(report["strategies"]),
            "hypothesis": deepcopy(none),
            "config_id": identity,
            "condition_label": "No stock filter",
            "is_baseline": True,
        }
        result = deepcopy(report)
        result["automatic_research"] = {
            "version": "automatic-hypotheses-v16",
            "selected_config_id": identity,
            "independently_validated": False,
            "counts": {
                "proposals": 0,
                "simulations": state["simulations"],
                "attempts_reserved": state["attempts"],
            },
        }
        result["experiment"] = {"rows": [], "selected_reports": {identity: deepcopy(report)}}
        return result

    def operating(result, *, baseline, baseline_hypothesis, evaluate, audit, **kwargs):
        settings = deepcopy(baseline)
        for row in settings:
            row["config"]["initial_capital"] = 200000.0
        for period in ("search", "fit1"):
            report = evaluate(settings, baseline_hypothesis, period, True)
            request = {
                "role": "cost_selected",
                "settings": settings,
                "hypothesis": baseline_hypothesis,
                "period": period,
                "stress": True,
            }
            assert report["summary"]["initial_capital"] == 200000.0
            assert all(
                row["config"]["cost_bps"]
                == max(2 * original["config"]["cost_bps"], original["config"]["cost_bps"] + 5)
                for row, original in zip(report["strategies"], settings, strict=True)
            )
            assert report["automatic_stock_condition"]["hypothesis"] == baseline_hypothesis
            assert report["portfolio_constraint_evidence"]["status"] == "supported"
            assert audit(report, request) == report["portfolio_constraint_evidence"]
            assert report["automatic_design"]["version"] == "research-automatic-design-v1"
            checked.append((period, report["evaluation_basis"]["period"]["observations"]))
        # The real operating loop publishes its completed account state. This
        # spy must do the same before execute() captures the final checkpoint.
        kwargs["persist"]()
        return result

    monkeypatch.setattr("research.available_history_research.run", primary)
    monkeypatch.setattr("research.operating_loop.run", operating)
    result, state = execute(evidence, native)
    assert checked[0][0] == "search" and checked[1][0] == "fit1"
    assert checked[1][1] < checked[0][1]
    assert 3 <= len(calls) <= 7
    assert sum(capital == 200000.0 for capital, _ in calls) == 2
    assert state["fits"] == {}
    assert result["automatic_research"]["counts"]["proposals"] == 0
    assert state["simulations"] == len(calls)
