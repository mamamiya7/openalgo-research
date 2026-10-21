"""Restore saved allocation dimensions without resampling or calculating accounts."""

from copy import deepcopy

import pytest
from test_portfolio_allocation_recipe import prepared

from research.execution_search import axes, descriptor
from research.study_analysis import reconstruct_study


def recorded():
    value = prepared()
    original = value["strategies"][0]
    value["strategies"] = [{**deepcopy(original), "id": f"s{i}"} for i in range(8)]
    specification = descriptor(value)
    domains = axes(specification)
    # The reader's explicit maximum is the prior 74 dimensions plus the one
    # account allocation axis. All extra domains use a supported numeric field.
    domains.update(
        {
            f"numeric{i}.target_pct": {"min": 1, "max": 2, "step": 1}
            for i in range(75 - len(domains))
        }
    )
    return {
        "experiment_version": "automatic-hypotheses-v16",
        "execution_research": specification,
        "trials": [],
        "search_space": {"axes": domains},
    }


def test_saved_v16_can_restore_75_declared_dimensions_without_native_work(monkeypatch):
    monkeypatch.setattr(
        "research.connectors.vectorbt_portfolio.evaluate",
        lambda *a, **k: pytest.fail("Analysis must never simulate"),
    )
    value = recorded()
    before = deepcopy(value)
    _, distributions = reconstruct_study(value)
    assert len(distributions) == 75 and value == before


def test_prior_reader_limit_is_not_expanded():
    value = recorded()
    value["experiment_version"] = "automatic-hypotheses-v15"
    with pytest.raises(ValueError, match="parameter space"):
        reconstruct_study(value)


@pytest.mark.parametrize("mutation", ["missing", "older", "axis_changed"])
def test_allocation_dimension_requires_its_exact_frozen_descriptor(mutation):
    value = recorded()
    if mutation == "missing":
        value.pop("execution_research")
    elif mutation == "older":
        value["execution_research"]["version"] = "research-execution-search-v4"
    else:
        value["search_space"]["axes"]["portfolio.allocation_mode"]["choices"] = [
            "original",
            "invented",
        ]
    with pytest.raises(ValueError, match="execution"):
        reconstruct_study(value)


def test_unversioned_allocation_axis_cannot_bypass_descriptor():
    value = {
        "trials": [],
        "search_space": {
            "axes": {
                "portfolio.allocation_mode": {
                    "type": "categorical",
                    "choices": ["original", "prior60_cluster_inverse_vol"],
                }
            }
        },
    }
    with pytest.raises(ValueError, match="frozen contract"):
        reconstruct_study(value)
