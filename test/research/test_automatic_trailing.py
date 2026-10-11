"""Versioned trailing activation uses complete native portfolio evaluations."""

from collections import OrderedDict
from copy import deepcopy

import pytest
from test_automatic_hypotheses import Engine, execute
from test_joint_stock_market import freeze_context
from test_joint_stock_market import prepared as joint_prepared

from research import automatic_trailing as trailing
from research.automatic_hypotheses import (
    JOINT_VERSION,
    TRAILING_VERSION,
    _gate,
    _screening,
    checkpoint_rows,
    policy_for_version,
)
from research.automatic_protocol import automatic_portfolio, compile_recipe, slice_period
from research.connectors.optuna_portfolio import _prepare, _settings
from research.joint_stock_market import apply, catalog, markers
from research.portfolio import fingerprint

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(autouse=True)
def no_optional_plots(monkeypatch):
    monkeypatch.setattr(
        "research.study_analysis.build_study_analysis", lambda *a, **k: {"charts": []}
    )


def prepared(sessions=160, *, enabled=False, distance=3.0):
    value = joint_prepared(sessions)
    cfg = value["portfolio"]["strategies"][0]["config"]
    cfg.update(trailing_enabled=enabled, trailing_pct=distance)
    value["portfolio"]["automatic_research"] = {"version": TRAILING_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["strategies"][0].update(deepcopy(value["portfolio"]["strategies"][0]))
    value["automatic_recipe"] = compile_recipe(value)
    return value


@pytest.mark.parametrize("distance", [0, 0.01, 3, 99])
def test_conditional_states_are_positive_bounded_and_off_has_one_identity(distance):
    value = prepared(sessions=60, distance=distance)
    spec = value["automatic_recipe"]["trailing_research"]
    states = spec["strategies"][0]["choices"]
    assert states[0] == 0 and 2 <= len(states) <= 8
    assert states == sorted(set(states)) and all(0 < item <= 99 for item in states[1:])
    assert "trailing_pct" not in value["strategies"][0]["search"]
    settings = _settings(value["strategies"])
    source = deepcopy(settings)
    chosen = trailing.apply_choices(settings, {"scanner.trailing_choice": 0.0}, spec)
    assert chosen[0]["config"]["trailing_enabled"] is False
    assert chosen[0]["config"]["trailing_pct"] == 0.0
    assert settings == source
    assert value["automatic_recipe"]["baseline"][0]["config"]["trailing_pct"] == distance
    with pytest.raises(ValueError, match="frozen choices"):
        trailing.apply_choices(settings, {"scanner.trailing_choice": True}, spec)


def test_every_family_screens_off_low_and_high_before_refining_with_fixed_budget():
    value = prepared(sessions=60)
    _, axes = _prepare(value["strategies"])
    spec = value["automatic_recipe"]["trailing_research"]
    numeric = deepcopy(axes)
    axes.update(trailing.axes(spec))
    screened = _screening(catalog(), axes, policy=policy_for_version(TRAILING_VERSION))
    families = {row["id"]: row["family"] for row in catalog()}
    assert len(screened) == 96
    for family in set(families.values()):
        rows = [row for row in screened if families[row["hypothesis"]] == family]
        assert [row["scanner.trailing_choice"] for row in rows] == [
            0.0,
            spec["strategies"][0]["choices"][1],
            spec["strategies"][0]["choices"][-1],
        ]
    assert trailing.grid_size(numeric, spec) > 0
    assert value["automatic_recipe"]["budget"]["proposals"] == 128
    assert value["automatic_recipe"]["budget"]["max_simulations"] == 320


def test_v6_activation_cache_retains_causal_membership_and_rebinds_execution():
    value = prepared()
    current = slice_period(value, "search")
    stock, hypothesis = value["automatic_stock_market_context"], catalog()[1]
    calls = []

    def counted(*args):
        calls.append(1)
        return apply(*args)

    cache = OrderedDict()
    args = {
        "apply_fn": counted,
        "markers_fn": markers,
        "calculation_version": "research-joint-stock-market-features-v1",
        "trailing_activation": True,
    }
    _, source_receipt = _gate(current, stock, hypothesis, cache, "search", **args)
    changed = deepcopy(current)
    changed["strategies"][0]["config"].update(trailing_enabled=True, trailing_pct=2.0)
    cached = _gate(changed, stock, hypothesis, cache, "search", **args)
    direct = apply(changed, stock, hypothesis)
    assert cached[0] == direct[0]
    for key in ("membership", "counts", "execution_inputs_id", "conditioned_strategies_id"):
        assert cached[1][key] == direct[1][key]
    # Subgate IDs are the source of reused causal membership, not a claim that
    # those indicator gates were recomputed against another exit configuration.
    assert cached[1]["stock_gate_id"] == source_receipt["stock_gate_id"]
    assert cached[1]["execution_inputs_id"] != source_receipt["execution_inputs_id"]
    assert len(calls) == 1
    _gate(changed, stock, hypothesis, cache, "search", **{**args, "trailing_activation": False})
    assert len(calls) == 2
    changed["strategies"][0]["config"]["entry_time"] = "10:00"
    _gate(changed, stock, hypothesis, cache, "search", **args)
    assert len(calls) == 3


@pytest.mark.parametrize("enabled", [False, True])
def test_stability_challenges_activation_and_only_active_distances(enabled):
    value = prepared(sessions=60, enabled=enabled)
    spec = value["automatic_recipe"]["trailing_research"]
    distance = spec["strategies"][0]["choices"][2] if enabled else 0.0
    settings = trailing.apply_choices(
        _settings(value["strategies"]), {"scanner.trailing_choice": distance}, spec
    )
    original = deepcopy(settings)
    result = trailing.stability_neighbors(settings, value["automatic_recipe"]["ranges"], spec)
    assert result["neighbors"][0]["axis"] == "trailing_enabled"
    assert result["neighbors"][0]["strategies"][0]["config"]["trailing_enabled"] is not enabled
    assert any(row["axis"] == "trailing_pct" for row in result["neighbors"]) is enabled
    assert any(row["axis"] == "trailing_pct" for row in result["neighbors"][:4]) is enabled
    assert len({row["config_id"] for row in result["neighbors"]}) == len(result["neighbors"])
    assert settings == original


def test_pause_resume_replays_conditional_params_and_preserves_unchanged_baseline():
    value = prepared()
    original = deepcopy(value)
    engine = Engine(value)
    _, partial = execute(value, engine, stop=lambda state: len(state["trials"]) == 41)
    committed = len(engine.calls)
    assert checkpoint_rows(partial)
    result, final = execute(value, engine, saved=partial)
    assert final["trials"][:41] == partial["trials"]
    assert len(final["trials"]) == 128
    assert committed < len(engine.calls) == final["simulations"] <= 320
    assert result["automatic_recipe"]["baseline"][0]["config"]["trailing_pct"] == 3.0
    assert result["automatic_recipe"]["baseline"][0]["config"]["trailing_enabled"] is False
    for row in checkpoint_rows(final):
        cfg = row["strategies"][0]["config"]
        assert cfg["trailing_enabled"] == (cfg["trailing_pct"] > 0)
    repeated, _ = execute(
        value, lambda *a, **k: pytest.fail("Completed resume recalculated"), saved=final
    )
    assert repeated == result
    assert value == original


def test_native_vectorbt_evaluates_enabled_and_disabled_whole_portfolios():
    from research.connectors.vectorbt_portfolio import evaluate

    value, calls = prepared(sessions=60), []

    def native(rows, snapshot, capital, progress=None):
        calls.append(deepcopy(rows))
        return evaluate(rows, snapshot, capital, progress=progress)

    result, state = execute(value, native)
    assert len(calls) == state["simulations"] <= 320
    assert {rows[0]["config"]["trailing_enabled"] for rows in calls} == {False, True}
    assert result["automatic_research"]["status"] != "supported"
    assert result["automatic_research"]["selected_is_baseline"]
    assert result["strategies"][0]["config"]["trailing_pct"] == 3.0
    assert result["strategies"][0]["config"]["trailing_enabled"] is False


@pytest.mark.parametrize("favored", [False, True])
def test_activation_is_selected_from_scores_and_must_survive_later_checks(favored):
    value = prepared(enabled=not favored)
    reference = Engine(value)

    def scored(rows, snapshot, capital, progress=None):
        report = reference(rows, snapshot, capital, progress)
        report["summary"]["net_return_pct"] = (
            20.0 if rows[0]["config"]["trailing_enabled"] is favored else 0.0
        )
        return report

    result, state = execute(value, scored)
    assert result["automatic_research"]["status"] == "supported"
    assert result["automatic_research"]["final"]["supports"] is True
    assert result["strategies"][0]["config"]["trailing_enabled"] is favored
    assert result["automatic_recipe"]["baseline"][0]["config"]["trailing_enabled"] is not favored
    assert len(reference.calls) == state["simulations"] <= 320


def test_v5_and_earlier_policy_and_normalization_retain_their_exact_semantics():
    value = joint_prepared(sessions=60)
    before = deepcopy(value)
    assert compile_recipe(value) == value["automatic_recipe"]
    assert automatic_portfolio(value["portfolio"]) == value["portfolio"]
    assert "trailing_research" not in value["automatic_recipe"]
    assert fingerprint(before) == fingerprint(value)
    assert policy_for_version(JOINT_VERSION) == {
        "version": JOINT_VERSION,
        "condition_version": "research-joint-entry-conditions-v1",
        "calculation_version": "research-joint-stock-market-features-v1",
        "proposals": 128,
        "max_simulations": 320,
        "family_screen": 3,
        "max_hypotheses": 92,
        "max_families": 32,
    }


def test_v6_minute_recipe_searches_only_the_active_deadline_and_retains_clocks():
    from test_automatic_protocol import evidence

    value = evidence(signal_sessions=60, minute=True, config={"entry_time": "09:16"})
    value["portfolio"]["automatic_research"] = {"version": TRAILING_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["strategies"][0].update(deepcopy(value["portfolio"]["strategies"][0]))
    value = freeze_context(value)
    search = value["strategies"][0]["search"]
    assert "hold_minutes" in search and "hold_sessions" not in search
    assert "trailing_pct" not in search
    assert value["automatic_recipe"]["baseline"][0]["config"]["entry_time"] == "09:16"
    assert value["automatic_recipe"]["budget"]["max_simulations"] == 320
    assert slice_period(value, "search")["snapshot"]["provenance"]["interval"] == "1m"


@pytest.mark.parametrize("count", [2, 8])
def test_independent_strategy_states_preserve_shared_account_risk_and_inputs(count):
    value = prepared(sessions=60)
    original = _settings(value["strategies"])[0]
    rows = []
    for index in range(count):
        row = deepcopy(original)
        row.update(id=f"strategy{index}", allocation_pct=100 / count)
        rows.append(row)
    before = deepcopy(rows)
    spec = trailing.recipe(rows)
    params = {
        f"{row['id']}.trailing_choice": 0.0
        if index % 2 == 0
        else spec["strategies"][index]["choices"][2]
        for index, row in enumerate(rows)
    }
    selected = trailing.apply_choices(rows, params, spec)
    assert {row["config"]["trailing_enabled"] for row in selected} == {False, True}
    for old, new in zip(before, selected, strict=True):
        assert new["allocation_pct"] == old["allocation_pct"]
        for key, setting in old["config"].items():
            if key not in {"trailing_enabled", "trailing_pct"}:
                assert new["config"][key] == setting
    assert rows == before
    assert len(trailing.axes(spec)) == count
    neighborhood = trailing.stability_neighbors(
        selected,
        [{"id": row["id"], "search": value["strategies"][0]["search"]} for row in rows],
        spec,
    )
    assert {"trailing_enabled", "trailing_pct"} <= {
        row["axis"] for row in neighborhood["neighbors"][:4]
    }


@pytest.mark.parametrize("minute", [False, True])
def test_native_trailing_state_changes_daily_and_minute_ledgers_causally(minute):
    from test_vectorbt_portfolio import DAYS, candle, signal, snapshot, strategy

    from research.connectors.vectorbt_portfolio import evaluate
    from research.engine import validate_config

    prices = snapshot(minute=minute)
    if minute:
        slots = prices["timeline"]
        prices["bars"]["AAA"][slots[1]] = candle(100, 120, 99, 115)
        prices["bars"]["AAA"][slots[2]] = candle(113, 115, 110, 112)
        row = strategy(
            "scanner",
            100,
            signals=[signal(timestamp="09:15")],
            trade_horizon="intraday",
            hold_minutes=3,
        )
    else:
        prices["bars"]["AAA"][DAYS[1]] = candle(100, 120, 99, 115)
        prices["bars"]["AAA"][DAYS[2]] = candle(113, 115, 110, 112)
        row = strategy("scanner", 100, hold_sessions=2)
    row["config"] = validate_config(
        {
            **row["config"],
            "target_pct": 50,
            "stop_pct": 20,
            "trailing_pct": 5.0,
            "trailing_enabled": False,
        }
    )
    spec = trailing.recipe([row])
    disabled = trailing.apply_choices([row], {"scanner.trailing_choice": 0.0}, spec)
    enabled = trailing.apply_choices([row], {"scanner.trailing_choice": 5.0}, spec)
    off = evaluate(disabled, prices, 1000)["ledger"][0]
    on = evaluate(enabled, prices, 1000)["ledger"][0]
    assert on["outcome"] == "trailing_stop" and on["exit_price"] == 113
    assert off["outcome"] == "hold" and off["exit_price"] == 100
    assert off["entry_date"] == on["entry_date"]
