"""Start the local OpenAlgo website and Research worker together.

This is a desktop launcher, not the public-server/container entry point. It
never changes .env and only binds the web, WebSocket and ZMQ listeners locally.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class InstanceBusy(RuntimeError):
    pass


class InstanceLock:
    """OS-held lock: closing the launcher or crashing releases it automatically."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            self.handle.seek(0, os.SEEK_END)
            if not self.handle.tell():
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            self.handle.close()
            self.handle = None
            raise InstanceBusy("OpenAlgo Research is already running from this folder.") from error
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class WindowsJob:
    """Own the complete child tree, even if Windows closes the console abruptly."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (field, ctypes.c_uint64)
                for field in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        handle = self.api.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.api.CloseHandle(handle)
            raise error
        self.handle = handle

    def assign(self, child):
        if self.handle is not None:
            import ctypes

            if not self.api.AssignProcessToJobObject(self.handle, int(child._handle)):
                raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, child):
        """Resume only after assignment, before even a Windows venv shim can fork."""
        if self.handle is None:
            return
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        self.api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.api.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.api.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenThread.restype = wintypes.HANDLE
        self.api.ResumeThread.argtypes = [wintypes.HANDLE]
        self.api.ResumeThread.restype = wintypes.DWORD
        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == child.pid:
                    handle = self.api.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not handle:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(handle) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(handle)
                    return
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("Could not resume the owned OpenAlgo service")
        finally:
            self.api.CloseHandle(snapshot)

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


class RotatingOutput(io.TextIOBase):
    """Bound Python output without output pipes or a draining background thread."""

    def __init__(self, path: Path, max_bytes=2 * 1024 * 1024, backups=2):
        super().__init__()
        self.handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        self.handler.terminator = ""
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    @property
    def encoding(self):
        return "utf-8"

    def isatty(self):
        return False

    def write(self, value):
        if not self.closed:
            # A single huge print must not bypass rotation's size bound.
            for start in range(0, len(value), 2048):
                self.handler.handle(
                    logging.LogRecord(
                        "desktop", logging.INFO, "", 0, value[start : start + 2048], (), None
                    )
                )
        return len(value)

    def flush(self):
        if not self.closed:
            self.handler.flush()

    def close(self):
        if not self.closed:
            self.flush()
            super().close()
            self.handler.close()


def local_environment(source, port):
    environment = dict(source)
    environment.update(
        FLASK_HOST_IP="127.0.0.1",
        FLASK_PORT=str(port),
        FLASK_DEBUG="False",
        FLASK_ENV="production",
        APP_MODE="integrated",
        WEBSOCKET_HOST="127.0.0.1",
        ZMQ_HOST="127.0.0.1",
        NGROK_ALLOW="FALSE",
        PYTHONUNBUFFERED="1",
        # Parent already loaded the complete .env. Native modules call dotenv
        # with override=True; prevent those from undoing this local-only launch.
        PYTHON_DOTENV_DISABLED="1",
    )
    environment.pop("WERKZEUG_RUN_MAIN", None)
    return environment


def installation_environment(path, ambient, port=None):
    """The selected installation's settings take precedence over shell settings.

    dotenv_values reads even when an inherited PYTHON_DOTENV_DISABLED is set.
    Children then receive these resolved settings with dotenv reloads disabled.
    """
    from dotenv import dotenv_values

    source = dict(ambient)
    source.update({key: value for key, value in dotenv_values(path).items() if value is not None})
    selected_port = int(source.get("FLASK_PORT", "5000")) if port is None else port
    return local_environment(source, selected_port), selected_port


def check_ports(ports):
    """Refuse collisions. Never terminate an unknown listener or choose a new port."""
    seen = set()
    for name, port in ports:
        if not 1 <= port <= 65535 or port in seen:
            raise ValueError(
                "Web, WebSocket and ZMQ ports must be distinct values between 1 and 65535."
            )
        seen.add(port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # Match the native POSIX servers' restart semantics. A closed
                # connection in TIME_WAIT has no listening process, and must
                # not be mistaken for another installation still running.
                # SO_REUSEADDR still refuses an active listener; SO_REUSEPORT
                # is deliberately never enabled by this probe.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise InstanceBusy(
                    f"The {name} port {port} is already in use. Close the existing OpenAlgo "
                    "window, or check the port settings in .env. No running app was stopped."
                ) from error


def web_ready(port, token):
    # Ignore proxy environment variables for this loopback-only health request.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/__research_desktop_ready")
        with opener.open(request, timeout=1) as response:
            return response.status == 200 and response.read(256).decode() == token
    except (OSError, urllib.error.URLError, UnicodeError):
        return False


def _remember_descendants(children, recorded):
    import psutil

    for _, child in children:
        try:
            for process in psutil.Process(child.pid).children(recursive=True):
                recorded[(process.pid, process.create_time())] = process
        except psutil.Error:
            pass
    # Keep only live identities; no accumulating per-calculation process cache.
    for key, process in tuple(recorded.items()):
        if not process.is_running():
            recorded.pop(key, None)


def _signal_children(children, recorded, *, force=False):
    import psutil

    for process in reversed(list(recorded.values())):
        try:
            process.kill() if force else process.terminate()
        except psutil.Error:
            pass
    for _, child in reversed(children):
        try:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif child.poll() is None:
                child.kill() if force else child.terminate()
        except OSError:
            pass


def supervise(
    commands,
    runtime_dir,
    *,
    cwd,
    environment=None,
    ready=None,
    on_ready=lambda: None,
    stop_requested=lambda: False,
    startup_seconds=180,
    grace_seconds=35,
    terminate_seconds=3,
):
    """Ordered startup and bounded shutdown of two owned processes and their trees."""
    if [name for name, _ in commands] != ["web", "worker"]:
        raise ValueError("Expected exactly one web process followed by one worker")
    if min(startup_seconds, grace_seconds, terminate_seconds) < 0:
        raise ValueError("Timeouts must be non-negative")
    requested = False
    children = []
    descendants = {}
    job = WindowsJob()
    signals = [signal.SIGINT, signal.SIGTERM]
    if os.name == "nt":
        signals.append(signal.SIGBREAK)
    previous = {sig: signal.getsignal(sig) for sig in signals}

    def request_stop(_signum, _frame):
        nonlocal requested
        requested = True

    def stopped():
        return requested or stop_requested()

    def failure():
        for name, child in children:
            code = child.poll()
            if code is not None:
                raise RuntimeError(
                    f"The {name} service stopped (exit {code}). See log/research-desktop-{name}.log."
                )

    def wait_until(predicate, timeout):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            _remember_descendants(children, descendants)
            time.sleep(0.1)
        return predicate()

    try:
        for sig in previous:
            signal.signal(sig, request_stop)
        for name, command in commands:
            if stopped():
                return 0
            options = (
                {"start_new_session": True}
                if os.name == "posix"
                else {
                    "creationflags": subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | 0x00000004,
                }
            )
            child = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **options,
            )
            children.append((name, child))
            job.assign(child)
            job.resume(child)
            # Production child code does no app imports/spawns before job assignment.
            (runtime_dir / f"{name}.start").touch()
            deadline = time.monotonic() + startup_seconds
            while True:
                if stopped():
                    return 0
                failure()
                _remember_descendants(children, descendants)
                if ready is None or ready(name):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"The {name} service did not become ready. See log/research-desktop-{name}.log."
                    )
                time.sleep(0.1)
        failure()
        on_ready()
        while not stopped():
            failure()
            _remember_descendants(children, descendants)
            time.sleep(0.1)
        return 0
    finally:
        try:
            checkpoint = True
            for name in ("worker", "web"):
                try:
                    (runtime_dir / f"{name}.stop").touch()
                except OSError:
                    checkpoint = False
            _remember_descendants(children, descendants)
            wait_until(
                lambda: all(child.poll() is not None for _, child in children),
                grace_seconds if checkpoint else 0,
            )
            _signal_children(children, descendants)
            wait_until(
                lambda: all(child.poll() is not None for _, child in children), terminate_seconds
            )
        finally:
            # Closing a Windows Job kills even descendants whose parent exited.
            job.close()
            _signal_children(children, descendants, force=True)
            for _, child in children:
                child.wait(timeout=5)
                # Windows Popen retains a kernel handle after wait; close it now.
                if os.name == "nt":
                    child._handle.Close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def child_main(name, runtime_dir):
    gate = runtime_dir / f"{name}.start"
    deadline = time.monotonic() + 30
    while not gate.exists():
        if time.monotonic() > deadline:
            return 1
        time.sleep(0.02)
    # Delay imports until the process belongs to its launcher's Job / process group.
    with RotatingOutput(ROOT / "log" / f"research-desktop-{name}.log") as output:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                if name == "web":
                    from tools.research_desktop_web import run

                    run(runtime_dir)
                else:
                    from services.scanner_research_worker import main as worker_main

                    sys.argv = [
                        "scanner_research_worker",
                        "--stop-file",
                        str(runtime_dir / "worker.stop"),
                        "--ready-file",
                        str(runtime_dir / "worker.ready"),
                    ]
                    worker_main()
                return 0
            except SystemExit as error:
                return error.code if isinstance(error.code, int) else 1
            except BaseException:
                import traceback

                traceback.print_exc()
                return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help="Override the local web port for this launch")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop-file", type=Path, help="Stop gracefully when this file is created")
    parser.add_argument("--child", choices=("web", "worker"), help=argparse.SUPPRESS)
    parser.add_argument("--runtime-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        if args.runtime_dir is None:
            parser.error("Missing child runtime directory")
        return child_main(args.child, args.runtime_dir)
    if not (ROOT / ".env").is_file():
        print("Run Setup first to prepare this installation.", file=sys.stderr)
        return 1
    try:
        environment, port = installation_environment(ROOT / ".env", os.environ, args.port)
        (ROOT / "log").mkdir(exist_ok=True)
        (ROOT / "tmp").mkdir(exist_ok=True)
        with InstanceLock(ROOT / "tmp" / "research-desktop.lock"):
            check_ports(
                [
                    ("website", port),
                    ("WebSocket", int(environment.get("WEBSOCKET_PORT", "8765"))),
                    ("market-data", int(environment.get("ZMQ_PORT", "5555"))),
                ]
            )
            with tempfile.TemporaryDirectory(
                prefix="research-desktop-", dir=ROOT / "tmp"
            ) as directory:
                runtime_dir = Path(directory)
                environment["RESEARCH_DESKTOP_TOKEN"] = secrets.token_hex(24)
                commands = [
                    (
                        name,
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--child",
                            name,
                            "--runtime-dir",
                            str(runtime_dir),
                        ],
                    )
                    for name in ("web", "worker")
                ]
                url = f"http://127.0.0.1:{port}/scanner-research"

                def ready(name):
                    return (
                        web_ready(port, environment["RESEARCH_DESKTOP_TOKEN"])
                        if name == "web"
                        else (runtime_dir / "worker.ready").exists()
                    )

                def opened():
                    print(
                        f"OpenAlgo Research is ready: {url}\nKeep this window open. Press Ctrl+C to stop safely.",
                        flush=True,
                    )
                    if not args.no_browser:
                        try:
                            webbrowser.open(url)
                        except Exception:
                            print("Open the address above in your browser.", flush=True)

                print("Starting OpenAlgo Research...", flush=True)
                return supervise(
                    commands,
                    runtime_dir,
                    cwd=ROOT,
                    environment=environment,
                    ready=ready,
                    on_ready=opened,
                    stop_requested=lambda: bool(args.stop_file and args.stop_file.exists()),
                )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"OpenAlgo Research could not start: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
