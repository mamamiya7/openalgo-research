"""Saved trade charts preserve native observations, ownership and period fences."""

# ruff: noqa: F811 -- shared isolated pytest fixtures

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from test_jobs import app, client  # noqa: F401
from test_vectorbt_portfolio import DAYS, candle, snapshot

from database.research_db import ResearchJob
from services import research_trade_chart as charts
from services import scanner_research_service as service

JOB = "a" * 32


def retained(app, *, minute=False, report=None, prices=None, origin=None):
    store = app.extensions["research_store"]
    prices = prices or snapshot(symbols=("AAA",), minute=minute)
    if report is None:
        trade = {
            "symbol": "AAA",
            "strategy_id": "first",
            "strategy_name": "First",
            "source_row": 2,
            "signal_date": DAYS[0],
            "status": "closed",
            "entry_date": DAYS[0],
            "exit_date": DAYS[1],
            "entry_price": 100.125,
            "exit_price": 101.25,
            "quantity": 7,
            "pnl": 7.875,
            "fees": 0,
            "exit_timing": "close",
        }
        if minute:
            trade.update(
                entry_timestamp=prices["timeline"][0], exit_timestamp=prices["timeline"][2]
            )
        report = {
            "ledger": [trade],
            "portfolio": {"engine": "vectorbt"},
            "coverage": {"date_from": DAYS[0], "date_to": DAYS[-1]},
            "strategies": [{"id": "first", "name": "First", "allocation_pct": 100, "config": {}}],
        }
    if origin:
        report["replay_origin"] = origin
    inputs = {"snapshot": prices, "frozen_prices": True}
    inputs_id = service.save_artifact(store, inputs)
    bundle = {
        "kind": "portfolio_backtest",
        "job_id": JOB,
        "inputs_artifact": inputs_id,
        "result": report,
    }
    result_id = service.save_artifact(store, bundle)
    with store.sessions.begin() as db:
        db.add(
            ResearchJob(
                id=JOB,
                owner="research-test",
                source_id="b" * 32,
                config="{}",
                status="completed",
                progress=100,
                created_at=1,
                updated_at=1,
                result_artifact=result_id,
            )
        )
    return result_id, inputs_id, report


def get(client, artifact, *, trade=0, period="full", **query):
    return client.get(
        f"/scanner-research/api/portfolio/jobs/{JOB}/trades/{trade}/chart",
        query_string={"expected_result_artifact": artifact, "period": period, **query},
    )


def digest_files(store):
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (store.root / "artifacts").iterdir()
        if path.is_file()
    }


def test_exact_daily_prices_markers_and_artifacts_unchanged(app, client, monkeypatch):
    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"][DAYS[0]] = {
        **candle(100.000123, high=105.2, low=97.4),
        "timestamp": 1767571200,
    }
    artifact, inputs_id, report = retained(app, prices=prices)
    store = app.extensions["research_store"]
    before = digest_files(store)
    monkeypatch.setattr(service, "save_artifact", lambda *_: pytest.fail("chart wrote evidence"))
    response = get(client, artifact)
    assert response.status_code == 200, response.json
    chart = response.json
    assert chart["identity"]["inputs_artifact"] == inputs_id
    assert chart["trade"] == report["ledger"][0]
    assert chart["candles"][0]["open"] == 100.000123
    assert chart["candles"][0]["source_timestamp"] == 1767571200
    assert chart["candles"][0]["time"] == int(datetime(2026, 1, 5, tzinfo=UTC).timestamp())
    assert chart["markers"][0]["price"] == 100.125
    assert [marker["bar_index"] for marker in chart["markers"]] == [0, 1]
    assert "display coordinate" in chart["markers"][0]["basis"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert get(client, artifact).json == chart
    assert digest_files(store) == before


def test_minute_close_maps_to_preceding_observed_candle_without_rounding(app, client):
    artifact, _, report = retained(app, minute=True)
    chart = get(client, artifact).json
    exit_marker = chart["markers"][1]
    assert exit_marker["timestamp"] == report["ledger"][0]["exit_timestamp"]
    assert exit_marker["time"] == chart["candles"][1]["time"]
    assert exit_marker["bar_index"] == 1
    assert chart["candles"][0]["timestamp"].endswith("+05:30")
    assert "preceding observed candle" in exit_marker["basis"]


def test_missing_exit_candle_does_not_snap_marker_to_neighbor(app, client):
    prices = snapshot(symbols=("AAA",), minute=True)
    prices["bars"]["AAA"].pop(prices["timeline"][1])
    artifact, _, _ = retained(app, minute=True, prices=prices)
    chart = get(client, artifact).json
    assert [marker["kind"] for marker in chart["markers"]] == ["entry"]
    assert chart["unmapped_markers"] == ["exit"]
    assert chart["window"]["exit_index"] is None
    assert len(chart["candles"]) == 19


def test_auth_owner_stale_identity_and_unpublished_period(app, client, monkeypatch):
    artifact, _, _ = retained(app)
    assert (
        app.test_client()
        .get(f"/scanner-research/api/portfolio/jobs/{JOB}/trades/0/chart")
        .status_code
        == 401
    )
    with client.session_transaction() as session:
        session["user"] = "other-owner"
    monkeypatch.setattr(
        service, "read_artifact", lambda *_: pytest.fail("foreign artifact was read")
    )
    assert get(client, artifact).status_code == 404
    with client.session_transaction() as session:
        session["user"] = "research-test"
    assert get(client, "c" * 64).status_code == 409
    monkeypatch.undo()
    assert get(client, artifact, period="evaluation").status_code == 400
    assert get(client, artifact, period="selection").status_code == 400


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 2001},
        {"limit": 0},
        {"offset": -1},
        {"limit": "1.0"},
        {"unexpected": 1},
        {"limit": [1, 2]},
    ],
)
def test_query_bounds_are_strict(app, client, query):
    artifact, _, _ = retained(app)
    assert get(client, artifact, **query).status_code == 400


def test_large_minute_trade_pages_every_observation_once(app, client):
    start = datetime.fromisoformat("2026-01-05T09:15:00+05:30")
    keys = [(start + timedelta(minutes=i)).isoformat() for i in range(5007)]
    prices = snapshot(symbols=("AAA",), minute=True)
    prices["timeline"] = keys
    prices["bars"]["AAA"] = {key: candle(100 + i / 100) for i, key in enumerate(keys)}
    trade = {
        "symbol": "AAA",
        "status": "closed",
        "entry_date": keys[0][:10],
        "exit_date": keys[-1][:10],
        "entry_timestamp": keys[0],
        "exit_timestamp": keys[-1],
        "entry_price": 100,
        "exit_price": 150,
        "quantity": 2,
        "exit_timing": "open",
    }
    report = {"ledger": [trade], "coverage": {"date_from": keys[0][:10], "date_to": keys[-1][:10]}}
    artifact, _, _ = retained(app, prices=prices, report=report)
    all_bars, offset = [], 0
    while offset is not None:
        response = get(client, artifact, offset=offset, limit=2000)
        assert response.status_code == 200, response.json
        chart = response.json
        assert chart["window"]["total"] == 5007
        assert chart["window"]["entry_index"] == 0
        assert chart["window"]["exit_index"] == 5006
        assert len(chart["candles"]) <= 2000
        all_bars.extend(chart["candles"])
        offset = chart["window"]["next_offset"]
    assert [bar["timestamp"] for bar in all_bars] == keys
    assert get(client, artifact, offset=5007).status_code == 400


def test_published_earlier_later_fences_and_original_ledger_index(app, client):
    trade = {
        "symbol": "AAA",
        "status": "closed",
        "entry_date": DAYS[0],
        "exit_date": DAYS[1],
        "entry_price": 100,
        "exit_price": 101,
        "quantity": 1,
    }
    excluded = {"symbol": "AAA", "status": "excluded", "quantity": 0}
    later = {**trade, "entry_date": DAYS[2], "exit_date": DAYS[3]}
    report = {
        "ledger": [excluded, trade],
        "coverage": {"date_from": DAYS[0], "date_to": DAYS[-1]},
        "validation": {
            "train_to": DAYS[1],
            "test_from": DAYS[2],
            "test_to": DAYS[-1],
            "result": {"ledger": [later], "coverage": {"date_from": DAYS[0], "date_to": DAYS[-1]}},
        },
    }
    artifact, _, _ = retained(app, report=report)
    earlier = get(client, artifact, trade=1, period="selection").json
    assert [bar["timestamp"] for bar in earlier["candles"]] == DAYS[:2]
    assert earlier["identity"]["trade_index"] == 1
    assert get(client, artifact, trade=0, period="selection").status_code == 400
    after = get(client, artifact, trade=0, period="evaluation").json
    assert [bar["timestamp"] for bar in after["candles"]] == DAYS[2:]
    assert get(client, artifact, period="full").status_code == 400


def test_reserved_later_prices_never_returned(app, client):
    trade = {
        "symbol": "AAA",
        "status": "pending",
        "entry_date": DAYS[0],
        "entry_price": 100,
        "quantity": 1,
    }
    report = {
        "ledger": [trade],
        "coverage": {"date_from": DAYS[0], "date_to": DAYS[-1]},
        "reserved_evaluation": {
            "selection": {"from": DAYS[0], "to": DAYS[1]},
            "evaluation": {"from": DAYS[2], "to": DAYS[-1]},
        },
    }
    artifact, _, _ = retained(app, report=report)
    chart = get(client, artifact, period="selection").json
    assert [bar["timestamp"] for bar in chart["candles"]] == DAYS[:2]
    assert chart["window"]["exit_index"] is None
    assert get(client, artifact, period="evaluation").status_code == 400


@pytest.mark.parametrize("kind", ["candidate_report", "condition_replay"])
def test_child_reports_use_their_own_exact_inputs(app, client, kind):
    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"][DAYS[0]]["close"] = 103.25
    trade = {
        "symbol": "AAA",
        "status": "closed",
        "entry_date": DAYS[0],
        "exit_date": DAYS[1],
        "entry_price": 102,
        "exit_price": 103,
        "quantity": 3,
    }
    report = {
        "ledger": [trade],
        "coverage": {"date_from": DAYS[0], "date_to": DAYS[1]},
        kind: {"parent_result_artifact": "d" * 64},
    }
    artifact, inputs_id, _ = retained(app, prices=prices, report=report)
    chart = get(client, artifact).json
    assert chart["identity"]["result_artifact"] == artifact
    assert chart["identity"]["inputs_artifact"] == inputs_id
    assert chart["candles"][0]["close"] == 103.25


def test_replay_report_period_identity_and_evaluation_basis_boundary(app, client):
    prices = snapshot(symbols=("AAA",), minute=True)
    trade = {
        "symbol": "AAA",
        "status": "closed",
        "entry_date": DAYS[0],
        "exit_date": DAYS[0],
        "entry_timestamp": prices["timeline"][1],
        "exit_timestamp": prices["timeline"][2],
        "entry_price": 101,
        "exit_price": 102,
        "quantity": 1,
        "exit_timing": "intraday",
    }
    report = {
        "ledger": [trade],
        "evaluation_basis": {
            "period": {"from": prices["timeline"][1], "to": prices["timeline"][3]}
        },
    }
    artifact, _, _ = retained(app, prices=prices, report=report, origin={"period": "evaluation"})
    chart = get(client, artifact, period="evaluation").json
    assert [bar["timestamp"] for bar in chart["candles"]] == prices["timeline"][1:4]
    assert "exact time inside the minute is unknown" in chart["markers"][1]["basis"]
    assert get(client, artifact, period="full").status_code == 400


def test_missing_corrupted_inputs_fail_without_changing_saved_result(app, client):
    artifact, inputs_id, _ = retained(app)
    store = app.extensions["research_store"]
    path = store.root / "artifacts" / f"{inputs_id}.json"
    path.write_text('{"modified":true}', encoding="utf-8")
    before = digest_files(store)
    response = get(client, artifact)
    assert response.status_code == 400
    assert "integrity" in response.json["message"]
    assert digest_files(store) == before


def test_nautilus_minute_markers_disclose_modelled_events(app, client):
    prices = snapshot(symbols=("AAA",), minute=True)
    trade = {
        "symbol": "AAA",
        "status": "closed",
        "entry_date": DAYS[0],
        "exit_date": DAYS[0],
        "entry_timestamp": prices["timeline"][0],
        "exit_timestamp": prices["timeline"][2],
        "entry_price": 100.25,
        "exit_price": 101.5,
        "quantity": 2,
        "exit_timing": "intraday",
    }
    report = {
        "ledger": [trade],
        "execution": {"engine": "nautilus"},
        "coverage": {"date_from": DAYS[0], "date_to": DAYS[-1]},
    }
    artifact, _, _ = retained(app, prices=prices, report=report)
    response = get(client, artifact)
    assert response.status_code == 200
    assert all(
        "model-derived OHLC events" in marker["basis"] for marker in response.json["markers"]
    )
    assert response.json["markers"][1]["time"] == response.json["candles"][2]["time"]


def test_unrecorded_raw_candles_and_other_symbols_stay_out(app, client):
    prices = snapshot(symbols=("AAA", "BBB"))
    prices["raw_bars"] = {"AAA": {"2026-01-01": candle(900)}}
    prices["bars"]["AAA"]["2026-01-01"] = candle(900)
    artifact, _, _ = retained(app, prices=prices)
    chart = get(client, artifact).json
    assert [bar["timestamp"] for bar in chart["candles"]] == DAYS
    assert chart["symbol"] == "AAA"


@pytest.mark.parametrize("replacement", ["kind", "job_id", "status", "period", "missing_inputs"])
def test_unsupported_saved_identity_is_rejected(app, client, replacement):
    artifact, _, _ = retained(app)
    store = app.extensions["research_store"]
    bundle = service.read_artifact(store, artifact)
    if replacement == "status":
        with store.sessions.begin() as db:
            db.get(ResearchJob, JOB).status = "running"
    else:
        if replacement == "kind":
            bundle["kind"] = "portfolio_analysis"
        elif replacement == "job_id":
            bundle["job_id"] = "f" * 32
        elif replacement == "missing_inputs":
            bundle.pop("inputs_artifact")
        else:
            bundle["result"].pop("coverage")
        artifact = service.save_artifact(store, bundle)
        with store.sessions.begin() as db:
            db.get(ResearchJob, JOB).result_artifact = artifact
    assert get(client, artifact).status_code == 400


def test_only_explicit_finite_saved_volume_is_returned(app, client):
    prices = snapshot(symbols=("AAA",))
    prices["bars"]["AAA"][DAYS[0]]["volume"] = 1234.5
    prices["bars"]["AAA"][DAYS[1]]["volume"] = 0
    artifact, _, _ = retained(app, prices=prices)
    bars = get(client, artifact).json["candles"]
    assert bars[0]["volume"] == 1234.5
    assert bars[1]["volume"] == 0
    assert "volume" not in bars[2]


@pytest.mark.parametrize("minute", [False, True])
def test_actual_vectorbt_saved_ledger_chart_matches_fill_and_ohlc(app, client, minute):
    pytest.importorskip("vectorbt")
    from test_vectorbt_portfolio import signal, strategy

    from research.connectors.vectorbt_portfolio import evaluate

    prices = snapshot(symbols=("AAA",), minute=minute)
    config = {"trade_horizon": "intraday", "hold_minutes": 2} if minute else {}
    report = evaluate(
        [
            strategy(
                "first",
                allocation=100,
                signals=[signal(timestamp="09:15" if minute else None)],
                **config,
            )
        ],
        prices,
        10000,
    )
    assert report["ledger"][0]["status"] == "closed"
    artifact, _, _ = retained(app, prices=prices, report=report)
    response = get(client, artifact)
    assert response.status_code == 200, response.json
    chart, trade = response.json, report["ledger"][0]
    assert chart["trade"] == trade
    for marker in chart["markers"]:
        kind = marker["kind"]
        assert marker["price"] == trade[f"{kind}_price"]
        assert marker["quantity"] == trade["quantity"]
        mapped = chart["candles"][marker["bar_index"]]
        assert marker["time"] == mapped["time"]
        assert {key: mapped[key] for key in ("open", "high", "low", "close")} == prices["bars"][
            "AAA"
        ][mapped["timestamp"]]
        recorded = trade.get(f"{kind}_timestamp") or trade[f"{kind}_date"]
        assert marker["timestamp"] == recorded
    assert len(chart["markers"]) == 2
