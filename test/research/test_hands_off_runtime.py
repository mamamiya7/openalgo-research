"""Checkpoint authority and real numeric search at a frozen refinement boundary.

Planner responses are explicit contract fixtures, not evidence of a live AI call.
The numerical integration uses the existing Optuna/VectorBT adapters unchanged.
"""

from copy import deepcopy

import pytest

from research import hands_off_runtime as runtime
from research.engine import validate_config
from research.execution_policy import normalize_policy
from research.hands_off_plan import VERSION, compile_plan


@pytest.fixture
def case():
    policy = normalize_policy({"version": "research-execution-aware-v1"})
    first = {
        "id": "scan",
        "name": "Original scan",
        "allocation_pct": 100,
        "config": validate_config({"hold_sessions": 2}),
        "execution_policy": policy,
    }
    second = deepcopy(first)
    second["config"]["hold_sessions"] = 4
    rows = [first, second]
    trials = [
        {
            "number": n,
            "params": {
                "hypothesis": "none",
                "scan.hold_sessions": row["config"]["hold_sessions"],
                "scan.stop_pct": row["config"]["stop_pct"],
                "scan.execution_choice": "percent",
                "portfolio.allocation_mode": "original",
            },
            "value": n + 1,
        }
        for n, row in enumerate(rows)
    ]
    current = {
        "trials": trials,
        "candidates": {
            str(n): {
                "config_id": str(n),
                "trial_number": n,
                "strategies": [row],
                "score": n + 1,
                "summary": {"net_return_pct": n + 1, "closed_trades": 9},
                "automatic_design": {"qualified": False, "reasons": ["Too few trades"]},
            }
            for n, row in enumerate(rows)
        },
    }
    state = {"fits": {"fit1": current}, "attempts": 3, "simulations": 3}
    inputs = {
        "fit_name": "fit1",
        "index": 2,
        "screening": 2,
        "total": 12,
        "axes": {
            "scan.hold_sessions": {"min": 2, "max": 8, "step": 2},
            "scan.stop_pct": {"min": 2, "max": 10, "step": 4},
            "scan.execution_choice": {"type": "categorical", "choices": ["percent", "atr_2_4"]},
            "portfolio.allocation_mode": {
                "type": "categorical",
                "choices": ["original", "prior60_cluster_inverse_vol"],
            },
        },
        "baseline": [first],
        "reference": {"summary": {"net_return_pct": 0.5}},
        "signal_id": "a" * 64,
        "style": "positional",
        "risk_limits": policy["risk_limits"],
    }
    return state, current, inputs


def proposal(**changes):
    value = {
        "version": VERSION,
        "action": "search",
        "method": "holding_and_exits",
        "hypothesis": "Different supported holding durations may change measured occupancy and costs",
        "reason": "Completed prefix accounts expose a mechanism worth testing",
        "expected_evidence": "Actual after-cost returns, occupancy and all unchanged validation checks",
        "stop_rule": "Stop when no distinct permitted domain addresses the observed failure",
        "domains": {"scan": {"hold_sessions": {"min": 2, "max": 6, "step": 2}}},
    }
    value.update(changes)
    return value


def invoke(case, callback=None, **overrides):
    state, current, inputs = case
    captures = []
    args = {
        **inputs,
        "callback": callback
        or (lambda evidence: compile_plan(proposal(), evidence["planning_context"])),
        "persist": lambda: captures.append(deepcopy(state)),
        "cancelled": lambda: None,
        **overrides,
    }
    result = runtime.refinement(state, current, **args)
    return result, captures


def test_mandatory_screening_does_not_call_model_or_create_reservation(case):
    before = deepcopy(case)

    def forbidden(_):
        pytest.fail("The mandatory screen cannot be replaced with an AI refinement")

    result, writes = invoke(case, forbidden, index=1)
    assert result is None and writes == []
    assert case == before
    assert runtime.boundaries(50, 68) == [50, 59]


def test_reservation_is_durable_before_model_and_uses_actual_prefix_anchor(case):
    state, current, _ = case
    original = deepcopy(current)
    evidence_seen = []
    persisted = []

    def select(evidence):
        evidence_seen.append(deepcopy(evidence))
        reservation = persisted[-1]["hands_off_planning"]
        assert reservation["calls_spent"] == 1
        assert reservation["active"] == "fit1:2"
        assert reservation["records"]["fit1:2"]["phase"] == "requesting"
        return compile_plan(proposal(), evidence["planning_context"])

    record, _ = invoke(case, select, persist=lambda: persisted.append(deepcopy(state)))
    assert record["phase"] == "completed"
    assert record["anchor_params"] == current["trials"][1]["params"]
    assert (
        record["plan"]["strategies"][0]["config"]
        == current["candidates"]["1"]["strategies"][0]["config"]
    )
    assert evidence_seen[0]["programme"]["proposals"] == 408
    assert evidence_seen[0]["programme"]["max_simulations"] == 512
    assert record["plan"]["budget"] == {"proposals": 408, "max_simulations": 512}
    assert current == original
    assert state["hands_off_planning"]["active"] is None
    assert len(persisted) == 2


def test_screened_categorical_policy_changes_do_not_require_exact_baseline_controls(case):
    state, current, inputs = case
    inputs["baseline"] = deepcopy(inputs["baseline"])
    # Real screens change sizing/allocation even while keeping percent exits.
    # Neither actual candidate exactly matches the original policy anymore.
    for candidate in current["candidates"].values():
        row = candidate["strategies"][0]
        row["execution_policy"] = normalize_policy(
            {
                **row["execution_policy"],
                "sizing": "stop_risk",
                "risk_budget_fraction": 0.25,
            }
        )
        row["config"]["trailing_enabled"] = True
        row["config"]["trailing_pct"] = 2
    result, _ = invoke(case)
    assert result["phase"] == "completed"
    assert set(result["plan"]["domains"]["scan"]) == {"hold_sessions"}
    assert result["plan"]["strategies"][0]["execution_policy"]["sizing"] == "stop_risk"
    assert result["plan"]["strategies"][0]["config"]["trailing_enabled"] is True
    assert state["hands_off_planning"]["calls_spent"] == 1


def test_anchor_prefers_original_exit_style_with_active_numeric_controls_over_raw_score(case):
    _, current, _ = case
    best = current["candidates"]["1"]
    best["strategies"][0]["execution_policy"] = normalize_policy(
        {
            **best["strategies"][0]["execution_policy"],
            "exit_mode": "atr",
            "atr_period": 14,
            "atr_stop_multiple": 2,
            "atr_target_multiple": 4,
        }
    )
    best["score"] = 5000
    seen = []

    def choose(evidence):
        seen.append(deepcopy(evidence))
        return compile_plan(proposal(), evidence["planning_context"])

    record, _ = invoke(case, choose)
    assert record["anchor_params"] == current["trials"][0]["params"]
    assert set(seen[0]["planning_context"]["domains"]["scan"]) == {"hold_sessions", "stop_pct"}
    assert any(r["score"] == 5000 for r in seen[0]["observed_candidates"])


def test_anchor_preserves_cost_and_risk_locks_when_screened_controls_differ(case):
    _, current, inputs = case
    inputs["baseline"] = deepcopy(inputs["baseline"])
    inputs["risk_limits"] = deepcopy(inputs["risk_limits"])
    current["candidates"]["1"]["strategies"][0]["config"]["cost_bps"] = 0
    record, _ = invoke(case)
    assert record["anchor_params"] == current["trials"][0]["params"]
    current["candidates"]["0"]["strategies"][0]["execution_policy"]["risk_limits"][
        "max_trade_risk_pct"
    ] = 99
    # Use fresh checkpoint state so the changed evidence cannot be an old-plan replay.
    case[0].pop("hands_off_planning")
    with pytest.raises(ValueError, match="preserving source, style, costs and risk"):
        invoke(case)


@pytest.mark.parametrize(
    "change", ["active_missing", "active_completed", "multiple_requesting", "wrong_key"]
)
def test_checkpoint_active_and_phase_consistency_is_checked_before_provider(case, change):
    def interrupted(_):
        raise InterruptedError("reserved model operation interrupted")

    with pytest.raises(InterruptedError):
        invoke(case, interrupted)
    book = case[0]["hands_off_planning"]
    if change == "active_missing":
        book["active"] = None
    elif change == "active_completed":
        row = book["records"]["fit1:2"]
        row.update(phase="completed", plan={})
    elif change == "multiple_requesting":
        row = deepcopy(book["records"]["fit1:2"])
        row["index"] = 7
        book["records"]["fit1:7"] = row
        book["calls_spent"] += 1
    else:
        book["records"]["fit1:7"] = book["records"].pop("fit1:2")
        book["active"] = "fit1:7"
    with pytest.raises(ValueError, match="checkpoint|reservation"):
        invoke(case, lambda _: pytest.fail("Invalid reservation cannot contact provider"))


def test_replay_reuses_exact_plan_ignores_future_candidates_and_never_recontacts_model(case):
    state, current, _ = case
    record, _ = invoke(case)
    future = deepcopy(current["candidates"]["1"])
    future.update(config_id="future", trial_number=2, score=99999)
    future["summary"] = {"held_out_secret": "future result must not enter planning"}
    current["candidates"]["future"] = future
    current["trials"].append({"number": 2, "params": {"future": True}, "value": 99999})
    calls = state["hands_off_planning"]["calls_spent"]

    def forbidden(_):
        pytest.fail("Saved-plan replay cannot spend another model request")

    restored, writes = invoke(case, forbidden, index=3)
    assert restored == record
    assert writes == []
    assert state["hands_off_planning"]["calls_spent"] == calls == 1


@pytest.mark.parametrize("stage", ["provider", "cancelled"])
def test_interrupted_reserved_call_is_consumed_without_blind_retry(case, stage):
    def interrupt(*_):
        raise InterruptedError("simulated interruption")

    kwargs = {"cancelled": interrupt} if stage == "cancelled" else {}
    with pytest.raises(InterruptedError):
        invoke(case, interrupt, **kwargs)
    book = case[0]["hands_off_planning"]
    assert book["calls_spent"] == 1
    assert book["records"]["fit1:2"]["phase"] == "requesting"
    with pytest.raises(ValueError, match="no blind provider retry"):
        invoke(case, lambda _: pytest.fail("Retry must be rejected before provider"))
    assert book["calls_spent"] == 1


def test_changed_prefix_evidence_cannot_reuse_old_plan(case):
    invoke(case)
    case[1]["candidates"]["1"]["summary"]["net_return_pct"] = 999
    with pytest.raises(ValueError, match="planning evidence changed"):
        invoke(
            case, lambda _: pytest.fail("Changed evidence cannot trigger a hidden replacement call")
        )


def test_model_stop_is_preserved_without_numeric_fallback(case):
    stop = proposal(
        action="stop",
        method=None,
        domains={},
        reason="Remaining ranges cannot repair sample adequacy",
    )
    record, _ = invoke(case, lambda e: compile_plan(stop, e["planning_context"]))
    assert record["plan"]["action"] == "stop"
    assert record["plan"]["domains"] == {}
    assert "strategies" not in record["plan"]
    stopped = {"hands_off_planning": {"stop_reason": record["plan"]["reason"]}}

    def branch():
        raise runtime.HandsOffStopped(stopped)

    assert runtime.run_guard(branch) == stopped


def test_out_of_domain_plan_is_rejected_after_reserved_attempt(case):
    def malicious(evidence):
        # Compilation is a real call, not a stub: future/unprepared holding data
        # cannot enter an Optuna domain even if the provider requests it.
        return compile_plan(
            proposal(domains={"scan": {"hold_sessions": {"min": 2, "max": 10, "step": 2}}}),
            evidence["planning_context"],
        )

    with pytest.raises(ValueError, match="outside the frozen"):
        invoke(case, malicious)
    assert case[0]["hands_off_planning"]["calls_spent"] == 1


def test_nonplanned_categorical_and_risk_controls_remain_anchored(case):
    before = deepcopy(case[1])
    record, _ = invoke(case)
    anchor = before["candidates"]["1"]["strategies"][0]
    planned = record["plan"]["strategies"][0]
    assert set(record["plan"]["domains"]["scan"]) == {"hold_sessions"}
    assert planned["config"] == anchor["config"]
    assert planned["execution_policy"] == anchor["execution_policy"]
    assert planned["execution_policy"]["risk_limits"] == case[2]["risk_limits"]
    assert record["anchor_params"]["scan.execution_choice"] == "percent"
    assert record["anchor_params"]["portfolio.allocation_mode"] == "original"
    assert record["plan"]["context_id"]
    assert case[2]["signal_id"] == "a" * 64


def test_compiled_plan_cannot_smuggle_changed_risk_or_capital(case):
    before = deepcopy(case[1])

    def altered(evidence):
        plan = compile_plan(proposal(), evidence["planning_context"])
        plan["strategies"][0]["config"]["initial_capital"] *= 10
        plan["strategies"][0]["execution_policy"]["risk_limits"]["max_trade_risk_pct"] = 99
        return plan

    with pytest.raises(ValueError, match="compiled research plan changed"):
        invoke(case, altered)
    assert case[1] == before
    assert case[0]["hands_off_planning"]["calls_spent"] == 1


def test_trace_counts_actual_work_under_unchanged_global_allowance(case):
    invoke(case)
    trace = runtime.trace(case[0])
    assert trace["allowance"] == {"proposals": 408, "max_simulations": 512}
    assert trace["attempts"] == trace["simulations"] == 3
    assert trace["proposals"] == 2
    assert trace["calls_spent"] == 1
    saved = deepcopy(trace)
    case[0]["hands_off_planning"]["records"]["fit1:2"]["plan"]["reason"] = "later mutation"
    assert trace == saved


def test_compiled_domains_drive_real_optuna_vectorbt_without_changing_source_or_risk(case):
    pytest.importorskip("vectorbt")
    from test_vectorbt_portfolio import snapshot

    from research.connectors.optuna_portfolio import run_search
    from research.connectors.vectorbt_portfolio import evaluate

    record, _ = invoke(case)
    rows = deepcopy(record["plan"]["strategies"])
    days = [
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-12",
        "2026-01-13",
        "2026-01-14",
    ]
    prices = snapshot(symbols=("AAA",), days=days)
    signals = [{"symbol": "AAA", "date": days[0], "row": 2}]
    rows[0]["signals"] = signals
    before = deepcopy(rows)
    capital = rows[0]["config"]["initial_capital"]
    result = run_search(
        rows,
        prices,
        capital,
        {"sampler": "grid", "trials": 3, "seed": 0, "objective": "balanced"},
        evaluate=evaluate,
        execution={"engine": "vectorbt", "engine_version": "0.28.5"},
    )
    assert len(result["experiment"]["rows"]) == 3
    assert {
        r["strategies"][0]["config"]["hold_sessions"] for r in result["experiment"]["rows"]
    } == {2, 4, 6}
    assert result["summary"]["accepted_trades"] == 1
    assert result["strategies"][0]["execution_policy"] == before[0]["execution_policy"]
    assert rows == before
