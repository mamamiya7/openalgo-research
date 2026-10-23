"""Cached leaves share strict publication without exposing uncommitted references."""

# ruff: noqa: F811 -- imported isolated fixture is injected by pytest

from copy import deepcopy

import pytest
from test_cached_context_checkpoints import final_reference, native_checkpoint
from test_chunked_artifacts import store  # noqa: F401
from test_stock_features import execution, record

from research.stock_features import fingerprint
from services import research_benchmarks, research_stock_features
from services import scanner_research_service as service
from services.research_storage import _input_references


def test_sixteen_owned_records_share_one_admission_and_keep_exact_bytes(store, monkeypatch):
    original = service._managed_storage_snapshot
    scans = []

    def snapshot(selected):
        scans.append(1)
        return original(selected)

    monkeypatch.setattr(service, "_managed_storage_snapshot", snapshot)
    pending = research_stock_features._CachedRecordPublications(store)
    values = [{"symbol": f"S{i}", "prices": [i, 1.25]} for i in range(17)]
    saved = deepcopy(values)
    ids = []
    for value in values[:16]:
        identifier, deferred = pending.add(value)
        assert deferred
        ids.append(identifier)
    assert scans == [] and len(pending.records) == 16
    values[0]["prices"].clear()
    identifier, deferred = pending.add(values[-1])
    ids.append(identifier)
    assert deferred and scans == [1, 1] and len(pending.records) == 1
    assert [service.read_artifact(store, key) for key in ids[:16]] == saved[:16]
    assert service._artifact_publication.get() is None
    pending.flush()
    assert scans == [1, 1, 1]
    assert service.read_artifact(store, ids[-1]) == saved[-1]
    assert pending.records == {} and pending.size == 0


def test_combined_canonical_and_heap_limit_flushes_before_exceeding_bound(store, monkeypatch):
    monkeypatch.setattr(research_stock_features, "_CACHE_RECORD_BATCH_BYTES", 2048)
    pending = research_stock_features._CachedRecordPublications(store)
    saved = {}
    for i in range(20):
        value = {"symbol": str(i), "prices": [1.0, 2.0, 3.0]}
        identifier, _ = pending.add(value)
        saved[identifier] = value
        assert len(pending.records) <= 16 and pending.size <= 2048
    pending.flush()
    assert all(service.read_artifact(store, key) == value for key, value in saved.items())


def test_queued_records_still_take_fresh_quota_admission(store, monkeypatch):
    pending = research_stock_features._CachedRecordPublications(store)
    pending.add({"first": True})
    pending.add({"second": True})
    monkeypatch.setenv("RESEARCH_QUOTA_MB", "1")
    (store.root / "external-receipt.json").write_bytes(b"x" * 1024**2)
    with pytest.raises(ValueError, match="storage is full"):
        pending.flush()
    assert service._artifact_publication.get() is None


@pytest.mark.parametrize("deferred", [False, True])
def test_benchmark_defers_ready_checkpoint_only_for_its_private_cached_publisher(
    store, monkeypatch, deferred
):
    stock = record()
    evidence = execution(stock)
    monkeypatch.setattr(
        "services.research_native_calendar.native_calendar_window",
        lambda *args, **kwargs: stock["calendar"],
    )
    monkeypatch.setattr(
        "services.research_sources.acquisition_paths",
        lambda *args: (store.root / "history.duckdb", store.root / "receipts"),
    )
    monkeypatch.setattr(
        "services.research_market_series.acquire_series",
        lambda *args, **kwargs: {
            "batch_pending": False,
            "hard_failures": [],
            "evidence": stock["evidence"],
            "acquisition_receipts": [],
        },
    )
    states = []
    bounds = {
        "summary": {"initial_capital": 100000},
        "equity_curve": [
            {"date": day, "equity": 100000} for day in evidence["snapshot"]["sessions"]
        ],
    }
    options = {"_publish_completed": lambda selected: None} if deferred else {}
    result = research_benchmarks.prepare(
        store,
        "alice",
        bounds,
        stock["evidence"]["descriptor"],
        checkpoint=lambda state, counts: states.append(state),
        warmup_sessions=100,
        **options,
    )
    assert result == stock
    assert len(states) == (0 if deferred else 1)
    if not deferred:
        assert service.read_artifact(store, states[0]["inputs_artifact"]) == stock


def controlled_scope(monkeypatch):
    class Scope:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def finish(self, symbol):
            pass

        def calendar(self, first, last, warmup, *, recorded):
            return recorded

    monkeypatch.setattr("services.research_market_series.StockFeaturePreparationScope", Scope)


def acquire_cached(stocks, *, boundary=None):
    def acquire(store, owner, bounds, descriptor, **kwargs):
        symbol = descriptor["symbol"]
        selected = stocks[symbol]
        service.save_artifact(store, selected["calendar"])
        pending = native_checkpoint(selected)
        if boundary is not None:
            pending = boundary(store, symbol, pending, kwargs)
        kwargs["checkpoint"](pending, {"completed": 0, "total": 1})
        artifact = kwargs["_publish_completed"](selected)
        if artifact is not None:
            kwargs["checkpoint"](final_reference(selected, pending), {"completed": 1, "total": 1})
        return selected

    return acquire


def run(store, evidence, states, *, activity=None):
    def checkpoint(state, counts):
        assert service._artifact_publication.get() is None
        assert all(
            service.read_artifact(store, key) is not None for key in _input_references(state)
        )
        states.append(deepcopy(state))

    def progress(*args):
        assert service._artifact_publication.get() is None

    return research_stock_features.prepare_context(
        store,
        "alice",
        evidence,
        saved=None,
        checkpoint=checkpoint,
        progress=progress,
        cancelled=lambda: None,
        activity=activity,
    )


def test_cached_stock_journey_flushes_every_reference_before_checkpoint(store, monkeypatch):
    controlled_scope(monkeypatch)
    stocks = {f"S{i:02}": record(f"S{i:02}") for i in range(17)}
    evidence = execution(*stocks.values())
    monkeypatch.setattr(research_benchmarks, "prepare", acquire_cached(stocks))
    states = []
    result = run(store, evidence, states)
    assert result["automatic_stock_features"]["series"] == stocks
    assert [len(state["completed_symbols"]) for state in states] == [0, 16, 16, 17]
    assert states[-1]["complete"]


def test_cancelled_cached_prefix_has_no_published_recovery_reference(store, monkeypatch):
    controlled_scope(monkeypatch)
    stocks = {f"S{i}": record(f"S{i}") for i in range(3)}
    evidence = execution(*stocks.values())
    monkeypatch.setattr(research_benchmarks, "prepare", acquire_cached(stocks))
    states = []

    def stop(event):
        if event.get("features", {}).get("completed_symbols") == 2:
            raise RuntimeError("cancelled before grouped publication")

    with pytest.raises(RuntimeError, match="cancelled"):
        run(store, evidence, states, activity=stop)
    assert len(states) == 1 and not states[0]["completed_symbols"]
    for stock in list(stocks.values())[:2]:
        with pytest.raises((LookupError, ValueError, FileNotFoundError)):
            service.read_artifact(store, fingerprint(stock))
    assert service._artifact_publication.get() is None


def test_broker_boundary_flushes_pending_cache_and_keeps_native_ready_checkpoint(
    store, monkeypatch
):
    controlled_scope(monkeypatch)
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    states = []

    def boundary(store, symbol, pending, kwargs):
        if symbol == "BBB":
            pending = native_checkpoint(stocks[symbol], downloaded=1, phase="broker_prices")
            kwargs["checkpoint"](pending, {"completed": 0, "total": 1})
            assert service._artifact_publication.get() is None
            assert service.read_artifact(store, fingerprint(stocks["AAA"])) == stocks["AAA"]
            assert states[-1]["market_checkpoint"] == pending
        return pending

    monkeypatch.setattr(research_benchmarks, "prepare", acquire_cached(stocks, boundary=boundary))
    result = run(store, execution(*stocks.values()), states)
    assert result["automatic_stock_features"]["series"] == stocks
    assert any(
        state["market_checkpoint"] and state["market_checkpoint"].get("inputs_artifact")
        for state in states
    )
