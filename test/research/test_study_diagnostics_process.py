"""Optional plots have bounded inputs, cancellable children and balanced cleanup."""

import copy
import json
import subprocess
import sys

import pytest
from test_study_analysis import experiment, trailing_experiment

from research import study_diagnostics as diagnostics


class FakeProcess:
    def __init__(self, *, running=False, returncode=0):
        self.running, self.returncode = running, returncode
        self.kills, self.waits = 0, []

    def poll(self):
        return None if self.running else self.returncode

    def kill(self):
        self.kills += 1
        self.running, self.returncode = False, -9

    def wait(self, timeout=None):
        self.waits.append(timeout)
        assert not self.running
        return self.returncode


def response():
    return {
        "version": diagnostics.VERSION,
        "result": {
            "version": "research-analysis-v1",
            "parameters": [],
            "charts": [{"id": "history", "status": "unavailable", "reason": "No trials"}],
        },
    }


def fake(monkeypatch, *, running=False, reply=None, returncode=0, missing=False):
    process = FakeProcess(running=running, returncode=returncode)
    calls = []

    def start(args, **kwargs):
        target = diagnostics.Path(args[-1])
        calls.append((args, kwargs, target, json.loads((target / "input.json").read_text())))
        if not missing:
            (target / "output.json").write_text(json.dumps(reply or response()), encoding="utf-8")
        return process

    monkeypatch.setattr(diagnostics.subprocess, "Popen", start)
    return process, calls


def test_only_recorded_proposals_cross_boundary_and_private_environment_is_excluded(
    monkeypatch, tmp_path
):
    for name in (
        "BROKER_API_KEY",
        "APP_KEY",
        "API_KEY_PEPPER",
        "DATABASE_URL",
        "RESEARCH_DATA_DIR",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(name, "private-test-marker")
    original = experiment(timed=True)
    original.update(
        account={"password": "private-test-marker"},
        selected_reports={"equity": "private-test-marker"},
    )
    original["trials"][0]["hidden_account"] = "private-test-marker"
    original["search_space"]["private_config"] = "private-test-marker"
    before = copy.deepcopy(original)
    process, calls = fake(monkeypatch)
    assert diagnostics.build(original, directory=tmp_path)["charts"]
    args, options, target, request = calls[0]
    assert args[1] == "-I"
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert "private-test-marker" not in json.dumps(request) + json.dumps(options["env"])
    assert options["env"]["PYTHON_DOTENV_DISABLED"] == "1"
    assert options["env"]["OMP_NUM_THREADS"] == "1"
    assert original == before and not target.exists()
    assert process.kills == 0 and process.waits == [None, 10]


def test_cancellation_after_spawn_kills_reaps_and_removes_temporary_files(monkeypatch, tmp_path):
    process, calls = fake(monkeypatch, running=True)
    checked = 0

    def cancel():
        nonlocal checked
        checked += 1
        if checked == 2:
            raise RuntimeError("controlled cancellation")

    with pytest.raises(RuntimeError, match="controlled cancellation"):
        diagnostics.build(experiment(), directory=tmp_path, cancelled=cancel)
    assert process.kills == 1 and process.waits == [10]
    assert not calls[0][2].exists()


def test_timeout_kills_reaps_and_retains_findings(monkeypatch, tmp_path):
    process, calls = fake(monkeypatch, running=True)
    times = iter((0, diagnostics.MAX_SECONDS + 1))
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: next(times))
    with pytest.raises(ValueError, match="saved findings are unchanged"):
        diagnostics.build(experiment(), directory=tmp_path)
    assert process.kills == 1 and process.waits == [10]
    assert not calls[0][2].exists()


@pytest.mark.parametrize(
    "case", ["missing", "oversized", "wrong_version", "wrong_shape", "child_error", "exit_error"]
)
def test_child_failure_cannot_publish_invalid_analysis_and_always_reaps(
    monkeypatch, tmp_path, case
):
    reply = response()
    if case == "wrong_version":
        reply["version"] = "wrong"
    elif case == "wrong_shape":
        reply["result"] = {"charts": "wrong"}
    elif case == "child_error":
        reply["error"] = "controlled chart failure"
    if case == "oversized":
        monkeypatch.setattr(diagnostics, "MAX_OUTPUT_BYTES", 2)
    process, calls = fake(
        monkeypatch,
        reply=reply,
        returncode=1 if case == "exit_error" else 0,
        missing=case == "missing",
    )
    with pytest.raises(ValueError):
        diagnostics.build(experiment(), directory=tmp_path)
    assert process.kills == 0 and process.waits == [None, 10]
    assert not calls[0][2].exists()


@pytest.mark.parametrize("case", ["size", "trials", "axes", "parameters", "nan"])
def test_invalid_or_unbounded_input_is_rejected_before_starting_child(monkeypatch, tmp_path, case):
    original = experiment()
    parameters = None
    if case == "size":
        monkeypatch.setattr(diagnostics, "MAX_INPUT_BYTES", 2)
    elif case == "trials":
        original["trials"] *= 100
    elif case == "axes":
        original["search_space"]["axes"] = {str(i): {} for i in range(58)}
    elif case == "parameters":
        parameters = ["missing"]
    else:
        original["trials"][0]["value"] = float("nan")
    monkeypatch.setattr(
        diagnostics.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("Invalid input started a child"),
    )
    with pytest.raises(ValueError):
        diagnostics.build(original, parameters=parameters, directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_actual_isolated_child_renders_native_optuna_plots_without_mutating_proposals(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BROKER_API_KEY", "private-test-marker")
    original = experiment(timed=True)
    before = copy.deepcopy(original)
    progress = []
    result = diagnostics.build(original, directory=tmp_path, progress=lambda *p: progress.append(p))
    charts = {chart["id"]: chart for chart in result["charts"]}
    assert len(charts) == 12
    for name in (
        "history",
        "importance",
        "slice",
        "contour",
        "parallel",
        "rank",
        "edf",
        "timeline",
    ):
        assert charts[name]["status"] == "available", charts[name]
    assert progress[-1] == (1, 1)
    assert "private-test-marker" not in json.dumps(result)
    assert original == before and not list(tmp_path.iterdir())


@pytest.mark.parametrize("interval", ["D", "1m"])
def test_actual_child_preserves_v7_joint_trailing_and_registered_clocks(tmp_path, interval):
    from research.automatic_timing import VERSION

    original = trailing_experiment()
    original["experiment_version"] = "automatic-hypotheses-v7"
    branches = [{"id": "original", "label": "Original timing", "changes": {}}]
    if interval == "1m":
        branches.append(
            {"id": "entry_1015", "label": "Entry from 10:15", "changes": {"entry_time": "10:15"}}
        )
    original["timing_research"] = {
        "version": VERSION,
        "interval": interval,
        "strategies": [{"id": "a", "original": {}, "branches": branches}],
    }
    choices = [branch["id"] for branch in branches]
    original["search_space"]["axes"]["a.timing_choice"] = {
        "type": "categorical",
        "choices": choices,
    }
    for index, trial in enumerate(original["trials"]):
        trial["params"]["a.timing_choice"] = choices[index % len(choices)]
    before = copy.deepcopy(original)
    result = diagnostics.build(
        original, parameters=["a.timing_choice", "a.target_pct"], directory=tmp_path
    )
    charts = {chart["id"]: chart for chart in result["charts"]}
    assert charts["history"]["status"] == charts["parallel"]["status"] == "available"
    assert charts["importance"]["status"] == "unavailable"
    assert original == before and not list(tmp_path.iterdir())


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_actual_cancelled_or_timed_out_processes_are_reaped_without_handle_growth(
    tmp_path, monkeypatch, stop
):
    import psutil

    fixture_root = tmp_path / "controlled-worker"
    (fixture_root / "tools").mkdir(parents=True)
    (fixture_root / "tools" / "research_study_worker.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    temporary = tmp_path / "owned-temporary"
    temporary.mkdir()
    monkeypatch.setattr(diagnostics, "ROOT", fixture_root)
    processes = []
    original_popen = diagnostics.subprocess.Popen

    def capture(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(diagnostics.subprocess, "Popen", capture)
    if stop == "timeout":
        monkeypatch.setattr(diagnostics, "MAX_SECONDS", 0.05)
    current = psutil.Process()
    measure = current.num_handles if sys.platform == "win32" else current.num_fds
    baseline = measure()
    for _ in range(5):
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("controlled cancellation")

        with pytest.raises((RuntimeError, ValueError)):
            diagnostics.build(
                experiment(), directory=temporary, cancelled=cancel if stop == "cancel" else None
            )
        assert processes[-1].returncode is not None
        assert not psutil.pid_exists(processes[-1].pid)
        assert not list(temporary.iterdir())
        # The test deliberately captures children. Release those observation
        # references too: Windows Popen objects retain the OS process handle.
        processes.clear()
    assert measure() <= baseline + 3
