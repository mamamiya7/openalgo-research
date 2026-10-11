# ruff: noqa: F811 -- shared isolated native fixtures
"""Real native jobs, conditional Optuna, durable inspection and frozen stock replay."""

from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from test_automatic_workflow import app, calculate, client, request_and_prices

from research.automatic_protocol import HYPOTHESIS_VERSION
from research.market_series import freeze_series
from research.portfolio import fingerprint
from services import research_portfolio
from services import scanner_research_service as service


def frozen_stock(value):
    if value["portfolio"].get("automatic_research", {}).get("version") == "automatic-hypotheses-v5":
        from test_joint_stock_market import freeze_context

        return freeze_context(value)
    days = value["snapshot"]["sessions"]
    first = date.fromisoformat(days[0])
    before = []
    cursor = first - timedelta(days=1)
    while len(before) < 100:
        if cursor.weekday() < 5:
            before.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    required = list(reversed(before)) + days
    calendar = {
        "sessions": required,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in required},
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }
    series = {}
    for symbol in {signal["symbol"] for signal in value["signals"]}:
        bars = {
            day: {
                "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
            }
            for day in required
        }
        frozen = freeze_series(
            {"symbol": symbol, "exchange": "NSE", "interval": "D", "role": "feature_warmup"},
            required,
            calendar,
            bars,
        )
        series[symbol] = {"evidence": frozen, "calendar": calendar, "acquisition_receipts": []}
    payload = {"version": "research-stock-features-v1", "series": series}
    if value["portfolio"].get("automatic_research", {}).get("version") == "automatic-hypotheses-v4":
        from research.stock_relative_strength import BENCHMARK_DESCRIPTOR, VERSION

        prices = {
            day: {
                "timestamp": int(datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()),
                "open": 20000 - i * 10,
                "high": 20005 - i * 10,
                "low": 19995 - i * 10,
                "close": 20000 - i * 10,
            }
            for i, day in enumerate(required)
        }
        payload = {
            "version": VERSION,
            "series": series,
            "benchmark": {
                "evidence": freeze_series(BENCHMARK_DESCRIPTOR, required, calendar, prices),
                "calendar": calendar,
                "acquisition_receipts": [],
            },
        }
    return {**value, "automatic_stock_features": {**payload, "id": fingerprint(payload)}}


@pytest.mark.timeout(600)
@pytest.mark.parametrize(
    "holding, version",
    [
        (1, HYPOTHESIS_VERSION),
        (45, HYPOTHESIS_VERSION),
        (1, "automatic-hypotheses-v3"),
        (45, "automatic-hypotheses-v3"),
        (1, "automatic-hypotheses-v4"),
        (45, "automatic-hypotheses-v4"),
        (1, "automatic-hypotheses-v5"),
    ],
)
def test_smart_native_pause_inspect_resume_and_stock_trial_replay(
    app, client, monkeypatch, holding, version
):
    request = request_and_prices(client, monkeypatch)
    request["automatic_research"]["version"] = version
    joint = version == "automatic-hypotheses-v5"
    context_key = "automatic_stock_market_context" if joint else "automatic_stock_features"
    relative = version == "automatic-hypotheses-v4"
    expanded = version in ("automatic-hypotheses-v3", "automatic-hypotheses-v4")
    proposals, limit = (
        (128, 320) if joint else (96, 240) if relative else (84, 220) if expanded else (60, 180)
    )
    request["strategies"][0]["config"]["hold_sessions"] = holding
    from services import research_stock_features

    preparations = []

    def prepared(store, owner, evidence, **kwargs):
        if evidence.get(context_key):
            return evidence
        preparations.append(1)
        prepared_evidence = frozen_stock(evidence)
        if relative:
            # Exercise the real durable wrapper; only its native price leaves
            # are controlled here. Resume must reuse its actual context phase.
            bundle = prepared_evidence["automatic_stock_features"]
            stock = {"version": "research-stock-features-v1", "series": bundle["series"]}
            prepared_evidence["automatic_stock_features"] = {**stock, "id": fingerprint(stock)}
        return prepared_evidence

    if joint:
        from services import research_stock_market_context

        monkeypatch.setattr(research_stock_market_context, "prepare_context", prepared)
    else:
        monkeypatch.setattr(research_stock_features, "prepare_context", prepared)
    benchmark_preparations = []
    if relative:
        from services import research_benchmarks

        def benchmark(store, owner, bounds, descriptor, **kwargs):
            benchmark_preparations.append(1)
            # Same frozen native calendar as the stock warmup, one shared index.
            stock_evidence = frozen_stock({**request_and_prices_evidence, "portfolio": request})
            return stock_evidence["automatic_stock_features"]["benchmark"]

        from test_automatic_protocol import evidence as sample_evidence

        request_and_prices_evidence = sample_evidence(
            config={"hold_sessions": 1, "target_pct": 4, "stop_pct": 2}
        )
        monkeypatch.setattr(research_benchmarks, "prepare", benchmark)
    preview = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert preview.status_code == 200, preview.json
    assert preview.json["portfolio"]["optimization"]["trials"] == proposals
    assert preview.json["automatic_plan"]["summary"]["condition_presets"] == (
        92 if joint else 23 if relative else 18 if expanded else 7
    )
    response = client.post(
        "/scanner-research/api/portfolio/jobs",
        json={"portfolio": request, "request_id": "smart-native"},
    )
    assert response.status_code == 202, response.json
    job_id = response.json["id"]
    store = app.extensions["research_store"]
    from research.connectors import vectorbt_portfolio

    actual = vectorbt_portfolio.evaluate
    calls = []

    def pause(*args, **kwargs):
        calls.append(1)
        if len(calls) == 8:
            receipt = client.post(f"/scanner-research/api/jobs/{job_id}/pause")
            assert receipt.status_code == 200, receipt.json
        return actual(*args, **kwargs)

    monkeypatch.setattr(vectorbt_portfolio, "evaluate", pause)
    calculate(store, "smart-test")
    stopped = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert stopped["status"] == "paused", stopped
    partial = client.get(f"/scanner-research/api/portfolio/jobs/{job_id}/partial-study")
    assert partial.status_code == 200 and partial.json["available"], partial.json
    assert len(calls) == 8
    assert client.post(f"/scanner-research/api/jobs/{job_id}/resume").status_code == 200
    calculate(store, "smart-test")
    completed = client.get(f"/scanner-research/api/jobs/{job_id}").json
    assert completed["status"] == "completed", completed
    result = completed["result"]
    assert result["automatic_research"]["counts"]["proposals"] == proposals
    assert result["automatic_research"]["counts"]["simulations"] == len(calls) <= limit
    assert len(preparations) == 1
    assert len(benchmark_preparations) == (1 if relative else 0)
    from services import research_study_activity

    observed = research_study_activity.read(store, "research-test", job_id, limit=100)
    finished = [row for row in observed["rows"] if row["state"] in ("evaluated", "reused")]
    assert finished and all(row["checkpointed"] for row in finished)
    assert any(isinstance(row["params"].get("hypothesis"), str) for row in finished)
    assert result["automatic_research"]["selected_is_baseline"]
    if holding == 45:
        assert result["automatic_research"]["recipe"]["validation_mode"] == "exploratory_only"
        assert result["automatic_research"]["status"] == "inconclusive"
        assert result["automatic_research"]["final"] is None
        assert "validation" not in result
        assert (
            client.post(
                f"/scanner-research/api/portfolio/jobs/{job_id}/rerun",
                json={"period": "evaluation", "request_id": "no-fake-final"},
            ).status_code
            == 400
        )
    original = client.get(f"/scanner-research/api/jobs/{job_id}/export").data
    monkeypatch.setattr(
        research_portfolio,
        "_prepare_prices",
        lambda *a, **kw: pytest.fail("Replay cannot acquire execution prices"),
    )

    def retained(store, owner, evidence, **kw):
        assert evidence.get(context_key)
        return evidence

    monkeypatch.setattr(
        research_stock_market_context if joint else research_stock_features,
        "prepare_context",
        retained,
    )
    trial = next(
        row
        for row in result["experiment"]["rows"]
        if row["strategies"][0].get("entry_condition")
        and (
            not expanded
            or row["hypothesis"]["family"] == ("relative_strength" if relative else "rsi")
        )
    )
    replay = client.post(
        f"/scanner-research/api/portfolio/jobs/{job_id}/rerun",
        json={
            "period": "selection",
            "trial_id": trial["config_id"],
            "request_id": "smart-stock-replay",
        },
    )
    assert replay.status_code == 202, replay.json
    calculate(store, "smart-replay")
    tested = client.get(f"/scanner-research/api/jobs/{replay.json['id']}").json
    assert tested["status"] == "completed", tested
    assert tested["result"]["summary"] == trial["summary"]
    assert tested["result"]["strategies"] == trial["strategies"]
    from services.research_setup_reuse import retained_entry_conditions

    bundle = service.read_artifact(
        store, service.get_job(store, "research-test", replay.json["id"]).result_artifact
    )
    assert retained_entry_conditions(store, bundle) == tested["result"]["entry_conditions"]
    if holding == 1:
        from services import research_candidates

        candidate = research_candidates.prepare(store, "research-test", job_id, trial["config_id"])
        assert candidate["status"] == "queued", candidate
        calculate(store, "smart-candidate")
        candidate_job = client.get(f"/scanner-research/api/jobs/{candidate['report_job_id']}").json
        assert candidate_job["status"] == "completed", candidate_job
        assert candidate_job["result"]["candidate_report"]["verification"]["basis"] == "matched"
        assert candidate_job["result"]["summary"] == trial["summary"]
        from services import research_chosen_setups as chosen
        from services import research_decisions as decisions
        from services import research_library as library

        experiment = library.from_job(
            store,
            "research-test",
            {"job_id": replay.json["id"], "mode": "backtest", "request_id": "smart-library"},
        )
        kept = decisions.save_direct_decision(
            store,
            "research-test",
            experiment["id"],
            replay.json["id"],
            {
                "revision": 0,
                "state": "keep",
                "reason": "Exploratory stock rule",
                "request_id": "smart-keep",
            },
        )
        choice = chosen.choose(
            store,
            "research-test",
            experiment["id"],
            {
                "revision": experiment["revision"],
                "decision_id": kept["decision"]["id"],
                "event_id": kept["event"]["id"],
                "name": "Stock trend",
                "request_id": "smart-choice",
            },
        )
        assert choice["choice"]["entry_conditions"] == tested["result"]["entry_conditions"]
        preview = chosen.preview_use(
            store, "research-test", experiment["id"], {"choice_id": choice["choice"]["id"]}
        )
        assert (
            preview["draft"]["portfolio"]["entry_conditions"]
            == tested["result"]["entry_conditions"]
        )
        assert (
            preview["draft"]["portfolio"]["strategies"][0]["entry_condition"]
            == trial["strategies"][0]["entry_condition"]
        )
    assert tested["result"]["entry_conditions"]["hypothesis"] == trial["hypothesis"]
    assert (
        tested["result"]["automatic_stock_condition"]["bundle_id"]
        == trial["automatic_stock_condition"]["bundle_id"]
    )
    assert client.get(f"/scanner-research/api/jobs/{job_id}/export").data == original


def test_smart_preflight_cannot_download_or_hide_unsupported_engine(client, monkeypatch):
    request = request_and_prices(client, monkeypatch)
    request["automatic_research"]["version"] = HYPOTHESIS_VERSION
    from services import research_stock_features

    monkeypatch.setattr(
        research_stock_features,
        "prepare_context",
        lambda *a, **kw: pytest.fail("Preview cannot acquire features"),
    )
    before = deepcopy(request)
    response = client.post("/scanner-research/api/portfolio/preflight", json=request)
    assert response.status_code == 200, response.json
    assert request == before
    assert any(
        row["id"] == "liquidity" and row["status"] == "unsupported"
        for row in response.json["automatic_plan"]["families"]
    )
    request["engine"] = "nautilus"
    assert client.post("/scanner-research/api/portfolio/preflight", json=request).status_code == 400
