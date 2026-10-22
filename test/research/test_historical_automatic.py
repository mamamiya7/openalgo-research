"""Historical reuse is explicit, identity-bound and never independently validated."""

# ruff: noqa: F811 -- shared isolated HTTP fixtures

from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_portfolio_allocation_recipe import prepared

from database.research_db import ResearchStore
from research.automatic_protocol import (
    ALLOCATION_VERSION,
    SEED_VERSION,
    compile_recipe,
    normalize_auto,
)
from research.portfolio import normalize
from research.research_mandate import build, verify
from services import scanner_research_service as artifacts

LABEL = "Historical optimisation — not independently validated"


@pytest.fixture(scope="module")
def strict_inputs():
    return prepared()


def historical(value):
    value = deepcopy(value)
    value["portfolio"]["automatic_research"] = {
        "version": ALLOCATION_VERSION,
        "validation_mode": "historical_only",
    }
    value["portfolio"] = normalize(value["portfolio"])
    value["automatic_recipe"] = compile_recipe(value)
    return value


@pytest.fixture
def store(tmp_path):
    instance = ResearchStore(tmp_path / "research")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


def test_only_explicit_current_recipe_enables_historical_mode_and_legacy_stays_strict():
    explicit = {"version": ALLOCATION_VERSION, "validation_mode": "historical_only"}
    assert normalize_auto(explicit) == explicit
    for version in (ALLOCATION_VERSION, SEED_VERSION):
        assert normalize_auto({"version": version}) == {"version": version}


@pytest.mark.parametrize(
    "raw",
    [
        {"version": SEED_VERSION, "validation_mode": "historical_only"},
        {"version": ALLOCATION_VERSION, "validation_mode": "chronological"},
        {"version": ALLOCATION_VERSION, "validation_mode": None},
        {"version": ALLOCATION_VERSION, "validation_mode": True},
        {"version": ALLOCATION_VERSION, "validation_mode": "historical"},
        {"version": ALLOCATION_VERSION, "validation_mode": "historical_only", "trials": 9999},
    ],
)
def test_historical_mode_rejects_other_recipes_unknown_modes_and_budget_overrides(raw):
    with pytest.raises(ValueError):
        normalize_auto(raw)


def test_historical_recipe_keeps_periods_search_and_budget_but_has_a_distinct_identity(
    strict_inputs,
):
    strict = strict_inputs["automatic_recipe"]
    value = historical(strict_inputs)
    recipe = value["automatic_recipe"]
    assert recipe["validation_mode"] == "historical_only"
    assert strict["validation_mode"] == "chronological"
    assert recipe["id"] != strict["id"]
    for key in (
        "periods",
        "budget",
        "search",
        "hypothesis_catalog",
        "execution_research",
        "trailing_research",
        "timing_research",
    ):
        assert recipe[key] == strict[key], key
    assert (
        normalize(value["portfolio"])["automatic_research"]
        == value["portfolio"]["automatic_research"]
    )


def test_historical_mandate_has_comparison_roles_and_cannot_claim_validated_advice(strict_inputs):
    value = historical(strict_inputs)
    mandate = build(value, inputs_artifact="a" * 64)
    assert mandate["validation"]["mode"] == "historical_only"
    final = next(row for row in mandate["validation"]["periods"] if row["name"] == "final")
    assert final["role"] == "historical_comparison"
    assert all(not row["untouched_claim"] for row in mandate["validation"]["periods"])
    assert mandate["supported_promise"]["validated_recommendation_possible"] is False
    assert verify(mandate, evidence=value, inputs_artifact="a" * 64)
    with pytest.raises(ValueError):
        verify(mandate, evidence=strict_inputs, inputs_artifact="a" * 64)


@pytest.mark.parametrize("mode", [None, "historical_only"])
def test_service_reserves_dates_only_for_strict_mode_without_running_a_fit(
    strict_inputs, store, monkeypatch, mode
):
    from services.research_portfolio import run

    value = historical(strict_inputs) if mode else deepcopy(strict_inputs)
    value["frozen_prices"] = True
    claims = []

    class ReachedAutomatic(Exception):
        pass

    def stopped_fit(*args, **kwargs):
        raise ReachedAutomatic

    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *args: None)
    monkeypatch.setattr(
        "services.research_stock_market_context.prepare_context", lambda *a, **k: a[2]
    )
    monkeypatch.setattr("research.execution_features.prepare", lambda value, *a, **k: value)
    monkeypatch.setattr(
        "services.research_portfolio_features.prepare", lambda store, value, **k: value
    )
    monkeypatch.setattr("services.research_automatic_periods.claim", lambda *a: claims.append(a))
    monkeypatch.setattr("research.automatic_walk_forward.run", stopped_fit)
    with pytest.raises(ReachedAutomatic):
        run(
            store,
            "isolated-owner",
            value,
            {"portfolio": value["portfolio"], "versions": value["versions"]},
            checkpoint=lambda *a: None,
            progress=lambda *a: None,
            cancelled=lambda: None,
            job_id="historical-mode-check",
            defer_study_analysis=True,
        )
    assert len(claims) == (0 if mode else 1)


def test_resume_cannot_change_validation_mode_of_saved_inputs(strict_inputs, store, monkeypatch):
    from services.research_portfolio import run

    original = deepcopy(strict_inputs)
    original["frozen_prices"] = True
    artifact = artifacts.save_artifact(store, original)
    changed = historical(strict_inputs)
    monkeypatch.setattr("services.research_portfolio.validate_submission", lambda *a: None)
    with pytest.raises(ValueError, match="Saved calculation inputs do not match"):
        run(
            store,
            "isolated-owner",
            changed,
            {"portfolio": changed["portfolio"], "versions": changed["versions"]},
            saved={"phase": "calculation", "inputs_artifact": artifact, "calculation": {}},
            checkpoint=lambda *a: None,
            progress=lambda *a: None,
            cancelled=lambda: None,
            job_id="changed-mode",
        )


@pytest.mark.parametrize("supported", [False, True])
def test_historical_result_labels_only_preserve_native_accounts_and_selected_settings(supported):
    from research.automatic_walk_forward import _historical_result

    final = {
        "summary": {"net_return_pct": -2.5},
        "equity_curve": [{"date": "2026-07-01", "equity": 9750}],
        "ledger": [{"status": "closed", "pnl": -250}],
    }
    value = {
        "summary": {"net_return_pct": 2},
        "strategies": [{"id": "scanner", "config": {"target_pct": 4}}],
        "equity_curve": [{"date": "2026-01-05", "equity": 10200}],
        "experiment": {
            "rows": [{"config_id": "selected", "net_return_pct": 2}],
            "findings": ["Previous chronological headline"],
        },
        "automatic_research": {
            "status": "supported" if supported else "inconclusive",
            "headline": "Previous chronological headline",
            "selected_config_id": "selected",
            "selected_is_baseline": not supported,
            "final": {"supports": supported, "summary": deepcopy(final["summary"]), "reasons": []},
        },
        "validation": {"label": "Final later-period check", "result": final},
    }
    before = deepcopy(value)
    returned = _historical_result(value)
    result = value if returned is None else returned
    findings = result["automatic_research"]
    assert findings["status"] == "historical"
    assert findings["headline"] == LABEL
    assert findings["independently_validated"] is False
    assert findings["historical_checks_passed"] is supported
    assert findings["final"]["supports"] is False
    assert findings["final"]["historical_checks_passed"] is supported
    assert result["validation"]["label"] == "Historical later-period comparison"
    assert result["validation"]["independently_validated"] is False
    for key in ("summary", "strategies", "equity_curve"):
        assert result[key] == before[key]
    assert result["experiment"]["rows"] == before["experiment"]["rows"]
    assert result["experiment"]["findings"][0] == LABEL
    assert findings["selected_config_id"] == before["automatic_research"]["selected_config_id"]
    assert result["validation"]["result"] == before["validation"]["result"]


def test_historical_choice_survives_saved_draft_reopening(app, client):
    from services.research_library import fresh_draft

    draft = fresh_draft()
    draft["portfolio"]["automatic_research"] = {
        "version": ALLOCATION_VERSION,
        "validation_mode": "historical_only",
    }
    draft["optimizing"] = True
    created = client.post(
        "/scanner-research/api/library/experiments",
        json={"name": "Historical scanner research", "draft": draft},
    )
    assert created.status_code == 201, created.json
    identifier = created.json["id"]
    second = app.test_client()
    with second.session_transaction() as session:
        session["user"] = "research-test"
    reopened = second.get(f"/scanner-research/api/library/experiments/{identifier}")
    assert reopened.status_code == 200, reopened.json
    assert (
        reopened.json["draft"]["portfolio"]["automatic_research"]
        == draft["portfolio"]["automatic_research"]
    )


def test_early_admission_is_read_only_and_reports_reserved_dates(strict_inputs, store):
    from sqlalchemy import select

    from database.research_db import ResearchAutomaticPeriodUse
    from services.research_automatic_periods import check_available, claim

    check_available(store, "owner", strict_inputs)
    with store.sessions() as db:
        assert db.scalars(select(ResearchAutomaticPeriodUse)).all() == []
    claim(store, "owner", "a" * 64, strict_inputs, "earlier-job")
    with store.sessions() as db:
        original = [(row.day, row.job_id) for row in db.scalars(select(ResearchAutomaticPeriodUse))]
    final = strict_inputs["automatic_recipe"]["periods"]["final"]
    with pytest.raises(ValueError, match=f"{final['from']} to {final['to']}.*already used"):
        check_available(store, "owner", strict_inputs)
    with store.sessions() as db:
        assert [
            (row.day, row.job_id) for row in db.scalars(select(ResearchAutomaticPeriodUse))
        ] == original


def test_early_historical_preparation_skips_independent_date_admission(
    strict_inputs, store, monkeypatch
):
    from services.research_portfolio import _check_automatic_period_admission

    calls = []
    monkeypatch.setattr(
        "services.research_automatic_periods.check_available", lambda *args: calls.append(args)
    )
    _check_automatic_period_admission(store, "owner", strict_inputs)
    assert len(calls) == 1
    recipe = _check_automatic_period_admission(store, "owner", historical(strict_inputs))
    assert len(calls) == 1
    assert recipe["validation_mode"] == "historical_only"
