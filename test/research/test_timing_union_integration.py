"""Explicit timing foundations reuse native prices and one fair frozen opportunity cohort."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from research.portfolio import execution_versions, normalize
from services import research_native_prices
from services.research_historify import NativeHistorifyArchive

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07"]


def calendar():
    return {
        "sessions": list(DAYS),
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in DAYS},
        "bars": {},
        "provenance": {
            "exchange": "NSE",
            "calendar_basis": "openalgo-market-calendar-v1",
            "calendar_source": "isolated-controlled-native-calendar",
        },
    }


def evidence(*, minute=True):
    portfolio = normalize(
        {
            "name": "Controlled timing foundation",
            "capital": 100000,
            "engine": "vectorbt",
            "strategies": [
                {
                    "id": "scanner",
                    "name": "Scanner",
                    "source_id": "a" * 32,
                    "allocation_pct": 100,
                    "config": {
                        "hold_sessions": 1,
                        "order_size_pct": 25,
                        "cost_bps": 5,
                        "target_pct": 50,
                        "stop_pct": 25,
                        **({"trade_horizon": "intraday", "hold_minutes": 30} if minute else {}),
                    },
                }
            ],
        }
    )
    signals = [
        {
            "symbol": "AAA",
            "date": day,
            "row": i + 2,
            **({"timestamp": day + "T09:15:30+05:30"} if minute else {}),
        }
        for i, day in enumerate(DAYS[:2] if minute else DAYS[:1])
    ]
    return {
        "portfolio": portfolio,
        "strategies": [{**deepcopy(portfolio["strategies"][0]), "signals": signals}],
        "signals": [dict(signal, strategy_id="scanner") for signal in signals],
        "versions": execution_versions(portfolio),
        "receipt": {"signal_count": len(signals), "symbol_count": 1},
    }


def candle(slot):
    return {
        "timestamp": int(datetime.fromisoformat(slot).timestamp()),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 100,
        "oi": 0,
    }


@pytest.fixture
def archive(tmp_path, monkeypatch):
    from database import historify_db

    target = tmp_path / "timing-union-historify.duckdb"
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("HISTORIFY_DATABASE_PATH", str(target))
    monkeypatch.setenv("RESEARCH_HISTORIFY_DATABASE_PATH", str(target))
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(target))
    return target


def seed(target, plan, *, missing=()):
    interval = plan["interval"]
    field = "required_timestamps" if interval == "1m" else "required_dates"
    native = NativeHistorifyArchive(target, interval=interval)
    for symbol, slots in plan[field].items():
        rows = [
            candle(slot if interval == "1m" else slot + "T09:15:00+05:30")
            for slot in slots
            if slot not in missing
        ]
        native.write(symbol, rows)


@pytest.fixture
def store(tmp_path):
    from database.research_db import ResearchStore

    instance = ResearchStore(tmp_path / "research-store")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.close()


def contract(value, recorded=None):
    from research.timing_search import build_union, propose

    recorded = recorded or calendar()
    descriptor = propose(value["portfolio"], value["strategies"], recorded)
    return descriptor, build_union(value["portfolio"], value["strategies"], recorded, descriptor)


def run(store, value, *, saved=None, descriptor=None, recorded=None):
    from services.research_timing_prices import prepare

    checkpoints = []
    prepared = prepare(
        store,
        "controlled-owner",
        value,
        recorded or calendar(),
        descriptor=descriptor,
        saved=saved,
        checkpoint=lambda state, counts: checkpoints.append(deepcopy(state)),
        progress=lambda *a: None,
        cancelled=lambda: None,
    )
    return prepared, checkpoints


def no_history(**request):
    pytest.fail("Complete native Historify union must not make a broker call")


@pytest.mark.timeout(180)
def test_one_native_union_is_reused_for_all_clock_branches_and_complete_recovery(
    archive, store, monkeypatch
):
    from research.timing_search import materialize

    value = evidence()
    original = deepcopy(value)
    descriptor, plan = contract(value)
    seed(archive, plan)
    monkeypatch.setattr(research_native_prices, "native_history", no_history)
    monkeypatch.setattr("services.research_sources.resolve_broker_session", no_history)
    acquisitions = []
    actual = research_native_prices.acquire_native_prices

    def counted(*args, **kwargs):
        acquisitions.append(args[1]["id"])
        return actual(*args, **kwargs)

    monkeypatch.setattr(research_native_prices, "acquire_native_prices", counted)
    prepared, checkpoints = run(store, value)
    assert acquisitions == [plan["id"]]
    assert checkpoints[-1]["phase"] == "timing_union_ready"
    assert prepared["timing_cohort"]["counts"] == {
        "eligible": 2,
        "common_excluded": 0,
        "original_excluded": 0,
        "all_rule_filtered": 0,
    }
    states = descriptor["strategies"][0]["branches"]
    assert 2 < len(states) <= 4
    for branch in states:
        fixed = materialize(prepared, prepared["timing_cohort"], {"scanner": branch["id"]})
        assert fixed["snapshot"] is prepared["snapshot"]
        assert fixed["capital"] == original["portfolio"]["capital"]
        assert fixed["diagnostic"]["automatic_recipe"] is None
        for key in ("order_size_pct", "target_pct", "stop_pct", "cost_bps", "hold_minutes"):
            assert fixed["strategies"][0]["config"][key] == original["strategies"][0]["config"][key]
    assert acquisitions == [plan["id"]]
    restored, repeated_checkpoints = run(store, value, saved=checkpoints[-1])
    assert restored == prepared and not repeated_checkpoints
    assert acquisitions == [plan["id"]]
    assert value == original
    from services.research_storage import _input_references

    references = set(_input_references(prepared))
    assert prepared["timing_contract"]["inputs_artifact"] in references
    assert prepared["reference_artifact"] in references


@pytest.mark.timeout(180)
def test_missing_union_bar_downloads_once_then_next_experiment_uses_historify(
    archive, store, monkeypatch
):
    value = evidence()
    _, plan = contract(value)
    gap = "2026-01-05T10:30:00+05:30"
    assert gap in plan["required_timestamps"]["AAA"]
    seed(archive, plan, missing=(gap,))
    calls = []

    def history(**request):
        calls.append(request)
        return True, {"data": [candle(gap)]}, 200

    monkeypatch.setattr(research_native_prices, "native_history", history)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: {"broker": "controlled", "auth_token": "ISOLATED_TEST_NOT_REAL_AUTH"},
    )
    first, _ = run(store, value)
    assert len(calls) == 1 and calls[0]["interval"] == "1m"
    assert first["timing_cohort"]["counts"]["eligible"] == 2
    monkeypatch.setattr(research_native_prices, "native_history", no_history)
    monkeypatch.setattr("services.research_sources.resolve_broker_session", no_history)
    second, _ = run(store, value)
    assert second["snapshot"]["bars"] == first["snapshot"]["bars"]
    assert second["timing_cohort"]["counts"] == first["timing_cohort"]["counts"]
    assert any(item["receipt"]["broker"] == "controlled" for item in first["acquisition_receipts"])
    assert all(item["receipt"]["broker"] is None for item in second["acquisition_receipts"])


@pytest.mark.timeout(180)
def test_missing_only_a_later_branch_bar_excludes_same_opportunity_from_real_native_ledgers(
    archive, store, monkeypatch
):
    from research.connectors.vectorbt_portfolio import evaluate
    from research.timing_search import materialize

    value = evidence()
    original = deepcopy(value)
    descriptor, plan = contract(value)
    gap = "2026-01-05T10:30:00+05:30"
    seed(archive, plan, missing=(gap,))
    calls = []

    def absent(**request):
        calls.append(request)
        return True, {"data": []}, 200

    monkeypatch.setattr(research_native_prices, "native_history", absent)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: {"broker": "controlled", "auth_token": "ISOLATED_TEST_NOT_REAL_AUTH"},
    )
    prepared, _ = run(store, value)
    cohort = prepared["timing_cohort"]
    assert len(calls) == 1
    assert cohort["counts"] == {
        "eligible": 1,
        "common_excluded": 1,
        "original_excluded": 0,
        "all_rule_filtered": 0,
    }
    assert gap in cohort["exclusions"][0]["reason"]
    for branch in descriptor["strategies"][0]["branches"]:
        fixed = materialize(prepared, cohort, {"scanner": branch["id"]})
        result = evaluate(fixed["strategies"], fixed["snapshot"], fixed["capital"])
        assert len(result["ledger"]) == 2
        assert result["ledger"][0]["status"] == "excluded"
        assert gap in result["ledger"][0]["reason"]
        assert result["ledger"][1]["status"] == "closed"
        assert result["summary"]["excluded_signals"] == 1
    assert len(calls) == 1
    assert value == original
    assert all("research_exclusion" not in signal for signal in value["strategies"][0]["signals"])


@pytest.mark.timeout(180)
def test_paused_union_resumes_committed_prices_and_rejects_changed_source_calendar_or_descriptor(
    archive, store, monkeypatch
):
    from services.research_timing_prices import prepare

    value = evidence()
    descriptor, plan = contract(value)
    gaps = ("2026-01-05T10:30:00+05:30", "2026-01-06T10:30:00+05:30")
    seed(archive, plan, missing=gaps)
    calls, committed = [], []

    def history(**request):
        calls.append(request["start_date"])
        return (
            True,
            {"data": [candle(next(slot for slot in gaps if slot[:10] == request["start_date"]))]},
            200,
        )

    monkeypatch.setattr(research_native_prices, "native_history", history)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: {"broker": "controlled", "auth_token": "ISOLATED_TEST_NOT_REAL_AUTH"},
    )

    class Paused(Exception):
        pass

    def pause(state, _counts):
        committed.append(deepcopy(state))
        if (
            state.get("acquisition_manifest", {})
            .get("lists", {})
            .get("completed_windows", {})
            .get("count", 0)
        ):
            raise Paused

    with pytest.raises(Paused):
        prepare(
            store,
            "controlled-owner",
            value,
            calendar(),
            checkpoint=pause,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    assert calls == [DAYS[0]]
    saved = committed[-1]
    for mutation in ("source", "calendar", "descriptor", "csv_identity"):
        changed, recorded, branch_contract = deepcopy(value), calendar(), deepcopy(descriptor)
        if mutation == "source":
            changed["strategies"][0]["signals"][0]["row"] += 1
            changed["signals"][0]["row"] += 1
        elif mutation == "calendar":
            recorded["session_hours"][DAYS[0]]["open"] = "09:20"
        elif mutation == "csv_identity":
            changed["strategies"][0]["original_csv_sha256"] = "f" * 64
        else:
            branch_contract["strategies"][0]["branches"][1]["changes"]["entry_time"] = "09:40"
        with pytest.raises(ValueError):
            run(store, changed, recorded=recorded, descriptor=branch_contract, saved=saved)
        assert calls == [DAYS[0]]
    resumed, _ = run(store, value, saved=saved)
    assert calls == DAYS[:2]
    assert resumed["timing_cohort"]["counts"]["eligible"] == 2
    assert (
        sum(item["receipt"]["broker"] == "controlled" for item in resumed["acquisition_receipts"])
        == 2
    )


def test_daily_only_and_incomplete_calendar_are_rejected_before_price_io(
    archive, store, monkeypatch
):
    monkeypatch.setattr(research_native_prices, "acquire_native_prices", no_history)
    monkeypatch.setattr("services.research_sources.acquisition_paths", no_history)
    before = (
        list((store.root / "artifacts").rglob("*")) if (store.root / "artifacts").exists() else []
    )
    daily = evidence(minute=False)
    original = deepcopy(daily)
    descriptor, plan = contract(daily)
    assert plan["interval"] == "D" and plan["status"] == "not_applicable"
    with pytest.raises(ValueError, match="Daily-only"):
        run(store, daily)
    assert daily == original
    timed = evidence()
    timed["strategies"][0]["signals"][0]["date"] = DAYS[-1]
    timed["strategies"][0]["signals"][0]["timestamp"] = DAYS[-1] + "T15:29:00+05:30"
    timed["signals"][0].update(timed["strategies"][0]["signals"][0])
    with pytest.raises(ValueError):
        run(store, timed)
    after = (
        list((store.root / "artifacts").rglob("*")) if (store.root / "artifacts").exists() else []
    )
    assert after == before


def test_source_aggregate_mismatch_fails_before_artifact_or_archive_io(store, monkeypatch):
    from services.research_timing_prices import prepare

    value = evidence()
    value["signals"][0]["symbol"] = "DIFFERENT"
    monkeypatch.setattr(research_native_prices, "acquire_native_prices", no_history)
    monkeypatch.setattr("services.research_sources.acquisition_paths", no_history)
    with pytest.raises(ValueError, match="source observations do not match"):
        prepare(
            store,
            "controlled-owner",
            value,
            calendar(),
            checkpoint=lambda *a: None,
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    assert not (store.root / "artifacts").exists() or not list(
        (store.root / "artifacts").rglob("*")
    )


def test_frozen_contract_excludes_csv_price_and_arbitrary_source_payloads(
    archive, store, monkeypatch
):
    from services import scanner_research_service as service

    value = evidence()
    recorded = calendar()
    marker = "ARBITRARY_PAYLOAD_MUST_NOT_ENTER_TIMING_BINDING"
    value["strategies"][0]["original_csv_base64"] = marker
    value["strategies"][0]["source_receipt"] = {"unrelated": marker}
    recorded["bars"] = {"IGNORED": {"unrelated": marker}}
    recorded["provenance"]["unrelated"] = marker
    recorded["session_hours"]["2099-12-31"] = {"unrelated": marker}
    recorded["session_hours"][DAYS[0]]["unrelated"] = marker
    _, plan = contract(value, recorded)
    seed(archive, plan)
    monkeypatch.setattr(research_native_prices, "native_history", no_history)
    prepared, _ = run(store, value, recorded=recorded)
    frozen = service.read_artifact(store, prepared["timing_contract"]["inputs_artifact"])
    admitted_calendar = service.read_artifact(store, prepared["reference_artifact"])
    assert marker not in service.encoded(frozen).decode()
    assert admitted_calendar["bars"] == {}
    assert marker not in service.encoded(admitted_calendar).decode()
    assert value["strategies"][0]["original_csv_base64"] == marker
    assert prepared["strategies"][0]["original_csv_base64"] == marker


@pytest.mark.parametrize("excess", ["strategies", "observations", "sessions"])
def test_input_bounds_reject_before_identity_expansion_and_price_io(store, monkeypatch, excess):
    value, recorded = evidence(), calendar()
    if excess == "strategies":
        value["strategies"] = [value["strategies"][0]] * 9
    elif excess == "observations":
        value["signals"] = [value["signals"][0]] * 25001
    else:
        recorded["sessions"] = [DAYS[0]] * 3001
    monkeypatch.setattr(research_native_prices, "acquire_native_prices", no_history)
    monkeypatch.setattr("services.research_sources.acquisition_paths", no_history)
    monkeypatch.setattr("research.timing_search.build_union", no_history)
    with pytest.raises(ValueError, match="supports|recorded native sessions"):
        run(store, value, recorded=recorded)
    assert not (store.root / "artifacts").exists() or not list(
        (store.root / "artifacts").rglob("*")
    )


def test_auth_failure_retains_recoverable_union_checkpoint_and_asks_to_reconnect(
    archive, store, monkeypatch
):
    from services import scanner_research_service as service
    from services.research_checkpoint import unpack_checkpoint
    from services.research_timing_prices import PHASE, prepare

    value = evidence()
    original = deepcopy(value)
    _, plan = contract(value)
    gap = "2026-01-05T10:30:00+05:30"
    seed(archive, plan, missing=(gap,))
    calls, checkpoints = [], []

    def expired(**request):
        calls.append(request)
        return False, {"message": "expired ISOLATED_PRIVATE_AUTH_MESSAGE"}, 401

    monkeypatch.setattr(research_native_prices, "native_history", expired)
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda _: {"broker": "controlled", "auth_token": "ISOLATED_NOT_REAL_SECRET"},
    )
    with pytest.raises(ValueError, match="Reconnect your broker in OpenAlgo, then resume"):
        prepare(
            store,
            "controlled-owner",
            value,
            calendar(),
            checkpoint=lambda state, counts: checkpoints.append(deepcopy(state)),
            progress=lambda *a: None,
            cancelled=lambda: None,
        )
    assert len(calls) == 1 and calls[0]["interval"] == "1m"
    assert checkpoints[-1]["phase"] == PHASE
    restored = unpack_checkpoint(store, checkpoints[-1])
    assert restored["acquisition"]["receipts"][-1]["outcome"] == "auth_expired"
    assert gap not in restored["acquisition"]["bars"]["AAA"]
    assert "ISOLATED_PRIVATE_AUTH_MESSAGE" not in service.encoded(restored).decode()
    assert "ISOLATED_NOT_REAL_SECRET" not in service.encoded(restored).decode()
    assert value == original


def test_initial_cancellation_precedes_artifacts_archive_and_broker_io(store, monkeypatch):
    from services import scanner_research_service as service
    from services.research_timing_prices import prepare

    value = evidence()
    original = deepcopy(value)
    monkeypatch.setattr(service, "save_artifact", no_history)
    monkeypatch.setattr(research_native_prices, "acquire_native_prices", no_history)
    monkeypatch.setattr("services.research_sources.acquisition_paths", no_history)
    monkeypatch.setattr("services.research_sources.resolve_broker_session", no_history)
    checkpoints = []

    def cancel():
        raise InterruptedError("Controlled cancellation before acquisition")

    with pytest.raises(InterruptedError, match="Controlled cancellation"):
        prepare(
            store,
            "controlled-owner",
            value,
            calendar(),
            checkpoint=lambda *a: checkpoints.append(a),
            progress=lambda *a: None,
            cancelled=cancel,
        )
    assert not checkpoints
    assert value == original
    assert not (store.root / "artifacts").exists() or not list(
        (store.root / "artifacts").rglob("*")
    )
