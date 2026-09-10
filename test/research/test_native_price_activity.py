"""Durable native price segments and counters from verified unique candles."""

import copy
from datetime import datetime

import pytest
from test_native_price_acquisition import DAYS, calendar, candle, plan

from services.research_acquisition import IST
from services.research_native_prices import NativePriceNoProgress, acquire_native_prices


class NativeArchive:
    """A controlled archive boundary; never opens the user's data or broker."""

    def __init__(self, tmp_path, interval="D", symbols=("AAA", "BBB")):
        self.signals = [
            {"symbol": symbol, "date": DAYS[0], "row": index + 2}
            for index, symbol in enumerate(symbols)
        ]
        self.plan = plan(interval)
        self.field = "required_dates" if interval == "D" else "required_timestamps"
        self.slots = self.plan[self.field]["AAA"]
        self.plan[self.field] = {symbol: list(self.slots) for symbol in symbols}
        self.rows = {symbol: [] for symbol in symbols}
        self.responses = {symbol: [candle(slot) for slot in self.slots] for symbol in symbols}
        self.calls = []
        self.reads = []
        self.events = []
        self.saved = []
        self.now = 0
        self.after_read = self.after_history = self.after_write = lambda *_: None
        self.cancelled = False
        self.tmp_path = tmp_path

    def reader(self, symbol, first, last):
        self.reads.append(symbol)
        self.after_read(symbol)
        result = []
        for row in self.rows[symbol]:
            stamp = datetime.fromtimestamp(row["timestamp"], IST).isoformat()
            slot = stamp[:10] if self.plan["interval"] == "D" else stamp
            if first <= slot <= last:
                result.append(copy.deepcopy(row))
        return result

    def writer(self, symbol, rows):
        self.rows[symbol].extend(copy.deepcopy(rows))
        self.after_write(symbol)

    def history(self, **request):
        self.calls.append(request["symbol"])
        self.after_history(request["symbol"])
        return True, {"data": self.responses[request["symbol"]]}, 200

    def run(self, **kwargs):
        options = {
            "reader": self.reader,
            "writer": self.writer,
            "credentials": lambda: {"broker": "controlled", "auth_token": "test-only"},
            "history": self.history,
            "archive_dir": self.tmp_path,
            "clock": lambda: self.now,
            "activity": self.events.append,
            "checkpoint": lambda value: self.saved.append(copy.deepcopy(value)),
            "cancelled": lambda: self.cancelled,
        }
        return acquire_native_prices(self.signals, self.plan, calendar(), **{**options, **kwargs})

    def advance(self, *_):
        self.now += 2


def prices(result):
    return result["acquisition_checkpoint"]["activity"]["prices"]


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_unique_required_cache_download_and_omission_counts(tmp_path, interval):
    native = NativeArchive(tmp_path, interval)
    native.signals.append({**native.signals[0], "row": 10})
    native.rows["AAA"] = [candle(native.slots[0])]
    native.responses["BBB"] = native.responses["BBB"][:-1]
    observed_before_verification = []
    native.after_write = lambda *_: observed_before_verification.append(
        native.events[-1]["prices"]["downloaded_candles"]
    )
    result = native.run()
    total = len(native.slots) * 2
    assert prices(result) == {
        "interval": interval,
        "total_symbols": 2,
        "checked_symbols": 2,
        "covered_symbols": 1,
        "required_candles": total,
        "cached_candles": 1,
        "downloaded_candles": total - 2,
        "available_candles": total - 1,
        "missing_candles": 1,
        "unavailable_candles": 1,
        "pending_windows": 0,
        "current_symbol": None,
        "cache_complete": True,
    }
    assert observed_before_verification == [0, len(native.slots) - 1]
    assert all(event["prices"]["required_candles"] == total for event in native.events)
    assert all(
        event["prices"]["cached_candles"] + event["prices"]["downloaded_candles"]
        == event["prices"]["available_candles"]
        for event in native.events
    )
    assert native.saved[-1]["activity"] == native.events[-1]


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_all_cached_completes_without_pretend_download(tmp_path, interval):
    native = NativeArchive(tmp_path, interval)
    native.rows = copy.deepcopy(native.responses)
    result = native.run(credentials=lambda: pytest.fail("No broker login needed"))
    assert native.calls == []
    assert "download" not in [event["stage"] for event in native.events]
    assert prices(result)["cached_candles"] == len(native.slots) * 2
    assert prices(result)["downloaded_candles"] == 0
    assert prices(result)["covered_symbols"] == 2
    assert result["acquisition_checkpoint"]["activity"]["stage"] == "verify"


def test_elapsed_cache_check_is_saved_and_not_repeated(tmp_path):
    native = NativeArchive(tmp_path)
    native.after_read = native.advance
    first = native.run(max_seconds=1)
    assert first["provenance"]["batch_pending"] is True
    assert first["acquisition_checkpoint"]["continuation_reason"] == "time_budget"
    assert prices(first)["checked_symbols"] == 1
    assert prices(first)["cache_complete"] is False
    assert native.reads == ["AAA"] and native.calls == []
    native.now = 0
    native.after_read = lambda *_: None
    second = native.run(prior=first["acquisition_checkpoint"], max_seconds=1)
    assert second["provenance"]["batch_pending"] is False
    assert native.reads == ["AAA", "BBB", "AAA", "BBB"]  # cache once, then readback
    assert native.calls == ["AAA", "BBB"]


@pytest.mark.parametrize("interval", ["D", "1m"])
@pytest.mark.parametrize("boundary", ["history", "write", "readback"])
def test_deadline_finishes_received_candles_before_continuation(tmp_path, interval, boundary):
    native = NativeArchive(tmp_path, interval)
    if boundary == "history":
        native.after_history = native.advance
    elif boundary == "write":
        native.after_write = native.advance
    else:
        native.after_read = lambda symbol: native.advance() if native.calls else None
    first = native.run(max_seconds=1)
    state = first["acquisition_checkpoint"]
    assert state["batch_pending"] is True
    assert state["completed_windows"] == [["AAA", native.slots[0], native.slots[-1]]]
    assert prices(first)["downloaded_candles"] == len(native.slots)
    assert prices(first)["covered_symbols"] == 1
    native.now = 0
    second = native.run(prior=state, max_seconds=1)
    assert native.calls == ["AAA", "BBB"]
    assert second["provenance"]["batch_pending"] is False
    assert prices(second)["downloaded_candles"] == len(native.slots) * 2
    assert prices(second)["cached_candles"] == 0


@pytest.mark.parametrize("unavailable", [False, True])
def test_elapsed_completed_empty_response_never_downloaded_again(tmp_path, unavailable):
    native = NativeArchive(tmp_path)
    original = native.history

    def response(**request):
        if request["symbol"] == "AAA":
            native.calls.append("AAA")
            native.advance()
            if unavailable:
                return False, {"error_code": "symbol_unavailable"}, 400
            return True, {"data": []}, 200
        return original(**request)

    first = native.run(history=response, max_seconds=1)
    assert prices(first)["unavailable_candles"] == len(native.slots)
    assert prices(first)["covered_symbols"] == 0
    assert first["provenance"]["batch_pending"] is True
    native.now = 0
    second = native.run(history=response, prior=first["acquisition_checkpoint"], max_seconds=1)
    assert native.calls == ["AAA", "BBB"]
    assert second["provenance"]["acquisition_status"] == "partial"
    assert prices(second)["unavailable_candles"] == len(native.slots)
    assert prices(second)["pending_windows"] == 0


def test_old_checkpoint_recovers_download_attribution_without_replaying(tmp_path):
    native = NativeArchive(tmp_path)
    native.rows["AAA"] = [candle(native.slots[0])]
    first = native.run(max_requests=1)
    legacy = copy.deepcopy(first["acquisition_checkpoint"])
    for field in ("source_counts", "activity", "cache_checked_windows"):
        legacy.pop(field)
    native.events.clear()
    second = native.run(prior=legacy)
    assert native.calls == ["AAA", "BBB"]
    assert prices(second)["cached_candles"] == 1
    assert prices(second)["downloaded_candles"] == len(native.slots) * 2 - 1
    assert native.events[0]["prices"]["downloaded_candles"] == len(native.slots) - 1
    assert second["acquisition_checkpoint"]["identity"] == legacy["identity"]


@pytest.mark.parametrize("boundary", ["cache", "history", "write", "readback"])
def test_external_timeout_never_becomes_automatic_continuation(tmp_path, boundary):
    native = NativeArchive(tmp_path)

    def failed(*_):
        raise TimeoutError("external timeout")

    if boundary == "cache":
        native.after_read = failed
    elif boundary == "history":
        native.after_history = failed
    elif boundary == "write":
        native.after_write = failed
    else:
        native.after_read = lambda *_: failed() if native.calls else None
    with pytest.raises(TimeoutError, match="external timeout"):
        native.run(max_seconds=1)
    assert native.saved[-1]["batch_pending"] is False
    assert native.saved[-1]["source_counts"]["AAA"]["downloaded"] == 0


def test_expired_auth_wins_over_elapsed_budget(tmp_path):
    native = NativeArchive(tmp_path)

    def expired(**_):
        native.advance()
        return False, {"message": "expired"}, 401

    result = native.run(history=expired, max_seconds=1)
    assert result["provenance"]["batch_pending"] is False
    assert result["provenance"]["hard_failures"][0]["kind"] == "auth_expired"
    assert prices(result)["unavailable_candles"] == 0


def test_too_small_budget_cannot_loop_without_durable_progress(tmp_path):
    native = NativeArchive(tmp_path, symbols=("AAA",))
    native.after_read = native.advance
    first = native.run(max_seconds=1)
    assert first["provenance"]["batch_pending"] is True
    native.now = 0

    def slow_credentials():
        native.advance()
        return {"broker": "controlled", "auth_token": "test-only"}

    with pytest.raises(NativePriceNoProgress, match="without saving progress"):
        native.run(
            prior=first["acquisition_checkpoint"], credentials=slow_credentials, max_seconds=1
        )
    assert native.saved[-1]["batch_pending"] is False
    assert native.calls == []


def test_cancellation_after_ingestion_preserves_stop_semantics(tmp_path):
    native = NativeArchive(tmp_path)
    native.after_write = lambda *_: setattr(native, "cancelled", True)
    with pytest.raises(InterruptedError):
        native.run()
    assert native.saved[-1]["batch_pending"] is False
    assert native.saved[-1]["receipts"]
    assert native.saved[-1]["source_counts"]["AAA"]["downloaded"] == 0


def test_real_failure_retry_rechecks_newly_populated_archive(tmp_path):
    native = NativeArchive(tmp_path)
    first = native.run(history=lambda **_: (False, {}, 401))
    native.rows = copy.deepcopy(native.responses)
    second = native.run(prior=first["acquisition_checkpoint"])
    assert native.calls == []
    assert prices(second)["cached_candles"] == len(native.slots) * 2
    assert second["provenance"]["acquisition_status"] == "complete"


def test_checkpoint_time_is_part_of_budget_and_continues_saved_cache(tmp_path):
    native = NativeArchive(tmp_path)

    def slow_checkpoint(state):
        native.saved.append(copy.deepcopy(state))
        native.advance()

    first = native.run(checkpoint=slow_checkpoint, max_seconds=1)
    assert first["provenance"]["batch_pending"] is True
    assert native.reads == ["AAA"]
    native.now = 0
    second = native.run(prior=first["acquisition_checkpoint"])
    assert second["provenance"]["batch_pending"] is False
    assert native.reads == ["AAA", "BBB", "AAA", "BBB"]


def test_activity_failure_retains_original_exception_and_recovery(tmp_path):
    native = NativeArchive(tmp_path)

    def interrupted(_):
        raise InterruptedError("worker ownership lost")

    with pytest.raises(InterruptedError, match="worker ownership lost"):
        native.run(activity=interrupted)
    assert native.saved[-1]["progress"]
    assert native.saved[-1]["batch_pending"] is False


def test_legacy_source_recovery_rejects_modified_receipt(tmp_path):
    native = NativeArchive(tmp_path)
    first = native.run(max_requests=1)
    legacy = copy.deepcopy(first["acquisition_checkpoint"])
    legacy.pop("source_counts")
    receipt = tmp_path / (legacy["receipts"][0]["sha256"] + ".json")
    receipt.write_text(receipt.read_text().replace('"broker":"controlled"', '"broker":null'))
    with pytest.raises(ValueError, match="receipt failed integrity"):
        native.run(prior=legacy)
