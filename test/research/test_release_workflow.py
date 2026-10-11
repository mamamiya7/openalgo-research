"""Keep scoped artifact validation explicit without weakening normal CI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.research_test_slice import partition


def workflow():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/research-distribution.yml"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_complete_engine_matrix_is_skipped_only_by_explicit_manual_package_or_slice_request():
    config = workflow()
    triggers = config["on"]
    choice = triggers["workflow_dispatch"]["inputs"]["validation_scope"]
    assert choice["default"] == "full"
    assert choice["options"] == ["full", "package", "windows-slice", "runtime-followup"]
    assert triggers["push"]["branches"] == ["main"]
    assert "pull_request" in triggers
    assert config["jobs"]["supported-runtime"]["if"] == (
        "${{ github.event_name != 'workflow_dispatch' || inputs.validation_scope == 'full' }}"
    )


def test_package_scope_retains_exact_artifact_installation_and_discloses_skipped_engines():
    jobs = workflow()["jobs"]
    package = jobs["package"]
    assert package["if"] == (
        "${{ github.event_name != 'workflow_dispatch' || inputs.validation_scope == 'full' || inputs.validation_scope == 'package' }}"
    )
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


def test_slice_recheck_is_explicit_manual_bounded_and_keeps_other_qualification_alive():
    config = workflow()
    inputs = config["on"]["workflow_dispatch"]["inputs"]
    assert inputs["slice_index"]["options"] == ["0", "1", "2", "3"]
    assert inputs["slice_index"]["default"] == "3"
    assert inputs["qualified_source_sha"]["required"] == "true"
    assert inputs["qualified_source_sha"]["default"] == ("e24c066f7e8fe07965538715b843a69c6fc4fbb8")
    assert "inputs.validation_scope == 'windows-slice'" in config["concurrency"]["group"]
    assert "format('-windows-slice-{0}', inputs.slice_index)" in config["concurrency"]["group"]
    job = config["jobs"]["windows-slice"]
    assert job["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.validation_scope == 'windows-slice' }}"
    )
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == "45"
    assert job["env"]["OPENBLAS_NUM_THREADS"] == job["env"]["NUMBA_NUM_THREADS"] == "2"
    assert job["env"]["PYTHON_DOTENV_DISABLED"] == "1"
    checkout = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == "0"
    assert checkout["with"]["persist-credentials"] == "false"
    assert any(step.get("run") == "uv sync --frozen --extra research" for step in job["steps"])
    assert any("tools/research_check.py" in step.get("run", "") for step in job["steps"])
    assert any(
        "pytest test/research/test_release_workflow.py -q" in step.get("run", "")
        for step in job["steps"]
    )
    selected = next(
        step
        for step in job["steps"]
        if step.get("name") == "Recheck selected Windows research slice"
    )
    assert selected["shell"] == "python" and "subprocess.run(" in selected["run"]
    assert (
        "os.environ['RECHECK_SLICE_INDEX']" in selected["run"]
        and "'--count', '4'" in selected["run"]
    )
    assert "${{" not in selected["run"] and "shell=True" not in selected["run"]
    host = next(step for step in job["steps"] if step.get("name", "").startswith("Host upgrade"))
    full_host = next(
        step
        for step in config["jobs"]["supported-runtime"]["steps"]
        if step.get("name", "").startswith("Host upgrade")
    )
    assert host["if"] == "${{ inputs.slice_index == '0' }}"
    assert host["run"] == full_host["run"]


def recheck_guard():
    return next(
        step["run"]
        for step in workflow()["jobs"]["windows-slice"]["steps"]
        if step.get("name") == "Verify unchanged runtime and exact test partition"
    )


def isolated_environment():
    env = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")
        if key in os.environ
    }
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, PYTHONUTF8="1")
    return env


def git_at(root, *args):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Workflow Tests",
            "-c",
            "user.email=workflow@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=root,
        env=isolated_environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


@pytest.fixture
def qualified_repository(tmp_path):
    git_at(tmp_path, "init", "-q")
    files = {
        "test/research/test_a.py": "# Another slice\n",
        "test/research/test_b.py": "# Another slice\n",
        "test/research/test_c.py": "# Another slice\n",
        "test/research/test_existing.py": "def test_existing(): pass\n",
        "test/research/test_release_package.py": "# Corrected package contracts\n",
        "test/research/test_release_workflow.py": "# Explicit requalification contracts\n",
        "test/test_history_service_source_validation.py": "# Controlled test inventory\n",
        "tools/research_test_slice.py": (
            Path(__file__).resolve().parents[2] / "tools/research_test_slice.py"
        ).read_text(encoding="utf-8"),
        "services/unchanged.py": "# Runtime baseline\n",
    }
    for name, value in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    git_at(tmp_path, "add", ".")
    git_at(tmp_path, "commit", "-qm", "Qualified isolated source")
    return tmp_path, git_at(tmp_path, "rev-parse", "HEAD")


def run_guard(root, qualified, *, index="3", mode="windows-slice", followup_sha=None):
    return subprocess.run(
        [sys.executable, "-c", recheck_guard()],
        cwd=root,
        env={
            **isolated_environment(),
            "QUALIFIED_SOURCE_SHA": qualified,
            "RECHECK_SLICE_INDEX": index,
            "RECHECK_MODE": mode,
            "FOLLOWUP_QUALIFIED_SOURCE_SHA": followup_sha or qualified,
            "GITHUB_STEP_SUMMARY": str(root / "summary.txt"),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )


def commit_change(root, path, value="changed\n"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")
    git_at(root, "add", "-A")
    git_at(root, "commit", "-qm", "Scoped candidate change")


def test_recheck_guard_accepts_test_docs_and_workflow_changes_without_partition_shift(
    qualified_repository,
):
    root, sha = qualified_repository
    commit_change(root, "test/research/test_existing.py", "# Larger explicit test timeout\n")
    commit_change(root, "test/research/test_release_workflow.py", "# Always rechecked explicitly\n")
    commit_change(root, "docs/research/receipt.md")
    commit_change(root, ".github/workflows/research-distribution.yml")
    result = run_guard(root, sha)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((root / ".agent-native/runtime-recheck/qualification.json").read_text())
    assert receipt["qualified_source_sha"] == sha
    assert receipt["current_source_sha"] == git_at(root, "rev-parse", "HEAD")
    assert receipt["runtime_unchanged"] and receipt["inventory_unchanged"]
    assert receipt["slice_index"] == 3 and receipt["slice_count"] == 4
    summary = (root / "summary.txt").read_text()
    assert "cannot independently qualify" in summary
    assert "package, or fresh installation" in summary


@pytest.mark.parametrize(
    "path", ["test/research/test_a.py", "test/research/helper.py", "test/assets/fixture.csv"]
)
def test_recheck_guard_rejects_changed_other_slice_tests_and_shared_fixtures(
    qualified_repository, path
):
    root, sha = qualified_repository
    commit_change(root, path)
    result = run_guard(root, sha)
    assert result.returncode != 0 and "outside the selected slice" in result.stderr
    assert not (root / ".agent-native/runtime-recheck/qualification.json").exists()


@pytest.mark.parametrize(
    "path",
    [
        "services/unchanged.py",
        "pyproject.toml",
        "tools/research_test_slice.py",
        ".github/workflows/ci.yml",
    ],
)
def test_recheck_guard_rejects_unqualified_runtime_or_other_workflow_changes(
    qualified_repository, path
):
    root, sha = qualified_repository
    commit_change(root, path)
    result = run_guard(root, sha)
    assert result.returncode != 0 and "cannot qualify runtime changes" in result.stderr
    assert not (root / ".agent-native/runtime-recheck/qualification.json").exists()


@pytest.mark.parametrize("action", ["add", "delete", "rename", "untracked"])
def test_recheck_guard_rejects_any_test_module_inventory_shift(qualified_repository, action):
    root, sha = qualified_repository
    if action in ("add", "untracked"):
        (root / "test/research/test_new.py").touch()
    elif action == "delete":
        (root / "test/research/test_existing.py").unlink()
    else:
        (root / "test/research/test_existing.py").rename(root / "test/research/test_renamed.py")
    if action != "untracked":
        git_at(root, "add", "-A")
        git_at(root, "commit", "-qm", "Changed test partition")
    result = run_guard(root, sha)
    assert result.returncode != 0 and "Test-module inventory changed" in result.stderr


@pytest.mark.parametrize(
    "sha,index",
    [
        ("e24c066f7", "3"),
        ("--upload-pack=evil", "3"),
        ("$(echo secret)", "3"),
        ("a" * 40, "4"),
        ("a" * 40, "3; echo secret"),
    ],
)
def test_recheck_inputs_are_validated_before_git_and_never_interpolated_into_shell(
    tmp_path, sha, index
):
    result = run_guard(tmp_path, sha, index=index)
    assert result.returncode != 0
    assert (
        "fully pinned 40-character" in result.stderr or "slice index from 0 to 3" in result.stderr
    )
    assert "not a git repository" not in result.stderr
    assert "${{" not in recheck_guard() and "shell=True" not in recheck_guard()


def test_runtime_followup_checks_only_corrected_contracts_then_remaining_linux_steps():
    config = workflow()
    assert "inputs.validation_scope == 'runtime-followup'" in config["concurrency"]["group"]
    assert "'-runtime-followup'" in config["concurrency"]["group"]
    job = config["jobs"]["runtime-followup"]
    assert job["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.validation_scope == 'runtime-followup' }}"
    )
    assert job["timeout-minutes"] == "45"
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    assert job["env"]["FOLLOWUP_QUALIFIED_SOURCE_SHA"] == (
        "bc68f2ce0ee1927da9c6857df71f874b7e968990"
    )
    assert job["env"]["RECHECK_MODE"] == "runtime-followup"
    assert job["env"]["OPENBLAS_NUM_THREADS"] == job["env"]["NUMBA_NUM_THREADS"] == "2"
    steps = job["steps"]
    guard = next(
        step
        for step in steps
        if step.get("name") == "Verify unchanged runtime and exact test partition"
    )
    assert guard["run"] == recheck_guard()
    assert any(
        step.get("run")
        == "uv run --no-sync pytest test/research/test_release_package.py test/research/test_release_workflow.py -q"
        for step in steps
    )
    assert not any(
        "research_test_slice.py" in step.get("run", "") for step in steps if step is not guard
    )
    full_steps = config["jobs"]["supported-runtime"]["steps"]
    for step in steps:
        if step.get("if") == "runner.os == 'Linux'":
            original = next(
                original for original in full_steps if original.get("name") == step["name"]
            )
            assert step["run"] == original["run"]
    assert sum(step.get("if") == "runner.os == 'Linux'" for step in steps) == 6
    assert not any("tools/research_release.py" in step.get("run", "") for step in steps)


def test_runtime_followup_accepts_exact_allowlist_and_discloses_combined_qualification(
    qualified_repository,
):
    root, sha = qualified_repository
    for name in (
        "test/research/test_release_package.py",
        "test/research/test_release_workflow.py",
        "docs/research/receipt.md",
        ".github/workflows/research-distribution.yml",
    ):
        commit_change(root, name)
    result = run_guard(root, sha, mode="runtime-followup")
    assert result.returncode == 0, result.stderr
    receipt = json.loads((root / ".agent-native/runtime-recheck/qualification.json").read_text())
    assert receipt["selected_modules"] == [
        "test/research/test_release_package.py",
        "test/research/test_release_workflow.py",
    ]
    assert receipt["slice_index"] is None and receipt["runtime_unchanged"]
    summary = (root / "summary.txt").read_text()
    assert "2,516 core passes" in summary and "not a full matrix" in summary
    assert "only when the runtime is unchanged" in summary


@pytest.mark.parametrize(
    "path", ["test/research/test_existing.py", "test/research/helper.py", "services/unchanged.py"]
)
def test_runtime_followup_rejects_changes_outside_its_exact_contract_allowlist(
    qualified_repository, path
):
    root, sha = qualified_repository
    commit_change(root, path)
    result = run_guard(root, sha, mode="runtime-followup")
    assert result.returncode != 0
    assert (
        "outside the selected slice" in result.stderr
        or "cannot qualify runtime changes" in result.stderr
    )


def test_runtime_followup_requires_its_explicit_fully_pinned_qualified_baseline(
    qualified_repository,
):
    root, sha = qualified_repository
    result = run_guard(root, sha, mode="runtime-followup", followup_sha="f" * 40)
    assert result.returncode != 0 and "explicitly qualified baseline SHA" in result.stderr
