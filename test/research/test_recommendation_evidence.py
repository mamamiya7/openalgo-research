"""Development-only recommendation gates, using exact synthetic account marks."""

from copy import deepcopy
from datetime import date, timedelta
from random import Random

import pytest

from research.development_evidence import build
from research.evaluation_basis import IDENTITIES
from research.evaluation_basis import VERSION as BASIS_VERSION
from research.recommendation_evidence import MAX_DAYS, _interval, _pair, account_evidence, assess
from research.report_contract import fingerprint


def days(count=120):
    first = date(2026, 1, 1)
    return [(first + timedelta(days=index)).isoformat() for index in range(count)]


def shard(
    returns,
    *,
    dates=None,
    period="search",
    stress=False,
    variant="selected",
    minute=False,
    binding=None,
    capital=1000,
):
    dates = dates or days(len(returns))
    keys = [f"{day}T15:30:00+05:30" for day in dates] if minute else dates
    config = {"cost_bps": 5 if stress else 0, "slippage_bps": 0, "variant": variant}
    settings = [{"id": "a", "name": "A", "allocation_pct": 100, "config": config}]
    comparison = {
        "period": "selection",
        "currency": "INR",
        "capital": float(capital),
        "execution": {"engine": "vectorbt", "adapter_version": "v4"},
        "costs": [{"strategy_id": "a", "cost_bps": config["cost_bps"], "slippage_bps": 0}],
    }
    comparison["id"] = fingerprint({"version": BASIS_VERSION, **comparison})
    basis = {
        "version": BASIS_VERSION,
        "status": "verified",
        **{key: fingerprint({"key": key, "dates": keys}) for key in IDENTITIES},
    }
    basis["observations_id"] = fingerprint({"interval": "1m" if minute else "D", "timeline": keys})
    basis["evidence_id"] = fingerprint(
        {"version": BASIS_VERSION, **{key: basis[key] for key in IDENTITIES}}
    )
    basis["comparison"] = comparison
    basis["period"] = {
        "kind": "selection",
        "from": keys[0],
        "to": keys[-1],
        "observations": len(keys),
        "interval": "1m" if minute else "D",
    }
    curve, equity, peak = [], capital, capital
    for key, value in zip(keys, returns, strict=True):
        equity *= 1 + value
        peak = max(peak, equity)
        curve.append(
            {
                "date": key[:10],
                **({"timestamp": key} if minute else {}),
                "equity": equity,
                "cash": 0,
                "drawdown_pct": (peak - equity) / peak * 100,
                "open_positions": 1,
            }
        )
    report = {
        "strategies": settings,
        "config": {"initial_capital": capital},
        "evaluation_basis": basis,
        "equity_curve": curve,
    }
    kwargs = {
        "evaluation_id": fingerprint({"settings": settings, "period": period, "stress": stress}),
        "binding": binding
        or {"inputs_artifact": "fixture-inputs", "search_identity": "fixture-search"},
        "settings": settings,
        "hypothesis": {"id": "none"},
        "period": period,
        "stress": stress,
        "expected_timeline": keys,
    }
    return build(report, **kwargs)


def scenario(*, size=120, edge=0.001, cost_edge=None, minute=False):
    dates = days(size)
    original = [0.001 if index % 2 else -0.0005 for index in range(size)]
    gain = [value + edge for value in original]
    higher = [value + (edge if cost_edge is None else cost_edge) - 0.0001 for value in original]
    kwargs = {"dates": dates, "minute": minute}
    result = {
        "candidate": shard(gain, **kwargs),
        "baseline": shard(original, variant="baseline", **kwargs),
        "stress_candidate": shard(higher, stress=True, **kwargs),
        "stress_baseline": shard(
            [value - 0.0001 for value in original], variant="baseline", stress=True, **kwargs
        ),
        "folds": [],
        "sensitivity": [
            shard(
                [value + edge * fraction for value in original],
                variant=f"neighbour{index}",
                **kwargs,
            )
            for index, fraction in enumerate((0.9, 0.8))
        ],
    }
    # Three disjoint later-development windows inside the final training prefix.
    width = min(21, size // 3)
    for index in range(3):
        start = size - (3 - index) * width
        stop = start + width
        part = {"dates": dates[start:stop], "period": f"check{index + 1}", "minute": minute}
        result["folds"].append(
            {
                "candidate": shard(gain[start:stop], **part),
                "baseline": shard(original[start:stop], variant="baseline", **part),
                "stress_candidate": shard(higher[start:stop], stress=True, **part),
                "stress_baseline": shard(
                    [value - 0.0001 for value in original[start:stop]],
                    variant="baseline",
                    stress=True,
                    **part,
                ),
            }
        )
    return result


def test_consistent_account_gain_qualifies_with_descriptive_not_confidence_intervals():
    inputs = scenario()
    before = deepcopy(inputs)
    result = assess(**inputs)
    assert result["status"] == "supported"
    assert result["qualified"] is True
    assert result["reasons"] == []
    assert result["procedure_sessions"] == 63
    assert result["uncertainty"]["sessions"] == 120
    assert result["uncertainty"]["mean_daily_edge_bps"] == pytest.approx(10)
    assert [row["block_sessions"] for row in result["uncertainty"]["ranges"]] == [5, 10]
    assert all(row["lower_bps"] > 0 for row in result["uncertainty"]["ranges"])
    assert "Not search-adjusted confidence" in result["uncertainty"]["interpretation"]
    assert "probability" not in result["uncertainty"]
    assert inputs == before
    assert assess(**inputs) == result


def test_no_edge_is_not_a_recommendation_even_with_many_observations():
    result = assess(**scenario(edge=0))
    assert result["status"] == "not_supported"
    assert not result["qualified"]
    assert any("ranges include" in reason for reason in result["reasons"])


def test_gain_that_disappears_at_higher_costs_fails():
    result = assess(**scenario(cost_edge=-0.001))
    assert result["status"] == "not_supported"
    assert any("higher-cost" in reason for reason in result["reasons"])


def test_one_later_fold_failure_rejects_an_excellent_training_winner():
    inputs = scenario()
    inputs["folds"][1]["candidate"] = deepcopy(inputs["folds"][1]["baseline"])
    inputs["folds"][1]["stress_candidate"] = deepcopy(inputs["folds"][1]["stress_baseline"])
    result = assess(**inputs)
    assert result["status"] == "not_supported"
    assert any("later development" in reason for reason in result["reasons"])


def test_one_lucky_jump_does_not_supply_independent_evidence():
    inputs = scenario()
    original = [0.0001] * 120
    lucky = list(original)
    lucky[63] += 0.3
    inputs["baseline"] = shard(original, variant="baseline")
    inputs["candidate"] = shard(lucky)
    result = assess(**inputs)
    assert result["status"] == "not_supported"
    assert any(row["lower_bps"] <= 0 for row in result["uncertainty"]["ranges"])


def test_sparse_history_returns_insufficient_without_resampling():
    result = assess(**scenario(size=59))
    assert result["status"] == "insufficient"
    assert "uncertainty" not in result
    assert result["qualified"] is False


def test_parameter_cliff_rejects_an_isolated_winner():
    inputs = scenario()
    original = [0.001 if index % 2 else -0.0005 for index in range(120)]
    inputs["sensitivity"] = [
        shard([value + 0.0001 for value in original], variant=f"neighbour{index}")
        for index in range(2)
    ]
    result = assess(**inputs)
    assert result["sensitivity"]["passing"] == 2
    assert result["sensitivity"]["status"] == "not_supported"
    assert result["status"] == "not_supported"


@pytest.mark.parametrize("name", ["candidate", "baseline", "stress_candidate", "stress_baseline"])
def test_missing_retained_account_is_unsupported(name):
    inputs = scenario()
    inputs[name] = None
    result = assess(**inputs)
    assert result["status"] == "unsupported"
    assert not result["qualified"]


@pytest.mark.parametrize(
    "damage",
    [
        "hash",
        "final",
        "basis_final",
        "binding",
        "capital",
        "source",
        "cost",
        "neighbour_duplicate",
        "neighbour_missing",
        "fold_overlap",
        "fold_outside",
    ],
)
def test_incompatible_or_unqualified_inputs_never_produce_recommendation(damage):
    inputs = scenario()
    if damage == "hash":
        inputs["candidate"]["curve"][0]["equity"] += 1
    elif damage == "final":
        inputs["candidate"]["period"] = "final"
    elif damage == "basis_final":
        inputs["candidate"]["evaluation_basis"]["period"]["kind"] = "evaluation"
    elif damage == "binding":
        inputs["candidate"] = shard(
            [0.002] * 120, binding={"inputs_artifact": "other", "search_identity": "other"}
        )
    elif damage == "capital":
        inputs["candidate"] = shard([0.002] * 120, capital=2000)
    elif damage == "source":
        inputs["candidate"]["evaluation_basis"]["source_id"] = "other"
    elif damage == "cost":
        inputs["stress_candidate"] = shard([0.002] * 120, stress=False)
        inputs["stress_candidate"]["stress"] = True
        inputs["stress_candidate"]["id"] = fingerprint(
            {key: value for key, value in inputs["stress_candidate"].items() if key != "id"}
        )
    elif damage == "neighbour_duplicate":
        inputs["sensitivity"][1] = deepcopy(inputs["sensitivity"][0])
    elif damage == "neighbour_missing":
        inputs["sensitivity"] = None
    elif damage == "fold_overlap":
        inputs["folds"][1] = deepcopy(inputs["folds"][0])
    else:
        for key in inputs["folds"][0]:
            inputs["folds"][0][key] = shard(
                [0.001] * 21,
                dates=days(300)[200:221],
                period="check1",
                stress=key.startswith("stress"),
                variant="baseline" if "baseline" in key else "selected",
            )
    result = assess(**inputs)
    assert result["status"] in ("unsupported", "insufficient")
    assert not result["qualified"]


def test_minute_marks_count_sessions_not_candles():
    result = assess(**scenario(minute=True))
    assert result["status"] == "supported"
    assert result["uncertainty"]["sessions"] == 120


def test_serially_correlated_edge_has_wider_block_range_than_independent_shuffling():
    edges = ([0.002] * 20 + [-0.001] * 20) * 3
    blocks = _interval(edges, 10)
    iid = _interval(edges, 1)
    assert blocks["upper_bps"] - blocks["lower_bps"] > 2 * (iid["upper_bps"] - iid["lower_bps"])
    assert blocks["lower_bps"] < 0


def test_session_bound_is_checked_before_resampling():
    too_many = shard([0.001] * (MAX_DAYS + 1))
    inputs = scenario()
    inputs["candidate"] = too_many
    result = assess(**inputs)
    assert result["status"] == "unsupported"
    assert "limit" in result["reasons"][0]


def test_account_report_adapter_builds_existing_full_shard_and_rejects_final():
    saved = scenario()["candidate"]
    report = {
        "strategies": saved["settings"],
        "config": {"initial_capital": saved["account"]["initial_capital"]},
        "equity_curve": saved["curve"],
        "evaluation_basis": saved["evaluation_basis"],
    }
    adapted = account_evidence(report, period="search", binding=saved["binding"])
    assert adapted["curve"] == saved["curve"]
    assert adapted["simple_returns"] == saved["simple_returns"]
    with pytest.raises(ValueError, match="final-period"):
        account_evidence(report, period="final", binding=saved["binding"])


def rebuild(saved, *, settings=None):
    settings = settings or deepcopy(saved["settings"])
    return build(
        {
            "strategies": settings,
            "config": {"initial_capital": saved["account"]["initial_capital"]},
            "equity_curve": saved["curve"],
            "evaluation_basis": saved["evaluation_basis"],
            **(
                {"research_opportunity_basis": saved["research_opportunity_basis"]}
                if "research_opportunity_basis" in saved
                else {}
            ),
            **(
                {"automatic_stock_condition": saved["stock_condition"]}
                if "stock_condition" in saved
                else {}
            ),
        },
        evaluation_id=saved["evaluation_id"],
        binding=saved["binding"],
        settings=settings,
        hypothesis=saved["hypothesis"],
        period=saved["period"],
        stress=saved["stress"],
        expected_timeline=saved["timeline"],
    )


def test_validly_signed_cost_account_cannot_silently_change_an_exit_rule():
    inputs = scenario()
    settings = deepcopy(inputs["stress_candidate"]["settings"])
    settings[0]["config"]["hold_sessions"] = 99
    inputs["stress_candidate"] = rebuild(inputs["stress_candidate"], settings=settings)
    result = assess(**inputs)
    assert result["status"] == "unsupported"
    assert "rules other" in result["reasons"][0]


def test_validly_signed_neighbour_cannot_change_the_hard_limits():
    inputs = scenario()
    settings = deepcopy(inputs["sensitivity"][0]["settings"])
    settings[0]["execution_policy"] = {"risk_limits": {"max_gross_exposure_pct": 200}}
    inputs["sensitivity"][0] = rebuild(inputs["sensitivity"][0], settings=settings)
    result = assess(**inputs)
    assert result["status"] == "unsupported"
    assert "hard portfolio limits" in result["reasons"][0]


def test_fold_cannot_silently_change_unchanged_reference():
    inputs = scenario()
    for key in ("baseline", "stress_baseline"):
        saved = inputs["folds"][0][key]
        settings = deepcopy(saved["settings"])
        settings[0]["config"]["variant"] = "changed_reference"
        inputs["folds"][0][key] = rebuild(saved, settings=settings)
    result = assess(**inputs)
    assert result["status"] == "unsupported"
    assert "unchanged reference" in result["reasons"][0]


def replace_returns(inputs, selected, neighbours):
    original = [0.0002] * len(selected)
    dates = days(len(selected))
    inputs.update(
        candidate=shard(selected),
        baseline=shard(original, variant="baseline"),
        stress_candidate=shard([value - 0.0001 for value in selected], stress=True),
        stress_baseline=shard(
            [value - 0.0001 for value in original], stress=True, variant="baseline"
        ),
        sensitivity=[
            shard(values, variant=f"neighbour{index}") for index, values in enumerate(neighbours)
        ],
    )
    for index, fold in enumerate(inputs["folds"]):
        start, stop = 57 + 21 * index, 78 + 21 * index
        part = {"dates": dates[start:stop], "period": f"check{index + 1}"}
        fold.update(
            candidate=shard(selected[start:stop], **part),
            baseline=shard(original[start:stop], variant="baseline", **part),
            stress_candidate=shard(
                [value - 0.0001 for value in selected[start:stop]], stress=True, **part
            ),
            stress_baseline=shard(
                [value - 0.0001 for value in original[start:stop]],
                stress=True,
                variant="baseline",
                **part,
            ),
        )
    return inputs


@pytest.mark.parametrize("seed", range(8))
def test_correlated_null_winner_suite_does_not_turn_trial_count_into_evidence(seed):
    # Deliberate training selection from noisy alternatives with common temporal
    # dependence. This small regression suite is not an estimate of false-positive
    # probability or a correction for the optimiser's adaptive history.
    rng = Random(seed)
    shared = [rng.gauss(0, 0.003) for _ in range(12)]
    alternatives = [
        [0.0002 + shared[index // 10] + rng.gauss(0, 0.004) for index in range(120)]
        for _ in range(32)
    ]
    selected = max(alternatives, key=sum)
    result = assess(**replace_returns(scenario(), selected, alternatives[:2]))
    assert not result["qualified"]


@pytest.mark.parametrize("seed", range(4))
def test_consistent_paired_gain_survives_shared_account_noise(seed):
    rng = Random(seed)
    selected = [0.0012 + rng.gauss(0, 0.0001) for _ in range(120)]
    neighbours = [[value - reduction for value in selected] for reduction in (0.0001, 0.0002)]
    result = assess(**replace_returns(scenario(), selected, neighbours))
    assert result["qualified"]


def test_shifting_market_is_not_hidden_by_a_positive_full_development_average():
    selected = [0.003] * 60 + [-0.001] * 60
    result = assess(**replace_returns(scenario(), selected, [list(selected), list(selected)]))
    assert result["development"]["candidate_return_pct"] > 0
    assert result["status"] == "not_supported"
    assert any("later development" in reason for reason in result["reasons"])


def densify(saved):
    """Two real retained marks per session, with identical final session equity."""
    timeline, curve = [], []
    previous = peak = saved["account"]["initial_capital"]
    for point in saved["curve"]:
        for time, equity in (("09:15:00", previous), ("15:30:00", point["equity"])):
            key = f"{point['date']}T{time}+05:30"
            timeline.append(key)
            peak = max(peak, equity)
            curve.append(
                {
                    "date": point["date"],
                    "timestamp": key,
                    "equity": equity,
                    "cash": 0,
                    "drawdown_pct": (peak - equity) / peak * 100,
                    "open_positions": 1,
                }
            )
        previous = point["equity"]
    basis = deepcopy(saved["evaluation_basis"])
    basis["observations_id"] = fingerprint({"interval": "1m", "timeline": timeline})
    basis["evidence_id"] = fingerprint(
        {"version": BASIS_VERSION, **{key: basis[key] for key in IDENTITIES}}
    )
    basis["period"].update(
        interval="1m", observations=len(timeline), **{"from": timeline[0], "to": timeline[-1]}
    )
    return build(
        {
            "strategies": saved["settings"],
            "config": {"initial_capital": saved["account"]["initial_capital"]},
            "equity_curve": curve,
            "evaluation_basis": basis,
        },
        evaluation_id=saved["evaluation_id"],
        binding=saved["binding"],
        settings=saved["settings"],
        hypothesis=saved["hypothesis"],
        period=saved["period"],
        stress=saved["stress"],
        expected_timeline=timeline,
    )


def test_more_intraday_marks_do_not_inflate_account_day_support_or_ranges():
    inputs = scenario()
    original = assess(**inputs)
    for name in ("candidate", "baseline", "stress_candidate", "stress_baseline"):
        inputs[name] = densify(inputs[name])
    for fold in inputs["folds"]:
        for name in fold:
            fold[name] = densify(fold[name])
    inputs["sensitivity"] = [densify(row) for row in inputs["sensitivity"]]
    assert len(inputs["candidate"]["timeline"]) == 240
    result = assess(**inputs)
    assert result["status"] == "supported"
    assert result["uncertainty"] == original["uncertainty"]
    assert result["procedure_sessions"] == original["procedure_sessions"] == 63


def test_account_ruin_never_invents_later_daily_returns():
    inputs = scenario()
    inputs["candidate"] = shard([-1] + [0] * 119)
    result = assess(**inputs)
    assert result["status"] == "unsupported"
    assert "zero" in result["reasons"][0]


def test_raw_mark_limit_precedes_any_hashing_or_resampling():
    inputs = scenario()
    inputs["candidate"]["timeline"] = ["2026-01-01"] * 100_001
    result = assess(**inputs)
    assert result["status"] == "unsupported"
    assert "observation limit" in result["reasons"][0]


def test_reserved_final_outcomes_are_not_an_accepted_selection_argument():
    with pytest.raises(TypeError, match="final"):
        assess(**scenario(), final={"profit": 999_999})


def test_extreme_bounded_inputs_never_produce_nonfinite_resampling_evidence():
    with pytest.raises(ValueError, match="finite diagnostic bounds"):
        _interval([1e307, -1] * 60, 10)


def native_filtered_pair(*, attested):
    """Actual controller slicing/gating and basis; synthetic account marks only."""
    from test_turnover_integration import prepared

    from research import expanded_stock_market as context
    from research.automatic_hypotheses import _gate
    from research.automatic_protocol import slice_period
    from research.connectors.optuna_portfolio import _settings
    from research.connectors.vectorbt_portfolio import _curve
    from research.entry_conditions import enrich
    from research.evaluation_basis import build_evaluation_basis

    value = prepared()
    current = slice_period(value, "search")
    original = build_evaluation_basis(current, period="selection")
    bundle = value["automatic_stock_market_context"]
    hypothesis = next(row for row in context.catalog() if row["id"] == "momentum_5_gt2__all")
    with context.PreparedJointContext(bundle) as prepared_gate:
        rows, receipt = _gate(
            current,
            bundle,
            hypothesis,
            {},
            "search",
            apply_fn=prepared_gate.apply,
            markers_fn=context.markers,
            calculation_version=context.FEATURE_VERSION,
            trailing_activation=True,
        )
    timeline, capital = current["snapshot"]["sessions"], current["portfolio"]["capital"]
    marks = _curve(
        timeline,
        [capital * (1 + index / 1000) for index in range(len(timeline))],
        [0] * len(timeline),
        [1] * len(timeline),
        capital,
        False,
    )
    baseline = {
        "strategies": _settings(current["strategies"]),
        "config": {"initial_capital": capital},
        "equity_curve": marks,
        "evaluation_basis": original,
    }
    candidate = {
        "strategies": _settings(rows),
        "config": {"initial_capital": capital},
        "equity_curve": marks,
        "evaluation_basis": build_evaluation_basis(
            {**current, "strategies": rows}, period="selection"
        ),
        "automatic_stock_condition": {
            "hypothesis": hypothesis,
            "bundle_id": bundle["id"],
            "gate_id": receipt["id"],
            "counts": receipt["counts"],
        },
    }
    conditioned = {
        **current,
        "strategies": rows,
        "portfolio": {
            **current["portfolio"],
            "entry_conditions": {"version": context.ENTRY_VERSION, "hypothesis": hypothesis},
        },
        "entry_condition_gates": [receipt],
    }
    enrich(candidate, conditioned)
    if attested:
        baseline["research_opportunity_basis"] = deepcopy(original)
        candidate["research_opportunity_basis"] = deepcopy(original)
    binding = {
        "inputs_artifact": "native-fixture-input",
        "search_identity": "native-fixture-search",
    }
    return account_evidence(candidate, period="search", binding=binding), account_evidence(
        baseline, period="search", binding=binding
    )


def test_native_entry_filter_pairs_original_opportunities_without_equating_actual_admissions():
    candidate, baseline = native_filtered_pair(attested=True)
    assert candidate["evaluation_basis"]["cohort_id"] != baseline["evaluation_basis"]["cohort_id"]
    assert candidate["stock_condition"]["counts"]["filtered"] == 125
    paired = _pair(candidate, baseline, period="search")
    assert (
        paired["opportunities"]["candidate_admission_id"]
        != paired["opportunities"]["baseline_admission_id"]
    )
    assert paired["opportunities"]["basis_id"] == fingerprint(
        baseline["research_opportunity_basis"]
    )


def test_native_different_admissions_without_original_opportunity_attestation_do_not_qualify():
    candidate, baseline = native_filtered_pair(attested=False)
    with pytest.raises(ValueError, match="original opportunity"):
        _pair(candidate, baseline, period="search")


def test_original_opportunity_attestation_cannot_disagree_or_be_one_sided():
    candidate, baseline = native_filtered_pair(attested=True)
    missing = deepcopy(baseline)
    del missing["research_opportunity_basis"]
    missing = rebuild(missing)
    with pytest.raises(ValueError, match="original opportunity"):
        _pair(candidate, missing, period="search")
    changed = deepcopy(baseline)
    opportunity = changed["research_opportunity_basis"]
    opportunity["cohort_id"] = fingerprint({"different_original_admissions": True})
    opportunity["evidence_id"] = fingerprint(
        {"version": BASIS_VERSION, **{key: opportunity[key] for key in IDENTITIES}}
    )
    changed = rebuild(changed)
    with pytest.raises(ValueError, match="original opportunity"):
        _pair(candidate, changed, period="search")


def test_original_opportunity_attestation_cannot_hide_changed_prices_or_original_source():
    candidate, _baseline = native_filtered_pair(attested=True)
    for key in ("source_id", "prices_id", "calendar_id", "instruments_id", "observations_id"):
        changed = deepcopy(candidate)
        opportunity = changed["research_opportunity_basis"]
        opportunity[key] = fingerprint({"different": key})
        opportunity["evidence_id"] = fingerprint(
            {"version": BASIS_VERSION, **{name: opportunity[name] for name in IDENTITIES}}
        )
        with pytest.raises(ValueError, match="opportunity|observation"):
            rebuild(changed)


@pytest.mark.parametrize(
    "damage", ["engine", "adapter", "clock", "condition_version", "condition_hypothesis", "cost"]
)
def test_enriched_native_pair_does_not_relax_other_execution_facts_or_undeclared_conditions(damage):
    candidate, baseline = native_filtered_pair(attested=True)
    changed = deepcopy(candidate)
    comparison = changed["evaluation_basis"]["comparison"]
    execution = comparison["execution"]
    if damage == "engine":
        execution["engine"] = "another_engine"
    elif damage == "adapter":
        execution["adapter_version"] = "another_adapter"
    elif damage == "clock":
        execution["native_clock"] = "different_closing_clock"
    elif damage == "condition_version":
        execution["entry_conditions"]["version"] = "unknown_future_rule"
    elif damage == "condition_hypothesis":
        execution["entry_conditions"]["hypothesis"] = {"id": "not_the_selected_condition"}
    else:
        comparison["costs"][0]["cost_bps"] += 1
        changed["settings"][0]["config"]["cost_bps"] += 1
    comparison["id"] = fingerprint(
        {
            "version": BASIS_VERSION,
            **{key: value for key, value in comparison.items() if key != "id"},
        }
    )
    if damage == "cost":
        changed = rebuild(changed)
        with pytest.raises(ValueError, match="execution, costs or limits"):
            _pair(changed, baseline, period="search")
    else:
        with pytest.raises(ValueError, match="execution|condition"):
            rebuild(changed)


def test_refitting_procedure_may_choose_different_declared_conditions_in_each_fold():
    from research.expanded_stock_market import catalog

    inputs = scenario()
    rules = catalog()[1:5]

    def attest(saved, original, rule=None):
        result = deepcopy(saved)
        result["research_opportunity_basis"] = deepcopy(original)
        if rule is not None:
            result["hypothesis"] = deepcopy(rule)
            condition = {
                "version": "research-joint-entry-conditions-v2",
                "hypothesis": deepcopy(rule),
            }
            for row in result["settings"]:
                row["entry_condition"] = deepcopy(condition)
            comparison = result["evaluation_basis"]["comparison"]
            comparison["execution"]["entry_conditions"] = condition
            comparison["id"] = fingerprint(
                {
                    "version": BASIS_VERSION,
                    **{key: value for key, value in comparison.items() if key != "id"},
                }
            )
        return rebuild(result)

    opportunity = deepcopy(inputs["baseline"]["evaluation_basis"])
    for key in ("candidate", "stress_candidate", "baseline", "stress_baseline"):
        inputs[key] = attest(inputs[key], opportunity, rules[0] if "candidate" in key else None)
    inputs["sensitivity"] = [attest(row, opportunity, rules[0]) for row in inputs["sensitivity"]]
    for index, fold in enumerate(inputs["folds"]):
        opportunity = deepcopy(fold["baseline"]["evaluation_basis"])
        for key in fold:
            fold[key] = attest(
                fold[key], opportunity, rules[index + 1] if "candidate" in key else None
            )
    result = assess(**inputs)
    assert result["status"] == "supported", result["reasons"]
    assert all(row["normal"]["passes"] for row in result["folds"])
