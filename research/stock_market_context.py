"""One immutable index history with distinct stock and market lookback views.

The stock projection is the existing v4 contract. Market classification sees
the longer reference, without widening stock acquisition or changing old math.
No acquisition or global cache lives here. Prepared job-local views own one
detached bounded history and release it when their context closes.
"""

from copy import deepcopy
from datetime import date

from research import stock_features as stock
from research import stock_relative_strength as relative
from research.market_series import fingerprint, slice_series

VERSION = "research-stock-market-context-v1"
MARKET_WARMUP_SESSIONS = 160
KEY = "automatic_stock_market_context"


def _market_context():
    from research.market_conditions import VERSION as CONDITIONS_VERSION
    from research.regimes import recipe

    return {"version": CONDITIONS_VERSION, "recipe": recipe()}


def _compatible_calendar(left, right, days):
    if any(left["session_hours"][day] != right["session_hours"][day] for day in days):
        raise ValueError("Stock and market context session hours changed")

    # Native calendars differ only in their explicitly requested warmup count.
    def metadata(calendar):
        value = deepcopy(calendar)
        value.pop("sessions", None)
        value.pop("session_hours", None)
        value.get("provenance", {}).pop("warmup_sessions", None)
        return value

    if metadata(left) != metadata(right):
        raise ValueError("Stock and market context calendar provenance changed")


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
    stock._json_size(bundle, stock.MAX_BUNDLE_BYTES)
    if bundle["id"] != fingerprint({k: v for k, v in bundle.items() if k != "id"}):
        raise ValueError("Saved stock-market context changed")
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
    source dictionaries. No entry decision, feature result or account is cached.
    """

    __slots__ = ("__stock", "__market", "__bundle_id")

    def __init__(self, bundle):
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

    @property
    def bundle_id(self):
        return self.__bundle_id

    def __validate_stock(self, bundle):
        if self.__stock is None or bundle is not self.__stock:
            raise ValueError("Prepared stock gates require their exact privately owned projection")
        return bundle

    def apply_stock(self, evidence, hypothesis, *, _gate=None):
        return (_gate or relative._apply_with_validator)(
            evidence, self.__stock, hypothesis, self.__validate_stock
        )

    def apply_market(self, evidence, condition):
        from research.condition_replay import apply

        if self.__market is None:
            raise ValueError("The prepared stock-market context has been released")
        return apply(evidence, self.__market, condition)

    def close(self):
        self.__stock = None
        self.__market = None
