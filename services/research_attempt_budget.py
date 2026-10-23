"""Durable per-calculation admission, independent of grouped evidence saves."""

import json
import re
from copy import deepcopy
from threading import get_ident

from sqlalchemy import update

from database.research_db import ResearchExperiment, ResearchJob, ResearchWorker

KEY = "_attempt_budget"
VERSION = "research-attempt-budget-v1"
MAX_ATTEMPTS = 512
MAX_LOST_ATTEMPTS = 64
MAX_COUNTS_BYTES = 4 * 1024 * 1024
FIELDS = {
    "version",
    "identity",
    "policy",
    "limit",
    "attempts",
    "checkpoint_attempts",
    "checkpoint_simulations",
}


class AttemptLeaseLost(RuntimeError):
    """The caller no longer owns a running calculation admission."""


def _integer(value, maximum=MAX_ATTEMPTS):
    return type(value) is int and 0 <= value <= maximum


def _counts(value):
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_COUNTS_BYTES:
            raise ValueError("Research progress exceeds its bounded metadata limit")
        value = json.loads(value)
    if type(value) is not dict:
        raise ValueError("Research progress metadata must be an object")
    return value


def _counter(value):
    if (
        type(value) is not dict
        or set(value) != FIELDS
        or value.get("version") != VERSION
        or not isinstance(value.get("identity"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["identity"])
        or not isinstance(value.get("policy"), str)
        or not 1 <= len(value["policy"]) <= 256
        or not _integer(value.get("limit"))
        or value["limit"] == 0
        or not _integer(value.get("attempts"), value["limit"])
        or not _integer(value.get("checkpoint_attempts"), value["attempts"])
        or not _integer(value.get("checkpoint_simulations"), value["checkpoint_attempts"])
    ):
        raise ValueError("Invalid durable research attempt budget")
    return value


def merge_counts(existing, replacement, *, checkpoint_state=None):
    """Keep the reservation journal when replacing display/checkpoint counts.

    Call inside the existing fenced metadata transaction, after rereading the
    receipt. A checkpoint can advance its watermark only to attempts already
    durably reserved. Other writers cannot replace or clear the journal.
    """
    previous, result = _counts(existing), deepcopy(_counts(replacement))
    if KEY not in previous:
        if KEY in result:
            raise ValueError("Only calculation admission can create an attempt budget")
        return result
    counter = deepcopy(_counter(previous[KEY]))
    if KEY in result and result[KEY] != counter:
        raise ValueError("Research progress cannot replace its durable attempt budget")
    if checkpoint_state is not None:
        attempts, simulations = (
            checkpoint_state.get("attempts"),
            checkpoint_state.get("simulations"),
        )
        if (
            not _integer(attempts, counter["attempts"])
            or not _integer(simulations, attempts)
            or attempts < counter["checkpoint_attempts"]
            or simulations < counter["checkpoint_simulations"]
        ):
            raise ValueError("Research checkpoint rolled back its durable attempt accounting")
        counter.update(checkpoint_attempts=attempts, checkpoint_simulations=simulations)
    result[KEY] = counter
    return result


def attempt_reserver(
    store,
    owner,
    job_id,
    token,
    identity,
    policy,
    *,
    checkpoint_attempts=0,
    checkpoint_simulations=0,
    limit=MAX_ATTEMPTS,
):
    """Return ``reserve(expected_attempts, simulations, reserve, limit)``.

    Baselines must come from the controller's verified saved checkpoint. The
    first call reconciles reservations lost after that checkpoint; those lost
    calculations still consume both the total and 64-failure allowances. A
    legacy receipt without a journal is initialized from that explicit verified
    baseline. Once initialized, missing or decreasing counters are rejected.
    """
    baseline = {
        "version": VERSION,
        "identity": identity,
        "policy": policy,
        "limit": limit,
        "attempts": checkpoint_attempts,
        "checkpoint_attempts": checkpoint_attempts,
        "checkpoint_simulations": checkpoint_simulations,
    }
    _counter(baseline)
    thread = get_ident()
    first, last_attempts, last_simulations = True, checkpoint_attempts, checkpoint_simulations

    def reserve_attempt(expected_attempts, simulations, reserve, requested_limit):
        nonlocal first, last_attempts, last_simulations
        if get_ident() != thread:
            raise ValueError("Research attempt admission cannot leave its owning thread")
        if (
            not _integer(expected_attempts, limit)
            or expected_attempts != last_attempts
            or not _integer(simulations, expected_attempts)
            or simulations < last_simulations
            or (first and simulations != checkpoint_simulations)
            or type(requested_limit) is not int
            or requested_limit != limit
            # Hold back the recorded final pair and/or three/four capital
            # planning accounts. A larger reserve only restricts admission;
            # the immutable total and lost-attempt bounds remain unchanged.
            or type(reserve) is not int
            or reserve not in (0, 2, 3, 4, 5)
        ):
            raise ValueError("Research attempt admission changed its recorded accounting")
        with store.sessions.begin() as db:
            # Acquire the same serialized writer fence used by checkpoints.
            db.execute(update(ResearchWorker).where(ResearchWorker.id == 1).values(id=1))
            lease, job = db.get(ResearchWorker, 1), db.get(ResearchJob, job_id)
            if (
                lease is None
                or job is None
                or lease.token != token
                or job.owner != owner
                or job.worker != token
                or job.status not in ("running", "pausing")
            ):
                raise AttemptLeaseLost("Cancellation requested or worker lease lost")
            receipt = db.get(ResearchExperiment, job_id)
            if receipt is None or receipt.identity != identity:
                raise ValueError("Research attempt admission belongs to different frozen inputs")
            counts = deepcopy(_counts(receipt.counts))
            if KEY in counts:
                counter = deepcopy(_counter(counts[KEY]))
            elif first:
                counter = deepcopy(baseline)
            else:
                raise ValueError("Durable research attempt budget disappeared")
            if any(counter[key] != baseline[key] for key in ("identity", "policy", "limit")):
                raise ValueError("Research attempt admission changed its frozen policy or budget")
            attempts = counter["attempts"]
            if (
                attempts < expected_attempts
                or (not first and attempts != expected_attempts)
                or (first and counter["checkpoint_attempts"] != checkpoint_attempts)
                or (first and counter["checkpoint_simulations"] != checkpoint_simulations)
                or counter["checkpoint_attempts"] > expected_attempts
                or counter["checkpoint_simulations"] > simulations
            ):
                raise ValueError("Durable research attempt accounting rolled back or changed")
            if attempts >= limit - reserve or attempts - simulations >= MAX_LOST_ATTEMPTS:
                raise ValueError(
                    "Walk-forward research reached its recorded calculation limit; saved progress is retained"
                )
            counter["attempts"] = attempts + 1
            counts[KEY] = counter
            receipt.counts = json.dumps(
                counts, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        # Advance local state only after the durable transaction commits.
        first = False
        last_attempts, last_simulations = attempts + 1, simulations
        return last_attempts

    return reserve_attempt
