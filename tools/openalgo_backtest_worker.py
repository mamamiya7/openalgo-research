"""Offline optional-engine entry point; no account, app or environment imports."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research_data/openalgo-backtest-sdk"))


def main():
    from research.connectors.openalgo_portfolio import TESTED_VERSION, evaluate
    from tools.research_engine_worker import read_json, write_json

    def offline(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen"}:
            raise RuntimeError("Optional OpenAlgo calculation is saved-data only")

    sys.addaudithook(offline)
    if sys.argv[1:] == ["--probe"]:
        from importlib import metadata

        import openalgo.backtest as bt

        if metadata.version("openalgo") != TESTED_VERSION:
            raise ValueError("Optional SDK version mismatch")
        bt.calendar.Calendar(builtin=False)
        print(f"OpenAlgo SDK {TESTED_VERSION}: native Rust kernel available; network disabled")
        return 0

    directory = Path(sys.argv[1]).resolve()
    # The worker is private and accepts only the parent's owned scratch boundary.
    parent = (ROOT / ".agent-native/runtime-jobs").resolve()
    if directory.parent != parent or not directory.name.startswith("openalgo-"):
        raise ValueError("Invalid optional-engine scratch directory")
    try:
        # Supplied bars are the only permitted market input. Block all network
        # connections, even if future SDK defaults attempt history or telemetry.
        value = read_json(directory / "input.json")
        result = evaluate(value["strategies"], value["snapshot"], value["capital"])
        write_json(directory / "output.json", {"version": TESTED_VERSION, "result": result})
    except Exception as exc:
        write_json(directory / "output.json", {"version": TESTED_VERSION, "error": str(exc)[:2000]})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
