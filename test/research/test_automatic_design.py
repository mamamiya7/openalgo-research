"""Historical support checks reject sparse wins without rewarding cash use."""

from copy import deepcopy

import pytest

from research.automatic_design import assess, descriptor, reasons
from research.automatic_protocol import normalize_auto
from research.automatic_research import _support
from research.available_history_protocol import entry_bins

OPPORTUNITY_DATES = [f"2026-{month:02d}-{day:02d}" for month in (3, 4, 5) for day in range(1, 11)]
BINS = entry_bins(OPPORTUNITY_DATES)


def report(entries):
    return {
        "summary": {"closed_trades": len(entries), "net_return_pct": 5, "max_drawdown_pct": 1},
        "ledger": [
            {"status": "closed", "entry_date": day, "symbol": symbol, "pnl": pnl}
            for day, symbol, pnl in entries
        ],
        "equity_curve": [{"equity": 100000, "cash": 99000}],
    }


def balanced():
    return report(
        [
            (f"2026-{month:02d}-{day:02d}", f"S{day % 5}", 10)
            for month in (3, 4, 5)
            for day in range(1, 11)
        ]
    )


def test_march_cluster_that_passed_old_gate_is_rejected_automatically():
    value = report(
        [(f"2026-03-{day:02d}", f"S{day % 5}", 10) for day in range(1, 8) for _ in range(2)]
    )
    assert not _support(
        value["summary"],
        {"net_return_pct": -1, "max_drawdown_pct": 2, "closed_trades": 100},
        dates=7,
    )
    receipt = assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    assert not receipt["qualified"]
    assert len(receipt["reasons"]) >= 3
    assert receipt["active_bins"] == 1
    assert receipt["independently_validated"] is False


def test_diverse_historical_support_can_pass_with_little_cash_invested():
    receipt = assess(balanced(), BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    assert receipt["qualified"] and receipt["reasons"] == []
    assert receipt["capital_use"]["mean_invested_pct"] == 1
    assert receipt["capital_use"]["idle_cash_is_failure"] is False


def test_single_stock_cannot_supply_most_of_positive_profit():
    value = balanced()
    value["ledger"][0]["pnl"] = 1000
    receipt = assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    assert not receipt["qualified"]
    assert any("one stock" in reason for reason in receipt["reasons"])


def test_receipt_must_match_native_ledger_and_replay_identity():
    value = balanced()
    receipt = assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    row = {"summary": value["summary"], "entry_dates": 30, "automatic_design": receipt}
    assert reasons(row, descriptor()) == []
    changed = deepcopy(row)
    changed["automatic_design"]["qualified"] = False
    with pytest.raises(ValueError, match="changed"):
        reasons(changed, descriptor())
    value["summary"]["closed_trades"] += 1
    with pytest.raises(ValueError, match="disagree"):
        assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)


def test_minute_entries_use_recorded_entry_day_and_censored_tail_fails_closed():
    value = balanced()
    for item in value["ledger"]:
        item["entry_timestamp"] = item.pop("entry_date") + "T09:15:00+05:30"
    assert assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)["qualified"]
    value["ledger"][0]["entry_timestamp"] = "2026-10-20T09:15:00+05:30"
    with pytest.raises(ValueError, match="outside"):
        assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)


def test_new_policy_is_explicit_old_saved_recipes_keep_their_contract():
    old = {"version": "automatic-hypotheses-v16", "history_policy": "available_history"}
    assert normalize_auto(old) == old
    new = {**old, "design_policy": "automatic_design_v1"}
    assert normalize_auto(new) == new
    for raw in (
        {**old, "design_policy": "skip_checks"},
        {"version": old["version"], "design_policy": "automatic_design_v1"},
    ):
        with pytest.raises(ValueError, match="design policy"):
            normalize_auto(raw)


def test_no_signal_date_inside_a_bin_gap_cannot_be_used_as_an_entry():
    dates = [f"2026-{month:02d}-{day:02d}" for month in (3, 4, 5) for day in (1, 3, 5, 7, 9)]
    bins = entry_bins(dates)
    value = report([("2026-03-02", "AAA", 1)])
    assert bins[0]["from"] < "2026-03-02" < bins[0]["to"]
    with pytest.raises(ValueError, match="outside"):
        assess(value, bins, descriptor(), opportunity_dates=dates)


@pytest.mark.parametrize(
    "dates",
    [
        ["2026-03-01", "2026-03-01"],
        ["2026-03-02", "2026-03-01"],
        ["2026-03-99"],
        ["not-a-date"],
        [True],
        None,
    ],
)
def test_invalid_or_noncanonical_opportunity_calendar_fails_closed(dates):
    with pytest.raises(ValueError, match="calendar"):
        assess(balanced(), BINS, descriptor(), opportunity_dates=dates)


def test_bins_and_receipt_are_bound_to_the_exact_actual_opportunity_calendar():
    policy = descriptor()
    original = assess(balanced(), BINS, policy, opportunity_dates=OPPORTUNITY_DATES)
    bins = deepcopy(BINS)
    bins[0]["sessions"] += 1
    with pytest.raises(ValueError, match="calendar"):
        assess(balanced(), bins, policy, opportunity_dates=OPPORTUNITY_DATES)
    # Another no-fill opportunity changes identity even when every actual ledger
    # trade stays the same. The original fixed calendar must survive recovery.
    extra_dates = sorted([*OPPORTUNITY_DATES, "2026-05-11"])
    changed = assess(balanced(), entry_bins(extra_dates), policy, opportunity_dates=extra_dates)
    assert changed["calendar_id"] != original["calendar_id"]
    assert original["independently_validated"] is False
    assert "training admission only" in policy["scope"]


@pytest.mark.parametrize("count", [True, -1, 1.5, float("nan"), float("inf")])
def test_invalid_native_closed_trade_counts_fail_closed(count):
    value = balanced()
    value["summary"]["closed_trades"] = count
    with pytest.raises(ValueError, match="count"):
        assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)


@pytest.mark.parametrize("field", ["calendar_id", "reasons", "closed_trades", "entry_dates"])
def test_receipt_tampering_is_rejected(field):
    value = balanced()
    receipt = assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    row = {"summary": value["summary"], "entry_dates": 30, "automatic_design": receipt}
    row["automatic_design"][field] = None
    with pytest.raises(ValueError, match="changed"):
        reasons(row, descriptor())


def test_support_ledger_identity_detects_small_profit_change_even_if_ratios_stay_equal():
    value = balanced()
    for row in value["ledger"]:
        row["symbol"] = "ONE_STOCK"
    original = assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    value["ledger"][0]["pnl"] += 1
    changed = assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    assert (
        original["largest_symbol_positive_profit_fraction"]
        == changed["largest_symbol_positive_profit_fraction"]
        == 1
    )
    assert original["support_ledger_id"] != changed["support_ledger_id"]
    assert original["id"] != changed["id"]


def test_support_ledger_identity_preserves_native_order_and_ignores_unscored_rows():
    value = balanced()
    original = assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)
    value["ledger"].append({"status": "excluded", "reason": "Censored holding tail"})
    assert assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES) == original
    value["ledger"][0]["unused_display_note"] = "Display-only information"
    assert assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES) == original
    value["ledger"][0], value["ledger"][1] = value["ledger"][1], value["ledger"][0]
    assert (
        assess(value, BINS, descriptor(), opportunity_dates=OPPORTUNITY_DATES)["support_ledger_id"]
        != original["support_ledger_id"]
    )
