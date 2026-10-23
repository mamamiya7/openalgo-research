"""Three native accounts and a closed-store saved-setup reopen, without a search."""

from copy import deepcopy
from time import perf_counter

import pytest
from test_automatic_design import BINS, OPPORTUNITY_DATES, balanced
from test_portfolio_wide_allocation import account

from database.research_db import ResearchJob, ResearchStore
from research.automatic_design import assess, descriptor
from research.capital_planning import account_receipt, finish, plan, settings_for
from research.connectors.vectorbt_portfolio import evaluate
from research.report_contract import fingerprint
from services import research_library as library
from services import scanner_research_service as service


@pytest.mark.timeout(30)
def test_small_native_capital_design_saved_setup_and_closed_store_reopen(tmp_path, record_property):
    started = perf_counter()
    _, attached, _, _ = account(mode="original")
    baseline = deepcopy(attached["strategies"])
    baseline[0]["config"].update(
        initial_capital=10000, order_size_pct=3, cost_bps=0, slippage_bps=0
    )
    selected = deepcopy(baseline)
    selected[0]["config"]["stop_pct"] = 5
    frozen_limits = deepcopy(baseline[0]["execution_policy"]["risk_limits"])
    planned = plan(baseline, selected, 10000)
    assert planned["tickets"] == [{"strategy_id": baseline[0]["id"], "initial_ticket": 300}]
    assert len(attached["signals"]) == 3
    baseline_report = evaluate(baseline, attached["snapshot"], 10000)
    variants, receipts = [], []
    for amount in planned["candidate_capitals"][:2]:
        settings = settings_for(amount, baseline, selected, original_capital=10000)
        assert settings[0]["execution_policy"]["risk_limits"] == frozen_limits
        cfg = settings[0]["config"]
        assert amount * settings[0]["allocation_pct"] / 100 * cfg[
            "order_size_pct"
        ] / 100 == pytest.approx(300)
        native = evaluate(settings, attached["snapshot"], amount)
        variants.append(native)
        receipts.append(account_receipt(native, planned, amount))
    assert len(variants) == 2
    assert all(item["verified_sufficient"] for item in receipts)
    # The second variant is a test comparison, not a controller calculation
    # after sufficient funding: production stops at the first verified amount.
    funding = finish(planned, receipts[:1])
    assert funding["verified_capital"] < 10000
    assert funding["independently_validated"] is False
    concentrated = balanced()
    concentrated["ledger"][0]["pnl"] = 1000
    historical_check = assess(concentrated, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    assert not historical_check["qualified"]
    assert any("one stock" in reason for reason in historical_check["reasons"])

    owner, root = "small-smoke-owner", tmp_path / "isolated-research"
    store = ResearchStore(root)
    store.initialize()
    try:
        source_input = deepcopy(attached)
        dates = sorted({row["date"] for row in attached["signals"]})
        source_input["receipt"] = {
            "name": "Three-stock controlled smoke",
            "input_type": "csv",
            "signal_count": 3,
            "symbol_count": 3,
            "date_from": dates[0],
            "date_to": dates[-1],
            "warnings": [],
        }
        source_input["snapshot"]["coverage"] = baseline_report["coverage"]
        _, source = service.register_source(store, owner, source_input)
        chosen = deepcopy(variants[0])
        rows = [
            {
                **deepcopy(row),
                "source_id": source["id"],
                "type": "signals",
                "search": {},
            }
            for row in chosen["strategies"]
        ]
        portfolio = {
            "version": "research-portfolio-v1",
            "name": "Small capital planning result",
            "engine": "vectorbt",
            "capital": planned["candidate_capitals"][0],
            "risk_limits": frozen_limits,
            "strategies": rows,
        }
        chosen["portfolio"] = portfolio
        chosen["capital_planning"] = funding
        inputs = service.save_artifact(store, {"portfolio": portfolio})
        bundle = {"kind": "portfolio_backtest", "inputs_artifact": inputs, "result": chosen}
        artifact = service.save_artifact(store, bundle)
        original_fingerprint = fingerprint(bundle)
        assert service.read_artifact(store, artifact) == bundle
        with store.sessions.begin() as db:
            db.add(
                ResearchJob(
                    id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    owner=owner,
                    source_id=source["id"],
                    config="{}",
                    status="completed",
                    progress=100,
                    created_at=1,
                    updated_at=1,
                    result_artifact=artifact,
                )
            )
        copied = library.from_job(
            store,
            owner,
            {
                "job_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "mode": "backtest",
                "name": "Small native capital smoke",
                "request_id": "small-native-smoke-copy",
            },
        )
        saved = library.save_version(
            store,
            owner,
            copied["id"],
            {
                "revision": copied["revision"],
                "name": "Verified small native test",
            },
        )
        experiment_id, version_id = copied["id"], saved["version"]["id"]
    finally:
        store.close()
    reopened_store = ResearchStore(root)
    reopened_store.initialize()
    try:
        reopened = library.get_version(reopened_store, owner, experiment_id, version_id)
        assert reopened["parent_job_id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert reopened["parent_result_artifact"] == artifact
        assert reopened["draft"]["portfolio"]["capital"] == funding["verified_capital"]
        assert (
            reopened["draft"]["portfolio"]["strategies"][0]["execution_policy"]["risk_limits"]
            == frozen_limits
        )
        restored = service.read_artifact(reopened_store, artifact)
        assert restored == bundle
        assert fingerprint(restored) == original_fingerprint
        assert restored["result"]["capital_planning"] == funding
    finally:
        reopened_store.close()
    record_property("native_calculations", 3)
    record_property("stocks", 3)
    record_property("signals", 3)
    record_property("elapsed_seconds", perf_counter() - started)
