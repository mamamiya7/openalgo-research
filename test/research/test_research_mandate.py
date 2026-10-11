"""Pre-outcome programme admission cannot be replaced by results or relabelled."""

import copy

import pytest
from test_automatic_hypotheses import prepared as intelligent_inputs
from test_automatic_protocol import evidence
from test_automatic_timing import prepared as timing_inputs

from research.automatic_protocol import automatic_portfolio, compile_recipe
from research.engine import validate_config
from research.report_contract import fingerprint
from research.research_mandate import MAX_METADATA_BYTES, VERSION, build, verify

SOURCE = "a" * 64


def rehash(value):
    value["id"] = fingerprint({key: item for key, item in value.items() if key != "id"})
    return value


def test_freezes_real_recipe_assumptions_and_period_roles_before_results():
    value = evidence()
    value["automatic_recipe"] = compile_recipe(value)
    before = copy.deepcopy(value)
    mandate = build(value, inputs_artifact=SOURCE)
    assert mandate["version"] == VERSION
    assert mandate["stage"] == "pre_outcome"
    assert mandate["source"]["recipe_id"] == value["automatic_recipe"]["id"]
    assert mandate["economic_assumptions"]["capital"] == 100000
    assert mandate["economic_assumptions"]["currency"] == "INR"
    assert (
        mandate["economic_assumptions"]["settings"][0]["config"] == value["strategies"][0]["config"]
    )
    assert (
        mandate["objective"]["id"] == value["portfolio"]["optimization"]["objective"] == "balanced"
    )
    assert (
        mandate["budget"]["max_simulations"]
        == value["automatic_recipe"]["budget"]["max_simulations"]
    )
    assert [row["role"] for row in mandate["validation"]["periods"]] == [
        "development_selection",
        "development_validation",
        "development_validation",
        "reserved_final_verification",
    ]
    assert all(not row["untouched_claim"] for row in mandate["validation"]["periods"])
    assert mandate["validation"]["reservation_status"] == "not_certified_by_this_receipt"
    assert mandate["supported_promise"]["global_optimum"] is False
    assert mandate["supported_promise"]["live_execution"] is False
    assert verify(mandate, evidence=value, inputs_artifact=SOURCE)
    assert build(value, inputs_artifact=SOURCE) == mandate
    assert value == before


def test_registered_v7_methods_and_budgets_are_reused_without_expansion():
    value = timing_inputs()
    mandate = build(value, inputs_artifact=SOURCE)
    recipe = value["automatic_recipe"]
    assert mandate["method_admission"] == recipe["research_plan"]["methods"]
    assert mandate["search"]["hypothesis_catalog"] == recipe["hypothesis_catalog"]
    assert mandate["search"]["data_requirements"] == recipe["research_plan"]["data"]
    assert mandate["budget"]["proposals"] == 128
    assert mandate["budget"]["max_simulations"] == 320
    assert (
        mandate["economic_assumptions"]["settings"][0]["config"]["trailing_enabled"]
        == value["strategies"][0]["config"]["trailing_enabled"]
    )
    assert verify(mandate, evidence=value, inputs_artifact=SOURCE)


def test_exploratory_history_cannot_claim_reserved_untouched_final_or_validated_advice():
    value = intelligent_inputs(sessions=25)
    mandate = build(value, inputs_artifact=SOURCE)
    assert mandate["validation"]["mode"] == "exploratory_only"
    assert all(row["role"] == "exploratory_selection" for row in mandate["validation"]["periods"])
    assert mandate["supported_promise"]["validated_recommendation_possible"] is False
    assert mandate["budget"]["max_final_verification_evaluations"] == 0
    assert "No untouched later period" in mandate["validation"]["policies"]["final"]


def test_timestamped_signals_do_not_reinterpret_declared_multiday_economic_intent():
    value = evidence(minute=True)
    row = value["portfolio"]["strategies"][0]
    row["config"] = validate_config(
        {**row["config"], "trade_horizon": "multiday", "hold_minutes": None, "hold_sessions": 1}
    )
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    value["strategies"][0].update(copy.deepcopy(value["portfolio"]["strategies"][0]))
    value["automatic_recipe"] = compile_recipe(value)
    mandate = build(value, inputs_artifact=SOURCE)
    cfg = mandate["economic_assumptions"]["settings"][0]["config"]
    assert mandate["subject"]["interval"] == "1m"
    assert cfg["trade_horizon"] == "multiday"
    assert cfg["hold_minutes"] is None
    assert cfg["hold_sessions"] == 1


def test_results_and_poisoned_final_outcomes_are_never_read_or_hashed():
    value = evidence()
    first = build(value, inputs_artifact=SOURCE)
    # Noncanonical outcome payloads would fail JSON hashing if consulted.
    value.update(
        summary={"net_return_pct": float("nan")},
        final_report={"equity_curve": object()},
        trials=[object()],
        ledger=[object()],
        selected_report={"score": float("inf")},
    )
    assert build(value, inputs_artifact=SOURCE) == first
    assert verify(first, evidence=value, inputs_artifact=SOURCE)


@pytest.mark.parametrize(
    "field", ["objective", "capital", "costs", "periods", "method", "global_claim", "extra"]
)
def test_rehashed_changes_do_not_verify_against_actual_source_programme(field):
    value = intelligent_inputs()
    mandate = build(value, inputs_artifact=SOURCE)
    if field == "objective":
        mandate["objective"]["id"] = "return"
    elif field == "capital":
        mandate["economic_assumptions"]["capital"] = 1
    elif field == "costs":
        mandate["economic_assumptions"]["costs"][0]["cost_bps"] += 1
    elif field == "periods":
        mandate["validation"]["periods"][-1]["role"] = "development_selection"
    elif field == "method":
        mandate["method_admission"].append({"id": "invented_method", "status": "admitted"})
    elif field == "global_claim":
        mandate["supported_promise"]["global_optimum"] = True
    else:
        mandate["unexpected"] = "metadata"
    with pytest.raises(ValueError, match="source assumptions"):
        verify(rehash(mandate), evidence=value, inputs_artifact=SOURCE)


@pytest.mark.parametrize(
    "change", ["price", "source_row", "capital", "cost", "interval", "artifact"]
)
def test_changed_input_evidence_requires_new_mandate(change):
    value = evidence()
    before = build(value, inputs_artifact=SOURCE)
    source = SOURCE
    if change == "price":
        last = value["snapshot"]["sessions"][-1]
        value["snapshot"]["bars"]["AAA"][last]["close"] = 100.25
    elif change == "source_row":
        value["signals"][0]["row"] = 999
        value["strategies"][0]["signals"][0]["row"] = 999
    elif change in ("capital", "cost"):
        if change == "capital":
            value["portfolio"]["capital"] = 250000
        else:
            value["portfolio"]["strategies"][0]["config"]["cost_bps"] = 20
        value["portfolio"] = automatic_portfolio(value["portfolio"])
        value["strategies"][0].update(copy.deepcopy(value["portfolio"]["strategies"][0]))
    elif change == "interval":
        value = evidence(minute=True)
    else:
        source = "b" * 64
    after = build(value, inputs_artifact=source)
    assert after["id"] != before["id"]
    with pytest.raises(ValueError):
        verify(before, evidence=value, inputs_artifact=source)


def test_saved_recipe_cannot_override_changed_frozen_inputs():
    value = evidence()
    value["automatic_recipe"] = compile_recipe(value)
    value["snapshot"]["bars"]["AAA"][value["snapshot"]["sessions"][-1]]["close"] = 100.5
    with pytest.raises(ValueError, match="recipe conflicts"):
        build(value, inputs_artifact=SOURCE)


def test_missing_stock_context_is_not_displayed_as_an_available_admitted_feature():
    value = intelligent_inputs()
    value.pop("automatic_stock_features")
    value["automatic_recipe"] = compile_recipe(value)
    mandate = build(value, inputs_artifact=SOURCE)
    assert (
        next(row for row in mandate["method_admission"] if row["id"] == "stock_trend")["status"]
        == "unavailable"
    )
    assert (
        next(row for row in mandate["method_admission"] if row["id"] == "exit_management")["status"]
        == "admitted"
    )


@pytest.mark.parametrize("source", [None, "", "not-a-hash", "A" * 64, "a" * 65])
def test_source_artifact_identity_is_required_and_bounded(source):
    with pytest.raises(ValueError, match="inputs artifact"):
        build(evidence(), inputs_artifact=source)


def test_metadata_is_bounded_and_nonfinite_assumptions_are_rejected():
    value = intelligent_inputs()
    mandate = build(value, inputs_artifact=SOURCE)
    mandate["extra"] = "a" * MAX_METADATA_BYTES
    with pytest.raises(ValueError, match="metadata limit"):
        verify(mandate, evidence=value, inputs_artifact=SOURCE)
    mandate = build(value, inputs_artifact=SOURCE)
    mandate["economic_assumptions"]["capital"] = float("nan")
    with pytest.raises(ValueError):
        verify(mandate, evidence=value, inputs_artifact=SOURCE)


def test_returned_metadata_has_no_mutable_links_to_source_or_future_builds():
    value = intelligent_inputs()
    retained = build(value, inputs_artifact=SOURCE)
    before = copy.deepcopy(retained)
    retained["economic_assumptions"]["settings"][0]["config"]["cost_bps"] = 999
    assert build(value, inputs_artifact=SOURCE) == before
    assert retained != before


def test_legacy_evidence_need_not_already_have_an_additive_recipe_or_mandate():
    value = evidence()
    assert "automatic_recipe" not in value
    first = build(value, inputs_artifact=SOURCE)
    value["research_mandate"] = first
    assert build(value, inputs_artifact=SOURCE) == first
    assert "research_mandate" not in compile_recipe(value)
