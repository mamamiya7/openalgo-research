"""Frozen-calendar recovery never guesses hours or overwrites saved evidence."""

# ruff: noqa: F811 -- isolated fixtures shared with native acquisition tests
import copy
import json
import sys
from types import SimpleNamespace

import pytest
from test_jobs import app, client  # noqa: F401
from test_native_price_acquisition import DAYS, SIGNALS, calendar, candle, forbidden, options, plan
from test_native_price_acquisition import archive as native_archive
from test_stock_feature_preparation import archive  # noqa: F401
from test_stock_market_context import fixtures
from test_stock_market_context_preparation import controlled, prepare
from test_study_activity import run_worker, seed

from database.research_db import ResearchExperiment, ResearchJob
from research.failures import ResearchFailure, check_execution_hours
from research.market_series import fingerprint
from research.stock_market_context import KEY, freeze_bundle
from services import research_stock_market_context as context
from services import scanner_research_service as service
from services.research_native_prices import acquire_native_prices
from services.research_recovery import LEGACY_HOURS_ERROR, assessment


def test_native_daily_acquisition_retains_original_calendar_without_minute_prices(native_archive):
    native = calendar()
    native["session_hours"][DAYS[-1]] = {"open": "13:45", "close": "14:45"}
    native["session_hours_source"] = "original-native-calendar"
    before = copy.deepcopy(native)
    opts = options(native_archive)
    opts["writer"]("AAA", [candle(day) for day in DAYS[1:]])
    opts.update(history=forbidden, credentials=forbidden)
    result = acquire_native_prices(SIGNALS, plan(), native, **opts)
    assert result["provenance"]["interval"] == "D" and "timeline" not in result
    assert result["session_hours"] == before["session_hours"]
    assert result["session_hours_source"] == before["session_hours_source"]
    result["session_hours"][DAYS[0]]["close"] = "14:00"
    assert native == before


def legacy(archive):
    stocks, market, evidence = fixtures()
    evidence["snapshot"]["provenance"]["calendar_basis"] = "openalgo-market-calendar-v1"
    reference = copy.deepcopy(evidence["snapshot"])
    evidence["reference_artifact"] = service.save_artifact(None, reference)
    evidence["snapshot"].pop("session_hours")  # old daily acquisition assembler
    return stocks, market, evidence, reference


def test_old_daily_snapshot_recovers_recorded_hours_without_altering_identity_or_refetch(
    archive, monkeypatch
):
    stocks, market, evidence, _ = legacy(archive)
    calls, _ = controlled(monkeypatch, stocks, market)
    original = copy.deepcopy(evidence)
    # Retain the actual failed stock-market acquisition checkpoint before the
    # original assembler would compare against absent daily snapshot hours.
    states = []
    result, _, _, _ = prepare(
        evidence, checkpoint=lambda state, _: states.append(copy.deepcopy(state))
    )
    assert result[KEY] == freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    assert evidence == original and "session_hours" not in result["snapshot"]
    count = len(calls)
    restored, new_states, _, _ = prepare(evidence, saved=states[-1])
    assert restored == result and not new_states and len(calls) == count
    assert fingerprint(evidence) == fingerprint(original)


def test_hours_representation_matches_without_rewriting_recorded_evidence(archive):
    stocks, market, evidence = fixtures()
    evidence[KEY] = freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    for hours in evidence["snapshot"]["session_hours"].values():
        hours.update(open="09:15:00", close="15:30:00")
    before = copy.deepcopy(evidence)
    assert prepare(evidence)[0] == before
    assert evidence == before


@pytest.mark.parametrize("conflict", [False, True])
def test_existing_failed_daily_attempt_is_classified_from_original_saved_calendar(
    archive, monkeypatch, conflict
):
    stocks, market, evidence, reference = legacy(archive)
    if conflict:
        reference["session_hours"][reference["sessions"][0]]["close"] = "15:29"
        evidence["reference_artifact"] = service.save_artifact(None, reference)
    controlled(monkeypatch, stocks, market)
    states = []
    actual_check = context._check_execution

    def old_assembler(bundle, inputs, symbols, **kwargs):
        raise ValueError(LEGACY_HOURS_ERROR)

    monkeypatch.setattr(context, "_check_execution", old_assembler)
    with pytest.raises(ValueError, match="different execution session hours"):
        prepare(evidence, checkpoint=lambda state, _: states.append(copy.deepcopy(state)))
    monkeypatch.setattr(context, "_check_execution", actual_check)
    from research.connectors.vectorbt_portfolio import POLICY_VERSION

    checkpoint = service.save_artifact(
        None, {"identity": "original", "policy_version": POLICY_VERSION, "state": states[-1]}
    )
    attempt = SimpleNamespace(status="failed", error=LEGACY_HOURS_ERROR)
    experiment = SimpleNamespace(
        counts="{}",
        checkpoint=checkpoint,
        identity="original",
        kind="portfolio_optimize",
        specification=json.dumps({"portfolio": {"engine": "vectorbt"}}),
    )
    before = copy.deepcopy(archive)
    failure, recovery = assessment(None, attempt, experiment)
    assert recovery["status"] == ("blocked" if conflict else "available")
    assert failure["code"] == (
        "execution_session_hours_mismatch" if conflict else "daily_calendar_hours_omitted"
    )
    assert ("resume" in recovery["actions"]) is not conflict
    assert archive == before


@pytest.mark.parametrize(
    "change", ["missing_reference", "partial_hours", "different_reference", "minute_missing"]
)
def test_missing_hours_cannot_be_inferred_from_current_calendar(archive, change):
    stocks, market, evidence, reference = legacy(archive)
    evidence[KEY] = freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    if change == "missing_reference":
        evidence.pop("reference_artifact")
    elif change == "partial_hours":
        evidence["snapshot"]["session_hours"] = {}
    elif change == "different_reference":
        reference["sessions"] = reference["sessions"][1:]
        evidence["reference_artifact"] = service.save_artifact(None, reference)
    else:
        evidence["snapshot"]["provenance"]["interval"] = "1m"
    with pytest.raises(ResearchFailure) as caught:
        prepare(evidence)
    assert not caught.value.receipt["retryable"]
    assert "resume" not in caught.value.receipt["actions"]


def test_real_session_conflict_names_first_date_actual_expected_and_count(archive):
    stocks, market, evidence = fixtures()
    evidence[KEY] = freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    first, second = evidence["snapshot"]["sessions"][:2]
    for day in (first, second):
        evidence["snapshot"]["session_hours"][day]["close"] = "15:29"
    before = copy.deepcopy(evidence)
    with pytest.raises(ResearchFailure) as caught:
        prepare(evidence)
    failure = caught.value.receipt
    assert failure["code"] == "execution_session_hours_mismatch"
    assert failure["details"] == {
        "first_affected_session": first,
        "expected_hours": {"open": "09:15", "close": "15:30"},
        "actual_hours": {"open": "09:15", "close": "15:29"},
        "mismatch_count": 2,
    }
    assert first in failure["message"] and "15:29" in failure["message"]
    assert evidence == before


def test_worker_publishes_structured_conflict_and_resume_cannot_loop(app, client, monkeypatch):
    store, job_id = seed(app)

    def fail(store, owner, evidence, spec, *, checkpoint, **kwargs):
        checkpoint(
            {
                "phase": "calculation",
                "inputs_artifact": service.save_artifact(store, evidence),
                "calculation": None,
            },
            {"completed": 60, "total": 100, "stage": "backtest"},
        )
        check_execution_hours(
            {"2026-01-05": {"open": "09:15", "close": "15:30"}},
            {"2026-01-05": {"open": "09:15", "close": "15:29"}},
            ["2026-01-05"],
        )

    monkeypatch.setattr("services.research_portfolio.run", fail)
    run_worker(store)
    route = f"/scanner-research/api/jobs/{job_id}"
    failed = client.get(route).json
    assert failed["status"] == "failed" and not failed["resumable"]
    assert failed["failure"]["details"]["first_affected_session"] == "2026-01-05"
    assert failed["recovery"]["status"] == "blocked"
    assert failed["recovery"]["actions"] == ["new_run", "edit_setup"]
    with store.sessions() as db:
        saved_checkpoint = db.get(ResearchExperiment, job_id).checkpoint
    assert client.post(route + "/resume").status_code == 400
    assert client.get(route).json["status"] == "failed"
    with store.sessions() as db:
        assert db.get(ResearchExperiment, job_id).checkpoint == saved_checkpoint


def test_corrupt_checkpoint_is_not_advertised_as_resumable(app, client):
    store, job_id = seed(app)
    with store.sessions.begin() as db:
        experiment = db.get(ResearchExperiment, job_id)
        experiment.checkpoint = service.save_artifact(
            store, {"identity": "wrong", "policy_version": "wrong", "state": {}}
        )
        db.get(ResearchJob, job_id).status = "paused"
    value = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert not value["resumable"] and value["recovery"]["status"] == "blocked"
    assert client.post(f"/scanner-research/api/jobs/{job_id}/resume").status_code == 400


def test_recovery_receipts_release_db_and_artifact_handles_on_available_and_blocked_paths(
    app, client
):
    import psutil

    from research.connectors.vectorbt_portfolio import POLICY_VERSION

    store, job_id = seed(app)
    with store.sessions.begin() as db:
        experiment = db.get(ResearchExperiment, job_id)
        valid = service.save_artifact(
            store,
            {
                "identity": experiment.identity,
                "policy_version": POLICY_VERSION,
                "state": {"phase": "calculation"},
            },
        )
        invalid = service.save_artifact(store, {"identity": "changed", "state": {}})
        experiment.checkpoint = valid
        db.get(ResearchJob, job_id).status = "paused"
    measure = psutil.Process().num_handles if sys.platform == "win32" else psutil.Process().num_fds
    route = f"/scanner-research/api/jobs/{job_id}"
    for _ in range(5):
        assert client.get(route).json["resumable"]
    baseline = measure()
    for _ in range(100):
        assert client.get(route).json["resumable"]
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, job_id).checkpoint = invalid
    for _ in range(100):
        assert client.get(route).json["recovery"]["status"] == "blocked"
    assert measure() <= baseline + 3
