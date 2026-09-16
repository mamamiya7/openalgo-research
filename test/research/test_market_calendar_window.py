"""Auxiliary history extends warmup while preserving the existing trading calendar."""

from datetime import date

import pytest
from test_native_price_policy import native_window

from services.research_native_calendar import native_calendar_snapshot, native_calendar_window


def test_explicit_window_has_no_fake_signals_or_unrelated_tail():
    seen = []

    def reader(day, exchange):
        seen.append(day)
        return native_window(day, exchange)

    result = native_calendar_window(
        "2026-01-06", "2026-01-08", warmup_sessions=1, today=date(2026, 9, 12), window_reader=reader
    )
    assert result["sessions"] == ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    assert max(seen) == date(2026, 1, 8)
    assert result["provenance"]["warmup_sessions"] == 1


def test_indicators_can_request_a_year_of_prior_sessions_without_changing_legacy_limit():
    result = native_calendar_window(
        "2026-06-01",
        "2026-06-02",
        warmup_sessions=252,
        today=date(2026, 9, 12),
        window_reader=native_window,
    )
    assert result["provenance"]["warmup_sessions"] == 252
    assert len([day for day in result["sessions"] if day < "2026-06-01"]) == 252
    with pytest.raises(ValueError, match="warm-up"):
        native_calendar_snapshot(
            [{"date": "2026-06-01"}],
            warmup_sessions=252,
            today=date(2026, 9, 12),
            window_reader=native_window,
        )


@pytest.mark.parametrize("warmup", [-1, 253, True, 1.5])
def test_auxiliary_warmup_is_bounded_before_any_io(warmup):
    with pytest.raises(ValueError, match="warmup"):
        native_calendar_window(
            "2026-06-01",
            "2026-06-02",
            warmup_sessions=warmup,
            window_reader=lambda *a: pytest.fail("Must reject before reading"),
        )


def test_unverified_prior_year_is_not_invented_for_indicators():
    with pytest.raises(ValueError, match="252 earlier verified"):
        native_calendar_window(
            "2025-02-01",
            "2025-02-03",
            warmup_sessions=252,
            today=date(2026, 9, 12),
            window_reader=native_window,
        )
