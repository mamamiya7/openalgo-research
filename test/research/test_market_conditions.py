"""Saved-fill condition cohorts are descriptive attribution, not filtered portfolios."""

from copy import deepcopy

import pytest
from test_regimes import evidence, growth

from research.market_conditions import build_conditions
from services.research_regimes import enrich


def schedule(market):
    return {
        "sessions": market["required_dates"],
        "session_hours": {
            day: {"open": "09:15", "close": "15:30"} for day in market["required_dates"]
        },
        "provenance": {"exchange": "NSE", "calendar_basis": "openalgo-market-calendar-v1"},
    }


def report(market, first=160, last=180, ledger=None, *, interval="D"):
    return {
        "config": {"initial_capital": 10000},
        "summary": {"initial_capital": 10000, "final_equity": 10100, "net_return_pct": 1.0},
        "execution": {"engine": "vectorbt", "engine_version": "0.28.5", "interval": interval},
        "equity_curve": [
            {"date": day, "equity": 10000 + index, "cash": 9000}
            for index, day in enumerate(market["required_dates"][first : last + 1])
        ],
        "ledger": ledger or [],
    }


def trade(market, index, *, pnl=20.0, **changes):
    return {
        "status": "closed",
        "strategy_id": "a",
        "strategy_name": "A",
        "symbol": "TEST",
        "entry_date": market["required_dates"][index],
        "exit_date": market["required_dates"][index + 1],
        "entry_price": 100.0,
        "exit_price": 102.0,
        "quantity": 10,
        "pnl": pnl,
        "fees": 1.0,
        **changes,
    }


def cohort(result, dimension="trend", regime="up"):
    return next(
        row
        for row in result["cohorts"]
        if row["dimension"] == dimension and row["regime"] == regime
    )


def test_known_volatility_with_uncertain_trend_is_partial_not_fully_classified():
    from research.regimes import classify

    market = evidence(growth([0.003] * 180 + [-0.003] * 100))
    decisions = [day + "T09:15:00+05:30" for day in market["required_dates"][181:240]]
    rows = classify(market, decisions)["timeline"]
    uncertain = next(
        row
        for row in rows
        if row["status"] == "available"
        and row["trend"] == "unknown"
        and row["volatility"] != "unknown"
    )
    index = market["required_dates"].index(uncertain["decision_at"][:10])
    result = build_conditions(
        report(market, index, index, [trade(market, index)]), market, schedule(market)
    )
    assert result["status"] == "partial"
    assert result["coverage"]["classified_sessions"] == 0
    assert result["coverage"]["classified_trades"] == 0
    assert result["coverage"]["unclassified_trades"] == 1
    assert cohort(result, "trend", "unknown")["closed_trades"] == 1
    assert cohort(result, "volatility", uncertain["volatility"])["closed_trades"] == 1


@pytest.mark.timeout(180)
def test_saved_native_net_pnl_and_entry_notional_arithmetic_keep_original_ledger_indices():
    market = evidence(growth([0.003] * 220))
    ledger = [
        {"status": "skipped", "pnl": None, "reason": "Insufficient shared cash"},
        trade(
            market,
            160,
            pnl=18,
            entry_price=100,
            exit_price=110,
            quantity=2,
            fees=2,
            requested_budget=1000,
        ),
        {"status": "open", "pnl": None, "unrealized_pnl": 5000},
        trade(
            market,
            161,
            pnl=-5,
            entry_price=50,
            exit_price=46,
            quantity=1,
            fees=1,
            requested_budget=1000,
        ),
    ]
    original = report(market, ledger=ledger)
    before = deepcopy(original)
    result = build_conditions(original, market, schedule(market))
    up = cohort(result)
    assert up["closed_trades"] == up["entry_sessions"] == 2
    assert up["net_pnl"] == 18 - 5 == 13
    assert up["average_net_return_pct"] == pytest.approx(((18 / 200 * 100) + (-5 / 50 * 100)) / 2)
    assert up["average_net_return_pct"] == pytest.approx(-0.5)
    assert up["win_rate_pct"] == 50
    assert [item["ledger_index"] for item in result["trade_assignments"]] == [1, 3]
    assert result["coverage"]["closed_trades"] == 2
    assert original == before
    assert "not a filtered portfolio return" in " ".join(result["basis"])


def test_thirty_same_day_symbols_do_not_supply_twenty_independent_entry_dates():
    market = evidence(growth([0.003] * 220))
    ledger = [trade(market, 160, symbol=f"STOCK{i}") for i in range(30)]
    result = build_conditions(report(market, ledger=ledger), market, schedule(market))
    up = cohort(result)
    assert up["closed_trades"] == 30 and up["entry_sessions"] == 1
    assert up["evidence"] == "limited"
    assert result["finding"]["status"] == "insufficient"


def test_dates_with_unknown_entry_notional_cannot_satisfy_return_evidence_gate():
    market = evidence(growth([0.003] * 220))
    ledger = [trade(market, 160, symbol=f"STOCK{i}") for i in range(30)]
    ledger += [trade(market, index, quantity=0) for index in range(161, 180)]
    result = build_conditions(report(market, ledger=ledger), market, schedule(market))
    up = cohort(result)
    assert up["closed_trades"] == 49
    assert up["evidence"] == "limited"
    assert up["average_net_return_pct"] == 2
    assert result["finding"]["status"] == "insufficient"


def test_minute_entries_without_verified_in_session_instants_stay_unclassified():
    market = evidence(growth([0.003] * 220))
    day = market["required_dates"][160]
    ledger = [
        trade(market, 160, entry_timestamp=day + "T10:30:00+05:30"),
        trade(market, 160),  # A daily date alone is insufficient for a minute fill.
        trade(market, 160, entry_timestamp=day + "T08:59:00+05:30"),
        trade(market, 160, entry_timestamp=day + "T16:00:00+05:30"),
        trade(market, 160, entry_timestamp=day + "T10:30:00"),
        trade(market, 160, entry_date=None),
        trade(market, 160, entry_timestamp=market["required_dates"][161] + "T10:30:00+05:30"),
    ]
    result = build_conditions(
        report(market, ledger=ledger, interval="1m"), market, schedule(market)
    )
    assert result["coverage"]["classified_trades"] == 1
    assert result["coverage"]["unclassified_trades"] == 6
    assert result["status"] == "partial"
    assert [item["classified"] for item in result["trade_assignments"]] == [True] + [False] * 6
    assert cohort(result, regime="unknown")["closed_trades"] == 6


def two_regime_market():
    # The fixture has an established rising phase and then an established falling
    # phase. Classification uses the real native SMA/causal percentile foundation.
    return evidence(growth([0.003] * 209 + [-0.003] * 150))


def test_observed_condition_is_only_a_saved_sample_lead_with_unchanged_native_trade_values():
    market = two_regime_market()
    ledger = [trade(market, index, pnl=20) for index in range(170, 200)]
    ledger += [trade(market, index, pnl=-10) for index in range(270, 300)]
    original = report(market, 170, 300, ledger)
    before = deepcopy(original)
    result = build_conditions(original, market, schedule(market))
    up, down = cohort(result), cohort(result, regime="down")
    assert up["closed_trades"] == down["closed_trades"] == 30
    assert up["entry_sessions"] == down["entry_sessions"] == 30
    assert up["evidence"] == down["evidence"] == "descriptive"
    assert up["average_net_return_pct"] == 2
    assert down["average_net_return_pct"] == -1
    assert up["net_pnl"] == 600 and down["net_pnl"] == -300
    assert result["finding"]["status"] == "observed"
    assert "saved sample" in result["finding"]["text"]
    assert "separate filtered backtest" in result["finding"]["next_step"]
    assert "untouched later-period" in result["finding"]["next_step"]
    assert "removing signals changes shared cash" in " ".join(result["basis"]).lower()
    assert original == before


def test_positive_full_average_does_not_pass_when_second_chronological_half_loses():
    market = two_regime_market()
    ledger = [trade(market, index, pnl=50 if index < 185 else -10) for index in range(170, 200)]
    ledger += [trade(market, index, pnl=-20) for index in range(270, 300)]
    result = build_conditions(report(market, 170, 300, ledger), market, schedule(market))
    assert cohort(result)["average_net_return_pct"] == 2
    assert cohort(result)["evidence"] == "descriptive"
    assert result["finding"]["status"] == "insufficient"


def test_adequate_positive_cohort_needs_an_adequate_comparison_cohort_too():
    market = two_regime_market()
    ledger = [trade(market, index, pnl=20) for index in range(170, 200)]
    ledger += [trade(market, 270 + index % 19, pnl=-10) for index in range(30)]
    result = build_conditions(report(market, 170, 300, ledger), market, schedule(market))
    assert cohort(result)["evidence"] == "descriptive"
    assert cohort(result, regime="down")["closed_trades"] == 30
    assert cohort(result, regime="down")["entry_sessions"] == 19
    assert cohort(result, regime="down")["evidence"] == "limited"
    assert result["finding"]["status"] == "insufficient"


def test_earlier_report_and_identity_ignore_future_market_prices_and_later_period_outcomes():
    prices = growth([0.003] * 299)
    market = evidence(prices)
    earlier = report(market, 170, 189, [trade(market, 175, pnl=20)])
    original = deepcopy(earlier)
    expected = build_conditions(earlier, market, schedule(market))
    mutated = evidence(prices[:190] + [price * 0.3 for price in prices[190:]])
    later = report(mutated, 220, 239, [trade(mutated, 225, pnl=-9999)])
    earlier["validation"] = {"result": later}
    assert build_conditions(earlier, mutated, schedule(mutated)) == expected
    output = {
        "analysis": {"untouched": True},
        "validation": {"untouched": "later"},
        "summary": deepcopy(earlier["summary"]),
        "objective_winner_id": "same-winner",
    }
    saved = deepcopy(earlier)
    enrich(output, earlier, {"evidence": mutated, "calendar": schedule(mutated)})
    selected_context = output["analysis"]["market_conditions"]
    later_context = output["validation"]["market_conditions"]
    assert selected_context == expected
    assert selected_context["dates"] == {
        "from": market["required_dates"][170],
        "to": market["required_dates"][189],
    }
    assert later_context["dates"] == {
        "from": market["required_dates"][220],
        "to": market["required_dates"][239],
    }
    assert selected_context["id"] != later_context["id"]
    assert cohort(selected_context)["net_pnl"] == 20
    assert (
        sum(row["net_pnl"] for row in later_context["cohorts"] if row["dimension"] == "trend")
        == -9999
    )
    assert output["summary"] == original["summary"]
    assert output["objective_winner_id"] == "same-winner"
    assert output["analysis"]["untouched"] is True
    assert earlier == saved
