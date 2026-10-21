"""Sealed synthetic coverage metadata; no native accounts or broker calls."""

from copy import deepcopy

import pytest

from research.automatic_protocol import ALLOCATION_VERSION, slice_period
from research.automatic_timing import axes as timing_axes
from research.automatic_trailing import apply_choices as trailing_choices
from research.automatic_trailing import axes as trailing_axes
from research.automatic_walk_forward import _screening_program, data_period, fit_names
from research.connectors.optuna_portfolio import _candidate, _prepare, _settings
from research.development_evidence import plan
from research.evaluation_basis import build_evaluation_basis
from research.execution_search import ALLOCATION_AXIS, apply_choices
from research.execution_search import axes as execution_axes
from research.expanded_stock_market import markers
from research.method_coverage import build
from research.report_contract import fingerprint, settings_identity
from research.research_mandate import build as build_mandate


def seal(value, key="id"):
    value.pop(key, None)
    value[key] = fingerprint(value)
    return value


def metadata(value, *, fits=("fit1",), allocation_mode="prior60_cluster_inverse_vol"):
    """Build consistent synthetic observations on the actual v16 frozen domains."""
    recipe = value["automatic_recipe"]
    version = recipe["version"]
    source = "1" * 64
    mandate = build_mandate(value, inputs_artifact=source)
    binding = {
        "inputs_artifact": source,
        "search_identity": fingerprint(
            {"recipe": recipe, "stock": value["automatic_stock_market_context"]}
        ),
    }
    state = {
        "version": version,
        "binding": binding["search_identity"],
        "optimizer": {"adapter_version": version},
        "attempts": 0,
        "simulations": 0,
        "evaluations": {},
        "fits": {},
        "active_fit": fits[-1],
        "folds": [],
        "selection": None,
        "selected_report": None,
        "baseline_final_report": None,
        "final_report": None,
    }
    manifest = {
        "version": "research-development-evidence-manifest-v1",
        "binding": binding,
        "plan": plan(value, 512),
        "status": "partial",
        "entries": {},
        "marks": 0,
        "bytes": 0,
        "reserved_final_excluded": True,
    }
    base, numerical = _prepare(value["strategies"])
    dimensions = {
        **numerical,
        **trailing_axes(recipe["trailing_research"]),
        **timing_axes(recipe["timing_research"]),
        **execution_axes(recipe["execution_research"]),
    }
    params = _screening_program(
        version,
        recipe["hypothesis_catalog"],
        dimensions,
        recipe["timing_research"],
        recipe["execution_research"],
    )[0]
    if ALLOCATION_AXIS in dimensions:
        params[ALLOCATION_AXIS] = allocation_mode
    hypothesis = next(
        row for row in recipe["hypothesis_catalog"] if row["id"] == params["hypothesis"]
    )
    materialized = trailing_choices(_candidate(base, params), params, recipe["trailing_research"])
    materialized = apply_choices(materialized, params, recipe["execution_research"])
    settings = markers(_settings(materialized), hypothesis)
    config_id = settings_identity(settings)
    for fit_name in fits:
        period = data_period(fit_name)
        current = slice_period(value, period)
        rows = [{**row, **settings[i]} for i, row in enumerate(current["strategies"])]
        basis = build_evaluation_basis({**current, "strategies": rows}, period="selection")
        counts = {
            "allowed": sum(len(row["signals"]) for row in rows),
            "filtered": 0,
            "unknown": 0,
            "original_excluded": 0,
            "pending": 0,
        }
        condition = {
            "hypothesis": hypothesis,
            "bundle_id": recipe["stock_evidence_id"],
            "gate_id": fingerprint({"synthetic": True, "period": period, "config": config_id}),
            "counts": counts,
        }
        evaluation = {
            "summary": {"net_return_pct": 2.0, "max_drawdown_pct": 1.0, "closed_trades": 12},
            "entry_dates": 12,
            "counts": counts,
            "automatic_stock_condition": condition,
            "evaluation_basis": basis,
        }
        key = fingerprint(
            {"settings": settings, "hypothesis": hypothesis, "period": period, "stress": False}
        )
        descriptor = {
            "evaluation_id": key,
            "binding": binding,
            "period": period,
            "stress": False,
            "basis_id": fingerprint(basis),
            "hypothesis_id": fingerprint(hypothesis),
            "condition_id": fingerprint(condition),
            "status": "unavailable",
            "reason": "synthetic_metadata_only",
        }
        manifest["entries"][key] = descriptor
        if key not in state["evaluations"]:
            state["evaluations"][key] = {**deepcopy(evaluation), "development_evidence": descriptor}
            state["simulations"] += 1
            state["attempts"] += 1
        candidate = {
            "config_id": config_id,
            "strategies": settings,
            "hypothesis": hypothesis,
            "condition_label": hypothesis["label"],
            "trial_number": 0,
            "score": 1.0,
            "stage": "family_screening",
            **deepcopy(evaluation),
        }
        state["fits"][fit_name] = {
            "trials": [
                {
                    "number": 0,
                    "params": deepcopy(params),
                    "config_id": config_id,
                    "state": "complete",
                    "reused": False,
                    "phase": "family_screening",
                    "value": 1.0,
                }
            ],
            "candidates": {config_id: candidate},
            "selection": None,
        }
    return mandate, seal(state, "checkpoint_id"), seal(manifest)


@pytest.fixture(scope="module")
def cases():
    from test_portfolio_allocation_recipe import prepared

    value = prepared()
    return {"single": metadata(value), "six": metadata(value, fits=fit_names(ALLOCATION_VERSION))}


def test_allocation_coverage_exposes_the_real_six_fit_contract(cases):
    values = deepcopy(cases["single"])
    result = build(*values)
    assert result["status"] == "available"
    assert result["source"]["recipe_version"] == ALLOCATION_VERSION
    assert len(result["fits"]) == 6
    assert all(row["proposal_limit"] == 68 for row in result["fits"])
    assert result["totals"]["registered_hypotheses"] == 108
    assert result["totals"]["proposals"] == 1
    assert result["totals"]["unavailable_histories"] == 1
    assert result["totals"]["retained_histories"] == 0
    assert values == cases["single"]


def test_six_seed_fits_reuse_search_period_metadata_without_independent_data_claim(cases):
    result = build(*deepcopy(cases["six"]))
    assert result["status"] == "available"
    assert result["totals"]["proposals"] == result["totals"]["evaluated"] == 6
    assert result["totals"]["native_evaluations"] == 4
    assert [row["id"] for row in result["fits"]] == list(fit_names(ALLOCATION_VERSION))
    assert len(result["entry_priority"]["fits"]) == 6
    assert any(
        "not distinct calculations or independent samples" in line for line in result["limitations"]
    )


def test_explicitly_skipped_repeat_fits_remain_not_run(cases):
    values = deepcopy(cases["single"])
    values[1]["recommendation_checks"] = {"search_seeds": {"status": "not_run"}}
    seal(values[1], "checkpoint_id")
    result = build(*values)
    assert [row["status"] for row in result["fits"]][-2:] == ["not_run", "not_run"]


@pytest.mark.parametrize(
    "damage", ["budget", "descriptor", "missing_axis", "invalid_mode", "candidate_mode"]
)
def test_resealed_inconsistent_allocation_metadata_fails_closed(cases, damage):
    values = deepcopy(cases["single"])
    mandate, state, _ = values
    trial = state["fits"]["fit1"]["trials"][0]
    if damage == "budget":
        mandate["budget"]["max_simulations"] = 384
    elif damage == "descriptor":
        mandate["search"]["execution_research"]["version"] = "research-execution-search-v4"
        seal(mandate["search"]["execution_research"])
    elif damage == "missing_axis":
        trial["params"].pop(ALLOCATION_AXIS)
    elif damage == "invalid_mode":
        trial["params"][ALLOCATION_AXIS] = "future_covariance"
    elif damage == "candidate_mode":
        candidate = next(iter(state["fits"]["fit1"]["candidates"].values()))
        candidate["strategies"][0]["execution_policy"]["allocation_mode"] = "original"
    seal(mandate)
    seal(state, "checkpoint_id")
    with pytest.raises(ValueError):
        build(*values)


def test_saved_v15_coverage_identity_remains_exact():
    from test_recommendation_seeds import prepared

    # Captured from the unchanged production reader before admitting v16.
    result = build(*metadata(prepared()))
    assert result["id"] == "94bb73021711a9182cd38a2f303a0fa14c85f781a92ab2449e18258e6a00db1f"
