"""Supervise the native OpenAlgo research distribution on Linux/macOS.

The container runs this under tini. Children inherit the container log streams;
there are no output pipes or per-job threads in this process. Calculations remain
in the existing bounded research worker, outside Gunicorn/eventlet.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_START_GUIDANCE = (
    "This managed supervisor requires Linux/macOS. On Windows, start "
    "'uv run --no-sync python app.py' and "
    "'uv run --no-sync python -m services.scanner_research_worker' "
    "in separate terminals; see docs/research/RUNTIME.md."
)


def commands(python: str, port: int, runtime_dir: Path) -> list[tuple[str, list[str]]]:
    """One proxy, one calculation worker and one eventlet web worker."""
    return [
        ("market-data", [python, "-m", "websocket_proxy.server"]),
        (
            "research",
            [
                python,
                "-m",
                "services.scanner_research_worker",
                "--stop-file",
                str(runtime_dir / "worker.stop"),
            ],
        ),
        (
            "web",
            [
                python,
                "-m",
                "gunicorn",
                "--worker-class",
                "eventlet",
                "--workers",
                "1",
                "--bind",
                f"0.0.0.0:{port}",
                "--timeout",
                "300",
                "--graceful-timeout",
                "30",
                "--worker-tmp-dir",
                str(runtime_dir),
                "--no-control-socket",
                "--log-level",
                "warning",
                "app:app",
            ],
        ),
    ]


def _signal_group(child: subprocess.Popen, sig: int) -> None:
    # start_new_session gives each owned child a private group. Signal the group
    # even if its leader has exited, so a failed launcher cannot orphan workers.
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


def _stop_children(
    children: list[tuple[str, subprocess.Popen]],
    stop_file: Path,
    grace_seconds: float,
    terminate_seconds: float,
) -> None:
    """Allow a checkpoint, then terminate/kill and reap every direct child."""
    try:
        stop_file.write_text("stop\n", encoding="utf-8")
    except OSError as error:
        # An unwritable disk must not bypass process cleanup.
        print(f"[OpenAlgo] Could not request a research checkpoint: {error}", file=sys.stderr)
        grace_seconds = 0
    for name, child in children:
        if name != "research":
            # Gunicorn's master owns graceful shutdown of its web worker.
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
    deadline = time.monotonic() + grace_seconds
    while any(child.poll() is None for _, child in children) and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    # Signal whole groups, including surviving descendants of an exited leader.
    for _, child in children:
        _signal_group(child, signal.SIGTERM)
    deadline = time.monotonic() + terminate_seconds
    while any(child.poll() is None for _, child in children) and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    for _, child in children:
        _signal_group(child, signal.SIGKILL)
    for _, child in children:
        # SIGKILL is the final bounded escalation. wait() reaps each Popen child;
        # tini reaps orphaned grandchildren when this runs as a container service.
        child.wait()


def supervise(
    child_commands: Sequence[tuple[str, Sequence[str]]],
    stop_file: Path,
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    grace_seconds: float = 35,
    terminate_seconds: float = 5,
    stop_requested: Callable[[], bool] = lambda: False,
) -> int:
    """Stop the complete service on a signal, startup error or any child exit.

    Only three service entries are admitted; retained process state is constant.
    No child is automatically restarted independently of the shared service.
    """
    if os.name != "posix":
        raise RuntimeError(WINDOWS_START_GUIDANCE)
    names = [name for name, _ in child_commands]
    if not names or len(names) > 3 or len(set(names)) != len(names):
        raise ValueError("At most three uniquely named service processes are supported")
    if grace_seconds < 0 or terminate_seconds < 0:
        raise ValueError("Shutdown timeouts must be non-negative")
    requested = False

    def request_stop(_signum, _frame):
        nonlocal requested
        requested = True

    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    children: list[tuple[str, subprocess.Popen]] = []
    try:
        for sig in old_handlers:
            signal.signal(sig, request_stop)
        for name, command in child_commands:
            if requested or stop_requested():
                return 0
            child = subprocess.Popen(
                list(command), cwd=cwd, env=environment, start_new_session=True
            )
            children.append((name, child))
            print(f"[OpenAlgo] Started {name}", flush=True)
        while not (requested or stop_requested()):
            for name, child in children:
                code = child.poll()
                if code is not None:
                    print(f"[OpenAlgo] {name} stopped (exit {code}); stopping services", flush=True)
                    return code if 0 < code < 126 else 1
            time.sleep(0.1)
        return 0
    finally:
        try:
            _stop_children(children, stop_file, grace_seconds, terminate_seconds)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")))
    args = parser.parse_args()
    if os.name != "posix":
        parser.error(WINDOWS_START_GUIDANCE)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    environment = dict(os.environ)
    environment["APP_MODE"] = "standalone"  # The supervisor already owns the proxy.
    data = Path(environment.get("RESEARCH_DATA_DIR", str(ROOT / "research_data")))
    if not data.is_absolute():
        data = ROOT / data
    data.mkdir(parents=True, exist_ok=True)
    environment["RESEARCH_DATA_DIR"] = str(data.resolve())
    runtime_root = ROOT / "tmp"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="research-runtime-", dir=runtime_root) as directory:
        runtime_dir = Path(directory)
        return supervise(
            commands(sys.executable, args.port, runtime_dir),
            runtime_dir / "worker.stop",
            cwd=ROOT,
            environment=environment,
        )


if __name__ == "__main__":
    raise SystemExit(main())
