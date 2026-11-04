"""Restore registered execution studies without running another search/account."""

from copy import deepcopy

import optuna
import pytest
from test_automatic_capital_design import evidence

from research.execution_search import axes, descriptor
from research.report_contract import fingerprint
from research.study_analysis import reconstruct_study


def recorded(version="v7"):
    value = evidence(design=version != "v5")
    if version == "v7":
        value["portfolio"]["automatic_research"]["action_policy"] = "automatic_actions_v2"
    specification = descriptor(value)
    domains = axes(specification)
    return {
        "experiment_version": "automatic-hypotheses-v16",
        "execution_research": specification,
        "search_space": {"axes": domains},
        "trials": [
            {
                "number": index,
                "state": "complete",
                "value": index / 7,
                "params": {
                    name: domain["choices"][index % len(domain["choices"])]
                    for name, domain in domains.items()
                },
            }
            for index in range(6)
        ],
    }


@pytest.mark.parametrize("version", ["v5", "v6", "v7"])
def test_exact_saved_execution_descriptor_restores_recorded_trials_only(monkeypatch, version):
    monkeypatch.setattr(
        "research.connectors.vectorbt_portfolio.evaluate",
        lambda *a, **kw: pytest.fail("Analysis calculated a native account"),
    )
    monkeypatch.setattr(
        optuna.study.Study,
        "optimize",
        lambda *a, **kw: pytest.fail("Analysis started a new search"),
    )
    monkeypatch.setattr(
        optuna.samplers.TPESampler,
        "sample_independent",
        lambda *a, **kw: pytest.fail("Analysis sampled new parameters"),
    )
    saved = recorded(version)
    before = deepcopy(saved)
    study, distributions = reconstruct_study(saved)
    assert set(distributions) == set(saved["search_space"]["axes"])
    assert [row.params for row in study.trials] == [row["params"] for row in saved["trials"]]
    assert [row.value for row in study.trials] == [row["value"] for row in saved["trials"]]
    assert saved == before


@pytest.mark.parametrize(
    "damage",
    [
        "marker_only",
        "extra_field",
        "allocation_modes",
        "risk_target",
        "original_policy",
        "missing_design",
        "older_programme",
        "relabelled_v5",
        "axis_domain",
    ],
)
def test_v7_marker_never_bypasses_exact_frozen_descriptor_grammar(damage):
    saved = recorded()
    specification = saved["execution_research"]
    if damage == "marker_only":
        specification = {
            "version": "research-execution-search-v7",
            "design_policy": "automatic_design_v1",
        }
        saved["execution_research"] = specification
    elif damage == "extra_field":
        specification["unregistered_policy"] = True
    elif damage == "allocation_modes":
        specification["allocation_modes"] = ["original", "anything"]
    elif damage == "risk_target":
        specification["stop_risk_target"] = "Unrestricted sizing"
    elif damage == "original_policy":
        specification["strategies"][0]["original"]["execution_policy"]["sizing"] = "stop_risk"
    elif damage == "missing_design":
        specification.pop("design_policy")
    elif damage == "older_programme":
        saved["experiment_version"] = "automatic-hypotheses-v12"
    elif damage == "relabelled_v5":
        specification = recorded("v5")["execution_research"]
        specification.update(
            version="research-execution-search-v7", design_policy="automatic_design_v1"
        )
        saved["execution_research"] = specification
    else:
        saved["search_space"]["axes"]["portfolio.allocation_mode"]["choices"].append("anything")
    specification["id"] = fingerprint(
        {key: value for key, value in specification.items() if key != "id"}
    )
    with pytest.raises(ValueError):
        reconstruct_study(saved)
