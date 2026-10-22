"""Automatic reporting reuses frozen context and cannot select financial settings."""

from copy import deepcopy

import pytest
from test_market_conditions import report, trade
from test_stock_market_context import bundle, refreeze, resign

from research.market_suitability import VERSION, build_suitability, enrich


def saved_report():
    context, _ = bundle()
    market = context["market"]["evidence"]
    development = report(market, 160, 189, [trade(market, day) for day in range(160, 185)])
    development["automatic_research"] = {
        "version": "automatic-hypotheses-v16",
        "status": "not_supported",
        "selected_config_id": "original",
        "selected_is_baseline": True,
        "recipe": {"frozen": True},
    }
    development["validation"] = {
        "result": report(market, 190, 219, [trade(market, day) for day in range(190, 210)])
    }
    return development, context


def test_real_classifier_projects_two_periods_without_mutating_fills_or_frozen_inputs():
    value, context = saved_report()
    before = deepcopy((value, context))
    result = build_suitability(value, context)
    assert result["version"] == VERSION
    assert result["status"] in ("available", "partial")
    assert result["development"]["coverage"]["closed_trades"] == 25
    assert result["final"]["coverage"]["closed_trades"] == 20
    assert result["development"]["dates"]["to"] < result["final"]["dates"]["from"]
    rows = result["development"]["cohorts"]
    trend = next(row for row in rows if row["dimension"] == "trend")
    assert trend["entry_sessions"] == 25
    assert trend["average_net_return_pct"] == pytest.approx(2)
    assert trend["evidence"] == "limited"  # 25 trades do not meet existing 30-trade support.
    assert result["development"]["finding"]["status"] == "insufficient"
    assert "timeline" not in result["development"]
    assert "trade_assignments" not in result["development"]
    assert (value, context) == before


def test_later_market_changes_and_final_losses_cannot_change_development_attribution():
    value, context = saved_report()
    before = build_suitability(value, context)
    altered_value, altered_context = deepcopy(value), deepcopy(context)
    days = altered_context["market"]["calendar"]["sessions"]
    for day in days[190:]:
        bar = altered_context["market"]["evidence"]["bars"][day]
        for field in ("open", "high", "low", "close"):
            bar[field] *= 0.25
    refreeze(altered_context["market"])
    resign(altered_context)
    for row in altered_value["validation"]["result"]["ledger"]:
        row["pnl"] = -500
    after = build_suitability(altered_value, altered_context)
    assert after["development"] == before["development"]
    assert after["final"] != before["final"]
    assert altered_value["automatic_research"] == value["automatic_research"]


def test_enrichment_adds_only_optional_display_data_and_preserves_original_choice():
    value, context = saved_report()
    before = deepcopy(value)
    enrich(value, {"automatic_stock_market_context": context})
    assert value["automatic_research"]["market_suitability"]["version"] == VERSION
    saved = deepcopy(value)
    enrich(value, {"automatic_stock_market_context": context})
    assert value == saved
    value["automatic_research"].pop("market_suitability")
    assert value == before


def test_sparse_repeated_entries_are_not_independent_dates_or_supported_conditions():
    value, context = saved_report()
    market = context["market"]["evidence"]
    value["ledger"] = [trade(market, 160, symbol=f"STOCK{n}") for n in range(40)]
    result = build_suitability(value, context)["development"]
    assert result["coverage"]["closed_trades"] == 40
    assert all(row["entry_sessions"] == 1 for row in result["cohorts"])
    assert all(row["evidence"] == "limited" for row in result["cohorts"])
    assert result["finding"]["status"] == "insufficient"


def test_absent_or_altered_context_does_not_request_replacement_data():
    value, context = saved_report()
    assert build_suitability(value, None)["status"] == "unavailable"
    context["id"] = "altered"
    result = build_suitability(value, context)
    assert result["status"] == "unavailable"
    assert "could not be verified" in result["reason"]
    assert "development" not in result


def test_unknown_history_keeps_unclassified_counts_and_no_preferred_condition():
    value, context = saved_report()
    market = context["market"]["evidence"]
    value = report(market, 1, 15, [trade(market, 2)])
    result = build_suitability(value, context)
    assert result["status"] == "unavailable"
    assert result["development"]["coverage"]["unclassified_trades"] == 1
    assert result["development"]["finding"]["status"] == "insufficient"
    assert all(row["regime"] == "unknown" for row in result["development"]["cohorts"])


def test_missing_final_report_preserves_development_and_reports_partial_coverage():
    value, context = saved_report()
    value["validation"] = None
    result = build_suitability(value, context)
    assert result["status"] == "partial"
    assert "development" in result and "final" not in result
    assert "one of the saved periods" in result["reason"]


def test_unavailable_development_marks_do_not_promote_the_final_finding():
    value, context = saved_report()
    value["equity_curve"] = []
    before = deepcopy(value["automatic_research"])
    enrich(value, {"automatic_stock_market_context": context})
    result = value["automatic_research"].pop("market_suitability")
    assert result["status"] == "partial"
    assert "development" not in result and "final" in result
    assert value["automatic_research"] == before


@pytest.mark.parametrize("version", [None, "automatic-hypotheses-v8", "automatic-hypotheses-v15"])
def test_older_saved_numerical_versions_remain_exact(version):
    value = {"automatic_research": {"version": version, "selected_config_id": "saved"}}
    before = deepcopy(value)
    enrich(value, {})
    assert value == before
