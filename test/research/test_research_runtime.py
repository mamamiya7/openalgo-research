"""Isolated real-process tests for production service supervision.

These children write only under pytest's temporary directory. No app, database,
broker, dotenv file or numerical package is imported by the harness.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from tools.research_runtime import commands, supervise

POSIX = pytest.mark.skipif(os.name != "posix", reason="Production supervisor uses POSIX groups")

CHILD = """
import os, pathlib, signal, subprocess, sys, time
root, name, mode = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if mode == 'stubborn':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == 'descendant':
    descendant = subprocess.Popen([sys.executable, __file__, str(root), 'grandchild', 'stubborn'])
(root / (name + '.pid')).write_text(str(os.getpid()))
if mode == 'exit':
    time.sleep(0.2)
    raise SystemExit(7)
if mode == 'success':
    time.sleep(0.2)
    raise SystemExit(0)
while True:
    if mode == 'checkpoint' and (root / 'worker.stop').exists():
        (root / 'checkpoint').write_text('saved')
        raise SystemExit(0)
    time.sleep(0.01)
"""


def child_script(tmp_path):
    path = tmp_path / "child.py"
    path.write_text(CHILD, encoding="utf-8")
    return path


def child_command(path, name, mode):
    return name, [sys.executable, str(path), str(path.parent), name, mode]


def wait_for(predicate, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Child process did not reach the expected state"


def assert_exited(tmp_path):
    for receipt in tmp_path.glob("*.pid"):
        pid = int(receipt.read_text())

        def exited(pid=pid):
            try:
                process = psutil.Process(pid)
                return process.status() == psutil.STATUS_ZOMBIE or not process.is_running()
            except psutil.NoSuchProcess:
                return True

        wait_for(exited)
        # Direct children must be reaped, not just terminated. A grandchild is
        # reaped by tini in the distribution (the external test runner owns it here).
        if receipt.stem != "grandchild":
            with pytest.raises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)


def test_commands_preserve_native_runtime_and_single_worker(tmp_path):
    configured = dict(commands("python", 5020, tmp_path))
    assert set(configured) == {"market-data", "research", "web"}
    assert configured["market-data"] == ["python", "-m", "websocket_proxy.server"]
    assert configured["research"][-2:] == ["--stop-file", str(tmp_path / "worker.stop")]
    web = configured["web"]
    assert web[web.index("--workers") + 1] == "1"
    assert web[web.index("--worker-class") + 1] == "eventlet"
    assert web[web.index("--bind") + 1] == "0.0.0.0:5020"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows startup guidance")
def test_windows_supervisor_refuses_before_starting_services(tmp_path, monkeypatch, capsys):
    from tools import research_runtime

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported supervisor must not start a child")

    monkeypatch.setattr(research_runtime, "ROOT", tmp_path)
    monkeypatch.setattr(research_runtime.subprocess, "Popen", forbidden)
    monkeypatch.setattr(sys, "argv", ["research_runtime.py"])
    with pytest.raises(SystemExit) as error:
        research_runtime.main()
    assert error.value.code == 2
    guidance = capsys.readouterr().err
    assert "uv run --no-sync python app.py" in guidance
    assert "services.scanner_research_worker" in guidance
    assert "docs/research/RUNTIME.md" in guidance
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(RuntimeError, match="separate terminals"):
        supervise([("web", ["unused"])], tmp_path / "stop", cwd=tmp_path)
    assert list(tmp_path.iterdir()) == []


@POSIX
def test_stop_checkpoints_worker_and_reaps_children(tmp_path):
    script = child_script(tmp_path)
    configured = [
        child_command(script, "web", "wait"),
        child_command(script, "research", "checkpoint"),
    ]
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    code = supervise(
        configured,
        tmp_path / "worker.stop",
        cwd=tmp_path,
        grace_seconds=1,
        terminate_seconds=0.1,
        stop_requested=lambda: len(list(tmp_path.glob("*.pid"))) == 2,
    )
    assert code == 0
    assert (tmp_path / "checkpoint").read_text() == "saved"
    assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    assert_exited(tmp_path)


@POSIX
@pytest.mark.parametrize(("mode", "expected"), [("exit", 7), ("success", 1)])
def test_child_exit_stops_other_services(tmp_path, mode, expected):
    script = child_script(tmp_path)
    code = supervise(
        [child_command(script, "web", mode), child_command(script, "research", "checkpoint")],
        tmp_path / "worker.stop",
        cwd=tmp_path,
        grace_seconds=1,
        terminate_seconds=0.1,
    )
    assert code == expected
    assert (tmp_path / "checkpoint").read_text() == "saved"
    assert_exited(tmp_path)


@POSIX
def test_startup_error_reaps_already_started_child(tmp_path, monkeypatch):
    script = child_script(tmp_path)
    spawned = []
    popen = subprocess.Popen

    def remember(*args, **kwargs):
        child = popen(*args, **kwargs)
        spawned.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", remember)
    with pytest.raises(FileNotFoundError):
        supervise(
            [child_command(script, "web", "wait"), ("research", [str(tmp_path / "missing")])],
            tmp_path / "worker.stop",
            cwd=tmp_path,
            grace_seconds=0.1,
            terminate_seconds=0.1,
        )
    # No children remain even when startup failed before a receipt was written.
    assert_exited(tmp_path)
    assert len(spawned) == 1
    assert spawned[0].returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(spawned[0].pid, os.WNOHANG)


@POSIX
def test_forced_shutdown_cleans_descendants_of_exited_leader(tmp_path):
    script = child_script(tmp_path)
    start = time.monotonic()
    code = supervise(
        [child_command(script, "web", "descendant")],
        tmp_path / "worker.stop",
        cwd=tmp_path,
        grace_seconds=0.1,
        terminate_seconds=0.1,
        stop_requested=lambda: (tmp_path / "grandchild.pid").exists(),
    )
    assert code == 0
    assert time.monotonic() - start < 3
    assert_exited(tmp_path)


@POSIX
def test_stop_file_failure_still_kills_stubborn_worker(tmp_path):
    script = child_script(tmp_path)
    code = supervise(
        [child_command(script, "research", "stubborn")],
        tmp_path / "missing-directory" / "worker.stop",
        cwd=tmp_path,
        grace_seconds=1,
        terminate_seconds=0.1,
        stop_requested=lambda: (tmp_path / "research.pid").exists(),
    )
    assert code == 0
    assert_exited(tmp_path)


@POSIX
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_service_signal_checkpoints_worker(tmp_path, sig):
    script = child_script(tmp_path)
    runner = tmp_path / "runner.py"
    module_root = Path(__file__).resolve().parents[2] / ".."
    runner.write_text(
        "import pathlib, sys\n"
        f"sys.path.insert(0, {str(module_root)!r})\n"
        "from tools.research_runtime import supervise\n"
        f"root = pathlib.Path({str(tmp_path)!r})\n"
        "raise SystemExit(supervise(\n"
        f"    {[child_command(script, 'research', 'checkpoint')]!r},\n"
        "    root / 'worker.stop', cwd=root, grace_seconds=1, terminate_seconds=0.1))\n",
        encoding="utf-8",
    )
    with (tmp_path / "supervisor.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(runner)], stdout=log, stderr=log)
        try:
            wait_for(lambda: (tmp_path / "research.pid").exists() or process.poll() is not None)
            assert process.poll() is None, (tmp_path / "supervisor.log").read_text()
            process.send_signal(sig)
            assert process.wait(timeout=5) == 0
            assert (tmp_path / "checkpoint").read_text() == "saved"
            assert_exited(tmp_path)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@POSIX
def test_service_count_is_bounded(tmp_path):
    with pytest.raises(ValueError, match="three uniquely"):
        supervise([(str(i), ["unused"]) for i in range(4)], tmp_path / "stop", cwd=tmp_path)


@POSIX
@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="Linux descriptor measurement")
def test_repeated_service_exits_do_not_accumulate_descriptors_or_children(tmp_path):
    before_fds = len(list(Path("/proc/self/fd").iterdir()))
    before_children = {child.pid for child in psutil.Process().children()}
    for _ in range(20):
        code = supervise(
            [("web", [sys.executable, "-c", "pass"])],
            tmp_path / "worker.stop",
            cwd=tmp_path,
            grace_seconds=0,
            terminate_seconds=0,
        )
        assert code == 1
    assert len(list(Path("/proc/self/fd").iterdir())) == before_fds
    assert {child.pid for child in psutil.Process().children()} == before_children
