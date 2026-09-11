"""Build one versioned OpenAlgo Research source distribution. Does not publish or deploy.

Run with Python 3.12, Git, Node and npm installed. npm ci runs only in the staged
source tree, using its lockfile. No live application or broker service is started.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

try:
    from tools.research_check import REQUIRED_RESEARCH_FILES, check, distribution
except ModuleNotFoundError:  # Direct `python tools/research_release.py` invocation.
    from research_check import REQUIRED_RESEARCH_FILES, check, distribution

ROOT_FILES = set(
    """__init__.py .dockerignore .gitignore .sample.env app.py
benchmark_api.py Caddyfile CONTRIBUTING.md cors.py csp.py DOCKER_README.md
docker-build.bat docker-build.sh docker-compose.yaml Dockerfile extensions.py
INSTALL.md License.md limiter.py pyproject.toml README.md requirements-nginx.txt
requirements.txt SECURITY.md start.sh utils.py uv.lock CLAUDE.md AGENTS.md""".split()
)
ROOT_FILES.add(".github/workflows/research-distribution.yml")
SOURCE_DIRS = set(
    """blueprints broker database docs events examples frontend install
mcp okf portfolio research restx_api sandbox scripts services sip strategies
subscribers test tools upgrade utils websocket_proxy""".split()
)
NEW_PREFIXES = (
    "research/",
    "docs/research/",
    "test/research/",
    "frontend/src/",
    "extensions/chartink/",
)
NEW_FILES = {
    ".github/workflows/research-distribution.yml",
    "tools/research_release.py",
    "tools/research_worker.py",
    "tools/research_dev.py",
    "blueprints/scanner_research.py",
    "database/research_db.py",
    "services/research_acquisition.py",
    "services/research_sources.py",
    "services/research_storage.py",
    "services/scanner_research_service.py",
    "services/scanner_research_worker.py",
    "tools/benchmark_scanner_research.py",
    "tools/research_evidence.py",
    "tools/research_storage.py",
    "upgrade/migrate_scanner_research.py",
} | REQUIRED_RESEARCH_FILES
BLOCK_PARTS = {
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    ".agent-native",
    "research_data",
    "workspace",
    "keys",
    "db",
    "log",
    "tmp",
    "data",
    "test-results",
    "playwright-report",
    "coverage",
    ".vite",
}
BLOCK_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".duckdb",
    ".log",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".pyc",
    ".parquet",
    ".feather",
    ".csv",
    ".zip",
    ".gz",
    ".tsbuildinfo",
}


def run(command, cwd, log, timeout=1200):
    """Drain output to disk, bound runtime, and reap the process tree on failure."""
    with Path(log).open("wb") as output:
        options = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        with subprocess.Popen(
            command,
            cwd=cwd,
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **options,
        ) as child:
            try:
                code = child.wait(timeout=timeout)
            except BaseException:
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                            stdout=output,
                            stderr=output,
                            timeout=30,
                            check=False,
                        )
                    else:
                        os.killpg(child.pid, signal.SIGKILL)
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait()
                raise
            if code:
                raise RuntimeError(f"Command failed ({code}); inspect {log}")


def git(root, *args):
    with tempfile.TemporaryDirectory(prefix="research-git-") as temp:
        output = Path(temp) / "git-output"
        run(["git", *args], root, output, timeout=60)
        if output.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Git inventory exceeds review limit")
        return output.read_bytes()


def permitted(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    # Shipped exchange quantity limits are application metadata, not price history.
    if name == "data/qtyfreeze.csv":
        return True
    if any(part.lower() in BLOCK_PARTS for part in path.parts):
        return False
    if name.startswith("frontend/dist/") or path.suffix.lower() in BLOCK_SUFFIXES:
        return False
    if any(part.lower().startswith((".env", "secrets", "credentials")) for part in path.parts):
        return False
    return (
        name in ROOT_FILES
        or (len(path.parts) > 1 and path.parts[0] in SOURCE_DIRS)
        or name.startswith("extensions/chartink/")
    )


def safe_file(root, name):
    path = root / name
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError(f"Links cannot be packaged: {name}")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Not a regular source file: {name}")
    return path


def inventory(root):
    tracked = set(git(root, "ls-files", "-z").decode().split("\0")) - {""}
    new = set(
        git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    ) - {""}
    selected = sorted(
        name
        for name in tracked | new
        if permitted(name)
        and (name in tracked or name.startswith(NEW_PREFIXES) or name in NEW_FILES)
        and (root / name).exists()
    )
    # --no-index applies ignore policy even to accidentally tracked runtime data.
    ignored = set(
        git(root, "ls-files", "--cached", "--ignored", "--exclude-standard", "-z")
        .decode()
        .split("\0")
    )
    return [name for name in selected if name not in ignored], tracked, new


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def build_frontend(stage, output):
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError("npm is required for the locked frontend build")
    run([npm, "ci", "--no-audit", "--no-fund"], stage / "frontend", output / "npm-ci.log")
    run([npm, "run", "build"], stage / "frontend", output / "frontend-build.log")
    node = shutil.which("node")
    versions = {}
    for name, command in (("npm", npm), ("node", node)):
        if not command:
            raise RuntimeError(f"{name} is required")
        log = output / f"{name}-version.log"
        run([command, "--version"], stage, log, timeout=30)
        versions[name] = log.read_text(encoding="utf-8").strip()
    return versions


def package(root, output, builder=build_frontend):
    root, output = Path(root).resolve(), Path(output).absolute()
    if output.exists():
        raise FileExistsError("Release output already exists; choose a fresh directory")
    if not output.resolve().is_relative_to(root / ".agent-native" / "release"):
        raise ValueError("Release output must be inside .agent-native/release")
    output.mkdir(parents=True, exist_ok=False)
    source_names, tracked, new = inventory(root)
    required = {
        "app.py",
        "License.md",
        "pyproject.toml",
        "uv.lock",
        "start.sh",
        "Dockerfile",
        "frontend/package.json",
        "frontend/package-lock.json",
        "docker-compose.yaml",
    } | REQUIRED_RESEARCH_FILES
    if not required.issubset(source_names):
        raise ValueError(f"Required native source missing: {sorted(required - set(source_names))}")
    base = git(root, "rev-parse", "HEAD").decode().strip()
    changed = set(git(root, "diff", "HEAD", "--name-only", "-z").decode().split("\0")) - {""}
    with tempfile.TemporaryDirectory(prefix="staging-", dir=output) as temp:
        stage = Path(temp)
        source_hashes = {}
        for name in source_names:
            source = safe_file(root, name)
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            source_hashes[name] = digest(dest)
        compatibility = check(stage, source_only=True)
        if not compatibility["ok"]:
            failed = [item["name"] for item in compatibility["checks"] if not item["ok"]]
            raise ValueError(f"Distribution compatibility checks failed: {', '.join(failed)}")
        release = distribution(stage)
        toolchain = builder(stage, output)
        dist = stage / "frontend" / "dist"
        if not (dist / "index.html").is_file():
            raise ValueError("Frontend build did not produce index.html")
        # Only the audited source inventory and newly built assets enter the archive.
        assets = sorted(p.relative_to(stage).as_posix() for p in dist.rglob("*") if p.is_file())
        names = sorted(source_names + assets)
        for name in source_names:
            if digest(safe_file(stage, name)) != source_hashes[name]:
                raise ValueError(f"Build modified staged source: {name}")
        files = {
            name: {"sha256": digest(safe_file(stage, name)), "bytes": (stage / name).stat().st_size}
            for name in names
        }
        manifest = {
            "format": "openalgo-native-source-build-v1",
            "base_commit": base,
            "release_version": release["version"],
            "distribution": release,
            "compatibility_check": compatibility,
            "dirty": bool(changed or new),
            "included_changes": sorted((changed | new) & set(source_names)),
            "omitted_change_count": len((changed | new) - set(source_names)),
            "build": ["npm ci --no-audit --no-fund", "npm run build"],
            "toolchain": toolchain,
            "reproducibility": "Sorted archive paths, fixed timestamps and modes; frontend bytes depend on the recorded source and toolchain.",
            "dependency_locks": {
                name: files[name]["sha256"] for name in ("uv.lock", "frontend/package-lock.json")
            },
            "host_contract_hashes": {name: files[name]["sha256"] for name in release["host_calls"]},
            "files": files,
        }
        content = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
        (output / "manifest.json").write_bytes(content)
        archive = output / f"openalgo-research-{release['version']}.zip"
        pending_archive = archive.with_suffix(".partial")
        with zipfile.ZipFile(
            pending_archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as bundle:
            for name in [*names, "RELEASE_MANIFEST.json"]:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
                if name == "RELEASE_MANIFEST.json":
                    bundle.writestr(info, content)
                else:
                    with safe_file(stage, name).open("rb") as src, bundle.open(info, "w") as dest:
                        shutil.copyfileobj(src, dest, 1024 * 1024)
        pending_archive.rename(archive)
        (output / "SHA256SUMS").write_text(f"{digest(archive)}  {archive.name}\n", encoding="utf-8")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.root / ".agent-native" / "release" / uuid.uuid4().hex
    print(package(args.root, output))


if __name__ == "__main__":
    main()
