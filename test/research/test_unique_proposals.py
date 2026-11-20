"""Real seeded Optuna proposal mechanics, with no financial engine or data run."""

from copy import deepcopy

import optuna
import pytest

from research.unique_proposals import (
    ProposalSpaceExhausted,
    alternatives,
    draw,
    effective_identity,
)


def settings(params):
    return [
        {
            "id": "scan",
            "name": "Saved scan",
            "allocation_pct": 100,
            "config": {
                "initial_capital": 100000,
                "hold_sessions": int(params["hold"]),
                "stop_pct": float(params.get("stop", 2)),
                "target_pct": 8,
                "trailing_enabled": False,
                "trailing_pct": 0,
            },
            "execution_policy": {"exit_mode": "percent", "sizing": "allocation"},
        }
    ]


def study():
    # A real Optuna sampler deliberately repeats the incumbent. Enqueued trials
    # remain authoritative and must store/score their actual queued parameters.
    sampler = optuna.samplers.PartialFixedSampler(
        {"hold": 2, "stop": 2}, optuna.samplers.TPESampler(seed=17)
    )
    return optuna.create_study(direction="maximize", sampler=sampler)


def sample(trial):
    return {"hold": trial.suggest_int("hold", 2, 6, step=2)}


def test_duplicate_asks_are_pruned_before_one_admitted_configuration_is_scored():
    native = study()
    seen = {effective_identity(settings({"hold": 2}))}
    trial, params, rows, metadata = draw(
        native,
        sample=sample,
        build=settings,
        seen=seen,
        domains={"hold": {"min": 2, "max": 6, "step": 2}},
        seed="tiny",
    )
    assert len(metadata["sampler_rejections"]) == 8
    assert metadata["queued_unique"] is True
    assert params == native.trials[-1].params == trial.params
    assert params["hold"] in (4, 6)
    assert rows[0]["config"]["hold_sessions"] == params["hold"]
    value = params["hold"] * 11
    native.tell(trial, value)
    pruned = [t for t in native.trials if t.state == optuna.trial.TrialState.PRUNED]
    complete = [t for t in native.trials if t.state == optuna.trial.TrialState.COMPLETE]
    assert len(pruned) == 8 and all(t.value is None for t in pruned)
    assert len(complete) == 1 and complete[0].value == value
    assert complete[0].params == params


def test_every_unseen_effective_configuration_is_admitted_once_before_exhaustion():
    native, seen = study(), set()
    complete = []
    domain = {"hold": {"min": 2, "max": 6, "step": 2}, "stop": {"min": 2, "max": 4, "step": 2}}

    def paired_sample(trial):
        return {**sample(trial), "stop": trial.suggest_int("stop", 2, 4, step=2)}

    for _ in range(6):
        trial, params, rows, _ = draw(
            native, sample=paired_sample, build=settings, seen=seen, domains=domain, seed="all-six"
        )
        identity = effective_identity(rows)
        assert identity not in seen
        seen.add(identity)
        complete.append(params)
        native.tell(trial, params["hold"] + params["stop"])
    assert {(r["hold"], r["stop"]) for r in complete} == {(h, s) for h in (2, 4, 6) for s in (2, 4)}
    with pytest.raises(ProposalSpaceExhausted, match="bounded proposal generator"):
        draw(
            native, sample=paired_sample, build=settings, seen=seen, domains=domain, seed="all-six"
        )
    assert sum(t.state == optuna.trial.TrialState.COMPLETE for t in native.trials) == 6
    assert all(t.value is None for t in native.trials if t.state == optuna.trial.TrialState.PRUNED)


def test_ai_grid_is_exhausted_before_returning_to_broader_approved_domains():
    native = study()
    seen = {effective_identity(settings({"hold": 2})), effective_identity(settings({"hold": 4}))}
    ai_domain = {"hold": {"min": 2, "max": 4, "step": 2}}
    built = []

    def build(params):
        built.append(params["hold"])
        return settings(params)

    def ai_sample(trial):
        return {"hold": trial.suggest_int("hold", 2, 4, step=2)}

    trial, params, _, metadata = draw(
        native,
        sample=ai_sample,
        build=build,
        seen=seen,
        domains=ai_domain,
        broaden=(sample, {"hold": {"min": 2, "max": 6, "step": 2}}, None),
        seed="broader",
    )
    assert params == {"hold": 6}
    assert {2, 4}.issubset(set(built[:10]))
    assert metadata["proposal_source"] == "approved_domain"
    assert metadata["generation_reason"] == "ai_grid_exhausted"
    assert metadata["ai_domain_exhausted"] is True
    assert trial.params == params


def test_large_ai_grid_generation_bound_does_not_claim_full_exhaustion():
    domain = {"hold": {"min": 2, "max": 1002, "step": 2}}
    native = study()
    sampled = list(alternatives(domain, seed="large", limit=256))
    seen = {effective_identity(settings(p)) for p in sampled}
    seen.add(effective_identity(settings({"hold": 2})))

    def ai_sample(trial):
        return {"hold": trial.suggest_int("hold", 2, 1002, step=2)}

    def broad_sample(trial):
        return {"hold": trial.suggest_int("hold", 2, 1004, step=2)}

    trial, params, _, metadata = draw(
        native,
        sample=ai_sample,
        build=settings,
        seen=seen,
        domains=domain,
        broaden=(broad_sample, {"hold": {"min": 2, "max": 1004, "step": 2}}, None),
        seed="large",
    )
    assert len(seen) < 501
    assert metadata["proposal_source"] == "approved_domain"
    assert metadata["generation_reason"] == "ai_generation_bound_reached"
    assert metadata["ai_domain_exhausted"] is False
    assert trial.params == params


def test_seeded_replay_reproduces_pruned_queued_parameters_and_scores():
    def execute():
        native = study()
        seen = {effective_identity(settings({"hold": 2}))}
        receipts = []
        for _ in range(2):
            trial, params, rows, metadata = draw(
                native,
                sample=sample,
                build=settings,
                seen=seen,
                domains={"hold": {"min": 2, "max": 6, "step": 2}},
                seed="replay",
            )
            seen.add(effective_identity(rows))
            native.tell(trial, params["hold"] * 11)
            receipts.append((params, metadata))
        return receipts, [(t.number, t.state.name, t.params, t.value) for t in native.trials]

    first, second = execute(), execute()
    assert first == second
    assert sum(state == "PRUNED" for _, state, _, _ in first[1]) == 16
    assert sum(state == "COMPLETE" for _, state, _, _ in first[1]) == 2


def test_inactive_atr_percentage_exits_and_disabled_trailing_are_equivalent():
    base = settings({"hold": 4})
    base[0]["execution_policy"].update(exit_mode="atr", atr_stop_multiple=2, atr_target_multiple=4)
    changed = deepcopy(base)
    changed[0]["config"].update(stop_pct=50, target_pct=90, trailing_pct=99)
    assert effective_identity(base) == effective_identity(changed)
    assert base[0]["config"]["stop_pct"] == 2  # identity must remain pure


def test_numeric_representation_is_equivalent_without_mutating_financial_settings():
    base = settings({"hold": 4})
    after = deepcopy(base)
    after[0]["config"].update(initial_capital=100000.0, stop_pct=2, hold_sessions=4.0)
    after[0]["allocation_pct"] = 100.0
    before = deepcopy((base, after))
    assert effective_identity(base) == effective_identity(after)
    assert (base, after) == before
    after[0]["config"]["trailing_enabled"] = 0
    assert effective_identity(base) != effective_identity(after)  # bool is not number


def test_effective_numeric_identity_does_not_round_large_integers_through_float():
    base = settings({"hold": 4})
    after = deepcopy(base)
    base[0]["config"]["initial_capital"] = 10**60 + 1
    after[0]["config"]["initial_capital"] = 10**60 + 2
    assert effective_identity(base) != effective_identity(after)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_controls_fail_closed(value):
    rows = settings({"hold": 4})
    rows[0]["config"]["initial_capital"] = value
    with pytest.raises(ValueError, match="finite numeric"):
        effective_identity(rows)


@pytest.mark.parametrize(
    "change",
    [
        "holding",
        "stop",
        "target",
        "enabled_trailing",
        "allocation",
        "sizing",
        "atr_stop",
        "capital",
    ],
)
def test_active_trading_controls_remain_distinct(change):
    base = settings({"hold": 4})
    after = deepcopy(base)
    if change == "holding":
        after[0]["config"]["hold_sessions"] = 6
    elif change == "stop":
        after[0]["config"]["stop_pct"] = 3
    elif change == "target":
        after[0]["config"]["target_pct"] = 9
    elif change == "enabled_trailing":
        base[0]["config"]["trailing_enabled"] = after[0]["config"]["trailing_enabled"] = True
        after[0]["config"]["trailing_pct"] = 2
    elif change == "allocation":
        after[0]["allocation_pct"] = 80
    elif change == "sizing":
        after[0]["execution_policy"]["sizing"] = "stop_risk"
    elif change == "atr_stop":
        for rows in (base, after):
            rows[0]["execution_policy"].update(
                exit_mode="atr", atr_stop_multiple=2, atr_target_multiple=4
            )
        after[0]["execution_policy"]["atr_stop_multiple"] = 3
    elif change == "capital":
        after[0]["config"]["initial_capital"] = 200000
    assert effective_identity(base) != effective_identity(after)


def test_alternative_generator_is_bounded_deterministic_and_keeps_fixed_parameters():
    domains = {f"axis{i}": {"min": 0, "max": 1000, "step": 1} for i in range(8)}
    first = list(alternatives(domains, fixed={"hypothesis": "original"}, seed="bounded", limit=13))
    assert len(first) == 13
    assert first == list(
        alternatives(domains, fixed={"hypothesis": "original"}, seed="bounded", limit=13)
    )
    assert len({tuple(p.items()) for p in first}) == 13
    assert all(p["hypothesis"] == "original" and len(p) == 9 for p in first)
    assert list(alternatives(domains, seed="bounded", limit=0)) == []


def test_small_categorical_lattice_visits_each_configuration_without_duplicates():
    domains = {
        "exit": {"type": "categorical", "choices": ["percent", "atr"]},
        "hold": {"min": 2, "max": 6, "step": 2},
    }
    all_params = list(alternatives(domains, seed="small", limit=256))
    assert len(all_params) == 6
    assert {(p["exit"], p["hold"]) for p in all_params} == {
        (e, h) for e in ("percent", "atr") for h in (2, 4, 6)
    }
