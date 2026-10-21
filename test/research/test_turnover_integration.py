"""Frozen turnover programmes and saved recommendation evidence, isolated only."""

# ruff: noqa: F811
from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_priority_research import PriorityAccountEngine
from test_priority_research import prepared as previous_prepared

from research.automatic_protocol import TURNOVER_VERSION, compile_recipe
from research.execution_policy import normalize_limits, normalize_policy
from research.execution_search import apply_choices, axes, descriptor, original_sizing
from research.portfolio import execution_versions, normalize
from research.recommendation_plan import challenges
from research.report_contract import settings_identity

pytestmark = pytest.mark.timeout(300)


def prepared():
    value = previous_prepared()
    portfolio = value["portfolio"]
    portfolio["automatic_research"] = {"version": TURNOVER_VERSION}
    portfolio["risk_limits"] = normalize_limits(
        {
            **portfolio["risk_limits"],
            "version": "research-risk-limits-v2",
            "max_daily_buy_turnover_pct": 75,
        }
    )
    for row in portfolio["strategies"]:
        row["execution_policy"] = normalize_policy(
            {
                **row["execution_policy"],
                "version": "research-execution-aware-v3",
                "risk_limits": portfolio["risk_limits"],
            }
        )
    value["portfolio"] = normalize(portfolio)
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["versions"] = execution_versions(value["portfolio"])
    value.pop("automatic_recipe", None)
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_v12_fixed_turnover_contract_no_new_price_or_search_axis():
    value = prepared()
    recipe = value["automatic_recipe"]
    assert value["versions"]["adapter_version"] == "vectorbt-portfolio-adapter-v4"
    assert recipe["budget"]["proposals"] == 272
    assert recipe["budget"]["max_simulations"] == 384
    assert recipe["budget"]["max_recommendation_accounts"] == 6
    assert recipe["recommendation_checks"]["seed"] == 0
    spec = descriptor(value)
    assert spec["version"] == "research-execution-search-v3"
    domains = axes(spec)
    assert not any("turnover" in name for name in domains)
    params = {name: domain["choices"][0] for name, domain in domains.items()}
    result = apply_choices(value["strategies"], params, spec)
    assert result[0]["execution_policy"]["risk_limits"]["max_daily_buy_turnover_pct"] == 75
    assert (
        original_sizing(result, spec)[0]["execution_policy"]["risk_limits"]
        == value["portfolio"]["risk_limits"]
    )
    portfolio = deepcopy(value["portfolio"])
    portfolio["strategies"][0]["execution_policy"]["version"] = "research-execution-aware-v2"
    with pytest.raises(ValueError):
        normalize(portfolio)


@pytest.mark.parametrize("limits", [[], False, "", 0])
@pytest.mark.parametrize(
    "scope", ["automatic-hypotheses-v10", "automatic-hypotheses-v11", TURNOVER_VERSION]
)
def test_malformed_falsy_account_limits_are_not_replaced_with_defaults(limits, scope):
    value = previous_prepared()["portfolio"]
    value["automatic_research"] = {"version": scope}
    value["risk_limits"] = limits
    with pytest.raises(ValueError, match="risk limits"):
        normalize(value)


def test_challenges_are_bounded_active_and_do_not_change_hard_caps():
    value = prepared()
    rows = value["strategies"]
    rows[0]["execution_policy"] = normalize_policy(
        {
            **rows[0]["execution_policy"],
            "exit_mode": "atr",
            "atr_period": 14,
            "atr_stop_multiple": 2,
            "atr_target_multiple": 3,
        }
    )
    plan = challenges(rows, value["automatic_recipe"]["ranges"])
    assert 2 <= len(plan["neighbors"]) <= 4
    assert len({row["config_id"] for row in plan["neighbors"]}) == len(plan["neighbors"])
    for row in plan["neighbors"]:
        assert row["axis"] not in ("stop_pct", "target_pct", "trailing_pct")
        assert (
            row["strategies"][0]["execution_policy"]["risk_limits"]
            == value["portfolio"]["risk_limits"]
        )


def test_full_service_retains_robustness_before_final_and_recovers_exactly(
    app, client, monkeypatch
):
    from research.expanded_stock_market import PreparedJointContext
    from services.research_portfolio import run

    value = prepared()
    original = deepcopy(value)
    store = app.extensions["research_store"]
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **kw: a[2]
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: None)
    monkeypatch.setattr("research.automatic_walk_forward.prepare_method_gate", PreparedJointContext)
    saved = None
    final_checks = []

    def on_call(call):
        if call["period"] == "final":
            calculation = saved["calculation"]
            assert calculation["selection"] is not None
            assert "recommendation_checks" in calculation
            final_checks.append(deepcopy(calculation["recommendation_checks"]))

    engine = PriorityAccountEngine(value, on_call=on_call)
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", engine)

    class Paused(Exception):
        pass

    def pause(state, counts):
        nonlocal saved
        saved = state
        if (
            len((state.get("calculation") or {}).get("fits", {}).get("fit1", {}).get("trials", []))
            == 41
        ):
            raise Paused

    kwargs = {
        "progress": lambda *a: None,
        "cancelled": lambda: None,
        "job_id": "e" * 32,
        "defer_study_analysis": True,
    }
    spec = {"portfolio": value["portfolio"], "versions": value["versions"]}
    with pytest.raises(Paused):
        run(store, "research-test", value, spec, checkpoint=pause, **kwargs)
    partial = deepcopy(saved)

    def checkpoint(state, counts):
        nonlocal saved
        saved = state

    result, _ = run(
        store, "research-test", value, spec, saved=partial, checkpoint=checkpoint, **kwargs
    )
    assert value == original
    findings = result["automatic_research"]
    checks = findings["recommendation_checks"]
    assert checks["status"] == "supported", checks
    assert not findings["selected_is_baseline"]
    assert final_checks and all(check == checks for check in final_checks)
    assert result["development_evidence"]["status"] == "complete"
    assert all(
        row["period"] != "final" for row in result["development_evidence"]["entries"].values()
    )
    assert findings["counts"]["proposals"] == 272
    assert len(engine.calls) <= 384
    before = len(engine.calls)
    repeated, _ = run(
        store, "research-test", value, spec, saved=deepcopy(saved), checkpoint=checkpoint, **kwargs
    )
    assert repeated == result and len(engine.calls) == before
    assert settings_identity(repeated["strategies"]) == findings["selected_config_id"]
    from research.research_tradeoffs import analyze

    retained = deepcopy(result)
    alternatives = analyze(result)
    assert alternatives["status"] == "available", alternatives
    assert alternatives["counts"]["recorded"] == len(result["experiment"]["rows"])
    for excluded in alternatives["unavailable"]:
        assert all(
            reason
            in {
                "Return after recorded costs is not positive",
                "Drawdown exceeds the recorded research display limit of 25%",
                "Fewer than five closed trades or five recorded distinct entry dates",
            }
            for reason in excluded["reasons"]
        ), excluded
    assert result == retained and len(engine.calls) == before


def test_v12_saved_coverage_and_preflight_are_metadata_only():
    from test_method_coverage import prepared_metadata

    from research.method_coverage import build
    from services.research_automatic_catalog import build_plan

    value = prepared()
    plan = build_plan(value["portfolio"], interval="D")
    assert plan["summary"]["max_simulations"] == 384
    assert plan["search"]["proposals"] == 272
    mandate, calculation, manifest = prepared_metadata(version=TURNOVER_VERSION)
    coverage = build(mandate, calculation, manifest)
    assert coverage["status"] != "unavailable", coverage
    assert coverage["registry"]["recipe_version"] == TURNOVER_VERSION


def test_short_calendar_stops_before_any_optimisation_accounts():
    value = prepared()
    value.pop("automatic_recipe")
    value["snapshot"]["sessions"] = value["snapshot"]["sessions"][:120]
    last = value["snapshot"]["sessions"][-1]
    value["signals"] = [row for row in value["signals"] if row["date"] <= last]
    for row in value["strategies"]:
        row["signals"] = [signal for signal in row["signals"] if signal["date"] <= last]
    with pytest.raises(ValueError, match="60 later-window"):
        compile_recipe(value)
