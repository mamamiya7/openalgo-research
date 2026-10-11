"""Native joint context, durable inspection and exact replay retain v6 trailing state."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_automatic_hypotheses import Engine, Stopped
from test_joint_stock_market import prepared as joint_prepared

from research.automatic_protocol import automatic_portfolio, compile_recipe
from research.entry_conditions import stock_module
from research.portfolio import execution_versions, fingerprint, normalize
from research.report_contract import settings_identity
from services import research_partial_study, research_portfolio

VERSION = "automatic-hypotheses-v6"


def prepared():
    value = joint_prepared()
    value["portfolio"]["automatic_research"] = {"version": VERSION}
    value["portfolio"] = normalize(value["portfolio"])
    value["strategies"][0].update(deepcopy(value["portfolio"]["strategies"][0]))
    value["versions"] = execution_versions(value["portfolio"])
    value["automatic_recipe"] = compile_recipe(value)
    return value


def test_new_search_retains_joint_data_versions_without_upgrading_saved_v5():
    old = joint_prepared()
    saved_portfolio = deepcopy(old["portfolio"])
    old_versions = execution_versions(old["portfolio"])
    new = prepared()
    assert new["automatic_recipe"]["version"] == VERSION
    assert new["automatic_recipe"]["budget"] == old["automatic_recipe"]["budget"]
    assert {
        key: version
        for key, version in new["versions"].items()
        if key != "automatic_research_version"
    } == {
        key: version for key, version in old_versions.items() if key != "automatic_research_version"
    }
    assert "trailing_research" in new["automatic_recipe"]
    assert "trailing_research" not in old["automatic_recipe"]
    assert automatic_portfolio(saved_portfolio) == saved_portfolio
    assert old["portfolio"] == saved_portfolio


@pytest.mark.parametrize("enabled,distance", [(True, 2.5), (False, 7.0)])
def test_exact_candidate_replay_retains_joint_rule_and_concrete_trailing_state(
    monkeypatch, enabled, distance
):
    value = prepared()
    original = deepcopy(value)
    selected = deepcopy(value["portfolio"]["strategies"])
    selected[0]["config"].update(trailing_enabled=enabled, trailing_pct=distance)
    rule = next(
        row
        for row in stock_module("research-joint-entry-conditions-v1").catalog()
        if row["id"] == "rsi_14_gt60__all"
    )
    marker = {"version": "research-joint-entry-conditions-v1", "hypothesis": rule}
    selected[0]["entry_condition"] = deepcopy(marker)
    config_id = settings_identity(selected)
    report = {
        "strategies": selected,
        "automatic_research": {"version": VERSION},
        "experiment": {
            "rows": [
                {
                    "config_id": config_id,
                    "trial_number": 9,
                    "strategies": selected,
                }
            ]
        },
    }
    job = SimpleNamespace(id="b" * 32, status="completed", result_artifact="c" * 64)
    bundle = {"kind": "portfolio_optimize", "inputs_artifact": "d" * 64, "result": report}
    monkeypatch.setattr(research_portfolio.service, "get_job", lambda *_: job)
    monkeypatch.setattr(
        research_portfolio.service,
        "read_artifact",
        lambda _store, identifier: deepcopy(bundle if identifier == job.result_artifact else value),
    )
    monkeypatch.setattr(
        research_portfolio,
        "_prepare_prices",
        lambda *a, **k: pytest.fail("Frozen replay cannot download prices"),
    )
    _, replay = research_portfolio.replay_inputs(None, "owner", job.id, trial_id=config_id)
    portfolio = replay["portfolio"]
    assert not {"optimization", "automatic_research", "validation"}.intersection(portfolio)
    assert portfolio["entry_conditions"] == marker
    assert portfolio["strategies"][0]["entry_condition"] == marker
    assert portfolio["strategies"][0]["config"]["trailing_enabled"] is enabled
    assert portfolio["strategies"][0]["config"]["trailing_pct"] == distance
    assert replay["strategies"][0]["config"] == portfolio["strategies"][0]["config"]
    assert settings_identity(portfolio["strategies"]) == config_id
    assert replay["automatic_stock_market_context"] == value["automatic_stock_market_context"]
    assert replay["replay_origin"]["config_id"] == config_id
    assert (
        replay["snapshot"]["sessions"][-1] == value["automatic_recipe"]["periods"]["search"]["to"]
    )
    assert value == original


def test_native_service_uses_shared_joint_context_and_durable_job_for_v6(monkeypatch):
    from research import automatic_hypotheses
    from services import (
        research_automatic_periods,
        research_stock_features,
        research_stock_market_context,
    )

    value = prepared()
    source = deepcopy(value)
    calls, artifacts, claims = [], [], []

    def context(_store, _owner, evidence, **kwargs):
        calls.append(deepcopy(evidence["automatic_stock_market_context"]))
        return evidence

    def calculate(evidence, **kwargs):
        assert evidence["automatic_recipe"]["version"] == VERSION
        assert (
            evidence["automatic_stock_market_context"] == source["automatic_stock_market_context"]
        )
        assert "automatic_stock_features" not in evidence
        assert callable(kwargs["evaluate"])
        return {"automatic_research": {"version": VERSION}}

    monkeypatch.setattr(research_stock_market_context, "prepare_context", context)
    monkeypatch.setattr(
        research_stock_features,
        "prepare_context",
        lambda *a, **k: pytest.fail("v6 must not drop joint market context"),
    )
    monkeypatch.setattr(automatic_hypotheses, "run", calculate)
    monkeypatch.setattr(research_automatic_periods, "claim", lambda *args: claims.append(args))
    monkeypatch.setattr(
        research_portfolio.service,
        "save_artifact",
        lambda _store, artifact: artifacts.append(deepcopy(artifact)) or fingerprint(artifact),
    )
    result, retained = research_portfolio.run(
        None,
        "owner",
        value,
        {"portfolio": value["portfolio"], "versions": value["versions"]},
        job_id="e" * 32,
        checkpoint=lambda *a: None,
        progress=lambda *a: None,
        cancelled=lambda: None,
    )
    assert result["automatic_research"]["version"] == VERSION
    assert len(calls) == 1 and len(claims) == 1
    assert claims[0][-1] == "e" * 32
    assert len(artifacts) == 2
    assert retained["automatic_stock_market_context"] == source["automatic_stock_market_context"]


@pytest.mark.timeout(180)
def test_paused_v6_inspection_binds_joint_context_and_retains_committed_trailing_settings(
    monkeypatch,
):
    from research.automatic_hypotheses import run

    value = prepared()
    engine = Engine(value)
    checkpoints = []

    def stop(state, _counts):
        checkpoints.append(deepcopy(state))
        if len(state["trials"]) == 4:
            raise Stopped

    with pytest.raises(Stopped):
        run(
            value,
            evaluate=engine,
            checkpoint=stop,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    saved = checkpoints[-1]
    before = deepcopy(saved)
    calls_before_inspection = deepcopy(engine.calls)
    monkeypatch.setattr(
        "research.connectors.optuna_portfolio.run_search",
        lambda *a, **k: pytest.fail("Inspecting retained trials cannot run a search"),
    )
    selection, checked, scope, automatic, completed = research_partial_study._search_inputs(
        value, saved
    )
    rows, counts = research_partial_study._validated_rows(selection, checked, completed)
    assert scope == "search" and automatic and not completed
    assert counts["proposed"] == 4
    assert counts["evaluated"] + counts["reused"] == 4
    assert rows and all(
        type(row["strategies"][0]["config"]["trailing_enabled"]) is bool for row in rows
    )
    assert saved == before
    assert engine.calls == calls_before_inspection
    changed = deepcopy(value)
    changed["automatic_stock_market_context"]["id"] = "f" * 64
    with pytest.raises(ValueError, match="Saved stock-market context changed"):
        research_partial_study._search_inputs(changed, saved)


def test_v6_preflight_keeps_bounded_universe_before_any_acquisition(monkeypatch):
    value = prepared()
    dates = value["snapshot"]["sessions"][:160]
    signals = [
        {"symbol": f"STOCK{i}", "date": dates[i % len(dates)], "row": i + 2} for i in range(200)
    ]
    monkeypatch.setattr(
        research_portfolio.service,
        "source_for",
        lambda *_: {"signals": deepcopy(signals), "receipt": {"input_rows": len(signals)}},
    )
    accepted = research_portfolio.resolve_inputs(None, "owner", value["portfolio"])
    assert accepted["receipt"]["symbol_count"] == 200
    assert accepted["snapshot"]["coverage"]["status"] == "preparing"
    signals.append({"symbol": "EXTRA", "date": dates[-1], "row": 202})
    with pytest.raises(ValueError, match="at most 200 NSE stocks"):
        research_portfolio.resolve_inputs(None, "owner", value["portfolio"])


def test_v6_candidate_requires_its_own_verified_basis_before_creating_a_report(monkeypatch):
    from services import research_candidates

    value = prepared()
    selected = deepcopy(value["strategies"])
    selected[0]["config"].update(trailing_enabled=True, trailing_pct=2.5)
    config_id = settings_identity(selected)
    row = {
        "config_id": config_id,
        "strategies": selected,
        "evaluation_basis": {"status": "unverified"},
    }
    parent = SimpleNamespace(id="b" * 32)
    bundle = {
        "result": {
            "automatic_research": {"version": VERSION},
            "evaluation_basis": {"status": "verified"},
        }
    }
    monkeypatch.setattr(
        research_candidates,
        "_study",
        lambda *_: (parent, bundle, {"rows": [row]}, "selection"),
    )
    monkeypatch.setattr(
        research_candidates,
        "availability",
        lambda *_: {"candidates": [{"config_id": config_id, "status": "available"}]},
    )
    monkeypatch.setattr(research_portfolio, "replay_inputs", lambda *a, **k: (parent, value))
    monkeypatch.setattr(
        research_portfolio,
        "validate_submission",
        lambda *a, **k: pytest.fail("Unverified candidate must not publish a new report"),
    )
    receipt = research_candidates.prepare(None, "owner", parent.id, config_id)
    assert receipt["status"] == "unavailable"
    assert "its own verified evaluation basis" in receipt["error"]
