"""Install the locked Linux calculation environment, including Windows WSL.

WSL stages only the two pinned dependency manifests into a Linux cache directory.
The application, account data and broker credentials stay in the Windows checkout.
"""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.research_engine_worker import clean_environment, stop_process, write_json

TESTED_VERSION = "1.231.0"


def _project(root, wsl_distribution, runtime_dir):
    if wsl_distribution:
        if not str(root).startswith("/mnt/") or len(root.parts) < 4 or len(root.parts[2]) != 1:
            raise ValueError("A Windows-hosted checkout must be accessible under /mnt/<drive>")
        if (
            not isinstance(wsl_distribution, str)
            or len(wsl_distribution) > 128
            or any(c in wsl_distribution for c in "\0\r\n")
        ):
            raise ValueError("Invalid WSL distribution name")
    if runtime_dir is not None:
        project = Path(runtime_dir).expanduser().resolve()
    elif wsl_distribution:
        key = hashlib.sha256(str(root).encode()).hexdigest()[:16]
        project = Path.home() / ".cache/openalgo-research" / key / "nautilus"
    else:
        project = root / "research/runtimes/nautilus"
    if wsl_distribution and str(project).startswith("/mnt/"):
        raise ValueError(
            "Choose a Linux runtime directory, for example ~/.cache/openalgo-research/nautilus"
        )
    return project


def _stage(source, destination):
    """Copy manifests only, atomically and with a bounded temporary file."""
    if source.resolve() == destination.resolve():
        return
    for name in ("pyproject.toml", "uv.lock"):
        with (source / name).open("rb") as handle:
            contents = handle.read(2 * 1024 * 1024 + 1)
        if len(contents) > 2 * 1024 * 1024:
            raise ValueError("Invalid Nautilus dependency manifest")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination, prefix=name + ".", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(contents)
            temporary.replace(destination / name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _run(command, timeout, label):
    # Child logs are deliberately discarded; the installer publishes a small status
    # receipt instead of retaining unbounded package-manager/native-library output.
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=clean_environment(),
    )
    try:
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"{label} exceeded {timeout} seconds") from exc
        if code:
            raise ValueError(f"{label} failed (exit {code})")
    finally:
        stop_process(child)


def install(root=ROOT, *, uv=None, wsl_distribution=None, runtime_dir=None):
    if sys.platform != "linux":
        raise ValueError("Run this installer in Linux or your Windows WSL terminal")
    import fcntl

    root = Path(root).resolve()
    libc, version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, version.split(".")[:2])) < (2, 35):
        raise ValueError("Nautilus requires Linux with glibc 2.35 or newer")
    project = _project(root, wsl_distribution, runtime_dir)
    uv = uv or shutil.which("uv")
    if not uv:
        raise ValueError("Install uv in this Linux environment, then run this installer again")
    uv = str(Path(uv).resolve())
    project.mkdir(parents=True, exist_ok=True)
    output = root / ".agent-native/nautilus-install"
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / "install.log"
    with (project / ".install.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("A Nautilus installation is already running for this runtime") from exc
        try:
            write_json(receipt, {"status": "installing", "runtime": str(project)}, 8192)
            _stage(root / "research/runtimes/nautilus", project)
            _run(
                [
                    uv,
                    "--no-config",
                    "sync",
                    "--project",
                    str(project),
                    "--python",
                    "3.12",
                    "--frozen",
                    "--no-dev",
                    "--quiet",
                ],
                900,
                "Nautilus dependency installation",
            )
            python = project / ".venv/bin/python"
            probe = (
                "import sys; from importlib import metadata; "
                "from nautilus_trader.backtest.engine import BacktestEngine; "
                "assert sys.version_info[:2] == (3,12); "
                f"assert metadata.version('nautilus-trader') == '{TESTED_VERSION}'; "
                "assert metadata.version('numpy') == '2.4.4'; "
                "assert metadata.version('pandas') == '2.3.3'; "
                "assert metadata.version('pyarrow') == '25.0.1'"
            )
            _run([str(python), "-I", "-c", probe], 60, "Nautilus startup check")
            config = {"transport": "wsl" if wsl_distribution else "local", "python": str(python)}
            if wsl_distribution:
                config["distribution"] = wsl_distribution
            destination = root / "research_data/nautilus-runtime.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_json(destination, config, 8192)
            result = {
                "installed": True,
                "engine": "NautilusTrader",
                "version": TESTED_VERSION,
                "configuration": str(destination),
                "runtime": str(project),
            }
            write_json(receipt, {"status": "complete", **result}, 8192)
            return result
        except Exception as exc:
            write_json(receipt, {"status": "failed", "error": str(exc)[:2000]}, 8192)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", help="Path to the Linux uv executable")
    parser.add_argument(
        "--wsl-distribution", help="WSL distribution running a Windows-hosted OpenAlgo installation"
    )
    parser.add_argument("--runtime-dir", help="Optional runtime directory on the Linux filesystem")
    args = parser.parse_args()
    print(
        json.dumps(
            install(
                uv=args.uv, wsl_distribution=args.wsl_distribution, runtime_dir=args.runtime_dir
            )
        )
    )
