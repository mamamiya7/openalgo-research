"""Keep scoped artifact validation explicit without weakening normal CI."""

from pathlib import Path

import pytest
import yaml

from tools.research_test_slice import partition


def workflow():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/research-distribution.yml"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_engine_matrix_can_only_be_skipped_by_explicit_manual_package_request():
    config = workflow()
    triggers = config["on"]
    choice = triggers["workflow_dispatch"]["inputs"]["validation_scope"]
    assert choice["default"] == "full"
    assert choice["options"] == ["full", "package"]
    assert triggers["push"]["branches"] == ["main"]
    assert "pull_request" in triggers
    assert config["jobs"]["supported-runtime"]["if"] == (
        "${{ github.event_name != 'workflow_dispatch' || inputs.validation_scope != 'package' }}"
    )


def test_package_scope_retains_exact_artifact_installation_and_discloses_skipped_engines():
    jobs = workflow()["jobs"]
    package = jobs["package"]
    assert "if" not in package
    steps = package["steps"]
    disclosure = next(step for step in steps if step.get("name") == "Record validation scope")
    assert "engine matrix is deliberately skipped" in disclosure["run"]
    assert "does not establish engine/runtime compatibility" in disclosure["run"]
    assert any("npm audit --audit-level=high" in step.get("run", "") for step in steps)
    assert any("tools/research_release.py" in step.get("run", "") for step in steps)
    fresh = jobs["fresh-install"]
    assert fresh["needs"] == "package"
    assert "if" not in fresh
    assert fresh["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    assert any("actions/download-artifact@" in step.get("uses", "") for step in fresh["steps"])
    assert any(
        "tools/research_install_smoke.py --archive" in step.get("run", "")
        for step in fresh["steps"]
    )


def test_windows_slices_cover_every_module_once_and_include_future_modules(tmp_path):
    directory = tmp_path / "test/research"
    directory.mkdir(parents=True)
    for name in ("test_orders.py", "test_saved_evidence.py", "test_future_feature.py"):
        (directory / name).touch()
    (tmp_path / "test/test_history_service_source_validation.py").touch()
    expected = partition(tmp_path, 0, 1)
    parts = [partition(tmp_path, index, 4) for index in range(4)]
    flattened = [name for part in parts for name in part]
    assert sorted(flattened) == sorted(expected)
    assert len(flattened) == len(set(flattened))
    assert "test/research/test_future_feature.py" in flattened
    assert "test/test_history_service_source_validation.py" in flattened


@pytest.mark.parametrize("index,count", [(0, 0), (-1, 4), (4, 4), (0, 5)])
def test_invalid_slice_cannot_silently_skip_the_suite(tmp_path, index, count):
    with pytest.raises(ValueError, match="Test slice"):
        partition(tmp_path, index, count)


def test_ci_runs_the_complete_windows_partition_and_full_linux_suite():
    job = workflow()["jobs"]["supported-runtime"]
    matrix = job["strategy"]["matrix"]["include"]
    windows = [row for row in matrix if row["os"] == "windows-latest"]
    assert [(row["slice_index"], row["slice_count"]) for row in windows] == [
        (str(index), "4") for index in range(4)
    ]
    assert [row for row in matrix if row["os"] == "ubuntu-latest"] == [
        {"os": "ubuntu-latest", "slice_index": "0", "slice_count": "1"}
    ]
    step = next(step for step in job["steps"] if step.get("name", "").startswith("Engine,"))
    assert "research_test_slice.py" in step["run"]
    assert "matrix.slice_index" in step["run"] and "matrix.slice_count" in step["run"]
    assert "if" not in step
