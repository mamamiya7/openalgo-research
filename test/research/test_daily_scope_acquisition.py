"""Daily preparation downloads only the dates needed by the requested holds."""

import copy
from datetime import date, timedelta

import pytest
from test_acquisition import candle, reference
from test_historify_first import options

from services.research_acquisition import acquire_history, acquisition_receipts, trading_date
from services.research_historify import native_historify_read

SIGNALS = [{"symbol": "AAA", "date": "2026-01-05", "row": 2}]


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    from database import historify_db

    path = tmp_path / "historify.duckdb"
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(path))
    return path


def no_download(**_):
    raise AssertionError("Complete required stored prices need no broker call")


def wider_reference(count=25):
    result = reference()
    sessions = []
    cursor = date(2026, 1, 5)
    while len(sessions) < count:
        if cursor.weekday() < 5:
            sessions.append(cursor.isoformat())
        cursor += timedelta(days=1)
    result["sessions"] = sessions
    bar = result["raw_bars"]["AAA"]["2026-01-05"]
    result["raw_bars"] = {"AAA": {day: dict(bar) for day in sessions}}
    result["bars"] = copy.deepcopy(result["raw_bars"])
    result["provenance"]["symbol_identities"]["AAA"] = {
        day: {"isin": "INE123A01016", "series": "EQ"} for day in sessions
    }
    return result


class MemoryArchive:
    def __init__(self):
        self.rows = {}
        self.reads = []
        self.downloads = []

    def read(self, symbol, first, last):
        self.reads.append((symbol, first, last))
        return [
            dict(row)
            for (own_symbol, day), row in sorted(self.rows.items())
            if own_symbol == symbol and first <= day <= last
        ]

    def write(self, symbol, rows):
        for row in rows:
            self.rows[symbol, trading_date(row["timestamp"])] = dict(row)

    def history(self, sessions, **request):
        self.downloads.append(request)
        return (
            True,
            {
                "data": [
                    candle(day)
                    for day in sessions
                    if request["start_date"] <= day <= request["end_date"]
                ]
            },
            200,
        )

    def arguments(self, tmp_path, evidence):
        return {
            "archive_dir": str(tmp_path / "receipts"),
            "reference_snapshot": evidence,
            "reader": self.read,
            "writer": self.write,
            "history": lambda **request: self.history(evidence["sessions"], **request),
            "credentials": lambda: {"broker": "dhan", "auth_token": "test-only"},
            "broker": None,
        }


def test_complete_required_native_cache_excludes_signal_date_and_needs_no_login(archive):
    opts = options(archive)
    opts["writer"]("AAA", [candle(day) for day in reference()["sessions"]])
    reads = []
    native_reader = opts["reader"]

    def read(symbol, first, last):
        reads.append((first, last))
        return native_reader(symbol, first, last)

    opts["reader"] = read
    required = {"AAA": ["2026-01-06", "2026-01-07"]}
    result = acquire_history(
        SIGNALS, required_dates=required, history=no_download, credentials=no_download, **opts
    )
    assert reads == [("2026-01-06", "2026-01-07")]
    assert sorted(result["raw_bars"]["AAA"]) == required["AAA"]
    assert result["required_dates"] == required
    assert result["coverage"]["symbols"][0]["missing_sessions"] == []
    assert result["provenance"]["acquisition_status"] == "complete"
    assert result["provenance"]["interval"] == "D"


def test_daily_hole_uses_broker_then_exact_native_readback(archive):
    opts = options(archive)
    opts["writer"]("AAA", [candle("2026-01-05"), candle("2026-01-07")])
    calls = []

    def history(**request):
        calls.append(request)
        return True, {"data": [candle("2026-01-06", 101.01)]}, 200

    result = acquire_history(
        SIGNALS,
        required_dates={"AAA": ["2026-01-06", "2026-01-07"]},
        credentials=lambda: {"broker": "zerodha", "auth_token": "test-only"},
        history=history,
        **opts,
    )
    assert [(c["interval"], c["start_date"], c["end_date"]) for c in calls] == [
        ("D", "2026-01-06", "2026-01-06")
    ]
    stored = native_historify_read("AAA", "2026-01-06", "2026-01-06", archive)
    assert result["raw_bars"]["AAA"]["2026-01-06"]["close"] == stored[0]["close"] == 101.01
    assert result["provenance"]["acquisition_status"] == "complete"


def test_unheld_gap_is_neither_read_nor_requested(tmp_path):
    evidence = wider_reference()
    required = {"AAA": [evidence["sessions"][1], evidence["sessions"][-1]]}
    cache = MemoryArchive()
    result = acquire_history(
        SIGNALS, required_dates=required, **cache.arguments(tmp_path, evidence)
    )
    expected = [(day, day) for day in required["AAA"]]
    assert [(c["start_date"], c["end_date"]) for c in cache.downloads] == expected
    assert all((first, last) in expected for _, first, last in cache.reads)
    assert sorted(result["raw_bars"]["AAA"]) == required["AAA"]
    assert result["coverage"]["symbols"][0]["missing_sessions"] == []


def test_partial_response_preserves_receipt_and_resume_only_requests_hole(tmp_path):
    evidence, cache = reference(), MemoryArchive()
    opts = cache.arguments(tmp_path, evidence)
    required = {"AAA": ["2026-01-06", "2026-01-07"]}
    normal_history = opts["history"]
    opts["history"] = lambda **_: (True, {"data": [candle("2026-01-06")]}, 200)
    updates = []
    first = acquire_history(
        SIGNALS, required_dates=required, progress=lambda n, d: updates.append(n / d), **opts
    )
    original_receipts = acquisition_receipts(first, opts["archive_dir"])
    assert first["provenance"]["acquisition_status"] == "partial"
    assert first["provenance"]["batch_pending"] is False
    assert first["acquisition_checkpoint"]["progress"]["covered_dates"] == 1
    assert 0 < updates[-1] < 1 and updates == sorted(updates)
    opts["history"] = normal_history
    second = acquire_history(
        SIGNALS,
        required_dates=required,
        prior=first["acquisition_checkpoint"],
        progress=lambda n, d: updates.append(n / d),
        **opts,
    )
    assert [(c["start_date"], c["end_date"]) for c in cache.downloads] == [
        ("2026-01-07", "2026-01-07")
    ]
    assert second["provenance"]["acquisition_status"] == "complete"
    assert updates == sorted(updates)
    assert (
        acquisition_receipts(second, opts["archive_dir"])[: len(original_receipts)]
        == original_receipts
    )


def test_required_unqualified_daily_price_stays_missing(tmp_path):
    evidence, cache = reference(), MemoryArchive()
    evidence["bars"]["AAA"].pop("2026-01-07")
    cache.write("AAA", [candle("2026-01-06")])
    result = acquire_history(
        SIGNALS,
        required_dates={"AAA": ["2026-01-06", "2026-01-07"]},
        **cache.arguments(tmp_path, evidence),
    )
    assert result["provenance"]["acquisition_status"] == "partial"
    assert result["provenance"]["pending_windows"] == [["AAA", "2026-01-07", "2026-01-07"]]
    assert result["coverage"]["symbols"][0]["missing_sessions"] == ["2026-01-07"]
    assert "2026-01-07" not in result["raw_bars"]["AAA"]


def test_scope_change_cannot_resume_different_immutable_inputs(tmp_path):
    evidence, cache = reference(), MemoryArchive()
    opts = cache.arguments(tmp_path, evidence)
    first = acquire_history(SIGNALS, required_dates={"AAA": ["2026-01-06"]}, **opts)
    with pytest.raises(ValueError, match="different immutable inputs"):
        acquire_history(
            SIGNALS,
            required_dates={"AAA": ["2026-01-06", "2026-01-07"]},
            prior=first["acquisition_checkpoint"],
            **opts,
        )


def test_more_than_500_windows_use_bounded_pass_and_resume_without_redownload(tmp_path):
    evidence, cache = wider_reference(1003), MemoryArchive()
    required = {"AAA": evidence["sessions"][1::2]}
    assert len(required["AAA"]) == 501
    opts = cache.arguments(tmp_path, evidence)
    first = acquire_history(SIGNALS, required_dates=required, **opts)
    assert len(cache.downloads) == 500
    assert first["provenance"]["batch_pending"] is True
    assert first["provenance"]["acquisition_status"] == "partial"
    first_dates = set(first["raw_bars"]["AAA"])
    assert len(first_dates) == 500
    second = acquire_history(
        SIGNALS, required_dates=required, prior=first["acquisition_checkpoint"], **opts
    )
    assert len(cache.downloads) == 501
    assert cache.downloads[-1]["start_date"] not in first_dates
    assert second["provenance"]["batch_pending"] is False
    assert second["provenance"]["acquisition_status"] == "complete"


@pytest.mark.parametrize("success", [False, True])
def test_failed_or_empty_response_does_not_automatically_repeat_at_pass_limit(tmp_path, success):
    evidence, cache = wider_reference(5), MemoryArchive()
    opts = cache.arguments(tmp_path, evidence)
    opts["history"] = lambda **_: (success, {"data": []}, 200 if success else 500)
    result = acquire_history(
        SIGNALS,
        required_dates={"AAA": [evidence["sessions"][1], evidence["sessions"][3]]},
        max_requests=1,
        **opts,
    )
    assert result["provenance"]["acquisition_status"] == "partial"
    assert result["provenance"]["batch_pending"] is False
    assert len(result["provenance"]["broker_receipts"]) == 1


def test_broker_extra_dates_do_not_expand_saved_price_scope(tmp_path):
    evidence, cache = reference(), MemoryArchive()
    opts = cache.arguments(tmp_path, evidence)
    opts["history"] = lambda **_: (
        True,
        {"data": [candle(day) for day in evidence["sessions"]]},
        200,
    )
    result = acquire_history(SIGNALS, required_dates={"AAA": ["2026-01-06"]}, **opts)
    assert sorted(result["raw_bars"]["AAA"]) == ["2026-01-06"]
    assert sorted(cache.rows) == [("AAA", "2026-01-06")]
    receipt = acquisition_receipts(result, opts["archive_dir"])[0]["receipt"]
    assert {item["date"] for item in receipt["rejected_observations"]} == {
        "2026-01-05",
        "2026-01-07",
    }


def test_no_eligible_next_session_needs_no_prices_or_login(tmp_path):
    evidence, cache = reference(), MemoryArchive()
    opts = cache.arguments(tmp_path, evidence)
    opts.update(history=no_download, credentials=no_download)
    result = acquire_history(
        [{"symbol": "AAA", "date": "2026-01-07", "row": 2}],
        required_dates={"AAA": []},
        **opts,
    )
    assert cache.reads == []
    assert result["provenance"]["acquisition_status"] == "complete"
    assert result["coverage"]["status"] == "ready"


@pytest.mark.parametrize(
    "required",
    [{}, {"AAA": ["2026-01-08"]}, {"AAA": ["2026-01-06", "2026-01-06"]}],
)
def test_invalid_daily_scope_is_rejected_before_any_read(tmp_path, required):
    cache = MemoryArchive()
    with pytest.raises(ValueError, match="Required daily dates"):
        acquire_history(SIGNALS, required_dates=required, **cache.arguments(tmp_path, reference()))
    assert cache.reads == []
