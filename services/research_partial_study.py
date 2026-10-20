"""Read committed search evidence without resuming, ranking or calculating a study.

Operational observations are deliberately not a source of results. Only the
atomically published, content-addressed scientific checkpoint is inspected.
"""

import copy
import json
import math
import re

from database.research_db import ResearchExperiment, ResearchJob
from research.connectors import optuna_portfolio as search
from services import scanner_research_service as service

VERSION = "research-partial-study-v1"
STOPPED = {"paused", "interrupted", "cancelled", "failed"}


class CheckpointChanged(ValueError):
    pass


def _integer(value, default, minimum, maximum):
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r"0|[1-9][0-9]{0,5}", str(value)):
        raise ValueError("Invalid saved-trial page")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError("Invalid saved-trial page")
    return parsed


def _scalar_metrics(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid saved trial metrics")
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str)
        and (item is None or (type(item) in (int, float) and math.isfinite(item)))
    }


def _search_inputs(evidence, calculation):
    portfolio = evidence["portfolio"]
    automatic = portfolio.get("automatic_research")
    if automatic:
        from research.automatic_protocol import compile_recipe, slice_period
        from research.portfolio import fingerprint

        recipe = compile_recipe(evidence)
        if evidence.get("automatic_recipe") != recipe:
            raise ValueError("Saved automatic recipe changed")
        version = recipe["version"]
        if version in (
            "automatic-hypotheses-v2",
            "automatic-hypotheses-v3",
            "automatic-hypotheses-v4",
            "automatic-hypotheses-v5",
            "automatic-hypotheses-v6",
            "automatic-hypotheses-v7",
            "automatic-hypotheses-v8",
            "automatic-hypotheses-v9",
        ):
            from research.automatic_hypotheses import checkpoint_rows

            if calculation.get("binding") != fingerprint(
                {
                    "recipe": recipe,
                    "stock": evidence[
                        "automatic_stock_market_context"
                        if version
                        in (
                            "automatic-hypotheses-v5",
                            "automatic-hypotheses-v6",
                            "automatic-hypotheses-v7",
                            "automatic-hypotheses-v8",
                            "automatic-hypotheses-v9",
                        )
                        else "automatic_stock_features"
                    ],
                }
            ):
                raise ValueError("Saved stock research inputs changed")
            checkpoint_rows(calculation)
            period = (
                calculation["active_fit"]
                if version in ("automatic-hypotheses-v8", "automatic-hypotheses-v9")
                else "search"
            )
            return slice_period(evidence, period), calculation, period, True, False
        if version == "automatic-entry-conditions-v1":
            binding = fingerprint({"recipe": recipe, "market": evidence["entry_condition_market"]})
            maximum = 100
        elif version in ("automatic-trade-management-v1", "automatic-trade-management-v2"):
            binding = fingerprint(
                {key: evidence[key] for key in ("portfolio", "strategies", "snapshot", "versions")}
                | {"recipe": recipe}
            )
            maximum = 118 if version == "automatic-trade-management-v2" else 70
        else:
            raise ValueError("Unsupported saved automatic recipe")
        if (
            calculation.get("version") != version
            or calculation.get("binding") != binding
            or type(calculation.get("simulations")) is not int
            or not 0 <= calculation["simulations"] <= maximum
            or calculation.get("checkpoint_id")
            != search._fingerprint(
                {key: value for key, value in calculation.items() if key != "checkpoint_id"},
                max_bytes=search.MAX_CHECKPOINT_BYTES,
            )
        ):
            raise ValueError("Saved automatic checkpoint changed")
        pending = calculation.get("search_checkpoint")
        finished = calculation.get("search_result")
        if pending is not None and finished is not None:
            raise ValueError("Ambiguous saved automatic search")
        if pending is not None:
            saved = pending
            completed = False
        elif finished is not None:
            saved = finished["experiment"]
            if (
                saved.get("kind") != "portfolio_optimize"
                or saved.get("experiment_version") != search.ADAPTER_VERSION
            ):
                raise ValueError("Unsupported saved search result")
            completed = True
        else:
            return None, None, "search", True, False
        return slice_period(evidence, "search"), saved, "search", True, completed
    selected = evidence
    scope = "full"
    if portfolio.get("validation"):
        from research.portfolio_validation import partition

        selected, _, _ = partition(evidence, prepare_period="selection")
        scope = "selection"
    if portfolio.get("entry_conditions"):
        from research.entry_conditions import STOCK_VERSIONS, apply

        selected = apply(
            selected,
            evidence["automatic_stock_market_context"]
            if portfolio["entry_conditions"]["version"]
            in ("research-joint-entry-conditions-v1", "research-joint-entry-conditions-v2")
            else evidence["automatic_stock_features"]
            if portfolio["entry_conditions"]["version"] in STOCK_VERSIONS
            else evidence["entry_condition_market"],
        )
    return selected, calculation, scope, False, False


def _validated_rows(evidence, saved, completed):
    """Check input binding and row/proposal correspondence without executing Optuna."""
    if not isinstance(saved, dict):
        raise ValueError("Invalid saved search")
    if saved.get("version") in (
        "automatic-hypotheses-v2",
        "automatic-hypotheses-v3",
        "automatic-hypotheses-v4",
        "automatic-hypotheses-v5",
        "automatic-hypotheses-v6",
        "automatic-hypotheses-v7",
        "automatic-hypotheses-v8",
        "automatic-hypotheses-v9",
    ):
        from research.automatic_hypotheses import checkpoint_rows

        rows = checkpoint_rows(saved)
        trials = (
            saved["fits"].get(saved["active_fit"], {"trials": []})["trials"]
            if saved["version"] in ("automatic-hypotheses-v8", "automatic-hypotheses-v9")
            else saved["trials"]
        )
        return rows, {
            "proposed": len(trials),
            "evaluated": sum(not row["reused"] for row in trials),
            "reused": sum(row["reused"] for row in trials),
            "rejected": 0,
        }
    base, axes = search._prepare(evidence["strategies"])
    capital = search._number(evidence["portfolio"]["capital"], 1, 1e9, "Portfolio capital")
    for row in base:
        row["config"]["initial_capital"] = float(capital)
    specification = evidence["portfolio"]["optimization"]
    plan = search.describe_search(base, specification)
    spec = plan["specification"]
    optimizer = {
        "id": "optuna",
        "version": search.TESTED_OPTUNA_VERSION,
        "adapter_version": search.ADAPTER_VERSION,
        "sampler": "TPESampler" if spec["sampler"] == "tpe" else "GridSampler",
        "seed": spec["seed"],
        "startup_trials": 10 if spec["sampler"] == "tpe" else 0,
        "resume_policy": "serial-seeded-trial-replay-v1",
        "objective_definition": search.OBJECTIVES[spec["objective"]],
        "first_trial": "feasible lower corner" if spec["sampler"] == "tpe" else "seeded grid order",
    }
    scientific = {
        "strategies": base,
        "snapshot": evidence["snapshot"],
        "capital": capital,
        "execution": evidence["versions"],
        "optimizer": optimizer,
    }
    binding = search._fingerprint({**scientific, "specification": spec})
    if saved.get("optimizer") != optimizer or saved.get("specification", spec) != spec:
        raise ValueError("Saved search settings changed")
    if not completed:
        info = search.validate_checkpoint(saved)
        if saved["binding"] != binding or (
            info["specification"] is not None
            and (
                info["specification"] != spec
                or info["proposal_budget"] != plan["proposal_budget"]
                or info["search_identity"]
                != search._fingerprint(
                    {
                        **scientific,
                        "identity_version": search.SEARCH_IDENTITY_VERSION,
                        "specification": {k: v for k, v in spec.items() if k != "trials"},
                    }
                )
            )
        ):
            raise ValueError("Saved search inputs changed")
    elif saved.get("search_space") != plan:
        raise ValueError("Saved completed search settings changed")
    trials, rows = saved.get("trials"), saved.get("rows")
    if (
        not isinstance(trials, list)
        or not 1 <= len(trials) <= plan["proposal_budget"]
        or not isinstance(rows, list)
        or len(rows) > len(trials)
        or any(not isinstance(row, dict) for row in rows + trials)
    ):
        raise ValueError("Invalid saved search bounds")
    if completed and len(trials) != plan["proposal_budget"]:
        raise ValueError("Incomplete saved search result")
    by_id = {row["config_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Duplicate saved trial settings")
    seen = set()
    for number, trial in enumerate(trials):
        params = trial.get("params")
        if (
            type(trial.get("number")) is not int
            or trial["number"] != number
            or not isinstance(params, dict)
            or set(params) != set(axes)
        ):
            raise ValueError("Invalid saved trial proposal")
        candidate = search._candidate(base, params)
        settings = search._settings(candidate)
        identity = search._fingerprint(settings)
        feasible = search._feasible(candidate)
        if (
            trial.get("config_id") != identity
            or type(trial.get("reused")) is not bool
            or trial["reused"] != (identity in seen)
            or trial.get("state") != ("complete" if feasible else "pruned")
        ):
            raise ValueError("Saved trial settings changed")
        if not feasible:
            if trial.get("value") is not None or identity in by_id:
                raise ValueError("Rejected allocation contains a saved result")
            continue
        row = by_id[identity]
        score = search._checked_score(row.get("summary"), spec["objective"])
        if (
            row.get("strategies") != settings
            or type(row.get("score")) not in (int, float)
            or row["score"] != score
            or type(trial.get("value")) not in (int, float)
            or trial["value"] != score
            or type(row.get("trial_number")) is not int
            or row.get("stage") != spec["sampler"]
            or (identity not in seen and row["trial_number"] != number)
        ):
            raise ValueError("Saved trial result changed")
        seen.add(identity)
    if seen != set(by_id):
        raise ValueError("Uncommitted saved trial result")
    ordered = sorted(rows, key=lambda row: row["trial_number"])
    return ordered, {
        "proposed": len(trials),
        "evaluated": len(rows),
        "reused": sum(trial["reused"] for trial in trials),
        "rejected": sum(trial["state"] == "pruned" for trial in trials),
    }


def read(store, owner, job_id, *, offset=None, limit=None, expected_checkpoint=None):
    offset = _integer(offset, 0, 0, search.MAX_TRIALS)
    limit = _integer(limit, 25, 1, 100)
    if expected_checkpoint is not None and (
        not isinstance(expected_checkpoint, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_checkpoint)
    ):
        raise ValueError("Invalid saved checkpoint identity")
    if offset and expected_checkpoint is None:
        raise ValueError("Pin the saved checkpoint before loading another page")
    job = service.get_job(store, owner, job_id)
    with store.sessions() as db:
        native = db.get(ResearchExperiment, job_id)
    digest = native.checkpoint if native else None
    if expected_checkpoint is not None and expected_checkpoint != digest:
        raise CheckpointChanged(
            "Saved trials changed. Reopen the saved trials to inspect the current checkpoint."
        )
    receipt = {
        "version": VERSION,
        "job_id": job_id,
        "status": job.status,
        "available": False,
        "reason": None,
        "checkpoint_artifact": digest,
        "checkpoint_id": None,
        "scope": None,
        "automatic": False,
        "counts": {"proposed": 0, "evaluated": 0, "reused": 0, "rejected": 0},
        "total_rows": 0,
        "offset": offset,
        "limit": limit,
        "next_offset": None,
        "rows": [],
        "analysis_catalog": [],
    }
    if job.status not in STOPPED or not native or native.kind != "portfolio_optimize":
        receipt["reason"] = (
            "Saved trial inspection is available for paused or stopped optimization studies."
        )
        return receipt
    if not digest:
        receipt["reason"] = "No completed trials were saved before this study stopped."
        return receipt
    try:
        envelope = service.read_artifact(store, digest)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("state"), dict):
            raise ValueError("Invalid saved study checkpoint")
        state = envelope["state"]
        if envelope.get("identity") != native.identity:
            raise ValueError("Saved study identity changed")
        if state.get("phase") != "calculation" or state.get("calculation") is None:
            receipt["reason"] = "No completed trials were saved before this study stopped."
            return receipt
        if not isinstance(state["calculation"], dict):
            raise ValueError("Invalid saved calculation checkpoint")
        evidence = service.read_artifact(store, state["inputs_artifact"])
        specification = json.loads(native.specification)
        from research.connectors.registry import policy_for_request

        if (
            evidence["portfolio"] != specification["portfolio"]
            or evidence["versions"] != specification["versions"]
            or envelope.get("policy_version")
            != policy_for_request(evidence["snapshot"], specification)
        ):
            raise ValueError("Saved study inputs changed")
        selected, saved, scope, automatic, completed = _search_inputs(
            evidence, state["calculation"]
        )
        receipt.update(scope=scope, automatic=automatic)
        if saved is None:
            receipt["reason"] = "No completed search trials were saved before this study stopped."
            return receipt
        rows, counts = _validated_rows(selected, saved, completed)
        page = []
        for row in rows[offset : offset + limit]:
            item = {
                key: copy.deepcopy(row[key])
                for key in ("config_id", "trial_number", "strategies", "score")
            }
            item["summary"] = _scalar_metrics(row["summary"])
            if isinstance(row.get("analysis"), dict):
                item["analysis"] = {
                    "version": row["analysis"].get("version"),
                    "metrics": _scalar_metrics(row["analysis"]["metrics"]),
                    "unavailable": copy.deepcopy(row["analysis"].get("unavailable", {})),
                }
            page.append(item)
        receipt.update(
            available=True,
            checkpoint_id=state["calculation"].get("checkpoint_id"),
            counts=counts,
            total_rows=len(rows),
            rows=page,
            analysis_catalog=copy.deepcopy(saved.get("analysis_catalog", [])),
            next_offset=offset + limit if offset + limit < len(rows) else None,
        )
    except (ValueError, KeyError, TypeError, OSError, OverflowError, ArithmeticError):
        receipt["reason"] = (
            "The saved trials could not be verified. Resume from valid saved progress or start a new study."
        )
        return receipt
    # A concurrent resume may publish a new checkpoint while this read verifies
    # old evidence. Never combine pages from the old and new snapshots.
    with store.sessions() as db:
        current = db.get(ResearchExperiment, job_id)
        current_job = db.get(ResearchJob, job_id)
        if not current_job or current_job.owner != owner:
            raise LookupError("Research run not found")
        if not current or current.checkpoint != digest or current_job.status != job.status:
            raise CheckpointChanged(
                "Saved trials changed. Reopen the saved trials to inspect the current checkpoint."
            )
    return receipt
