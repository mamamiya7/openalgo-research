"""Finite causal daily stock features applied to original scanner opportunities.

No acquisition, numerical runtime, global cache or trade-ledger filtering lives
here. Native account engines receive the complete original opportunity set with
explicit entry exclusions. Every feature window ends before its entry session.
"""

import json
import sys
from bisect import bisect_left
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from math import isclose, isfinite
from threading import get_ident

from research.canonical_json import (
    bounded_canonical_bytes,
    bounded_canonical_fragments,
    bounded_canonical_snapshot,
)
from research.gate_input_binding import copy_gate_signal, execution_inputs_id, freeze_gate_signals
from research.market_series import IST, _planned_series, fingerprint, validate_series
from research.portfolio import fingerprint as account_fingerprint

VERSION = "research-stock-features-v1"
ENTRY_VERSION = "research-stock-entry-conditions-v1"
WARMUP_SESSIONS = 100
MAX_SYMBOLS = 200
MAX_FEATURE_BARS = 200_000
MAX_SIGNALS = 25_000
MAX_BUNDLE_BYTES = 64 * 1024**2
MAX_RECEIPT_BYTES = 32 * 1024**2
MAX_PREPARED_FEATURES = 50_000
MAX_PREPARED_FEATURE_BYTES = 128 * 1024**2
MAX_PREPARED_WINDOWS = 4096
MAX_PREPARED_WINDOW_BYTES = 64 * 1024**2
MAX_PREPARED_RECORD_BUFFERS = MAX_SYMBOLS + 1
MAX_PREPARED_RECORD_BUFFER_BYTES = 64 * 1024**2
_observation_cache = ContextVar("prepared_stock_observations", default=None)


def _heap_bytes(value):
    """Conservatively charge every object in an admitted small plain tree."""
    from research.owned_entry_features import owned_json_bounds

    pending, seen, total = [value], set(), 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        bounds = owned_json_bounds(item)
        if bounds is not None:
            total += sys.getsizeof(bounds) + sum(sys.getsizeof(part) for part in bounds)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (tuple, list)):
            pending.extend(item)
    return total


def _record_observation_buffer(record):
    """Encode a private record's daily observations once, with bounded fallback.

    A gap remains a gap. A malformed/unusual future row declines this optional
    buffer so the original requested-window path keeps its error precedence.
    No indicator or return is calculated here.
    """
    series = record["evidence"]
    dates = tuple(record["calendar"]["sessions"])
    maximum = MAX_PREPARED_RECORD_BUFFER_BYTES
    if len(dates) > MAX_FEATURE_BARS or len(dates) * 256 > maximum:
        return None
    descriptor = bounded_canonical_bytes(
        series["descriptor"], max_bytes=maximum, json_types_only=True
    )
    if descriptor is None:
        return None
    members, available, closes, highs, lows = [], [], [], [], []
    allowance = len(descriptor) + 512
    try:
        for day in dates:
            bar = series["bars"].get(day)
            if bar is None:
                member = at = close = high = low = None
            else:
                member = bounded_canonical_bytes(
                    {"date": day, "bar": bar, "available_at": series["available_at"][day]},
                    max_bytes=maximum - allowance,
                    json_types_only=True,
                )
                if member is None:
                    return None
                at = datetime.fromisoformat(series["available_at"][day])
                close, high, low = (bar[field] for field in ("close", "high", "low"))
            # Include tuple/list slots, date/numeric references and the parsed
            # datetime's separately allocated timezone in the construction cap.
            allowance += 512 + (len(member) if member is not None else 0) + len(day) * 4
            if allowance > maximum:
                return None
            members.append(member)
            available.append(at)
            closes.append(close)
            highs.append(high)
            lows.append(low)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    result = {
        "dates": dates,
        "descriptor": descriptor,
        "members": tuple(members),
        "available": tuple(available),
        "closes": tuple(closes),
        "highs": tuple(highs),
        "lows": tuple(lows),
    }
    # _heap_bytes counts datetime objects; the timezone/timedelta payload is
    # additionally charged per timestamp rather than assuming shared offsets.
    charge = _heap_bytes(result) + 128 * sum(at is not None for at in available) + 256
    return (result, charge) if charge <= maximum else None


class _PreparedFeatureCache:
    """Bounded results for one exact detached and validated stock projection."""

    __slots__ = (
        "__bundle",
        "__entries",
        "__bytes",
        "__owner",
        "__providers",
        "__enabled",
        "__records",
        "__windows",
        "__window_bytes",
        "__buffers",
        "__buffer_bytes",
        "__signature",
    )

    def __init__(self, bundle):
        from research import stock_indicators, stock_relative_strength

        self.__bundle, self.__entries, self.__bytes = bundle, OrderedDict(), 0
        self.__owner = get_ident()
        self.__enabled = True
        self.__records = {id(record): record for record in bundle["series"].values()}
        if "benchmark" in bundle:
            self.__records[id(bundle["benchmark"])] = bundle["benchmark"]
        self.__windows, self.__window_bytes = OrderedDict(), 0
        self.__buffers, self.__buffer_bytes = OrderedDict(), 0
        self.__signature = None
        self.__providers = (
            _feature,
            stock_relative_strength._feature,
            stock_indicators._feature,
            stock_indicators._window,
            _record_observation_buffer,
        )

    def check(self):
        if self.__bundle is None or self.__owner != get_ident():
            raise ValueError("Prepared stock features require their synchronous live owner")

    def resolve(
        self, bundle, symbol, record, decision, hypothesis, feature_fn, version, *, _immutable=False
    ):
        from research import stock_indicators, stock_relative_strength

        self.check()
        providers = (
            _feature,
            stock_relative_strength._feature,
            stock_indicators._feature,
            stock_indicators._window,
            _record_observation_buffer,
        )
        if (
            not self.__enabled
            or bundle is not self.__bundle
            or record is not self.__bundle["series"].get(symbol)
            or providers != self.__providers
            or type(version) is not str
            or len(version) > 256
        ):
            return feature_fn(record, decision, hypothesis)
        from research.owned_entry_features import _capture, owned_json_bounds
        from research.prepared_calculation import _thaw

        bounds = owned_json_bounds(hypothesis)
        retained = self.__signature
        signature = retained[1] if retained is not None and retained[0] is hypothesis else None
        if signature is None:
            signature = bounded_canonical_bytes(
                hypothesis, max_bytes=4096, json_types_only=True, allow_native_float64=True
            )
            # One exact immutable rule at a time; its full node/ASCII allowance
            # bounds retained heap conservatively. Mutable rules always re-encode.
            if (
                signature is not None
                and bounds is not None
                and bounds[2] + 64 * bounds[0] <= 64 * 1024
            ):
                self.__signature = (hypothesis, signature)
        if signature is None:
            return feature_fn(record, decision, hypothesis)
        key = (symbol, decision.isoformat(), signature, version)
        previous = self.__entries.get(key)
        if previous is not None:
            self.__entries.move_to_end(key)
            copied = previous[0] if _immutable else _thaw(previous[0])
            return copied["feature"], copied["reason"]
        token = _observation_cache.set(self)
        try:
            feature, reason = feature_fn(record, decision, hypothesis)
        finally:
            _observation_cache.reset(token)
        captured = bounded_canonical_snapshot(
            {"feature": feature, "reason": reason},
            max_bytes=64 * 1024,
            allow_native_float64=True,
        )
        if captured is None:
            return feature, reason
        result, raw = captured
        del captured, raw
        try:
            result = _capture(result)
        except (TypeError, ValueError, RecursionError):
            pass  # Unusual accepted native scalars retain their ordinary detached path.
        charge = _heap_bytes(key) + _heap_bytes(result) + 256
        if charge <= MAX_PREPARED_FEATURE_BYTES:
            while self.__entries and (
                len(self.__entries) >= MAX_PREPARED_FEATURES
                or self.__bytes + charge > MAX_PREPARED_FEATURE_BYTES
            ):
                _, (_, removed) = self.__entries.popitem(last=False)
                self.__bytes -= removed
            if MAX_PREPARED_FEATURES > 0:
                self.__entries[key] = result, charge
                self.__bytes += charge
        copied = result if _immutable else _thaw(result)
        return copied["feature"], copied["reason"]

    def clear(self):
        self.__signature = None
        self.__entries.clear()
        self.__bytes = 0
        self.__windows.clear()
        self.__window_bytes = 0
        self.__buffers.clear()
        self.__buffer_bytes = 0

    def _record_buffer(self, record):
        identity = id(record)
        previous = self.__buffers.get(identity)
        if previous is not None:
            self.__buffers.move_to_end(identity)
            return previous[0]
        captured = _record_observation_buffer(record)
        value, charge = captured if captured is not None else (None, 256)
        while self.__buffers and (
            len(self.__buffers) >= MAX_PREPARED_RECORD_BUFFERS
            or self.__buffer_bytes + charge > MAX_PREPARED_RECORD_BUFFER_BYTES
        ):
            _, (_, removed) = self.__buffers.popitem(last=False)
            self.__buffer_bytes -= removed
        if MAX_PREPARED_RECORD_BUFFERS > 0 and charge <= MAX_PREPARED_RECORD_BUFFER_BYTES:
            self.__buffers[identity] = value, charge
            self.__buffer_bytes += charge
        return value

    def observation(self, record, decision, count):
        """Reuse only this synchronous owner's private validated records."""
        self.check()
        if not self.__enabled or self.__records.get(id(record)) is not record:
            return None
        key = (id(record), decision.isoformat(), count)
        previous = self.__windows.get(key)
        if previous is not None:
            self.__windows.move_to_end(key)
            return previous[0]
        series, calendar = record["evidence"], record["calendar"]
        day, dates = decision.date().isoformat(), calendar["sessions"]
        problem, required = None, ()
        buffered = None
        end = bisect_left(dates, day)
        if day not in calendar["session_hours"] or day not in dates:
            problem = ("calendar", None)
        elif end < count:
            problem = ("count", count)
        else:
            required = tuple(dates[end - count : end])
            buffered = self._record_buffer(record)
            for offset, prior in enumerate(required, end - count):
                if (
                    buffered["members"][offset] is None
                    if buffered is not None
                    else prior not in series["bars"]
                ):
                    problem = ("missing", prior)
                    break
                available_at = (
                    buffered["available"][offset]
                    if buffered is not None
                    else datetime.fromisoformat(series["available_at"][prior])
                )
                if available_at > decision:
                    problem = ("late", prior)
                    break
        window = {"required": required, "problem": problem}
        if problem is None and buffered is not None:
            start = end - count
            window.update(
                closes=buffered["closes"][start:end],
                highs=buffered["highs"][start:end],
                lows=buffered["lows"][start:end],
                descriptor=buffered["descriptor"],
                observations=b"[" + b",".join(buffered["members"][start:end]) + b"]",
            )
        elif problem is None:
            window.update(
                closes=tuple(series["bars"][day]["close"] for day in required),
                highs=tuple(series["bars"][day]["high"] for day in required),
                lows=tuple(series["bars"][day]["low"] for day in required),
                descriptor=json.dumps(
                    series["descriptor"], sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode(),
                observations=json.dumps(
                    [
                        {
                            "date": day,
                            "bar": series["bars"][day],
                            "available_at": series["available_at"][day],
                        }
                        for day in required
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode(),
            )
        charge = _heap_bytes(key) + _heap_bytes(window) + 256
        while self.__windows and (
            len(self.__windows) >= MAX_PREPARED_WINDOWS
            or self.__window_bytes + charge > MAX_PREPARED_WINDOW_BYTES
        ):
            _, (_, removed) = self.__windows.popitem(last=False)
            self.__window_bytes -= removed
        if MAX_PREPARED_WINDOWS > 0 and charge <= MAX_PREPARED_WINDOW_BYTES:
            self.__windows[key] = window, charge
            self.__window_bytes += charge
        return window

    def disable(self):
        self.clear()
        self.__enabled = False

    def close(self):
        self.clear()
        self.__bundle = self.__providers = None
        self.__records.clear()


class _PreparedFeatureCall:
    __slots__ = ("cache", "owner", "closed")

    def __init__(self, cache):
        self.cache, self.owner, self.closed = cache, get_ident(), False


def _prepared_window(record, decision, count):
    owner = _observation_cache.get()
    return owner.observation(record, decision, count) if owner is not None else None


def _window_problem(window, label):
    code, value = window["problem"]
    if code == "calendar":
        return "Entry session is absent from the saved stock calendar"
    if code == "count":
        return f"Stock {label} needs {value} earlier consecutive session closes"
    if code == "missing":
        return f"Stock {label} is missing the {value} session close"
    return f"The {value} stock close was not available before entry"


def _prepared_basis_record(record, decision, required):
    window = _prepared_window(record, decision, len(required))
    if window is None or window["problem"] is not None or window["required"] != tuple(required):
        return None
    return (
        b'{"descriptor":',
        window["descriptor"],
        b',"observations":',
        window["observations"],
        b"}",
    )


def _basis_members(members):
    """Hash the exact existing sorted JSON members; never hashes of hashes."""
    digest = sha256()
    digest.update(b"{")
    for i, key in enumerate(sorted(members)):
        if i:
            digest.update(b",")
        digest.update(json.dumps(key).encode())
        digest.update(b":")
        value = members[key]
        if isinstance(value, tuple):
            for part in value:
                digest.update(part)
        else:
            digest.update(value)
    digest.update(b"}")
    return digest.hexdigest()


def _basis_id(record, decision, required, hypothesis, version, dependency=None):
    window = _prepared_window(record, decision, len(required))
    payload = {
        "version": version,
        "hypothesis": hypothesis,
        "descriptor": record["evidence"]["descriptor"],
    }
    if dependency is not None:
        payload["dependency_version"] = dependency
    if window is not None and window["problem"] is None and window["required"] == tuple(required):
        members = {
            key: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            for key, value in payload.items()
            if key != "descriptor"
        }
        members["descriptor"], members["observations"] = (
            window["descriptor"],
            window["observations"],
        )
        return _basis_members(members)
    series = record["evidence"]
    payload["observations"] = [
        {"date": day, "bar": series["bars"][day], "available_at": series["available_at"][day]}
        for day in required
    ]
    return fingerprint(payload)


@contextmanager
def _prepared_feature_scope(cache):
    cache.check()
    call = _PreparedFeatureCall(cache)
    try:
        yield call
    except BaseException:
        cache.clear()
        raise
    finally:
        call.closed = True
        call.cache = None


def catalog():
    values = [
        {
            "id": "none",
            "family": "none",
            "label": "No stock filter",
            "lookback": 0,
            "threshold": None,
        }
    ]
    values.extend(
        {
            "id": f"ema_above_{n}",
            "family": "ema_trend",
            "label": f"Above {n}-session EMA",
            "lookback": n,
            "threshold": 0.0,
        }
        for n in (10, 20)
    )
    values.extend(
        {
            "id": f"momentum_{n}_gt{threshold}",
            "family": "momentum",
            "label": f"{n}-session momentum above {threshold}%",
            "lookback": n,
            "threshold": float(threshold),
        }
        for n in (5, 20)
        for threshold in (0, 2)
    )
    return values


def normalize_hypothesis(value):
    from research.prepared_stock_catalogue import normalize_if_owned

    admitted, selected = normalize_if_owned("legacy", value)
    if admitted:
        if selected is not None:
            return selected
        raise ValueError("The stock-feature hypothesis is not in the recorded catalog")
    if not isinstance(value, dict):
        raise ValueError("Choose a recorded stock-feature hypothesis")
    for expected in catalog():
        if value.get("id") == expected["id"] and fingerprint(value) == fingerprint(expected):
            return expected
    raise ValueError("The stock-feature hypothesis is not in the recorded catalog")


def recipe():
    return {
        "version": VERSION,
        "ema": {
            "windows": [10, 20],
            "continuous_closes": "5 * lookback",
            "seed": "arithmetic mean of the first lookback closes in the finite window",
            "alpha": "2 / (lookback + 1)",
            "recurrence": "alpha * close + (1 - alpha) * previous_ema",
            "initialization": "restart the declared finite window at each decision; not an infinite-history EMA",
        },
        "momentum": {
            "windows": [5, 20],
            "formula": "100 * (last_close / close_lookback_sessions_ago - 1)",
        },
        "availability": "Prior session dates only; recorded daily close available_at <= actual entry decision",
        "missing": "unknown if any expected session in the required window lacks observed OHLC",
        "threshold_equality": "within relative tolerance 1e-12 or absolute tolerance 1e-12 is not above",
        "warmup_sessions": WARMUP_SESSIONS,
    }


def _json_size(value, maximum):
    encoded = bounded_canonical_bytes(value, max_bytes=maximum, allow_native_float64=True)
    if encoded is not None:
        if len(encoded) > maximum:
            raise ValueError("Stock-feature evidence exceeds its bounded byte allowance")
        return len(encoded)
    fragments = bounded_canonical_fragments(value, max_bytes=maximum, allow_native_float64=True)
    if fragments is not None:
        return sum(map(len, fragments))
    size = 0
    for part in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(
        value
    ):
        size += len(part.encode())
        if size > maximum:
            raise ValueError("Stock-feature evidence exceeds its bounded byte allowance")
    return size


def _json_size_and_encoding(value, maximum):
    """Retain a bounded size-check's exact bytes for the following integrity check."""
    encoded = bounded_canonical_bytes(value, max_bytes=maximum, allow_native_float64=True)
    if encoded is None:
        return _json_size(value, maximum), None
    if len(encoded) > maximum:
        raise ValueError("Stock-feature evidence exceeds its bounded byte allowance")
    return len(encoded), encoded


def validate_record(symbol, record):
    if not isinstance(record, dict) or set(record) != {
        "evidence",
        "calendar",
        "acquisition_receipts",
    }:
        raise ValueError(
            "Stock features require retained prices, calendar and acquisition receipts"
        )
    series = validate_series(record["evidence"])
    descriptor, calendar = series["descriptor"], record["calendar"]
    if descriptor != {
        "version": series["version"],
        "symbol": symbol,
        "exchange": "NSE",
        "interval": "D",
        "role": "feature_warmup",
    }:
        raise ValueError("Stock features require the exact NSE stock warmup instrument")
    _, closing_times = _planned_series(descriptor, series["required_dates"], calendar)
    if series["required_dates"] != calendar["sessions"] or any(
        series["available_at"][day] != closing_times[day] for day in series["required_dates"]
    ):
        raise ValueError("Stock-feature calendar or completed-close availability changed")
    receipts = record["acquisition_receipts"]
    if not isinstance(receipts, list) or len(receipts) > 1000:
        raise ValueError("Stock-feature acquisition receipts exceed their bound")
    total = 0
    for item in receipts:
        if not isinstance(item, dict) or set(item) != {"sha256", "receipt"}:
            raise ValueError("Stock-feature acquisition receipt is invalid")
        size, encoded = _json_size_and_encoding(
            item["receipt"], min(MAX_RECEIPT_BYTES, 16 * 1024**2)
        )
        total += size
        if (
            total > MAX_RECEIPT_BYTES
            or (
                sha256(encoded).hexdigest() if encoded is not None else fingerprint(item["receipt"])
            )
            != item["sha256"]
        ):
            raise ValueError("Stock-feature acquisition receipt integrity changed")
        if (
            item["receipt"].get("version") != "openalgo-market-series-receipt-v1"
            or item["receipt"].get("descriptor") != descriptor
            or item["receipt"].get("symbol") != symbol
            or item["receipt"].get("exchange") != "NSE"
            or item["receipt"].get("interval") != "D"
        ):
            raise ValueError("Stock-feature acquisition receipt belongs to another instrument")
    return record


def validate_bundle(bundle):
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {"version", "series", "id"}
        or bundle["version"] != VERSION
        or not isinstance(bundle["series"], dict)
        or not 1 <= len(bundle["series"]) <= MAX_SYMBOLS
    ):
        raise ValueError("Stock-feature bundle needs a supported version and bounded symbol set")
    count = 0
    for symbol, record in bundle["series"].items():
        validate_record(symbol, record)
        count += len(record["evidence"]["required_dates"])
        if count > MAX_FEATURE_BARS:
            raise ValueError("Stock-feature history exceeds its total session-bar limit")
    identifier = bundle["id"]
    # A canonical nonempty object adds exactly72 bytes for its SHA256 id
    # member. Reuse one detached encoding for both the whole-object byte bound
    # and payload digest; each record's schema/price/receipt checks above stay
    # unchanged. Unsupported/custom trees retain the original strict path.
    pieces = None
    if (
        type(bundle) is dict
        and type(identifier) is str
        and len(identifier) == 64
        and all(character in "0123456789abcdef" for character in identifier)
    ):
        payload = {key: value for key, value in bundle.items() if key != "id"}
        raw = bounded_canonical_bytes(
            payload, max_bytes=max(0, MAX_BUNDLE_BYTES - 72), json_types_only=True
        )
        pieces = (
            (raw,)
            if raw is not None
            else bounded_canonical_fragments(payload, max_bytes=max(0, MAX_BUNDLE_BYTES - 72))
        )
    if pieces is None:
        _json_size(bundle, MAX_BUNDLE_BYTES)
        expected = fingerprint({key: value for key, value in bundle.items() if key != "id"})
    else:
        digest = sha256()
        for piece in pieces:
            digest.update(piece)
        expected = digest.hexdigest()
    if bundle["id"] != expected:
        raise ValueError("Saved stock-feature bundle changed")
    return bundle


def markers(settings, hypothesis, bundle_id=None):
    """Portable rule identity excludes data; the gate receipt binds exact evidence."""
    selected = normalize_hypothesis(hypothesis)
    result = deepcopy(settings)
    for row in result:
        row.pop("entry_condition", None)
        if selected["family"] != "none":
            row["entry_condition"] = {"version": ENTRY_VERSION, "hypothesis": deepcopy(selected)}
    return result


def _feature(record, decision, hypothesis):
    series, calendar = record["evidence"], record["calendar"]
    lookback = hypothesis["lookback"]
    count = 5 * lookback if hypothesis["family"] == "ema_trend" else lookback + 1
    window = _prepared_window(record, decision, count)
    if window is not None:
        if window["problem"] is not None:
            return None, _window_problem(window, "feature")
        required, closes = window["required"], list(window["closes"])
    else:
        day = decision.date().isoformat()
        dates = calendar["sessions"]
        if day not in calendar["session_hours"] or day not in dates:
            return None, "Entry session is absent from the saved stock calendar"
        end = bisect_left(dates, day)
        if end < count:
            return None, f"Stock feature needs {count} earlier consecutive session closes"
        required = dates[end - count : end]
        for prior in required:
            if prior not in series["bars"]:
                return None, f"Stock feature is missing the {prior} session close"
            if datetime.fromisoformat(series["available_at"][prior]) > decision:
                return None, f"The {prior} stock close was not available before entry"
        closes = [series["bars"][prior]["close"] for prior in required]
    result = {
        "lookback": lookback,
        "threshold": hypothesis["threshold"],
        "observations": count,
        "from": required[0],
        "to": required[-1],
        "available_at": series["available_at"][required[-1]],
        "close": closes[-1],
    }
    if hypothesis["family"] == "ema_trend":
        ema = sum(close / lookback for close in closes[:lookback])
        alpha = 2 / (lookback + 1)
        for close in closes[lookback:]:
            ema = alpha * close + (1 - alpha) * ema
        result.update(ema=ema, value=100 * (closes[-1] / ema - 1))
    else:
        result["value"] = 100 * (closes[-1] / closes[0] - 1)
    if not isfinite(result["value"]):
        return None, "Stock feature is outside finite calculation bounds"
    result["basis_id"] = _basis_id(record, decision, required, hypothesis, VERSION)
    return result, None


def apply(evidence, bundle, hypothesis):
    """Return gated original opportunities and a reproducible causal membership receipt."""
    from research.connectors.vectorbt_portfolio import _schedule

    return _apply(
        evidence, bundle, normalize_hypothesis(hypothesis), feature_fn=_feature, markers_fn=markers
    )


def _apply(
    evidence,
    bundle,
    selected,
    *,
    feature_fn,
    markers_fn,
    compare_fn=None,
    calculation_version=VERSION,
    feature_recipe=None,
    bundle_validator=None,
    _feature_cache=None,
):
    """Shared native schedule and receipt contract for versioned stock features."""
    from research.connectors.vectorbt_portfolio import _schedule

    (bundle_validator or validate_bundle)(bundle)
    if not evidence.get("frozen_prices"):
        raise ValueError("Stock feature gates require frozen native execution prices")
    if evidence["portfolio"]["engine"] != "vectorbt":
        raise ValueError("Stock feature gates currently require the VectorBT portfolio engine")
    total = sum(len(row["signals"]) for row in evidence["strategies"])
    if not 1 <= total <= MAX_SIGNALS:
        raise ValueError("Stock features need a bounded original scanner opportunity set")
    symbols = {signal["symbol"] for row in evidence["strategies"] for signal in row["signals"]}
    if symbols - set(bundle["series"]):
        raise ValueError("Saved stock features do not include every requested signal symbol")
    snapshot = evidence["snapshot"]
    interval = snapshot["provenance"]["interval"]
    if interval not in ("D", "1m"):
        raise ValueError("Stock conditions require daily or minute execution inputs")
    minute = interval == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    counts = dict.fromkeys(("allowed", "filtered", "unknown", "original_excluded", "pending"), 0)
    from research.causal_gate_components import components_only

    causal_only = components_only()
    if causal_only:
        from research.owned_entry_features import _capture

        selected = _capture(selected)
    strategies, membership, cached = [], [], {}
    for strategy in evidence["strategies"]:
        signals = []
        for i, original in enumerate(strategy["signals"]):
            signal = None if causal_only else copy_gate_signal(original)
            entry, reason = _schedule(
                original, snapshot, strategy["config"], timeline, index, minute
            )
            feature, decision, scheduled_at = None, None, None
            if reason:
                state = "original_excluded"
            elif entry < 0:
                state = "pending"
            elif selected["family"] == "none":
                state = "allowed"
            else:
                day = timeline[entry][:10]
                record = bundle["series"][original["symbol"]]
                hours = record["calendar"]["session_hours"].get(day)
                if hours:
                    opening = datetime.fromisoformat(f"{day}T{hours['open']}:00+05:30")
                    closing = datetime.fromisoformat(f"{day}T{hours['close']}:00+05:30")
                    scheduled_at = timeline[entry] if minute else opening.isoformat()
                    at = datetime.fromisoformat(scheduled_at)
                    if at.utcoffset() == IST.utcoffset(None) and opening <= at < closing:
                        decision = at
                if decision is None:
                    reason = "Stock feature entry is outside its recorded session hours"
                else:
                    key = (original["symbol"], decision.isoformat())
                    if key not in cached:
                        call = _feature_cache
                        if type(call) is _PreparedFeatureCall:
                            if call.closed or call.owner != get_ident():
                                raise ValueError(
                                    "Prepared stock feature call has expired or changed owner"
                                )
                            cached[key] = call.cache.resolve(
                                bundle,
                                original["symbol"],
                                record,
                                decision,
                                selected,
                                feature_fn,
                                calculation_version,
                                _immutable=causal_only,
                            )
                        else:
                            cached[key] = feature_fn(record, decision, selected)
                    feature, reason = cached[key]
                if feature is None:
                    state = "unknown"
                    if not causal_only:
                        signal["research_exclusion"] = reason
                elif not (
                    compare_fn(feature, selected)
                    if compare_fn
                    else feature["value"] > selected["threshold"]
                    and not isclose(
                        feature["value"], selected["threshold"], rel_tol=1e-12, abs_tol=1e-12
                    )
                ):
                    state = "filtered"
                    if not causal_only:
                        signal["research_exclusion"] = (
                            f"Stock condition not met: {selected['label']}"
                        )
                else:
                    state = "allowed"
            counts[state] += 1
            membership.append(
                {
                    "strategy_id": strategy["id"],
                    "signal_index": i,
                    "symbol": original["symbol"],
                    "signal_date": original["date"],
                    "source_row": original.get("row"),
                    "scheduled_at": scheduled_at,
                    "decision_at": decision.isoformat() if decision else None,
                    "state": state,
                    "feature": deepcopy(feature),
                    "reason": reason,
                }
            )
            if not causal_only:
                signals.append(signal)
        # Signal rows above are already detached; do not copy and discard a
        # second complete signal/entry-feature tree when replacing this field.
        if not causal_only:
            strategies.append(
                {
                    **deepcopy({key: value for key, value in strategy.items() if key != "signals"}),
                    "signals": freeze_gate_signals(signals),
                }
            )
    if not causal_only:
        strategies = markers_fn(strategies, selected)
    receipt = {
        "version": VERSION,
        "hypothesis": selected,
        "bundle_id": bundle["id"],
        "counts": counts,
        "membership": membership,
        "feature_recipe": recipe() if feature_recipe is None else feature_recipe,
        "timing": "Only completed daily observations from sessions before the actual entry decision",
        "unknown_policy": "Exclude unknown features; preserve original exclusions and pending entries",
    }
    if calculation_version != VERSION:
        receipt["calculation_version"] = calculation_version
    if causal_only:
        return [], receipt
    receipt.update(
        execution_inputs_id=execution_inputs_id(evidence),
        conditioned_strategies_id=account_fingerprint(strategies),
    )
    return strategies, {**receipt, "id": fingerprint(receipt)}
