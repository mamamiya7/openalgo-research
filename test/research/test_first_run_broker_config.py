"""The normal setup UI remains reachable before a user enters broker keys."""

from pathlib import Path

import pytest
from dotenv import dotenv_values

from utils import env_check


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    sample = Path(__file__).resolve().parents[2] / ".sample.env"
    content = sample.read_text(encoding="utf-8")
    (tmp_path / ".sample.env").write_text(content, encoding="utf-8")
    (tmp_path / ".env").write_text(content, encoding="utf-8")
    monkeypatch.setattr(env_check, "__file__", str(tmp_path / "utils" / "env_check.py"))
    (tmp_path / "utils").mkdir()
    for key, value in dotenv_values(sample).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    # These tests exercise validation only; secret generation has its own flow.
    monkeypatch.setattr(env_check, "load_dotenv", lambda **_kwargs: None)
    monkeypatch.setattr(env_check, "configure_llvmlite_paths", lambda: None)
    monkeypatch.setattr(env_check, "_generate_keys_on_first_run", lambda _path: None)
    monkeypatch.setattr(env_check, "_ensure_fernet_salt", lambda _path: None)
    return monkeypatch


@pytest.mark.parametrize("broker", ["fivepaisa", "flattrade", "dhan", "fyers"])
def test_empty_keys_allow_native_account_and_broker_setup(configuration, broker):
    configuration.setenv("REDIRECT_URL", f"http://127.0.0.1:5000/{broker}/callback")
    configuration.setenv("BROKER_API_KEY", "")
    configuration.setenv("BROKER_API_SECRET", "")
    env_check.load_and_check_env_variables()


@pytest.mark.parametrize("broker", ["fivepaisa", "flattrade", "dhan"])
def test_supplied_malformed_key_is_still_rejected(configuration, broker):
    configuration.setenv("REDIRECT_URL", f"http://127.0.0.1:5000/{broker}/callback")
    configuration.setenv("BROKER_API_KEY", "malformed-key")
    with pytest.raises(SystemExit) as failure:
        env_check.load_and_check_env_variables()
    assert failure.value.code == 1


@pytest.mark.parametrize(
    "broker,key",
    [("fivepaisa", "key:::user:::client"), ("flattrade", "client:::key"), ("dhan", "client:::key")],
)
def test_supplied_valid_format_is_accepted(configuration, broker, key):
    configuration.setenv("REDIRECT_URL", f"http://127.0.0.1:5000/{broker}/callback")
    configuration.setenv("BROKER_API_KEY", key)
    env_check.load_and_check_env_variables()
