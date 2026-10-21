"""Offline full-controller qualification on generated markets and genuine fills.

The market is a discrete-time martingale under every null: all price movement is
an unpredictable opening jump and each bar is then flat. This avoids inventing an
intrabar path, or a favourable stop/target ordering from daily OHLC extrema. The
signals are fixed before prices; after costs no predictable rule has positive
expected trading profit. Common shocks and changing variance do not change that
conditional mean. The positive control deliberately has predictable upward drift.
Synthetic volume has unknown broker units, so reviewed-broker capacity is disabled;
actual native turnover, stop-risk, concentration and correlation checks remain.
"""

from __future__ import annotations

import json
import math
import random
from collections import OrderedDict
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import patch

from test_recommendation_seeds import prepared as template
from test_stock_market_context import refreeze, resign

from research.automatic_protocol import compile_recipe
from research.automatic_walk_forward import run
from research.connectors.vectorbt_portfolio import evaluate
from research.development_evidence import MAX_TOTAL_BYTES, MAX_TOTAL_MARKS, build
from research.execution_features import prepare as prepare_atr
from research.portfolio import execution_versions, normalize
from research.portfolio_constraint_evidence import assess as actual_constraint_audit
from research.portfolio_execution_features import prepare as prepare_portfolio
from research.procedure_seed_calibration import FITS, seed_plan
from research.report_contract import fingerprint, settings_identity
from research.search_calibration import verify_plan
from research.volume_provenance import capture_rows

RUNNER_IDENTITY = "full-v15-native-synthetic-opening-jump-markets-v1"
GENERATOR_IDENTITY = "causal-martingale-opening-jumps-adverse-final-v1"


class NativeAccountCache:
    """Dataset-scoped bounded reuse of actual, pristine native account reports.

    This never retains an Optuna study or checkpoint. Independent programmes
    still ask/tell every proposal and audit each returned account separately.
    Use one cache per dataset and one OS process per dataset worker.
    """

    def __init__(self, *, max_entries=512, max_bytes=64 * 1024**2):
        if type(max_entries) is not int or not 1 <= max_entries <= 512:
            raise ValueError("Native account cache entry bound is invalid")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 64 * 1024**2:
            raise ValueError("Native account cache byte bound is invalid")
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.reports = OrderedDict()
        self.bytes = self.calculations = self.reuses = 0
        self.dataset_id = None
        self.closed = False

    def bind(self, dataset_id):
        if self.closed or self.dataset_id not in (None, dataset_id):
            raise ValueError("Native account cache cannot cross datasets or be reopened")
        self.dataset_id = dataset_id

    def evaluate(self, rows, snapshot, capital, progress=None):
        if self.closed or self.dataset_id is None:
            raise ValueError("Native account cache requires its open dataset scope")
        key = fingerprint({"rows": rows, "snapshot": snapshot, "capital": capital})
        if key in self.reports:
            self.reports.move_to_end(key)
            self.reuses += 1
            if progress:
                progress(1, 1)
            return deepcopy(self.reports[key][0])
        report = evaluate(rows, snapshot, capital, progress=progress)
        self.calculations += 1
        size = len(
            json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        )
        if size <= self.max_bytes:
            while self.reports and (
                len(self.reports) >= self.max_entries or self.bytes + size > self.max_bytes
            ):
                _, (_, discarded) = self.reports.popitem(last=False)
                self.bytes -= discarded
            self.reports[key] = (deepcopy(report), size)
            self.bytes += size
        return report

    def close(self):
        self.reports.clear()
        self.bytes = 0
        self.dataset_id = None
        self.closed = True

    def __enter__(self):
        if self.closed:
            raise ValueError("Native account cache is closed")
        return self

    def __exit__(self, *error):
        self.close()


def market_paths(days, symbols, *, data_seed, scenario, adverse_from=None):
    """Generate one immutable path; prefixes never resample or depend on length."""
    if scenario not in {
        "independent_null",
        "correlated_null",
        "shifted_null",
        "sparse_null",
        "persistent_edge",
    }:
        raise ValueError("Unknown declared native market scenario")
    common = random.Random(data_seed)
    generators = {
        symbol: random.Random(int(fingerprint({"seed": data_seed, "symbol": symbol})[:16], 16))
        for symbol in symbols
    }
    # A valid dense source calendar can still have no executable support: the
    # sparse model's nominal whole-share price exceeds the complete account.
    prices = dict.fromkeys(symbols, 100_000_000.0 if scenario == "sparse_null" else 100.0)
    result = {symbol: {} for symbol in symbols}
    for day in days:
        common_sign = common.choice((-1, 1))
        for symbol in symbols:
            generator = generators[symbol]
            own_sign = generator.choice((-1, 1))
            shock = (
                math.sqrt(0.85) * common_sign + math.sqrt(0.15) * own_sign
                if scenario == "correlated_null"
                else own_sign
            )
            # Calendar-fixed switch is prefix invariant and known in advance.
            volatility = 0.001
            if scenario == "shifted_null":
                volatility *= 0.35 if day < "2026-03-01" else 2.0
            drift = 0.004 if scenario == "persistent_edge" else 0.0
            if scenario == "persistent_edge" and adverse_from is not None and day >= adverse_from:
                drift = -0.004
            prices[symbol] *= 1 + drift + volatility * shock
            result[symbol][day] = {
                "open": prices[symbol],
                "high": prices[symbol],
                "low": prices[symbol],
                "close": prices[symbol],
                "volume": 100_000 + generator.randrange(50_000),
            }
    return result


def prepared(case):
    """Reuse native contract scaffolding, replace every price/source observation."""
    value = template()
    context = value["automatic_stock_market_context"]
    records = {**context["series"], "MARKET": context["market"]}
    days = sorted({day for record in records.values() for day in record["calendar"]["sessions"]})
    # Calendar-only boundary exists before any generated market/outcome is read.
    # A held-out reversal tests that disappointing confirmation cannot rerank.
    adverse_from = value["automatic_recipe"]["periods"]["final"]["from"]
    paths = market_paths(
        days,
        tuple(records),
        data_seed=case["data_seed"],
        scenario=case["scenario"],
        adverse_from=adverse_from,
    )
    for symbol, record in records.items():
        record["calendar"]["provenance"].update(
            synthetic=True, provider="native_calibration_synthetic", generator=GENERATOR_IDENTITY
        )
        record["acquisition_receipts"] = []
        for day, bar in record["evidence"]["bars"].items():
            bar.update({key: paths[symbol][day][key] for key in ("open", "high", "low", "close")})
        refreeze(record)
    resign(context)
    snapshot = value["snapshot"]
    snapshot["provenance"].update(
        synthetic=True,
        provider="native_calibration_synthetic",
        generator=GENERATOR_IDENTITY,
        adjustment_basis="synthetic_unadjusted",
        data_seed=case["data_seed"],
    )
    for symbol in context["series"]:
        snapshot["bars"][symbol] = {
            day: deepcopy(context["series"][symbol]["evidence"]["bars"][day])
            for day in snapshot["sessions"]
        }
    for row in value["portfolio"]["strategies"]:
        row["execution_policy"]["capacity_mode"] = "disabled"
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["signals"] = [
        dict(signal, strategy_id=row["id"])
        for row in value["strategies"]
        for signal in row["signals"]
    ]
    value["versions"] = execution_versions(value["portfolio"])
    for key in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        value.pop(key, None)
    value = prepare_atr(value, context)
    volume = {}
    for symbol, record in context["series"].items():
        rows = {
            day: {**bar, "volume": paths[symbol][day]["volume"]}
            for day, bar in record["evidence"]["bars"].items()
        }
        volume[symbol] = capture_rows(
            symbol,
            list(rows.values()),
            rows,
            broker="native_calibration_synthetic",
            adapter_sha256=fingerprint(GENERATOR_IDENTITY),
            captured_at="2026-10-21T00:00:00+00:00",
        )
    value = prepare_portfolio(value, context, volume)
    value["automatic_recipe"] = compile_recipe(value)
    return value


def run_case(case, plan, *, calibration_seed_plan=None, native_cache=None):
    """Run real Optuna, real native gates/fills/audits and immutable histories."""
    verify_plan(plan)
    if (
        plan["execution_model"] != "native_synthetic_market"
        or plan["runner_identity"] != RUNNER_IDENTITY
    ):
        raise ValueError("Native runner requires its frozen native-synthetic declaration")
    if case not in plan["cases"]:
        raise ValueError("Case was not frozen in the calibration plan")
    value = prepared(case)
    recipe = value["automatic_recipe"]
    if (
        recipe["version"] != plan["recipe_version"]
        or recipe["budget"]["proposals"] != plan["proposal_budget"]
        or recipe["budget"]["max_simulations"] != plan["max_accounts"]
        or recipe["search_seeds"]["seeds"] != plan["sampler_seeds"]
    ):
        raise ValueError("Native fixture differs from declared full procedure")
    cache = NativeAccountCache() if native_cache is None else native_cache
    cache.bind(fingerprint(value))
    before_calculations, before_reuses = cache.calculations, cache.reuses
    latest = None
    shards, descriptors = {}, {}
    total_bytes = total_marks = calls = audits = 0
    audit_rechecks = 0
    pending_native_audits = set()
    final_selections = []
    paused = False

    class OfflinePause(Exception):
        """A predeclared committed-proposal boundary, never an in-flight fill."""

    inputs_id = fingerprint(
        {"case": case, "snapshot": value["snapshot"], "runner": RUNNER_IDENTITY}
    )

    def checkpoint(state, counts):
        nonlocal latest, paused
        latest = state
        if not paused and len(state["fits"].get("fit1", {}).get("trials", [])) == 52:
            paused = True
            raise OfflinePause

    def native(rows, snapshot, capital, progress=None):
        nonlocal calls
        if snapshot["sessions"][0] >= recipe["periods"]["final"]["from"]:
            if latest["selection"] is None:
                raise AssertionError("Native final requested before frozen selection")
            final_selections.append(latest["selection"]["config_id"])
        report = cache.evaluate(rows, snapshot, capital, progress=progress)
        if report["policy_version"] != "vectorbt-joint-causal-bars-v5":
            raise AssertionError("Native calibration lost constrained execution")
        pending_native_audits.add(id(report))
        calls += 1
        return report

    def retain(report, *, search_identity, **context):
        nonlocal total_bytes, total_marks
        audit = report.get("portfolio_constraint_evidence")
        if audit is None or audit["status"] == "invalid" or "test_double" in audit:
            raise AssertionError("Native controller must retain its actual constraint audit")
        binding = {"inputs_artifact": inputs_id, "search_identity": search_identity}
        shard = build(report, binding=binding, **context)
        key = context["evaluation_id"]
        if key in shards:
            if shards[key] != shard:
                raise AssertionError("Native account failed immutable reproduction")
            return deepcopy(descriptors[key])
        size = len(
            json.dumps(shard, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        )
        marks = len(shard["curve"])
        if (
            len(shards) >= plan["max_accounts"]
            or total_bytes + size > MAX_TOTAL_BYTES
            or total_marks + marks > MAX_TOTAL_MARKS
        ):
            raise ValueError("Native calibration history exceeded declared bound")
        descriptor = {
            "evaluation_id": key,
            "id": shard["id"],
            "binding": binding,
            "period": context["period"],
            "stress": context["stress"],
            "status": "retained",
            "basis_id": fingerprint(report["evaluation_basis"]),
            "marks": marks,
            "bytes": size,
        }
        shards[key], descriptors[key] = shard, descriptor
        total_bytes += size
        total_marks += marks
        return deepcopy(descriptor)

    def audited(report, features):
        # Observation-only wrapper: invoke the genuine consumer exactly once;
        # never synthesize, alter or weaken its numerical evidence or status.
        nonlocal audits, audit_rechecks
        result = actual_constraint_audit(report, features)
        if id(report) in pending_native_audits:
            pending_native_audits.remove(id(report))
            audits += 1
        else:
            audit_rechecks += 1
        return result

    kwargs = (
        {} if calibration_seed_plan is None else {"calibration_seed_plan": calibration_seed_plan}
    )
    owned_scope = cache if native_cache is None else nullcontext(cache)
    with owned_scope, patch("research.portfolio_constraint_evidence.assess", new=audited):
        common = {
            "evaluate": native,
            "checkpoint": checkpoint,
            "progress": lambda *args: None,
            "cancelled": lambda: None,
            "defer_study_analysis": True,
            "retain_evaluation": retain,
            "read_evaluation": lambda key: deepcopy(shards.get(key)),
            **kwargs,
        }
        try:
            run(value, **common)
        except OfflinePause:
            prefix = deepcopy(latest)
            prefix_histories = {key: shard["id"] for key, shard in shards.items()}
            prefix_calls = calls
            if prefix["simulations"] != calls or len(prefix["evaluations"]) != len(shards):
                raise AssertionError("Native pause did not retain all completed accounts") from None
        else:
            raise AssertionError("Native programme missed its declared recovery boundary")
        result = run(value, saved=prefix, **common)
    if any(shards[key]["id"] != identity for key, identity in prefix_histories.items()):
        raise AssertionError("Native resume replaced a retained prefix account")
    selection = latest["selection"]
    if not final_selections or any(key != selection["config_id"] for key in final_selections):
        raise AssertionError("Final result changed the frozen development choice")
    if calls != latest["simulations"] or calls > plan["max_accounts"]:
        raise AssertionError("Native calculation accounting was incomplete")
    if result["automatic_research"]["selected_config_id"] != selection["config_id"]:
        raise AssertionError("Native report changed the frozen choice")
    adverse_final_verified = None
    if case["scenario"] == "persistent_edge":
        adverse_final_verified = (
            latest["baseline_final_report"]["summary"]["net_return_pct"] < 0
            and latest["final_report"]["summary"]["net_return_pct"] <= 0
        )
        if not adverse_final_verified:
            raise AssertionError("Native positive control did not exercise adverse final outcomes")
    proposals = sum(len(fit["trials"]) for fit in latest["fits"].values())
    context = seed_plan(0) if calibration_seed_plan is None else calibration_seed_plan
    history_id = fingerprint(sorted(shard["id"] for shard in shards.values()))
    facts = {
        "execution_model": "native_synthetic_market",
        "native_execution": True,
        "seed_plan_id": context["id"],
        "data_seed": case["data_seed"],
        "constraint_audits": audits,
        "dataset_id": fingerprint(value),
        "history_id": history_id,
        "generator_identity": GENERATOR_IDENTITY,
        "adverse_final_verified": adverse_final_verified,
        "recovery_verified": True,
        "resumed_evaluations": len(prefix_histories),
        "resume_prefix_calls": prefix_calls,
        "resume_prefix_history_id": fingerprint(prefix_histories),
        "capacity_scope": "disabled_no_reviewed_broker_volume_in_synthetic_market",
        "fit_evidence": {
            name: {
                "sampler_seed": context["fit_sampler_seeds"][name],
                "proposals": len(latest["fits"].get(name, {}).get("trials", [])),
                "status": "completed"
                if latest["fits"].get(name, {}).get("selection") is not None
                else "not_run",
                "trials_id": fingerprint(latest["fits"].get(name, {}).get("trials", [])),
                "selected_config_id": (latest["fits"].get(name, {}).get("selection") or {}).get(
                    "config_id"
                ),
            }
            for name in FITS
        },
    }
    if audits != calls or pending_native_audits:
        raise AssertionError("Every native account must receive its genuine constraint audit")
    calculations = cache.calculations - before_calculations
    reuses = cache.reuses - before_reuses
    if calculations + reuses != calls:
        raise AssertionError("Native cache accounting differs from actual account requests")
    facts.update(native_calculations=calculations, native_reuses=reuses)
    return {
        "case_id": case["id"],
        "status": "completed",
        "baseline_config_id": settings_identity(recipe["baseline"]),
        "selected_config_id": selection["config_id"],
        "proposal_count": proposals,
        "seed_checks_status": "completed" if proposals == plan["proposal_budget"] else "not_run",
        "account_count": calls,
        "sampler_seeds": plan["sampler_seeds"],
        "procedure_completed": True,
        "development_selection_frozen": True,
        "final_excluded_from_selection": True,
        "evidence_id": fingerprint(
            {
                "case": case,
                "checkpoint": latest["checkpoint_id"],
                "histories": sorted(shard["id"] for shard in shards.values()),
                "runner": RUNNER_IDENTITY,
            }
        ),
        "native_procedure": {**facts, "id": fingerprint(facts)},
        "native_details": {
            "generator_identity": GENERATOR_IDENTITY,
            "audit_count": audits,
            "audit_rechecks": audit_rechecks,
            "native_calculations": calculations,
            "native_reuses": reuses,
            "history_count": len(shards),
            "history_marks": total_marks,
            "history_bytes": total_bytes,
            "history_digest": fingerprint(sorted(shard["id"] for shard in shards.values())),
            "checkpoint_id": latest["checkpoint_id"],
            "fit_fingerprints": {name: fingerprint(fit) for name, fit in latest["fits"].items()},
            "recommendation_checks": deepcopy(
                result["automatic_research"].get("recommendation_checks", {})
            ),
        },
    }
