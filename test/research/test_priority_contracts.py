"""Priority scope admission and scientific descriptor/version fences."""

from copy import deepcopy

import pytest
from test_automatic_catalog import request
from test_execution_aware_research import prepared as execution_prepared
from test_priority_research import prepared

from research.execution_search import axes
from research.portfolio import execution_versions, normalize
from research.study_analysis import reconstruct_study
from services.research_automatic_catalog import build_plan
from services.research_library import _selected_execution_followup
from services.research_study_activity import _params


def test_new_scope_is_explicit_and_pins_native_v3_without_changing_v10_defaults():
    old = normalize(request(version="automatic-hypotheses-v10"))
    new = normalize(request(version="automatic-hypotheses-v11"))
    assert old["strategies"][0]["execution_policy"]["version"] == "research-execution-aware-v1"
    assert "entry_priority" not in old["strategies"][0]["execution_policy"]
    assert new["strategies"][0]["execution_policy"]["version"] == "research-execution-aware-v2"
    assert new["strategies"][0]["execution_policy"]["entry_priority"] == "source"
    assert normalize(new) == new
    versions = execution_versions(new)
    assert versions["adapter_version"] == "vectorbt-portfolio-adapter-v3"
    assert versions["policy_version"] == "vectorbt-joint-causal-bars-v3"
    assert (
        versions["execution_features_version"]
        == execution_versions(old)["execution_features_version"]
    )


def test_account_cannot_mix_priority_or_policy_versions():
    value = normalize(request(version="automatic-hypotheses-v11", strategies=2))
    value.pop("automatic_research")
    value["strategies"][1]["execution_policy"]["entry_priority"] = "reversed_source"
    with pytest.raises(ValueError, match="share one execution"):
        normalize(value)
    value["strategies"][1]["execution_policy"].pop("entry_priority")
    value["strategies"][1]["execution_policy"]["version"] = "research-execution-aware-v1"
    with pytest.raises(ValueError, match="share one execution"):
        normalize(value)


@pytest.mark.parametrize(
    "tag,fixture",
    [
        ("automatic-hypotheses-v10", prepared),
        ("automatic-hypotheses-v11", execution_prepared),
    ],
)
def test_diagnostics_cannot_relabel_an_execution_domain_as_another_programme(tag, fixture):
    descriptor = fixture()["automatic_recipe"]["execution_research"]
    experiment = {
        "experiment_version": tag,
        "execution_research": descriptor,
        "search_space": {"axes": axes(descriptor)},
        "trials": [],
    }
    with pytest.raises(ValueError, match="descriptor differs"):
        reconstruct_study(experiment)


def test_missing_priority_descriptor_is_not_interpreted_as_a_valid_empty_study():
    with pytest.raises(ValueError, match="descriptor differs"):
        reconstruct_study({"experiment_version": "automatic-hypotheses-v11", "trials": []})


def test_global_priority_capture_and_preflight_do_not_invent_extra_data():
    assert _params({"portfolio.entry_priority": "lower_stop_risk"})
    with pytest.raises(ValueError, match="entry priority"):
        _params({"portfolio.entry_priority": "future_profit"})
    plan = build_plan(request(version="automatic-hypotheses-v11", strategies=2), interval="D")
    assert plan["search"]["budget"]["entry_priority_probe_limit_per_fit"] == 2
    assert plan["search"]["budget"]["proposals"] == 272
    for row in plan["strategies"]:
        priority = [p for p in row["parameters"] if p["id"] == "entry_priority"]
        assert len(priority) == 1 and priority[0]["axis"] == "portfolio.entry_priority"
    decision = next(row for row in plan["decisions"]["automatic"] if row["id"] == "entry_priority")
    assert "no extra history" in decision["decision"].lower()


def test_selected_priority_followup_preserves_the_actual_policy_as_custom_reference():
    value = normalize(request(version="automatic-hypotheses-v11"))
    value["strategies"][0]["execution_policy"]["entry_priority"] = "lower_stop_risk"
    policy = deepcopy(value["strategies"][0]["execution_policy"])
    with pytest.raises(ValueError, match="Custom search"):
        normalize(value)
    _selected_execution_followup(value, optimizing=True)
    assert "automatic_research" not in value
    assert normalize(value)["strategies"][0]["execution_policy"] == policy


@pytest.mark.parametrize("version", ["automatic-hypotheses-v10", "automatic-hypotheses-v11"])
def test_chartink_recapture_preserves_selected_execution_and_allocation(version):
    from research.execution_policy import normalize_policy
    from services.research_chartink import _draft

    portfolio = normalize(request(version=version))
    row = portfolio["strategies"][0]
    row["execution_policy"] = normalize_policy({**row["execution_policy"], "exit_mode": "atr"})
    row["allocation_pct"] = 40
    previous = {
        "portfolio": portfolio,
        "optimization": {"sampler": "tpe", "trials": 25, "objective": "balanced", "seed": 0},
    }
    original = deepcopy(previous)
    fresh = _draft({"title": "Updated scanner"}, {"id": "b" * 32}, (previous, row["source_id"]))
    assert previous == original
    assert fresh["portfolio"]["strategies"][0]["allocation_pct"] == 40
    assert fresh["portfolio"]["strategies"][0]["execution_policy"] == row["execution_policy"]
    assert fresh["portfolio"]["risk_limits"] == portfolio["risk_limits"]
