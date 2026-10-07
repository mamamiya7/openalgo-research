"""Real isolated Historify and unchanged native broker routing with owned resources."""

# ruff: noqa: F811 -- shared isolated native fixture
from copy import deepcopy

import pytest
from test_historify_scope import native  # noqa: F401
from test_market_series import DAYS, calendar, descriptor
from test_market_series import candle as price
from test_native_price_acquisition import SIGNALS, plan

from research.market_series import fingerprint, slice_series, validate_series
from services.research_historify import NativeHistorifyArchive
from services.research_market_series import acquire_series
from services.research_native_prices import MODE, POLICY, acquire_native_prices


def forbidden(*args, **kwargs):
    pytest.fail("No broker or credentials should be requested")


def candle(day, close=101):
    return {**price(day, close), "volume": 0, "oi": 0}


def kwargs(path):
    return {
        "archive_path": path,
        "receipts_dir": path.parent / "series-receipts",
        "credentials": lambda: {"broker": "controlled", "auth_token": "test-only"},
    }


def test_native_index_exchange_storage_readback_and_cache_reuse(native):
    database, path, stats = native
    asks = []

    def history(**request):
        assert stats["active"] == 0  # Never hold an archive connection during broker work.
        asks.append(request)
        return True, {"data": [candle(day) for day in DAYS]}, 200

    initial = acquire_series(descriptor(), DAYS, calendar(), **kwargs(path), history=history)
    assert len(asks) == 1
    assert asks[0]["symbol"] == "NIFTY" and asks[0]["exchange"] == "NSE_INDEX"
    assert asks[0]["interval"] == "D" and asks[0]["source"] == "api"
    assert validate_series(initial["evidence"])["coverage"]["status"] == "complete"
    assert all(row["receipt"]["exchange"] == "NSE_INDEX" for row in initial["acquisition_receipts"])
    assert all(
        row["receipt"]["descriptor"]["role"] == "benchmark"
        for row in initial["acquisition_receipts"]
    )
    assert NativeHistorifyArchive(path).read("NIFTY", DAYS[0], DAYS[-1]) == []
    assert stats["active"] == 0
    assert stats["initializations"] == 1
    repeat = acquire_series(
        descriptor(),
        DAYS,
        calendar(),
        **{**kwargs(path), "credentials": forbidden},
        history=forbidden,
    )
    assert repeat["evidence"] == initial["evidence"]
    assert repeat["checkpoint"]["source_counts"]["NIFTY"] == {"cached": 4, "downloaded": 0}
    assert stats["initializations"] == 1
    with database.get_connection() as db:
        assert db.execute(
            "SELECT exchange, interval, record_count FROM data_catalog"
        ).fetchall() == [("NSE_INDEX", "D", 4)]


def test_stock_warmup_uses_existing_nse_archive_without_index_alias(native):
    _, path, _ = native
    original = NativeHistorifyArchive(path)
    original.write("AAA", [candle(day) for day in DAYS])
    result = acquire_series(
        descriptor(symbol="AAA", exchange="NSE", role="feature_warmup"),
        DAYS,
        calendar(),
        **{**kwargs(path), "credentials": forbidden},
        history=forbidden,
    )
    assert len(result["evidence"]["bars"]) == 4
    assert result["checkpoint"]["source_counts"]["AAA"]["cached"] == 4


def test_acquired_lookback_slice_excludes_later_archive_revisions(native):
    _, path, _ = native
    original = acquire_series(
        descriptor(),
        DAYS,
        calendar(),
        **kwargs(path),
        history=lambda **_: (True, {"data": [candle(day) for day in DAYS]}, 200),
    )["evidence"]
    before = slice_series(original, DAYS[1], DAYS[2], warmup_sessions=1)
    NativeHistorifyArchive(path, exchange="NSE_INDEX").write("NIFTY", [candle(DAYS[3], 9999)])
    revised = acquire_series(
        descriptor(),
        DAYS,
        calendar(),
        **{**kwargs(path), "credentials": forbidden},
        history=forbidden,
    )["evidence"]
    assert revised["id"] != original["id"]
    assert slice_series(revised, DAYS[1], DAYS[2], warmup_sessions=1) == before
    assert DAYS[3] not in str(before)


def test_sparse_success_records_gap_and_resume_does_not_redownload_or_fill(native):
    _, path, _ = native
    initial = acquire_series(
        descriptor(),
        DAYS,
        calendar(),
        **kwargs(path),
        history=lambda **_: (True, {"data": [candle(DAYS[0]), candle(DAYS[-1])]}, 200),
    )
    assert initial["evidence"]["coverage"]["missing_dates"] == DAYS[1:-1]
    assert not initial["batch_pending"] and not initial["hard_failures"]
    repeat = acquire_series(
        descriptor(),
        DAYS,
        calendar(),
        **{**kwargs(path), "credentials": forbidden},
        prior=initial["checkpoint"],
        history=forbidden,
    )
    assert repeat["evidence"] == initial["evidence"]


def test_bounded_resume_reuses_committed_prices_after_mutable_archive_changes(native):
    _, path, stats = native
    days = ["2025-01-01", "2025-12-31"]
    calls, checkpoints = [], []

    def history(**request):
        calls.append((request["start_date"], request["end_date"]))
        return (
            True,
            {
                "data": [
                    candle(day)
                    for day in days
                    if request["start_date"] <= day <= request["end_date"]
                ]
            },
            200,
        )

    first = acquire_series(
        descriptor(),
        days,
        calendar(days),
        **kwargs(path),
        history=history,
        max_requests=1,
        checkpoint=lambda state: checkpoints.append(deepcopy(state)),
    )
    assert first["batch_pending"] and len(first["evidence"]["bars"]) == 1
    NativeHistorifyArchive(path, exchange="NSE_INDEX").write("NIFTY", [candle(days[0], 120)])
    resumed = acquire_series(
        descriptor(), days, calendar(days), **kwargs(path), prior=checkpoints[-1], history=history
    )
    assert not resumed["batch_pending"] and len(calls) == 2
    assert resumed["evidence"]["bars"][days[0]]["close"] == 101
    assert resumed["checkpoint"]["source_counts"]["NIFTY"] == {"cached": 0, "downloaded": 2}
    assert stats["active"] == 0


def test_recovery_binds_exchange_role_dates_and_calendar(native):
    _, path, _ = native
    first = acquire_series(
        descriptor(),
        DAYS,
        calendar(),
        **kwargs(path),
        history=lambda **_: (True, {"data": [candle(day) for day in DAYS]}, 200),
    )
    for desc, days, clock in [
        (descriptor(exchange="NSE", role="feature_warmup"), DAYS, calendar()),
        (descriptor(), DAYS[:-1], calendar()),
        (
            descriptor(),
            DAYS,
            {
                **calendar(),
                "session_hours": {day: {"open": "09:15", "close": "13:00"} for day in DAYS},
            },
        ),
    ]:
        with pytest.raises(ValueError, match="different immutable inputs"):
            acquire_series(
                desc,
                days,
                clock,
                **{**kwargs(path), "credentials": forbidden},
                prior=first["checkpoint"],
                history=forbidden,
            )


def test_failed_broker_request_has_truthful_gap_without_secret_error_text(native):
    _, path, stats = native
    result = acquire_series(
        descriptor(),
        DAYS,
        calendar(),
        **kwargs(path),
        history=lambda **_: (False, {"message": "expired token private-example"}, 401),
    )
    assert result["hard_failures"][0]["kind"] == "auth_expired"
    assert result["evidence"]["coverage"]["missing_dates"] == DAYS
    assert "private-example" not in str(result)
    assert stats["active"] == 0


def test_existing_nse_checkpoint_identity_and_mode_remain_exact(native):
    _, path, _ = native
    scope = NativeHistorifyArchive(path)
    scope.write("AAA", [candle(day) for day in DAYS[1:3]])
    original_plan = plan()
    clock = calendar(DAYS[:3])
    result = acquire_native_prices(
        SIGNALS,
        original_plan,
        clock,
        reader=scope.read,
        writer=scope.write,
        credentials=forbidden,
        archive_dir=path.parent / "legacy-receipts",
        history=forbidden,
    )
    saved = result["acquisition_checkpoint"]
    assert saved["mode"] == MODE
    assert saved["identity"] == fingerprint(
        {"signals": SIGNALS, "plan": original_plan, "calendar": clock, "policy": POLICY}
    )
    assert result["provenance"]["exchange"] == "NSE"
    assert "market_series" not in result
    assert all(
        set(row) == {"open", "high", "low", "close"} for row in result["bars"]["AAA"].values()
    )
