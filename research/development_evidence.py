"""Bounded, pure retention contract for actual development account observations.

These records do not choose candidates or change engine calculations. The final
reserved period is deliberately absent from the development corpus. No curve is
resampled and no return is invented when the preceding account equity is zero.
All limits and intermediate state are local to a call; persistence belongs to the
service that owns the immutable inputs and artifact storage.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import date, datetime

from research.evaluation_basis import IDENTITIES
from research.evaluation_basis import VERSION as BASIS_VERSION
from research.report_contract import fingerprint, settings_identity

VERSION = "research-development-evidence-v1"
MAX_EVALUATIONS = 320
EXECUTION_MAX_EVALUATIONS = 384
MAX_MARKS_PER_EVALUATION = 100_000
MAX_TOTAL_MARKS = 4_000_000
MAX_SHARD_BYTES = 24 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
# Every accepted mark has bounded timestamps, IEEE-754 numbers and counts. This
# deliberately exceeds its largest canonical JSON representation, including the
# timeline key and return. Metadata is separately admitted below.
ESTIMATED_BYTES_PER_MARK = 640
MAX_METADATA_BYTES = 64 * 1024
RETURN_POLICY = "simple-account-return-from-initial-capital;zero-denominator-unavailable"


def _raw_size(value):
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Development evidence {label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Development evidence {label} must be finite") from error
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(
            f"Development evidence {label} must be finite and {'positive' if positive else 'nonnegative'}"
        )
    return result


def _timeline(values, interval):
    if not isinstance(values, list) or not values:
        raise ValueError("Development evidence needs its exact recorded timeline")
    if len(values) > MAX_MARKS_PER_EVALUATION:
        raise ValueError("Development evidence observation limit reached")
    if interval not in ("D", "1m"):
        raise ValueError("Development evidence interval is unsupported")
    previous = None
    for value in values:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("Development evidence has an invalid observation key")
        try:
            if interval == "D":
                current = date.fromisoformat(value)
                if current.isoformat() != value:
                    raise ValueError("Noncanonical day")
            else:
                current = datetime.fromisoformat(value)
                if current.tzinfo is None or current.utcoffset() is None:
                    raise ValueError("Minute observations require an offset")
                if len(value) < 19 or "T" not in value:
                    raise ValueError("Minute observations require an exact timestamp")
        except ValueError as error:
            raise ValueError("Development evidence has an invalid observation key") from error
        if previous is not None and current <= previous:
            raise ValueError("Development evidence timeline is duplicated or reordered")
        previous = current
    return list(values)


def plan(evidence, max_evaluations):
    """Admit conservative retention before scoring, without blocking calculation.

    A worst-case candidate may consume any development period's complete union
    timeline. Repeated proposals do not justify predicting a smaller corpus. A
    huge or incomplete input returns ``unavailable`` rather than guessing marks.
    The recipe is already compiled by the numerical caller; this helper does not
    compile or alter a recipe and never examines the final-period outcomes.
    """
    portfolio = evidence.get("portfolio") if isinstance(evidence, dict) else None
    automatic = portfolio.get("automatic_research") if isinstance(portfolio, dict) else None
    maximum = (
        512
        if isinstance(automatic, dict)
        and automatic.get("version") in ("automatic-hypotheses-v15", "automatic-hypotheses-v16")
        else EXECUTION_MAX_EVALUATIONS
        if isinstance(automatic, dict)
        and automatic.get("version")
        in (
            "automatic-hypotheses-v10",
            "automatic-hypotheses-v11",
            "automatic-hypotheses-v12",
            "automatic-hypotheses-v13",
            "automatic-hypotheses-v14",
            "automatic-hypotheses-v15",
            "automatic-hypotheses-v16",
        )
        else MAX_EVALUATIONS
    )
    limits = {
        "max_evaluations": maximum,
        "max_marks_per_evaluation": MAX_MARKS_PER_EVALUATION,
        "max_total_marks": MAX_TOTAL_MARKS,
        "max_shard_bytes": MAX_SHARD_BYTES,
        "max_total_bytes": MAX_TOTAL_BYTES,
    }
    result = {"version": VERSION, "status": "unavailable", "limits": limits}
    if (
        isinstance(max_evaluations, bool)
        or not isinstance(max_evaluations, int)
        or not 1 <= max_evaluations <= maximum
    ):
        return {**result, "reason": "evaluation_limit"}
    try:
        snapshot = evidence["snapshot"]
        interval = snapshot["provenance"]["interval"]
        # Validate the full ordering even when only a subset is retained. The
        # full snapshot may exceed a per-evaluation cap: period counts decide.
        raw_timeline = snapshot["timeline" if interval == "1m" else "sessions"]
        if not isinstance(raw_timeline, list) or not raw_timeline or interval not in ("D", "1m"):
            raise ValueError("Missing timeline")
        if len(raw_timeline) > MAX_TOTAL_MARKS:
            return {**result, "reason": "observation_limit"}
        # Validation in bounded chunks does not accidentally admit cross-chunk
        # duplicates or reorderings, including offset-equivalent timestamps.
        for start in range(0, len(raw_timeline), MAX_MARKS_PER_EVALUATION):
            chunk = raw_timeline[max(0, start - 1) : start + MAX_MARKS_PER_EVALUATION]
            if len(chunk) > MAX_MARKS_PER_EVALUATION:
                _timeline(chunk[:-1], interval)
                _timeline(chunk[-2:], interval)
            else:
                _timeline(chunk, interval)
        recipe = evidence.get("automatic_recipe", {})
        if not isinstance(recipe, dict):
            raise ValueError("Invalid recipe")
        periods = recipe.get("periods", {})
        if not isinstance(periods, dict) or len(periods) > 16:
            raise ValueError("Invalid periods")
        counts = {}
        for name, period in periods.items():
            if name == "final":
                continue
            if (
                not isinstance(name, str)
                or not name
                or len(name) > 64
                or not isinstance(period, dict)
            ):
                raise ValueError("Invalid period")
            first, last = period["from"], period["to"]
            if not isinstance(first, str) or not isinstance(last, str) or first > last:
                raise ValueError("Invalid period dates")
            if (
                date.fromisoformat(first).isoformat() != first
                or date.fromisoformat(last).isoformat() != last
            ):
                raise ValueError("Invalid period dates")
            # Recipe bounds are session dates. Exact union timestamps stay intact.
            counts[name] = sum(first <= key[:10] <= last for key in raw_timeline)
        if not periods:
            counts["full"] = len(raw_timeline)
        if not counts or not all(counts.values()):
            raise ValueError("Empty development period")
    except (AttributeError, KeyError, TypeError, ValueError):
        return {**result, "reason": "unverified_timeline"}
    marks = max(counts.values())
    total_marks = marks * max_evaluations
    shard_bytes = MAX_METADATA_BYTES + marks * ESTIMATED_BYTES_PER_MARK
    total_bytes = shard_bytes * max_evaluations
    result.update(
        max_evaluations=max_evaluations,
        per_period_counts=counts,
        estimated_marks_per_evaluation=marks,
        estimated_total_marks=total_marks,
        estimated_shard_bytes=shard_bytes,
        estimated_total_bytes=total_bytes,
        reserved_final_excluded=True,
    )
    reason = next(
        (
            reason
            for failed, reason in (
                (marks > MAX_MARKS_PER_EVALUATION, "observation_limit"),
                (total_marks > MAX_TOTAL_MARKS, "total_observation_limit"),
                (shard_bytes > MAX_SHARD_BYTES, "shard_byte_limit"),
                (total_bytes > MAX_TOTAL_BYTES, "total_byte_limit"),
            )
            if failed
        ),
        None,
    )
    return {
        **result,
        "status": "unavailable" if reason else "available",
        **({"reason": reason} if reason else {}),
    }


def _basis(value, timeline):
    if (
        not isinstance(value, dict)
        or value.get("version") != BASIS_VERSION
        or value.get("status") != "verified"
    ):
        raise ValueError("Development evidence needs a verified evaluation basis")
    if any(
        not isinstance(value.get(key), str) or not value[key]
        for key in (*IDENTITIES, "evidence_id")
    ):
        raise ValueError("Development evidence basis identities are incomplete")
    if value["evidence_id"] != fingerprint(
        {"version": BASIS_VERSION, **{key: value[key] for key in IDENTITIES}}
    ):
        raise ValueError("Development evidence basis identity conflicts")
    period, comparison = value.get("period", {}), value.get("comparison", {})
    if not isinstance(period, dict) or not isinstance(comparison, dict) or not comparison.get("id"):
        raise ValueError("Development evidence basis context is incomplete")
    if period.get("kind") == "evaluation" or comparison.get("period") == "evaluation":
        raise ValueError("Development evidence cannot contain a reserved final period")
    if period.get("kind") not in ("selection", "full") or comparison.get("period") != period.get(
        "kind"
    ):
        raise ValueError("Development evidence basis period conflicts")
    content = {key: item for key, item in comparison.items() if key != "id"}
    if comparison["id"] != fingerprint({"version": BASIS_VERSION, **content}):
        raise ValueError("Development evidence comparison identity conflicts")
    interval = period.get("interval")
    keys = _timeline(timeline, interval)
    if (period.get("from"), period.get("to"), period.get("observations")) != (
        keys[0],
        keys[-1],
        len(keys),
    ):
        raise ValueError("Development evidence basis timeline conflicts")
    if value["observations_id"] != fingerprint({"interval": interval, "timeline": keys}):
        raise ValueError("Development evidence observation identity conflicts")
    if not isinstance(comparison.get("currency"), str) or not comparison["currency"]:
        raise ValueError("Development evidence currency is missing")
    return keys, {
        "currency": comparison["currency"],
        "initial_capital": _number(comparison.get("capital"), "capital", positive=True),
    }


def _observations(curve, timeline, account):
    if not isinstance(curve, list) or len(curve) != len(timeline):
        raise ValueError("Development evidence curve is missing recorded marks")
    capital = account["initial_capital"]
    previous, peak, marks, returns = capital, capital, [], []
    for expected, point in zip(timeline, curve, strict=True):
        if not isinstance(point, dict):
            raise ValueError("Development evidence has an invalid account mark")
        if (
            point.get("timestamp", point.get("date")) != expected
            or point.get("date") != expected[:10]
        ):
            raise ValueError("Development evidence curve timeline conflicts")
        if len(expected) == 10 and "timestamp" in point:
            raise ValueError("Development evidence daily mark has an unexpected timestamp")
        equity = _number(point.get("equity"), "equity")
        cash = _number(point.get("cash"), "cash")
        drawdown = _number(point.get("drawdown_pct"), "drawdown")
        count = point.get("open_positions")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 1_000_000_000:
            raise ValueError("Development evidence open positions are invalid")
        if cash > equity + max(1e-8, equity * 1e-10) or drawdown > 100:
            raise ValueError("Development evidence account mark is inconsistent")
        peak = max(peak, equity)
        expected_dd = (peak - equity) / peak * 100
        if not math.isclose(drawdown, expected_dd, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError("Development evidence drawdown conflicts with actual equity")
        mark = {
            "date": point["date"],
            "equity": equity,
            "cash": cash,
            "drawdown_pct": drawdown,
            "open_positions": count,
        }
        if "timestamp" in point:
            mark["timestamp"] = point["timestamp"]
        marks.append(mark)
        value = equity / previous - 1 if previous > 0 else None
        if value is not None and not math.isfinite(value):
            raise ValueError("Development evidence return is nonfinite")
        returns.append(value)
        previous = equity
    return marks, returns


def _stock_condition(value, hypothesis):
    """Keep the existing causal membership receipt, without expanding its schema."""
    if not isinstance(value, dict) or set(value) != {
        "hypothesis",
        "bundle_id",
        "gate_id",
        "counts",
    }:
        raise ValueError("Development evidence stock condition receipt is malformed")
    if value["hypothesis"] != hypothesis:
        raise ValueError(
            "Development evidence hypothesis conflicts with the stock condition receipt"
        )
    if any(
        not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key])
        for key in ("bundle_id", "gate_id")
    ):
        raise ValueError("Development evidence stock condition identity is malformed")
    counts = value["counts"]
    if (
        not isinstance(counts, dict)
        or set(counts) != {"allowed", "filtered", "unknown", "original_excluded", "pending"}
        or any(type(count) is not int or not 0 <= count <= 25_000 for count in counts.values())
        or sum(counts.values()) > 25_000
    ):
        raise ValueError("Development evidence stock condition counts are malformed")
    return deepcopy(value)


def _sensitivity_evidence(value, basis, period):
    """Retain caller-checked native facts, bound to this immutable account shard."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "version",
            "plan_id",
            "scenario_id",
            "period",
            "basis_id",
            "support",
            "constraints",
            "id",
        }
        or value.get("version") != "research-sensitivity-account-evidence-v1"
        or value.get("period") != period
        or period not in ("sens_symbols_a", "sens_symbols_b", "sens_dates_early", "sens_dates_late")
        or value.get("id") != fingerprint({key: item for key, item in value.items() if key != "id"})
        or value.get("basis_id") != fingerprint(basis)
        or any(
            not isinstance(value.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", value[key])
            for key in ("plan_id", "scenario_id")
        )
    ):
        raise ValueError("Development sensitivity account identity changed")
    support, constraints = value.get("support"), value.get("constraints")
    if (
        not isinstance(support, dict)
        or set(support) != {"closed_trades", "entry_dates"}
        or any(type(number) is not int or not 0 <= number <= 25_000 for number in support.values())
        or support["entry_dates"] > support["closed_trades"]
        or not isinstance(constraints, dict)
        or constraints.get("id")
        != fingerprint({key: item for key, item in constraints.items() if key != "id"})
        or constraints.get("feature_id")
        != basis["comparison"]["execution"].get("portfolio_features", {}).get("id")
    ):
        raise ValueError("Development sensitivity native facts changed")
    return deepcopy(value)


def _search_seed_evidence(value, basis, period, stress):
    """Bind native winner facts to immutable search accounts, never final data."""
    from research.recommendation_seeds import ACCOUNT_VERSION

    if (
        period != "search"
        or not isinstance(value, dict)
        or set(value) != {"version", "plan_id", "basis_id", "support", "constraints", "id"}
        or value.get("version") != ACCOUNT_VERSION
        or not isinstance(value.get("plan_id"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["plan_id"])
        or value.get("basis_id") != fingerprint(basis)
        or value.get("id") != fingerprint({k: v for k, v in value.items() if k != "id"})
    ):
        raise ValueError("Development search-repeat account identity changed")
    support, audit = value.get("support"), value.get("constraints")
    if (
        not isinstance(support, dict)
        or set(support) != {"closed_trades", "entry_dates"}
        or any(type(n) is not int or not 0 <= n <= 25_000 for n in support.values())
        or support["entry_dates"] > support["closed_trades"]
        or not isinstance(audit, dict)
        or audit.get("id") != fingerprint({k: v for k, v in audit.items() if k != "id"})
        or audit.get("feature_id")
        != basis["comparison"]["execution"].get("portfolio_features", {}).get("id")
    ):
        raise ValueError("Development search-repeat native facts changed")
    return deepcopy(value)


def build(
    report, *, evaluation_id, binding, settings, hypothesis, period, stress, expected_timeline
):
    """Retain one actual scored development account with immutable identities.

    Invalid/unavailable report evidence raises ``ValueError``; callers record the
    failed retention and continue their original numerical contract. The returned
    full curve and initial-capital first return are separate from display samples.
    """
    if not isinstance(report, dict) or not isinstance(report.get("config"), dict):
        raise ValueError("Development evidence actual report is missing")
    if not isinstance(evaluation_id, str) or not evaluation_id or len(evaluation_id) > 256:
        raise ValueError("Development evidence evaluation identity is missing")
    if not isinstance(binding, dict) or not binding or not isinstance(hypothesis, dict):
        raise ValueError("Development evidence source binding or hypothesis is missing")
    if any(
        not isinstance(binding.get(key), str) or not binding[key] or len(binding[key]) > 256
        for key in ("inputs_artifact", "search_identity")
    ):
        raise ValueError("Development evidence immutable source binding is incomplete")
    if (
        not isinstance(period, str)
        or not period
        or len(period) > 64
        or period in ("final", "evaluation")
        or not isinstance(stress, bool)
    ):
        raise ValueError("Development evidence cannot contain a reserved final period")
    config_id = settings_identity(settings)
    if not config_id or settings_identity(report.get("strategies")) != config_id:
        raise ValueError("Development evidence settings conflict with the actual report")
    basis = report.get("evaluation_basis")
    timeline, account = _basis(basis, expected_timeline)
    expected_costs = []
    for row in settings:
        cfg = row.get("config")
        if not isinstance(cfg, dict):
            raise ValueError("Development evidence settings configuration is missing")
        expected_costs.append(
            {
                "strategy_id": row["id"],
                "cost_bps": _number(cfg.get("cost_bps", 0), "cost"),
                "slippage_bps": _number(cfg.get("slippage_bps", 0), "slippage"),
            }
        )
    if basis["comparison"].get("costs") != expected_costs:
        raise ValueError("Development evidence cost scenario conflicts with actual settings")
    if (
        _number(report.get("config", {}).get("initial_capital"), "report capital", positive=True)
        != account["initial_capital"]
    ):
        raise ValueError("Development evidence report capital conflicts")
    metadata = {
        "version": VERSION,
        "evaluation_id": evaluation_id,
        "binding": deepcopy(binding),
        "config_id": config_id,
        "settings": deepcopy(settings),
        "hypothesis": deepcopy(hypothesis),
        "period": period,
        "stress": stress,
        "evaluation_basis": deepcopy(basis),
        "evaluation_basis_id": basis["evidence_id"],
        "account": account,
        "return_policy": RETURN_POLICY,
    }
    if "research_opportunity_basis" in report:
        opportunity = report["research_opportunity_basis"]
        _, opportunity_account = _basis(opportunity, timeline)
        if opportunity_account != account or any(
            opportunity[key] != basis[key] for key in IDENTITIES if key != "cohort_id"
        ):
            raise ValueError("Development opportunity evidence changed its source observations")
        if any(
            opportunity["comparison"].get(key) != basis["comparison"].get(key)
            for key in ("period", "currency", "capital", "risk_limits")
        ):
            raise ValueError("Development opportunity evidence changed its account assumptions")
        actual_execution = basis["comparison"].get("execution", {})
        common_execution = opportunity["comparison"].get("execution", {})
        if {
            key: value for key, value in actual_execution.items() if key != "entry_conditions"
        } != common_execution:
            raise ValueError(
                "Development opportunity evidence changed native execution assumptions"
            )
        if "entry_conditions" in actual_execution and actual_execution["entry_conditions"] != {
            "version": "research-joint-entry-conditions-v2",
            "hypothesis": hypothesis,
        }:
            raise ValueError(
                "Development opportunity condition does not match the actual hypothesis"
            )
        metadata["research_opportunity_basis"] = deepcopy(opportunity)
    if "automatic_stock_condition" in report:
        metadata["stock_condition"] = _stock_condition(
            report["automatic_stock_condition"], hypothesis
        )
    if "sensitivity_evidence" in report:
        metadata["sensitivity_evidence"] = _sensitivity_evidence(
            report["sensitivity_evidence"], basis, period
        )
    if "search_seed_evidence" in report:
        metadata["search_seed_evidence"] = _search_seed_evidence(
            report["search_seed_evidence"], basis, period, stress
        )
    try:
        if _raw_size(metadata) > MAX_METADATA_BYTES:
            raise ValueError("Development evidence metadata limit reached")
        curve, returns = _observations(report.get("equity_curve"), timeline, account)
        shard = {**metadata, "timeline": timeline, "curve": curve, "simple_returns": returns}
        shard["id"] = fingerprint(shard)
        if _raw_size(shard) > MAX_SHARD_BYTES:
            raise ValueError("Development evidence shard byte limit reached")
    except (TypeError, OverflowError) as error:
        raise ValueError("Development evidence metadata cannot be recorded") from error
    return shard


def verify_shard(shard, *, descriptor):
    """Verify a loaded shard against its frozen index, without trusting its hash."""
    if (
        not isinstance(shard, dict)
        or shard.get("version") != VERSION
        or not isinstance(descriptor, dict)
    ):
        raise ValueError("Development evidence shard contract is unavailable")
    try:
        if _raw_size(shard) > MAX_SHARD_BYTES:
            raise ValueError("Development evidence shard byte limit reached")
        body = {key: value for key, value in shard.items() if key != "id"}
        if shard.get("id") != fingerprint(body):
            raise ValueError("Development evidence shard identity conflicts")
        for key in (
            "id",
            "evaluation_id",
            "config_id",
            "binding",
            "period",
            "stress",
            "evaluation_basis_id",
        ):
            if key not in descriptor or descriptor[key] != shard.get(key):
                raise ValueError(f"Development evidence descriptor {key} conflicts")
        rebuilt = build(
            {
                "strategies": shard["settings"],
                "evaluation_basis": shard["evaluation_basis"],
                "config": {"initial_capital": shard["account"]["initial_capital"]},
                "equity_curve": shard["curve"],
                **(
                    {"automatic_stock_condition": shard["stock_condition"]}
                    if "stock_condition" in shard
                    else {}
                ),
                **(
                    {"research_opportunity_basis": shard["research_opportunity_basis"]}
                    if "research_opportunity_basis" in shard
                    else {}
                ),
                **(
                    {"sensitivity_evidence": shard["sensitivity_evidence"]}
                    if "sensitivity_evidence" in shard
                    else {}
                ),
                **(
                    {"search_seed_evidence": shard["search_seed_evidence"]}
                    if "search_seed_evidence" in shard
                    else {}
                ),
            },
            evaluation_id=shard["evaluation_id"],
            binding=shard["binding"],
            settings=shard["settings"],
            hypothesis=shard["hypothesis"],
            period=shard["period"],
            stress=shard["stress"],
            expected_timeline=shard["timeline"],
        )
        if rebuilt != shard:
            raise ValueError("Development evidence shard content conflicts")
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("Development evidence shard is incomplete") from error
    return True
