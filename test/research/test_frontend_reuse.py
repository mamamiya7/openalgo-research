"""Reuse a validated build only for the identical complete frontend input tree."""

import hashlib
import json
import zipfile

import pytest
import test_release_package as fixtures

from tools import research_release as release
from tools.research_install_smoke import validate_archive

source = fixtures.source
build = fixtures.build


@pytest.fixture
def previous(source):
    # This fixture runs the real source inventory, compatibility and ZIP writer.
    return release.package(source, source / ".agent-native/release/original", build)


def unexpected_build(*_):
    pytest.fail("npm/build must not run during verified reuse")


def rewrite_archive(previous, *, add=None, remove=None, change=None, manifest_change=None):
    with zipfile.ZipFile(previous) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    manifest = json.loads(files.pop("RELEASE_MANIFEST.json"))
    if add:
        for name, data in add.items():
            files[name] = data
            manifest["files"][name] = {
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
    if remove:
        files.pop(remove)
    if change:
        files.update(change)  # Preserve the original receipt to detect tampering.
    if manifest_change:
        manifest_change(manifest)
    target = previous.with_name("modified.zip")
    with zipfile.ZipFile(target, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
        bundle.writestr("RELEASE_MANIFEST.json", json.dumps(manifest))
    return target


def assert_rejected(source, previous, *, match=None):
    output = source / ".agent-native/release/rejected"
    with pytest.raises(ValueError, match=match):
        release.package(source, output, unexpected_build, reuse_frontend_from=previous)
    assert not list(output.glob("staging-*"))
    assert not list(output.glob("*.zip"))
    assert not (output / "manifest.json").exists()
    # No open archive handle remains, including on Windows.
    renamed = previous.with_name("closed-handle.zip")
    previous.rename(renamed)
    renamed.rename(previous)


def test_identical_inputs_reuse_dist_but_current_source_and_honest_provenance(source, previous):
    (source / "extensions/chartink/manifest.json").write_text(
        '{"manifest_version":3,"version":"0.1.2"}'
    )
    (source / "docs/research").mkdir(parents=True, exist_ok=True)
    (source / "docs/research/reuse.md").write_text("Current publication note")
    output = source / ".agent-native/release/reused"
    artifact = release.package(source, output, unexpected_build, reuse_frontend_from=previous)
    with zipfile.ZipFile(previous) as old, zipfile.ZipFile(artifact) as new:
        manifest = validate_archive(new)
        assert manifest["build"] == []
        proof = manifest["frontend_build"]
        assert proof["mode"] == "verified-reuse"
        assert proof["source_archive_sha256"] == release.digest(previous)
        assert proof["original_build"]["commands"] == release.FRONTEND_COMMANDS
        assert proof["original_build"]["toolchain"] == {"node": "fixture", "npm": "fixture"}
        assert manifest["toolchain"] == proof["original_build"]["toolchain"]
        for name in old.namelist():
            if name.startswith("frontend/dist/"):
                assert new.read(name) == old.read(name)
        assert json.loads(new.read("extensions/chartink/manifest.json"))["version"] == "0.1.2"
        assert new.read("docs/research/reuse.md") == b"Current publication note"
    assert not (output / "npm-ci.log").exists()
    assert not list(output.glob("staging-*"))
    assert (source / "frontend/dist/index.html").read_text() == "OLD BUILD"


def test_old_qualified_manifest_can_be_reused_and_original_provenance_survives_chain(
    source, previous
):
    old = rewrite_archive(previous, manifest_change=lambda manifest: manifest.pop("frontend_build"))
    first = release.package(
        source,
        source / ".agent-native/release/first-reuse",
        unexpected_build,
        reuse_frontend_from=old,
    )
    second = release.package(
        source,
        source / ".agent-native/release/second-reuse",
        unexpected_build,
        reuse_frontend_from=first,
    )
    with zipfile.ZipFile(first) as left, zipfile.ZipFile(second) as right:
        a = validate_archive(left)["frontend_build"]
        b = validate_archive(right)["frontend_build"]
        assert b["source_archive_sha256"] == release.digest(first)
        assert a["original_build"] == b["original_build"]
        assert b["original_build"]["archive_sha256"] == release.digest(old)


@pytest.mark.parametrize(
    "name", ["frontend/src/main.ts", "frontend/package-lock.json", "frontend/package.json"]
)
def test_changed_frontend_source_or_lock_refuses_before_assets(source, previous, name):
    (source / name).write_text("changed input")
    assert_rejected(source, previous, match="inputs differ")


@pytest.mark.parametrize(
    "name", ["frontend/src/addition.ts", "frontend/public/icon.svg", "frontend/vite.config.ts"]
)
def test_added_frontend_input_refuses_even_when_old_common_inputs_match(source, previous, name):
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("new input")
    # Public/config files need to be tracked, as in a real release inventory.
    release.git(source, "add", name)
    assert_rejected(source, previous, match="inputs differ")


def test_removed_frontend_input_refuses(source, previous):
    (source / "frontend/src/main.ts").unlink()
    assert_rejected(source, previous, match="inputs differ")


@pytest.mark.parametrize("name", ["frontend/dist/index.html", "frontend/dist/assets/current.js"])
def test_incomplete_artifact_refuses(source, previous, name):
    assert_rejected(source, rewrite_archive(previous, remove=name), match="inventory")


def test_corrupted_asset_refuses_before_copy(source, previous):
    modified = rewrite_archive(
        previous, change={"frontend/dist/assets/current.js": b"forged content"}
    )
    assert_rejected(source, modified, match="size mismatch|checksum mismatch")


def test_self_consistent_archive_with_missing_browser_entrypoint_refuses(source, previous):
    modified = rewrite_archive(
        previous,
        remove="frontend/dist/assets/current.js",
        add={"frontend/dist/assets/other.js": b"unrelated"},
        manifest_change=lambda manifest: manifest["files"].pop("frontend/dist/assets/current.js"),
    )
    assert_rejected(source, modified, match="entrypoint is missing")


@pytest.mark.parametrize(
    "name",
    [
        "../outside.js",
        "frontend/dist/../../escape.js",
        "frontend/dist/.env",
        "frontend/dist/credentials.json",
        ".agent-native/private.json",
        "frontend/.env.production",
    ],
)
def test_private_or_traversal_archive_content_is_never_copied(source, previous, name):
    modified = rewrite_archive(previous, add={name: b"NEVER_COPY_PRIVATE_DATA"})
    assert_rejected(source, modified, match="Unsafe")
    assert not (source / "outside.js").exists()


@pytest.mark.parametrize(
    "limit,value",
    [
        ("MAX_FRONTEND_ASSETS", 1),
        ("MAX_FRONTEND_ASSET_BYTES", 1),
        ("MAX_FRONTEND_TOTAL_BYTES", 1),
        ("MAX_REUSE_ARCHIVE_BYTES", 1),
    ],
)
def test_reuse_has_explicit_file_byte_and_archive_limits(
    source, previous, monkeypatch, limit, value
):
    monkeypatch.setattr(release, limit, value)
    assert_rejected(source, previous, match="limits|bounded")


@pytest.mark.parametrize("field", ["build", "toolchain", "dependency_locks", "compatibility_check"])
def test_unqualified_or_missing_build_provenance_refuses(source, previous, field):
    modified = rewrite_archive(previous, manifest_change=lambda manifest: manifest.pop(field))
    assert_rejected(source, modified, match="provenance|qualified")


def test_stream_copy_failure_closes_archive_and_removes_owned_stage(source, previous, monkeypatch):
    original = zipfile.ZipFile.open
    reads = 0

    def fail_second_read(bundle, name, *args, **kwargs):
        nonlocal reads
        if name == "frontend/dist/assets/current.js":
            reads += 1
            # validate_archive opens a ZipInfo; the actual copy opens this name.
            raise OSError("fixture copy interrupted")
        return original(bundle, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fail_second_read)
    output = source / ".agent-native/release/copy-interrupted"
    with pytest.raises(OSError, match="copy interrupted"):
        release.package(source, output, unexpected_build, reuse_frontend_from=previous)
    assert reads == 1
    assert not list(output.iterdir())
    previous.unlink()  # Windows prevents deletion if the archive is still open.
