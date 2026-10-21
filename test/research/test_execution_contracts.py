"""Opt-in risk contracts, immutable settings and honest preflight presentation."""

from copy import deepcopy

import pytest
from test_automatic_catalog import request

from research.development_evidence import plan
from research.execution_policy import normalize_limits, normalize_policy
from research.portfolio import execution_versions, normalize
from research.report_contract import settings_identity
from services.research_automatic_catalog import build_plan


def test_new_execution_defaults_are_explicit_and_legacy_remains_unmodified():
    old = normalize(request(version="automatic-hypotheses-v9"))
    assert "risk_limits" not in old
    assert all("execution_policy" not in row for row in old["strategies"])
    new = normalize(request(version="automatic-hypotheses-v10"))
    assert new["risk_limits"] == normalize_limits()
    assert all(row["execution_policy"] == normalize_policy() for row in new["strategies"])
    assert normalize(new) == new
    versions = execution_versions(new)
    assert versions["adapter_version"] == "vectorbt-portfolio-adapter-v2"
    assert versions["execution_features_version"] == "research-execution-features-v1"
    assert versions["entry_conditions_version"] == "research-joint-entry-conditions-v2"


@pytest.mark.parametrize("invalid", [["unexpected"], "automatic-hypotheses-v10", True])
def test_untrusted_recipe_shape_is_rejected_without_attribute_errors(invalid):
    raw = request()
    raw["automatic_research"] = invalid
    with pytest.raises(ValueError, match="recipe"):
        normalize(raw)


def test_mixed_or_mismatched_policies_cannot_silently_acquire_defaults():
    raw = request(version="automatic-hypotheses-v10", strategies=2)
    raw["strategies"][0]["execution_policy"] = normalize_policy()
    with pytest.raises(ValueError, match="Every strategy"):
        normalize(raw)
    raw["strategies"][1]["execution_policy"] = normalize_policy()
    raw["risk_limits"] = normalize_limits({"max_open_positions": 4})
    with pytest.raises(ValueError, match="risk limits"):
        normalize(raw)


def test_fixed_policies_round_trip_and_cannot_move_to_legacy_auto_or_nautilus():
    raw = request(version="automatic-hypotheses-v10")
    raw.pop("automatic_research")
    raw["risk_limits"] = normalize_limits({"max_open_positions": 4})
    raw["strategies"][0]["execution_policy"] = normalize_policy(
        {"exit_mode": "atr", "sizing": "stop_risk", "risk_limits": raw["risk_limits"]}
    )
    normalized = normalize(raw)
    assert normalize(normalized) == normalized
    changed = deepcopy(normalized["strategies"])
    changed[0]["execution_policy"]["atr_stop_multiple"] = 3
    assert settings_identity(changed) != settings_identity(normalized["strategies"])
    changed = deepcopy(normalized["strategies"])
    changed[0]["execution_policy"]["risk_limits"]["max_open_positions"] = 3
    assert settings_identity(changed) != settings_identity(normalized["strategies"])
    raw["automatic_research"] = {"version": "automatic-hypotheses-v9"}
    with pytest.raises(ValueError, match="execution-aware"):
        normalize(raw)
    raw.pop("automatic_research")
    raw["engine"] = "nautilus"
    with pytest.raises(ValueError, match="VectorBT"):
        normalize(raw)


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_preflight_discloses_execution_budget_and_frozen_risk_ceiling(interval):
    raw = request(version="automatic-hypotheses-v10")
    result = build_plan(raw, interval=interval)
    assert result["search"]["budget"]["proposals"] == 256
    assert result["search"]["budget"]["proposals_per_fit"] == 64
    assert result["summary"]["max_simulations"] == 384
    assert "hard risk limits" in result["fixed_assumptions"]
    risk = next(row for row in result["decisions"]["retained"] if row["id"] == "risk")
    assert "matched original-sizing" in risk["decision"]
    assert "allocations, order size and exposure unchanged" not in risk["decision"]


def test_history_limit_expansion_is_version_specific_and_malformed_input_is_bounded():
    legacy = plan({}, 384)
    assert legacy["reason"] == "evaluation_limit"
    current = plan(
        {"portfolio": {"automatic_research": {"version": "automatic-hypotheses-v10"}}}, 384
    )
    assert current["limits"]["max_evaluations"] == 384
    assert current["reason"] == "unverified_timeline"
    assert plan(None, 1)["reason"] == "unverified_timeline"


def test_selected_atr_followup_keeps_policy_and_uses_only_meaningful_custom_axes():
    from services.research_library import _selected_execution_followup

    selected = normalize(request(version="automatic-hypotheses-v10"))
    policy = normalize_policy({"exit_mode": "atr", "sizing": "stop_risk"})
    selected["strategies"][0]["execution_policy"] = policy
    with pytest.raises(ValueError, match="Custom search"):
        normalize(selected)
    _selected_execution_followup(selected, optimizing=True)
    assert "automatic_research" not in selected
    assert selected["strategies"][0]["execution_policy"] == policy
    assert "target_pct" not in selected["strategies"][0]["search"]
    assert "stop_pct" not in selected["strategies"][0]["search"]
    assert selected["strategies"][0]["search"]
    assert normalize(selected)["strategies"][0]["execution_policy"] == policy


def test_eight_strategy_mandate_fits_its_pre_outcome_metadata_bound():
    import json

    from test_execution_aware_research import prepared

    from research.automatic_protocol import compile_recipe
    from research.execution_features import prepare
    from research.research_mandate import EXECUTION_MAX_METADATA_BYTES, build

    evidence = prepared()
    source = evidence["strategies"][0]
    evidence["strategies"] = [
        {**deepcopy(source), "id": f"row{index}", "allocation_pct": 12.5} for index in range(8)
    ]
    evidence["portfolio"]["strategies"] = [
        {key: deepcopy(value) for key, value in row.items() if key != "signals"}
        for row in evidence["strategies"]
    ]
    evidence["signals"] = [signal for row in evidence["strategies"] for signal in row["signals"]]
    for key in (
        "automatic_recipe",
        "execution_features",
        "timing_descriptor",
        "timing_union",
        "timing_cohort",
    ):
        evidence.pop(key, None)
    evidence = prepare(evidence, evidence["automatic_stock_market_context"])
    evidence["automatic_recipe"] = compile_recipe(evidence)
    mandate = build(evidence, inputs_artifact="a" * 64)
    assert len(json.dumps(mandate, separators=(",", ":")).encode()) <= EXECUTION_MAX_METADATA_BYTES
    assert len(mandate["economic_assumptions"]["settings"]) == 8
