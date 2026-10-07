"""Rule-driven price planning shared by intake, preflight and native acquisition.

The source is chosen by execution needs, never by a user's guess about candle size.
Only metadata is planned here; broker routing belongs to OpenAlgo's history service.
"""

import hashlib
import json
from datetime import datetime, timedelta

from research.data import MAX_BARS, NATIVE_CALENDAR_BASIS
from research.engine import validate_config
from research.experiments import axis_values, validate_search, validate_variants

VERSION = "research-price-requirements-v2"
SESSION_HOURS = {"2025-10-21": {"open": "13:45", "close": "14:45"}}
HOURS_SOURCE = "https://nsearchives.nseindia.com/content/circulars/CMTR70319.pdf"


def normalize_request(request=None):
    request = {} if request is None else request
    if not isinstance(request, dict) or set(request) - {"config", "kind", "specification"}:
        raise ValueError("Invalid backtest data requirements")
    config = validate_config(request.get("config", {}))
    kind = request.get("kind", "backtest")
    spec = request.get("specification", {})
    if kind not in {"backtest", "optimize", "research", "sensitivity"} or not isinstance(
        spec, dict
    ):
        raise ValueError("Invalid backtest data requirements")
    # Connectors use this same interval/window planner. Their selection is part
    # of the request identity, but never a numeric search axis.
    from research.connectors.registry import split_specification, validate_capabilities

    spec = validate_capabilities(kind, spec, config)
    _, planning_spec = split_specification(spec)
    # Calendar-dependent experiment validation runs once the reference is loaded.
    configs = [config]
    if kind in {"research", "sensitivity"}:
        for variant in validate_variants(spec.get("variants", []), config):
            configs.append(validate_config({**config, **variant["changes"]}))
    search = (
        planning_spec if kind == "optimize" else spec.get("search") if kind == "research" else None
    )
    if search is not None:
        search = validate_search(search, config)
        extremes = {key: max(axis_values(axis)) for key, axis in search["axes"].items()}
        selected_extreme = validate_config({**config, **extremes})
        configs.append(selected_extreme)
        if kind == "research":
            # Later sensitivity operates on the selected search configuration,
            # so cover the product of searched holding bounds and each variant.
            for variant in validate_variants(spec.get("variants", []), selected_extreme):
                configs.append(validate_config({**selected_extreme, **variant["changes"]}))
    return {"config": config, "kind": kind, "specification": spec}, configs


def requirement_key(request):
    canonical, _ = normalize_request(request)
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _intraday_reasons(signals, configs):
    reasons = []
    if any(signal.get("timestamp") for signal in signals):
        reasons.append("timestamped_signals")
    if any(
        cfg.get("trade_horizon") == "intraday"
        or cfg.get("hold_minutes") is not None
        or cfg.get("entry_time") is not None
        or cfg.get("exit_time") is not None
        for cfg in configs
    ):
        reasons.append("timed_execution")
    return reasons


def select_interval(signals, request=None):
    """Choose candle resolution from normalized signals and all requested setups.

    A dated scanner uses the daily evaluator's recorded OHLC policy, including
    stops, targets and trailing. Only observed timestamps or explicit intraday
    timing require minute candles; a multiday holding period alone does not.
    No reference data or broker access is needed to announce this choice at intake.
    """
    _, configs = normalize_request(request)
    return "1m" if _intraday_reasons(signals, configs) else "D"


def build_plan(signals, request, reference):
    canonical, configs = normalize_request(request)
    sessions = reference["sessions"]
    from research.connectors.registry import validate_input_scope

    validate_input_scope(signals, sessions, canonical["specification"])
    if not signals or len(signals) > 25000 or len(sessions) > 3000:
        raise ValueError("Data requirements exceed bounded signal/session limits")
    if not sessions:
        raise ValueError("No exchange sessions are available for these signals")
    reasons = _intraday_reasons(signals, configs)
    if not reasons:
        index = {day: i for i, day in enumerate(sessions)}
        required_days = {signal["symbol"]: set() for signal in signals}
        required_count = 0
        for signal in signals:
            observed_i = index.get(signal["date"])
            if observed_i is None:
                continue
            opening_i = observed_i + 1
            for cfg in configs:
                # The daily evaluator closes at the holding deadline's CLOSE.
                # Signal counts need the full calendar, but signal-day OHLC and
                # dates between separate holding windows cannot affect a trade.
                end_i = min(len(sessions) - 1, opening_i + cfg["hold_sessions"])
                existing = required_days[signal["symbol"]]
                added = set(sessions[opening_i : end_i + 1]) - existing
                required_count += len(added)
                if required_count > MAX_BARS:
                    raise ValueError(
                        "This setup needs more than two million daily prices; reduce the signal range or holding period"
                    )
                existing.update(added)
        return {
            "version": VERSION,
            "interval": "D",
            "request_key": requirement_key(canonical),
            "required_dates": {
                symbol: sorted(days) for symbol, days in sorted(required_days.items())
            },
        }
    native_calendar = reference.get("provenance", {}).get("calendar_basis") == NATIVE_CALENDAR_BASIS
    if not native_calendar and "2026-11-08" in sessions:
        raise ValueError(
            "The exchange has not yet supplied reviewed hours for the 2026 Muhurat session"
        )
    hours = (
        {}
        if native_calendar
        else {day: SESSION_HOURS[day] for day in sessions if day in SESSION_HOURS}
    )
    hours.update(reference.get("session_hours", {}))
    if native_calendar and any(day not in hours for day in sessions):
        raise ValueError("Native calendar must record hours for every exchange session")
    by_day = {}
    for day in sessions:
        opening = hours.get(day, {}).get("open", "09:15")
        closing = hours.get(day, {}).get("close", "15:30")
        cursor = datetime.fromisoformat(f"{day}T{opening}:00+05:30")
        end = datetime.fromisoformat(f"{day}T{closing}:00+05:30")
        stamps = []
        while cursor < end:
            stamps.append(cursor.isoformat())
            cursor += timedelta(minutes=1)
        by_day[day] = stamps
    index = {day: i for i, day in enumerate(sessions)}
    from research.intraday import entry_open

    temporal_reference = {"sessions": sessions, "session_hours": hours}
    required_days = {}
    required_count = 0
    for signal in signals:
        if signal["date"] not in index:
            continue
        for cfg in configs:
            entered = entry_open(signal, temporal_reference, cfg)
            if entered is None or (
                cfg.get("exit_time") and entered.strftime("%H:%M") >= cfg["exit_time"]
            ):
                continue
            opening_i = index[entered.date().isoformat()]
            if cfg.get("trade_horizon") == "intraday" or cfg.get("exit_time"):
                candidate_end = opening_i
            else:
                candidate_end = min(len(sessions) - 1, opening_i + cfg["hold_sessions"])
            if cfg.get("hold_minutes"):
                due = entered + timedelta(minutes=cfg["hold_minutes"])
                due_i = next(
                    (
                        i
                        for i in range(opening_i, len(sessions))
                        if by_day[sessions[i]][-1] >= due.isoformat()
                    ),
                    len(sessions) - 1,
                )
                candidate_end = min(candidate_end, due_i)
            existing = required_days.setdefault(signal["symbol"], set())
            added = set(sessions[opening_i : candidate_end + 1]) - existing
            required_count += sum(len(by_day[day]) for day in added)
            if required_count > MAX_BARS:
                raise ValueError(
                    "This setup needs more than two million minute prices; reduce the signal range or holding period"
                )
            existing.update(added)
    required = {
        symbol: [stamp for day in sorted(days) for stamp in by_day[day]]
        for symbol, days in sorted(required_days.items())
    }
    if not required:
        raise ValueError("No recorded exchange session follows these signals")
    if sum(map(len, required.values())) > MAX_BARS:
        raise ValueError(
            "This setup needs more than two million minute prices; reduce the signal range or holding period"
        )
    return {
        "version": VERSION,
        "request_key": requirement_key(canonical),
        "interval": "1m",
        "reasons": reasons,
        "timeline": [stamp for day in sessions for stamp in by_day[day]],
        "required_timestamps": required,
        "session_hours": hours,
        "session_hours_source": reference.get("session_hours_source", NATIVE_CALENDAR_BASIS)
        if native_calendar
        else HOURS_SOURCE,
    }


def covers_plan(snapshot, plan):
    """Frozen scope coverage, distinct from a broker's delivery success."""
    previous = snapshot.get("data_requirements", {})
    if (
        # V2 changes automatic selection, not the meaning of stored minute slots.
        # Keep a V1 scope reusable only when its interval still matches the request.
        previous.get("version") not in {"research-price-requirements-v1", VERSION}
        or snapshot.get("provenance", {}).get("interval") != plan["interval"]
    ):
        return False
    field = "required_timestamps" if plan["interval"] == "1m" else "required_dates"
    old = previous.get(field)
    if not isinstance(old, dict):
        return False
    return all(
        set(stamps) <= set(old.get(symbol, [])) for symbol, stamps in plan.get(field, {}).items()
    )
