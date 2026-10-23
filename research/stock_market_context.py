"""One immutable index history with distinct stock and market lookback views.

The stock projection is the existing v4 contract. Market classification sees
the longer reference, without widening stock acquisition or changing old math.
No acquisition or global cache lives here. Prepared job-local views own one
detached bounded history and release it when their context closes.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import date
from functools import wraps
from hashlib import sha256

from research import canonical_json
from research import stock_features as stock
from research import stock_relative_strength as relative
from research.canonical_json import bounded_canonical_bytes
from research.market_series import fingerprint, slice_series

VERSION = "research-stock-market-context-v1"
MARKET_WARMUP_SESSIONS = 160
KEY = "automatic_stock_market_context"
_RELATIVE_STOCK_GATE = relative._apply_with_validator


class _BundleValidation:
    """One bounded successful content signature, released with its owning job.

    This retains canonical bytes and an immutable id, never caller dictionaries
    or an object-id approval. A
    changed input must pass all validators again. Tuples and custom objects are
    ineligible: their JSON may match a list/plain value with different schema
    semantics. Inputs beyond the fast encoder's bounds use normal validation.
    """

    __slots__ = ("_encoded", "_closed", "__weakref__")

    def __init__(self):
        self._encoded = None
        self._closed = False

    def matches(self, encoded):
        return not self._closed and encoded is not None and encoded == self._encoded

    def remember(self, encoded):
        if not self._closed:
            self._encoded = encoded

    def close(self):
        self._encoded = None
        self._closed = True


_bundle_validation = ContextVar("research_stock_market_bundle_validation", default=None)


@contextmanager
def _bundle_validation_scope():
    """Isolate one job's pure bundle validation, including nested/error paths."""
    owner = _BundleValidation()
    token = _bundle_validation.set(owner)
    try:
        yield owner
    finally:
        owner.close()
        _bundle_validation.reset(token)


def _with_bundle_validation(function):
    @wraps(function)
    def scoped(*args, **kwargs):
        with _bundle_validation_scope():
            return function(*args, **kwargs)

    return scoped


def _market_context():
    from research.market_conditions import VERSION as CONDITIONS_VERSION
    from research.regimes import recipe

    return {"version": CONDITIONS_VERSION, "recipe": recipe()}


def _compatible_calendar(left, right, days):
    if any(left["session_hours"][day] != right["session_hours"][day] for day in days):
        raise ValueError("Stock and market context session hours changed")

    # Native calendars differ only in their explicitly requested warmup count.
    def metadata(calendar):
        value = {
            key: item for key, item in calendar.items() if key not in {"sessions", "session_hours"}
        }
        if "provenance" in value:
            value["provenance"] = {
                key: item for key, item in value["provenance"].items() if key != "warmup_sessions"
            }
        return value

    if metadata(left) != metadata(right):
        raise ValueError("Stock and market context calendar provenance changed")


def _validation_encoding(bundle):
    """Bind exact plain content once for size, identity and scoped admission.

    A proper outer id contributes exactly 72 bytes to this nonempty canonical
    object. Encoding its payload therefore supplies the same size and hash as
    the separate full-object size and payload-fingerprint walks. The bounded
    copier still excludes custom values, cycles and type aliases such as tuples;
    those retain the original validators and streaming size/error behavior.
    """
    if type(bundle) is not dict:
        return None
    identifier = bundle["id"]
    if (
        type(identifier) is not str
        or len(identifier) != 64
        or any(character not in "0123456789abcdef" for character in identifier)
    ):
        return None
    if type(bundle["series"]) is dict and len(bundle["series"]) > 32:
        encoded = _large_validation_encoding(bundle)
    else:
        encoded = bounded_canonical_bytes(
            {key: value for key, value in bundle.items() if key != "id"},
            max_bytes=max(
                0, min(stock.MAX_BUNDLE_BYTES, canonical_json.MAX_MEDIUM_ASCII_BYTES) - 72
            ),
            json_types_only=True,
        )
    return (encoded, identifier) if encoded is not None else None


def _large_validation_encoding(bundle):
    """Assemble exact JSON without copying a whole large scanner tree at once.

    Each of at most MAX_SYMBOLS records retains the encoder's node, depth and
    plain-type limits. The aggregate signature retains its original 32 MiB
    ceiling and the full bundle byte allowance. Thus a large admissible native
    history may reuse validation without raising any research work allowance.
    """
    series = bundle["series"]
    if (
        type(series) is not dict
        or not 1 <= len(series) <= stock.MAX_SYMBOLS
        or any(type(symbol) is not str for symbol in series)
    ):
        return None
    maximum = min(stock.MAX_BUNDLE_BYTES, canonical_json.MAX_MEDIUM_ASCII_BYTES) - 72
    if maximum < 2:
        return None
    members, size = [], 2
    try:
        for key in sorted(key for key in bundle if key != "id"):
            if key == "series":
                records, record_size = [], len(b'"series":{}')
                for symbol in sorted(series):
                    raw = bounded_canonical_bytes(
                        {symbol: series[symbol]},
                        max_bytes=max(0, maximum - size - record_size + 2),
                        json_types_only=True,
                    )
                    if raw is None:
                        return None
                    record_size += len(raw) - 2 + (1 if records else 0)
                    if size + record_size > maximum:
                        return None
                    records.append(raw[1:-1])
                member = b'"series":{' + b",".join(records) + b"}"
            else:
                raw = bounded_canonical_bytes(
                    {key: bundle[key]},
                    max_bytes=max(0, maximum - size + 2),
                    json_types_only=True,
                )
                if raw is None:
                    return None
                member = raw[1:-1]
            size += len(member) + (1 if members else 0)
            if size > maximum:
                return None
            members.append(member)
        return b"{" + b",".join(members) + b"}"
    except (KeyError, TypeError, RuntimeError):
        return None


def _receipt_scope(record):
    """Keep admitted receipt slots within this acquisition's declared calendar."""
    required = set(record["evidence"]["required_dates"])
    for item in record["acquisition_receipts"]:
        receipt = item["receipt"]
        slots = receipt.get("accepted_slots")
        if slots is not None and (
            not isinstance(slots, list)
            or any(not isinstance(day, str) or day not in required for day in slots)
            or len(slots) != len(set(slots))
        ):
            raise ValueError("Context acquisition receipt belongs to another session window")
        if "requested_from" in receipt or "requested_to" in receipt:
            try:
                first, last = receipt["requested_from"], receipt["requested_to"]
                if (
                    date.fromisoformat(first).isoformat() != first
                    or date.fromisoformat(last).isoformat() != last
                    or not record["evidence"]["required_dates"][0]
                    <= first
                    <= last
                    <= record["evidence"]["required_dates"][-1]
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "Context acquisition receipt belongs to another session window"
                ) from exc


def validate_bundle(bundle):
    if (
        not isinstance(bundle, dict)
        or set(bundle)
        != {"version", "execution_sessions", "series", "market", "market_context", "id"}
        or bundle.get("version") != VERSION
        or not isinstance(bundle["series"], dict)
        or not 1 <= len(bundle["series"]) <= stock.MAX_SYMBOLS
    ):
        raise ValueError("Stock-market context needs a supported version and bounded symbol set")
    if bundle["market_context"] != _market_context():
        raise ValueError("The saved stock-market classifier recipe changed")
    days = bundle["execution_sessions"]
    if (
        not isinstance(days, list)
        or not days
        or any(not isinstance(day, str) for day in days)
        or days != sorted(set(days))
    ):
        raise ValueError("Stock-market context needs ordered execution sessions")
    owner = _bundle_validation.get()
    encoded = _validation_encoding(bundle) if owner is not None else None
    if owner is not None and owner.matches(encoded):
        return bundle
    market = relative.validate_benchmark(bundle["market"])
    market_days = market["calendar"]["sessions"]
    if market_days[MARKET_WARMUP_SESSIONS:] != days:
        raise ValueError("Market context needs its exact 160-session warmup and execution window")
    _receipt_scope(market)
    expected_stock_days = market_days[MARKET_WARMUP_SESSIONS - stock.WARMUP_SESSIONS :]
    count = len(market_days)
    first_calendar = None
    for symbol, record in bundle["series"].items():
        stock.validate_record(symbol, record)
        if record["calendar"]["sessions"] != expected_stock_days:
            raise ValueError("Stock context needs its exact aligned 100-session warmup")
        _compatible_calendar(record["calendar"], market["calendar"], expected_stock_days)
        if first_calendar is not None and record["calendar"] != first_calendar:
            raise ValueError("Stock context calendars disagree")
        first_calendar = record["calendar"]
        _receipt_scope(record)
        count += len(expected_stock_days)
        if count > stock.MAX_FEATURE_BARS:
            raise ValueError("Stock-market history exceeds its total session-bar limit")
    admitted = _validation_encoding(bundle)
    if admitted is None:
        stock._json_size(bundle, stock.MAX_BUNDLE_BYTES)
        identifier = fingerprint({k: v for k, v in bundle.items() if k != "id"})
    else:
        raw, _ = admitted
        if len(raw) + 72 > stock.MAX_BUNDLE_BYTES:
            raise ValueError("Stock-feature evidence exceeds its bounded byte allowance")
        identifier = sha256(raw).hexdigest()
    if bundle["id"] != identifier:
        raise ValueError("Saved stock-market context changed")
    if owner is not None and encoded is not None and admitted == encoded:
        # Do not approve bytes observed before validation if the caller changed
        # its dictionary while admission was running.
        owner.remember(encoded)
    return bundle


def freeze_bundle(series, market, execution_sessions):
    payload = {
        "version": VERSION,
        "execution_sessions": deepcopy(execution_sessions),
        "series": deepcopy(series),
        "market": deepcopy(market),
        "market_context": _market_context(),
    }
    return validate_bundle({**payload, "id": fingerprint(payload)})


def stock_view(bundle):
    """Project one shared index onto the unchanged stock/relative-strength schema."""
    validate_bundle(bundle)
    calendar = next(iter(bundle["series"].values()))["calendar"]
    days = calendar["sessions"]
    market = bundle["market"]
    payload = {
        "version": relative.VERSION,
        "series": deepcopy(bundle["series"]),
        "benchmark": {
            "calendar": deepcopy(calendar),
            "evidence": slice_series(market["evidence"], days[0], days[-1]),
            # Receipts describe the single wider acquisition, not another fetch.
            "acquisition_receipts": deepcopy(market["acquisition_receipts"]),
        },
    }
    return relative.validate_bundle({**payload, "id": fingerprint(payload)})


def market_view(bundle):
    """Return the native market-condition record without any new calculation."""
    validate_bundle(bundle)
    return {
        **deepcopy(bundle["market"]),
        "context": deepcopy(bundle["market_context"]),
    }


class _PreparedViews:
    """One private detached owner; no raw projection is returned to callers.

    Projection records share this owner's validated dictionaries, never the
    source dictionaries. A bounded private feature cache reuses only identical
    causal stock observations; every current account decision remains fresh.
    """

    __slots__ = ("__stock", "__market", "__bundle_id", "__classifier", "__features")

    def __init__(self, bundle):
        self.__stock = self.__market = self.__classifier = self.__features = None
        # Enforce the serialized ceiling before allocating the detached copy.
        stock._json_size(bundle, stock.MAX_BUNDLE_BYTES)
        detached = deepcopy(bundle)
        validate_bundle(detached)
        self.__bundle_id = detached["id"]
        calendar = next(iter(detached["series"].values()))["calendar"]
        days = calendar["sessions"]
        market = detached["market"]
        full = market["evidence"]
        # Reproduce slice_series' exact schema and identity without copying the
        # privately owned stock history or market bars a second time.
        sliced = {
            **full,
            "required_dates": list(days),
            "bars": {day: full["bars"][day] for day in days if day in full["bars"]},
            "available_at": {day: full["available_at"][day] for day in days},
        }
        from research.market_series import _coverage

        sliced["coverage"] = _coverage(days, sliced["bars"])
        sliced["id"] = fingerprint({key: value for key, value in sliced.items() if key != "id"})
        payload = {
            "version": relative.VERSION,
            "series": detached["series"],
            "benchmark": {
                "calendar": calendar,
                "evidence": sliced,
                "acquisition_receipts": market["acquisition_receipts"],
            },
        }
        projected = {**payload, "id": fingerprint(payload)}
        relative.validate_bundle(projected)
        self.__stock = projected
        self.__market = {**market, "context": detached["market_context"]}
        self.__features = stock._PreparedFeatureCache(projected)

    @property
    def bundle_id(self):
        return self.__bundle_id

    def __validate_stock(self, bundle):
        if self.__stock is None or bundle is not self.__stock:
            raise ValueError("Prepared stock gates require their exact privately owned projection")
        return bundle

    def apply_stock(self, evidence, hypothesis, *, _gate=None):
        if self.__features is None:
            raise ValueError("The prepared stock-market context has been released")
        self.__features.check()
        gate = _gate or relative._apply_with_validator
        native = gate is _RELATIVE_STOCK_GATE
        if not native and _gate is not None:
            from research.expanded_stock_market import _PREPARED_STOCK_GATE

            native = gate is _PREPARED_STOCK_GATE
        if native:
            with stock._prepared_feature_scope(self.__features) as capability:
                return gate(
                    evidence,
                    self.__stock,
                    hypothesis,
                    self.__validate_stock,
                    _feature_cache=capability,
                )
        # An arbitrary callback sees the old mutable projection contract. It
        # cannot leave an approval for later private observation reuse.
        self.__features.disable()
        return gate(evidence, self.__stock, hypothesis, self.__validate_stock)

    def apply_market(self, evidence, condition):
        from research.condition_replay import _apply
        from research.regimes import _PreparedClassifier

        if self.__market is None:
            raise ValueError("The prepared stock-market context has been released")
        if self.__classifier is None:
            self.__classifier = _PreparedClassifier(self.__market["evidence"])
        return _apply(evidence, self.__market, condition, classify_fn=self.__classify)

    def __classify(self, evidence, decisions):
        if self.__market is None or evidence is not self.__market["evidence"]:
            raise ValueError("Prepared market gates require their exact privately owned projection")
        return self.__classifier.classify(decisions)

    def close(self):
        if self.__features is not None:
            self.__features.close()
            self.__features = None
        if self.__classifier is not None:
            self.__classifier.close()
            self.__classifier = None
        self.__stock = None
        self.__market = None
