"""Pure offline sampler declarations; no engine or service is invoked here."""

from copy import deepcopy

import pytest

from research.procedure_seed_calibration import FITS, LIMITATION, seed_plan, verify_seed_plan
from research.report_contract import fingerprint


def reseal(plan):
    plan["id"] = fingerprint({key: value for key, value in plan.items() if key != "id"})
    return plan


@pytest.mark.parametrize("index", range(3))
def test_declared_programmes_reproduce_exactly_and_are_caller_owned(index):
    plan = seed_plan(index)
    assert plan == seed_plan(index) == verify_seed_plan(plan)
    assert list(plan["fit_sampler_seeds"]) == list(FITS)
    assert plan["offline_only"] is True
    assert plan["limitation"] == LIMITATION
    assert "not independent dataset" in LIMITATION
    verified = verify_seed_plan(plan)
    verified["fit_sampler_seeds"]["search"] = 999
    plan["repeat_fits"].append("final")
    assert seed_plan(index)["repeat_fits"] == ["search_seed1", "search_seed2"]
    assert verified != seed_plan(index)


def test_zero_is_actual_production_v15_map_and_alternates_change_every_fit():
    primary = seed_plan(0)["fit_sampler_seeds"]
    assert list(primary.values()) == [0, 0, 0, 0, 1, 2]
    maps = [seed_plan(index)["fit_sampler_seeds"] for index in range(3)]
    for fit in FITS:
        assert len({mapping[fit] for mapping in maps}) == 3
    assert len({value for mapping in maps[1:] for value in mapping.values()}) == 12


@pytest.mark.parametrize("index", [None, True, False, -1, 3, 100, 0.0, 1.0, "0", [], {}])
def test_index_is_small_explicit_integer(index):
    with pytest.raises(ValueError):
        seed_plan(index)


@pytest.mark.parametrize("value", [None, [], 0, True, "seed0"])
def test_malformed_plan_rejected(value):
    with pytest.raises(ValueError):
        verify_seed_plan(value)


@pytest.mark.parametrize(
    "change",
    [
        lambda plan: plan.update(index=True),
        lambda plan: plan.update(index=0.0),
        lambda plan: plan.update(version="new-version"),
        lambda plan: plan.update(offline_only=False),
        lambda plan: plan.update(offline_only=1),
        lambda plan: plan.update(scope="production"),
        lambda plan: plan.update(primary_fit="search_seed1"),
        lambda plan: plan.update(repeat_fits=["search_seed2", "search_seed1"]),
        lambda plan: plan.update(extra="undeclared"),
        lambda plan: plan["fit_sampler_seeds"].update(search=7),
        lambda plan: plan["fit_sampler_seeds"].update(search=False),
        lambda plan: plan["fit_sampler_seeds"].update(search=0.0),
        lambda plan: plan["fit_sampler_seeds"].update(search_seed1=True),
        lambda plan: plan["fit_sampler_seeds"].update(search_seed1=1.0),
        lambda plan: plan["fit_sampler_seeds"].update(final=5),
        lambda plan: plan["fit_sampler_seeds"].pop("fit1"),
        lambda plan: plan.pop("limitation"),
    ],
)
@pytest.mark.parametrize("rehash", [False, True])
def test_changed_even_resealed_declarations_rejected(change, rehash):
    plan = deepcopy(seed_plan(0))
    change(plan)
    if rehash:
        reseal(plan)
    with pytest.raises(ValueError):
        verify_seed_plan(plan)


def test_seal_is_required_even_with_matching_declaration():
    plan = seed_plan(2)
    plan["id"] = "0" * 64
    with pytest.raises(ValueError):
        verify_seed_plan(plan)


def test_nonserialisable_extra_value_fails_as_invalid_plan():
    plan = seed_plan(1)
    plan["extra"] = object()
    with pytest.raises(ValueError):
        verify_seed_plan(plan)
