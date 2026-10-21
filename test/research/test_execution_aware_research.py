"""Real Optuna over finite execution domains and synthetic account economics.

Native ATR preparation/attachment is retained. Deliberately favorable synthetic
accounts establish chronology, policy plumbing and recovery, not sizing alpha.
"""

from copy import deepcopy
from unittest.mock import patch

import pytest
from test_automatic_walk_forward import AccountEngine, execute
from test_expanded_walk_forward import prepared as method_prepared
from test_expanded_walk_forward import synthetic_prepare_method_gate

from research.automatic_hypotheses import checkpoint_rows, policy_for_version
from research.automatic_protocol import EXECUTION_VERSION, automatic_portfolio, compile_recipe
from research.automatic_walk_forward import FITS, METHOD_PROBES, _screening_program
from research.connectors.optuna_portfolio import _fingerprint, _prepare, _settings
from research.execution_features import prepare as prepare_features
from research.execution_policy import normalize_limits, normalize_policy
from research.execution_search import apply_choices, axes, descriptor, original_sizing
from research.report_contract import fingerprint, settings_identity

pytestmark = pytest.mark.timeout(300)


def prepared():
    value = method_prepared()
    value["portfolio"]["automatic_research"] = {"version": EXECUTION_VERSION}
    value["portfolio"]["risk_limits"] = normalize_limits({"max_open_positions": 7})
    for row in value["portfolio"]["strategies"]:
        row["execution_policy"] = normalize_policy(
            {"risk_limits": value["portfolio"]["risk_limits"]}
        )
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value.pop("automatic_recipe", None)
    value = prepare_features(value, value["automatic_stock_market_context"])
    value["automatic_recipe"] = compile_recipe(value)
    return value


class ExecutionAccountEngine(AccountEngine):
    """Synthetic sizing-sensitive economics with internally consistent marks."""

    def __call__(self, rows, snapshot, capital, progress=None):
        result = super().__call__(rows, snapshot, capital, progress)
        policy = rows[0]["execution_policy"]
        profit = result["summary"]["net_return_pct"]
        if policy["exit_mode"] == "atr":
            profit += policy["atr_target_multiple"]
        if policy["sizing"] == "stop_risk":
            profit += 50  # Synthetic diagnostic trigger, never a financial promise.
        ratio = rows[0]["allocation_pct"] / self.recipe["baseline"][0]["allocation_pct"]
        profit *= ratio
        risk = 1.0 if policy["sizing"] == "stop_risk" else 2.0
        curve = result["equity_curve"]
        initial, low, final = (
            float(capital),
            float(capital) * (1 - risk / 100),
            float(capital) * (1 + profit / 100),
        )
        peak = initial
        for index, point in enumerate(curve):
            equity = initial if index == 0 else low + (final - low) * (index - 1) / (len(curve) - 2)
            peak = max(peak, equity)
            point.update(equity=equity, cash=equity, drawdown_pct=(peak - equity) / peak * 100)
        result["summary"].update(
            net_return_pct=profit, max_drawdown_pct=max(point["drawdown_pct"] for point in curve)
        )
        return result


@pytest.fixture(autouse=True)
def isolate_gate(monkeypatch):
    monkeypatch.setattr(
        "research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate
    )


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
            assert len(latest["fits"][fit]["trials"]) == 64
            assert latest["fits"][fit]["selection"] is not None
            assert latest["fits"][fit]["sizing_control"] is not None
        if call["period"] == "final":
            assert latest["selection"] is not None
            assert len(latest["fits"]["search"]["trials"]) == 64

    engine = ExecutionAccountEngine(value, on_call=check)
    with patch(
        "research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate
    ):
        result, state = execute(value, engine, observe=events.append, on_checkpoint=checkpoint)
    assert value == original
    return value, result, state, engine.calls, events


def test_full_four_fits_use_registered_policies_and_separate_final_accounts(completed):
    value, result, state, calls, events = completed
    assert state["version"] == EXECUTION_VERSION
    assert result["automatic_research"]["counts"]["proposals"] == 256
    assert len(calls) == state["simulations"] <= state["attempts"] <= 384
    assert len(result["experiment"]["trials"]) == 64
    assert result["experiment"]["counts"]["proposed"] == 64
    assert (
        result["experiment"]["execution_research"]
        == value["automatic_recipe"]["execution_research"]
    )
    assert len([call for call in calls if call["period"] == "final"]) == 2
    for fit in state["fits"].values():
        assert len(fit["trials"]) == 64
        assert tuple(row["params"]["hypothesis"] for row in fit["trials"][32:36]) == METHOD_PROBES
        assert sum(row["phase"] == "joint_refinement" for row in fit["trials"]) >= 16
        policies = [
            candidate["strategies"][0]["execution_policy"]
            for candidate in fit["candidates"].values()
        ]
        assert {policy["exit_mode"] for policy in policies} == {"percent", "atr"}
        assert {policy["sizing"] for policy in policies} == {"allocation", "stop_risk"}
        assert all(
            policy["risk_limits"] == value["portfolio"]["risk_limits"] for policy in policies
        )
        assert fit["sizing_control"]["role"] == "matched_original_sizing_development"
        assert fit["sizing_control"]["sizing_changed"]
        assert fit["sizing_control"]["selected_config_id"] == fit["selection"]["config_id"]
        assert fit["sizing_control"]["strategies"] == original_sizing(
            fit["selection"]["strategies"], value["automatic_recipe"]["execution_research"]
        )
    controls = result["automatic_research"]["execution_research"]["sizing_controls"]
    assert [row["period"] for row in controls] == list(FITS)
    assert all(row["period"] != "final" for row in controls)
    started = [row for row in events if row["kind"] == "proposal_started"]
    assert [row["number"] for row in started] == list(range(256))
    from research.study_analysis import reconstruct_study

    study, _ = reconstruct_study(result["experiment"])
    assert len(study.trials) == 64
    assert len(study.trials[0].distributions["hypothesis"].choices) == 108
    assert (
        len(
            study.trials[0]
            .distributions[f"{value['strategies'][0]['id']}.execution_choice"]
            .choices
        )
        == 5
    )


def test_atomic_policies_canonicalize_atr_and_never_increase_original_ceilings():
    value = prepared()
    spec = descriptor(value)
    original = deepcopy(value)
    identifier = value["strategies"][0]["id"]
    for execution in spec["strategies"][0]["execution_choices"]:
        for portfolio in spec["strategies"][0]["portfolio_choices"]:
            params = {
                f"{identifier}.execution_choice": execution["id"],
                f"{identifier}.portfolio_choice": portfolio["id"],
            }
            rows = apply_choices(value["strategies"], params, spec)
            assert rows[0]["allocation_pct"] <= value["strategies"][0]["allocation_pct"]
            assert (
                rows[0]["config"]["order_size_pct"]
                <= value["strategies"][0]["config"]["order_size_pct"]
            )
            assert rows[0]["execution_policy"]["risk_limits"] == value["portfolio"]["risk_limits"]
    params = {
        f"{identifier}.execution_choice": "atr_1_2",
        f"{identifier}.portfolio_choice": "original",
    }
    changed = deepcopy(value["strategies"])
    changed[0]["config"].update(target_pct=29, stop_pct=17)
    first, second = (
        apply_choices(value["strategies"], params, spec),
        apply_choices(changed, params, spec),
    )
    assert settings_identity(_settings(first)) == settings_identity(_settings(second))
    assert value == original
    with pytest.raises(ValueError, match="frozen choices"):
        apply_choices(
            value["strategies"], {**params, f"{identifier}.portfolio_choice": "double_cash"}, spec
        )


def test_pause_during_execution_probes_and_resume_preserves_every_account(completed):
    value, result, complete, control_calls, _ = completed
    engine = ExecutionAccountEngine(value)
    _, partial = execute(
        value,
        engine,
        stop=lambda state: (
            state["active_fit"] == "fit1" and len(state["fits"]["fit1"]["trials"]) == 38
        ),
    )
    assert partial["fits"]["fit1"]["trials"][-1]["params"][
        f"{value['strategies'][0]['id']}.execution_choice"
    ].startswith("atr_")
    assert checkpoint_rows(partial)
    resumed, state = execute(value, engine, saved=partial)
    assert resumed == result
    assert state == complete
    assert engine.calls == control_calls
    repeated, _ = execute(
        value,
        lambda *args, **kwargs: pytest.fail("Completed v10 resume repeated native account"),
        saved=state,
    )
    assert repeated == result
    forged = deepcopy(state)
    forged["fits"]["fit1"]["sizing_control"]["score_delta"] += 1
    forged["checkpoint_id"] = _fingerprint(
        {key: item for key, item in forged.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="sizing.*control changed"):
        execute(
            value,
            lambda *args, **kwargs: pytest.fail("Changed control called engine"),
            saved=forged,
        )


def test_fixed_program_reserves_execution_and_portfolio_probes_then_sixteen_refinements():
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
    program = _screening_program(
        EXECUTION_VERSION, value["automatic_recipe"]["hypothesis_catalog"], combined, timing, spec
    )
    assert len(program) == 48
    identifier = value["strategies"][0]["id"]
    assert [row[f"{identifier}.execution_choice"] for row in program[40:44]] == [
        "atr_1_2",
        "atr_1_3",
        "atr_2_3",
        "atr_2_4",
    ]
    assert [row[f"{identifier}.portfolio_choice"] for row in program[44:]] == [
        "allocation_75",
        "allocation_50",
        "stop_risk",
        "stop_risk_50",
    ]
    assert 64 - len(program) == 16
    assert policy_for_version(EXECUTION_VERSION)["max_simulations"] == 384
    assert value["automatic_recipe"]["budget"]["proposals"] == 256
    assert value["automatic_recipe"]["execution_features_id"] == value["execution_features"]["id"]


def test_risk_limits_and_feature_binding_are_frozen_before_the_first_score():
    value = prepared()
    changed = deepcopy(value)
    changed["portfolio"]["risk_limits"]["max_open_positions"] += 1
    with pytest.raises(ValueError, match="baseline|limits|recipe"):
        execute(changed, lambda *args, **kwargs: pytest.fail("Changed risk limits evaluated"))
    changed = deepcopy(value)
    changed["execution_features"]["id"] = "0" * 64
    with pytest.raises(ValueError, match="recipe changed"):
        execute(changed, lambda *args, **kwargs: pytest.fail("Changed features evaluated"))
    changed = deepcopy(value["automatic_recipe"]["execution_research"])
    changed["strategies"][0]["portfolio_choices"][1]["fraction"] = 2
    changed["id"] = fingerprint({key: item for key, item in changed.items() if key != "id"})
    with pytest.raises(ValueError, match="choices changed"):
        axes(changed)
