"""Group only complete cached stock recovery; broker boundaries stay durable."""

# ruff: noqa: F811 -- imported isolated fixtures are injected into test arguments

from copy import deepcopy

import pytest
from test_historify_scope import native  # noqa: F401
from test_stock_feature_preparation import archive, prepare  # noqa: F401
from test_stock_feature_scope import cached  # noqa: F401
from test_stock_features import execution, record

from research.stock_features import fingerprint
from services import research_benchmarks, research_stock_features
from services import scanner_research_service as service


def native_checkpoint(stock, *, downloaded=0, phase="checking_prices"):
    symbol = stock["evidence"]["descriptor"]["symbol"]
    bars = stock["evidence"]["bars"]
    total = len(stock["calendar"]["sessions"])
    return {
        "phase": "benchmark",
        "binding": fingerprint({"symbol": symbol}),
        "reference_artifact": fingerprint(stock["calendar"]),
        "acquisition": {
            "progress": {"phase": phase, "covered_dates": len(bars), "required_dates": total},
            "batch_pending": False,
            "hard_failures": [],
            "transport_attempts": [{"attempt": 1}] if downloaded else [],
            "completed_windows": [[symbol, "first", "last"]] if downloaded else [],
            "source_counts": {symbol: {"cached": total - downloaded, "downloaded": downloaded}},
            "bars": {symbol: deepcopy(bars)},
            "receipts": [{"broker": "controlled"}] if downloaded else [],
        },
        "acquisition_receipts": [],
    }


def final_reference(stock, pending):
    return {
        "phase": "benchmark",
        "binding": pending["binding"],
        "reference_artifact": pending["reference_artifact"],
        "inputs_artifact": fingerprint(stock),
    }


def test_real_cached_group_keeps_exact_final_evidence_and_all_visible_symbol_progress(
    cached, monkeypatch
):
    _, evidence, _, _, _, connections = cached
    monkeypatch.setattr(research_stock_features, "_CACHE_CHECKPOINT_BATCH_SIZE", 1)
    ordinary, old_states = prepare(evidence)
    monkeypatch.setattr(research_stock_features, "_CACHE_CHECKPOINT_BATCH_SIZE", 16)
    states, events = [], []
    grouped = research_stock_features.prepare_context(
        object(),
        "alice",
        evidence,
        saved=None,
        checkpoint=lambda state, counts: states.append(deepcopy(state)),
        progress=lambda *args: None,
        cancelled=lambda: None,
        activity=events.append,
    )
    assert grouped == ordinary and states[-1] == old_states[-1]
    assert len(old_states) == 69 and len(states) == 4
    assert [len(state["completed_symbols"]) for state in states] == [0, 16, 16, 17]
    assert {
        event["features"]["completed_symbols"] for event in events if "features" in event
    } == set(range(18))
    assert connections["active"] == 0 and connections["opened"] == connections["closed"]


def test_cancelled_cached_group_reloads_only_native_cache_and_recovers_exactly(cached):
    _, evidence, _, _, _, connections = cached
    states = []

    def activity(event):
        if event.get("features", {}).get("completed_symbols") == 5:
            raise RuntimeError("controlled interruption after five cached stocks")

    with pytest.raises(RuntimeError, match="after five"):
        research_stock_features.prepare_context(
            object(),
            "alice",
            evidence,
            saved=None,
            checkpoint=lambda state, counts: states.append(deepcopy(state)),
            progress=lambda *args: None,
            cancelled=lambda: None,
            activity=activity,
        )
    assert len(states) == 1 and states[-1]["completed_symbols"] == {}
    assert connections["active"] == 0
    resumed, saved = prepare(evidence, saved=states[-1])
    ordinary, _ = prepare(evidence)
    assert resumed == ordinary and saved[-1]["complete"]
    assert connections["active"] == 0 and connections["opened"] == connections["closed"]


def test_every_broker_boundary_flushes_pending_cached_prefix_before_more_work(archive, monkeypatch):
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    evidence = execution(*stocks.values())
    states, broker_boundaries = [], []

    def acquire(store, owner, bounds, descriptor, **kwargs):
        symbol = descriptor["symbol"]
        stock = stocks[symbol]
        pending = native_checkpoint(stock)
        if symbol == "BBB":
            for label, downloaded in (
                ("before request", 0),
                ("first response", 60),
                ("second response", 130),
            ):
                current = native_checkpoint(stock, downloaded=downloaded, phase="broker_prices")
                kwargs["checkpoint"](current, {"completed": 0, "total": 1})
                committed = states[-1]
                assert set(committed["completed_symbols"]) == {"AAA"}
                assert committed["current_symbol"] == "BBB"
                assert committed["market_checkpoint"] == current
                broker_boundaries.append(label)
            pending = native_checkpoint(stock, downloaded=130)
        kwargs["checkpoint"](pending, {"completed": 0, "total": 1})
        service.save_artifact(store, stock)
        kwargs["checkpoint"](final_reference(stock, pending), {"completed": 1, "total": 1})
        return stock

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    result, _ = prepare(evidence, checkpoint=lambda state, counts: states.append(deepcopy(state)))
    assert broker_boundaries == ["before request", "first response", "second response"]
    assert set(result["automatic_stock_features"]["series"]) == {"AAA", "BBB"}
    assert states[-1]["complete"]


@pytest.mark.parametrize(
    "change",
    [
        lambda state: state["acquisition"]["progress"].update(phase="stored_prices"),
        lambda state: state["acquisition"].update(batch_pending=True),
        lambda state: state["acquisition"].update(hard_failures=[{"kind": "auth_expired"}]),
        lambda state: state["acquisition"]["source_counts"]["AAA"].update(downloaded=1),
        lambda state: state["acquisition"].update(transport_attempts=[{"attempt": 1}]),
        lambda state: state["acquisition"]["bars"]["AAA"].pop(
            next(iter(state["acquisition"]["bars"]["AAA"]))
        ),
    ],
    ids=["partial phase", "time limit", "failure", "download", "broker attempted", "missing bar"],
)
def test_incomplete_or_broker_observations_are_never_buffered(change):
    state = native_checkpoint(record())
    assert research_stock_features._cache_only_checkpoint(state, "AAA")
    change(state)
    assert not research_stock_features._cache_only_checkpoint(state, "AAA")


def test_cached_candidate_still_validates_complete_record_before_admission(archive, monkeypatch):
    stock = record()
    evidence = execution(stock)
    pending = native_checkpoint(stock)

    def acquire(store, owner, bounds, descriptor, **kwargs):
        kwargs["checkpoint"](pending, {"completed": 0, "total": 1})
        altered = deepcopy(stock)
        altered["acquisition_receipts"] = [{"sha256": "a" * 64, "receipt": {}}]
        return altered

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    with pytest.raises(ValueError, match="integrity"):
        prepare(evidence)


def test_changed_completion_binding_is_published_instead_of_buffered(archive, monkeypatch):
    stock = record()
    evidence = execution(stock)
    states = []

    def acquire(store, owner, bounds, descriptor, **kwargs):
        pending = native_checkpoint(stock)
        kwargs["checkpoint"](pending, {"completed": 0, "total": 1})
        changed = final_reference(stock, pending)
        changed["binding"] = "changed"
        kwargs["checkpoint"](changed, {"completed": 1, "total": 1})
        assert states[-1]["market_checkpoint"] == changed
        return stock

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    prepare(evidence, checkpoint=lambda state, counts: states.append(deepcopy(state)))
