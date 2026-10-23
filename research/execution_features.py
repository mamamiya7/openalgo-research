"""Frozen causal ATR observations and a common opportunity mask, without prices.

Preparation reads the existing retained context once. Trial attachment validates
bounded derived metadata, never downloads or recomputes an indicator. The source
strategies and native timing contracts remain unchanged.
"""

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from io import StringIO
from math import isfinite
from re import fullmatch

from research import stock_indicators as indicators
from research.canonical_json import bounded_canonical_snapshot
from research.portfolio import fingerprint
from research.stock_features import _json_size

VERSION = "research-execution-features-v1"
ENTRY_VERSION = "research-entry-atr-v1"
MAX_BYTES = 16 * 1024**2
MAX_FEATURES = 25_000
MAX_SIGNALS = 25_000


def recipe():
    return {
        "version": VERSION,
        "dependency": {"package": "openalgo", "version": indicators.runtime_version()},
        "atr_period": 14,
        "observations": 100,
        "calculation": "Pinned native Wilder ATR14, restarted in100 prior consecutive observed daily OHLC bars",
        "availability": "Prior session dates only; recorded close available_at <= actual scheduled entry",
        "zero": "Zero, nonfinite or missing ATR is unavailable for every admitted clock candidate and percentage reference",
        "cohort": "A missing required ATR in any admitted native clock branch excludes that source opportunity in every candidate",
        "entry": "Stop and target distances use this completed ATR and anchor to the actual native entry fill",
    }


def _seal(value):
    return {**value, "id": fingerprint(value)}


def _identity(signal):
    return {key: signal[key] for key in ("symbol", "date", "timestamp", "row") if key in signal}


def _opportunity_keys(signals):
    seen = Counter()
    for signal in signals:
        identity = fingerprint(_identity(signal))
        key = f"{identity}:{seen[identity]}"
        seen[identity] += 1
        yield key, signal


def _schedule_days(snapshot):
    minute = snapshot["provenance"]["interval"] == "1m"
    grouped = {day: [] for day in snapshot["sessions"]}
    if minute:
        for slot in snapshot["timeline"]:
            grouped[slot[:10]].append(slot)
    return {
        day: {
            "hours": snapshot.get("session_hours", {}).get(day),
            "timeline_id": fingerprint(grouped[day]) if minute else None,
        }
        for day in snapshot["sessions"]
    }


def validate_entry_feature(value, *, symbol, entry_session, decision_at):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "version",
            "symbol",
            "entry_session",
            "atr_period",
            "value",
            "observations",
            "from",
            "to",
            "available_at",
            "basis_id",
            "recipe_id",
            "id",
        }
        or value.get("version") != ENTRY_VERSION
        or value.get("id") != fingerprint({key: item for key, item in value.items() if key != "id"})
        or value.get("symbol") != symbol
        or value.get("entry_session") != entry_session
        or value.get("atr_period") != 14
        or value.get("observations") != 100
        or isinstance(value.get("value"), bool)
        or not isinstance(value.get("value"), (int, float))
        or not isfinite(value["value"])
        or value["value"] <= 0
        or any(
            not isinstance(value.get(key), str) or not fullmatch(r"[0-9a-f]{64}", value[key])
            for key in ("basis_id", "recipe_id")
        )
    ):
        raise ValueError("Execution ATR needs an intact positive causal observation")
    try:
        available, decision = (
            datetime.fromisoformat(value["available_at"]),
            datetime.fromisoformat(decision_at),
        )
        if (
            available.tzinfo is None
            or decision.tzinfo is None
            or available > decision
            or not value["from"] <= value["to"] < entry_session
        ):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError("Execution ATR was not available before the scheduled entry") from error
    return value


def _intact(value):
    _json_size(value, MAX_BYTES)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"version", "recipe", "source", "features", "opportunities", "counts", "id"}
        or value.get("version") != VERSION
        or value.get("id") != fingerprint({key: item for key, item in value.items() if key != "id"})
        or not isinstance(value.get("features"), dict)
        or len(value["features"]) > MAX_FEATURES
        or not isinstance(value.get("opportunities"), dict)
        or any(not isinstance(rows, dict) for rows in value["opportunities"].values())
        or sum(len(rows) for rows in value["opportunities"].values()) > MAX_SIGNALS
        or value.get("recipe") != recipe()
    ):
        raise ValueError("Saved execution features changed or exceed their bounds")
    try:
        counts = {
            "opportunities": 0,
            "common_excluded": 0,
            "derived": len(value["features"]),
            "available": 0,
        }
        if (
            set(value["source"]) != {"context_id", "timing_id", "interval", "schedule_days"}
            or value["source"]["interval"] not in ("D", "1m")
            or not isinstance(value["source"]["schedule_days"], dict)
            or not 1 <= len(value["source"]["schedule_days"]) <= 3000
        ):
            raise ValueError
        if any(
            not isinstance(value["source"][key], str)
            or not fullmatch(r"[0-9a-f]{64}", value["source"][key])
            for key in ("context_id", "timing_id")
        ):
            raise ValueError
        for key, row in value["features"].items():
            if set(row) != {"feature", "reason"}:
                raise ValueError
            feature = row["feature"]
            if feature is None:
                if not isinstance(row["reason"], str) or not 1 <= len(row["reason"]) <= 500:
                    raise ValueError
            else:
                if (
                    row["reason"] is not None
                    or key != f"{feature['symbol']}|{feature['entry_session']}"
                    or feature["recipe_id"] != fingerprint(value["recipe"])
                ):
                    raise ValueError
                validate_entry_feature(
                    feature,
                    symbol=feature["symbol"],
                    entry_session=feature["entry_session"],
                    decision_at=feature["entry_session"] + "T23:59:00+05:30",
                )
                counts["available"] += 1
        for rows in value["opportunities"].values():
            for key, row in rows.items():
                if (
                    set(row) != {"signal", "original_exclusion", "required", "common_exclusion"}
                    or not key.startswith(fingerprint(row["signal"]) + ":")
                    or not isinstance(row["required"], list)
                    or len(row["required"]) > 4
                    or row["required"] != sorted(set(row["required"]))
                ):
                    raise ValueError
                missing = any(
                    value["features"][feature]["feature"] is None for feature in row["required"]
                )
                if bool(row["common_exclusion"]) != missing:
                    raise ValueError
                counts["opportunities"] += 1
                counts["common_excluded"] += missing
        if value["counts"] != counts:
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError("Saved execution feature bindings or common mask changed") from error
    return value


def _derive(bundle, symbol, day, specification):
    record = bundle["series"].get(symbol)
    if record is None:
        raise ValueError("Execution features do not include the source symbol")
    hours = record["calendar"]["session_hours"].get(day)
    decision = datetime.fromisoformat(f"{day}T{hours['open']}:00+05:30") if hours else None
    hypothesis = {"id": "execution_atr14", "family": "atr", "lookback": 14, "threshold": 0.0}
    observation, reason = (
        indicators._feature(record, decision, hypothesis)
        if decision
        else (None, "Entry session hours are unavailable")
    )
    if observation is not None and observation["atr"] <= 0:
        observation, reason = None, "Completed ATR14 is zero"
    feature = None
    if observation is not None:
        feature = _seal(
            {
                "version": ENTRY_VERSION,
                "symbol": symbol,
                "entry_session": day,
                "atr_period": 14,
                "value": observation["atr"],
                "observations": observation["observations"],
                "from": observation["from"],
                "to": observation["to"],
                "available_at": observation["available_at"],
                "basis_id": observation["basis_id"],
                "recipe_id": fingerprint(specification),
            }
        )
    return {"feature": feature, "reason": reason}


def prepare(evidence, bundle):
    from research.automatic_timing import _clock_config, descriptor
    from research.connectors.vectorbt_portfolio import _schedule
    from research.stock_market_context import validate_bundle

    existing = evidence.get("execution_features")
    if existing is not None:
        _intact(existing)
    validate_bundle(bundle)
    if existing is not None and evidence.get("frozen_prices") and evidence.get("replay_origin"):
        # A fixed chosen-setup replay intentionally changes settings and may
        # slice the execution period. Verify the original derived observations
        # against their retained source, rather than inventing a new clock union
        # from the selected settings or requiring the original full period.
        if existing["source"]["context_id"] != bundle["id"]:
            raise ValueError("Saved execution ATR belongs to another recorded context")
        for key, recorded in existing["features"].items():
            symbol, day = key.rsplit("|", 1)
            if _derive(bundle, symbol, day, existing["recipe"]) != recorded:
                raise ValueError(
                    "Saved execution ATR does not reproduce its original recorded context"
                )
        attach({**evidence, "automatic_stock_market_context": bundle})
        return {**evidence, "execution_features": existing}
    timing, spec = descriptor(evidence), recipe()
    snapshot = evidence["snapshot"]
    minute = snapshot["provenance"]["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    definitions = {row["id"]: row for row in timing["strategies"]}
    features, opportunities = {}, {}
    counts = {"opportunities": 0, "common_excluded": 0, "derived": 0, "available": 0}
    for strategy in evidence["strategies"]:
        item = definitions[strategy["id"]]
        configs = [_clock_config(strategy["config"], item, branch) for branch in item["branches"]]
        rows = opportunities.setdefault(strategy["id"], {})
        for key, signal in _opportunity_keys(strategy["signals"]):
            counts["opportunities"] += 1
            if counts["opportunities"] > MAX_SIGNALS:
                raise ValueError("Execution features exceed the source opportunity bound")
            required, missing = set(), None
            for cfg in configs:
                entry, excluded = _schedule(signal, snapshot, cfg, timeline, index, minute)
                if entry < 0 or excluded:
                    continue
                day = timeline[entry][:10]
                feature_key = f"{signal['symbol']}|{day}"
                required.add(feature_key)
                if feature_key not in features:
                    if len(features) >= MAX_FEATURES:
                        raise ValueError("Execution ATR observations exceed their bound")
                    features[feature_key] = _derive(bundle, signal["symbol"], day, spec)
                if features[feature_key]["feature"] is None and missing is None:
                    missing = f"Execution ATR unavailable: {signal['symbol']} for {day}: {features[feature_key]['reason']}"
            rows[key] = {
                "signal": _identity(signal),
                "original_exclusion": signal.get("research_exclusion"),
                "required": sorted(required),
                "common_exclusion": missing,
            }
            counts["common_excluded"] += missing is not None
    counts["derived"] = len(features)
    counts["available"] = sum(row["feature"] is not None for row in features.values())
    payload = {
        "version": VERSION,
        "recipe": spec,
        "source": {
            "context_id": bundle["id"],
            "timing_id": fingerprint(timing),
            "interval": snapshot["provenance"]["interval"],
            "schedule_days": _schedule_days(snapshot),
        },
        "features": features,
        "opportunities": opportunities,
        "counts": counts,
    }
    result = _intact(_seal(payload))
    if existing is not None and existing != result:
        raise ValueError("Saved execution ATR features do not reproduce their frozen source")
    return {**evidence, "execution_features": result}


def attach(evidence):
    return _attach(evidence, _intact(evidence.get("execution_features")))


def _attach(evidence, receipt):
    """Apply admitted features; all candidate bindings and entry checks remain fresh."""
    from research.connectors.vectorbt_portfolio import _schedule
    from research.intraday import bounds

    snapshot, source = evidence["snapshot"], receipt["source"]
    if source["interval"] != snapshot["provenance"]["interval"]:
        raise ValueError("Execution ATR interval differs from its frozen source")
    context = evidence.get("automatic_stock_market_context")
    if context is not None and context.get("id") != source["context_id"]:
        raise ValueError("Execution ATR belongs to another recorded context")
    if any(
        source["schedule_days"].get(day) != value for day, value in _schedule_days(snapshot).items()
    ):
        raise ValueError("Execution ATR calendar/timeline differs from its frozen source")
    minute = source["interval"] == "1m"
    timeline = snapshot["timeline" if minute else "sessions"]
    index = {key: i for i, key in enumerate(timeline)}
    strategies = []
    for strategy in evidence["strategies"]:
        original = receipt["opportunities"].get(strategy["id"])
        if original is None:
            raise ValueError("Execution ATR source strategy changed")
        signals = []
        for key, raw in _opportunity_keys(strategy["signals"]):
            saved = original.get(key)
            if saved is None or saved["signal"] != _identity(raw):
                raise ValueError("Execution ATR source opportunity changed")
            signal = dict(raw)
            signal.pop("execution_atr", None)
            reason = (
                signal.get("research_exclusion")
                or saved["original_exclusion"]
                or saved["common_exclusion"]
            )
            if reason:
                signal["research_exclusion"] = deepcopy(reason)
            else:
                entry, excluded = _schedule(
                    signal, snapshot, strategy["config"], timeline, index, minute
                )
                if entry >= 0 and not excluded:
                    day = timeline[entry][:10]
                    feature_key = f"{signal['symbol']}|{day}"
                    if feature_key not in saved["required"]:
                        raise ValueError("Execution ATR entry is outside its frozen clock branches")
                    feature = receipt["features"][feature_key]["feature"]
                    if feature is None:
                        raise ValueError("Execution ATR common mask is incomplete")
                    # Match the native daily adapter's long-standing bounds()
                    # convention when older daily snapshots omit clock metadata.
                    decision_at = (
                        timeline[entry] if minute else bounds(snapshot, day)[0].isoformat()
                    )
                    signal["execution_atr"] = deepcopy(
                        validate_entry_feature(
                            feature,
                            symbol=signal["symbol"],
                            entry_session=day,
                            decision_at=decision_at,
                        )
                    )
            signals.append(signal)
        strategies.append({**strategy, "signals": signals})
    return {**evidence, "strategies": strategies}


class _PreparedExecutionFeatures:
    """One private, fully validated receipt for one automatic research run.

    The original source object is an identity capability, never a mutable hash
    cache. Construction captures a detached bounded JSON tree before validating
    that same owned tree. Only whole-receipt checks are reused; candidate source,
    timing, opportunity and causal entry checks still run on every attachment.
    """

    __slots__ = ("__source", "__receipt", "__weakref__")

    def __init__(self, receipt):
        self.__source = self.__receipt = None
        captured = bounded_canonical_snapshot(receipt, max_bytes=MAX_BYTES)
        if captured is not None:
            owned, raw = captured
            del captured, raw
        else:
            # Optional conservative node/byte admission must not reject an
            # otherwise supported receipt. Preserve the original exact byte
            # limit while capturing its stream, then detach before validation.
            # Validate unusual input before JSON projection: converting a tuple
            # or custom container must never turn an invalid schema into an
            # admitted plain list/dict. Validate the captured tree again below
            # so a concurrent mutation cannot escape admission either.
            _intact(receipt)
            with StringIO() as stream:
                size = 0
                for part in json.JSONEncoder(
                    sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True,
                ).iterencode(receipt):
                    size += len(part)
                    if size > MAX_BYTES:
                        raise ValueError("Stock-feature evidence exceeds its bounded byte allowance")
                    stream.write(part)
                owned = json.loads(stream.getvalue())
        _intact(owned)
        self.__source, self.__receipt = receipt, owned

    def attach(self, evidence):
        if self.__receipt is None:
            raise ValueError("The prepared execution features have been released")
        if evidence.get("execution_features") is not self.__source:
            raise ValueError("Prepared execution features require the original source object")
        return _attach(evidence, self.__receipt)

    def close(self):
        self.__source = self.__receipt = None

    def __enter__(self):
        if self.__receipt is None:
            raise ValueError("The prepared execution features have been released")
        return self

    def __exit__(self, *error):
        self.close()
