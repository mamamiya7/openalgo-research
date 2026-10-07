"""One durable shared native benchmark over the existing stock acquisition path."""

# ruff: noqa: F811 -- imported pytest fixtures are injected into test arguments

from copy import deepcopy

import pytest
from test_historify_scope import native  # noqa: F401 -- isolated native archive fixture
from test_stock_feature_preparation import archive  # noqa: F401 -- content-addressed fixture
from test_stock_features import bundle, execution, record

from research.market_series import freeze_series
from research.stock_features import fingerprint
from research.stock_relative_strength import validate_bundle
from services import research_benchmarks, research_stock_context
from services.research_activity import merge_activity
from services.research_storage import _input_references


def benchmark(*, missing=()):
    stock = record(missing=missing)
    return {
        "calendar": stock["calendar"],
        "acquisition_receipts": [],
        "evidence": freeze_series(
            {"symbol": "NIFTY", "exchange": "NSE_INDEX", "interval": "D", "role": "benchmark"},
            stock["calendar"]["sessions"],
            stock["calendar"],
            stock["evidence"]["bars"],
        ),
    }


def context(*stocks, reference=None):
    payload = {
        "version": research_stock_context.VERSION,
        "series": bundle(*stocks)["series"],
        "benchmark": reference if reference is not None else benchmark(),
    }
    return {**payload, "id": fingerprint(payload)}


def prepare(evidence, *, saved=None, checkpoint=None, owner="alice", cancelled=lambda: None):
    states, progress, events = [], [], []

    def save(state, counts):
        states.append(deepcopy(state))
        if checkpoint:
            checkpoint(state, counts)

    def activity(event):
        events.append(merge_activity({}, event, 1))

    result = research_stock_context.prepare_context(
        object(),
        owner,
        evidence,
        saved=saved,
        checkpoint=save,
        progress=lambda *args: progress.append(args),
        cancelled=cancelled,
        activity=activity,
    )
    return result, states, progress, events


def test_shared_benchmark_counts_toward_total_bar_bound_before_acquisition(monkeypatch):
    stock = record()
    evidence = execution(stock)
    # One stock alone fits this limit, but the second shared series does not.
    monkeypatch.setattr(research_stock_context, "MAX_FEATURE_BARS", 150)
    monkeypatch.setattr(
        research_benchmarks,
        "prepare",
        lambda *a, **k: pytest.fail("Started acquisition beyond bound"),
    )
    with pytest.raises(ValueError, match="Stock and benchmark history exceeds"):
        prepare(evidence)


def controlled(monkeypatch, stocks, reference=None):
    calls = []

    def acquire(store, owner, bounds, descriptor, **kwargs):
        calls.append((descriptor, kwargs["saved"]))
        assert owner == "alice" and kwargs["warmup_sessions"] == 100
        assert descriptor["interval"] == "D"
        assert bounds["equity_curve"][0]["date"] == stocks["AAA"]["calendar"]["sessions"][100]
        if descriptor["symbol"] == "NIFTY":
            assert descriptor["exchange"] == "NSE_INDEX" and descriptor["role"] == "benchmark"
            return reference if reference is not None else benchmark()
        assert descriptor["exchange"] == "NSE" and descriptor["role"] == "feature_warmup"
        return stocks[descriptor["symbol"]]

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    return calls


def test_one_shared_benchmark_and_artifact_only_stock_progress(archive, monkeypatch):
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    evidence = execution(*stocks.values())
    before = deepcopy(evidence)
    calls = controlled(monkeypatch, stocks)
    result, states, progress, events = prepare(evidence)
    assert result["automatic_stock_features"] == context(*stocks.values())
    assert evidence == before
    assert [descriptor["symbol"] for descriptor, _ in calls] == ["AAA", "BBB", "NIFTY"]
    assert states[-1]["complete"] and progress[-1] == (3, 3)
    assert any(event["prices"].get("current_symbol") == "NIFTY" for event in events)
    for state in states:
        assert state["phase"] == "stock_context_prices"
        assert state["state_id"] == fingerprint({k: v for k, v in state.items() if k != "state_id"})
        if state["stock_checkpoint"] is not None:
            assert set(state["stock_checkpoint"]) == {"inputs_artifact"}
        assert all(reference in archive for reference in _input_references(state))


@pytest.mark.parametrize("interrupt", ["BBB", "NIFTY"])
def test_partial_stock_or_benchmark_resume_preserves_finished_evidence(
    archive, monkeypatch, interrupt
):
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    evidence = execution(*stocks.values())
    captured, calls = [], []
    native_state = {"phase": "benchmark", "binding": "controlled", "acquisition": {"receipts": []}}

    def acquire(store, owner, bounds, descriptor, **kwargs):
        symbol = descriptor["symbol"]
        calls.append((symbol, deepcopy(kwargs["saved"])))
        if symbol == interrupt and kwargs["saved"] is None:
            kwargs["checkpoint"](native_state, {"completed": 0, "total": 1})
            raise RuntimeError("controlled interruption")
        return benchmark() if symbol == "NIFTY" else stocks[symbol]

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    with pytest.raises(RuntimeError, match="controlled interruption"):
        prepare(evidence, checkpoint=lambda state, _: captured.append(deepcopy(state)))
    completed, states, _, _ = prepare(evidence, saved=captured[-1])
    expected = [("AAA", None), ("BBB", None)]
    if interrupt == "BBB":
        expected += [("BBB", native_state), ("NIFTY", None)]
    else:
        expected += [("NIFTY", None), ("NIFTY", native_state)]
    assert calls == expected
    assert completed["automatic_stock_features"] == context(*stocks.values())
    monkeypatch.setattr(
        research_benchmarks, "prepare", lambda *a, **kw: pytest.fail("No reacquisition")
    )
    reopened, new_states, _, _ = prepare(completed, saved=states[-1])
    assert reopened == completed and new_states == []


def test_old_complete_stock_bundle_is_reused_and_missing_benchmark_bars_retained(
    archive, monkeypatch
):
    stock = record()
    evidence = execution(stock)
    evidence["automatic_stock_features"] = bundle(stock)
    ref = benchmark(missing=(115,))
    calls = controlled(monkeypatch, {"AAA": stock}, reference=ref)
    result, states, _, _ = prepare(evidence)
    assert [descriptor["symbol"] for descriptor, _ in calls] == ["NIFTY"]
    assert result["automatic_stock_features"] == context(stock, reference=ref)
    assert validate_bundle(result["automatic_stock_features"])
    assert result["automatic_stock_features"]["benchmark"]["evidence"]["coverage"]["missing_dates"]
    # A loader may supply the stock-only evidence saved during preparation.
    stock_only = archive[states[-2]["inputs_artifact"]]
    assert stock_only["automatic_stock_features"]["version"] == "research-stock-features-v1"


def test_owner_input_state_and_completed_leaf_changes_fail_before_downloads(archive, monkeypatch):
    evidence = execution(record())
    captured = []

    def stop(state, counts):
        captured.append(deepcopy(state))
        raise RuntimeError("pause")

    with pytest.raises(RuntimeError, match="pause"):
        prepare(evidence, checkpoint=stop)
    monkeypatch.setattr(
        research_benchmarks, "prepare", lambda *a, **kw: pytest.fail("No price work")
    )
    with pytest.raises(ValueError, match="different inputs"):
        prepare(evidence, saved=captured[-1], owner="bob")
    altered = deepcopy(evidence)
    altered["strategies"][0]["config"]["target_pct"] += 1
    with pytest.raises(ValueError, match="different inputs"):
        prepare(altered, saved=captured[-1])
    wrong = deepcopy(captured[-1])
    wrong["benchmark_checkpoint"] = {"phase": "benchmark"}
    with pytest.raises(ValueError, match="different inputs"):
        prepare(evidence, saved=wrong)
    reference = captured[-1]["stock_checkpoint"]["inputs_artifact"]
    archive[reference]["binding"] = "changed"
    with pytest.raises(ValueError, match="checkpoint integrity"):
        prepare(evidence, saved=captured[-1])


def test_replay_context_offline_and_stock_only_replay_refuses_acquisition(archive, monkeypatch):
    stock = record()
    evidence = execution(stock)
    evidence["replay_origin"] = {"parent_job_id": "controlled"}
    monkeypatch.setattr(
        research_benchmarks, "prepare", lambda *a, **kw: pytest.fail("No replay downloads")
    )
    with pytest.raises(ValueError, match="original saved stock and benchmark"):
        prepare(evidence)
    evidence["automatic_stock_features"] = bundle(stock)
    with pytest.raises(ValueError, match="original saved stock and benchmark"):
        prepare(evidence)
    evidence["automatic_stock_features"] = context(stock)
    result, states, _, _ = prepare(evidence)
    assert result == evidence and not states


def test_calculation_resume_uses_retained_context_without_repreparation(archive, monkeypatch):
    stock = record()
    evidence = execution(stock)
    evidence["automatic_stock_features"] = context(stock)
    monkeypatch.setattr(
        research_benchmarks, "prepare", lambda *a, **kw: pytest.fail("No resumed downloads")
    )
    result, states, _, _ = prepare(
        evidence,
        saved={
            "phase": "calculation",
            "inputs_artifact": fingerprint(evidence),
            "calculation": {"trials": [{"number": 1}]},
        },
    )
    assert result == evidence and not states


def test_runner_checkpoint_before_context_can_begin_acquisition(archive, monkeypatch):
    stock = record()
    evidence = execution(stock)
    controlled(monkeypatch, {"AAA": stock})
    result, states, _, _ = prepare(evidence, saved={"phase": "calculation", "calculation": None})
    assert result["automatic_stock_features"] == context(stock) and states[-1]["complete"]


def test_calculation_without_original_context_and_foreign_phase_refuse_downloads(
    archive, monkeypatch
):
    monkeypatch.setattr(
        research_benchmarks, "prepare", lambda *a, **kw: pytest.fail("No resumed downloads")
    )
    evidence = execution(record())
    with pytest.raises(ValueError, match="original stock and benchmark"):
        prepare(evidence, saved={"phase": "calculation", "calculation": {"trials": [1]}})
    with pytest.raises(ValueError, match="different inputs"):
        prepare(evidence, saved={"phase": "prices"})


def test_calendar_mismatch_cannot_commit_context(archive, monkeypatch):
    stock = record()
    reference = benchmark()
    reference["calendar"]["provenance"]["source"] = "different reference"
    controlled(monkeypatch, {"AAA": stock}, reference=reference)
    with pytest.raises(ValueError, match="calendar"):
        prepare(execution(stock))


@pytest.mark.parametrize(
    "failure,expected",
    [
        (
            "Reconnect your broker, then retry the benchmark. Your report is saved.",
            "Reconnect your broker, then resume research",
        ),
        (
            "The benchmark could not be downloaded. Retry when the broker is available.",
            "NIFTY prices could not be downloaded",
        ),
    ],
)
def test_broker_failure_saves_context_and_can_resume(archive, monkeypatch, failure, expected):
    stock = record()
    evidence = execution(stock)
    evidence["automatic_stock_features"] = bundle(stock)
    captured = []
    native_state = {"phase": "benchmark", "binding": "controlled", "acquisition": {"receipts": []}}

    def fail(store, owner, bounds, descriptor, **kwargs):
        kwargs["checkpoint"](native_state, {"completed": 0, "total": 1})
        raise ValueError(failure)

    monkeypatch.setattr(research_benchmarks, "prepare", fail)
    with pytest.raises(ValueError, match=expected):
        prepare(evidence, checkpoint=lambda state, _: captured.append(deepcopy(state)))
    saved = captured[-1]
    assert saved["benchmark_checkpoint"] == native_state and not saved["complete"]
    calls = controlled(monkeypatch, {"AAA": stock})
    completed, _, _, _ = prepare(evidence, saved=saved)
    assert calls[0][1] == native_state
    assert completed["automatic_stock_features"] == context(stock)


def test_native_facade_cache_and_broker_share_one_benchmark_and_close_resources(
    archive,
    native,
    monkeypatch,
    tmp_path,  # noqa: F811 -- shared isolated fixtures
):
    from services.research_historify import NativeHistorifyArchive

    _, path, connections = native
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    ref = benchmark(missing=(115,))
    calls, credentials = [], []
    monkeypatch.setattr(
        "services.research_native_calendar.native_calendar_window",
        lambda *a, **kw: deepcopy(stocks["AAA"]["calendar"]),
    )
    monkeypatch.setattr(
        "services.research_sources.acquisition_paths", lambda store: (path, tmp_path / "receipts")
    )

    def login(owner):
        credentials.append(owner)
        return {"broker": "controlled", "auth_token": "context-test-only"}

    monkeypatch.setattr("services.research_sources.resolve_broker_session", login)

    def history(**request):
        assert connections["active"] == 0
        assert request["interval"] == "D"
        calls.append(request)
        source = ref if request["symbol"] == "NIFTY" else stocks[request["symbol"]]
        return (
            True,
            {
                "data": [
                    {**candle, "volume": 0, "oi": 0}
                    for day, candle in source["evidence"]["bars"].items()
                    if request["start_date"] <= day <= request["end_date"]
                ]
            },
            200,
        )

    monkeypatch.setattr("services.research_native_prices.native_history", history)
    stock_archive = NativeHistorifyArchive(path, interval="D", exchange="NSE")
    for symbol, value in stocks.items():
        stock_archive.write(
            symbol, [{**bar, "volume": 0, "oi": 0} for bar in value["evidence"]["bars"].values()]
        )
    captured = []

    def pause_after_reference(state, counts):
        captured.append(deepcopy(state))
        if (state["benchmark_checkpoint"] or {}).get("inputs_artifact") and not state["complete"]:
            raise RuntimeError("pause after benchmark commit")

    evidence = execution(*stocks.values())
    with pytest.raises(RuntimeError, match="benchmark commit"):
        prepare(evidence, checkpoint=pause_after_reference)
    downloaded = len(calls)
    result, states, _, _ = prepare(evidence, saved=captured[-1])
    assert len(calls) == downloaded
    retained = result["automatic_stock_features"]
    assert {request["symbol"] for request in calls} == {"NIFTY"}
    assert all(request["exchange"] == "NSE_INDEX" for request in calls)
    assert credentials == ["alice"]
    assert retained["benchmark"]["evidence"]["coverage"]["missing_dates"] == [
        stocks["AAA"]["calendar"]["sessions"][115]
    ]
    assert retained["benchmark"]["acquisition_receipts"]
    assert connections["active"] == 0 and connections["opened"] == connections["closed"]
    monkeypatch.setattr(
        "services.research_native_prices.native_history",
        lambda **kw: pytest.fail("Completed context is offline"),
    )
    for _ in range(100):
        reopened, fresh, _, _ = prepare(result, saved=states[-1])
        assert reopened == result and not fresh
    assert connections["active"] == 0 and connections["opened"] == connections["closed"]
