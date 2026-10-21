"""Frozen sampler programmes for offline whole-procedure qualification only.

These programmes vary optimisation randomness, never market datasets or the
production recipe. Programme zero reproduces v15's actual sampler seed map.
Repeated programmes on one dataset remain dependent observations.
"""

from copy import deepcopy

from research.report_contract import fingerprint

VERSION = "research-procedure-seed-calibration-v1"
FITS = ("fit1", "fit2", "fit3", "search", "search_seed1", "search_seed2")
LIMITATION = (
    "Offline sampler-path qualification only. Sampler programmes on the same "
    "dataset are not independent dataset replicates or search-adjusted confidence. "
    "No programme may replace the primary procedure with a luckier result."
)

# Frozen before outcome generation. Every alternative changes all six sampler
# streams; these are actual Optuna seeds, not aliases for canonical seeds 0/1/2.
_SEEDS = (
    (0, 0, 0, 0, 1, 2),
    (101, 211, 307, 401, 409, 419),
    (1009, 1013, 1019, 1021, 1031, 1033),
)


def seed_plan(index):
    """Return one sealed, outcome-independent programme from a fixed small set."""
    if type(index) is not int or not 0 <= index < len(_SEEDS):
        raise ValueError("Choose a declared offline sampler programme index")
    body = {
        "version": VERSION,
        "scope": "offline_full_procedure_seed_qualification",
        "offline_only": True,
        "index": index,
        "fit_sampler_seeds": dict(zip(FITS, _SEEDS[index], strict=True)),
        "primary_fit": "search",
        "repeat_fits": list(FITS[4:]),
        "limitation": LIMITATION,
    }
    return {**body, "id": fingerprint(body)}


def verify_seed_plan(plan):
    """Reject changed/resealed programmes, extra fields and implicit seed casts."""
    if not isinstance(plan, dict):
        raise ValueError("Offline sampler programme must be a sealed declaration")
    expected = seed_plan(plan.get("index"))
    try:
        actual_id = fingerprint({key: value for key, value in plan.items() if key != "id"})
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Offline sampler programme cannot be verified") from error
    # Check both exact declaration and seal: Python equality alone treats bools
    # and integral floats as integers, while the canonical seal distinguishes them.
    if plan != expected or plan.get("id") != actual_id:
        raise ValueError("Offline sampler programme changed its frozen declaration")
    return deepcopy(expected)
