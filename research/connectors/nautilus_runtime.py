"""File-bounded calculation boundary for Nautilus's independent dependencies."""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from research.connectors.nautilus_portfolio import TESTED_VERSION
from tools.research_engine_worker import clean_environment, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 128 * 1024 * 1024
MAX_SECONDS = 1800


def configuration():
    path = Path(
        os.getenv("RESEARCH_NAUTILUS_CONFIG", str(ROOT / "research_data/nautilus-runtime.json"))
    )
    if path.is_file():
        value = read_json(path, 8192)
        if not isinstance(value, dict) or set(value) - {"transport", "python", "distribution"}:
            raise ValueError("Invalid Nautilus runtime configuration")
    else:
        value = {
            "transport": "local",
            "python": str(ROOT / "research/runtimes/nautilus/.venv/bin/python"),
        }
    if value.get("transport") not in ("local", "wsl") or not isinstance(value.get("python"), str):
        raise ValueError("Install this release's Nautilus runtime first")
    if (
        not value["python"]
        or len(value["python"]) > 4096
        or any(c in value["python"] for c in "\0\r\n")
    ):
        raise ValueError("Invalid Nautilus runtime configuration")
    if value["transport"] == "wsl":
        distribution = value.get("distribution")
        if (
            os.name != "nt"
            or not isinstance(distribution, str)
            or not distribution
            or len(distribution) > 128
            or any(c in distribution for c in "\0\r\n")
            or not value["python"].startswith("/")
        ):
            raise ValueError("Invalid WSL Nautilus runtime configuration")
    elif os.name == "nt" or not Path(value["python"]).is_file():
        raise ValueError(
            "Nautilus needs the included Linux runtime; on Windows install it through WSL"
        )
    return value


def status():
    try:
        configuration()
        return {"available": True, "tested_version": TESTED_VERSION}
    except (ValueError, OSError):
        return {
            "available": False,
            "tested_version": TESTED_VERSION,
            "reason": "Install the Nautilus runtime to enable this engine",
        }


def _path(path, config):
    value = str(Path(path).resolve())
    if config["transport"] == "wsl":
        if len(value) < 3 or value[1:3] != ":\\":
            raise ValueError("WSL calculation files must be on a local Windows drive")
        return "/mnt/" + value[0].lower() + "/" + value[3:].replace("\\", "/")
    return value


def _command(config, directory, nonce, *, stop=None):
    prefix = []
    if config["transport"] == "wsl":
        # WSL can inject distribution/WSLENV settings; clear them before Python starts.
        prefix = [
            "wsl.exe",
            "--distribution",
            config["distribution"],
            "--exec",
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "PYTHON_DOTENV_DISABLED=1",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONUNBUFFERED=1",
        ]
    return [
        *prefix,
        config["python"],
        "-I",
        _path(ROOT / "tools/research_engine_worker.py", config),
        *(["--stop", str(stop)] if stop else []),
        _path(directory, config),
        nonce,
    ]


def _stop(process, config, directory, nonce):
    if process.poll() is not None:
        process.wait()
        return
    try:
        (directory / "cancel").touch()
    except OSError:
        pass
    # A separate Linux supervisor checks this file even when native calculations hang.
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    for sig in (15, 9):
        try:
            subprocess.run(
                _command(config, directory, nonce, stop=sig),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=clean_environment(),
                timeout=10,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # Never let malformed metadata or a failed helper skip wrapper reaping.
            pass
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
    try:
        process.kill()
    except OSError:
        pass
    process.wait(timeout=5)


def evaluate(strategies, snapshot, capital, *, progress=None):
    from research.connectors.nautilus_portfolio import validate

    validate(strategies, snapshot, capital)
    config = configuration()
    parent = ROOT / ".agent-native/runtime-jobs"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nautilus-", dir=parent) as temp:
        directory, nonce = Path(temp), uuid.uuid4().hex
        write_json(
            directory / "input.json",
            {"strategies": strategies, "snapshot": snapshot, "capital": capital},
            MAX_BYTES,
        )
        process = subprocess.Popen(
            _command(config, directory, nonce),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=clean_environment(),
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        began, previous = time.monotonic(), None
        try:
            while process.poll() is None:
                if time.monotonic() - began > MAX_SECONDS:
                    raise ValueError(
                        "Nautilus calculation exceeded 30 minutes; reduce the run size"
                    )
                if progress:
                    progress_path = directory / "progress.json"
                    if progress_path.is_file():
                        previous = read_json(progress_path, 1024)
                        if (
                            not isinstance(previous, list)
                            or len(previous) != 2
                            or not all(
                                type(v) in (int, float) and math.isfinite(v) and v >= 0
                                for v in previous
                            )
                        ):
                            raise ValueError("Invalid Nautilus calculation progress")
                    # This callback renews the owning job lease and checks cancellation.
                    progress(*(previous or [0, 1]))
                time.sleep(0.1)
            process.wait()
            output = directory / "output.json"
            if not output.is_file():
                raise ValueError(
                    "Nautilus could not finish this calculation; check the installed runtime"
                )
            report = read_json(output, MAX_BYTES)
            if not isinstance(report, dict):
                raise ValueError("Invalid Nautilus calculation result")
            if report.get("error"):
                raise ValueError(str(report["error"])[:2000])
            if process.returncode or report.get("version") != TESTED_VERSION:
                raise ValueError("Nautilus runtime does not match this release")
            if not isinstance(report.get("result"), dict):
                raise ValueError("Invalid Nautilus calculation result")
            return report["result"]
        finally:
            _stop(process, config, directory, nonce)
