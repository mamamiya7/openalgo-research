"""Keep causal projections exact while avoiding discarded full-history copies."""

from copy import deepcopy

import pytest
from test_market_series import DAYS, calendar, candle, descriptor, evidence

from research import market_series


def test_projection_copies_selected_prices_once_without_discarded_history(monkeypatch):
    source = evidence()
    expected = deepcopy(source)
    expected["required_dates"] = DAYS[1:3]
    expected["bars"] = {day: deepcopy(source["bars"][day]) for day in DAYS[1:3]}
    expected["available_at"] = {day: source["available_at"][day] for day in DAYS[1:3]}
    expected["coverage"] = market_series._coverage(DAYS[1:3], expected["bars"])
    expected["id"] = market_series.fingerprint(
        {key: value for key, value in expected.items() if key != "id"}
    )
    copied = []
    original = deepcopy

    def capture(value):
        copied.append(value)
        return original(value)

    monkeypatch.setattr(market_series.copy, "deepcopy", capture)
    actual = market_series.slice_series(source, DAYS[1], DAYS[2])
    assert actual == expected
    assert all(value is not source and value is not source["bars"] for value in copied)
    assert actual["bars"][DAYS[1]] is not source["bars"][DAYS[1]]
    actual["provenance"]["observed_fields"].append("changed")
    actual["bars"][DAYS[1]]["close"] += 1
    assert source["provenance"]["observed_fields"] == list(market_series.FIELDS)
    assert source["bars"][DAYS[1]]["close"] != actual["bars"][DAYS[1]]["close"]


def test_freeze_reuses_closes_from_its_validated_plan(monkeypatch):
    observed = []
    original = market_series._close_at

    def close(day, hours):
        observed.append(day)
        return original(day, hours)

    monkeypatch.setattr(market_series, "_close_at", close)
    value = market_series.freeze_series(
        descriptor(), DAYS, calendar(), {day: candle(day) for day in DAYS}
    )
    assert observed == DAYS
    assert market_series.validate_series(value) is value


@pytest.mark.parametrize(
    "hours", [{"open": "15:30", "close": "09:15"}, {"open": "bad", "close": "15:30"}, {}]
)
def test_planning_still_rejects_invalid_hours(hours):
    current = calendar()
    current["session_hours"][DAYS[1]] = hours
    with pytest.raises(ValueError, match="opening and closing"):
        market_series._planned_series(descriptor(), DAYS, current)


def test_retained_context_is_fully_admitted_once_without_unused_recovery_binding(monkeypatch):
    from test_stock_market_context import bundle

    from research.stock_market_context import KEY
    from services import research_stock_features, research_stock_market_context

    context, execution = bundle(symbols=("AAA", "BBB"))
    incoming = {**execution, KEY: context}
    original = research_stock_market_context._check_execution
    admitted = []

    def checked(*args, **kwargs):
        admitted.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(research_stock_market_context, "_check_execution", checked)
    monkeypatch.setattr(
        research_stock_features,
        "_identity",
        lambda *_: pytest.fail("Retained inputs do not need a recovery binding"),
    )

    def run(value):
        return research_stock_market_context.prepare_context(
            object(),
            "alice",
            value,
            saved={"phase": "calculation", "calculation": None},
            checkpoint=lambda *_: pytest.fail("No new acquisition"),
            progress=lambda *_: None,
            cancelled=lambda: None,
        )

    assert run(incoming) is incoming
    assert len(admitted) == 1
    changed = deepcopy(incoming)
    changed[KEY]["series"]["AAA"]["evidence"]["bars"].clear()
    with pytest.raises(ValueError):
        run(changed)
    assert len(admitted) == 2
