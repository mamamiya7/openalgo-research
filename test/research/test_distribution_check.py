"""Update checks run without numerical imports, dotenv, databases or a broker."""

import ast
from importlib import metadata

import pytest

from tools.research_check import ROOT, accepts_keywords, check, distribution
from tools.research_release import inventory


def test_current_source_and_installed_contract():
    pins = distribution()["packages"]
    result = check(installed_version=pins.__getitem__, python="3.12")
    assert result["ok"], [item for item in result["checks"] if not item["ok"]]


@pytest.mark.parametrize(
    "name,version", [("vectorbt", "1.1.0"), ("optuna", "5.0.1"), ("numpy", "2.5.0")]
)
def test_untested_package_version_does_not_pass(name, version):
    pins = {**distribution()["packages"], name: version}
    result = check(installed_version=pins.__getitem__, python="3.12")
    assert not result["ok"]
    assert (
        next(item for item in result["checks"] if item["name"] == f"{name} installed")["ok"]
        is False
    )


def test_missing_package_is_actionable():
    def missing(_):
        raise metadata.PackageNotFoundError

    result = check(installed_version=missing, python="3.12")
    assert not result["ok"]
    assert "uv sync --frozen --extra research" in next(
        item["detail"] for item in result["checks"] if item["name"] == "vectorbt installed"
    )


def test_source_check_does_not_inspect_installed_packages():
    def unexpected(_):
        pytest.fail("Source bundle validation inspected the runtime")

    assert check(source_only=True, installed_version=unexpected, python="3.12")["ok"]


def test_missing_install_template_fails_source_check(monkeypatch, tmp_path):
    from tools import research_check

    original = research_check.source_path
    monkeypatch.setattr(
        research_check,
        "source_path",
        lambda root, name: tmp_path / name if name == ".sample.env" else original(root, name),
    )
    result = check(source_only=True, python="3.12")
    assert not result["ok"]
    assert not next(item for item in result["checks"] if item["name"] == ".sample.env")["ok"]


@pytest.mark.parametrize(
    "name",
    [
        "research/automatic_walk_forward.py",
        "research/research_mandate.py",
        "research/method_registry.py",
        "research/expanded_stock_market.py",
        "research/method_coverage.py",
        "services/research_method_coverage.py",
        "frontend/src/components/research/MethodCoverage.tsx",
    ],
)
def test_missing_validation_and_method_modules_fail_before_startup(monkeypatch, tmp_path, name):
    from tools import research_check

    original = research_check.source_path
    monkeypatch.setattr(
        research_check,
        "source_path",
        lambda root, candidate: (
            tmp_path / candidate if candidate == name else original(root, candidate)
        ),
    )
    result = check(source_only=True, python="3.12")
    assert not result["ok"]
    assert not next(item for item in result["checks"] if item["name"] == name)["ok"]


def test_untested_python_version_does_not_pass():
    assert not check(source_only=True, python="3.13")["ok"]


@pytest.mark.parametrize("engine", ["vectorbt", "optuna", "nautilus"])
def test_untested_portfolio_adapter_does_not_pass(monkeypatch, engine):
    from tools import research_check

    release = distribution()
    release["portfolio_adapters"][engine] = "untested-new-adapter"
    monkeypatch.setattr(research_check, "distribution", lambda _: release)
    result = check(source_only=True, python="3.12")
    assert not result["ok"]
    assert not next(
        item for item in result["checks"] if item["name"] == f"{engine} portfolio adapter"
    )["ok"]


def test_untested_side_runtime_pin_does_not_pass(monkeypatch):
    from tools import research_check

    release = distribution()
    release["side_runtime"]["packages"]["pyarrow"] = "26.0.0"
    monkeypatch.setattr(research_check, "distribution", lambda _: release)
    result = check(source_only=True, python="3.12")
    assert not result["ok"]
    assert not next(
        item for item in result["checks"] if item["name"] == "pyarrow side runtime pin"
    )["ok"]


@pytest.mark.parametrize(
    "definition,compatible",
    [
        ("def fetch(symbol, interval='D'): pass", True),
        ("def fetch(symbol, interval='D', new_optional=None): pass", True),
        ("def fetch(symbol, mandatory, interval='D'): pass", False),
        ("def fetch(symbol, *, mandatory, interval='D'): pass", False),
        ("def fetch(symbol): pass", False),
        ("def fetch(symbol, /, interval='D'): pass", False),
        ("def renamed(symbol, interval='D'): pass", False),
    ],
)
def test_host_update_signature_changes(definition, compatible):
    assert accepts_keywords(ast.parse(definition), "fetch", ["symbol", "interval"]) is compatible


def test_broken_manifest_fails_cleanly(tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / "research/distribution.json").write_text("not json")
    assert not check(tmp_path, source_only=True)["ok"]


def test_distribution_files_do_not_escape_root(tmp_path):
    # The production source inventory has every research service, including newly
    # added untracked adapters (the former packager omitted several of these).
    names, _, _ = inventory(ROOT)
    required_services = {
        path.relative_to(ROOT).as_posix() for path in (ROOT / "services").glob("research_*.py")
    }
    assert required_services <= set(names)
    from tools.research_check import source_path

    with pytest.raises(ValueError, match="escapes"):
        source_path(tmp_path, "../elsewhere.py")


def test_distribution_includes_native_openscript_host():
    # The upgraded host's strategy runner lives in a new top-level package.
    # Omitting it ships a working research page with broken native strategies.
    names, _, _ = inventory(ROOT)
    required = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "openscript_host").rglob("*.py")
        if "__pycache__" not in path.parts
    }
    assert "openscript_host/openscript_runner.py" in required
    assert required <= set(names)
