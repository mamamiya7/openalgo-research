"""Install only the optional SDK overlay; never upgrade the main application's SDK."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check only; install nothing")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from research.connectors.openalgo_runtime import sdk_path, status
    from tools.research_engine_worker import clean_environment

    def check_native():
        subprocess.run(
            [sys.executable, "-I", str(ROOT / "tools/openalgo_backtest_worker.py"), "--probe"],
            check=True,
            timeout=30,
            env=clean_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

    state = status()
    if state["available"] or args.check:
        print(state)
        if state["available"]:
            check_native()
        return 0 if state["available"] else 1
    target = sdk_path()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(
            "Optional SDK directory is not empty. Review its contents before replacing an existing installation."
        )
    if shutil.disk_usage(ROOT).free < 512 * 1024**2:
        raise SystemExit("At least 512 MiB of free space is required before optional SDK setup")
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Use this app's uv dependency installer to install the optional SDK")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--target",
            str(target),
            "openalgo==2.2.0",
        ],
        check=True,
    )
    state = status()
    print(state)
    if state["available"]:
        check_native()
    return 0 if state["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
