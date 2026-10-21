"""V13 chosen settings remain native, frozen and reusable outside the search."""

# ruff: noqa: F811 -- isolated shared application fixtures

from copy import deepcopy

import pytest
from test_jobs import app, client  # noqa: F401
from test_library import BASE, OWNER, create
from test_portfolio_capacity_integration import prepared
from test_portfolio_workflow import uploaded

from database.research_db import ResearchJob, ResearchStore
from research.portfolio import execution_versions, normalize
from research.portfolio_constraint_evidence import assess
from research.portfolio_validation import partition
from research.report_contract import settings_identity
from services import research_library as library
from services import research_portfolio as portfolio
from services import scanner_research_service as service
from services.research_setup_reuse import reuse_draft

pytestmark = pytest.mark.timeout(180)


def fixed_input(value):
    earlier, _, _ = partition(value, prepare_period="selection")
    spec = deepcopy(earlier["portfolio"])
    for key in ("automatic_research", "optimization", "validation"):
        spec.pop(key, None)
    for strategy in spec["strategies"]:
        strategy["search"] = {}
    spec = normalize(spec)
    earlier["portfolio"] = spec
    earlier["versions"] = execution_versions(spec)
    earlier["strategies"] = [{**row, "search": {}} for row in earlier["strategies"]]
    # The controlled completed-study fixture calculates only its original
    # development period; use the existing retained-context subset admission.
    earlier["replay_origin"] = {"period": "selection", "controlled_fixture": True}
    return earlier


@pytest.fixture(scope="module")
def retained(tmp_path_factory):
    value = prepared()
    value["receipt"] = {
        "input_type": "portfolio",
        "signal_count": len(value["signals"]),
        "symbol_count": len({row["symbol"] for row in value["signals"]}),
        "date_from": min(row["date"] for row in value["signals"]),
        "date_to": max(row["date"] for row in value["signals"]),
        "name": "Controlled v13 saved replay",
        "warnings": [],
    }
    from research.automatic_protocol import compile_recipe

    hours = value["automatic_stock_market_context"]["market"]["calendar"]["session_hours"]
    value["snapshot"]["session_hours"] = {
        day: deepcopy(hours[day]) for day in value["snapshot"]["sessions"]
    }
    # The earlier numerical fixture used implicit default hours. Freeze this
    # fixture's explicit hours before any result, using the real producers.
    from research.execution_features import prepare as prepare_atr
    from research.portfolio_execution_features import prepare as prepare_portfolio_features

    volume_records = value.pop("portfolio_execution_features")["volume_records"]
    value.pop("execution_features")
    value = prepare_atr(value, value["automatic_stock_market_context"])
    value = prepare_portfolio_features(
        value, value["automatic_stock_market_context"], volume_records
    )
    value["automatic_recipe"] = compile_recipe(value)
    fixed = fixed_input(deepcopy(value))
    store = ResearchStore(tmp_path_factory.mktemp("native-capacity-replay"))
    store.initialize()
    try:
        report, _ = portfolio.run(
            store,
            OWNER,
            fixed,
            {"portfolio": fixed["portfolio"], "versions": fixed["versions"]},
            checkpoint=lambda *a: None,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
        assert report["policy_version"] == "vectorbt-joint-causal-bars-v5"
        assert assess(report, value["portfolio_execution_features"])["status"] == "supported"
        config_id = settings_identity(report["strategies"])
        report["automatic_research"] = {
            "version": "automatic-hypotheses-v13",
            "selected_config_id": config_id,
        }
        report["experiment"] = {
            "rows": [
                {
                    "config_id": config_id,
                    "trial_number": 7,
                    "strategies": deepcopy(report["strategies"]),
                    "summary": deepcopy(report["summary"]),
                }
            ],
            "recommendation_id": config_id,
            "objective_winner_id": config_id,
        }
        return value, report
    finally:
        store.close()


def seed(app, retained):
    value, report = deepcopy(retained)
    store = app.extensions["research_store"]
    _, source = service.register_source(store, OWNER, value)
    queued = service.submit(
        store,
        OWNER,
        source["id"],
        {"initial_capital": value["portfolio"]["capital"]},
        kind="portfolio_optimize",
        specification={"portfolio": value["portfolio"], "versions": value["versions"]},
    )
    artifact = service.save_artifact(
        store,
        {
            "kind": "portfolio_optimize",
            "inputs_artifact": service.save_artifact(store, value),
            "result": report,
        },
    )
    with store.sessions.begin() as db:
        job = db.get(ResearchJob, queued["id"])
        job.status, job.result_artifact = "completed", artifact
    return store, queued["id"], value, report, artifact


def assert_controls(value, expected):
    assert value["portfolio"]["risk_limits"] == expected["portfolio"]["risk_limits"]
    assert value["versions"]["adapter_version"] == "vectorbt-portfolio-adapter-v5"
    for row in value["strategies"]:
        policy = row["execution_policy"]
        assert policy["capacity_mode"] == "prior20_broker_reported"
        assert policy["correlation_mode"] == "prior60_positive_080"
        assert policy["risk_limits"] == expected["portfolio"]["risk_limits"]
    assert value["portfolio_execution_features"] == expected["portfolio_execution_features"]


def test_selected_exact_replay_preserves_frozen_constraints_without_acquisition(
    app, client, retained, monkeypatch
):
    store, job_id, value, report, artifact = seed(app, retained)
    before = service.read_artifact(store, artifact)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Exact native replay must not acquire data, broker credentials or run search"
        )

    for target in (
        "services.research_portfolio._prepare_prices",
        "services.research_sources.resolve_broker_session",
        "services.research_historify.NativeHistorifyArchive.read",
        "services.research_historify.NativeHistorifyArchive.read_many",
        "services.research_native_prices.native_history",
        "research.connectors.optuna_portfolio.run_search",
    ):
        monkeypatch.setattr(target, forbidden)
    job, replay = portfolio.replay_inputs(store, OWNER, job_id)
    assert job.id == job_id
    assert_controls(replay, value)
    assert "automatic_research" not in replay["portfolio"]
    assert "optimization" not in replay["portfolio"]
    assert replay["frozen_prices"] is True
    assert replay["replay_origin"]["config_id"] == settings_identity(report["strategies"])
    assert replay["replay_origin"]["trial_number"] == 7
    repeated, frozen = portfolio.run(
        store,
        OWNER,
        replay,
        {"portfolio": replay["portfolio"], "versions": replay["versions"]},
        checkpoint=lambda *a: None,
        progress=lambda *a: None,
        cancelled=lambda: None,
    )
    assert repeated["summary"] == report["summary"]
    assert repeated["execution"]["capacity"] == report["execution"]["capacity"]
    assert repeated["execution"]["correlation"] == report["execution"]["correlation"]
    assert repeated["ledger"] == report["ledger"]
    assert assess(repeated, replay["portfolio_execution_features"])["status"] == "supported"
    assert_controls(frozen, value)
    assert service.read_artifact(store, artifact) == before


def test_trial_replay_and_reserved_period_keep_the_same_v5_controls(app, client, retained):
    store, job_id, value, report, _ = seed(app, retained)
    config_id = settings_identity(report["strategies"])
    _, trial = portfolio.replay_inputs(store, OWNER, job_id, trial_id=config_id)
    assert_controls(trial, value)
    _, later = portfolio.replay_inputs(store, OWNER, job_id, period="evaluation")
    assert_controls(later, value)
    assert later["replay_origin"]["period"] == "evaluation"
    assert later["snapshot"]["sessions"][0] > trial["snapshot"]["sessions"][-1]


def test_capacity_setup_persists_and_reuses_modes_and_limits(app, client):
    value = prepared()
    raw = (
        "Date,Symbol\n"
        + "\n".join(f"{row['date']},{row['symbol']}" for row in value["signals"])
        + "\n"
    )
    source_id = uploaded(client, raw.encode())
    draft = library.fresh_draft()
    draft["portfolio"] = deepcopy(value["portfolio"])
    draft["optimizing"] = True
    draft["equalWeights"] = False
    for row in draft["portfolio"]["strategies"]:
        row["source_id"] = source_id
    original = deepcopy(draft)
    experiment = create(client, draft, name="Capacity-constrained research")
    reopened = client.get(f"{BASE}/experiments/{experiment['id']}")
    assert reopened.status_code == 200
    persisted = reopened.json["draft"]
    assert persisted["portfolio"]["risk_limits"] == value["portfolio"]["risk_limits"]
    assert (
        persisted["portfolio"]["strategies"][0]["execution_policy"]
        == original["portfolio"]["strategies"][0]["execution_policy"]
    )
    saved = client.post(
        f"{BASE}/experiments/{experiment['id']}/versions",
        json={"revision": experiment["revision"], "name": "Reviewed volume and correlation limits"},
    )
    assert saved.status_code == 201, saved.json
    version = saved.json["version"]
    retained_version = client.get(f"{BASE}/experiments/{experiment['id']}/versions/{version['id']}")
    assert retained_version.status_code == 200
    assert (
        retained_version.json["draft"]["portfolio"]["risk_limits"]
        == value["portfolio"]["risk_limits"]
    )
    reused = reuse_draft(app.extensions["research_store"], OWNER, persisted, mode="backtest")
    payload = normalize(library.portfolio_payload(reused["draft"]))
    assert payload["risk_limits"] == value["portfolio"]["risk_limits"]
    assert (
        payload["strategies"][0]["execution_policy"]
        == original["portfolio"]["strategies"][0]["execution_policy"]
    )
    assert execution_versions(payload)["adapter_version"] == "vectorbt-portfolio-adapter-v5"
    assert draft == original


@pytest.mark.parametrize("mutation", ["unknown_version", "extra_limit"])
def test_unknown_saved_risk_schema_is_rejected_before_persistence(client, mutation):
    draft = library.fresh_draft()
    draft["portfolio"]["risk_limits"] = deepcopy(prepared()["portfolio"]["risk_limits"])
    if mutation == "unknown_version":
        draft["portfolio"]["risk_limits"]["version"] = "research-risk-limits-v999"
    else:
        draft["portfolio"]["risk_limits"]["invented_liquidity"] = 1
    rejected = client.post(f"{BASE}/experiments", json={"draft": draft})
    assert rejected.status_code == 400
    assert client.get(f"{BASE}/experiments").json["items"] == []
