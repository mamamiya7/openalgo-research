"""Local launcher resource, readiness and cleanup checks using isolated child processes."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from tools import research_desktop as desktop

CHILD = """
import os, pathlib, signal, subprocess, sys, time
root, name, mode = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if mode == 'stubborn':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == 'descendant':
    subprocess.Popen([sys.executable, __file__, str(root), 'grandchild', 'stubborn'],
                     start_new_session=(os.name == 'posix'))
(root / (name + '.pid')).write_text(str(os.getpid()))
if mode == 'fail':
    time.sleep(0.2)
    raise SystemExit(7)
while True:
    if mode == 'checkpoint' and (root / (name + '.stop')).exists():
        (root / (name + '.checkpoint')).write_text('saved')
        raise SystemExit(0)
    time.sleep(0.01)
"""


def commands(tmp_path, web="checkpoint", worker="checkpoint"):
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    return [
        (name, [sys.executable, str(script), str(tmp_path), name, mode])
        for name, mode in (("web", web), ("worker", worker))
    ]


def assert_children_gone(tmp_path):
    for receipt in tmp_path.glob("*.pid"):
        pid = int(receipt.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                child = psutil.Process(pid)
                if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"Owned process {pid} survived cleanup")


def test_local_configuration_preserves_data_and_never_exposes_debugger():
    source = {
        "DATABASE_URL": "sqlite:///db/mine.db",
        "APP_KEY": "unchanged",
        "FLASK_DEBUG": "True",
        "WEBSOCKET_HOST": "0.0.0.0",
        "WERKZEUG_RUN_MAIN": "true",
    }
    result = desktop.local_environment(source, 5211)
    assert source["FLASK_DEBUG"] == "True"
    assert result["DATABASE_URL"] == source["DATABASE_URL"]
    assert result["APP_KEY"] == "unchanged"
    assert result["FLASK_PORT"] == "5211"
    assert result["FLASK_DEBUG"] == "False"
    assert result["WEBSOCKET_HOST"] == result["FLASK_HOST_IP"] == result["ZMQ_HOST"] == "127.0.0.1"
    assert "WERKZEUG_RUN_MAIN" not in result


def test_port_collision_leaves_existing_listener_alone():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(desktop.InstanceBusy, match="already in use"):
            desktop.check_ports([("website", port)])
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


@pytest.mark.skipif(sys.platform != "linux", reason="Linux TIME_WAIT restart semantics")
def test_recently_closed_connection_allows_restart_but_active_listener_does_not():
    import errno

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
            accepted, _ = listener.accept()
            with accepted:
                # The server actively closes, putting its local port in
                # TIME_WAIT after the client's FIN; no arbitrary sleep needed.
                accepted.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""
                client.shutdown(socket.SHUT_WR)
                assert accepted.recv(1) == b""

    # Prove this exercises the original failure, rather than an unused port.
    with socket.socket() as plain:
        with pytest.raises(OSError) as error:
            plain.bind(("127.0.0.1", port))
        assert error.value.errno == errno.EADDRINUSE

    desktop.check_ports([("market-data", port)])
    with socket.socket() as restarted:
        restarted.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        restarted.bind(("127.0.0.1", port))
        restarted.listen()
        with pytest.raises(desktop.InstanceBusy, match="already in use"):
            desktop.check_ports([("market-data", port)])
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


def test_own_env_overrides_another_installation_and_disabled_dotenv(tmp_path):
    path = tmp_path / ".env"
    path.write_text("FLASK_PORT=5327\nDATABASE_URL=sqlite:///db/own.db\nAPP_KEY=own-key\n")
    ambient = {
        "FLASK_PORT": "9999",
        "DATABASE_URL": "sqlite:///other.db",
        "APP_KEY": "other-key",
        "PYTHON_DOTENV_DISABLED": "1",
    }
    environment, port = desktop.installation_environment(path, ambient)
    assert port == 5327
    assert environment["DATABASE_URL"] == "sqlite:///db/own.db"
    assert environment["APP_KEY"] == "own-key"
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert ambient["FLASK_PORT"] == "9999"
    overridden, port = desktop.installation_environment(path, ambient, 5328)
    assert port == 5328 and overridden["FLASK_PORT"] == "5328"


def test_lock_releases_for_restart_and_refuses_duplicate(tmp_path):
    path = tmp_path / "launcher.lock"
    with desktop.InstanceLock(path):
        with pytest.raises(desktop.InstanceBusy, match="already running"):
            with desktop.InstanceLock(path):
                pytest.fail("duplicate acquired the lock")
    with desktop.InstanceLock(path):
        pass


def test_startup_order_readiness_stop_and_restart(tmp_path):
    configured = commands(tmp_path)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    for _ in range(2):
        for item in tmp_path.glob("*.stop"):
            item.unlink()
        for item in tmp_path.glob("*.pid"):
            item.unlink()
        opened = []

        def ready(name):
            if name == "web":
                assert not (tmp_path / "worker.pid").exists()
            return (tmp_path / f"{name}.pid").exists()

        assert (
            desktop.supervise(
                configured,
                tmp_path,
                cwd=tmp_path,
                ready=ready,
                on_ready=lambda opened=opened: opened.append(True),
                stop_requested=lambda opened=opened: bool(opened),
                grace_seconds=1,
                terminate_seconds=0.1,
            )
            == 0
        )
        assert opened == [True]
        assert (tmp_path / "worker.checkpoint").read_text() == "saved"
        assert (tmp_path / "web.checkpoint").read_text() == "saved"
        assert_children_gone(tmp_path)
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


def test_child_failure_stops_the_other_service(tmp_path):
    configured = commands(tmp_path, worker="fail")
    with pytest.raises(RuntimeError, match="worker service stopped"):
        desktop.supervise(
            configured,
            tmp_path,
            cwd=tmp_path,
            ready=lambda name: (tmp_path / f"{name}.pid").exists(),
            grace_seconds=1,
            terminate_seconds=0.1,
        )
    assert (tmp_path / "web.checkpoint").exists()
    assert_children_gone(tmp_path)


def test_startup_error_reaps_started_child(tmp_path):
    configured = commands(tmp_path)
    configured[1] = ("worker", [str(tmp_path / "missing-python")])
    with pytest.raises(FileNotFoundError):
        desktop.supervise(
            configured,
            tmp_path,
            cwd=tmp_path,
            ready=lambda name: (tmp_path / f"{name}.pid").exists(),
            grace_seconds=1,
            terminate_seconds=0.1,
        )
    assert_children_gone(tmp_path)


def test_readiness_timeout_never_opens_browser(tmp_path):
    with pytest.raises(RuntimeError, match="did not become ready"):
        desktop.supervise(
            commands(tmp_path),
            tmp_path,
            cwd=tmp_path,
            ready=lambda name: False,
            startup_seconds=0.2,
            on_ready=lambda: pytest.fail("opened too early"),
            grace_seconds=1,
            terminate_seconds=0.1,
        )
    assert not (tmp_path / "worker.pid").exists()
    assert_children_gone(tmp_path)


def test_forced_shutdown_reaps_detached_descendant(tmp_path):
    desktop.supervise(
        commands(tmp_path, worker="descendant"),
        tmp_path,
        cwd=tmp_path,
        ready=lambda name: (tmp_path / f"{name}.pid").exists(),
        stop_requested=lambda: (tmp_path / "grandchild.pid").exists(),
        grace_seconds=0.1,
        terminate_seconds=0.1,
    )
    assert_children_gone(tmp_path)


def test_log_output_stays_bounded_and_handles_close(tmp_path):
    output = tmp_path / "output.log"
    with desktop.RotatingOutput(output, max_bytes=4096, backups=2) as stream:
        for _ in range(100):
            stream.write("x" * 10000)
        stream.flush()
    files = list(tmp_path.glob("output.log*"))
    assert len(files) == 3
    assert sum(path.stat().st_size for path in files) <= 3 * (4096 + 2048)
    assert stream.closed
    for path in files:
        path.unlink()


def test_repeated_failures_do_not_accumulate_owned_handles(tmp_path):
    process = psutil.Process()
    measure = process.num_handles if os.name == "nt" else process.num_fds
    before = measure()
    children = {child.pid for child in process.children()}
    for _ in range(8):
        with pytest.raises(RuntimeError, match="web service stopped"):
            desktop.supervise(
                [("web", [sys.executable, "-c", "raise SystemExit(7)"]), ("worker", ["unused"])],
                tmp_path,
                cwd=tmp_path,
                ready=lambda name: False,
                grace_seconds=0,
                terminate_seconds=0,
            )
    assert measure() <= before + 2
    assert {child.pid for child in process.children()} == children


def test_worker_readiness_marker_is_after_acquire_and_removed(tmp_path, monkeypatch):
    from services import scanner_research_worker as worker

    marker = tmp_path / "worker.ready"
    events = []

    class Store:
        def initialize(self):
            events.append("initialize")

        def close(self):
            events.append("close")

    def acquired(*_):
        assert not marker.exists()
        events.append("acquire")

    def run(*args, **kwargs):
        assert marker.read_text() == "ready\n"
        events.append("run")
        return False

    monkeypatch.setattr(worker, "ResearchStore", Store)
    monkeypatch.setattr(worker, "acquire", acquired)
    monkeypatch.setattr(worker, "release", lambda *_: events.append("release"))
    monkeypatch.setattr(worker, "run_one", run)
    monkeypatch.setattr(sys, "argv", ["worker", "--once", "--ready-file", str(marker)])
    worker.main()
    assert events == ["initialize", "acquire", "run", "release", "close"]
    assert not marker.exists()

    marker.write_text("existing")
    monkeypatch.setattr(worker, "acquire", lambda *_: None)
    with pytest.raises(FileExistsError):
        worker.main()
    assert marker.read_text() == "existing"


@pytest.mark.skipif(os.name != "nt", reason="Windows kill-on-console-close ownership")
def test_abrupt_launcher_exit_kills_owned_tree(tmp_path):
    configured = commands(tmp_path, worker="descendant")
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import os, pathlib, sys\n"
        f"sys.path.insert(0, {str(desktop.ROOT)!r})\n"
        "from tools.research_desktop import supervise\n"
        f"root = pathlib.Path({str(tmp_path)!r})\n"
        "(root / 'launcher.pid').write_text(str(os.getpid()))\n"
        f"supervise({configured!r}, root, cwd=root, "
        "ready=lambda name: (root / (name + '.pid')).exists())\n",
        encoding="utf-8",
    )
    with (tmp_path / "runner.log").open("w") as output:
        launcher = subprocess.Popen(
            [sys.executable, str(runner)],
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            deadline = time.monotonic() + 10
            while not (tmp_path / "grandchild.pid").exists() and time.monotonic() < deadline:
                assert launcher.poll() is None
                time.sleep(0.02)
            assert (tmp_path / "grandchild.pid").exists()
            # Terminate the real interpreter, bypassing all Python finally blocks.
            psutil.Process(int((tmp_path / "launcher.pid").read_text())).kill()
            launcher.wait(timeout=5)
            assert_children_gone(tmp_path)
        finally:
            if launcher.poll() is None:
                for child in psutil.Process(launcher.pid).children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                launcher.kill()
            launcher.wait(timeout=5)


def test_web_entry_refuses_unmanaged_or_nonlocal_launch(monkeypatch, tmp_path):
    from tools.research_desktop_web import run

    monkeypatch.setenv("FLASK_HOST_IP", "0.0.0.0")
    monkeypatch.setenv("FLASK_DEBUG", "False")
    with pytest.raises(RuntimeError, match="loopback"):
        run(tmp_path)
    monkeypatch.setenv("FLASK_HOST_IP", "127.0.0.1")
    monkeypatch.delenv("RESEARCH_DESKTOP_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="desktop launcher"):
        run(tmp_path)


def test_web_startup_failure_joins_watcher_and_uses_native_shutdown(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from tools.research_desktop_web import run

    events = []
    routes = {}

    class App:
        def get(self, path):
            def register(function):
                routes[path] = function
                return function

            return register

    def socket_run(app, **kwargs):
        assert kwargs == {
            "host": "127.0.0.1",
            "port": 5317,
            "debug": False,
            "use_reloader": False,
            "allow_unsafe_werkzeug": True,
        }
        assert routes["/__research_desktop_ready"]()[0] == "a" * 48
        raise RuntimeError("bind failed")

    monkeypatch.setitem(
        sys.modules, "app", SimpleNamespace(app=App(), socketio=SimpleNamespace(run=socket_run))
    )
    monkeypatch.setitem(
        sys.modules,
        "utils.shutdown",
        SimpleNamespace(
            install_signal_handlers=lambda: events.append("signals"),
            shutdown_runtime=lambda: events.append("shutdown"),
        ),
    )
    monkeypatch.setenv("FLASK_HOST_IP", "127.0.0.1")
    monkeypatch.setenv("FLASK_PORT", "5317")
    monkeypatch.setenv("FLASK_DEBUG", "False")
    monkeypatch.setenv("RESEARCH_DESKTOP_TOKEN", "a" * 48)
    before = set(threading.enumerate())
    with pytest.raises(RuntimeError, match="bind failed"):
        run(tmp_path)
    assert set(threading.enumerate()) == before
    assert events == ["signals", "shutdown"]
