"""Bounded global priority search with real causal gates and seeded Optuna.

Synthetic account economics exercise selection and recovery, not financial edge.
The independent native execution tests own fills, risk and chronology correctness.
"""

from copy import deepcopy

import pytest
from test_automatic_walk_forward import execute
from test_execution_aware_research import ExecutionAccountEngine
from test_execution_aware_research import prepared as execution_prepared

from research.automatic_hypotheses import policy_for_version
from research.automatic_protocol import PRIORITY_VERSION, automatic_portfolio, compile_recipe
from research.automatic_walk_forward import FITS, _screening_program
from research.connectors.optuna_portfolio import _fingerprint, _prepare
from research.execution_features import prepare as prepare_features
from research.execution_policy import normalize_policy
from research.execution_search import (
    ENTRY_PRIORITIES,
    PRIORITY_AXIS,
    PRIORITY_SEARCH_VERSION,
    apply_choices,
    axes,
    descriptor,
    original_sizing,
)
from research.method_registry import catalog
from research.report_contract import fingerprint

pytestmark = pytest.mark.timeout(300)


def prepared():
    value = execution_prepared()
    value["portfolio"]["automatic_research"] = {"version": PRIORITY_VERSION}
    for row in value["portfolio"]["strategies"]:
        row["execution_policy"] = normalize_policy(
            {
                **row["execution_policy"],
                "version": "research-execution-aware-v2",
                "entry_priority": "source",
            }
        )
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    for key in ("automatic_recipe", "execution_features"):
        value.pop(key, None)
    value = prepare_features(value, value["automatic_stock_market_context"])
    value["automatic_recipe"] = compile_recipe(value)
    return value


class PriorityAccountEngine(ExecutionAccountEngine):
    def __call__(self, rows, snapshot, capital, progress=None):
        result = super().__call__(rows, snapshot, capital, progress)
        priority = rows[0]["execution_policy"]["entry_priority"]
        bonus = {"source": 0, "reversed_source": 5, "lower_stop_risk": 15}[priority]
        curve = result["equity_curve"]
        for index, point in enumerate(curve):
            extra = float(capital) * bonus / 100 * index / (len(curve) - 1)
            point["equity"] += extra
            point["cash"] += extra
        peak = float(capital)
        for point in curve:
            peak = max(peak, point["equity"])
            point["drawdown_pct"] = (peak - point["equity"]) / peak * 100
        result["summary"]["net_return_pct"] += bonus
        result["summary"]["max_drawdown_pct"] = max(point["drawdown_pct"] for point in curve)
        return result


@pytest.fixture(scope="module")
def completed():
    value = prepared()
    original = deepcopy(value)
    events = []
    latest = None

    def checkpoint(state):
        nonlocal latest
        latest = state

    def check(call):
        if call["period"].startswith("check"):
            fit = "fit" + call["period"][-1]
            assert len(latest["fits"][fit]["trials"]) == 68
            assert latest["fits"][fit]["sizing_control"] is not None
        if call["period"] == "final":
            assert latest["selection"] is not None
            assert len(latest["fits"]["search"]["trials"]) == 68

    engine = PriorityAccountEngine(value, on_call=check)
    result, state = execute(value, engine, observe=events.append, on_checkpoint=checkpoint)
    assert value == original
    return value, result, state, engine.calls, events


def test_all_four_native_gated_fits_search_one_frozen_priority_before_final(completed):
    value, result, state, calls, events = completed
    assert state["version"] == PRIORITY_VERSION
    assert result["automatic_research"]["counts"]["proposals"] == 272
    assert len(calls) == state["simulations"] <= state["attempts"] <= 384
    assert len([call for call in calls if call["period"] == "final"]) == 2
    assert len(result["experiment"]["trials"]) == 68
    for fit in state["fits"].values():
        assert len(fit["trials"]) == 68
        assert {trial["params"][PRIORITY_AXIS] for trial in fit["trials"]} == set(ENTRY_PRIORITIES)
        assert sum(trial["phase"] == "joint_refinement" for trial in fit["trials"]) >= 18
        assert fit["sizing_control"]["strategies"] == original_sizing(
            fit["selection"]["strategies"], value["automatic_recipe"]["execution_research"]
        )
        for trial in fit["trials"]:
            chosen = fit["candidates"][trial["config_id"]]["strategies"]
            assert {row["execution_policy"]["entry_priority"] for row in chosen} == {
                trial["params"][PRIORITY_AXIS]
            }
    findings = result["automatic_research"]["execution_research"]
    assert findings["version"] == "research-execution-findings-v2"
    assert findings["entry_priorities"] == list(ENTRY_PRIORITIES)
    assert findings["selected_entry_priority"] in ENTRY_PRIORITIES
    assert [row["period"] for row in findings["entry_priority_counts"]] == list(FITS)
    for row in findings["entry_priority_counts"]:
        assert sum(choice["proposed"] for choice in row["choices"]) == 68
        assert all(
            choice["evaluated"] + choice["reused"] == choice["proposed"]
            for choice in row["choices"]
        )
    assert [row["period"] for row in findings["sizing_controls"]] == list(FITS)
    assert [row["number"] for row in events if row["kind"] == "proposal_started"] == list(
        range(272)
    )


def test_pause_inside_priority_screens_and_exact_completed_recovery(completed):
    value, expected, complete, expected_calls, _ = completed
    engine = PriorityAccountEngine(value)
    _, partial = execute(
        value,
        engine,
        stop=lambda state: (
            state["active_fit"] == "fit1" and len(state["fits"]["fit1"]["trials"]) == 45
        ),
    )
    assert partial["fits"]["fit1"]["trials"][-1]["params"][PRIORITY_AXIS] == "reversed_source"
    resumed, state = execute(value, engine, saved=partial)
    assert resumed == expected and state == complete and engine.calls == expected_calls
    repeated, _ = execute(
        value,
        lambda *a, **k: pytest.fail("Completed priority resume repeated accounting"),
        saved=state,
    )
    assert repeated == expected
    changed = deepcopy(partial)
    changed["fits"]["fit1"]["trials"][-1]["params"][PRIORITY_AXIS] = "source"
    changed["checkpoint_id"] = _fingerprint(
        {key: item for key, item in changed.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="changed|different|replay"):
        execute(
            value, lambda *a, **k: pytest.fail("Changed priority called account"), saved=changed
        )


def test_one_account_axis_and_controls_preserve_selected_priority():
    value = prepared()
    value["strategies"][0]["allocation_pct"] = 50
    value["strategies"].append({**deepcopy(value["strategies"][0]), "id": "second"})
    value["portfolio"]["strategies"] = [
        {key: deepcopy(item) for key, item in row.items() if key != "signals"}
        for row in value["strategies"]
    ]
    spec = descriptor(value)
    assert spec["version"] == PRIORITY_SEARCH_VERSION
    assert axes(spec)[PRIORITY_AXIS]["choices"] == list(ENTRY_PRIORITIES)
    assert len([key for key in axes(spec) if key.endswith("entry_priority")]) == 1
    row = value["strategies"][0]
    for priority in ENTRY_PRIORITIES:
        params = {
            **{f"{item['id']}.execution_choice": "atr_1_2" for item in value["strategies"]},
            **{f"{item['id']}.portfolio_choice": "stop_risk_50" for item in value["strategies"]},
            PRIORITY_AXIS: priority,
        }
        chosen = apply_choices(value["strategies"], params, spec)
        assert {item["execution_policy"]["entry_priority"] for item in chosen} == {priority}
        assert {
            item["execution_policy"]["entry_priority"] for item in original_sizing(chosen, spec)
        } == {priority}
        assert chosen[0]["allocation_pct"] <= row["allocation_pct"]
    for bad in ("alphabetical", None, [], True):
        with pytest.raises(ValueError, match="priority"):
            apply_choices(value["strategies"], {**params, PRIORITY_AXIS: bad}, spec)
    forged = deepcopy(spec)
    forged["entry_priorities"].reverse()
    forged["id"] = fingerprint({key: item for key, item in forged.items() if key != "id"})
    with pytest.raises(ValueError, match="priority choices changed"):
        axes(forged)


def test_maximum_clock_union_keeps_eighteen_refinements_and_lost_attempt_reserve():
    value = prepared()
    spec = value["automatic_recipe"]["execution_research"]
    _, numeric = _prepare(value["strategies"])
    combined = {**numeric, **axes(spec)}
    timing = {"strategies": []}
    for index in range(4):
        identifier = f"clock{index}"
        branches = [{"id": key} for key in ("original", "early", "late")]
        timing["strategies"].append({"id": identifier, "branches": branches})
        combined[f"{identifier}.timing_choice"] = {
            "type": "categorical",
            "choices": [row["id"] for row in branches],
        }
    screens = _screening_program(
        PRIORITY_VERSION, value["automatic_recipe"]["hypothesis_catalog"], combined, timing, spec
    )
    assert len(screens) == 50
    assert [row[PRIORITY_AXIS] for row in screens[-2:]] == list(ENTRY_PRIORITIES[1:])
    assert all(row[PRIORITY_AXIS] == "source" for row in screens[:-2])
    assert 68 - len(screens) == 18
    budget = policy_for_version(PRIORITY_VERSION)
    assert budget["proposals"] == 272 and budget["max_simulations"] == 384
    assert 4 * (68 + 2) + 3 * 4 + 1 + 2 + 64 <= budget["max_simulations"]
    registry = catalog(PRIORITY_VERSION)
    assert registry["hypothesis_count"] == 108 and registry["family_count"] == 32


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_preflight_declares_global_priority_and_new_budget_without_more_features(interval):
    from test_automatic_catalog import request

    from services.research_automatic_catalog import build_plan

    plan = build_plan(request(version=PRIORITY_VERSION), interval=interval)
    assert plan["summary"]["condition_presets"] == 108
    assert plan["summary"]["max_simulations"] == 384
    budget = plan["search"]["budget"]
    assert budget["proposals"] == 272 and budget["proposals_per_fit"] == 68
    assert budget["screening_proposals"] + budget["refinement_proposals"] == 272
    assert budget["entry_priority_probe_limit_per_fit"] == 2


def test_coverage_reconstructs_global_axis_and_rejects_resealed_changed_priority():
    from test_method_coverage import prepared_metadata, seal

    from research.method_coverage import build

    metadata = prepared_metadata(version=PRIORITY_VERSION, proposals=68)
    coverage = build(*metadata)
    assert coverage["source"]["recipe_version"] == PRIORITY_VERSION
    assert coverage["fits"][0]["proposal_limit"] == 68
    assert coverage["fits"][0]["status"] == "complete"
    assert coverage["totals"]["proposals"] == 68
    assert coverage["entry_priority"]["choices"] == list(ENTRY_PRIORITIES)
    assert sum(row["proposed"] for row in coverage["entry_priority"]["fits"][0]["choices"]) == 68
    changed = deepcopy(metadata)
    trial = changed[1]["fits"]["fit1"]["trials"][44]
    trial["params"][PRIORITY_AXIS] = "lower_stop_risk"
    seal(changed[1], "checkpoint_id")
    with pytest.raises(ValueError, match="proposal settings changed"):
        build(*changed)
    changed = deepcopy(metadata)
    changed[0]["search"]["execution_research"]["entry_priorities"].reverse()
    seal(changed[0]["search"]["execution_research"])
    seal(changed[0])
    with pytest.raises(ValueError, match="priority choices changed"):
        build(*changed)


def test_v10_frozen_fixture_and_registry_never_acquire_priority_defaults():
    legacy = execution_prepared()
    assert fingerprint(legacy) == "a8edf64efdb406c515cf0e4447b36933abf24204d38f9f98914da37e2b9d40c0"
    assert (
        fingerprint(catalog("automatic-hypotheses-v10"))
        == "f0da03681398203274d7e7fdd0fb10add27bd443d10ab41ff3af105a259c2f6e"
    )
    recipe = legacy["automatic_recipe"]
    assert recipe["execution_research"]["version"] == "research-execution-search-v1"
    assert PRIORITY_AXIS not in axes(recipe["execution_research"])
    assert recipe["budget"]["proposals"] == 256
    assert all("entry_priority" not in row["execution_policy"] for row in legacy["strategies"])
