"""Bounded operational failures, independent of numerical result evidence."""

from copy import deepcopy
from datetime import datetime

VERSION = "research-failure-v1"
RECOVERY_VERSION = "research-recovery-v1"
ACTIONS = {"resume", "new_run", "edit_setup", "reconnect_broker"}


class ResearchFailure(ValueError):
    def __init__(
        self,
        code,
        message,
        *,
        stage="prepare_prices",
        retryable=False,
        actions=("new_run", "edit_setup"),
        details=None,
    ):
        super().__init__(message)
        self.receipt = {
            "version": VERSION,
            "code": code,
            "stage": stage,
            "message": message,
            "retryable": retryable,
            "actions": list(actions),
        }
        if details is not None:
            self.receipt["details"] = deepcopy(details)


def canonical_hours(value):
    """Compare clock representations without changing recorded calendar bytes."""
    if not isinstance(value, dict) or not {"open", "close"}.issubset(value):
        return None
    result = {}
    for key in ("open", "close"):
        text = value[key]
        if not isinstance(text, str):
            return None
        try:
            parsed = datetime.strptime(text, "%H:%M" if len(text) == 5 else "%H:%M:%S")
        except ValueError:
            return None
        if parsed.second or text not in (parsed.strftime("%H:%M"), parsed.strftime("%H:%M:%S")):
            return None
        result[key] = parsed.strftime("%H:%M")
    if result["open"] >= result["close"]:
        return None
    return result


def check_execution_hours(expected, actual, sessions):
    mismatches = []
    for day in sessions:
        before, after = canonical_hours(expected.get(day)), canonical_hours(actual.get(day))
        if before is None or after is None or before != after:
            mismatches.append((day, before, after))
    if not mismatches:
        return
    day, before, after = mismatches[0]

    def display(hours):
        return f"{hours['open']}–{hours['close']} IST" if hours else "not recorded"

    raise ResearchFailure(
        "execution_session_hours_mismatch",
        f"Saved stock-market context has different execution session hours on {day}: "
        f"context {display(before)}; execution {display(after)}. "
        "Start a new run after reviewing the session hours; this saved run is unchanged.",
        details={
            "first_affected_session": day,
            "expected_hours": before,
            "actual_hours": after,
            "mismatch_count": len(mismatches),
        },
    )


def failure_receipt(error, *, stage="calculation"):
    if isinstance(error, ResearchFailure):
        return deepcopy(error.receipt)
    message = (
        str(error)
        if isinstance(error, ValueError)
        else "Calculation failed; see the local error log."
    )
    lower = message.lower()
    if "reconnect your broker" in lower:
        code, retryable, actions = "broker_session_expired", True, ["reconnect_broker", "resume"]
    elif "could not be downloaded" in lower:
        code, retryable, actions = "price_download_failed", True, ["resume"]
    elif any(
        text in lower
        for text in (
            "different inputs",
            "different execution inputs",
            "changed",
            "integrity",
            "does not match",
            "do not match",
            "older nse calendar",
        )
    ):
        code, retryable, actions = "saved_evidence_incompatible", False, ["new_run", "edit_setup"]
    else:
        # Compatibility is checked separately. Preserve retries for transient
        # worker/runtime failures; known immutable conflicts above never resume.
        code, retryable, actions = "calculation_failed", True, ["resume", "new_run"]
    return {
        "version": VERSION,
        "code": code,
        "stage": stage,
        "message": message,
        "retryable": retryable,
        "actions": actions,
    }
