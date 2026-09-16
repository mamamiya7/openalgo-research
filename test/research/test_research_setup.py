"""Installer contracts use private temporary folders and never start a real app."""

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tools import research_setup as setup

SAMPLE = """ENV_CONFIG_VERSION='1.0.7'
VALID_BROKERS='zerodha,fyers,dhan,flattrade,fivepaisa'
BROKER_API_KEY='YOUR_BROKER_API_KEY'
BROKER_API_SECRET='YOUR_BROKER_API_SECRET'
APP_KEY='OPENALGO_PLACEHOLDER_APP_KEY_REGENERATE_BEFORE_USE'
API_KEY_PEPPER='OPENALGO_PLACEHOLDER_API_KEY_PEPPER_REGENERATE_BEFORE_USE'
FERNET_SALT='OPENALGO_PLACEHOLDER_FERNET_SALT_REGENERATE_BEFORE_USE'
REDIRECT_URL='http://127.0.0.1:5000/<broker>/callback'
DATABASE_URL='sqlite:///db/openalgo.db'
FLASK_PORT='5000'
"""


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "OpenAlgo trader's research & tests"
    root.mkdir()
    (root / ".sample.env").write_text(SAMPLE, encoding="utf-8")
    assets = root / "frontend/dist/assets"
    assets.mkdir(parents=True)
    (assets / "index.js").write_text("// built", encoding="utf-8")
    (assets.parent / "index.html").write_text(
        '<script type="module" src="/assets/index.js"></script>', encoding="utf-8"
    )
    uv = root / "private uv.exe"
    uv.touch()
    return root, uv


def test_fresh_config_uses_selected_broker_unique_secrets_and_loopback(install):
    root, _ = install
    assert setup.ensure_env(root, "fyers")
    values = setup.env_values((root / ".env").read_text(encoding="utf-8"))
    assert values["REDIRECT_URL"] == "http://127.0.0.1:5000/fyers/callback"
    keys = [values[name] for name in ("APP_KEY", "API_KEY_PEPPER", "FERNET_SALT")]
    assert len(set(keys)) == 3
    assert all(len(value) == 64 and int(value, 16) for value in keys)
    assert values["BROKER_API_KEY"] == values["BROKER_API_SECRET"] == ""
    assert values["FLASK_HOST_IP"] == "127.0.0.1"
    assert values["FLASK_DEBUG"] == "False"
    assert values["MCP_HTTP_ENABLED"] == "False"
    if os.name != "nt":
        assert (root / ".env").stat().st_mode & 0o777 == 0o600


def test_existing_config_is_preserved_byte_for_byte_without_prompt(install):
    root, _ = install
    content = b"# Existing private configuration\r\nAPP_KEY='keep'\r\nDATABASE_URL='sqlite:///custom data/store.db'\r\n"
    (root / ".env").write_bytes(content)
    assert not setup.ensure_env(root, "fyers", prompt=lambda _: pytest.fail("no prompt"))
    assert (root / ".env").read_bytes() == content


@pytest.mark.parametrize(
    "relative", ["db/openalgo.db", "db/historify.duckdb", "research_data/research.db"]
)
def test_missing_config_with_saved_data_refuses_new_encryption_keys(install, relative):
    root, _ = install
    path = root / relative
    path.parent.mkdir(parents=True)
    path.touch()
    with pytest.raises(setup.SetupError, match="original .env"):
        setup.ensure_env(root, "fyers")
    assert not (root / ".env").exists()


def test_broker_prompt_is_case_insensitive_and_accepts_unique_search():
    assert setup.choose_broker(SAMPLE, None, prompt=lambda _: "FY") == "fyers"
    assert setup.choose_broker(SAMPLE, " ZERODHA ") == "zerodha"


def test_bad_broker_does_not_write_config(install):
    root, _ = install
    with pytest.raises(setup.SetupError, match="Unknown broker"):
        setup.ensure_env(root, "anything\nAPP_KEY=bad")
    assert not (root / ".env").exists()


def test_unattended_broker_prompt_explains_flag():
    def eof(_):
        raise EOFError

    with pytest.raises(setup.SetupError, match="--broker"):
        setup.choose_broker(SAMPLE, None, prompt=eof)


def test_env_reader_handles_shipped_quotes_comments_and_empty_keys():
    values = setup.env_values(
        "FLASK_PORT = '5311' # a comment\nBROKER_API_KEY=''\nAPP_KEY = abc # comment\n"
    )
    assert values == {"FLASK_PORT": "5311", "BROKER_API_KEY": "", "APP_KEY": "abc"}


def test_frontend_checks_referenced_assets_and_handles_query_strings(install):
    root, _ = install
    index = root / "frontend/dist/index.html"
    index.write_text('<script src="/assets/index.js?v=1"></script>', encoding="utf-8")
    setup.verify_frontend(root)
    (root / "frontend/dist/assets/index.js").unlink()
    with pytest.raises(setup.SetupError, match="incomplete"):
        setup.verify_frontend(root)


@pytest.mark.parametrize(
    "source", ["https://example.org/a.js", "/../outside.js", "/%2e%2e/outside.js"]
)
def test_frontend_rejects_external_or_escaping_assets(install, source):
    root, _ = install
    (root / "frontend/dist/index.html").write_text(
        f'<script src="{source}"></script>', encoding="utf-8"
    )
    with pytest.raises(setup.SetupError, match="incomplete"):
        setup.verify_frontend(root)


def test_source_checkout_fails_before_uv_download_or_config_write(install):
    root, uv = install
    (root / "frontend/dist/index.html").unlink()
    with pytest.raises(setup.SetupError, match="npm ci"):
        setup.setup(
            root,
            uv,
            broker="fyers",
            no_launch=True,
            runner=lambda *_: pytest.fail("no dependencies before interface"),
        )
    assert not (root / ".env").exists()


def test_release_manifest_detects_changed_and_missing_files(install):
    root, _ = install
    target = root / "frontend/dist/assets/index.js"
    manifest = {
        "files": {
            "frontend/dist/assets/index.js": {
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()
            }
        }
    }
    (root / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    setup.verify_release_files(root)
    target.write_text("changed", encoding="utf-8")
    with pytest.raises(setup.SetupError, match="changed"):
        setup.verify_release_files(root)
    target.unlink()
    with pytest.raises(setup.SetupError, match="missing"):
        setup.verify_release_files(root)


def test_subprocess_environment_excludes_other_installation_config(install, monkeypatch):
    root, _ = install
    monkeypatch.setenv("DATABASE_URL", "sqlite:///do-not-use.db")
    monkeypatch.setenv("BROKER_API_KEY", "do-not-pass")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "some-other-install")
    monkeypatch.setenv("UV_PYTHON_PREFERENCE", "only-system")
    monkeypatch.setenv("UV_MANAGED_PYTHON", "false")
    monkeypatch.setenv("UV_NO_MANAGED_PYTHON", "true")
    values = setup.clean_environment(root)
    assert "DATABASE_URL" not in values and "BROKER_API_KEY" not in values
    assert values["UV_PROJECT_ENVIRONMENT"] == str(root / ".venv")
    assert not {"UV_PYTHON_PREFERENCE", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON"} & values.keys()


def test_running_app_prevents_setup(install):
    root, _ = install
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        (root / ".env").write_text(f"FLASK_PORT='{port}'\n", encoding="utf-8")
        with pytest.raises(setup.SetupError, match="already in use"):
            setup.check_not_running(root)


def test_setup_orders_sync_check_migrations_preserves_settings_and_handles_spaces(
    install, monkeypatch
):
    root, uv = install
    original = b"FLASK_PORT='59991'\r\nAPP_KEY='private'\r\n"
    (root / ".env").write_bytes(original)
    monkeypatch.setattr(setup, "check_not_running", lambda *_, **__: None)
    calls = []

    def runner(command, cwd, log, label, **kwargs):
        assert cwd == root
        assert isinstance(command, list) and command[0] == str(uv)
        calls.append(command)

    assert setup.setup(root, uv, no_launch=True, runner=runner) == 0
    assert calls[0][1:5] == ["sync", "--frozen", "--extra", "research"]
    assert "--managed-python" in calls[0]
    assert "UV_PYTHON_PREFERENCE" not in setup.clean_environment(root)
    assert calls[1][-1] == "tools/research_check.py"
    assert [command[-1] for command in calls[2:]] == list(setup.MIGRATIONS)
    # uv 0.12.5 reparses absolute Windows env-file paths containing spaces.
    # cwd is the installation above; a relative filename preserves that scope.
    assert all(command[command.index("--env-file") + 1] == ".env" for command in calls[1:])
    assert (root / ".env").read_bytes() == original


def test_failed_dependency_download_preserves_existing_configuration(install, monkeypatch):
    root, uv = install
    original = b"APP_KEY='preserve'\r\n"
    (root / ".env").write_bytes(original)
    monkeypatch.setattr(setup, "check_not_running", lambda *_, **__: None)

    def offline(*_args, **_kwargs):
        raise setup.SetupError("Installing dependencies did not finish: offline")

    with pytest.raises(setup.SetupError, match="offline"):
        setup.setup(root, uv, no_launch=True, runner=offline)
    assert (root / ".env").read_bytes() == original


def test_child_failure_and_timeout_are_actionable_and_reaped(install):
    root, _ = install
    log = root / "test.log"
    with pytest.raises(setup.SetupError, match="internet connection"):
        setup.run_step([sys.executable, "-c", "raise SystemExit(4)"], root, log, "Downloading")
    with pytest.raises(setup.SetupError, match="took too long"):
        setup.run_step(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            root,
            log,
            "Downloading",
            timeout=0.1,
        )
    # Windows fails this if a child/process still holds the file open.
    log.unlink()


def test_windows_application_control_failure_is_identified_without_policy_changes(install):
    root, _ = install
    with pytest.raises(setup.SetupError, match="Windows Application Control.*administrator"):
        setup.run_step(
            [
                sys.executable,
                "-c",
                "print('An Application Control policy has blocked this file. (os error 4551)'); raise SystemExit(2)",
            ],
            root,
            root / "policy-test.log",
            "Checking compatibility",
        )


def test_entry_scripts_quote_paths_and_start_does_not_install():
    root = Path(__file__).resolve().parents[2]
    windows = (root / "Start.cmd").read_text()
    assert '"%~dp0.venv\\Scripts\\python.exe"' in windows
    assert "research_desktop.py" in windows
    assert "uv sync" not in windows and "powershell" not in windows
    linux = (root / "start-research.sh").read_text()
    assert 'exec "$task_root/.venv/bin/python"' in linux
    assert "uv sync" not in linux


def test_bootstrap_pins_and_checksums_match_platforms():
    root = Path(__file__).resolve().parents[2]
    windows = (root / "tools/research_setup.ps1").read_text()
    linux = (root / "setup-research.sh").read_text()
    assert "Get-FileHash" in windows and "sha256sum" in linux
    assert "/download/0.12.5/" in windows and "/download/0.12.5/" in linux
    assert "Set-ExecutionPolicy" not in windows
    assert "Invoke-Expression" not in windows
    assert "eval " not in linux
    assert "$env:UV_PYTHON_PREFERENCE = $null" in windows
    assert "$env:UV_MANAGED_PYTHON = $null" in windows
    assert "$env:UV_NO_MANAGED_PYTHON = $null" in windows
    assert "unset UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON" in linux
    assert "--managed-python" in windows and "--managed-python" in linux


def test_fresh_port_overrides_keep_all_urls_consistent(install):
    root, _ = install
    assert setup.ensure_env(root, "fyers", port=5191, websocket_port=8791, zmq_port=5591)
    values = setup.env_values((root / ".env").read_text(encoding="utf-8"))
    assert values["FLASK_PORT"] == "5191"
    assert values["HOST_SERVER"] == "http://127.0.0.1:5191"
    assert values["REDIRECT_URL"] == "http://127.0.0.1:5191/fyers/callback"
    assert values["WEBSOCKET_PORT"] == "8791"
    assert values["WEBSOCKET_URL"] == "ws://127.0.0.1:8791"
    assert values["ZMQ_PORT"] == "5591"


@pytest.mark.parametrize(
    "overrides", [{"port": 5191}, {"websocket_port": 8791}, {"zmq_port": 5591}]
)
def test_port_overrides_cannot_change_existing_configuration(install, overrides):
    root, _ = install
    original = b"FLASK_PORT='5000'\r\nWEBSOCKET_PORT='8765'\r\nZMQ_PORT='5555'\r\n"
    (root / ".env").write_bytes(original)
    with pytest.raises(setup.SetupError, match="conflicts"):
        setup.ensure_env(root, None, **overrides)
    assert (root / ".env").read_bytes() == original
    assert not setup.ensure_env(root, None, port=5000, websocket_port=8765, zmq_port=5555)
    assert (root / ".env").read_bytes() == original


@pytest.mark.parametrize(
    "overrides", [{"port": 0}, {"websocket_port": 65536}, {"zmq_port": -1}, {"port": 8765}]
)
def test_invalid_or_overlapping_ports_are_rejected(install, overrides):
    root, _ = install
    with pytest.raises(setup.SetupError, match="between|different"):
        setup.ensure_env(root, "fyers", **overrides)
    assert not (root / ".env").exists()


def test_full_fresh_setup_passes_selected_ports_to_preflight_and_config(install, monkeypatch):
    root, uv = install
    seen = []
    monkeypatch.setattr(setup, "check_not_running", lambda _, *, ports: seen.append(ports))
    assert (
        setup.setup(
            root,
            uv,
            broker="fyers",
            no_launch=True,
            port=5191,
            websocket_port=8791,
            zmq_port=5591,
            runner=lambda *_, **__: None,
        )
        == 0
    )
    assert seen == [{"FLASK_PORT": 5191, "WEBSOCKET_PORT": 8791, "ZMQ_PORT": 5591}]
    assert setup.env_values((root / ".env").read_text())["FLASK_PORT"] == "5191"
