"""Pure union of already verified checked-branch catalogues.

The caller owns artifact/conclusion, source, permission and missing-data checks.
This module never ranks by profits, calculates, contacts a model or grants budget.
"""

from copy import deepcopy

from research.report_contract import fingerprint, settings_identity
from services.research_experiments import VERSION as CATALOG_VERSION
from services.research_experiments import configuration_identity

VERSION = "research-checked-frontier-v1"
MAX_BRANCHES = 25  # Initial root plus at most24 admitted continuation proposals.
MAX_BRANCH_OPTIONS = 8
MAX_OPTIONS = MAX_BRANCHES * MAX_BRANCH_OPTIONS


def _hex(value, label):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"The checked frontier needs its exact {label} digest")
    return value


def build(branches, *, remaining_accounts, validation_mode="exploratory_only"):
    """Keep every supported affordable alternative and its immutable origin.

    Each input is {job_id,result_artifact,report,catalog,scorecard,engine?}.
    Branch ordering is caller-supplied deterministic checked-history order;
    equivalent alternatives retain all origins instead of choosing by outcomes.
    """
    from services.research_supervisor import REMEDIES, validation_reserve

    if not isinstance(branches, list) or not 1 <= len(branches) <= MAX_BRANCHES:
        raise ValueError(
            "Checked frontier exceeds its root-plus24-branch bound; review is incomplete"
        )
    if type(remaining_accounts) is not int or not 0 <= remaining_accounts <= 512:
        raise ValueError("Checked frontier cannot reset the original512-account allowance")
    if not isinstance(validation_mode, str) or not 1 <= len(validation_mode) <= 64:
        raise ValueError("Checked frontier needs its frozen validation mode")
    checked_settings = set()
    for branch in branches:
        report = branch.get("report") if isinstance(branch, dict) else None
        if (
            not isinstance(report, dict)
            or not isinstance(report.get("strategies"), list)
            or not 1 <= len(report["strategies"]) <= 8
        ):
            raise ValueError("Checked frontier source lacks complete bounded strategy settings")
        engine = branch.get("engine", report.get("portfolio", {}).get("engine"))
        checked_settings.add(configuration_identity(report["strategies"], engine=engine))
    options, summaries, unavailable, identities, seen_branches = [], [], [], {}, set()
    raw_count, missing_branches, duplicate_count, measured_count = 0, 0, 0, 0
    for branch in branches:
        if not isinstance(branch, dict):
            raise ValueError("Checked frontier branch is incomplete")
        job_id, artifact = branch.get("job_id"), branch.get("result_artifact")
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 64:
            raise ValueError("Checked frontier needs the owned origin job")
        _hex(artifact, "origin artifact")
        if job_id in seen_branches:
            raise ValueError("Checked frontier cannot count one source branch twice")
        seen_branches.add(job_id)
        report, catalog, card = branch.get("report"), branch.get("catalog"), branch.get("scorecard")
        if (
            not isinstance(report, dict)
            or not isinstance(catalog, dict)
            or not isinstance(card, dict)
        ):
            raise ValueError(
                "Checked frontier needs verified reports, catalogues and safe scorecards"
            )
        source_settings = report.get("strategies")
        source_id = settings_identity(source_settings)
        if not source_id:
            raise ValueError("Checked frontier source lacks complete strategy settings")
        engine = branch.get("engine", report.get("portfolio", {}).get("engine"))
        if engine is not None and (not isinstance(engine, str) or not 1 <= len(engine) <= 64):
            raise ValueError("Checked frontier engine identity changed")
        supplied = catalog.get("options")
        blocked = catalog.get("unavailable", [])
        if (
            not isinstance(supplied, list)
            or len(supplied) > MAX_BRANCH_OPTIONS
            or not isinstance(blocked, list)
        ):
            raise ValueError(
                "Checked frontier branch exceeds its eight-option bound; review is incomplete"
            )
        raw_count += len(supplied)
        if raw_count > MAX_OPTIONS:
            raise ValueError("Checked frontier exceeds200 options; review is incomplete")
        evidence = catalog.get("feedback", {}).get("evidence_status", {})
        missing = evidence.get("missing_required") is True
        missing_branches += missing
        summary = {
            "job_id": job_id,
            "result_artifact": artifact,
            "settings_id": source_id,
            "scorecard": deepcopy(card),
            "supplied_options": len(supplied),
            "available_options": 0,
            "duplicate_options": 0,
            "unaffordable_options": 0,
            "already_tested_options": 0,
            "stop_reason": catalog.get("stop_reason"),
            "unavailable": deepcopy(blocked),
            "missing_required_evidence": missing,
        }
        summaries.append(summary)
        unavailable.extend(
            {"origin_job_id": job_id, "origin_result_artifact": artifact, "detail": deepcopy(item)}
            for item in blocked
        )
        if missing:
            unavailable.append(
                {
                    "origin_job_id": job_id,
                    "origin_result_artifact": artifact,
                    "reason_code": "missing_required_evidence",
                    "reason": catalog.get("stop_reason")
                    or "This branch lacks required execution or sizing evidence",
                }
            )
            continue
        for candidate in supplied:
            if not isinstance(candidate, dict) or candidate.get("remedy") not in REMEDIES:
                raise ValueError("Checked frontier cannot introduce an unsupported method")
            remedy, settings = candidate["remedy"], candidate.get("settings")
            exact = settings_identity(settings)
            if not exact:
                raise ValueError("Checked frontier option lacks complete settings")
            expected_id = fingerprint(
                {
                    "version": CATALOG_VERSION,
                    "source_artifact": artifact,
                    "remedy": remedy,
                    "settings_id": exact,
                }
            )
            if candidate.get("id") != expected_id:
                raise ValueError(
                    "Checked frontier option no longer matches its exact origin catalogue"
                )
            effective = configuration_identity(settings, engine=engine)
            reserve = validation_reserve(remedy, validation_mode)
            if remedy != "funding_and_size" and effective in checked_settings:
                measured_count += 1
                summary["already_tested_options"] += 1
                unavailable.append(
                    {
                        "origin_job_id": job_id,
                        "origin_result_artifact": artifact,
                        "candidate_id": candidate["id"],
                        "effective_configuration_id": effective,
                        "reason_code": "already_tested_configuration",
                        "reason": "This complete effective configuration already has a checked native result",
                    }
                )
                continue
            origin = {
                "job_id": job_id,
                "result_artifact": artifact,
                "candidate_id": candidate["id"],
                "remedy": remedy,
                "required_accounts": reserve,
            }
            # Funding executes a cause-routed native account programme, whereas
            # performance remedies run the same complete-setting validation.
            identity = ("funding" if remedy == "funding_and_size" else "performance", effective)
            if identity in identities:
                duplicate = identities[identity]
                duplicate["origin_refs"].append(origin)
                summary["duplicate_options"] += 1
                duplicate_count += 1
                continue
            option = {
                **deepcopy(candidate),
                "origin_job_id": job_id,
                "origin_result_artifact": artifact,
                "required_accounts": reserve,
                "effective_configuration_id": effective,
                "origin_refs": [origin],
            }
            identities[identity] = option
            if reserve > remaining_accounts:
                summary["unaffordable_options"] += 1
                unavailable.append(
                    {
                        "origin_job_id": job_id,
                        "origin_result_artifact": artifact,
                        "candidate_id": candidate["id"],
                        "reason_code": "full_validation_budget",
                        "required_accounts": reserve,
                        "remaining_accounts": remaining_accounts,
                        "reason": "The complete native validation reserve is unavailable; validation was not reduced",
                    }
                )
                continue
            options.append(option)
            summary["available_options"] += 1
    stop = None
    if not options:
        if identities:
            minimum = min(row["required_accounts"] for row in identities.values())
            stop = (
                f"The remaining {remaining_accounts} of512 native accounts cannot fund a complete "
                f"{minimum}-account experiment from any checked branch; validation was not reduced."
            )
        elif missing_branches:
            stop = (
                f"No executable experiment remains across the checked branches; {missing_branches} branch(es) "
                "lack required execution or sizing evidence. Other branch restrictions are listed."
            )
        else:
            stop = (
                "No untried supported experiment was supplied by any checked branch; "
                "this bounded catalogue is exhausted, not every conceivable setting."
            )
    body = {
        "version": VERSION,
        "options": options,
        "branches": summaries,
        "unavailable": unavailable,
        "feedback": {
            "checked_branches": len(branches),
            "supplied_options": raw_count,
            "distinct_configurations": len(identities),
            "affordable_options": len(options),
            "equivalent_options": duplicate_count,
            "already_tested_options": measured_count,
            "checked_configurations": len(checked_settings),
            "remaining_accounts": remaining_accounts,
            "native_account_limit": 512,
            "coverage": "All supplied checked branches and their complete supported catalogues; no profit or heldout ranking",
            "complete": True,
        },
        "stop_reason": stop,
    }
    return {**body, "id": fingerprint(body)}
