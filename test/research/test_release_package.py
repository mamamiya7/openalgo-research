"""A tiny Git tree exercises the real inventory and archive safety boundary."""

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from tools.research_check import REQUIRED_RESEARCH_FILES, distribution
from tools.research_release import cleanup_staging, package, permitted, run, staging_directory


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
        "extensions/chartink/manifest.json": '{"manifest_version":3}',
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
        "extensions/chartink/signals.csv",
        "extensions/chartink/node_modules/pkg/private.js",
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
        assert "extensions/chartink/manifest.json" in names
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


def windows_cleanup_error():
    error = OSError(145, "The directory is not empty")
    error.winerror = 145
    return error


def test_transient_windows_staging_cleanup_still_returns_valid_package(source, monkeypatch):
    from tools import research_release

    output = source / ".agent-native/release/cleanup-retry"
    remove = shutil.rmtree
    attempts = []
    waits = []

    def temporarily_locked(path, *args, **kwargs):
        if Path(path).name.startswith("staging-") and Path(path).parent.samefile(output):
            attempts.append(Path(path))
            if len(attempts) == 1:
                raise windows_cleanup_error()
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(research_release.shutil, "rmtree", temporarily_locked)
    monkeypatch.setattr(research_release.time, "sleep", waits.append)
    artifact = package(source, output, build)
    assert zipfile.is_zipfile(artifact)
    assert (output / "manifest.json").is_file()
    assert len(attempts) == 2
    assert waits == [0.25]
    assert not list(output.glob("staging-*"))


@pytest.mark.parametrize("target", ["output", "outside", "ordinary", "nested", "parent_escape"])
def test_cleanup_refuses_every_path_except_owned_direct_staging_child(
    tmp_path, monkeypatch, target
):
    from tools import research_release

    output = tmp_path / "release-output"
    output.mkdir()
    paths = {
        "output": output,
        "outside": tmp_path / "staging-outside",
        "ordinary": output / "manifest-files",
        "nested": output / "nested/staging-child",
        "parent_escape": output / "../staging-escape",
    }
    removed = []
    monkeypatch.setattr(research_release.shutil, "rmtree", removed.append)
    with pytest.raises(ValueError, match="Refusing cleanup"):
        cleanup_staging(paths[target], output)
    assert removed == []


def test_permanent_cleanup_failure_is_bounded_and_visible(tmp_path, monkeypatch):
    from tools import research_release

    output = tmp_path / "release-output"
    stage = output / "staging-stuck"
    stage.mkdir(parents=True)
    failure = windows_cleanup_error()
    attempts, waits = [], []

    def locked(path):
        attempts.append(Path(path))
        raise failure

    monkeypatch.setattr(research_release.shutil, "rmtree", locked)
    monkeypatch.setattr(research_release.time, "sleep", waits.append)
    with pytest.raises(RuntimeError, match="temporary files remain") as caught:
        cleanup_staging(stage, output)
    assert caught.value.__cause__ is failure
    assert len(attempts) == 5
    assert all(path.samefile(stage) for path in attempts)
    assert waits == [0.25, 0.5, 1.0, 2.0]
    assert stage.is_dir()


def test_build_exception_survives_cleanup_failure_with_visible_note(tmp_path, monkeypatch):
    from tools import research_release

    original = RuntimeError("original builder failure")

    def locked(*_args, **_kwargs):
        raise windows_cleanup_error()

    monkeypatch.setattr(research_release.shutil, "rmtree", locked)
    monkeypatch.setattr(research_release.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="original builder failure") as caught:
        with staging_directory(tmp_path):
            raise original
    assert caught.value is original
    assert "cleanup also failed" in original.__notes__[0]
    assert "temporary files remain" in original.__notes__[0]


def test_staging_cleanup_keeps_native_read_only_file_handling(tmp_path):
    with staging_directory(tmp_path) as stage:
        readonly = stage / "readonly.js"
        readonly.write_text("temporary build dependency")
        readonly.chmod(0o444)
    assert not stage.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length filesystem regression")
def test_staging_cleanup_removes_real_windows_long_paths_and_readonly_files(tmp_path):
    # Real filesystem operations: npm dependencies can exceed MAX_PATH below a
    # managed source checkout even though every individual name is short enough.
    output = tmp_path / "release-output"
    output.mkdir()
    outside = tmp_path / "keep.txt"
    outside.write_text("outside the release")
    with staging_directory(output) as stage:
        leaf = stage / "frontend/node_modules"
        for index in range(5):
            leaf /= f"dependency-{index}-" + "x" * 48
        leaf /= "variable_anchor_offset_collection.mjs.map"
        assert len(str(leaf)) > 260
        native_leaf = Path("\\\\?\\" + str(leaf))
        native_leaf.parent.mkdir(parents=True)
        native_leaf.write_text("real long-path dependency")
        native_leaf.chmod(0o444)
        assert native_leaf.read_text() == "real long-path dependency"
    assert not stage.exists()
    assert list(output.iterdir()) == []
    assert outside.read_text() == "outside the release"


def test_cleanup_revalidates_resolved_boundary_before_retry(tmp_path, monkeypatch):
    from tools import research_release

    output = tmp_path / "release-output"
    stage = output / "staging-replaced"
    stage.mkdir(parents=True)
    resolve = Path.resolve
    attempts = []

    def moved(path, *args, **kwargs):
        if path == stage and attempts:
            return tmp_path / "outside/staging-replaced"
        return resolve(path, *args, **kwargs)

    def locked(path):
        attempts.append(path)
        raise windows_cleanup_error()

    monkeypatch.setattr(Path, "resolve", moved)
    monkeypatch.setattr(research_release.shutil, "rmtree", locked)
    monkeypatch.setattr(research_release.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="Refusing cleanup"):
        cleanup_staging(stage, output)
    assert len(attempts) == 1
    assert Path(attempts[0]).samefile(stage)


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


def test_missing_install_template_blocks_package_before_build(source):
    (source / ".sample.env").unlink()

    def unexpected(*_):
        pytest.fail("Package build started without the fresh-install template")

    with pytest.raises(ValueError, match=r"\.sample\.env"):
        package(source, source / ".agent-native/release/missing-template", unexpected)


@pytest.mark.parametrize(
    "module",
    [
        "research_native_prices",
        "research_library",
        "research_activity",
        "research_result_presentation",
    ],
)
def test_missing_native_module_blocks_package(source, module):
    (source / f"services/{module}.py").unlink()
    with pytest.raises(ValueError, match=module):
        package(source, source / ".agent-native/release/missing-adapter", build)


def test_incompatible_host_blocks_package_before_build(source):
    path = source / "services/history_service.py"
    path.write_text("def get_history(symbol): pass\n")
    with pytest.raises(ValueError, match="compatibility.*history_service"):
        package(source, source / ".agent-native/release/host-drift", build)
