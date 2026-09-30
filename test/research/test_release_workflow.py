"""Keep scoped artifact validation explicit without weakening normal CI."""

from pathlib import Path

import yaml


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
