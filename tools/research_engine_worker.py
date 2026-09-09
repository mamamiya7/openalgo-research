"""Standalone Nautilus supervisor and calculation child; no web/account imports."""

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 128 * 1024 * 1024
MAX_ERROR = 2000


def clean_environment():
    """Pass only operating-system paths, never application or broker settings."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA"}
    }
    environment.update(
        PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1"
    )
    return environment


def read_json(path, limit=MAX_BYTES):
    # Read a bounded amount even if a writer replaces/grows the file after stat.
    with Path(path).open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Nautilus calculation file exceeds its size limit")

    def invalid_number(_value):
        raise ValueError("Non-finite value in Nautilus calculation file")

    return json.loads(payload, parse_constant=invalid_number)


def write_json(path, value, limit=MAX_BYTES):
    """Publish complete JSON atomically, with a strict bound during encoding."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=path.name + ".", delete=False
        ) as handle:
            temporary = Path(handle.name)
            written = 0
            encoder = json.JSONEncoder(allow_nan=False, separators=(",", ":"))
            for chunk in encoder.iterencode(value):
                encoded = chunk.encode("utf-8")
                written += len(encoded)
                if written > limit:
                    raise ValueError("Nautilus calculation file exceeds its size limit")
                handle.write(encoded)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stop_process(process, *, grace=2):
    """Reap an owned Linux process group on every normal/error exit path."""
    if process.poll() is not None:
        process.wait()
        return
    for sig, timeout in ((signal.SIGTERM, grace), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except OSError:
            # A group can exit between poll and kill; still reap its leader.
            pass
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
    try:
        process.kill()
    except OSError:
        pass
    process.wait(timeout=5)


def _identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "group": os.getpgid(pid), "start": fields[19]}
    except (OSError, IndexError):
        return None


def stop_recorded(directory, nonce, sig):
    """Fallback: signal only processes whose launch token AND Linux identity match."""
    try:
        record = read_json(directory / "process.json", 8192)
        if not isinstance(record, dict) or record.get("nonce") != nonce:
            return
        processes = record.get("processes")
        if not isinstance(processes, list) or len(processes) > 2:
            return
        for entry in reversed(processes):
            if (
                not isinstance(entry, dict)
                or type(entry.get("pid")) is not int
                or entry["pid"] <= 1
            ):
                continue
            pid = entry["pid"]
            if _identity(pid) != entry or entry.get("group") != pid:
                continue
            arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if str(directory).encode() not in arguments or nonce.encode() not in arguments:
                continue
            # The supervisor remains alive to reap its child after a forced kill.
            is_supervisor = b"--calculate" not in arguments
            os.killpg(pid, signal.SIGTERM if is_supervisor else sig)
    except (OSError, ValueError, TypeError):
        return


def supervise(directory, nonce, *, command=None, deadline=1800, grace=2):
    """The supervisor stays responsive when a native numerical call is hung."""
    if sys.platform != "linux":
        raise ValueError("This Nautilus runtime requires Linux")
    directory = Path(directory).resolve()
    if os.getpgrp() != os.getpid():
        os.setsid()
    stopped = False

    def request_stop(*_args):
        nonlocal stopped
        stopped = True

    old_handlers = {
        sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    child = None
    try:
        record = {"nonce": nonce, "processes": [_identity(os.getpid())]}
        write_json(directory / "process.json", record, 8192)
        if (directory / "cancel").exists():
            raise ValueError("Calculation cancelled")
        child = subprocess.Popen(
            command
            or [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--calculate",
                str(directory),
                nonce,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=clean_environment(),
            start_new_session=True,
        )
        identity = _identity(child.pid)
        if identity:
            record["processes"].append(identity)
        write_json(directory / "process.json", record, 8192)
        began = time.monotonic()
        while child.poll() is None:
            if stopped or not directory.is_dir() or (directory / "cancel").exists():
                raise ValueError("Calculation cancelled")
            if time.monotonic() - began > deadline:
                raise ValueError("Nautilus calculation exceeded 30 minutes; reduce the run size")
            time.sleep(0.1)
        return child.wait()
    finally:
        try:
            if child is not None:
                stop_process(child, grace=grace)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def calculate(directory):
    directory = Path(directory).resolve()
    from research.connectors.nautilus_portfolio import TESTED_VERSION, evaluate

    required = {
        "nautilus-trader": TESTED_VERSION,
        "numpy": "2.4.4",
        "pandas": "2.3.3",
        "pyarrow": "25.0.1",
    }
    if sys.version_info[:2] != (3, 12) or any(
        metadata.version(key) != value for key, value in required.items()
    ):
        raise ValueError("Install the locked Nautilus dependencies for this release")
    last = 0

    def progress(done, total):
        nonlocal last
        if (directory / "cancel").exists():
            raise ValueError("Calculation cancelled")
        if not all(
            type(value) in (int, float) and math.isfinite(value) and value >= 0
            for value in (done, total)
        ):
            raise ValueError("Invalid Nautilus calculation progress")
        if time.monotonic() - last < 0.2 and done != total:
            return
        write_json(directory / "progress.json", [done, total], 1024)
        last = time.monotonic()

    request = read_json(directory / "input.json")
    if not isinstance(request, dict) or set(request) != {"strategies", "snapshot", "capital"}:
        raise ValueError("Invalid Nautilus calculation request")
    return {"version": TESTED_VERSION, "result": evaluate(**request, progress=progress)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calculate", action="store_true")
    parser.add_argument("--stop", type=int, choices=[15, 9])
    parser.add_argument("directory", type=Path)
    parser.add_argument("nonce")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.stop:
        stop_recorded(directory, args.nonce, args.stop)
        return 0
    try:
        if args.calculate:
            write_json(directory / "output.json", calculate(directory))
            return 0
        return supervise(directory, args.nonce)
    except Exception as exc:
        write_json(directory / "output.json", {"error": str(exc)[:MAX_ERROR]}, 8192)
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    environment = clean_environment()
    os.environ.clear()
    os.environ.update(environment)
    raise SystemExit(main())
