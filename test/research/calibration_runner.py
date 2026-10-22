"""Explicit offline full-controller runner with controlled account economics.

Import with ``test/research`` on PYTHONPATH. This runs genuine Optuna and native
causal indicator admission, but NOT native fills: account marks below are a
declared stochastic model and portfolio-constraint audits are an explicit stub.
Independent native execution and durable service-recovery suites are separate.
No database, broker, network, trading route or normal installation is used.
"""

from __future__ import annotations

import json
import math
import random
from copy import deepcopy
from unittest.mock import patch

from test_automatic_walk_forward import AccountEngine
from test_recommendation_seeds import prepared

from research.automatic_walk_forward import run
from research.development_evidence import MAX_TOTAL_BYTES, MAX_TOTAL_MARKS, build
from research.report_contract import fingerprint, settings_identity
from research.search_calibration import verify_plan

RUNNER_IDENTITY = "full-v15-optuna-native-gates-controlled-account-model-v2"


def economic_identity(settings):
    """Cost stress uses the same potential outcomes, with only costs changing."""
    canonical = deepcopy(settings)
    for row in canonical:
        row["config"]["cost_bps"] = 0
        row["config"]["slippage_bps"] = 0
    return settings_identity(canonical)


class ControlledAccounts:
    """Frozen potential outcomes keyed by dataset seed, settings and session.

    Every possible settings identity has a deterministic independent innovation
    stream fixed by its hash, not by proposal order or observed score. All null
    configurations have the same conditional expected daily return. Correlation
    and a variance shift change noise, not its expectation. The persistent-edge
    control deliberately assigns every changed configuration a common positive
    edge; it checks power and complete branching, not realistic alpha discovery.
    """

    def __init__(self, value, case, checkpoint):
        self.value = value
        self.case = case
        self.checkpoint = checkpoint
        self.base = AccountEngine(value)
        self.baseline_id = settings_identity(value["automatic_recipe"]["baseline"])
        self.baseline_economic_id = economic_identity(value["automatic_recipe"]["baseline"])
        self.timeline = value["snapshot"]["sessions"]
        self.indices = {key: index for index, key in enumerate(self.timeline)}
        generator = random.Random(case["data_seed"])
        self.common = [generator.gauss(0, 1) for _ in self.timeline]
        self.final_selections = []
        self.calls = 0

    def __call__(self, rows, snapshot, capital, progress=None):
        result = self.base(rows, snapshot, capital, progress)
        config_id = economic_identity(result["strategies"])
        changed = config_id != self.baseline_economic_id
        scenario = self.case["scenario"]
        # A complete potential-outcome stream is a pure function of this frozen
        # model, dataset seed and settings. Prefixes do not regenerate their RNG.
        candidate_seed = int(
            fingerprint({"data_seed": self.case["data_seed"], "config_id": config_id})[:16], 16
        )
        generator = random.Random(candidate_seed)
        innovations = [generator.gauss(0, 1) for _ in self.timeline]
        final = (
            snapshot["sessions"][0] >= self.value["automatic_recipe"]["periods"]["final"]["from"]
        )
        if final:
            selection = self.checkpoint()["selection"]
            if selection is None:
                raise AssertionError("Final outcomes requested before development choice froze")
            self.final_selections.append(selection["config_id"])
        cost = rows[0]["config"]["cost_bps"]
        original_cost = self.value["automatic_recipe"]["baseline"][0]["config"]["cost_bps"]
        # Equal common cost stress for all configurations, not a fitted axis.
        stress = max(0.0, cost - original_cost) * 0.000001
        equity = peak = float(capital)
        for offset, point in enumerate(result["equity_curve"]):
            index = self.indices[point["date"]]
            if final:
                daily_return = -0.003 if changed else -0.002
            elif scenario == "persistent_edge":
                daily_return = (0.0018 if changed else 0.0001) + 0.00004 * innovations[index]
            else:
                correlation = 0.85 if scenario == "correlated_null" else 0.0
                volatility = 0.003
                if scenario == "shifted_null":
                    volatility *= 0.35 if index < len(self.timeline) // 2 else 2.0
                daily_return = 0.0001 + volatility * (
                    math.sqrt(correlation) * self.common[index]
                    + math.sqrt(1 - correlation) * innovations[index]
                )
            if offset:
                equity *= 1 + daily_return - stress
            peak = max(peak, equity)
            point.update(equity=equity, cash=equity, drawdown_pct=(peak - equity) / peak * 100)
        # Support uses actual native-admitted observations, not invented dates.
        allowed = sorted(
            {
                signal["date"]
                for row in rows
                for signal in row["signals"]
                if not signal.get("research_exclusion")
            }
        )
        support = allowed[: 4 if scenario == "sparse_null" else 10]
        result["ledger"] = [{"status": "closed", "entry_date": key} for key in support]
        result["summary"].update(
            net_return_pct=(equity / float(capital) - 1) * 100,
            max_drawdown_pct=max(point["drawdown_pct"] for point in result["equity_curve"]),
            closed_trades=len(support),
        )
        self.calls += 1
        return result


def controlled_audit(report, features, _identity_check=None):
    """Explicit schema-compatible stub, never evidence of native risk arithmetic.

    The scope/qualification strings implement the interface consumed by the
    real controller. The sealed test_double flag and outer runner/model identity
    distinguish these simulated facts from native execution qualification.
    """
    body = {
        "version": "research-portfolio-constraint-evidence-v1",
        "status": "supported",
        "feature_id": features["id"],
        "report_evidence_id": fingerprint(
            {
                key: report[key]
                for key in ("summary", "equity_curve", "evaluation_basis", "strategies")
            }
        ),
        "unavailable_entries": 0,
        "scope": "caller_admitted_development_account",
        "qualification": "recorded_constraints_and_native_fills_only",
        "test_double": {
            "kind": "controlled_account_model",
            "native_execution": False,
            "purpose": "full_controller_orchestration_only",
        },
    }
    return {**body, "id": fingerprint(body)}


def run_case(case, plan):
    """Run the exact v15 controller branch, returning its frozen decision facts."""
    verify_plan(plan)
    if (
        plan["execution_model"] != "controlled_account_model"
        or plan["runner_identity"] != RUNNER_IDENTITY
    ):
        raise ValueError("This runner qualifies only its declared controlled account model")
    if case not in plan["cases"]:
        raise ValueError("Case was not frozen in the calibration plan")
    value = prepared()
    if (
        value["automatic_recipe"]["version"] != plan["recipe_version"]
        or value["automatic_recipe"]["budget"]["proposals"] != plan["proposal_budget"]
        or value["automatic_recipe"]["budget"]["max_simulations"] != plan["max_accounts"]
        or value["automatic_recipe"]["search_seeds"]["seeds"] != plan["sampler_seeds"]
    ):
        raise ValueError("Calibration declaration differs from actual frozen controller")
    latest = None
    shards = {}
    descriptors = {}
    total_bytes = total_marks = 0
    inputs_id = fingerprint(
        {"case": case, "snapshot": value["snapshot"], "runner": RUNNER_IDENTITY}
    )

    def checkpoint(state, counts):
        nonlocal latest
        latest = state

    def retain(report, *, search_identity, **context):
        nonlocal total_bytes, total_marks
        binding = {"inputs_artifact": inputs_id, "search_identity": search_identity}
        shard = build(report, binding=binding, **context)
        key = context["evaluation_id"]
        if key in shards:
            if shards[key] != shard:
                raise ValueError("Controlled account did not reproduce its immutable marks")
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
            raise ValueError("Calibration history retention exceeded its bound")
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

    engine = ControlledAccounts(value, case, lambda: latest)
    # Do not patch prepare_method_gate: the genuine native causal gate owns and
    # releases its projections for every complete optimisation invocation.
    with patch("research.portfolio_constraint_evidence._assess", controlled_audit):
        result = run(
            value,
            evaluate=engine,
            checkpoint=checkpoint,
            progress=lambda *args: None,
            cancelled=lambda: None,
            defer_study_analysis=True,
            retain_evaluation=retain,
            read_evaluation=lambda key: deepcopy(shards.get(key)),
        )
    selection = latest["selection"]
    if not engine.final_selections or any(
        key != selection["config_id"] for key in engine.final_selections
    ):
        raise AssertionError("Final evaluation changed or preceded the frozen development choice")
    if engine.calls != latest["simulations"] or engine.calls > plan["max_accounts"]:
        raise AssertionError("Controller accounting was incomplete")
    proposals = sum(len(fit["trials"]) for fit in latest["fits"].values())
    seed_status = "completed" if proposals == plan["proposal_budget"] else "not_run"
    evidence_id = fingerprint(
        {
            "case": case,
            "checkpoint": latest["checkpoint_id"],
            "histories": sorted(shard["id"] for shard in shards.values()),
            "model": RUNNER_IDENTITY,
        }
    )
    if result["automatic_research"]["selected_config_id"] != selection["config_id"]:
        raise AssertionError("Report selection differs from frozen development choice")
    return {
        "case_id": case["id"],
        "status": "completed",
        "baseline_config_id": engine.baseline_id,
        "selected_config_id": selection["config_id"],
        "proposal_count": proposals,
        "seed_checks_status": seed_status,
        "account_count": engine.calls,
        "sampler_seeds": plan["sampler_seeds"],
        "procedure_completed": True,
        "development_selection_frozen": True,
        "final_excluded_from_selection": True,
        "evidence_id": evidence_id,
    }
