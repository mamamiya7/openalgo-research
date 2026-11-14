"""Guard the destructive boundary of fresh-release acceptance without installing packages."""

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest
from test_broker_first_setup import account
from test_broker_first_setup import app as broker_app  # noqa: F401 -- isolated native route fixture

from tools import research_install_smoke as smoke


def archive(tmp_path, *, changed=None, extra=None, version="0.1.0-preview.5", native_ready=False):
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
    preview = int(version.rsplit(".", 1)[1]) if version.startswith("0.1.0-preview.") else None
    if version == "0.1.0" or (preview is not None and preview >= 32):
        files.update(
            dict.fromkeys(
                (
                    "research/supervisor_accounts.py",
                    "services/research_supervisor.py",
                    "services/research_supervisor_program.py",
                    "services/agent/tools/research.py",
                ),
                b"source",
            )
        )
    if version == "0.1.0" or (preview is not None and preview >= 33):
        files.update(
            dict.fromkeys(
                ("services/research_automation.py", "services/agent/research_decision.py"),
                b"source",
            )
        )
    if native_ready:
        files["tools/research_native_ready.py"] = b"native readiness source"
    distribution = {"version": version}
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
    path = tmp_path / f"openalgo-research-{version}.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("RELEASE_MANIFEST.json", json.dumps(manifest))
        for name, data in files.items():
            bundle.writestr(name, changed if changed is not None and name == "Setup.cmd" else data)
        if extra:
            bundle.writestr(*extra)
    return path


@pytest.mark.parametrize(
    "version",
    [
        "0.1.0-preview.5",
        "0.1.0-preview.31",
        "0.1.0-preview.32",
        "0.1.0-preview.33",
        "0.1.0-preview.36",
    ],
)
def test_retained_older_archives_without_native_preflight_remain_valid(tmp_path, version):
    source = archive(tmp_path, version=version)
    with zipfile.ZipFile(source) as bundle:
        manifest = smoke.validate_archive(bundle, expected_version=version)
    assert manifest["release_version"] == version
    assert "tools/research_native_ready.py" not in manifest["files"]


@pytest.mark.parametrize("version", ["0.1.0-preview.37", "0.1.0-preview.38", "0.1.0"])
def test_new_archives_require_native_preflight_before_extraction(tmp_path, version):
    missing = archive(tmp_path, version=version)
    target = tmp_path / "not-created"
    with pytest.raises(ValueError, match="incomplete"):
        smoke.extract_archive(missing, target, expected_version=version)
    assert not target.exists()
    complete = archive(tmp_path, version=version, native_ready=True)
    with zipfile.ZipFile(complete) as bundle:
        manifest = smoke.validate_archive(bundle, expected_version=version)
    assert "tools/research_native_ready.py" in manifest["files"]


def test_new_archive_native_gate_preserves_checksum_validation(tmp_path):
    forged = archive(tmp_path, version="0.1.0-preview.37", native_ready=True, changed=b"forged")
    with zipfile.ZipFile(forged) as bundle, pytest.raises(ValueError, match="checksum"):
        smoke.validate_archive(bundle, expected_version="0.1.0-preview.37")


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


def test_fresh_native_broker_configuration_uses_cards_and_never_implies_live_login():
    data = {
        "logged_in": None,
        "brokers": [
            {
                "id": "fyers",
                "active": False,
                "configured": False,
                "source": None,
                "fields": [{"key": "api_key", "saved": ""}, {"key": "api_secret", "saved": ""}],
            }
        ],
    }
    smoke.verify_fresh_broker_cards(data)
    data["brokers"][0]["configured"] = True
    with pytest.raises(RuntimeError, match="contains keys"):
        smoke.verify_fresh_broker_cards(data)
    with pytest.raises(RuntimeError, match="logged-in broker"):
        smoke.verify_fresh_broker_cards({**data, "logged_in": "fyers"})


def test_fresh_broker_smoke_reaches_the_actual_authenticated_native_route(broker_app):  # noqa: F811 -- shared isolated fixture
    client = broker_app.test_client()
    assert client.get("/api/broker/list", headers={"Accept": "application/json"}).status_code == 401
    account(client)
    paths = []

    def get(path):
        paths.append(path)
        response = client.get(path, headers={"Accept": "application/json"})
        assert response.status_code == 200
        assert response.json["status"] == "success"
        return response.json

    smoke.verify_fresh_broker_setup(get)
    assert paths == ["/api/broker/list"]


@pytest.mark.parametrize(
    "response",
    [{}, {"data": {}}, {"status": "error", "data": {}}, {"status": "success", "data": []}],
)
def test_fresh_broker_smoke_rejects_an_invalid_native_envelope(response):
    with pytest.raises(RuntimeError, match="invalid response"):
        smoke.verify_fresh_broker_setup(lambda path: response)


def test_failure_diagnostics_keep_safe_frames_not_log_messages_or_generated_secrets(tmp_path):
    log = tmp_path / "workflow.log"
    log.write_text(
        'Traceback (most recent call last):\n  File "/private/secret-owner/tools/research_install_smoke.py", line 537, in exercise\nKeyError: current_broker private-generated-token\nhttps://hooks.slack.com/services/private/generated/token\n'
    )
    result = smoke.failure_diagnostics(log, stage="workflow", exit_code=1)
    published = json.loads((tmp_path / "diagnostics.json").read_text())
    assert published == result and result["exception_type"] == "KeyError"
    assert result["traceback_frames"] == [
        {"file": "research_install_smoke.py", "line": 537, "function": "exercise"}
    ]
    assert not any(
        value in json.dumps(published)
        for value in (
            "private-generated-token",
            "secret-owner",
            "hooks.slack.com",
            "current_broker",
        )
    )
    assert result["raw_log_or_exception_message_included"] is False


def test_reinstall_waits_for_managed_interpreter_exit_and_retains_the_same_test_account(
    tmp_path, monkeypatch
):
    evidence = tmp_path / "tmp/install-smoke"
    evidence.mkdir(parents=True)
    (tmp_path / ".env").write_text("unique-test-environment")
    receipt_path = evidence / "receipt.json"
    receipt_path.write_text(
        json.dumps({"state": "installed", "environment_sha256": smoke.digest(tmp_path / ".env")})
    )
    events, accounts = [], []
    active = False

    class ManagedExercise:
        def __init__(self, command, directory, environment, log):
            self.phase = command[-1]
            assert command[-2] == "--exercise-phase"
            accounts.append(
                (environment["OPENALGO_SMOKE_USERNAME"], environment["OPENALGO_SMOKE_PASSWORD"])
            )

        def __enter__(self):
            nonlocal active
            assert not active
            active = True
            events.append("enter-" + self.phase)
            return self

        def wait(self, timeout):
            assert timeout == 1800
            current = json.loads(receipt_path.read_text())
            assert current["state"] == ("installed" if self.phase == "initial" else "reinstalled")
            current["state"] = "exercised" if self.phase == "initial" else "passed"
            receipt_path.write_text(json.dumps(current))

        def __exit__(self, *args):
            nonlocal active
            active = False
            events.append("exit-" + self.phase)

    def reinstall(directory, environment, ports, name):
        assert not active, (
            "Reinstallation must not run while its test interpreter owns native files"
        )
        assert name == "reinstall" and ports == [1, 2, 3]
        assert "OPENALGO_SMOKE_PASSWORD" not in environment
        events.append("reinstall")

    monkeypatch.setattr(smoke, "OwnedCommand", ManagedExercise)
    monkeypatch.setattr(smoke, "install", reinstall)
    smoke.qualify_installed_phases(tmp_path, {}, [1, 2, 3])
    assert events == ["enter-initial", "exit-initial", "reinstall", "enter-restart", "exit-restart"]
    assert accounts[0] == accounts[1] and all(accounts[0])
    assert accounts[0][1] not in receipt_path.read_text()
    assert json.loads(receipt_path.read_text())["state"] == "passed"


def test_wrapping_failure_does_not_discard_inner_redacted_diagnostics(tmp_path):
    inner = tmp_path / "reinstall.log"
    inner.write_text("PermissionError: private-path secret-account\n")
    original = smoke.failure_diagnostics(inner, stage="reinstall", exit_code=1)
    outer = tmp_path / "workflow-initial.log"
    outer.write_text("RuntimeError: inspect private-log\n")
    smoke.failure_diagnostics(outer, stage="workflow-initial", exit_code=1)
    retained = json.loads((tmp_path / "diagnostics.reinstall.json").read_text())
    assert retained == original and retained["exception_type"] == "PermissionError"
    assert "secret-account" not in json.dumps(retained)
