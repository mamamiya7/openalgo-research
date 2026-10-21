"""Saved account trade-off semantics, without engines, history or market data."""

from copy import deepcopy

import pytest

from research.evaluation_basis import IDENTITIES
from research.evaluation_basis import VERSION as BASIS_VERSION
from research.execution_policy import normalize_policy
from research.report_contract import fingerprint, settings_identity
from research.research_tradeoffs import MAX_ROWS, analyze


def _seal_basis(value):
    value["evidence_id"] = fingerprint(
        {"version": BASIS_VERSION, **{key: value[key] for key in IDENTITIES}}
    )
    comparison = value["comparison"]
    comparison.pop("id", None)
    comparison["id"] = fingerprint({"version": BASIS_VERSION, **comparison})
    return value


def report_fixture(version=12):
    """Small real-format metadata fixture shared with read-only service tests."""
    policy = normalize_policy(
        {
            "version": {
                10: "research-execution-aware-v1",
                11: "research-execution-aware-v2",
                12: "research-execution-aware-v3",
            }.get(version, "research-execution-aware-v1")
        }
    )
    baseline = [
        {
            "id": "s1",
            "name": "Saved signals",
            "allocation_pct": 100,
            "config": {
                "initial_capital": 100000,
                "cost_bps": 2,
                "slippage_bps": 1,
                "order_size_pct": 10,
                "hold_sessions": 5,
            },
            **({"execution_policy": policy} if version >= 10 else {}),
        }
    ]
    basis = _seal_basis(
        {
            "version": BASIS_VERSION,
            "status": "verified",
            **{key: fingerprint(key) for key in IDENTITIES},
            "period": {
                "kind": "selection",
                "from": "2026-01-01",
                "to": "2026-07-01",
                "observations": 120,
                "interval": "D",
            },
            "comparison": {
                "period": "selection",
                "currency": "INR",
                "capital": 100000,
                "execution": {
                    "engine": "vectorbt",
                    "engine_version": "0.28.5",
                    "adapter_version": f"adapter-{version}",
                    "policy_version": f"policy-{version}",
                    "portfolio_version": "v2",
                },
                "costs": [{"strategy_id": "s1", "cost_bps": 2, "slippage_bps": 1}],
                **({"risk_limits": policy["risk_limits"]} if version >= 10 else {}),
            },
        }
    )
    result = {
        "strategies": deepcopy(baseline),
        "evaluation_basis": basis,
        "summary": {"net_return_pct": 1, "max_drawdown_pct": 3, "closed_trades": 25},
        "automatic_stock_condition": {"hypothesis": {"id": "none"}},
        "automatic_research": {
            "version": f"automatic-hypotheses-v{version}",
            "recipe": {
                "version": f"automatic-hypotheses-v{version}",
                "baseline": deepcopy(baseline),
            },
            "baseline": {"net_return_pct": 1, "max_drawdown_pct": 3, "closed_trades": 25},
            "selected_config_id": settings_identity(baseline),
            "final": {"summary": {"net_return_pct": -90}},
        },
        "experiment": {"rows": []},
    }
    for index, (ret, drawdown, trades) in enumerate(
        ((10, 5, 20), (16, 9, 30), (7, 2, 25), (6, 4, 8), (5, 6, 22))
    ):
        settings = deepcopy(baseline)
        settings[0]["config"]["hold_sessions"] = index + 1
        result["experiment"]["rows"].append(
            {
                "config_id": settings_identity(settings),
                "strategies": settings,
                "summary": {
                    "net_return_pct": ret,
                    "max_drawdown_pct": drawdown,
                    "closed_trades": trades,
                },
                "entry_dates": 7,
                "evaluation_basis": deepcopy(basis),
                "hypothesis": {"id": "none"},
                "automatic_stock_condition": {"hypothesis": {"id": "none"}},
            }
        )
    return result


def _row(result, index=0):
    return result["experiment"]["rows"][index]


def _changed_settings(row):
    row["config_id"] = settings_identity(row["strategies"])


@pytest.mark.parametrize("version", [8, 9, 10, 11, 12])
def test_supported_versions_have_actual_nondominated_tradeoffs(version):
    result = report_fixture(version)
    view = analyze(result)
    assert view["status"] == "available", view
    assert view["counts"] == {
        "recorded": 5,
        "eligible": 5,
        "frontier": 4,
        "excluded": 0,
        "duplicate_rows": 0,
    }
    assert 1 <= len(view["choices"]) <= 4
    assert _row(result, 4)["config_id"] not in {row["config_id"] for row in view["choices"]}
    assert {role for row in view["choices"] for role in row["roles"]} == {
        "balanced",
        "best_return",
        "lower_drawdown",
        "fewer_closed_trades",
    }
    assert view["baseline"]["selected"] is True
    assert view["baseline"]["metrics"]["entry_dates"] is None


def test_no_mutation_reordering_or_final_outcome_access():
    result = report_fixture()
    before = deepcopy(result)
    first = analyze(result)
    assert result == before
    result["automatic_research"]["final"] = object()
    result["automatic_research"]["checks"] = object()
    result["experiment"]["rows"].reverse()
    assert analyze(result) == first


@pytest.mark.parametrize(
    "key,value",
    [
        ("net_return_pct", None),
        ("net_return_pct", float("nan")),
        ("net_return_pct", float("inf")),
        ("net_return_pct", True),
        ("net_return_pct", 0),
        ("net_return_pct", -1),
        ("max_drawdown_pct", 26),
        ("max_drawdown_pct", -1),
        ("closed_trades", 4),
        ("closed_trades", 5.5),
    ],
)
def test_missing_invalid_or_unsupported_metrics_do_not_qualify(key, value):
    result = report_fixture()
    _row(result)["summary"][key] = value
    view = analyze(result)
    assert view["status"] == "available"
    assert view["counts"]["excluded"] == 1
    assert _row(result)["config_id"] not in {row["config_id"] for row in view["choices"]}


@pytest.mark.parametrize("dates", [None, 0, 4, 30, 5.5, True])
def test_distinct_dates_are_actual_support_not_trade_count(dates):
    result = report_fixture()
    _row(result)["entry_dates"] = dates
    assert analyze(result)["counts"]["excluded"] == 1


def test_no_positive_candidate_keeps_explicit_unchanged_reference():
    result = report_fixture()
    for row in result["experiment"]["rows"]:
        row["summary"]["net_return_pct"] = -1
    view = analyze(result)
    assert view["status"] == "no_eligible_alternatives"
    assert not view["choices"]
    assert view["baseline"]["selected"] is True
    assert view["selected_config_id"] == result["automatic_research"]["selected_config_id"]


def test_selected_alternative_is_only_marked_existing_not_validated():
    result = report_fixture()
    row = _row(result, 1)
    result["strategies"] = deepcopy(row["strategies"])
    result["automatic_research"]["selected_config_id"] = row["config_id"]
    view = analyze(result)
    chosen = next(item for item in view["choices"] if item["config_id"] == row["config_id"])
    assert chosen["selected_existing"] is True
    assert chosen["validation"] == "exploratory_development_only"
    assert view["baseline"]["selected"] is False


@pytest.mark.parametrize(
    "axis,value",
    [
        ("capital", 200000),
        ("currency", "USD"),
        ("execution", {"engine": "nautilus"}),
        ("costs", [{"strategy_id": "s1", "cost_bps": 20, "slippage_bps": 1}]),
        ("risk_limits", {"max_open_positions": 100}),
    ],
)
def test_recorded_account_assumptions_must_match(axis, value):
    result = report_fixture()
    row = _row(result)
    row["evaluation_basis"]["comparison"][axis] = value
    _seal_basis(row["evaluation_basis"])
    assert analyze(result)["counts"]["excluded"] == 1


@pytest.mark.parametrize(
    "key", ["source_id", "prices_id", "calendar_id", "observations_id", "instruments_id"]
)
def test_observed_input_identities_cannot_change(key):
    result = report_fixture()
    row = _row(result)
    row["evaluation_basis"][key] = fingerprint("other")
    _seal_basis(row["evaluation_basis"])
    assert analyze(result)["counts"]["excluded"] == 1


def test_declared_gate_can_change_admitted_cohort_only():
    result = report_fixture()
    row = _row(result)
    hypothesis = {"id": "momentum"}
    row["hypothesis"] = hypothesis
    row["automatic_stock_condition"]["hypothesis"] = hypothesis
    row["evaluation_basis"]["cohort_id"] = fingerprint("gated-cohort")
    row["evaluation_basis"]["comparison"]["execution"]["entry_conditions"] = {
        "version": "research-joint-entry-conditions-v2",
        "hypothesis": hypothesis,
    }
    row["strategies"][0]["entry_condition"] = deepcopy(
        row["evaluation_basis"]["comparison"]["execution"]["entry_conditions"]
    )
    _changed_settings(row)
    _seal_basis(row["evaluation_basis"])
    assert analyze(result)["counts"]["excluded"] == 0
    row["evaluation_basis"]["comparison"]["execution"]["entry_conditions"]["hypothesis"] = {
        "id": "other"
    }
    _seal_basis(row["evaluation_basis"])
    assert analyze(result)["counts"]["excluded"] == 1


@pytest.mark.parametrize("change", ["cost", "capital", "risk", "allocation", "order_size"])
def test_candidate_cannot_relax_frozen_costs_or_risk(change):
    result = report_fixture()
    row = _row(result)
    strategy = row["strategies"][0]
    if change == "cost":
        strategy["config"]["cost_bps"] = 0
    elif change == "capital":
        strategy["config"]["initial_capital"] = 200000
    elif change == "risk":
        strategy["execution_policy"]["risk_limits"]["max_open_positions"] = 100
    elif change == "allocation":
        strategy["allocation_pct"] = 101
    else:
        strategy["config"]["order_size_pct"] = 11
    _changed_settings(row)
    assert analyze(result)["counts"]["excluded"] == 1


def test_equal_metrics_keep_distinct_ids_but_deterministic_roles():
    result = report_fixture()
    rows = result["experiment"]["rows"][:2]
    rows[1]["summary"] = deepcopy(rows[0]["summary"])
    result["experiment"]["rows"] = rows
    view = analyze(result)
    assert view["counts"]["frontier"] == 2
    assert len(view["choices"]) == 1
    assert view["choices"][0]["config_id"] == min(row["config_id"] for row in rows)


def test_identical_reused_rows_deduplicate_conflicts_do_not():
    result = report_fixture()
    result["experiment"]["rows"].append(deepcopy(_row(result)))
    view = analyze(result)
    assert view["counts"]["eligible"] == 5
    assert view["counts"]["duplicate_rows"] == 1
    result["experiment"]["rows"][-1]["summary"]["net_return_pct"] += 1
    assert analyze(result)["counts"]["excluded"] == 1


@pytest.mark.parametrize(
    "change",
    [
        "unsupported",
        "no_recipe",
        "missing_basis",
        "changed_selected",
        "too_many",
        "later_period",
        "tampered_basis",
    ],
)
def test_saved_context_admission_fails_closed(change):
    result = report_fixture()
    if change == "unsupported":
        result["automatic_research"]["version"] = "automatic-hypotheses-v13"
    elif change == "no_recipe":
        result["automatic_research"]["recipe"] = {}
    elif change == "missing_basis":
        result.pop("evaluation_basis")
    elif change == "changed_selected":
        result["automatic_research"]["selected_config_id"] = fingerprint("wrong")
    elif change == "too_many":
        result["experiment"]["rows"] *= MAX_ROWS
    elif change == "later_period":
        result["evaluation_basis"]["period"]["kind"] = "evaluation"
    else:
        result["evaluation_basis"]["comparison"]["capital"] += 1
    assert analyze(result)["status"] == "unavailable"


def test_ineligible_selected_reference_does_not_discard_other_exploration():
    result = report_fixture()
    result["automatic_research"]["baseline"]["net_return_pct"] = -10
    assert analyze(result)["status"] == "available"
    result["automatic_research"]["baseline"]["closed_trades"] = None
    view = analyze(result)
    assert view["status"] == "available"
    assert view["baseline"]["metrics"] is None
    assert view["baseline"]["reasons"]


@pytest.mark.parametrize("equal", [False, True])
def test_unchanged_reference_dominates_or_matches_all_alternatives(equal):
    result = report_fixture()
    summary = {"net_return_pct": 20, "max_drawdown_pct": 1, "closed_trades": 7}
    result["automatic_research"]["baseline"] = summary
    if equal:
        for row in result["experiment"]["rows"]:
            row["summary"] = deepcopy(summary)
    view = analyze(result)
    assert view["status"] == "no_eligible_alternatives"
    assert view["counts"]["eligible"] == 5
    assert view["counts"]["frontier"] == 0
    assert view["baseline"]["dominates_or_matches_candidates"] == 5
    assert view["reasons"]
    assert not view["choices"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_exposure_pct", 101),
        ("exposure_fill_mode", "partial"),
        ("priority_seed", 5),
        ("entry_priority", "random"),
        ("modes", ["RSI"]),
    ],
)
def test_fixed_native_execution_fields_cannot_change(key, value):
    result = report_fixture()
    row = _row(result)
    row["strategies"][0]["config"][key] = value
    _changed_settings(row)
    assert analyze(result)["counts"]["excluded"] == 1


@pytest.mark.parametrize("version", [8, 9])
@pytest.mark.parametrize("key,value", [("allocation_pct", 90), ("order_size_pct", 9)])
def test_old_recipes_cannot_change_fixed_sizing(version, key, value):
    result = report_fixture(version)
    row = _row(result)
    if key == "allocation_pct":
        row["strategies"][0][key] = value
    else:
        row["strategies"][0]["config"][key] = value
    _changed_settings(row)
    assert analyze(result)["counts"]["excluded"] == 1


def test_size_and_missing_native_version_bounds(monkeypatch):
    result = report_fixture()
    monkeypatch.setattr("research.research_tradeoffs.MAX_BYTES", 100)
    assert analyze(result)["status"] == "unavailable"
    monkeypatch.undo()
    result["evaluation_basis"]["comparison"]["execution"].pop("engine_version")
    _seal_basis(result["evaluation_basis"])
    assert analyze(result)["status"] == "unavailable"


def test_unavailable_order_is_deterministic_and_gate_version_is_recipe_pinned():
    result = report_fixture(8)
    for row in result["experiment"]["rows"]:
        row["summary"]["net_return_pct"] = -1
    first = analyze(result)
    result["experiment"]["rows"].reverse()
    assert analyze(result) == first
    result = report_fixture(8)
    row = _row(result)
    row["hypothesis"] = {"id": "momentum"}
    row["automatic_stock_condition"]["hypothesis"] = deepcopy(row["hypothesis"])
    row["evaluation_basis"]["comparison"]["execution"]["entry_conditions"] = {
        "version": "research-joint-entry-conditions-v2",
        "hypothesis": row["hypothesis"],
    }
    _seal_basis(row["evaluation_basis"])
    assert analyze(result)["counts"]["excluded"] == 1
    row["evaluation_basis"]["comparison"]["execution"]["entry_conditions"]["version"] = (
        "research-joint-entry-conditions-v1"
    )
    row["strategies"][0]["entry_condition"] = deepcopy(
        row["evaluation_basis"]["comparison"]["execution"]["entry_conditions"]
    )
    _changed_settings(row)
    _seal_basis(row["evaluation_basis"])
    assert analyze(result)["counts"]["excluded"] == 0


def test_maximum_saved_row_bound_keeps_small_display_without_truncating_frontier():
    result = report_fixture()
    source = _row(result)
    rows = []
    for index in range(MAX_ROWS):
        row = deepcopy(source)
        row["strategies"][0]["name"] = f"Recorded candidate {index}"
        _changed_settings(row)
        row["summary"].update(net_return_pct=2 + index / 1000, max_drawdown_pct=1 + index / 1000)
        rows.append(row)
    result["experiment"]["rows"] = rows
    view = analyze(result)
    assert view["status"] == "available"
    assert view["counts"]["eligible"] == MAX_ROWS
    assert view["counts"]["frontier"] == MAX_ROWS
    assert len(view["choices"]) <= 4


@pytest.mark.parametrize("version", [8, 12])
@pytest.mark.parametrize(
    "missing", ["account", "strategy", "both", "mismatched_strategy", "none_with_marker"]
)
def test_declared_conditions_require_exact_account_and_strategy_markers(version, missing):
    result = report_fixture(version)
    row = _row(result)
    hypothesis = {"id": "momentum"}
    marker = {
        "version": f"research-joint-entry-conditions-v{1 if version == 8 else 2}",
        "hypothesis": hypothesis,
    }
    row["hypothesis"] = deepcopy(hypothesis)
    row["automatic_stock_condition"]["hypothesis"] = deepcopy(hypothesis)
    row["evaluation_basis"]["comparison"]["execution"]["entry_conditions"] = deepcopy(marker)
    row["strategies"][0]["entry_condition"] = deepcopy(marker)
    if missing in ("account", "both"):
        row["evaluation_basis"]["comparison"]["execution"].pop("entry_conditions")
    if missing in ("strategy", "both"):
        row["strategies"][0].pop("entry_condition")
    if missing == "mismatched_strategy":
        row["strategies"][0]["entry_condition"]["hypothesis"] = {"id": "other"}
    if missing == "none_with_marker":
        row["hypothesis"] = {"id": "none"}
        row["automatic_stock_condition"]["hypothesis"] = {"id": "none"}
    _changed_settings(row)
    _seal_basis(row["evaluation_basis"])
    view = analyze(result)
    assert view["counts"]["excluded"] == 1


def test_unexplained_admission_mutation_fails_even_with_unchanged_verified_sources():
    result = report_fixture()
    row = _row(result)
    row["evaluation_basis"]["cohort_id"] = fingerprint("unexplained")
    _seal_basis(row["evaluation_basis"])
    view = analyze(result)
    assert view["counts"]["excluded"] == 1
    assert "Admission cohort" in view["unavailable"][0]["reasons"][0]


@pytest.mark.parametrize("admitted", [True, False])
def test_actual_frozen_clock_branch_can_explain_changed_cohort(admitted):
    result = report_fixture()
    result["automatic_research"]["recipe"]["timing_research"] = {
        "version": "automatic-timing-states-v1",
        "interval": "1m",
        "strategies": [
            {
                "id": "s1",
                "original": {},
                "branches": [
                    {"id": "original", "changes": {}},
                    {"id": "entry_0930", "changes": {"entry_time": "09:30"}},
                ],
            }
        ],
    }
    row = _row(result)
    row["evaluation_basis"]["cohort_id"] = fingerprint("later-clock")
    row["strategies"][0]["config"]["entry_time"] = "09:30" if admitted else "09:45"
    _changed_settings(row)
    _seal_basis(row["evaluation_basis"])
    assert analyze(result)["counts"]["excluded"] == (0 if admitted else 1)


def test_bad_selected_gate_fails_the_whole_view():
    result = report_fixture()
    result["automatic_stock_condition"]["hypothesis"] = {"id": "momentum"}
    assert analyze(result)["status"] == "unavailable"
