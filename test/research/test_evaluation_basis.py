"""Like-for-like evidence admission before baseline/candidate comparison."""

# ruff: noqa: F811 -- shared isolated fixtures

import copy

import pytest
from test_jobs import app, client  # noqa: F401
from test_library import create, run
from test_portfolio_coverage import evidence as coverage_evidence
from test_portfolio_validation import inputs as validation_inputs
from test_report_period_workflow import draft, run_worker
from test_vectorbt_portfolio import DAYS, signal, snapshot, strategy

from research.connectors.vectorbt_portfolio import validate_config
from research.evaluation_basis import VERSION, build_evaluation_basis, comparison_status
from research.portfolio_coverage import prepare
from research.portfolio_validation import partition, period_plan


def complete(evidence):
    value = copy.deepcopy(evidence)
    value["portfolio"].setdefault("capital", 10000)
    value["versions"] = {
        "engine": "vectorbt",
        "engine_version": "0.28.5",
        "adapter_version": "vectorbt-portfolio-adapter-v1",
        "policy_version": "vectorbt-joint-causal-bars-v1",
        "portfolio_version": "research-portfolio-v1",
    }
    for row in value["strategies"]:
        row["config"] = validate_config(row["config"])
    return value


def basis(value, period="full"):
    return build_evaluation_basis(value, period=period)


def test_short_baseline_and_maximum_window_study_have_different_admission():
    rows = [strategy("a"), strategy("b", signals=[signal("BBB")])]
    prices = snapshot()
    prices["bars"]["BBB"].pop(DAYS[3])
    baseline = prepare(complete(coverage_evidence(rows, prices)))
    rows[1]["search"] = {"hold_sessions": {"min": 1, "max": 2, "step": 1}}
    study = prepare(complete(coverage_evidence(rows, prices, {"sampler": "tpe", "trials": 2})))
    baseline_basis, study_basis = basis(baseline), basis(study)
    assert baseline_basis["source_id"] == study_basis["source_id"]
    assert baseline_basis["observations_id"] == study_basis["observations_id"]
    assert baseline_basis["admission"] == {"eligible": 2, "excluded": 0, "pending": 0}
    assert study_basis["admission"] == {"eligible": 1, "excluded": 1, "pending": 0}
    assert comparison_status(baseline_basis, study_basis) == {
        "compatible": False,
        "differences": ["cohort"],
    }
    # Exact replay changes the structural request and holding configuration while
    # retaining the study's frozen eligibility and observations.
    replay = copy.deepcopy(study)
    replay["portfolio"].pop("optimization")
    replay["strategies"][1]["search"] = {}
    replay["strategies"][1]["config"]["hold_sessions"] = 1
    replay.update(frozen_prices=True, parent_result_artifact="different-parent")
    assert basis(replay) == study_basis


def test_reserved_boundary_mask_is_distinct_and_replay_does_not_reinstate_signals():
    baseline = validation_inputs()
    baseline["portfolio"].pop("optimization")
    baseline["strategies"][0]["search"] = {}
    study = copy.deepcopy(baseline)
    study["portfolio"]["optimization"] = {"sampler": "tpe", "trials": 2}
    study["strategies"][0]["search"] = {"hold_sessions": {"min": 1, "max": 3, "step": 1}}
    first = partition(baseline, prepare_period="selection")[0]
    second = partition(study, prepare_period="selection")[0]
    first_basis, second_basis = basis(first, "selection"), basis(second, "selection")
    assert first_basis["period"] == second_basis["period"]
    assert first_basis["admission"]["excluded"] < second_basis["admission"]["excluded"]
    assert "cohort" in comparison_status(first_basis, second_basis)["differences"]
    replay = copy.deepcopy(second)
    replay["portfolio"].pop("optimization")
    replay["portfolio"].pop("validation")
    replay["strategies"][0].update(search={})
    replay["strategies"][0]["config"]["hold_sessions"] = 1
    assert basis(replay, "selection") == second_basis


def test_exact_minute_observations_override_same_coverage_dates():
    prices = snapshot(minute=True, days=DAYS[:1])
    rows = [strategy("a", signals=[signal(timestamp="09:15")], trade_horizon="intraday")]
    full = prepare(complete(coverage_evidence(rows, prices)))
    trimmed = copy.deepcopy(full)
    trimmed["snapshot"]["timeline"] = prices["timeline"][1:]
    first, second = basis(full), basis(trimmed)
    assert full["snapshot"]["sessions"] == trimmed["snapshot"]["sessions"]
    assert first["period"]["from"] == "2026-01-05T09:15:00+05:30"
    assert second["period"]["from"] == "2026-01-05T09:16:00+05:30"
    assert second["period"]["observations"] == first["period"]["observations"] - 1
    assert "observations" in comparison_status(first, second)["differences"]
    assert not comparison_status(first, second)["compatible"]


def test_selection_identity_ignores_later_prices_sources_and_acquisition_metadata():
    original = validation_inputs()
    original["period_plan"] = period_plan(original)
    changed = copy.deepcopy(original)
    test_start = original["period_plan"]["evaluation"]["from"]
    for key, bar in changed["snapshot"]["bars"]["AAA"].items():
        if key >= test_start:
            bar.update(open=400, high=410, low=390, close=401)
    changed["strategies"][0].update(
        source_receipt={"original_csv_sha256": "different", "signals_sha256": "whole-file"},
        original_csv_sha256="other-parent-file",
        original_csv_base64="other-parent-bytes",
    )
    changed["snapshot"]["provenance"].update(
        download_brokers=["test-broker"],
        available_through="2026-01-15",
        quality_findings=[{"date": "2026-01-14", "reason": "later-only"}],
    )
    before, later, _ = partition(original)
    after, changed_later, _ = partition(changed)
    assert basis(before, "selection") == basis(after, "selection")
    assert (
        basis(later, "evaluation")["prices_id"] != basis(changed_later, "evaluation")["prices_id"]
    )
    assert basis(before, "selection")["evidence_id"] != basis(later, "evaluation")["evidence_id"]


def test_order_and_intraday_observation_identity_are_not_collapsed_to_date_symbol():
    rows = [strategy("a"), strategy("b", signals=[signal("AAA")])]
    original = complete(coverage_evidence(rows, snapshot()))
    other_order = copy.deepcopy(original)
    other_order["strategies"].reverse()
    assert basis(original)["source_id"] != basis(other_order)["source_id"]
    changed_time = copy.deepcopy(original)
    changed_time["strategies"][0]["signals"][0]["timestamp"] = "2026-01-05T09:15:00+05:30"
    assert basis(original)["source_id"] != basis(changed_time)["source_id"]


def test_exclusion_reason_wording_does_not_change_the_frozen_admission_mask():
    rows = [strategy("a"), strategy("b", signals=[signal("BBB")])]
    rows[1]["signals"][0]["research_exclusion"] = "Holding window crosses the later-period boundary"
    original = prepare(complete(coverage_evidence(rows, snapshot())))
    changed = copy.deepcopy(original)
    changed["strategies"][1]["signals"][0]["research_exclusion"] = (
        "Historical price is incompatible with the supplied tick size at an unused later date"
    )
    assert basis(original) == basis(changed)


def test_budget_candidate_parameters_and_fill_outcomes_do_not_change_evidence_identity():
    rows = [strategy("a")]
    rows[0]["search"] = {"hold_sessions": {"min": 1, "max": 2, "step": 1}}
    original = prepare(
        complete(coverage_evidence(rows, snapshot(), {"sampler": "tpe", "trials": 2}))
    )
    unchanged = copy.deepcopy(original)
    changed = copy.deepcopy(original)
    changed["portfolio"]["optimization"]["trials"] = 20
    changed["strategies"][0]["config"].update(
        target_pct=15, stop_pct=10, hold_sessions=2, order_size_pct=75
    )
    changed["strategies"][0].update(name="Renamed", allocation_pct=25)
    changed["ledger"] = [{"status": "skipped", "reason": "No available cash"}]
    changed["summary"] = {"closed_trades": 0}
    assert basis(original) == basis(changed)
    assert original == unchanged


@pytest.mark.parametrize("change", ["capital", "costs", "engine", "period"])
def test_account_and_execution_context_are_separate_from_equal_evidence(change):
    original = prepare(complete(coverage_evidence([strategy("a")], snapshot())))
    changed = copy.deepcopy(original)
    expected = change
    if change == "capital":
        changed["portfolio"]["capital"] = 20000
    elif change == "costs":
        changed["strategies"][0]["config"]["cost_bps"] = 20
    elif change == "engine":
        changed["versions"].update(engine="nautilus", policy_version="different-model")
        expected = "execution"
    first = basis(original, "selection")
    second = basis(changed, "evaluation" if change == "period" else "selection")
    assert first["evidence_id"] == second["evidence_id"]
    assert first["comparison"]["id"] != second["comparison"]["id"]
    assert comparison_status(first, second) == {"compatible": False, "differences": [expected]}
    assert comparison_status(first, first) == {"compatible": True, "differences": []}


def test_calendar_and_instrument_changes_are_distinct_even_with_equal_prices():
    original = prepare(complete(coverage_evidence([strategy("a")], snapshot())))
    changed = copy.deepcopy(original)
    changed["snapshot"]["session_hours"] = {DAYS[0]: {"open": "09:00", "close": "15:30"}}
    changed["snapshot"]["instruments"] = {"AAA": {"currency": "INR", "tick_size": "0.05"}}
    first, second = basis(original), basis(changed)
    assert first["prices_id"] == second["prices_id"]
    assert comparison_status(first, second) == {
        "compatible": False,
        "differences": ["calendar", "instruments"],
    }


@pytest.mark.parametrize("legacy", [None, {}, {"version": VERSION, "status": "unverified"}])
def test_legacy_missing_basis_cannot_become_a_same_sample_claim(legacy):
    current = basis(prepare(complete(coverage_evidence([strategy("a")], snapshot()))))
    assert comparison_status(legacy, current) == {
        "compatible": False,
        "differences": ["unverified"],
    }
    assert comparison_status(legacy, legacy)["compatible"] is False


def test_saved_study_and_exact_replays_keep_bases_for_each_evaluated_period(
    app, client, monkeypatch
):
    monkeypatch.setattr(
        "services.research_sources.resolve_broker_session",
        lambda *args: pytest.fail("evaluation metadata must not request broker access"),
    )

    def frozen_prices(_store, _owner, evidence, **_kwargs):
        return prepare(
            {
                **evidence,
                "snapshot": copy.deepcopy(validation_inputs()["snapshot"]),
                "frozen_prices": True,
            }
        )

    monkeypatch.setattr("services.research_portfolio._prepare_prices", frozen_prices)
    value = draft(client, True)
    value["portfolio"]["validation"]["mode"] = "evaluate"
    experiment = create(client, value)
    parent_id = run(client, experiment)["job"]["id"]
    store = app.extensions["research_store"]
    run_worker(store)
    path = f"/scanner-research/api/jobs/{parent_id}"
    parent = client.get(path).json["result"]
    selected = parent["evaluation_basis"]
    later = parent["validation"]["result"]["evaluation_basis"]
    assert selected["period"]["kind"] == "selection"
    assert later["period"]["kind"] == "evaluation"
    assert parent["report_context"]["evaluation_basis"] == selected
    assert parent["experiment"]["evaluation_basis_id"] == selected["evidence_id"]
    assert parent["validation"]["result"]["report_context"]["evaluation_basis"] == later
    assert not comparison_status(selected, later)["compatible"]
    original_export = client.get(path + "/export").data

    for period, expected in (("selection", selected), ("evaluation", later)):
        replay = client.post(
            f"/scanner-research/api/portfolio/jobs/{parent_id}/rerun",
            json={"period": period, "request_id": f"basis-exact-{period}"},
        )
        assert replay.status_code == 202, replay.json
        run_worker(store)
        result = client.get(f"/scanner-research/api/jobs/{replay.json['id']}").json["result"]
        assert result["evaluation_basis"] == expected
        assert comparison_status(result["evaluation_basis"], expected)["compatible"]
    assert client.get(path + "/export").data == original_export
