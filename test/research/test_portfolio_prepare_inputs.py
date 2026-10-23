"""Preparation stops before account calculation and retains exact run inputs."""

# ruff: noqa: F811 -- shared isolated fixtures

from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_native_price_workflow import prepare_archive
from test_portfolio_workflow import portfolio

from research.stock_market_context import _bundle_validation
from services import research_portfolio as orchestration
from services import scanner_research_service as service


def request_inputs(app, client, *, optimize=False, timed=False):
    store = app.extensions.get("research_store")
    request = portfolio(client, optimize=optimize, timed=timed)
    store = store or app.extensions["research_store"]
    evidence = orchestration.resolve_inputs(store, "research-test", request)
    return (
        store,
        evidence,
        {
            "portfolio": evidence["portfolio"],
            "versions": evidence["versions"],
        },
    )


def prepare(store, evidence, spec, **kwargs):
    states = []
    result = orchestration.prepare_inputs(
        store,
        "research-test",
        evidence,
        spec,
        checkpoint=lambda state, counts: states.append((deepcopy(state), deepcopy(counts))),
        progress=lambda *args: None,
        cancelled=lambda: None,
        **kwargs,
    )
    return result, states


@pytest.mark.parametrize("optimize,timed", [(False, False), (True, False), (False, True)])
def test_standalone_preparation_matches_run_boundary_without_calculation(
    app, client, monkeypatch, tmp_path, optimize, timed
):
    from test_acquisition import reference

    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: pytest.fail("Complete native archive must not need broker credentials"),
    )
    monkeypatch.setattr(
        "research.connectors.vectorbt_portfolio.evaluate",
        lambda *args, **kwargs: pytest.fail("Preparation must not calculate an account"),
    )
    store, evidence, spec = request_inputs(app, client, optimize=optimize, timed=timed)
    original = deepcopy(evidence)
    (prepared, identifier), states = prepare(store, evidence, spec)
    assert evidence == original
    assert prepared["frozen_prices"] is True
    assert service.read_artifact(store, identifier) == prepared
    assert states[-1][0] == {
        "phase": "calculation",
        "inputs_artifact": identifier,
        "calculation": None,
    }
    if not optimize:
        assert orchestration.validate_prepared_inputs(store, prepared, spec) is prepared
    run_states = []

    def boundary():
        raise RuntimeError("before calculation")

    with pytest.raises(RuntimeError, match="before calculation"):
        orchestration.run(
            store,
            "research-test",
            evidence,
            spec,
            checkpoint=lambda state, counts: run_states.append((deepcopy(state), deepcopy(counts))),
            progress=lambda *args: None,
            cancelled=lambda: None,
            boundary=boundary,
        )
    assert run_states == states

    monkeypatch.setattr(
        orchestration,
        "_prepare_prices",
        lambda *args, **kwargs: pytest.fail("Completed preparation must not reacquire prices"),
    )
    resumed, resumed_states = prepare(store, evidence, spec, saved=states[-1][0])
    assert resumed == (prepared, identifier)
    assert resumed_states == []


def test_preparation_cancellation_releases_validation_scope(app, client, monkeypatch):
    store, evidence, spec = request_inputs(app, client)
    scopes = []

    def acquire(*args, **kwargs):
        scopes.append(_bundle_validation.get())
        kwargs["cancelled"]()
        pytest.fail("Cancelled preparation must not publish inputs")

    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks > 1:
            raise InterruptedError("stop preparation")

    monkeypatch.setattr(orchestration, "_prepare_prices", acquire)
    with pytest.raises(InterruptedError, match="stop preparation"):
        orchestration.prepare_inputs(
            store,
            "research-test",
            evidence,
            spec,
            checkpoint=lambda *args: pytest.fail("No completed checkpoint is expected"),
            progress=lambda *args: None,
            cancelled=cancelled,
        )
    assert len(scopes) == 1 and scopes[0]._closed
    assert _bundle_validation.get() is None


def test_saved_preparation_requires_exact_portfolio(app, client, monkeypatch):
    store, evidence, spec = request_inputs(app, client)
    different = deepcopy(evidence)
    different["portfolio"]["capital"] += 1
    monkeypatch.setattr(service, "read_artifact", lambda *args: different)
    monkeypatch.setattr(
        orchestration,
        "_prepare_prices",
        lambda *args, **kwargs: pytest.fail("Changed saved inputs must be rejected"),
    )
    with pytest.raises(ValueError, match="Saved calculation inputs do not match"):
        prepare(
            store,
            evidence,
            spec,
            saved={"phase": "calculation", "inputs_artifact": "a" * 64, "calculation": None},
        )


@pytest.mark.parametrize("tamper", ["coverage", "receipt", "receipt_reference", "prices"])
def test_completed_preparation_rechecks_execution_integrity_without_writes(
    app, client, monkeypatch, tmp_path, tamper
):
    from test_acquisition import reference

    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    store, evidence, spec = request_inputs(app, client)
    (prepared, _), _ = prepare(store, evidence, spec)
    monkeypatch.setattr(
        service,
        "save_artifact",
        lambda *args, **kwargs: pytest.fail("Readmission must not publish or acquire inputs"),
    )
    assert orchestration.validate_prepared_inputs(store, prepared, spec) is prepared
    changed = deepcopy(prepared)
    if tamper == "coverage":
        changed["signal_coverage"]["eligible_signals"] += 1
    elif tamper == "receipt":
        changed["acquisition_receipts"] = [{"sha256": "a" * 64, "receipt": {"bad": True}}]
    elif tamper == "receipt_reference":
        changed["snapshot"]["provenance"]["broker_receipts"] = [{"sha256": "a" * 64}]
    else:
        series = next(iter(changed["snapshot"]["bars"].values()))
        next(iter(series.values()))["close"] = -1
    with pytest.raises(ValueError):
        orchestration.validate_prepared_inputs(store, changed, spec)


@pytest.mark.parametrize("tamper", [None, "context", "execution", "portfolio"])
def test_seeded_run_performs_original_context_and_feature_validation_once(monkeypatch, tamper):
    from test_portfolio_execution_features import source

    from research import execution_features, portfolio_execution_features
    from research.execution_policy import normalize_policy
    from research.portfolio import execution_versions, fingerprint, normalize
    from research.portfolio_coverage import prepare as prepare_coverage
    from services import research_portfolio_features, research_stock_market_context

    evidence, context, volume = source(symbols=("AAA", "BBB"), count=190)
    strategy = evidence["strategies"][0]
    strategy["source_id"] = "a" * 32
    strategy["execution_policy"] = normalize_policy(
        {**strategy["execution_policy"], "exit_mode": "atr"}
    )
    definition = {key: value for key, value in strategy.items() if key != "signals"}
    evidence["portfolio"] = normalize(
        {
            "capital": 100000,
            "strategies": [definition],
            "risk_limits": strategy["execution_policy"]["risk_limits"],
        }
    )
    evidence["versions"] = execution_versions(evidence["portfolio"])
    evidence["acquisition_receipts"] = []
    evidence = prepare_coverage(evidence)
    prepared = portfolio_execution_features.prepare(
        execution_features.prepare(evidence, context), context, volume
    )
    spec = {"portfolio": evidence["portfolio"], "versions": evidence["versions"]}
    raw = {
        key: deepcopy(value)
        for key, value in evidence.items()
        if key not in ("frozen_prices", "automatic_stock_market_context")
    }
    raw["snapshot"] = {"sessions": [], "bars": {}, "provenance": {"interval": "D"}}
    calls = {"context": 0, "execution": 0, "portfolio": 0}

    def counted(key, function):
        def validate(*args, **kwargs):
            calls[key] += 1
            return function(*args, **kwargs)

        return validate

    monkeypatch.setattr(
        research_stock_market_context,
        "prepare_context",
        counted("context", research_stock_market_context.prepare_context),
    )
    monkeypatch.setattr(
        execution_features, "prepare", counted("execution", execution_features.prepare)
    )
    monkeypatch.setattr(
        research_portfolio_features,
        "prepare",
        counted("portfolio", research_portfolio_features.prepare),
    )
    if tamper == "context":
        prepared["automatic_stock_market_context"]["id"] = "b" * 64
    elif tamper == "execution":
        prepared["execution_features"]["id"] = "b" * 64
    elif tamper == "portfolio":
        prepared["portfolio_execution_features"]["id"] = "b" * 64
    assert orchestration.validate_prepared_inputs(object(), prepared, spec) is prepared
    assert calls == {"context": 0, "execution": 0, "portfolio": 0}
    monkeypatch.setattr(service, "read_artifact", lambda *args: deepcopy(prepared))
    monkeypatch.setattr(service, "save_artifact", lambda _, value: fingerprint(value))
    monkeypatch.setattr(
        orchestration,
        "_prepare_prices",
        lambda *args, **kwargs: pytest.fail("Seeded run must not acquire execution prices"),
    )

    def boundary():
        raise RuntimeError("validated before calculation")

    with pytest.raises(ValueError if tamper else RuntimeError):
        orchestration.run(
            object(),
            "research-test",
            raw,
            spec,
            saved={"phase": "calculation", "inputs_artifact": "c" * 64, "calculation": None},
            checkpoint=lambda *args: None,
            progress=lambda *args: None,
            cancelled=lambda: None,
            boundary=boundary,
        )
    assert calls == {
        "context": 1,
        "execution": 0 if tamper == "context" else 1,
        "portfolio": 0 if tamper in ("context", "execution") else 1,
    }
