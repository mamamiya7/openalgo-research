"""Entry gates rerun the real joint account, retain evidence, and never download."""

import copy
import os
import time

import pytest
from test_regimes import evidence as index_evidence
from test_vectorbt_portfolio import snapshot

from database.research_db import ResearchJob, ResearchStore
from research.condition_replay import apply
from research.data import validate_snapshot
from research.portfolio import execution_versions, normalize
from services import research_condition_replay as condition_service
from services import research_portfolio
from services import scanner_research_service as service
from services import scanner_research_worker as worker
from services.research_regimes import request_context

NATIVE_TIMEOUT = 600 if os.name == "nt" else 180
pytestmark = pytest.mark.timeout(NATIVE_TIMEOUT)


def inputs():
    market_prices = [100.0] * 151 + [100 + i * 2 for i in range(1, 50)]
    series = index_evidence(market_prices)
    days = series["required_dates"]
    calendar = {
        "sessions": list(days),
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in days},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }
    prices = snapshot(symbols=("AAA", "BBB", "ZZZ"), days=days[149:179])
    for day in prices["sessions"]:
        value = 110 if day >= days[175] else 100
        prices["bars"]["BBB"][day] = {"open": value, "high": value, "low": value, "close": value}
    observations = [
        {"symbol": "AAA", "date": days[150], "row": 2},
        {"symbol": "BBB", "date": days[170], "row": 3},
        {
            "symbol": "ZZZ",
            "date": days[152],
            "row": 4,
            "research_exclusion": "Original broker price exclusion",
        },
        {"symbol": "AAA", "date": days[178], "row": 5},
    ]
    portfolio = normalize(
        {
            "name": "Condition test",
            "capital": 10000,
            "strategies": [
                {
                    "id": "a",
                    "name": "A",
                    "source_id": "a" * 32,
                    "allocation_pct": 100,
                    "config": {
                        "order_size_pct": 100,
                        "cost_bps": 0,
                        "hold_sessions": 45,
                        "stop_pct": 99,
                        "target_pct": 100,
                    },
                }
            ],
        }
    )
    strategies = [{**portfolio["strategies"][0], "signals": observations}]
    prices["coverage"] = validate_snapshot(prices, observations)
    evidence = {
        "portfolio": portfolio,
        "strategies": strategies,
        "signals": [dict(row, strategy_id="a") for row in observations],
        "snapshot": prices,
        "versions": execution_versions(portfolio),
        "frozen_prices": True,
        "receipt": {
            "name": "Condition test",
            "signal_count": len(observations),
            "symbol_count": 3,
            "input_rows": len(observations),
            "input_type": "portfolio",
            "date_from": observations[0]["date"],
            "date_to": observations[-1]["date"],
            "strategy_count": 1,
            "warnings": [],
        },
    }
    market = {
        "evidence": series,
        "calendar": calendar,
        "context": request_context(),
        "acquisition_receipts": [],
    }
    return evidence, market


CONDITION = {"strategy_id": "a", "dimension": "trend", "regime": "up"}


def evaluate(evidence):
    from research.connectors.vectorbt_portfolio import evaluate as native

    return native(evidence["strategies"], evidence["snapshot"], evidence["portfolio"]["capital"])


@pytest.mark.timeout(NATIVE_TIMEOUT)
def test_entry_gate_frees_cash_for_a_previously_skipped_signal_and_retains_every_row():
    evidence, market = inputs()
    before = copy.deepcopy(evidence)
    baseline = evaluate(evidence)
    assert baseline["ledger"][0]["quantity"] == 100
    assert baseline["ledger"][1]["status"] == "skipped"
    strategies, gate = apply(evidence, market, CONDITION)
    filtered = evaluate({**evidence, "strategies": strategies})
    assert [row["status"] for row in filtered["ledger"]] == [
        "excluded",
        "pending",
        "excluded",
        "pending",
    ]
    assert filtered["ledger"][1]["quantity"] == 100
    assert filtered["summary"]["final_equity"] == 11000
    assert baseline["summary"]["final_equity"] == 10000
    assert filtered["ledger"][2]["reason"] == "Original broker price exclusion"
    assert gate["counts"] == {
        "allowed": 1,
        "filtered": 1,
        "unknown": 0,
        "original_excluded": 1,
        "pending": 1,
        "unaffected": 0,
    }
    assert [row["source_row"] for row in filtered["ledger"]] == [2, 3, 4, 5]
    assert evidence == before


def test_unknown_missing_history_excludes_without_deleting_observations():
    evidence, market = inputs()
    day = market["evidence"]["required_dates"][140]
    market["evidence"]["bars"].pop(day)
    from research.market_series import freeze_series

    old = market["evidence"]
    market["evidence"] = freeze_series(
        old["descriptor"], old["required_dates"], market["calendar"], old["bars"]
    )
    strategies, gate = apply(evidence, market, CONDITION)
    assert gate["counts"]["unknown"] == 2 and gate["counts"]["allowed"] == 0
    assert len(strategies[0]["signals"]) == 4
    assert "unavailable" in strategies[0]["signals"][0]["research_exclusion"]


def test_gate_never_consumes_the_entry_day_close_or_later_prices():
    evidence, market = inputs()
    strategies, original = apply(evidence, market, CONDITION)
    modified = copy.deepcopy(market)
    from research.market_series import freeze_series

    series = modified["evidence"]
    cutoff = original["membership"][1]["scheduled_at"][:10]
    for day, bar in series["bars"].items():
        if day >= cutoff:
            for key in ("open", "high", "low", "close"):
                bar[key] *= 0.01
    modified["evidence"] = freeze_series(
        series["descriptor"], series["required_dates"], modified["calendar"], series["bars"]
    )
    changed, later = apply(evidence, modified, CONDITION)
    assert changed == strategies
    assert later["membership"] == original["membership"]
    assert later["counts"] == original["counts"]


def test_gate_outside_pinned_calendar_does_not_reuse_a_stale_label():
    evidence, market = inputs()
    market["calendar"]["sessions"].remove(evidence["snapshot"]["sessions"][22])
    _, gate = apply(evidence, market, CONDITION)
    assert gate["counts"]["unknown"] == 1
    assert gate["membership"][1]["state"] == "unknown"


def test_minute_entry_uses_session_open_context_before_intraday_signal():
    evidence, market = inputs()
    days = evidence["snapshot"]["sessions"][-3:]
    evidence["snapshot"] = snapshot(symbols=("AAA",), minute=True, days=days)
    # Pinned index calendar has longer sessions; the actual 09:16 entry is still inside both.
    signal = {"symbol": "AAA", "date": days[0], "timestamp": days[0] + "T09:15:00+05:30", "row": 8}
    evidence["strategies"][0]["signals"] = [signal]
    evidence["signals"] = [dict(signal, strategy_id="a")]
    strategies, gate = apply(evidence, market, CONDITION)
    assert gate["membership"][0]["scheduled_at"].endswith("09:16:00+05:30")
    assert gate["membership"][0]["decision_at"].endswith("09:15:00+05:30")
    assert gate["counts"]["allowed"] == 1
    assert strategies[0]["signals"] == [signal]


def test_other_strategies_and_original_exclusions_are_untouched():
    evidence, market = inputs()
    other = copy.deepcopy(evidence["strategies"][0])
    other.update(id="b", name="B", allocation_pct=50)
    evidence["strategies"][0]["allocation_pct"] = 50
    evidence["strategies"].append(other)
    strategies, gate = apply(evidence, market, CONDITION)
    assert strategies[1] == other
    assert gate["counts"]["unaffected"] == 4
    assert gate["counts"]["original_excluded"] == 1


@pytest.mark.timeout(NATIVE_TIMEOUT)
def test_all_filtered_account_remains_cash_with_all_ledger_rows():
    evidence, market = inputs()
    strategies, gate = apply(evidence, market, {**CONDITION, "regime": "down"})
    filtered = evaluate({**evidence, "strategies": strategies})
    assert gate["counts"]["filtered"] == 2
    assert len(filtered["ledger"]) == 4
    assert filtered["summary"]["final_equity"] == 10000
    assert all(row["quantity"] == 0 for row in filtered["ledger"])


@pytest.mark.parametrize("change", ["nautilus", "unfrozen", "condition", "unknown-strategy"])
def test_unsupported_requests_fail_without_acquisition(change):
    evidence, market = inputs()
    condition = dict(CONDITION)
    if change == "nautilus":
        evidence["portfolio"]["engine"] = "nautilus"
    elif change == "unfrozen":
        evidence["frozen_prices"] = False
    elif change == "condition":
        condition["regime"] = "crisis"
    else:
        condition["strategy_id"] = "missing"
    with pytest.raises(ValueError):
        apply(evidence, market, condition)


@pytest.fixture
def saved(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "condition-store")
    store.initialize()
    evidence, market = inputs()
    _, source = service.register_source(store, "owner", evidence)
    evidence["portfolio"]["strategies"][0]["source_id"] = source["id"]
    evidence["strategies"][0]["source_id"] = source["id"]
    _, source = service.register_source(store, "owner", evidence)
    job = service.submit(
        store,
        "owner",
        source["id"],
        {"initial_capital": 10000},
        kind="portfolio_backtest",
        specification={"portfolio": evidence["portfolio"], "versions": evidence["versions"]},
    )
    monkeypatch.setattr(
        "services.research_portfolio._prepare_prices",
        lambda *a, **k: pytest.fail("Condition replay requested prices"),
    )
    run(store)
    parent = service.get_job(store, "owner", job["id"])
    assert parent.status == "completed", (parent.status, parent.error)
    from services.research_library import from_job

    from_job(
        store, "owner", {"job_id": parent.id, "mode": "backtest", "request_id": "parent-library-1"}
    )
    from services.research_analysis import submit as submit_analysis

    analysis_job = submit_analysis(store, "owner", parent.id, market_conditions=True)
    analysis_id = analysis_job["analysis_job_id"]
    artifact = service.save_artifact(
        store,
        {
            "kind": "portfolio_analysis",
            "parent_result_artifact": parent.result_artifact,
            "result": {"market_conditions_evidence": market},
        },
    )
    with store.sessions.begin() as db:
        row = db.get(ResearchJob, analysis_id)
        row.status, row.result_artifact, row.updated_at = "completed", artifact, time.time()
    request = {**CONDITION, "analysis_artifact": artifact, "period": "selection"}
    yield store, parent, request
    store.close()


def run(store):
    worker.acquire(store, "condition-test")
    try:
        assert worker.run_one(store, "condition-test")
    finally:
        worker.release(store, "condition-test")


def result(store, job_id):
    job = service.get_job(store, "owner", job_id)
    assert job.status == "completed", (job.status, job.error)
    return service.read_artifact(store, job.result_artifact)["result"]


@pytest.mark.timeout(NATIVE_TIMEOUT)
def test_saved_job_native_run_exact_replay_and_backup_restore_are_offline(saved, tmp_path):
    store, parent, request = saved
    original = service.encoded(service.read_artifact(store, parent.result_artifact))
    submitted = condition_service.submit(
        store, "owner", parent.id, request, request_id="condition-1"
    )
    assert (
        condition_service.submit(store, "owner", parent.id, request, request_id="condition-1")["id"]
        == submitted["id"]
    )
    run(store)
    child = result(store, submitted["id"])
    assert child["condition_replay"]["delta"]["net_return_pct"] == pytest.approx(10)
    assert child["condition_replay"]["counts"]["allowed"] == 1
    baseline = service.read_artifact(store, parent.result_artifact)["result"]
    assert child["evaluation_basis"]["source_id"] == baseline["evaluation_basis"]["source_id"]
    assert child["evaluation_basis"]["cohort_id"] != baseline["evaluation_basis"]["cohort_id"]
    assert child["evaluation_basis"]["prices_id"] == baseline["evaluation_basis"]["prices_id"]
    from research.evaluation_basis import comparison_status
    from services.research_storage import backup_store, prune_orphans, restore_store

    assert (
        "execution"
        in comparison_status(child["evaluation_basis"], baseline["evaluation_basis"])["differences"]
    )

    old = time.time() - 7200
    for artifact in (store.root / "artifacts").iterdir():
        if artifact.is_file():
            os.utime(artifact, (old, old))
    prune_orphans(store, apply=True)
    backup_store(store, tmp_path / "condition-backup")
    restore_store(tmp_path / "condition-backup", tmp_path / "restored")
    restored = ResearchStore(tmp_path / "restored")
    try:
        restored.initialize()
        replay = research_portfolio.rerun(
            restored, "owner", submitted["id"], request_id="exact-replay-1"
        )
        run(restored)
        repeated = result(restored, replay["id"])
        assert repeated["summary"] == child["summary"]
        assert repeated["ledger"] == child["ledger"]
        assert repeated["condition_replay"] == child["condition_replay"]
    finally:
        restored.close()
    assert service.encoded(service.read_artifact(store, parent.result_artifact)) == original


def test_saved_request_owner_analysis_published_period_and_no_chaining_guards(saved):
    store, parent, request = saved
    with pytest.raises((ValueError, LookupError)):
        condition_service.submit(store, "other", parent.id, request)
    with pytest.raises(ValueError, match="completed market conditions"):
        condition_service.submit(
            store, "owner", parent.id, {**request, "analysis_artifact": "f" * 64}
        )
    with pytest.raises(ValueError, match="published later period"):
        condition_service.submit(store, "owner", parent.id, {**request, "period": "validation"})
    child = condition_service.submit(
        store, "owner", parent.id, request, request_id="condition-guard-1"
    )
    run(store)
    with pytest.raises(ValueError, match="original unfiltered"):
        condition_service.submit(store, "owner", child["id"], request)


def test_mutated_condition_input_or_membership_rejected_before_execution(saved):
    store, parent, request = saved
    child = condition_service.submit(
        store, "owner", parent.id, request, request_id="condition-mutate-1"
    )
    evidence = service.source_for(store, "owner", child["source_id"])
    evidence = condition_service.prepare(store, evidence)
    changed = copy.deepcopy(evidence)
    changed["strategies"][0]["signals"][0].pop("research_exclusion")
    with pytest.raises(ValueError, match="membership changed"):
        condition_service.verify(store, changed)
    changed = copy.deepcopy(evidence)
    changed["snapshot"]["bars"]["AAA"][changed["snapshot"]["sessions"][0]]["close"] = 200
    with pytest.raises(ValueError, match="execution inputs changed"):
        condition_service.verify(store, changed)


def test_http_submission_only_pins_inputs_and_defers_numerical_gate_to_worker(saved, monkeypatch):
    store, parent, request = saved
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "research.condition_replay.apply", lambda *a, **k: pytest.fail("HTTP calculated a gate")
        )
        submitted = condition_service.submit(
            store, "owner", parent.id, request, request_id="worker-only-gate"
        )
    queued = service.source_for(store, "owner", submitted["source_id"])
    assert "condition_gate" not in queued
    assert "research_exclusion" not in queued["strategies"][0]["signals"][0]
    run(store)
    assert result(store, submitted["id"])["condition_replay"]["counts"]["filtered"] == 1
