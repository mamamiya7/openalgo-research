"""Read-only distribution checks; never import the app, engines or broker clients.

Run after a locked installation, before startup. --source-only checks a source
bundle without installed dependencies. These are structural compatibility checks;
numerical, data and lifecycle regression tests still gate each supported update.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RESEARCH_FILES = {
    "research/distribution.json",
    "research/connectors/registry.py",
    "research/connectors/vectorbt_adapter.py",
    "research/connectors/optuna_adapter.py",
    "research/portfolio.py",
    "research/analytics.py",
    "research/report_contract.py",
    "research/evaluation_basis.py",
    "frontend/src/components/research/PortfolioContinuousReport.tsx",
    "frontend/src/components/research/portfolioReportMetrics.ts",
    "frontend/src/components/research/ReportDrawdowns.tsx",
    "frontend/src/components/research/ReportInvestigation.tsx",
    "frontend/src/components/research/reportInvestigationEvidence.ts",
    "frontend/src/components/research/reportChartPresentation.ts",
    "frontend/src/components/research/ReportCustomize.tsx",
    "frontend/src/components/research/useReportPreferences.ts",
    "frontend/src/api/reportPreferences.ts",
    "research/study_analysis.py",
    "research/portfolio_coverage.py",
    "research/portfolio_validation.py",
    "research/connectors/vectorbt_portfolio.py",
    "research/connectors/optuna_portfolio.py",
    "research/connectors/nautilus_portfolio.py",
    "research/connectors/nautilus_runtime.py",
    "research/runtimes/nautilus/pyproject.toml",
    "research/runtimes/nautilus/uv.lock",
    "blueprints/scanner_research.py",
    "database/research_db.py",
    "database/research_calendar.py",
    "research/calendar_coverage.py",
    "services/research_acquisition.py",
    "services/research_activity.py",
    "services/research_checkpoint.py",
    "services/research_historify.py",
    "services/research_intraday.py",
    "services/research_library.py",
    "services/research_chartink.py",
    "services/research_native_calendar.py",
    "services/research_native_prices.py",
    "services/research_result_presentation.py",
    "services/research_analysis.py",
    "services/research_preferences.py",
    "services/research_sources.py",
    "services/research_storage.py",
    "services/research_portfolio.py",
    "services/research_instruments.py",
    "services/research_mcp.py",
    "tools/research_engine_worker.py",
    "tools/research_install_nautilus.py",
    "services/scanner_research_service.py",
    "services/scanner_research_worker.py",
    "upgrade/migrate_scanner_research.py",
    "upgrade/migrate_research_nse_calendar.py",
    "tools/research_runtime.py",
    "tools/research_check.py",
}


def distribution(root=ROOT):
    with (Path(root) / "research/distribution.json").open(encoding="utf-8") as handle:
        release = json.load(handle)
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?", release["version"]):
        raise ValueError("Invalid research release version")
    return release


def source_path(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Compatibility source escapes the installation")
    return path


def syntax(root, name):
    return ast.parse(source_path(root, name).read_text(encoding="utf-8-sig"))


def constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError(f"Missing connector constant: {name}")


def accepts_keywords(tree, function, supplied):
    """Reject a removed keyword or an added required argument without executing code."""
    node = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function),
        None,
    )
    if node is None or node.args.posonlyargs:
        return False
    args = node.args
    accepted = {arg.arg for arg in [*args.args, *args.kwonlyargs]}
    required = {arg.arg for arg in args.args[: len(args.args) - len(args.defaults)]}
    required.update(
        arg.arg
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        if default is None
    )
    return required <= set(supplied) and (args.kwarg is not None or set(supplied) <= accepted)


def check(root=ROOT, *, source_only=False, installed_version=metadata.version, python=None):
    root = Path(root)
    checks = []

    def record(name, okay, detail):
        checks.append({"name": name, "ok": bool(okay), "detail": detail})

    try:
        release = distribution(root)
        if release["format"] != "openalgo-research-distribution-v1":
            raise ValueError("Unsupported distribution format")
        with (root / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        record(
            "OpenAlgo",
            project["version"] == release["openalgo_version"],
            f"Expected {release['openalgo_version']}; found {project['version']}",
        )
        current_python = python or f"{sys.version_info.major}.{sys.version_info.minor}"
        record(
            "Python",
            current_python == release["python"],
            f"Expected {release['python']}; found {current_python}",
        )
        for name in sorted(REQUIRED_RESEARCH_FILES):
            record(name, source_path(root, name).is_file(), "Required research module")
        registry = syntax(root, "research/connectors/registry.py")
        record(
            "Connector contract",
            constant(registry, "CONTRACT_VERSION") == release["connector_contract"],
            release["connector_contract"],
        )
        record(
            "Portfolio contract",
            constant(syntax(root, "research/portfolio.py"), "VERSION")
            == release["portfolio_contract"],
            release["portfolio_contract"],
        )
        for name, expected in release["portfolio_adapters"].items():
            adapter = constant(
                syntax(root, f"research/connectors/{name}_portfolio.py"), "ADAPTER_VERSION"
            )
            record(f"{name} portfolio adapter", adapter == expected, adapter)
        side = release["side_runtime"]
        with source_path(root, side["path"] + "/pyproject.toml").open("rb") as handle:
            side_project = tomllib.load(handle)["project"]
        with source_path(root, side["path"] + "/uv.lock").open("rb") as handle:
            side_lock = tomllib.load(handle)
        record("Nautilus Python", side_project["requires-python"] == side["python"], side["python"])
        locked_packages = {
            (package["name"], package.get("version")) for package in side_lock["package"]
        }
        for name, expected in side["packages"].items():
            record(
                f"{name} side runtime pin",
                f"{name}=={expected}" in side_project["dependencies"]
                and (name, expected) in locked_packages,
                f"Expected locked {name}=={expected}",
            )
        record(
            "Nautilus engine contract",
            constant(syntax(root, "research/connectors/nautilus_portfolio.py"), "TESTED_VERSION")
            == side["packages"]["nautilus-trader"],
            side["packages"]["nautilus-trader"],
        )
        pins = constant(registry, "TESTED_PACKAGES")
        requirements = set(project["dependencies"] + project["optional-dependencies"]["research"])
        for name, expected in release["packages"].items():
            record(
                f"{name} dependency pin",
                f"{name}=={expected}" in requirements,
                f"Expected {name}=={expected}",
            )
            if name in release["adapters"]:
                record(f"{name} registry pin", pins.get(name) == expected, expected)
                adapter = constant(
                    syntax(root, f"research/connectors/{name}_adapter.py"), "ADAPTER_VERSION"
                )
                record(f"{name} adapter", adapter == release["adapters"][name], adapter)
            if not source_only:
                try:
                    actual = installed_version(name)
                except metadata.PackageNotFoundError:
                    actual = "missing"
                record(
                    f"{name} installed",
                    actual == expected,
                    f"Expected {expected}; found {actual}. Install with uv sync --frozen --extra research.",
                )
        for name, calls in release["host_calls"].items():
            tree = syntax(root, name)
            for function, keywords in calls.items():
                record(
                    f"{name}:{function}",
                    accepts_keywords(tree, function, keywords),
                    "Native host call contract",
                )
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        record("Distribution metadata", False, f"{type(exc).__name__}: {exc}")
        release = {}
    return {
        "ok": all(item["ok"] for item in checks),
        "distribution": release.get("name"),
        "version": release.get("version"),
        "scope": "source" if source_only else "source-and-installed-packages",
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check(args.root, source_only=args.source_only)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"OpenAlgo Research {result['version'] or 'unknown'}: {'compatible' if result['ok'] else 'needs attention'}"
        )
        for item in result["checks"]:
            if not item["ok"]:
                print(f"- {item['name']}: {item['detail']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
