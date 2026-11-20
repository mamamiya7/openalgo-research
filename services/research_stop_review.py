"""Bounded option-by-option stop evidence; no calculations, grants or I/O."""

from copy import deepcopy

from research.report_contract import fingerprint

VERSION = "research-option-stop-review-v1"
MAX_OPTIONS = 200
BATCH_LIMIT = 6  # Fit the unchanged600-token provider response allowance.


class IncompleteStopReview(ValueError):
    """An unaudited model stop is attention, never catalogue exhaustion."""


def context(evidence, saved=None):
    frontier = evidence.get("experiment_frontier", {})
    options = evidence.get("experiment_options", [])
    if frontier.get("version") != "research-checked-frontier-v1" or not options:
        return None
    branches = frontier.get("branches")
    if (
        not isinstance(options, list)
        or not 1 <= len(options) <= MAX_OPTIONS
        or not isinstance(branches, list)
        or not 1 <= len(branches) <= 25
        or frontier.get("feedback", {}).get("complete") is not True
        or frontier["feedback"].get("checked_branches") != len(branches)
    ):
        raise IncompleteStopReview("The remaining options lack a complete checked-branch review")
    ids = [option["id"] for option in options]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise IncompleteStopReview("Remaining option identities are incomplete or duplicated")
    key = fingerprint({"version": VERSION, "frontier_id": frontier.get("id"), "option_ids": ids})
    refs = {}
    for number, branch in enumerate(branches, 1):
        card = branch.get("scorecard", {})
        for check_number, check in enumerate(card.get("checks", []), 1):
            if check.get("passed") is False and isinstance(check.get("key"), str):
                refs[f"b{number}/{check['key']}"] = {
                    "branch": number,
                    "check": check_number,
                }
        feasibility = card.get("feasibility")
        if feasibility:
            refs[f"b{number}/feasibility"] = {
                "branch": number,
                "field": "scorecard.feasibility",
            }
    # A funding hypothesis also carries its exact verified native capacity counts.
    for number, option in enumerate(options, 1):
        change = option.get("change", {})
        if option.get("remedy") == "funding_and_size" and change.get("diagnosis"):
            refs[f"o{number}/feasibility"] = {
                "option": number,
                "field": "change.diagnosis",
            }
    result = {
        "version": VERSION,
        "key": key,
        "frontier_id": frontier.get("id"),
        "option_ids": ids,
        "option_count": len(ids),
        "batch_limit": BATCH_LIMIT,
        "evidence_refs": refs,
        "recorded_reviews": [],
        "unreviewed_options": list(range(1, len(ids) + 1)),
    }
    if saved and saved.get("key") == key:
        _binding(saved, result)
        rows = _rows(saved.get("reviews"), result, limit=MAX_OPTIONS)
        result["recorded_reviews"] = rows
        result["unreviewed_options"] = sorted(
            set(result["unreviewed_options"]) - {r["option"] for r in rows}
        )
    return result


def _binding(saved, review):
    if any(
        saved.get(key) != review[key]
        for key in ("version", "key", "frontier_id", "option_ids", "option_count")
    ):
        raise IncompleteStopReview("The saved stop review no longer binds every exact option")


def _rows(values, review, *, limit):
    if not isinstance(values, list) or not 1 <= len(values) <= limit:
        raise IncompleteStopReview(
            "A model stop must justify each option in bounded review batches"
        )
    rows, seen, reasons = [], set(), set()
    for row in values:
        if not isinstance(row, dict) or set(row) != {"option", "evidence_ref", "reason"}:
            raise IncompleteStopReview(
                "Every skipped option needs its own evidence reference and reason"
            )
        number, ref, reason = row["option"], row["evidence_ref"], row["reason"]
        if type(number) is not int or not 1 <= number <= review["option_count"] or number in seen:
            raise IncompleteStopReview(
                "A skipped option is duplicated or outside the exact remaining catalogue"
            )
        if not isinstance(ref, str) or ref not in review["evidence_refs"]:
            raise IncompleteStopReview("A skipped option cites unavailable or passing evidence")
        if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 240:
            raise IncompleteStopReview("Each skipped option needs a specific bounded explanation")
        normalized = " ".join(reason.casefold().split())
        if normalized in reasons:
            raise IncompleteStopReview(
                "Copying one generic explanation across options is not a complete review"
            )
        seen.add(number)
        reasons.add(normalized)
        rows.append({"option": number, "evidence_ref": ref, "reason": reason.strip()})
    return rows


def merge(evidence, values):
    review = context(evidence)
    if review is None:
        raise IncompleteStopReview("A stop review requires its exact checked frontier")
    supplied = evidence.get("stop_review_context")
    if supplied:
        _binding(supplied, review)
        prior = supplied.get("recorded_reviews", [])
        review["recorded_reviews"] = _rows(prior, review, limit=MAX_OPTIONS) if prior else []
        review["unreviewed_options"] = sorted(
            set(review["unreviewed_options"]) - {row["option"] for row in prior}
        )
    fresh = _rows(values, review, limit=BATCH_LIMIT)
    if any(row["option"] not in review["unreviewed_options"] for row in fresh):
        raise IncompleteStopReview("A stop review repeats an already reviewed option")
    rows = _rows([*review["recorded_reviews"], *fresh], review, limit=MAX_OPTIONS)
    remaining = sorted(set(range(1, review["option_count"] + 1)) - {row["option"] for row in rows})
    return {
        "version": VERSION,
        "key": review["key"],
        "frontier_id": review["frontier_id"],
        "option_ids": review["option_ids"],
        "option_count": review["option_count"],
        "reviews": sorted(rows, key=lambda row: row["option"]),
        "complete": not remaining,
        "unreviewed_options": remaining,
    }


def validate_complete(evidence, saved):
    review = context(evidence)
    if review is None:
        return None
    if not isinstance(saved, dict) or saved.get("key") != review["key"]:
        raise IncompleteStopReview(
            "The stop review belongs to a stale or unavailable option catalogue"
        )
    _binding(saved, review)
    rows = _rows(saved.get("reviews"), review, limit=MAX_OPTIONS)
    if {row["option"] for row in rows} != set(range(1, review["option_count"] + 1)):
        raise IncompleteStopReview("The model has not justified skipping every remaining option")
    if saved.get("complete") is not True or saved.get("unreviewed_options") != []:
        raise IncompleteStopReview("The saved model stop review is incomplete")
    return deepcopy(saved)
