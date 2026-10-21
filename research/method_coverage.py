"""Metadata-only coverage of actual v8 work; no prices, shards or engine calls.

The service owns checkpoint content-address/owner verification. This builder
cross-checks the bounded mandate, calculation and development index it supplies.
It does not prove numerical replay, shard availability or complete price coverage.
"""

import re
from copy import deepcopy

from research.automatic_trailing import apply_choices as trailing_choices
from research.automatic_walk_forward import FITS, checkpoint_rows, data_period, fit_names
from research.connectors.optuna_portfolio import (
    MAX_CHECKPOINT_BYTES,
    _candidate,
    _prepare,
    _settings,
)
from research.evaluation_basis import IDENTITIES
from research.evaluation_basis import VERSION as BASIS_VERSION
from research.joint_stock_market import markers
from research.method_registry import (
    CAPACITY_RECIPE_VERSION,
    EXECUTION_RECIPE_VERSION,
    EXPANDED_RECIPE_VERSION,
    PRIORITY_RECIPE_VERSION,
    RECIPE_VERSION,
    SEED_RECIPE_VERSION,
    SENSITIVITY_RECIPE_VERSION,
    TURNOVER_RECIPE_VERSION,
    bounded,
    catalog,
    pinned_hypotheses,
)
from research.report_contract import fingerprint, settings_identity

VERSION = "research-method-coverage-v1"
MAX_MANIFEST_BYTES = 512 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
COUNTS = ("allowed", "filtered", "unknown", "original_excluded", "pending")


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _sealed(value, key="id", maximum=64 * 1024):
    bounded(value, maximum)
    if (
        not isinstance(value, dict)
        or not _hash(value.get(key))
        or value[key] != fingerprint({k: v for k, v in value.items() if k != key})
    ):
        raise ValueError("Saved method evidence seal changed")


def _count(value, maximum=25000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Saved method evidence count exceeds its bound")
    return value


def _record(family):
    return {
        "id": family["id"],
        "registered": len(family["registered_hypothesis_ids"]),
        "proposed": 0,
        "evaluated": 0,
        "reused": 0,
        "retained_histories": 0,
        "unavailable_histories": 0,
        "untested": len(family["registered_hypothesis_ids"]),
        "unknown_entry_checks": 0,
        "budget_deferred": 0,
        "data_status": "not_observed",
        "status": "pending",
    }


def _manifest(manifest, calculation, binding, periods, context_id, recipe_version):
    _sealed(manifest, maximum=MAX_MANIFEST_BYTES)
    if (
        manifest.get("version") != "research-development-evidence-manifest-v1"
        or manifest.get("binding") != binding
        or manifest.get("reserved_final_excluded") is not True
        or manifest.get("status") not in ("collecting", "complete", "partial", "unavailable")
        or not isinstance(manifest.get("entries"), dict)
        or len(manifest["entries"])
        > (
            512
            if recipe_version == SEED_RECIPE_VERSION
            else 384
            if recipe_version
            in (
                EXECUTION_RECIPE_VERSION,
                PRIORITY_RECIPE_VERSION,
                TURNOVER_RECIPE_VERSION,
                CAPACITY_RECIPE_VERSION,
                SENSITIVITY_RECIPE_VERSION,
                SEED_RECIPE_VERSION,
            )
            else 320
        )
    ):
        raise ValueError("Saved method development index changed")
    expected = {}
    marks = size = 0
    by_hypothesis = {row["id"]: row for row in pinned_hypotheses(recipe_version)}
    for key, row in calculation["evaluations"].items():
        if not _hash(key) or not isinstance(row, dict):
            raise ValueError("Saved method evaluation identity changed")
        basis = row.get("evaluation_basis", {})
        comparison = basis.get("comparison", {})
        if (
            basis.get("version") != BASIS_VERSION
            or basis.get("status") != "verified"
            or not all(_hash(basis.get(field)) for field in IDENTITIES)
            or basis.get("evidence_id")
            != fingerprint(
                {"version": BASIS_VERSION, **{field: basis[field] for field in IDENTITIES}}
            )
            or basis.get("period", {}).get("kind") not in ("selection", "evaluation")
            or comparison.get("period") != basis["period"]["kind"]
            or comparison.get("id")
            != fingerprint(
                {
                    "version": BASIS_VERSION,
                    **{field: value for field, value in comparison.items() if field != "id"},
                }
            )
        ):
            raise ValueError("Saved method evaluation basis changed")
        if basis.get("period", {}).get("kind") == "evaluation":
            if "development_evidence" in row:
                raise ValueError("Final method evidence was included in development histories")
            continue
        descriptor = row.get("development_evidence")
        condition = row.get("automatic_stock_condition")
        if (
            not isinstance(descriptor, dict)
            or not isinstance(condition, dict)
            or descriptor.get("evaluation_id") != key
            or descriptor.get("binding") != binding
            or descriptor.get("period") not in periods
            or descriptor.get("period") == "final"
            or type(descriptor.get("stress")) is not bool
            or descriptor.get("basis_id") != fingerprint(basis)
            or descriptor.get("hypothesis_id") != fingerprint(condition.get("hypothesis"))
            or descriptor.get("condition_id") != fingerprint(condition)
            or condition.get("bundle_id") != context_id
            or not _hash(condition.get("gate_id"))
            or row.get("counts") != condition.get("counts")
            or descriptor.get("status") not in ("retained", "unavailable")
        ):
            raise ValueError("Saved method evaluation and history index disagree")
        hypothesis = condition.get("hypothesis", {})
        counts = row.get("counts")
        if (
            hypothesis != by_hypothesis.get(hypothesis.get("id"))
            or not isinstance(counts, dict)
            or set(counts) != set(COUNTS)
            or sum(_count(counts[field]) for field in COUNTS) > 25000
        ):
            raise ValueError("Saved method evaluation conditions changed")
        if descriptor["status"] == "retained":
            if (
                manifest.get("plan", {}).get("status") != "available"
                or descriptor.get("evaluation_basis_id") != basis["evidence_id"]
            ):
                raise ValueError("Saved method history admission or basis changed")
            if not all(
                _hash(descriptor.get(field))
                for field in ("id", "config_id", "evaluation_basis_id", "inputs_artifact")
            ):
                raise ValueError("Saved method history identity changed")
            marks += _count(descriptor.get("marks"), 100000)
            size += _count(descriptor.get("bytes"), 24 * 1024 * 1024)
            if not descriptor["marks"] or not descriptor["bytes"]:
                raise ValueError("Saved method history quantities changed")
        expected[key] = descriptor
    if (
        expected != manifest["entries"]
        or manifest.get("marks") != marks
        or manifest.get("bytes") != size
        or marks > 4_000_000
        or size > 128 * 1024 * 1024
    ):
        raise ValueError("Saved method development index totals changed")
    return expected


def build(mandate, calculation, development_manifest):
    try:
        return _build(mandate, calculation, development_manifest)
    except (
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        RecursionError,
        ArithmeticError,
    ) as error:
        raise ValueError("Saved method metadata is malformed") from error


def _build(mandate, calculation, development_manifest):
    """Describe recorded methods, without changing any saved numerical identity."""
    if mandate is None or calculation is None or development_manifest is None:
        return {
            "version": VERSION,
            "status": "unavailable",
            "reason": "This run has no complete saved method metadata.",
        }
    from research.research_mandate import EXECUTION_MAX_METADATA_BYTES, MAX_METADATA_BYTES

    source_version = (mandate.get("source") or {}).get("recipe_version")
    _sealed(
        mandate,
        maximum=EXECUTION_MAX_METADATA_BYTES
        if source_version
        in (
            EXECUTION_RECIPE_VERSION,
            PRIORITY_RECIPE_VERSION,
            TURNOVER_RECIPE_VERSION,
            CAPACITY_RECIPE_VERSION,
            SENSITIVITY_RECIPE_VERSION,
            SEED_RECIPE_VERSION,
        )
        else MAX_METADATA_BYTES,
    )
    source = mandate.get("source", {})
    recipe_version = source.get("recipe_version")
    if recipe_version not in (
        RECIPE_VERSION,
        EXPANDED_RECIPE_VERSION,
        EXECUTION_RECIPE_VERSION,
        PRIORITY_RECIPE_VERSION,
        TURNOVER_RECIPE_VERSION,
        CAPACITY_RECIPE_VERSION,
        SENSITIVITY_RECIPE_VERSION,
        SEED_RECIPE_VERSION,
    ):
        return {
            "version": VERSION,
            "status": "unavailable",
            "reason": "Method coverage is available only for the pinned expanding-refit recipe.",
        }
    registry = catalog(recipe_version)
    hypotheses = pinned_hypotheses(recipe_version)
    materialize_markers = markers
    if recipe_version in (
        EXPANDED_RECIPE_VERSION,
        EXECUTION_RECIPE_VERSION,
        PRIORITY_RECIPE_VERSION,
        TURNOVER_RECIPE_VERSION,
        CAPACITY_RECIPE_VERSION,
        SENSITIVITY_RECIPE_VERSION,
        SEED_RECIPE_VERSION,
    ):
        from research.expanded_stock_market import markers as materialize_markers
    by_hypothesis = {row["id"]: row for row in hypotheses}
    search = mandate.get("search", {})
    proposals_per_fit = (
        68
        if recipe_version
        in (
            PRIORITY_RECIPE_VERSION,
            TURNOVER_RECIPE_VERSION,
            CAPACITY_RECIPE_VERSION,
            SENSITIVITY_RECIPE_VERSION,
            SEED_RECIPE_VERSION,
        )
        else 64
        if recipe_version == EXECUTION_RECIPE_VERSION
        else 48
    )
    max_simulations = (
        512
        if recipe_version == SEED_RECIPE_VERSION
        else 384
        if recipe_version
        in (
            EXECUTION_RECIPE_VERSION,
            PRIORITY_RECIPE_VERSION,
            TURNOVER_RECIPE_VERSION,
            CAPACITY_RECIPE_VERSION,
            SENSITIVITY_RECIPE_VERSION,
            SEED_RECIPE_VERSION,
        )
        else 320
    )
    if (
        mandate.get("version") != "research-mandate-v1"
        or search.get("hypothesis_catalog") != hypotheses
        or not all(_hash(source.get(key)) for key in ("inputs_artifact", "recipe_id"))
        or not _hash(search.get("stock_evidence_id"))
        or mandate.get("budget", {}).get("proposals")
        != proposals_per_fit * len(fit_names(recipe_version))
        or mandate.get("budget", {}).get("max_simulations") != max_simulations
        or mandate.get("economic_assumptions", {}).get("engine") != "vectorbt"
        or mandate.get("subject", {}).get("asset_profile") != "NSE_long_cash_equities"
    ):
        raise ValueError("Saved method mandate differs from the pinned programme")
    bounded(calculation, MAX_CHECKPOINT_BYTES)
    checkpoint_rows(calculation)
    if calculation["version"] != recipe_version or not _hash(calculation.get("binding")):
        raise ValueError("Saved method calculation belongs to another programme")
    binding = {
        "inputs_artifact": source["inputs_artifact"],
        "search_identity": calculation["binding"],
    }
    periods = {row["name"] for row in mandate.get("validation", {}).get("periods", [])}
    expected_periods = {"fit1", "check1", "fit2", "check2", "fit3", "check3", "search", "final"}
    if recipe_version in (SENSITIVITY_RECIPE_VERSION, SEED_RECIPE_VERSION):
        from research.recommendation_sensitivity import _verified_plan

        scenarios = _verified_plan(search.get("recommendation_sensitivity"))
        expected_periods.update(row["name"] for row in scenarios if row["status"] == "available")
    if recipe_version == SEED_RECIPE_VERSION:
        from research.recommendation_seeds import policy

        seed_plan = search.get("search_seeds", {})
        if seed_plan != policy(screening_proposals=seed_plan.get("screening_proposals_per_search")):
            raise ValueError("Saved search-repeat policy changed")
    if periods != expected_periods:
        raise ValueError("Saved method period roles changed")
    entries = _manifest(
        development_manifest,
        calculation,
        binding,
        periods,
        search["stock_evidence_id"],
        recipe_version,
    )
    settings = mandate.get("economic_assumptions", {}).get("settings")
    ranges = search.get("ranges")
    if (
        not isinstance(settings, list)
        or not 1 <= len(settings) <= 8
        or not isinstance(ranges, list)
        or len(ranges) != len(settings)
    ):
        raise ValueError("Saved method strategy settings changed")
    domains = {row["id"]: row["search"] for row in ranges}
    base, axes = _prepare([{**row, "search": domains[row["id"]]} for row in settings])
    execution = None
    execution_axes = {}
    if recipe_version in (
        EXECUTION_RECIPE_VERSION,
        PRIORITY_RECIPE_VERSION,
        TURNOVER_RECIPE_VERSION,
        CAPACITY_RECIPE_VERSION,
        SENSITIVITY_RECIPE_VERSION,
        SEED_RECIPE_VERSION,
    ):
        from research.execution_search import axes as policy_axes
        from research.execution_search import descriptor as execution_descriptor

        execution = search.get("execution_research")
        execution_axes = policy_axes(execution)
        if (
            execution_descriptor(
                {
                    "portfolio": {
                        "automatic_research": {"version": recipe_version},
                        "risk_limits": mandate.get("economic_assumptions", {}).get("risk_limits"),
                    },
                    "strategies": settings,
                }
            )
            != execution
            or not _hash(search.get("execution_features_id"))
            or mandate["budget"].get("proposals_per_fit") != proposals_per_fit
        ):
            raise ValueError("Saved execution method domains or hard limits changed")
    # Do not change the existing mandate contract. Its baseline determines the
    # exact v8 trailing domain; native clock-grid membership cannot be certified
    # without the larger source descriptor and is explicitly outside this view.
    from research.automatic_timing import _clock_config
    from research.automatic_trailing import descriptor as trailing_descriptor

    trailing = trailing_descriptor(base)
    aggregate = {row["id"]: _record(row) for row in registry["families"]}
    total_proposed = total_evaluated = total_reused = retained = unavailable = 0
    fits = []
    for fit_name in fit_names(recipe_version):
        period_name = data_period(fit_name)
        fit = calculation["fits"].get(fit_name)
        records = {row["id"]: _record(row) for row in registry["families"]}
        proposed_ids = {key: set() for key in records}
        if fit is None:
            skipped = (
                fit_name.startswith("search_seed")
                and calculation.get("recommendation_checks", {})
                .get("search_seeds", {})
                .get("status")
                == "not_run"
            )
            fits.append(
                {
                    "id": fit_name,
                    "status": "not_run" if skipped else "pending",
                    "proposals": 0,
                    "proposal_limit": proposals_per_fit,
                    "families": list(records.values()),
                }
            )
            continue
        seen = set()
        for number, trial in enumerate(fit["trials"]):
            params = trial.get("params", {})
            hypothesis = by_hypothesis.get(params.get("hypothesis"))
            if (
                hypothesis is None
                or type(trial.get("number")) is not int
                or trial.get("number") != number
                or trial.get("state") != "complete"
                or type(trial.get("reused")) is not bool
            ):
                raise ValueError("Saved method proposal identity changed")
            config_id = trial.get("config_id")
            candidate = fit["candidates"].get(config_id)
            if (
                candidate is None
                or candidate.get("hypothesis") != hypothesis
                or trial.get("value") != candidate["score"]
                or trial.get("reused") != (config_id in seen)
            ):
                raise ValueError("Saved method proposal and candidate disagree")
            expected_params = {
                "hypothesis",
                *axes,
                *(f"{row['id']}.trailing_choice" for row in base),
                *(f"{row['id']}.timing_choice" for row in base),
                *execution_axes,
            }
            if set(params) != expected_params:
                raise ValueError("Saved method proposal domains changed")
            materialized = trailing_choices(_candidate(base, params), params, trailing)
            # Check the finite clock identifier grammar and numerical settings.
            # This does not prove membership in the original calendar's grid.
            for row in materialized:
                choice = params[f"{row['id']}.timing_choice"]
                if choice != "original":
                    if mandate["subject"]["interval"] == "D":
                        raise ValueError("Daily method proposals cannot change native clocks")
                    if not isinstance(choice, str) or not re.fullmatch(
                        r"(?:entry|exit)_[0-9]{4}", choice
                    ):
                        raise ValueError("Saved method clock choice changed")
                    side, clock = choice.split("_")
                    original = {
                        key: row["config"][key]
                        for key in ("entry_time", "exit_time")
                        if key in row["config"]
                    }
                    row["config"] = _clock_config(
                        row["config"],
                        {"original": original},
                        {"changes": {f"{side}_time": clock[:2] + ":" + clock[2:]}},
                    )
            if execution is not None:
                from research.execution_search import apply_choices as execution_choices

                materialized = execution_choices(materialized, params, execution)
            if materialize_markers(_settings(materialized), hypothesis) != candidate["strategies"]:
                raise ValueError("Saved method proposal settings changed")
            family = hypothesis["family"]
            record = records[family]
            record["proposed"] += 1
            record["reused"] += int(trial["reused"])
            proposed_ids[family].add(hypothesis["id"])
            if config_id not in seen:
                key = fingerprint(
                    {
                        "settings": candidate["strategies"],
                        "hypothesis": hypothesis,
                        "period": period_name,
                        "stress": False,
                    }
                )
                evaluation = calculation["evaluations"].get(key)
                if not isinstance(evaluation, dict) or any(
                    candidate.get(field) != evaluation.get(field)
                    for field in (
                        "summary",
                        "entry_dates",
                        "counts",
                        "automatic_stock_condition",
                        "evaluation_basis",
                    )
                ):
                    raise ValueError("Saved method candidate has no matching native evaluation")
                descriptor = entries.get(key)
                if (
                    descriptor is None
                    or descriptor["hypothesis_id"] != fingerprint(hypothesis)
                    or descriptor["period"] != period_name
                    or descriptor["stress"]
                ):
                    raise ValueError("Saved method candidate history identity changed")
                if descriptor["status"] == "retained" and descriptor["config_id"] != config_id:
                    raise ValueError("Saved method history settings changed")
                counts = evaluation["counts"]
                if (
                    not isinstance(counts, dict)
                    or set(counts) != set(COUNTS)
                    or sum(_count(counts[key]) for key in COUNTS) > 25000
                ):
                    raise ValueError("Saved method entry counts changed")
                record["evaluated"] += 1
                record["unknown_entry_checks"] += counts["unknown"]
                record[
                    "retained_histories"
                    if descriptor["status"] == "retained"
                    else "unavailable_histories"
                ] += 1
            seen.add(config_id)
        if set(fit["candidates"]) != seen:
            raise ValueError("Saved method candidates have no recorded proposals")
        complete = len(fit["trials"]) == proposals_per_fit
        for key, record in records.items():
            record["untested"] -= len(proposed_ids[key])
            record["budget_deferred"] = record["untested"] if complete else 0
            record["status"] = (
                "evaluated"
                if record["evaluated"]
                else "proposed"
                if record["proposed"]
                else "budget_deferred"
                if complete
                else "pending"
            )
            record["data_status"] = (
                "unknown_entries_reported"
                if record["unknown_entry_checks"]
                else "no_unknown_entries_reported"
                if record["evaluated"]
                else "not_observed"
            )
            for metric in (
                "proposed",
                "evaluated",
                "reused",
                "retained_histories",
                "unavailable_histories",
                "unknown_entry_checks",
            ):
                aggregate[key][metric] += record[metric]
        total_proposed += len(fit["trials"])
        total_evaluated += sum(row["evaluated"] for row in records.values())
        total_reused += sum(row["reused"] for row in records.values())
        retained += sum(row["retained_histories"] for row in records.values())
        unavailable += sum(row["unavailable_histories"] for row in records.values())
        fits.append(
            {
                "id": fit_name,
                "status": "complete" if complete else "running",
                "proposals": len(fit["trials"]),
                "proposal_limit": proposals_per_fit,
                "families": list(records.values()),
            }
        )
    all_complete = all(row["status"] in ("complete", "not_run") for row in fits)
    for key, record in aggregate.items():
        observed = {
            row["params"]["hypothesis"]
            for fit in calculation["fits"].values()
            for row in fit["trials"]
            if by_hypothesis[row["params"]["hypothesis"]]["family"] == key
        }
        record["untested"] -= len(observed)
        record["budget_deferred"] = record["untested"] if all_complete else 0
        record["status"] = (
            "evaluated"
            if record["evaluated"]
            else "proposed"
            if record["proposed"]
            else "budget_deferred"
            if all_complete
            else "pending"
        )
        record["data_status"] = (
            "unknown_entries_reported"
            if record["unknown_entry_checks"]
            else "no_unknown_entries_reported"
            if record["evaluated"]
            else "not_observed"
        )
    result = {
        "version": VERSION,
        "status": "available",
        "registry": registry,
        "source": {
            "inputs_artifact": source["inputs_artifact"],
            "recipe_id": source["recipe_id"],
            "recipe_version": recipe_version,
            "stock_evidence_id": search["stock_evidence_id"],
            "mandate_id": mandate["id"],
            "checkpoint_id": calculation["checkpoint_id"],
            "manifest_id": development_manifest["id"],
            "search_identity": calculation["binding"],
        },
        "fits": fits,
        "families": list(aggregate.values()),
        "totals": {
            "registered_families": 32,
            "registered_hypotheses": len(hypotheses),
            "proposals": total_proposed,
            "evaluated": total_evaluated,
            "reused": total_reused,
            "retained_histories": retained,
            "unavailable_histories": unavailable,
            "native_evaluations": len(calculation["evaluations"]),
            "native_calculations": calculation["simulations"],
            "budget_deferred": sum(row["budget_deferred"] for row in aggregate.values()),
        },
        "limitations": [
            "Metadata consistency only; no account replay, shard read or verified price-coverage claim",
            "Evaluated counts are unique proposal-linked training accounts; native totals also include references, later checks and reconstruction",
            "Unknown-entry counts count repeated checks, not unique missing bars",
            "Native clock-grid membership is not re-certified from this compact metadata",
        ],
    }
    if recipe_version in (
        EXECUTION_RECIPE_VERSION,
        PRIORITY_RECIPE_VERSION,
        TURNOVER_RECIPE_VERSION,
        CAPACITY_RECIPE_VERSION,
        SENSITIVITY_RECIPE_VERSION,
        SEED_RECIPE_VERSION,
    ):
        result["limitations"].append(
            "Condition-family counts include execution and sizing variants; exit choices, sizing controls and hard limits are not additional indicator families"
        )
    if recipe_version in (
        PRIORITY_RECIPE_VERSION,
        TURNOVER_RECIPE_VERSION,
        CAPACITY_RECIPE_VERSION,
        SENSITIVITY_RECIPE_VERSION,
        SEED_RECIPE_VERSION,
    ):
        from research.execution_search import ENTRY_PRIORITIES, priority_counts

        result["entry_priority"] = {
            "choices": list(ENTRY_PRIORITIES),
            "fits": [
                {
                    "period": period,
                    "choices": priority_counts(
                        calculation["fits"].get(period, {}).get("trials", [])
                    ),
                }
                for period in fit_names(recipe_version)
            ],
            "count_basis": "Proposal-linked training accounts; reused proposals counted separately. Matched sizing controls and final accounts are excluded.",
        }
    if recipe_version == SEED_RECIPE_VERSION:
        result["limitations"].append(
            "Search-repeat fits can reference the same retained native account; per-fit evaluation links are not distinct calculations or independent samples. Skipped repeat fits are explicitly not run."
        )
    bounded(result, MAX_OUTPUT_BYTES)
    return {**result, "id": fingerprint(result)}
