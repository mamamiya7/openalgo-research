"""Optional Optuna presentation in a bounded, reaped calculation subprocess.

Only recorded proposals are sent to the child. No account configuration, broker
credentials, prices or mutable runtime database are needed for native plots.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = "research-study-diagnostics-v1"
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 48 * 1024 * 1024
MAX_SECONDS = 90
ROOT = Path(__file__).resolve().parents[1]
TRIAL_FIELDS = {
    "number",
    "state",
    "params",
    "value",
    "config_id",
    "reused",
    "datetime_start",
    "datetime_complete",
}


def deferred():
    return {"version": VERSION, "status": "available", "required": False}


def _environment():
    allowed = {
        "systemroot",
        "windir",
        "systemdrive",
        "comspec",
        "path",
        "pathext",
        "temp",
        "tmp",
        "userprofile",
        "appdata",
        "localappdata",
        "programfiles",
        "programfiles(x86)",
        "programdata",
    }
    result = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    result.update(
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHON_DOTENV_DISABLED="1",
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
    )
    return result


def _reap(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def payload(experiment, parameters):
    """Only the finite recorded proposal contract crosses the process boundary."""
    from research.study_analysis import MAX_TRIALS, parameter_limit

    if not isinstance(experiment, dict):
        raise ValueError("Study diagnostics need recorded proposals")
    trials = experiment.get("trials", [])
    search = experiment.get("search_space", {})
    axes = search.get("axes", {}) if isinstance(search, dict) else None
    if (
        not isinstance(trials, list)
        or len(trials) > MAX_TRIALS
        or not isinstance(axes, dict)
        or len(axes) > parameter_limit(experiment.get("experiment_version"))
        or any(not isinstance(row, dict) for row in trials)
        or any(
            not isinstance(name, str) or not 1 <= len(name) <= 128 or not isinstance(axis, dict)
            for name, axis in axes.items()
        )
    ):
        raise ValueError("Study diagnostics exceed their recorded proposal/parameter limits")
    if parameters is not None and (
        not isinstance(parameters, list)
        or len(parameters) > 2
        or any(not isinstance(name, str) or name not in axes for name in parameters)
        or len(set(parameters)) != len(parameters)
    ):
        raise ValueError("Choose up to two parameters from this study")
    selected = {
        key: value
        for key, value in experiment.items()
        if key
        in {"experiment_version", "trailing_research", "timing_research", "execution_research"}
    }
    selected["trials"] = [
        {key: value for key, value in row.items() if key in TRIAL_FIELDS} for row in trials
    ]
    selected["search_space"] = {
        "axes": {
            name: {
                key: value
                for key, value in axis.items()
                if key in {"type", "choices", "min", "max", "step"}
            }
            for name, axis in axes.items()
        },
        "objective_definition": search.get("objective_definition", "Objective score"),
    }
    return {"experiment": selected, "parameters": parameters}


def validate_result(result):
    from research.study_analysis import VERSION as ANALYSIS_VERSION

    if (
        not isinstance(result, dict)
        or result.get("version") != ANALYSIS_VERSION
        or not isinstance(result.get("charts"), list)
        or not 1 <= len(result["charts"]) <= 12
    ):
        raise ValueError("Invalid recorded study diagnostics")
    seen = set()
    for chart in result["charts"]:
        if (
            not isinstance(chart, dict)
            or not isinstance(chart.get("id"), str)
            or len(chart["id"]) > 64
            or chart["id"] in seen
            or chart.get("status") not in {"available", "unavailable"}
            or (chart["status"] == "available" and not isinstance(chart.get("figure"), dict))
        ):
            raise ValueError("Invalid recorded study diagnostics")
        seen.add(chart["id"])
    return result


def build(experiment, *, parameters=None, progress=None, cancelled=None, directory=None):
    # Preserve original recorded trial ordering/identities but omit retained
    # account curves and reports; Optuna reconstruction does not consume them.
    raw = json.dumps(payload(experiment, parameters), allow_nan=False).encode()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(
            "Study diagnostics exceed the recorded input limit; your results are saved."
        )
    if cancelled:
        cancelled()
    with tempfile.TemporaryDirectory(prefix="research-diagnostics-", dir=directory) as temp:
        target = Path(temp)
        (target / "input.json").write_bytes(raw)
        process = subprocess.Popen(
            [sys.executable, "-I", str(ROOT / "tools/research_study_worker.py"), str(target)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_environment(),
            cwd=target,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        began = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled:
                    cancelled()
                if time.monotonic() - began > MAX_SECONDS:
                    raise ValueError(
                        f"Optional study charts exceeded {MAX_SECONDS:g} seconds. Your saved findings are unchanged."
                    )
                if progress:
                    progress(0, 1)
                time.sleep(0.1)
            process.wait()
            if cancelled:
                cancelled()
            output = target / "output.json"
            if not output.is_file() or output.stat().st_size > MAX_OUTPUT_BYTES:
                raise ValueError(
                    "Optional study charts could not be prepared. Your saved findings are unchanged."
                )
            response = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(response, dict):
                raise ValueError("Invalid recorded study diagnostics response")
            if process.returncode or response.get("version") != VERSION or response.get("error"):
                raise ValueError(str(response.get("error") or "Study diagnostics failed")[:500])
            result = validate_result(response.get("result"))
            if progress:
                progress(1, 1)
            return result
        finally:
            _reap(process)
