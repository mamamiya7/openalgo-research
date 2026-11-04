"""One native baseline and one bounded remedy on prepared saved data; no search."""

# ruff: noqa: F811 -- only preparation fixtures, never completed-study fixtures
import json
import time
from copy import deepcopy

import pytest
from test_automatic_actions_runtime import acting  # noqa: F401
from test_automatic_decision_runtime import deciding  # noqa: F401
from test_automatic_design_runtime import designed  # noqa: F401
from test_available_history_runtime import available  # noqa: F401

from database.research_db import ResearchExperiment, ResearchJob, ResearchStore, ResearchSupervisor
from research import capital_planning
from research.automatic_actions import entry_prices, request_prices
from research.automatic_protocol import compile_recipe
from research.connectors import vectorbt_portfolio
from research.execution_features import prepare as prepare_execution
from research.portfolio import execution_versions, normalize
from research.portfolio_execution_features import prepare as prepare_portfolio
from research.report_contract import fingerprint, settings_identity
from research.supervisor_accounts import FrozenAccounts
from services import research_supervisor as supervisor
from services import research_supervisor_program as program
from services import scanner_research_service as service


@pytest.mark.timeout(60)
def test_native_saved_parent_to_bounded_remedy_and_exact_checkpoint_resume(
    acting, tmp_path, monkeypatch
):
    store = ResearchStore(tmp_path / "research")
    store.initialize()
    owner, conversation, parent_id = "small-native-owner", "small-native-chat", "a" * 32
    # Make the single recorded baseline unable to buy one share. The real native
    # ledger, rather than fabricated profit or failure flags, justifies funding.
    source = deepcopy(acting)
    source["portfolio"]["capital"] = 1
    source["portfolio"] = normalize(source["portfolio"])
    for row, normalized in zip(
        source["strategies"], source["portfolio"]["strategies"], strict=True
    ):
        row.update(deepcopy(normalized))
    source["versions"] = execution_versions(source["portfolio"])
    source["snapshot"]["session_hours"] = {
        day: {"open": "09:15", "close": "15:30"} for day in source["snapshot"]["sessions"]
    }
    for key in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        source.pop(key, None)
    context = source["automatic_stock_market_context"]
    source = prepare_portfolio(prepare_execution(source, context), context)
    source["automatic_recipe"] = compile_recipe(source)
    source["receipt"] = {
        "name": "Tiny prepared native fixture",
        "signal_count": len(source["signals"]),
        "symbol_count": 1,
        "input_type": "dated_signals",
    }
    source["snapshot"].setdefault("coverage", {"status": "complete"})
    calls = []
    original_evaluate = vectorbt_portfolio.evaluate

    def counted(*args, **kwargs):
        calls.append(1)
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", counted)
    # Any attempt to leave these frozen inputs is a failure of this test.
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *a, **k: pytest.fail("Saved-data remedy requested a broker"),
    )
    try:
        with FrozenAccounts(source, counted, lambda: None) as accounts:
            hypothesis = next(h for h in accounts.context.catalog() if h["id"] == "none")
            baseline = accounts.calculate(
                source["automatic_recipe"]["baseline"], hypothesis, "search"
            )
            capacity = capital_planning.selected_account_plan(
                baseline["strategies"],
                1,
                entry_prices(
                    baseline, baseline["strategies"], accounts.window("search")["snapshot"]
                ),
                request_prices=request_prices(
                    baseline, baseline["strategies"], accounts.window("search")["snapshot"]
                ),
                version=capital_planning.CLOSED_LOOP_VERSION,
            )
            diagnosis = capital_planning.selected_account_receipt(baseline, capacity, 0)
        assert len(calls) == 1
        assert diagnosis["counts"]["whole_share_affordability"] > 0
        baseline["automatic_research"] = {
            "version": "automatic-hypotheses-v16",
            "recipe": deepcopy(source["automatic_recipe"]),
            "counts": {"proposals": 0, "attempts_reserved": 1, "simulations": 1},
            "capital_planning": {"diagnosis": {"categories": diagnosis["counts"]}},
            "decision": {"checks": [], "prefix_diagnostics": []},
        }
        _, receipt = service.register_source(store, owner, source)
        inputs_id = service.save_artifact(store, source)
        specification = {"portfolio": source["portfolio"], "versions": source["versions"]}
        result_id = service.save_artifact(
            store,
            {
                "job_id": parent_id,
                "kind": "portfolio_optimize",
                "inputs_artifact": inputs_id,
                "result": baseline,
                "specification": specification,
            },
        )
        with store.sessions.begin() as db:
            db.add(
                ResearchJob(
                    id=parent_id,
                    owner=owner,
                    source_id=receipt["id"],
                    config="{}",
                    status="completed",
                    progress=100,
                    created_at=time.time(),
                    updated_at=time.time(),
                    result_artifact=result_id,
                )
            )
            db.add(
                ResearchExperiment(
                    job_id=parent_id,
                    kind="portfolio_optimize",
                    specification=json.dumps(specification),
                    identity="b" * 64,
                )
            )
        opened = supervisor.open_supervisor(
            store, owner, parent_id, conversation_id=conversation, request_id="small-native-parent"
        )
        proposal = supervisor.propose(
            store,
            owner,
            opened["id"],
            "funding_and_size",
            "Native baseline cannot fund a whole share",
            conversation_id=conversation,
        )
        queued = supervisor.advance(store, owner, opened["id"], conversation_id=conversation)
        job_id = queued["proposals"][0]["job_id"]
        job = service.get_job(store, owner, job_id)
        evidence = service.source_for(store, owner, job.source_id)
        with store.sessions() as db:
            spec = json.loads(db.get(ResearchExperiment, job_id).specification)
        checkpoints = []

        def checkpoint(value, progress):
            checkpoints[:] = [deepcopy(value)]

        kwargs = {
            "checkpoint": checkpoint,
            "progress": lambda *a: None,
            "cancelled": lambda: None,
            "job_id": job_id,
        }
        result, _ = program.run(store, owner, evidence, spec, saved=None, **kwargs)
        validation = result["supervisor_validation"]
        native = {key: value for key, value in result.items() if key != "supervisor_validation"}
        assert validation["native_report_id"] == fingerprint(native)
        assert validation["selected_config_id"] == settings_identity(result["strategies"])
        assert (
            validation["capital"]
            == result["summary"]["initial_capital"]
            == result["portfolio"]["capital"]
        )
        assert validation["capital"] > 1
        assert validation["supervisor_id"] == opened["id"]
        assert validation["proposal_id"] == proposal["proposal"]["id"]
        assert validation["source_result_artifact"] == result_id
        assert validation["outcome"] in ("rejected", "blocked")
        assert validation["independently_validated"] is False
        assert validation["operating_validation"]["selected_report_id"] == fingerprint(native)
        with store.sessions() as db:
            state = json.loads(db.get(ResearchSupervisor, opened["id"]).state)
        spent = state["proposals"][0]["spent"]
        assert spent == len(calls) - 1
        assert state["proposals"][0]["completed"] == spent
        assert 1 < spent <= proposal["proposal"]["reserved_accounts"] <= 17
        saved, calls_before = deepcopy(checkpoints[-1]), len(calls)
        reopened, _ = program.run(store, owner, evidence, spec, saved=saved, **kwargs)
        assert reopened == result
        assert len(calls) == calls_before
        with store.sessions() as db:
            restored = json.loads(db.get(ResearchSupervisor, opened["id"]).state)
        assert restored["proposals"][0]["spent"] == spent
        assert restored["proposals"][0]["completed"] == spent
    finally:
        store.close()
