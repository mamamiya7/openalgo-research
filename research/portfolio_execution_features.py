"""Bounded causal volume/correlation inputs for native portfolio limits.

These are historical simulation assumptions, not executable liquidity or a
prediction of future dependence. Native acquisition owns volume provenance.
"""

import hashlib
import json
from bisect import bisect_left
from copy import deepcopy
from datetime import datetime
from io import StringIO
from math import isfinite
from re import fullmatch

from research.canonical_json import bounded_canonical_bytes, bounded_canonical_fragments
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


def _bounded_sealed_identity(value, maximum, *, _capture=None):
    """Optional exact plain-JSON seal check; ``None`` retains the old path."""
    if (
        type(maximum) is not int
        or type(value) is not dict
        or type(value.get("id")) is not str
        or not fullmatch(r"[a-f0-9]{64}", value["id"])
        or any(type(key) is not str for key in value)
    ):
        return None
    payload = {key: item for key, item in value.items() if key != "id"}
    overhead = 71 + bool(payload)
    raw = bounded_canonical_bytes(
        payload, max_bytes=maximum - overhead, json_types_only=True, allow_native_float64=True
    )
    if raw is None:
        fragments = bounded_canonical_fragments(
            payload, max_bytes=maximum - overhead, allow_native_float64=True
        )
        if fragments is None:
            return None
        digest = hashlib.sha256()
        for fragment in fragments:
            digest.update(fragment)
            if _capture is not None:
                _capture.write(fragment.decode("ascii"))
        return digest.hexdigest() == value["id"]
    if _capture is not None:
        _capture.write(raw.decode("ascii"))
    return hashlib.sha256(raw).hexdigest() == value["id"]


def _sealed_identity(value, maximum, *, _capture=None):
    """Check a sealed receipt's exact byte bound and digest in one traversal.

    Canonical JSON differs from its payload only by the top-level id member:
    71 bytes, plus one comma for a nonempty payload. Nothing is retained across
    calls. Malformed/custom inputs and failures keep the original size-check
    path, including its error precedence and streaming early byte bound.
    """
    intact = _bounded_sealed_identity(value, maximum, _capture=_capture)
    if intact is not None:
        return intact
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
    from research.owned_entry_features import is_owned_entry

    admitted = is_owned_entry(value, version)
    intact = True if admitted else _bounded_sealed_identity(value, 64 * 1024)
    if intact is None:
        _json_size(value, 64 * 1024)
    if (
        not isinstance(value, dict)
        or value.get("version") != version
        or (
            not intact
            if intact is not None
            else value.get("id") != fingerprint({k: v for k, v in value.items() if k != "id"})
        )
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
    from research.owned_entry_features import is_owned_entry

    _base(value, CAPACITY_VERSION, symbol, entry_session, decision_at)
    if not is_owned_entry(value, CAPACITY_VERSION) and (
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
    from research.owned_entry_features import is_owned_entry

    _base(value, CORRELATION_VERSION, symbol, entry_session, decision_at)
    correlations = value.get("correlations")
    if not is_owned_entry(value, CORRELATION_VERSION) and (
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


class _ReturnWindows:
    """One symbol's bounded overlapping windows, owned by one preparation.

    Every close, availability timestamp and canonical bar is read once here.
    Each window still has its own exact sixty returns, causal availability check
    and complete 61-bar digest. Joining canonical object members reproduces the
    original sorted JSON bytes; no cross-call identity or validation is cached.
    Unusual non-plain JSON bars retain the original fingerprint path.
    """

    __slots__ = ("record", "sessions", "available", "closes", "returns", "members")

    def __init__(self, record):
        self.record = record
        self.sessions = tuple(record["calendar"]["sessions"])
        self.available, self.closes, self.returns, self.members = [], [], [], []
        series = record["evidence"]
        for day in self.sessions:
            bar = series["bars"].get(day)
            close = bar["close"] if bar is not None else None
            valid = type(close) in (int, float) and isfinite(close) and close > 0
            self.closes.append(close if valid else None)
            self.available.append(
                datetime.fromisoformat(series["available_at"][day]) if bar is not None else None
            )
            # One bounded object member, including the exact original escaped
            # date key. A failed plain-JSON encoding keeps the legacy fallback.
            encoded = (
                bounded_canonical_bytes(
                    {day: bar},
                    max_bytes=MAX_BYTES,
                    json_types_only=True,
                    allow_native_float64=True,
                )
                if bar is not None
                else None
            )
            self.members.append(encoded[1:-1] if encoded is not None else None)
            previous = self.closes[-2] if len(self.closes) > 1 else None
            value = close / previous - 1 if valid and previous is not None else None
            self.returns.append(value if value is not None and isfinite(value) else None)

    def window(self, day):
        end = bisect_left(self.sessions, day)
        if end < 61:
            return None
        start = end - 61
        earlier = list(self.sessions[start:end])
        hours = self.record["calendar"]["session_hours"].get(day)
        if not hours or any(value is None for value in self.closes[start:end]):
            return None
        decision = datetime.fromisoformat(day + "T" + hours["open"] + ":00+05:30")
        if any(value > decision for value in self.available[start:end]):
            return None
        values = self.returns[start + 1 : end]
        if any(value is None for value in values):
            return None
        members = self.members[start:end]
        identity = (
            hashlib.sha256(b"{" + b",".join(members) + b"}").hexdigest()
            if all(member is not None for member in members)
            else fingerprint({d: self.record["evidence"]["bars"][d] for d in earlier})
        )
        return {
            "dates": earlier,
            "returns": values,
            "available_at": max(self.record["evidence"]["available_at"][d] for d in earlier),
            "source_id": identity,
        }


def _correlation_inputs(prepared):
    """Share identical day-local inputs without changing ordered pair values."""
    import numpy as np

    arrays = {
        symbol: np.asarray(candidate["returns"])
        for symbol, candidate in prepared.items()
        if candidate is not None
    }
    varying = {symbol: np.std(values) > 1e-14 for symbol, values in arrays.items()}
    identities = {
        symbol: candidate["source_id"] if candidate is not None else None
        for symbol, candidate in prepared.items()
    }
    return (
        arrays,
        varying,
        fingerprint(identities),
        max(
            (candidate["available_at"] for candidate in prepared.values() if candidate),
            default=None,
        ),
    )


def _correlation(symbol, day, series, prepared, *, _inputs=None, _pairs=None):
    import numpy as np

    own = prepared.get(symbol)
    if own is None:
        return None
    arrays, varying, source_id, available_at = (
        _correlation_inputs(prepared) if _inputs is None else _inputs
    )
    values = {}
    for other, candidate in prepared.items():
        rho = None
        if other == symbol:
            rho = 1.0  # Same-instrument exposure is always counted together.
        elif candidate is not None and candidate["dates"] == own["dates"]:
            if varying[symbol] and varying[other]:
                if _pairs is not None and (symbol, other) in _pairs:
                    rho = _pairs[symbol, other]
                else:
                    pair = np.corrcoef(arrays[symbol], arrays[other])
                    rho = float(np.clip(pair[0, 1], -1, 1))
                    if _pairs is not None:
                        _pairs[symbol, other] = rho
                        # Retain the independently normalized reverse element;
                        # correlation symmetry can differ by a floating bit.
                        _pairs[other, symbol] = float(np.clip(pair[1, 0], -1, 1))
        values[other] = rho
    return _seal(
        {
            "version": CORRELATION_VERSION,
            "symbol": symbol,
            "entry_session": day,
            "from": own["dates"][0],
            "to": own["dates"][-1],
            "observations": 60,
            "available_at": available_at,
            "source_id": source_id,
            "correlations": values,
        }
    )


def _batched_correlation_pairs(prepared, inputs, requested):
    """Batch independent 2-by-60 NumPy covariances, retaining ordered bits.

    A single universe-wide corrcoef changes BLAS reduction shapes and can change
    saved values. Each batch member instead uses the same two-row covariance,
    scaling and two ordered divisions as NumPy 2.4's corrcoef/cov. Both directed
    elements are retained, just like the existing day-local pair cache. Temporary
    arrays hold at most 128 pairs; results live only for the current entry date.
    """
    import numpy as np

    arrays, varying, _, _ = inputs
    if len(prepared) * len(requested) > MAX_PAIRS:
        raise ValueError("Correlation batch exceeds the bounded symbol/date allowance")
    pairs, pending = {}, []

    def flush():
        if not pending:
            return
        values = np.array([[arrays[a], arrays[b]] for a, b in pending], dtype=np.float64)
        values -= np.average(values, axis=2)[:, :, None]
        covariance = np.matmul(values, values.swapaxes(1, 2))
        covariance *= np.true_divide(1, values.shape[2] - 1)
        deviation = np.sqrt(np.diagonal(covariance, axis1=1, axis2=2))
        covariance /= deviation[:, :, None]
        covariance /= deviation[:, None, :]
        np.clip(covariance, -1, 1, out=covariance)
        for (a, b), pair in zip(pending, covariance, strict=True):
            pairs[a, b], pairs[b, a] = float(pair[0, 1]), float(pair[1, 0])
        pending.clear()

    for symbol in requested:
        own = prepared.get(symbol)
        if own is None or not varying[symbol]:
            continue
        for other, candidate in prepared.items():
            if (
                other == symbol
                or candidate is None
                or not varying[other]
                or candidate["dates"] != own["dates"]
                or (symbol, other) in pairs
            ):
                continue
            # Nonstandard standalone callers retain NumPy's original promotion.
            if any(
                arrays[key].dtype.kind not in "iuf" or arrays[key].dtype.itemsize > 8
                for key in (symbol, other)
            ):
                pair = np.corrcoef(arrays[symbol], arrays[other])
                pairs[symbol, other] = float(np.clip(pair[0, 1], -1, 1))
                pairs[other, symbol] = float(np.clip(pair[1, 0], -1, 1))
                continue
            pairs[symbol, other] = pairs[other, symbol] = None
            pending.append((symbol, other))
            if len(pending) == 128:
                flush()
    flush()
    return pairs


def prepare(evidence, bundle, volume_records=None):
    """Prepare once for every admitted clock; reuse the exact frozen receipt."""
    from research.stock_market_context import validate_bundle
    from research.volume_provenance import _ObservedVolume, assess_observed

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
    observed = _ObservedVolume.admit(volume_records)
    allocation_on = any(
        row.get("execution_policy", {}).get("version") == "research-execution-aware-v5"
        for row in evidence["strategies"]
    )
    allocations = {}
    correlations, capacity = {}, {}
    windows = (
        {symbol: _ReturnWindows(record) for symbol, record in bundle["series"].items()}
        if required
        else {}
    )
    day_cache = {}
    correlation_inputs = None
    pairs = {}
    required_symbols = {}
    for key in required:
        symbol, day = key.rsplit("|", 1)
        required_symbols.setdefault(day, []).append(symbol)
    for key in required:
        symbol, day = key.rsplit("|", 1)
        if day not in day_cache:
            day_cache = {day: {symbol: value.window(day) for symbol, value in windows.items()}}
            correlation_inputs = _correlation_inputs(day_cache[day])
            pairs = _batched_correlation_pairs(
                day_cache[day], correlation_inputs, required_symbols[day]
            )
        if allocation_on and day not in allocations:
            from research.portfolio_allocation import build

            allocations[day] = build(day_cache[day], day)
        feature = _correlation(
            symbol, day, bundle["series"], day_cache[day], _inputs=correlation_inputs, _pairs=pairs
        )
        correlations[key] = feature
        record = bundle["series"][symbol]
        hours = record["calendar"]["session_hours"].get(day)
        decision = day + "T" + hours["open"] + ":00+05:30" if hours else day
        calendar = windows[symbol].sessions
        end = bisect_left(calendar, day)
        prior = calendar[max(0, end - 20) : end]
        if observed is not None:
            model = observed.assess(
                prior, record["evidence"]["bars"], record["calendar"], symbol, day, decision
            )
        else:
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
    if observed is not None:
        volume_records = observed.release()
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


def _attach(evidence, receipt, *, own_entries=False, identities=None, daily=None):
    """Check current bindings and effective modes before immutable daily reuse."""
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
    decisions = {}
    for strategy in evidence["strategies"]:
        signals = []
        policy = strategy.get("execution_policy", {})
        modes = tuple(
            policy.get(key) for key in ("capacity_mode", "correlation_mode", "allocation_mode")
        )
        cache_key = daily.key(snapshot, strategy, _schedule, modes) if daily is not None else None
        reused = daily.get(cache_key, snapshot, strategy["signals"]) if daily is not None else None
        if reused is not None:
            strategies.append({**strategy, "signals": reused})
            continue
        for opportunity, original in _opportunity_keys(strategy["signals"], identities):
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
                if not minute and day not in decisions:
                    decisions[day] = bounds(snapshot, day)[0].isoformat()
                decision = timeline[entry] if minute else decisions[day]
                if policy.get("capacity_mode") == "prior20_broker_reported":
                    feature = receipt["capacity"].get(key, {}).get("feature")
                    if feature is None:
                        signal["research_exclusion"] = (
                            "capacity_unavailable: 20 completed broker-volume sessions required"
                        )
                    else:
                        if own_entries:
                            from research.owned_entry_features import admit_entry

                            feature = admit_entry(feature, CAPACITY_VERSION)
                            receipt["capacity"][key]["feature"] = feature
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
                        if own_entries:
                            from research.owned_entry_features import admit_entry

                            feature = admit_entry(feature, CORRELATION_VERSION)
                            receipt["correlation"][key] = feature
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
                        if own_entries:
                            from research.owned_entry_features import admit_entry
                            from research.portfolio_allocation import VERSION as ALLOCATION_VERSION

                            feature = admit_entry(feature, ALLOCATION_VERSION)
                            receipt["allocation"][day] = feature
                        signal["entry_allocation"] = deepcopy(
                            validate(
                                feature,
                                symbol=signal["symbol"],
                                entry_session=day,
                                decision_at=decision,
                            )
                        )
            signals.append(signal)
        if daily is not None:
            signals = daily.put(cache_key, snapshot, strategy["signals"], signals)
        strategies.append({**strategy, "signals": signals})
    return {**evidence, "strategies": strategies}


class _PreparedPortfolioFeatures:
    """One bounded, detached sealed receipt owned by one automatic run.

    The caller's receipt is an identity capability, never a cache key. Only the
    full immutable-source seal is reused. Identical immutable daily lists may
    share checked entry attachments for the same effective modes. Actual native
    schedules, fills and portfolio constraints still run for every account.
    The privately owned JSON projection is never returned to callers.
    Closing the owner releases both source and projection, including on errors.
    """

    __slots__ = ("__source", "__receipt", "__identities", "__daily", "__weakref__")

    def __init__(self, receipt):
        self.__source = self.__receipt = None
        self.__identities = {}
        from research.prepared_daily_signals import DailySignals

        self.__daily = DailySignals()
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
        return _attach(
            evidence,
            self.__receipt_for(evidence.get("portfolio_execution_features")),
            own_entries=True,
            identities=self.__identities,
            daily=self.__daily,
        )

    def assess(self, report, source):
        from research.native_report_ownership import take_native_report
        from research.portfolio_constraint_evidence import _assess

        return _assess(
            report,
            self.__receipt_for(source),
            self.__identity,
            _owned_report=take_native_report(report),
        )

    def close(self):
        self.__source = self.__receipt = None
        self.__identities.clear()
        self.__daily.close()

    def __enter__(self):
        if self.__receipt is None:
            raise ValueError("The prepared portfolio features have been released")
        return self

    def __exit__(self, *_error):
        self.close()
