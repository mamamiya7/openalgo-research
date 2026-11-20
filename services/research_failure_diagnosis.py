"""Origin-bound hypotheses about failed native checks; no calculation or I/O."""

from research.report_contract import fingerprint

VERSION = "research-failure-diagnosis-v1"
RAW_FIELDS = ("target_ref", "cause", "prediction", "falsifier")
_PERFORMANCE_CHECKS = frozenset(
    {
        "higher_costs",
        "neighboring_settings",
        "sample_and_distribution",
        "prefix_0",
        "prefix_1",
        "prefix_2",
    }
)


class InvalidFailureDiagnosis(ValueError):
    """A prose proposal without a testable, bound hypothesis cannot execute."""


def _causes(option):
    axis = option.get("change", {}).get("axis")
    if option.get("remedy") == "funding_and_size":
        return ["affordability_and_sizing"]
    if axis == "allocation_mode" or option.get("remedy") == "reduce_concentration":
        return ["concentration"]
    if axis in ("risk_budget_fraction", "order_size_pct"):
        return ["exposure_sensitivity"]
    if axis in ("hold_sessions", "hold_minutes"):
        return ["occupancy", "temporal_instability"]
    if axis in ("trailing_choice", "target_pct", "stop_pct", "execution_choice"):
        return ["exit_churn", "exit_sensitivity"]
    return []


def context(evidence):
    """Reference existing complete branch facts, never duplicate or invent them."""
    frontier = evidence.get("experiment_frontier", {})
    supplied = evidence.get("experiment_options", [])
    if frontier.get("version") != "research-checked-frontier-v1" or not supplied:
        return None
    branches = frontier.get("branches")
    if (
        not isinstance(supplied, list)
        or not 1 <= len(supplied) <= 200
        or not isinstance(branches, list)
        or not 1 <= len(branches) <= 25
        or frontier.get("feedback", {}).get("complete") is not True
        or frontier["feedback"].get("checked_branches") != len(branches)
        or not isinstance(frontier.get("id"), str)
        or not frontier["id"]
    ):
        raise InvalidFailureDiagnosis(
            "A failure diagnosis requires the complete sealed checked frontier"
        )
    origins = {}
    for number, branch in enumerate(branches, 1):
        job_id = branch.get("job_id")
        if not isinstance(job_id, str) or not job_id or job_id in origins:
            raise InvalidFailureDiagnosis("Failure diagnosis branch identities changed")
        origins[job_id] = (number, branch)
    targets, options = {}, {}
    for option in supplied:
        candidate, origin = option.get("id"), option.get("origin_job_id")
        if (
            not isinstance(candidate, str)
            or not candidate
            or candidate in options
            or origin not in origins
        ):
            raise InvalidFailureDiagnosis("An experiment lacks its exact checked origin")
        number, branch = origins[origin]
        if option.get("origin_result_artifact") != branch.get("result_artifact"):
            raise InvalidFailureDiagnosis("The candidate's saved source artifact changed")
        card = branch.get("scorecard", {})
        funding = option.get("remedy") == "funding_and_size"
        allowed = []
        for check_number, check in enumerate(card.get("checks", []), 1):
            key = check.get("key")
            if check.get("passed") is not False:
                continue
            if (funding and key != "operating_capacity") or (
                not funding and key not in _PERFORMANCE_CHECKS
            ):
                continue
            ref = f"b{number}/{key}"
            targets[ref] = {"branch": number, "check": check_number}
            allowed.append(ref)
        feasibility = card.get("feasibility")
        if (
            funding
            and isinstance(feasibility, dict)
            and feasibility.get("version") == "research-feasibility-v1"
            and feasibility.get("scope") == "feasibility_only"
            and feasibility.get("unresolved_constraints")
        ):
            ref = f"b{number}/feasibility"
            targets[ref] = {"branch": number, "field": "scorecard.feasibility"}
            allowed.append(ref)
        options[candidate] = {
            "origin_job_id": origin,
            "allowed_targets": allowed,
            "allowed_causes": _causes(option),
        }
    body = {
        "version": VERSION,
        "frontier_id": frontier["id"],
        "targets": targets,
        "options": options,
    }
    return {**body, "key": fingerprint(body)}


def validate(value, evidence, candidate_id):
    actual = context(evidence)
    if actual is None:
        raise InvalidFailureDiagnosis("A diagnosed experiment requires its checked source evidence")
    supplied = evidence.get("failure_diagnosis_context")
    if supplied is not None and (
        not isinstance(supplied, dict)
        or supplied.get("version") != VERSION
        or supplied.get("key") != actual["key"]
    ):
        raise InvalidFailureDiagnosis("The failure diagnosis context is stale")
    if not isinstance(value, dict) or set(value) != set(RAW_FIELDS):
        raise InvalidFailureDiagnosis(
            "Choose the failed check, hypothesized cause, prediction and falsifier"
        )
    option = actual["options"].get(candidate_id)
    if option is None:
        raise InvalidFailureDiagnosis("The diagnosed experiment is no longer available")
    if value["target_ref"] not in option["allowed_targets"]:
        raise InvalidFailureDiagnosis(
            "The target must be a failed check of this complete origin configuration"
        )
    if value["cause"] not in option["allowed_causes"]:
        raise InvalidFailureDiagnosis(
            "The diagnosed cause is outside this registered experiment's controls"
        )
    result = {key: value[key] for key in RAW_FIELDS}
    for key in ("prediction", "falsifier"):
        text = value[key]
        if not isinstance(text, str) or not 20 <= len(text.strip()) <= 240:
            raise InvalidFailureDiagnosis(
                "A diagnosis requires a specific bounded prediction and disconfirmation"
            )
        result[key] = text.strip()
    if result["prediction"].casefold() == result["falsifier"].casefold():
        raise InvalidFailureDiagnosis("The prediction and its disconfirmation must be distinct")
    return {
        **result,
        "version": VERSION,
        "context_key": actual["key"],
        "candidate_id": candidate_id,
        "origin_job_id": option["origin_job_id"],
    }


def raw(value):
    """Remove server attestation fields before validating a durable decision again."""
    return (
        {key: value[key] for key in RAW_FIELDS if key in value}
        if isinstance(value, dict)
        else value
    )
