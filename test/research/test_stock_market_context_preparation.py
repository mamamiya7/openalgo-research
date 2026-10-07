"""Isolated acquisition/recovery with one wider index and original stock windows."""

# ruff: noqa: F811 -- imported pytest fixtures are injected into test arguments

from copy import deepcopy

import pytest
from test_historify_scope import native  # noqa: F401
from test_stock_feature_preparation import archive  # noqa: F401
from test_stock_features import bundle as stock_bundle
from test_stock_market_context import fixtures

from research.market_series import fingerprint
from research.stock_market_context import KEY, freeze_bundle, stock_view
from services import research_benchmarks, research_regimes, research_stock_market_context
from services.research_storage import _input_references


def prepare(evidence, *, saved=None, checkpoint=None, owner="alice", cancelled=lambda: None):
    states, events, progress = [], [], []

    def save(state, counts):
        states.append(deepcopy(state))
        if checkpoint:
            checkpoint(state, counts)

    result = research_stock_market_context.prepare_context(
        object(),
        owner,
        evidence,
        saved=saved,
        checkpoint=save,
        progress=lambda *row: progress.append(row),
        cancelled=cancelled,
        activity=events.append,
    )
    return result, states, events, progress


def controlled(monkeypatch, stocks, market, *, interrupt=None):
    calls = []
    pending = {"phase": "benchmark", "binding": "controlled", "acquisition": {"receipts": []}}

    def acquire(store, owner, bounds, descriptor, **kwargs):
        symbol = descriptor["symbol"]
        calls.append((symbol, kwargs["warmup_sessions"], deepcopy(kwargs["saved"])))
        if symbol == interrupt and kwargs["saved"] is None:
            kwargs["checkpoint"](pending, {"completed": 0, "total": 1})
            raise RuntimeError("controlled interruption")
        assert owner == "alice"
        if symbol == "NIFTY":
            assert descriptor["exchange"] == "NSE_INDEX" and kwargs["warmup_sessions"] == 160
            return market
        assert descriptor["exchange"] == "NSE" and kwargs["warmup_sessions"] == 100
        return stocks[symbol]

    monkeypatch.setattr(research_benchmarks, "prepare", acquire)
    monkeypatch.setattr(research_regimes, "prepare", acquire)
    return calls, pending


def test_native_stock_path_and_one_union_reference_preserve_original_inputs(archive, monkeypatch):
    stocks, market, evidence = fixtures(symbols=("AAA", "BBB"))
    original = deepcopy(evidence)
    calls, _ = controlled(monkeypatch, stocks, market)
    result, states, events, progress = prepare(evidence)
    assert evidence == original and result == {
        **original,
        KEY: freeze_bundle(stocks, market, evidence["snapshot"]["sessions"]),
    }
    assert calls == [("AAA", 100, None), ("BBB", 100, None), ("NIFTY", 160, None)]
    assert progress[-1] == (3, 3) and states[-1]["complete"]
    assert any(row.get("prices", {}).get("current_symbol") == "NIFTY" for row in events)
    for state in states:
        assert state["state_id"] == fingerprint({k: v for k, v in state.items() if k != "state_id"})
        for key in ("stock_checkpoint", "market_checkpoint"):
            assert state[key] is None or set(state[key]) == {"inputs_artifact"}
        assert all(reference in archive for reference in _input_references(state))


@pytest.mark.parametrize("interrupt", ["BBB", "NIFTY"])
def test_interrupted_child_resumes_and_does_not_repeat_completed_symbols(
    archive, monkeypatch, interrupt
):
    stocks, market, evidence = fixtures(symbols=("AAA", "BBB"))
    calls, pending = controlled(monkeypatch, stocks, market, interrupt=interrupt)
    saved = []
    with pytest.raises(RuntimeError, match="controlled interruption"):
        prepare(evidence, checkpoint=lambda state, _: saved.append(deepcopy(state)))
    result, states, _, _ = prepare(evidence, saved=saved[-1])
    assert result[KEY] == freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    expected = [("AAA", 100, None), ("BBB", 100, None)]
    expected += (
        [("BBB", 100, pending), ("NIFTY", 160, None)]
        if interrupt == "BBB"
        else [("NIFTY", 160, None), ("NIFTY", 160, pending)]
    )
    assert calls == expected
    reopened, fresh, _, _ = prepare(result, saved=states[-1])
    assert reopened == result and not fresh and calls == expected


@pytest.mark.parametrize("old", ["stock", "relative"])
def test_existing_auxiliary_prices_are_reused_and_original_evidence_is_unchanged(
    archive, monkeypatch, old
):
    stocks, market, evidence = fixtures()
    context = freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    evidence["automatic_stock_features"] = (
        stock_bundle(*stocks.values()) if old == "stock" else stock_view(context)
    )
    before = deepcopy(evidence)
    calls, _ = controlled(monkeypatch, stocks, market)
    result, _, _, _ = prepare(evidence)
    assert calls == [("NIFTY", 160, None)]
    assert result["automatic_stock_features"] == before["automatic_stock_features"]
    assert result[KEY] == context and evidence == before


def test_owner_input_and_leaf_integrity_rejected_before_new_acquisition(archive, monkeypatch):
    stocks, market, evidence = fixtures()
    calls, _ = controlled(monkeypatch, stocks, market, interrupt="NIFTY")
    saved = []
    with pytest.raises(RuntimeError):
        prepare(evidence, checkpoint=lambda state, _: saved.append(deepcopy(state)))
    count = len(calls)
    with pytest.raises(ValueError, match="different inputs"):
        prepare(evidence, saved=saved[-1], owner="bob")
    changed = deepcopy(evidence)
    changed["strategies"][0]["config"]["target_pct"] += 1
    with pytest.raises(ValueError, match="different inputs"):
        prepare(changed, saved=saved[-1])
    reference = saved[-1]["market_checkpoint"]["inputs_artifact"]
    archive[reference]["binding"] = "tampered"
    with pytest.raises(ValueError, match="integrity"):
        prepare(evidence, saved=saved[-1])
    assert len(calls) == count


def test_replay_and_started_calculation_require_retained_context(archive, monkeypatch):
    stocks, market, evidence = fixtures()
    calls, _ = controlled(monkeypatch, stocks, market)
    for marker in ({"replay_origin": {"job": "saved"}}, {"parent_result_artifact": "a" * 64}):
        missing = {**evidence, **marker}
        with pytest.raises(ValueError, match="original saved stock-market"):
            prepare(missing)
        retained = {**missing, KEY: freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])}
        assert prepare(retained)[0] == retained
    with pytest.raises(ValueError, match="original stock-market"):
        prepare(evidence, saved={"phase": "calculation", "calculation": {"trials": [1]}})
    assert not calls
    assert prepare(evidence, saved={"phase": "calculation", "calculation": None})[0][KEY]


def test_changed_execution_calendar_or_symbols_cannot_reuse_context(archive, monkeypatch):
    stocks, market, evidence = fixtures()
    evidence[KEY] = freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    changed = deepcopy(evidence)
    changed["snapshot"]["session_hours"][changed["snapshot"]["sessions"][0]]["close"] = "15:29"
    with pytest.raises(ValueError, match="different execution session hours"):
        prepare(changed)
    changed = deepcopy(evidence)
    changed["strategies"][0]["signals"][0]["symbol"] = "BBB"
    with pytest.raises(ValueError, match="different execution inputs"):
        prepare(changed)


@pytest.mark.parametrize("span", [(0, 20), (10, 40), (40, 60)])
def test_exact_replay_uses_retained_full_context_for_contiguous_period_and_symbol_subset(
    archive, monkeypatch, span
):
    stocks, market, evidence = fixtures(symbols=("AAA", "BBB"))
    context = freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    evidence[KEY] = context
    before = deepcopy(context)
    evidence["replay_origin"] = {"parent_job_id": "controlled"}
    selected = evidence["snapshot"]["sessions"][span[0] : span[1]]
    evidence["snapshot"]["sessions"] = selected
    evidence["snapshot"]["session_hours"] = {
        day: evidence["snapshot"]["session_hours"][day] for day in selected
    }
    evidence["strategies"][0]["signals"] = evidence["strategies"][0]["signals"][:1]
    calls, _ = controlled(monkeypatch, stocks, market)
    assert prepare(evidence)[0] == evidence
    assert context == before and not calls
    evidence.pop("replay_origin")
    with pytest.raises(ValueError, match="different execution inputs"):
        prepare(evidence)


def test_replay_does_not_admit_holes_reordering_or_changed_session_hours(archive):
    stocks, market, evidence = fixtures()
    evidence[KEY] = freeze_bundle(stocks, market, evidence["snapshot"]["sessions"])
    evidence["replay_origin"] = {"parent_job_id": "controlled"}
    for altered in (
        evidence["snapshot"]["sessions"][::2],
        list(reversed(evidence["snapshot"]["sessions"])),
    ):
        wrong = deepcopy(evidence)
        wrong["snapshot"]["sessions"] = altered
        with pytest.raises(ValueError, match="different execution inputs"):
            prepare(wrong)
    day = evidence["snapshot"]["sessions"][0]
    evidence["snapshot"]["session_hours"][day]["close"] = "15:29"
    with pytest.raises(ValueError, match="different execution session hours"):
        prepare(evidence)


def test_resource_bound_precedes_any_price_work(archive, monkeypatch):
    stocks, market, evidence = fixtures()
    calls, _ = controlled(monkeypatch, stocks, market)
    evidence["snapshot"]["sessions"] *= 4000
    with pytest.raises(ValueError, match="total session-bar limit"):
        prepare(evidence)
    assert not calls


@pytest.mark.parametrize("interrupt", ["acquisition", "complete"])
def test_native_archive_reuse_resume_receipts_and_balanced_resource_cleanup(
    archive, native, monkeypatch, tmp_path, interrupt
):
    from services.research_historify import NativeHistorifyArchive

    stocks, market, evidence = fixtures(symbols=("AAA", "BBB"), missing=(178,))
    _, path, connections = native
    calls = []
    monkeypatch.setattr(
        "services.research_native_calendar.native_calendar_window",
        lambda *a, **kw: deepcopy(
            market["calendar"] if kw["warmup_sessions"] == 160 else stocks["AAA"]["calendar"]
        ),
    )
    monkeypatch.setattr(
        "services.research_sources.acquisition_paths", lambda store: (path, tmp_path / "receipts")
    )
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda owner: {"broker": "controlled", "auth_token": "isolated-test-only"},
    )

    def history(**request):
        assert connections["active"] == 0
        assert request["symbol"] == "NIFTY" and request["exchange"] == "NSE_INDEX"
        calls.append(request)
        return (
            True,
            {
                "data": [
                    {**bar, "volume": 0, "oi": 0}
                    for day, bar in market["evidence"]["bars"].items()
                    if request["start_date"] <= day <= request["end_date"]
                ]
            },
            200,
        )

    monkeypatch.setattr("services.research_native_prices.native_history", history)
    store = NativeHistorifyArchive(path, interval="D", exchange="NSE")
    for symbol, item in stocks.items():
        store.write(
            symbol, [{**bar, "volume": 0, "oi": 0} for bar in item["evidence"]["bars"].values()]
        )
    captured = []

    def pause(state, _):
        captured.append(deepcopy(state))
        ref = state["market_checkpoint"]
        child = archive[ref["inputs_artifact"]] if ref else {}
        committed = (
            child.get("inputs_artifact")
            if interrupt == "complete"
            else child.get("acquisition", {}).get("bars", {}).get("NIFTY")
        )
        if committed and not state["complete"]:
            raise RuntimeError("pause after committed market")

    with pytest.raises(RuntimeError, match="committed market"):
        prepare(evidence, checkpoint=pause)
    downloaded = len(calls)
    for sidecar in (tmp_path / "receipts").glob("*.json"):
        sidecar.unlink()
    result, states, _, _ = prepare(evidence, saved=captured[-1])
    assert downloaded >= 1
    if interrupt == "complete":
        assert len(calls) == downloaded
    else:
        # The native facade chunks the union window. Resume can request its
        # unfinished tail, but never re-request the already committed chunk.
        assert len(calls) > downloaded
        assert all(
            request["start_date"] > calls[downloaded - 1]["end_date"]
            for request in calls[downloaded:]
        )
        assert list((tmp_path / "receipts").glob("*.json"))
    assert result[KEY]["market"]["evidence"]["bars"] == market["evidence"]["bars"]
    assert result[KEY]["market"]["acquisition_receipts"]
    assert result[KEY]["market"]["evidence"]["coverage"]["missing_dates"] == [
        market["calendar"]["sessions"][178]
    ]
    assert connections["active"] == 0 and connections["opened"] == connections["closed"]
    counts = dict(connections)
    completed_calls = len(calls)
    for _ in range(20):
        reopened, fresh, _, _ = prepare(result, saved=states[-1])
        assert reopened == result and not fresh
    assert connections == counts and len(calls) == completed_calls
