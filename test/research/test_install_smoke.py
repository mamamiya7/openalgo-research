"""Guard the destructive boundary of fresh-release acceptance without installing packages."""

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from tools import research_install_smoke as smoke


def archive(tmp_path, *, changed=None, extra=None):
    files = dict.fromkeys(
        (
            "Setup.cmd",
            "setup-research.sh",
            "tools/research_install_smoke.py",
            "tools/research_desktop.py",
            "frontend/dist/index.html",
            ".sample.env",
            "uv.lock",
            "pyproject.toml",
        ),
        b"source",
    )
    distribution = {"version": "0.1.0-preview.5"}
    files["research/distribution.json"] = json.dumps(distribution).encode()
    manifest = {
        "format": "openalgo-native-source-build-v1",
        "release_version": distribution["version"],
        "distribution": distribution,
        "files": {
            name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        },
    }
    path = tmp_path / "openalgo-research-0.1.0-preview.5.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("RELEASE_MANIFEST.json", json.dumps(manifest))
        for name, data in files.items():
            bundle.writestr(name, changed if changed is not None and name == "Setup.cmd" else data)
        if extra:
            bundle.writestr(*extra)
    return path


def test_valid_release_extracts_only_after_full_validation(tmp_path):
    source = archive(tmp_path)
    target = tmp_path / "fresh installation"
    manifest, sha = smoke.extract_archive(source, target, expected_version="0.1.0-preview.5")
    assert manifest["release_version"] == "0.1.0-preview.5"
    assert sha == smoke.digest(source)
    assert (target / "Setup.cmd").read_bytes() == b"source"
    assert not (target / ".env").exists()


@pytest.mark.parametrize(
    "bad", ["../escape", "/absolute", "C:/absolute", "folder\\escape", "AUX.txt", "folder./x"]
)
def test_unsafe_paths_rejected_without_writing_destination(tmp_path, bad):
    target = tmp_path / "new"
    with pytest.raises(ValueError):
        smoke.extract_archive(archive(tmp_path, extra=(bad, b"bad")), target)
    assert not target.exists()


def test_checksum_version_and_inventory_rejected_before_extraction(tmp_path):
    target = tmp_path / "new"
    with pytest.raises(ValueError, match="checksum"):
        smoke.extract_archive(archive(tmp_path, changed=b"forged"), target)
    assert not target.exists()
    with pytest.raises(ValueError, match="expected release"):
        smoke.extract_archive(archive(tmp_path), target, expected_version="0.1.0-preview.4")
    assert not target.exists()
    with pytest.raises(ValueError, match="inventory"):
        smoke.extract_archive(archive(tmp_path, extra=(".env", b"private")), target)
    assert not target.exists()


def test_nonempty_relative_root_and_link_targets_rejected(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        smoke.fresh_directory(Path("relative"))
    with pytest.raises(ValueError, match="absolute"):
        smoke.fresh_directory(Path(tmp_path.anchor))
    owned = tmp_path / "existing"
    owned.mkdir()
    (owned / "keep").write_text("never touch")
    with pytest.raises(ValueError, match="new or empty"):
        smoke.fresh_directory(owned)
    assert (owned / "keep").read_text() == "never touch"
    link = tmp_path / "link"
    try:
        link.symlink_to(owned, target_is_directory=True)
    except OSError:
        return  # Windows symlink privilege is optional; other guards still run.
    with pytest.raises(ValueError, match="links"):
        smoke.fresh_directory(link / "fresh")


def test_archive_symlink_and_sibling_checksum_rejected(tmp_path):
    link = zipfile.ZipInfo("linked")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="regular"):
        smoke.extract_archive(archive(tmp_path, extra=(link, b"elsewhere")), tmp_path / "fresh")
    source = archive(tmp_path)
    (tmp_path / "SHA256SUMS").write_text(f"{'0' * 64}  {source.name}\n")
    with pytest.raises(ValueError, match="SHA256SUMS"):
        smoke.extract_archive(source, tmp_path / "fresh")


def test_clean_environment_drops_personal_data_and_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "never-pass-this")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///personal.db")
    monkeypatch.setenv("PYTHONPATH", "personal-code")
    monkeypatch.setenv("RESEARCH_DATA_DIR", "personal-research")
    result = smoke.clean_environment(tmp_path)
    for key in ("BROKER_API_KEY", "DATABASE_URL", "PYTHONPATH", "RESEARCH_DATA_DIR"):
        assert key not in result
    assert Path(result["HOME"]).is_relative_to(tmp_path)
    assert Path(result["TEMP"]).is_relative_to(tmp_path)


def test_actual_bootstrap_entry_handles_a_spaced_folder(tmp_path):
    root = tmp_path / "Fresh Install With Spaces"
    root.mkdir()
    (root / "Setup.cmd").write_text('@echo off\necho complete>"%~dp0finished"\nexit /b 0\n')
    (root / "setup-research.sh").write_text(
        '#!/usr/bin/env bash\nprintf complete > "$(dirname "$0")/finished"\n'
    )
    environment = smoke.clean_environment(root)
    with smoke.OwnedCommand(
        smoke.bootstrap_command(root, [5318, 5319, 5320]), root, environment, root / "bootstrap.log"
    ) as child:
        child.wait(10)
    assert (root / "finished").read_text().strip() == "complete"
