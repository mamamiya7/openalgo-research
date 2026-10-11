"""Run a deterministic, complete partition of the isolated research regression suite.

Each CI slice runs on its own runner, including its own test databases. Linux
uses one slice; Windows spreads the same complete suite over four runners.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def partition(root: Path, index: int, count: int) -> list[str]:
    if not 1 <= count <= 4 or not 0 <= index < count:
        raise ValueError("Test slice requires 1–4 parts and an index within those parts")
    modules = sorted(
        path.relative_to(root).as_posix() for path in (root / "test/research").rglob("test_*.py")
    )
    history = "test/test_history_service_source_validation.py"
    if not modules or not (root / history).is_file():
        raise ValueError("Research test inventory is incomplete")
    modules.append(history)
    return modules[index::count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        modules = partition(root, args.index, args.count)
    except ValueError as error:
        parser.error(str(error))
    print(f"Research slice {args.index + 1}/{args.count}: {len(modules)} test modules", flush=True)
    sys.path.insert(0, str(root))
    import pytest

    return int(pytest.main([*modules, "-q"]))


if __name__ == "__main__":
    raise SystemExit(main())
