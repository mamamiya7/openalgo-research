"""Independent regression checks for portfolio data planning and frozen periods."""

# ruff: noqa: F811 -- shared isolated pytest fixtures

import copy
import os
import time

import pytest
from test_acquisition import reference
from test_jobs import app, client  # noqa: F401
from test_native_price_workflow import prepare_archive
from test_portfolio_validation import inputs
from test_portfolio_workflow import portfolio as request_for
from test_vectorbt_portfolio import signal, snapshot, strategy

from database.research_db import ResearchStore
from research import portfolio
from research.connectors.vectorbt_portfolio import _deadline, _schedule
from research.portfolio_validation import partition
from services.research_portfolio import run


def test_holdout_trims_native_raw_ohlc_as_well_as_calculation_ohlc():
    evidence = inputs()
    evidence["snapshot"]["raw_bars"] = copy.deepcopy(evidence["snapshot"]["bars"])
    before = copy.deepcopy(evidence)
    training, testing, info = partition(evidence)
    for period in (training, testing):
        assert period["snapshot"]["raw_bars"] == period["snapshot"]["bars"]
    assert max(training["snapshot"]["raw_bars"]["AAA"]) <= info["train_to"]
    assert min(testing["snapshot"]["raw_bars"]["AAA"]) >= info["test_from"]
    assert evidence == before


def test_minute_plan_omits_unused_calendar_padding_without_omitting_held_minutes():
    prices = snapshot(minute=True)
    rows = [
        strategy(
            "timed", signals=[signal(timestamp="09:15")], trade_horizon="intraday", hold_minutes=2
        )
    ]
    plan = portfolio.price_plan({"strategies": rows}, rows, prices)
    required = plan["required_timestamps"]["AAA"]
    assert plan["timeline"] == required
    assert len(plan["timeline"]) == 3
    compact = {
        **prices,
        "timeline": plan["timeline"],
        "bars": {
            symbol: {key: bar for key, bar in bars.items() if key in plan["timeline"]}
            for symbol, bars in prices["bars"].items()
        },
    }
    from research.connectors.vectorbt_portfolio import evaluate

    original = evaluate(rows, prices, 10000)
    result = evaluate(rows, compact, 10000)
    assert result["summary"] == original["summary"]
    assert result["ledger"] == original["ledger"]


def test_rapid_calculation_callbacks_keep_control_checks_bounded_and_check_completion(
    tmp_path, monkeypatch
):
    from research.connectors import vectorbt_portfolio
    from services import research_portfolio as service

    original = inputs()
    original["portfolio"].pop("optimization", None)
    original["portfolio"].pop("validation", None)
    for row in original["portfolio"]["strategies"]:
        row["search"] = {}
    original["versions"] = portfolio.execution_versions(original["portfolio"])
    times = iter(i / 1000 for i in range(1100))
    monkeypatch.setattr(service, "monotonic", lambda: next(times))
    controls = []

    def fake_evaluate(*args, progress):
        for i in range(1001):
            progress(i, 1000)
        return {"summary": {}, "strategies": original["strategies"]}

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", fake_evaluate)
    store = ResearchStore(tmp_path / "controls")
    store.initialize()
    try:
        service.run(
            store,
            "owner",
            original,
            {"portfolio": original["portfolio"], "versions": original["versions"]},
            checkpoint=lambda *args: None,
            progress=lambda *args: None,
            cancelled=lambda: controls.append(True),
        )
    finally:
        store.close()
    assert 4 <= len(controls) <= 6


@pytest.mark.parametrize("exit_time", ["09:19", "09:20", "09:21"])
def test_price_union_covers_actual_multiday_exit_deadline_at_session_boundary(exit_time):
    # This deliberately short native session makes both a final minute-open
    # and the exchange close visible without manufacturing unavailable bars.
    prices = snapshot(minute=True)
    rows = [
        strategy("timed", signals=[signal(timestamp="09:15")], exit_time=exit_time),
        strategy("dated", signals=[signal("BBB")]),
    ]
    normalized = portfolio.normalize(
        {
            "capital": 10000,
            "strategies": [
                {
                    **{key: value for key, value in row.items() if key != "signals"},
                    "source_id": "a" * 32,
                }
                for row in rows
            ],
        }
    )
    rows = [
        {**row, **definition}
        for row, definition in zip(rows, normalized["strategies"], strict=True)
    ]
    plan = portfolio.price_plan(normalized, rows, prices)
    timeline = prices["timeline"]
    index = {key: i for i, key in enumerate(timeline)}
    for row in rows:
        for observation in row["signals"]:
            entry, reason = _schedule(observation, prices, row["config"], timeline, index, True)
            assert entry >= 0 and reason is None
            deadline = _deadline(entry, prices, row["config"], timeline, True)
            held_slots = set(timeline[entry : deadline + 1])
            assert held_slots <= set(plan["required_timestamps"][observation["symbol"]])


def test_resumed_holdout_search_uses_saved_calculation_prices_after_input_change(tmp_path):
    store = ResearchStore(tmp_path / "review-research")
    store.initialize()
    saved = []
    original = inputs()
    specification = {"portfolio": original["portfolio"], "versions": original["versions"]}

    def execute(evidence, checkpoint, restored=None):
        return run(
            store,
            "review-owner",
            evidence,
            specification,
            saved=restored,
            checkpoint=checkpoint,
            progress=lambda *args: None,
            cancelled=lambda: None,
        )[0]

    def stop_after_trial(state, counts):
        saved.append(copy.deepcopy(state))
        if state.get("calculation"):
            raise InterruptedError("review pause after persisted trial")

    try:
        expected = execute(original, lambda *args: None)
        with pytest.raises(InterruptedError):
            execute(original, stop_after_trial)
        assert saved[-1]["phase"] == "calculation" and saved[-1]["calculation"]
        changed = copy.deepcopy(original)
        for bars in changed["snapshot"]["bars"].values():
            for bar in bars.values():
                bar.update(open=50, high=50, low=50, close=50)
        resumed = execute(changed, lambda *args: None, saved[-1])
        assert resumed["experiment"]["rows"] == expected["experiment"]["rows"]
        assert resumed["summary"] == expected["summary"]
        assert resumed["validation"] == expected["validation"]
    finally:
        store.close()


def test_selected_trial_survives_orphan_pruning_backup_and_restore(
    app, client, monkeypatch, tmp_path, tmp_path_factory
):
    from services import research_portfolio as service
    from services import scanner_research_service as saved
    from services import scanner_research_worker as worker
    from services.research_storage import backup_store, prune_orphans, restore_store

    prepare_archive(monkeypatch, tmp_path, reference()["sessions"])
    request = request_for(client, optimize=True)
    store = app.extensions["research_store"]
    owner = "research-test"
    job = service.submit(store, owner, request, "review-storage-run")

    def work(target, token):
        worker.acquire(target, token)
        try:
            assert worker.run_one(target, token)
        finally:
            worker.release(target, token)

    work(store, "review-storage")
    original = saved.get_job(store, owner, job["id"])
    assert original.status == "completed", original.error
    bundle = saved.read_artifact(store, original.result_artifact)
    trial = bundle["result"]["experiment"]["rows"][-1]
    replay = service.rerun(
        store, owner, original.id, trial_id=trial["config_id"], request_id="review-storage-replay"
    )
    monkeypatch.setattr(
        service,
        "_prepare_prices",
        lambda *args, **kwargs: pytest.fail("Frozen replay must not fetch replacement prices"),
    )
    work(store, "review-replay")
    orphan = saved.save_artifact(store, {"abandoned": "not part of either completed run"})
    for path in (store.root / "artifacts").iterdir():
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    assert prune_orphans(store, apply=True)["deleted_files"] >= 1
    assert not (store.root / "artifacts" / f"{orphan}.json.gz").exists()
    backup_root = tmp_path_factory.mktemp("portfolio-review-backups")
    backup_store(store, backup_root / "review-backup")
    restore_store(backup_root / "review-backup", backup_root / "review-restored")
    restored = ResearchStore(backup_root / "review-restored")
    restored.initialize()
    try:
        assert saved.read_artifact(restored, original.result_artifact) == bundle
        prior = saved.get_job(restored, owner, replay["id"])
        prior_bundle = saved.read_artifact(restored, prior.result_artifact)
        assert prior_bundle["result"]["summary"] == trial["summary"]
        again = service.rerun(restored, owner, prior.id, request_id="review-restored-replay")
        work(restored, "review-restored")
        completed = saved.get_job(restored, owner, again["id"])
        assert completed.status == "completed", completed.error
        assert (
            saved.read_artifact(restored, completed.result_artifact)["result"]["summary"]
            == trial["summary"]
        )
    finally:
        restored.close()
