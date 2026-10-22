"""Bounded causal volume/correlation inputs for native portfolio limits.

These are historical simulation assumptions, not executable liquidity or a
prediction of future dependence. Native acquisition owns volume provenance.
"""

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from io import StringIO
from math import isfinite
from re import fullmatch

from research.portfolio import fingerprint
from research.stock_features import _json_size

VERSION = "research-portfolio-execution-features-v1"
CAPACITY_VERSION = "research-entry-capacity-v1"
CORRELATION_VERSION = "research-entry-correlation-v1"
MAX_BYTES = 64 * 1024**2
MAX_PAIRS = 500_000
MAX_FEATURES = 25_000


def _seal(value):
    return {**value, "id": fingerprint(value)}


def _sealed_identity(value, maximum, *, _capture=None):
    """Check a sealed receipt's exact byte bound and digest in one traversal.

    Canonical JSON differs from its payload only by the top-level id member:
    71 bytes, plus one comma for a nonempty payload. Nothing is retained across
    calls. Malformed/custom inputs and failures keep the original size-check
    path, including its error precedence and streaming early byte bound.
    """
    if (
        type(value) is not dict
        or type(value.get("id")) is not str
        or not fullmatch(r"[a-f0-9]{64}", value["id"])
        or any(type(key) is not str for key in value)
    ):
        _json_size(value, maximum)
        return isinstance(value, dict) and value.get("id") == fingerprint(
            {key: item for key, item in value.items() if key != "id"}
        )
    payload = {key: item for key, item in value.items() if key != "id"}
    size = 71 + bool(payload)
    digest = hashlib.sha256()
    try:
        for part in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(payload):
            encoded = part.encode()
            size += len(encoded)
            if size > maximum:
                # The id may occur later in the original sorted stream. On an
                # invalid input preserve whether its old bound or JSON error
                # would have been encountered first.
                raise ValueError("Stock-feature evidence exceeds its bounded byte allowance")
            digest.update(encoded)
            if _capture is not None:
                _capture.write(part)
    except (TypeError, ValueError, RecursionError):
        _json_size(value, maximum)
        raise
    return digest.hexdigest() == value["id"]


def _base(value, version, symbol, entry_session, decision_at):
    _json_size(value, 64 * 1024)
    if (
        not isinstance(value, dict)
        or value.get("version") != version
        or value.get("id") != fingerprint({k: v for k, v in value.items() if k != "id"})
        or value.get("symbol") != symbol
        or value.get("entry_session") != entry_session
        or not isinstance(value.get("source_id"), str)
        or not fullmatch(r"[a-f0-9]{64}", value["source_id"])
    ):
        raise ValueError("Portfolio entry evidence changed its source or identity")
    try:
        available = datetime.fromisoformat(value["available_at"])
        decision = datetime.fromisoformat(decision_at)
        if (
            available.tzinfo is None
            or decision.tzinfo is None
            or available > decision
            or not value["from"] <= value["to"] < entry_session
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Portfolio entry evidence was not available before entry") from error


def validate_capacity_entry(value, *, symbol, entry_session, decision_at):
    _base(value, CAPACITY_VERSION, symbol, entry_session, decision_at)
    if (
        set(value)
        != {
            "version",
            "symbol",
            "entry_session",
            "from",
            "to",
            "observations",
            "available_at",
            "source_id",
            "mean_daily_shares",
            "basis",
            "id",
        }
        or value.get("observations") != 20
        or value.get("basis") != "provider_returned_historical_coordinates"
        or type(value.get("mean_daily_shares")) not in (float, int)
        or not isfinite(value["mean_daily_shares"])
        or not 0 < value["mean_daily_shares"] <= 2**53 - 1
    ):
        raise ValueError("capacity_unavailable: completed broker volume is unavailable")
    return value


def validate_correlation_entry(value, *, symbol, entry_session, decision_at, required_symbols):
    _base(value, CORRELATION_VERSION, symbol, entry_session, decision_at)
    correlations = value.get("correlations")
    if (
        set(value)
        != {
            "version",
            "symbol",
            "entry_session",
            "from",
            "to",
            "observations",
            "available_at",
            "source_id",
            "correlations",
            "id",
        }
        or value.get("observations") != 60
        or not isinstance(correlations, dict)
        or not 1 <= len(correlations) <= 200
        or correlations.get(symbol) != 1.0
        or any(not isinstance(k, str) or len(k) > 100 for k in correlations)
        or any(
            v is not None and (type(v) not in (int, float) or not isfinite(v) or not -1 <= v <= 1)
            for v in correlations.values()
        )
    ):
        raise ValueError("correlation_unavailable: causal correlation evidence is invalid")
    if any(correlations.get(other) is None for other in required_symbols):
        raise ValueError("correlation_unavailable: a held symbol lacks aligned prior returns")
    return value


def _returns(record, day):
    """Exactly sixty returns from sixty-one prior expected completed closes."""
    series, calendar = record["evidence"], record["calendar"]
    earlier = [d for d in calendar["sessions"] if d < day][-61:]
    if len(earlier) != 61:
        return None
    bars = series["bars"]
    if any(d not in bars for d in earlier):
        return None
    hours = calendar["session_hours"].get(day)
    if not hours:
        return None
    decision = datetime.fromisoformat(day + "T" + hours["open"] + ":00+05:30")
    if any(datetime.fromisoformat(series["available_at"][d]) > decision for d in earlier):
        return None
    closes = [bars[d]["close"] for d in earlier]
    if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in closes):
        return None
    returns = [b / a - 1 for a, b in zip(closes[:-1], closes[1:], strict=True)]
    if not all(isfinite(value) for value in returns):
        return None
    return {
        "dates": earlier,
        "returns": returns,
        "available_at": max(series["available_at"][d] for d in earlier),
        "source_id": fingerprint({d: bars[d] for d in earlier}),
    }


def _correlation(symbol, day, series, prepared):
    import numpy as np

    own = prepared.get(symbol)
    if own is None:
        return None
    values, identities = {}, {}
    for other, candidate in prepared.items():
        rho = None
        if other == symbol:
            rho = 1.0  # Same-instrument exposure is always counted together.
        elif candidate is not None and candidate["dates"] == own["dates"]:
            left, right = np.asarray(own["returns"]), np.asarray(candidate["returns"])
            if np.std(left) > 1e-14 and np.std(right) > 1e-14:
                rho = float(np.clip(np.corrcoef(left, right)[0, 1], -1, 1))
        values[other] = rho
        identities[other] = candidate["source_id"] if candidate is not None else None
    return _seal(
        {
            "version": CORRELATION_VERSION,
            "symbol": symbol,
            "entry_session": day,
            "from": own["dates"][0],
            "to": own["dates"][-1],
            "observations": 60,
            "available_at": max(v["available_at"] for v in prepared.values() if v),
            "source_id": fingerprint(identities),
            "correlations": values,
        }
    )


def prepare(evidence, bundle, volume_records=None):
    """Prepare once for every admitted clock; reuse the exact frozen receipt."""
    from research.stock_market_context import validate_bundle
    from research.volume_provenance import assess_observed

    validate_bundle(bundle)
    atr = evidence.get("execution_features")
    if atr is None:
        from research.connectors.vectorbt_portfolio import _schedule
        from research.execution_features import _identity, _opportunity_keys

        snapshot = evidence["snapshot"]
        minute = snapshot["provenance"]["interval"] == "1m"
        timeline = snapshot["timeline" if minute else "sessions"]
        index = {key: i for i, key in enumerate(timeline)}
        opportunities = {}
        for strategy in evidence["strategies"]:
            opportunities[strategy["id"]] = {}
            for key, signal in _opportunity_keys(strategy["signals"]):
                entry, reason = _schedule(
                    signal, snapshot, strategy["config"], timeline, index, minute
                )
                opportunities[strategy["id"]][key] = {
                    "signal": _identity(signal),
                    "required": [f"{signal['symbol']}|{timeline[entry][:10]}"]
                    if entry >= 0 and not reason
                    else [],
                }
        atr = {"id": None, "opportunities": opportunities}
    required = sorted(
        {
            k
            for rows in atr["opportunities"].values()
            for row in rows.values()
            for k in row["required"]
        },
        key=lambda key: (key.rsplit("|", 1)[1], key),
    )
    if len(required) > MAX_FEATURES or len(required) * len(bundle["series"]) > MAX_PAIRS:
        raise ValueError("Portfolio feature work exceeds its bounded symbol/date allowance")
    existing = evidence.get("portfolio_execution_features")
    if volume_records is None:
        volume_records = existing.get("volume_records", {}) if existing else {}
    _json_size(volume_records, MAX_BYTES)
    allocation_on = any(
        row.get("execution_policy", {}).get("version") == "research-execution-aware-v5"
        for row in evidence["strategies"]
    )
    allocations = {}
    correlations, capacity = {}, {}
    day_cache = {}
    for key in required:
        symbol, day = key.rsplit("|", 1)
        if day not in day_cache:
            day_cache = {day: {s: _returns(record, day) for s, record in bundle["series"].items()}}
        if allocation_on and day not in allocations:
            from research.portfolio_allocation import build

            allocations[day] = build(day_cache[day], day)
        feature = _correlation(symbol, day, bundle["series"], day_cache[day])
        correlations[key] = feature
        record = bundle["series"][symbol]
        hours = record["calendar"]["session_hours"].get(day)
        decision = day + "T" + hours["open"] + ":00+05:30" if hours else day
        prior = [d for d in record["calendar"]["sessions"] if d < day][-20:]
        observations = volume_records.get(symbol, {})
        aligned = {
            d: observations[d]
            for d in prior
            if d in observations
            and d in record["evidence"]["bars"]
            and isinstance(observations[d], dict)
            and isinstance(observations[d].get("candle"), dict)
            and all(
                observations[d]["candle"].get(field) == record["evidence"]["bars"][d][field]
                for field in ("timestamp", "open", "high", "low", "close")
            )
        }
        model = assess_observed(aligned, record["calendar"], symbol, day, decision)
        cap = None
        if model.get("status") == "available" and model.get("mean_daily_shares", 0) > 0:
            window = model["window"]
            cap = _seal(
                {
                    "version": CAPACITY_VERSION,
                    "symbol": symbol,
                    "entry_session": day,
                    "from": window["from"],
                    "to": window["to"],
                    "observations": 20,
                    "available_at": window["available_at"],
                    "source_id": model["id"],
                    "mean_daily_shares": model["mean_daily_shares"],
                    "basis": "provider_returned_historical_coordinates",
                }
            )
        capacity[key] = {"feature": cap, "assessment": model}
    opportunities = {}
    policies = {row["id"]: row["execution_policy"] for row in evidence["strategies"]}
    for strategy, rows in atr["opportunities"].items():
        opportunities[strategy] = {}
        for key, row in rows.items():
            reason = None
            if policies[strategy].get("capacity_mode") == "prior20_broker_reported" and any(
                capacity[k]["feature"] is None for k in row["required"]
            ):
                reason = "capacity_unavailable: 20 completed broker-volume sessions required"
            if policies[strategy].get("correlation_mode") == "prior60_positive_080" and any(
                correlations[k] is None for k in row["required"]
            ):
                reason = "correlation_unavailable: 60 aligned completed daily returns required"
            opportunities[strategy][key] = {"signal": row["signal"], "reason": reason}
    allocation_marks = {}
    if allocation_on:
        snapshot = evidence["snapshot"]
        # Cover every admitted timing branch, not just the original entry clock.
        # These frozen opening marks reconcile fills; they are never features
        # available to the classifier before the entry opening itself.
        if sum(len(bars) for bars in snapshot["bars"].values()) > MAX_PAIRS:
            raise ValueError("Allocation opening marks exceed their bounded allowance")
        for symbol, bars in snapshot["bars"].items():
            for key, bar in bars.items():
                allocation_marks.setdefault(key, {})[symbol] = bar["open"]
        if existing is not None:
            retained_marks = existing.get("allocation_marks")
            if not isinstance(retained_marks, dict):
                raise ValueError("Saved allocation opening marks are missing")
            # Replays and development slices retain the original union. Check
            # every expected timestamp in this slice, including absent bars;
            # an omitted or changed current mark must never pass as a subset.
            keys = snapshot.get("timeline", snapshot["sessions"])
            if any(allocation_marks.get(key, {}) != retained_marks.get(key, {}) for key in keys):
                raise ValueError("Saved allocation opening marks changed their source")
            allocation_marks = retained_marks
    result = _seal(
        {
            **(
                {"allocation": allocations, "allocation_marks": allocation_marks}
                if allocation_on
                else {}
            ),
            "version": VERSION,
            "context_id": bundle["id"],
            "atr_id": atr["id"],
            "correlation": correlations,
            "capacity": capacity,
            "volume_records": volume_records,
            "opportunities": opportunities,
            "counts": {
                "entry_windows": len(required),
                "capacity_available": sum(v["feature"] is not None for v in capacity.values()),
                "correlation_available": sum(v is not None for v in correlations.values()),
            },
        }
    )
    _json_size(result, MAX_BYTES)
    if existing is not None and existing != result:
        raise ValueError("Saved portfolio features do not reproduce their frozen source")
    return {**evidence, "portfolio_execution_features": result}


def attach(evidence):
    receipt = evidence.get("portfolio_execution_features")
    intact = _sealed_identity(receipt, MAX_BYTES)
    if not isinstance(receipt, dict) or receipt.get("version") != VERSION or not intact:
        raise ValueError("Saved portfolio execution features changed")
    return _attach(evidence, receipt)


def _attach(evidence, receipt):
    """Apply an admitted receipt; candidate schedules and entry checks remain fresh."""
    from research.connectors.vectorbt_portfolio import _schedule
    from research.execution_features import _identity, _opportunity_keys
    from research.intraday import bounds

    if evidence.get("execution_features", {}).get("id") != receipt["atr_id"]:
        raise ValueError("Portfolio features belong to another clock/admission mask")
    if (
        evidence.get("automatic_stock_market_context", {}).get("id", receipt["context_id"])
        != receipt["context_id"]
    ):
        raise ValueError("Portfolio features belong to another frozen context")
    snapshot = evidence["snapshot"]
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    strategies = []
    for strategy in evidence["strategies"]:
        signals = []
        policy = strategy.get("execution_policy", {})
        for opportunity, original in _opportunity_keys(strategy["signals"]):
            signal = {
                k: v
                for k, v in original.items()
                if k not in ("entry_capacity", "entry_correlation", "entry_allocation")
            }
            saved = receipt["opportunities"].get(strategy["id"], {}).get(opportunity)
            if saved is None or saved["signal"] != _identity(original):
                raise ValueError("Portfolio feature source opportunity changed")
            if saved["reason"] and not signal.get("research_exclusion"):
                signal["research_exclusion"] = saved["reason"]
            entry, reason = _schedule(signal, snapshot, strategy["config"], timeline, index, minute)
            if entry >= 0 and not reason and not signal.get("research_exclusion"):
                day = timeline[entry][:10]
                key = f"{signal['symbol']}|{day}"
                decision = timeline[entry] if minute else bounds(snapshot, day)[0].isoformat()
                if policy.get("capacity_mode") == "prior20_broker_reported":
                    feature = receipt["capacity"].get(key, {}).get("feature")
                    if feature is None:
                        signal["research_exclusion"] = (
                            "capacity_unavailable: 20 completed broker-volume sessions required"
                        )
                    else:
                        signal["entry_capacity"] = deepcopy(
                            validate_capacity_entry(
                                feature,
                                symbol=signal["symbol"],
                                entry_session=day,
                                decision_at=decision,
                            )
                        )
                if policy.get("correlation_mode") == "prior60_positive_080":
                    feature = receipt["correlation"].get(key)
                    if feature is None:
                        signal["research_exclusion"] = (
                            "correlation_unavailable: 60 aligned completed daily returns required"
                        )
                    else:
                        signal["entry_correlation"] = deepcopy(
                            validate_correlation_entry(
                                feature,
                                symbol=signal["symbol"],
                                entry_session=day,
                                decision_at=decision,
                                required_symbols=[signal["symbol"]],
                            )
                        )
                if policy.get("allocation_mode") == "prior60_cluster_inverse_vol":
                    from research.portfolio_allocation import validate

                    feature = receipt.get("allocation", {}).get(day)
                    if feature is None or signal["symbol"] not in feature["weights"]:
                        signal["research_exclusion"] = (
                            "allocation_unavailable: 60 aligned completed daily returns required"
                        )
                    else:
                        signal["entry_allocation"] = deepcopy(
                            validate(
                                feature,
                                symbol=signal["symbol"],
                                entry_session=day,
                                decision_at=decision,
                            )
                        )
            signals.append(signal)
        strategies.append({**strategy, "signals": signals})
    return {**evidence, "strategies": strategies}


class _PreparedPortfolioFeatures:
    """One bounded, detached sealed receipt owned by one automatic run.

    The caller's receipt is an identity capability, never a cache key. Only the
    full immutable-source seal is reused: schedules, availability, entry feature
    identities and actual native fill/portfolio constraints still run for every
    account. The privately owned JSON projection is never returned to callers.
    Closing the owner releases both source and projection, including on errors.
    """

    __slots__ = ("__source", "__receipt", "__weakref__")

    def __init__(self, receipt):
        self.__source = self.__receipt = None
        if (
            type(receipt) is not dict
            or receipt.get("version") != VERSION
            or type(receipt.get("id")) is not str
            or not fullmatch(r"[a-f0-9]{64}", receipt["id"])
            or any(type(key) is not str for key in receipt)
        ):
            raise ValueError("Saved portfolio execution features changed")
        identity = receipt["id"]
        # Capture the same bounded canonical stream that is hashed. This avoids
        # validating one mutable tree and then copying a possibly changed tree.
        # JSON decoding detaches every nested object; StringIO closes on all paths.
        with StringIO() as captured:
            if (
                not _sealed_identity(receipt, MAX_BYTES, _capture=captured)
                or receipt["id"] != identity
            ):
                raise ValueError("Saved portfolio execution features changed")
            owned = json.loads(captured.getvalue())
        owned["id"] = identity
        self.__source, self.__receipt = receipt, owned

    def __receipt_for(self, source):
        if self.__receipt is None:
            raise ValueError("The prepared portfolio features have been released")
        if source is not self.__source:
            raise ValueError("Prepared portfolio features require the original source object")
        return self.__receipt

    def __identity(self, receipt, maximum):
        # This capability is available only for the exact private JSON tree that
        # construction detached and validated, never for a caller supplied dict.
        if self.__receipt is None or receipt is not self.__receipt or maximum != MAX_BYTES:
            raise ValueError("Prepared portfolio feature identity is unavailable")
        return True

    def attach(self, evidence):
        return _attach(evidence, self.__receipt_for(evidence.get("portfolio_execution_features")))

    def assess(self, report, source):
        from research.portfolio_constraint_evidence import _assess

        return _assess(report, self.__receipt_for(source), self.__identity)

    def close(self):
        self.__source = self.__receipt = None

    def __enter__(self):
        if self.__receipt is None:
            raise ValueError("The prepared portfolio features have been released")
        return self

    def __exit__(self, *_error):
        self.close()
