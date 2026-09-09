"""A tiny Git tree exercises the real inventory and archive safety boundary."""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from tools.research_check import REQUIRED_RESEARCH_FILES, distribution
from tools.research_release import package, permitted, run


@pytest.fixture
def source(tmp_path):
    def command(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    command("init")
    command("config", "user.email", "fixture@example.invalid")
    command("config", "user.name", "Packaging fixture")
    files = {
        "app.py": "print('upstream')",
        "License.md": "License fixture",
        "Dockerfile": "FROM scratch",
        "start.sh": "#!/bin/sh\n",
        "docker-compose.yaml": "services: {}\n",
        "pyproject.toml": "[project]\nname='fixture'",
        "uv.lock": "version=1",
        "frontend/package.json": "{}",
        "frontend/package-lock.json": "{}",
        "frontend/src/main.ts": "current source",
        "frontend/dist/index.html": "OLD BUILD",
        ".gitignore": ".agent-native/\n.env\nresearch_data/\n*.db\n",
    }
    original = Path(__file__).resolve().parents[2]
    # Include the real integration contracts. No modules from this tree are executed.
    for name in REQUIRED_RESEARCH_FILES | set(distribution()["host_calls"]) | {"pyproject.toml"}:
        path = original / name
        files[name] = path.read_text(encoding="utf-8") if path.exists() else "# runtime fixture\n"
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    command("add", ".")
    command("commit", "-m", "Fixture")
    (tmp_path / "app.py").write_text("print('current dirty feature')")
    for name in [
        ".env",
        "research_data/private.csv",
        ".agent-native/preview.html",
        "secrets.json",
        "test/research/token.db",
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NEVER_SHIP_SECRET")
    (tmp_path / "research").mkdir(exist_ok=True)
    (tmp_path / "research/engine.py").write_text("# intentional new feature")
    return tmp_path


def build(stage, _output):
    dist = stage / "frontend/dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text('<script src="assets/current.js"></script>')
    (dist / "assets").mkdir()
    (dist / "assets/current.js").write_text((stage / "frontend/src/main.ts").read_text())
    return {"node": "fixture", "npm": "fixture"}


def test_current_source_build_hashes_and_private_exclusions(source):
    output = source / ".agent-native/release/first"
    artifact = package(source, output, build)
    with zipfile.ZipFile(artifact) as archive:
        names = archive.namelist()
        assert "research/engine.py" in names
        assert archive.read("app.py") == b"print('current dirty feature')"
        assert b"current.js" in archive.read("frontend/dist/index.html")
        assert "License.md" in names
        manifest = json.loads(archive.read("RELEASE_MANIFEST.json"))
        assert manifest["dirty"]
        assert manifest["release_version"] == distribution()["version"]
        assert manifest["compatibility_check"]["ok"]
        assert REQUIRED_RESEARCH_FILES <= set(names)
        assert "research/engine.py" in manifest["included_changes"]
        for name, receipt in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == receipt["sha256"]
        assert not any(b"NEVER_SHIP_SECRET" in archive.read(name) for name in names)
    assert (source / "frontend/dist/index.html").read_text() == "OLD BUILD"
    assert not list(output.glob("staging-*"))
    second = package(source, source / ".agent-native/release/second", build)
    assert artifact.read_bytes() == second.read_bytes()
    with pytest.raises(FileExistsError):
        package(source, output, build)


def test_failed_build_releases_stage_and_never_publishes_archive(source):
    output = source / ".agent-native/release/failure"

    def fail(stage, _):
        assert (stage / "app.py").exists()
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        package(source, output, fail)
    assert not list(output.glob("staging-*"))
    assert not list(output.glob("*.zip"))


def test_link_escape_is_rejected(source, tmp_path):
    path = source / "research/escape.py"
    try:
        path.symlink_to(source / ".env")
    except OSError:
        pytest.skip("Host does not permit symlink creation")
    with pytest.raises(ValueError, match="Links"):
        package(source, source / ".agent-native/release/link", build)


@pytest.mark.parametrize(
    "name",
    [
        "../app.py",
        "/app.py",
        "frontend/.env.production",
        "research/private.parquet",
        "frontend/node_modules/pkg/a.js",
        "research/secrets.json",
        "data/raw.csv",
    ],
)
def test_sensitive_and_escape_names_are_not_sources(name):
    assert not permitted(name)


def test_subprocess_failure_is_logged_and_reaped(tmp_path):
    import sys

    log = tmp_path / "failure.log"
    with pytest.raises(RuntimeError, match="Command failed"):
        run([sys.executable, "-c", "print('diagnostic'); raise SystemExit(3)"], tmp_path, log)
    assert "diagnostic" in log.read_text()
    log.unlink()  # Windows rejects this if the file remains open.


def test_subprocess_timeout_closes_log(tmp_path):
    import sys

    log = tmp_path / "timeout.log"
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, log, timeout=0.2)
    log.unlink()


def test_build_cannot_change_audited_source(source):
    def mutate(stage, output):
        build(stage, output)
        (stage / "app.py").write_text("unexpected mutation")

    with pytest.raises(ValueError, match="modified staged source"):
        package(source, source / ".agent-native/release/mutation", mutate)


def test_output_cannot_escape_release_area(source):
    with pytest.raises(ValueError, match="inside"):
        package(source, source / "public-output", build)


def test_missing_native_data_adapter_blocks_package(source):
    (source / "services/research_native_prices.py").unlink()
    with pytest.raises(ValueError, match="research_native_prices"):
        package(source, source / ".agent-native/release/missing-adapter", build)


def test_incompatible_host_blocks_package_before_build(source):
    path = source / "services/history_service.py"
    path.write_text("def get_history(symbol): pass\n")
    with pytest.raises(ValueError, match="compatibility.*history_service"):
        package(source, source / ".agent-native/release/host-drift", build)
