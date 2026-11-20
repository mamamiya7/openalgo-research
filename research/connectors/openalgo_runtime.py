"""Credential-free SDK overlay in a bounded, reaped local worker process."""

import os
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

from research.connectors.openalgo_portfolio import TESTED_VERSION, validate
from tools.research_engine_worker import clean_environment, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 128 * 1024 * 1024
MAX_SECONDS = 1800


def sdk_path():
    return ROOT / "research_data/openalgo-backtest-sdk"


def status():
    installed = None
    try:
        distributions = metadata.distributions(path=[str(sdk_path())])
        installed = next(
            (d.version for d in distributions if d.metadata["Name"] == "openalgo"), None
        )
    except (OSError, ValueError):
        pass
    package = sdk_path() / "openalgo"
    native_files = list(package.glob("_oaindicators*.pyd")) + list(
        package.glob("_oaindicators*.so")
    )
    available = (
        installed == TESTED_VERSION
        and bool(native_files)
        and (package / "backtest/run.py").is_file()
    )
    return {
        "available": available,
        "installed_version": installed,
        "tested_version": TESTED_VERSION,
        **(
            {}
            if available
            else {
                "reason": "Install the optional OpenAlgo backtest SDK with tools/install_openalgo_backtest.py"
            }
        ),
    }


def evaluate(strategies, snapshot, capital, *, progress=None):
    validate(strategies, snapshot, capital)
    if not status()["available"]:
        raise ValueError(status()["reason"])
    parent = ROOT / ".agent-native/runtime-jobs"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openalgo-", dir=parent) as temp:
        directory = Path(temp)
        write_json(
            directory / "input.json",
            {"strategies": strategies, "snapshot": snapshot, "capital": capital},
            MAX_BYTES,
        )
        with (directory / "worker.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    str(ROOT / "tools/openalgo_backtest_worker.py"),
                    str(directory),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=clean_environment(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            began = time.monotonic()
            try:
                while process.poll() is None:
                    if time.monotonic() - began > MAX_SECONDS:
                        raise ValueError(
                            "OpenAlgo calculation exceeded its 30-minute runtime bound"
                        )
                    if progress:
                        # Lease renewal/cancellation remains owned by the existing job.
                        progress(0, 1)
                    time.sleep(0.1)
                process.wait()
                output = directory / "output.json"
                if not output.is_file():
                    raise ValueError(
                        "Optional OpenAlgo worker could not finish; check its SDK installation"
                    )
                result = read_json(output, MAX_BYTES)
                if not isinstance(result, dict) or result.get("version") != TESTED_VERSION:
                    raise ValueError("Optional OpenAlgo worker version mismatch")
                if result.get("error"):
                    raise ValueError(str(result["error"])[:2000])
                if process.returncode or not isinstance(result.get("result"), dict):
                    raise ValueError("Invalid optional OpenAlgo calculation result")
                if progress:
                    progress(1, 1)
                return result["result"]
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
