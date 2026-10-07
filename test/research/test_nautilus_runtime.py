"""Owned process cancellation, bounded files and isolated Nautilus installation."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from research.connectors import nautilus_portfolio
from research.connectors import nautilus_runtime as runtime
from tools import research_engine_worker as worker
from tools import research_install_nautilus as installer

pytestmark = pytest.mark.timeout(60)
linux = pytest.mark.skipif(sys.platform != "linux", reason="Linux process-group lifecycle")


def test_json_limit_is_checked_during_write_and_preserves_previous_evidence(tmp_path):
    path = tmp_path / "result.json"
    worker.write_json(path, {"saved": True})
    with pytest.raises(ValueError, match="size limit"):
        worker.write_json(path, {"too_large": "x" * 2000}, 64)
    assert worker.read_json(path) == {"saved": True}
    assert list(tmp_path.iterdir()) == [path]


def test_json_encoder_error_removes_temporary_file(tmp_path):
    with pytest.raises(ValueError):
        worker.write_json(tmp_path / "result.json", {"value": float("nan")})
    assert list(tmp_path.iterdir()) == []


def test_json_reader_has_strict_byte_bound(tmp_path):
    path = tmp_path / "result.json"
    path.write_bytes(b'"' + b"x" * 100 + b'"')
    with pytest.raises(ValueError, match="size limit"):
        worker.read_json(path, 32)


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"transport": []},
        {"transport": "wsl", "python": "/python", "distribution": []},
        {"transport": "local", "python": "a\0b"},
    ],
)
def test_invalid_configuration_returns_unavailable_without_type_error(tmp_path, monkeypatch, value):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value))
    monkeypatch.setenv("RESEARCH_NAUTILUS_CONFIG", str(path))
    assert runtime.status()["available"] is False


def test_child_environment_contains_no_application_credentials(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("AUTH_TOKEN", "not-a-real-secret")
    monkeypatch.setenv("DATABASE_URL", "private-database")
    monkeypatch.setenv("PYTHONPATH", "private-python-path")
    monkeypatch.setenv("WSLENV", "BROKER_API_KEY")
    result = worker.clean_environment()
    assert (
        not {"BROKER_API_KEY", "AUTH_TOKEN", "DATABASE_URL", "PYTHONPATH", "WSLENV"} & result.keys()
    )
    assert result["PYTHON_DOTENV_DISABLED"] == "1"


def test_wsl_command_clears_linux_environment_before_python(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_path", lambda path, _: str(path))
    command = runtime._command(
        {"transport": "wsl", "distribution": "Ubuntu", "python": "/private/runtime/python"},
        tmp_path,
        "token",
    )
    assert command[:7] == [
        "wsl.exe",
        "--distribution",
        "Ubuntu",
        "--exec",
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
    ]
    assert command[command.index("/private/runtime/python") + 1] == "-I"


@pytest.mark.parametrize(
    "failure", [OSError("helper missing"), subprocess.TimeoutExpired("helper", 10)]
)
def test_stop_helper_failure_still_reaps_wrapper(tmp_path, monkeypatch, failure):
    (tmp_path / "process.json").write_text("{broken")
    process = Mock(pid=99)
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("worker", 5)] * 3 + [0]
    monkeypatch.setattr(runtime, "_command", lambda *args, **kwargs: ["controlled-helper"])
    monkeypatch.setattr(runtime.subprocess, "run", Mock(side_effect=failure))
    runtime._stop(process, {"transport": "wsl"}, tmp_path, "token")
    assert (tmp_path / "cancel").is_file()
    process.kill.assert_called_once()
    assert process.wait.call_count == 4


def test_stop_reaps_already_finished_wrapper(tmp_path):
    process = Mock()
    process.poll.return_value = 0
    runtime._stop(process, {}, tmp_path, "token")
    process.wait.assert_called_once_with()
    process.kill.assert_not_called()


@pytest.fixture
def controlled_runtime(tmp_path, monkeypatch):
    script = tmp_path / "controlled_worker.py"
    script.write_text(
        "import json, os, pathlib, sys, time\n"
        "folder = pathlib.Path(sys.argv[1]); mode = sys.argv[2]\n"
        "request = json.loads((folder/'input.json').read_text())\n"
        "assert not {'BROKER_API_KEY', 'AUTH_TOKEN', 'DATABASE_URL'} & os.environ.keys()\n"
        "if mode == 'wait':\n"
        "    while not (folder/'cancel').exists(): time.sleep(.01)\n"
        "    sys.exit(1)\n"
        "if mode == 'progress':\n"
        "    (folder/'progress.json').write_text('[0]')\n"
        "    while not (folder/'cancel').exists(): time.sleep(.01)\n"
        "    sys.exit(1)\n"
        "if mode == 'large': output = {'version':'1.231.0','result':{'large':'x'*2000}}\n"
        "elif mode == 'mismatch': output = {'version':'0.0.0','result':{}}\n"
        "elif mode == 'error': output = {'error':'Controlled calculation error'}\n"
        "elif mode == 'malformed': output = []\n"
        "else: output = {'version':'1.231.0','result':{'capital':request['capital']}}\n"
        "(folder/'output.json').write_text(json.dumps(output))\n"
    )
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(
        runtime, "configuration", lambda: {"transport": "local", "python": sys.executable}
    )
    monkeypatch.setattr(nautilus_portfolio, "validate", lambda *args: None)
    mode = ["ok"]
    monkeypatch.setattr(
        runtime,
        "_command",
        lambda _config, directory, _nonce, **kwargs: [
            sys.executable,
            str(script),
            str(directory),
            mode[0],
        ],
    )
    launched = []
    original = runtime.subprocess.Popen

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    return tmp_path, mode, launched


def test_repeated_evaluations_reap_children_and_remove_temp_files(controlled_runtime, monkeypatch):
    directory, _mode, launched = controlled_runtime
    monkeypatch.setenv("BROKER_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("AUTH_TOKEN", "not-a-real-secret")
    monkeypatch.setenv("DATABASE_URL", "private-database")
    for _ in range(8):
        assert runtime.evaluate([], {}, 123) == {"capital": 123}
        assert list((directory / ".agent-native/runtime-jobs").iterdir()) == []
    assert len(launched) == 8 and all(process.returncode == 0 for process in launched)


@pytest.mark.parametrize(
    "mode,message",
    [
        ("mismatch", "does not match"),
        ("error", "Controlled calculation error"),
        ("malformed", "Invalid Nautilus calculation result"),
        ("large", "size limit"),
    ],
)
def test_runtime_errors_clean_up_all_artifacts(controlled_runtime, monkeypatch, mode, message):
    directory, selected, launched = controlled_runtime
    selected[0] = mode
    if mode == "large":
        monkeypatch.setattr(runtime, "MAX_BYTES", 1024)
    with pytest.raises(ValueError, match=message):
        runtime.evaluate([], {}, 100)
    assert launched[0].poll() is not None
    assert list((directory / ".agent-native/runtime-jobs").iterdir()) == []


def test_cancellation_callback_stops_and_reaps_child_without_masking_error(controlled_runtime):
    directory, mode, launched = controlled_runtime
    mode[0] = "wait"

    def cancelled(_done, _total):
        raise InterruptedError("User cancelled")

    with pytest.raises(InterruptedError, match="User cancelled"):
        runtime.evaluate([], {}, 100, progress=cancelled)
    assert launched[0].poll() is not None
    assert list((directory / ".agent-native/runtime-jobs").iterdir()) == []


def test_input_limit_prevents_child_launch_and_removes_partial_file(
    controlled_runtime, monkeypatch
):
    directory, _mode, launched = controlled_runtime
    monkeypatch.setattr(runtime, "MAX_BYTES", 8)
    with pytest.raises(ValueError, match="size limit"):
        runtime.evaluate([], {}, 100)
    assert launched == []
    assert list((directory / ".agent-native/runtime-jobs").iterdir()) == []


def test_dependency_version_mismatch_never_starts_engine(tmp_path, monkeypatch):
    evaluate = Mock()
    monkeypatch.setattr(nautilus_portfolio, "evaluate", evaluate)
    monkeypatch.setattr(worker.metadata, "version", lambda _: "wrong")
    with pytest.raises(ValueError, match="locked Nautilus dependencies"):
        worker.calculate(tmp_path)
    evaluate.assert_not_called()


def test_stage_copies_only_locked_manifests(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "pyproject.toml").write_text("pinned manifest")
    (source / "uv.lock").write_text("pinned lock")
    (source / ".env").write_text("private broker configuration")
    (source / "live.db").write_text("private database")
    installer._stage(source, target)
    assert {path.name for path in target.iterdir()} == {"pyproject.toml", "uv.lock"}
    assert (target / "uv.lock").read_text() == "pinned lock"


def test_stage_oversized_manifest_leaves_no_temporary_file(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "pyproject.toml").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="manifest"):
        installer._stage(source, target)
    assert list(target.iterdir()) == []


@linux
def test_wsl_runtime_uses_stable_linux_cache_without_copying_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = Path("/mnt/c/private/openalgo")
    project = installer._project(root, "Ubuntu", None)
    assert str(project).startswith(str(tmp_path / ".cache/openalgo-research"))
    assert project == installer._project(root, "Ubuntu", None)
    assert project != installer._project(Path("/mnt/c/private/other"), "Ubuntu", None)
    assert installer._project(root, "Ubuntu", tmp_path / "chosen") == tmp_path / "chosen"
    with pytest.raises(ValueError, match="Linux runtime directory"):
        installer._project(root, "Ubuntu", "/mnt/c/slow/runtime")


@linux
@pytest.mark.parametrize("cancel", [True, False])
def test_supervisor_reaps_hard_hung_native_child_with_corrupt_record(tmp_path, cancel):
    """A real child ignores SIGTERM; only its owned group is terminated."""
    child_script = tmp_path / "hang.py"
    child_script.write_text(
        "import os, pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.01)\n"
    )
    pid_file = tmp_path / "child.pid"
    runner = tmp_path / "supervisor.py"
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(worker.ROOT)!r})\n"
        "from tools.research_engine_worker import supervise\n"
        f"supervise({str(tmp_path)!r}, 'controlled-token', command=[sys.executable, {str(child_script)!r}, {str(pid_file)!r}], deadline={15 if cancel else 0.5}, grace=.1)\n"
    )
    supervisor = subprocess.Popen(
        [sys.executable, str(runner)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        until = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < until:
            time.sleep(0.02)
        assert pid_file.is_file()
        pid = int(pid_file.read_text())
        (tmp_path / "process.json").write_text("{broken")
        if cancel:
            (tmp_path / "cancel").touch()
        assert supervisor.wait(timeout=10) != 0
        assert not Path(f"/proc/{pid}").exists(), "Owned native child was not reaped"
    finally:
        worker.stop_process(supervisor, grace=0.1)


@linux
def test_installer_timeout_reaps_owned_process_group(tmp_path):
    marker = tmp_path / "pid"
    code = f"import os, pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(20)"
    with pytest.raises(ValueError, match="exceeded"):
        installer._run([sys.executable, "-c", code], 0.3, "Controlled probe")
    assert marker.is_file()
    assert not Path(f"/proc/{int(marker.read_text())}").exists()


@linux
def test_installer_failure_preserves_existing_config_and_unlocks(tmp_path, monkeypatch):
    root = tmp_path / "app"
    source = root / "research/runtimes/nautilus"
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text("pinned")
    (source / "uv.lock").write_text("locked")
    destination = root / "research_data/nautilus-runtime.json"
    destination.parent.mkdir()
    destination.write_text('{"saved": true}')
    monkeypatch.setattr(installer, "_run", Mock(side_effect=ValueError("Controlled failure")))
    for _ in range(2):
        with pytest.raises(ValueError, match="Controlled failure"):
            installer.install(root, uv=sys.executable, runtime_dir=tmp_path / "runtime")
    assert worker.read_json(destination) == {"saved": True}
    assert {p.name for p in destination.parent.iterdir()} == {"nautilus-runtime.json"}
    receipt = root / ".agent-native/nautilus-install/install.log"
    assert receipt.stat().st_size < 8192 and worker.read_json(receipt)["status"] == "failed"


def test_invalid_progress_cancels_child_and_cleans_files(controlled_runtime):
    directory, mode, launched = controlled_runtime
    mode[0] = "progress"
    with pytest.raises(ValueError, match="Invalid Nautilus calculation progress"):
        runtime.evaluate([], {}, 100, progress=lambda *_args: None)
    assert launched[0].poll() is not None
    assert list((directory / ".agent-native/runtime-jobs").iterdir()) == []


def test_runtime_deadline_cancels_child_and_cleans_files(controlled_runtime, monkeypatch):
    directory, mode, launched = controlled_runtime
    mode[0] = "wait"
    monkeypatch.setattr(runtime, "MAX_SECONDS", 0.01)
    with pytest.raises(ValueError, match="exceeded 30 minutes"):
        runtime.evaluate([], {}, 100)
    assert launched[0].poll() is not None
    assert list((directory / ".agent-native/runtime-jobs").iterdir()) == []


def test_failed_atomic_replace_retains_old_file_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "process.json"
    worker.write_json(destination, {"previous": True})
    monkeypatch.setattr(Path, "replace", Mock(side_effect=PermissionError("locked")))
    with pytest.raises(PermissionError, match="locked"):
        worker.write_json(destination, {"new": True})
    assert worker.read_json(destination) == {"previous": True}
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_child_json_is_rejected(tmp_path, value):
    path = tmp_path / "output.json"
    path.write_text('{"result":' + value + "}")
    with pytest.raises(ValueError, match="Non-finite"):
        worker.read_json(path)


@linux
@pytest.mark.parametrize("corruption", ["nonce", "identity", "arguments"])
def test_fallback_never_signals_unowned_process(tmp_path, monkeypatch, corruption):
    entry = {"pid": 12345, "group": 12345, "start": "123"}
    record = {"nonce": "our-launch", "processes": [entry]}
    worker.write_json(tmp_path / "process.json", record)
    monkeypatch.setattr(
        worker,
        "_identity",
        lambda _: entry if corruption != "identity" else {**entry, "start": "456"},
    )
    command = [
        b"python",
        str(tmp_path).encode(),
        b"other-launch" if corruption == "arguments" else b"our-launch",
    ]
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"\0".join(command))
    kill = Mock()
    monkeypatch.setattr(worker.os, "killpg", kill)
    worker.stop_recorded(
        tmp_path, "other-launch" if corruption == "nonce" else "our-launch", signal.SIGKILL
    )
    kill.assert_not_called()


@pytest.mark.skipif(
    os.name != "nt" or os.getenv("RESEARCH_NAUTILUS_INTEGRATION") != "1",
    reason="Opt-in configured Windows to WSL bridge",
)
def test_windows_cancel_reaps_hung_linux_child_without_valid_process_record(tmp_path):
    config = runtime.configuration()
    if config["transport"] != "wsl":
        pytest.skip("This integration case requires the configured WSL runtime")
    directory = runtime._path(tmp_path, config)
    child = tmp_path / "hang.py"
    child.write_text(
        "import os, pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "while True: os.write(1, b'controlled-noisy-worker' * 1024)\n"
    )
    runner = tmp_path / "supervisor.py"
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {runtime._path(worker.ROOT, config)!r})\n"
        "from tools.research_engine_worker import supervise\n"
        f"supervise({directory!r}, sys.argv[1], command=[sys.executable, {directory + '/hang.py'!r}, {directory + '/child.pid'!r}], grace=.1)\n"
    )
    token = "controlled-wsl-integration"
    command = runtime._command(config, tmp_path, token)
    index = command.index(runtime._path(worker.ROOT / "tools/research_engine_worker.py", config))
    command[index:] = [runtime._path(runner, config), token]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=worker.clean_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        until = time.monotonic() + 15
        while not (tmp_path / "child.pid").exists() and time.monotonic() < until:
            time.sleep(0.05)
        assert (tmp_path / "child.pid").is_file()
        pid = int((tmp_path / "child.pid").read_text())
        (tmp_path / "process.json").write_text("{broken")
        runtime._stop(process, config, tmp_path, token)
        assert process.returncode is not None
        # Read-only check of the exact child PID created by this test.
        check = subprocess.run(
            [
                "wsl.exe",
                "--distribution",
                config["distribution"],
                "--exec",
                "/usr/bin/test",
                "!",
                "-e",
                f"/proc/{pid}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert check.returncode == 0, "Owned Linux calculation child was not reaped"
        assert not (tmp_path / "runtime.log").exists()
    finally:
        runtime._stop(process, config, tmp_path, token)
