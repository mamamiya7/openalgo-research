"""Pure, bounded expanding-fit chronology compiled before any account outcomes.

The protocol owns dates and budgets, not slicing, fitting, simulation or storage.
Every fit can use its expanding training prefix only. Later test scores describe
the frozen fit procedure; they cannot select settings inside that fit.
"""

import json
from collections import Counter
from datetime import date, datetime
from math import ceil, isfinite

from research.report_contract import fingerprint

VERSION = "research-walk-forward-protocol-v1"
MAX_METADATA_BYTES = 64 * 1024
MAX_RECORDED_SESSIONS = 10000
MAX_SIGNAL_SESSIONS = 3000
MAX_SIGNALS = 25000
MAX_TIMELINE = 2_000_000
FOLDS = 3


def _bounded(value):
    size = 0
    try:
        for chunk in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value):
            size += len(chunk.encode())
            if size > MAX_METADATA_BYTES:
                raise ValueError("Walk-forward protocol metadata limit reached")
    except (TypeError, OverflowError) as error:
        raise ValueError("Walk-forward protocol needs canonical JSON metadata") from error


def _day(value):
    if not isinstance(value, str):
        raise ValueError("Walk-forward protocol needs recorded ISO session dates")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Walk-forward protocol needs recorded ISO session dates") from error
    if parsed.isoformat() != value:
        raise ValueError("Walk-forward protocol needs recorded ISO session dates")
    return value


def _positive(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError("Walk-forward holding limits must be finite and positive")
    return value


def _maximum(row, key):
    config, search = row.get("config"), row.get("search", {})
    if not isinstance(config, dict) or not isinstance(search, dict):
        raise ValueError("Walk-forward protocol needs canonical strategy settings")
    domain = search.get(key)
    if domain is not None and not isinstance(domain, dict):
        raise ValueError("Walk-forward protocol needs declared holding domains")
    baseline = _positive(config.get(key))
    # The original account remains a comparator even if a supplied search domain
    # does not contain its original deadline.
    return max(baseline, _positive(domain.get("max"))) if domain else baseline


def _calendar(evidence):
    snapshot = evidence.get("snapshot", {})
    if not isinstance(snapshot, dict):
        raise ValueError("Walk-forward protocol needs its frozen calendar")
    recorded = snapshot.get("sessions")
    if not isinstance(recorded, list) or not 1 <= len(recorded) <= MAX_RECORDED_SESSIONS:
        raise ValueError("Walk-forward recorded calendar limit reached")
    previous = None
    for value in recorded:
        day = _day(value)
        if previous is not None and previous >= day:
            raise ValueError("Walk-forward recorded sessions must be unique and ordered")
        previous = day
    signals = evidence.get("signals")
    if not isinstance(signals, list) or not 1 <= len(signals) <= MAX_SIGNALS:
        raise ValueError("Walk-forward protocol needs bounded dated signals")
    try:
        signal_dates = [_day(row["date"]) for row in signals]
    except (KeyError, TypeError) as error:
        raise ValueError("Walk-forward protocol needs dated scanner signals") from error
    first, last = min(signal_dates), max(signal_dates)
    sessions = [day for day in recorded if first <= day <= last]
    if not 1 <= len(sessions) <= MAX_SIGNAL_SESSIONS:
        raise ValueError("Walk-forward signal calendar limit reached")
    provenance = snapshot.get("provenance", {})
    interval = provenance.get("interval") if isinstance(provenance, dict) else None
    if interval not in ("D", "1m"):
        raise ValueError("Walk-forward protocol needs daily or minute native prices")
    return snapshot, sessions, interval


def _holding(portfolio, snapshot, sessions, interval):
    rows = portfolio.get("strategies")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise ValueError("Walk-forward strategy limit reached")
    observed_minutes = None
    if interval == "1m":
        timeline = snapshot.get("timeline")
        if not isinstance(timeline, list) or not 1 <= len(timeline) <= MAX_TIMELINE:
            raise ValueError("Walk-forward protocol needs bounded recorded minute observations")
        calendar = set(snapshot["sessions"])
        counts = Counter()
        previous = None
        for value in timeline:
            if not isinstance(value, str):
                raise ValueError("Walk-forward minute observations need aware timestamps")
            try:
                stamp = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError(
                    "Walk-forward minute observations need aware timestamps"
                ) from error
            if stamp.tzinfo is None or stamp.utcoffset() is None or value[:10] not in calendar:
                raise ValueError("Walk-forward minute observations need recorded session dates")
            if previous is not None and stamp <= previous:
                raise ValueError("Walk-forward minute observations must be unique and ordered")
            previous = stamp
            counts[value[:10]] += 1
        # A signal received after close can first execute next session. The
        # native timing union then legitimately has no execution prices on that
        # signal's own calendar date. Count actual observed sessions only; never
        # invent bars for a zero-observation day. Native clock/cohort slicing
        # separately verifies every required entry and complete deadline.
        observed_minutes = min(counts.values())
    longest = 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("config"), dict):
            raise ValueError("Walk-forward protocol needs canonical strategy settings")
        cfg = row["config"]
        if interval == "D" or (
            cfg.get("hold_minutes") is None and cfg.get("trade_horizon") != "intraday"
        ):
            maximum = ceil(_maximum(row, "hold_sessions"))
        elif cfg.get("hold_minutes") is not None:
            maximum = ceil(_maximum(row, "hold_minutes") / observed_minutes)
        else:
            maximum = 1  # Session-close intraday deadline; no invented session length.
        longest = max(longest, maximum)
    return {
        "max_sessions": longest,
        # Next-session daily entry and the maximum hold must leave five
        # complete signal dates. Minute clocks may need less; exact native
        # clock slicing remains authoritative after this conservative admission.
        "min_window_sessions": max(15, longest + 6),
        "minimum_observed_minutes_per_session": observed_minutes,
        "purge": "Every permitted maximum holding deadline must end within its own period",
    }


def _period(sessions, start, stop):
    return {"from": sessions[start], "to": sessions[stop - 1], "sessions": stop - start}


def build(evidence):
    """Seal three expanding-fit folds plus a final development-only refit.

    No bar, return, trial, ledger or report value is read. Daily and minute inputs
    share the recorded session calendar; minute counts only bound holding needs.
    The three test blocks never overlap. Fit prefixes intentionally overlap and
    include earlier test dates only when those dates have become past history.
    """
    if not isinstance(evidence, dict) or not isinstance(evidence.get("portfolio"), dict):
        raise ValueError("Walk-forward protocol needs its frozen portfolio")
    portfolio = evidence["portfolio"]
    _bounded(portfolio)
    snapshot, sessions, interval = _calendar(evidence)
    holding = _holding(portfolio, snapshot, sessions, interval)
    count = len(sessions)
    dev_end = count * 80 // 100
    initial_end = dev_end // 2
    cuts = [initial_end + (dev_end - initial_end) * i // FOLDS for i in range(FOLDS + 1)]
    floor = holding["min_window_sessions"]
    ready = (
        initial_end >= floor
        and count - dev_end >= floor
        and all(b - a >= floor for a, b in zip(cuts, cuts[1:], strict=False))
    )
    payload = {
        "version": VERSION,
        "status": "ready" if ready else "insufficient_history",
        "interval": interval,
        "calendar": {
            "session_count": count,
            "date_from": sessions[0],
            "date_to": sessions[-1],
            "session_id": fingerprint(sessions),
        },
        "settings_id": fingerprint(portfolio),
        "holding": holding,
        "periods": {},
        "folds": [],
        "final_fit": None,
        "budget": {
            "fits": 4,
            "proposals_per_fit": 48,
            "family_screening_per_fit": 32,
            "screening_proposals_per_fit": 32,
            "refinement_proposals_per_fit": 16,
            "max_clock_probes_per_fit": 8,
            "proposals": 192,
            "total_proposals": 192,
            "max_nominal_simulations": 256,
            "nonproposal_simulation_reserve": 64,
            "final_reserved_calls": 2,
            "reconstruction_reserved_calls": 1,
            "retry_reservations": 64,
            "max_simulations": 320,
        },
        "policies": {
            "fit": "Expanding historical prefixes; fit settings only on their own prefix",
            "test": "Freeze each fit selection before its next non-overlapping test window",
            "optimizer": "Test and final scores are never told to Optuna or used to select within a fit",
            "final_fit": "Refit the fixed procedure on all development dates; exclude final dates",
            "final": "Evaluate the final frozen selection once; no final-result reranking",
            "account": "Fresh identical capital and fixed risk/cost assumptions for each account; no carried positions",
            "aggregation": "Require every fold's ordinary and higher-cost check; show separate results; do not pool or compound reset-account returns",
            "transform": "Registered causal formulas and fixed domains only; no learned-transform claim",
            "purge": holding["purge"],
        },
    }
    available_dates = {
        row["date"] for row in evidence["signals"] if not row.get("research_exclusion")
    }
    payload["calendar"]["signal_dates_id"] = fingerprint(sorted(available_dates))
    if ready:
        for index, (start, stop) in enumerate(zip(cuts, cuts[1:], strict=False), 1):
            fit_name, test_name = f"fit{index}", f"check{index}"
            fit, test = _period(sessions, 0, start), _period(sessions, start, stop)
            payload["periods"][fit_name] = fit
            payload["periods"][test_name] = test
            payload["folds"].append(
                {
                    "id": f"fold{index}",
                    "fit_period": fit_name,
                    "test_period": test_name,
                    "fit": fit,
                    "test": test,
                }
            )
        fit, test = _period(sessions, 0, dev_end), _period(sessions, dev_end, count)
        payload["periods"].update(search=fit, final=test)
        payload["final_fit"] = {
            "fit_period": "search",
            "test_period": "final",
            "fit": fit,
            "test": test,
        }
        support = {
            name: sum(bounds["from"] <= day <= bounds["to"] for day in available_dates)
            for name, bounds in payload["periods"].items()
        }
        payload["signal_date_support"] = support
        complete_support = None
        if interval == "D":
            complete_support = {}
            for name, bounds in payload["periods"].items():
                period_dates = [day for day in sessions if bounds["from"] <= day <= bounds["to"]]
                # No price or realised exit is read: every permitted deadline
                # must fit wholly inside this reset-capital account period.
                eligible_dates = set(
                    period_dates[: max(0, len(period_dates) - holding["max_sessions"] - 1)]
                )
                complete_support[name] = len(available_dates.intersection(eligible_dates))
            payload["complete_signal_date_support"] = complete_support
        insufficient_complete = complete_support is not None and (
            complete_support["fit1"] < 20
            or any(complete_support[name] < 5 for name in ("check1", "check2", "check3", "final"))
        )
        if (
            insufficient_complete
            or support["fit1"] < 20
            or any(support[name] < 5 for name in ("check1", "check2", "check3", "final"))
        ):
            payload.update(
                status="insufficient_history",
                reason_code="sparse_dates",
                reason=(
                    "Expanding validation needs at least 20 distinct signal dates in the "
                    "initial fit and five in every later test and final window, with complete "
                    "maximum holding deadlines where daily timing is known. "
                    "Use signals spread across more dates."
                ),
                periods={},
                folds=[],
                final_fit=None,
            )
    else:
        payload["reason_code"] = "short_history"
        payload["reason"] = (
            f"Expanding validation needs three later development windows and a final window "
            f"of at least {floor} recorded sessions each; this history has {count} sessions. "
            "Use longer signal history or shorten the allowed holding period."
        )
        # With a 40% initial fit, each later development block is at most 40%/3
        # of the total. Integer cuts can need one more session than this bound.
        payload["minimum_session_lower_bound"] = ceil(floor * 15 / 2)
    _bounded(payload)
    return {**payload, "id": fingerprint(payload)}


def verify(plan, *, evidence):
    """Reject edited plans or changed settings/calendar without reading outcomes."""
    if not isinstance(plan, dict) or plan.get("version") != VERSION:
        raise ValueError("Saved walk-forward protocol version is unsupported")
    _bounded(plan)
    if plan != build(evidence):
        raise ValueError("Saved walk-forward protocol differs from frozen settings or calendar")
    return True
