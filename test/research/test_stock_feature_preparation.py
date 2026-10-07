"""Durable per-symbol recovery without brokers or user runtime databases."""

from copy import deepcopy

import pytest
from test_historify_scope import native  # noqa: F401 -- isolated real archive fixture
from test_stock_features import bundle, execution, record

from research.stock_features import VERSION, fingerprint
from services import research_benchmarks, research_stock_features
from services import scanner_research_service as service
from services.research_activity import merge_activity
from services.research_storage import _input_references


@pytest.fixture
def archive(monkeypatch):
    artifacts = {}

    def save(store, value):
        digest = fingerprint(value)
        artifacts[digest] = deepcopy(value)
        return digest

    monkeypatch.setattr(service, "save_artifact", save)
    monkeypatch.setattr(service, "read_artifact", lambda store, digest: deepcopy(artifacts[digest]))
    return artifacts


def prepare(evidence, *, saved=None, checkpoint=None, owner="alice", cancelled=lambda: None):
    states = []

    def save(state, counts):
        states.append(deepcopy(state))
        if checkpoint:
            checkpoint(state, counts)

    result = research_stock_features.prepare_context(
        object(),
        owner,
        evidence,
        saved=saved,
        checkpoint=save,
        progress=lambda *args: None,
        cancelled=cancelled,
        activity=lambda event: merge_activity({}, event, 1),
    )
    return result, states


def test_correct_stock_descriptors_fixed_warmup_and_artifact_only_progress(archive, monkeypatch):
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    evidence = execution(*stocks.values())
    original = deepcopy(evidence)
    calls = []

    def acquire(store, owner, bounds, descriptor, **kwargs):
        calls.append(descriptor)
        assert owner == "alice" and descriptor["exchange"] == "NSE"
        assert descriptor["role"] == "feature_warmup" and descriptor["interval"] == "D"
        assert kwargs["warmup_sessions"] == 100
        assert bounds["equity_curve"][0]["date"] == evidence["snapshot"]["sessions"][0]
        return stocks[descriptor["symbol"]]

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    result, states = prepare(evidence)
    assert evidence == original
    assert result["automatic_stock_features"] == bundle(*stocks.values())
    assert [row["symbol"] for row in calls] == ["AAA", "BBB"]
    assert states[-1]["complete"] and states[-1]["current_symbol"] is None
    for state in states:
        assert state["version"] == VERSION
        assert state["state_id"] == fingerprint({k: v for k, v in state.items() if k != "state_id"})
        assert all(
            set(value) == {"inputs_artifact"} for value in state["completed_symbols"].values()
        )
        assert all(reference in archive for reference in _input_references(state))
    assert archive[states[-1]["inputs_artifact"]] == result


def test_exception_resumes_active_native_state_and_never_reacquires_completed_symbol(
    archive, monkeypatch
):
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    evidence = execution(*stocks.values())
    calls, states = [], []
    native_state = {
        "phase": "benchmark",
        "binding": "controlled-native",
        "acquisition": {"receipts": []},
    }

    def acquire(store, owner, bounds, descriptor, **kwargs):
        symbol = descriptor["symbol"]
        calls.append((symbol, deepcopy(kwargs["saved"])))
        if symbol == "BBB" and kwargs["saved"] is None:
            kwargs["checkpoint"](native_state, {})
            raise RuntimeError("controlled broker interruption")
        return stocks[symbol]

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    with pytest.raises(RuntimeError, match="controlled broker"):
        prepare(evidence, checkpoint=lambda state, _: states.append(deepcopy(state)))
    saved = states[-1]
    assert list(saved["completed_symbols"]) == ["AAA"]
    assert saved["current_symbol"] == "BBB" and saved["market_checkpoint"] == native_state
    completed, final = prepare(evidence, saved=saved)
    assert calls == [("AAA", None), ("BBB", None), ("BBB", native_state)]
    assert completed["automatic_stock_features"] == bundle(*stocks.values())
    monkeypatch.setattr(
        research_benchmarks,
        "prepare",
        lambda *a, **kw: pytest.fail("Committed completion cannot reacquire"),
    )
    reopened, checkpoints = prepare(completed, saved=final[-1])
    assert reopened == completed and checkpoints == []


def test_owner_settings_or_symbol_changes_cannot_resume_another_preparation(archive, monkeypatch):
    evidence = execution(record())
    captured = []

    def stop(state, counts):
        captured.append(deepcopy(state))
        raise RuntimeError("pause")

    with pytest.raises(RuntimeError, match="pause"):
        prepare(evidence, checkpoint=stop)
    monkeypatch.setattr(
        research_benchmarks,
        "prepare",
        lambda *a, **kw: pytest.fail("No price work before identity check"),
    )
    with pytest.raises(ValueError, match="different inputs"):
        prepare(evidence, saved=captured[-1], owner="bob")
    altered = deepcopy(evidence)
    altered["strategies"][0]["config"]["target_pct"] += 1
    with pytest.raises(ValueError, match="different inputs"):
        prepare(altered, saved=captured[-1])
    tampered = deepcopy(captured[-1])
    tampered["current_symbol"] = "OTHER"
    with pytest.raises(ValueError, match="different inputs"):
        prepare(evidence, saved=tampered)


def test_replay_uses_retained_features_offline_and_missing_replay_prices_fail(archive, monkeypatch):
    a, b = record(), record("BBB")
    replay = execution(a)
    replay["replay_origin"] = {"parent_job_id": "test"}
    monkeypatch.setattr(
        research_benchmarks, "prepare", lambda *a, **kw: pytest.fail("Replay cannot download")
    )
    with pytest.raises(ValueError, match="original saved"):
        prepare(replay)
    replay["automatic_stock_features"] = bundle(a, b)
    result, states = prepare(replay)
    assert result == replay and states == []  # A sliced replay may need fewer original symbols.


def test_bar_budget_cancellation_and_bad_completed_leaf_fail_before_new_downloads(
    archive, monkeypatch
):
    evidence = execution(record(), record("BBB"))
    monkeypatch.setattr(
        research_benchmarks, "prepare", lambda *a, **kw: pytest.fail("No new price work")
    )
    monkeypatch.setattr(research_stock_features, "MAX_FEATURE_BARS", 259)
    with pytest.raises(ValueError, match="session-bar limit"):
        prepare(evidence)
    monkeypatch.setattr(research_stock_features, "MAX_FEATURE_BARS", 200000)
    with pytest.raises(RuntimeError, match="cancelled"):
        prepare(evidence, cancelled=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))


def test_completed_leaf_tampering_is_rejected_before_next_symbol(archive, monkeypatch):
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    evidence = execution(*stocks.values())
    monkeypatch.setattr(
        research_benchmarks,
        "prepare",
        lambda store, owner, bounds, descriptor, **kwargs: stocks[descriptor["symbol"]],
    )
    saved = []

    def stop(state, counts):
        saved.append(deepcopy(state))
        if state["completed_symbols"]:
            raise RuntimeError("pause after first symbol")

    with pytest.raises(RuntimeError, match="first symbol"):
        prepare(evidence, checkpoint=stop)
    digest = saved[-1]["completed_symbols"]["AAA"]["inputs_artifact"]
    archive[digest]["acquisition_receipts"] = [{"sha256": "a" * 64, "receipt": {}}]
    monkeypatch.setattr(
        research_benchmarks,
        "prepare",
        lambda *a, **kw: pytest.fail("Invalid old evidence cannot trigger downloads"),
    )
    with pytest.raises(ValueError, match="integrity"):
        prepare(evidence, saved=saved[-1])


def test_real_native_facade_reuses_historify_and_retains_missing_sessions(
    archive,
    native,  # noqa: F811 -- shared isolated fixture
    monkeypatch,
    tmp_path,
):
    from test_stock_features import apply, hypothesis

    from services.research_historify import NativeHistorifyArchive

    _, path, connections = native
    stocks = {symbol: record(symbol) for symbol in ("AAA", "BBB")}
    days = stocks["AAA"]["calendar"]["sessions"]
    missing = days[115]
    evidence = execution(*stocks.values())
    before = deepcopy(evidence)
    calls, credentials = [], []
    monkeypatch.setattr(
        "services.research_native_calendar.native_calendar_window",
        lambda first, last, **kwargs: deepcopy(stocks["AAA"]["calendar"]),
    )
    monkeypatch.setattr(
        "services.research_sources.acquisition_paths", lambda store: (path, tmp_path / "receipts")
    )

    def login(owner):
        credentials.append(owner)
        return {"broker": "controlled", "auth_token": "stock-feature-test-only"}

    monkeypatch.setattr("services.research_sources.resolve_broker_session", login)

    def history(**request):
        assert connections["active"] == 0
        assert request["exchange"] == "NSE" and request["interval"] == "D"
        calls.append(request)
        return (
            True,
            {
                "data": [
                    {**candle, "volume": 0, "oi": 0}
                    for day, candle in stocks[request["symbol"]]["evidence"]["bars"].items()
                    if request["start_date"] <= day <= request["end_date"]
                    and not (request["symbol"] == "AAA" and day == missing)
                ]
            },
            200,
        )

    monkeypatch.setattr("services.research_native_prices.native_history", history)
    native_archive = NativeHistorifyArchive(path, interval="D", exchange="NSE")
    native_archive.write(
        "BBB",
        [{**candle, "volume": 0, "oi": 0} for candle in stocks["BBB"]["evidence"]["bars"].values()],
    )
    result, states = prepare(evidence)
    retained = result["automatic_stock_features"]
    assert evidence == before
    assert {request["symbol"] for request in calls} == {"AAA"}
    assert credentials == ["alice"]
    assert retained["series"]["AAA"]["evidence"]["coverage"]["missing_dates"] == [missing]
    assert all(row["acquisition_receipts"] for row in retained["series"].values())
    _, gate = apply(result, retained, hypothesis("ema_above_20"))
    assert gate["counts"]["unknown"] == 1
    assert missing in gate["membership"][0]["reason"]
    assert connections["active"] == 0
    assert connections["opened"] == connections["closed"]
    monkeypatch.setattr(
        "services.research_native_prices.native_history",
        lambda **kwargs: pytest.fail("Saved feature replay cannot request prices"),
    )
    repeated, new_states = prepare(result, saved=states[-1])
    assert repeated == result and new_states == []
