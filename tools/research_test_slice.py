"""Run a deterministic, complete partition of the isolated research regression suite.

Each CI slice runs on its own runner, including its own test databases. Current
Linux and Windows release jobs each use eight balanced slices. The historic
one/four-slice partition stays unchanged for explicitly qualified rechecks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Approximate module completion deltas from release run 39585585902 (21 October
# 2026), in seconds, taking the larger observed platform value. They are scheduling
# hints, not test timeouts or exclusions. Unknown/future modules still enter the
# complete inventory with a conservative scheduling weight. JUnit/durations output
# retains fresh measurements so this small table can be reviewed deliberately.
MODULE_SECONDS = {
    "test_intelligent_workflow.py": 1230,
    "test_portfolio_allocation_replay.py": 403,
    "test_automatic_walk_forward.py": 359,
    "test_automatic_hypotheses.py": 302,
    "test_portfolio_seed_integration.py": 252,
    "test_automatic_workflow.py": 212,
    "test_automatic_trailing.py": 212,
    "test_execution_aware_research.py": 197,
    "test_priority_integration.py": 189,
    "test_automatic_timing.py": 175,
    "test_automatic_condition_workflow.py": 171,
    "test_expanded_walk_forward.py": 129,
    "test_portfolio_capacity_recommendation.py": 127,
    "test_execution_core_integration.py": 123,
    "test_automatic_calculation_activity.py": 116,
    "test_matched_baselines.py": 113,
    "test_validation_workflow.py": 99,
    "test_method_expansion_integration.py": 92,
    "test_chosen_setups.py": 89,
    "test_development_evidence_integration.py": 80,
}


def partition(root: Path, index: int, count: int) -> list[str]:
    if not 1 <= count <= 8 or not 0 <= index < count:
        raise ValueError("Test slice requires 1–8 parts and an index within those parts")
    modules = sorted(
        path.relative_to(root).as_posix() for path in (root / "test/research").rglob("test_*.py")
    )
    history = "test/test_history_service_source_validation.py"
    if not modules or not (root / history).is_file():
        raise ValueError("Research test inventory is incomplete")
    modules.append(history)
    if count <= 4:
        return modules[index::count]
    parts: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    for module in sorted(
        modules, key=lambda name: (-MODULE_SECONDS.get(Path(name).name, 15), name)
    ):
        target = min(range(count), key=lambda candidate: (loads[candidate], candidate))
        parts[target].append(module)
        loads[target] += MODULE_SECONDS.get(Path(module).name, 15)
    return sorted(parts[index])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--junit-xml", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        modules = partition(root, args.index, args.count)
    except ValueError as error:
        parser.error(str(error))
    print(f"Research slice {args.index + 1}/{args.count}: {len(modules)} test modules", flush=True)
    if not modules:
        # pytest with no explicit paths would collect the entire repository.
        print("No modules assigned to this slice; the complete inventory is in the other slices.")
        return 0
    sys.path.insert(0, str(root))
    import pytest

    options = [*modules, "-q", "--durations=20"]
    if args.junit_xml is not None:
        args.junit_xml.parent.mkdir(parents=True, exist_ok=True)
        options.append(f"--junitxml={args.junit_xml}")
    return int(pytest.main(options))


if __name__ == "__main__":
    raise SystemExit(main())
